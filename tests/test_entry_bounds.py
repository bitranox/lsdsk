"""Every collection in a file from outside the tool is bounded in ENTRIES, not only in bytes.

The file ceiling bounds bytes, and a parsed document costs memory and time in
proportion to its entry count with a constant of a few hundred: 200,000 empty
PCI entries in a 4 MB capture made ``findings`` take 7 s and 703 MB. So every
collection a capture or a history store declares carries the entry bound, and
the bound is checked before a single entry is validated.

Two kinds of test, because each misses what the other holds. The census walks
the type graph from the ROOT models, recursively through nested models of any
kind, and requires the bound on every collection it finds - so a collection
added later cannot arrive unbounded. The behavioural arms hold the boundary
itself at exactly the limit and one past it, through the real loaders, with
entries as cheap as a model accepts.
"""

from __future__ import annotations

import errno
import json
import types
import typing
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

from lsdsk.adapters.history.store import HistoryFile, load_history, save_history
from lsdsk.adapters.hw import snapshot
from lsdsk.adapters.hw.linux.capture import LinuxCapture
from lsdsk.adapters.hw.windows.capture import WindowsCapture
from lsdsk.adapters.validation import BOUNDED, MAX_ENTRIES
from lsdsk.domain.errors import ConfigurationError
from lsdsk.domain.history import DiskSeries, History, Sample

if TYPE_CHECKING:
    from pathlib import Path

_COLLECTIONS = (dict, tuple, list, set, frozenset)
_UNIONS = (Union, types.UnionType)
HEADER: dict[str, Any] = {"schema": 2, "platform": "linux", "hostname": "box", "kernel": "6.1"}


def _unbounded_collections(model: type[BaseModel], seen: set[type[BaseModel]]) -> list[str]:
    """Every collection reachable from ``model`` that does not carry :data:`BOUNDED`."""
    if model in seen:
        return []
    seen.add(model)
    found: list[str] = []
    for name, field in model.model_fields.items():
        found += _walk(field.annotation, f"{model.__name__}.{name}", bounded=BOUNDED in field.metadata, seen=seen)
    return found


def _walk(annotation: Any, where: str, *, bounded: bool, seen: set[type[BaseModel]]) -> list[str]:
    """One annotation, and every type nested in it."""
    origin = get_origin(annotation)
    if origin is typing.Annotated:
        inner, *metadata = get_args(annotation)
        return _walk(inner, where, bounded=bounded or BOUNDED in metadata, seen=seen)
    if origin in _UNIONS:
        return [found for arg in get_args(annotation) for found in _walk(arg, where, bounded=bounded, seen=seen)]
    if origin in _COLLECTIONS:
        found = [] if bounded else [where]
        for arg in get_args(annotation):
            if arg is not Ellipsis:
                found += _walk(arg, f"{where}[]", bounded=False, seen=seen)
        return found
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _unbounded_collections(annotation, seen)
    return []


@pytest.mark.os_agnostic
@pytest.mark.parametrize("root", [LinuxCapture, WindowsCapture])
def test_every_collection_a_capture_declares_is_bounded(root: type[BaseModel]) -> None:
    assert _unbounded_collections(root, set()) == []


@pytest.mark.os_agnostic
def test_the_census_finds_an_unbounded_collection_nested_in_a_model_of_another_kind() -> None:
    """The control: a plain collection two models down is reported, so the census can fail."""

    class Leaf(BaseModel):
        items: tuple[str, ...] = ()

    class Root(BaseModel):
        leaf: Leaf | None = None

    assert _unbounded_collections(Root, set()) == ["Leaf.items"]


@pytest.mark.os_agnostic
def test_a_history_store_bounds_its_series_and_each_series_samples() -> None:
    """The store's series are declared by the domain, so the bound sits on the file model."""
    assert BOUNDED in HistoryFile.model_fields["series"].metadata


def _pci(count: int) -> dict[str, Any]:
    return {f"0000:{index // 256 % 256:02x}:{index % 256 // 8:02x}.{index % 8}#{index}": {} for index in range(count)}


@pytest.mark.os_agnostic
def test_a_capture_map_of_exactly_the_limit_is_read() -> None:
    parsed = snapshot.parse_capture({**HEADER, "pci": _pci(MAX_ENTRIES)})
    assert isinstance(parsed, LinuxCapture)
    assert len(parsed.pci) == MAX_ENTRIES


@pytest.mark.os_agnostic
def test_a_capture_map_one_past_the_limit_is_refused_in_this_tool_s_words(tmp_path: Path) -> None:
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps({**HEADER, "pci": _pci(MAX_ENTRIES + 1)}), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=rf"pci: holds {MAX_ENTRIES + 1} entries, more than the {MAX_ENTRIES}"):
        snapshot.load(capture)


@pytest.mark.os_agnostic
def test_a_capture_list_one_past_the_limit_is_refused() -> None:
    """A tuple is a collection too: one device's children can carry as much as a map."""
    device = {"children": ["x"] * (MAX_ENTRIES + 1)}
    with pytest.raises(ValueError, match=rf"children\n.*holds {MAX_ENTRIES + 1} entries"):
        snapshot.parse_capture({**HEADER, "pci": {"0000:00:00.0": device}})


def _store(path: Path, series: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"schema": 1, "hostname": "box", "series": series}), encoding="utf-8")
    return path


def _series(identity: str, samples: int = 0) -> dict[str, Any]:
    return {
        "identity": identity,
        "model": "m",
        "samples": [{"power_on_hours": hour, "captured_at": ""} for hour in range(samples)],
    }


@pytest.mark.os_agnostic
def test_a_history_store_of_exactly_the_limit_in_series_and_samples_is_read(tmp_path: Path) -> None:
    series = [_series(str(index)) for index in range(MAX_ENTRIES - 1)] + [_series("long", MAX_ENTRIES)]
    loaded = load_history(_store(tmp_path / "history.json", series), hostname="box", cap=MAX_ENTRIES)
    assert len(loaded.series) == MAX_ENTRIES
    assert len(loaded.series[-1].samples) == MAX_ENTRIES


@pytest.mark.os_agnostic
def test_a_history_store_one_series_past_the_limit_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "history.json", [_series(str(index)) for index in range(MAX_ENTRIES + 1)])
    with pytest.raises(ConfigurationError, match=rf"series: holds {MAX_ENTRIES + 1} entries"):
        load_history(store, hostname="box")


@pytest.mark.os_agnostic
def test_a_history_series_one_sample_past_the_limit_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "history.json", [_series("a"), _series("b", MAX_ENTRIES + 1)])
    with pytest.raises(ConfigurationError, match=rf"series: the series 'b' holds {MAX_ENTRIES + 1} samples"):
        load_history(store, hostname="box")


@pytest.mark.os_agnostic
def test_the_writer_refuses_a_store_with_more_series_than_the_reader_accepts(tmp_path: Path) -> None:
    """The writer holds the same bound, reported the way every failed write is."""
    store = tmp_path / "history.json"
    many = tuple(
        DiskSeries.model_construct(identity=str(index), model="m", samples=()) for index in range(MAX_ENTRIES + 1)
    )
    one = Sample(power_on_hours=1, captured_at="")

    with pytest.raises(OSError, match="not be a store the history reader accepts"):
        save_history(History.model_construct(hostname="box", series=many), store)
    assert not store.exists(), "a store its own reader would refuse was written"

    save_history(History(hostname="box", series=(DiskSeries(identity="a", model="m", samples=(one,)),)), store)
    assert load_history(store, hostname="box").series, "the control: an ordinary store must still be written"


@pytest.mark.os_agnostic
def test_the_writer_refuses_a_single_series_with_more_samples_than_the_reader_accepts(tmp_path: Path) -> None:
    """The per-series sample bound binds a ``DiskSeries`` built directly, not only a mapping read from a file.

    ``HistoryFile`` does not revalidate a ``DiskSeries`` instance it is handed -
    pydantic trusts a submodel it did not itself construct - so the non-mapping
    branch of ``_refuse_an_oversized_series``, which reads ``samples`` off the
    object with ``getattr`` before anything is validated, is the only guard on
    this path.
    """
    store = tmp_path / "history.json"
    one = Sample(power_on_hours=1, captured_at="")
    oversized = DiskSeries.model_construct(identity="a", model="m", samples=(one,) * (MAX_ENTRIES + 1))

    with pytest.raises(OSError) as excinfo:
        save_history(History.model_construct(hostname="box", series=(oversized,)), store)

    assert excinfo.value.errno == errno.EINVAL, f"a refused write left errno {excinfo.value.errno}"
    assert not store.exists(), "a series its own reader would refuse was written"


@pytest.mark.os_agnostic
def test_the_writers_refusal_quotes_an_oversized_identity_escaped_and_cut(tmp_path: Path) -> None:
    """The reason a refused write reports stays inert and short, even when the identity itself is not.

    ``_refuse_an_oversized_series`` builds its message around whatever
    ``str(identity)`` hands it, unescaped and uncut. What reaches the caller is
    whatever ``what_is_wrong_with_it`` renders from the resulting
    ``ValidationError``, so this pins that the escaping and the length cut both
    still apply there, not only on a hand-edited file.
    """
    store = tmp_path / "history.json"
    one = Sample(power_on_hours=1, captured_at="")
    evil_identity = "\x1b]8;;http://example.invalid\x1b\\" + "A" * 10_000
    oversized = DiskSeries.model_construct(identity=evil_identity, model="m", samples=(one,) * (MAX_ENTRIES + 1))

    with pytest.raises(OSError) as excinfo:
        save_history(History.model_construct(hostname="box", series=(oversized,)), store)

    message = str(excinfo.value)
    assert "\x1b" not in message, f"a raw escape sequence reached the caller: {message!r}"
    assert len(message) < 1000, f"the oversized identity was not cut: {len(message)} characters"
