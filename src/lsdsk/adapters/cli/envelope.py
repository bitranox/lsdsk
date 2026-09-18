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

from pydantic import BaseModel, SerializeAsAny

from lsdsk.domain.enums import ActionCommand

from . import safe_console


class ActionResult(BaseModel, extra="forbid"):
    """What one acting command did, as that command's own fields.

    A base rather than a dict, so the envelope's ``data`` keeps the type of what
    was put in it and a caller reading ``deployed`` or ``recorded`` is checked
    against the model that carries it. Each acting command declares its own
    subclass beside the command itself, which is where the fields belong; this
    is only what they have in common.

    Fields are forbidden rather than ignored, so a payload built with a
    misspelled field name is refused here instead of reaching a caller with the
    key silently missing. The one payload whose keys are genuinely data - the
    user's own merged configuration - says so with :class:`MappingResult`.
    """


class MappingResult(ActionResult, extra="allow"):
    """A payload whose KEYS are data rather than a set of fields.

    Two jobs, and the second is easy to miss. It carries ``config``'s merged
    configuration, whose keys are the user's own sections and cannot be declared
    here. And it is what a caller gets when it parses an emitted envelope BACK
    through :class:`ActionEnvelope`: the base forbids fields it does not declare,
    so without this arm in the union, validating a real envelope fails on every
    key of its own payload - measured on the shipped ``config`` output, which
    the suite drives through the real entry point and parses back.

    What it does NOT do is recover which result model a payload came from.
    Allowing any extra key is what lets it stand in for every payload, and that
    is also what makes it match every payload: measured on pydantic 2.13.5, all
    five acting commands' results parse back as this class rather than as
    themselves. Recovering the concrete model would mean naming the arms here,
    and each one lives beside its own command and imports FROM this module, so
    the union cannot see them without inverting that. Parse-back therefore
    proves the envelope's SHAPE; to check a payload IS its command's model, name
    that model, as ``test_every_structured_mode_actually_emits_the_envelope``
    does.
    """


class ActionEnvelope(BaseModel):
    """What an acting command did, for a caller that is not a person.

    ``data`` is annotated ``SerializeAsAny`` because pydantic serialises a field
    by its DECLARED type: with the bare base it would emit ``{}`` for every
    payload, since the base declares no fields of its own, and both the type
    checker and a test asserting the outer keys would still pass.

    Attributes:
        ok: Whether the command did everything it was asked to.
        command: The command that produced this.
        data: What it did, as the command's own result model. On the way OUT
            that is exactly what is serialised; on the way back IN it arrives as
            :class:`MappingResult`, which is what that class's docstring
            explains.
        skipped: What was not done, and why. Empty when nothing was.

    Example:
        >>> class Wrote(ActionResult):
        ...     path: str
        >>> envelope = ActionEnvelope(ok=True, command=ActionCommand.SNAPSHOT, data=Wrote(path="/tmp/x"))
        >>> envelope.model_dump_json(by_alias=True)
        '{"ok":true,"command":"snapshot","data":{"path":"/tmp/x"},"skipped":[]}'
    """

    ok: bool
    command: ActionCommand
    data: SerializeAsAny[ActionResult] | MappingResult
    skipped: list[str] = []


def emit_action(command: ActionCommand, data: ActionResult, skipped: list[str] | None = None) -> None:
    """Write an acting command's result as JSON.

    Args:
        command: The command emitting this.
        data: What it did, as the command's own result model.
        skipped: What it did not do, and why.
    """
    reasons = skipped or []
    envelope = ActionEnvelope(ok=not reasons, command=command, data=data, skipped=reasons)
    # The one export this boundary performs, and it reaches the payload too
    # because the payload is nested rather than flattened on the way in.
    # by_alias so a field renamed to satisfy Pydantic still lands under the key
    # a caller reads: SnapshotResult cannot call its field `schema`, and the
    # wire key is the contract.
    safe_console.echo(envelope.model_dump_json(indent=2, by_alias=True))


__all__ = ["ActionEnvelope", "ActionResult", "MappingResult", "emit_action"]
