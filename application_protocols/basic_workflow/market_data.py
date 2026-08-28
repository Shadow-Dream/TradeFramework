"""Online market snapshot providers for the Basic Workflow application.

Providers return application records only.  Dataset publication and Backtest
execution remain separate, ordinary Trade Engine operations.
"""

from __future__ import annotations

import csv
import io
import json
import hashlib
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from engine.core import clock as engine_clock


DEFAULT_PROVIDER_ID = "nasdaq-us-snapshot"
NASDAQ_LISTED_URL = (
    "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
)
NASDAQ_OTHER_LISTED_URL = (
    "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
)
NASDAQ_HISTORICAL_URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"

_MAX_CATALOG_BYTES = 16 * 1024 * 1024
_MAX_BARS_BYTES = 32 * 1024 * 1024
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]*$")
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")

EODHD_DEMO_PROVIDER_ID = "eodhd-demo-us"
EODHD_DEMO_URL = "https://eodhd.com/api/eod/{symbol}.US"
EODHD_DEMO_SYMBOLS = ("AAPL", "TSLA", "VTI", "AMZN")
EODHD_LOOKBACK_DAYS = 31
EODHD_ADJUSTMENT_POLICY = {
    "adjustedClose": "excluded",
    "ohlc": "raw-unadjusted",
    "volume": "provider-native-split-adjusted",
}
EODHD_REVISION_POLICY = {
    "mode": "latest-provider-snapshot",
    "pointInTimeReconstruction": False,
}
_EODHD_PUBLIC_DEMO_TOKEN = "demo"


def _download_text(url, *, limit):
    request = Request(
        url,
        headers={
            "Accept": "text/plain,text/csv;q=0.9,*/*;q=0.1",
            "User-Agent": "TradeEngine-BasicWorkflow/1.0",
        },
        method="GET",
    )
    with urlopen(request, timeout=25) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if content_type and not any(
            token in content_type
            for token in ("text/", "csv", "octet-stream")
        ):
            raise ValueError("Market snapshot provider returned non-text content.")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Market snapshot provider response exceeds the size limit.")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Market snapshot provider response must be UTF-8.") from exc


def _download_json(url, *, limit):
    request = Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.8",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        },
        method="GET",
    )
    with urlopen(request, timeout=25) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if content_type and "json" not in content_type:
            raise ValueError("NASDAQ historical endpoint returned non-JSON content.")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("NASDAQ historical response exceeds the size limit.")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NASDAQ historical endpoint returned invalid JSON.") from exc
    if type(payload) is not dict:
        raise ValueError("NASDAQ historical response must be an object.")
    return payload


def _download_eodhd(url, *, limit):
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=25) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if content_type and "json" not in content_type:
                raise ValueError("EODHD demo response is not JSON.")
            content = response.read(limit + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        # Never relay a provider exception because it can embed the query string.
        raise ValueError("EODHD demo request failed.") from None
    if len(content) > limit:
        raise ValueError("EODHD demo response exceeds the size limit.")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("EODHD demo response is invalid JSON.") from exc
    if type(payload) is not list:
        raise ValueError("EODHD demo response must be an array.")
    return content, payload


def _observed_fixed_holiday(value):
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _nth_weekday(year, month, weekday, occurrence):
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday(year, month, weekday):
    following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    value = following - timedelta(days=1)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _easter_sunday(year):
    """Gregorian Easter, used only to reject the Good Friday closure."""

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _is_known_us_market_closure(value):
    fixed = set()
    for year in (value.year - 1, value.year, value.year + 1):
        fixed.add(_observed_fixed_holiday(date(year, 1, 1)))
        fixed.add(_observed_fixed_holiday(date(year, 7, 4)))
        fixed.add(_observed_fixed_holiday(date(year, 12, 25)))
        if year >= 2022:
            fixed.add(_observed_fixed_holiday(date(year, 6, 19)))
    if value in fixed:
        return True
    year = value.year
    return value in {
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _easter_sunday(year) - timedelta(days=2),
    }


def _is_typical_early_close(value):
    thanksgiving = _nth_weekday(value.year, 11, 3, 4)
    if value == thanksgiving + timedelta(days=1):
        return True
    if value == date(value.year, 12, 24) and value.weekday() < 5:
        return True
    independence_eve = date(value.year, 7, 4) - timedelta(days=1)
    return value == independence_eve and value.weekday() < 5


def _regular_session_close(value):
    """Return 16:00 ET only inside the provider's conservative ordinary-day scope.

    This is deliberately not advertised as a complete exchange calendar. Known
    closures and typical early closes fail closed rather than being stamped with
    a fabricated 16:00 regular close.
    """

    if (
        value.weekday() >= 5
        or _is_known_us_market_closure(value)
        or _is_typical_early_close(value)
    ):
        raise ValueError("date is outside the supported ordinary US trading sessions")
    return datetime.combine(
        value,
        time(16),
        tzinfo=ZoneInfo("America/New_York"),
    )


def _required_text(value, label):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value.strip()


def _instrument_id(symbol):
    segment = re.sub(r"[^A-Za-z0-9_-]+", "-", symbol).strip("-")
    if not segment:
        raise ValueError("Market instrument symbol cannot form an instrumentId.")
    value = f"US-{segment}"
    if not _SEGMENT.fullmatch(value):
        raise ValueError("Market instrumentId is invalid.")
    return value


def _catalog_rows(text, *, listed):
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    required = (
        {"Symbol", "Security Name", "ETF", "Test Issue"}
        if listed
        else {"ACT Symbol", "Security Name", "Exchange", "ETF", "Test Issue"}
    )
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("NASDAQ Trader symbol directory header is invalid.")
    exchange_names = {
        "A": "NYSE American",
        "M": "NYSE American",
        "N": "NYSE",
        "P": "NYSE Arca",
        "V": "IEX",
        "Z": "Cboe BZX",
    }
    for row in reader:
        symbol = str(row.get("Symbol" if listed else "ACT Symbol") or "").strip()
        if not symbol or symbol.startswith("File Creation Time"):
            continue
        if str(row.get("Test Issue") or "").strip() != "N":
            continue
        symbol = symbol.upper()
        if not _SYMBOL.fullmatch(symbol):
            continue
        exchange = (
            "NASDAQ"
            if listed
            else exchange_names.get(
                str(row.get("Exchange") or "").strip(),
                str(row.get("Exchange") or "").strip(),
            )
        )
        yield {
            "instrumentId": _instrument_id(symbol),
            "symbol": symbol,
            "name": _required_text(
                row.get("Security Name"),
                f"NASDAQ Trader {symbol} Security Name",
            ),
            "exchange": _required_text(exchange, f"NASDAQ Trader {symbol} exchange"),
            "currency": "USD",
            "assetType": (
                "etf" if str(row.get("ETF") or "").strip() == "Y" else "stock"
            ),
            "availablePeriods": ["day"],
        }


def _canonical_catalog(*directories):
    by_symbol = {}
    instrument_ids = {}
    for text, listed in directories:
        for instrument in _catalog_rows(text, listed=listed):
            symbol = instrument["symbol"]
            existing = by_symbol.get(symbol)
            if existing is not None:
                if existing != instrument:
                    raise ValueError(
                        f"Market snapshot contains conflicting symbol '{symbol}'."
                    )
                continue
            instrument_id = instrument["instrumentId"]
            previous_symbol = instrument_ids.get(instrument_id)
            if previous_symbol is not None and previous_symbol != symbol:
                raise ValueError(
                    "Market snapshot symbols collide on instrumentId "
                    f"'{instrument_id}'."
                )
            instrument_ids[instrument_id] = symbol
            by_symbol[symbol] = instrument
    if not by_symbol:
        raise ValueError("Market snapshot provider returned no instruments.")
    return sorted(by_symbol.values(), key=lambda item: item["symbol"])


def _market_close_time(value):
    parsed = None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(value, pattern).date()
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError("NASDAQ daily bar date is invalid.")
    close = datetime.combine(
        parsed,
        time(16, 0),
        tzinfo=ZoneInfo("America/New_York"),
    )
    return close.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive_number(value, label):
    if isinstance(value, str):
        value = value.strip().replace("$", "").replace(",", "")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be finite and positive.")
    return result


def _daily_bars(rows):
    if type(rows) is not list:
        raise ValueError("NASDAQ historical rows must be an array.")
    by_time = {}
    for number, row in enumerate(rows, start=1):
        if type(row) is not dict:
            raise ValueError(f"NASDAQ historical row {number} must be an object.")
        event_time = _market_close_time(str(row.get("date") or "").strip())
        values = {
            field: _positive_number(
                row.get(field),
                f"NASDAQ historical row {number} {field}",
            )
            for field in ("open", "close", "high", "low")
        }
        if values["low"] > min(values["open"], values["close"]):
            raise ValueError(
                f"NASDAQ historical row {number} violates the OHLC lower bound."
            )
        if max(values["open"], values["close"]) > values["high"]:
            raise ValueError(
                f"NASDAQ historical row {number} violates the OHLC upper bound."
            )
        bar = {"time": event_time, **values}
        if event_time in by_time and by_time[event_time] != bar:
            raise ValueError(
                f"NASDAQ historical response conflicts at '{event_time}'."
            )
        by_time[event_time] = bar
    bars = [by_time[event_time] for event_time in sorted(by_time)]
    if not bars:
        raise ValueError("Market snapshot provider returned no daily bars.")
    return bars


def _eodhd_bars(rows):
    """Parse the public EODHD demo JSON without manufacturing volume."""
    if type(rows) is not list:
        raise ValueError("EODHD demo response must be an array.")
    bars = []
    previous_date = None
    for number, row in enumerate(rows, 1):
        if type(row) is not dict:
            raise ValueError(f"EODHD row {number} must be an object.")
        fields = {"date", "open", "close", "high", "low", "volume", "adjusted_close"}
        if set(row) != fields:
            raise ValueError(f"EODHD row {number} has an invalid OHLCV schema.")
        date_value = str(row["date"]).strip()
        try:
            parsed = datetime.strptime(date_value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"EODHD row {number} date is invalid.") from exc
        if previous_date is not None and parsed <= previous_date:
            raise ValueError("EODHD row dates must be unique and strictly increasing.")
        try:
            close = _regular_session_close(parsed)
        except ValueError as exc:
            raise ValueError(f"EODHD row {number} {exc}.") from exc
        event_time = close.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        values = {}
        for field in ("open", "close", "high", "low"):
            try:
                value = float(row[field])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"EODHD row {number} {field} must be numeric.") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"EODHD row {number} {field} must be finite and positive.")
            values[field] = value
        try:
            volume = float(row["volume"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"EODHD row {number} volume must be numeric.") from exc
        if not math.isfinite(volume) or volume < 0:
            raise ValueError(f"EODHD row {number} volume must be finite and non-negative.")
        if values["low"] > min(values["open"], values["close"]) or max(values["open"], values["close"]) > values["high"]:
            raise ValueError(f"EODHD row {number} violates OHLC bounds.")
        available = (close.astimezone(timezone.utc) + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        bar = {"time": available, "eventTime": event_time, **values, "volume": volume}
        bars.append(bar)
        previous_date = parsed
    if not bars:
        raise ValueError("EODHD demo provider returned no daily bars.")
    return bars


def _historical_rows(payload):
    data = payload.get("data") if type(payload) is dict else None
    table = data.get("tradesTable") if type(data) is dict else None
    rows = table.get("rows") if type(table) is dict else None
    if type(rows) is not list:
        raise ValueError("NASDAQ historical response has no tradesTable rows.")
    total = data.get("totalRecords")
    if isinstance(total, str):
        total = total.replace(",", "")
        total = int(total) if total.isdecimal() else None
    if isinstance(total, bool) or (total is not None and not isinstance(total, int)):
        raise ValueError("NASDAQ historical totalRecords is invalid.")
    return rows, total


class NasdaqSnapshotProvider:
    """NASDAQ Trader directory plus official NASDAQ historical OHLC snapshots."""

    provider_id = DEFAULT_PROVIDER_ID
    label = "US listed securities (NASDAQ official snapshots)"

    def __init__(self, fetch_text=None, fetch_json=None, *, today=None):
        self._fetch_text = fetch_text or _download_text
        self._fetch_json = fetch_json or _download_json
        self._today = today

    def sync_catalog(self):
        listed = self._fetch_text(NASDAQ_LISTED_URL, limit=_MAX_CATALOG_BYTES)
        other = self._fetch_text(
            NASDAQ_OTHER_LISTED_URL,
            limit=_MAX_CATALOG_BYTES,
        )
        return {
            "asOf": engine_clock.utc_now(),
            "instruments": _canonical_catalog(
                (listed, True),
                (other, False),
            ),
        }

    def download_bars(self, instrument, period):
        if type(instrument) is not dict or period != "day" or period not in instrument.get("availablePeriods", []):
            raise ValueError("Market instrument does not provide the requested period.")
        symbol = _required_text(instrument.get("symbol"), "Market instrument symbol")
        today = self._today or datetime.now(ZoneInfo("America/New_York")).date()
        start = today.replace(year=today.year - 10, day=min(today.day, 28))
        url = NASDAQ_HISTORICAL_URL.format(symbol=symbol) + "?" + urlencode({"assetclass": "etf" if instrument.get("assetType") == "etf" else "stocks", "fromdate": start.isoformat(), "limit": 5000, "offset": 0, "todate": today.isoformat()})
        payload = self._fetch_json(url, limit=_MAX_BARS_BYTES)
        rows, _total = _historical_rows(payload)
        return {"providerId": self.provider_id, "asOf": engine_clock.utc_now(), "period": period, "bars": _daily_bars(rows)}


class EodhdDemoSnapshotProvider:
    """Bounded, public EODHD demo provider (AAPL, TSLA, VTI, AMZN only)."""
    provider_id = EODHD_DEMO_PROVIDER_ID
    label = "EODHD public demo US EOD OHLCV"

    def __init__(self, fetch_json=None, *, today=None):
        self._fetch_json = fetch_json or _download_eodhd
        self._today = today

    def sync_catalog(self):
        instruments = [{
            "instrumentId": _instrument_id(symbol), "symbol": symbol,
            "name": symbol, "exchange": "US", "currency": "USD",
            "assetType": "etf" if symbol == "VTI" else "stock",
            "availablePeriods": ["day"],
        } for symbol in sorted(EODHD_DEMO_SYMBOLS)]
        return {"asOf": engine_clock.utc_now(), "instruments": instruments}

    def download_bars(self, instrument, period):
        if period != "day" or not isinstance(instrument, dict) or period not in instrument.get("availablePeriods", []):
            raise ValueError("EODHD demo instrument does not provide the requested period.")
        symbol = _required_text(instrument.get("symbol"), "Market instrument symbol").upper()
        if symbol not in EODHD_DEMO_SYMBOLS:
            raise ValueError("EODHD demo catalog supports only its public demo symbols.")
        today = self._today or datetime.now(ZoneInfo("America/New_York")).date()
        start = today - timedelta(days=EODHD_LOOKBACK_DAYS)
        url = EODHD_DEMO_URL.format(symbol=symbol) + "?" + urlencode({
            "from": start.isoformat(),
            "to": today.isoformat(),
            "api_token": _EODHD_PUBLIC_DEMO_TOKEN,
            "fmt": "json",
            "period": "d",
            "order": "a",
        })
        try:
            fetched = self._fetch_json(url, limit=_MAX_BARS_BYTES)
        except Exception:
            # An injected downloader can also include the URL in its exception.
            raise ValueError("EODHD demo download failed.") from None
        if (
            type(fetched) is not tuple
            or len(fetched) != 2
            or type(fetched[0]) is not bytes
            or type(fetched[1]) is not list
        ):
            raise ValueError("EODHD demo downloader must return raw bytes and an array.")
        raw, payload = fetched
        if len(raw) > _MAX_BARS_BYTES:
            raise ValueError("EODHD demo response exceeds the size limit.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("EODHD demo raw response is invalid JSON.") from exc
        if decoded != payload:
            raise ValueError("EODHD demo decoded response does not match its raw bytes.")
        bars = _eodhd_bars(payload)
        retrieved = engine_clock.utc_now()
        return {
            "providerId": self.provider_id,
            "asOf": retrieved,
            "retrievedAt": retrieved,
            "rawSha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "adjustmentPolicy": dict(EODHD_ADJUSTMENT_POLICY),
            "revisionPolicy": dict(EODHD_REVISION_POLICY),
            "period": period,
            "bars": bars,
        }

def default_provider_registry():
    provider = NasdaqSnapshotProvider()
    eodhd = EodhdDemoSnapshotProvider()
    return {provider.provider_id: provider, eodhd.provider_id: eodhd}


__all__ = (
    "DEFAULT_PROVIDER_ID",
    "NASDAQ_LISTED_URL",
    "NASDAQ_OTHER_LISTED_URL",
    "NASDAQ_HISTORICAL_URL",
    "NasdaqSnapshotProvider",
    "EodhdDemoSnapshotProvider",
    "EODHD_DEMO_PROVIDER_ID",
    "EODHD_DEMO_URL",
    "EODHD_DEMO_SYMBOLS",
    "EODHD_LOOKBACK_DAYS",
    "EODHD_ADJUSTMENT_POLICY",
    "EODHD_REVISION_POLICY",
    "_eodhd_bars",
    "default_provider_registry",
)
