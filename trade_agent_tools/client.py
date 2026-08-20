"""Credential-free browser-independent client for the Agent tool boundary."""

from __future__ import annotations

import json
import os
import stat
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 512 * 1024


class TradeToolError(RuntimeError):
    def __init__(self, message, *, code="tool_call_failed", retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TradeToolError("TradeEngine tool endpoint redirected unexpectedly", code="unexpected_redirect")


def _read_grant_file(path):
    if not isinstance(path, str) or not os.path.isabs(path) or len(path) > 4096:
        raise TradeToolError("TradeEngine tool grant file is missing", code="tool_grant_missing")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TradeToolError("TradeEngine tool grant file is unavailable", code="tool_grant_missing") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_nlink != 1
        ):
            raise TradeToolError("TradeEngine tool grant file is unsafe", code="tool_config_invalid")
        raw = os.read(descriptor, 129)
        if len(raw) > 128 or os.read(descriptor, 1):
            raise TradeToolError("TradeEngine tool grant file is invalid", code="tool_config_invalid")
    finally:
        os.close(descriptor)
    try:
        os.unlink(path)
    except OSError:
        pass
    try:
        return raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise TradeToolError("TradeEngine tool grant file is invalid", code="tool_config_invalid") from exc


def _origin(value):
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise TradeToolError("TRADE_ENGINE_TOOL_URL must be an exact http(s) origin", code="tool_config_invalid")
    return f"{parsed.scheme}://{parsed.netloc}"


class TradeToolClient:
    def __init__(self, endpoint=None, grant=None, timeout=30):
        self.endpoint = _origin(endpoint or os.environ.get("TRADE_ENGINE_TOOL_URL", "http://127.0.0.1:30809"))
        self.grant = grant or _read_grant_file(os.environ.get("TRADE_ENGINE_TOOL_GRANT_FILE", ""))
        if len(self.grant) != 43:
            raise TradeToolError("TradeEngine tool grant is missing", code="tool_grant_missing")
        self.timeout = timeout
        self.opener = build_opener(_RejectRedirects())

    def call(self, tool, arguments):
        body = json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self.endpoint}/api/agent-tools/call",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.grant}",
                "Content-Type": "application/json",
            },
        )
        try:
            response = self.opener.open(request, timeout=self.timeout)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
        except HTTPError as exc:
            status = exc.code
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, URLError) as exc:
            raise TradeToolError(
                "TradeEngine tool service is offline",
                code="engine_offline",
                retryable=True,
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TradeToolError("TradeEngine tool response is too large", code="tool_response_too_large")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TradeToolError("TradeEngine tool service returned invalid JSON", code="invalid_tool_response") from exc
        if status != 200:
            raise TradeToolError(
                str(payload.get("error") or f"TradeEngine tool call failed ({status})"),
                code=str(payload.get("code") or "tool_call_failed"),
                retryable=payload.get("retryable") is True,
            )
        if type(payload) is not dict or set(payload) != {"result"}:
            raise TradeToolError("TradeEngine tool response has an invalid schema", code="invalid_tool_response")
        return payload["result"]
