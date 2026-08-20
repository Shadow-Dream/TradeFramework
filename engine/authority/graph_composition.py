"""External composition-contract proof for one frozen Graph artifact."""

from __future__ import annotations

import copy

from engine.contracts import strict_json
from engine.contracts.contract_expansion import (
    expanded_contract_root_paths,
    expand_contracts,
    resolve_expanded_contract_path,
)
from engine.contracts.data_model import normalize_data_key_schema
from engine.contracts.data_path import split_data_path


__all__ = ()


def _require_match(actual, expected, *, label):
    options = {"sort_keys": True, "separators": (",", ":")}
    if strict_json.dumps(actual, **options) != strict_json.dumps(expected, **options):
        raise ValueError(f"{label} does not match its verified composition.")


def verify_frozen_boundary_contracts(
    plan,
    graph,
    initial_contracts,
    required_roots,
    source_contracts,
    source_required_roots,
    *,
    label,
):
    """Prove artifact input states from the independently verified composition."""

    initial = expand_contracts({
        data_key: normalize_data_key_schema(schema, path=data_key)
        for data_key, schema in initial_contracts.items()
    })
    initial_roots = expanded_contract_root_paths(initial)
    required_roots = frozenset(required_roots)
    if not required_roots <= initial_roots:
        raise ValueError(f"{label} required roots are not declared.")
    referenced_sources = {
        boundary["source"]
        for boundary in graph["inputs"].values()
        if "source" in boundary
    }
    normalized_sources = {}
    normalized_source_roots = {}
    for source in sorted(referenced_sources):
        raw_contracts = source_contracts[source]
        if not isinstance(raw_contracts, dict):
            raise ValueError(
                f"{label} input source '{source}' contracts must be an object."
            )
        contracts = expand_contracts({
            data_key: normalize_data_key_schema(schema, path=data_key)
            for data_key, schema in raw_contracts.items()
        })
        contract_roots = expanded_contract_root_paths(contracts)
        roots = frozenset(source_required_roots.get(source, contract_roots))
        if not roots <= contract_roots:
            raise ValueError(
                f"{label} input source '{source}' required roots are not declared."
            )
        normalized_sources[source] = contracts
        normalized_source_roots[source] = roots

    consumed_inputs = {}
    consumed_sources = {source: {} for source in normalized_sources}
    missing_contract = object()
    for boundary in graph["inputs"].values():
        source = boundary.get("source")
        contracts = initial if source is None else normalized_sources[source]
        schema = resolve_expanded_contract_path(
            contracts,
            boundary["dataKey"],
            missing_contract,
        )
        if schema is missing_contract:
            continue
        root = split_data_path(boundary["dataKey"])[0]
        target = consumed_inputs if source is None else consumed_sources[source]
        target[root] = copy.deepcopy(contracts[root])
    consumed_inputs = expand_contracts(consumed_inputs)
    _require_match(
        plan["inputContracts"],
        consumed_inputs,
        label=f"{label} inputContracts",
    )
    _require_match(
        plan["inputRequiredRoots"],
        sorted(required_roots & expanded_contract_root_paths(consumed_inputs)),
        label=f"{label} inputRequiredRoots",
    )
    expected_sources = {}
    for source, contracts in consumed_sources.items():
        contracts = expand_contracts(contracts)
        expected_sources[source] = {
            "contracts": contracts,
            "requiredRoots": sorted(
                normalized_source_roots[source]
                & expanded_contract_root_paths(contracts)
            ),
        }
    _require_match(
        plan.get("inputSources", {}),
        expected_sources,
        label=f"{label} inputSources",
    )
