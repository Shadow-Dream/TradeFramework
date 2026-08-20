# Independent Mining service helper

The old combined Agent Gateway / Engine / Mining offline installer has been
removed. It targeted the deleted Python Agent Gateway, recreated obsolete
SQLite/API/service contracts and is not a supported deployment path.

This directory now contains only the independent Mining worker helper and its
locked Python runtime requirements:

- `mining_service_supervisor.py`
- `mining-requirements.lock`

Mining owns a separate `miningRoot`, process lock, provider configuration and
worker lifecycle. It must not package, start or probe Agent Web.

During development, TradeEngine and the Kanna-based Agent Web use the user-mode
services described in [`deploy/user/README.md`](../user/README.md). Agent Web
build/reload is handled by `scripts/build_agent_web.sh` and
`scripts/reload_agent_web.sh`; it does not install or restart Mining.

A future root deployment must package TradeEngine, Agent Web and Mining as
independent release units. It must not restore `agent_gateway/**`,
`trade-agent-gateway.service`, Gateway SQLite, or the retired
`/api/agent/threads|runs|events|preferences|backends` routes.
