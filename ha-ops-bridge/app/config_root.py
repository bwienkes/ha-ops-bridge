"""Config-root resolution for the ha-ops-bridge Phase A runtime.

Production: the bridge reads the HA config tree from the single fixed
Supervisor mount target ``/homeassistant`` (produced by
``map: [{type: homeassistant_config, read_only: true}]``). No fallback
scanning, no ``HOMEASSISTANT_PATH`` assumption, no ``/config`` fallback,
no ``~/config`` fallback. Startup fails closed unless
``/homeassistant/configuration.yaml`` exists and the resolved root stays
inside ``/homeassistant``.

Testing only: ``HA_OPS_BRIDGE_CONFIG_ROOT`` overrides the root so the test
suite can point at a deterministic fixture tree. This env var is never
read on the production path except as the single test override.
"""

from __future__ import annotations

import os

DEFAULT_CONFIG_ROOT = "/homeassistant"
CONFIG_ROOT_ENV = "HA_OPS_BRIDGE_CONFIG_ROOT"

CONFIGURATION_FILE = "configuration.yaml"


class ConfigRootError(Exception):
    """Raised when the config root is missing, malformed, or unsafe."""


def read_config_root_env():
    """Return the configured root path or the fixed default mount target."""
    value = os.environ.get(CONFIG_ROOT_ENV)
    if value:
        return value.strip()
    return DEFAULT_CONFIG_ROOT


def resolve_config_root(root=None):
    """Resolve and validate the HA config root.

    Returns the absolute root path when the root is a directory that
    contains a real (non-symlink-to-outside) ``configuration.yaml`` and the
    resolved root does not escape itself via a symlink. Fails closed
    (ConfigRootError) when:
      - the root path is empty or relative,
      - the root directory does not exist (mapping absent),
      - the root is not a directory,
      - configuration.yaml is absent from the root,
      - configuration.yaml resolves outside the root directory
        (symlink escape),
      - the resolved root directory does not exist (root symlinked away).
    """
    path = root if root is not None else read_config_root_env()
    if not path:
        raise ConfigRootError("config root is empty")

    root_path = os.path.abspath(path)
    if not os.path.isabs(root_path):
        raise ConfigRootError("config root is not absolute")

    if not os.path.isdir(root_path):
        raise ConfigRootError("config root directory is absent")

    # The resolved root must remain the same directory: a root that is a
    # symlink must still resolve to an existing directory at that location.
    real_root = os.path.realpath(root_path)
    if not os.path.isdir(real_root):
        raise ConfigRootError("config root resolved outside expected mount")

    config_file = os.path.join(root_path, CONFIGURATION_FILE)
    if not os.path.isfile(config_file):
        raise ConfigRootError("configuration.yaml is absent from config root")

    # configuration.yaml must resolve inside the root (no symlink escape).
    real_config = os.path.realpath(config_file)
    real_parent = os.path.realpath(os.path.dirname(real_config))
    if real_parent != real_root:
        raise ConfigRootError("configuration.yaml resolves outside config root")

    return root_path


def assert_under_root(path, root):
    """Verify *path* resolves inside *root* (path-boundary safe).

    Uses realpath on both sides and compares with an explicit
    parent-directory check so sibling prefixes (e.g. ``/homeassistant2``)
    are never accepted. Returns the resolved path on success; raises
    ConfigRootError on escape.
    """
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(path)
    if real_path != real_root and os.path.dirname(real_path) != real_root:
        # Allow subpaths, not just the root itself.
        prefix = real_root + os.sep
        if not real_path.startswith(prefix) and real_path != real_root:
            raise ConfigRootError("path escapes config root")
    return real_path
