#!/usr/bin/env python3
import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from .bundle import encode_file, write_bundle


def load_strategy(path):
    source = Path(path).resolve()
    sys.path.insert(0, str(source.parent))
    sys.path.insert(0, str(Path.cwd()))
    spec = importlib.util.spec_from_file_location("submitted_strategy", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Strategy()


def cs_string(value):
    return json.dumps(value)


def cs_symbol(symbol):
    return (
        f'Symbol.Create({cs_string(symbol["value"])}, '
        f'SecurityType.{symbol.get("securityType", "Equity")}, '
        f'{cs_string(symbol.get("market", "usa"))})'
    )


def generate_plugin_source(inputs, universe_symbols):
    input_lines = []
    for item in inputs:
        resolution = item.get("resolution")
        resolution_expr = f"Resolution.{resolution}" if resolution else "null"
        input_lines.append(
            f"yield return new InputRegistration({cs_symbol(item['symbol'])}, {resolution_expr});"
        )
    if not input_lines:
        input_lines.append("yield break;")

    universe_list = ", ".join(cs_symbol(item) for item in universe_symbols)

    return textwrap.dedent(
        f"""
        using System.Collections.Generic;
        using System.Threading;
        using System.Threading.Tasks;
        using QuantConnect;
        using QuantConnect.Algorithm;
        using QuantConnect.Algorithm.Framework.Selection;
        using QuantConnect.Algorithm.Modules;
        using QuantConnect.Data.UniverseSelection;
        using QuantConnect.Logging;
        using QuantConnect.Modules;

        namespace StrategyDevKit.Generated;

        public sealed class GeneratedInputModule : IInputModule
        {{
            private readonly ModuleState _state = new(typeof(GeneratedInputModule), ModuleKind.Input, ModuleHotSwapMode.Live);

            public string Key => _state.Key;
            public ModuleKind Kind => _state.Kind;
            public ModuleActivationMode ActivationMode => _state.ActivationMode;
            public string Version => _state.Version;
            public ModuleHotSwapMode HotSwapMode => _state.HotSwapMode;

            public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default) => _state.Initialize(configuration, cancellationToken);
            public ValueTask Pause(CancellationToken cancellationToken = default) => _state.Pause(cancellationToken);
            public ValueTask Resume(CancellationToken cancellationToken = default) => _state.Resume(cancellationToken);
            public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default) => _state.CreateSnapshot(cancellationToken);
            public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default) => _state.RestoreSnapshot(snapshot, cancellationToken);
            public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default) => _state.CheckHealth(cancellationToken);

            public IEnumerable<InputRegistration> CreateInputs(QCAlgorithm algorithm)
            {{
                Log.Trace($"STRATEGY_DEVKIT_INPUT:{{algorithm.Time:yyyy-MM-dd}}");
                {chr(10).join(input_lines)}
            }}
        }}

        public sealed class GeneratedUniverseModule : ManualUniverseSelectionModel
        {{
            public GeneratedUniverseModule()
                : base(new Symbol[] {{ {universe_list} }})
            {{
            }}

            public override IEnumerable<Universe> CreateUniverses(QCAlgorithm algorithm)
            {{
                Log.Trace($"STRATEGY_DEVKIT_UNIVERSE:{{algorithm.Time:yyyy-MM-dd}}");
                return base.CreateUniverses(algorithm);
            }}
        }}
        """
    ).strip() + "\n"


def build_plugin(work_dir, lean_root, inputs, universe_symbols):
    plugin_dir = work_dir / "generated-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    source_path = plugin_dir / "GeneratedStrategyModules.cs"
    source_path.write_text(generate_plugin_source(inputs, universe_symbols), encoding="utf-8")
    (plugin_dir / "GeneratedStrategyModules.csproj").write_text(
        textwrap.dedent(
            f"""
            <Project Sdk="Microsoft.NET.Sdk">
              <PropertyGroup>
                <TargetFramework>net10.0</TargetFramework>
                <Nullable>enable</Nullable>
                <ImplicitUsings>enable</ImplicitUsings>
                <AssemblyName>GeneratedStrategyModules</AssemblyName>
                <RootNamespace>StrategyDevKit.Generated</RootNamespace>
              </PropertyGroup>
              <ItemGroup>
                <ProjectReference Include="{lean_root / 'Common' / 'QuantConnect.csproj'}" />
                <ProjectReference Include="{lean_root / 'Algorithm' / 'QuantConnect.Algorithm.csproj'}" />
              </ItemGroup>
            </Project>
            """
        ).strip() + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "/root/.dotnet/dotnet",
            "build",
            str(plugin_dir / "GeneratedStrategyModules.csproj"),
            "-c",
            "Debug",
            "--nologo",
            "-m:1",
            "/p:BuildInParallel=false",
            "/p:RunAnalyzers=false",
            "/p:AnalysisMode=None",
        ],
        check=True,
    )
    return plugin_dir / "bin" / "Debug" / "net10.0" / "GeneratedStrategyModules.dll"


def manifest(strategy_id, version, remote_url):
    return {
        "name": f"{strategy_id}-{version}",
        "modules": [
            {
                "key": "input.main",
                "kind": "Input",
                "activationMode": "InProcessPlugin",
                "entryPoint": "StrategyDevKit.Generated.GeneratedInputModule",
                "version": version,
                "parameters": {"assemblyPath": "{{releaseRoot}}/artifacts/GeneratedStrategyModules.dll"},
                "hotSwapMode": "Live",
            },
            {
                "key": "universe.main",
                "kind": "Universe",
                "activationMode": "InProcessPlugin",
                "entryPoint": "StrategyDevKit.Generated.GeneratedUniverseModule",
                "version": version,
                "parameters": {"assemblyPath": "{{releaseRoot}}/artifacts/GeneratedStrategyModules.dll"},
                "hotSwapMode": "Live",
            },
            {
                "key": "signal.main",
                "kind": "Signal",
                "activationMode": "RemoteService",
                "entryPoint": "strategy-devkit.signal",
                "version": version,
                "parameters": {"baseUrl": remote_url},
                "hotSwapMode": "Live",
            },
            {
                "key": "target.main",
                "kind": "Target",
                "activationMode": "ScriptRunner",
                "entryPoint": "strategy-devkit.target",
                "version": version,
                "parameters": {
                    "command": "python3",
                    "arguments": "{{releaseRoot}}/strategy.py worker",
                    "workingDirectory": "{{releaseRoot}}",
                },
                "hotSwapMode": "Live",
            },
            {
                "key": "constraint.main",
                "kind": "Constraint",
                "activationMode": "RemoteService",
                "entryPoint": "strategy-devkit.constraint",
                "version": version,
                "parameters": {"baseUrl": remote_url},
                "hotSwapMode": "RequiresPause",
            },
            {
                "key": "execution.main",
                "kind": "Execution",
                "activationMode": "OutOfProcessWorker",
                "entryPoint": "strategy-devkit.execution",
                "version": version,
                "parameters": {
                    "command": "python3",
                    "arguments": "{{releaseRoot}}/strategy.py worker",
                    "workingDirectory": "{{releaseRoot}}",
                },
                "hotSwapMode": "RequiresPause",
            },
            {
                "key": "market.main",
                "kind": "MarketRule",
                "activationMode": "RemoteService",
                "entryPoint": "strategy-devkit.market",
                "version": version,
                "parameters": {"baseUrl": remote_url},
                "hotSwapMode": "RequiresFlatNoOrders",
            },
            {
                "key": "analyzer.main",
                "kind": "Analyzer",
                "activationMode": "ScriptRunner",
                "entryPoint": "strategy-devkit.analyzer",
                "version": version,
                "parameters": {
                    "command": "python3",
                    "arguments": "{{releaseRoot}}/strategy.py worker",
                    "workingDirectory": "{{releaseRoot}}",
                },
                "hotSwapMode": "RequiresPause",
            },
        ],
        "inputs": ["input.main"],
        "universe": ["universe.main"],
        "signal": ["signal.main"],
        "target": ["target.main"],
        "constraint": ["constraint.main"],
        "execution": ["execution.main"],
        "marketRule": "market.main",
        "analyzer": ["analyzer.main"],
    }


def main():
    parser = argparse.ArgumentParser(description="Build a clean strategy.py into a submission bundle.")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--remote-url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--lean-root", default="/file/share/data_jyz/trade/Lean")
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    lean_root = Path(args.lean_root).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    strategy = load_strategy(args.strategy)
    context = {"time": None, "raw": {}}
    inputs = strategy.inputs(context)
    universe_symbols = strategy.universe(context)
    plugin_dll = build_plugin(work_dir, lean_root, inputs, universe_symbols)

    sdk_dir = Path(__file__).resolve().parent
    files = [
        encode_file(args.strategy, "strategy.py", True),
        encode_file(sdk_dir / "__init__.py", "strategy_devkit/__init__.py"),
        encode_file(sdk_dir / "sdk.py", "strategy_devkit/sdk.py"),
        encode_file(plugin_dll, "artifacts/GeneratedStrategyModules.dll"),
    ]
    write_bundle(
        args.out,
        args.strategy_id,
        args.version,
        manifest(args.strategy_id, args.version, args.remote_url),
        files,
    )
    print(args.out)


if __name__ == "__main__":
    main()
