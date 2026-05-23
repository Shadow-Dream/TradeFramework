#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path("/file/share/data_jyz/trade")
LEAN = ROOT / "Lean"
PLUGIN = ROOT / "hotswap-modules"
DOTNET = Path("/root/.dotnet/dotnet")


def run(cmd, cwd):
    print(f"+ {' '.join(map(str, cmd))}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def find_single(pattern_root: Path, name: str) -> Path:
    matches = sorted(pattern_root.rglob(name))
    if not matches:
        raise FileNotFoundError(f"Unable to find {name} under {pattern_root}")
    return matches[-1]


def write_json_atomic(path: Path, payload: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def build_all():
    run([str(DOTNET), "build", "Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj", "-c", "Debug", "--nologo", "-m:1", "/p:BuildInParallel=false", "/p:RunAnalyzers=false", "/p:AnalysisMode=None"], LEAN)
    run([str(DOTNET), "build", "Engine/QuantConnect.Lean.Engine.csproj", "-c", "Debug", "--nologo", "-m:1", "/p:BuildInParallel=false", "/p:RunAnalyzers=false", "/p:AnalysisMode=None"], LEAN)
    run([str(DOTNET), "build", str(PLUGIN / "QuantConnect.HotSwap.Modules.csproj"), "-c", "Debug", "--nologo", "-m:1", "/p:BuildInParallel=false", "/p:RunAnalyzers=false", "/p:AnalysisMode=None"], ROOT)
    run([str(DOTNET), "build", "Launcher/QuantConnect.Lean.Launcher.csproj", "-c", "Debug", "--nologo", "--no-restore", "-m:1", "/p:BuildProjectReferences=false", "/p:BuildInParallel=false", "/p:RunAnalyzers=false", "/p:AnalysisMode=None"], LEAN)


def manifest(plugin_dll: Path, strategy: str) -> dict:
    prefix = f"Strategy{strategy}"
    return {
        "name": f"hotswap-{strategy.lower()}",
        "modules": [
            {
                "key": "data.main",
                "kind": "Data",
                "activationMode": "InProcessPlugin",
                "entryPoint": f"QuantConnect.HotSwap.Modules.{prefix}DataModule",
                "parameters": {"assemblyPath": str(plugin_dll)},
                "hotSwapMode": "Live",
            },
            {
                "key": "signal.main",
                "kind": "Signal",
                "activationMode": "InProcessPlugin",
                "entryPoint": f"QuantConnect.HotSwap.Modules.{prefix}AlphaModule",
                "parameters": {"assemblyPath": str(plugin_dll)},
                "hotSwapMode": "Live",
            },
            {
                "key": "target.main",
                "kind": "Target",
                "activationMode": "InProcessPlugin",
                "entryPoint": f"QuantConnect.HotSwap.Modules.{prefix}PortfolioModule",
                "parameters": {"assemblyPath": str(plugin_dll)},
                "hotSwapMode": "Live",
            },
            {
                "key": "risk.main",
                "kind": "Constraint",
                "activationMode": "InProcessPlugin",
                "entryPoint": f"QuantConnect.HotSwap.Modules.{prefix}RiskModule",
                "parameters": {"assemblyPath": str(plugin_dll)},
                "hotSwapMode": "Live",
            },
            {
                "key": "execution.main",
                "kind": "Execution",
                "activationMode": "InProcessPlugin",
                "entryPoint": f"QuantConnect.HotSwap.Modules.{prefix}ExecutionModule",
                "parameters": {"assemblyPath": str(plugin_dll)},
                "hotSwapMode": "RequiresPause",
            },
            {
                "key": "market.main",
                "kind": "MarketRule",
                "activationMode": "InProcessPlugin",
                "entryPoint": f"QuantConnect.HotSwap.Modules.{prefix}BrokerageModule",
                "parameters": {"assemblyPath": str(plugin_dll)},
                "hotSwapMode": "RequiresFlatNoOrders",
            },
        ],
        "data": ["data.main"],
        "signal": ["signal.main"],
        "target": ["target.main"],
        "constraint": ["risk.main"],
        "execution": ["execution.main"],
        "marketRule": "market.main",
    }


def config(algorithm_dll: Path, manifest_path: Path) -> dict:
    return {
        "environment": "backtesting",
        "algorithm-type-name": "PipelineHotSwapShellAlgorithm",
        "algorithm-language": "CSharp",
        "algorithm-location": str(algorithm_dll),
        "data-folder": str(LEAN / "Data"),
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.NoAuthApi",
        "setup-handler": "QuantConnect.Lean.Engine.Setup.ConsoleSetupHandler",
        "result-handler": "QuantConnect.Lean.Engine.Results.BacktestingResultHandler",
        "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
        "real-time-handler": "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler",
        "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
        "history-provider": "SubscriptionDataReaderHistoryProvider",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "pipeline-manifest": str(manifest_path),
        "pipeline-hot-reload-interval-ms": 100,
        "close-automatically": True,
        "show-missing-data-logs": False,
        "debugging": False,
    }


def main():
    build_all()

    algorithm_dll = find_single(LEAN / "Algorithm.CSharp" / "bin", "QuantConnect.Algorithm.CSharp.dll")
    launcher_dll = find_single(LEAN / "Launcher" / "bin", "QuantConnect.Lean.Launcher.dll")
    plugin_dll = find_single(PLUGIN / "bin", "QuantConnect.HotSwap.Modules.dll")

    temp_dir = Path(tempfile.mkdtemp(prefix="lean-hotswap-"))
    manifest_path = temp_dir / "pipeline.json"
    config_path = temp_dir / "config.json"
    output_path = temp_dir / "launcher.log"

    write_json_atomic(manifest_path, manifest(plugin_dll, "A"))
    write_json_atomic(config_path, config(algorithm_dll, manifest_path))

    flags = {
        "swapped_to_b": False,
        "swapped_to_a": False,
        "reload_count": 0,
    }
    markers = {
        "DATA_A": False,
        "ALPHA_A": False,
        "PORT_A": False,
        "RISK_A": False,
        "EXEC_A": False,
        "BROKER_A": False,
        "DATA_B": False,
        "ALPHA_B": False,
        "PORT_B": False,
        "RISK_B": False,
        "EXEC_B": False,
        "BROKER_B": False,
    }
    lock = threading.Lock()

    def swap_later(target: str, delay: float):
        time.sleep(delay)
        write_json_atomic(manifest_path, manifest(plugin_dll, target))
        print(f"### swapped manifest to strategy {target}", flush=True)

    process = subprocess.Popen(
        [str(DOTNET), str(launcher_dll), "--config", str(config_path)],
        cwd=LEAN,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        with output_path.open("w", encoding="utf-8") as output:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\n")
                output.write(line + "\n")
                output.flush()
                print(line, flush=True)

                for marker in list(markers.keys()):
                    if marker in line:
                        markers[marker] = True

                if "PipelineHotReloadService: hot reloaded pipeline" in line:
                    flags["reload_count"] += 1

                with lock:
                    if not flags["swapped_to_b"] and "SHELL:2014-06-10" in line:
                        flags["swapped_to_b"] = True
                        threading.Thread(target=swap_later, args=("B", 0.8), daemon=True).start()
                    elif flags["swapped_to_b"] and not flags["swapped_to_a"] and "SHELL:2014-06-17" in line:
                        flags["swapped_to_a"] = True
                        threading.Thread(target=swap_later, args=("A", 0.8), daemon=True).start()

        return_code = process.wait(timeout=120)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    if return_code != 0:
        print(f"Launcher exited with code {return_code}. Full log: {output_path}", file=sys.stderr)
        sys.exit(return_code)

    if flags["reload_count"] < 2:
        print(f"Expected at least 2 hot reloads, saw {flags['reload_count']}. Full log: {output_path}", file=sys.stderr)
        sys.exit(2)

    missing = [name for name, present in markers.items() if not present]
    if missing:
        print(f"Missing expected markers: {', '.join(missing)}. Full log: {output_path}", file=sys.stderr)
        sys.exit(3)

    print(f"HOTSWAP_SMOKE_OK log={output_path}", flush=True)


if __name__ == "__main__":
    main()
