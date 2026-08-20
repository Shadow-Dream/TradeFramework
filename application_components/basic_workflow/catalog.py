"""Protocol-level catalog of reusable ordinary BuiltIn Module identities."""

from __future__ import annotations

import copy

from builtin_implementations import analysis_contracts, environment_contracts


COMPONENT_IDS = {
    "account": (
        "basic-multi-asset-bar-account",
        "paper-account-ledger",
    ),
    "brokerRule": (
        "numeric-target-submit-rule",
        "lot-size-order-update-rule",
        "configurable-shortable-provider",
        "maximum-leverage-rule",
        "cash-equity-buying-power-model",
        "target-delta-execution-rule",
        "constant-bps-slippage-model",
        "proportional-fill-model",
        "fixed-plus-bps-fee-model",
        "immediate-settlement-model",
        "negative-cash-interest-model",
        "paper-order-summary",
    ),
    "analysis": (
        "cycle-count-analyzer",
        "numeric-change-analyzer",
        "performance-metrics-analyzer",
    ),
}


def component_catalog():
    definitions = {
        item["moduleId"]: item
        for item in (
            *environment_contracts.ENVIRONMENT_MODULES,
            *analysis_contracts.ANALYSIS_MODULES,
        )
    }
    missing = sorted(
        module_id
        for module_ids in COMPONENT_IDS.values()
        for module_id in module_ids
        if module_id not in definitions
    )
    if missing:
        raise ValueError(
            "Basic Workflow component definitions are missing: " + ", ".join(missing)
        )
    return {
        category: [copy.deepcopy(definitions[module_id]) for module_id in module_ids]
        for category, module_ids in COMPONENT_IDS.items()
    }


__all__ = ("COMPONENT_IDS", "component_catalog")
