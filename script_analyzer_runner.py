#!/usr/bin/env python3
from lean_module_worker import serve


strategy = "A"


def handle(command, payload):
    global strategy

    if command == "initialize":
        config = payload.get("configuration") or {}
        parameters = config.get("parameters") or {}
        strategy = parameters.get("strategy", "A")
        return {"requiredObservations": ["stats.summary.net_profit", "orders.count"]}
    if command in {"pause", "resume", "restore"}:
        return {"status": command}
    if command == "snapshot":
        return {"contentType": "application/x.quantconnect.empty-snapshot"}
    if command == "health":
        return {"status": "Healthy"}
    if command == "analyze":
        observations = payload.get("observations") or {}
        return {
            "marker": f"SCRIPT_ANALYZER_{strategy}",
            "values": {
                f"script_{strategy.lower()}_net_profit": observations.get("stats.summary.net_profit", ""),
                f"script_{strategy.lower()}_order_count": observations.get("orders.count", 0),
            },
        }

    raise ValueError(f"Unsupported command: {command}")


if __name__ == "__main__":
    serve(handle)
