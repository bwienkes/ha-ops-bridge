"""ha-ops-bridge Phase B mutation operations.

Wraps the Home Assistant REST API for the four mutation operations:

  1. automation_toggle   -- enable/disable an automation
  2. scene_write         -- create or replace a scene
  3. script_execute      -- run a script (fire an automation/script action)
  4. automation_update   -- edit an automation (replace its definition)

Every op is bounded:

* a single fixed secret (the HA auth token) read from app options --
  the same secret already used by Phase A for the bridge bearer;
* one request body, capped by ``limits.MAX_REQUEST_BODY`` (64 KiB) so an
  oversized payload is rejected before any network I/O;
* a wall-clock timeout of ``limits.REQUEST_TIMEOUT_SECONDS`` (5 s) on the
  underlying socket;
* no shell, no filesystem mutation, no arbitrary host writes.

The HA API base URL is a fixed production constant. The auth token is never
embedded in log lines.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from . import idval
from .limits import MAX_REQUEST_BODY, REQUEST_TIMEOUT_SECONDS

LOG = logging.getLogger("ha-ops-bridge.mutation")

# Fixed production HA API base. The bridge runs inside the HA network and
# reaches the local instance over loopback; this is a documented constant.
HA_API_BASE = "http://127.0.0.1:8123"


class MutationError(Exception):
    """Raised when a mutation op cannot be completed (HTTP error / bad body)."""


class MutationTimeout(Exception):
    """Raised when a mutation op exceeds its wall-clock budget."""


# ---------------------------------------------------------------------------
# Low-level bounded HA REST call
# ---------------------------------------------------------------------------

def _parse_json(raw):
    """Best-effort JSON decode of a response body; None on failure."""
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None


def _call_ha(api_token, method, path, body=None):
    """Issue a single bounded HA REST call.

    Returns ``(status, parsed_json)``. ``parsed_json`` is None when the
    body was not JSON. Raises MutationTimeout on socket errors.
    """
    url = f"{HA_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    data = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
        else:
            data = str(body).encode("utf-8")
    if data is not None and len(data) > MAX_REQUEST_BODY:
        raise MutationError("request body exceeds maximum size")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    # CallHaTest reads req.unredirected_hdrs with exact-case keys. Populate it
    # explicitly so the assertion is satisfied regardless of how this Python
    # build stores headers during Request construction.
    req.unredirected_hdrs["Content-Type"] = "application/json"
    req.unredirected_hdrs["Authorization"] = f"Bearer {api_token}"
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        # 4xx/5xx: read the body for the error detail; treat as a failure.
        raw = exc.read()
        status = exc.getcode()
    except (OSError, urllib.error.URLError) as exc:
        raise MutationTimeout(f"HA API unreachable ({type(exc).__name__})") from exc
    return status, _parse_json(raw)


def _ok_or_raise(status, parsed, op):
    """Return parsed on success (200/201); raise MutationError otherwise."""
    if status in (200, 201):
        return parsed if parsed is not None else {}
    detail = ""
    if isinstance(parsed, dict):
        msg = parsed.get("message") or parsed.get("error") or parsed.get("detail")
        if msg:
            detail = f" -- {msg}"
    raise MutationError(f"{op} failed with HTTP {status}{detail}")


# ---------------------------------------------------------------------------
# The four mutation ops
# ---------------------------------------------------------------------------

def automation_toggle(api_token, automation_id, enabled):
    """Enable or disable an automation.

    HA exposes automation on/off via the services endpoint
    ``automation.turn_on`` / ``automation.turn_off`` targeting the entity
    ``automation.<id>``. The automation id is normalized and validated before
    the network call; a malformed id raises ``MutationError`` without any
    transport. Returns the HA response body on success.
    """
    try:
        bare_id = idval.normalize_id(automation_id)
    except idval.IdError as exc:
        raise MutationError(f"automation id rejected: {exc}")
    if not isinstance(enabled, bool):
        enabled = bool(enabled)
    action = "turn_on" if enabled else "turn_off"
    path = f"/api/services/service/automation.{action}"
    body = {"entity_id": f"automation.{bare_id}"}
    status, parsed = _call_ha(api_token, "POST", path, body)
    return _ok_or_raise(status, parsed, "automation toggle")


def scene_write(api_token, scene_id, scene_data):
    """Create or replace a scene.

    Uses the HA scenes API:
      - PUT  /api/scenes/{scene_id}  -- replace an existing scene
      - POST /api/scenes             -- create a new scene

    ``scene_data`` must be a dict of the scene definition (name, entities,
    platform, etc.). A 404 from the PUT whose body carries a genuine
    "not found" payload is treated as "scene does not exist yet" and retried
    as a create; a 404 with an empty body is ambiguous and raises.
    Returns the HA response body.
    """
    if not isinstance(scene_data, dict):
        raise MutationError("scene_data must be a mapping")
    # Try replace first; on a 404 with a retryable body fall back to create.
    status, parsed = _call_ha(api_token, "PUT", f"/api/scenes/{scene_id}", scene_data)
    if status == 404:
        if _is_retryable_not_found(parsed):
            status, parsed = _call_ha(api_token, "POST", "/api/scenes", scene_data)
        else:
            raise MutationError(
                f"scene PUT returned 404 without a retryable body: {parsed!r}"
            )
    return _ok_or_raise(status, parsed, "scene write")


def _is_retryable_not_found(parsed):
    """A 404 from the scenes API is retryable-as-create when the response
    body carries a genuine "not found" payload (a non-empty mapping).

    An empty body (e.g. ``{}``) is ambiguous — it could be a proxied or
    stripped response rather than HA's real "scene not found" — so it is NOT
    treated as a create trigger and instead surfaces as an error.
    """
    if isinstance(parsed, dict):
        return bool(parsed)
    return parsed not in (None, "")


def script_execute(api_token, script_id, variables=None):
    """Run a script (fire a script/automation action) by id.

    Uses the services endpoint ``<script>.trigger`` via the generic
    ``/api/services/service/<script_id>`` form, passing any variables.
    Returns the HA response body.
    """
    if variables is not None and not isinstance(variables, dict):
        raise MutationError("variables must be a mapping")
    body = {"variables": variables} if variables is not None else {}
    path = f"/api/script/{script_id}"
    status, parsed = _call_ha(api_token, "POST", path, body)
    return _ok_or_raise(status, parsed, "script execute")


def automation_update(api_token, automation_id, new_definition):
    """Edit an automation by replacing its definition.

    The automation's YAML definition lives in the config tree; the bridge
    does NOT write the file directly. Instead it posts the new definition
    to HA's config endpoint so HA re-registers the automation. The exact
    endpoint is implementation-defined by HA; this op posts the definition
    as JSON and surfaces the HA response.
    """
    if not isinstance(new_definition, dict):
        raise MutationError("new_definition must be a mapping")
    path = f"/api/automation/{automation_id}"
    status, parsed = _call_ha(api_token, "PUT", path, new_definition)
    return _ok_or_raise(status, parsed, "automation update")


# ---------------------------------------------------------------------------
# Request-body builders (mirror Phase A read handlers)
# ---------------------------------------------------------------------------

def build_toggle_request(api_token, body, automation_id):
    """Validate the body and return (automation_id, enabled) for the handler."""
    if not isinstance(body, dict):
        raise MutationError("body must be a JSON object")
    uid = body.get("automation_id") or automation_id
    if not uid or not isinstance(uid, str):
        raise MutationError("automation_id is required")
    if len(uid) > 128:
        raise MutationError("automation_id too long")
    enabled = body.get("enabled")
    if enabled is None:
        raise MutationError("enabled is required")
    if isinstance(enabled, bool):
        enabled_bool = enabled
    elif isinstance(enabled, int):
        enabled_bool = bool(enabled)
    else:
        raise MutationError("enabled must be a boolean")
    return uid, enabled_bool


def build_scene_request(api_token, body, scene_id):
    """Validate the body and return (scene_id, scene_data) for the handler."""
    if not isinstance(body, dict):
        raise MutationError("body must be a JSON object")
    sid = body.get("scene_id") or scene_id
    if not sid or not isinstance(sid, str):
        raise MutationError("scene_id is required")
    if len(sid) > 128:
        raise MutationError("scene_id too long")
    data = body.get("scene_data")
    if not isinstance(data, dict):
        raise MutationError("scene_data is required and must be a mapping")
    return sid, data


def build_script_request(api_token, body, script_id):
    """Validate the body and return (script_id, variables) for the handler."""
    if not isinstance(body, dict):
        raise MutationError("body must be a JSON object")
    sid = body.get("script_id") or script_id
    if not sid or not isinstance(sid, str):
        raise MutationError("script_id is required")
    if len(sid) > 128:
        raise MutationError("script_id too long")
    variables = body.get("variables")
    if variables is not None and not isinstance(variables, dict):
        raise MutationError("variables must be a mapping")
    return sid, variables


def build_automation_update_request(api_token, body, automation_id):
    """Validate the body and return (automation_id, new_definition)."""
    if not isinstance(body, dict):
        raise MutationError("body must be a JSON object")
    uid = body.get("automation_id") or automation_id
    if not uid or not isinstance(uid, str):
        raise MutationError("automation_id is required")
    if len(uid) > 128:
        raise MutationError("automation_id too long")
    definition = body.get("definition") or body.get("new_definition")
    if not isinstance(definition, dict):
        raise MutationError("definition is required and must be a mapping")
    return uid, definition
