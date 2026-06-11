#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def slugify(value):
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return text or "strategy"


def pascal_case(value):
    parts = re.split(r"[^a-zA-Z0-9]+", value)
    result = "".join(part[:1].upper() + part[1:] for part in parts if part)
    return result or "Strategy"


def write_text(path, content, force=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise SystemExit(f"Refusing to overwrite existing file: {path}")
    path.write_text(content, encoding="utf-8")


def write_json(path, payload, force=False):
    write_text(path, json.dumps(payload, indent=2) + "\n", force)


def quantconnect_references(sdk_bin):
    names = [
        "QuantConnect.Common",
        "QuantConnect.Algorithm",
        "QuantConnect.Logging",
        "QuantConnect.Configuration",
        "QuantConnect.Compression",
        "QuantConnect.Indicators",
        "NodaTime",
        "Python.Runtime",
    ]
    return "\n".join(
        f'    <Reference Include="{name}">\n'
        f'      <HintPath>{sdk_bin}/{name}.dll</HintPath>\n'
        f"      <Private>true</Private>\n"
        f"    </Reference>"
        for name in names
    )


def csproj(project_name, lean_root, sdk_bin):
    return f"""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <AssemblyName>{project_name}.Modules</AssemblyName>
    <RootNamespace>{project_name}.Modules</RootNamespace>
    <RestoreConfigFile>{lean_root}/.nuget/NuGet.config</RestoreConfigFile>
  </PropertyGroup>
  <ItemGroup>
    <Compile Remove="tests/**/*.cs" />
  </ItemGroup>
  <ItemGroup>
{quantconnect_references(sdk_bin)}
  </ItemGroup>
</Project>
"""


def test_csproj(project_name, lean_root, sdk_bin):
    return f"""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <IsPackable>false</IsPackable>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <RestoreConfigFile>{lean_root}/.nuget/NuGet.config</RestoreConfigFile>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="16.9.4" />
    <PackageReference Include="Newtonsoft.Json" Version="13.0.2" />
    <PackageReference Include="NUnit" Version="4.2.2" />
    <PackageReference Include="NUnit3TestAdapter" Version="4.6.0">
      <PrivateAssets>all</PrivateAssets>
    </PackageReference>
  </ItemGroup>
  <ItemGroup>
{quantconnect_references(sdk_bin)}
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="../{project_name}.Modules.csproj" />
  </ItemGroup>
  <ItemGroup>
    <None Include="fixtures/**/*.json" Link="fixtures/%(RecursiveDir)%(Filename)%(Extension)" CopyToOutputDirectory="PreserveNewest" />
    <None Include="{lean_root}/Data/market-hours/**/*" Link="Data/market-hours/%(RecursiveDir)%(Filename)%(Extension)" CopyToOutputDirectory="PreserveNewest" />
    <None Include="{lean_root}/Data/symbol-properties/**/*" Link="Data/symbol-properties/%(RecursiveDir)%(Filename)%(Extension)" CopyToOutputDirectory="PreserveNewest" />
  </ItemGroup>
</Project>
"""


def signal_source(project_name):
    return f"""using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Modules;
using StrategyDevKit.Modules;

namespace {project_name}.Modules;

public sealed class {project_name}SignalConfig
{{
    [ConfigField("Insight holding period in calendar days.", Minimum = 1, Maximum = 365, Group = "Signal")]
    public int InsightDays {{ get; set; }} = 35;

    [ConfigField("Default signal weight used by the starter template.", Minimum = -1, Maximum = 1, Group = "Signal")]
    public double DefaultWeight {{ get; set; }} = 1.0;
}}

public sealed class {project_name}SignalModule : AlphaModel
{{
    private {project_name}SignalConfig _config = new();

    public {project_name}SignalModule()
    {{
        Name = ModuleIdentity.SignalSourceModel;
    }}

    public override ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
    {{
        var result = base.Initialize(configuration, cancellationToken);
        _config = ModuleConfig.Read<{project_name}SignalConfig>(configuration);
        return result;
    }}

    public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
    {{
        // Implement signal logic here. Keep input registration in Input, sizing in Target,
        // risk changes in Constraint, and order mechanics in Execution.
        yield break;
    }}
}}
"""


def identity_source(project_name, strategy_id, version):
    return f"""namespace {project_name}.Modules;

internal static class ModuleIdentity
{{
    public const string StrategyId = "{strategy_id}";
    public const string Version = "{version}";
    public const string SignalModuleId = "{strategy_id}-signal";
    public const string TargetModuleId = "{strategy_id}-target";
    public const string SignalSourceModel = "{project_name}Signal";
}}
"""


def config_helper_source():
    return """using System;
using System.Text.Json;
using QuantConnect.Modules;

namespace StrategyDevKit.Modules;

[AttributeUsage(AttributeTargets.Property)]
internal sealed class ConfigFieldAttribute : Attribute
{
    public ConfigFieldAttribute(string description = "")
    {
        Description = description;
    }

    public string Description { get; }
    public double Minimum { get; set; } = double.NaN;
    public double Maximum { get; set; } = double.NaN;
    public string[] Options { get; set; } = Array.Empty<string>();
    public string Group { get; set; } = "";
    public string Unit { get; set; } = "";
}

internal static class ModuleConfig
{
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public static T Read<T>(ModuleConfiguration configuration) where T : new()
    {
        if (!configuration.Parameters.TryGetValue("config", out var raw) || string.IsNullOrWhiteSpace(raw))
        {
            return new T();
        }

        return JsonSerializer.Deserialize<T>(raw, Options) ?? new T();
    }
}
"""


def test_source(project_name):
    return f"""using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using NUnit.Framework;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Modules;
using StrategyDevKit.Tests;

namespace {project_name}.Modules.Tests;

[TestFixture]
public sealed class ModuleSmokeTests
{{
    [Test]
    public async Task SignalModuleInitializesAndUpdateCanRun()
    {{
        var module = new {project_name}.Modules.{project_name}SignalModule();
        var configuration = new ModuleConfiguration(
            "signal.test",
            ModuleKind.Signal,
            ModuleActivationMode.InProcessPlugin,
            typeof({project_name}.Modules.{project_name}SignalModule).FullName!,
            parameters: new Dictionary<string, string>
            {{
                ["config"] = "{{\\"insightDays\\":10,\\"defaultWeight\\":0.5}}"
            }});

        await module.Initialize(configuration);

        var insights = module.Update(null!, null!).ToArray();
        Assert.That(insights, Is.Empty, "The scaffold starts with no signal logic; add assertions as strategy logic is implemented.");
    }}

    [Test]
    public void TargetModuleCanRunWithNoInsights()
    {{
        var module = new {project_name}.Modules.{project_name}TargetModule();
        var targets = module.CreateTargets(null!, System.Array.Empty<Insight>()).ToArray();

        Assert.That(targets, Is.Empty);
    }}

    [Test]
    public async Task ReplayFixtureCanDriveSignalAndTarget()
    {{
        var fixture = ReplayFixture.Load("fixtures/replay.json");
        var result = await ModuleReplayHarness.Run(
            new {project_name}.Modules.{project_name}SignalModule(),
            new {project_name}.Modules.{project_name}TargetModule(),
            fixture);

        Assert.That(result.Insights.Count, Is.GreaterThanOrEqualTo(fixture.Expected.MinInsights ?? 0));
        if (fixture.Expected.TargetCount is not null)
        {{
            Assert.That(result.Targets.Count, Is.EqualTo(fixture.Expected.TargetCount.Value));
        }}
    }}
}}
"""


def replay_harness_source():
    return """using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using NUnit.Framework;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Configuration;
using QuantConnect.Data.Auxiliary;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Interfaces;
using QuantConnect.Securities;
using QuantConnect.Util;

namespace StrategyDevKit.Tests;

public sealed class ReplayFixture
{
    public JsonElement Config { get; set; }
    public List<string> Symbols { get; set; } = new();
    public List<ReplayBar> Bars { get; set; } = new();
    public ExpectedReplayResult Expected { get; set; } = new();

    public static ReplayFixture Load(string relativePath)
    {
        var path = Path.Combine(TestContext.CurrentContext.TestDirectory, relativePath);
        return JsonSerializer.Deserialize<ReplayFixture>(
            File.ReadAllText(path),
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? new ReplayFixture();
    }

    public string ConfigJson()
    {
        return Config.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null ? "{}" : Config.GetRawText();
    }
}

public sealed class ReplayBar
{
    public DateTime Time { get; set; }
    public string Symbol { get; set; } = "";
    public decimal Open { get; set; }
    public decimal High { get; set; }
    public decimal Low { get; set; }
    public decimal Close { get; set; }
    public decimal Volume { get; set; }
}

public sealed class ExpectedReplayResult
{
    public int? MinInsights { get; set; }
    public int? TargetCount { get; set; }
}

public sealed class ReplayResult
{
    public List<Insight> Insights { get; } = new();
    public List<IPortfolioTarget> Targets { get; } = new();
}

public static class ModuleReplayHarness
{
    public static async Task<ReplayResult> Run(AlphaModel signal, PortfolioConstructionModel target, ReplayFixture fixture)
    {
        Config.Set("data-folder", Path.Combine(TestContext.CurrentContext.TestDirectory, "Data"));
        var dataProvider = new ReplayDataProvider();
        var mapFileProvider = new LocalDiskMapFileProvider();
        mapFileProvider.Initialize(dataProvider);
        Composer.Instance.AddPart<IDataProvider>(dataProvider);
        Composer.Instance.AddPart<QuantConnect.Interfaces.IMapFileProvider>(mapFileProvider);
        var algorithm = new QCAlgorithm();
        var symbols = fixture.Symbols.Count > 0
            ? fixture.Symbols
            : fixture.Bars.Select(x => x.Symbol).Where(x => !string.IsNullOrWhiteSpace(x)).Distinct().ToList();

        if (fixture.Bars.Count > 0)
        {
            var first = fixture.Bars.Min(x => x.Time);
            algorithm.SetStartDate(first.Year, first.Month, first.Day);
            algorithm.SetDateTime(first);
        }

        var securities = new List<QuantConnect.Securities.Security>();
        var byTicker = new Dictionary<string, Symbol>(StringComparer.OrdinalIgnoreCase);
        foreach (var ticker in symbols)
        {
            var symbol = new Symbol(SecurityIdentifier.GenerateEquity(SecurityIdentifier.DefaultDate, ticker, Market.USA), ticker);
            var security = new Security(
                SecurityExchangeHours.AlwaysOpen(TimeZones.NewYork),
                new SubscriptionDataConfig(
                    typeof(TradeBar),
                    symbol,
                    Resolution.Daily,
                    TimeZones.NewYork,
                    TimeZones.NewYork,
                    true,
                    false,
                    false),
                new Cash(Currencies.USD, 0, 1m),
                SymbolProperties.GetDefault(Currencies.USD),
                ErrorCurrencyConverter.Instance,
                RegisteredSecurityDataTypesProvider.Null,
                new SecurityCache());
            algorithm.Securities.Add(security);
            securities.Add(security);
            byTicker[ticker] = security.Symbol;
        }

        var changes = new SecurityChangesConstructor();
        foreach (var security in securities)
        {
            changes.Add(security, false);
        }

        var configuration = new QuantConnect.Modules.ModuleConfiguration(
            "signal.replay",
            QuantConnect.Modules.ModuleKind.Signal,
            QuantConnect.Modules.ModuleActivationMode.InProcessPlugin,
            signal.GetType().FullName ?? signal.Name,
            parameters: new Dictionary<string, string>
            {
                ["config"] = fixture.ConfigJson()
            });
        await signal.Initialize(configuration);
        signal.OnSecuritiesChanged(algorithm, changes.Flush());
        target.OnSecuritiesChanged(algorithm, SecurityChanges.None);

        var result = new ReplayResult();
        foreach (var group in fixture.Bars.GroupBy(x => x.Time).OrderBy(x => x.Key))
        {
            algorithm.SetDateTime(group.Key);
            var data = new List<BaseData>();
            foreach (var row in group)
            {
                if (!byTicker.TryGetValue(row.Symbol, out var symbol))
                {
                    continue;
                }

                var bar = new TradeBar(group.Key, symbol, row.Open, row.High, row.Low, row.Close, row.Volume, TimeSpan.FromDays(1));
                algorithm.Securities[symbol].SetMarketPrice(bar);
                data.Add(bar);
            }

            var slice = new Slice(group.Key, data, group.Key);
            result.Insights.AddRange(signal.Update(algorithm, slice));
        }

        result.Targets.AddRange(target.CreateTargets(algorithm, result.Insights.ToArray()));
        return result;
    }
}

internal sealed class ReplayDataProvider : IDataProvider
{
    public event EventHandler<DataProviderNewDataRequestEventArgs>? NewDataRequest
    {
        add { }
        remove { }
    }

    public Stream Fetch(string key)
    {
        var path = Path.IsPathRooted(key) ? key : Path.Combine(Config.Get("data-folder"), key);
        return File.Exists(path) ? File.OpenRead(path) : Stream.Null;
    }
}
"""


def target_source(project_name):
    return f"""using System.Collections.Generic;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;

namespace {project_name}.Modules;

public sealed class {project_name}TargetModule : PortfolioConstructionModel
{{
    public override IEnumerable<IPortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
    {{
        foreach (var insight in insights)
        {{
            if (insight.SourceModel != ModuleIdentity.SignalSourceModel)
            {{
                continue;
            }}

            if (insight.Weight == null)
            {{
                continue;
            }}

            yield return PortfolioTarget.Percent(algorithm, insight.Symbol, insight.Weight.Value);
        }}
    }}
}}
"""


def readme(name, project_name, strategy_id, version):
    return f"""# {name}

This is a strategy-side module scaffold. It keeps strategy logic outside Engine code and uses the control API for publishing.

## Edit

- `src/{project_name}SignalModule.cs`: signal logic.
- `src/{project_name}TargetModule.cs`: target construction from signal weights.
- `src/ModuleIdentity.cs`: generated ids and source model constants used by Signal/Target.
- `src/ModuleConfig.cs`: typed config helper used by generated modules.
- `payloads/input.config.json`: symbols and resolution.
- `payloads/signal.config.json`: signal config.
- `payloads/package.json`: package-level module entry points.
- `tests/fixtures/replay.json`: local bar replay sample.
- `tests/ReplayHarness.cs`: local replay runner for Signal and Target tests.

## Build

```bash
/root/.dotnet/dotnet build {project_name}.Modules.csproj -c Release --nologo
DLL=$(find bin/Release -name {project_name}.Modules.dll | head -n 1)
test -n "$DLL"
```

## Test

```bash
/root/.dotnet/dotnet test tests/{project_name}.Modules.Tests.csproj -c Release --nologo --logger "console;verbosity=minimal"
```

The generated tests run the module lifecycle and stage interfaces without starting Engine. Extend `tests/ModuleSmokeTests.cs` as strategy logic is implemented.

Refresh generated JSON schemas after editing config classes:

```bash
python3 -m strategy_devkit.schema --root . --write
```

## Publish

```bash
python3 -m strategy_devkit.publish --root . --api http://127.0.0.1:8777
```

`publish` builds, runs `tests/`, uploads one DLL package, registers all module entry points in `payloads/package.json`, merges `payloads/*.instance.json` into one inline attach request, and records a source artifact. Instances are loaded into the target pipeline as part of attach; there is no separate pre-loaded instance step.
"""


def replay_fixture():
    return {
        "config": {
            "insightDays": 10,
            "defaultWeight": 0.5,
        },
        "symbols": ["SPY"],
        "bars": [
            {
                "time": "2020-01-02T00:00:00",
                "symbol": "SPY",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 1000000,
            },
            {
                "time": "2020-01-03T00:00:00",
                "symbol": "SPY",
                "open": 100.5,
                "high": 102,
                "low": 100,
                "close": 101.5,
                "volume": 1200000,
            },
        ],
        "expected": {
            "minInsights": 0,
            "targetCount": 0,
        },
    }


def create_project(args):
    name_slug = slugify(args.name)
    project_name = pascal_case(args.name)
    strategy_id = args.strategy_id or name_slug
    version = args.version
    root = Path(args.out).resolve()
    sdk_bin = Path(args.sdk_bin)
    required_sdk_files = [
        sdk_bin / "QuantConnect.Common.dll",
        sdk_bin / "QuantConnect.Algorithm.dll",
    ]
    missing_sdk_files = [str(path) for path in required_sdk_files if not path.exists()]
    if missing_sdk_files:
        raise SystemExit(
            "Module SDK binary directory is not ready. Missing: "
            + ", ".join(missing_sdk_files)
            + "\nRun: /root/.dotnet/dotnet test /data/data_jyz/trade/Lean/Tests.Modules/QuantConnect.Modules.Tests.csproj "
            "-c Debug --nologo --logger \"console;verbosity=minimal\""
        )

    write_text(root / f"{project_name}.Modules.csproj", csproj(project_name, args.lean_root, args.sdk_bin), args.force)
    write_text(root / "src" / "ModuleIdentity.cs", identity_source(project_name, strategy_id, version), args.force)
    write_text(root / "src" / "ModuleConfig.cs", config_helper_source(), args.force)
    write_text(root / "src" / f"{project_name}SignalModule.cs", signal_source(project_name), args.force)
    write_text(root / "src" / f"{project_name}TargetModule.cs", target_source(project_name), args.force)
    write_text(root / "tests" / f"{project_name}.Modules.Tests.csproj", test_csproj(project_name, args.lean_root, args.sdk_bin), args.force)
    write_text(root / "tests" / "ModuleSmokeTests.cs", test_source(project_name), args.force)
    write_text(root / "tests" / "ReplayHarness.cs", replay_harness_source(), args.force)
    write_json(root / "tests" / "fixtures" / "replay.json", replay_fixture(), args.force)
    write_text(root / "README.md", readme(args.name, project_name, strategy_id, version), args.force)

    write_json(root / "payloads" / "input.config.json", {
        "symbols": ["SPY"],
        "resolution": "Daily",
        "securityType": "Equity",
        "market": "usa",
        "fillForward": True,
    }, args.force)
    write_json(root / "payloads" / "signal.config.json", {
        "insightDays": 35,
        "defaultWeight": 1.0,
    }, args.force)
    write_json(root / "payloads" / "signal.schema.json", {
        "type": "object",
        "properties": {
            "insightDays": {"type": "integer"},
            "defaultWeight": {"type": "number"},
        },
    }, args.force)
    write_json(root / "payloads" / "package.json", {
        "packageId": strategy_id,
        "version": version,
        "metadata": {
            "source": "strategy_devkit.scaffold",
        },
        "modules": [
            {
                "kind": "Signal",
                "moduleId": f"{strategy_id}-signal",
                "version": version,
                "activationMode": "InProcessPlugin",
                "entryPoint": f"{project_name}.Modules.{project_name}SignalModule",
                "hotSwapMode": "Live",
                "parameters": {
                    "assemblyPath": f"{{{{packageRoot}}}}/artifacts/{project_name}.Modules.dll",
                },
                "configSchema": {
                    "type": "object",
                    "properties": {
                        "insightDays": {"type": "integer"},
                        "defaultWeight": {"type": "number"},
                    },
                },
                "description": f"{args.name} signal module.",
            },
            {
                "kind": "Target",
                "moduleId": f"{strategy_id}-target",
                "version": version,
                "activationMode": "InProcessPlugin",
                "entryPoint": f"{project_name}.Modules.{project_name}TargetModule",
                "hotSwapMode": "Live",
                "parameters": {
                    "assemblyPath": f"{{{{packageRoot}}}}/artifacts/{project_name}.Modules.dll",
                },
                "description": f"{args.name} target module.",
            },
        ],
    }, args.force)

    write_json(root / "payloads" / "input.instance.json", {
        "instanceId": f"{strategy_id}.input",
        "kind": "Input",
        "moduleId": "json-input",
        "version": "builtin",
        "config": {"symbols": ["SPY"], "resolution": "Daily", "securityType": "Equity", "market": "usa", "fillForward": True},
    }, args.force)
    write_json(root / "payloads" / "signal.instance.json", {
        "instanceId": f"{strategy_id}.signal",
        "kind": "Signal",
        "moduleId": f"{strategy_id}-signal",
        "version": version,
        "config": {"insightDays": 35, "defaultWeight": 1.0},
    }, args.force)
    write_json(root / "payloads" / "target.instance.json", {
        "instanceId": f"{strategy_id}.target",
        "kind": "Target",
        "moduleId": f"{strategy_id}-target",
        "version": version,
    }, args.force)
    write_json(root / "payloads" / "universe.instance.json", {
        "instanceId": f"{strategy_id}.universe.none",
        "kind": "Universe",
        "moduleId": "null-universe",
        "version": "builtin",
    }, args.force)
    write_json(root / "payloads" / "risk.instance.json", {
        "instanceId": f"{strategy_id}.risk.none",
        "kind": "Constraint",
        "moduleId": "null-risk",
        "version": "builtin",
    }, args.force)
    write_json(root / "payloads" / "execution.instance.json", {
        "instanceId": f"{strategy_id}.execution.immediate",
        "kind": "Execution",
        "moduleId": "immediate-execution",
        "version": "builtin",
    }, args.force)
    write_json(root / "payloads" / "market.instance.json", {
        "instanceId": f"{strategy_id}.market.default",
        "kind": "MarketRule",
        "moduleId": "default-market",
        "version": "builtin",
    }, args.force)
    write_json(root / "payloads" / "attach.json", {
        "strategyId": strategy_id,
        "version": version,
        "stages": {
            "inputs": [f"{strategy_id}.input"],
            "universe": [f"{strategy_id}.universe.none"],
            "signal": [f"{strategy_id}.signal"],
            "target": [f"{strategy_id}.target"],
            "constraint": [f"{strategy_id}.risk.none"],
            "execution": [f"{strategy_id}.execution.immediate"],
            "analyzer": [],
        },
        "marketRule": f"{strategy_id}.market.default",
    }, args.force)

    print(root)


def main():
    parser = argparse.ArgumentParser(description="Create a strategy-side module scaffold.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new = subparsers.add_parser("new", help="Create a new module scaffold.")
    new.add_argument("--name", required=True)
    new.add_argument("--out", required=True)
    new.add_argument("--strategy-id", default="")
    new.add_argument("--version", default="dev")
    new.add_argument("--lean-root", default="/data/data_jyz/trade/Lean")
    new.add_argument("--sdk-bin", default="/data/data_jyz/trade/Lean/Tests.Modules/bin/Debug/net10.0")
    new.add_argument("--force", action="store_true")
    new.set_defaults(func=create_project)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
