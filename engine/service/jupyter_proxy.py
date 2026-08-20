#!/usr/bin/env python3
"""Streaming HTTP and WebSocket proxy for one resolved Jupyter instance."""

from __future__ import annotations

import http.client
import selectors
import socket


HOP_BY_HOP_HEADERS = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
})


def _forward_headers(handler, websocket=False, token=""):
    headers = {}
    for name, value in handler.headers.items():
        lower = name.lower()
        if not websocket and lower in HOP_BY_HOP_HEADERS:
            continue
        headers[name] = value
    headers["X-Forwarded-Host"] = handler.headers.get("Host", "")
    headers["X-Forwarded-Proto"] = handler.headers.get(
        "X-Forwarded-Proto", "http"
    )
    headers["X-Forwarded-For"] = handler.headers.get(
        "X-Forwarded-For", handler.client_address[0]
    )
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _is_websocket(handler):
    return handler.headers.get("Upgrade", "").lower() == "websocket"


def _handler_stopping(handler):
    stopping = getattr(handler, "stopping", None)
    return stopping is not None and stopping.is_set()


def _proxy_websocket(handler, instance):
    upstream = socket.create_connection(
        (instance["host"], instance["port"]), timeout=10
    )
    try:
        request = [f"{handler.command} {handler.path} HTTP/1.1\r\n"]
        for name, value in _forward_headers(
            handler, websocket=True, token=instance["token"]
        ).items():
            request.append(f"{name}: {value}\r\n")
        request.append("\r\n")
        upstream.sendall("".join(request).encode("iso-8859-1"))
        handler.connection.settimeout(1.0)
        upstream.settimeout(1.0)
        relay = selectors.DefaultSelector()
        try:
            relay.register(handler.connection, selectors.EVENT_READ, upstream)
            relay.register(upstream, selectors.EVENT_READ, handler.connection)
            while not _handler_stopping(handler):
                events = relay.select(timeout=0.5)
                if not events:
                    continue
                for key, _mask in events:
                    data = key.fileobj.recv(64 * 1024)
                    if not data:
                        return
                    key.data.sendall(data)
        finally:
            relay.close()
    finally:
        upstream.close()
        handler.close_connection = True


class _FixedLengthBody:
    """Expose exactly one declared request body without Engine buffering."""

    def __init__(self, stream, length):
        self.stream = stream
        self.remaining = length

    def read(self, size=-1):
        if self.remaining == 0:
            return b""
        if size is None or size < 0:
            size = self.remaining
        chunk = self.stream.read(min(size, self.remaining))
        if not chunk:
            raise ConnectionError(
                "Jupyter proxy request ended before its declared Content-Length."
            )
        if len(chunk) > self.remaining:
            raise ConnectionError(
                "Jupyter proxy request stream exceeded Content-Length."
            )
        self.remaining -= len(chunk)
        return chunk


def _request_body(handler):
    transfer_encodings = handler.headers.get_all("Transfer-Encoding", [])
    if transfer_encodings:
        raise ValueError(
            "Jupyter proxy does not accept Transfer-Encoding; use Content-Length."
        )
    lengths = handler.headers.get_all("Content-Length", [])
    if len(lengths) > 1:
        raise ValueError("Jupyter proxy requires exactly one Content-Length header.")
    if not lengths:
        return None, 0
    raw = lengths[0].strip()
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(
            "Jupyter proxy Content-Length must be a non-negative integer."
        )
    length = int(raw)
    return (_FixedLengthBody(handler.rfile, length) if length else None), length


def _proxy_http(handler, instance):
    body, length = _request_body(handler)
    headers = {
        name: value
        for name, value in _forward_headers(
            handler, token=instance["token"]
        ).items()
        if name.lower() != "content-length"
    }
    if "Content-Length" in handler.headers:
        headers["Content-Length"] = str(length)
    connection = http.client.HTTPConnection(
        instance["host"], instance["port"], timeout=60
    )
    try:
        connection.request(
            handler.command,
            handler.path,
            body=body,
            headers=headers,
        )
        response = connection.getresponse()
        handler.send_response(response.status, response.reason)
        for name, value in response.getheaders():
            if name.lower() not in HOP_BY_HOP_HEADERS:
                handler.send_header(name, value)
        handler.end_headers()
        if handler.command != "HEAD":
            while chunk := response.read(64 * 1024):
                handler.wfile.write(chunk)
    finally:
        connection.close()


def proxy_request(handler, instance):
    if _handler_stopping(handler):
        raise RuntimeError("Engine service is stopping.")
    if _is_websocket(handler):
        _proxy_websocket(handler, instance)
    else:
        _proxy_http(handler, instance)


__all__ = ("proxy_request",)
