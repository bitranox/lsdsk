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

from typing import Any

from pydantic import BaseModel

from . import safe_console


class ActionEnvelope(BaseModel):
    """What an acting command did, for a caller that is not a person.

    Attributes:
        ok: Whether the command did everything it was asked to.
        command: The command that produced this.
        data: What it did, shaped by the command.
        skipped: What was not done, and why. Empty when nothing was.

    Example:
        >>> ActionEnvelope(ok=True, command="snapshot", data={"path": "/tmp/x"}).ok
        True
    """

    ok: bool
    command: str
    data: dict[str, Any]
    skipped: list[str] = []


def emit_action(command: str, data: dict[str, Any], skipped: list[str] | None = None) -> None:
    """Write an acting command's result as JSON.

    Args:
        command: The command emitting this.
        data: What it did.
        skipped: What it did not do, and why.
    """
    reasons = skipped or []
    envelope = ActionEnvelope(ok=not reasons, command=command, data=data, skipped=reasons)
    safe_console.echo(envelope.model_dump_json(indent=2))


__all__ = ["ActionEnvelope", "emit_action"]
