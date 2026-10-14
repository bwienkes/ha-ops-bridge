"""App-option loading for the ha-ops-bridge Phase A runtime.

The Supervisor writes the app's option values (including ``bridge_token``)
to the app data volume as ``options.json``. In-container this file always
lands at the fixed path ``/data/options.json`` (app DATA mapping).

Production startup loads the bridge secret from that file only. The
``HA_OPS_BRIDGE_TOKEN`` environment variable is a test-only override; the
production entrypoint does not set it.
"""

from __future__ import annotations

import json
import os

OPTIONS_PATH = "/data/options.json"
TOKEN_KEY = "bridge_token"


class OptionsError(Exception):
    """Raised when app options are missing or malformed."""


def load_bridge_token(path=OPTIONS_PATH):
    """Return the ``bridge_token`` option value from the app options file.

    Raises OptionsError on any failure (missing file, unreadable, invalid
    JSON, non-object, absent key, non-string value, or wrong type). Never
    returns or embeds the secret in the error message.
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise OptionsError("app options file is absent")

    try:
        with open(path, "r", encoding="utf-8") as handle:
            options = json.load(handle)
    except (OSError, ValueError):
        raise OptionsError("app options file is unreadable or invalid JSON")

    if not isinstance(options, dict):
        raise OptionsError("app options file must be a JSON object")

    token = options.get(TOKEN_KEY)
    if not isinstance(token, str) or not token:
        raise OptionsError("bridge_token option is absent or empty")

    return token
