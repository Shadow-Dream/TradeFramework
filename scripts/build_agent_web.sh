#!/usr/bin/env bash
set -euo pipefail

trade_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
agent_root="$trade_root/agent_web"
bun_bin="${BUN_BIN:-/home/motion/.local/bin/bun}"

cd "$agent_root"
"$bun_bin" install --frozen-lockfile
./node_modules/.bin/tsc --noEmit
"$bun_bin" test
"$bun_bin" run build
cd "$trade_root"
python3 scripts/install_agent_skills.py --check
