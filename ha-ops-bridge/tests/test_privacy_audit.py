"""Tests for the public-release privacy audit (release/privacy_audit.py).

Run with the local CPython from the repo root:

    .._tools\\python311\\python.exe ha-ops-bridge/run_tests.py

This file is discovered by ``run_tests.py`` (unittest) and, like the rest of
the Phase A suite, uses only the standard library plus PyYAML. It is part of
the release package, so it imports the audit module from the sibling
``release/`` directory rather than a path that would leak a private layout.
"""

import os
import re
import sys
import unittest

# ---------------------------------------------------------------------------
# Import the release audit module from the sibling ``release/`` package.
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))            # .../ha-ops-bridge/tests
APP_ROOT = os.path.dirname(HERE)                              # .../ha-ops-bridge
REPO_ROOT = os.path.dirname(APP_ROOT)                         # .../ha-ops-bridge-release
RELEASE_DIR = os.path.join(REPO_ROOT, "release")
if RELEASE_DIR not in sys.path:
    sys.path.insert(0, RELEASE_DIR)

import privacy_audit  # noqa: E402  (import after path setup)


# ---------------------------------------------------------------------------
# Rule corpus
# ---------------------------------------------------------------------------

# (rule_id, concrete value) pairs that MUST be reported as findings.
# Each value is deliberately private-looking and NOT on any allowlist.
BLOCKED_SAMPLES = [
    ("EMAIL", "owner@myhomelab.net"),
    ("PRIVATE_IP", "192.168.1.25"),
    ("INTERNAL_HOSTNAME", "printer.lan.home"),
    ("WINDOWS_PATH", r"C:\Users\Owner\.ssh\id_rsa"),
    ("LARGE_NUMERIC_ID", "492843734629319713"),
    ("CREDENTIAL_KEY", "AKIAIOSFODNN7EXAMPLE"),
    ("PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----"),
    ("WEBHOOK_URL", "https://discord.com/api/webhooks/12345678/abcd1234"),
    ("QUERY_SECRET", "token=abc1234567890"),
    ("MACHINE_GUID", "3F5B2C1A-4D6E-7F80-9A0B-C1D2E3F40567"),
    ("SLACK_ID", "T1234567890ABC"),
    ("HIGH_ENTROPY_TOKEN", "sk-9f3k2jQ7xLmNpR4vZ8wBcD5eA1hYgTuJ6sK3mPnQrW"),
]

# Values that MUST be permitted (no finding) because they are documented
# disclosures, RFC 2606 example data, or placeholder/fake samples.
PERMITTED_SAMPLES = [
    # EMAIL: the documented noreply identity + example domains.
    ("EMAIL", "ha-ops-bridge@users.noreply.github.com"),
    ("EMAIL", "contact@example.com"),
    # PRIVATE_IP: RFC 2606 example space.
    ("PRIVATE_IP", "192.0.2.1"),
    # HIGH_ENTROPY_TOKEN: fake/repeated + placeholder + ordered charset runs.
    ("HIGH_ENTROPY_TOKEN", "ghp_00000000000000000000000000000000"),
    ("HIGH_ENTROPY_TOKEN", "<TOKEN>"),
    ("HIGH_ENTROPY_TOKEN", "abcdef012345"),
    # QUERY_SECRET: placeholder / short assigned value.
    ("QUERY_SECRET", "token=abcdef"),
    ("QUERY_SECRET", "token="),
]


class TestPrivacyAuditRules(unittest.TestCase):
    """Every rule must fire on a concrete private-looking sample."""

    def test_rule_count(self):
        self.assertEqual(len(privacy_audit.RULES), 12)

    def test_each_blocked_sample_is_reported(self):
        for rule_id, value in BLOCKED_SAMPLES:
            with self.subTest(rule_id=rule_id):
                findings = privacy_audit.audit_text(value)
                rule_ids = {f.rule_id for f in findings}
                self.assertIn(
                    rule_id,
                    rule_ids,
                    "expected %s to be flagged for %r" % (rule_id, value),
                )

    def test_finding_shape_and_masking(self):
        """Findings carry a rule id, a line, and a masked snippet (no leak)."""
        findings = privacy_audit.audit_text("192.168.1.25")
        self.assertTrue(findings)
        finding = findings[0]
        self.assertEqual(finding.rule_id, "PRIVATE_IP")
        self.assertIsInstance(finding.line_no, int)
        # The raw private IP must not be echoed back verbatim.
        self.assertNotIn("192.168.1.25", str(finding.snippet))


class TestPrivacyAuditAllowlists(unittest.TestCase):
    """Documented disclosures / placeholders / example data must be permitted."""

    def test_each_permitted_sample_is_not_reported(self):
        for rule_id, value in PERMITTED_SAMPLES:
            with self.subTest(rule_id=rule_id, value=value):
                findings = privacy_audit.audit_text(value)
                rule_ids = {f.rule_id for f in findings}
                self.assertNotIn(
                    rule_id,
                    rule_ids,
                    "expected %s to be permitted for %r (got %r)"
                    % (rule_id, value, sorted(rule_ids)),
                )

    def test_noreply_identity_is_the_documented_disclosure(self):
        # The release identity uses a users.noreply.github.com address; it must
        # never surface as a finding, while a personal mailbox must.
        self.assertEqual(
            privacy_audit.audit_text("ha-ops-bridge@users.noreply.github.com"),
            [],
        )
        findings = privacy_audit.audit_text("owner@myhomelab.net")
        self.assertTrue(any(f.rule_id == "EMAIL" for f in findings))


class TestPrivacyAuditTextHelpers(unittest.TestCase):
    """The masking helper must hide the concrete secret."""

    def test_mask_hides_middle(self):
        masked = privacy_audit._mask("192.168.1.25")
        self.assertNotEqual(masked, "192.168.1.25")
        self.assertTrue(masked)


if __name__ == "__main__":
    unittest.main()
