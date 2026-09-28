"""A copy of a domain value answers from its own data, never from its original's index.

Some domain models keep an index as a ``functools.cached_property``: the first
lookup builds it and stores it in the instance's ``__dict__``. Pydantic's
``model_copy`` copies that ``__dict__`` whole, so a copy made AFTER the index
was warmed carried it along, and with ``update=`` the copy's fields and its index
described two different values: a copied ``History`` answered ``for_identity``
from the series it no longer held.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lsdsk.adapters.hw.snapshot import load
from lsdsk.domain.history import DiskSeries, History

SNAPSHOT = Path(__file__).parent / "fixtures" / "hw" / "linux-sas-hba.json"


@pytest.mark.os_agnostic
def test_a_copied_history_answers_from_the_series_it_was_given() -> None:
    original = History(hostname="box", series=(DiskSeries(identity="naa.1", model="old"),))
    warmed = original.for_identity("naa.1")
    assert warmed is not None and warmed.model == "old", "the control: the original must answer first"

    copy = original.model_copy(update={"series": (DiskSeries(identity="naa.1", model="new"),)})

    answered = copy.for_identity("naa.1")
    assert answered is not None and answered.model == "new", "the copy answered from its original's index"
    assert original.for_identity("naa.1") == warmed, "copying changed the original"


@pytest.mark.os_agnostic
def test_a_copied_inventory_answers_from_the_disks_it_was_given() -> None:
    inventory = load(SNAPSHOT)
    address = next(controller.address for controller in inventory.controllers if inventory.disks_on(controller.address))

    emptied = inventory.model_copy(update={"disks": ()})

    assert emptied.disks_on(address) == (), "the copy answered from its original's disk index"
    assert inventory.disks_on(address), "copying changed the original"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("deep", [False, True])
def test_a_plain_copy_carries_no_cached_index_either(deep: bool) -> None:
    """Without ``update`` the copy's data is the original's, but it still builds its own index.

    Otherwise the rule "a copy never carries a stale index" would hold only
    while nobody changes a copy afterwards by any other route.
    """
    original = History(hostname="box", series=(DiskSeries(identity="naa.1", model="X"),))
    original.for_identity("naa.1")
    assert "_series_by_identity" in original.__dict__, "the control: the index was never warmed"

    copy = original.model_copy(deep=deep)

    assert "_series_by_identity" not in copy.__dict__, "the copy carried its original's index"
    assert copy.for_identity("naa.1") == original.for_identity("naa.1")


@pytest.mark.os_agnostic
def test_a_copy_refuses_a_field_the_model_does_not_have() -> None:
    """``update=`` goes through validation, as ``with_changes`` does, so a typo is refused."""
    with pytest.raises(ValidationError, match="seires"):
        History(hostname="box").model_copy(update={"seires": ()})
