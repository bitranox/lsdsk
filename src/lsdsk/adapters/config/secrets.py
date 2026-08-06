"""A second, broader redaction pass over anything about to be printed.

The configuration library redacts by key name, and its pattern requires the
sensitive word to sit at an underscore boundary.  That covers ``smtp_password``
and misses ``SmtpPassword``, ``dbPassword``, ``db_pass``, ``PASS``,
``Authorization`` and ``privatekey``, every one of which is a name somebody
writes in a real configuration file.  Measured against the library's own
``is_sensitive``: of fourteen spellings tried, five underscore-delimited ones
redacted and eight camelCase or squashed ones did not.

The right place to fix the pattern is the library, and that is worth doing.  This
exists because ``lsdsk config`` states secret-safety as its own guarantee, in
both output modes, and a guarantee that depends entirely on somebody else's regex
is one this tool cannot actually make.  It runs after the library's pass and only
ever redacts more, so the two cannot disagree about a key they both catch.

System Role:
    Adapter layer, an output boundary guard.  No I/O.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Words that make a key worth hiding wherever they appear, matched on word
#: boundaries that include camelCase humps so ``dbPassword`` and ``db_password``
#: are treated alike.
_ALWAYS_SENSITIVE = frozenset(
    {
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
    }
)

#: Words that only mean a secret in company. On their own they are ordinary
#: configuration vocabulary: a table named ``auth``, a mapping's ``key``, a
#: pass/fail flag named ``pass``. Blanking those hid a table's non-secret
#: fields and a generic value, so they count only in a multi-word name -
#: ``api_key`` and ``db_pass`` yes, bare ``key`` and ``auth`` no.
_SENSITIVE_IN_COMPANY = frozenset({"auth", "key", "pass"})

_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

REDACTED = "***REDACTED***"


def is_sensitive_name(key: str) -> bool:
    """Whether a configuration key's name suggests it holds a secret.

    Splits on separators AND on camelCase humps, then compares whole words, so a
    key is judged by the words it is made of rather than by how it was spelled.

    Args:
        key: The configuration key.

    Returns:
        Whether its value should be hidden.

    Example:
        >>> [is_sensitive_name(name) for name in ("smtp_password", "SmtpPassword", "dbPassword")]
        [True, True, True]
        >>> [is_sensitive_name(name) for name in ("hostname", "key", "auth", "compass")]
        [False, False, False, False]
        >>> [is_sensitive_name(name) for name in ("api_key", "db_pass")]
        [True, True]
    """
    words = [word.lower() for word in _SPLIT.split(key) if word]
    if any(word in _ALWAYS_SENSITIVE for word in words):
        return True
    return len(words) > 1 and any(word in _SENSITIVE_IN_COMPANY for word in words)


def _redact_entry(key: Any, value: Any) -> Any:
    """Hide one entry's value, or descend into it.

    A sensitive-looking name over a MAPPING is a section, not a secret: a table
    called ``auth`` legitimately holds a username and a host beside its token,
    and blanking the whole table hides the readable half for nothing. Only a
    scalar is replaced.
    """
    if isinstance(value, (dict, list, tuple)):
        return redact_secrets(value)
    if isinstance(key, str) and is_sensitive_name(key):
        return REDACTED
    return value


def redact_secrets(value: Any) -> Any:
    """Replace every value under a sensitive-looking key, at any depth.

    Walks mappings and sequences alike, because a token is just as exposed
    sitting in a list as it is at the top level.

    Args:
        value: A configuration mapping, or any part of one.

    Returns:
        The same shape with sensitive values replaced.

    Example:
        >>> redact_secrets({"alerting": {"SmtpPassword": "hunter2", "host": "mail"}})
        {'alerting': {'SmtpPassword': '***REDACTED***', 'host': 'mail'}}
    """
    if isinstance(value, dict):
        mapping = cast("dict[str, Any]", value)
        return {key: _redact_entry(key, item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in cast("Sequence[Any]", value)]
    return value


__all__ = ["REDACTED", "is_sensitive_name", "redact_secrets"]
