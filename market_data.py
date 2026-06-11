#!/usr/bin/env python3
import csv
import io
import json
import math
import os
import sqlite3
import time as sleep_time
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path


SCHEMA_VERSION = 1
RUNTIME_PROXY_STATE = "data-proxy.json"
RUNTIME_SOURCE_DEFINITIONS = "data-sources.json"
RUNTIME_SOURCE_ACTIVATIONS = "data-source-activations.json"


def env_configured_proxy():
    return (
        os.environ.get("TRADE_DATA_PROXY")
        or os.environ.get("TRADE_HTTPS_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or ""
    )


def configured_proxy(config=None):
    if config:
        path = Path(config["controlRoot"]) / RUNTIME_PROXY_STATE
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            return str(payload.get("url") or "").strip()
    return env_configured_proxy()


def save_configured_proxy(config, url):
    payload = {
        "url": str(url or "").strip(),
        "updatedAt": utc_now(),
    }
    path = Path(config["controlRoot"]) / RUNTIME_PROXY_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return payload


def load_json_state(path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_json_state(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def open_request(request, timeout=30, config=None):
    proxy = configured_proxy(config)
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({
            "http": proxy,
            "https": proxy,
        }))
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def db_path(config):
    return str(Path(config["controlRoot"]) / "engine-data.db")


def connect(config):
    path = Path(db_path(config))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    initialize(conn)
    return conn


def initialize(conn):
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            interval TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            csv_path TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bars (
            dataset_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (dataset_id, ts)
        );
        CREATE TABLE IF NOT EXISTS backtests (
            backtest_id TEXT PRIMARY KEY,
            lane_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            runner TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            request_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            visualization_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS visualizations (
            visualization_id TEXT PRIMARY KEY,
            backtest_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            spec_json TEXT NOT NULL
        );
        """
    )
    conn.commit()


def dataset_root(config):
    root = Path(config["releaseRoot"]) / "_data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def result_root(config):
    root = Path(config["releaseRoot"]) / "_backtests"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_id(value):
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    text = "-".join(part for part in text.split("-") if part)
    return text or "item"


def parse_date(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value[:10]


def stooq_symbol(symbol):
    symbol = str(symbol or "").strip().lower()
    if not symbol:
        raise ValueError("symbol is required.")
    if "." not in symbol and not symbol.startswith("^"):
        symbol = f"{symbol}.us"
    return symbol


def stooq_download(symbol, start="", end="", interval="d", api_key="", config=None):
    symbol = stooq_symbol(symbol)
    interval = interval or "d"
    params = {
        "s": symbol,
        "i": interval,
    }
    if start:
        params["d1"] = parse_date(start).replace("-", "")
    if end:
        params["d2"] = parse_date(end).replace("-", "")
    if api_key:
        params["apikey"] = str(api_key).strip()
    url = "https://stooq.com/q/d/l/?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "TradeFramework/1.0"})
    with open_request(request, timeout=30, config=config) as response:
        csv_text = response.read().decode("utf-8-sig")
    rows = parse_ohlcv_csv(csv_text)
    if not rows:
        raise ValueError(f"No Stooq rows returned for {symbol}. Use symbols like AAPL.US, SPY.US, ^SPX, BTC.V.")
    return {
        "source": "stooq",
        "sourceUrl": url,
        "symbol": symbol.upper(),
        "csvText": csv_text,
        "rows": rows,
    }


def unix_day(value, fallback):
    parsed = parse_date(value)
    if not parsed:
        return fallback
    dt = datetime.combine(datetime.fromisoformat(parsed).date(), time.min, tzinfo=timezone.utc)
    return int(dt.timestamp())


def yahoo_interval(interval):
    return {
        "1m": "1m",
        "2m": "2m",
        "5m": "5m",
        "10m": "15m",
        "15m": "15m",
        "h": "1h",
        "1h": "1h",
        "d": "1d",
        "w": "1wk",
        "m": "1mo",
        "5minute": "5m",
        "10minute": "15m",
        "hour": "1h",
        "1d": "1d",
        "1wk": "1wk",
        "1mo": "1mo",
    }.get(str(interval or "d"), "1d")


def yahoo_download(symbol, start="", end="", interval="d", config=None):
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required.")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    yf_interval = yahoo_interval(interval)
    fallback_days = 7 if yf_interval in {"1m", "2m", "5m", "15m"} else 60 if yf_interval == "1h" else 365 * 5
    period1 = unix_day(start, now_ts - fallback_days * 86400)
    period2 = unix_day(end, now_ts)
    if period2 <= period1:
        raise ValueError("endDate must be after startDate.")
    params = urllib.parse.urlencode({
        "period1": period1,
        "period2": period2,
        "interval": yf_interval,
        "events": "history",
        "includeAdjustedClose": "true",
    })
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TradeFramework/1.0"})
    with open_request(request, timeout=30, config=config) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        raise ValueError(error.get("description") or str(error))
    result = (chart.get("result") or [None])[0]
    if not result:
        raise ValueError(f"No Yahoo rows returned for {symbol}.")
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for index, ts in enumerate(timestamps):
        try:
            open_ = quote.get("open", [])[index]
            high = quote.get("high", [])[index]
            low = quote.get("low", [])[index]
            close = quote.get("close", [])[index]
            volume = quote.get("volume", [])[index] or 0
        except IndexError:
            continue
        if open_ is None or high is None or low is None or close is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
            if yf_interval in {"1m", "2m", "5m", "15m", "1h"}
            else datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
        })
    if not rows:
        raise ValueError(f"No Yahoo rows returned for {symbol}.")
    return {
        "source": "yahoo",
        "sourceUrl": url,
        "symbol": symbol,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


def parse_market_number(value):
    text = str(value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return 0.0
    return float(text)


def nasdaq_download(symbol, start="", end="", interval="d", config=None):
    if interval not in {"d", "1d"}:
        raise ValueError("Nasdaq source currently supports daily bars only.")
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required.")
    start_date = parse_date(start) or "1900-01-01"
    end_date = parse_date(end)
    rows = []
    url = ""
    for asset_class in ["stocks", "etf"]:
        params = {
            "assetclass": asset_class,
            "fromdate": start_date,
            "limit": 9999,
        }
        url = f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(symbol)}/historical?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 TradeFramework/1.0",
                "Accept": "application/json, text/plain, */*",
                "Referer": f"https://www.nasdaq.com/market-activity/{asset_class}/{symbol.lower()}/historical",
            },
        )
        with open_request(request, timeout=30, config=config) as response:
            payload = json.loads(response.read().decode("utf-8"))
        table = ((payload.get("data") or {}).get("tradesTable") or {})
        rows = []
        for item in table.get("rows") or []:
            date = datetime.strptime(item["date"], "%m/%d/%Y").date().isoformat()
            if end_date and date > end_date:
                continue
            rows.append({
                "date": date,
                "open": parse_market_number(item.get("open")),
                "high": parse_market_number(item.get("high")),
                "low": parse_market_number(item.get("low")),
                "close": parse_market_number(item.get("close")),
                "volume": parse_market_number(item.get("volume")),
            })
        rows.sort(key=lambda row: row["date"])
        if rows:
            break
    if not rows:
        raise ValueError(f"No Nasdaq rows returned for {symbol}.")
    return {
        "source": "nasdaq",
        "sourceUrl": url,
        "symbol": symbol,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


def robinhood_interval(interval):
    value = str(interval or "5m").lower()
    return {
        "m": "5minute",
        "1m": "5minute",
        "5m": "5minute",
        "5minute": "5minute",
        "10m": "10minute",
        "10minute": "10minute",
        "h": "hour",
        "1h": "hour",
        "hour": "hour",
        "d": "day",
        "1d": "day",
        "day": "day",
    }.get(value, "5minute")


def robinhood_span(interval, start=""):
    rh_interval = robinhood_interval(interval)
    if rh_interval in {"5minute", "10minute"}:
        return "day"
    if rh_interval == "hour":
        return "month"
    start_date = parse_date(start)
    if start_date:
        days = (datetime.now(timezone.utc).date() - datetime.fromisoformat(start_date).date()).days
        return "5year" if days > 370 else "year"
    return "year"


def robinhood_download(symbol, start="", end="", interval="5m", config=None):
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required.")
    rh_interval = robinhood_interval(interval)
    span = robinhood_span(interval, start)
    params = {
        "interval": rh_interval,
        "span": span,
        "bounds": "regular",
    }
    url = f"https://api.robinhood.com/quotes/historicals/{urllib.parse.quote(symbol)}/?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TradeFramework/1.0"})
    last_error = None
    for attempt in range(3):
        try:
            with open_request(request, timeout=30, config=config) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise
            sleep_time.sleep(0.5 * (attempt + 1))
    start_date = parse_date(start)
    end_date = parse_date(end)
    rows = []
    for item in payload.get("historicals") or []:
        ts = item.get("begins_at")
        if not ts:
            continue
        date_key = ts[:10]
        if start_date and date_key < start_date:
            continue
        if end_date and date_key > end_date:
            continue
        rows.append({
            "date": ts,
            "open": float(item["open_price"]),
            "high": float(item["high_price"]),
            "low": float(item["low_price"]),
            "close": float(item["close_price"]),
            "volume": float(item.get("volume") or 0),
        })
    rows.sort(key=lambda row: row["date"])
    if not rows:
        raise ValueError(f"No Robinhood rows returned for {symbol}. Try a recent date range for intraday bars.")
    return {
        "source": "robinhood",
        "sourceUrl": url,
        "symbol": symbol,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


def date_timestamp_ms(value, fallback):
    parsed = parse_date(value)
    if not parsed:
        return fallback
    dt = datetime.combine(datetime.fromisoformat(parsed).date(), time.min, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def binance_interval(interval):
    return {
        "d": "1d",
        "w": "1w",
        "m": "1M",
        "1d": "1d",
        "1w": "1w",
        "1wk": "1w",
        "1mo": "1M",
    }.get(str(interval or "d"), "1d")


def binance_download(symbol, start="", end="", interval="d", config=None):
    symbol = str(symbol or "").strip().upper().replace("-", "")
    if not symbol:
        raise ValueError("symbol is required.")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    params = {
        "symbol": symbol,
        "interval": binance_interval(interval),
        "limit": 1000,
    }
    if start:
        params["startTime"] = date_timestamp_ms(start, 0)
    if end:
        params["endTime"] = date_timestamp_ms(end, now_ms)
    url = "https://data-api.binance.vision/api/v3/klines?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "TradeFramework/1.0"})
    with open_request(request, timeout=30, config=config) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = []
    for item in payload:
        rows.append({
            "date": datetime.fromtimestamp(item[0] / 1000, timezone.utc).date().isoformat(),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    if not rows:
        raise ValueError(f"No Binance rows returned for {symbol}.")
    return {
        "source": "binance",
        "sourceUrl": url,
        "symbol": symbol,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


def coinbase_granularity(interval):
    return {
        "d": 86400,
        "w": 86400,
        "m": 86400,
        "1d": 86400,
        "1wk": 86400,
        "1mo": 86400,
    }.get(str(interval or "d"), 86400)


def coinbase_download(symbol, start="", end="", interval="d", config=None):
    symbol = str(symbol or "").strip().upper().replace("/", "-")
    if not symbol:
        raise ValueError("symbol is required.")
    end_date = parse_date(end) or datetime.now(timezone.utc).date().isoformat()
    start_date = parse_date(start)
    params = {
        "granularity": coinbase_granularity(interval),
        "end": f"{end_date}T00:00:00Z",
    }
    if start_date:
        params["start"] = f"{start_date}T00:00:00Z"
    url = f"https://api.exchange.coinbase.com/products/{urllib.parse.quote(symbol)}/candles?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "TradeFramework/1.0"})
    with open_request(request, timeout=30, config=config) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = [
        {
            "date": datetime.fromtimestamp(item[0], timezone.utc).date().isoformat(),
            "open": float(item[3]),
            "high": float(item[2]),
            "low": float(item[1]),
            "close": float(item[4]),
            "volume": float(item[5]),
        }
        for item in payload
    ]
    rows.sort(key=lambda row: row["date"])
    if interval in {"w", "1wk", "m", "1mo"}:
        rows = aggregate_rows(rows, "month" if interval in {"m", "1mo"} else "week")
    if not rows:
        raise ValueError(f"No Coinbase rows returned for {symbol}.")
    return {
        "source": "coinbase",
        "sourceUrl": url,
        "symbol": symbol,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


def eastmoney_secid(symbol):
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required.")
    if "." in symbol:
        code, market = symbol.split(".", 1)
        market = market.upper()
        if market in {"SH", "SS"}:
            return f"1.{code}", symbol
        if market in {"SZ", "SHE"}:
            return f"0.{code}", symbol
    if symbol.startswith("6") or symbol.startswith("9"):
        return f"1.{symbol}", f"{symbol}.SH"
    return f"0.{symbol}", f"{symbol}.SZ"


def eastmoney_klt(interval):
    return {
        "d": "101",
        "w": "102",
        "m": "103",
        "1d": "101",
        "1wk": "102",
        "1mo": "103",
    }.get(str(interval or "d"), "101")


def eastmoney_download(symbol, start="", end="", interval="d", config=None):
    secid, normalized = eastmoney_secid(symbol)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": eastmoney_klt(interval),
        "fqt": "1",
        "beg": parse_date(start).replace("-", "") or "19900101",
        "end": parse_date(end).replace("-", "") or "20500101",
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TradeFramework/1.0"})
    with open_request(request, timeout=30, config=config) as response:
        payload = json.loads(response.read().decode("utf-8"))
    klines = ((payload.get("data") or {}).get("klines") or [])
    rows = []
    for line in klines:
        fields = line.split(",")
        if len(fields) < 6:
            continue
        rows.append({
            "date": fields[0],
            "open": float(fields[1]),
            "close": float(fields[2]),
            "high": float(fields[3]),
            "low": float(fields[4]),
            "volume": float(fields[5]),
        })
    if not rows:
        raise ValueError(f"No Eastmoney rows returned for {normalized}.")
    return {
        "source": "eastmoney",
        "sourceUrl": url,
        "symbol": normalized,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


def tencent_symbol(symbol):
    symbol = str(symbol or "").strip().lower()
    if not symbol:
        raise ValueError("symbol is required.")
    if "." in symbol:
        code, market = symbol.split(".", 1)
        market = market.lower()
        if market in {"sh", "ss"}:
            return f"sh{code}", f"{code}.SH"
        if market in {"sz", "she"}:
            return f"sz{code}", f"{code}.SZ"
    code = "".join(ch for ch in symbol if ch.isdigit())
    if not code:
        raise ValueError("A-share symbol must be a stock code such as 600519.SH or 000001.SZ.")
    if code.startswith(("6", "9")):
        return f"sh{code}", f"{code}.SH"
    return f"sz{code}", f"{code}.SZ"


def tencent_interval(interval):
    return {
        "d": "day",
        "w": "week",
        "m": "month",
        "1d": "day",
        "1wk": "week",
        "1mo": "month",
    }.get(str(interval or "d"), "day")


def tencent_download(symbol, start="", end="", interval="d", config=None):
    market_symbol, normalized = tencent_symbol(symbol)
    period = tencent_interval(interval)
    start_date = parse_date(start) or "1990-01-01"
    end_date = parse_date(end) or datetime.now(timezone.utc).date().isoformat()
    params = f"{market_symbol},{period},{start_date},{end_date},1000,qfq"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urllib.parse.urlencode({"param": params})
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TradeFramework/1.0"})
    with open_request(request, timeout=30, config=config) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = ((payload.get("data") or {}).get(market_symbol) or {})
    rows_payload = data.get(f"qfq{period}") or data.get(period) or []
    rows = []
    for fields in rows_payload:
        if len(fields) < 6:
            continue
        rows.append({
            "date": fields[0],
            "open": float(fields[1]),
            "close": float(fields[2]),
            "high": float(fields[3]),
            "low": float(fields[4]),
            "volume": float(fields[5]),
        })
    if not rows:
        raise ValueError(f"No Tencent rows returned for {normalized}.")
    return {
        "source": "tencent",
        "sourceUrl": url,
        "symbol": normalized,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


def fred_download(symbol, start="", end="", interval="d", config=None):
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required.")
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode({"id": symbol})
    request = urllib.request.Request(url, headers={"User-Agent": "TradeFramework/1.0"})
    with open_request(request, timeout=30, config=config) as response:
        csv_text = response.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    start_date = parse_date(start)
    end_date = parse_date(end)
    rows = []
    for item in reader:
        date = item.get("observation_date") or item.get("DATE") or item.get("date")
        value = item.get(symbol) or item.get("value")
        if not date or value in {None, "", "."}:
            continue
        date = parse_date(date)
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        close = float(value)
        rows.append({"date": date, "open": close, "high": close, "low": close, "close": close, "volume": 0.0})
    if not rows:
        raise ValueError(f"No FRED rows returned for {symbol}.")
    return {
        "source": "fred",
        "sourceUrl": url,
        "symbol": symbol,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


def aggregate_rows(rows, period):
    groups = {}
    for row in rows:
        date = datetime.fromisoformat(row["date"]).date()
        key = f"{date.isocalendar().year}-W{date.isocalendar().week:02d}" if period == "week" else f"{date.year}-{date.month:02d}"
        groups.setdefault(key, []).append(row)
    result = []
    for group in groups.values():
        group.sort(key=lambda row: row["date"])
        result.append({
            "date": group[-1]["date"],
            "open": group[0]["open"],
            "high": max(row["high"] for row in group),
            "low": min(row["low"] for row in group),
            "close": group[-1]["close"],
            "volume": sum(row["volume"] for row in group),
        })
    result.sort(key=lambda row: row["date"])
    return result


def alpha_vantage_download(symbol, start="", end="", interval="d", api_key="demo", config=None):
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required.")
    api_key = str(api_key or "demo").strip()
    function = "TIME_SERIES_WEEKLY" if interval in {"w", "1wk"} else "TIME_SERIES_MONTHLY" if interval in {"m", "1mo"} else "TIME_SERIES_DAILY"
    params = {
        "function": function,
        "symbol": symbol,
        "apikey": api_key,
        "datatype": "csv",
    }
    if api_key.lower() != "demo":
        params["outputsize"] = "full"
    params = urllib.parse.urlencode(params)
    url = f"https://www.alphavantage.co/query?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "TradeFramework/1.0"})
    with open_request(request, timeout=30, config=config) as response:
        csv_text = response.read().decode("utf-8-sig")
    if csv_text.lstrip().startswith("{"):
        payload = json.loads(csv_text)
        message = payload.get("Information") or payload.get("Note") or payload.get("Error Message") or str(payload)
        raise ValueError(message)
    rows = parse_ohlcv_csv(csv_text)
    start_date = parse_date(start)
    end_date = parse_date(end)
    if start_date:
        rows = [row for row in rows if row["date"] >= start_date]
    if end_date:
        rows = [row for row in rows if row["date"] <= end_date]
    if not rows:
        raise ValueError(f"No Alpha Vantage rows returned for {symbol}. The demo key only supports limited symbols such as IBM.")
    return {
        "source": "alphavantage",
        "sourceUrl": url.replace(api_key, "***") if api_key else url,
        "symbol": symbol,
        "csvText": rows_to_csv(rows),
        "rows": rows,
    }


DATA_SOURCE_REGISTRY = {
    "robinhood": {
        "source": "robinhood",
        "name": "Robinhood US Intraday",
        "description": "No-key US stock/ETF OHLCV with 5-minute, 10-minute, hourly and daily bars. Intraday ranges are recent-market windows.",
        "requiresKey": False,
        "intervals": ["5m", "10m", "h", "d"],
        "downloader": robinhood_download,
    },
    "nasdaq": {
        "source": "nasdaq",
        "name": "Nasdaq US Stock History",
        "description": "No-key US stock daily OHLCV from Nasdaq public historical endpoint. Symbols use tickers like AAPL, MSFT, SPY.",
        "requiresKey": False,
        "intervals": ["d"],
        "downloader": nasdaq_download,
    },
    "binance": {
        "source": "binance",
        "name": "Binance Public Klines",
        "description": "No-key crypto OHLCV from Binance public market data. Symbols use pairs like BTCUSDT or ETHUSDT.",
        "requiresKey": False,
        "intervals": ["d", "w", "m"],
        "downloader": binance_download,
    },
    "tencent": {
        "source": "tencent",
        "name": "Tencent A-share Klines",
        "description": "No-key A-share OHLCV from Tencent public K-line endpoint. Symbols use codes like 600519.SH or 000001.SZ.",
        "requiresKey": False,
        "intervals": ["d", "w", "m"],
        "downloader": tencent_download,
    },
    "fred": {
        "source": "fred",
        "name": "FRED CSV",
        "description": "No-key macro time series from FRED CSV. Single-value series are stored as OHLC with identical open/high/low/close.",
        "requiresKey": False,
        "intervals": ["d"],
        "downloader": fred_download,
    },
    "alphavantage": {
        "source": "alphavantage",
        "name": "Alpha Vantage CSV",
        "description": "Free API-key historical OHLCV CSV. Demo key works for IBM; use a free key for other symbols.",
        "requiresKey": True,
        "intervals": ["d", "w", "m"],
        "downloader": alpha_vantage_download,
    },
    "yahoo": {
        "source": "yahoo",
        "name": "Yahoo Finance Chart",
        "description": "No-key Yahoo chart endpoint. In this environment it must run through a working proxy.",
        "requiresKey": False,
        "requiresProxy": True,
        "intervals": ["1m", "2m", "5m", "15m", "h", "d", "w", "m"],
        "downloader": yahoo_download,
    },
    "stooq": {
        "source": "stooq",
        "name": "Stooq EOD CSV",
        "description": "Historical daily CSV. Stooq may require an apikey depending on symbol and access path.",
        "requiresKey": True,
        "intervals": ["d", "w", "m"],
        "downloader": stooq_download,
    },
}


def source_definition_key(source, version):
    return f"{source}/{version}"


def default_source_definitions():
    return {
        source_definition_key(provider["source"], "builtin"): {
            "schemaVersion": 1,
            "kind": "DataSource",
            "source": provider["source"],
            "version": "builtin",
            "activationMode": "BuiltIn",
            "hotSwapMode": "Live",
            "name": provider["name"],
            "description": provider["description"],
            "requiresKey": bool(provider.get("requiresKey")),
            "requiresProxy": bool(provider.get("requiresProxy")),
            "intervals": list(provider.get("intervals") or []),
            "parameters": {
                "builtinDownloader": provider["source"],
            },
            "builtin": True,
            "enabled": True,
            "createdAt": "builtin",
        }
        for provider in DATA_SOURCE_REGISTRY.values()
    }


def load_source_definitions(config):
    definitions = default_source_definitions()
    path = Path(config["controlRoot"]) / RUNTIME_SOURCE_DEFINITIONS
    definitions.update(load_json_state(path, {}))
    return definitions


def load_source_activations(config):
    path = Path(config["controlRoot"]) / RUNTIME_SOURCE_ACTIVATIONS
    return load_json_state(path, {})


def save_source_definitions(config, definitions):
    builtins = set(default_source_definitions())
    custom = {key: value for key, value in definitions.items() if key not in builtins}
    save_json_state(Path(config["controlRoot"]) / RUNTIME_SOURCE_DEFINITIONS, custom)


def save_source_activations(config, activations):
    save_json_state(Path(config["controlRoot"]) / RUNTIME_SOURCE_ACTIVATIONS, activations)


def validate_source_definition(definition):
    if not isinstance(definition, dict):
        raise ValueError("source definition must be an object.")
    source = str(definition.get("source") or "").strip().lower()
    version = str(definition.get("version") or "").strip()
    if not source:
        raise ValueError("source is required.")
    if not version:
        raise ValueError("version is required.")
    if not definition.get("name"):
        raise ValueError("name is required.")
    if not definition.get("description"):
        raise ValueError("description is required.")
    activation_mode = definition.get("activationMode") or "BuiltIn"
    if activation_mode != "BuiltIn":
        raise ValueError(f"Unsupported data source activationMode '{activation_mode}'.")
    hot_swap_mode = definition.get("hotSwapMode") or "Live"
    if hot_swap_mode != "Live":
        raise ValueError(f"Unsupported data source hotSwapMode '{hot_swap_mode}'.")
    intervals = definition.get("intervals") or []
    if not isinstance(intervals, list) or not intervals or not all(isinstance(item, str) and item for item in intervals):
        raise ValueError("intervals must be a non-empty string array.")
    parameters = definition.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object.")
    downloader_id = str(parameters.get("builtinDownloader") or "").strip().lower()
    if downloader_id not in DATA_SOURCE_REGISTRY:
        supported = "', '".join(sorted(DATA_SOURCE_REGISTRY))
        raise ValueError(f"parameters.builtinDownloader must be one of '{supported}'.")


def active_source_definition(config, source):
    source = str(source or "").strip().lower()
    definitions = load_source_definitions(config)
    activations = load_source_activations(config)
    preferred_version = activations.get(source)
    if preferred_version:
        selected = definitions.get(source_definition_key(source, preferred_version))
        if selected and selected.get("enabled", True):
            return selected
    builtin = definitions.get(source_definition_key(source, "builtin"))
    if builtin and builtin.get("enabled", True):
        return builtin
    candidates = [
        definition
        for definition in definitions.values()
        if definition.get("source") == source and definition.get("enabled", True)
    ]
    candidates.sort(key=lambda item: (str(item.get("createdAt") or ""), str(item.get("version") or "")))
    return candidates[-1] if candidates else None


def list_sources(config):
    definitions = load_source_definitions(config)
    sources = []
    seen = set()
    for definition in definitions.values():
        source = definition.get("source")
        if source in seen:
            continue
        active = active_source_definition(config, source)
        if not active:
            continue
        seen.add(source)
        sources.append({
            "source": active["source"],
            "version": active["version"],
            "name": active["name"],
            "description": active["description"],
            "requiresKey": bool(active.get("requiresKey")),
            "requiresProxy": bool(active.get("requiresProxy")),
            "intervals": list(active.get("intervals") or []),
            "activationMode": active.get("activationMode", "BuiltIn"),
            "hotSwapMode": active.get("hotSwapMode", "Live"),
            "builtin": bool(active.get("builtin")),
        })
    sources.sort(key=lambda item: item["source"])
    sources.append(
        {
            "source": "upload",
            "version": "builtin",
            "name": "CSV Upload",
            "description": "Upload Date/Open/High/Low/Close/Volume CSV into the local SQLite store.",
            "requiresKey": False,
            "intervals": ["custom"],
            "activationMode": "BuiltIn",
            "hotSwapMode": "Live",
            "builtin": True,
        }
    )
    return sources


def save_source_definition(config, request):
    definition = {
        "schemaVersion": 1,
        "kind": "DataSource",
        "source": str(request.get("source") or "").strip().lower(),
        "version": str(request.get("version") or "").strip(),
        "activationMode": request.get("activationMode") or "BuiltIn",
        "hotSwapMode": request.get("hotSwapMode") or "Live",
        "name": request.get("name") or "",
        "description": request.get("description") or "",
        "requiresKey": bool(request.get("requiresKey")),
        "requiresProxy": bool(request.get("requiresProxy")),
        "intervals": list(request.get("intervals") or []),
        "parameters": request.get("parameters") or {},
        "builtin": False,
        "enabled": request.get("enabled", True) is not False,
        "createdAt": utc_now(),
    }
    validate_source_definition(definition)
    definitions = load_source_definitions(config)
    key = source_definition_key(definition["source"], definition["version"])
    if key in definitions:
        raise ValueError(f"Data source definition already exists: {key}")
    definitions[key] = definition
    save_source_definitions(config, definitions)
    if request.get("activate", True):
        activations = load_source_activations(config)
        activations[definition["source"]] = definition["version"]
        save_source_activations(config, activations)
    return {
        "accepted": True,
        "definitionKey": key,
        "definition": definition,
    }


def activate_source_definition(config, source, version):
    definition = load_source_definitions(config).get(source_definition_key(source, version))
    if not definition:
        raise ValueError(f"Unknown data source definition: {source}/{version}")
    if not definition.get("enabled", True):
        raise ValueError(f"Data source definition is disabled: {source}/{version}")
    activations = load_source_activations(config)
    activations[source] = version
    save_source_activations(config, activations)
    return {
        "accepted": True,
        "source": source,
        "version": version,
        "definition": definition,
    }


def download_source(source, symbol, start="", end="", interval="d", api_key="", config=None):
    source_id = str(source or "alphavantage").strip().lower()
    definition = active_source_definition(config, source_id) if config else default_source_definitions().get(source_definition_key(source_id, "builtin"))
    if not definition:
        supported = "', '".join(sorted(set(item["source"] for item in default_source_definitions().values())))
        raise ValueError(f"Supported data sources are '{supported}'.")
    downloader_id = str((definition.get("parameters") or {}).get("builtinDownloader") or source_id).strip().lower()
    provider = DATA_SOURCE_REGISTRY.get(downloader_id)
    if not provider:
        raise ValueError(f"Unsupported builtin downloader: {downloader_id}")
    if downloader_id == "alphavantage":
        return provider["downloader"](symbol, start, end, interval, api_key or "demo", config=config)
    if downloader_id == "stooq":
        return provider["downloader"](symbol, start, end, interval, api_key or "", config=config)
    return provider["downloader"](symbol, start, end, interval, config=config)


def parse_ohlcv_csv(csv_text):
    if not csv_text or not csv_text.strip():
        return []
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    if not reader.fieldnames:
        return []
    aliases = {name.lower().strip(): name for name in reader.fieldnames}

    def field(row, *names, default=""):
        for name in names:
            original = aliases.get(name)
            if original is not None:
                return row.get(original, default)
        return default

    rows = []
    for row in reader:
        raw_date = field(row, "date", "datetime", "time", "timestamp")
        date = str(raw_date or "").strip()
        if "T" not in date and " " not in date:
            date = parse_date(date)
        if not date:
            continue
        try:
            item = {
                "date": date,
                "open": float(field(row, "open")),
                "high": float(field(row, "high")),
                "low": float(field(row, "low")),
                "close": float(field(row, "close", "adj close", "adj_close")),
                "volume": float(field(row, "volume", default="0") or 0),
            }
        except ValueError:
            continue
        rows.append(item)
    rows.sort(key=lambda item: item["date"])
    return rows


def rows_to_csv(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date", "open", "high", "low", "close", "volume"])
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def save_dataset(config, *, dataset_id, name, symbol, source, interval, rows, metadata=None, csv_text=None):
    if not rows:
        raise ValueError("dataset requires at least one OHLCV row.")
    dataset_id = safe_id(dataset_id)
    path = dataset_root(config) / dataset_id / "bars.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_text = csv_text or rows_to_csv(rows)
    path.write_text(csv_text, encoding="utf-8")
    created_at = utc_now()
    metadata = metadata or {}
    with connect(config) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO datasets
            (dataset_id, name, symbol, source, interval, start_date, end_date, row_count, created_at, metadata_json, csv_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                name or dataset_id,
                symbol,
                source,
                interval,
                rows[0]["date"],
                rows[-1]["date"],
                len(rows),
                created_at,
                json.dumps(metadata, sort_keys=True),
                str(path),
            ),
        )
        conn.execute("DELETE FROM bars WHERE dataset_id = ?", (dataset_id,))
        conn.executemany(
            """
            INSERT INTO bars (dataset_id, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (dataset_id, row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"])
                for row in rows
            ],
        )
        conn.commit()
    return get_dataset(config, dataset_id)


def search_datasets(config, query=""):
    query = str(query or "").lower()
    local = list_datasets(config)
    if query:
        local = [
            item for item in local
            if query in item["datasetId"].lower() or query in item["symbol"].lower() or query in item["name"].lower()
        ]
    candidates = []
    if query:
        symbol = query.upper()
        if symbol.isalpha() or symbol in {"SPY", "QQQ", "DIA", "IWM"}:
            candidates.append({
                "source": "nasdaq",
                "symbol": symbol,
                "name": f"Download {symbol} from Nasdaq",
                "downloadable": True,
            })
        if symbol.endswith("USDT") or symbol in {"BTC", "ETH", "SOL", "BNB"}:
            candidates.append({
                "source": "binance",
                "symbol": symbol if symbol.endswith("USDT") else f"{symbol}USDT",
                "name": f"Download {symbol if symbol.endswith('USDT') else f'{symbol}USDT'} from Binance",
                "downloadable": True,
            })
        if symbol.isdigit() or symbol.endswith((".SH", ".SZ")):
            candidates.append({
                "source": "tencent",
                "symbol": symbol,
                "name": f"Download {symbol} from Tencent",
                "downloadable": True,
            })
        candidates.append({
            "source": "fred",
            "symbol": symbol,
            "name": f"Download {symbol} from FRED",
            "downloadable": True,
        })
    return {"local": local, "candidates": candidates}


def count_datasets(config):
    with connect(config) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM datasets").fetchone()
    return int(row["count"])


def list_datasets(config, limit=None):
    sql = "SELECT * FROM datasets ORDER BY created_at DESC"
    params = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    with connect(config) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dataset_row(row) for row in rows]


def get_dataset(config, dataset_id):
    with connect(config) as conn:
        row = conn.execute("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
    if not row:
        raise ValueError(f"Unknown dataset: {dataset_id}")
    return dataset_row(row)


def dataset_row(row):
    return {
        "datasetId": row["dataset_id"],
        "name": row["name"],
        "symbol": row["symbol"],
        "source": row["source"],
        "interval": row["interval"],
        "startDate": row["start_date"],
        "endDate": row["end_date"],
        "rowCount": row["row_count"],
        "createdAt": row["created_at"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "csvPath": row["csv_path"],
    }


def get_bars(config, dataset_id, limit=1000):
    with connect(config) as conn:
        rows = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM bars WHERE dataset_id = ? ORDER BY ts DESC LIMIT ?",
            (dataset_id, int(limit)),
        ).fetchall()
    bars = [
        {
            "date": row["ts"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for row in rows
    ]
    bars.reverse()
    return bars


def moving_average(values, period):
    result = []
    total = 0.0
    window = []
    for value in values:
        window.append(value)
        total += value
        if len(window) > period:
            total -= window.pop(0)
        result.append(total / period if len(window) == period else None)
    return result


def result_path_key(value):
    return safe_id(value).replace("-", "_")


def pipeline_wire_value(bar, wire):
    wire = str(wire or "")
    values = {
        "null": 0.0,
        "price": bar["close"],
        "price.close": bar["close"],
        "close": bar["close"],
        "price.open": bar["open"],
        "open": bar["open"],
        "price.high": bar["high"],
        "high": bar["high"],
        "price.low": bar["low"],
        "low": bar["low"],
        "price.volume": bar["volume"],
        "volume": bar["volume"],
    }
    return values.get(wire)


def number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def push_window(state, name, value, period):
    window = state.setdefault(name, [])
    window.append(value)
    while len(window) > period:
        window.pop(0)
    return window


def eval_builtin_factor(module_id, config, inputs, state, bar):
    module_id = str(module_id or "")
    config = config or {}

    if module_id == "graph-output":
        return {"output": inputs.get("value")}

    if module_id == "price-source":
        price_field = str(config.get("priceField") or "close")
        price = bar.get(price_field, bar["close"])
        return {
            "price": price,
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
        }

    def period(name="period", default=20):
        return max(1, int(config.get(name) or default))

    if module_id == "sma-indicator":
        value = number(inputs.get("value"))
        if value is None:
            return {"sma": None}
        window = push_window(state, "values", value, period())
        return {"sma": sum(window) / len(window) if len(window) == period() else None}

    if module_id == "ema-indicator":
        value = number(inputs.get("value"))
        if value is None:
            return {"ema": None}
        alpha = 2.0 / (period() + 1.0)
        state["ema"] = value if state.get("ema") is None else value * alpha + state["ema"] * (1.0 - alpha)
        return {"ema": state["ema"]}

    if module_id == "wma-indicator":
        value = number(inputs.get("value"))
        if value is None:
            return {"wma": None}
        p = period()
        window = push_window(state, "values", value, p)
        if len(window) < p:
            return {"wma": None}
        divisor = sum(range(1, len(window) + 1))
        return {"wma": sum(item * (index + 1) for index, item in enumerate(window)) / divisor}

    if module_id == "vwma-indicator":
        price = number(inputs.get("price"))
        volume = number(inputs.get("volume"))
        if price is None or volume is None:
            return {"vwma": None}
        p = period()
        pv = push_window(state, "priceVolume", price * volume, p)
        vv = push_window(state, "volume", volume, p)
        return {"vwma": sum(pv) / sum(vv) if len(vv) == p and sum(vv) else None}

    if module_id == "rsi-indicator":
        price = number(inputs.get("price"))
        if price is None:
            return {"rsi": None}
        p = period(default=14)
        previous = state.get("previous")
        state["previous"] = price
        if previous is None:
            return {"rsi": None}
        delta = price - previous
        gains = push_window(state, "gains", max(delta, 0.0), p)
        losses = push_window(state, "losses", max(-delta, 0.0), p)
        if len(gains) < p:
            return {"rsi": None}
        avg_gain = sum(gains) / p
        avg_loss = sum(losses) / p
        if avg_loss == 0:
            return {"rsi": 100.0}
        rs = avg_gain / avg_loss
        return {"rsi": 100.0 - 100.0 / (1.0 + rs)}

    if module_id == "roc-indicator":
        price = number(inputs.get("price"))
        if price is None:
            return {"roc": None}
        p = period(default=12)
        values = state.setdefault("values", [])
        values.append(price)
        if len(values) <= p:
            return {"roc": None}
        previous = values.pop(0)
        return {"roc": price / previous - 1.0 if previous else None}

    if module_id == "macd-indicator":
        price = number(inputs.get("price"))
        if price is None:
            return {"macd": None, "signal": None, "histogram": None}

        def ema(name, value, p):
            alpha = 2.0 / (p + 1.0)
            state[name] = value if state.get(name) is None else value * alpha + state[name] * (1.0 - alpha)
            return state[name]

        fast = ema("fast", price, period("fastPeriod", 12))
        slow = ema("slow", price, period("slowPeriod", 26))
        macd = fast - slow
        signal = ema("signal", macd, period("signalPeriod", 9))
        return {"macd": macd, "signal": signal, "histogram": macd - signal}

    if module_id == "bollinger-bands-indicator":
        price = number(inputs.get("price"))
        if price is None:
            return {"middle": None, "upper": None, "lower": None, "bandwidth": None, "percentB": None}
        p = period()
        k = float(config.get("k") or 2)
        window = push_window(state, "values", price, p)
        if len(window) < p:
            return {"middle": None, "upper": None, "lower": None, "bandwidth": None, "percentB": None}
        middle = sum(window) / p
        deviation = math.sqrt(sum((item - middle) ** 2 for item in window) / p)
        upper = middle + k * deviation
        lower = middle - k * deviation
        return {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "bandwidth": (upper - lower) / middle if middle else None,
            "percentB": (price - lower) / (upper - lower) if upper != lower else None,
        }

    if module_id == "atr-indicator":
        high = number(inputs.get("high"))
        low = number(inputs.get("low"))
        close = number(inputs.get("close"))
        if high is None or low is None or close is None:
            return {"atr": None}
        previous = state.get("previousClose")
        true_range = high - low if previous is None else max(high - low, abs(high - previous), abs(low - previous))
        state["previousClose"] = close
        p = period(default=14)
        window = push_window(state, "ranges", true_range, p)
        return {"atr": sum(window) / p if len(window) == p else None}

    if module_id == "stochastic-indicator":
        high = number(inputs.get("high"))
        low = number(inputs.get("low"))
        close = number(inputs.get("close"))
        if high is None or low is None or close is None:
            return {"k": None, "d": None}
        p = period(default=14)
        d_period = max(1, int(config.get("dPeriod") or 3))
        highs = push_window(state, "highs", high, p)
        lows = push_window(state, "lows", low, p)
        if len(highs) < p:
            return {"k": None, "d": None}
        highest = max(highs)
        lowest = min(lows)
        k_value = 100.0 * (close - lowest) / (highest - lowest) if highest != lowest else 0.0
        k_values = push_window(state, "kValues", k_value, d_period)
        return {"k": k_value, "d": sum(k_values) / d_period if len(k_values) == d_period else None}

    if module_id == "obv-indicator":
        close = number(inputs.get("close"))
        volume = number(inputs.get("volume"))
        if close is None or volume is None:
            return {"obv": None}
        previous = state.get("previousClose")
        state["previousClose"] = close
        if previous is not None:
            if close > previous:
                state["obv"] = state.get("obv", 0.0) + volume
            elif close < previous:
                state["obv"] = state.get("obv", 0.0) - volume
        else:
            state.setdefault("obv", 0.0)
        return {"obv": state["obv"]}

    if module_id == "cross-over-gate":
        fast = number(inputs.get("fast"))
        slow = number(inputs.get("slow"))
        if fast is None or slow is None:
            return {"direction": None}
        current = 1 if fast > slow else -1 if fast < slow else 0
        previous = int(state.get("previous") or 0)
        direction = "rise" if current > 0 and previous <= 0 else "fall" if current < 0 and previous >= 0 else "flat"
        if current:
            state["previous"] = current
        return {"direction": direction}

    return {}


def pipeline_data_key_outputs(config, lane_id, bars):
    try:
        import strategy_submit_api as control

        attachment = control.load_lane_attachment(config, lane_id) or {}
        alpha_graph = attachment.get("alphaGraph") or {}
        node_ids = alpha_graph.get("nodes") or []
        if not node_ids:
            return {}, {}, {}
        definitions = control.load_module_definitions(config)
        instances = control.attachment_instances(config, attachment)
    except Exception:
        return {}, {}, {}

    derived = {}
    data_keys = {}
    states = {}
    output_ports = {}
    for node_id in node_ids:
        instance = instances.get(node_id)
        if not instance:
            continue
        try:
            definition = control.get_definition(definitions, instance["kind"], instance["moduleId"], instance["version"])
            ports = control.normalize_ports(definition.get("ports") or {})
        except Exception:
            continue
        output_ports[node_id] = ports["outputs"]
        if instance.get("moduleId") == "graph-output":
            data_key = str((instance.get("config") or {}).get("dataKey") or "").strip() or f"{instance['instanceId']}.data"
            path_key = result_path_key(data_key)
            derived[path_key] = []
            data_keys[data_key] = {
                "label": data_key,
                "type": "series.number",
                "path": f"derived.{path_key}",
                "defaultDrawCallback": "series.line",
                "paneRole": "line",
                "encoding": {"time": "date", "value": "value"},
                "style": {"lineWidth": 2},
                "module": {
                    "source": "pipeline-output",
                    "instanceId": instance["instanceId"],
                    "kind": instance["kind"],
                    "moduleId": instance["moduleId"],
                    "version": instance["version"],
                    "config": instance.get("config") or {},
                    "output": "dataKey",
                },
            }
            continue
        for port_name, wire_id in (instance.get("outputs") or {}).items():
            path_key = result_path_key(wire_id)
            derived[path_key] = []
            port_type = ports["outputs"].get(port_name, {}).get("type", "series.number")
            data_keys[wire_id] = {
                "label": wire_id,
                "type": port_type,
                "path": f"derived.{path_key}",
                "defaultDrawCallback": "series.line",
                "paneRole": "line",
                "encoding": {"time": "date", "value": "value"},
                "style": {"lineWidth": 2},
                "module": {
                    "source": "pipeline",
                    "instanceId": instance["instanceId"],
                    "kind": instance["kind"],
                    "moduleId": instance["moduleId"],
                    "version": instance["version"],
                    "config": instance.get("config") or {},
                    "output": port_name,
                },
            }

    for bar in bars:
        current = {
            "price": bar["close"],
            "price.close": bar["close"],
            "close": bar["close"],
            "price.open": bar["open"],
            "open": bar["open"],
            "price.high": bar["high"],
            "high": bar["high"],
            "price.low": bar["low"],
            "low": bar["low"],
            "price.volume": bar["volume"],
            "volume": bar["volume"],
        }
        for node_id in node_ids:
            instance = instances.get(node_id)
            if not instance:
                continue
            inputs = {
                port_name: current.get(wire_id, pipeline_wire_value(bar, wire_id))
                for port_name, wire_id in (instance.get("inputs") or {}).items()
            }
            if instance.get("moduleId") == "graph-output":
                data_key = str((instance.get("config") or {}).get("dataKey") or "").strip() or f"{instance['instanceId']}.data"
                value = inputs.get("value")
                current[data_key] = value
                derived[result_path_key(data_key)].append({"date": bar["date"], "value": value})
                continue
            state = states.setdefault(node_id, {})
            outputs = eval_builtin_factor(instance.get("moduleId"), instance.get("config") or {}, inputs, state, bar)
            for port_name, wire_id in (instance.get("outputs") or {}).items():
                value = outputs.get(port_name)
                current[wire_id] = value
                derived[result_path_key(wire_id)].append({"date": bar["date"], "value": value})

    return derived, data_keys, {
        "laneId": lane_id,
        "nodes": node_ids,
    }


def run_backtest(config, request):
    dataset_id = request.get("datasetId")
    if not dataset_id:
        raise ValueError("datasetId is required.")
    bars = get_bars(config, dataset_id, int(request.get("limit") or 1000000))
    if len(bars) < 2:
        raise ValueError("backtest requires at least two bars.")
    lane_id = request.get("laneId") or "main"
    name = request.get("name") or f"{lane_id}-{dataset_id}-benchmark"
    runner = request.get("runner") or "local-ohlcv-benchmark"
    closes = [bar["close"] for bar in bars]
    fast_period = int(request.get("fastPeriod") or 20)
    slow_period = int(request.get("slowPeriod") or 50)
    fast_average = moving_average(closes, fast_period)
    slow_average = moving_average(closes, slow_period)
    cash = 100000.0
    shares = 0.0
    equity = []
    positions = []
    trades = []
    signals = []
    last_cross = 0
    for index, bar in enumerate(bars):
        fast = fast_average[index]
        slow = slow_average[index]
        cross = 0
        if fast is not None and slow is not None:
            cross = 1 if fast > slow else -1 if fast < slow else 0
        if cross > 0 and last_cross <= 0 and shares == 0:
            shares = cash / bar["close"]
            cash = 0.0
            signals.append({"date": bar["date"], "type": "entry", "price": bar["close"], "reason": "fast_ma_cross_up"})
            trades.append({"date": bar["date"], "side": "buy", "price": bar["close"], "quantity": shares})
        elif cross < 0 and last_cross >= 0 and shares > 0:
            cash = shares * bar["close"]
            signals.append({"date": bar["date"], "type": "exit", "price": bar["close"], "reason": "fast_ma_cross_down"})
            trades.append({"date": bar["date"], "side": "sell", "price": bar["close"], "quantity": shares})
            shares = 0.0
        if cross:
            last_cross = cross
        value = cash + shares * bar["close"]
        equity.append({"date": bar["date"], "value": value})
        positions.append({"date": bar["date"], "quantity": shares, "value": shares * bar["close"]})

    start_value = equity[0]["value"]
    end_value = equity[-1]["value"]
    returns = [(equity[i]["value"] / equity[i - 1]["value"] - 1.0) for i in range(1, len(equity)) if equity[i - 1]["value"]]
    total_return = end_value / start_value - 1.0 if start_value else 0.0
    max_drawdown = compute_max_drawdown([item["value"] for item in equity])
    volatility = compute_volatility(returns)
    metrics = {
        "startValue": start_value,
        "endValue": end_value,
        "totalReturn": total_return,
        "maxDrawdown": max_drawdown,
        "volatilityDaily": volatility,
        "tradeCount": len(trades),
        "barCount": len(bars),
    }
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "bars": bars,
        "signals": signals,
        "trades": trades,
        "positions": positions,
        "equity": equity,
        "metrics": metrics,
    }
    result["dataKeys"] = benchmark_result_data_keys()
    derived, pipeline_data_keys, pipeline_meta = pipeline_data_key_outputs(config, lane_id, bars)
    if derived:
        result["derived"] = derived
        result["dataKeys"].update(pipeline_data_keys)
        result["pipelineOutputs"] = pipeline_meta
    visualization = default_visualization_spec(dataset_id, result)
    backtest_id = safe_id(request.get("backtestId") or f"{name}-{utc_now()}")
    now = utc_now()
    path = result_root(config) / backtest_id / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    with connect(config) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO backtests
            (backtest_id, lane_id, dataset_id, name, status, runner, created_at, completed_at, request_json, metrics_json, result_json, visualization_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                backtest_id,
                lane_id,
                dataset_id,
                name,
                "completed",
                runner,
                now,
                now,
                json.dumps(request, sort_keys=True),
                json.dumps(metrics, sort_keys=True),
                json.dumps(result, sort_keys=True),
                json.dumps(visualization, sort_keys=True),
            ),
        )
        conn.commit()
    return get_backtest(config, backtest_id)


def compute_max_drawdown(values):
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_dd = min(max_dd, value / peak - 1.0)
    return max_dd


def compute_volatility(returns):
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    return math.sqrt(variance)


def benchmark_result_data_keys():
    marker_style = {
        "entry": {"shape": "arrowUp", "position": "belowBar", "color": "#089981"},
        "buy": {"shape": "arrowUp", "position": "belowBar", "color": "#089981"},
        "exit": {"shape": "arrowDown", "position": "aboveBar", "color": "#f23645"},
        "sell": {"shape": "arrowDown", "position": "aboveBar", "color": "#f23645"},
        "default": {"shape": "circle", "position": "aboveBar", "color": "#475569"},
    }
    return {
        "price": {
            "label": "Price",
            "type": "ohlcv",
            "path": "bars",
            "defaultDrawCallback": "ohlc.candles",
            "paneRole": "ohlcv",
            "encoding": {"time": "date", "open": "open", "high": "high", "low": "low", "close": "close"},
            "style": {"upColor": "#089981", "downColor": "#f23645"},
            "required": True,
        },
        "signals": {
            "label": "Signals",
            "type": "event.marker",
            "path": "signals",
            "defaultDrawCallback": "overlay.markers",
            "defaultTarget": "price",
            "paneRole": "ohlcv",
            "encoding": {"time": "date", "price": "price", "side": "type", "text": "reason", "shape": "shape"},
            "style": marker_style,
        },
        "trades": {
            "label": "Trades",
            "type": "event.marker",
            "path": "trades",
            "defaultDrawCallback": "overlay.markers",
            "defaultTarget": "price",
            "paneRole": "ohlcv",
            "encoding": {"time": "date", "price": "price", "side": "side", "text": "side", "shape": "shape"},
            "style": marker_style,
        },
        "volume": {
            "label": "Volume",
            "type": "series.number",
            "path": "bars",
            "defaultDrawCallback": "series.histogram",
            "paneRole": "line",
            "encoding": {"time": "date", "value": "volume"},
            "style": {"color": "#64748b"},
        },
        "equity": {
            "label": "Equity",
            "type": "series.number",
            "path": "equity",
            "defaultDrawCallback": "series.line",
            "paneRole": "line",
            "encoding": {"time": "date", "value": "value"},
            "style": {"color": "#0f766e", "lineWidth": 3},
        },
        "position.value": {
            "label": "Position Value",
            "type": "series.number",
            "path": "positions",
            "defaultDrawCallback": "series.line",
            "paneRole": "line",
            "encoding": {"time": "date", "value": "value"},
            "style": {"color": "#7c3aed", "lineWidth": 3},
        },
        "position.quantity": {
            "label": "Position Quantity",
            "type": "series.number",
            "path": "positions",
            "defaultDrawCallback": "series.line",
            "paneRole": "line",
            "encoding": {"time": "date", "value": "quantity"},
            "style": {"color": "#9a5b00", "lineWidth": 3},
        },
    }


def default_visualization_spec(dataset_id, result):
    return {
        "schemaVersion": 2,
        "datasetId": dataset_id,
        "dataKeys": result.get("dataKeys") or {},
        "panes": [],
    }


def count_backtests(config):
    with connect(config) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM backtests").fetchone()
    return int(row["count"])


def list_backtests(config, limit=None):
    sql = "SELECT * FROM backtests ORDER BY created_at DESC"
    params = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    with connect(config) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [backtest_row(row, include_result=False, include_visualization=False) for row in rows]


def get_backtest(config, backtest_id):
    with connect(config) as conn:
        row = conn.execute("SELECT * FROM backtests WHERE backtest_id = ?", (backtest_id,)).fetchone()
    if not row:
        raise ValueError(f"Unknown backtest: {backtest_id}")
    return backtest_row(row, include_result=True, include_visualization=True)


def get_backtest_meta(config, backtest_id):
    with connect(config) as conn:
        row = conn.execute("SELECT * FROM backtests WHERE backtest_id = ?", (backtest_id,)).fetchone()
    if not row:
        raise ValueError(f"Unknown backtest: {backtest_id}")
    return backtest_row(row, include_result=False, include_visualization=True, include_data_keys=True)


def get_backtest_result_slice(config, backtest_id, paths, temporary_modules=None):
    with connect(config) as conn:
        row = conn.execute("SELECT result_json FROM backtests WHERE backtest_id = ?", (backtest_id,)).fetchone()
    if not row:
        raise ValueError(f"Unknown backtest: {backtest_id}")
    result = json.loads(row["result_json"] or "{}")
    return slice_result_payload(result, paths or [], temporary_modules or [])


def backtest_row(row, include_result, include_visualization=False, include_data_keys=False):
    item = {
        "backtestId": row["backtest_id"],
        "laneId": row["lane_id"],
        "datasetId": row["dataset_id"],
        "name": row["name"],
        "status": row["status"],
        "runner": row["runner"],
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"],
        "request": json.loads(row["request_json"] or "{}"),
        "metrics": json.loads(row["metrics_json"] or "{}"),
    }
    if include_visualization:
        item["visualization"] = json.loads(row["visualization_json"] or "{}")
    if include_result:
        result = json.loads(row["result_json"] or "{}")
        item["result"] = result
        if include_data_keys:
            item["dataKeys"] = result.get("dataKeys") or {}
    elif include_data_keys:
        result = json.loads(row["result_json"] or "{}")
        item["dataKeys"] = result.get("dataKeys") or {}
    return item


def assign_nested_path(target, path, value):
    if isinstance(target, dict) and path in target:
        target[path] = value
        return
    parts = [part for part in str(path or "").split(".") if part]
    if not parts:
        return
    node = target
    for part in parts[:-1]:
        current = node.get(part)
        if current is None:
            node = node.setdefault(part, {})
            continue
        if not isinstance(current, dict):
            target[str(path)] = value
            return
        node = current
    node[parts[-1]] = value


def slice_result_payload(result, paths, temporary_modules=None):
    payload = {
        "dataKeys": result.get("dataKeys") or {},
    }
    for path in paths:
        if not path:
            continue
        value = resolve_path(result, path)
        if value is None:
            continue
        assign_nested_path(payload, path, value)
    for data_key, rows in compute_temporary_result_series(result, temporary_modules or []).items():
        assign_nested_path(payload, data_key, rows)
    return payload


def resolve_path(root, path):
    if isinstance(root, dict) and path in root:
        return root[path]
    node = root
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def series_row_map(result, declaration):
    path = (declaration.get("source") or {}).get("path") or declaration.get("path")
    rows = resolve_path(result, path)
    if not isinstance(rows, list):
        return {}
    encoding = declaration.get("encoding") or {}
    time_field = encoding.get("time", "date")
    return {
        row.get(time_field): row
        for row in rows
        if isinstance(row, dict) and row.get(time_field) is not None
    }


def data_key_scalar(row, declaration, port_name):
    if not isinstance(row, dict):
        return None
    data_type = str(declaration.get("type") or "")
    encoding = declaration.get("encoding") or {}
    if data_type == "ohlcv":
        field = {
            "open": encoding.get("open", "open"),
            "high": encoding.get("high", "high"),
            "low": encoding.get("low", "low"),
            "close": encoding.get("close", "close"),
            "volume": "volume",
        }.get(port_name, encoding.get("close", "close"))
        return row.get(field)
    field = encoding.get("value", "value")
    return row.get(field)


def compute_temporary_result_series(result, temporary_modules):
    if not temporary_modules:
        return {}
    declarations = dict(result.get("dataKeys") or {})
    bars = result.get("bars") or []
    timeline = [bar.get("date") for bar in bars if isinstance(bar, dict) and bar.get("date")]
    if not timeline:
        return {}
    row_maps = {
        key: series_row_map(result, declaration)
        for key, declaration in declarations.items()
    }
    output_series = {}
    output_maps = {}
    states = {}
    for module in temporary_modules:
        module_id = module.get("moduleId") or ""
        instance_id = module.get("instanceId") or module_id
        if not module.get("outputs"):
            continue
        module_rows = {data_key: [] for data_key in module["outputs"].values() if data_key}
        module_maps = {data_key: {} for data_key in module["outputs"].values() if data_key}
        state = states.setdefault(instance_id, {})
        for timestamp in timeline:
            current_values = {}
            for port_name, data_key in (module.get("inputs") or {}).items():
                if data_key in output_maps:
                    current_values[port_name] = output_maps[data_key].get(timestamp)
                    continue
                declaration = declarations.get(data_key)
                if not declaration:
                    current_values[port_name] = None
                    continue
                row = row_maps.get(data_key, {}).get(timestamp)
                current_values[port_name] = data_key_scalar(row, declaration, port_name)
            outputs = eval_builtin_factor(module_id, module.get("config") or {}, current_values, state, {"date": timestamp})
            if not outputs:
                raise ValueError(f"Temporary module execution is unsupported for '{module_id}'.")
            for port_name, data_key in (module.get("outputs") or {}).items():
                if not data_key:
                    continue
                value = outputs.get(port_name)
                module_rows[data_key].append({"date": timestamp, "value": value})
                module_maps[data_key][timestamp] = value
        for data_key, rows in module_rows.items():
            output_series[data_key] = rows
            output_maps[data_key] = module_maps[data_key]
            declarations[data_key] = {
                "label": data_key,
                "type": "series.number",
                "path": data_key,
                "encoding": {"time": "date", "value": "value"},
            }
    return output_series


def save_visualization(config, request):
    backtest_id = request.get("backtestId")
    spec = request.get("spec")
    if not backtest_id or not isinstance(spec, dict):
        raise ValueError("backtestId and spec object are required.")
    get_backtest(config, backtest_id)
    visualization_id = safe_id(request.get("visualizationId") or f"{backtest_id}-{request.get('name') or 'custom'}-{utc_now()}")
    with connect(config) as conn:
        conn.execute(
            "UPDATE backtests SET visualization_json = ? WHERE backtest_id = ?",
            (json.dumps(spec, sort_keys=True), backtest_id),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO visualizations (visualization_id, backtest_id, name, created_at, spec_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (visualization_id, backtest_id, request.get("name") or visualization_id, utc_now(), json.dumps(spec, sort_keys=True)),
        )
        conn.commit()
    return {"accepted": True, "visualization": get_visualization(config, visualization_id)}


def get_visualization(config, visualization_id):
    with connect(config) as conn:
        row = conn.execute("SELECT * FROM visualizations WHERE visualization_id = ?", (visualization_id,)).fetchone()
    if not row:
        raise ValueError(f"Unknown visualization: {visualization_id}")
    return {
        "visualizationId": row["visualization_id"],
        "backtestId": row["backtest_id"],
        "name": row["name"],
        "createdAt": row["created_at"],
        "spec": json.loads(row["spec_json"] or "{}"),
    }


def list_visualizations(config, backtest_id=""):
    sql = "SELECT * FROM visualizations"
    params = ()
    if backtest_id:
        sql += " WHERE backtest_id = ?"
        params = (backtest_id,)
    sql += " ORDER BY created_at DESC"
    with connect(config) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "visualizationId": row["visualization_id"],
            "backtestId": row["backtest_id"],
            "name": row["name"],
            "createdAt": row["created_at"],
            "spec": json.loads(row["spec_json"] or "{}"),
        }
        for row in rows
    ]
