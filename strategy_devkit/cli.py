#!/usr/bin/env python3
import argparse
import atexit
import getpass
import json
import os
import sys
from urllib.parse import quote

from strategy_devkit.bundle import encode_file
from strategy_devkit.api_client import AuthenticatedApiClient
from engine.contracts import strict_json


API_CLIENT = None


def post_json(url, payload):
    return API_CLIENT.post(url, payload)


def get_json(url):
    return API_CLIENT.get(url)


def load_json_value(value, path):
    if path:
        with open(path, encoding="utf-8") as handle:
            return strict_json.load(handle)
    if value:
        return strict_json.loads(value)
    return {}


def load_payload(path):
    with open(path, encoding="utf-8") as handle:
        return strict_json.load(handle)


def require_args(args, names, command):
    missing = [name for name in names if not getattr(args, name)]
    if missing:
        flags = ", ".join("--" + name.replace("_", "-") for name in missing)
        raise SystemExit(f"{command} requires {flags} unless a full JSON file is provided.")


def parse_file_specs(local_files):
    files = []
    for item in local_files or []:
        parts = item.split(":")
        if len(parts) < 2:
            raise SystemExit("--file must be local_path:bundle_path[:x]")
        files.append(encode_file(parts[0], parts[1], len(parts) > 2 and parts[2] == "x"))

    return files


def print_response(status, response):
    print(json.dumps(response, indent=2))
    if status < 200 or status >= 300 or response.get("accepted") is False:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Strategy control API client.")
    parser.add_argument("--api", default="https://trade.duckduckrun.com", help="Authenticated Engine API base URL.")
    parser.add_argument("--email", default=os.environ.get("TRADE_AUTH_EMAIL", "0shadow0dream0@gmail.com"))
    parser.add_argument("--password-stdin", action="store_true", help="Read the password from stdin instead of a terminal prompt.")
    subparsers = parser.add_subparsers(dest="command")

    add_module = subparsers.add_parser("add-module", help="Add a module definition to the module repository.")
    add_module.add_argument("--kind")
    add_module.add_argument("--module-id")
    add_module.add_argument("--activation-mode")
    add_module.add_argument("--parameters-json", default="")
    add_module.add_argument("--parameters-file", default="")
    add_module.add_argument("--config-schema-json", default="")
    add_module.add_argument("--config-schema-file", default="")
    add_module.add_argument("--ports-json", default="")
    add_module.add_argument("--ports-file", default="")
    add_module.add_argument("--definition", help="Full module definition JSON file.")
    add_module.add_argument("--file", action="append", default=[], help="local_path:module_path[:x]")
    add_module.add_argument(
        "--repository",
        choices=("modules", "analysis-modules", "environment-modules"),
        default="modules",
    )

    save_pipeline = subparsers.add_parser("save-pipeline", help="Archive a complete Pipeline Draft.")
    save_pipeline.add_argument("--pipeline", required=True, help="Complete Pipeline Draft JSON file.")

    subparsers.add_parser("list-modules", help="List module definitions.")
    subparsers.add_parser("list-pipelines", help="List Pipelines.")
    history = subparsers.add_parser("history", help="Show persisted control-plane history.")
    history.add_argument("--limit", type=int, default=100)
    current = subparsers.add_parser("current", help="Show one Pipeline manifest.")
    current.add_argument("--pipeline-id", required=True)
    args = parser.parse_args()

    api = args.api.rstrip("/")
    if not args.command:
        parser.print_help()
        return
    password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass.getpass("Trade Engine password: ")
    global API_CLIENT
    try:
        API_CLIENT = AuthenticatedApiClient(api)
        API_CLIENT.login(args.email, password)
        atexit.register(API_CLIENT.logout)
    except (ValueError, PermissionError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        password = ""

    if args.command == "add-module":
        if args.definition:
            payload = load_payload(args.definition)
        else:
            require_args(args, ["kind", "module_id", "activation_mode"], "add-module")
            parameters = load_json_value(args.parameters_json, args.parameters_file)
            payload = {
                "kind": args.kind,
                "moduleId": args.module_id,
                "activationMode": args.activation_mode,
                "parameters": parameters,
                "configSchema": load_json_value(args.config_schema_json, args.config_schema_file),
                "ports": load_json_value(args.ports_json, args.ports_file),
                "files": parse_file_specs(args.file),
            }
        status, response = post_json(api + f"/api/{args.repository}", payload)
    elif args.command == "save-pipeline":
        payload = load_payload(args.pipeline)
        pipeline_id = quote(str(payload.get("pipelineId") or ""), safe="")
        if not pipeline_id:
            raise SystemExit("Pipeline Definition requires pipelineId.")
        status, response = post_json(api + f"/api/pipelines/{pipeline_id}/versions", payload)
    elif args.command == "list-modules":
        status, response = get_json(api + "/api/modules")
    elif args.command == "list-pipelines":
        status, response = get_json(api + "/api/pipelines")
    elif args.command == "history":
        status, response = get_json(api + f"/api/history?limit={args.limit}")
    elif args.command == "current":
        status, response = get_json(api + f"/api/pipelines/{quote(args.pipeline_id, safe='')}")
    else:
        parser.print_help()
        sys.exit(2)

    print_response(status, response)


if __name__ == "__main__":
    main()
