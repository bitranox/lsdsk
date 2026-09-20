"""Every Win32 structure has the layout Windows gives it, on every runner.

``ctypes.wintypes`` takes its integer widths from the platform the interpreter
is running on. ``DWORD`` there is ``c_ulong``: four bytes under Windows' LLP64
and eight under the LP64 that Linux and macOS use. So this module imported
cleanly off Windows, exactly as its docstrings promise, while computing a
different size for 12 of its 13 structures and a different offset for the one
field in the whole binding whose value was settled by testing against real
hardware.

The figures below are not this file's opinion. They were read from
``ctypes.sizeof`` on a real Windows machine, with the pre-change file and the
post-change file both loaded there in the same run: the two agreed on Windows
down to the byte, which is what makes this change a no-op there and a
correction everywhere else. On Linux before the change the same table read
GUID 24, SP_DEVINFO_DATA 48, STORAGE_PROTOCOL_SPECIFIC_DATA 80 and the offset
16.
"""

from __future__ import annotations

import ast
import ctypes
from pathlib import Path

import pytest

from lsdsk.adapters.hw.windows import winapi as api

WINAPI_SOURCE = Path(api.__file__)

#: Structures whose every field is a fixed width, so the size is the same
#: number on any machine Windows runs on.
FIXED_SIZES = {
    "ATA_PASS_THROUGH_DIRECT": 48,
    "DEVICE_SEEK_PENALTY_DESCRIPTOR": 12,
    "DEVPROPKEY": 20,
    "GUID": 16,
    "SCSI_ADDRESS": 8,
    "STORAGE_DEVICE_DESCRIPTOR": 36,
    "STORAGE_DEVICE_NUMBER": 12,
    "STORAGE_PROPERTY_QUERY": 12,
    "STORAGE_PROTOCOL_SPECIFIC_DATA": 40,
    "STORAGE_TEMPERATURE_DATA_DESCRIPTOR": 36,
    "STORAGE_TEMPERATURE_INFO": 10,
}

#: The two that carry a ``Reserved`` pointer, which genuinely follows the
#: machine's word size. Stated as the arithmetic rather than as 32, so the
#: assertion says WHY it is 32 on x64 and does not simply fail on anything else.
POINTER_BEARING = ("SP_DEVINFO_DATA", "SP_DEVICE_INTERFACE_DATA")
FIELDS_BEFORE_THE_POINTER = 24


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "expected"), sorted(FIXED_SIZES.items()))
def test_a_structure_is_the_size_windows_makes_it(name: str, expected: int) -> None:
    """Measured on Windows; asserted here so any runner catches a width slip."""
    assert ctypes.sizeof(getattr(api, name)) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", POINTER_BEARING)
def test_a_structure_carrying_a_reserved_pointer_is_its_fields_plus_one_pointer(name: str) -> None:
    """The one width that is allowed to follow the machine, and only it."""
    assert ctypes.sizeof(getattr(api, name)) == FIELDS_BEFORE_THE_POINTER + ctypes.sizeof(ctypes.c_void_p)


@pytest.mark.os_agnostic
def test_the_offset_real_hardware_settled_is_pinned_at_last() -> None:
    """The number a comment in the reader records, and nothing could check.

    ``windows/reader.py`` writes the ATA passthrough request at
    ``AdditionalParameters``, and its comment records that the offset is 8 and
    that at 11 every request is rejected. Off Windows that member sat at 16, so
    no runner this project has could hold the figure somebody paid for with a
    machine.
    """
    assert api.STORAGE_PROPERTY_QUERY.AdditionalParameters.offset == 8


@pytest.mark.os_agnostic
def test_the_widths_the_windows_abi_fixes_are_declared_fixed() -> None:
    """The direct claim, under the names the SDK uses for them."""
    widths = {"DWORD": 4, "ULONG": 4, "BOOL": 4, "WORD": 2, "USHORT": 2, "BOOLEAN": 1}
    measured = {name: ctypes.sizeof(getattr(api, name)) for name in widths}
    assert measured == widths


@pytest.mark.os_agnostic
def test_no_structure_field_takes_its_width_from_the_running_machine() -> None:
    """The trap itself, so it cannot be reintroduced by the next field added.

    A field spelled ``wintypes.DWORD`` or ``ctypes.c_ulong`` reads as deliberate
    and is the whole defect. Pointer types are exempt by name, because a pointer
    IS the machine's word.
    """
    following_the_machine = {"c_ulong", "c_long", "c_ulonglong", "c_longlong", "c_size_t"}
    from_wintypes = {"DWORD", "ULONG", "BOOL", "BOOLEAN", "WORD", "USHORT", "UINT", "INT", "LONG", "ULONG64"}
    offenders: list[str] = []

    for node in ast.walk(ast.parse(WINAPI_SOURCE.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_fields_" for t in node.targets)):
            continue
        # A pointer TO one of these is a pointer, and a pointer is the machine's
        # word by definition, so everything under a POINTER() is exempt.
        exempt = {
            id(under)
            for call in ast.walk(node.value)
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "POINTER"
            for under in ast.walk(call)
        }
        for inner in ast.walk(node.value):
            if not isinstance(inner, ast.Attribute) or id(inner) in exempt:
                continue
            if inner.attr in following_the_machine:
                offenders.append(f"line {inner.lineno}: ctypes.{inner.attr}")
            if getattr(inner.value, "id", "") == "wintypes" and inner.attr in from_wintypes:
                offenders.append(f"line {inner.lineno}: wintypes.{inner.attr}")

    assert not offenders, "a field whose width follows the running machine: " + "; ".join(offenders)


@pytest.mark.os_agnostic
def test_the_sweep_above_can_still_find_what_it_searches_for() -> None:
    """Its control: the names it looks for must still exist to be found.

    Without this the sweep passes just as well against a file it failed to read
    or a ctypes that renamed these, which is the same green for the opposite
    reason.
    """
    assert "_fields_" in WINAPI_SOURCE.read_text(encoding="utf-8")
    assert all(hasattr(ctypes, name) for name in ("c_ulong", "c_long", "c_void_p"))

    planted = ast.parse('class X:\n    _fields_ = (("a", ctypes.c_ulong),)\n')
    found = [
        inner.attr
        for node in ast.walk(planted)
        if isinstance(node, ast.Assign)
        for inner in ast.walk(node.value)
        if isinstance(inner, ast.Attribute) and inner.attr == "c_ulong"
    ]
    assert found == ["c_ulong"], "the sweep's own shape no longer matches a field it must catch"
