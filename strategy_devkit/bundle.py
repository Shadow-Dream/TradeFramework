#!/usr/bin/env python3
import argparse
import base64
import json
from pathlib import Path


def encode_file(path, bundle_path=None, executable=False):
    source = Path(path)
    return {
        "path": bundle_path or source.name,
        "contentBase64": base64.b64encode(source.read_bytes()).decode("ascii"),
        "executable": executable,
    }


def remote_file(source_url, bundle_path, sha256=None, executable=False):
    item = {
        "path": bundle_path,
        "sourceUrl": source_url,
        "executable": executable,
    }
    if sha256:
        item["sha256"] = sha256
    return item


def write_bundle(path, strategy_id, version, manifest, files):
    payload = {
        "strategyId": strategy_id,
        "version": version,
        "manifest": manifest,
        "files": files,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description="Create a strategy submission bundle.")
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--file", action="append", default=[], help="local_path:bundle_path[:x]")
    parser.add_argument("--remote-file", action="append", default=[], help="url,bundle_path[,sha256=<hex>][,x]")
    args = parser.parse_args()

    with open(args.manifest, encoding="utf-8") as handle:
        manifest = json.load(handle)

    files = []
    for item in args.file:
        parts = item.split(":")
        if len(parts) < 2:
            raise SystemExit("--file must be local_path:bundle_path[:x]")
        files.append(encode_file(parts[0], parts[1], len(parts) > 2 and parts[2] == "x"))

    for item in args.remote_file:
        parts = item.split(",")
        if len(parts) < 2:
            raise SystemExit("--remote-file must be url,bundle_path[,sha256=<hex>][,x]")
        flags = parts[2:]
        checksum = next((flag.removeprefix("sha256=") for flag in flags if flag.startswith("sha256=")), None)
        files.append(remote_file(parts[0], parts[1], checksum, "x" in flags))

    write_bundle(args.out, args.strategy_id, args.version, manifest, files)


if __name__ == "__main__":
    main()
