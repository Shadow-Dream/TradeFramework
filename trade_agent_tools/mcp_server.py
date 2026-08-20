#!/usr/bin/env python3
"""Small stdio MCP adapter for TradeEngine's exact Agent tools.

This deliberately implements only the MCP initialization and tool messages we
need.  It is not an Agent loop and it carries no browser credential.
"""

from __future__ import annotations

import json
import sys

from .client import TradeToolClient, TradeToolError


TOOLS = [
    ("trade_context_get", "Resolve this Turn's exact TradeEngine Context and capability summary.", {"type": "object", "additionalProperties": False}),
    ("trade_catalog_find", "Find compatible versioned TradeEngine resources.", {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"kind": {"type": "string", "enum": ["pipeline", "dataset", "environment", "analysis", "module", "backtest", "result"]}, "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}),
    ("trade_dataset_inspect", "Inspect an exact Dataset version with a bounded record preview and conformance metadata.", {"type": "object", "additionalProperties": False, "required": ["datasetId", "version"], "properties": {"datasetId": {"type": "string"}, "version": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}),
    ("trade_validate", "Run authoritative validation for a draft without saving it.", {"type": "object", "additionalProperties": False, "required": ["kind", "draft"], "properties": {"kind": {"type": "string", "enum": ["pipeline", "environment", "analysis", "module", "backtest-composition"]}, "draft": {"type": "object"}}}),
    ("trade_backtest_get", "Read one Backtest's frozen composition, job status, errors and Result availability.", {"type": "object", "additionalProperties": False, "required": ["backtestId"], "properties": {"backtestId": {"type": "string"}}}),
    ("trade_result_query", "Describe or query bounded fields/cycles from a verified Result.", {"type": "object", "additionalProperties": False, "required": ["backtestId", "mode"], "properties": {"backtestId": {"type": "string"}, "mode": {"type": "string", "enum": ["describe", "fields", "cycles"]}, "paths": {"type": "array", "maxItems": 16, "items": {"type": "string"}}, "offset": {"type": "integer", "minimum": 0, "maximum": 10000}, "limit": {"type": "integer", "minimum": 1, "maximum": 25}}}),
    ("trade_proposal_create", "Validate and create a display-only AnalysisBrief/Proposal artifact. This never applies or executes it.", {"type": "object", "additionalProperties": False, "required": ["artifact"], "properties": {"artifact": {"type": "object"}}}),
    ("trade_ui_state_get", "Read the live Engine/Jupyter tab, semantic selection, open-document and operation state from the UI Hub.", {"type": "object", "additionalProperties": False}),
    ("trade_ui_document_get", "Read an exact currently open text or structured draft document from its live editor.", {"type": "object", "additionalProperties": False, "required": ["documentId"], "properties": {"operationId": {"type": "string", "minLength": 8, "maxLength": 128}, "documentId": {"type": "string", "minLength": 1, "maxLength": 256}, "includeContent": {"type": "boolean"}}}),
    ("trade_ui_document_patch", "Apply one compare-and-swap text replacement to a live editor. Read again after revision_conflict; operationId makes retries idempotent.", {"type": "object", "additionalProperties": False, "required": ["operationId", "documentId", "baseRevision", "baseDigest", "patch", "save"], "properties": {"operationId": {"type": "string", "minLength": 8, "maxLength": 128}, "documentId": {"type": "string", "minLength": 1, "maxLength": 256}, "baseRevision": {"type": "integer", "minimum": 0}, "baseDigest": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "patch": {"type": "object", "additionalProperties": False, "required": ["type", "start", "end", "text"], "properties": {"type": {"const": "replace"}, "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 0}, "text": {"type": "string", "maxLength": 262144}}}, "save": {"type": "boolean"}}}),
]


def _write(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(request_id, value):
    _write({"jsonrpc": "2.0", "id": request_id, "result": value})


def _error(request_id, code, message):
    _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def main():
    client = None
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "Parse error")
            continue
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
            continue
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            continue
        if method == "initialize":
            try:
                client = TradeToolClient()
            except TradeToolError as exc:
                _error(request_id, -32001, f"{exc.code}: {exc}")
                continue
            _result(request_id, {"protocolVersion": "2025-11-25", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "trade-engine", "version": "1"}})
            continue
        if method == "ping":
            _result(request_id, {})
            continue
        if method == "tools/list":
            _result(request_id, {"tools": [{"name": name, "description": description, "inputSchema": schema} for name, description, schema in TOOLS]})
            continue
        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str) or not isinstance(params.get("arguments", {}), dict):
                _error(request_id, -32602, "Invalid tool arguments")
                continue
            try:
                if client is None:
                    client = TradeToolClient()
                value = client.call(params["name"], params.get("arguments", {}))
                _result(request_id, {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value, "isError": False})
            except TradeToolError as exc:
                _result(request_id, {"content": [{"type": "text", "text": f"{exc.code}: {exc}"}], "structuredContent": {"code": exc.code, "retryable": exc.retryable}, "isError": True})
            continue
        _error(request_id, -32601, "Method not found")


if __name__ == "__main__":
    main()
