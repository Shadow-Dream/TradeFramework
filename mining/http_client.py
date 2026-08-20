"""Bounded HTTP client with failures classified for persistent retry state."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from typing import Any, Callable

import httpx

from .providers.base import BlockedProviderError, FetchPage, RetryableProviderError


SAFE_RESPONSE_HEADERS = {
    "content-type",
    "date",
    "etag",
    "last-modified",
    "retry-after",
    "x-mbx-used-weight-1m",
    "x-request-id",
}


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return min(604800.0, max(0.0, seconds)) if math.isfinite(seconds) else None
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return min(604800.0, max(0.0, (target - (now or datetime.now(timezone.utc))).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class RobustHttpClient:
    """HTTPX pooling/timeouts plus mining-specific durable error classes.

    Retries are deliberately not slept here.  The worker writes `retry_wait`
    and `next_run_at` to SQLite so a process exit during backoff loses nothing.
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        on_request: Callable[[], None] | None = None,
    ):
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            limits=limits,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "TradeEngine-Mining/1.0", "Accept-Encoding": "identity"},
        )
        self._on_request = on_request

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RobustHttpClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> FetchPage:
        if self._on_request:
            self._on_request()
        try:
            response = self._client.get(url, params=params)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RetryableProviderError(f"HTTP transport failed: {exc}") from exc
        status = response.status_code
        if status in {401, 403}:
            raise BlockedProviderError(f"Provider authorization/security response: HTTP {status}")
        if status in {408, 418, 429} or 500 <= status <= 599:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            raise RetryableProviderError(
                f"Provider transient response: HTTP {status}", retry_after=retry_after
            )
        if 400 <= status <= 499:
            message = response.text[:300].replace("\n", " ")
            raise BlockedProviderError(f"Provider rejected request: HTTP {status}: {message}")
        if status < 200 or status >= 300:
            raise RetryableProviderError(f"Unexpected provider response: HTTP {status}")
        headers = {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower() in SAFE_RESPONSE_HEADERS
        }
        return FetchPage(
            raw=response.content,
            payload=None,
            records=(),
            status_code=status,
            headers=headers,
            source=str(response.request.url.copy_with(query=None)),
        )
