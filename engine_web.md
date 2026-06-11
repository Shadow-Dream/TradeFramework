# Engine Web Service

The Engine web service provides a lightweight API and frontend for the current control plane. It is lane-aware: one Engine process can read `.runtime/live/lanes-manifest.json` and run multiple active pipeline lanes.

## Start

```bash
cd /data/data_jyz/trade
python3 engine_service.py \
  --host 0.0.0.0 \
  --port 30808 \
  --config /data/data_jyz/trade/.runtime/strategy-control.json \
  --public-url https://trade.duckduckrun.com
```

Local URL:

```text
http://localhost:30808
```

Public URL:

```text
https://trade.duckduckrun.com
```

## API

Read endpoints:

- `GET /api/health`
- `GET /api/summary`
- `GET /api/lanes`
- `GET /api/current?laneId=main`
- `GET /api/attachment?laneId=main`
- `GET /api/modules`
- `GET /api/packages`
- `GET /api/instances`
- `GET /api/artifacts`
- `GET /api/history?limit=100`
- `GET /api/data/sources`
- `GET /api/data/search?q=IBM`
- `GET /api/data/datasets`
- `GET /api/data/datasets/{datasetId}/bars?limit=500`
- `GET /api/backtests`
- `GET /api/backtests/{backtestId}`
- `GET /api/backtests/{backtestId}/visualization`
- `GET /api/visualizations?backtestId={backtestId}`
- `GET /api/events` as Server-Sent Events

Write endpoints:

- `POST /api/modules`
- `POST /api/packages`
- `POST /api/instances`
- `POST /api/attach?laneId=main`
- `POST /api/detach?laneId=main`
- `POST /api/artifacts`
- `POST /api/data/download`
- `POST /api/data/upload`
- `POST /api/backtests`
- `POST /api/visualizations`
- `DELETE /api/modules/{kind}/{moduleId}/versions/{version}`

The service uses the same persisted control state as `strategy_submit_api.py` and the same write lock, so module/package/instance/attach changes remain serialized.

## Pipeline Builder

Create or update a running lane without restarting Engine:

```bash
curl -X POST 'http://localhost:30808/api/attach?laneId=my-lane' \
  -H 'Content-Type: application/json' \
  -d '{
    "laneId": "my-lane",
    "strategyId": "my-strategy",
    "version": "20260526-001",
    "name": "my-strategy-20260526-001",
    "stages": {
      "inputs": ["my.input"],
      "universe": ["my.universe"],
      "signal": ["my.signal"],
      "target": ["my.target"],
      "constraint": ["my.constraint"],
      "execution": ["my.execution"],
      "analyzer": []
    },
    "market": {},
    "marketRule": "my.market-rule",
    "alphaGraph": {
      "nodes": [],
      "outputs": {}
    }
  }'
```

Read the active attachment that the frontend Pipeline Builder uses to prefill the form:

```bash
curl 'http://localhost:30808/api/attachment?laneId=my-lane'
```

The Web UI exposes this as the `Pipeline` tab. Enter a new lane id to create another active mainline, select configured component instances by stage, optionally set `marketRule` and `alphaGraph`, then press `Attach`. Engine observes `.runtime/live/lanes-manifest.json`; it should not be restarted for strategy assembly changes.

## Data And Backtests

Download public OHLCV data into the local database:

```bash
curl -X POST http://localhost:30808/api/data/download \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "alphavantage",
    "symbol": "IBM",
    "startDate": "2026-01-01",
    "endDate": "2026-05-26",
    "interval": "d",
    "datasetId": "ibm-2026-demo"
  }'
```

Upload a local CSV dataset:

```bash
curl -X POST http://localhost:30808/api/data/upload \
  -H 'Content-Type: application/json' \
  -d '{
    "datasetId": "my-bars",
    "name": "My Uploaded Bars",
    "symbol": "MY",
    "interval": "d",
    "csvText": "Date,Open,High,Low,Close,Volume\n2026-01-02,10,11,9,10.5,1000\n"
  }'
```

Run a persisted backtest from a stored dataset:

```bash
curl -X POST http://localhost:30808/api/backtests \
  -H 'Content-Type: application/json' \
  -d '{
    "laneId": "main",
    "datasetId": "ibm-2026-demo",
    "name": "ibm-ma-cross",
    "runner": "local-ohlcv-benchmark",
    "params": {
      "fastPeriod": 20,
      "slowPeriod": 50
    }
  }'
```

Read the result and the visualization spec:

```bash
curl http://localhost:30808/api/backtests
curl http://localhost:30808/api/backtests/{backtestId}
curl http://localhost:30808/api/backtests/{backtestId}/visualization
```

Current no-key data sources shown in the Web UI:

- `robinhood`: US stock/ETF OHLCV with `5m`, `10m`, `h`, and `d` intervals, for symbols such as `AAPL`, `MSFT`, and `SPY`. Intraday bars use recent-market windows.
- `nasdaq`: US stock daily OHLCV from Nasdaq public historical data, for symbols such as `AAPL`, `MSFT`, and `SPY`.
- `yahoo`: Yahoo Finance chart data with `5m`, `h`, `d`, `w`, and `m` intervals when `TRADE_DATA_PROXY` is configured.
- `binance`: crypto OHLCV from Binance public Kline data, for symbols such as `BTCUSDT` and `ETHUSDT`.
- `fred`: macro time series from FRED CSV, for symbols such as `DGS10`; values are stored as OHLC with identical open/high/low/close.

Compatibility data sources still available through the API:

- `tencent`: A-share OHLCV from Tencent public K-line data.
- `alphavantage`: works with the public demo key for limited symbols such as `IBM`; pass `apiKey` for normal usage.
- `stooq`: accepts `apiKey` when required by Stooq.
- `yahoo`: kept as a convenience source, but Yahoo may throttle or reject server-side requests.

Data source providers are registered in `market_data.DATA_SOURCE_REGISTRY`. To add another provider, implement a downloader that returns `{source, sourceUrl, symbol, csvText, rows}` and register it with source metadata and supported intervals.

Backtest and dataset state is persisted in `.runtime/engine-data.db`; exported CSV and result JSON files are stored under `.runtime/releases/_data` and `.runtime/releases/_backtests`.

The current built-in runner is `local-ohlcv-benchmark`, a deterministic OHLCV moving-average benchmark for validating the data, persistence, and visualization path. It is intentionally separate from the Lean Engine runner hook.

## Reverse Proxy

Example Nginx server block:

```nginx
server {
    listen 80;
    server_name trade.duckduckrun.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name trade.duckduckrun.com;

    ssl_certificate /etc/letsencrypt/live/trade.duckduckrun.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/trade.duckduckrun.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:30808;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/events {
        proxy_pass http://127.0.0.1:30808;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
    }
}
```

## Notes

- This is a control-plane service, not the trading Engine process itself.
- It reads lane state from `.runtime/live/lanes-manifest.json`; `.runtime/live/pipeline.json` remains the compatibility manifest for the `main` lane.
- It can mutate control-plane state through the same handlers used by `strategy_submit_api.py`.
- Engine should be configured with `pipeline-lanes-manifest=/data/data_jyz/trade/.runtime/live/lanes-manifest.json` for single-Engine multi-pipeline mode.
- Public deployment still needs authentication before exposing write endpoints on the internet.
