"""The machine-readable envelope for commands that act rather than report.

``ScanEnvelope`` carries a machine's inventory and its findings. The commands
that do something instead - write a capture, record a reading, deploy a config -
have no inventory to carry, and were human-only: they printed a sentence and
left a caller to parse it or guess from the exit code.

The two envelopes share their outer keys on purpose. ``ok`` says whether the
answer is complete, ``command`` names what produced it, and ``skipped`` says what
was not done and why, so one reader handles both without branching on shape.

System Role:
    Adapter-layer output boundary for the acting commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from . import safe_console

if TYPE_CHECKING:
    from collections.abc import Mapping


class ActionEnvelope(BaseModel):
    """What an acting command did, for a caller that is not a person.

    Attributes:
        ok: Whether the command did everything it was asked to.
        command: The command that produced this.
        data: What it did, already exported.
        skipped: What was not done, and why. Empty when nothing was.

    Example:
        >>> ActionEnvelope(ok=True, command="snapshot", data={"path": "/tmp/x"}).ok
        True
    """

    ok: bool
    command: str
    data: dict[str, Any]
    skipped: list[str] = []


def emit_action(command: str, data: BaseModel | Mapping[str, Any], skipped: list[str] | None = None) -> None:
    """Write an acting command's result as JSON.

    Args:
        command: The command emitting this.
        data: What it did, as the command's own result model. A plain mapping
            only where the payload is genuinely keyed by data rather than by a
            known set of fields.
        skipped: What it did not do, and why.
    """
    reasons = skipped or []
    # The one export this boundary performs. by_alias so a field renamed to
    # satisfy Pydantic still lands under the key a caller reads: SnapshotResult
    # cannot call its field `schema`, and the wire key is the contract.
    payload = data.model_dump(by_alias=True) if isinstance(data, BaseModel) else dict(data)
    envelope = ActionEnvelope(ok=not reasons, command=command, data=payload, skipped=reasons)
    safe_console.echo(envelope.model_dump_json(indent=2))


__all__ = ["ActionEnvelope", "emit_action"]
