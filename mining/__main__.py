from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import MiningApi
from .config import load_config
from .providers.base import strict_json_loads
from .providers import get_provider
from .store import MiningStore
from .worker import MiningWorker, WorkerAlreadyRunning


def load_create_payload(path: str | Path):
    return strict_json_loads(
        Path(path).read_bytes(), label="Mining create payload"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Trade Engine provider-native mining worker.")
    parser.add_argument("--config", required=True, help="Engine JSON config containing miningRoot.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("worker")
    subparsers.add_parser("run-once")
    subparsers.add_parser("list")
    subparsers.add_parser("health")
    subparsers.add_parser("integrity")
    export = subparsers.add_parser("export")
    export.add_argument("job_id")
    export.add_argument("target")
    create = subparsers.add_parser("create")
    create.add_argument("payload", help="Path to strict mining job JSON payload.")
    args = parser.parse_args()

    config = load_config(args.config)
    store = MiningStore(config)
    if args.command == "worker":
        try:
            MiningWorker(config, store=store).run()
        except WorkerAlreadyRunning:
            raise SystemExit(73)
        return
    if args.command == "run-once":
        count = MiningWorker(config, store=store).run(once=True)
        print(json.dumps({"processed": count}, indent=2))
        return
    if args.command == "list":
        print(json.dumps({"jobs": store.list_jobs()}, indent=2))
        return
    if args.command == "health":
        print(json.dumps(store.health(), indent=2))
        return
    if args.command == "integrity":
        print(json.dumps(store.run_integrity_check(), indent=2))
        return
    if args.command == "export":
        print(json.dumps(store.export_records(args.job_id, args.target), indent=2))
        return
    if args.command == "create":
        payload = load_create_payload(args.payload)
        status, result = MiningApi(config).handle_post("/api/mining/jobs", payload)
        print(json.dumps({"status": status, **result}, indent=2))


if __name__ == "__main__":
    main()
