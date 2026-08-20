"""Pure causal-cycle contracts and input projection for Graph runtimes."""

from __future__ import annotations

from collections.abc import Mapping

from engine.contracts.contract_expansion import (
    contract_root_paths,
    expand_contracts,
    prefixed_contracts,
)
from engine.contracts.data_path import get_data_path, set_data_path


DECISION_TIME_DATA_KEY = "decisionTime"
PREVIOUS_CYCLE_ROOT = "last"
CURRENT_PIPELINE_SOURCE = "currentPipeline"


__all__ = (
    "DECISION_TIME_DATA_KEY",
    "PREVIOUS_CYCLE_ROOT",
    "CURRENT_PIPELINE_SOURCE",
    "build_cycle_graph_input",
    "cycle_input_contract_state",
    "cycle_input_contracts",
    "validate_cycle_graph_inputs",
)


def validate_cycle_graph_inputs(graph, *, label="Cycle Graph"):
    """Reject the full previous-cycle Data Dict as a Graph boundary input."""
    for boundary_id, boundary in graph["inputs"].items():
        if (
            "source" not in boundary
            and boundary["dataKey"] == PREVIOUS_CYCLE_ROOT
        ):
            raise ValueError(
                f"{label} Data Input '{boundary_id}' may not bind the full '{PREVIOUS_CYCLE_ROOT}' "
                f"Data Dict; bind explicit '{PREVIOUS_CYCLE_ROOT}.<DataKey>' paths instead."
            )


def cycle_input_contracts(sample_contracts, previous_data_contracts):
    if not isinstance(sample_contracts, Mapping):
        raise ValueError("Cycle sample contracts must be an object.")
    if not isinstance(previous_data_contracts, Mapping):
        raise ValueError("Cycle previous-data contracts must be an object.")
    sample = expand_contracts(sample_contracts)
    if DECISION_TIME_DATA_KEY in sample:
        raise ValueError(
            f"Sampler output may not use reserved cycle DataKey '{DECISION_TIME_DATA_KEY}'."
        )
    if PREVIOUS_CYCLE_ROOT in sample:
        raise ValueError(
            f"Sampler output may not use reserved cycle root DataKey '{PREVIOUS_CYCLE_ROOT}'."
        )
    return expand_contracts({
        **sample,
        DECISION_TIME_DATA_KEY: {"type": "string"},
        **prefixed_contracts(PREVIOUS_CYCLE_ROOT, previous_data_contracts),
    })


def cycle_input_contract_state(sample_contracts, previous_data_contracts):
    """Compile cycle schemas plus roots guaranteed on the very first cycle."""
    contracts = cycle_input_contracts(sample_contracts, previous_data_contracts)
    sample_roots = set(contract_root_paths(sample_contracts))
    sample_roots.add(DECISION_TIME_DATA_KEY)
    # ``last`` is deliberately not guaranteed: no previous Data Dict exists
    # for the first cycle, regardless of which fields later cycles contain.
    return contracts, frozenset(sample_roots)


def build_cycle_graph_input(sample, previous_data, decision_time, data_keys):
    """Materialize only explicit DataKeys connected to one causal Cycle Graph."""
    if not isinstance(sample, Mapping):
        raise ValueError("Current cycle Sample must be an object.")
    if not isinstance(previous_data, Mapping):
        raise ValueError("Previous cycle Data Dict must be an object.")
    if PREVIOUS_CYCLE_ROOT in sample:
        raise ValueError(
            f"Sampler output may not use reserved cycle root DataKey '{PREVIOUS_CYCLE_ROOT}'."
        )
    if DECISION_TIME_DATA_KEY in sample:
        raise ValueError(
            f"Sampler output may not use reserved cycle DataKey '{DECISION_TIME_DATA_KEY}'."
        )
    if not isinstance(decision_time, str) or not decision_time.strip():
        raise ValueError("Cycle decisionTime must be a non-empty string.")

    requested = tuple(dict.fromkeys(str(item) for item in data_keys))
    if PREVIOUS_CYCLE_ROOT in requested:
        raise ValueError(
            f"Cycle Graph may not read the full '{PREVIOUS_CYCLE_ROOT}' Data Dict; "
            f"bind explicit '{PREVIOUS_CYCLE_ROOT}.<DataKey>' paths instead."
        )

    result = {}
    missing = object()
    prefix = PREVIOUS_CYCLE_ROOT + "."
    for data_key in requested:
        if data_key == DECISION_TIME_DATA_KEY:
            value = decision_time
        elif data_key.startswith(prefix):
            value = get_data_path(
                previous_data,
                data_key.removeprefix(prefix),
                missing,
            )
        else:
            value = get_data_path(sample, data_key, missing)
        if value is not missing:
            set_data_path(result, data_key, value)
    return result
