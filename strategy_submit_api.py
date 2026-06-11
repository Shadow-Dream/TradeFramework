#!/usr/bin/env python3
import argparse
import base64
import fcntl
import hashlib
import json
import os
import tempfile
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
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

ARTIFACT_KINDS = {
    "Data",
    "Snapshot",
    "Checkpoint",
    "Result",
    "Report",
    "Log",
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

DEFAULT_LANE_ID = "main"
REMOTE_BACKEND_REQUIRED_FIELDS = {
    "kind",
    "moduleId",
    "version",
    "protocolVersion",
    "contractHash",
    "deploymentId",
}
FLOATING_REMOTE_DEPLOYMENTS = {"latest", "dev", "main", "master", "head", "current"}


def preset_module(kind, module_id, entry_point, hot_swap_mode, description, config_schema=None, ports=None):
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
        "ports": ports or {},
        "description": description,
        "builtin": True,
    }


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
    }


def factor_module(module_id, entry_point, description, inputs, outputs, config_schema=None):
    return preset_module(
        "Signal",
        module_id,
        entry_point,
        "Live",
        description,
        config_schema=object_schema(config_schema or {}),
        ports={
            "inputs": inputs,
            "outputs": outputs,
        },
    )


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
        "ports": {},
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
        "ports": {},
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
        "ports": {},
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
        "ports": {},
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
        "ports": {},
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
        "ports": {},
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
        "ports": {},
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
        "ports": {},
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

PRESET_MODULES.extend([
    preset_module(
        "FeeModel",
        "constant-fee",
        "QuantConnect.Orders.Fees.ConstantFeeModel",
        "RequiresPause",
        "Built-in Lean constant fee model template; config.fee controls the fixed order fee.",
        config_schema=object_schema({"fee": {"type": "number", "default": 0}}),
    ),
    preset_module(
        "FeeModel",
        "interactive-brokers-fee",
        "QuantConnect.Orders.Fees.InteractiveBrokersFeeModel",
        "RequiresPause",
        "Built-in Lean Interactive Brokers fee model template.",
    ),
    preset_module(
        "FeeModel",
        "alpaca-fee",
        "QuantConnect.Orders.Fees.AlpacaFeeModel",
        "RequiresPause",
        "Built-in Lean Alpaca fee model template.",
    ),
    preset_module(
        "FeeModel",
        "coinbase-fee",
        "QuantConnect.Orders.Fees.CoinbaseFeeModel",
        "RequiresPause",
        "Built-in Lean Coinbase fee model template.",
    ),
    preset_module(
        "FeeModel",
        "binance-fee",
        "QuantConnect.Orders.Fees.BinanceFeeModel",
        "RequiresPause",
        "Built-in Lean Binance fee model template.",
    ),
    preset_module(
        "SlippageModel",
        "null-slippage",
        "QuantConnect.Orders.Slippage.NullSlippageModel",
        "RequiresPause",
        "Built-in Lean no-slippage model template.",
    ),
    preset_module(
        "SlippageModel",
        "constant-slippage",
        "QuantConnect.Orders.Slippage.ConstantSlippageModel",
        "RequiresPause",
        "Built-in Lean constant slippage model template; config.slippagePercent controls the approximation.",
        config_schema=object_schema({"slippagePercent": {"type": "number", "default": 0}}),
    ),
    preset_module(
        "SlippageModel",
        "volume-share-slippage",
        "QuantConnect.Orders.Slippage.VolumeShareSlippageModel",
        "RequiresPause",
        "Built-in Lean volume-share slippage model template.",
    ),
    preset_module(
        "SlippageModel",
        "market-impact-slippage",
        "QuantConnect.Orders.Slippage.MarketImpactSlippageModel",
        "RequiresPause",
        "Built-in Lean market-impact slippage model template.",
    ),
    preset_module(
        "FillModel",
        "immediate-fill",
        "QuantConnect.Orders.Fills.ImmediateFillModel",
        "RequiresPause",
        "Built-in Lean immediate fill model template.",
    ),
    preset_module(
        "FillModel",
        "equity-fill",
        "QuantConnect.Orders.Fills.EquityFillModel",
        "RequiresPause",
        "Built-in Lean equity fill model template.",
    ),
    preset_module(
        "FillModel",
        "latest-price-fill",
        "QuantConnect.Orders.Fills.LatestPriceFillModel",
        "RequiresPause",
        "Built-in Lean latest-price fill model template.",
    ),
    preset_module(
        "FillModel",
        "future-fill",
        "QuantConnect.Orders.Fills.FutureFillModel",
        "RequiresPause",
        "Built-in Lean future fill model template.",
    ),
    preset_module(
        "BuyingPowerModel",
        "security-margin-buying-power",
        "QuantConnect.Securities.SecurityMarginModel",
        "RequiresPause",
        "Built-in Lean security margin buying-power model template.",
        config_schema=object_schema({
            "leverage": {"type": "number", "default": 2},
            "requiredFreeBuyingPowerPercent": {"type": "number", "default": 0},
        }),
    ),
    preset_module(
        "BuyingPowerModel",
        "cash-buying-power",
        "QuantConnect.Securities.CashBuyingPowerModel",
        "RequiresPause",
        "Built-in Lean cash buying-power model template.",
    ),
    preset_module(
        "BuyingPowerModel",
        "constant-buying-power",
        "QuantConnect.Securities.ConstantBuyingPowerModel",
        "RequiresPause",
        "Built-in Lean constant buying-power model template.",
        config_schema=object_schema({"buyingPower": {"type": "number", "default": 0}}),
    ),
    preset_module(
        "BuyingPowerModel",
        "null-buying-power",
        "QuantConnect.Securities.NullBuyingPowerModel",
        "RequiresPause",
        "Built-in Lean null buying-power model template.",
    ),
    preset_module(
        "SettlementModel",
        "immediate-settlement",
        "QuantConnect.Securities.ImmediateSettlementModel",
        "RequiresPause",
        "Built-in Lean immediate settlement model template.",
    ),
    preset_module(
        "SettlementModel",
        "delayed-settlement",
        "QuantConnect.Securities.DelayedSettlementModel",
        "RequiresPause",
        "Built-in Lean delayed settlement model template; config.days controls settlement delay.",
        config_schema=object_schema({"days": {"type": "integer", "default": 2, "minimum": 0}}),
    ),
    preset_module(
        "SettlementModel",
        "account-currency-immediate-settlement",
        "QuantConnect.Securities.AccountCurrencyImmediateSettlementModel",
        "RequiresPause",
        "Built-in Lean account-currency immediate settlement model template.",
    ),
    preset_module(
        "SettlementModel",
        "future-settlement",
        "QuantConnect.Securities.Future.FutureSettlementModel",
        "RequiresPause",
        "Built-in Lean future settlement model template.",
    ),
])

PRESET_MODULES.extend([
    factor_module(
        "price-source",
        "QuantConnect.Algorithm.Modules.PriceSourceNode",
        "Built-in factor node that publishes the current OHLCV fields for a configured symbol.",
        {},
        {
            "price": {"type": "series.price"},
            "open": {"type": "series.price.open", "required": False},
            "high": {"type": "series.price.high", "required": False},
            "low": {"type": "series.price.low", "required": False},
            "close": {"type": "series.price.close", "required": False},
            "volume": {"type": "series.volume", "required": False},
        },
        {
            "symbol": {"type": "string"},
            "priceField": {"type": "string", "default": "close"},
        },
    ),
    factor_module(
        "sma-indicator",
        "QuantConnect.Algorithm.Modules.SimpleMovingAverageNode",
        "Built-in SMA factor node. Config controls period; inputs/outputs bind graph wire ids.",
        {"value": {"type": "series.number"}},
        {"sma": {"type": "series.indicator.sma"}},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    factor_module(
        "ema-indicator",
        "QuantConnect.Algorithm.Modules.ExponentialMovingAverageNode",
        "Built-in EMA factor node. Config controls period; inputs/outputs bind graph wire ids.",
        {"value": {"type": "series.number"}},
        {"ema": {"type": "series.indicator.ema"}},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    factor_module(
        "wma-indicator",
        "QuantConnect.Algorithm.Modules.WeightedMovingAverageNode",
        "Built-in weighted moving average factor node.",
        {"value": {"type": "series.number"}},
        {"wma": {"type": "series.indicator.wma"}},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    factor_module(
        "vwma-indicator",
        "QuantConnect.Algorithm.Modules.VolumeWeightedMovingAverageNode",
        "Built-in volume-weighted moving average factor node.",
        {
            "price": {"type": "series.price"},
            "volume": {"type": "series.volume"},
        },
        {"vwma": {"type": "series.indicator.vwma"}},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    factor_module(
        "rsi-indicator",
        "QuantConnect.Algorithm.Modules.RelativeStrengthIndexNode",
        "Built-in RSI factor node.",
        {"price": {"type": "series.price"}},
        {"rsi": {"type": "series.indicator.rsi"}},
        {"period": {"type": "integer", "default": 14, "minimum": 1}},
    ),
    factor_module(
        "macd-indicator",
        "QuantConnect.Algorithm.Modules.MacdNode",
        "Built-in MACD factor node with macd, signal, and histogram outputs.",
        {"price": {"type": "series.price"}},
        {
            "macd": {"type": "series.indicator.macd"},
            "signal": {"type": "series.indicator.macd.signal", "required": False},
            "histogram": {"type": "series.indicator.macd.histogram", "required": False},
        },
        {
            "fastPeriod": {"type": "integer", "default": 12, "minimum": 1},
            "slowPeriod": {"type": "integer", "default": 26, "minimum": 1},
            "signalPeriod": {"type": "integer", "default": 9, "minimum": 1},
        },
    ),
    factor_module(
        "bollinger-bands-indicator",
        "QuantConnect.Algorithm.Modules.BollingerBandsNode",
        "Built-in Bollinger Bands factor node.",
        {"price": {"type": "series.price"}},
        {
            "middle": {"type": "series.indicator.bollinger.middle"},
            "upper": {"type": "series.indicator.bollinger.upper", "required": False},
            "lower": {"type": "series.indicator.bollinger.lower", "required": False},
            "bandwidth": {"type": "series.indicator.bollinger.bandwidth", "required": False},
            "percentB": {"type": "series.indicator.bollinger.percentB", "required": False},
        },
        {
            "period": {"type": "integer", "default": 20, "minimum": 1},
            "k": {"type": "number", "default": 2},
        },
    ),
    factor_module(
        "atr-indicator",
        "QuantConnect.Algorithm.Modules.AverageTrueRangeNode",
        "Built-in ATR factor node.",
        {
            "high": {"type": "series.price.high"},
            "low": {"type": "series.price.low"},
            "close": {"type": "series.price.close"},
        },
        {"atr": {"type": "series.indicator.atr"}},
        {"period": {"type": "integer", "default": 14, "minimum": 1}},
    ),
    factor_module(
        "stochastic-indicator",
        "QuantConnect.Algorithm.Modules.StochasticNode",
        "Built-in stochastic oscillator factor node.",
        {
            "high": {"type": "series.price.high"},
            "low": {"type": "series.price.low"},
            "close": {"type": "series.price.close"},
        },
        {
            "k": {"type": "series.indicator.stochastic.k"},
            "d": {"type": "series.indicator.stochastic.d", "required": False},
        },
        {
            "period": {"type": "integer", "default": 14, "minimum": 1},
            "dPeriod": {"type": "integer", "default": 3, "minimum": 1},
        },
    ),
    factor_module(
        "obv-indicator",
        "QuantConnect.Algorithm.Modules.OnBalanceVolumeNode",
        "Built-in OBV factor node.",
        {
            "close": {"type": "series.price.close"},
            "volume": {"type": "series.volume"},
        },
        {"obv": {"type": "series.indicator.obv"}},
        {},
    ),
    factor_module(
        "roc-indicator",
        "QuantConnect.Algorithm.Modules.RateOfChangeNode",
        "Built-in rate-of-change factor node.",
        {"price": {"type": "series.price"}},
        {"roc": {"type": "series.indicator.roc"}},
        {"period": {"type": "integer", "default": 12, "minimum": 1}},
    ),
    factor_module(
        "cross-over-gate",
        "QuantConnect.Algorithm.Modules.CrossOverGateNode",
        "Built-in logic node that emits rise/fall/flat when two numeric inputs cross.",
        {
            "fast": {"type": "series.number"},
            "slow": {"type": "series.number"},
        },
        {"direction": {"type": "signal.direction"}},
        {},
    ),
    factor_module(
        "graph-output",
        "QuantConnect.Algorithm.Modules.GraphOutputNode",
        "Built-in graph output node. It behaves as an independent output module and keeps its input as null until connected.",
        {"value": {"type": "any", "required": False}},
        {},
        {"dataKey": {"type": "string", "default": ""}},
    ),
    factor_module(
        "direction-to-price-insight",
        "QuantConnect.Algorithm.Modules.DirectionToPriceInsightNode",
        "Built-in bridge node that converts a direction signal into Lean price insights.",
        {"direction": {"type": "signal.direction"}},
        {"insights": {"type": "insight.list"}},
        {
            "symbol": {"type": "string"},
            "periodDays": {"type": "integer", "default": 1, "minimum": 1},
        },
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
        "lanesManifestPath": str(Path(config.get(
            "lanesManifestPath",
            Path(live_manifest).expanduser().parent / "lanes-manifest.json",
        )).expanduser()),
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


def canonical_artifact_kind(kind):
    for candidate in ARTIFACT_KINDS:
        if str(kind or "").lower() == candidate.lower():
            return candidate
    raise ValueError(f"Invalid artifact kind: {kind}")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp_iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def json_digest(payload):
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def reject_path(path):
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Invalid bundle file path: {path}")
    if not str(candidate):
        raise ValueError("Bundle file path cannot be empty.")
    return candidate


def write_bundle_files(release_dir, files):
    written = []
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
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if expected_sha256:
            if actual_sha256.lower() != expected_sha256.lower():
                raise ValueError(f"Bundle file '{relative}' sha256 mismatch: expected {expected_sha256}, got {actual_sha256}.")

        target.write_bytes(data)
        if item.get("executable"):
            target.chmod(target.stat().st_mode | 0o111)
        summary = {
            "path": str(relative),
            "sizeBytes": len(data),
            "sha256": actual_sha256,
            "executable": bool(item.get("executable")),
        }
        if source_url:
            summary["sourceUrl"] = source_url
        written.append(summary)
    return written


def summarize_bundle_file_spec(item):
    summary = {
        "path": item.get("path", ""),
        "executable": bool(item.get("executable")),
    }
    if item.get("sourceUrl"):
        summary["sourceUrl"] = item.get("sourceUrl")
    if item.get("sha256"):
        summary["sha256"] = item.get("sha256")
    if item.get("sizeBytes") is not None:
        summary["sizeBytes"] = item.get("sizeBytes")
    content = item.get("contentBase64")
    if content is not None:
        try:
            data = base64.b64decode(content)
            summary["sizeBytes"] = len(data)
            summary["sha256"] = hashlib.sha256(data).hexdigest()
        except Exception:
            summary["contentBase64Present"] = True
    return summary


def summarize_bundle_files(files):
    return [summarize_bundle_file_spec(item) for item in files or []]


def sanitize_artifact_record(record):
    result = dict(record or {})
    result["files"] = summarize_bundle_files(result.get("files") or [])
    return result


def sanitize_artifacts(artifacts):
    return {
        key: sanitize_artifact_record(value)
        for key, value in (artifacts or {}).items()
    }


def sanitize_history_event(event):
    result = dict(event or {})
    payload = result.get("payload")
    if result.get("type") == "artifact.recorded":
        result["payload"] = sanitize_artifact_record(payload)
    elif isinstance(payload, dict):
        compact = {}
        keep_keys = [
            "accepted",
            "status",
            "laneId",
            "strategyId",
            "version",
            "name",
            "moduleId",
            "instanceId",
            "packageId",
            "artifactId",
            "iterationId",
            "backtestId",
            "datasetId",
            "source",
            "symbol",
            "interval",
            "rowCount",
            "manifestHash",
            "liveManifestPath",
            "releaseManifest",
        ]
        for key in keep_keys:
            if key in payload:
                compact[key] = payload[key]
        if isinstance(payload.get("dataset"), dict):
            dataset = payload["dataset"]
            compact.update({
                "datasetId": dataset.get("datasetId"),
                "symbol": dataset.get("symbol"),
                "source": dataset.get("source"),
                "interval": dataset.get("interval"),
                "rowCount": dataset.get("rowCount"),
            })
        if isinstance(payload.get("backtest"), dict):
            backtest = payload["backtest"]
            compact.update({
                "backtestId": backtest.get("backtestId"),
                "laneId": backtest.get("laneId"),
                "datasetId": backtest.get("datasetId"),
                "status": backtest.get("status"),
                "runner": backtest.get("runner"),
                "metrics": backtest.get("metrics"),
            })
        if isinstance(payload.get("iteration"), dict):
            iteration = payload["iteration"]
            compact.update({
                "iterationId": iteration.get("iterationId"),
                "operation": iteration.get("operation"),
                "manifestHash": iteration.get("manifestHash"),
            })
        if isinstance(payload.get("attachment"), dict):
            attachment = payload["attachment"]
            compact.update({
                "laneId": attachment.get("laneId", compact.get("laneId")),
                "strategyId": attachment.get("strategyId", compact.get("strategyId")),
                "version": attachment.get("version", compact.get("version")),
                "name": attachment.get("name", compact.get("name")),
            })
        result["payload"] = {key: value for key, value in compact.items() if value not in (None, "", [], {})}
    return result


def state_path(config, name):
    root = Path(config["controlRoot"])
    root.mkdir(parents=True, exist_ok=True)
    return root / name


@contextmanager
def control_state_lock(config):
    lock_path = state_path(config, ".control.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def normalize_lane_id(value):
    lane_id = str(value or DEFAULT_LANE_ID).strip()
    if not lane_id:
        lane_id = DEFAULT_LANE_ID
    reject_path(lane_id)
    return lane_id


def lane_state_name(lane_id, name):
    return str(Path("lanes") / normalize_lane_id(lane_id) / name)


def lane_live_manifest_path(config, lane_id):
    lane_id = normalize_lane_id(lane_id)
    return str(Path(config["liveManifestPath"]).parent / "lanes" / lane_id / "pipeline.json")


def load_lane_attachment(config, lane_id):
    lane_id = normalize_lane_id(lane_id)
    if lane_id == DEFAULT_LANE_ID:
        lane_attachment = load_state(config, lane_state_name(lane_id, "attachment.json"), None)
        attachment = lane_attachment or load_state(config, "attachment.json", None)
        return sync_signal_stage_with_alpha_graph(attachment) if attachment else None
    attachment = load_state(config, lane_state_name(lane_id, "attachment.json"), None)
    return sync_signal_stage_with_alpha_graph(attachment) if attachment else None


def save_lane_attachment(config, lane_id, attachment):
    lane_id = normalize_lane_id(lane_id)
    attachment = sync_signal_stage_with_alpha_graph(attachment)
    save_state(config, lane_state_name(lane_id, "attachment.json"), attachment)
    if lane_id == DEFAULT_LANE_ID:
        save_state(config, "attachment.json", attachment)


def load_lanes(config):
    lanes = load_state(config, "lanes.json", {})
    if lanes:
        return lanes

    attachment = load_state(config, "attachment.json", None)
    manifest = load_json_file(config["liveManifestPath"], None)
    if not attachment or not manifest:
        return {}

    return {
        DEFAULT_LANE_ID: {
            "schemaVersion": 1,
            "laneId": DEFAULT_LANE_ID,
            "status": "active",
            "strategyId": attachment.get("strategyId"),
            "version": attachment.get("version"),
            "name": attachment.get("name") or manifest.get("name"),
            "liveManifestPath": config["liveManifestPath"],
            "releaseManifest": config["liveManifestPath"],
            "iterationId": "",
            "manifestHash": json_digest(manifest),
            "updatedAt": timestamp_iso(Path(config["liveManifestPath"]).stat().st_mtime),
        }
    }


def save_lanes(config, lanes):
    save_state(config, "lanes.json", lanes)
    refresh_engine_lanes_manifest(config, lanes)


def refresh_engine_lanes_manifest(config, lanes=None):
    lanes = lanes if lanes is not None else load_lanes(config)
    records = []
    for lane_id in sorted(lanes):
        lane = lanes[lane_id]
        if lane.get("status") == "active" and lane.get("liveManifestPath"):
            records.append({
                "laneId": lane_id,
                "name": lane.get("name") or lane_id,
                "strategyId": lane.get("strategyId"),
                "version": lane.get("version"),
                "manifestPath": lane["liveManifestPath"],
                "manifestHash": lane.get("manifestHash", ""),
                "updatedAt": lane.get("updatedAt", ""),
            })
    payload = {
        "schemaVersion": 1,
        "updatedAt": utc_now(),
        "lanes": records,
    }
    current = load_json_file(config["lanesManifestPath"], None)
    if current and current.get("lanes") == records:
        return
    atomic_write_json(config["lanesManifestPath"], payload)


def update_lane_record(config, lane_id, attachment, manifest, release_manifest, iteration, status="active"):
    lane_id = normalize_lane_id(lane_id)
    lanes = load_lanes(config)
    lanes[lane_id] = {
        "schemaVersion": 1,
        "laneId": lane_id,
        "status": status,
        "strategyId": attachment.get("strategyId"),
        "version": attachment.get("version"),
        "name": attachment.get("name") or f"{attachment.get('strategyId')}-{attachment.get('version')}",
        "liveManifestPath": lane_live_manifest_path(config, lane_id),
        "releaseManifest": str(release_manifest),
        "iterationId": iteration.get("iterationId") if iteration else "",
        "manifestHash": json_digest(manifest),
        "updatedAt": utc_now(),
    }
    save_lanes(config, lanes)
    return lanes[lane_id]


def append_history_event(config, event_type, payload):
    event = {
        "schemaVersion": 1,
        "timestamp": utc_now(),
        "type": event_type,
        "payload": payload,
    }
    event["id"] = f"{event['timestamp'].replace(':', '').replace('.', '')}-{json_digest(event)[:12]}"

    path = state_path(config, "events.jsonl")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
    return event


def load_history_events(config, limit=100):
    path = state_path(config, "events.jsonl")
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    if limit and limit > 0:
        return events[-limit:]
    return events


def load_sanitized_history_events(config, limit=100):
    return [sanitize_history_event(event) for event in load_history_events(config, limit)]


def append_iteration(config, iteration):
    iterations = load_state(config, "iterations.json", [])
    iterations.append(iteration)
    save_state(config, "iterations.json", iterations)


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


def package_version_dir(config, package_id, version):
    return Path(config["releaseRoot"]) / "_packages" / reject_path(package_id) / reject_path(version)


def artifact_dir(config, kind, artifact_id):
    return Path(config["releaseRoot"]) / "_artifacts" / reject_path(kind) / reject_path(artifact_id)


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


def substitute_package_root(value, package_dir):
    if isinstance(value, str):
        return value.replace("{{packageRoot}}", str(package_dir))
    if isinstance(value, list):
        return [substitute_package_root(item, package_dir) for item in value]
    if isinstance(value, dict):
        return {key: substitute_package_root(item, package_dir) for key, item in value.items()}
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
        if activation == "RemoteService":
            validate_remote_service_backend(key, kind, module.get("moduleId"), module.get("version"), parameters)
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


def normalize_ports(ports):
    ports = ports or {}
    if not isinstance(ports, dict):
        raise ValueError("ports must be an object.")

    result = {"inputs": {}, "outputs": {}}
    for direction in ("inputs", "outputs"):
        values = ports.get(direction) or {}
        if not isinstance(values, dict):
            raise ValueError(f"ports.{direction} must be an object.")
        for name, spec in values.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"ports.{direction} contains an invalid port name.")
            if spec is None:
                spec = {}
            if not isinstance(spec, dict):
                raise ValueError(f"ports.{direction}.{name} must be an object.")
            port_type = spec.get("type", "any")
            if not isinstance(port_type, str) or not port_type:
                raise ValueError(f"ports.{direction}.{name}.type must be a non-empty string.")
            normalized = dict(spec)
            normalized["type"] = port_type
            if "required" not in normalized:
                normalized["required"] = True
            if not isinstance(normalized["required"], bool):
                raise ValueError(f"ports.{direction}.{name}.required must be a boolean.")
            result[direction][name] = normalized
    return result


def port_type_is_compatible(output_type, input_type):
    if input_type == "any" or output_type == "any":
        return True
    if output_type == input_type:
        return True
    if input_type == "series.number" and output_type.startswith((
        "series.price",
        "series.volume",
        "series.indicator",
    )):
        return True
    return output_type.startswith(input_type + ".")


def validate_remote_service_backend(label, kind, module_id, version, parameters):
    if not parameters.get("baseUrl"):
        raise ValueError(f"RemoteService module '{label}' requires parameters.baseUrl.")

    backend = parameters.get("backend")
    if not isinstance(backend, dict):
        raise ValueError(
            f"RemoteService module '{label}' requires parameters.backend with pinned version metadata."
        )

    missing = sorted(name for name in REMOTE_BACKEND_REQUIRED_FIELDS if not backend.get(name))
    if missing:
        raise ValueError(
            f"RemoteService module '{label}' parameters.backend missing required field(s): {', '.join(missing)}."
        )

    if backend["kind"] != kind:
        raise ValueError(f"RemoteService module '{label}' backend.kind must be '{kind}'.")
    if module_id and backend["moduleId"] != module_id:
        raise ValueError(f"RemoteService module '{label}' backend.moduleId must be '{module_id}'.")
    if version and backend["version"] != version:
        raise ValueError(f"RemoteService module '{label}' backend.version must be '{version}'.")

    contract_hash = backend.get("contractHash", "")
    if not isinstance(contract_hash, str) or not contract_hash.startswith("sha256:") or len(contract_hash) != 71:
        raise ValueError(
            f"RemoteService module '{label}' backend.contractHash must use sha256:<64-hex> format."
        )
    try:
        int(contract_hash.removeprefix("sha256:"), 16)
    except ValueError as exc:
        raise ValueError(
            f"RemoteService module '{label}' backend.contractHash must use sha256:<64-hex> format."
        ) from exc

    deployment_id = str(backend.get("deploymentId", "")).strip()
    if deployment_id.lower() in FLOATING_REMOTE_DEPLOYMENTS or deployment_id.lower().endswith(":latest"):
        raise ValueError(
            f"RemoteService module '{label}' backend.deploymentId must be immutable, not '{deployment_id}'."
        )


def validate_instance_wiring(instance, definition):
    ports = normalize_ports(definition.get("ports") or {})
    inputs = instance.get("inputs") or {}
    outputs = instance.get("outputs") or {}
    if not isinstance(inputs, dict):
        raise ValueError("instance inputs must be an object.")
    if not isinstance(outputs, dict):
        raise ValueError("instance outputs must be an object.")

    for name in inputs:
        if name not in ports["inputs"]:
            raise ValueError(f"Instance '{instance['instanceId']}' binds unknown input port '{name}'.")
    for name in outputs:
        if name not in ports["outputs"]:
            raise ValueError(f"Instance '{instance['instanceId']}' binds unknown output port '{name}'.")

    for name, spec in ports["inputs"].items():
        if spec.get("required", True) and not inputs.get(name):
            raise ValueError(f"Instance '{instance['instanceId']}' requires input port '{name}'.")
    for name, spec in ports["outputs"].items():
        if spec.get("required", False) and not outputs.get(name):
            raise ValueError(f"Instance '{instance['instanceId']}' requires output port '{name}'.")

    for direction, values in (("inputs", inputs), ("outputs", outputs)):
        for name, wire_id in values.items():
            if not isinstance(wire_id, str) or not wire_id:
                raise ValueError(f"Instance '{instance['instanceId']}' {direction}.{name} must be a non-empty wire id.")


def normalize_module_instance(config, request, *, status="loaded"):
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
        "inputs": request.get("inputs") or {},
        "outputs": request.get("outputs") or {},
        "hotSwapMode": request.get("hotSwapMode") or definition.get("hotSwapMode", "RequiresPause"),
        "status": status,
    }
    if instance["hotSwapMode"] not in HOT_SWAP_MODES:
        raise ValueError(f"Instance '{instance_id}' has invalid hotSwapMode '{instance['hotSwapMode']}'.")
    if not isinstance(instance["config"], dict):
        raise ValueError("instance config must be an object.")
    if not isinstance(instance["parameters"], dict):
        raise ValueError("instance parameters must be an object.")
    validate_instance_wiring(instance, definition)
    return instance


def normalize_attachment_instances(config, instances):
    instances = instances or {}
    if not isinstance(instances, dict):
        raise ValueError("instances must be an object keyed by instanceId.")
    normalized = {}
    for key, item in instances.items():
        if not isinstance(item, dict):
            raise ValueError(f"instances.{key} must be an object.")
        payload = dict(item)
        payload.setdefault("instanceId", key)
        instance = normalize_module_instance(config, payload, status=payload.get("status") or "loaded")
        if instance["instanceId"] != key:
            raise ValueError(f"instances key '{key}' must match instanceId '{instance['instanceId']}'.")
        normalized[key] = instance
    return normalized


def attachment_instances(config, attachment):
    global_instances = load_state(config, "instances.json", {})
    local_instances = normalize_attachment_instances(config, attachment.get("instances") or {})
    return {**global_instances, **local_instances}


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
    if activation == "RemoteService":
        validate_remote_service_backend(module_id, kind, module_id, version, parameters)
    if activation in {"ScriptRunner", "OutOfProcessWorker"} and not parameters.get("command"):
        raise ValueError(f"{activation} module '{module_id}' requires parameters.command.")
    if activation == "InProcessPlugin" and not parameters.get("assemblyPath"):
        raise ValueError(f"InProcessPlugin module '{module_id}' requires parameters.assemblyPath.")
    definition["ports"] = normalize_ports(definition.get("ports") or {})


def handle_add_module(config, request):
    kind = request.get("kind")
    module_id = request.get("moduleId")
    version = request.get("version")
    if not kind or not module_id or not version:
        raise ValueError("kind, moduleId and version are required.")
    if is_preset_definition(kind, module_id, version):
        raise ValueError(f"Cannot overwrite built-in module definition: {definition_key(kind, module_id, version)}")

    definitions = load_state(config, "modules.json", {})
    key = definition_key(kind, module_id, version)
    if key in definitions:
        raise ValueError(f"Module definition already exists: {key}. Use a new version for a new implementation.")
    if any(item.get("moduleKey") == key for item in load_state(config, "deleted-modules.json", [])):
        raise ValueError(f"Module definition was previously deleted: {key}. Use a new version to preserve history.")

    module_dir = module_version_dir(config, kind, module_id, version)
    module_dir.mkdir(parents=True, exist_ok=True)
    written_files = write_bundle_files(module_dir, request.get("files"))

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
        "ports": request.get("ports") or {},
        "description": request.get("description", ""),
    }
    validate_module_definition(definition)

    definitions[key] = definition
    save_state(config, "modules.json", definitions)
    atomic_write_json(module_dir / "module.json", definition)
    append_history_event(config, "module.added", {
        "moduleKey": key,
        "moduleDir": str(module_dir),
        "definition": definition,
        "artifactPaths": [item.get("path") for item in written_files],
    })

    return {
        "accepted": True,
        "moduleKey": key,
        "moduleDir": str(module_dir),
        "definition": definition,
    }


def handle_add_package(config, request):
    package_id = request.get("packageId")
    version = request.get("version")
    modules = request.get("modules") or []
    if not package_id or not version:
        raise ValueError("packageId and version are required.")
    if not isinstance(modules, list) or not modules:
        raise ValueError("modules must be a non-empty array.")
    reject_path(package_id)
    reject_path(version)

    package_key = f"{package_id}/{version}"
    packages = load_state(config, "packages.json", {})
    if package_key in packages:
        raise ValueError(f"Module package already exists: {package_key}. Use a new version for a new package.")

    definitions = load_state(config, "modules.json", {})
    deleted_modules = load_state(config, "deleted-modules.json", [])
    package_dir = package_version_dir(config, package_id, version)
    module_records = []

    for item in modules:
        kind = item.get("kind")
        module_id = item.get("moduleId")
        module_version = item.get("version") or version
        if not kind or not module_id:
            raise ValueError("Each package module requires kind and moduleId.")
        if is_preset_definition(kind, module_id, module_version):
            raise ValueError(f"Cannot overwrite built-in module definition: {definition_key(kind, module_id, module_version)}")

        key = definition_key(kind, module_id, module_version)
        if key in definitions:
            raise ValueError(f"Module definition already exists: {key}. Use a new version for a new implementation.")
        if any(deleted.get("moduleKey") == key for deleted in deleted_modules):
            raise ValueError(f"Module definition was previously deleted: {key}. Use a new version to preserve history.")

        module_dir = module_version_dir(config, kind, module_id, module_version)
        parameters = substitute_package_root(item.get("parameters") or {}, package_dir)
        parameters = substitute_module_root(parameters, module_dir)
        definition = {
            "kind": kind,
            "moduleId": module_id,
            "version": module_version,
            "activationMode": item.get("activationMode"),
            "entryPoint": item.get("entryPoint", ""),
            "hotSwapMode": item.get("hotSwapMode", "RequiresPause"),
            "parameters": parameters,
            "dependencies": item.get("dependencies") or [],
            "configSchema": item.get("configSchema") or {},
            "ports": item.get("ports") or {},
            "description": item.get("description", ""),
            "package": {
                "packageId": package_id,
                "version": version,
                "packageDir": str(package_dir),
            },
        }
        validate_module_definition(definition)
        module_records.append((key, module_dir, definition))

    package_dir.mkdir(parents=True, exist_ok=True)
    written_files = write_bundle_files(package_dir, request.get("files"))
    package_record = {
        "schemaVersion": 1,
        "packageId": package_id,
        "version": version,
        "packageKey": package_key,
        "createdAt": utc_now(),
        "packageDir": str(package_dir),
        "files": written_files,
        "moduleKeys": [key for key, _, _ in module_records],
        "metadata": request.get("metadata") or {},
    }
    if not isinstance(package_record["metadata"], dict):
        raise ValueError("package metadata must be an object.")

    for key, module_dir, definition in module_records:
        module_dir.mkdir(parents=True, exist_ok=True)
        definitions[key] = definition
        atomic_write_json(module_dir / "module.json", definition)

    packages[package_key] = package_record
    save_state(config, "modules.json", definitions)
    save_state(config, "packages.json", packages)
    atomic_write_json(package_dir / "package.json", {
        **package_record,
        "modules": [definition for _, _, definition in module_records],
    })
    append_history_event(config, "package.added", {
        "package": package_record,
        "modules": [definition for _, _, definition in module_records],
    })

    return {
        "accepted": True,
        "packageKey": package_key,
        "packageDir": str(package_dir),
        "files": written_files,
        "modules": [definition for _, _, definition in module_records],
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
    tombstones = load_state(config, "deleted-modules.json", [])
    tombstone = {
        "moduleKey": key,
        "deletedAt": utc_now(),
        "moduleDir": str(module_version_dir(config, kind, module_id, version)),
        "definition": definition,
    }
    tombstones.append(tombstone)
    save_state(config, "deleted-modules.json", tombstones)
    append_history_event(config, "module.deleted", tombstone)
    return {
        "accepted": True,
        "deleted": key,
        "definition": definition,
    }


def handle_create_instance(config, request):
    instance = normalize_module_instance(config, request, status="created")
    instance_id = instance["instanceId"]

    instances = load_state(config, "instances.json", {})
    previous = instances.get(instance_id)
    instances[instance_id] = instance
    save_state(config, "instances.json", instances)
    append_history_event(config, "instance.saved", {
        "instanceId": instance_id,
        "previous": previous,
        "instance": instance,
    })
    return {
        "accepted": True,
        "instance": instance,
    }


def handle_record_artifact(config, request):
    kind = canonical_artifact_kind(request.get("kind"))
    artifact_id = request.get("artifactId")
    if not artifact_id:
        raise ValueError("artifactId is required.")
    reject_path(artifact_id)

    target_dir = artifact_dir(config, kind, artifact_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    written_files = write_bundle_files(target_dir, request.get("files"))

    record = {
        "schemaVersion": 1,
        "kind": kind,
        "artifactId": artifact_id,
        "createdAt": utc_now(),
        "strategyId": request.get("strategyId", ""),
        "iterationId": request.get("iterationId", ""),
        "moduleId": request.get("moduleId", ""),
        "instanceId": request.get("instanceId", ""),
        "version": request.get("version", ""),
        "metadata": request.get("metadata") or {},
        "payload": request.get("payload") or {},
        "artifactDir": str(target_dir),
        "files": written_files,
    }
    if not isinstance(record["metadata"], dict):
        raise ValueError("artifact metadata must be an object.")
    if not isinstance(record["payload"], dict):
        raise ValueError("artifact payload must be an object.")

    key = f"{kind}/{artifact_id}"
    artifacts = load_state(config, "artifacts.json", {})
    if key in artifacts:
        raise ValueError(f"Artifact already exists: {key}. Use a new artifactId to preserve history.")
    artifacts[key] = record
    save_state(config, "artifacts.json", artifacts)
    atomic_write_json(target_dir / "artifact.json", record)
    append_history_event(config, "artifact.recorded", record)

    return {
        "accepted": True,
        "artifactKey": key,
        "artifact": record,
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


def active_instance_ids(attachment):
    stages = normalize_stage_references(attachment.get("stages") or {})
    ids = collect_attachment_instance_ids(stages, attachment.get("market") or {})
    ids.extend((attachment.get("alphaGraph") or {}).get("nodes") or [])
    if attachment.get("marketRule"):
        ids.append(attachment["marketRule"])
    return ids


def sync_signal_stage_with_alpha_graph(attachment):
    if not attachment:
        return attachment
    next_attachment = dict(attachment)
    alpha_graph = normalize_alpha_graph(next_attachment.get("alphaGraph") or {})
    stages = normalize_stage_references(next_attachment.get("stages") or {})
    instances = next_attachment.get("instances") or {}
    stages["signal"] = [
        node_id
        for node_id in dict.fromkeys(alpha_graph.get("nodes") or [])
        if (instances.get(node_id) or {}).get("moduleId") != "graph-output"
    ]
    next_attachment["alphaGraph"] = alpha_graph
    next_attachment["stages"] = stages
    return next_attachment


def collect_data_dependencies(attachment, instances):
    stages = normalize_stage_references(attachment.get("stages") or {})
    dependencies = []
    for instance_id in stages.get("inputs", []):
        instance = instances.get(instance_id)
        if not instance:
            continue
        dependencies.append({
            "instanceId": instance_id,
            "moduleId": instance.get("moduleId"),
            "version": instance.get("version"),
            "config": instance.get("config") or {},
            "inputs": instance.get("inputs") or {},
            "outputs": instance.get("outputs") or {},
        })
    return dependencies


def attachment_snapshot(config, attachment, manifest):
    definitions = load_module_definitions(config)
    instances = attachment_instances(config, attachment)
    ids = active_instance_ids(attachment)
    module_keys = set()
    active_instances = {}
    active_definitions = {}

    for instance_id in ids:
        instance = instances.get(instance_id)
        if not instance:
            continue
        active_instances[instance_id] = instance
        key = definition_key(instance["kind"], instance["moduleId"], instance["version"])
        module_keys.add(key)
        definition = definitions.get(key)
        if definition:
            active_definitions[key] = definition

    return {
        "schemaVersion": 1,
        "createdAt": utc_now(),
        "attachment": attachment,
        "manifest": manifest,
        "manifestHash": json_digest(manifest),
        "activeInstanceIds": ids,
        "activeInstances": active_instances,
        "activeModuleDefinitions": active_definitions,
        "dataDependencies": collect_data_dependencies(attachment, instances),
    }


def persist_attachment_iteration(config, attachment, manifest, release_dir, operation):
    snapshot = attachment_snapshot(config, attachment, manifest)
    release_dir = Path(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(release_dir / "pipeline.json", manifest)
    atomic_write_json(release_dir / "attachment.json", attachment)
    atomic_write_json(release_dir / "control-snapshot.json", snapshot)

    iteration = {
        "schemaVersion": 1,
        "iterationId": json_digest({
            "operation": operation,
            "laneId": attachment.get("laneId"),
            "attachment": attachment,
            "manifestHash": snapshot["manifestHash"],
            "createdAt": snapshot["createdAt"],
        }),
        "createdAt": snapshot["createdAt"],
        "operation": operation,
        "laneId": attachment.get("laneId") or DEFAULT_LANE_ID,
        "strategyId": attachment.get("strategyId"),
        "version": attachment.get("version"),
        "name": attachment.get("name"),
        "releaseDir": str(release_dir),
        "releaseManifest": str(release_dir / "pipeline.json"),
        "manifestHash": snapshot["manifestHash"],
        "activeInstanceIds": snapshot["activeInstanceIds"],
        "dataDependencies": snapshot["dataDependencies"],
    }
    append_iteration(config, iteration)
    append_history_event(config, f"pipeline.{operation}", {
        "iteration": iteration,
        "attachment": attachment,
        "manifest": manifest,
    })
    return iteration


def normalize_alpha_graph(alpha_graph):
    alpha_graph = alpha_graph or {}
    if not isinstance(alpha_graph, dict):
        raise ValueError("alphaGraph must be an object.")
    nodes = alpha_graph.get("nodes") or []
    if not isinstance(nodes, list):
        raise ValueError("alphaGraph.nodes must be an array.")
    for node in nodes:
        if not isinstance(node, str) or not node:
            raise ValueError("alphaGraph.nodes must contain non-empty instance ids.")

    outputs = alpha_graph.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise ValueError("alphaGraph.outputs must be an object.")
    for name, wire_ids in outputs.items():
        if not isinstance(name, str) or not name:
            raise ValueError("alphaGraph.outputs contains an invalid output name.")
        if not isinstance(wire_ids, list) or not all(isinstance(x, str) and x for x in wire_ids):
            raise ValueError(f"alphaGraph.outputs.{name} must be an array of wire ids.")

    return {
        "nodes": nodes,
        "outputs": outputs,
    }


def validate_alpha_graph(alpha_graph, instances, definitions):
    alpha_graph = normalize_alpha_graph(alpha_graph)
    nodes = alpha_graph["nodes"]
    if not nodes:
        return alpha_graph

    node_set = set()
    for node_id in nodes:
        if node_id in node_set:
            raise ValueError(f"alphaGraph contains duplicate node '{node_id}'.")
        node_set.add(node_id)
        instance = instances.get(node_id)
        if instance is None:
            raise ValueError(f"alphaGraph references unknown instance '{node_id}'.")
        if instance.get("kind") != "Signal":
            raise ValueError(f"alphaGraph node '{node_id}' must be a Signal instance.")
        get_definition(definitions, instance["kind"], instance["moduleId"], instance["version"])

    producers = {}
    bindings = {}
    for node_id in nodes:
        instance = instances[node_id]
        definition = get_definition(definitions, instance["kind"], instance["moduleId"], instance["version"])
        ports = normalize_ports(definition.get("ports") or {})
        validate_instance_wiring(instance, definition)
        bindings[node_id] = {
            "instanceId": node_id,
            "kind": instance["kind"],
            "moduleId": instance["moduleId"],
            "version": instance["version"],
            "config": instance.get("config") or {},
            "inputs": instance.get("inputs") or {},
            "outputs": instance.get("outputs") or {},
            "ports": ports,
        }
        for port_name, wire_id in (instance.get("outputs") or {}).items():
            if wire_id in producers:
                previous = producers[wire_id]
                raise ValueError(
                    f"alphaGraph wire '{wire_id}' has multiple producers: "
                    f"{previous['node']}.{previous['port']} and {node_id}.{port_name}"
                )
            producers[wire_id] = {
                "node": node_id,
                "port": port_name,
                "type": ports["outputs"][port_name]["type"],
            }

    dependencies = {node_id: set() for node_id in nodes}
    edges = []
    for node_id in nodes:
        instance = instances[node_id]
        definition = get_definition(definitions, instance["kind"], instance["moduleId"], instance["version"])
        ports = normalize_ports(definition.get("ports") or {})
        for port_name, wire_id in (instance.get("inputs") or {}).items():
            producer = producers.get(wire_id)
            if producer is None:
                continue
            input_type = ports["inputs"][port_name]["type"]
            if not port_type_is_compatible(producer["type"], input_type):
                raise ValueError(
                    f"alphaGraph wire '{wire_id}' type mismatch: "
                    f"{producer['node']}.{producer['port']} outputs '{producer['type']}', "
                    f"but {node_id}.{port_name} requires '{input_type}'."
                )
            dependencies[node_id].add(producer["node"])
            edges.append({
                "wire": wire_id,
                "from": {
                    "node": producer["node"],
                    "port": producer["port"],
                    "type": producer["type"],
                },
                "to": {
                    "node": node_id,
                    "port": port_name,
                    "type": input_type,
                },
            })

    for name, wire_ids in alpha_graph["outputs"].items():
        for wire_id in wire_ids:
            if wire_id not in producers:
                raise ValueError(f"alphaGraph output '{name}' references unknown wire '{wire_id}'.")

    visiting = set()
    visited = set()

    def visit(node_id):
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError(f"alphaGraph contains a cycle involving node '{node_id}'.")
        visiting.add(node_id)
        for dependency in dependencies[node_id]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)

    alpha_graph["bindings"] = bindings
    alpha_graph["edges"] = edges
    return alpha_graph


def compile_attachment_manifest(config, attachment):
    attachment = sync_signal_stage_with_alpha_graph(attachment)
    definitions = load_module_definitions(config)
    instances = attachment_instances(config, attachment)
    stages = normalize_stage_references(attachment.get("stages") or {})
    market = attachment.get("market") or {}
    alpha_graph = validate_alpha_graph(attachment.get("alphaGraph") or {}, instances, definitions)
    referenced_ids = collect_attachment_instance_ids(stages, market)
    referenced_ids.extend(alpha_graph.get("nodes") or [])
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
    if alpha_graph.get("nodes"):
        manifest["alphaGraph"] = alpha_graph

    market_rule = attachment.get("marketRule") or market.get("marketRule") or market.get("brokerageModel")
    if market_rule:
        manifest["marketRule"] = market_rule
    if control_only:
        manifest["controlOnlyModules"] = control_only

    validate_manifest(manifest)
    return manifest


def handle_attach(config, request):
    lane_id = normalize_lane_id(request.get("laneId"))
    strategy_id = request.get("strategyId")
    version = request.get("version")
    if not strategy_id or not version:
        raise ValueError("strategyId and version are required.")

    attachment = sync_signal_stage_with_alpha_graph({
        "laneId": lane_id,
        "strategyId": strategy_id,
        "version": version,
        "name": request.get("name") or f"{strategy_id}-{version}",
        "instances": normalize_attachment_instances(config, request.get("instances") or {}),
        "stages": normalize_stage_references(request.get("stages") or {}),
        "market": request.get("market") or {},
        "marketRule": request.get("marketRule", ""),
        "alphaGraph": normalize_alpha_graph(request.get("alphaGraph") or {}),
    })

    manifest = compile_attachment_manifest(config, attachment)
    release_dir = Path(config["releaseRoot"]) / "_attachments" / reject_path(lane_id) / reject_path(strategy_id) / reject_path(version)
    release_dir.mkdir(parents=True, exist_ok=True)
    release_manifest = release_dir / "pipeline.json"
    atomic_write_json(release_manifest, manifest)
    live_manifest_path = lane_live_manifest_path(config, lane_id)
    atomic_write_json(live_manifest_path, manifest)
    if lane_id == DEFAULT_LANE_ID:
        atomic_write_json(config["liveManifestPath"], manifest)
    save_lane_attachment(config, lane_id, attachment)
    iteration = persist_attachment_iteration(config, attachment, manifest, release_dir, "attached")
    lane = update_lane_record(config, lane_id, attachment, manifest, release_manifest, iteration)

    return {
        "accepted": True,
        "laneId": lane_id,
        "strategyId": strategy_id,
        "version": version,
        "releaseManifest": str(release_manifest),
        "liveManifestPath": live_manifest_path,
        "lanesManifestPath": config["lanesManifestPath"],
        "lane": lane,
        "iteration": iteration,
        "manifest": manifest,
    }


def handle_detach(config, request):
    lane_id = normalize_lane_id(request.get("laneId"))
    attachment = load_lane_attachment(config, lane_id)
    if not attachment:
        raise ValueError(f"No active attachment exists for lane '{lane_id}'.")

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
    alpha_graph = normalize_alpha_graph(attachment.get("alphaGraph") or {})
    if alpha_graph.get("nodes"):
        alpha_graph["nodes"] = [node_id for node_id in alpha_graph["nodes"] if node_id not in detach_instances]
        attachment["alphaGraph"] = alpha_graph
    if attachment.get("instances"):
        attachment["instances"] = {
            instance_id: instance
            for instance_id, instance in attachment["instances"].items()
            if instance_id in active_instance_ids(attachment)
        }

    attachment["laneId"] = lane_id
    manifest = compile_attachment_manifest(config, attachment)
    live_manifest_path = lane_live_manifest_path(config, lane_id)
    atomic_write_json(live_manifest_path, manifest)
    if lane_id == DEFAULT_LANE_ID:
        atomic_write_json(config["liveManifestPath"], manifest)
    save_lane_attachment(config, lane_id, attachment)
    release_dir = Path(config["releaseRoot"]) / "_attachments" / reject_path(lane_id) / reject_path(attachment["strategyId"]) / reject_path(attachment["version"]) / "_detach"
    iteration = persist_attachment_iteration(config, attachment, manifest, release_dir / json_digest({
        "laneId": lane_id,
        "attachment": attachment,
        "manifest": manifest,
        "createdAt": utc_now(),
    })[:16], "detached")
    release_manifest = Path(iteration["releaseManifest"])
    lane = update_lane_record(config, lane_id, attachment, manifest, release_manifest, iteration)

    return {
        "accepted": True,
        "laneId": lane_id,
        "attachment": attachment,
        "liveManifestPath": live_manifest_path,
        "lanesManifestPath": config["lanesManifestPath"],
        "lane": lane,
        "iteration": iteration,
        "manifest": manifest,
    }


def handle_submit(config, request):
    lane_id = normalize_lane_id(request.get("laneId"))
    strategy_id = request.get("strategyId")
    version = request.get("version")
    if not strategy_id or not version:
        raise ValueError("strategyId and version are required.")

    release_dir = Path(config["releaseRoot"]) / "_submitted" / reject_path(lane_id) / reject_path(strategy_id) / reject_path(version)
    release_dir.mkdir(parents=True, exist_ok=True)
    write_bundle_files(release_dir, request.get("files"))

    manifest = substitute_release_root(request.get("manifest"), release_dir)
    validate_manifest(manifest)

    release_manifest = release_dir / "pipeline.json"
    atomic_write_json(release_manifest, manifest)
    live_manifest_path = lane_live_manifest_path(config, lane_id)
    atomic_write_json(live_manifest_path, manifest)
    if lane_id == DEFAULT_LANE_ID:
        atomic_write_json(config["liveManifestPath"], manifest)
    attachment = sync_signal_stage_with_alpha_graph({
        "laneId": lane_id,
        "strategyId": strategy_id,
        "version": version,
        "name": manifest["name"],
        "instances": normalize_attachment_instances(config, request.get("instances") or {}),
        "stages": {stage: manifest.get(stage, []) for stage in STAGES},
        "market": manifest.get("market") or {},
        "marketRule": manifest.get("marketRule", ""),
        "alphaGraph": manifest.get("alphaGraph") or {},
    })
    save_lane_attachment(config, lane_id, attachment)
    iteration = persist_attachment_iteration(config, attachment, manifest, release_dir, "submitted")
    lane = update_lane_record(config, lane_id, attachment, manifest, release_manifest, iteration)

    return {
        "accepted": True,
        "laneId": lane_id,
        "strategyId": strategy_id,
        "version": version,
        "releaseDir": str(release_dir),
        "releaseManifest": str(release_manifest),
        "liveManifestPath": live_manifest_path,
        "lanesManifestPath": config["lanesManifestPath"],
        "lane": lane,
        "iteration": iteration,
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
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        lane_id = normalize_lane_id((query.get("laneId") or [DEFAULT_LANE_ID])[0])

        if path == "/v1/health":
            self.send_json(200, {"status": "ok"})
            return
        if path == "/v1/modules":
            self.send_json(200, {"modules": load_module_definitions(self.config)})
            return
        if path == "/v1/module-packages":
            self.send_json(200, {"packages": load_state(self.config, "packages.json", {})})
            return
        if path == "/v1/pipeline/instances":
            self.send_json(200, {"instances": load_state(self.config, "instances.json", {})})
            return
        if path == "/v1/lanes":
            refresh_engine_lanes_manifest(self.config)
            self.send_json(200, {
                "lanes": load_lanes(self.config),
                "lanesManifestPath": self.config["lanesManifestPath"],
            })
            return
        if path == "/v1/pipeline/attachment":
            self.send_json(200, {
                "laneId": lane_id,
                "attachment": load_lane_attachment(self.config, lane_id),
            })
            return
        if path == "/v1/artifacts":
            self.send_json(200, {"artifacts": sanitize_artifacts(load_state(self.config, "artifacts.json", {}))})
            return
        if path.startswith("/v1/history"):
            limit = 100
            for item in parsed.query.split("&"):
                if item.startswith("limit="):
                    try:
                        limit = int(item.split("=", 1)[1])
                    except ValueError:
                        limit = 100
            self.send_json(200, {
                "events": load_sanitized_history_events(self.config, limit),
                "iterations": load_state(self.config, "iterations.json", []),
                "artifacts": sanitize_artifacts(load_state(self.config, "artifacts.json", {})),
                "packages": load_state(self.config, "packages.json", {}),
                "deletedModules": load_state(self.config, "deleted-modules.json", []),
                "lanes": load_lanes(self.config),
            })
            return
        if path == "/v1/strategies/current":
            lanes = load_lanes(self.config)
            manifest_path = lanes.get(lane_id, {}).get("liveManifestPath") or (
                self.config["liveManifestPath"] if lane_id == DEFAULT_LANE_ID else lane_live_manifest_path(self.config, lane_id)
            )
            manifest_file = Path(manifest_path)
            if not manifest_file.exists():
                self.send_json(404, {"error": f"Live manifest does not exist for lane '{lane_id}': {manifest_file}"})
                return
            with manifest_file.open(encoding="utf-8") as handle:
                self.send_json(200, {
                    "laneId": lane_id,
                    "lane": lanes.get(lane_id),
                    "manifest": json.load(handle),
                })
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["v1", "lanes"] and parts[3] == "current":
            lane_id = normalize_lane_id(parts[2])
            lanes = load_lanes(self.config)
            manifest_path = lanes.get(lane_id, {}).get("liveManifestPath") or lane_live_manifest_path(self.config, lane_id)
            manifest_file = Path(manifest_path)
            if not manifest_file.exists():
                self.send_json(404, {"error": f"Live manifest does not exist for lane '{lane_id}': {manifest_file}"})
                return
            with manifest_file.open(encoding="utf-8") as handle:
                self.send_json(200, {
                    "laneId": lane_id,
                    "lane": lanes.get(lane_id),
                    "manifest": json.load(handle),
                })
            return
        if len(parts) == 4 and parts[:2] == ["v1", "lanes"] and parts[3] == "attachment":
            lane_id = normalize_lane_id(parts[2])
            self.send_json(200, {
                "laneId": lane_id,
                "attachment": load_lane_attachment(self.config, lane_id),
            })
            return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        try:
            request = read_request_json(self)
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            with control_state_lock(self.config):
                if self.path == "/v1/strategies/submit":
                    result = handle_submit(self.config, request)
                elif self.path == "/v1/modules":
                    result = handle_add_module(self.config, request)
                elif self.path == "/v1/module-packages":
                    result = handle_add_package(self.config, request)
                elif self.path == "/v1/pipeline/instances":
                    result = handle_create_instance(self.config, request)
                elif self.path == "/v1/pipeline/attach":
                    result = handle_attach(self.config, request)
                elif self.path == "/v1/pipeline/detach":
                    result = handle_detach(self.config, request)
                elif len(parts) == 4 and parts[:2] == ["v1", "lanes"] and parts[3] in {"attach", "detach"}:
                    request["laneId"] = parts[2]
                    result = handle_attach(self.config, request) if parts[3] == "attach" else handle_detach(self.config, request)
                elif self.path == "/v1/artifacts":
                    result = handle_record_artifact(self.config, request)
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
                with control_state_lock(self.config):
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
    refresh_engine_lanes_manifest(StrategySubmitHandler.config)
    server = ThreadingHTTPServer((args.host, args.port), StrategySubmitHandler)
    print(f"strategy submit api listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
