#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ENGINE_MODULE_KINDS = {
    "Input",
    "Universe",
    "Signal",
    "Target",
    "Constraint",
    "Execution",
    "MarketRule",
    "Analyzer",
}

MARKET_COMPONENT_KINDS = {
    "OrderSubmitRule",
    "OrderUpdateRule",
    "OrderExecutionRule",
    "LeverageRule",
    "FeeModel",
    "SlippageModel",
    "FillModel",
    "BuyingPowerModel",
    "SettlementModel",
    "MarginInterestModel",
    "ShortableProvider",
    "BenchmarkProvider",
}

MODULE_KINDS = ENGINE_MODULE_KINDS | MARKET_COMPONENT_KINDS

ACTIVATION_MODES = {
    "BuiltIn",
    "InProcessPlugin",
    "RemoteService",
    "ScriptRunner",
    "OutOfProcessWorker",
}

HOT_SWAP_MODES = {
    "Live",
    "RequiresPause",
    "RequiresFlatNoOrders",
    "RequiresRestart",
}

STAGES = {
    "inputs": list,
    "universe": list,
    "signal": list,
    "target": list,
    "constraint": list,
    "execution": list,
    "analyzer": list,
}


def preset_module(kind, module_id, entry_point, hot_swap_mode, description, config_schema=None):
    return {
        "kind": kind,
        "moduleId": module_id,
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": entry_point,
        "hotSwapMode": hot_swap_mode,
        "parameters": {},
        "dependencies": [],
        "configSchema": config_schema or {},
        "description": description,
        "builtin": True,
    }


PRESET_MODULES = [
    {
        "kind": "Input",
        "moduleId": "json-input",
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": "QuantConnect.Algorithm.Modules.JsonInputModule",
        "hotSwapMode": "Live",
        "parameters": {},
        "dependencies": [],
        "configSchema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": ["string", "object"]},
                                    "resolution": {"type": "string"},
                                    "fillForward": {"type": "boolean"},
                                    "leverage": {"type": "number"},
                                    "extendedMarketHours": {"type": "boolean"},
                                },
                            },
                        ]
                    },
                },
                "inputs": {"type": "array"},
                "resolution": {"type": "string"},
                "securityType": {"type": "string"},
                "market": {"type": "string"},
                "fillForward": {"type": "boolean"},
                "leverage": {"type": "number"},
                "extendedMarketHours": {"type": "boolean"},
            },
        },
        "description": "Built-in configurable input module that registers symbols from instance config.",
        "builtin": True,
    },
    {
        "kind": "Universe",
        "moduleId": "null-universe",
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": "QuantConnect.Algorithm.Framework.Selection.NullUniverseSelectionModel",
        "hotSwapMode": "Live",
        "parameters": {},
        "dependencies": [],
        "configSchema": {},
        "description": "Built-in universe model that emits no universes.",
        "builtin": True,
    },
    {
        "kind": "Signal",
        "moduleId": "null-signal",
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": "QuantConnect.Algorithm.Framework.Alphas.NullAlphaModel",
        "hotSwapMode": "Live",
        "parameters": {},
        "dependencies": [],
        "configSchema": {},
        "description": "Built-in alpha model that emits no insights.",
        "builtin": True,
    },
    {
        "kind": "Target",
        "moduleId": "null-target",
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": "QuantConnect.Algorithm.Framework.Portfolio.NullPortfolioConstructionModel",
        "hotSwapMode": "Live",
        "parameters": {},
        "dependencies": [],
        "configSchema": {},
        "description": "Built-in portfolio construction model that emits no targets.",
        "builtin": True,
    },
    {
        "kind": "Constraint",
        "moduleId": "null-risk",
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": "QuantConnect.Algorithm.Framework.Risk.NullRiskManagementModel",
        "hotSwapMode": "Live",
        "parameters": {},
        "dependencies": [],
        "configSchema": {},
        "description": "Built-in risk model that emits no target adjustments.",
        "builtin": True,
    },
    {
        "kind": "Execution",
        "moduleId": "null-execution",
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": "QuantConnect.Algorithm.Framework.Execution.NullExecutionModel",
        "hotSwapMode": "RequiresPause",
        "parameters": {},
        "dependencies": [],
        "configSchema": {},
        "description": "Built-in execution model that does not place orders.",
        "builtin": True,
    },
    {
        "kind": "Execution",
        "moduleId": "immediate-execution",
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": "QuantConnect.Algorithm.Modules.ImmediateExecutionModule",
        "hotSwapMode": "RequiresPause",
        "parameters": {},
        "dependencies": [],
        "configSchema": {},
        "description": "Built-in execution model that submits targets immediately.",
        "builtin": True,
    },
    {
        "kind": "MarketRule",
        "moduleId": "default-market",
        "version": "builtin",
        "activationMode": "BuiltIn",
        "entryPoint": "QuantConnect.Algorithm.Modules.DefaultMarketRuleModule",
        "hotSwapMode": "RequiresFlatNoOrders",
        "parameters": {},
        "dependencies": [],
        "configSchema": {},
        "description": "Built-in default brokerage model used as the default market rule module.",
        "builtin": True,
    },
]

PRESET_MODULES.extend([
    preset_module(
        "Universe",
        "qc500-universe",
        "QuantConnect.Algorithm.Framework.Selection.QC500UniverseSelectionModel",
        "Live",
        "Built-in QC500 fundamental universe selection model with default parameters.",
    ),
    preset_module(
        "Universe",
        "ema-cross-universe",
        "QuantConnect.Algorithm.Framework.Selection.EmaCrossUniverseSelectionModel",
        "Live",
        "Built-in EMA-cross fundamental universe selection model; config may bind fastPeriod, slowPeriod, universeCount.",
    ),
    preset_module(
        "Signal",
        "ema-cross-alpha",
        "QuantConnect.Algorithm.Framework.Alphas.EmaCrossAlphaModel",
        "Live",
        "Built-in EMA-cross alpha model; config may bind fastPeriod, slowPeriod, resolution.",
    ),
    preset_module(
        "Signal",
        "historical-returns-alpha",
        "QuantConnect.Algorithm.Framework.Alphas.HistoricalReturnsAlphaModel",
        "Live",
        "Built-in historical returns alpha model; config may bind lookback, resolution.",
    ),
    preset_module(
        "Signal",
        "macd-alpha",
        "QuantConnect.Algorithm.Framework.Alphas.MacdAlphaModel",
        "Live",
        "Built-in MACD alpha model; config may bind fastPeriod, slowPeriod, signalPeriod, movingAverageType, resolution.",
    ),
    preset_module(
        "Signal",
        "rsi-alpha",
        "QuantConnect.Algorithm.Framework.Alphas.RsiAlphaModel",
        "Live",
        "Built-in RSI alpha model; config may bind period, resolution.",
    ),
    preset_module(
        "Signal",
        "pearson-correlation-pairs-alpha",
        "QuantConnect.Algorithm.Framework.Alphas.PearsonCorrelationPairsTradingAlphaModel",
        "Live",
        "Built-in Pearson-correlation pairs alpha model; config may bind lookback, resolution, threshold, minimumCorrelation.",
    ),
    preset_module(
        "Target",
        "equal-weighting-target",
        "QuantConnect.Algorithm.Framework.Portfolio.EqualWeightingPortfolioConstructionModel",
        "Live",
        "Built-in equal-weighting portfolio construction model; config may bind resolution and portfolioBias.",
    ),
    preset_module(
        "Target",
        "insight-weighting-target",
        "QuantConnect.Algorithm.Framework.Portfolio.InsightWeightingPortfolioConstructionModel",
        "Live",
        "Built-in insight-weighting portfolio construction model; config may bind resolution and portfolioBias.",
    ),
    preset_module(
        "Target",
        "confidence-weighted-target",
        "QuantConnect.Algorithm.Framework.Portfolio.ConfidenceWeightedPortfolioConstructionModel",
        "Live",
        "Built-in confidence-weighted portfolio construction model; config may bind resolution and portfolioBias.",
    ),
    preset_module(
        "Target",
        "accumulative-insight-target",
        "QuantConnect.Algorithm.Framework.Portfolio.AccumulativeInsightPortfolioConstructionModel",
        "Live",
        "Built-in accumulative insight portfolio construction model; config may bind rebalancingFunc and portfolioBias when using compatible values.",
    ),
    preset_module(
        "Target",
        "mean-variance-optimization-target",
        "QuantConnect.Algorithm.Framework.Portfolio.MeanVarianceOptimizationPortfolioConstructionModel",
        "Live",
        "Built-in mean-variance optimization portfolio construction model; config may bind rebalanceResolution, portfolioBias, lookback, period, resolution, targetReturn.",
    ),
    preset_module(
        "Target",
        "black-litterman-optimization-target",
        "QuantConnect.Algorithm.Framework.Portfolio.BlackLittermanOptimizationPortfolioConstructionModel",
        "Live",
        "Built-in Black-Litterman optimization portfolio construction model; config may bind rebalanceResolution, portfolioBias, lookback, period, resolution, riskFreeRate, delta, tau.",
    ),
    preset_module(
        "Target",
        "risk-parity-target",
        "QuantConnect.Algorithm.Framework.Portfolio.RiskParityPortfolioConstructionModel",
        "Live",
        "Built-in risk-parity portfolio construction model; config may bind rebalanceResolution, portfolioBias, lookback, period, resolution.",
    ),
    preset_module(
        "Target",
        "mean-reversion-target",
        "QuantConnect.Algorithm.Framework.Portfolio.MeanReversionPortfolioConstructionModel",
        "Live",
        "Built-in mean-reversion portfolio construction model; config may bind rebalanceResolution, portfolioBias, reversionThreshold, windowSize, resolution.",
    ),
    preset_module(
        "Target",
        "sector-weighting-target",
        "QuantConnect.Algorithm.Framework.Portfolio.SectorWeightingPortfolioConstructionModel",
        "Live",
        "Built-in sector-weighting portfolio construction model; config may bind resolution.",
    ),
    preset_module(
        "Constraint",
        "trailing-stop-risk",
        "QuantConnect.Algorithm.Framework.Risk.TrailingStopRiskManagementModel",
        "Live",
        "Built-in trailing-stop risk model; config may bind maximumDrawdownPercent.",
    ),
    preset_module(
        "Constraint",
        "maximum-sector-exposure-risk",
        "QuantConnect.Algorithm.Framework.Risk.MaximumSectorExposureRiskManagementModel",
        "Live",
        "Built-in maximum sector exposure risk model; config may bind maximumSectorExposure.",
    ),
    preset_module(
        "Execution",
        "spread-execution",
        "QuantConnect.Algorithm.Framework.Execution.SpreadExecutionModel",
        "RequiresPause",
        "Built-in spread execution model; config may bind acceptingSpreadPercent and asynchronous.",
    ),
    preset_module(
        "Execution",
        "standard-deviation-execution",
        "QuantConnect.Algorithm.Framework.Execution.StandardDeviationExecutionModel",
        "RequiresPause",
        "Built-in standard-deviation execution model; config may bind period, deviations, resolution, asynchronous.",
    ),
    preset_module(
        "Execution",
        "vwap-execution",
        "QuantConnect.Algorithm.Framework.Execution.VolumeWeightedAveragePriceExecutionModel",
        "RequiresPause",
        "Built-in VWAP execution model; config may bind asynchronous.",
    ),
])


def load_config(path):
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)

    live_manifest = config.get("liveManifestPath")
    release_root = config.get("releaseRoot")
    if not live_manifest:
        raise ValueError("Control API config requires liveManifestPath.")
    if not release_root:
        raise ValueError("Control API config requires releaseRoot.")

    return {
        "liveManifestPath": str(Path(live_manifest).expanduser()),
        "releaseRoot": str(Path(release_root).expanduser()),
        "controlRoot": str(Path(config.get("controlRoot", Path(release_root).expanduser() / "_control")).expanduser()),
    }


def atomic_write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def reject_path(path):
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Invalid bundle file path: {path}")
    if not str(candidate):
        raise ValueError("Bundle file path cannot be empty.")
    return candidate


def write_bundle_files(release_dir, files):
    for item in files or []:
        relative = reject_path(item.get("path", ""))
        content = item.get("contentBase64")
        source_url = item.get("sourceUrl")
        if content is None and source_url is None:
            raise ValueError(f"Bundle file '{relative}' requires contentBase64 or sourceUrl.")

        target = release_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if content is not None:
            data = base64.b64decode(content)
        else:
            data = download_file(source_url)

        expected_sha256 = item.get("sha256")
        if expected_sha256:
            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256.lower() != expected_sha256.lower():
                raise ValueError(f"Bundle file '{relative}' sha256 mismatch: expected {expected_sha256}, got {actual_sha256}.")

        target.write_bytes(data)
        if item.get("executable"):
            target.chmod(target.stat().st_mode | 0o111)


def state_path(config, name):
    root = Path(config["controlRoot"])
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def load_json_file(path, default):
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_state(config, name, default):
    return load_json_file(state_path(config, name), default)


def save_state(config, name, payload):
    atomic_write_json(state_path(config, name), payload)


def definition_key(kind, module_id, version):
    return f"{kind}/{module_id}/{version}"


def default_module_definitions():
    return {
        definition_key(definition["kind"], definition["moduleId"], definition["version"]): dict(definition)
        for definition in PRESET_MODULES
    }


def load_module_definitions(config):
    definitions = default_module_definitions()
    definitions.update(load_state(config, "modules.json", {}))
    return definitions


def is_preset_definition(kind, module_id, version):
    return definition_key(kind, module_id, version) in default_module_definitions()


def module_version_dir(config, kind, module_id, version):
    return Path(config["releaseRoot"]) / "_modules" / reject_path(kind) / reject_path(module_id) / reject_path(version)


def get_definition(definitions, kind, module_id, version):
    key = definition_key(kind, module_id, version)
    definition = definitions.get(key)
    if definition is None:
        raise ValueError(f"Module definition does not exist: {key}")
    return definition


def read_request_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    return json.loads(handler.rfile.read(length) or b"{}")


def download_file(source_url):
    if not source_url:
        raise ValueError("sourceUrl cannot be empty.")
    if not source_url.startswith(("http://", "https://")):
        raise ValueError(f"Unsupported sourceUrl scheme: {source_url}")

    request = urllib.request.Request(source_url, headers={"User-Agent": "lean-strategy-submit-api"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def substitute_release_root(value, release_dir):
    if isinstance(value, str):
        return value.replace("{{releaseRoot}}", str(release_dir))
    if isinstance(value, list):
        return [substitute_release_root(item, release_dir) for item in value]
    if isinstance(value, dict):
        return {key: substitute_release_root(item, release_dir) for key, item in value.items()}
    return value


def substitute_module_root(value, module_dir):
    if isinstance(value, str):
        return value.replace("{{moduleRoot}}", str(module_dir))
    if isinstance(value, list):
        return [substitute_module_root(item, module_dir) for item in value]
    if isinstance(value, dict):
        return {key: substitute_module_root(item, module_dir) for key, item in value.items()}
    return value


def validate_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object.")
    if not manifest.get("name"):
        raise ValueError("manifest.name is required.")
    modules = manifest.get("modules")
    if not isinstance(modules, list):
        raise ValueError("manifest.modules must be an array.")

    module_keys = set()
    for module in modules:
        key = module.get("key")
        if not key:
            raise ValueError("Each module requires key.")
        if key in module_keys:
            raise ValueError(f"Duplicate module key: {key}")
        module_keys.add(key)

        kind = module.get("kind")
        if kind not in MODULE_KINDS:
            raise ValueError(f"Module '{key}' has invalid kind '{kind}'.")
        if kind not in ENGINE_MODULE_KINDS:
            raise ValueError(f"Module '{key}' has control-plane-only kind '{kind}' and cannot be written to Engine manifest.")

        activation = module.get("activationMode")
        if activation not in ACTIVATION_MODES:
            raise ValueError(f"Module '{key}' has invalid activationMode '{activation}'.")

        hot_swap = module.get("hotSwapMode")
        if hot_swap not in HOT_SWAP_MODES:
            raise ValueError(f"Module '{key}' has invalid hotSwapMode '{hot_swap}'.")

        parameters = module.get("parameters") or {}
        if activation == "RemoteService" and not parameters.get("baseUrl"):
            raise ValueError(f"RemoteService module '{key}' requires parameters.baseUrl.")
        if activation in {"ScriptRunner", "OutOfProcessWorker"} and not parameters.get("command"):
            raise ValueError(f"{activation} module '{key}' requires parameters.command.")
        if activation == "InProcessPlugin" and not parameters.get("assemblyPath"):
            raise ValueError(f"InProcessPlugin module '{key}' requires parameters.assemblyPath.")

    for stage, expected_type in STAGES.items():
        value = manifest.get(stage, [])
        if not isinstance(value, expected_type):
            raise ValueError(f"manifest.{stage} must be an array.")
        for key in value:
            if key not in module_keys:
                raise ValueError(f"manifest.{stage} references unknown module '{key}'.")

    market_rule = manifest.get("marketRule", "")
    if market_rule and market_rule not in module_keys:
        raise ValueError(f"manifest.marketRule references unknown module '{market_rule}'.")


def validate_module_definition(definition):
    kind = definition.get("kind")
    module_id = definition.get("moduleId")
    version = definition.get("version")
    if kind not in MODULE_KINDS:
        raise ValueError(f"Invalid module kind: {kind}")
    if not module_id:
        raise ValueError("moduleId is required.")
    if not version:
        raise ValueError("version is required.")

    activation = definition.get("activationMode")
    if activation not in ACTIVATION_MODES:
        raise ValueError(f"Module '{module_id}' has invalid activationMode '{activation}'.")

    if kind in ENGINE_MODULE_KINDS:
        if not definition.get("entryPoint"):
            raise ValueError(f"Engine module '{module_id}' requires entryPoint.")
        hot_swap = definition.get("hotSwapMode")
        if hot_swap not in HOT_SWAP_MODES:
            raise ValueError(f"Engine module '{module_id}' has invalid hotSwapMode '{hot_swap}'.")

    parameters = definition.get("parameters") or {}
    if activation == "RemoteService" and not parameters.get("baseUrl"):
        raise ValueError(f"RemoteService module '{module_id}' requires parameters.baseUrl.")
    if activation in {"ScriptRunner", "OutOfProcessWorker"} and not parameters.get("command"):
        raise ValueError(f"{activation} module '{module_id}' requires parameters.command.")
    if activation == "InProcessPlugin" and not parameters.get("assemblyPath"):
        raise ValueError(f"InProcessPlugin module '{module_id}' requires parameters.assemblyPath.")


def handle_add_module(config, request):
    kind = request.get("kind")
    module_id = request.get("moduleId")
    version = request.get("version")
    if not kind or not module_id or not version:
        raise ValueError("kind, moduleId and version are required.")
    if is_preset_definition(kind, module_id, version):
        raise ValueError(f"Cannot overwrite built-in module definition: {definition_key(kind, module_id, version)}")

    module_dir = module_version_dir(config, kind, module_id, version)
    module_dir.mkdir(parents=True, exist_ok=True)
    write_bundle_files(module_dir, request.get("files"))

    definition = {
        "kind": kind,
        "moduleId": module_id,
        "version": version,
        "activationMode": request.get("activationMode"),
        "entryPoint": request.get("entryPoint", ""),
        "hotSwapMode": request.get("hotSwapMode", "RequiresPause"),
        "parameters": substitute_module_root(request.get("parameters") or {}, module_dir),
        "dependencies": request.get("dependencies") or [],
        "configSchema": request.get("configSchema") or {},
        "description": request.get("description", ""),
    }
    validate_module_definition(definition)

    definitions = load_state(config, "modules.json", {})
    key = definition_key(kind, module_id, version)
    definitions[key] = definition
    save_state(config, "modules.json", definitions)
    atomic_write_json(module_dir / "module.json", definition)

    return {
        "accepted": True,
        "moduleKey": key,
        "moduleDir": str(module_dir),
        "definition": definition,
    }


def find_instance_references(instances, kind, module_id, version):
    result = []
    for instance_id, instance in instances.items():
        if (instance.get("kind") == kind and
                instance.get("moduleId") == module_id and
                instance.get("version") == version):
            result.append(instance_id)
    return result


def handle_delete_module(config, kind, module_id, version):
    definitions = load_state(config, "modules.json", {})
    instances = load_state(config, "instances.json", {})
    key = definition_key(kind, module_id, version)
    if is_preset_definition(kind, module_id, version):
        raise ValueError(f"Cannot delete built-in module definition: {key}")
    if key not in definitions:
        raise ValueError(f"Module definition does not exist: {key}")

    references = find_instance_references(instances, kind, module_id, version)
    if references:
        raise ValueError(f"Module definition '{key}' is still referenced by instances: {', '.join(references)}")

    definition = definitions.pop(key)
    save_state(config, "modules.json", definitions)
    shutil.rmtree(module_version_dir(config, kind, module_id, version), ignore_errors=True)
    return {
        "accepted": True,
        "deleted": key,
        "definition": definition,
    }


def handle_create_instance(config, request):
    instance_id = request.get("instanceId")
    kind = request.get("kind")
    module_id = request.get("moduleId")
    version = request.get("version")
    if not instance_id or not kind or not module_id or not version:
        raise ValueError("instanceId, kind, moduleId and version are required.")
    reject_path(instance_id)

    definitions = load_module_definitions(config)
    definition = get_definition(definitions, kind, module_id, version)
    instance = {
        "instanceId": instance_id,
        "kind": kind,
        "moduleId": module_id,
        "version": version,
        "config": request.get("config") or {},
        "parameters": request.get("parameters") or {},
        "hotSwapMode": request.get("hotSwapMode") or definition.get("hotSwapMode", "RequiresPause"),
        "status": "created",
    }
    if instance["hotSwapMode"] not in HOT_SWAP_MODES:
        raise ValueError(f"Instance '{instance_id}' has invalid hotSwapMode '{instance['hotSwapMode']}'.")
    if not isinstance(instance["config"], dict):
        raise ValueError("instance config must be an object.")
    if not isinstance(instance["parameters"], dict):
        raise ValueError("instance parameters must be an object.")

    instances = load_state(config, "instances.json", {})
    instances[instance_id] = instance
    save_state(config, "instances.json", instances)
    return {
        "accepted": True,
        "instance": instance,
    }


def compile_module(instance, definition):
    kind = instance["kind"]
    if kind not in ENGINE_MODULE_KINDS:
        return None

    parameters = dict(definition.get("parameters") or {})
    parameters.update(instance.get("parameters") or {})
    config = instance.get("config") or {}
    if config:
        parameters["config"] = json.dumps(config, separators=(",", ":"), sort_keys=True)

    return {
        "key": instance["instanceId"],
        "kind": kind,
        "activationMode": definition["activationMode"],
        "entryPoint": definition.get("entryPoint", ""),
        "version": definition["version"],
        "parameters": parameters,
        "hotSwapMode": instance.get("hotSwapMode") or definition.get("hotSwapMode", "RequiresPause"),
        "dependencies": definition.get("dependencies") or [],
    }


def normalize_stage_references(stages):
    stages = stages or {}
    result = {}
    for stage, expected_type in STAGES.items():
        value = stages.get(stage, [])
        if not isinstance(value, expected_type):
            raise ValueError(f"stages.{stage} must be an array.")
        result[stage] = value
    return result


def collect_attachment_instance_ids(stages, market):
    ids = []
    for values in stages.values():
        ids.extend(values)
    for value in (market or {}).values():
        if isinstance(value, str) and value:
            ids.append(value)
        elif isinstance(value, list):
            ids.extend(item for item in value if isinstance(item, str) and item)
    return ids


def compile_attachment_manifest(config, attachment):
    definitions = load_module_definitions(config)
    instances = load_state(config, "instances.json", {})
    stages = normalize_stage_references(attachment.get("stages") or {})
    market = attachment.get("market") or {}
    referenced_ids = collect_attachment_instance_ids(stages, market)
    if attachment.get("marketRule"):
        referenced_ids.append(attachment["marketRule"])

    modules = []
    module_keys = set()
    control_only = {}
    for instance_id in referenced_ids:
        instance = instances.get(instance_id)
        if instance is None:
            raise ValueError(f"Pipeline attachment references unknown instance '{instance_id}'.")
        definition = get_definition(definitions, instance["kind"], instance["moduleId"], instance["version"])
        module = compile_module(instance, definition)
        if module is None:
            control_only[instance_id] = {
                "kind": instance["kind"],
                "moduleId": instance["moduleId"],
                "version": instance["version"],
            }
            continue
        if module["key"] not in module_keys:
            modules.append(module)
            module_keys.add(module["key"])

    manifest = {
        "name": attachment.get("name") or f"{attachment['strategyId']}-{attachment['version']}",
        "modules": modules,
        "inputs": stages["inputs"],
        "universe": stages["universe"],
        "signal": stages["signal"],
        "target": stages["target"],
        "constraint": stages["constraint"],
        "execution": stages["execution"],
        "analyzer": stages["analyzer"],
        "market": market,
    }

    market_rule = attachment.get("marketRule") or market.get("marketRule") or market.get("brokerageModel")
    if market_rule:
        manifest["marketRule"] = market_rule
    if control_only:
        manifest["controlOnlyModules"] = control_only

    validate_manifest(manifest)
    return manifest


def handle_attach(config, request):
    strategy_id = request.get("strategyId")
    version = request.get("version")
    if not strategy_id or not version:
        raise ValueError("strategyId and version are required.")

    attachment = {
        "strategyId": strategy_id,
        "version": version,
        "name": request.get("name") or f"{strategy_id}-{version}",
        "stages": normalize_stage_references(request.get("stages") or {}),
        "market": request.get("market") or {},
        "marketRule": request.get("marketRule", ""),
    }

    manifest = compile_attachment_manifest(config, attachment)
    release_dir = Path(config["releaseRoot"]) / "_attachments" / reject_path(strategy_id) / reject_path(version)
    release_dir.mkdir(parents=True, exist_ok=True)
    release_manifest = release_dir / "pipeline.json"
    atomic_write_json(release_manifest, manifest)
    atomic_write_json(config["liveManifestPath"], manifest)
    save_state(config, "attachment.json", attachment)

    instances = load_state(config, "instances.json", {})
    active_instance_ids = collect_attachment_instance_ids(attachment["stages"], attachment["market"])
    if attachment.get("marketRule"):
        active_instance_ids.append(attachment["marketRule"])
    for instance_id in active_instance_ids:
        if instance_id in instances:
            instances[instance_id]["status"] = "active"
    save_state(config, "instances.json", instances)

    return {
        "accepted": True,
        "strategyId": strategy_id,
        "version": version,
        "releaseManifest": str(release_manifest),
        "liveManifestPath": config["liveManifestPath"],
        "manifest": manifest,
    }


def handle_detach(config, request):
    attachment = load_state(config, "attachment.json", None)
    if not attachment:
        raise ValueError("No active attachment exists.")

    detach_instances = set(request.get("instances") or [])
    detach_stages = set(request.get("stages") or [])
    detach_market = set(request.get("market") or [])
    if not detach_instances and not detach_stages and not detach_market:
        raise ValueError("detach requires instances, stages, or market fields.")

    stages = normalize_stage_references(attachment.get("stages") or {})
    for stage in detach_stages:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage}")
        stages[stage] = []
    for stage, values in stages.items():
        stages[stage] = [value for value in values if value not in detach_instances]

    market = dict(attachment.get("market") or {})
    for slot in detach_market:
        market.pop(slot, None)
    for slot, value in list(market.items()):
        if isinstance(value, str) and value in detach_instances:
            market.pop(slot, None)
        elif isinstance(value, list):
            market[slot] = [item for item in value if item not in detach_instances]

    attachment["stages"] = stages
    attachment["market"] = market
    if attachment.get("marketRule") in detach_instances:
        attachment["marketRule"] = ""

    manifest = compile_attachment_manifest(config, attachment)
    atomic_write_json(config["liveManifestPath"], manifest)
    save_state(config, "attachment.json", attachment)

    return {
        "accepted": True,
        "attachment": attachment,
        "manifest": manifest,
    }


def handle_submit(config, request):
    strategy_id = request.get("strategyId")
    version = request.get("version")
    if not strategy_id or not version:
        raise ValueError("strategyId and version are required.")

    release_dir = Path(config["releaseRoot"]) / strategy_id / version
    release_dir.mkdir(parents=True, exist_ok=True)
    write_bundle_files(release_dir, request.get("files"))

    manifest = substitute_release_root(request.get("manifest"), release_dir)
    validate_manifest(manifest)

    release_manifest = release_dir / "pipeline.json"
    atomic_write_json(release_manifest, manifest)
    atomic_write_json(config["liveManifestPath"], manifest)

    return {
        "accepted": True,
        "strategyId": strategy_id,
        "version": version,
        "releaseDir": str(release_dir),
        "releaseManifest": str(release_manifest),
        "liveManifestPath": config["liveManifestPath"],
        "manifestName": manifest["name"],
    }


class StrategySubmitHandler(BaseHTTPRequestHandler):
    config = None

    def send_json(self, status, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/health":
            self.send_json(200, {"status": "ok"})
            return
        if self.path == "/v1/modules":
            self.send_json(200, {"modules": load_module_definitions(self.config)})
            return
        if self.path == "/v1/pipeline/instances":
            self.send_json(200, {"instances": load_state(self.config, "instances.json", {})})
            return
        if self.path == "/v1/pipeline/attachment":
            self.send_json(200, {"attachment": load_state(self.config, "attachment.json", None)})
            return
        if self.path == "/v1/strategies/current":
            path = Path(self.config["liveManifestPath"])
            if not path.exists():
                self.send_json(404, {"error": f"Live manifest does not exist: {path}"})
                return
            with path.open(encoding="utf-8") as handle:
                self.send_json(200, {"manifest": json.load(handle)})
            return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        try:
            request = read_request_json(self)
            if self.path == "/v1/strategies/submit":
                result = handle_submit(self.config, request)
            elif self.path == "/v1/modules":
                result = handle_add_module(self.config, request)
            elif self.path == "/v1/pipeline/instances":
                result = handle_create_instance(self.config, request)
            elif self.path == "/v1/pipeline/attach":
                result = handle_attach(self.config, request)
            elif self.path == "/v1/pipeline/detach":
                result = handle_detach(self.config, request)
            else:
                self.send_json(404, {"error": "Not found"})
                return
            self.send_json(200, result)
        except Exception as exc:
            self.send_json(400, {"accepted": False, "error": str(exc)})

    def do_DELETE(self):
        try:
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 6 and parts[:2] == ["v1", "modules"] and parts[4] == "versions":
                result = handle_delete_module(self.config, parts[2], parts[3], parts[5])
                self.send_json(200, result)
                return
            self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(400, {"accepted": False, "error": str(exc)})

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="Strategy submission control API.")
    parser.add_argument("--config", required=True, help="Path to control API config JSON.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args()

    StrategySubmitHandler.config = load_config(args.config)
    server = ThreadingHTTPServer((args.host, args.port), StrategySubmitHandler)
    print(f"strategy submit api listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
