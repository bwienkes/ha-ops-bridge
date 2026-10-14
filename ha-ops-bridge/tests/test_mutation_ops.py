"""Phase B mutation-ops tests: pure request-shaping helpers and the four
mutation operations plus the _call_ha transport.

All network access is mocked: the four ops run against a patched module-level
_call_ha, and _call_ha itself runs against a patched urllib.request.urlopen.
No real HTTP is ever issued; the suite stays deterministic and stdlib-only.
"""

import io
import json
import unittest
from unittest import mock

from app import idval, limits, mutation_ops


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class ParseJsonTest(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(mutation_ops._parse_json(None))

    def test_valid_bytes(self):
        self.assertEqual(mutation_ops._parse_json(b'{"a": 1}'), {"a": 1})

    def test_invalid_returns_none(self):
        self.assertIsNone(mutation_ops._parse_json(b"not-json"))

    def test_empty_object(self):
        self.assertEqual(mutation_ops._parse_json(b"{}"), {})


class OkOrRaiseTest(unittest.TestCase):
    def test_200_returns_parsed(self):
        self.assertEqual(mutation_ops._ok_or_raise(200, {"x": 1}, "op"), {"x": 1})

    def test_201_returns_parsed(self):
        self.assertEqual(mutation_ops._ok_or_raise(201, {"x": 2}, "op"), {"x": 2})

    def test_200_none_parsed_returns_empty_dict(self):
        self.assertEqual(mutation_ops._ok_or_raise(200, None, "op"), {})

    def test_404_raises_with_status(self):
        with self.assertRaises(mutation_ops.MutationError) as ctx:
            mutation_ops._ok_or_raise(404, {"message": "not found"}, "op")
        self.assertIn("404", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))

    def test_500_uses_error_field(self):
        with self.assertRaises(mutation_ops.MutationError) as ctx:
            mutation_ops._ok_or_raise(500, {"error": "boom"}, "op")
        self.assertIn("500", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))

    def test_500_uses_detail_field(self):
        with self.assertRaises(mutation_ops.MutationError) as ctx:
            mutation_ops._ok_or_raise(500, {"detail": "bad"}, "op")
        self.assertIn("bad", str(ctx.exception))


# ---------------------------------------------------------------------------
# build_*_request pure shapers
# ---------------------------------------------------------------------------

class BuildToggleRequestTest(unittest.TestCase):
    def test_valid_true(self):
        uid, enabled = mutation_ops.build_toggle_request(
            "tok", {"automation_id": "clean", "enabled": True}, "clean")
        self.assertEqual(uid, "clean")
        self.assertTrue(enabled is True)

    def test_coerces_int_true(self):
        _, enabled = mutation_ops.build_toggle_request(
            "tok", {"automation_id": "a", "enabled": 1}, "a")
        self.assertTrue(enabled is True)

    def test_coerces_int_false(self):
        _, enabled = mutation_ops.build_toggle_request(
            "tok", {"automation_id": "a", "enabled": 0}, "a")
        self.assertTrue(enabled is False)

    def test_missing_enabled_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_toggle_request("tok", {"automation_id": "a"}, "a")

    def test_non_dict_body_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_toggle_request("tok", "body", "a")

    def test_missing_automation_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_toggle_request("tok", {"enabled": True}, "")

    def test_too_long_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_toggle_request("tok", {"enabled": True}, "x" * 251)

    def test_empty_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_toggle_request("tok", {"enabled": True}, "")


class BuildSceneRequestTest(unittest.TestCase):
    def test_valid(self):
        sid, data = mutation_ops.build_scene_request(
            "tok", {"scene_id": "12345", "scene_data": {"a": 1}}, "12345")
        self.assertEqual(sid, "12345")
        self.assertEqual(data, {"a": 1})

    def test_missing_data_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_scene_request("tok", {"scene_id": "1"}, "1")

    def test_non_dict_data_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_scene_request(
                "tok", {"scene_id": "1", "scene_data": "x"}, "1")

    def test_missing_scene_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_scene_request(
                "tok", {"scene_data": {}}, "")

    def test_too_long_scene_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_scene_request(
                "tok", {"scene_data": {}}, "x" * 251)


class BuildScriptRequestTest(unittest.TestCase):
    def test_valid_with_variables(self):
        sid, variables = mutation_ops.build_script_request(
            "tok", {"script_id": "scr", "variables": {"v": 1}}, "scr")
        self.assertEqual(sid, "scr")
        self.assertEqual(variables, {"v": 1})

    def test_no_variables_returns_none(self):
        sid, variables = mutation_ops.build_script_request(
            "tok", {"script_id": "scr"}, "scr")
        self.assertEqual(sid, "scr")
        self.assertIsNone(variables)

    def test_non_dict_variables_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_script_request(
                "tok", {"script_id": "scr", "variables": "v"}, "scr")

    def test_missing_script_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_script_request("tok", {}, "")

    def test_too_long_script_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_script_request("tok", {"variables": {}}, "x" * 251)


class BuildAutomationUpdateRequestTest(unittest.TestCase):
    def test_valid(self):
        uid, definition = mutation_ops.build_automation_update_request(
            "tok",
            {"automation_id": "auto", "new_definition": {"trigger": {}}},
            "auto",
        )
        self.assertEqual(uid, "auto")
        self.assertEqual(definition, {"trigger": {}})

    def test_missing_definition_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_automation_update_request("tok", {}, "auto")

    def test_non_dict_definition_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_automation_update_request(
                "tok", {"automation_id": "auto", "new_definition": "x"}, "auto")

    def test_missing_automation_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_automation_update_request(
                "tok", {"new_definition": {}}, "")

    def test_too_long_id_raises(self):
        with self.assertRaises(mutation_ops.MutationError):
            mutation_ops.build_automation_update_request(
                "tok", {"new_definition": {}}, "x" * 251)


# ---------------------------------------------------------------------------
# _call_ha transport (urlopen mocked)
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Context-manager response exposing read() and getcode()."""

    def __init__(self, body=b"{}", code=200):
        self._body = body
        self._code = code

    def read(self):
        return self._body

    def getcode(self):
        return self._code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CallHaTest(unittest.TestCase):
    def test_success_returns_status_and_parsed(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_FakeResponse(b'{"ok": true}', 200)) as up:
            status, parsed = mutation_ops._call_ha("tok", "POST", "/api/x", {"a": 1})
        self.assertEqual(status, 200)
        self.assertEqual(parsed, {"ok": True})
        up.assert_called_once()

    def test_creates_json_body(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_FakeResponse(b"{}", 201)) as up:
            mutation_ops._call_ha("tok", "POST", "/api/x", {"k": "v"})
        req = up.call_args.args[0]
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.unredirected_hdrs.get("Content-Type"), "application/json")
        self.assertEqual(req.unredirected_hdrs.get("Authorization"), "Bearer tok")
        self.assertEqual(json.loads(req.data), {"k": "v"})

    def test_get_has_no_body(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_FakeResponse(b"{}", 200)) as up:
            mutation_ops._call_ha("tok", "GET", "/api/y")
        req = up.call_args.args[0]
        self.assertIsNone(req.data)

    def test_passthrough_bytes_body(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_FakeResponse(b"{}", 200)) as up:
            mutation_ops._call_ha("tok", "PUT", "/api/z", b"raw")
        req = up.call_args.args[0]
        self.assertEqual(req.data, b"raw")
        self.assertEqual(req.unredirected_hdrs.get("Content-Type"), "application/json")

    def test_oversized_bytes_raises_without_call(self):
        big = b"0" * (limits.MAX_REQUEST_BODY + 1)
        with mock.patch("urllib.request.urlopen") as up:
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops._call_ha("tok", "POST", "/api/z", big)
        up.assert_not_called()

    def test_http_error_returns_status_and_parsed(self):
        err = _make_http_error(404, b'{"message": "no"}')
        with mock.patch("urllib.request.urlopen", side_effect=err):
            status, parsed = mutation_ops._call_ha("tok", "POST", "/api/x", {"a": 1})
        self.assertEqual(status, 404)
        self.assertEqual(parsed, {"message": "no"})

    def test_500_error_body_parsed(self):
        err = _make_http_error(500, b'{"detail": "denied"}')
        with mock.patch("urllib.request.urlopen", side_effect=err):
            status, parsed = mutation_ops._call_ha("tok", "POST", "/api/x", None)
        self.assertEqual(status, 500)
        self.assertEqual(parsed, {"detail": "denied"})

    def test_oserror_raises_timeout(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("timed out")):
            with self.assertRaises(mutation_ops.MutationTimeout):
                mutation_ops._call_ha("tok", "POST", "/api/x", None)

    def test_url_is_loopback_base_plus_path(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_FakeResponse(b"{}", 200)) as up:
            mutation_ops._call_ha("tok", "GET", "/api/ping")
        req = up.call_args.args[0]
        self.assertEqual(req.full_url, mutation_ops.HA_API_BASE + "/api/ping")


def _make_http_error(code, body=b"{}"):
    """Build a real urllib.error.HTTPError with a readable body."""
    err = urllib_error_factory(code, body)
    return err


def urllib_error_factory(code, body):
    import urllib.error
    fp = io.BytesIO(body)
    # HTTPError(url, code, msg, hdrs, fp) -> read()/getcode() work off fp/code
    return urllib.error.HTTPError("http://127.0.0.1:8123/api/x", code, "err", {}, fp)


# ---------------------------------------------------------------------------
# Four mutation ops (module-level _call_ha patched)
# ---------------------------------------------------------------------------

class AutomationToggleTest(unittest.TestCase):
    def test_enabled_posts_turn_on(self):
        captured = {}

        def fake_call(api_token, method, path, body):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            return 200, {"ok": True}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            result = mutation_ops.automation_toggle("tok", "clean", True)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/api/services/service/automation.turn_on")
        self.assertEqual(captured["body"], {"entity_id": "automation.clean"})

    def test_disabled_posts_turn_off(self):
        captured = {}

        def fake_call(api_token, method, path, body):
            captured["path"] = path
            captured["body"] = body
            return 200, {}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            mutation_ops.automation_toggle("tok", "vacuum", False)
        self.assertEqual(captured["path"], "/api/services/service/automation.turn_off")
        self.assertEqual(captured["body"], {"entity_id": "automation.vacuum"})

    def test_failure_status_raises(self):
        def fake_call(api_token, method, path, body):
            return 404, {"message": "no such automation"}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops.automation_toggle("tok", "missing", True)

    def test_invalid_id_raises_before_transport(self):
        called = {"n": 0}

        def fake_call(api_token, method, path, body):
            called["n"] += 1
            return 200, {}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops.automation_toggle("tok", "bad id!", True)
        self.assertEqual(called["n"], 0)


class SceneWriteTest(unittest.TestCase):
    def test_put_first(self):
        captured = []

        def fake_call(api_token, method, path, body):
            captured.append((method, path))
            return 200, {"ok": True}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            mutation_ops.scene_write("tok", "12345", {"scene_data": {}})
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], "PUT")
        self.assertEqual(captured[0][1], "/api/scenes/12345")

    def test_404_falls_back_to_create(self):
        calls = []

        def fake_call(api_token, method, path, body):
            calls.append((method, path))
            if method == "PUT":
                return 404, {"message": "not found"}
            return 201, {"ok": True}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            mutation_ops.scene_write("tok", "99999", {"scene_data": {}})
        self.assertEqual(calls[0][0], "PUT")
        self.assertEqual(calls[1], ("POST", "/api/scenes"))

    def test_404_without_retryable_body_raises(self):
        def fake_call(api_token, method, path, body):
            if method == "PUT":
                return 404, {}
            return 200, {}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops.scene_write("tok", "1", {"scene_data": {}})

    def test_non_dict_scene_data_raises_before_transport(self):
        called = {"n": 0}

        def fake_call(api_token, method, path, body):
            called["n"] += 1
            return 200, {}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops.scene_write("tok", "1", "not-a-dict")
        self.assertEqual(called["n"], 0)


class ScriptExecuteTest(unittest.TestCase):
    def test_posts_with_variables(self):
        captured = {}

        def fake_call(api_token, method, path, body):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            return 200, {"ok": True}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            result = mutation_ops.script_execute("tok", "laundry", {"temp": 40})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/api/script/laundry")
        self.assertEqual(captured["body"], {"variables": {"temp": 40}})

    def test_no_variables_posts_empty_object(self):
        captured = {}

        def fake_call(api_token, method, path, body):
            captured["body"] = body
            return 200, {}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            mutation_ops.script_execute("tok", "scr", None)
        self.assertEqual(captured["body"], {})

    def test_non_dict_variables_raises_before_transport(self):
        called = {"n": 0}

        def fake_call(api_token, method, path, body):
            called["n"] += 1
            return 200, {}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops.script_execute("tok", "scr", "vars")
        self.assertEqual(called["n"], 0)

    def test_failure_status_raises(self):
        def fake_call(api_token, method, path, body):
            return 500, {"error": "boom"}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops.script_execute("tok", "scr", None)


class AutomationUpdateTest(unittest.TestCase):
    def test_puts_replacement(self):
        captured = {}

        def fake_call(api_token, method, path, body):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            return 200, {"ok": True}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            result = mutation_ops.automation_update(
                "tok", "clean", {"trigger": {"platform": "homeassistant"}})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["method"], "PUT")
        self.assertEqual(captured["path"], "/api/automation/clean")
        self.assertEqual(captured["body"], {"trigger": {"platform": "homeassistant"}})

    def test_missing_definition_raises_before_transport(self):
        called = {"n": 0}

        def fake_call(api_token, method, path, body):
            called["n"] += 1
            return 200, {}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops.automation_update("tok", "clean", "not-a-dict")
        self.assertEqual(called["n"], 0)

    def test_failure_status_raises(self):
        def fake_call(api_token, method, path, body):
            return 404, {"message": "unknown automation"}

        with mock.patch.object(mutation_ops, "_call_ha", side_effect=fake_call):
            with self.assertRaises(mutation_ops.MutationError):
                mutation_ops.automation_update("tok", "clean", {})


if __name__ == "__main__":
    unittest.main()
