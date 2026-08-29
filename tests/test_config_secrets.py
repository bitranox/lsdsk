"""What the second redaction pass hides, and what it deliberately leaves alone.

This guard is the reason `lsdsk config` can state secret-safety as its own
guarantee rather than inheriting one, so the cases below are the guarantee
written down. Two of them pull in opposite directions and that is the point: a
secret must not survive anywhere under a sensitive-looking name, while a table
with such a name is a section whose readable half is worth keeping.
"""

from __future__ import annotations

import pytest

from lsdsk.adapters.config.secrets import REDACTED, is_sensitive_name, redact_secrets


@pytest.mark.os_agnostic
def test_a_scalar_under_a_sensitive_name_is_hidden() -> None:
    """The plainest case, and the one every other case is judged against."""
    assert redact_secrets({"api_key": "AKIA"}) == {"api_key": REDACTED}


@pytest.mark.os_agnostic
def test_a_sequence_under_a_sensitive_name_is_hidden_item_by_item() -> None:
    """A list of tokens is a list of secrets.

    The walk descends into sequences to find mappings inside them, and the key
    that governs the sequence governs its items too. Without that, the same name
    holding one secret was redacted and holding several was printed.
    """
    assert redact_secrets({"password": ["one", "two"]}) == {"password": [REDACTED, REDACTED]}
    assert redact_secrets({"token": ("T-ONE", "T-TWO")}) == {"token": [REDACTED, REDACTED]}
    assert redact_secrets({"api_key": [["nested"]]}) == {"api_key": [[REDACTED]]}


@pytest.mark.os_agnostic
def test_a_table_under_a_sensitive_name_keeps_its_readable_half() -> None:
    """A section is not a secret, however it is named.

    A table called `auth` legitimately holds a username and a host beside its
    token. Blanking the table hides the readable half for nothing, so only the
    scalars under a sensitive name inside it are replaced.
    """
    given = {"auth": {"token": "T", "host": "mail.example.com"}}
    assert redact_secrets(given) == {"auth": {"token": REDACTED, "host": "mail.example.com"}}


@pytest.mark.os_agnostic
def test_a_mapping_inside_a_sequence_is_judged_key_by_key() -> None:
    """The sequence rule must not swallow the table rule it contains."""
    given = {"accounts": [{"password": "hunter2", "user": "bob"}]}
    assert redact_secrets(given) == {"accounts": [{"password": REDACTED, "user": "bob"}]}


@pytest.mark.os_agnostic
def test_an_ordinary_name_survives_at_every_depth() -> None:
    """Redacting more than asked hides the configuration the reader came for."""
    given = {"display": {"columns": ["model", "wwn"], "width": 120}}
    assert redact_secrets(given) == given


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "name", ["password", "SmtpPassword", "dbPassword", "db_pass", "api_key", "private_key", "PrivateKey"]
)
def test_names_a_reader_would_expect_to_be_hidden(name: str) -> None:
    """The spellings the configuration library's own pattern misses."""
    assert is_sensitive_name(name)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["privatekey", "apikey", "accesstoken", "api_keys", "tokens", "passwords", "secrets"])
def test_names_this_pass_does_not_hide(name: str) -> None:
    """Two limits of matching whole words against a fixed set, pinned so they are known.

    A SQUASHED name splits into one word that equals no sensitive word, so
    `private_key` and `PrivateKey` match while `privatekey` does not. A PLURAL
    is a different word from its singular, so `token` matches and `tokens` does
    not. Both are reachable: either spelling is one somebody writes in a real
    configuration file.

    Neither is fixed here because both fixes reach further than they look. A
    substring test catches `privatekey` and also blanks any ordinary name
    containing a sensitive word, and stripping a trailing `s` changes what a
    security guard hides on names nobody has enumerated yet. Pinned rather than
    left to be discovered as a surprise.
    """
    assert not is_sensitive_name(name)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["auth", "key", "pass", "host", "columns"])
def test_names_that_only_mean_a_secret_in_company(name: str) -> None:
    """A bare word is ordinary configuration vocabulary, not a secret."""
    assert not is_sensitive_name(name)
