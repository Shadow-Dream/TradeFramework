"""Server-to-server bridge from Engine tool grants to Kanna's live UI Hub."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_UI_TOOL_RESPONSE_BYTES = 2 * 1024 * 1024


class UiToolBridgeError(RuntimeError):
    def __init__(self, message, *, code="ui_bridge_failed", retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UiToolBridgeError("Agent UI bridge redirected unexpectedly", code="unexpected_redirect")


def _exact_origin(value):
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
        raise UiToolBridgeError("Agent UI bridge origin is invalid", code="bridge_unavailable", retryable=True)
    return f"{parsed.scheme}://{parsed.netloc}"


def call_ui_tool(agent_origin, bridge_token, tool_name, arguments, *, timeout=15, opener=None):
    if not isinstance(bridge_token, str) or not bridge_token:
        raise UiToolBridgeError("Agent UI bridge is unavailable", code="bridge_unavailable", retryable=True)
    origin = _exact_origin(agent_origin)
    body = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        f"{origin}/api/internal/ui-tools/call",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bridge_token}",
            "Content-Type": "application/json",
        },
    )
    client = opener or build_opener(ProxyHandler({}), _RejectRedirects())
    try:
        response = client.open(request, timeout=timeout)
        status = response.status
        raw = response.read(MAX_UI_TOOL_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        raw = exc.read(MAX_UI_TOOL_RESPONSE_BYTES + 1)
    except (OSError, URLError) as exc:
        raise UiToolBridgeError(
            "Agent UI bridge is offline",
            code="ui_bridge_offline",
            retryable=True,
        ) from exc
    if len(raw) > MAX_UI_TOOL_RESPONSE_BYTES:
        raise UiToolBridgeError("Agent UI bridge response is too large", code="tool_output_too_large")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UiToolBridgeError("Agent UI bridge returned invalid JSON", code="invalid_tool_response") from exc
    if type(payload) is not dict:
        raise UiToolBridgeError("Agent UI bridge returned an invalid response", code="invalid_tool_response")
    if status != 200:
        raise UiToolBridgeError(
            str(payload.get("error") or f"Agent UI bridge failed ({status})"),
            code=str(payload.get("code") or "ui_bridge_failed"),
            retryable=payload.get("retryable") is True,
        )
    if set(payload) != {"result"}:
        raise UiToolBridgeError("Agent UI bridge returned an invalid response", code="invalid_tool_response")
    return payload["result"]
