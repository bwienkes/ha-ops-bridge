#!/usr/bin/env python3
"""Fail-closed public-release privacy guard (reusable enforcement).

Run this BEFORE every public commit / push / release. It scans the
repository -- the working tree, and by default the full Git history -- with
``release/privacy_audit.py`` and blocks publication on any finding.

Usage
-----
    python scripts/privacy_guard.py                 # full gate (worktree + history)
    python scripts/privacy_guard.py --no-history    # worktree only (pre-commit)
    python scripts/privacy_guard.py --root PATH     # audit an explicit root
    python scripts/privacy_guard.py --root PATH --no-history

Exit codes
----------
    0  clean -- no findings (safe to publish)
    1  findings -- release blocker (publication MUST be blocked)
    2  error -- fail-closed: scanner unavailable, bad root, unexpected failure
       (a guard that cannot run must NOT be treated as "clean")

The guard is cross-platform and stdlib-only. It locates the repository root
itself (git toplevel, falling back to the parent of ``scripts/``), so the
same script works from a checked-out repo, a Git hook, or a CI runner.
"""

from __future__ import annotations

import os
import subprocess
import sys

__doc__ = None  # placeholder replaced below

_DOC = """Fail-closed public-release privacy guard. See release/privacy_audit.py."""


def _find_repo_root():
    """Locate the release repository root, preferring git."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except OSError:
        pass
    # Fallback: this script lives in <root>/scripts/.
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _load_scanner(root):
    """Import release/privacy_audit from the repo root. Raises on failure."""
    release_dir = os.path.join(root, "release")
    if not os.path.isdir(release_dir):
        raise RuntimeError("release/ directory not found at repo root: %s" % root)
    if release_dir not in sys.path:
        sys.path.insert(0, release_dir)
    import privacy_audit  # noqa: E402
    return privacy_audit


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    history = True
    root = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--help", "-h"):
            print(_DOC)
            return 0
        if a == "--no-history":
            history = False
        elif a == "--root":
            i += 1
            if i >= len(argv):
                print("PRIVACY_GUARD_ERROR --root requires an argument")
                return 2
            root = argv[i]
        elif a.startswith("--root="):
            root = a.split("=", 1)[1]
        elif a.startswith("-"):
            print("PRIVACY_GUARD_ERROR unknown flag: %s" % a)
            return 2
        else:
            root = a
        i += 1

    if root is None:
        root = _find_repo_root()
    root = os.path.abspath(root)

    # Fail-closed: an audit we cannot perform is never "clean".
    if not os.path.exists(root):
        print("PRIVACY_GUARD_ERROR path does not exist: %s" % root)
        return 2

    # The scanner lives in the guard's home repo (where release/ exists),
    # independent of the audited --root (which may be an arbitrary tree).
    try:
        pa = _load_scanner(_find_repo_root())
    except Exception as exc:
        print("PRIVACY_GUARD_ERROR scanner unavailable: %s" % exc)
        return 2

    try:
        if os.path.isfile(root):
            findings = pa.audit_file(root)
        else:
            findings = pa.audit_release(root, history=history)
    except Exception as exc:
        print("PRIVACY_GUARD_ERROR audit failed: %s" % exc)
        return 2

    if not findings:
        print("PRIVACY_GUARD_OK root=%s history=%s findings=0"
              % (root, "on" if history else "off"))
        return 0

    for f in findings:
        print(f)
    print("PRIVACY_GUARD_FAIL findings=%d" % len(findings))
    return 1


if __name__ == "__main__":
    sys.exit(main())
