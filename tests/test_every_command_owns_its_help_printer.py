"""Every command prints its help through lsdsk's guarded sink, not rich-click's ``print``.

rich-click's own help callback writes straight to ``sys.stdout``, and under
``PYTHONUNBUFFERED`` a departed reader or a full disk then fails inside rich-click's
``main()``, which answers 1 and 28 where lsdsk answers 141 and 74. The fix replaces
the callback once over the assembled tree, so this walks the same tree and requires
the replacement on every command, which is what keeps a command added later covered.
"""

from __future__ import annotations

import pytest
import rich_click as click

from lsdsk.adapters.cli import root


def _every_command(command: click.Command, name: str) -> list[tuple[str, click.Command]]:
    found = [(name, command)]
    if isinstance(command, click.Group):
        for child_name, child in command.commands.items():
            found += _every_command(child, f"{name} {child_name}")
    return found


@pytest.mark.os_agnostic
def test_every_command_prints_its_help_through_the_guarded_sink() -> None:
    commands = _every_command(root.cli, "lsdsk")
    assert len(commands) > 10, f"the walk found {len(commands)} commands, so it read no tree"
    stray: list[str] = []
    for name, command in commands:
        ctx = click.Context(command, info_name=name.rsplit(" ", 1)[-1], **command.context_settings)
        option = command.get_help_option(ctx)
        assert option is not None, f"{name} has no help option to check"
        assert "-h" in option.opts, f"{name}: the walk built a context without the command's own settings"
        if option.callback is not root.show_help:
            stray.append(name)
    assert not stray, f"commands still printing help through rich-click's own print: {stray}"
