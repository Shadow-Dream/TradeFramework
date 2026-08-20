"""Binance Spot minute-page adapter preserving the official JSON response."""

from __future__ import annotations

import math
import time
from typing import Any
from urllib.parse import urlparse

from .base import FetchPage, MiningProvider, strict_json_loads


MINUTE_INTERVALS_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
}
ALLOWED_HOSTS = {
    "api.binance.com",
    "api-gcp.binance.com",
    "api1.binance.com",
    "api2.binance.com",
    "api3.binance.com",
    "api4.binance.com",
}


class BinanceSpotKlineProvider(MiningProvider):
    provider_id = "binance-spot-klines"
    label = "Binance Spot Klines"
    description = (
        "Official public Spot REST pages. The exact JSON array is retained; "
        "array positions are interpreted only inside this provider."
    )
    config_example = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "startTime": 1704067200000,
        "limit": 1000,
        "requestIntervalSeconds": 0.35,
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        allowed = {"symbol", "interval", "startTime", "endTime", "limit", "baseUrl", "requestIntervalSeconds"}
        unknown = sorted(set(config) - allowed)
        if unknown:
            raise ValueError(f"Unknown Binance provider config fields: {', '.join(unknown)}")
        raw_symbol = config.get("symbol")
        if type(raw_symbol) is not str:
            raise ValueError("Binance symbol must be a string.")
        symbol = raw_symbol.strip().upper()
        if not symbol or len(symbol) > 40 or not symbol.isascii() or not symbol.isalnum():
            raise ValueError("Binance symbol must contain 1-40 ASCII letters or digits.")
        raw_interval = config.get("interval")
        if type(raw_interval) is not str:
            raise ValueError("Binance interval must be a string.")
        interval = raw_interval
        if interval not in MINUTE_INTERVALS_MS:
            raise ValueError("Binance minute interval must be one of: 1m, 3m, 5m, 15m, 30m.")
        limit = config.get("limit", 1000)
        if type(limit) is not int:
            raise ValueError("Binance page limit must be an integer.")
        if not 1 <= limit <= 1000:
            raise ValueError("Binance page limit must be between 1 and 1000.")
        raw_base_url = config.get("baseUrl", "https://api.binance.com")
        if type(raw_base_url) is not str:
            raise ValueError("Binance baseUrl must be a string.")
        base_url = raw_base_url.rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.path:
            raise ValueError("Binance baseUrl must be an official HTTPS API host.")
        raw_request_interval = config.get("requestIntervalSeconds", 0.35)
        if type(raw_request_interval) not in {int, float}:
            raise ValueError("Binance requestIntervalSeconds must be a finite number.")
        request_interval = float(raw_request_interval)
        if not math.isfinite(request_interval) or not 0 <= request_interval <= 3600:
            raise ValueError("Binance requestIntervalSeconds must be between 0 and 3600.")
        result: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "baseUrl": base_url,
            "requestIntervalSeconds": max(0.2, request_interval),
        }
        for key in ("startTime", "endTime"):
            if key in config:
                value = config[key]
                if type(value) is not int:
                    raise ValueError(f"Binance {key} must be an integer.")
                if not 0 <= value <= 10**18:
                    raise ValueError(f"Binance {key} must be between 0 and 1e18.")
                result[key] = value
        if result.get("endTime") is not None and result.get("startTime") is not None:
            if result["endTime"] < result["startTime"]:
                raise ValueError("Binance endTime must not precede startTime.")
        return result

    @classmethod
    def initial_cursor(cls, config: dict[str, Any]) -> dict[str, Any]:
        cursor = {}
        if config.get("startTime") is not None:
            cursor["startTime"] = int(config["startTime"])
        if config.get("endTime") is not None:
            cursor["endTime"] = int(config["endTime"])
        return cursor

    @classmethod
    def overlap_cursor(
        cls, cursor: Any, overlap: int, config: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(cursor or {})
        if overlap > 0 and result.get("startTime") is not None:
            step = MINUTE_INTERVALS_MS[config["interval"]]
            floor = int(config.get("startTime", 0))
            result["startTime"] = max(floor, int(result["startTime"]) - overlap * step)
        return result

    def fetch_page(self, cursor: Any, config: dict[str, Any], client: Any) -> FetchPage:
        params = {
            "symbol": config["symbol"],
            "interval": config["interval"],
            "limit": config["limit"],
        }
        params.update(dict(cursor or {}))
        response = client.get(f"{config['baseUrl']}/api/v3/klines", params=params)
        payload = strict_json_loads(response.raw, label="Binance response")
        return FetchPage(
            raw=response.raw,
            payload=payload,
            records=tuple(payload) if isinstance(payload, list) else (),
            status_code=response.status_code,
            headers=response.headers,
            source=response.source,
        )

    @classmethod
    def validate_page(cls, page: FetchPage, config: dict[str, Any]) -> None:
        if not isinstance(page.payload, list):
            raise ValueError("Binance kline response must be a JSON array.")
        previous = None
        for index, record in enumerate(page.payload):
            if not isinstance(record, list) or len(record) < 7:
                raise ValueError(f"Binance record {index} is not an unmodified kline array.")
            if not isinstance(record[0], int) or not isinstance(record[6], int):
                raise ValueError(f"Binance record {index} has invalid provider timestamps.")
            if previous is not None and record[0] <= previous:
                raise ValueError("Binance page is not strictly ordered by provider identity.")
            previous = record[0]

    @classmethod
    def next_cursor(
        cls, cursor: Any, page: FetchPage, config: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not page.records:
            return None
        next_start = int(page.records[-1][0]) + MINUTE_INTERVALS_MS[config["interval"]]
        end_time = (cursor or {}).get("endTime", config.get("endTime"))
        if end_time is not None and next_start > int(end_time):
            return None
        result = {"startTime": next_start}
        if end_time is not None:
            result["endTime"] = int(end_time)
        return result

    @classmethod
    def should_continue(
        cls, page: FetchPage, next_cursor: Any | None, config: dict[str, Any]
    ) -> bool:
        return next_cursor is not None and len(page.records) >= int(config["limit"])

    @classmethod
    def record_identity(cls, record: Any, config: dict[str, Any]) -> Any:
        return record[0]

    @classmethod
    def event_time(cls, record: Any, config: dict[str, Any]) -> Any:
        return record[0]

    @classmethod
    def is_final(cls, record: Any, config: dict[str, Any]) -> bool:
        return int(record[6]) < int(time.time() * 1000)

    @classmethod
    def cursor_for_gap(
        cls, config: dict[str, Any], missing_start: float, missing_end: float
    ) -> dict[str, int]:
        return {"startTime": int(missing_start), "endTime": int(missing_end)}

    @classmethod
    def rate_limit_key(cls, config: dict[str, Any]) -> str:
        return f"{cls.provider_id}:{urlparse(config['baseUrl']).hostname}"

    @classmethod
    def minimum_request_interval(cls, config: dict[str, Any]) -> float:
        return max(0.2, float(config.get("requestIntervalSeconds", 0.35)))
