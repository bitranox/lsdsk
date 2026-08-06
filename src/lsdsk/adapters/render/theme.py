"""Formatting and colour vocabulary shared by every view.

Two rules hold everywhere here:

Colour only ever carries meaning.  Green is at capability, yellow is below it,
red is failing, dim is a ceiling nothing can be done about or a value that could
not be read.  Nothing is coloured for decoration.

Colour is never the only carrier.  Every severity also has an ASCII marker, so
the output survives a pipe, a log file, ``NO_COLOR`` and colour blindness.

System Role:
    Adapter layer, presentation vocabulary.  Imports domain values, never the
    other way round.
"""

from __future__ import annotations

from ...domain.enums import BusType, DiskKind, Severity
from ...domain.models import pcie_generation

#: A rendered cell: its text and the style to draw it in. Named once here so the
#: functions that produce one and the tables that consume it agree by type
#: rather than by convention.
from ...domain.thresholds import DEFAULT_THRESHOLDS

#: One rendered table cell: the text, and the style it is drawn in.
#:
#: Left an alias rather than promoted to a NamedTuple, which was measured: it
#: would force 28 construction sites to spell ``Cell(...)`` to give 5 read sites
#: ``.text``/``.style``, and it would NOT catch the swap that justified it,
#: because both fields are ``str`` so ``Cell(style, text)`` type-checks too.
#: Contrast ``TracebackState``, where the pair is consumed by name at two sites
#: and built at one, and the arithmetic runs the other way.
Cell = tuple[str, str]

# Severity styling. The markers are ASCII on purpose: this text has to survive a
# Windows cp1252 console, which is also why nothing here is an emoji.
SEVERITY_MARKERS: dict[Severity, str] = {
    Severity.CRITICAL: "!!",
    Severity.WARNING: "!",
    Severity.HINT: "~",
}

SEVERITY_STYLES: dict[Severity, str] = {
    Severity.CRITICAL: "bold red",
    Severity.WARNING: "yellow",
    Severity.HINT: "dim cyan",
}

SEVERITY_LABELS: dict[Severity, str] = {
    Severity.CRITICAL: "critical",
    Severity.WARNING: "warning",
    Severity.HINT: "hint",
}

# Styles for a measurement compared against what it could be.
STYLE_AT_CAPABILITY = "green"
STYLE_BELOW_CAPABILITY = "yellow"
STYLE_FAILING = "bold red"
STYLE_UNKNOWN = "dim"
STYLE_CEILING = "dim cyan"
STYLE_CAVEAT = "bold yellow"
# A drive that cannot use the port it occupies is not a fault, so it must not
# share the colour faults use. Orange reads as "look at this" without reading as
# "something is broken", which is exactly the difference.
STYLE_OPPORTUNITY = "orange3"

# Temperature bands, in Celsius, used only when a drive declares no thresholds
# of its own. A drive's own limits always win.
#
# Fixed here rather than configurable, like the severity colours above and for
# the same reason: this is presentation vocabulary. They were briefly [display]
# keys that nothing read, under a comment promising they changed severity and
# the exit code. They could not: _temperature_findings weighs a drive against
# the thresholds the drive itself publishes and never against a figure from a
# file, so a band here only ever picks a colour.
TEMPERATURE_WARM = 50
TEMPERATURE_HOT = 60

_SIZE_UNITS = ("B", "K", "M", "G", "T", "P")
_SIZE_STEP = 1024.0


def format_size(size_bytes: int | None) -> str:
    """Render a capacity the way a disk listing should.

    Args:
        size_bytes: Capacity in bytes, or ``None``.

    Returns:
        A short human-readable size.

    Example:
        >>> format_size(4_000_787_030_016)
        '3.6T'
        >>> format_size(500_107_862_016)
        '466G'
        >>> format_size(None)
        '-'
    """
    if size_bytes is None:
        return "-"
    value = float(size_bytes)
    for unit in _SIZE_UNITS:
        if value < _SIZE_STEP or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}" if value < 10 else f"{value:.0f}{unit}"  # noqa: PLR2004 - one decimal below ten
        value /= _SIZE_STEP
    return "-"


def format_speed(gbps: float | None) -> str:
    """Render an interface speed in Gb/s.

    Example:
        >>> format_speed(6.0)
        '6G'
        >>> format_speed(1.5)
        '1.5G'
        >>> format_speed(None)
        '-'
    """
    return "-" if gbps is None else f"{gbps:g}G"


def link_style(negotiated: float | None, port_max: float | None, drive_max: float | None) -> str:
    """Style a negotiated rate against the best the pairing could manage.

    The three speeds are shown as three plain numbers, so the reader can see
    which end is the constraint rather than being told. Only the negotiated rate
    is styled, and only when it falls below both ends, because that is the one
    state nobody can explain by looking at the hardware.

    Red is reserved for a link proven to be at fault, which needs both ends
    known. With one end unread the shortfall is real but unattributed, so it is
    yellow: an unread port may simply be the slower of the two.

    Example:
        >>> link_style(3.0, 12.0, 3.0)
        'green'
        >>> link_style(3.0, 6.0, 6.0)
        'bold red'
        >>> link_style(3.0, None, 6.0)
        'yellow'
        >>> link_style(6.0, None, 6.0)
        'green'
        >>> link_style(None, 6.0, 6.0)
        'dim'
    """
    if negotiated is None:
        return STYLE_UNKNOWN
    ends = [value for value in (port_max, drive_max) if value is not None]
    if not ends:
        return STYLE_UNKNOWN
    if negotiated >= min(ends):
        return STYLE_AT_CAPABILITY
    # Red, not yellow: everything else on the row explains itself from the
    # hardware, and this is the one number that cannot. Both ends agreed they
    # could go faster and then did not. That claim needs both ends measured.
    both_ends_known = port_max is not None and drive_max is not None
    return STYLE_FAILING if both_ends_known else STYLE_BELOW_CAPABILITY


def port_style(port_max_gbps: float | None, drive_max_gbps: float | None) -> str:
    """Style a port's capability against the drive plugged into it.

    Coloured only when the port is the thing holding the drive back. When the
    port is the more capable of the two, the disk column carries that signal, so
    colouring both would say the same thing twice in two colours.

    Example:
        >>> port_style(3.0, 6.0)
        'yellow'
        >>> port_style(12.0, 3.0)
        ''
        >>> port_style(None, 6.0)
        'dim'
    """
    if port_max_gbps is None or drive_max_gbps is None:
        return STYLE_UNKNOWN
    return STYLE_BELOW_CAPABILITY if port_max_gbps < drive_max_gbps else ""


def disk_style(drive_max_gbps: float | None, port_max_gbps: float | None) -> str:
    """Style a drive's capability against the port it occupies.

    Coloured when the drive is slower than its port, because that drive is
    holding a seat it cannot use and another drive may want it. Not a fault, and
    deliberately not the colour a fault gets.

    Example:
        >>> disk_style(3.0, 12.0)
        'orange3'
        >>> disk_style(6.0, 6.0)
        ''
        >>> disk_style(6.0, 3.0)
        ''
        >>> disk_style(6.0, None)
        'dim'
    """
    if drive_max_gbps is None or port_max_gbps is None:
        return STYLE_UNKNOWN
    return STYLE_OPPORTUNITY if drive_max_gbps < port_max_gbps else ""


def format_kind(kind: DiskKind) -> str:
    """Render a disk's media kind, or a dash when it is not known.

    "UNKNOWN" in a column reads as a value the drive reported, which it is not.

    Example:
        >>> format_kind(DiskKind.SSD)
        'SSD'
        >>> format_kind(DiskKind.UNKNOWN)
        '-'
    """
    return "-" if kind is DiskKind.UNKNOWN else kind.value.upper()


def format_bus(bus: BusType) -> str:
    """Render a disk's bus, or a dash when it is not known.

    Example:
        >>> format_bus(BusType.SATA)
        'SATA'
        >>> format_bus(BusType.UNKNOWN)
        '-'
    """
    return "-" if bus is BusType.UNKNOWN else bus.value.upper()


def format_pcie_generation(speed_gtps: float | None, width: int | None) -> str:
    """Render a PCIe link as a marketing generation and a width.

    The compact form, for a column beside a disk: ``Gen4 x4``.

    Example:
        >>> format_pcie_generation(16.0, 4)
        'Gen4 x4'
        >>> format_pcie_generation(None, 4)
        '-'
    """
    generation = pcie_generation(speed_gtps)
    return "-" if generation is None or width is None else f"Gen{generation} x{width}"


def format_pcie_decimal(speed_gtps: float | None, width: int | None) -> str:
    """Render a PCIe link the way a specification sheet writes it.

    The spelled-out form, for a controller header where it follows the word
    PCIe: ``3.0 x8``.

    Example:
        >>> format_pcie_decimal(8.0, 8)
        '3.0 x8'
        >>> format_pcie_decimal(8.0, None)
        '-'
    """
    generation = pcie_generation(speed_gtps)
    return "-" if generation is None or width is None else f"{generation}.0 x{width}"


def format_temperature(
    celsius: int | None,
    warning: int | None = None,
    critical: int | None = None,
    warm_band: int = TEMPERATURE_WARM,
    hot_band: int = TEMPERATURE_HOT,
) -> Cell:
    """Render a temperature and style it against the drive's own limits.

    A drive's declared thresholds always win over the generic bands, because a
    nearline disk happy at 55 C and an NVMe throttling at 70 C cannot share one
    fixed rule.

    Args:
        celsius: The reading.
        warning: The drive's own warning threshold.
        critical: The drive's own critical threshold.

    Returns:
        The text and the style to render it in.

    Example:
        >>> format_temperature(34)
        ('34C', 'green')
        >>> format_temperature(83, warning=82, critical=85)
        ('83C', 'yellow')
        >>> format_temperature(86, warning=82, critical=85)
        ('86C', 'bold red')
        >>> format_temperature(None)
        ('-', 'dim')
    """
    if celsius is None:
        return "-", STYLE_UNKNOWN
    text = f"{celsius}C"
    if critical is not None and celsius >= critical:
        return text, STYLE_FAILING
    if warning is not None and celsius >= warning:
        return text, STYLE_BELOW_CAPABILITY
    if warning is None and critical is None:
        if celsius >= hot_band:
            return text, STYLE_FAILING
        if celsius >= warm_band:
            return text, STYLE_BELOW_CAPABILITY
    return text, STYLE_AT_CAPABILITY


def format_wear(
    percent_used: int | None,
    warning_percent: int = DEFAULT_THRESHOLDS.wear_warning_percent,
    critical_percent: int = DEFAULT_THRESHOLDS.wear_critical_percent,
) -> Cell:
    """Render wear as a percentage consumed, and style it.

    Example:
        >>> format_wear(1)
        ('1%', 'green')
        >>> format_wear(85)
        ('85%', 'yellow')
        >>> format_wear(97)
        ('97%', 'bold red')
        >>> format_wear(None)
        ('-', 'dim')
    """
    if percent_used is None:
        return "-", STYLE_UNKNOWN
    text = f"{percent_used}%"
    if percent_used >= critical_percent:
        return text, STYLE_FAILING
    if percent_used >= warning_percent:
        return text, STYLE_BELOW_CAPABILITY
    return text, STYLE_AT_CAPABILITY


def marker_for(severity: Severity | None) -> str:
    """Return the ASCII marker for a severity, or blank for none.

    Example:
        >>> marker_for(Severity.CRITICAL)
        '!!'
        >>> marker_for(None)
        ''
    """
    return "" if severity is None else SEVERITY_MARKERS[severity]


def style_for(severity: Severity | None) -> str:
    """Return the style for a severity, or the neutral style for none.

    Example:
        >>> style_for(Severity.WARNING)
        'yellow'
        >>> style_for(None)
        ''
    """
    return "" if severity is None else SEVERITY_STYLES[severity]


__all__ = [
    "SEVERITY_LABELS",
    "SEVERITY_MARKERS",
    "SEVERITY_STYLES",
    "STYLE_AT_CAPABILITY",
    "STYLE_BELOW_CAPABILITY",
    "STYLE_CAVEAT",
    "STYLE_CEILING",
    "STYLE_FAILING",
    "STYLE_OPPORTUNITY",
    "STYLE_UNKNOWN",
    "TEMPERATURE_HOT",
    "TEMPERATURE_WARM",
    "Cell",
    "disk_style",
    "format_size",
    "format_speed",
    "format_temperature",
    "format_wear",
    "link_style",
    "marker_for",
    "port_style",
    "style_for",
]
