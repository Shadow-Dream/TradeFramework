#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error
import urllib.request

from strategy_devkit.bundle import encode_file, remote_file


ARTIFACT_KINDS = {
    "data": "Data",
    "snapshot": "Snapshot",
    "checkpoint": "Checkpoint",
    "result": "Result",
    "report": "Report",
    "log": "Log",
}


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


def require_args(args, names, command):
    missing = [name for name in names if not getattr(args, name)]
    if missing:
        flags = ", ".join("--" + name.replace("_", "-") for name in missing)
        raise SystemExit(f"{command} requires {flags} unless a full JSON file is provided.")


def canonical_artifact_kind(kind):
    value = ARTIFACT_KINDS.get((kind or "").lower())
    if not value:
        raise SystemExit(f"--kind must be one of: {', '.join(sorted(ARTIFACT_KINDS))}")
    return value


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
    submit.add_argument("--lane-id", default="")

    add_module = subparsers.add_parser("add-module", help="Add a module definition to the module repository.")
    add_module.add_argument("--kind")
    add_module.add_argument("--module-id")
    add_module.add_argument("--version")
    add_module.add_argument("--activation-mode")
    add_module.add_argument("--entry-point", default="")
    add_module.add_argument("--hot-swap-mode", default="RequiresPause")
    add_module.add_argument("--parameters-json", default="")
    add_module.add_argument("--parameters-file", default="")
    add_module.add_argument("--remote-url", default="", help="RemoteService base URL shortcut for parameters.baseUrl.")
    add_module.add_argument("--remote-protocol-version", default="lean-module-v1")
    add_module.add_argument("--remote-contract-hash", default="", help="RemoteService pinned contract hash, sha256:<64-hex>.")
    add_module.add_argument("--remote-deployment-id", default="", help="RemoteService immutable deployment identifier.")
    add_module.add_argument("--remote-manifest-url", default="", help="Optional URL for the remote backend manifest.")
    add_module.add_argument("--config-schema-json", default="")
    add_module.add_argument("--config-schema-file", default="")
    add_module.add_argument("--ports-json", default="")
    add_module.add_argument("--ports-file", default="")
    add_module.add_argument("--definition", help="Full module definition JSON file.")
    add_module.add_argument("--file", action="append", default=[], help="local_path:module_path[:x]")
    add_module.add_argument("--remote-file", action="append", default=[], help="url,module_path[,sha256=<hex>][,x]")

    add_package = subparsers.add_parser("add-package", help="Add one uploaded package and register one or more module definitions.")
    add_package.add_argument("--package-id")
    add_package.add_argument("--version")
    add_package.add_argument("--definition", help="Full package JSON file.")
    add_package.add_argument("--module", action="append", default=[], help="Module definition JSON file inside this package.")
    add_package.add_argument("--metadata-json", default="")
    add_package.add_argument("--metadata-file", default="")
    add_package.add_argument("--file", action="append", default=[], help="local_path:package_path[:x]")
    add_package.add_argument("--remote-file", action="append", default=[], help="url,package_path[,sha256=<hex>][,x]")

    delete_module = subparsers.add_parser("delete-module", help="Delete an unused module definition version.")
    delete_module.add_argument("--kind", required=True)
    delete_module.add_argument("--module-id", required=True)
    delete_module.add_argument("--version", required=True)

    create_instance = subparsers.add_parser("create-instance", help="Create a configured module instance.")
    create_instance.add_argument("--instance-id")
    create_instance.add_argument("--kind")
    create_instance.add_argument("--module-id")
    create_instance.add_argument("--version")
    create_instance.add_argument("--config-json", default="")
    create_instance.add_argument("--config-file", default="")
    create_instance.add_argument("--parameters-json", default="")
    create_instance.add_argument("--parameters-file", default="")
    create_instance.add_argument("--inputs-json", default="")
    create_instance.add_argument("--inputs-file", default="")
    create_instance.add_argument("--outputs-json", default="")
    create_instance.add_argument("--outputs-file", default="")
    create_instance.add_argument("--instance", help="Full instance JSON file.")

    attach = subparsers.add_parser("attach", help="Attach configured instances to the active pipeline.")
    attach.add_argument("--lane-id", default="")
    attach.add_argument("--strategy-id")
    attach.add_argument("--version")
    attach.add_argument("--name", default="")
    attach.add_argument("--stages-json", default="")
    attach.add_argument("--stages-file", default="")
    attach.add_argument("--market-json", default="")
    attach.add_argument("--market-file", default="")
    attach.add_argument("--market-rule", default="")
    attach.add_argument("--instances-json", default="")
    attach.add_argument("--instances-file", default="")
    attach.add_argument("--alpha-graph-json", default="")
    attach.add_argument("--alpha-graph-file", default="")
    attach.add_argument("--attachment", help="Full attachment JSON file.")

    detach = subparsers.add_parser("detach", help="Detach stages, market slots, or instances from the active pipeline.")
    detach.add_argument("--lane-id", default="")
    detach.add_argument("--instances-json", default="")
    detach.add_argument("--instances-file", default="")
    detach.add_argument("--stages-json", default="")
    detach.add_argument("--stages-file", default="")
    detach.add_argument("--market-json", default="")
    detach.add_argument("--market-file", default="")
    detach.add_argument("--request", help="Full detach request JSON file.")

    artifact = subparsers.add_parser("record-artifact", help="Persist a Data, Snapshot, Checkpoint, Result, Report, or Log artifact.")
    artifact.add_argument("--kind", required=True)
    artifact.add_argument("--artifact-id", required=True)
    artifact.add_argument("--strategy-id", default="")
    artifact.add_argument("--iteration-id", default="")
    artifact.add_argument("--module-id", default="")
    artifact.add_argument("--instance-id", default="")
    artifact.add_argument("--version", default="")
    artifact.add_argument("--metadata-json", default="")
    artifact.add_argument("--metadata-file", default="")
    artifact.add_argument("--payload-json", default="")
    artifact.add_argument("--payload-file", default="")
    artifact.add_argument("--file", action="append", default=[], help="local_path:artifact_path[:x]")
    artifact.add_argument("--remote-file", action="append", default=[], help="url,artifact_path[,sha256=<hex>][,x]")

    subparsers.add_parser("list-modules", help="List module definitions.")
    subparsers.add_parser("list-packages", help="List uploaded module packages.")
    subparsers.add_parser("list-instances", help="List module instances.")
    subparsers.add_parser("list-lanes", help="List active pipeline lanes.")
    subparsers.add_parser("list-artifacts", help="List persisted artifacts.")
    history = subparsers.add_parser("history", help="Show persisted control-plane history.")
    history.add_argument("--limit", type=int, default=100)
    current = subparsers.add_parser("current", help="Show current Engine manifest.")
    current.add_argument("--lane-id", default="")
    args = parser.parse_args()

    api = args.api.rstrip("/")

    if args.bundle:
        status, response = post_json(api + "/v1/strategies/submit", load_payload(args.bundle))
        print_response(status, response)
        return

    if args.command == "submit":
        payload = load_payload(args.bundle)
        if args.lane_id:
            payload["laneId"] = args.lane_id
        status, response = post_json(api + "/v1/strategies/submit", payload)
    elif args.command == "add-module":
        if args.definition:
            payload = load_payload(args.definition)
        else:
            require_args(args, ["kind", "module_id", "version", "activation_mode"], "add-module")
            parameters = load_json_value(args.parameters_json, args.parameters_file)
            if args.remote_url:
                parameters["baseUrl"] = args.remote_url
            if args.activation_mode == "RemoteService" and (
                args.remote_contract_hash or args.remote_deployment_id or args.remote_manifest_url
            ):
                backend = {
                    "kind": args.kind,
                    "moduleId": args.module_id,
                    "version": args.version,
                    "protocolVersion": args.remote_protocol_version,
                    "contractHash": args.remote_contract_hash,
                    "deploymentId": args.remote_deployment_id,
                }
                if args.remote_manifest_url:
                    backend["manifestUrl"] = args.remote_manifest_url
                parameters["backend"] = backend
            payload = {
                "kind": args.kind,
                "moduleId": args.module_id,
                "version": args.version,
                "activationMode": args.activation_mode,
                "entryPoint": args.entry_point,
                "hotSwapMode": args.hot_swap_mode,
                "parameters": parameters,
                "configSchema": load_json_value(args.config_schema_json, args.config_schema_file),
                "ports": load_json_value(args.ports_json, args.ports_file),
                "files": parse_file_specs(args.file, args.remote_file),
            }
        status, response = post_json(api + "/v1/modules", payload)
    elif args.command == "add-package":
        if args.definition:
            payload = load_payload(args.definition)
            extra_files = parse_file_specs(args.file, args.remote_file)
            if extra_files:
                payload["files"] = (payload.get("files") or []) + extra_files
            metadata = load_json_value(args.metadata_json, args.metadata_file)
            if metadata:
                payload["metadata"] = {**(payload.get("metadata") or {}), **metadata}
        else:
            require_args(args, ["package_id", "version"], "add-package")
            if not args.module:
                raise SystemExit("add-package requires --module unless a full package JSON file is provided.")
            payload = {
                "packageId": args.package_id,
                "version": args.version,
                "metadata": load_json_value(args.metadata_json, args.metadata_file),
                "modules": [load_payload(path) for path in args.module],
                "files": parse_file_specs(args.file, args.remote_file),
            }
        status, response = post_json(api + "/v1/module-packages", payload)
    elif args.command == "delete-module":
        path = f"/v1/modules/{args.kind}/{args.module_id}/versions/{args.version}"
        status, response = delete_json(api + path)
    elif args.command == "create-instance":
        if args.instance:
            payload = load_payload(args.instance)
        else:
            require_args(args, ["instance_id", "kind", "module_id", "version"], "create-instance")
            payload = {
                "instanceId": args.instance_id,
                "kind": args.kind,
                "moduleId": args.module_id,
                "version": args.version,
                "config": load_json_value(args.config_json, args.config_file),
                "parameters": load_json_value(args.parameters_json, args.parameters_file),
                "inputs": load_json_value(args.inputs_json, args.inputs_file),
                "outputs": load_json_value(args.outputs_json, args.outputs_file),
            }
        status, response = post_json(api + "/v1/pipeline/instances", payload)
    elif args.command == "attach":
        if args.attachment:
            payload = load_payload(args.attachment)
        else:
            require_args(args, ["strategy_id", "version"], "attach")
            payload = {
                "laneId": args.lane_id,
                "strategyId": args.strategy_id,
                "version": args.version,
                "name": args.name,
                "stages": load_json_value(args.stages_json, args.stages_file),
                "market": load_json_value(args.market_json, args.market_file),
                "marketRule": args.market_rule,
                "instances": load_json_value(args.instances_json, args.instances_file),
                "alphaGraph": load_json_value(args.alpha_graph_json, args.alpha_graph_file),
            }
        if args.lane_id:
            payload["laneId"] = args.lane_id
        status, response = post_json(api + "/v1/pipeline/attach", payload)
    elif args.command == "detach":
        payload = load_payload(args.request) if args.request else {
            "laneId": args.lane_id,
            "instances": load_json_value(args.instances_json, args.instances_file) or [],
            "stages": load_json_value(args.stages_json, args.stages_file) or [],
            "market": load_json_value(args.market_json, args.market_file) or [],
        }
        if args.lane_id:
            payload["laneId"] = args.lane_id
        status, response = post_json(api + "/v1/pipeline/detach", payload)
    elif args.command == "record-artifact":
        payload = {
            "kind": canonical_artifact_kind(args.kind),
            "artifactId": args.artifact_id,
            "strategyId": args.strategy_id,
            "iterationId": args.iteration_id,
            "moduleId": args.module_id,
            "instanceId": args.instance_id,
            "version": args.version,
            "metadata": load_json_value(args.metadata_json, args.metadata_file),
            "payload": load_json_value(args.payload_json, args.payload_file),
            "files": parse_file_specs(args.file, args.remote_file),
        }
        status, response = post_json(api + "/v1/artifacts", payload)
    elif args.command == "list-modules":
        status, response = get_json(api + "/v1/modules")
    elif args.command == "list-packages":
        status, response = get_json(api + "/v1/module-packages")
    elif args.command == "list-instances":
        status, response = get_json(api + "/v1/pipeline/instances")
    elif args.command == "list-lanes":
        status, response = get_json(api + "/v1/lanes")
    elif args.command == "list-artifacts":
        status, response = get_json(api + "/v1/artifacts")
    elif args.command == "history":
        status, response = get_json(api + f"/v1/history?limit={args.limit}")
    elif args.command == "current":
        suffix = f"?laneId={args.lane_id}" if args.lane_id else ""
        status, response = get_json(api + "/v1/strategies/current" + suffix)
    else:
        parser.print_help()
        sys.exit(2)

    print_response(status, response)


if __name__ == "__main__":
    main()
