"""Counter history, and the rules that turn a stored total into a rate.

Every error counter a drive publishes is a lifetime total held in the drive's
own non-volatile attribute table.  It survives reboots, power cycles and
reinstalls, and the host cannot clear it.  That is what makes it trustworthy and
also what makes it nearly useless on its own: the number says how much damage
there has ever been, never when it happened.  Two drives measured on one machine
made the point.  One carried 462640 interface CRC errors and had not gained a
single one in a day; the other carried a comparable total and was gaining a
thousand an hour.  The first is a cable somebody already reseated, the second is
corrupting frames right now, and the totals cannot tell them apart.

So the unit that matters is the derivative, and the time base for it is the
drive's own ``power_on_hours`` rather than the wall clock.  Power-on hours are
monotonic per drive, immune to clock steps and timezones, and do not advance
while the machine is off, so "errors per power-on hour" stays meaningful on a
host that runs two weeks a year.

The rules below are built to be able to say "I cannot tell".  A counter that has
not moved is only reported as quiet when the drive's own lifetime rate predicted
that errors *should* have appeared in the observed span.  A flat threshold
cannot do this job: measured against real captures, a fixed one-week rule
refuses every drive on the machine, including the one whose fault is provably
over, while the self-calibrating rule separates them correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .models import Disk
    from .thresholds import Thresholds

# One sample is a reading. A rate needs two. Structural rather than a policy: no
# arithmetic produces a delta from a single value, so this is not configurable.
MIN_SAMPLES_FOR_A_RATE = 2


class CounterKind(StrEnum):
    """A monotone counter that is worth watching over time.

    The values double as the matching field name on both :class:`Sample` and
    ``Health``, which keeps the two in step.

    Example:
        >>> f"{CounterKind.CRC_ERRORS}"
        'crc_errors'
    """

    CRC_ERRORS = "crc_errors"
    REALLOCATED_SECTORS = "reallocated_sectors"
    PENDING_SECTORS = "pending_sectors"
    UNCORRECTABLE_SECTORS = "uncorrectable_sectors"
    MEDIA_ERRORS = "media_errors"
    PERCENT_USED = "percent_used"
    BYTES_WRITTEN = "bytes_written"
    UNSAFE_SHUTDOWNS = "unsafe_shutdowns"
    ERROR_LOG_ENTRIES = "error_log_entries"
    POWER_CYCLES = "power_cycles"


class TrendVerdict(StrEnum):
    """What the samples support saying about a counter.

    Three of the five are refusals, which is deliberate.  Reporting "no new
    errors" from evidence that could not have shown any is the same fault as
    reporting a value that was never measured.

    Example:
        >>> f"{TrendVerdict.TOO_CLOSE}"
        'too-close'
    """

    FIRST_SAMPLE = "first-sample"
    TOO_CLOSE = "too-close"
    RISING = "rising"
    QUIET = "quiet"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class Sample:
    """One drive's monotone counters at one moment.

    Only counters are stored.  Temperature, link speed and negotiated width are
    point-in-time readings whose history says nothing useful about wear or
    damage, and keeping them would grow the store for no answer.

    Attributes:
        power_on_hours: The drive's own clock, and the time base for every rate.
        captured_at: ISO 8601 timestamp, for display only. No rule reads it.
        crc_errors: Frames corrupted in transit on the interface and resent.
        reallocated_sectors: Sectors retired to the spare pool.
        pending_sectors: Sectors awaiting reallocation.
        uncorrectable_sectors: Sectors that could not be recovered.
        media_errors: Unrecovered data integrity errors (NVMe).
        percent_used: Wear indicator, 0 is new and 100 is at rated endurance.
        bytes_written: Lifetime host writes.
        unsafe_shutdowns: Power lost without a clean shutdown notification.
        error_log_entries: Entries added to the NVMe error information log.
        power_cycles: Times the drive has been powered up.

    Example:
        >>> Sample(power_on_hours=100, captured_at="2026-08-05T00:00:00+00:00", crc_errors=7).crc_errors
        7
    """

    power_on_hours: int
    captured_at: str
    crc_errors: int | None = None
    reallocated_sectors: int | None = None
    pending_sectors: int | None = None
    uncorrectable_sectors: int | None = None
    media_errors: int | None = None
    percent_used: int | None = None
    bytes_written: int | None = None
    unsafe_shutdowns: int | None = None
    error_log_entries: int | None = None
    power_cycles: int | None = None

    def counters(self) -> tuple[tuple[CounterKind, int | None], ...]:
        """Every counter paired with its kind.

        Written out rather than resolved by attribute name. A kind added without
        a matching field then fails loudly the first time it is read, instead of
        reading as never-measured forever and quietly trending as absent.

        Returns:
            One pair per member of :class:`CounterKind`.

        Example:
            >>> len(Sample(power_on_hours=1, captured_at="x").counters()) == len(CounterKind)
            True
        """
        return (
            (CounterKind.CRC_ERRORS, self.crc_errors),
            (CounterKind.REALLOCATED_SECTORS, self.reallocated_sectors),
            (CounterKind.PENDING_SECTORS, self.pending_sectors),
            (CounterKind.UNCORRECTABLE_SECTORS, self.uncorrectable_sectors),
            (CounterKind.MEDIA_ERRORS, self.media_errors),
            (CounterKind.PERCENT_USED, self.percent_used),
            (CounterKind.BYTES_WRITTEN, self.bytes_written),
            (CounterKind.UNSAFE_SHUTDOWNS, self.unsafe_shutdowns),
            (CounterKind.ERROR_LOG_ENTRIES, self.error_log_entries),
            (CounterKind.POWER_CYCLES, self.power_cycles),
        )

    def counter(self, kind: CounterKind) -> int | None:
        """Read one counter by kind.

        Args:
            kind: Which counter to read.

        Returns:
            The stored value, or ``None`` when it was not read.

        Raises:
            KeyError: If the kind has no field, which means the two drifted.

        Example:
            >>> sample = Sample(power_on_hours=1, captured_at="x", media_errors=3)
            >>> sample.counter(CounterKind.MEDIA_ERRORS)
            3
            >>> sample.counter(CounterKind.CRC_ERRORS) is None
            True
        """
        return dict(self.counters())[kind]


@dataclass(frozen=True, slots=True)
class DiskSeries:
    """Every sample recorded for one drive, oldest first.

    Attributes:
        identity: The stable key the drive was recorded under.
        model: Model name at the most recent sample, for display.
        samples: Samples in chronological order.

    Example:
        >>> DiskSeries(identity="naa.1", model="X").samples
        ()
    """

    identity: str
    model: str
    samples: tuple[Sample, ...] = ()


@dataclass(frozen=True, slots=True)
class History:
    """Everything recorded on one machine.

    Attributes:
        hostname: The machine the samples were taken on.
        series: One series per drive.

    Example:
        >>> History(hostname="box").for_identity("naa.1") is None
        True
    """

    hostname: str
    series: tuple[DiskSeries, ...] = ()

    def for_identity(self, identity: str) -> DiskSeries | None:
        """Find the series recorded under a drive identity.

        Args:
            identity: The key from :func:`identity_of`.

        Returns:
            The series, or ``None`` when this drive has not been seen.
        """
        for candidate in self.series:
            if candidate.identity == identity:
                return candidate
        return None


@dataclass(frozen=True, slots=True)
class Trend:
    """What the samples support saying about one counter on one drive.

    Attributes:
        kind: Which counter this describes.
        verdict: What the evidence supports.
        latest: Most recent total, or ``None`` when never read.
        delta: Change across the measured span, ``None`` unless one was measured.
        span_hours: Power-on hours the span covers, ``None`` when not measured.
        per_hour: Errors gained per power-on hour, only when rising.
        expected_from_lifetime: How many errors the drive's own lifetime rate
            predicted across the span. This is what makes a quiet claim
            defensible, and what withholds it when the span proves nothing.

    Example:
        >>> Trend(CounterKind.CRC_ERRORS, TrendVerdict.QUIET, 5, 0, 400, None, 92.0).is_quiet
        True
    """

    kind: CounterKind
    verdict: TrendVerdict
    latest: int | None
    delta: int | None
    span_hours: int | None
    per_hour: float | None
    expected_from_lifetime: float | None

    @property
    def is_rising(self) -> bool:
        """Whether the counter provably gained across the measured span."""
        return self.verdict is TrendVerdict.RISING

    @property
    def is_quiet(self) -> bool:
        """Whether silence across the span is strong enough to mean something."""
        return self.verdict is TrendVerdict.QUIET


def identity_of(disk: Disk) -> str | None:
    """The stable key a drive is tracked under, across reboots and rescans.

    The device node is not usable: ``sda`` and ``PhysicalDrive0`` are positions
    in an enumeration that reorders whenever a controller comes up in a different
    sequence, so a history keyed on one would silently splice two drives'
    counters together.  The world-wide name is the right answer where it exists;
    Windows never reports one, so serial carries identity there.

    Args:
        disk: The drive to key.

    Returns:
        The identity, or ``None`` when the drive cannot be tracked at all.

    Example:
        >>> from lsdsk.domain.models import Disk
        >>> identity_of(Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1"))
        'naa.1'
        >>> identity_of(Disk(node="sda", path="/dev/sda", model="X")) is None
        True
    """
    return disk.wwn or disk.serial or None


def sample_from(disk: Disk, captured_at: str) -> Sample | None:
    """Take one sample of a drive's counters.

    Args:
        disk: The drive to sample.
        captured_at: ISO 8601 timestamp, recorded for display only.

    Returns:
        The sample, or ``None`` when the drive has no health reading or no
        power-on hours. Without the drive's own clock there is no axis to place
        the sample on, and a sample that cannot be placed can never yield a rate.

    Example:
        >>> from lsdsk.domain.models import Disk, Health
        >>> disk = Disk(node="sda", path="/dev/sda", model="X", health=Health(power_on_hours=9, crc_errors=2))
        >>> sample_from(disk, "2026-08-05T00:00:00+00:00").crc_errors
        2
    """
    health = disk.health
    if health is None or health.power_on_hours is None:
        return None
    return Sample(
        power_on_hours=health.power_on_hours,
        captured_at=captured_at,
        crc_errors=health.crc_errors,
        reallocated_sectors=health.reallocated_sectors,
        pending_sectors=health.pending_sectors,
        uncorrectable_sectors=health.uncorrectable_sectors,
        media_errors=health.media_errors,
        percent_used=health.percent_used,
        bytes_written=health.bytes_written,
        unsafe_shutdowns=health.unsafe_shutdowns,
        error_log_entries=health.error_log_entries,
        power_cycles=health.power_cycles,
    )


def _unknown(kind: CounterKind, verdict: TrendVerdict, latest: int | None) -> Trend:
    """A trend that measured no span."""
    return Trend(
        kind=kind,
        verdict=verdict,
        latest=latest,
        delta=None,
        span_hours=None,
        per_hour=None,
        expected_from_lifetime=None,
    )


def _quiet_run_start(samples: list[Sample], kind: CounterKind) -> Sample:
    """Walk back to the oldest consecutive sample holding the current value.

    A counter that has not moved should be judged over the whole span it has not
    moved for, not just since the previous sample. Sampling hourly would
    otherwise make every quiet span an hour long and no silence could ever
    amount to evidence.
    """
    latest = samples[-1]
    start = latest
    for candidate in reversed(samples[:-1]):
        if candidate.counter(kind) != latest.counter(kind):
            break
        if candidate.power_on_hours > start.power_on_hours:
            break  # the drive's clock went backwards; the run does not span it
        start = candidate
    return start


def thin(samples: tuple[Sample, ...], cap: int) -> tuple[Sample, ...]:
    """Reduce a series to at most ``cap`` samples, keeping what matters.

    The first sample is always kept: it is the only true baseline, and the
    further back it reaches the more a lifetime rate is worth.  Recent detail is
    kept in full, because that is what a live fault is judged on.  The middle is
    spread evenly over the hours it covers.

    Spread over HOURS rather than over array positions, because this runs again
    on its own output after every single reading, and a position-based stride is
    not stable under that.  Striding an already-strided middle decimates the
    same samples again and again: measured over four years of hourly readings at
    the shipped cap, it left the baseline plus the most recent three weeks and a
    hole covering 96% of the span, where one pass over the same readings leaves
    a largest gap of 0.4%.  That hole is not cosmetic.  ``_quiet_run_start``
    walks back through the middle to find how long a counter has held its
    current value, and that span is the evidence a QUIET verdict rests on, so
    losing the middle silently changes what the tool concludes.

    Bucketing by hour is stable because the bucket edges depend only on the span
    the samples cover, not on how many of them there are: a sample kept by one
    pass falls in the same bucket on the next and is kept again.

    Args:
        samples: The series, oldest first.
        cap: The most samples to keep. Values below three keep the newest only.

    Returns:
        The reduced series, still oldest first.

    Example:
        >>> made = tuple(Sample(power_on_hours=h, captured_at="x") for h in range(100))
        >>> kept = thin(made, 10)
        >>> len(kept) <= 10, kept[0] is made[0], kept[-1] is made[-1]
        (True, True, True)
        >>> thin(kept, 10) == kept  # applying it again changes nothing
        True
    """
    if len(samples) <= cap:
        return samples
    if cap < 3:  # noqa: PLR2004 - a baseline, a middle and a newest is the smallest meaningful shape
        return samples[-cap:] if cap > 0 else ()

    recent_count = cap // 2
    baseline, recent = samples[0], samples[-recent_count:]
    middle = samples[1:-recent_count]
    room = cap - 1 - recent_count
    if room <= 0:
        return (baseline, *recent)
    return (baseline, *_spread_over_hours(middle, room, after=baseline), *recent)


def _spread_over_hours(middle: tuple[Sample, ...], room: int, *, after: Sample) -> tuple[Sample, ...]:
    """Keep at most ``room`` of ``middle``, spread over the hours it spans.

    One sample per equal-width bucket of the drive's own clock, the oldest in
    each. A drive whose clock never advanced across the middle has no span to
    spread over, so those fall back to a positional stride.

    A drive's clock can go BACKWARDS, which this has to survive rather than
    assume away: the domain treats it as a real event and reports it as
    ``TrendVerdict.RESET``. Two things here exist only for that. The bucket is
    clamped at both ends, because a sample older than the baseline computes a
    negative index that is bounded by nothing. And a sample is kept only when
    its bucket is strictly beyond the last kept one, not merely different from
    it: on a clock that oscillates, "different" is satisfied over and over and
    keeps an unbounded number of samples, which is how a cap of 512 returned
    every one of its inputs. Strictly-increasing buckets can fire at most
    ``room`` times whatever the input does. On the ordinary monotonic series the
    two rules are identical, so this costs the normal path nothing.

    Args:
        middle: The samples between the baseline and the recent window.
        room: The most to keep.
        after: The baseline, which sets where the first bucket starts.

    Returns:
        The kept samples, oldest first, never more than ``room`` of them.
    """
    if len(middle) <= room:
        return middle
    start, end = after.power_on_hours, middle[-1].power_on_hours
    if end <= start:
        step = max(1, -(-len(middle) // room))
        return middle[::step][:room]

    # Integer arithmetic throughout, never a float divisor. A history store is a
    # file the user points at, so its numbers are input rather than measurement:
    # a power_on_hours large enough to exceed a float raised OverflowError from
    # inside the domain, which reaches the user as a traceback rather than as
    # this tool's own "that is not a history store" refusal. Python's integers
    # do not overflow, so the arithmetic simply holds.
    span = end - start
    kept: list[Sample] = []
    last_bucket = -1
    for sample in middle:
        bucket = max(0, min(room - 1, (sample.power_on_hours - start) * room // span))
        if bucket > last_bucket:
            kept.append(sample)
            last_bucket = bucket
    return tuple(kept)


def record(
    history: History,
    disks: Sequence[Disk],
    captured_at: str,
    *,
    cap: int | None = None,
) -> History:
    """Fold one reading of a machine into its history.

    A drive with no identity, or no counters to place on its own clock, is
    skipped rather than stored under a key that would later splice it onto a
    different drive.

    Args:
        history: What has been recorded so far.
        disks: The drives as this run read them.
        captured_at: ISO 8601 timestamp, recorded for display only.
        cap: Trim each series to at most this many samples.

    Returns:
        A new history including this reading.

    Example:
        >>> from lsdsk.domain.models import Disk, Health
        >>> disk = Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(power_on_hours=3))
        >>> len(record(History(hostname="box"), [disk], "t").series)
        1
    """
    existing = {series.identity: series for series in history.series}
    order = [series.identity for series in history.series]
    # An identity two drives share within one reading is not an identity. Folding
    # the second onto the first appends both to one series, so a single instant
    # becomes a span and the difference between two unrelated drives' totals
    # becomes a rate: measured, two drives at 0 and 8000 CRC errors produced a
    # confident "8000 in the last 40 power-on hours, about 200 an hour" and
    # escalated a warning to critical. Serials collide in practice, virtual
    # machines hand out shared synthetic ones, and on Windows the serial IS the
    # identity, so this is reachable rather than theoretical.
    seen: dict[str, int] = {}
    for disk in disks:
        identity = identity_of(disk)
        if identity is not None:
            seen[identity] = seen.get(identity, 0) + 1
    colliding = {identity for identity, count in seen.items() if count > 1}
    for disk in disks:
        identity = identity_of(disk)
        sample = sample_from(disk, captured_at)
        if identity is None or sample is None or identity in colliding:
            continue
        previous = existing.get(identity)
        samples = _fold_in(() if previous is None else previous.samples, sample)
        if cap is not None:
            samples = thin(samples, cap)
        if identity not in existing:
            order.append(identity)
        existing[identity] = DiskSeries(identity=identity, model=disk.model, samples=samples)
    return History(hostname=history.hostname, series=tuple(existing[identity] for identity in order))


def _fold_in(samples: tuple[Sample, ...], sample: Sample) -> tuple[Sample, ...]:
    """Add one reading, replacing the newest where it repeats that drive's hour.

    :func:`has_new_readings` asks whether ANY drive's clock advanced, so a run
    that records at all records every drive, including those whose own clock
    stood still. Kept as its own row, such a reading spends a slot of the sample
    cap on no information and leaves the newest pair unable to produce a span.

    The later reading wins the hour rather than the earlier one: these counters
    only climb, so it is the one true at the end of the hour it belongs to.

    Example:
        >>> rows = (Sample(power_on_hours=9, captured_at="a", crc_errors=1),)
        >>> _fold_in(rows, Sample(power_on_hours=9, captured_at="b", crc_errors=4))[-1].crc_errors
        4
    """
    if samples and samples[-1].power_on_hours == sample.power_on_hours:
        return (*samples[:-1], sample)
    return (*samples, sample)


def has_new_readings(history: History, disks: Sequence[Disk]) -> bool:
    """Whether any drive has anything new to say since it was last recorded.

    This is the rate limit, and it uses the drive's own clock rather than the
    wall clock, like every other judgement here. Two runs inside the same
    power-on hour cannot produce a rate no matter how far apart the wall clock
    says they were, so storing the second one adds a row and no information. A
    drive never seen before always has something to say.

    Args:
        history: What has been recorded so far.
        disks: The drives as this run read them.

    Returns:
        Whether recording this reading would add anything.

    Example:
        >>> from lsdsk.domain.models import Disk, Health
        >>> disk = Disk(node="sda", path="/dev/sda", model="X", wwn="naa.1", health=Health(power_on_hours=5))
        >>> has_new_readings(History(hostname="box"), [disk])
        True
        >>> has_new_readings(record(History(hostname="box"), [disk], "t"), [disk])
        False
    """
    for disk in disks:
        identity = identity_of(disk)
        sample = sample_from(disk, "")
        if identity is None or sample is None:
            continue
        series = history.for_identity(identity)
        if series is None or not series.samples:
            return True
        if sample.power_on_hours > series.samples[-1].power_on_hours:
            return True
    return False


def untracked_disks(disks: Sequence[Disk]) -> tuple[str, ...]:
    """Name the drives this run could not record, and why not.

    Args:
        disks: The drives as this run read them.

    Returns:
        One phrase per drive that cannot be tracked, empty when all can.

    Example:
        >>> from lsdsk.domain.models import Disk
        >>> untracked_disks([Disk(node="sda", path="/dev/sda", model="X")])
        ('/dev/sda: no world-wide name or serial, so it cannot be tracked',)
    """
    counts: dict[str, int] = {}
    for disk in disks:
        identity = identity_of(disk)
        if identity is not None:
            counts[identity] = counts.get(identity, 0) + 1
    reasons: list[str] = []
    for disk in disks:
        identity = identity_of(disk)
        # Said out loud rather than silently skipped: a drive dropped from the
        # history with no explanation looks exactly like one that is simply new.
        if identity is not None and counts[identity] > 1:
            reasons.append(f"{disk.path}: shares an identity with another drive, so it cannot be tracked")
            continue
        if identity_of(disk) is None:
            reasons.append(f"{disk.path}: no world-wide name or serial, so it cannot be tracked")
        elif sample_from(disk, "") is None:
            reasons.append(f"{disk.path}: no power-on hours, so a rate would have no time base")
    return tuple(reasons)


def _previous_reading(usable: list[Sample]) -> Sample:
    """The newest earlier reading that can carry a span.

    Readings from the latest one's own power-on hour are stepped over. Two
    readings inside one hour hold one hour of information, so judging against the
    row directly behind can compare a reading with itself and report the counter
    climbing fastest in the machine as quiet.

    Where every reading sits in that one hour there is nothing better to compare
    against, so the row behind is used and the zero-hour span it produces is what
    refuses to rate it.

    Example:
        >>> rows = [Sample(power_on_hours=h, captured_at="t") for h in (1000, 1004, 1008, 1008)]
        >>> _previous_reading(rows).power_on_hours
        1004
    """
    latest = usable[-1]
    for candidate in reversed(usable[:-1]):
        if candidate.power_on_hours != latest.power_on_hours:
            return candidate
    return usable[-2]


def _rising(kind: CounterKind, previous: Sample, latest: Sample, delta: int, thresholds: Thresholds) -> Trend:
    """Rate a counter that provably gained since the previous sample."""
    span = latest.power_on_hours - previous.power_on_hours
    latest_value = latest.counter(kind)
    if span < thresholds.min_span_hours:
        return Trend(kind, TrendVerdict.TOO_CLOSE, latest_value, delta, span, None, None)
    return Trend(kind, TrendVerdict.RISING, latest_value, delta, span, delta / span, None)


def _quiet(kind: CounterKind, usable: list[Sample], latest_value: int, thresholds: Thresholds) -> Trend:
    """Weigh a counter that has not moved against what the drive's own rate predicted."""
    latest = usable[-1]
    span = latest.power_on_hours - _quiet_run_start(usable, kind).power_on_hours
    lifetime_rate = latest_value / latest.power_on_hours if latest.power_on_hours > 0 else 0.0
    expected = lifetime_rate * span
    convincing = span >= thresholds.min_span_hours and expected >= thresholds.quiet_expected_min
    verdict = TrendVerdict.QUIET if convincing else TrendVerdict.TOO_CLOSE
    return Trend(kind, verdict, latest_value, 0, span, None, expected)


def trend_for(series: DiskSeries, kind: CounterKind, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> Trend:
    """Judge one counter across a drive's recorded samples.

    Args:
        series: The drive's samples, oldest first.
        kind: Which counter to judge.
        thresholds: The judgement values to weigh against.

    Returns:
        The verdict and, where the evidence supports one, a rate.

    Example:
        >>> early = Sample(power_on_hours=1000, captured_at="a", crc_errors=100)
        >>> late = Sample(power_on_hours=1010, captured_at="b", crc_errors=300)
        >>> trend = trend_for(DiskSeries("naa.1", "X", (early, late)), CounterKind.CRC_ERRORS)
        >>> trend.verdict is TrendVerdict.RISING, trend.per_hour
        (True, 20.0)
    """
    usable = [sample for sample in series.samples if sample.counter(kind) is not None]
    if len(usable) < MIN_SAMPLES_FOR_A_RATE:
        latest_only = usable[-1].counter(kind) if usable else None
        return _unknown(kind, TrendVerdict.FIRST_SAMPLE, latest_only)

    latest, previous = usable[-1], _previous_reading(usable)
    latest_value, previous_value = latest.counter(kind), previous.counter(kind)
    if latest_value is None or previous_value is None:  # pragma: no cover - guarded by `usable`
        return _unknown(kind, TrendVerdict.FIRST_SAMPLE, latest_value)

    if latest_value < previous_value or latest.power_on_hours < previous.power_on_hours:
        return _unknown(kind, TrendVerdict.RESET, latest_value)

    if latest_value > previous_value:
        return _rising(kind, previous, latest, latest_value - previous_value, thresholds)
    return _quiet(kind, usable, latest_value, thresholds)


__all__ = [
    "MIN_SAMPLES_FOR_A_RATE",
    "CounterKind",
    "DiskSeries",
    "History",
    "Sample",
    "Trend",
    "TrendVerdict",
    "identity_of",
    "record",
    "sample_from",
    "thin",
    "trend_for",
    "untracked_disks",
]
