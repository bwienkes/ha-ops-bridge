"""Bounded, safe YAML handling for the ha-ops-bridge Phase A runtime.

Only PyYAML's safe loader is used. There is no arbitrary code execution: no
`FullLoader`/`UnsafeLoader`, and the include-tag constructors we register never
run host code — they only resolve paths against the config root and load more
data. The base `yaml.SafeLoader` is never mutated: each parse builds a private
subclass, so a failed parse leaves the shared loader untouched and concurrent
parses never interfere.

Supported include directives (resolved relative to the config root, and never
allowed to escape it):
  `!include <rel.yaml>`            -> value of that file (parsed as YAML)
  `!include <rel-dir>`             -> basename-keyed mapping of the dir's files
  `!include_dir_merge_named <dir>` -> same as `!include <rel-dir>` above
  `!include_dir <dir>`             -> list of parsed values, one per file

Every parsed path is resolved to its real path and checked for containment
under the config root with `config_root.assert_under_root`, which uses a
path-boundary-safe check (realpath prefix with a separator, or equality) so
neither `../` traversal, absolute includes, nor symlinks escaping the root are
served. A hard include-depth limit and an aggregate byte budget bound any
single top-level load, and a node-count limit stops alias/anchor explosion.

Any other include tag (e.g. `!include_file`, `!include_var`, `!include_str`)
is rejected up front: the loader raises YAMLError so the caller fails closed.
"""

from __future__ import annotations

import os

import yaml

from .config_root import assert_under_root, ConfigRootError

MAX_INCLUDE_DEPTH = 32
MAX_TOTAL_BYTES = 10 * 1024 * 1024  # hard aggregate bytes parsed per top-level load
MAX_LOADED_NODES = 500_000          # hard cap on parsed nodes (alias explosion)


class YAMLError(Exception):
    """Raised when a YAML document is malformed or uses an unsupported construct."""


# ---------------------------------------------------------------------------
# Raw safe loading (no include resolution).
# ---------------------------------------------------------------------------

def _safe_load_raw(stream):
    """Load a YAML stream with the stock safe loader; max one document."""
    docs = []
    for doc in yaml.safe_load_all(stream):
        docs.append(doc)
        if len(docs) >= 2:
            break
    if not docs:
        return None
    if len(docs) > 1:
        raise YAMLError("multi-document stream not supported")
    return docs[0]


# ---------------------------------------------------------------------------
# Include-tagged loading with bounded, contained resolution.
# ---------------------------------------------------------------------------

def _count_nodes(obj):
    """Count scalar/mapping/sequence nodes; raise on excessive alias explosion."""
    total = 0
    stack = [obj]
    while stack:
        node = stack.pop()
        total += 1
        if total > MAX_LOADED_NODES:
            raise YAMLError("parsed document exceeds node budget (alias expansion)")
        if isinstance(node, dict):
            for key, value in node.items():
                stack.append(key)
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return total


def _strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
        return value[1:-1]
    return value


def _resolve_include_tag(value, root, base_dir, ctx, kind):
    """Resolve an include tag value against the root without escaping it.

    `ctx` is a mutable dict holding the shared `depth` and `budget` for the
    top-level load, so every nested include draws from the same counters.
    """
    if not value:
        raise YAMLError("include tag has empty target")
    if os.path.isabs(value):
        raise YAMLError("include target must be relative: %s" % value)
    if ".." in value.replace("\\", "/").split("/"):
        raise YAMLError("include target escapes config root: %s" % value)

    candidate_from_base = os.path.join(base_dir, value)
    candidate_from_root = os.path.join(root, value)
    target = None
    if os.path.exists(candidate_from_base):
        target = candidate_from_base
    elif os.path.exists(candidate_from_root):
        target = candidate_from_root
    else:
        raise YAMLError("include target not found: %s" % value)

    # Path-boundary containment: realpath prefix with separator, or equality.
    try:
        assert_under_root(target, root)
    except ConfigRootError:
        raise YAMLError("include target escapes config root: %s" % value)

    if kind == "include":
        if os.path.isdir(target):
            return _load_dir(target, root, ctx, "include")
        return _load_file(target, root, ctx)
    # include_dir and include_dir_merge_named: a directory of YAML files.
    if not os.path.isdir(target):
        raise YAMLError("include_dir target is not a directory: %s" % value)
    return _load_dir(target, root, ctx, kind)


def _load_dir(dir_path, root, ctx, kind):
    """Load every YAML file in dir_path.

    For `include`/`include_dir_merge_named` the result is a basename-keyed
    mapping (merge-named); for `include_dir` it is a list, one entry per file.
    """
    entries = []
    for name in sorted(os.listdir(dir_path)):
        path = os.path.join(dir_path, name)
        if not os.path.isfile(path):
            continue
        if not (name.endswith(".yaml") or name.endswith(".yml")):
            continue
        entries.append((name, _load_file(path, root, ctx)))
    if kind == "include_dir":
        return [data for _name, data in entries]
    return {name: data for name, data in entries}


def _load_file(path, root, ctx):
    """Load one YAML file, resolving include tags; enforces depth + byte budget."""
    ctx["depth"] += 1
    try:
        if ctx["depth"] > MAX_INCLUDE_DEPTH:
            raise YAMLError("include depth exceeded")
        with open(path, "rb") as handle:
            raw = handle.read()
        if len(raw) > ctx["budget"]:
            raise YAMLError("aggregate parsed bytes exceeded")
        ctx["budget"] -= len(raw)
        text = raw.decode("utf-8", errors="replace")
        return _load_text_including(text, root, os.path.dirname(path), ctx)
    finally:
        ctx["depth"] -= 1


def _load_text_including(text, root, base_dir, ctx):
    """Load YAML text, resolving include tags against root under a per-parse loader."""
    if not text.strip():
        return None

    for line in text.splitlines():
        stripped = line.strip()
        for tag in ("!include_file", "!include_var", "!include_str"):
            if stripped.startswith(tag):
                raise YAMLError("unsupported include tag: %s" % tag)

    class _Loader(yaml.SafeLoader):
        pass

    def _make_constructor(kind):
        def constructor(loader_obj, node):
            value = _strip_quotes(loader_obj.construct_scalar(node))
            return _resolve_include_tag(value, root, base_dir, ctx, kind)
        return constructor

    _Loader.add_constructor("!include", _make_constructor("include"))
    _Loader.add_constructor("!include_dir", _make_constructor("include_dir"))
    _Loader.add_constructor("!include_dir_merge_named", _make_constructor("include_dir_merge_named"))
    try:
        data = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as exc:
        first = str(exc).splitlines()[0] if str(exc).splitlines() else "malformed YAML"
        raise YAMLError("malformed YAML: %s" % first)
    _count_nodes(data)
    return data


def safe_load_including(path, root):
    """Load a YAML file, resolving include tags against the config root.

    This is the entry point the script/automation readers use. A single shared
    context carries the depth counter and byte budget across the whole parse.
    """
    ctx = {"depth": 0, "budget": MAX_TOTAL_BYTES}
    return _load_file(path, root, ctx)


def safe_load(stream, root=None):
    """Load a YAML stream into Python data using only the safe loader.

    Include tags are NOT resolved here (no root context); callers that need
    include resolution should use safe_load_including.
    """
    return _safe_load_raw(stream)


def safe_load_file(path, root=None):
    """Load a YAML file from disk.

    If `root` is given, include tags are resolved against it; otherwise only
    the safe loader is used and any include tag raises YAMLError (fail closed).
    """
    if root is not None:
        return safe_load_including(path, root)
    with open(path, "rb") as handle:
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    try:
        data = _safe_load_raw(text)
    except yaml.YAMLError as exc:
        first = str(exc).splitlines()[0] if str(exc).splitlines() else "malformed YAML"
        raise YAMLError("malformed YAML: %s" % first)
    _count_nodes(data)
    return data
