#!/usr/bin/env bash
set -euo pipefail

trade_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
agent_root="$trade_root/agent_web"
bun_bin="${BUN_BIN:-/home/motion/.local/bin/bun}"

if ! systemctl --user is-active --quiet trade-engine-preview.service; then
  python3 "$trade_root/scripts/prepare_engine_preview.py"
fi
cd "$agent_root"
"$bun_bin" run build
set -a
source "$trade_root/.runtime/preview/agent-web.env"
set +a
exec "$bun_bin" run ./src/server/cli.ts \
  --host 10.130.130.66 --port 30810 --strict-port --no-open
