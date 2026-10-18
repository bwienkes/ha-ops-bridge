#!/bin/sh
# ha-ops-bridge entrypoint.
#
# Reads /data/options.json directly with a tiny Python bootstrap (no
# grep/sed on the JSON), validates bridge_token (16-128 chars), never
# prints the secret, fails closed on any failure, then runs the bridge
# in the same process.
set -eu

cd /app

exec /usr/local/bin/python - <<'BOOT'
import os
import sys

from app.bridge_options import OPTIONS_PATH, OptionsError, load_bridge_token
from app.auth import is_valid_secret_length

try:
    token = load_bridge_token()
except OptionsError as exc:
    # OptionsError messages never embed the secret (see bridge_options).
    print("bridge: refusing to start: %s" % exc, file=sys.stderr)
    sys.exit(1)

if not is_valid_secret_length(token):
    print("bridge: refusing to start: bridge_token is not 16-128 chars", file=sys.stderr)
    sys.exit(1)

# Home Assistant protects /data/options.json for root. Read it first, then
# permanently drop every supplementary group plus the real/effective uid/gid
# before importing or starting the network service.
try:
    os.setgroups([])
    os.setgid(int(os.environ["BRIDGE_GID"]))
    os.setuid(int(os.environ["BRIDGE_UID"]))
except (KeyError, OSError, ValueError):
    print("bridge: refusing to start: could not drop runtime privileges", file=sys.stderr)
    sys.exit(1)

print("bridge: options verified, starting on port 8947", file=sys.stderr)

from app import main as bridge_main

sys.exit(bridge_main.main())
BOOT
