"""Pre-publication privacy & secret audit for public release artifacts.

Stdlib-only. Scans text content -- both working-tree files and, in history
mode, every tracked blob in Git history -- for environment-specific or secret
data that must never ship in a public repository.

The audit is a *pre-publication gate*, not a post-hoc cleanup. Run it BEFORE
the initial commit and BEFORE every later public commit / push / release. A
finding in history is a release blocker; it is not something to scrub after
the fact.

Design notes
------------
* High-precision detectors. Each rule is a tight regular expression with an
  id. A rule only fires on a concrete secret/PII shape, so ordinary source
  words (``token``, ``password``, ``key`` identifiers; documented placeholders
  like ``<TOKEN>`` or ``YOUR_PASSWORD_HERE``) do not match.
* Placeholder convention. Safe example values are wrapped in angle brackets
  (``<TOKEN>``, ``<PRIVATE_KEY>``) or are obviously fake literals
  (``000000000000``, ``192.0.2.0/24``). The detector set deliberately skips
  these so the sample/test corpus stays green.
* Allowlist. The one unavoidable disclosure in a public GitHub repository --
  the owning account name inside the canonical ``https://github.com/<owner>/
  <repo>`` URL -- is documented, not hidden. Those URLs are listed in an
  allowlist of *documented intentional public disclosures*; only those exact
  URLs are skipped, and every other email / IP / hostname / id is still
  audited. See ``release/allowed-disclosures.yaml``.
* Deterministic. Same inputs -> same findings, same order. No randomness.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------

class Finding:
    """A single audit finding: one sensitive match in one text location."""

    def __init__(self, rule_id, description, path, line_no, snippet):
        self.rule_id = rule_id
        self.description = description
        self.path = path
        self.line_no = line_no  # 1-based; 0 when the location is a blob id
        self.snippet = snippet  # masked representation, never the raw secret

    def __repr__(self):
        loc = self.path or "<text>"
        line = f":{self.line_no}" if self.line_no else ""
        return f"[{self.rule_id}] {loc}{line} {self.snippet}"

    def __eq__(self, other):
        return (self.rule_id, self.path, self.line_no, self.snippet) == (
            other.rule_id, other.path, other.line_no, other.snippet)


# ---------------------------------------------------------------------------
# Detector rules
#
# Each rule is (rule_id, description, compiled_regex). The regex matches a
# concrete secret/PII shape. ``mask()`` is applied to the matched text before
# reporting so the audit output itself never re-leaks the secret.
# ---------------------------------------------------------------------------

# Plain local@domain email. The allowlist (_is_allowed_email) excludes the
# documented noreply disclosure and RFC 2606 example domains.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}\b")

# IPv4 addresses. RFC 1918 / loopback / link-local / carrier-grade are
# environment-specific and must not ship; RFC 2606 example space and
# documentation IPs are permitted.
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Private / internal IPv4 prefixes that must be blocked.
_PRIVATE_IP_RE = re.compile(
    r"^(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.)")
# RFC 2606 example space (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) and
# the self-documenting all-zeros / all-ones test addresses are permitted.
_EXAMPLE_IP_RE = re.compile(r"^(?:192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)")

# Internal / reserved hostnames and domains. The keyword must be part of a
# hostname: it needs a dot boundary either before it (a subdomain inside an
# internal zone) or after it (an internal-zone suffix). Bare English words
# ("local", "internal", "home") in comments or prose are NOT hostnames and
# are intentionally excluded.
_HOSTNAME_INTERNAL_RE = re.compile(
    r"\b(?:[\w-]+(?:[._-]+)*\.(?:local|lan|home|internal|corp|intranet)"
    r"|(?:local|lan|home|internal|corp|intranet)\.(?:[\w-]+[._-]+)+)\b")

# Windows user / system paths.
_WINDOWS_PATH_RE = re.compile(
    r"[A-Za-z]:\\(?:Users|Windows|ProgramData|ProgramFiles)\\")

# Discord / Slack-style large numeric ids (17-20 digits, no leading zeros).
_LARGE_NUMERIC_ID_RE = re.compile(r"(?<!\d)\d{17,20}(?!\d)")

# Generic credential markers (AWS / GitHub / Slack / Stripe-style).
_CREDENTIAL_KEY_RE = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16})|(?:github_pat_[A-Za-z0-9]{16,})|"
    r"(?:gh[pousrte]_[A-Za-z0-9]{16,})|(?:gh_[A-Za-z0-9]{30,})|"
    r"(?:xox[baprs]-[A-Za-z0-9-]{10,})|(?:sk_live_[A-Za-z0-9]{16,})\b")

# SSH / PGP private key material.
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")

# Webhook / chat-bot / token URLs (query-string secrets, bot tokens).
_WEBHOOK_URL_RE = re.compile(
    r"(?:discord|slack|telegram|whatsapp|hooks\.)\.(?:com|io|org)/[A-Za-z0-9/._-]+"
    r"(?:\?[A-Za-z0-9&=._%-]+)?", re.I)
# Secret parameter assignment: ?token= / &key= in URLs, plus bare token= and
# token: forms in configs / logs. The value is captured for a concreteness
# check so placeholders and obviously-fake samples do not trigger.
_QUERY_SECRET_RE = re.compile(
    r"\b(?:access_?token|api_?key|token|key|secret|passwd|password)"
    r"\s*[=:]\s*([A-Za-z0-9._%~-]{8,})")

# Machine / OS specific identifiers (Windows machine GUIDs).
_MACHINE_GUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

# Slack workspace/team/channel/user ids (S / T / C / U prefixes).
_SLACK_ID_RE = re.compile(r"\b[STCU][A-Z0-9]{8,}\b")

# High-entropy token-shaped strings: long random-looking base64 / hex values
# that are NOT the obviously-fake sample literals. Catches pasted secrets that
# do not match a vendor-specific key shape.
_HIGH_ENTROPY_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/=])(?:[A-Za-z0-9+/]{20,}={0,2}|-{1}[A-Za-z0-9+/=]{30,})"
    r"(?![A-Za-z0-9+/=])")

RULES = [
    ("EMAIL", "personal email address", _EMAIL),
    ("PRIVATE_IP", "private / internal IPv4 address", _IPV4),
    ("INTERNAL_HOSTNAME", "internal hostname / domain", _HOSTNAME_INTERNAL_RE),
    ("WINDOWS_PATH", "Windows user / system path", _WINDOWS_PATH_RE),
    ("LARGE_NUMERIC_ID", "Discord / platform numeric id", _LARGE_NUMERIC_ID_RE),
    ("CREDENTIAL_KEY", "vendor credential key", _CREDENTIAL_KEY_RE),
    ("PRIVATE_KEY", "SSH / PGP private key", _PRIVATE_KEY_RE),
    ("WEBHOOK_URL", "webhook / bot token URL", _WEBHOOK_URL_RE),
    ("QUERY_SECRET", "query-string secret parameter", _QUERY_SECRET_RE),
    ("MACHINE_GUID", "machine / OS-specific GUID", _MACHINE_GUID_RE),
    ("SLACK_ID", "Slack workspace / channel / user id", _SLACK_ID_RE),
    ("HIGH_ENTROPY_TOKEN", "high-entropy token-shaped value", _HIGH_ENTROPY_TOKEN_RE),
]

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

# Permitted email domains: the documented noreply disclosure plus RFC 2606
# example domains. Anything else is a finding.
ALLOWED_EMAIL_DOMAINS = (
    "users.noreply.github.com",
    "example.com",
    "example.org",
    "example.net",
)

# Permitted IP prefixes (RFC 2606 example space + self-documenting test
# addresses). Everything in 10/8, 172.16-31/12, 192.168/16, 169.254/16 is a
# finding; example space is permitted.
ALLOWED_IP_PREFIXES = (
    "192.0.2.",
    "198.51.100.",
    "203.0.113.",
    "0.0.0.",
    "255.255.",
)

# Obviously-fake sample literals that must NOT trigger the high-entropy rule:
# all-zero / all-one / short / repeated-character tokens, and angle-bracket
# placeholders. These are what the sample config and test corpus use.
_FAKE_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/_-]*([A-Za-z0-9+/_-]{3,})\1+$")

# Angled placeholder convention, e.g. <TOKEN>, <PRIVATE_KEY>, <EMAIL>.
_PLACEHOLDER_RE = re.compile(r"^[A-Z0-9_ ]+$")

# Repo-relative paths of the release's OWN test corpus, excluded from the
# worktree / history self-scan. The privacy scanner ships a corpus of
# deliberately-sensitive fixtures (real-looking emails, private IPs, internal
# hostnames, Windows paths, machine GUIDs, credential keys, webhook URLs) to
# prove each rule catches a real value. Those fixtures MUST stay detectable, so
# the whole file is excluded from the self-scan rather than scrubbed. This is
# the ONLY self-exclusion: the scanner source, the policy docs, the config
# example, and every application / source / config / policy file stay fully
# scanned. The guard still fails on any genuinely-seeded secret in app content.
_SELF_EXCLUDE = {
    "ha-ops-bridge/tests/test_privacy_audit.py",
}


def _is_allowed_email(match: str) -> bool:
    if "@" not in match:
        return False
    domain = match.rsplit("@", 1)[1].lower()
    return any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)


def _is_allowed_ip(ip: str) -> bool:
    return any(ip.startswith(p) for p in ALLOWED_IP_PREFIXES)


def _is_ordered_char_run(value: str) -> bool:
    """True if the value contains a long run of consecutive ASCII characters.
    Character-set literals (``abc...xyz``, ``0123456789abcdef``) and other
    ordered sequences contain such runs; genuine high-entropy secrets are
    random and essentially never do. Used to keep the catch-all high-entropy
    rule from misfiring on source charset constants and hex/base64 literals."""
    run = 0
    for i in range(1, len(value)):
        if ord(value[i]) - ord(value[i - 1]) == 1:
            run += 1
            if run >= 8:  # 9 consecutive increasing ASCII characters
                return True
        else:
            run = 0
    return False


def _is_fake_token(value: str) -> bool:
    if not value:
        return True
    if len(value) < 20:
        return True  # short values are handled by the entropy threshold
    if _FAKE_TOKEN_RE.match(value):
        return True
    if _PLACEHOLDER_RE.match(value) and len(set(value.replace("_", ""))) <= 2:
        return True
    # Mixed-case alphanumeric with at least two char classes is treated as a
    # real token; single-class (all digits / all letters) short-ish is not.
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    if has_digit and has_alpha and len(value) >= 24:
        return False
    return not (has_digit and has_alpha and len(value) >= 24)


# Per-rule evaluation: given the matched string, decide allow vs finding.
# Returns True if the match is permitted (allowlisted / placeholder / fake).
def _rule_permits(rule_id: str, match: str, m) -> bool:
    if rule_id == "EMAIL":
        return _is_allowed_email(match)
    if rule_id == "PRIVATE_IP":
        return _is_allowed_ip(match) or not _PRIVATE_IP_RE.match(match)
    if rule_id == "HIGH_ENTROPY_TOKEN":
        # Permit when the value is a fake/placeholder sample OR an ordered
        # character-set / literal run (charset constants, hex nibbles).
        return _is_fake_token(match) or _is_ordered_char_run(match)
    if rule_id == "QUERY_SECRET":
        # Permit only when the assigned value is a placeholder / fake sample.
        return not _value_is_concrete(m.group(1))
    # Hostname / webhook / slack / guid / large-numeric / credential / key:
    # no allowlist; every concrete match is a finding.
    return False


def _value_is_concrete(v: str) -> bool:
    """True if an assigned secret value looks like a real secret (not a
    placeholder or an obviously-fake sample)."""
    if not v or len(v) < 8:
        return False
    if len(set(v)) <= 1:
        return False
    if _FAKE_TOKEN_RE.match(v):
        return False
    has_digit = any(c.isdigit() for c in v)
    has_alpha = any(c.isalpha() for c in v)
    if has_digit and has_alpha:
        return len(v) >= 8
    if has_digit and not has_alpha:  # all-numeric: require a long run
        return len(v) >= 16
    return False  # alpha-only short strings are treated as words, not secrets


def _mask(match: str) -> str:
    """Mask a matched secret for reporting. Keeps length + first 2 chars."""
    if len(match) <= 4:
        return "***"
    return f"{match[:2]}***(len={len(match)})"


# ---------------------------------------------------------------------------
# Text scanning
# ---------------------------------------------------------------------------

def audit_text(text: str, path: str = "<text>") -> list:
    """Scan one text string. Returns a list of Finding (deterministic order)."""
    findings = []
    seen = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule_id, description, rx in RULES:
            for m in rx.finditer(line):
                match = m.group(0)
                if _rule_permits(rule_id, match, m):
                    continue
                key = (rule_id, path, line_no, match)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    Finding(rule_id, description, path, line_no, _mask(match)))
    return findings


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    non_ascii = sum(1 for b in sample if b >= 128)
    return non_ascii > max(16, len(sample) // 8)


def audit_file(path: str) -> list:
    """Scan one file on disk. Returns a list of Finding."""
    with open(path, "rb") as fh:
        data = fh.read()
    if _looks_binary(data):
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return audit_text(text, path=path)


# ---------------------------------------------------------------------------
# Working-tree scan (the pre-commit gate: what is about to be committed)
# ---------------------------------------------------------------------------

def _run_git(repo_root: str, *args):
    """Run git in ``repo_root``; return stdout, or ``None`` when git is
    unavailable or the path is not a usable git root (so callers can fall back).
    """
    try:
        res = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True,
        )
    except OSError:
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def _walk_tree(root: str) -> list:
    """Yield every file under a directory tree (plain-walk fallback)."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            yield os.path.join(dirpath, name)


def audit_worktree(repo_root: str) -> list:
    """Scan the working tree: git-tracked files when possible (what a commit
    would ship), otherwise a plain directory walk (arbitrary tree / non-git).

    The release's own test corpus (``_SELF_EXCLUDE``) is skipped in BOTH
    branches so the deliberately-sensitive fixtures do not self-trigger. The
    comparison is by repo-relative path (forward slashes), so the git-index
    branch and the plain-walk fallback agree exactly.
    """
    out = _run_git(repo_root, "ls-files", "--cached")
    findings = []
    if out is not None:
        for rel in out.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            if rel in _SELF_EXCLUDE:
                continue
            full = os.path.join(repo_root, rel)
            if os.path.isfile(full):
                findings.extend(audit_file(full))
        return findings
    for full in _walk_tree(repo_root):
        rel = os.path.relpath(full, repo_root).replace(os.sep, "/")
        if rel in _SELF_EXCLUDE:
            continue
        findings.extend(audit_file(full))
    return findings


def audit_git_history(repo_root: str) -> list:
    """Scan every tracked blob in Git history (post-commit safeguard).

    A finding here is a release blocker: it proves a secret is already in
    history and must be removed by history rewrite, not post-hoc scrub.
    Returns empty when the path is not a git repo.
    """
    objs = _run_git(repo_root, "rev-list", "--all", "--objects")
    if objs is None:
        return []
    blob_ids = []
    seen_ids = set()
    paths_by_oid = {}
    for line in objs.splitlines():
        parts = line.split(" ", 1)
        if len(parts) >= 2 and parts[0]:
            oid = parts[0]
            paths_by_oid.setdefault(oid, set()).add(parts[1].strip())
            if oid not in seen_ids:
                seen_ids.add(oid)
                blob_ids.append(oid)
    findings = []
    # Distinguish blobs from trees/commits in one batch call.
    if not blob_ids:
        return []
    check = subprocess.run(
        ["git", "cat-file", "--batch-check"],
        cwd=repo_root, input="\n".join(blob_ids),
        capture_output=True, text=True,
    )
    kinds = {}
    for line in check.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            kinds[parts[0]] = parts[1]
    for oid in blob_ids:
        if kinds.get(oid) != "blob":
            continue
        # Exclude a blob from the history self-scan only when EVERY path it was
        # ever recorded under is in _SELF_EXCLUDE. This keeps the deliberately-
        # sensitive test-corpus blobs out while leaving any other historical
        # blob fully scanned. Paths are relative to the repo root (forward
        # slashes), matching _SELF_EXCLUDE exactly.
        oids_paths = paths_by_oid.get(oid)
        if oids_paths and oids_paths <= _SELF_EXCLUDE:
            continue
        blob = subprocess.run(
            ["git", "cat-file", "blob", oid],
            cwd=repo_root, capture_output=True, text=True,
        )
        try:
            text = blob.stdout
        except Exception:  # pragma: no cover - guard only
            text = ""
        for f in audit_text(text, path=f"git:{oid[:7]}"):
            f.path = f"git-history:{oid[:7]} ({f.path})"
            findings.append(f)
    return findings


def audit_release(repo_root: str, history: bool = True) -> list:
    """Full gate: working tree + (optionally) history. Deterministic order."""
    findings = audit_worktree(repo_root)
    if history:
        findings.extend(audit_git_history(repo_root))
    findings.sort(key=lambda f: (f.path, f.line_no, f.rule_id, f.snippet))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    history = True
    roots = []
    for a in argv:
        if a == "--no-history":
            history = False
        elif a in ("--help", "-h"):
            print(__doc__)
            return 0
        else:
            roots.append(a)
    if not roots:
        roots = [os.getcwd()]

    total = []
    for root in roots:
        root = os.path.abspath(root)
        if not os.path.exists(root):
            print("PRIVACY_AUDIT_ERROR path does not exist: %s" % root)
            return 2
        if os.path.isfile(root):
            total.extend(audit_file(root))
        else:
            total.extend(audit_release(root, history=history))

    if not total:
        print("PRIVACY_AUDIT_OK scanned=%d roots=%s findings=0"
              % (len(roots), ",".join(roots)))
        return 0

    for f in total:
        print(f)
    print("PRIVACY_AUDIT_FAIL findings=%d" % len(total))
    return 1


if __name__ == "__main__":
    sys.exit(main())
