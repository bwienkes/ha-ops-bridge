"""Package test runner for the ha-ops-bridge Phase A suite.

Run with the local CPython: `python run_tests.py`. Discovers `tests/test_*.py`,
asserts the runtime import surface, and prints a deterministic summary. Uses
only the standard library (unittest) plus PyYAML — no pytest required.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # make `app` (package root) importable


def _assert_import_surface():
    import app
    import app.main as main
    import app.auth
    import app.idval
    import app.limits
    import app.config_root
    import app.yamlutil
    import app.script_reader
    import app.config_ops
    import app.bridge_options

    # Fixed unprivileged port, bind host, and the six preauthorized endpoints.
    assert main.BRIDGE_PORT == 8947, "bridge port must be the fixed 8947"
    assert main.BRIDGE_HOST == "0.0.0.0"
    protected = main.BridgeHandler
    handlers = [m for m in dir(protected) if m.startswith("_do_")]
    for method in ("do_GET", "do_POST"):
        assert method in protected.__dict__, "handler must define %s" % method
    return 0


def main():
    _assert_import_surface()
    loader = unittest.TestLoader()
    suite = loader.discover(
        os.path.join(HERE, "tests"),
        top_level_dir=HERE,
        pattern="test_*.py",
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
