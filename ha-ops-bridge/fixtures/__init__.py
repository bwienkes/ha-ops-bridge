"""Fixture roots for the ha-ops-bridge permanent test suite.

Layout (one directory per fixture root; the build script below generates
the parameterized cases at test-run time rather than committing them):

- basic/            simple scripts.yaml (laundry target), unrelated script,
                    automations, valid configuration.yaml
- config-include/   configuration.yaml using !include for script/automation
- include-dir/      !include_dir over a directory of automation mappings
- packages/         configuration.yaml with packages defining a script
- malformed/        malformed YAML in scripts.yaml
- missing-config/   scripts.yaml present, configuration.yaml absent
- traversal/        include escaping the config root via ../
- absolute-include/ include with an absolute path
- recursive/        circular includes (a -> b -> a, self)
- dup-key/          duplicate object id (duplicate key) in scripts.yaml
- unsupported-tag/  include of a tag the bridge does not support
- deep-chain/       (generated at test-run time) >32-level include chain
- oversized/        (generated at test-run time) input >10 MB
- oversized-include/ (generated at test-run time) small file, huge include
"""

from __future__ import annotations

import os
import shutil

HERE = os.path.dirname(__file__)

# Roots that exist as committed files and are reused by the tests.
STATIC_ROOTS = [
    "basic",
    "config-include",
    "include-dir",
    "packages",
    "malformed",
    "missing-config",
    "traversal",
    "absolute-include",
    "recursive",
    "dup-key",
    "unsupported-tag",
]

# Roots generated on demand into a caller-supplied sandbox (deep/oversized
# cases are produced per run so the committed tree stays small).
GENERATED_ROOTS = ["deep-chain", "oversized", "oversized-include"]

ALL_ROOTS = STATIC_ROOTS + GENERATED_ROOTS


def fixture_root(name):
    """Absolute path of a static fixture root (must be an existing dir)."""
    path = os.path.join(HERE, name)
    if not os.path.isdir(path):
        raise FileNotFoundError("fixture root missing: %s" % name)
    return path


def build_generated_root(name, sandbox):
    """Build one generated fixture root inside a sandbox directory.

    Returns the absolute path to the root. Idempotent within a sandbox:
    existing generated content is wiped and rebuilt deterministically.
    """
    root = os.path.join(sandbox, name)
    if os.path.isdir(root):
        shutil.rmtree(root)

    if name == "deep-chain":
        # scripts.yaml includes a1.yaml, a1 includes a2, ... a32 includes a33.
        # 33 include hops > MAX_INCLUDE_DEPTH (32): must fail closed.
        os.makedirs(root)
        with open(os.path.join(root, "configuration.yaml"), "w", encoding="utf-8") as fh:
            fh.write("homeassistant:\n  name: deep chain\n")
        with open(os.path.join(root, "scripts.yaml"), "w", encoding="utf-8") as fh:
            fh.write('laundry: !include a1.yaml\n')
        for i in range(1, 33):
            with open(os.path.join(root, "a%d.yaml" % i), "w", encoding="alias") as fh:
                fh.write('nested: !include a%d.yaml\n' % (i + 1))
        with open(os.path.join(root, "a33.yaml"), "w", encoding="utf-8") as fh:
            fh.write("final: value\n")
        return root

    if name == "oversized":
        # A single scripts.yaml exceeding the 10 MB aggregate byte budget.
        os.makedirs(root)
        with open(os.path.join(root, "configuration.yaml"), "w", encoding="utf-8") as fh:
            fh.write("homeassistant:\n  name: oversized\n")
        filler = "x" * (10 * 1024 * 1024 + 1)
        with open(os.path.join(root, "scripts.yaml"), "w", encoding="utf-8") as fh:
            fh.write("# oversized fixture\n")
            fh.write("laundry:\n  alias: %s\n" % filler)
        return root

    if name == "oversized-include":
        # Small scripts.yaml whose include target exceeds the byte budget.
        os.makedirs(root)
        with open(os.path.join(root, "configuration.yaml"), "w", encoding="utf-8") as fh:
            fh.write("homeassistant:\n  name: oversized include\n")
        with open(os.path.join(root, "scripts.yaml"), "w", encoding="utf-8") as fh:
            fh.write("laundry: !include big.yaml\n")
        filler = "y" * (10 * 1024 * 1024 + 1)
        with open(os.path.join(root, "big.yaml"), "w", encoding="utf-8") as fh:
            fh.write("value: %s\n" % filler)
        return root

    raise ValueError("unknown generated fixture: %s" % name)


__all__ = [
    "HERE",
    "STATIC_ROOTS",
    "GENERATED_ROOTS",
    "ALL_ROOTS",
    "fixture_root",
    "build_generated_root",
]
