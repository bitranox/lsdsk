"""How full a filesystem is, and how much of its inode table is used.

Both are judged from the percentages :class:`~lsdsk.domain.models.Filesystem`
already computes, which round UP exactly as ``df`` does, so a finding and the
table above it never disagree about the same mount. A limit is met AT the figure
("at or above"), and a read-only mount is never judged: nothing can fill it.

System Role:
    Domain rule. Reads an :class:`Inventory` and the thresholds, produces
    :class:`Finding` values, touches nothing else.
"""

from __future__ import annotations

from .enums import Severity
from .models import Filesystem, Finding, Inventory
from .thresholds import DEFAULT_THRESHOLDS, Thresholds

_BYTES_PER_GB = 1e9

_SPACE_ACTION = (
    "Free space or grow the filesystem before it fills: a full filesystem fails writes, "
    "and a full ZFS pool also slows down."
)
_INODE_ACTION = (
    "Remove small files or rebuild it with more inodes: a filesystem out of inodes refuses new files "
    "while it still has free space."
)


def _severity_at(percent: int | None, hint: int, warning: int) -> tuple[Severity, int] | None:
    """The severity a fill earns and the limit that earned it, or ``None`` below both."""
    if percent is None:
        return None
    if percent >= warning:
        return Severity.WARNING, warning
    if percent >= hint:
        return Severity.HINT, hint
    return None


def _gigabytes(count: int) -> float:
    return count / _BYTES_PER_GB


def _on_disks(fs: Filesystem) -> str:
    """The ``, on sda`` clause naming the disks, or nothing when none resolved (a ZFS pool, a Windows volume)."""
    return f", on {', '.join(fs.disks)}" if fs.disks else ""


def _space_detail(fs: Filesystem) -> str:
    # Only reached when used_percent exists, so both figures were read.
    used = fs.used_bytes or 0
    free = fs.free_bytes or 0
    size = fs.size_bytes if fs.size_bytes is not None else used + free
    figures = f"{_gigabytes(used):.1f} GB of {_gigabytes(size):.1f} GB used, {_gigabytes(free):.1f} GB free"
    return f"{figures}{_on_disks(fs)}."


def _inode_detail(fs: Filesystem) -> str:
    total = fs.inodes_total or 0
    free = fs.inodes_free or 0
    return f"{total - free} of {total} inodes used, {free} free{_on_disks(fs)}."


def _space_finding(fs: Filesystem, thresholds: Thresholds) -> list[Finding]:
    verdict = _severity_at(
        fs.used_percent, thresholds.filesystem_full_hint_percent, thresholds.filesystem_full_warning_percent
    )
    if verdict is None:
        return []
    severity, limit = verdict
    return [
        Finding(
            severity=severity,
            subject=fs.name,
            title=f"{fs.name} is {fs.used_percent}% full, at or above the {limit}% {severity.value} threshold",
            detail=_space_detail(fs),
            action=_SPACE_ACTION,
        )
    ]


def _inode_finding(fs: Filesystem, thresholds: Thresholds) -> list[Finding]:
    verdict = _severity_at(
        fs.inodes_used_percent, thresholds.inodes_full_hint_percent, thresholds.inodes_full_warning_percent
    )
    if verdict is None:
        return []
    severity, limit = verdict
    return [
        Finding(
            severity=severity,
            subject=fs.name,
            title=(
                f"{fs.name} has used {fs.inodes_used_percent}% of its inodes, "
                f"at or above the {limit}% {severity.value} threshold"
            ),
            detail=_inode_detail(fs),
            action=_INODE_ACTION,
        )
    ]


def diagnose_filesystems(inventory: Inventory, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> list[Finding]:
    """Report filesystems and inode tables at or above their fill thresholds.

    Args:
        inventory: The machine to check; ``filesystems`` of ``None`` means none were read.
        thresholds: The judged figures, passed in so the domain reads no configuration itself.

    Returns:
        Findings for each space figure and each fixed inode table at or above a limit.

    Example:
        >>> from lsdsk.domain.models import FilesystemKind
        >>> full = Filesystem(kind=FilesystemKind.MOUNT, name="/", used_bytes=99, free_bytes=1)
        >>> [f.severity.value for f in diagnose_filesystems(Inventory(hostname="h", filesystems=(full,)))]
        ['warning']
    """
    findings: list[Finding] = []
    for fs in inventory.filesystems or ():
        if fs.read_only:
            continue
        findings.extend(_space_finding(fs, thresholds))
        findings.extend(_inode_finding(fs, thresholds))
    return findings


__all__ = ["diagnose_filesystems"]
