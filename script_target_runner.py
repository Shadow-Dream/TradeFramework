#!/usr/bin/env python3
from datetime import datetime

from lean_module_worker import serve


strategy = "A"
role = "target"


def marker(name, when):
    return f"{name}:{when}"


def handle(command, payload):
    global strategy, role

    if command == "initialize":
        config = payload.get("configuration") or {}
        parameters = config.get("parameters") or {}
        strategy = parameters.get("strategy", "A")
        role = parameters.get("role", "target")
        return {"status": "initialized"}
    if command in {"pause", "resume", "restore"}:
        return {"status": command}
    if command == "snapshot":
        return {"contentType": "application/x.quantconnect.empty-snapshot"}
    if command == "health":
        return {"status": "Healthy"}
    if command == "create_targets" and role == "target":
        when = datetime.fromisoformat(payload["timeUtc"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
        targets = []
        for insight in payload.get("insights", []):
            percent = 0.0 if insight.get("direction") == "Flat" else (0.4 if strategy == "A" else 0.8)
            targets.append(
                {
                    "symbol": insight["symbol"],
                    "percent": percent,
                    "tag": f"SCRIPT_TARGET_{strategy}:{when}",
                }
            )
        return {"marker": marker(f"SCRIPT_TARGET_{strategy}", when), "targets": targets}
    if command == "execute_targets" and role == "execution":
        when = datetime.fromisoformat(payload["timeUtc"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
        return {
            "marker": marker(f"SCRIPT_EXEC_{strategy}", when),
            "orders": [
                {
                    "symbol": item["symbol"],
                    "quantity": item.get("quantity", 0),
                    "tag": item.get("tag", "") + f"|SCRIPT_EXEC_{strategy}",
                }
                for item in payload.get("targets", [])
            ],
        }

    raise ValueError(f"Unsupported command: {command}")


if __name__ == "__main__":
    serve(handle)
