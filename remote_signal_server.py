#!/usr/bin/env python3
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


STATE = {}


def ok(payload=None):
    return {"success": True, "payload": payload or {}}


def marker(name, when):
    return f"{name}:{when}"


def insights_for(strategy, when):
    if strategy == "A":
        if when == "2014-06-06":
            return [("SPY", "Equity", "usa", "Up", 1)]
        if when == "2014-06-10":
            return [("SPY", "Equity", "usa", "Flat", 1)]
        if when == "2014-06-18":
            return [("SPY", "Equity", "usa", "Up", 1)]
        if when == "2014-06-19":
            return [("SPY", "Equity", "usa", "Flat", 1)]
    else:
        if when == "2014-06-16":
            return [("QQQ", "Equity", "usa", "Up", 1)]
        if when == "2014-06-17":
            return [("QQQ", "Equity", "usa", "Flat", 1)]
    return []


def scale_for(strategy):
    return 0.6 if strategy == "A" else 0.9


def module_state(payload):
    key = payload.get("moduleKey") or payload.get("module_key")
    if not key:
        return {"strategy": "A", "role": "signal"}
    return STATE.get(key, {"strategy": "A", "role": "signal"})


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/invoke":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        command = request.get("command") or request.get("Command")
        payload = request.get("payload") or request.get("Payload") or {}

        try:
            if command == "initialize":
                config = (payload.get("configuration") or {})
                parameters = config.get("parameters") or {}
                module_key = config.get("key")
                STATE[module_key] = {
                    "strategy": parameters.get("strategy", "A"),
                    "role": parameters.get("role", "signal"),
                }
                response = ok({"status": "initialized"})
            elif command in {"pause", "resume", "restore"}:
                response = ok({"status": command})
            elif command == "snapshot":
                response = ok({"contentType": "application/x.quantconnect.empty-snapshot"})
            elif command == "health":
                response = ok({"status": "Healthy"})
            elif command == "analyze" and module_state(payload).get("role") == "analyzer":
                strategy = module_state(payload)["strategy"]
                observations = payload.get("observations", {})
                response = ok(
                    {
                        "marker": f"REMOTE_ANALYZER_{strategy}",
                        "values": {
                            f"remote_{strategy.lower()}_status": observations.get("state.status", ""),
                            f"remote_{strategy.lower()}_orders": observations.get("orders.count", 0),
                        },
                        "requiredObservations": ["state.status", "orders.count"],
                    }
                )
            elif command == "update_signal" and module_state(payload).get("role") == "signal":
                when = datetime.fromisoformat(payload["timeUtc"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
                strategy = module_state(payload)["strategy"]
                response = ok(
                    {
                        "marker": marker(f"REMOTE_SIGNAL_{strategy}", when),
                        "insights": [
                            {
                                "symbol": {"value": value, "securityType": security_type, "market": market},
                                "direction": direction,
                                "periodDays": period_days,
                                "sourceModel": f"RemoteSignal{strategy}",
                            }
                            for value, security_type, market, direction, period_days in insights_for(strategy, when)
                        ],
                    }
                )
            elif command == "manage_risk" and module_state(payload).get("role") == "risk":
                when = datetime.fromisoformat(payload["timeUtc"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
                strategy = module_state(payload)["strategy"]
                factor = scale_for(strategy)
                response = ok(
                    {
                        "marker": marker(f"REMOTE_RISK_{strategy}", when),
                        "targets": [
                            {
                                "symbol": item["symbol"],
                                "quantity": item["quantity"] * factor,
                                "tag": item.get("tag", "") + f"|REMOTE_RISK_{strategy}",
                            }
                            for item in payload.get("targets", [])
                        ],
                    }
                )
            elif command in {"can_submit_order", "can_execute_order", "describe_market_rule"} and module_state(payload).get("role") == "market":
                strategy = module_state(payload)["strategy"]
                security = payload.get("security", {})
                symbol = security.get("value", "")
                suffix = strategy.upper()
                response = ok(
                    {
                        "allowed": True,
                        "leverage": 2 if strategy == "A" else 3,
                        "fee": 0.001 if strategy == "A" else 0.003,
                        "slippage": 0.0001 if strategy == "A" else 0.0008,
                        "marker": f"REMOTE_MARKET_{suffix}:{symbol}:{command}",
                        "message": "",
                    }
                )
            else:
                response = {"success": False, "error": f"Unsupported command: {command}"}
        except Exception as exc:
            response = {"success": False, "error": str(exc)}

        response.setdefault("protocolVersion", request.get("protocolVersion") or request.get("ProtocolVersion", "lean-module-v1"))
        response.setdefault("requestId", request.get("requestId") or request.get("RequestId", ""))
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()
