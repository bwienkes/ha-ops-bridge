# ha-ops-bridge

Preauthorized Home Assistant operations bridge, distributed as a Supervisor
add-on app for the Home Assistant Supervisor (main line, post app-store
rename). The bridge reads a read-only `homeassistant_config` mapping and
serves a small authenticated API on a single fixed unprivileged TCP port.

## Repository layout

- `repository.yaml`: add-on repository manifest (repo root).
- `ha-ops-bridge/`: add-on app (slug `ha-ops-bridge`, version `1.0.0`).
  - `config.yaml`: add-on manifest.
  - `app/`: Python runtime.
  - `tests/`: test suite.
  - `entrypoint.sh`: container entrypoint.
  - `Dockerfile`: image build.

## Routes

The bridge API (port 8947) exposes ten routes behind a single Bearer token
(`GET /v1/health` is unauthenticated):

- `GET /v1/health` - liveness and capability report.
- `POST /v1/script/read` - read one `scripts.yaml` script by id.
- `POST /v1/automation/read` - read one `automations.yaml` automation by id.
- `POST /v1/config/errors` - structured YAML parse errors in the config root.
- `POST /v1/log/read` - tail of Home Assistant log lines.
- `POST /v1/config/check` - report config root state (available/valid).
- `POST /v1/automation/execute` - enable or disable one automation.
- `POST /v1/automation/update` - replace one automation's definition.
- `POST /v1/script/execute` - run one script by id, optional variables.
- `POST /v1/scene/write` - create or replace one scene.

Mutation is limited to the four pre-approved operations above, executed
via the local HA REST API with bounded bodies and timeouts. There are no
generic file routes, no direct file writes, no shell access, and no
credentials returned by any endpoint.

## Publishing

1. Publish this repository to `https://github.com/bwienkes/ha-ops-bridge`.
2. In Home Assistant, add the add-on repository with that URL.
3. Install `ha-ops-bridge` from the app store.
4. Set the `bridge_token` option (dedicated secret, 16-128 characters).

## License

MIT
