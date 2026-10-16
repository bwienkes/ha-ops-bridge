# Privacy policy & release checklist — ha-ops-bridge

This file is the **public-release privacy policy** for the `ha-ops-bridge`
Home Assistant Supervisor app. It states what the release ships, what data it
handles, what must never appear in a public commit, and the enforcement gate
that is run before any public repository / release action. It is tracked in the
repository so every reader and every automation agent sees the same contract.

## What this release ships

- A Home Assistant **Supervisor app** manifest (`ha-ops-bridge/config.yaml`)
  and the Python package that implements it under `ha-ops-bridge/`.
- A documented, single unprivileged TCP port (**8947**) with **Bearer-token
  auth required** on every operational route.
- Read-only inspection routes plus four bounded mutation operations, all
  executed through the **local** Home Assistant REST API.
- The privacy scanner itself: `release/privacy_audit.py` and its tests
  (`ha-ops-bridge/tests/test_privacy_audit.py`).

## Data handled

The app handles **only** the values the operator supplies through the
Supervisor UI:

| Value | How it is used | Persisted to disk in this repo? |
|-------|---------------|--------------------------------|
| `bridge_token` | Bearer auth for every route | **No.** It is only ever held in the Supervisor container's app options. The checked-in `config.yaml` default is the empty string. |

The app **never** writes configuration files, **never** stores logs or secrets
on the host filesystem, and **never** transmits any data to a third party. The
Home Assistant configuration root is mapped **read-only**.

## Placeholder convention (what is safe to publish)

A value is safe to commit only if it is a **placeholder**, not a real secret.
The scanner (`release/privacy_audit.py`) is configured so that:

- **Angle-bracket placeholders** are permitted and never flagged:
  `<BRIDGE_TOKEN>`, `<PRIVATE_KEY>`, `<HOSTNAME>`, and so on.
- **Obviously-fake literals** are permitted: `000000000000`, `192.0.2.0/24`
  (RFC 6766 documentation space), and the like.

Anything that is a **real** secret, real path, real internal address, or real
identity MUST NOT be committed.

## Documented public disclosures (allowlist)

These values are intentionally public and are the only exceptions the scanner
recognizes as disclosed:

- The **release identity** `ha-ops-bridge maintainers <ha-ops-bridge@users.noreply.github.com>`
  (noreply address — never a personal mailbox).
- The **repository URL** `https://github.com/bwienkes/ha-ops-bridge`.
- The **fixed unprivileged port** `8947` and host `0.0.0.0` (documented).

## What MUST never appear in a public commit

The scanner reports, and blocks on, the following (each is a `rule_id` in
`release/privacy_audit.py`):

- **EMAIL** — personal email addresses (the noreply identity is allowed).
- **PRIVATE_IP** — private / internal IPv4 addresses (RFC 6766 examples allowed).
- **INTERNAL_HOSTNAME** — `.local` / `.lan` / `.home` / `.internal` / `.corp`
  / `.intranet` hostnames.
- **WINDOWS_PATH** — drive-lettered paths into a Windows user / system location (Users, Windows, ProgramData, ProgramFiles).
- **CREDENTIAL_KEY** — vendor credential shapes (`AKIA…`, `ghp_`, `github_pat_`,
  `xox…`, `sk_live_…`, …).
- **PRIVATE_KEY** — `-----BEGIN … PRIVATE KEY-----` blocks.
- **WEBHOOK_URL** — Discord / Slack / Telegram / webhook URLs with tokens.
- **QUERY_SECRET** — `?token=` / `key=` / `secret=` / `password=` assignments
  whose value is not an obvious placeholder.
- **MACHINE_GUID** — Windows machine / OS GUIDs.
- **SLACK_ID** — Slack workspace / channel / user ids.
- **LARGE_NUMERIC_ID**, **HIGH_ENTROPY_TOKEN** — long random-looking ids and
  tokens.

## Pre-publication checklist (run before every public commit / push / release)

Run this checklist from the **repository root** (`ha-ops-bridge-release/`).
The guard is **fail-closed**: any finding blocks the action.

1. **Pre-commit worktree audit** — the files about to be committed are clean:

   ```
   python release/privacy_audit.py . --no-history
   ```

   Expected: `PRIVACY_AUDIT_OK ... findings=0` and exit code `0`.

2. **Full-history audit** — the complete Git history is clean (after you
   commit):

   ```
   python release/privacy_audit.py .
   ```

   Expected: `PRIVACY_AUDIT_OK ... findings=0` and exit code `0`.

3. **Guard script (documented reusable gate):**

   ```
   python scripts/privacy_guard.py            # worktree + history
   python scripts/privacy_guard.py --no-history   # worktree only
   ```

   Exits `0` only on zero findings; `1` on findings; `2` on an error.

4. **Pre-commit hook** — the tracked template `scripts/pre-commit` is a
   fail-closed gate that runs step 1 automatically on every `git commit`.
   Install once per checkout:

   ```
   cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
   ```

5. **Suite + manifest validation:**

   ```
   python ha-ops-bridge/run_tests.py
   ```

   Expected: the full suite passes (139 tests) and the manifest is valid.

**STOP.** Do not create, push, tag, or publish until steps 1 and 2 report
`findings=0`. If any finding is reported, fix the offending value to a
placeholder, re-run, and only then proceed.
