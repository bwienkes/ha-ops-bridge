"""Object-id validation and normalization for script/automation readers.

An HA object id (script or automation) is a single logical name, never a path.
The bridge accepts a strict character set, rejects any path-like or shell
metacharacter, enforces a length cap, and normalizes the documented
`script.<id>` prefix. There is no way to reach a file from this module: the
output is always a bare id or None.
"""

from __future__ import annotations

# Allowed characters in a bare object id: letters, digits, underscore, hyphen,
# and a single dot separator for HA's nested form (e.g. "laundry.overlay").
_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
# Characters that are absolutely forbidden (path/shell metacharacters).
_FORBIDDEN = set("/\\:")  # slash, backslash, colon

MAX_OBJECT_ID_LEN = 128


class IdError(Exception):
    """Raised when an object id fails validation."""


def _has_shell_metacharacters(value):
    """True if value contains shell-special characters beyond the allowed set."""
    metachars = set("`'$&|;<>(){}[]!*?~#=@")
    return any(ch in metachars for ch in value)


def normalize_id(raw):
    """Normalize an incoming id value to a bare object id.

    - Trims surrounding whitespace (not internal whitespace, which is rejected).
    - Strips the documented `script.` / `automation.` prefix if present.
    - Rejects None, non-strings, empty, internal whitespace, and any forbidden
      path/shell metacharacter.
    - Enforces MAX_OBJECT_ID_LEN.

    Returns the normalized bare id string, or raises IdError.
    """
    if raw is None or not isinstance(raw, str):
        raise IdError("object id must be a string")

    value = raw.strip()
    if not value:
        raise IdError("object id is empty")

    # Strip the documented domain prefix if the client used the qualified form.
    for prefix in ("script.", "automation."):
        if value.startswith(prefix):
            value = value[len(prefix):]
            value = value.strip()
            break

    if not value:
        raise IdError("object id is empty after normalization")

    if any(ch.isspace() for ch in value):
        raise IdError("object id contains whitespace")

    if any(ch in _FORBIDDEN for ch in value):
        raise IdError("object id contains a forbidden path/shell character")

    if any(ch not in _ALLOWED for ch in value):
        raise IdError("object id contains a disallowed character")

    if _has_shell_metacharacters(value):
        raise IdError("object id contains a shell metacharacter")

    if len(value) > MAX_OBJECT_ID_LEN:
        raise IdError("object id exceeds maximum length")

    if ".." in value or value.startswith("."):
        raise IdError("object id must not use dot-relative segments")

    return value
