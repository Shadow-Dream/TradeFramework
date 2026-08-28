# Retired Mining service helper

The old combined Agent Gateway / Engine / Mining offline installer has been
removed. It targeted the deleted Python Agent Gateway, recreated obsolete
SQLite/API/service contracts and is not a supported deployment path.

The data-mining product surface is offline. These files are retained only as
an unmounted implementation reference and must not be installed or started:

- `mining_service_supervisor.py`
- `mining-requirements.lock`

During development, TradeEngine and the Kanna-based Agent Web use the user-mode
services described in [`deploy/user/README.md`](../user/README.md). Agent Web
build/reload is handled by `scripts/build_agent_web.sh` and
`scripts/reload_agent_web.sh`; neither service installs or restarts Mining.

A future reactivation requires a new architecture review and an independent
release decision. It must not restore `agent_gateway/**`,
`trade-agent-gateway.service`, Gateway SQLite, or the retired
`/api/agent/threads|runs|events|preferences|backends` routes.

The legacy host unit is outside this repository. An administrator taking an
existing installation offline must run exactly:

```bash
sudo systemctl disable --now trade-engine-mining-observation.service
```
