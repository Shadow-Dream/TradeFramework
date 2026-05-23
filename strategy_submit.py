#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error
import urllib.request

from strategy_devkit.bundle import encode_file, remote_file


def post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def delete_json(url):
    request = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def load_json_value(value, path):
    if path:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    if value:
        return json.loads(value)
    return {}


def load_payload(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def parse_file_specs(local_files, remote_files):
    files = []
    for item in local_files or []:
        parts = item.split(":")
        if len(parts) < 2:
            raise SystemExit("--file must be local_path:bundle_path[:x]")
        files.append(encode_file(parts[0], parts[1], len(parts) > 2 and parts[2] == "x"))

    for item in remote_files or []:
        parts = item.split(",")
        if len(parts) < 2:
            raise SystemExit("--remote-file must be url,bundle_path[,sha256=<hex>][,x]")
        flags = parts[2:]
        checksum = next((flag.removeprefix("sha256=") for flag in flags if flag.startswith("sha256=")), None)
        files.append(remote_file(parts[0], parts[1], checksum, "x" in flags))

    return files


def print_response(status, response):
    print(json.dumps(response, indent=2))
    if status < 200 or status >= 300 or response.get("accepted") is False:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Strategy control API client.")
    parser.add_argument("--api", default="http://127.0.0.1:8777", help="Strategy control API base URL.")
    parser.add_argument("--bundle", help="Legacy shortcut: submit a strategy bundle JSON.")
    subparsers = parser.add_subparsers(dest="command")

    submit = subparsers.add_parser("submit", help="Submit a legacy strategy bundle.")
    submit.add_argument("--bundle", required=True)

    add_module = subparsers.add_parser("add-module", help="Add a module definition to the module repository.")
    add_module.add_argument("--kind", required=True)
    add_module.add_argument("--module-id", required=True)
    add_module.add_argument("--version", required=True)
    add_module.add_argument("--activation-mode", required=True)
    add_module.add_argument("--entry-point", default="")
    add_module.add_argument("--hot-swap-mode", default="RequiresPause")
    add_module.add_argument("--parameters-json", default="")
    add_module.add_argument("--parameters-file", default="")
    add_module.add_argument("--config-schema-json", default="")
    add_module.add_argument("--config-schema-file", default="")
    add_module.add_argument("--definition", help="Full module definition JSON file.")
    add_module.add_argument("--file", action="append", default=[], help="local_path:module_path[:x]")
    add_module.add_argument("--remote-file", action="append", default=[], help="url,module_path[,sha256=<hex>][,x]")

    delete_module = subparsers.add_parser("delete-module", help="Delete an unused module definition version.")
    delete_module.add_argument("--kind", required=True)
    delete_module.add_argument("--module-id", required=True)
    delete_module.add_argument("--version", required=True)

    create_instance = subparsers.add_parser("create-instance", help="Create a configured module instance.")
    create_instance.add_argument("--instance-id", required=True)
    create_instance.add_argument("--kind", required=True)
    create_instance.add_argument("--module-id", required=True)
    create_instance.add_argument("--version", required=True)
    create_instance.add_argument("--config-json", default="")
    create_instance.add_argument("--config-file", default="")
    create_instance.add_argument("--parameters-json", default="")
    create_instance.add_argument("--parameters-file", default="")
    create_instance.add_argument("--instance", help="Full instance JSON file.")

    attach = subparsers.add_parser("attach", help="Attach configured instances to the active pipeline.")
    attach.add_argument("--strategy-id", required=True)
    attach.add_argument("--version", required=True)
    attach.add_argument("--name", default="")
    attach.add_argument("--stages-json", default="")
    attach.add_argument("--stages-file", default="")
    attach.add_argument("--market-json", default="")
    attach.add_argument("--market-file", default="")
    attach.add_argument("--market-rule", default="")
    attach.add_argument("--attachment", help="Full attachment JSON file.")

    detach = subparsers.add_parser("detach", help="Detach stages, market slots, or instances from the active pipeline.")
    detach.add_argument("--instances-json", default="")
    detach.add_argument("--instances-file", default="")
    detach.add_argument("--stages-json", default="")
    detach.add_argument("--stages-file", default="")
    detach.add_argument("--market-json", default="")
    detach.add_argument("--market-file", default="")
    detach.add_argument("--request", help="Full detach request JSON file.")

    subparsers.add_parser("list-modules", help="List module definitions.")
    subparsers.add_parser("list-instances", help="List module instances.")
    subparsers.add_parser("current", help="Show current Engine manifest.")
    args = parser.parse_args()

    api = args.api.rstrip("/")

    if args.bundle:
        status, response = post_json(api + "/v1/strategies/submit", load_payload(args.bundle))
        print_response(status, response)
        return

    if args.command == "submit":
        status, response = post_json(api + "/v1/strategies/submit", load_payload(args.bundle))
    elif args.command == "add-module":
        payload = load_payload(args.definition) if args.definition else {
            "kind": args.kind,
            "moduleId": args.module_id,
            "version": args.version,
            "activationMode": args.activation_mode,
            "entryPoint": args.entry_point,
            "hotSwapMode": args.hot_swap_mode,
            "parameters": load_json_value(args.parameters_json, args.parameters_file),
            "configSchema": load_json_value(args.config_schema_json, args.config_schema_file),
            "files": parse_file_specs(args.file, args.remote_file),
        }
        status, response = post_json(api + "/v1/modules", payload)
    elif args.command == "delete-module":
        path = f"/v1/modules/{args.kind}/{args.module_id}/versions/{args.version}"
        status, response = delete_json(api + path)
    elif args.command == "create-instance":
        payload = load_payload(args.instance) if args.instance else {
            "instanceId": args.instance_id,
            "kind": args.kind,
            "moduleId": args.module_id,
            "version": args.version,
            "config": load_json_value(args.config_json, args.config_file),
            "parameters": load_json_value(args.parameters_json, args.parameters_file),
        }
        status, response = post_json(api + "/v1/pipeline/instances", payload)
    elif args.command == "attach":
        payload = load_payload(args.attachment) if args.attachment else {
            "strategyId": args.strategy_id,
            "version": args.version,
            "name": args.name,
            "stages": load_json_value(args.stages_json, args.stages_file),
            "market": load_json_value(args.market_json, args.market_file),
            "marketRule": args.market_rule,
        }
        status, response = post_json(api + "/v1/pipeline/attach", payload)
    elif args.command == "detach":
        payload = load_payload(args.request) if args.request else {
            "instances": load_json_value(args.instances_json, args.instances_file) or [],
            "stages": load_json_value(args.stages_json, args.stages_file) or [],
            "market": load_json_value(args.market_json, args.market_file) or [],
        }
        status, response = post_json(api + "/v1/pipeline/detach", payload)
    elif args.command == "list-modules":
        status, response = get_json(api + "/v1/modules")
    elif args.command == "list-instances":
        status, response = get_json(api + "/v1/pipeline/instances")
    elif args.command == "current":
        status, response = get_json(api + "/v1/strategies/current")
    else:
        parser.print_help()
        sys.exit(2)

    print_response(status, response)


if __name__ == "__main__":
    main()
