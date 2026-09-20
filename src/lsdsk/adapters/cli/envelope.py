"""The machine-readable envelope for commands that act rather than report.

``ScanEnvelope`` carries a machine's inventory and its findings. The commands
that do something instead - write a capture, record a reading, deploy a config -
have no inventory to carry, and were human-only: they printed a sentence and
left a caller to parse it or guess from the exit code.

The two envelopes share their outer keys on purpose. ``ok`` says whether the
answer is complete, ``command`` names what produced it, and ``skipped`` says what
was not done and why, so one reader handles both without branching on shape.

A third joins them for the case neither covers: a command that FAILED.
:class:`ErrorEnvelope` keeps ``ok`` and ``command`` and carries an ``error``
instead of data, so the same reader branches on the same key. Without it a
pipeline that asked for JSON received an empty stream, which it cannot tell from
a command that produced nothing, and the sentence explaining why went to stderr,
which is not the stream being parsed.

System Role:
    Adapter-layer output boundary for the acting commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import rich_click as click
from pydantic import BaseModel, SerializeAsAny

from lsdsk.domain.enums import ActionCommand, OutputFormat

from . import safe_console
from .exit_codes import ExitCode

if TYPE_CHECKING:
    from typing import NoReturn


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


#: What a command is called when its name cannot be read from the invocation.
#:
#: Only reachable when a helper is called outside a Click invocation, which is a
#: direct call from a test rather than anything a user can produce.
UNNAMED_COMMAND = "lsdsk"


class ErrorDetail(BaseModel, frozen=True, extra="forbid"):
    """A typed error name and the sentence that goes with it.

    ``type`` is the :class:`~.exit_codes.ExitCode` member's own name rather than
    a second vocabulary. One decider, two readers: the code a caller switches on
    and the name it reads can never disagree, and a new code cannot be added
    without its name arriving with it.
    """

    type: str
    message: str


class ErrorEnvelope(BaseModel, frozen=True, extra="forbid"):
    """The failure form of the machine-readable envelope.

    Its own model rather than optional fields on the success envelope, so a
    complete answer's shape is untouched and never grows a null ``error`` key.
    ``ok`` is what a reader branches on, which is the question it already
    answers for a partial success.

    ``command`` is a plain string rather than
    :class:`~lsdsk.domain.enums.CliCommand`, because that enum names the eight
    section commands alone and a failure can come from ``config``, ``snapshot``
    or ``config-deploy`` as well. On the wire the two agree: the enum serialises
    to exactly this string.
    """

    ok: bool = False
    command: str
    error: ErrorDetail


def invoked_command() -> str:
    """The name of the command being run, as the caller typed it.

    Read from Click's own context rather than threaded through every helper that
    can fail: the subcommand's context carries it already, and a parameter would
    have to reach a dozen call sites that have no other use for it.

    Returns:
        The subcommand name, or :data:`UNNAMED_COMMAND` outside an invocation.
    """
    context = click.get_current_context(silent=True)
    if context is None or context.info_name is None:
        return UNNAMED_COMMAND
    return context.info_name


def fail(message: str, code: ExitCode, *, output_format: OutputFormat, hint: str | None = None) -> NoReturn:
    """Report `message` and leave with `code`, answering in the caller's format.

    The sentence always goes to stderr, whatever the format: a person running the
    command by hand reads it there, and moving it into the envelope would fix the
    pipeline and break the person.

    The ``SystemExit`` carries no ``__cause__``. It is not observable - ``_run_cli``
    catches ``SystemExit`` ahead of its general handler and reads only ``.code``,
    so no traceback is ever rendered from it - and requiring every call site to
    pass the exception along would buy nothing.

    Args:
        message: The failure, as one sentence, with no ``Error:`` prefix: this
            adds that for the person and leaves it out for the machine.
        code: The exit code to leave with, whose name becomes the error type.
        output_format: What the caller asked for.
        hint: An extra line for the person only. It stays out of the envelope,
            which carries what went wrong rather than what to try next.

    Raises:
        SystemExit: Always, with `code`.

    Example:
        >>> from lsdsk.adapters.cli.exit_codes import ExitCode
        >>> from lsdsk.domain.enums import OutputFormat
        >>> try:
        ...     fail("nothing to read", ExitCode.CONFIG_ERROR, output_format=OutputFormat.HUMAN)
        ... except SystemExit as leaving:
        ...     int(leaving.code)
        78
    """
    safe_console.echo(f"Error: {message}", err=True)
    if hint is not None:
        safe_console.echo(hint, err=True)
    if output_format is OutputFormat.JSON:
        envelope = ErrorEnvelope(
            command=invoked_command(),
            error=ErrorDetail(type=ExitCode(code).name, message=message),
        )
        safe_console.echo(envelope.model_dump_json())
    raise SystemExit(code)


__all__ = [
    "UNNAMED_COMMAND",
    "ActionEnvelope",
    "ActionResult",
    "ErrorDetail",
    "ErrorEnvelope",
    "MappingResult",
    "emit_action",
    "fail",
    "invoked_command",
]
