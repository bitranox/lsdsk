"""Formatting and colour vocabulary shared by every view.

Two rules hold everywhere here:

Colour only ever carries meaning.  Green is at capability, amber is below it,
red is failing, blue is a ceiling nothing can be done about, and grey is a value
that could not be read.  Nothing is coloured for decoration, and nothing is
faint: the faint attribute buys emphasis by removing contrast, which costs the
reader the value itself.

Colour is never the only carrier.  Every severity also has an ASCII marker, so
the output survives a pipe, a log file, ``NO_COLOR`` and colour blindness.

System Role:
    Adapter layer, presentation vocabulary.  Imports domain values, never the
    other way round.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Final, NamedTuple

from ...domain.enums import BusType, DiskKind, PciPortKind, Severity
from ...domain.models import PcieLink, pcie_generation

#: A rendered cell: its text and the style to draw it in. Named once here so the
#: functions that produce one and the tables that consume it agree by type
#: rather than by convention.
from ...domain.thresholds import DEFAULT_THRESHOLDS, Thresholds

if TYPE_CHECKING:
    from collections.abc import Iterable

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


# The palette is stated in hex rather than as the terminal's named colours, and
# nothing here uses the faint attribute. Both were measured, because a colour
# this tool cannot read is a colour that carries no meaning:
#
#   dim cyan          contrast 1.7 - 1.8 : 1   on every scheme tried
#   yellow            contrast 2.4 : 1         on a light background
#   orange3           contrast 2.6 : 1         on a light background
#
# WCAG puts the floor for large text at 3:1 and for body text at 4.5:1, so the
# old hint colour was roughly half of unreadable. Two causes compounded. The
# faint attribute (SGR 2) is implemented by blending toward the background, so it
# removes exactly the contrast the reader needs; and a NAMED colour resolves
# through the terminal's own palette, where cyan ranges from #3A96DD to the
# legacy console's #008080 - the tool cannot know which it will get.
#
# Each colour below is the most legible member of its hue: the lightness was
# swept and the variant kept whose WORST contrast across a dark console, a black
# console, macOS Terminal's white default and Solarized light was highest. That
# worst case is 4.2:1 for every one of them, which is the ceiling for a saturated
# hue that has to survive both black and white. Verified by
# ``test_every_palette_colour_is_legible_on_every_background``.
@dataclass(frozen=True)
class Palette:
    """The colours one view draws in, named by what each one MEANS.

    There are two. The printed one below has to stay legible on a black console
    and a white one at once, which is what caps it at 4.2:1 and why it cannot
    simply be brightened. The interactive view paints its own background, so it
    carries its own palette and is measured against that background instead;
    it lives in ``adapters/tui/palette.py``, because the render layer has no
    business knowing that a second one exists.

    Naming the roles in one type is what keeps the two in step: a role added
    here has to be answered by both, and the layer that swaps one for the other
    is built from the pair rather than from a list somebody maintains.

    Attributes:
        critical: A proven fault.
        warning: Below what both ends could manage, or a drive past its own
            warning threshold.
        at_capability: A measurement that is as good as the hardware allows.
        hint: A ceiling nothing can be done about.
        opportunity: Worth looking at, and explicitly not a fault.
        unknown: A value that could not be read.
        header: A column header. EMPTY in print, where a header is bold and
            takes no hue, because a hue there would claim a severity a heading
            does not have.
    """

    critical: str
    warning: str
    at_capability: str
    hint: str
    opportunity: str
    unknown: str
    header: str

    def roles(self) -> dict[str, str]:
        """Every role that carries a colour, by name; a hueless one is left out.

        Example:
            >>> "header" in PRINTED.roles()
            False
            >>> PRINTED.roles()["critical"] == STYLE_CRITICAL_COLOUR
            True
        """
        return {field.name: getattr(self, field.name) for field in fields(self) if getattr(self, field.name)}

    @property
    def header_style(self) -> str:
        """How a column header is drawn: bold, plus this palette's hue if it has one.

        Example:
            >>> PRINTED.header_style
            'bold'
        """
        return f"bold {self.header}" if self.header else "bold"


#: What every printed view draws in. The six hues are the measured ones above;
#: ``header`` is empty because a printed heading is bold and hueless.
PRINTED: Final = Palette(
    critical="#E12D2D",
    warning="#A5660D",
    at_capability="#22874C",
    hint="#1C7FA0",
    opportunity="#C65310",
    unknown="#6E7687",
    header="",
)

STYLE_CRITICAL_COLOUR = PRINTED.critical
STYLE_WARNING_COLOUR = PRINTED.warning
STYLE_AT_CAPABILITY_COLOUR = PRINTED.at_capability
STYLE_HINT_COLOUR = PRINTED.hint
STYLE_OPPORTUNITY_COLOUR = PRINTED.opportunity
STYLE_UNKNOWN_COLOUR = PRINTED.unknown

SEVERITY_STYLES: dict[Severity, str] = {
    Severity.CRITICAL: f"bold {STYLE_CRITICAL_COLOUR}",
    Severity.WARNING: STYLE_WARNING_COLOUR,
    Severity.HINT: STYLE_HINT_COLOUR,
}

SEVERITY_LABELS: dict[Severity, str] = {
    Severity.CRITICAL: "critical",
    Severity.WARNING: "warning",
    Severity.HINT: "hint",
}

# Styles for a measurement compared against what it could be.
STYLE_AT_CAPABILITY = STYLE_AT_CAPABILITY_COLOUR
STYLE_BELOW_CAPABILITY = STYLE_WARNING_COLOUR
STYLE_FAILING = f"bold {STYLE_CRITICAL_COLOUR}"
#: A value that could not be read. Grey rather than faint: the reader still has
#: to SEE that a dash is there, and the whole point of the dash is that it is
#: different from a zero.
STYLE_UNKNOWN = STYLE_UNKNOWN_COLOUR
STYLE_CEILING = STYLE_HINT_COLOUR
STYLE_CAVEAT = f"bold {STYLE_WARNING_COLOUR}"
#: A column header. Bold rather than faint: the headers name every column, so
#: they are the last thing that should be hard to read.
STYLE_HEADER = PRINTED.header_style
#: A note the view writes about itself - what it is drawing, and what to type or
#: press for another shape. Bold with no hue, which renders bright white on a
#: dark terminal and black on a light one: a literal white would be invisible on
#: the white backgrounds this palette was measured against, and a hue here would
#: claim a severity the sentence does not carry.
STYLE_NOTE = "bold"
#: An identifier - a PCI address, a device path. Bold WITHOUT a hue, because
#: colour here would claim a severity the value does not have, and the law of
#: this file is that colour only ever carries meaning.
STYLE_IDENTIFIER = "bold"
# A drive that cannot use the port it occupies is not a fault, so it must not
# share the colour faults use. Orange reads as "look at this" without reading as
# "something is broken", which is exactly the difference.
STYLE_OPPORTUNITY = STYLE_OPPORTUNITY_COLOUR

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

# A capacity has two honest answers and they differ by 7 percent per step: a
# drive is SOLD in powers of ten and REPORTS in powers of two. Written with the
# scale unnamed, the figure is read as whichever the reader expects, so a drive
# sold as 500 GB rendered `466G` reads as a different drive from the one on the
# invoice. Both unit lists therefore name their scale, and `format_size_both`
# writes the pair where there is room for it.
_BINARY_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_DECIMAL_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_BINARY_STEP = 1024.0
_DECIMAL_STEP = 1000.0


def _scaled(size_bytes: int, step: float, units: tuple[str, ...]) -> str:
    """One capacity on one scale, to the unit that keeps it under three digits."""
    value = float(size_bytes)
    for unit in units:
        if value < step or unit == units[-1]:
            if unit == units[0]:
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}" if value < 10 else f"{value:.0f}{unit}"  # noqa: PLR2004 - one decimal below ten
        value /= step
    return "-"


def format_size(size_bytes: int | None) -> str:
    """Render a capacity for a column, on the scale the drive reports in.

    The unit NAMES its scale (``GiB``, not ``G``), because the same drive is
    two different numbers on the two scales and a column is where a reader
    compares one drive against another. The pair form is
    :func:`format_size_both`, for the places with room for it.

    Args:
        size_bytes: Capacity in bytes, or ``None``.

    Returns:
        A short human-readable size.

    Example:
        >>> format_size(4_000_787_030_016)
        '3.6TiB'
        >>> format_size(500_107_862_016)
        '466GiB'
        >>> format_size(512)
        '512B'
        >>> format_size(None)
        '-'
    """
    return "-" if size_bytes is None else _scaled(size_bytes, _BINARY_STEP, _BINARY_UNITS)


def format_size_both(size_bytes: int | None) -> str:
    """Render a capacity on both scales, the way the box and the drive say it.

    The decimal figure first, because that is the one a reader arrived with -
    it is what the drive was sold as and what is printed on its label - and the
    binary one after it, because that is what every tool on the machine will
    report. Below a kilobyte the two agree and only one is written, since
    ``512B/512B`` says nothing twice.

    Args:
        size_bytes: Capacity in bytes, or ``None``.

    Returns:
        Both figures, or the single one where they agree.

    Example:
        >>> format_size_both(500_107_862_016)
        '500GB/466GiB'
        >>> format_size_both(4_000_787_030_016)
        '4.0TB/3.6TiB'
        >>> format_size_both(512)
        '512B'
        >>> format_size_both(None)
        '-'
    """
    if size_bytes is None:
        return "-"
    binary = _scaled(size_bytes, _BINARY_STEP, _BINARY_UNITS)
    decimal = _scaled(size_bytes, _DECIMAL_STEP, _DECIMAL_UNITS)
    return binary if binary == decimal else f"{decimal}/{binary}"


def format_speed(gbps: float | None) -> str:
    """Render an interface speed in Gb/s.

    Args:
        gbps: The rate, or ``None`` where nobody published one.

    Returns:
        The marketing figure, or the not-read marker.

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

    Args:
        negotiated: The rate the link actually came up at.
        port_max: What the port end can do, or ``None`` if unread.
        drive_max: What the drive end can do, or ``None`` if unread.

    Returns:
        The style for the negotiated figure, blank where there is nothing to say.

    Example:
        >>> link_style(3.0, 12.0, 3.0) == STYLE_AT_CAPABILITY
        True
        >>> link_style(3.0, 6.0, 6.0) == STYLE_FAILING
        True
        >>> link_style(3.0, None, 6.0) == STYLE_BELOW_CAPABILITY
        True
        >>> link_style(6.0, None, 6.0) == STYLE_AT_CAPABILITY
        True
        >>> link_style(None, 6.0, 6.0) == STYLE_UNKNOWN
        True
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

    Args:
        port_max_gbps: What the port can do, or ``None`` if unread.
        drive_max_gbps: What the drive in it can do, or ``None`` if unread.

    Returns:
        The style for the port figure, blank when the port is not the constraint.

    Example:
        >>> port_style(3.0, 6.0) == STYLE_BELOW_CAPABILITY
        True
        >>> port_style(12.0, 3.0)
        ''
        >>> port_style(None, 6.0) == STYLE_UNKNOWN
        True
    """
    if port_max_gbps is None or drive_max_gbps is None:
        return STYLE_UNKNOWN
    return STYLE_BELOW_CAPABILITY if port_max_gbps < drive_max_gbps else ""


def disk_style(drive_max_gbps: float | None, port_max_gbps: float | None) -> str:
    """Style a drive's capability against the port it occupies.

    Coloured when the drive is slower than its port, because that drive is
    holding a seat it cannot use and another drive may want it. Not a fault, and
    deliberately not the colour a fault gets.

    Args:
        drive_max_gbps: What the drive can do, or ``None`` if unread.
        port_max_gbps: What the port it sits in can do, or ``None`` if unread.

    Returns:
        The style for the drive figure, blank when it is not the slower end.

    Example:
        >>> disk_style(3.0, 12.0) == STYLE_OPPORTUNITY
        True
        >>> disk_style(6.0, 6.0)
        ''
        >>> disk_style(6.0, 3.0)
        ''
        >>> disk_style(6.0, None) == STYLE_UNKNOWN
        True
    """
    if drive_max_gbps is None or port_max_gbps is None:
        return STYLE_UNKNOWN
    return STYLE_OPPORTUNITY if drive_max_gbps < port_max_gbps else ""


def format_kind(kind: DiskKind) -> str:
    """Render a disk's media kind, or a dash when it is not known.

    "UNKNOWN" in a column reads as a value the drive reported, which it is not.

    Args:
        kind: The media kind the drive was classified as.

    Returns:
        The kind in upper case, or a dash where it was not known.

    Example:
        >>> format_kind(DiskKind.SSD)
        'SSD'
        >>> format_kind(DiskKind.UNKNOWN)
        '-'
    """
    return "-" if kind is DiskKind.UNKNOWN else kind.value.upper()


def format_bus(bus: BusType) -> str:
    """Render a disk's bus, or a dash when it is not known.

    Args:
        bus: The transport the drive speaks.

    Returns:
        The bus in upper case, or a dash where it was not known.

    Example:
        >>> format_bus(BusType.SATA)
        'SATA'
        >>> format_bus(BusType.UNKNOWN)
        '-'
    """
    return "-" if bus is BusType.UNKNOWN else bus.value.upper()


def format_pcie_generation(speed_gtps: float | None, width: int | None) -> str:
    """Render a PCIe link as a marketing generation and a width.

    The compact form, for a column beside a disk: ``Gen4x4``. Written closed,
    with no blank inside it, because it is ONE value in one column: the space
    invited a reader to take the width for a separate field, and cost a
    character in every hop column on the page.

    Args:
        speed_gtps: The link's rate per lane, where it was read.
        width: The link's lane count, where it was read.

    Returns:
        The closed figure, or a dash where either half was not read.

    Example:
        >>> format_pcie_generation(16.0, 4)
        'Gen4x4'
        >>> format_pcie_generation(None, 4)
        '-'
    """
    generation = pcie_generation(speed_gtps)
    return "-" if generation is None or width is None else f"Gen{generation}x{width}"


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
        warm_band: The generic figure used when the drive declares no warning.
        hot_band: The generic figure used when the drive declares no critical.

    Returns:
        The text and the style to render it in.

    Example:
        >>> format_temperature(34) == ('34C', STYLE_AT_CAPABILITY)
        True
        >>> format_temperature(83, warning=82, critical=85) == ('83C', STYLE_BELOW_CAPABILITY)
        True
        >>> format_temperature(86, warning=82, critical=85) == ('86C', STYLE_FAILING)
        True
        >>> format_temperature(None) == ('-', STYLE_UNKNOWN)
        True
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


def format_wear(percent_used: int | None, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> Cell:
    """Render wear as a percentage consumed, and style it.

    The thresholds come as the OBJECT the rules judge by rather than as two
    integers defaulted at definition time. Defaulted that way, every production
    call site passed neither and the table was judged by the shipped figures
    while the findings beside it used the configured ones - a fleet that lowers
    `wear_warning_percent` saw the finding and a cell still coloured at 80.

    Args:
        percent_used: The drive's own figure, or ``None`` where it published none.
        thresholds: What this run judges by.

    Returns:
        The text and its style, as one cell.

    Example:
        >>> format_wear(1) == ('1%', STYLE_AT_CAPABILITY)
        True
        >>> format_wear(85) == ('85%', STYLE_BELOW_CAPABILITY)
        True
        >>> format_wear(97) == ('97%', STYLE_FAILING)
        True
        >>> format_wear(None) == ('-', STYLE_UNKNOWN)
        True
    """
    if percent_used is None:
        return "-", STYLE_UNKNOWN
    text = f"{percent_used}%"
    if percent_used >= thresholds.wear_critical_percent:
        return text, STYLE_FAILING
    if percent_used >= thresholds.wear_warning_percent:
        return text, STYLE_BELOW_CAPABILITY
    return text, STYLE_AT_CAPABILITY


def marker_for(severity: Severity | None) -> str:
    """Return the ASCII marker for a severity, or blank for none.

    Args:
        severity: The severity to mark, or ``None``.

    Returns:
        The marker, which is what carries severity when colour is off.

    Example:
        >>> marker_for(Severity.CRITICAL)
        '!!'
        >>> marker_for(None)
        ''
    """
    return "" if severity is None else SEVERITY_MARKERS[severity]


# What a bridge is called in the fabric view, by its port kind. A port whose
# type was not read gets no tag rather than a wrong one; the class code still
# says it is a bridge.
_PCI_KIND_TAG: dict[PciPortKind, str] = {
    PciPortKind.ROOT: "root port",
    PciPortKind.SWITCH_UPSTREAM: "switch port",
    PciPortKind.SWITCH_DOWNSTREAM: "switch port",
}


def pci_tag(kind: PciPortKind) -> str:
    """A short noun for what kind of PCIe port a node is.

    Args:
        kind: The port kind read from the device.

    Returns:
        The noun, or blank where the kind is unknown.

    Example:
        >>> pci_tag(PciPortKind.ROOT)
        'root port'
        >>> pci_tag(PciPortKind.SWITCH_DOWNSTREAM)
        'switch port'
        >>> pci_tag(PciPortKind.UNKNOWN)
        ''
    """
    return _PCI_KIND_TAG.get(kind, "")


class HopPair(NamedTuple):
    """The two styled cells of one fabric hop, in the order its columns are drawn.

    Both fields are a :data:`Cell`, so a swap type-checks and reads correctly at
    every call site - which is the one shape this project's own rule says to
    name. It is a SECOND pair type rather than :class:`LinkPair` because the two
    views disagree about the order and each is right for itself: the hop columns
    are headed ``capable`` then ``running``, and the slot view draws the
    negotiated figure first. Collapsing them into one type would silently invert
    a column in whichever view lost, so the difference is carried in the names.
    """

    capable: Cell
    running: Cell


def hop_link_cells(link: PcieLink, *, capability_present: bool | None = None, bandwidth: bool = False) -> HopPair:
    """The capable and running columns for one hop of the fabric.

    Two columns, in the order the slot view uses, and three different facts in
    them: a measured figure, a register nobody published (:data:`NOT_READ`),
    and a device with no PCIe capability at all (:data:`LEGACY`). The last two
    are symbols rather than sentences, because a column repeating ``not read``
    down a whole page carries one bit at the cost of the name beside it, and
    :func:`hop_legend` is what makes a symbol honest: the section that draws
    one says what it means.

    Which of the two applies is carried by ``capability_present`` and by
    nothing in the link, because deciding it from the rendered dash read a
    fact back out of a string this function had just written - a device whose
    speeds were read and whose widths were not formats to two dashes and is
    not a legacy device at all.

    Args:
        link: The hop's link state and capability.
        capability_present: Whether the device has a PCIe capability. ``False``
            is a measured absence and prints :data:`LEGACY`; ``True`` and
            ``None`` both leave an unread column reading :data:`NOT_READ`,
            because an unanswered question is not an answer of no.
        bandwidth: Whether each figure carries what it is worth. The caller
            decides, because only it knows the width the column was measured
            for, and a cell that carries more than its column was sized for
            would be CLIPPED - and half a link figure is a different figure
            rather than a shorter one.

    Returns:
        The capable and running styled cells, named. A column whose figure was not
        read is styled :data:`STYLE_UNKNOWN`; a measured one carries none,
        because the styles that judge a link (below capability, failing) are
        the domain's severity verdicts and are carried by a marker on the row,
        not restated here per column.

    Example:
        >>> hop_link_cells(PcieLink(current_speed_gtps=8.0, current_width=4, max_speed_gtps=8.0, max_width=4))
        HopPair(capable=('Gen3x4', ''), running=('Gen3x4', ''))
        >>> hop_link_cells(
        ...     PcieLink(current_speed_gtps=8.0, current_width=4, max_speed_gtps=16.0, max_width=4), bandwidth=True
        ... )
        HopPair(capable=('Gen4x4 (7.88 GB/s)', ''), running=('Gen3x4 (3.94 GB/s)', ''))
        >>> hop_link_cells(PcieLink(), capability_present=False)
        HopPair(capable=('legacy', ''), running=('legacy', ''))
        >>> hop_link_cells(PcieLink(), capability_present=False, bandwidth=True)
        HopPair(capable=('legacy', ''), running=('legacy', ''))
        >>> hop_link_cells(PcieLink(), capability_present=None)[0] == (NOT_READ, STYLE_UNKNOWN)
        True
        >>> hop_link_cells(PcieLink(current_speed_gtps=16.0, current_width=2))[1]
        ('Gen4x2', '')
        >>> hop_link_cells(PcieLink(current_speed_gtps=16.0, current_width=2))[0] == (NOT_READ, STYLE_UNKNOWN)
        True
    """
    capable = format_pcie_generation(link.max_speed_gtps, link.max_width)
    running = format_pcie_generation(link.current_speed_gtps, link.current_width)
    if bandwidth:
        # Each figure with ITS OWN throughput: the capable link's from the
        # maximum, the running link's from what was negotiated. Crossing them
        # is the defect this whole change exists to fix.
        capable = with_bandwidth(capable, link.max_bandwidth_gbps)
        running = with_bandwidth(running, link.current_bandwidth_gbps)
    if capability_present is False:
        return HopPair((LEGACY, ""), (LEGACY, ""))
    # No ternary picking NOT_READ: it IS "-", so the two arms were the same
    # value and 0 of 32 branches across the captures ever changed one. What the
    # comparison really decides is the STYLE, and it is spelled NOT_READ so the
    # marker has one name here as everywhere else.
    return HopPair(
        (capable, STYLE_UNKNOWN if capable == NOT_READ else ""),
        (running, STYLE_UNKNOWN if running == NOT_READ else ""),
    )


class LinkPair(NamedTuple):
    """The two styled cells of one link.

    Named because both fields are a :data:`Cell`, so a type checker cannot
    catch them being swapped.
    """

    running: Cell
    capable: Cell


def link_pair_cells(link: PcieLink, *, bandwidth: bool = False) -> LinkPair:
    """The running and capable cells for one link, decided from the MODEL.

    Both homes of this pair used to choose the style by comparing the two
    strings they had just formatted, and a string comparison cannot tell an
    unread figure from a figure that was read and matched. Both ends unread
    formatted to two dashes, compared EQUAL, and were drawn with no style at
    all - identical to a link measured to be at capability, which is the
    blank-implies-fine this tool forbids itself. One end unread compared
    DIFFERENT and was drawn amber, claiming a shortfall against a capability
    nobody read.

    A figure is read only when BOTH halves of it were published: a speed with
    no width formats to a dash exactly as an unread speed does, so the state is
    asked of the link rather than of the text.

    Unlike :func:`hop_link_cells`, the below-capability judgement IS restated
    here, because these two columns are drawn without the row marker that
    carries it in the fabric view.

    Args:
        link: The link to describe.
        bandwidth: Whether each figure carries what it is worth.

    Returns:
        The running and capable cells. An unread figure is
        :data:`STYLE_UNKNOWN`; a running figure measured below a capability
        that was ALSO measured is :data:`STYLE_BELOW_CAPABILITY`.

    Example:
        >>> link_pair_cells(PcieLink()) == ((NOT_READ, STYLE_UNKNOWN), (NOT_READ, STYLE_UNKNOWN))
        True
        >>> link_pair_cells(PcieLink(current_speed_gtps=8.0, current_width=8)).running
        ('Gen3x8', '')
        >>> link_pair_cells(PcieLink(current_speed_gtps=8.0, current_width=8)).capable
        ('-', '#6E7687')
        >>> link_pair_cells(
        ...     PcieLink(current_speed_gtps=2.5, current_width=8, max_speed_gtps=8.0, max_width=8)
        ... ).running
        ('Gen1x8', '#A5660D')
    """
    running_read = link.current_speed_gtps is not None and link.current_width is not None
    capable_read = link.max_speed_gtps is not None and link.max_width is not None
    running = format_pcie_generation(link.current_speed_gtps, link.current_width)
    capable = format_pcie_generation(link.max_speed_gtps, link.max_width)
    if bandwidth:
        running = with_bandwidth(running, link.current_bandwidth_gbps)
        capable = with_bandwidth(capable, link.max_bandwidth_gbps)
    below = (
        running_read
        and capable_read
        and link.current_bandwidth_gbps is not None
        and link.max_bandwidth_gbps is not None
        and link.current_bandwidth_gbps < link.max_bandwidth_gbps
    )
    if not running_read:
        running_style = STYLE_UNKNOWN
    elif below:
        running_style = STYLE_BELOW_CAPABILITY
    else:
        running_style = ""
    return LinkPair((running, running_style), (capable, "" if capable_read else STYLE_UNKNOWN))


#: What a hop column prints when the register behind it was not read, and what
#: it prints for a device that has no PCIe capability at all. They are SYMBOLS,
#: short because a column of repeated words carries one bit down a whole page,
#: and a section that draws either says what it means in :func:`hop_legend`. A
#: dash alone would read as "nothing there" beside a measured figure, which is
#: the opposite of the truth; a dash WITH its legend does not.
NOT_READ = "-"
LEGACY = "legacy"

#: What a value prints when the thing it names cannot exist for this subject at
#: all: a numbered ATA attribute on an NVMe drive, the occupant of a socket with
#: nothing in it. A SECOND symbol rather than the dash, because the dash already
#: means "nobody read it" and one marker cannot carry both - a reader who cannot
#: tell them apart reads a field that never existed as a reading somebody
#: missed, which is the direction that sends them looking for a fault. Whichever
#: markers a panel drew are named under it.
NOT_APPLICABLE = "n/a"

#: What a figure carries when it is a CEILING rather than a measurement: the
#: two ends it is the lower of were not both read, so it can only be too high.
#: A THIRD symbol, because the other two say a value is absent and this one
#: qualifies a value that is present - drawn flat, the figure claims a
#: measurement that was never taken. ASCII rather than a glyph, so no console
#: needs a fallback for it. Whichever markers a panel drew are named under it.
AT_MOST = "<="

#: What each symbol means, keyed by the token the column actually prints, so
#: the legend cannot explain a word the view does not use.
_HOP_MEANINGS: Final[dict[str, str]] = {
    NOT_READ: "not read",
    LEGACY: "no PCIe capability",
}


#: The placeholders a bandwidth is never put beside. Kept as a set of the
#: TOKENS a column actually prints, so adding a fourth symbol to the vocabulary
#: and forgetting it here is one edit rather than a silent decoration of a value
#: nobody read - or of one that could not exist to be read.
_NO_BANDWIDTH: Final[frozenset[str]] = frozenset({NOT_READ, LEGACY, NOT_APPLICABLE})


def format_bandwidth(gbps: float | None) -> str:
    """Render a usable bandwidth in BYTES per second.

    One spelling for the whole tool, because a figure written two ways in one
    view reads as two measurements. Never in bits: the link shapes it stands
    beside are already a rate, and ``6G`` next to ``0.60 GB/s`` is one link
    written on two scales eight times apart.

    Args:
        gbps: The usable rate in GIGABITS per second, or ``None``.

    Returns:
        The figure in GB/s, or the not-read marker.

    Example:
        >>> format_bandwidth(7.876)
        '7.88 GB/s'
        >>> format_bandwidth(0.6)
        '0.60 GB/s'
        >>> format_bandwidth(None)
        '-'
    """
    return "-" if gbps is None else f"{gbps:.2f} GB/s"


def with_bandwidth(figure: str, gbps: float | None) -> str:
    """Put a link figure's OWN bandwidth beside it.

    Which bandwidth belongs to which figure is the caller's to get right, and
    it is the whole point: the panel used to end a line with the CAPABLE link's
    throughput while the first value on it was the RUNNING link, and a reader
    takes the number next to what they were looking at.

    The figure comes back unchanged when it is a placeholder or the bandwidth is
    unknown. A dash cannot carry a throughput, and a number after a figure
    nobody read would be an invention - the blank-implies-fine the link rules
    refuse.

    Args:
        figure: The link shape, already formatted.
        gbps: What THAT shape carries, in GB/s, or ``None``.

    Returns:
        The figure, with its bandwidth in parentheses where there is one.

    Example:
        >>> with_bandwidth("Gen3x4", 3.938)
        'Gen3x4 (3.94 GB/s)'
        >>> with_bandwidth("6G", 0.6)
        '6G (0.60 GB/s)'
        >>> with_bandwidth(NOT_READ, 3.94)
        '-'
        >>> with_bandwidth(LEGACY, 3.94)
        'legacy'
        >>> with_bandwidth("6G", None)
        '6G'
    """
    if gbps is None or figure in _NO_BANDWIDTH:
        return figure
    return f"{figure} ({format_bandwidth(gbps)})"


def hop_legend(drawn: Iterable[str]) -> str:
    """Spell out the hop symbols a section actually drew.

    Args:
        drawn: Every text the section put in a hop column.

    Returns:
        The legend, or an empty string when every hop was a figure and there is
        nothing to explain.

    Example:
        >>> hop_legend(["Gen3x4", NOT_READ])
        '- = not read'
        >>> hop_legend([NOT_READ, LEGACY])
        '- = not read, legacy = no PCIe capability'
        >>> hop_legend(["Gen3x4", "Gen1x1"])
        ''
    """
    seen = set(drawn)
    return ", ".join(f"{symbol} = {meaning}" for symbol, meaning in _HOP_MEANINGS.items() if symbol in seen)


def style_for(severity: Severity | None) -> str:
    """Return the style for a severity, or the neutral style for none.

    Args:
        severity: The severity to style, or ``None``.

    Returns:
        The style, blank where there is no severity.

    Example:
        >>> style_for(Severity.WARNING) == SEVERITY_STYLES[Severity.WARNING]
        True
        >>> style_for(None)
        ''
    """
    return "" if severity is None else SEVERITY_STYLES[severity]


__all__ = [
    "AT_MOST",
    "LEGACY",
    "NOT_APPLICABLE",
    "NOT_READ",
    "PRINTED",
    "SEVERITY_LABELS",
    "SEVERITY_MARKERS",
    "SEVERITY_STYLES",
    "STYLE_AT_CAPABILITY",
    "STYLE_BELOW_CAPABILITY",
    "STYLE_CAVEAT",
    "STYLE_CEILING",
    "STYLE_FAILING",
    "STYLE_HEADER",
    "STYLE_IDENTIFIER",
    "STYLE_NOTE",
    "STYLE_OPPORTUNITY",
    "STYLE_UNKNOWN",
    "TEMPERATURE_HOT",
    "TEMPERATURE_WARM",
    "Cell",
    "HopPair",
    "LinkPair",
    "Palette",
    "disk_style",
    "format_bandwidth",
    "format_bus",
    "format_kind",
    "format_pcie_generation",
    "format_size",
    "format_size_both",
    "format_speed",
    "format_temperature",
    "format_wear",
    "hop_legend",
    "hop_link_cells",
    "link_pair_cells",
    "link_style",
    "marker_for",
    "pci_tag",
    "port_style",
    "style_for",
    "with_bandwidth",
]
