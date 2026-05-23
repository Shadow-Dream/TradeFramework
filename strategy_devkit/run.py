#!/usr/bin/env python3
import argparse
import importlib.util
import sys
from pathlib import Path

from .sdk import serve_http, serve_json_lines


def load_strategy(path):
    source = Path(path).resolve()
    sys.path.insert(0, str(source.parent))
    sys.path.insert(0, str(Path.cwd()))
    spec = importlib.util.spec_from_file_location("dev_strategy", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Strategy()


def main():
    parser = argparse.ArgumentParser(description="Run a strategy.py through the dev kit runtime.")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--mode", choices=["worker", "http"], default="worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    strategy = load_strategy(args.strategy)
    if args.mode == "http":
        serve_http(strategy, args.host, args.port)
    else:
        serve_json_lines(strategy)


if __name__ == "__main__":
    main()
