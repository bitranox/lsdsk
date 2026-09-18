"""Domain enum tests: member values, and the string form that reaches output."""

from __future__ import annotations

import enum
import pytest

from lsdsk.domain import enums as enums_module
from lsdsk.domain.enums import DeployTarget, OutputFormat

# ======================== OutputFormat ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (OutputFormat.HUMAN, "human"),
        (OutputFormat.JSON, "json"),
    ],
)
def test_output_format_member_values(member: OutputFormat, expected_value: str) -> None:
    """Each OutputFormat member must have the expected string value."""
    assert member.value == expected_value


# ======================== DeployTarget ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (DeployTarget.APP, "app"),
        (DeployTarget.HOST, "host"),
        (DeployTarget.USER, "user"),
    ],
)
def test_deploy_target_member_values(member: DeployTarget, expected_value: str) -> None:
    """Each DeployTarget member must have the expected string value."""
    assert member.value == expected_value


# ======================== every StrEnum ========================


def _str_enums() -> list[type[enum.StrEnum]]:
    """Every StrEnum the domain declares, found rather than listed.

    A hand-written list covers the enums that existed when it was written, and
    this is exactly the check a new enum needs on the day it is added.

    Returns:
        The StrEnum classes declared in :mod:`lsdsk.domain.enums`.
    """
    found = [
        value
        for value in vars(enums_module).values()
        if isinstance(value, type) and issubclass(value, enum.StrEnum) and value is not enum.StrEnum
    ]
    return sorted(found, key=lambda member: member.__name__)


@pytest.mark.os_agnostic
def test_the_domain_declares_the_enums_this_check_walks() -> None:
    """The control: a walk that found nothing would pass every check below."""
    assert len(_str_enums()) >= 10, "the enum walk found almost nothing, so the checks below prove nothing"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("declared", _str_enums(), ids=lambda declared: declared.__name__)
def test_a_members_string_form_is_its_value_not_its_name(declared: type[enum.StrEnum]) -> None:
    """An interpolated member must write its value, on every supported Python.

    This is the form that moved: ``f"{member}"`` gives the VALUE on 3.10 and
    ``CLASS.NAME`` on 3.11+ for a plain ``Enum``, and a member reaching a key, a
    filename, a log line or a rendered cell by interpolation is the way that
    difference corrupts data rather than raising. ``.value`` equality cannot see
    it, because it never interpolates.
    """
    for member in declared:
        assert f"{member}" == member.value, f"{declared.__name__}.{member.name} interpolates as {f'{member}'!r}"
        assert str(member) == member.value, f"{declared.__name__}.{member.name} str()s as {str(member)!r}"
