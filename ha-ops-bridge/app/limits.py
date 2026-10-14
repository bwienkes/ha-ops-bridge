"""Bounded limits for the ha-ops-bridge Phase A runtime.

Every numeric bound is fixed here so request/response sizing, timeouts, and
input caps are deterministic and testable. Nothing in the bridge may exceed
these caps; oversized requests are rejected before any parsing.
"""

# --- request sizing --------------------------------------------------------
# Maximum raw request body accepted from the client, in bytes.
MAX_REQUEST_BODY = 64 * 1024  # 64 KiB

# Maximum allowed length of a script/automation object id, in characters.
MAX_SCRIPT_ID_LEN = 128

# Maximum allowed length of the log substring query, in characters.
MAX_LOG_QUERY_LEN = 200

# Maximum number of log lines returned by /v1/log/read.
MAX_LOG_LINES = 100

# Maximum bytes of a log file we will scan (we refuse larger files up front).
MAX_LOG_FILE_BYTES = 512 * 1024  # 512 KiB

# Maximum total response body the server will emit, in bytes.
MAX_RESPONSE_BODY = 512 * 1024  # 512 KiB

# --- timeouts -------------------------------------------------------------
# Bounded wall-clock budget for a single request, in seconds.
REQUEST_TIMEOUT_SECONDS = 5.0

# Bounded budget for a single YAML load, in seconds (guards deep/large files).
YAML_LOAD_TIMEOUT_SECONDS = 2.0


def clamp_int(value, lo, hi):
    """Return value coerced into [lo, hi]. Non-int falls back to hi (safe max)."""
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return hi
    return max(lo, min(hi, ivalue))


def clamp_bytes(value, hi):
    """Return value coerced to a non-negative int not exceeding hi."""
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return hi
    if ivalue < 0:
        return 0
    return min(hi, ivalue)
