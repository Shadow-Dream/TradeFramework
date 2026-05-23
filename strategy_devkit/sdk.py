#!/usr/bin/env python3
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PROTOCOL_VERSION = "lean-module-v1"


def equity(ticker, market="usa"):
    return {"value": ticker, "securityType": "Equity", "market": market}


def insight(symbol, direction, period_days=1, source_model=""):
    return {
        "symbol": symbol,
        "direction": direction,
        "periodDays": period_days,
        "sourceModel": source_model,
    }


def target_percent(symbol, percent, tag=""):
    return {"symbol": symbol, "percent": percent, "tag": tag}


def target_quantity(symbol, quantity, tag=""):
    return {"symbol": symbol, "quantity": quantity, "tag": tag}


def order(symbol, quantity, tag=""):
    return {"symbol": symbol, "quantity": quantity, "tag": tag}


def get_field(data, name, default=None):
    if name in data:
        return data[name]
    pascal = name[:1].upper() + name[1:]
    return data.get(pascal, default)


def parse_time(payload):
    raw = payload.get("timeUtc")
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


class LeanStrategy:
    def initialize(self, configuration):
        return {"status": "initialized"}

    def inputs(self, context):
        return []

    def universe(self, context):
        return []

    def signals(self, context):
        return []

    def targets(self, context, insights):
        return []

    def risk(self, context, targets):
        return targets

    def execute(self, context, targets):
        return [order(item["symbol"], item["quantity"], (item.get("tag") or "") + "|EXEC") for item in targets]

    def market_rule(self, context, command, payload):
        return {
            "allowed": True,
            "leverage": 1,
            "fee": 0,
            "slippage": 0,
            "marker": "",
            "message": "",
        }

    def analyze(self, observations):
        return {"values": {}, "requiredObservations": []}


def context_from_payload(payload):
    return {
        "time": parse_time(payload),
        "raw": payload,
    }


def handle_command(strategy, command, payload):
    if command == "initialize":
        return strategy.initialize(payload.get("configuration") or {})
    if command == "health":
        return {"status": "Healthy"}
    if command == "snapshot":
        return {"contentType": "application/x.quantconnect.empty-snapshot"}
    if command in {"pause", "resume", "restore"}:
        return {"status": command}

    context = context_from_payload(payload)
    if command == "register_inputs":
        return {"inputs": strategy.inputs(context)}
    if command == "update_signal":
        return {"insights": strategy.signals(context)}
    if command == "create_targets":
        return {"targets": strategy.targets(context, payload.get("insights", []))}
    if command == "manage_risk":
        return {"targets": strategy.risk(context, payload.get("targets", []))}
    if command == "execute_targets":
        return {"orders": strategy.execute(context, payload.get("targets", []))}
    if command in {"describe_market_rule", "can_submit_order", "can_execute_order"}:
        return strategy.market_rule(context, command, payload)
    if command == "analyze":
        return strategy.analyze(payload.get("observations", {}))

    raise ValueError(f"Unsupported command: {command}")


def make_response(request, success, payload=None, error=""):
    return {
        "protocolVersion": get_field(request, "protocolVersion", PROTOCOL_VERSION),
        "requestId": get_field(request, "requestId", ""),
        "success": success,
        "payload": payload or {},
        "error": error,
    }


def serve_json_lines(strategy, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        command = get_field(request, "command")
        payload = get_field(request, "payload", {}) or {}
        try:
            response = make_response(request, True, handle_command(strategy, command, payload))
        except Exception as exc:
            response = make_response(request, False, {}, str(exc))
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()


def serve_http(strategy, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/invoke":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
            command = get_field(request, "command")
            payload = get_field(request, "payload", {}) or {}
            try:
                response = make_response(request, True, handle_command(strategy, command, payload))
            except Exception as exc:
                response = make_response(request, False, {}, str(exc))

            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def run(strategy):
    mode = sys.argv[1] if len(sys.argv) > 1 else "worker"
    if mode == "http":
        host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 8765
        serve_http(strategy, host, port)
    elif mode == "worker":
        serve_json_lines(strategy)
    else:
        raise SystemExit("Usage: strategy.py [worker|http [host port]]")
