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
import sys
import types
from pathlib import Path

import pytest

from lsdsk.adapters.hw import windows as windows_package
from lsdsk.adapters.hw.windows import winapi as api

#: The ctypes names whose size follows the running machine's word, so a field
#: declared with one sizes itself differently on Windows' LLP64 than on the LP64
#: that Linux and macOS use. Matched by NAME in the source, never by the class:
#: the fixed-width names are aliases of these same classes on one platform or
#: the other, so the class cannot tell the trap from the correct declaration.
#: ``c_wchar`` is the C ``wchar_t``: two bytes on Windows and four on Linux and
#: macOS, so a WCHAR array declared with it is twice the size off Windows.
FOLLOWING_THE_MACHINE = frozenset({"c_ulong", "c_long", "c_ulonglong", "c_longlong", "c_size_t", "c_wchar"})

#: The ``ctypes.wintypes`` names, every one taken from the running machine.
FROM_WINTYPES = frozenset(
    {"DWORD", "ULONG", "BOOL", "BOOLEAN", "WORD", "USHORT", "UINT", "INT", "LONG", "ULONG64", "WCHAR"}
)

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
    "STORAGE_TEMPERATURE_DATA_DESCRIPTOR": 40,
    "STORAGE_TEMPERATURE_INFO": 16,
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
def test_the_sweep_can_still_find_what_it_searches_for() -> None:
    """Its control: the sweep reads one declaration per structure, and the names it looks for exist.

    Without this the sweep passes just as well against files it failed to read,
    a declaration spelled in a form it does not parse, or a ctypes that renamed
    these, which is the same green for the opposite reason.
    """
    declarations = sum(len(_fields_declarations(source)) for source in _windows_sources().values())
    assert declarations == len(_windows_structures())
    assert all(hasattr(ctypes, name) for name in FOLLOWING_THE_MACHINE)


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


def _names_fields(target: ast.expr) -> bool:
    """Whether an assignment target is ``_fields_`` - in a class body, or set on the class afterwards."""
    return getattr(target, "id", "") == "_fields_" or (isinstance(target, ast.Attribute) and target.attr == "_fields_")


def _fields_value(node: ast.AST) -> ast.expr | None:
    """The value ``node`` assigns to a structure's ``_fields_``, annotated or not; None for anything else."""
    if isinstance(node, ast.Assign) and any(_names_fields(target) for target in node.targets):
        return node.value
    if isinstance(node, ast.AnnAssign) and _names_fields(node.target):
        return node.value
    return None


#: What a top-level name in one module stands for: a dotted ``ctypes`` path such
#: as ``ctypes.c_ulong`` or ``ctypes.wintypes``, a sibling module of the package
#: as ``.winapi``, or the expression a module-level alias assigns.
_Binding = str | ast.expr


def _bindings(source: str) -> dict[str, _Binding]:
    """Every top-level import and plain assignment in ``source``, by the name it binds."""
    bound: dict[str, _Binding] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import ctypes.wintypes` binds `ctypes`; with `as` it binds the full path.
                bound[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            for alias in node.names:
                joined = f"{base}{alias.name}" if base.endswith(".") else f"{base}.{alias.name}"
                bound[alias.asname or alias.name] = joined
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            bound[node.targets[0].id] = node.value
    return bound


def _resolved(
    node: ast.expr, bound: dict[str, _Binding], siblings: dict[str, dict[str, _Binding]], depth: int = 0
) -> str | None:
    """The dotted path ``node`` stands for, followed through aliases and sibling modules; None where it leaves both.

    A name nobody bound resolves to itself, so a bare ``c_ulong`` with no import
    in sight - the planted controls - is still judged by its spelling.
    """
    if depth > len(bound) + 8:
        return None
    if isinstance(node, ast.Name):
        binding = bound.get(node.id, node.id)
        if isinstance(binding, str):
            return _into_sibling(binding, siblings, depth)
        return _resolved(binding, bound, siblings, depth + 1)
    if not isinstance(node, ast.Attribute):
        return None
    base = _resolved(node.value, bound, siblings, depth + 1)
    return None if base is None else _into_sibling(f"{base}.{node.attr}", siblings, depth)


def _into_sibling(path: str, siblings: dict[str, dict[str, _Binding]], depth: int) -> str | None:
    """Follow a relative ``.module.name`` path into that sibling's own bindings; any other path is returned as is."""
    if not path.startswith("."):
        return path
    module, _, name = path.lstrip(".").partition(".")
    if module not in siblings or not name:
        return path
    sibling = siblings[module]
    return _resolved(ast.Name(id=name), sibling, siblings, depth + 1) if name in sibling else None


def _is_machine_width(path: str) -> bool:
    """Whether a resolved dotted path names a ctypes type whose width follows the running machine."""
    module, _, name = path.rpartition(".")
    if module in {"ctypes", ""}:
        return name in FOLLOWING_THE_MACHINE
    return module in {"ctypes.wintypes", "wintypes"} and name in FROM_WINTYPES


def _under_a_pointer(fields: ast.expr) -> set[int]:
    """The ids of every node inside a ``POINTER(...)`` call, which is the machine's word by definition."""
    return {
        id(under)
        for call in ast.walk(fields)
        if isinstance(call, ast.Call) and getattr(call.func, "attr", getattr(call.func, "id", "")) == "POINTER"
        for under in ast.walk(call)
    }


def _spelled_machine_width(
    node: ast.Name | ast.Attribute, bound: dict[str, _Binding], siblings: dict[str, dict[str, _Binding]]
) -> str | None:
    """How ``node`` is spelled, plus what it resolves to where that differs, when it names a machine-width type."""
    path = _resolved(node, bound, siblings)
    if path is None or not _is_machine_width(path):
        return None
    spelling = ast.unparse(node)
    return spelling if path == spelling else f"{spelling} ({path})"


def _offending_fields(source: str, *, origin: str, siblings: dict[str, str] | None = None) -> list[str]:
    """Every non-pointer ``_fields_`` entry in ``source`` naming a machine-width type.

    Judged by SPELLING, never by the class a field resolves to at runtime: ctypes
    defines its fixed-width names as aliases of the platform's own C types, so
    ``c_uint32 is c_ulong`` on Windows and ``c_uint64 is c_ulong`` on Linux, and
    an identity check flags every correct ``ULONG = c_uint32`` field on one
    platform or every 64-bit one on the other.

    The spelling is followed through the module's own imports and top-level
    aliases first, and into ``siblings`` (the package's other modules, by name)
    for a relative import, because the name inside ``_fields_`` is rarely the
    one that decides the width: ``_F = ctypes.c_ulong`` or ``from ctypes
    import c_ulong as U32`` put the trap one line away from the field.
    """
    bound = _bindings(source)
    others = {name: _bindings(text) for name, text in (siblings or {}).items()}
    offenders: list[str] = []
    for fields in _fields_declarations(source):
        exempt = _under_a_pointer(fields)
        for inner in ast.walk(fields):
            if not isinstance(inner, ast.Name | ast.Attribute) or id(inner) in exempt:
                continue
            spelling = _spelled_machine_width(inner, bound, others)
            if spelling is not None:
                offenders.append(f"{origin}:{inner.lineno}: {spelling}")
    return offenders


def _fields_declarations(source: str) -> list[ast.expr]:
    """The value of every ``_fields_`` assignment in ``source``, in walk order."""
    return [fields for node in ast.walk(ast.parse(source)) if (fields := _fields_value(node)) is not None]


def _windows_sources() -> dict[Path, str]:
    """The source of every module under ``adapters/hw/windows/``, by path."""
    package_dir = Path(windows_package.__file__).parent
    return {path: path.read_text(encoding="utf-8") for path in sorted(package_dir.rglob("*.py"))}


def _layouts_defined_in(module: types.ModuleType) -> list[type[ctypes.Structure | ctypes.Union]]:
    """Every ``ctypes.Structure`` or ``ctypes.Union`` class ``module`` itself defines.

    A union carries ``_fields_`` and a layout exactly as a structure does, so a
    count of structures alone let one stand outside the coverage control. Only
    classes the module DEFINES are kept (``__module__`` matches), so a layout
    imported from ``winapi`` into another module's namespace is not counted twice.
    """
    return [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, ctypes.Structure | ctypes.Union)
        and value.__module__ == module.__name__
    ]


def _windows_structures() -> list[type[ctypes.Structure | ctypes.Union]]:
    """Every ``ctypes.Structure`` or ``ctypes.Union`` this project defines under ``adapters/hw/windows/``.

    Imports each module in the package rather than reading one file's text, so a
    structure declared in a file winapi.py's own AST sweep never looks at - such
    as ``windows/reader.py``'s ``_SatRequest`` - is swept automatically, and so
    is one added in a future file.
    """
    package_dir = Path(windows_package.__file__).parent
    structures: list[type[ctypes.Structure | ctypes.Union]] = []
    for info in pkgutil.iter_modules([str(package_dir)]):
        structures.extend(_layouts_defined_in(importlib.import_module(f"{windows_package.__name__}.{info.name}")))
    return structures


@pytest.mark.os_agnostic
def test_no_field_of_any_windows_structure_takes_its_width_from_the_running_machine() -> None:
    """The sweep over every structure under adapters/hw/windows/, not only winapi.py.

    ``windows/reader.py`` declares its own ``ctypes.Structure`` (``_SatRequest``),
    whose ``filler`` field has to be ``api.ULONG`` - a fixed four bytes - rather
    than ``ctypes.c_ulong``, which is eight on the LP64 that Linux and macOS use.
    A wider filler there shifts the sense and data buffer offsets the structure
    hands to ``DeviceIoControl`` exactly as a wrong field in winapi.py would.

    A field spelled ``wintypes.DWORD`` or ``ctypes.c_ulong`` reads as deliberate
    and is the whole defect. Pointer types are exempt, because a pointer IS the
    machine's word.
    """
    sources = _windows_sources()
    # Keyed by module name, so `from . import winapi as api` and `api.ULONG`
    # are judged by what winapi.py binds ULONG to, not by the spelling `api.ULONG`.
    siblings = {path.stem: source for path, source in sources.items()}
    offenders = [
        offender
        for path, source in sources.items()
        for offender in _offending_fields(source, origin=path.name, siblings=siblings)
    ]
    assert not offenders, "a field whose width follows the running machine: " + "; ".join(offenders)


@pytest.mark.os_agnostic
def test_the_general_sweep_reaches_every_structure_the_package_defines() -> None:
    """Its control for coverage: every module that defines a structure is a module whose source is read."""
    structures = _windows_structures()
    assert {structure.__name__ for structure in structures} >= set(FIXED_SIZES) | set(POINTER_BEARING) | {"_SatRequest"}

    defining = {Path(sys.modules[structure.__module__].__file__ or "").resolve() for structure in structures}
    assert defining <= {path.resolve() for path in _windows_sources()}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "spelling",
    ["ctypes.c_ulong", "ctypes.c_long", "ctypes.c_size_t", "wintypes.DWORD", "wintypes.ULONG", "c_ulong"],
)
def test_the_general_sweep_still_catches_a_planted_offender(spelling: str) -> None:
    """Its control for detection: a structure built like ``_SatRequest`` with the bug restored."""
    source = f'class _Offender:\n    _fields_ = (("filler", {spelling}),)\n'
    assert _offending_fields(source, origin="planted") == [f"planted:2: {spelling}"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("width", [f"c_{sign}int{bits}" for sign in ("u", "") for bits in (8, 16, 32, 64)])
def test_the_general_sweep_never_flags_a_fixed_width_field(width: str) -> None:
    """Its control for the opposite direction, on every platform at once.

    ctypes makes ``c_uint32`` the very same class as ``c_ulong`` on Windows and
    ``c_uint64`` the same as ``c_ulong`` on Linux, so a sweep keyed on the class
    flags these there. Measured: every ``ULONG`` field in the package was
    reported on all four Windows CI cells while Linux stayed green.
    """
    source = f'class _Fixed:\n    _fields_ = (("count", ctypes.{width}), ("filler", api.ULONG))\n'
    assert _offending_fields(source, origin="planted") == []


@pytest.mark.os_agnostic
def test_the_general_sweep_does_not_flag_a_pointer_field() -> None:
    """Its control for the exception: a pointer field must stay exempt in the general sweep too."""
    source = 'class _Clean:\n    _fields_: tuple = (("reserved", ctypes.POINTER(ctypes.c_ulong)),)\n'
    assert _offending_fields(source, origin="planted") == []


#: Each way a module can bring a machine-width type into a ``_fields_`` entry
#: under another name. The sweep judged the spelling INSIDE ``_fields_`` only, so
#: every one of these read as clean: the name it saw was ``_F``, ``U32``, a bare
#: ``DWORD`` or ``wt.DWORD``, none of which is on either list.
_FIELD = "class _O:\n    _fields_ = (('f', {}),)\n"
_ALIASED_OFFENDERS = {
    "a module-level alias": "import ctypes\n_F = ctypes.c_ulong\n" + _FIELD.format("_F"),
    "an alias of an alias": "import ctypes\n_A = ctypes.c_ulong\n_B = _A\n" + _FIELD.format("_B"),
    "a renamed ctypes import": "from ctypes import c_ulong as U32\n" + _FIELD.format("U32"),
    "a bare wintypes import": "from ctypes.wintypes import DWORD\n" + _FIELD.format("DWORD"),
    "a renamed wintypes module": "from ctypes import wintypes as wt\n" + _FIELD.format("wt.DWORD"),
    "a dotted wintypes import": "import ctypes.wintypes as wt\n" + _FIELD.format("wt.ULONG"),
    "a renamed ctypes module": "import ctypes as C\n" + _FIELD.format("C.c_ulong"),
    "a wide character array": "import ctypes\n" + _FIELD.format("ctypes.c_wchar * 8"),
    "a wintypes wide character": "from ctypes import wintypes\n" + _FIELD.format("wintypes.WCHAR"),
    "fields assigned after the class": "import ctypes\nclass _O:\n    pass\n_O._fields_ = (('f', ctypes.c_ulong),)\n",
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("source", list(_ALIASED_OFFENDERS.values()), ids=list(_ALIASED_OFFENDERS))
def test_the_general_sweep_resolves_a_name_before_judging_it(source: str) -> None:
    """Its control for indirection: the trap is caught however the name reached the field."""
    field_line = source.rstrip("\n").count("\n") + 1
    offenders = _offending_fields(source, origin="planted")
    assert len(offenders) == 1, f"expected one offender, got {offenders}"
    assert offenders[0].startswith(f"planted:{field_line}: "), offenders


@pytest.mark.os_agnostic
def test_the_general_sweep_follows_a_name_into_a_sibling_module() -> None:
    """``api.ULONG`` is only as fixed as winapi.py declares it, so the sweep reads that declaration."""
    winapi = "import ctypes\nULONG = ctypes.c_ulong\n"
    source = (
        "import ctypes\nfrom . import winapi as api\nclass _O(ctypes.Structure):\n    _fields_ = (('f', api.ULONG),)\n"
    )
    fixed = "import ctypes\nULONG = ctypes.c_uint32\n"

    assert _offending_fields(source, origin="planted", siblings={"winapi": winapi}) == [
        "planted:4: api.ULONG (ctypes.c_ulong)"
    ]
    assert _offending_fields(source, origin="planted", siblings={"winapi": fixed}) == []


@pytest.mark.os_agnostic
def test_a_union_is_counted_among_the_layouts_the_sweep_must_reach() -> None:
    """A ``ctypes.Union`` has ``_fields_`` and a layout too, and was not counted at all."""
    module = types.ModuleType("planted")

    class _Overlay(ctypes.Union):
        _fields_ = (("word", ctypes.c_uint32), ("bytes", ctypes.c_uint8 * 4))

    class _Record(ctypes.Structure):
        _fields_ = (("word", ctypes.c_uint32),)

    _Overlay.__module__ = _Record.__module__ = module.__name__
    vars(module).update(_Overlay=_Overlay, _Record=_Record)

    assert {layout.__name__ for layout in _layouts_defined_in(module)} == {"_Overlay", "_Record"}


@pytest.mark.os_agnostic
def test_the_sat_request_buffers_sit_where_the_sdk_sample_puts_them() -> None:
    """``_SatRequest`` is the SDK sample's SCSI_PASS_THROUGH_WITH_BUFFERS: the request, a ULONG filler, the buffers.

    The buffered IOCTL reads the sense and data buffers at the offsets the
    request NAMES, so these two offsets are where the drive answers. On x64
    Windows the request is 56 bytes, so the filler sits at 56, the 32-byte sense
    buffer at 60 and the data at 92. Stated as the arithmetic so a 32-bit runner
    asserts its own figures, and as the x64 numbers where the word is 8.
    """
    # Reached by name through the same discovery the sweep uses: the layout is
    # private to reader.py, and the subject here is exactly that private layout.
    layout = next(found for found in _windows_structures() if found.__name__ == "_SatRequest")

    def offset(field: str) -> int:
        return int(getattr(layout, field).offset)

    request = ctypes.sizeof(api.SCSI_PASS_THROUGH)
    assert offset("filler") == request
    assert offset("sense") == request + 4
    assert offset("data") == request + 4 + 32
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert (offset("sense"), offset("data")) == (60, 92)


@pytest.mark.os_agnostic
def test_the_temperature_property_is_the_id_the_sdk_assigns() -> None:
    """STORAGE_PROPERTY_ID runs 0..17 then 48..; StorageDeviceTemperatureProperty is 52, and 24 is undefined."""
    assert api.STORAGE_DEVICE_TEMPERATURE_PROPERTY == 52


@pytest.mark.os_agnostic
def test_a_temperature_sensor_starts_after_the_24_byte_header_the_sdk_declares() -> None:
    assert api.STORAGE_TEMPERATURE_DATA_DESCRIPTOR.TemperatureInfo.offset == 24
