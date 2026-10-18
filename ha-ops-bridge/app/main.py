"""ha-ops-bridge Phase A+B runtime.

A single fixed unprivileged TCP port (8947) serves read-only (Phase A) and
mutation (Phase B) endpoints. Authentication is one dedicated bridge secret
from app options, sent as `Authorization: Bearer <secret>` and compared in
constant time. No shell, no arbitrary filesystem writes.

Phase A endpoints (read-only)
------------------------------
GET  /v1/health
POST /v1/script/read
POST /v1/automation/read
POST /v1/config/errors
POST /v1/log/read
POST /v1/config/check

Phase B endpoints (mutations, HA REST API only)
-----------------------------------------------
POST /v1/automation/execute    enable/disable an automation
POST /v1/automation/update     replace an automation definition
POST /v1/script/execute        run a script by id (optional variables)
POST /v1/scene/write           create or replace a scene
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from . import mutation_ops
from .auth import MIN_TOKEN_LENGTH, constant_time_compare, extract_bearer, is_valid_secret_length
from .config_ops import (
    config_check,
    config_errors,
    health_payload,
    read_log,
)
from .config_root import ConfigRootError, resolve_config_root
from .idval import IdError, MAX_OBJECT_ID_LEN
from .limits import MAX_LOG_QUERY_LEN, MAX_REQUEST_BODY, REQUEST_TIMEOUT_SECONDS
from .script_reader import ReaderError, build_read_response, read_object
from .yamlutil import YAMLError

LOG = logging.getLogger("ha-ops-bridge")

# Fixed unprivileged TCP port; documented and consistent everywhere.
BRIDGE_PORT = 8947
BRIDGE_HOST = "0.0.0.0"


class RequestTooLarge(Exception):
    pass


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "ha-ops-bridge"
    sys_version = ""
    version = __version__

    def log_message(self, fmt, *args):
        # Never let request lines containing the token leak into logs.
        try:
            msg = fmt % args
        except Exception:
            msg = fmt
        self.server.access_log.append(msg)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def do_GET(self):
        if self.path == "/v1/health":
            self._handle_health()
        else:
            self._json_error(404, "not_found", "unknown route")

    def do_POST(self):
        if self.path not in (
            "/v1/script/read",
            "/v1/automation/read",
            "/v1/config/errors",
            "/v1/log/read",
            "/v1/config/check",
            "/v1/automation/execute",
            "/v1/automation/update",
            "/v1/script/execute",
            "/v1/scene/write",
        ):
            self._json_error(404, "not_found", "unknown route")
            return

        # Auth comes before we read or process the body at all.
        if not self._auth_ok():
            return

        body = self._read_body()
        if body is None:
            return  # error already sent

        if self.path == "/v1/script/read":
            self._handle_read_object(body, "script", "script_id")
        elif self.path == "/v1/automation/read":
            self._handle_read_object(body, "automation", "automation_id")
        elif self.path == "/v1/config/errors":
            self._handle_config_errors()
        elif self.path == "/v1/log/read":
            self._handle_log_read(body)
        elif self.path == "/v1/config/check":
            self._handle_config_check()
        elif self.path == "/v1/automation/execute":
            self._handle_automation_execute(body)
        elif self.path == "/v1/automation/update":
            self._handle_automation_update(body)
        elif self.path == "/v1/script/execute":
            self._handle_script_execute(body)
        elif self.path == "/v1/scene/write":
            self._handle_scene_execute(body)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_health(self):
        root = self.server.config_root
        self._json(200, health_payload(root=root, version=self.version))

    def _handle_read_object(self, body, kind, id_field):
        root = self.server.config_root
        try:
            resolved_root = resolve_config_root(root)
        except ConfigRootError as exc:
            self._json_error(503, "config_root_unavailable", str(exc))
            return

        if not isinstance(body, dict):
            self._json_error(400, "bad_request", "request body must be a JSON object")
            return

        raw_id = body.get(id_field)
        try:
            result = read_object(resolved_root, raw_id, kind)
        except IdError as exc:
            self._json_error(400, "bad_object_id", str(exc))
            return
        except ReaderError as exc:
            self._json_error(409, "unsupported_configuration_structure", str(exc))
            return
        except YAMLError as exc:
            self._json_error(409, "unsupported_configuration_structure", str(exc))
            return

        from .idval import normalize_id

        try:
            found_id = normalize_id(raw_id)
        except IdError as exc:
            self._json_error(400, "bad_object_id", str(exc))
            return

        payload = build_read_response(kind, found_id, result, found_id)
        self._json(200, payload)

    def _handle_config_errors(self):
        self._json(200, config_errors())

    def _handle_config_check(self):
        self._json(200, config_check())

    def _handle_log_read(self, body):
        root = self.server.config_root
        try:
            resolved_root = resolve_config_root(root)
        except ConfigRootError as exc:
            self._json_error(503, "config_root_unavailable", str(exc))
            return

        if not isinstance(body, dict):
            self._json_error(400, "bad_request", "request body must be a JSON object")
            return

        query = body.get("query")
        if isinstance(query, str) and len(query.encode("utf-8")) > MAX_LOG_QUERY_LEN:
            self._json_error(400, "query_too_long", "query exceeds %d byte limit" % MAX_LOG_QUERY_LEN)
            return

        max_lines = body.get("max_lines")
        payload = read_log(resolved_root, query, max_lines=max_lines, secret=self.server.bridge_secret)
        status = 200 if payload.get("available") else 404
        self._json(status, payload)

    # ------------------------------------------------------------------
    # Phase B mutation handlers
    # ------------------------------------------------------------------
    # Each handler: verify auth, read + size-cap the body, validate via the
    # mutation_ops builder, issue the single bounded HA REST call, and map
    # errors: builder/ValidationError -> 400, MutationTimeout -> 504,
    # HA non-20x -> 502 (409 for an HA 409), success -> 200.

    def _handle_automation_execute(self, body):
        """POST /v1/automations/execute -- enable or disable an automation."""
        api_token = self.server.bridge_secret
        try:
            automation_id, enabled = mutation_ops.build_toggle_request(api_token, body, None)
        except mutation_ops.MutationError as exc:
            self._json_error(400, "bad_request", str(exc))
            return
        try:
            result = mutation_ops.automation_toggle(api_token, automation_id, enabled)
        except mutation_ops.MutationTimeout as exc:
            self._json_error(504, "ha_timeout", str(exc))
            return
        except mutation_ops.MutationError as exc:
            self._json_error(502, "ha_error", str(exc))
            return
        self._json(200, result)

    def _handle_automation_update(self, body):
        """POST /v1/automations/update -- replace an automation definition."""
        api_token = self.server.bridge_secret
        try:
            automation_id, definition = mutation_ops.build_automation_update_request(api_token, body, None)
        except mutation_ops.MutationError as exc:
            self._json_error(400, "bad_request", str(exc))
            return
        try:
            result = mutation_ops.automation_update(api_token, automation_id, definition)
        except mutation_ops.MutationTimeout as exc:
            self._json_error(504, "ha_timeout", str(exc))
            return
        except mutation_ops.MutationError as exc:
            self._json_error(502, "ha_error", str(exc))
            return
        self._json(200, result)

    def _handle_script_execute(self, body):
        """POST /v1/scripts/execute -- run a script by id (optional variables)."""
        api_token = self.server.bridge_secret
        try:
            script_id, variables = mutation_ops.build_script_request(api_token, body, None)
        except mutation_ops.MutationError as exc:
            self._json_error(400, "bad_request", str(exc))
            return
        try:
            result = mutation_ops.script_execute(api_token, script_id, variables)
        except mutation_ops.MutationTimeout as exc:
            self._json_error(504, "ha_timeout", str(exc))
            return
        except mutation_ops.MutationError as exc:
            self._json_error(502, "ha_error", str(exc))
            return
        self._json(200, result)

    def _handle_scene_execute(self, body):
        """POST /v1/scenes/execute -- create or replace a scene."""
        api_token = self.server.bridge_secret
        try:
            scene_id, scene_data = mutation_ops.build_scene_request(api_token, body, None)
        except mutation_ops.MutationError as exc:
            self._json_error(400, "bad_request", str(exc))
            return
        try:
            result = mutation_ops.scene_write(api_token, scene_id, scene_data)
        except mutation_ops.MutationTimeout as exc:
            self._json_error(504, "ha_timeout", str(exc))
            return
        except mutation_ops.MutationError as exc:
            self._json_error(502, "ha_error", str(exc))
            return
        self._json(200, result)

    # ------------------------------------------------------------------
    # Auth and body plumbing
    # ------------------------------------------------------------------

    def _auth_ok(self):
        """Verify the dedicated bridge secret. Returns True on success and
        sends the 401 on failure (the request is otherwise not processed)."""
        secret = self.server.bridge_secret
        provided = extract_bearer(self.headers.get("Authorization", ""))
        ok = secret and constant_time_compare(provided, secret)
        if not ok:
            self._json_error(401, "unauthorized", "missing or invalid bridge secret")
            return False
        return True

    def _read_body(self):
        """Read and size-cap the request body; returns parsed JSON or None
        (error already sent)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_error(400, "bad_request", "invalid Content-Length")
            return None
        if length < 0:
            self._json_error(400, "bad_request", "invalid Content-Length")
            return None
        if length > MAX_REQUEST_BODY:
            self._json_error(413, "request_too_large", "request body exceeds %d bytes" % MAX_REQUEST_BODY)
            return None
        try:
            raw = self.rfile.read(length) if length else b""
        except Exception:
            self._json_error(400, "bad_request", "could not read request body")
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json_error(400, "bad_request", "request body must be valid UTF-8 JSON")
            return None

    # ------------------------------------------------------------------
    # Response plumbing
    # ------------------------------------------------------------------

    def _json(self, status, payload):
        self._send_json(status, payload)

    def _json_error(self, status, code, message):
        self._send_json(status, {"ok": False, "error": {"code": code, "message": message}})

    def _send_json(self, status, payload):
        body = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
        if len(body) > 512 * 1024:
            body = b'{"ok":false,"error":{"code":"response_too_large","message":"response exceeds cap"}}'
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    config_root = None
    bridge_secret = None
    access_log = []

    def __init__(self, addr, handler, config_root, bridge_secret):
        super().__init__(addr, handler)
        self.config_root = config_root
        self.bridge_secret = bridge_secret


def serve(port=BRIDGE_PORT, config_root=None, bridge_secret=None):
    """Start the bridge server; blocks until the process is signaled."""
    config_root = resolve_config_root(config_root)
    handler = BridgeHandler
    server = BridgeServer(
        (BRIDGE_HOST, port),
        handler,
        config_root,
        bridge_secret,
    )
    LOG.info("ha-ops-bridge v%s listening on %s:%s", __version__, BRIDGE_HOST, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return server


def main(argv=None, bridge_secret=None):
    if argv is None:
        argv = sys.argv[1:]

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from .bridge_options import OPTIONS_PATH, OptionsError, load_bridge_token

    port = int(os.environ.get("HA_OPS_BRIDGE_PORT", str(BRIDGE_PORT)))
    # Production: the secret comes from the app options file at
    # /data/options.json (key `bridge_token`), written by the Supervisor.
    # HA_OPS_BRIDGE_TOKEN is a test-only override and is not set by the
    # production entrypoint.
    # The production entrypoint reads the Supervisor-owned options file while
    # still root, then passes the validated secret in memory after dropping
    # privileges. Direct/test launches may still use the environment override.
    secret = bridge_secret or os.environ.get("HA_OPS_BRIDGE_TOKEN", "")
    if not secret:
        try:
            secret = load_bridge_token(OPTIONS_PATH)
        except OptionsError as exc:
            LOG.error("could not load bridge_token from %s (%s); refusing to start", OPTIONS_PATH, exc)
            return 2
    config_root = os.environ.get("HA_OPS_BRIDGE_CONFIG_ROOT") or None

    if not is_valid_secret_length(secret):
        LOG.error("bridge_token is missing or shorter than %d chars; refusing to start", MIN_TOKEN_LENGTH)
        return 2

    # Fail closed on a missing or invalid config root before we accept traffic.
    from .config_root import resolve_config_root as _resolve

    try:
        _resolve(config_root)
    except ConfigRootError as exc:
        LOG.warning("config root not resolvable at startup (%s); endpoints will report unavailable", exc)

    serve(port=port, config_root=config_root, bridge_secret=secret)
    return 0


if __name__ == "__main__":
    sys.exit(main())
