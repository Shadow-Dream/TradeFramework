#!/usr/bin/env python3
import json
import sys


PROTOCOL_VERSION = "lean-module-v1"


def get_field(data, name, default=None):
    if name in data:
        return data[name]
    pascal = name[:1].upper() + name[1:]
    return data.get(pascal, default)


def ok(request, payload=None):
    return response(request, True, payload or {}, "")


def fail(request, error):
    return response(request, False, {}, error)


def response(request, success, payload, error):
    return {
        "protocolVersion": get_field(request, "protocolVersion", PROTOCOL_VERSION),
        "requestId": get_field(request, "requestId", ""),
        "success": success,
        "payload": payload,
        "error": error,
    }


def serve(handler, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    for line in stdin:
        if not line.strip():
            continue

        request = json.loads(line)
        command = get_field(request, "command")
        payload = get_field(request, "payload", {}) or {}

        try:
            result = handler(command, payload)
            if result is None:
                result = {}
            message = ok(request, result)
        except Exception as exc:
            message = fail(request, str(exc))

        stdout.write(json.dumps(message) + "\n")
        stdout.flush()
