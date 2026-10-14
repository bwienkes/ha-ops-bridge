"""Filesystem-dependent tests: yamlutil, script_reader, config_ops.

Builds deterministic temp config trees under a temp root for every test so no
real production path is touched. Covers include resolution, path-escape
rejection, safe-load bounds, object readers, health/config checks, and
secret redaction.
"""

import os
import tempfile
import unittest

from app import config_ops, script_reader, yamlutil


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class YamlUtilTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_safe_load_plain(self):
        path = _write(os.path.join(self.root, "a.yaml"), "key: value\nnum: 1\n")
        data = yamlutil.safe_load_file(path)
        self.assertEqual(data["key"], "value")
        self.assertEqual(data["num"], 1)

    def test_include_value(self):
        _write(os.path.join(self.root, "inc.yaml"), "mode: auto\n")
        path = _write(os.path.join(self.root, "main.yaml"), "inc: !include inc.yaml\n")
        data = yamlutil.safe_load_including(path, self.root)
        self.assertEqual(data["inc"], {"mode": "auto"})

    def test_include_dir(self):
        os.makedirs(os.path.join(self.root, "incdir"))
        _write(os.path.join(self.root, "incdir", "one.yaml"), "x: 1\n")
        _write(os.path.join(self.root, "incdir", "two.yaml"), "y: 2\n")
        path = _write(
            os.path.join(self.root, "main.yaml"),
            "items: !include_dir incdir\n",
        )
        data = yamlutil.safe_load_including(path, self.root)
        # dir include yields a list with one entry per file in the dir
        self.assertIsInstance(data["items"], list)
        self.assertEqual(len(data["items"]), 2)

    def test_include_relative_resolves_inside_root(self):
        _write(os.path.join(self.root, "sub", "leaf.yaml"), "k: v\n")
        _write(os.path.join(self.root, "sub", "top.yaml"), "leaf: !include leaf.yaml\n")
        data = yamlutil.safe_load_including(
            os.path.join(self.root, "sub", "top.yaml"), self.root
        )
        self.assertEqual(data["leaf"], {"k": "v"})

    def test_rejects_path_escape(self):
        # An include pointing outside the root must fail closed.
        outside = self.tmp.name  # the temp base
        _write(os.path.join(outside, "outside.yaml"), "secret: 1\n")
        path = _write(os.path.join(self.root, "main.yaml"), "o: !include ../outside.yaml\n")
        with self.assertRaises(yamlutil.YAMLError):
            yamlutil.safe_load_including(path, self.root)

    def test_rejects_unsupported_include_file_tag(self):
        path = _write(os.path.join(self.root, "main.yaml"), "x: !include_file inc.yaml\n")
        with self.assertRaises(yamlutil.YAMLError):
            yamlutil.safe_load_including(path, self.root)

    def test_rejects_include_var_tag(self):
        path = _write(os.path.join(self.root, "main.yaml"), "x: !include_var env\n")
        with self.assertRaises(yamlutil.YAMLError):
            yamlutil.safe_load_including(path, self.root)

    def test_rejects_include_str_tag(self):
        path = _write(os.path.join(self.root, "main.yaml"), "x: !include_str inc.yaml\n")
        with self.assertRaises(yamlutil.YAMLError):
            yamlutil.safe_load_including(path, self.root)

    def test_rejects_too_deep_include_chain(self):
        # Build a chain deeper than MAX_INCLUDE_DEPTH to trip the depth budget.
        prev = None
        base = self.root
        depth = yamlutil.MAX_INCLUDE_DEPTH + 5
        # Build bottom-up: each level includes the previous.
        for i in range(depth, 0, -1):
            name = "lvl%d.yaml" % i
            if i == depth:
                content = "leaf: 1\n"
            else:
                content = "next: !include lvl%d.yaml\n" % (i + 1)
            p = os.path.join(base, name)
            _write(p, content)
            prev = p
        with self.assertRaises(yamlutil.YAMLError):
            yamlutil.safe_load_including(prev, self.root)

    def test_safe_load_file_rejects_bad_yaml(self):
        path = _write(os.path.join(self.root, "bad.yaml"), "a: b: c: d\n  bad\n")
        with self.assertRaises(yamlutil.YAMLError):
            yamlutil.safe_load_file(path)


class ScriptReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.scripts = os.path.join(self.root, "scripts.yaml")
        self.automations = os.path.join(self.root, "automations.yaml")

    def _mk(self, obj_id, extra=""):
        _write(
            self.scripts,
            "%s:\n  alias: %s\n  mode: single\n  sequence: []\n" % (obj_id, obj_id)
            + extra,
        )

    def test_read_script_found(self):
        self._mk("laundry")
        resolved = script_reader.read_object(self.root, "laundry", "script")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["source_file"], "scripts.yaml")
        self.assertIn("yaml", resolved)
        resp = script_reader.build_read_response("script", "laundry", resolved, "laundry")
        self.assertTrue(resp["found"])
        self.assertEqual(resp["object_id"], "laundry")
        self.assertIn("yaml", resp)
        self.assertTrue(resp.get("object_hash"))

    def test_read_script_missing(self):
        _write(self.scripts, "scripts: {}\n")
        resolved = script_reader.read_object(self.root, "nope", "script")
        self.assertIsNone(resolved)
        resp = script_reader.build_read_response("script", "nope", resolved, "nope")
        self.assertFalse(resp["found"])
        self.assertEqual(resp["object_id"], "nope")

    def test_read_automation_found(self):
        _write(
            self.automations,
            "clean:\n  alias: clean\n  trigger: {}\n  action: []\n",
        )
        resolved = script_reader.read_object(self.root, "clean", "automation")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["source_file"], "automations.yaml")
        resp = script_reader.build_read_response("automation", "clean", resolved, "clean")
        self.assertTrue(resp["found"])
        self.assertEqual(resp["object_id"], "clean")

    def test_read_script_rejects_path_id(self):
        _write(self.scripts, "scripts: {}\n")
        with self.assertRaises(script_reader.IdError):
            script_reader.read_object(self.root, "a/b", "script")

    def test_read_script_requires_root(self):
        _write(self.scripts, "scripts: {}\n")
        resolved = script_reader.read_object("", "x", "script")
        self.assertIsNone(resolved)

    def test_build_read_response_shape(self):
        self._mk("laundry")
        resolved = script_reader.read_object(self.root, "laundry", "script")
        resp = script_reader.build_read_response("script", "laundry", resolved, "laundry")
        for key in (
            "found", "object_id", "source_file", "source_hash",
            "object_hash", "object", "yaml", "read_only",
        ):
            self.assertIn(key, resp)
        missing = script_reader.build_read_response("script", "nope", None, "nope")
        self.assertFalse(missing["found"])
        self.assertTrue(missing["read_only"])
        self.assertEqual(missing["object_id"], "nope")


class ConfigOpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self._cfg = os.path.join(self.root, "configuration.yaml")
        _write(self._cfg, "homeassistant:\n  name: Test\n")

    def test_check_config_root_ok(self):
        ok, reason = config_ops.check_config_root(self.root)
        self.assertTrue(ok)

    def test_check_config_root_unavailable(self):
        ok, reason = config_ops.check_config_root(os.path.join(self.tmp.name, "missing"))
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_health_payload_shape(self):
        payload = config_ops.health_payload(self.root, version="test")
        self.assertEqual(payload["service"], "ha-ops-bridge")
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["config_root"], "mounted")

    def test_health_payload_unavailable(self):
        payload = config_ops.health_payload(os.path.join(self.tmp.name, "missing"))
        self.assertEqual(payload["config_root"], "unavailable")

    def test_redact_secret_replaces_values(self):
        text = "password: hunter2\ntoken: abc\ncurrent_password: x\n"
        redacted = config_ops.redact_secret(text, "hunter2")
        self.assertIn("password: [REDACTED]", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertIn("token: abc", redacted)
        self.assertIn("current_password: x", redacted)

    def test_redact_secret_keeps_other_lines(self):
        text = "host: example\npassword: secret\n"
        redacted = config_ops.redact_secret(text, "secret")
        self.assertIn("host: example", redacted)
        self.assertIn("password: [REDACTED]", redacted)
        self.assertNotIn("secret", redacted)


if __name__ == "__main__":
    unittest.main()
