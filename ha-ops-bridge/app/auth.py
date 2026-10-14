"""Bearer authentication for the ha-ops-bridge Phase A runtime.

A single dedicated bridge secret (the app option `bridge_token`) gates every
protected endpoint. The secret is NEVER the HA token, is never logged, and is
never returned to the client. Comparison is constant-time to avoid a timing
oracle.
"""

from __future__ import annotations

import hmac

# Minimum acceptable secret length (characters). Shorter secrets fail startup.
MIN_TOKEN_LENGTH = 16


def normalize_token(secret):
    """Return the secret as a bytes string, or None if unusable."""
    if not isinstance(secret, str):
        return None
    value = secret
    if not value:
        return None
    return value.encode("utf-8")


def is_valid_secret_length(secret):
    """True if the secret is a non-empty string of at least MIN_TOKEN_LENGTH."""
    if not isinstance(secret, str):
        return False
    return len(secret) >= MIN_TOKEN_LENGTH


def constant_time_compare(provided, expected):
    """Constant-time comparison of two strings.

    Returns False (not an exception) when inputs are unusable, so a caller can
    reject without branching on a timing-sensitive path.
    """
    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def extract_bearer(auth_header):
    """Pull the bearer token out of an Authorization header value.

    Returns None when the header is absent or not a `Bearer <token>` value.
    Returns the token string otherwise (no validation here).
    """
    if not isinstance(auth_header, str):
        return None
    header = auth_header.strip()
    prefix = "Bearer "
    if not header.lower().startswith(prefix.lower()):
        return None
    token = header[len(prefix):].strip()
    if not token:
        return None
    # A bearer token must not itself contain whitespace.
    if any(ch.isspace() for ch in token):
        return None
    return token


def verify_token(provided, expected):
    """Verify a provided bearer token against the expected secret.

    Returns True only when the token matches the expected secret under
    constant-time comparison and both are usable. Never returns the secret.
    """
    token = extract_bearer("Bearer " + (provided or "")) if provided else None
    if token is None:
        return False
    if not is_valid_secret_length(expected):
        return False
    return constant_time_compare(provided, expected)
