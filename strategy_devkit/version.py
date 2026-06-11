#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def bump_project(root, version):
    root = Path(root).resolve()
    payload_dir = root / "payloads"
    changed = []

    attach_path = payload_dir / "attach.json"
    if attach_path.exists():
        payload = load_json(attach_path)
        payload["version"] = version
        write_json(attach_path, payload)
        changed.append(str(attach_path))

    package_path = payload_dir / "package.json"
    if package_path.exists():
        payload = load_json(package_path)
        payload["version"] = version
        for module in payload.get("modules") or []:
            if module.get("version") != "builtin":
                module["version"] = version
        write_json(package_path, payload)
        changed.append(str(package_path))

    for instance_path in sorted(payload_dir.glob("*.instance.json")):
        payload = load_json(instance_path)
        if payload.get("version") and payload.get("version") != "builtin":
            payload["version"] = version
            write_json(instance_path, payload)
            changed.append(str(instance_path))

    identity_path = root / "src" / "ModuleIdentity.cs"
    if identity_path.exists():
        text = identity_path.read_text(encoding="utf-8")
        updated = re.sub(
            r'public const string Version = "[^"]*";',
            f'public const string Version = "{version}";',
            text,
        )
        if updated != text:
            identity_path.write_text(updated, encoding="utf-8")
            changed.append(str(identity_path))

    return changed


def main():
    parser = argparse.ArgumentParser(description="Update scaffold payloads to a new strategy/module version.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bump = subparsers.add_parser("bump", help="Bump payload and ModuleIdentity versions.")
    bump.add_argument("--root", default=".")
    bump.add_argument("--version", required=True)
    args = parser.parse_args()

    if args.command == "bump":
        changed = bump_project(args.root, args.version)
        print(json.dumps({"version": args.version, "changed": changed}, indent=2))


if __name__ == "__main__":
    main()
