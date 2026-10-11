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
@pytest.mark.parametrize("name", ["passwords", "tokens", "secrets", "api_keys", "SmtpPasswords"])
def test_a_plural_is_hidden_like_its_singular(name: str) -> None:
    """A plural of a secret is a secret.

    `token` matched and `tokens` did not, which is the spelling a configuration
    file uses precisely when it holds several. The plural is judged by trying
    the word without its trailing `s` IN ADDITION to the word itself, never
    instead of it: folding first turns `pass` into `pas` and loses the match it
    already had.
    """
    assert is_sensitive_name(name)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["privatekey", "apikey", "accesstoken"])
def test_a_squashed_spelling_is_a_known_limit(name: str) -> None:
    """Matching is by word, so a name written as one squashed word is missed.

    `private_key` and `PrivateKey` both split into words and match; `privatekey`
    splits into one word that equals no sensitive word. Widening this to a
    substring test would catch them and would also blank any ordinary name that
    happens to contain one, so the limit is pinned here rather than left to be
    discovered as a surprise.
    """
    assert not is_sensitive_name(name)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["hosts", "columns", "status", "address", "process", "keys", "flags"])
def test_an_ordinary_plural_is_still_ordinary(name: str) -> None:
    """The trailing-`s` rule must not widen what counts as a secret.

    `keys` is the case that decides it: bare `key` means a secret only in
    company, so its plural must too, and a mapping's `keys` stays readable.
    """
    assert not is_sensitive_name(name)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["auth", "key", "pass", "host", "columns"])
def test_names_that_only_mean_a_secret_in_company(name: str) -> None:
    """A bare word is ordinary configuration vocabulary, not a secret."""
    assert not is_sensitive_name(name)


#: The words that mean a secret wherever they stand, written out here rather than read from the
#: module so that a word dropped from its vocabulary is a test failure and not a silent shrink.
ALWAYS_SENSITIVE_WORDS = [
    "authorization",
    "credential",
    "credentials",
    "passphrase",
    "passwd",
    "password",
    "private",
    "pwd",
    "secret",
    "token",
]


@pytest.mark.parametrize("word", ALWAYS_SENSITIVE_WORDS)
def test_each_always_sensitive_word_hides_its_value_alone_and_in_any_spelling(word: str) -> None:
    """Every word of the vocabulary, on its own, snake-cased and camel-cased, upper-cased and plural."""
    assert is_sensitive_name(word)
    assert is_sensitive_name(word.upper())
    assert is_sensitive_name(f"db_{word}")
    assert is_sensitive_name(f"db{word.capitalize()}")
    assert is_sensitive_name(f"{word}s")


@pytest.mark.parametrize("word", ["auth", "key", "pass"])
def test_each_in_company_word_hides_its_value_beside_another_word_only(word: str) -> None:
    """The three words that need company: sensitive in a longer name, in either order, plural too; never alone."""
    assert not is_sensitive_name(word)
    assert is_sensitive_name(f"db_{word}")
    assert is_sensitive_name(f"{word}_db")
    assert is_sensitive_name(f"db{word.capitalize()}")
    assert is_sensitive_name(f"db_{word}s")


@pytest.mark.parametrize("name", ["author", "author_name", "authority_url", "oauth", "web_author", "compass_pass_id"])
def test_auth_inside_a_longer_word_is_not_auth(name: str) -> None:
    """Words are compared whole: `auth` at the front of `author` or `authority` names no credential.

    The last case is the control for the rule: `pass` stands as a whole word there and the name is
    in company, so it IS sensitive, which keeps this test from passing on a matcher that hides nothing.
    """
    assert is_sensitive_name(name) is (name == "compass_pass_id")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("APIKey", True),
        ("DBPass", True),
        ("HTTPPassword", True),
        ("XMLToken", True),
        ("APIHost", False),
        ("HTTPServer", False),
    ],
)
def test_an_acronym_followed_by_a_word_splits_between_them(name: str, expected: bool) -> None:
    """`APIKey` is `API` and `Key`, so the in-company word is found; unsplit it is the one word `apikey`."""
    assert is_sensitive_name(name) is expected


def test_a_sensitive_key_inside_nested_tables_is_hidden_at_every_depth() -> None:
    """The descent into a mapping, two and three levels down, beside readable siblings."""
    given = {"outer": {"user": "u", "inner": {"password": "p", "host": "h", "deeper": {"token": "t", "port": 25}}}}
    assert redact_secrets(given) == {
        "outer": {"user": "u", "inner": {"password": REDACTED, "host": "h", "deeper": {"token": REDACTED, "port": 25}}}
    }


def test_tables_inside_lists_inside_tables_are_walked_too() -> None:
    """Mapping to sequence to mapping to sequence, with the readable value kept at each step."""
    given = {"a": {"b": [{"c": [{"secret": "s", "name": "n"}]}]}}
    assert redact_secrets(given) == {"a": {"b": [{"c": [{"secret": REDACTED, "name": "n"}]}]}}
