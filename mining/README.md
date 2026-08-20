# K Line Mining

This subsystem accumulates provider-native minute pages for long-running use. It is deliberately independent from Engine Dataset storage and publishing: collection writes only beneath `miningRoot`, and no collected job is automatically interpreted or published as a Dataset.

## Data boundary

The core does not define a fixed price/volume schema, rename fields, or convert provider records. Every successful request produces two durable artifacts:

- `raw/<job>/<date>/<page>.response`: the exact HTTP response bytes and a SHA-256 digest in SQLite.
- `records/<job>/<date>/<page>.jsonl`: provider-native JSON values, serialized one value per line without field mapping.

Provider code supplies only runtime extraction semantics: `fetch_page`, `next_cursor`, `record_identity`, `event_time`, `is_final`, `validate_page`, overlap cursor handling, and optional gap-refill cursor construction. Those interpretations never change the stored record.

`manifests/<job>.json` is rebuildable. The commit path writes only a constant-size latest-page checkpoint; an explicit manifest read rebuilds a summary plus at most 100 recent page entries from SQLite, so historical growth does not make every commit slower. `mining-state.sqlite` is the source of truth for jobs, leases, page evidence, observations, record revisions, gaps, refill tasks, metrics, and worker health.

## Reliability protocol

The worker state machine is:

```text
queued/retry_wait/succeeded -> leased -> fetching -> committing
                                               -> leased (next page)
                                               -> succeeded (scheduled idle)
                                               -> paused
provider/transport failure -> retry_wait or blocked
```

- A file lock permits one collector writer per `miningRoot`.
- A job Lease has owner, heartbeat, and expiry. An expired `leased/fetching/committing` job is returned to durable retry state.
- Raw and partition temporary files are flushed with `fsync`, atomically renamed, and their directories are flushed before the SQLite transaction advances the cursor.
- A crash after rename but before the DB commit leaves the old cursor intact. On worker startup unreferenced files are moved to `orphaned/` and counted.
- Scheduled runs rewind by a provider-defined overlap. Identity/hash checks make repeat observations idempotent; changed provider records create immutable revisions instead of overwriting evidence.
- A page whose next cursor equals its request cursor is blocked. Repeated page/next-cursor pairs in a run are also blocked.
- HTTP 408/418/429/5xx and network failures enter persisted `retry_wait`. `Retry-After` is honored; otherwise exponential backoff plus jitter is used. HTTP 401/403 and permanent 4xx errors enter `blocked`.
- Optional `continuityStep` is a numeric distance in the provider's own event-time unit. It enables generic gap indexing; it does not imply any field names or calendar. A provider can construct an opaque refill cursor for a selected gap.
- Gap maintenance is incremental: only predecessor/successor neighborhoods touched by new or revised event times are re-evaluated. Distant gaps are not scanned or rewritten.

When explicitly enabled, the Engine service supervises the worker as a separate process and restarts unexpected exits with bounded backoff. `miningAutoStart` defaults to `false`, so installing this code does not add a process or require HTTPX for existing Engine instances. A standalone worker can be managed by systemd instead; its single-writer lock causes the embedded supervisor to stand down.

## Providers and reused components

The production vertical slice is `binance-spot-klines`, using the official public `GET /api/v3/klines` endpoint without an API key. The adapter supports official minute intervals and keeps the response array exactly as returned. Binance documents a maximum page size of 1000, chronological responses, open-time identity, and mandatory backoff for 429/418 responses. Historical bulk archives can later complement REST tail collection; the official project publishes daily/monthly native files.

- Binance Spot REST specification: <https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#klinecandlestick-data>
- Binance public data project: <https://github.com/binance/binance-public-data>
- Official generated Binance Python connectors: <https://github.com/binance/binance-connector-python>
- HTTPX timeouts and pooling: <https://www.python-httpx.org/advanced/timeouts/> and <https://www.python-httpx.org/advanced/resource-limits/>
- SQLite WAL/atomic transactions: <https://sqlite.org/wal.html> and <https://sqlite.org/atomiccommit.html>

HTTPX is used for bounded timeouts and connection pooling. The official Binance SDK was not added because this collector must retain exact response bytes and uses one public endpoint; using the generated object layer would add a second representation. APScheduler was evaluated but not added: its triggers/job stores handle wake-up scheduling, while mining correctness depends on the domain cursor, Lease, evidence, and file/DB commit protocol. The SQLite `next_run_at` queue already persists the only schedule required by this vertical slice.

`deterministic-fake` is hidden from normal UI and exists for failure/revision/recovery tests. Set `miningExposeTestProvider: true` only in a development config.

## Configuration

```json
{
  "liveRoot": "/srv/trade/live",
  "releaseRoot": "/srv/trade/releases",
  "controlRoot": "/srv/trade/control",
  "miningRoot": "/srv/trade-mining",
  "miningAutoStart": true,
  "miningExposeTestProvider": false,
  "miningHttpTimeout": 20,
  "miningMaxPageBytes": 67108864,
  "miningMaxPagesPerRun": 25
}
```

The standalone Mining CLI accepts exactly the fields shown above. Every field is
required; in particular, `controlRoot` has no inferred fallback. `miningRoot` is
mandatory to enable Mining. Keep it on a local filesystem with working advisory
locks, atomic rename, and durable `fsync`. Startup rejects a root equal to,
inside, or containing `controlRoot`, `releaseRoot`, `liveRoot`, or the Engine
source tree. Without `miningRoot`, embedded Mining APIs report `disabled` and do
not create any directory.

Use a fresh root for a new deployment. An existing `mining-state.sqlite` must carry exactly the current schema version and structure fingerprint and must structurally match that declaration. Mining never adds missing authority fields, migrates an older database, or otherwise rewrites an unrecognized schema; startup fails closed before serving or starting a worker.

`miningMaxPagesPerRun` defaults to 25. After that many committed pages the worker releases its Lease, leaves the committed main/refill cursor queued, and allows another due job to run. Provider/host request slots are persisted in SQLite. Binance defaults to 0.35 seconds between requests and enforces a 0.2 second safety floor even if a lower value is configured.

The polled health endpoint performs only bounded status/indexed count queries. Full SQLite `quick_check` is available only through the local `integrity` CLI command; its last result is cached for display and cannot be triggered through HTTP.

Example Binance job:

```json
{
  "jobId": "btc-usdt-one-minute",
  "name": "BTCUSDT official minute pages",
  "provider": "binance-spot-klines",
  "providerConfig": {
    "symbol": "BTCUSDT",
    "interval": "1m",
    "startTime": 1704067200000,
    "limit": 1000
  },
  "scheduleSeconds": 60,
  "overlapRecords": 3,
  "continuityStep": 60000
}
```

Provider access and redistribution rights remain the operator's responsibility. Open source client code does not grant rights to redistribute provider data.

## CLI and API

```bash
python3 -m mining --config .runtime/strategy-control.json worker
python3 -m mining --config .runtime/strategy-control.json run-once
python3 -m mining --config .runtime/strategy-control.json list
python3 -m mining --config .runtime/strategy-control.json health
python3 -m mining --config .runtime/strategy-control.json integrity
python3 -m mining --config .runtime/strategy-control.json export JOB_ID /tmp/provider-native.jsonl
```

Authenticated UI APIs:

```text
GET  /api/mining/providers
GET  /api/mining/health
GET  /api/mining/jobs
GET  /api/mining/jobs/{jobId}
GET  /api/mining/jobs/{jobId}/records
GET  /api/mining/jobs/{jobId}/gaps
GET  /api/mining/jobs/{jobId}/manifest
GET  /api/mining/events
POST /api/mining/jobs
POST /api/mining/jobs/{jobId}/pause
POST /api/mining/jobs/{jobId}/resume
POST /api/mining/jobs/{jobId}/run-now
POST /api/mining/jobs/{jobId}/gaps/{gapId}/refill
```

Publishing is intentionally absent. A later adapter may read a frozen provider-native export and call the Engine's general Dataset capability, but it must not make the mining store a Dataset implementation or add provider fields to the Engine contract.

The current vertical slice intentionally keeps one evidence/JSONL pair per fetched page. A future, separately tested pack/compaction layer will be needed for multi-year, high-symbol deployments; it must preserve raw hashes, revision provenance, and atomic recovery rather than rewriting provider records in place.
