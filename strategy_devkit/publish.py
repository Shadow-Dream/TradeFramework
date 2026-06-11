#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from .bundle import encode_file
from .schema import generate_project_schemas


def load_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_response(payload):
    print(json.dumps(payload, indent=2))


def post_json(api, path, payload):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get_json(api, path):
    try:
        with urllib.request.urlopen(api.rstrip("/") + path, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    except urllib.error.URLError:
        return None, None


def assert_accepted(step, status, response):
    if status < 200 or status >= 300 or response.get("accepted") is False:
        hint = ""
        error = str((response or {}).get("error", ""))
        if "already exists" in error:
            hint = (
                "\nVersioning hint: this version has already been persisted. "
                "Bump payloads/attach.json version, payloads/package.json version, and custom instance module versions."
            )
        raise SystemExit(f"{step} failed ({status}): {json.dumps(response, indent=2)}{hint}")


def run_command(command, cwd):
    subprocess.run(command, cwd=cwd, check=True)


def project_file(root):
    matches = sorted(root.glob("*.Modules.csproj"))
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one *.Modules.csproj under {root}, found {len(matches)}.")
    return matches[0]


def project_metadata(path):
    tree = ET.parse(path)
    root = tree.getroot()
    assembly_name = None
    root_namespace = None
    for property_group in root.findall("PropertyGroup"):
        assembly = property_group.find("AssemblyName")
        namespace = property_group.find("RootNamespace")
        if assembly is not None and assembly.text:
            assembly_name = assembly.text.strip()
        if namespace is not None and namespace.text:
            root_namespace = namespace.text.strip()
    assembly_name = assembly_name or path.stem
    root_namespace = root_namespace or assembly_name
    project_name = assembly_name[:-len(".Modules")] if assembly_name.endswith(".Modules") else assembly_name
    return project_name, assembly_name, root_namespace


def find_dll(root, configuration, assembly_name):
    expected = root / "bin" / configuration / "net10.0" / f"{assembly_name}.dll"
    if expected.exists():
        return expected
    matches = sorted((root / "bin" / configuration).glob(f"**/{assembly_name}.dll"))
    if not matches:
        raise SystemExit(f"Build output not found for {assembly_name}. Run build first or check the project file.")
    return matches[0]


def ordered_instance_files(payload_dir):
    preferred = [
        "input.instance.json",
        "signal.instance.json",
        "target.instance.json",
        "universe.instance.json",
        "risk.instance.json",
        "execution.instance.json",
        "market.instance.json",
    ]
    files = []
    seen = set()
    for name in preferred:
        path = payload_dir / name
        if path.exists():
            files.append(path)
            seen.add(path)
    for path in sorted(payload_dir.glob("*.instance.json")):
        if path not in seen:
            files.append(path)
    return files


def synthesize_package(root, project_name, assembly_name, namespace, strategy_id, version):
    payload_dir = root / "payloads"
    modules = []
    for kind, suffix, class_suffix, schema_name in [
        ("Signal", "signal", "SignalModule", "signal.schema.json"),
        ("Target", "target", "TargetModule", ""),
    ]:
        instance_path = payload_dir / f"{suffix}.instance.json"
        if not instance_path.exists():
            continue
        instance = load_json(instance_path)
        if instance.get("version") == "builtin":
            continue
        schema = {}
        if schema_name and (payload_dir / schema_name).exists():
            schema = load_json(payload_dir / schema_name)
        modules.append({
            "kind": kind,
            "moduleId": instance["moduleId"],
            "version": instance.get("version") or version,
            "activationMode": "InProcessPlugin",
            "entryPoint": f"{namespace}.{project_name}{class_suffix}",
            "hotSwapMode": "Live",
            "parameters": {
                "assemblyPath": f"{{{{packageRoot}}}}/artifacts/{assembly_name}.dll"
            },
            "configSchema": schema,
            "description": f"{strategy_id} {kind} module.",
        })
    if not modules:
        raise SystemExit("No custom module instances found. Add payloads/package.json or scaffold Signal/Target instances.")
    return {
        "packageId": strategy_id,
        "version": version,
        "metadata": {"source": "strategy_devkit.publish"},
        "modules": modules,
    }


def load_package_definition(root, project_name, assembly_name, namespace, strategy_id, version):
    package_path = root / "payloads" / "package.json"
    if package_path.exists():
        payload = load_json(package_path)
    else:
        payload = synthesize_package(root, project_name, assembly_name, namespace, strategy_id, version)
    payload.setdefault("packageId", strategy_id)
    payload.setdefault("version", version)
    payload.setdefault("metadata", {})
    payload.setdefault("modules", [])
    return payload


def source_text(root):
    chunks = []
    for path in sorted((root / "src").glob("*.cs")):
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def simple_class_name(entry_point):
    return str(entry_point or "").split(".")[-1]


def active_instance_ids(attachment):
    result = []
    stages = attachment.get("stages") or {}
    for values in stages.values():
        if isinstance(values, list):
            result.extend(values)
    market = attachment.get("market") or {}
    for value in market.values():
        if isinstance(value, str) and value:
            result.append(value)
        elif isinstance(value, list):
            result.extend(item for item in value if isinstance(item, str) and item)
    if attachment.get("marketRule"):
        result.append(attachment["marketRule"])
    for node in (attachment.get("alphaGraph") or {}).get("nodes") or []:
        result.append(node)
    return result


def validate_project_payloads(root, package, attachment):
    errors = []
    warnings = []
    payload_dir = root / "payloads"
    source = source_text(root)
    package_id = package.get("packageId")
    package_version = package.get("version")

    if package_id != attachment.get("strategyId"):
        errors.append("payloads/package.json packageId must match payloads/attach.json strategyId.")
    if package_version != attachment.get("version"):
        errors.append("payloads/package.json version must match payloads/attach.json version.")

    modules = package.get("modules") or []
    module_keys = {(m.get("kind"), m.get("moduleId"), m.get("version") or package_version): m for m in modules}
    for module in modules:
        entry_point = module.get("entryPoint", "")
        class_name = simple_class_name(entry_point)
        if class_name and f"class {class_name}" not in source:
            errors.append(f"Package module entryPoint '{entry_point}' does not match any class in src/*.cs.")
        if module.get("activationMode") == "InProcessPlugin":
            assembly_path = (module.get("parameters") or {}).get("assemblyPath", "")
            if "{{packageRoot}}" not in assembly_path:
                warnings.append(f"Module {module.get('moduleId')} assemblyPath should use {{{{packageRoot}}}} for package-level upload.")

    instance_by_id = {}
    for instance_path in ordered_instance_files(payload_dir):
        instance = load_json(instance_path)
        instance_id = instance.get("instanceId")
        if not instance_id:
            errors.append(f"{instance_path.name} is missing instanceId.")
            continue
        if instance_id in instance_by_id:
            errors.append(f"Duplicate instanceId '{instance_id}' in payloads.")
        instance_by_id[instance_id] = instance
        if instance.get("version") != "builtin":
            key = (instance.get("kind"), instance.get("moduleId"), instance.get("version"))
            if key not in module_keys:
                errors.append(
                    f"{instance_path.name} references custom module {key}, "
                    "but payloads/package.json does not define that module."
                )

    for instance_id in active_instance_ids(attachment):
        if instance_id not in instance_by_id:
            errors.append(f"payloads/attach.json references missing instance '{instance_id}'.")

    if errors:
        raise SystemExit("Local payload validation failed:\n- " + "\n- ".join(errors))
    return warnings


def preflight_version(api, strategy_id, version, package):
    package_key = f"{package.get('packageId')}/{package.get('version')}"
    status, response = get_json(api, "/v1/module-packages")
    if status and status < 400 and package_key in (response.get("packages") or {}):
        raise SystemExit(
            f"Package version already exists: {package_key}.\n"
            "Bump payloads/attach.json version, payloads/package.json version, and custom instance module versions before publishing."
        )
    status, response = get_json(api, "/v1/artifacts")
    artifact_key = f"Snapshot/{strategy_id}-module-source-{version}"
    if status and status < 400 and artifact_key in (response.get("artifacts") or {}):
        raise SystemExit(
            f"Source artifact already exists: {artifact_key}.\n"
            "Bump the strategy version before publishing another reproducible snapshot."
        )


def source_artifact_files(root):
    patterns = [
        "*.Modules.csproj",
        "README.md",
        "src/*.cs",
        "tests/*.cs",
        "tests/*.csproj",
        "tests/fixtures/*.json",
        "payloads/*.json",
        "logs/*.md",
        "logs/*.json",
        "logs/*.txt",
    ]
    files = []
    for pattern in patterns:
        files.extend(path for path in sorted(root.glob(pattern)) if path.is_file())
    return files


def main():
    parser = argparse.ArgumentParser(description="Build, test, publish, attach, and record a scaffolded strategy module project.")
    parser.add_argument("--root", default=".", help="Scaffold project root.")
    parser.add_argument("--api", default="http://127.0.0.1:8777", help="Strategy control API base URL.")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--dotnet", default="/root/.dotnet/dotnet")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--no-test", action="store_true")
    parser.add_argument("--no-artifact", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    payload_dir = root / "payloads"
    attachment_path = payload_dir / "attach.json"
    if not attachment_path.exists():
        raise SystemExit(f"Missing attachment payload: {attachment_path}")
    attachment = load_json(attachment_path)
    strategy_id = attachment.get("strategyId")
    version = attachment.get("version")
    if not strategy_id or not version:
        raise SystemExit("payloads/attach.json requires strategyId and version.")

    project = project_file(root)
    project_name, assembly_name, namespace = project_metadata(project)
    generate_project_schemas(root, write=True)
    package = load_package_definition(root, project_name, assembly_name, namespace, strategy_id, version)
    warnings = validate_project_payloads(root, package, attachment)
    for warning in warnings:
        print(f"publish warning: {warning}", file=sys.stderr)
    preflight_version(args.api, strategy_id, version, package)
    if not args.no_build:
        run_command([args.dotnet, "build", str(project), "-c", args.configuration, "--nologo"], root)
    if not args.no_test:
        test_projects = sorted((root / "tests").glob("*.csproj"))
        for test_project in test_projects:
            run_command([
                args.dotnet,
                "test",
                str(test_project),
                "-c",
                args.configuration,
                "--nologo",
                "--logger",
                "console;verbosity=minimal",
            ], root)

    dll = find_dll(root, args.configuration, assembly_name)
    package["files"] = [encode_file(dll, f"artifacts/{dll.name}")]
    status, response = post_json(args.api, "/v1/module-packages", package)
    assert_accepted("add-package", status, response)
    package_response = response

    inline_instances = {}
    for instance_path in ordered_instance_files(payload_dir):
        instance = load_json(instance_path)
        inline_instances[instance["instanceId"]] = instance

    attachment = dict(attachment)
    attachment["instances"] = inline_instances
    status, attach_response = post_json(args.api, "/v1/pipeline/attach", attachment)
    assert_accepted("attach", status, attach_response)

    artifact_response = None
    if not args.no_artifact:
        iteration_id = (attach_response.get("iteration") or {}).get("iterationId", "")
        artifact_id = f"{strategy_id}-module-source-{version}"
        files = [
            encode_file(path, str(path.relative_to(root)))
            for path in source_artifact_files(root)
        ]
        artifact = {
            "kind": "Snapshot",
            "artifactId": artifact_id,
            "strategyId": strategy_id,
            "iterationId": iteration_id,
            "version": version,
            "metadata": {
                "description": "Source, payload, and tests recorded by strategy_devkit.publish.",
            },
            "files": files,
        }
        status, artifact_response = post_json(args.api, "/v1/artifacts", artifact)
        assert_accepted("record-artifact", status, artifact_response)

    write_json_response({
        "accepted": True,
        "strategyId": strategy_id,
        "version": version,
        "package": {
            "packageKey": package_response.get("packageKey"),
            "moduleCount": len(package_response.get("modules") or []),
            "fileCount": len(package_response.get("files") or []),
        },
        "instances": sorted(inline_instances),
        "iteration": attach_response.get("iteration"),
        "artifact": artifact_response.get("artifactKey") if artifact_response else None,
    })


if __name__ == "__main__":
    main()
