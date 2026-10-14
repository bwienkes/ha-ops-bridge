"""Config, error, log, and health operations for the ha-ops-bridge.

Phase A scope: read-only, preauthorized operations only. There is no
subprocess, no shell, no docker socket, and no host escalation. Config
validation (config/check and config/errors) is only as good as the HA
runtime we can reach: if we cannot reach a supported HA/Supervisor
validation path, we return `available: false` and never fake validation
with PyYAML.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Optional

from .config_root import ConfigRootError, CONFIGURATION_FILE, resolve_config_root
from .limits import MAX_LOG_FILE_BYTES, MAX_LOG_LINES, clamp_bytes, clamp_int
from .yamlutil import YAMLError, safe_load_file


class ConfigOpsError(Exception):
    """Raised when a config/log/health operation cannot proceed safely."""


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def check_config_root(root=None):
    """Return (ok, reason). ok=True means the mapping is mounted and
    configuration.yaml is present. reason is a short human string when not ok.
    """
    try:
        resolve_config_root(root)
    except ConfigRootError as exc:
        return False, str(exc)
    return True, ""


def health_payload(root=None, version="unknown", extra=None):
    """Build the GET /v1/health response payload."""
    ok, reason = check_config_root(root)
    payload = {
        "ok": ok,
        "service": "ha-ops-bridge",
        "version": version,
        "read_only": True,
        "config_root": "mounted" if ok else "unavailable",
        "config_root_reason": reason if not ok else "",
    }
    if extra:
        for key, value in extra.items():
            payload.setdefault(key, value)
    return payload


# ---------------------------------------------------------------------------
# Config check
# ---------------------------------------------------------------------------

def config_check(root=None):
    """POST /v1/config/check response.

    We only run a supported check when an HA runtime is reachable via the
    Supervisor's own validation path. In the current Phase A build we have no
    supported runtime validation path, so we always return
    `available: false`. We never fake validation by parsing configuration.yaml
    ourselves and calling it "valid".
    """
    return {
        "available": False,
        "valid": None,
        "errors": [],
        "reason": "no supported HA/Supervisor validation path reachable from the bridge",
    }


# ---------------------------------------------------------------------------
# Config errors
# ---------------------------------------------------------------------------

def config_errors(root=None):
    """POST /v1/config/errors response.

    Same model as config_check: only report errors we can get from a supported
    HA/Supervisor path. Without one, return available: false.
    """
    return {
        "available": False,
        "errors": [],
        "reason": "no supported HA/Supervisor error path reachable from the bridge",
    }


# ---------------------------------------------------------------------------
# Log read
# ---------------------------------------------------------------------------

_LOG_FILE_CANDIDATES = ["home-assistant.log", "supervisor.log"]


def _find_log_file(root):
    """Return the absolute path of the first readable log file, or None."""
    for name in _LOG_FILE_CANDIDATES:
        path = os.path.join(root, name)
        if os.path.isfile(path) and os.access(path, os.R_OK):
            return path
    return None


def _redact(text, secret):
    """Replace occurrences of `secret` in `text` with [REDACTED].

    Constant-time not required (redaction is a data op), but we never expose
    the secret to the caller.
    """
    if not secret or not isinstance(text, str):
        return text
    return text.replace(secret, "[REDACTED]")


def read_log(root, query, max_lines=None, secret=None, max_file_bytes=None):
    """POST /v1/log/read response.

    - query: literal substring; never a regex.
    - max_lines: caller-supplied cap, clamped to MAX_LOG_LINES (100).
    - max_file_bytes: byte cap for the log file; clamped to MAX_LOG_FILE_BYTES.
    - secret: the bridge token, redacted from every returned line.

    Returns a dict:
      {
        "available": bool,
        "query_echo": None,            # we never echo the query back
        "matched_lines": int,
        "lines": [ ... matching lines only ... ],
        "reason": <short> when available is False
      }
    """
    available = False
    matched_lines = 0
    lines = []
    reason = ""

    if not isinstance(query, str) or not query:
        reason = "query must be a non-empty literal string"
        return {"available": False, "matched_lines": 0, "lines": [], "reason": reason}

    path = _find_log_file(root)
    if path is None:
        return {
            "available": False,
            "matched_lines": 0,
            "lines": [],
            "reason": "no readable log file at a supported location",
        }

    cap = clamp_int(max_file_bytes, 1, MAX_LOG_FILE_BYTES) if max_file_bytes is not None else MAX_LOG_FILE_BYTES
    line_cap = clamp_int(max_lines, 1, MAX_LOG_LINES) if max_lines is not None else MAX_LOG_LINES

    try:
        size = os.path.getsize(path)
    except OSError:
        return {
            "available": False,
            "matched_lines": 0,
            "lines": [],
            "reason": "log file unreadable",
        }

    if size > cap:
        # We refuse to scan an oversized log; return available=false.
        return {
            "available": False,
            "matched_lines": 0,
            "lines": [],
            "reason": "log file exceeds byte cap (%d > %d)" % (size, cap),
        }

    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return {
            "available": False,
            "matched_lines": 0,
            "lines": [],
            "reason": "log file unreadable",
        }

    text = raw.decode("utf-8", errors="replace")
    if secret:
        text = _redact(text, secret)

    matched = [line for line in text.splitlines() if query in line]
    matched = matched[:line_cap]
    matched_lines = len(matched)

    return {
        "available": True,
        "matched_lines": matched_lines,
        "lines": matched,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def redact_secret(text, secret):
    """Public helper: redact a secret string from arbitrary text."""
    return _redact(text, secret)
