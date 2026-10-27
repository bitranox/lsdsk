"""Port contract tests.

What each port PROMISES is a protocol, and ``adapters/memory/__init__.py`` asserts
every in-memory implementation against it under ``TYPE_CHECKING``, so pyright
proves that conformance before any test runs. Two tests here restated it at
runtime against the stubs alone and could only fail if someone edited a stub.

What a type checker cannot see is a container that left a field unwired, which is
what remains.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, get_type_hints

import pytest

from lsdsk.application import ports as port_module
from lsdsk.composition import AppServices, build_production, build_testing

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.os_agnostic
@pytest.mark.parametrize("build", [build_production, build_testing])
def test_every_service_container_is_fully_populated_and_callable(build: Callable[[], AppServices]) -> None:
    """Verify both wirings fill every port with what its protocol promises.

    A port is either one call (a protocol with ``__call__``), which must be
    callable, or an object with steps (the startup line), each of whose
    protocol methods must be there and callable.
    """
    services = build()
    # The container names its ports under TYPE_CHECKING only, so they resolve from the module that declares them.
    ports = get_type_hints(AppServices, globalns=dict(vars(port_module)))

    assert isinstance(services, AppServices)
    for field in AppServices.__dataclass_fields__:
        implementation = getattr(services, field)
        assert implementation is not None, f"{field} was left unwired"
        for step in _steps_of(ports[field]):
            assert callable(getattr(implementation, step, None)), f"{field} has no callable {step}"


def _steps_of(port: type) -> list[str]:
    """The methods a port's protocol declares, or ``__call__`` for a one-call port."""
    if "__call__" in vars(port):
        return ["__call__"]
    return [name for name, member in vars(port).items() if callable(member) and not name.startswith("_")]


def test_an_object_port_is_held_to_each_of_its_steps() -> None:
    """The loop above checks the steps this enumeration names, so it must name every one."""
    from lsdsk.application.ports import ShowsStartupProgress

    assert _steps_of(ShowsStartupProgress) == ["start", "pause", "resume", "stop"]
