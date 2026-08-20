#!/usr/bin/env python3
"""Minimal self-contained pipeline-data-v5 worker used by browser upload tests."""

import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    command = request.get("command")
    payload = {"status": "Healthy"} if command == "health" else (
        {"snapshot": {}} if command == "snapshot" else (
            {"status": "initialized"} if command == "initialize" else (
                {"outputs": {}} if command == "invoke" else {}
            )
        )
    )
    sys.stdout.write(json.dumps({
        "protocolVersion": "pipeline-data-v5",
        "requestId": request.get("requestId", ""),
        "success": True,
        "payload": payload,
        "error": "",
    }) + "\n")
    sys.stdout.flush()
    if command == "close":
        break
