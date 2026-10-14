"""Pure-function tests: idval, limits, auth, bridge_options, config_root.

No filesystem dependence except a couple of temp-file fixtures for
bridge_options; config_root tests are constructed with temp dirs.
"""

import json
import os
import tempfile
import unittest

from app import auth, bridge_options, config_root, idval, limits


class IdvalTest(unittest.TestCase):
    def test_normal_bare_id(self):
        self.assertEqual(idval.normalize_id("my_script"), "my_script")

    def test_strips_script_prefix(self):
        self.assertEqual(idval.normalize_id("script.laundry.overlay"), "laundry.overlay")

    def test_strips_automation_prefix(self):
        self.assertEqual(idval.normalize_id("automation.clean", "clean") if False else idval.normalize_id("automation.clean"), "clean")

    def test_trims_surrounding_whitespace(self):
        self.assertEqual(idval.normalize_id("  foo  "), "foo")

    def test_rejects_none(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id(None)

    def test_rejects_non_string(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id(123)

    def test_rejects_empty(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id("   ")

    def test_rejects_internal_whitespace(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id("has space")

    def test_rejects_path_slash(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id("a/b")

    def test_rejects_backslash(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id("a\\b")

    def test_rejects_shell_metachar(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id("foo;ls")

    def test_rejects_dot_relative(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id("..hidden")

    def test_rejects_over_length(self):
        with self.assertRaises(idval.IdError):
            idval.normalize_id("x" * (idval.MAX_OBJECT_ID_LEN + 1))

    def test_accepts_max_length(self):
        self.assertEqual(idval.normalize_id("x" * idval.MAX_OBJECT_ID_LEN), "x" * idval.MAX_OBJECT_ID_LEN)


class LimitsTest(unittest.TestCase):
    def test_clamp_int_within(self):
        self.assertEqual(limits.clamp_int(5, 1, 10), 5)

    def test_clamp_int_below(self):
        self.assertEqual(limits.clamp_int(0, 1, 10), 1)

    def test_clamp_int_above(self):
        self.assertEqual(limits.clamp_int(99, 1, 10), 10)

    def test_clamp_int_bad_falls_to_hi(self):
        self.assertEqual(limits.clamp_int("bad", 1, 10), 10)

    def test_clamp_bytes_negative(self):
        self.assertEqual(limits.clamp_bytes(-3, 100), 0)

    def test_clamp_bytes_within(self):
        self.assertEqual(limits.clamp_bytes(50, 100), 50)

    def test_clamp_bytes_above(self):
        self.assertEqual(limits.clamp_bytes(500, 100), 100)

    def test_clamp_bytes_bad_falls_to_hi(self):
        self.assertEqual(limits.clamp_bytes("x", 100), 100)

    def test_fixed_limits(self):
        self.assertEqual(limits.MAX_LOG_LINES, 100)
        self.assertEqual(limits.MAX_SCRIPT_ID_LEN, 128)
        self.assertEqual(limits.MAX_LOG_FILE_BYTES, 512 * 1024)


class AuthTest(unittest.TestCase):
    SECRET = "sup3r-secret-bridge-key-0123456789"

    def test_normalize_token_bytes(self):
        self.assertEqual(auth.normalize_token(self.SECRET), self.SECRET.encode())

    def test_normalize_token_rejects_non_string(self):
        self.assertIsNone(auth.normalize_token(42))

    def test_normalize_token_rejects_empty(self):
        self.assertIsNone(auth.normalize_token(""))

    def test_is_valid_secret_length(self):
        self.assertTrue(auth.is_valid_secret_length(self.SECRET))
        self.assertFalse(auth.is_valid_secret_length("short"))
        self.assertFalse(auth.is_valid_secret_length(42))

    def test_constant_time_compare_match(self):
        self.assertTrue(auth.constant_time_compare(self.SECRET, self.SECRET))

    def test_constant_time_compare_mismatch(self):
        self.assertFalse(auth.constant_time_compare(self.SECRET, self.SECRET + "x"))

    def test_constant_time_compare_non_string(self):
        self.assertFalse(auth.constant_time_compare(self.SECRET, 42))

    def test_extract_bearer_valid(self):
        self.assertEqual(auth.extract_bearer("Bearer abc"), "abc")

    def test_extract_bearer_missing(self):
        self.assertIsNone(auth.extract_bearer(None))

    def test_extract_bearer_wrong_scheme(self):
        self.assertIsNone(auth.extract_bearer("Basic abc"))

    def test_extract_bearer_empty_token(self):
        self.assertIsNone(auth.extract_bearer("Bearer "))

    def test_extract_bearer_whitespace_token(self):
        self.assertIsNone(auth.extract_bearer("Bearer has space"))

    def test_verify_token_match(self):
        self.assertTrue(auth.verify_token(self.SECRET, self.SECRET))

    def test_verify_token_mismatch(self):
        self.assertFalse(auth.verify_token("wrong", self.SECRET))

    def test_verify_token_bad_expected(self):
        self.assertFalse(auth.verify_token(self.SECRET, "short"))

    def test_verify_token_none_provided(self):
        self.assertFalse(auth.verify_token(None, self.SECRET))


class BridgeOptionsTest(unittest.TestCase):
    def _write(self, obj):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_loads_token(self):
        path = self._write({"bridge_token": "tok-value", "other": 1})
        self.assertEqual(bridge_options.load_bridge_token(path), "tok-value")

    def test_absent_file_raises(self):
        with self.assertRaises(bridge_options.OptionsError):
            bridge_options.load_bridge_token(os.path.join(tempfile.gettempdir(), "does-not-exist.json"))

    def test_invalid_json_raises(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.addCleanup(os.remove, path)
        with self.assertRaises(bridge_options.OptionsError):
            bridge_options.load_bridge_token(path)

    def test_non_object_raises(self):
        path = self._write(["a", "b"])
        with self.assertRaises(bridge_options.OptionsError):
            bridge_options.load_bridge_token(path)

    def test_missing_key_raises(self):
        path = self._write({"other": 1})
        with self.assertRaises(bridge_options.OptionsError):
            bridge_options.load_bridge_token(path)

    def test_non_string_token_raises(self):
        path = self._write({"bridge_token": 123})
        with self.assertRaises(bridge_options.OptionsError):
            bridge_options.load_bridge_token(path)


class ConfigRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_env_override(self):
        fd, envfile = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, envfile)
        old = os.environ.get(config_root.CONFIG_ROOT_ENV)
        os.environ[config_root.CONFIG_ROOT_ENV] = envfile
        try:
            value = config_root.read_config_root_env()
            self.assertEqual(value, envfile)
        finally:
            if old is None:
                os.environ.pop(config_root.CONFIG_ROOT_ENV, None)
            else:
                os.environ[config_root.CONFIG_ROOT_ENV] = old

    def test_env_unset_returns_default(self):
        old = os.environ.get(config_root.CONFIG_ROOT_ENV)
        os.environ.pop(config_root.CONFIG_ROOT_ENV, None)
        try:
            self.assertEqual(
                config_root.read_config_root_env(),
                config_root.DEFAULT_CONFIG_ROOT,
            )
        finally:
            if old is None:
                os.environ.pop(config_root.CONFIG_ROOT_ENV, None)
            else:
                os.environ[config_root.CONFIG_ROOT_ENV] = old

    def test_resolve_requires_configuration_yaml(self):
        with self.assertRaises(config_root.ConfigRootError):
            config_root.resolve_config_root(self.root)

    def test_resolve_accepts_valid_root(self):
        cfg = os.path.join(self.root, "configuration.yaml")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("homeassistant: {}\n")
        resolved = config_root.resolve_config_root(self.root)
        self.assertEqual(os.path.normpath(resolved), os.path.normpath(self.root))

    def test_resolve_rejects_empty_string(self):
        with self.assertRaises(config_root.ConfigRootError):
            config_root.resolve_config_root("  ")

    def test_resolve_rejects_non_directory(self):
        target = os.path.join(self.root, "file.txt")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("x")
        with self.assertRaises(config_root.ConfigRootError):
            config_root.resolve_config_root(target)

    def test_assert_under_root_allows_inside(self):
        cfg = os.path.join(self.root, "configuration.yaml")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("x")
        resolved = config_root.resolve_config_root(self.root)
        nested = os.path.join(resolved, "sub", "scripts.yaml")
        os.makedirs(os.path.join(resolved, "sub"))
        config_root.assert_under_root(nested, resolved)  # should not raise

    def test_assert_under_root_rejects_escape(self):
        cfg = os.path.join(self.root, "configuration.yaml")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("x")
        resolved = config_root.resolve_config_root(self.root)
        with self.assertRaises(config_root.ConfigRootError):
            config_root.assert_under_root(os.path.join(os.path.dirname(resolved), "outside"), resolved)

    def test_assert_under_root_rejects_relative(self):
        cfg = os.path.join(self.root, "configuration.yaml")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("x")
        resolved = config_root.resolve_config_root(self.root)
        with self.assertRaises(config_root.ConfigRootError):
            config_root.assert_under_root("relative/path", resolved)


if __name__ == "__main__":
    unittest.main()
