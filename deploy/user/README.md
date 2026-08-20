# User preview

The user preview runs the current working tree as two same-host services:

- TradeEngine: `http://10.130.130.66:30809`
- Agent Web: `http://10.130.130.66:30810`

TradeEngine control, release, live, session, and database state remain isolated
under `.runtime/preview`. Mining evidence lives in the current account's
repository-external `/file/share/data_jyz/.trade-engine-preview-mining` root, as
required by Mining's source/evidence isolation contract. The Engine supervises the preview-only Mining worker;
Engine BuiltIns are installed through the normal immutable publication flow.
Private strategy Projects and their resources are installed separately and are
declared through the server-owned `TRADE_AGENT_PROJECTS_JSON` setting. Agent Web uses its clean `~/.trade-agent` Event Store
and the locally installed Claude Code/Codex credentials. The two browser apps
share only TradeEngine's host-level `trade_session` cookie.

Install or refresh it as the `motion` user:

```bash
scripts/reload_agent_web.sh
```

Open <http://10.130.130.66:30809/login>. The preview accepts the same email
and password as production, but it does not copy browser sessions. Frontend
assets are read on every Engine request; `watchfiles` restarts Engine for Python
changes. Agent changes are typechecked, tested, rebuilt and restarted atomically
by `reload_agent_web.sh`. Always enter Agent Web through the TradeEngine Agent
link to verify shared authentication, return navigation and Context handoff.
Reload synchronizes account credentials and roles from the current Engine auth
database while preserving still-valid preview sessions, so normal UI iteration
does not require another sign-in. The reload script briefly stops the preview
Engine before seeding resources so the normal control-state single-writer guard
remains authoritative.

`.runtime/preview/agent-web.env` is generated with mode `0600`; it contains the
shared internal bridge token and the visible build identifier. It must not be
committed.
