"""The configuration cache, tested for what this project wired rather than what the stdlib guarantees.

``functools.lru_cache`` has been thread-safe since Python 3.2, so asserting that
two threads reading it agree tests CPython, not lsdsk. What IS this project's
own is the plumbing around it: a ``cache_clear`` attached to a function object
by hand so the loader satisfies ``ConfigLoaderProtocol``, and a cache key that
has to keep two profiles apart. Both are invisible to the type checker at the
point of attachment, so both need a test that would notice them coming undone.

Every test here leaves the process-wide cache empty, because it is real shared
state: a stale entry left behind is inherited by whichever test file pytest
collects next.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from lsdsk.adapters.config.loader import (
    # The cache accounting IS what these tests assert on, and the loader exposes
    # no public accessor for it. Widening the production API so a test can read
    # cache_info would be a worse trade than this one narrow exemption.
    _get_config_impl,  # pyright: ignore[reportPrivateUsage] - remove when the loader exposes cache_info
    get_config,
    get_default_config_path,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def empty_cache_either_side() -> Iterator[None]:
    """Leave the shared cache as clean as it was found.

    The repo-wide ``clear_config_cache`` fixture deliberately only clears
    beforehand. This file populates the cache on purpose, so it has to clear
    afterwards too or it hands its entries to the next file alphabetically.
    """
    _get_config_impl.cache_clear()
    yield
    _get_config_impl.cache_clear()


@pytest.mark.os_agnostic
class TestGetDefaultConfigPath:
    """Where the shipped defaults are read from."""

    def test_returns_a_toml_that_exists(self) -> None:
        """A path that does not resolve to a real file makes every layer below it empty."""
        result = get_default_config_path()

        assert result.suffix == ".toml"
        assert result.is_file(), f"the shipped default config is missing at {result}"


@pytest.mark.os_agnostic
class TestTheHandAttachedCacheClear:
    """``get_config.cache_clear`` is assigned to a function object, not inherited."""

    def test_it_actually_empties_the_underlying_cache(self) -> None:
        """The attachment is behind a ``type: ignore``, so nothing else checks it.

        Pointing it at the wrong function, or dropping the assignment, leaves a
        ``cache_clear`` that is callable and does nothing - which every caller
        would read as success.
        """
        get_config()
        assert _get_config_impl.cache_info().currsize > 0, "the control: nothing was cached to clear"

        get_config.cache_clear()

        assert _get_config_impl.cache_info().currsize == 0

    def test_a_second_call_is_served_from_the_cache(self) -> None:
        """The reason the cache exists: one CLI run must not re-read every layer."""
        get_config()
        hits_before = _get_config_impl.cache_info().hits

        get_config()

        assert _get_config_impl.cache_info().hits == hits_before + 1


@pytest.mark.os_agnostic
class TestTheCacheKey:
    """Two profiles must not be served each other's configuration."""

    def test_a_different_profile_is_a_different_entry(self) -> None:
        """A key that ignored the profile would hand the default config to every profile.

        Asserted on the cache's own accounting rather than on the returned
        values, because two profiles that happen to resolve to the same
        configuration on this machine would make a value comparison pass either
        way.
        """
        get_config(profile=None)
        assert _get_config_impl.cache_info().currsize == 1

        get_config(profile="test")

        assert _get_config_impl.cache_info().currsize == 2, "the profile is not part of the cache key"

    def test_the_same_profile_twice_is_one_entry(self) -> None:
        """The other direction: a key including something volatile would never hit."""
        get_config(profile="test")
        get_config(profile="test")

        assert _get_config_impl.cache_info().currsize == 1


@pytest.mark.os_agnostic
class TestConcurrentAccess:
    """Clearing the cache while other threads read it.

    Kept where the plain concurrent-read tests were deleted, because this one
    exercises the hand-attached ``cache_clear`` rather than the stdlib's
    locking: a clear that reached inside the cache incorrectly would surface
    here as an exception on a reader thread.
    """

    def test_a_clear_racing_readers_never_yields_corrupt_data(self) -> None:
        errors: list[Exception] = []

        def fetch_config() -> None:
            try:
                assert isinstance(get_config().as_dict(), dict)
            except Exception as exc:
                errors.append(exc)

        def clear_cache() -> None:
            try:
                get_config.cache_clear()
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures: list[Future[None]] = [
                pool.submit(clear_cache if index % 5 == 0 else fetch_config) for index in range(20)
            ]
            for future in futures:
                future.result()

        assert errors == [], f"Concurrent access errors: {errors}"
