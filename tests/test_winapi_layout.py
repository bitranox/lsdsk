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
import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.hw import windows as windows_package
from lsdsk.adapters.hw.windows import winapi as api

if TYPE_CHECKING:
    from collections.abc import Iterable

WINAPI_SOURCE = Path(api.__file__)

#: The exact ctypes classes whose size follows the running machine's word, so a
#: field declared with one of these - or with the ``ctypes.wintypes`` alias of
#: one, which IS the same object (``wintypes.DWORD is ctypes.c_ulong``) - sizes
#: itself differently on Windows' LLP64 than on the LP64 that Linux and macOS
#: use. Checked by identity rather than by name, so the wintypes spelling needs
#: no separate case.
FOLLOWING_THE_MACHINE = {ctypes.c_ulong, ctypes.c_long, ctypes.c_ulonglong, ctypes.c_longlong, ctypes.c_size_t}

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


@pytest.mark.os_agnostic
def test_the_scsi_passthrough_request_carries_its_one_pointer_width_field_where_windows_does() -> None:
    """``DataBufferOffset`` is a ULONG_PTR, so it alone follows the machine.

    Measured on x64 Windows: the structure is 56 bytes. Stated as the layout
    rather than as 56, so a 32-bit runner asserts its own 44 instead of failing.
    """
    word = ctypes.sizeof(ctypes.c_void_p)
    offset = api.SCSI_PASS_THROUGH.DataBufferOffset.offset
    fields_before = 20

    assert offset == -(-fields_before // word) * word
    assert api.SCSI_PASS_THROUGH.SenseInfoOffset.offset == offset + word
    assert api.SCSI_PASS_THROUGH.Cdb.offset == offset + word + 4
    assert ctypes.sizeof(api.SCSI_PASS_THROUGH) == -(-(offset + word + 4 + 16) // word) * word
    if word == 8:
        assert ctypes.sizeof(api.SCSI_PASS_THROUGH) == 56


#: The USB hub IOCTLs by their function number, copied from the `#define USB_GET_*` lines of the
#: SDK's usbiodef.h (usbioctl.h holds only the CTL_CODE macros that use them). Every one is
#: CTL_CODE(FILE_DEVICE_USB, function, METHOD_BUFFERED, FILE_ANY_ACCESS). A number taken from
#: anywhere else is a guess this test cannot catch: SuperSpeedPlus is 289, and 286 - its
#: neighbour USB_GET_FRAME_NUMBER_AND_QPC_FOR_TIME_SYNC - answers ERROR_INVALID_PARAMETER on
#: every port, which reads exactly like a port that is not running SuperSpeedPlus.
USB_HUB_FUNCTIONS = {
    "IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION": 260,
    "IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX": 274,
    "IOCTL_USB_GET_HUB_INFORMATION_EX": 277,
    "IOCTL_USB_GET_PORT_CONNECTOR_PROPERTIES": 278,
    "IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2": 279,
    "IOCTL_USB_GET_NODE_CONNECTION_SUPERSPEEDPLUS_INFORMATION": 289,
}
FILE_DEVICE_USB = 0x22


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "function"), sorted(USB_HUB_FUNCTIONS.items()))
def test_a_usb_hub_ioctl_code_is_built_from_its_function_number(name: str, function: int) -> None:
    """Written out as hex in winapi.py, so each is held against the macro that defines it."""
    method_buffered, file_any_access = 0, 0
    expected = (FILE_DEVICE_USB << 16) | (file_any_access << 14) | (function << 2) | method_buffered
    assert getattr(api, name) == expected


def _is_pointer_type(field_type: type) -> bool:
    """Whether a ctypes field type is a pointer, which is the machine's word by definition.

    A pointer class is the one ctypes shape that carries a ``contents``
    attribute; a scalar type (even one sized like a pointer) and an array type
    do not, so this needs no private ``ctypes._Pointer`` reference.
    """
    return hasattr(field_type, "contents")


def _offending_fields(structures: Iterable[type[ctypes.Structure]]) -> list[str]:
    """Every non-pointer field of ``structures`` whose width follows the running machine."""
    offenders: list[str] = []
    for structure in structures:
        for field in structure._fields_:
            name, field_type = field[0], field[1]
            if _is_pointer_type(field_type):
                continue
            if field_type in FOLLOWING_THE_MACHINE:
                offenders.append(f"{structure.__module__}.{structure.__name__}.{name}")
    return offenders


def _windows_structures() -> list[type[ctypes.Structure]]:
    """Every ``ctypes.Structure`` this project defines under ``adapters/hw/windows/``.

    Imports each module in the package rather than reading one file's text, so a
    structure declared in a file winapi.py's own AST sweep never looks at - such
    as ``windows/reader.py``'s ``_SatRequest`` - is swept automatically, and so
    is one added in a future file. Only classes the module itself DEFINES are
    kept (``__module__`` matches), so a structure imported from ``winapi`` into
    another module's namespace is not counted twice.
    """
    package_dir = Path(windows_package.__file__).parent
    structures: list[type[ctypes.Structure]] = []
    for info in pkgutil.iter_modules([str(package_dir)]):
        module = importlib.import_module(f"{windows_package.__name__}.{info.name}")
        structures.extend(
            value
            for value in vars(module).values()
            if isinstance(value, type) and issubclass(value, ctypes.Structure) and value.__module__ == module.__name__
        )
    return structures


@pytest.mark.os_agnostic
def test_no_field_of_any_windows_structure_takes_its_width_from_the_running_machine() -> None:
    """The sweep over every structure under adapters/hw/windows/, not only winapi.py.

    ``windows/reader.py`` declares its own ``ctypes.Structure`` (``_SatRequest``),
    whose ``filler`` field has to be ``api.ULONG`` - a fixed four bytes - rather
    than ``ctypes.c_ulong``, which is eight on the LP64 that Linux and macOS use.
    A wider filler there shifts the sense and data buffer offsets the structure
    hands to ``DeviceIoControl`` exactly as a wrong field in winapi.py would.
    """
    offenders = _offending_fields(_windows_structures())
    assert not offenders, "a field whose width follows the running machine: " + "; ".join(offenders)


@pytest.mark.os_agnostic
def test_the_general_sweep_reaches_every_structure_the_file_based_one_names() -> None:
    """Its control for coverage: the discovery must not silently find nothing or drop a file."""
    discovered = {structure.__name__ for structure in _windows_structures()}
    assert discovered >= set(FIXED_SIZES) | set(POINTER_BEARING) | {"_SatRequest"}


@pytest.mark.os_agnostic
def test_the_general_sweep_still_catches_a_planted_offender() -> None:
    """Its control for detection: a structure built like ``_SatRequest`` with the bug restored."""

    class _Offender(ctypes.Structure):
        _fields_ = (("filler", ctypes.c_ulong),)

    offenders = _offending_fields([_Offender])
    assert len(offenders) == 1
    assert offenders[0].endswith("_Offender.filler")


@pytest.mark.os_agnostic
def test_the_general_sweep_does_not_flag_a_pointer_field() -> None:
    """Its control for the exception: a pointer field must stay exempt in the general sweep too."""

    class _Clean(ctypes.Structure):
        _fields_ = (("reserved", ctypes.POINTER(ctypes.c_ulong)),)

    assert _offending_fields([_Clean]) == []
