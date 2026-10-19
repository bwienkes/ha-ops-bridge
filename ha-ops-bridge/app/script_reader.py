"""Script and automation readers for the ha-ops-bridge Phase A runtime.

Given a normalized object id, the reader resolves the single object from the
HA config tree and returns only that object plus bounded metadata. It never
exposes the rest of the containing file, never exposes arbitrary files, and
fails closed on any unsupported or ambiguous configuration structure.
"""

from __future__ import annotations

import hashlib
import json
import os

import yaml

from .idval import IdError, normalize_id
from .yamlutil import YAMLError, safe_load_including

SCRIPTS_FILE = "scripts.yaml"
AUTOMATIONS_FILE = "automations.yaml"
CONFIGURATION_FILE = "configuration.yaml"


class ReaderError(Exception):
    """Raised when an object cannot be resolved safely."""


def _sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _yaml_for(obj):
    """Serialize only the resolved object back to YAML for the response.

    The response `yaml` field must contain ONLY the requested object, never
    the rest of the containing file, so we re-dump the object with the safe
    dumper (deterministic, no custom constructors).
    """
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False,
                           width=10**9, allow_unicode=True)


def _load_yaml(path, root):
    """Load a YAML file with include resolution; raise ReaderError on fail."""
    try:
        return safe_load_including(path, root)
    except YAMLError as exc:
        raise ReaderError("malformed or unsupported YAML in %s: %s" % (os.path.basename(path), exc))


def _find_in_mapping(data, obj_id):
    """Return data[obj_id] when data is a mapping with that key, else None.

    The mapping form keys each object by its id (the YAML key IS the object
    id). Returns None when the key is absent. Raises ReaderError when the key
    is present but the value is not a mapping (unsupported, fail closed).
    """
    if not isinstance(data, dict):
        return None
    if obj_id not in data:
        return None
    found = data[obj_id]
    if not isinstance(found, dict):
        raise ReaderError("%s entry for %s is not a mapping" % (obj_id, data))
    return found


def _find_in_list(data, obj_id, filename):
    """Find the entry in a list-form file whose `id`/`unique_id` is obj_id.

    HA's native automations.yaml (and scripts) is a YAML LIST of automation
    objects, each identified by its in-body `id` (newer HA) or `unique_id`
    (older HA). Returns the single matched entry, or None when no entry
    matches. Raises ReaderError on ambiguous structure (more than one entry
    claims the same id) so the caller fails closed rather than guessing.
    """
    if not isinstance(data, list):
        return None
    matches = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        ident = entry.get("unique_id")
        if not isinstance(ident, str) or not ident:
            ident = entry.get("id")
        if isinstance(ident, str) and ident == obj_id:
            matches.append(entry)
            if len(matches) > 1:
                raise ReaderError(
                    "%s has ambiguous entries for %s" % (filename, obj_id)
                )
    return matches[0] if matches else None


def _resolve_from_yaml_file(root, filename, obj_id):
    """Resolve obj_id from a top-level YAML file. Returns result dict or None.

    Accepts either the mapping form (top-level mapping keyed by object id, as
    used in the existing fixtures/tests) or the HA-native list form (a list of
    objects each keyed by its `id` or `unique_id` field). Returns None when the
    file does not exist or the id is not found. Raises ReaderError when the
    file is malformed, ambiguous, or uses an unsupported include (fail closed).
    """
    path = os.path.join(root, filename)
    if not os.path.isfile(path):
        return None
    data = _load_yaml(path, root)
    if data is None:
        return None
    if isinstance(data, dict):
        found = _find_in_mapping(data, obj_id)
    elif isinstance(data, list):
        found = _find_in_list(data, obj_id, filename)
    else:
        raise ReaderError("%s is neither a mapping nor a list" % filename)
    if found is None:
        return None
    return {
        "object": found,
        "source_file": filename,
        "source_hash": _sha256_file(path),
        "yaml": _yaml_for(found),
    }


def _resolve_from_configuration_section(root, section_key, obj_id):
    """Resolve obj_id from a named top-level section of configuration.yaml."""
    path = os.path.join(root, CONFIGURATION_FILE)
    if not os.path.isfile(path):
        return None
    data = _load_yaml(path, root)
    if not isinstance(data, dict):
        raise ReaderError("configuration.yaml is not a mapping")
    section = data.get(section_key)
    if not isinstance(section, dict):
        return None
    found = section.get(obj_id)
    if found is None:
        return None
    return {
        "object": found,
        "source_file": CONFIGURATION_FILE,
        "source_hash": _sha256_file(path),
        "yaml": _yaml_for(found),
    }


def _resolve_packages(root, section_key, obj_id):
    """Resolve obj_id from packages defined in configuration.yaml."""
    path = os.path.join(root, CONFIGURATION_FILE)
    if not os.path.isfile(path):
        return None
    data = _load_yaml(path, root)
    if not isinstance(data, dict):
        return None
    packages = data.get("packages")
    if not isinstance(packages, dict):
        return None
    for pkg in packages.values():
        if not isinstance(pkg, dict):
            continue
        section = pkg.get(section_key)
        if isinstance(section, dict):
            obj = section.get(obj_id)
            if obj is not None:
                return {
                    "object": obj,
                    "source_file": CONFIGURATION_FILE,
                    "source_hash": _sha256_file(path),
                    "yaml": _yaml_for(obj),
                }
    return None


def _resolve_object(root, obj_id, kind):
    """Resolve a single object id from all supported locations.

    kind is "script" or "automation". Returns a result dict, or None if the
    id is not found in any supported location. Raises ReaderError on a
    malformed or unsupported supported location (fail closed).
    """
    if kind == "script":
        primary_file = SCRIPTS_FILE
        config_section = "script"
    else:
        primary_file = AUTOMATIONS_FILE
        config_section = "automation"

    # 1. Primary file (scripts.yaml / automations.yaml) with include support.
    result = _resolve_from_yaml_file(root, primary_file, obj_id)
    if result is not None:
        return result

    # 2. Named section in configuration.yaml.
    found = _resolve_from_configuration_section(root, config_section, obj_id)
    if found is not None:
        return found

    # 3. Packages in configuration.yaml.
    found = _resolve_packages(root, config_section, obj_id)
    if found is not None:
        return found

    return None


def read_object(root, obj_id, kind):
    """Public entry point: resolve an object by id and kind.

    Normalizes obj_id first (raising IdError on bad input). Returns a result
    dict for the found object, or None if not found anywhere supported.
    """
    bare = normalize_id(obj_id)
    return _resolve_object(root, bare, kind)


def build_read_response(kind, obj_id, resolved, found_id):
    """Build the /v1/script/read | /v1/automation/read response payload.

    `resolved` is a result dict from _resolve_object or None. `found_id` is
    the normalized id the client asked for.
    """
    if resolved is None:
        return {
            "found": False,
            "object_id": found_id,
            "read_only": True,
        }
    return {
        "found": True,
        "object_id": found_id,
        "source_file": resolved["source_file"],
        "source_hash": resolved["source_hash"],
        "object_hash": hashlib.sha256(_canonical(resolved["object"]).encode("utf-8")).hexdigest(),
        "object": resolved["object"],
        "yaml": resolved["yaml"],
        "read_only": True,
    }


def normalize_id_or_raise(raw):
    """Normalize and validate an object id; re-raises IdError for callers."""
    return normalize_id(raw)


__all__ = [
    "ReaderError",
    "read_object",
    "build_read_response",
    "normalize_id_or_raise",
    "SCRIPTS_FILE",
    "AUTOMATIONS_FILE",
    "CONFIGURATION_FILE",
    "IdError",
    "YAMLError",
]
