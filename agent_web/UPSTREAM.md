# Upstream provenance

`agent_web` is the TradeEngine-maintained fork of
[Kanna](https://github.com/jakemor/kanna), distributed under the MIT license in
[`LICENSE`](LICENSE).

- Upstream commit: `08dfafcd0839e2dc451cca5ea831ef4c6d7233df`
- Imported: 2026-08-16
- Upstream package version at import: `0.63.0`

The import includes the locally validated Claude Code + DeepSeek, Codex
app-server, workspace locking, and interrupted-turn recovery changes that were
used by the development preview. TradeEngine-specific changes are maintained
directly in this directory; upstream code is not downloaded at build or run
time.
