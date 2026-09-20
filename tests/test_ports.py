"""Port contract tests.

What each port PROMISES is a protocol, and ``adapters/memory/__init__.py`` asserts
every in-memory implementation against it under ``TYPE_CHECKING``, so pyright
proves that conformance before any test runs. Two tests here restated it at
runtime against the stubs alone and could only fail if someone edited a stub.

What a type checker cannot see is a container that left a field unwired, which is
what remains.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lsdsk.composition import AppServices, build_production, build_testing

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.os_agnostic
@pytest.mark.parametrize("build", [build_production, build_testing])
def test_every_service_container_is_fully_populated_and_callable(build: Callable[[], AppServices]) -> None:
    """Verify both wirings fill every port with something callable."""
    services = build()

    assert isinstance(services, AppServices)
    for field in AppServices.__dataclass_fields__:
        implementation = getattr(services, field)
        assert implementation is not None, f"{field} was left unwired"
        assert callable(implementation), f"{field} is not callable"
