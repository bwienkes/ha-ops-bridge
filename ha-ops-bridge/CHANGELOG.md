# Changelog

## 1.0.3

- Automation and script `read` now resolve both object-form and list-form
  definitions (the live `automations.yaml` stores automations as a top-level
  list), returning the matched entity with `found=true` and the same
  bounded, privacy-safe object-only response contract.

## 1.0.2

- Pass the already-validated app token directly to the service after dropping
  privileges, avoiding a second read of the root-protected options file.

## 1.0.0 — 2025-01-01

Initial release (Phase A + B).

- Supervisor app manifest (`slug: ha-ops-bridge`) with a read-only
  `homeassistant_config` mapping at `/homeassistant` and `bridge_token`
  (`password(16, 128)`) in app options.
- Ten routes on TCP port 8947: the six read-only routes (health, script
  read, automation read, config errors, log read, config check) plus four
  pre-approved mutation operations (automation toggle, automation update,
  script execute, scene write) through the local HA REST API.
- Constant-time Bearer authentication from `/data/options.json`;
  fail-closed startup validation; the secret is never returned or printed.
- Bounded, safe YAML loading (per-call SafeLoader, include containment,
  depth/byte/node budgets, object-only responses).
- Pinned `python:3.11-slim` image, `PyYAML==6.0.2` only, non-root user.
