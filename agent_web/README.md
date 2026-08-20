# TradeEngine Agent Web

TradeEngine Agent Web is a focused Kanna fork for displaying and controlling
the coding agents already installed on the TradeEngine host. It is an Agent
session UI, not a generic chat frontend.

Only two backends are part of the product contract:

- `claude-deepseek`: Claude Code runtime with a DeepSeek API endpoint;
- `codex-openai`: Codex app-server with OpenAI/ChatGPT device authentication.

The backend is fixed when a Session is created. A Session may change models
within that backend, but Claude Code and Codex native histories are never mixed.

## Product boundary

- TradeEngine owns accounts, precise resource identity, validation and runtime
  facts.
- Agent Web owns Projects, Agent Sessions, transcripts, live tool state and
  native-session resume.
- Projects are server-approved logical workspaces: the TradeEngine root and
  explicitly configured external private strategy repositories.
- Browsers submit only `projectId`; no filesystem picker, arbitrary cwd, clone,
  remote Git or provider URL is exposed.
- The Git panel is a local, read-only diff/touched-files view.
- Engine resources enter a turn as immutable `TradeContextV1` references.
- The shared TradeEngine MCP exposes only read, inspect, validate and propose.

The first release deliberately has no GitHub integration, cloud sharing,
self-updater, arbitrary provider registry, or Agent-side Engine execution.

## Session behavior

The append-only Event Store persists prompts, per-turn model/context snapshots,
tool calls/results, native session IDs and terminal status. Browser reconnects
restore from the server snapshot. A service restart marks an active turn
`interrupted` instead of replaying it; the next turn resumes the matching native
session. `clientRequestId` and canonical input digests prevent duplicate turns.

The UI distinguishes Running, Waiting for user, Interrupted, Failed,
Reauthentication required and Completed, including current tool, start time and
last event time.

## Development

Requirements:

- Bun 1.3.5 or newer;
- Claude Code and Codex installed by the host deployment;
- a valid `~/.setdeepseek` profile or a DeepSeek key saved by an administrator;
- TradeEngine running on the same hostname so `trade_session` is shared.

From the TradeEngine repository root:

```bash
scripts/build_agent_web.sh
scripts/reload_agent_web.sh
```

The user preview is then available at `http://10.130.130.66:30810`. Enter it
through TradeEngine's `/agent` route so navigation state and optional Context
handoff are preserved.

Useful package-local commands:

```bash
bun test
bun run check
bun run build
bun run start -- --host 10.130.130.66 --port 30810 --strict-port --no-open
```

Runtime data is stored under `~/.trade-agent/`. Strategy Project source remains
in an independent private repository declared by the server-only
`TRADE_AGENT_PROJECTS_JSON` setting. The browser never receives absolute paths.

## Structure

```text
src/client/   React Agent UI and reconnecting WebSocket client
src/server/   Event Store, runtime adapters, auth bridge and Project catalog
src/shared/   exact browser/server contracts
dist/client/  built browser assets
```

Upstream provenance and license are documented in [UPSTREAM.md](UPSTREAM.md)
and [LICENSE](LICENSE).
