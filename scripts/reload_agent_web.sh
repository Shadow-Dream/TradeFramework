#!/usr/bin/env bash
set -euo pipefail

trade_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$trade_root/scripts/build_agent_web.sh"
if systemctl --user is-active --quiet trade-engine-preview.service; then
  systemctl --user stop trade-engine-preview.service
fi
python3 "$trade_root/scripts/prepare_engine_preview.py"
"$trade_root/scripts/build_jupyter_ui_sync.sh"
systemctl --user link "$trade_root/deploy/user/trade-engine-preview.service" >/dev/null
systemctl --user link "$trade_root/deploy/user/trade-agent-web.service" >/dev/null
systemctl --user daemon-reload
systemctl --user enable trade-engine-preview.service trade-agent-web.service >/dev/null
systemctl --user restart trade-engine-preview.service trade-agent-web.service
systemctl --user --no-pager --full status trade-engine-preview.service trade-agent-web.service
