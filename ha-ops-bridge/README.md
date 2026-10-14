# ha-ops-bridge

A preauthorized operations bridge for a Home Assistant configuration root,
designed for the Home Assistant Supervisor (main line, post app-store
rename). The bridge exposes a small authenticated API on a single fixed
unprivileged TCP port: read-only inspection (Phase A) plus four bounded
mutation operations executed through the local HA REST API (Phase B). It
never writes configuration files directly.

## What it is

- Supervisor **app** (`slug: ha-ops-bridge`, version `1.0.0`).
- Container image built from `python:3.11-slim`; only dependency is
  `PyYAML==6.0.2`.
- The configuration root is mapped read-only to `/homeassistant` in the
  container (`homeassistant_config` mapping, `read_only: true`).
- The bridge listens on TCP **8947** (`0.0.0.0`) and serves ten routes:
  six read-only (Phase A) and four pre-approved mutation operations
  (Phase B):

  | Route | Method | Purpose |
  |-------|--------|---------|
  | `/v1/health` | GET | Liveness and capability report (unauthenticated). |
  | `/v1/script/read` | POST | Read one `scripts.yaml` script by id. |
  | `/v1/automation/read` | POST | Read one `automations.yaml` automation by id. |
  | `/v1/config/errors` | POST | Structured YAML parse errors in the config root. |
  | `/v1/log/read` | POST | Read the tail of Home Assistant log lines. |
  | `/v1/config/check` | POST | Report config root state (available/valid). |
  | `/v1/automation/execute` | POST | Enable or disable one automation (pre-approved mutation). |
  | `/v1/automation/update` | POST | Replace one automation's definition (pre-approved mutation). |
  | `/v1/script/execute` | POST | Run one script by id, optional variables (pre-approved mutation). |
  | `/v1/scene/write` | POST | Create or replace one scene (pre-approved mutation). |

- Mutation is limited to the four pre-approved operations above, executed
  against the local HA REST API with bounded bodies (64 KiB), 5 s timeouts,
  and per-operation identifier validation. No generic file route, no
  direct file writes, no shell access.

## Options

| Option | Schema | Description |
|--------|--------|-------------|
| `bridge_token` | `password(16, 128)` | Dedicated Bearer secret for the bridge API. |

The token is written by the Supervisor to `/data/options.json` (app data
volume). The entrypoint validates it at startup and refuses to start on a
missing or malformed value. The secret is never returned by any endpoint
and is never printed.

## Usage

Send `Authorization: Bearer <bridge_token>` on all POST routes. Responses
are JSON; object reads return the requested object only (`yaml` field).

Example:

```
GET  /v1/health
POST /v1/script/read {"script_id": "laundry"}
```

## Security posture

- Non-root runtime user (unprivileged UID).
- No `sudo`, `ssh`, Docker socket, `curl`/`wget`, or compiler toolchain in
  the image. No host PID/IPC namespaces; no privileged mode.
- YAML loading is a per-call `SafeLoader` subclass: no global loader
  mutation; only `!include`/`!include_dir`/`!include_dir_merge_named` are
  supported; include targets must stay inside the config root; depth and
  byte budgets fail closed.
- Identifier validation rejects path components and metacharacters.
- Bounded request/response sizes and timeouts; structured errors with no
  traceback leakage.
- The mutation routes target a fixed local HA API base constant with the
  same Bearer secret; the token is never embedded in log lines.
