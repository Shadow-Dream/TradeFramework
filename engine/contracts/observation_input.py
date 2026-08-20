"""Canonical Pipeline configuration for projecting an Observation."""

from __future__ import annotations

from engine.contracts.data_path import canonical_data_key_order, split_data_path
from engine.contracts.exact_fields import require_exact_fields


PIPELINE_CONFIG_FIELDS = frozenset({"observationInput"})
OBSERVATION_INPUT_FIELDS = frozenset({"whitelist", "blacklist"})


def _path_is_same_or_descendant(path, parent):
    path_parts = split_data_path(path)
    parent_parts = split_data_path(parent)
    return path_parts[:len(parent_parts)] == parent_parts


def _normalize_observation_paths(value, *, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array.")
    if any(not isinstance(path, str) or not path.strip() for path in value):
        raise ValueError(f"{label} must contain non-empty DataKey paths.")
    paths = [path.strip() for path in value]
    for path in paths:
        parts = split_data_path(path)
        if parts[0] in {"last", "decisionTime"}:
            raise ValueError(
                f"{label} cannot select reserved Engine path '{path}'."
            )
    if len(paths) != len(set(paths)):
        raise ValueError(f"{label} must not contain duplicate paths.")
    return sorted(paths, key=canonical_data_key_order)


def _minimal_observation_paths(paths):
    """Collapse descendants already covered by an earlier canonical parent."""

    result = []
    for path in paths:
        if not any(_path_is_same_or_descendant(path, parent) for parent in result):
            result.append(path)
    return result


def normalize_pipeline_config(config):
    """Return the only canonical form of Pipeline Observation input config."""

    require_exact_fields(
        config,
        allowed=PIPELINE_CONFIG_FIELDS,
        required=PIPELINE_CONFIG_FIELDS,
        label="Pipeline config",
    )
    observation_input = config["observationInput"]
    require_exact_fields(
        observation_input,
        allowed=OBSERVATION_INPUT_FIELDS,
        required=OBSERVATION_INPUT_FIELDS,
        label="Pipeline config.observationInput",
    )
    whitelist = _normalize_observation_paths(
        observation_input["whitelist"],
        label="Pipeline config.observationInput.whitelist",
    )
    blacklist = _normalize_observation_paths(
        observation_input["blacklist"],
        label="Pipeline config.observationInput.blacklist",
    )
    outside = [
        path
        for path in blacklist
        if not any(_path_is_same_or_descendant(path, allowed) for allowed in whitelist)
    ]
    if outside:
        raise ValueError(
            "Pipeline config.observationInput.blacklist path(s) are outside the "
            "whitelist: " + ", ".join(outside)
        )
    return {
        "observationInput": {
            "whitelist": whitelist,
            "blacklist": blacklist,
        }
    }


def compile_observation_projection_plan(config):
    """Compile the only runtime path plan represented by normalized config.

    The versioned config retains every unique user-selected path.  The
    execution plan removes parent/child redundancy and paths that a blacklist
    entry deletes in full, so Runtime never reparses or reinterprets draft
    strings inside the cycle loop.
    """

    observation_input = normalize_pipeline_config(config)["observationInput"]
    whitelist = _minimal_observation_paths(observation_input["whitelist"])
    blacklist = _minimal_observation_paths(observation_input["blacklist"])
    selected = [
        path
        for path in whitelist
        if not any(
            _path_is_same_or_descendant(path, denied)
            for denied in blacklist
        )
    ]
    denied = [
        path
        for path in blacklist
        if any(
            path != allowed and _path_is_same_or_descendant(path, allowed)
            for allowed in selected
        )
    ]

    def entries(paths):
        return [
            {"dataKey": path, "segments": list(split_data_path(path))}
            for path in paths
        ]

    return {
        "whitelist": entries(selected),
        "blacklist": entries(denied),
    }


__all__ = (
    "OBSERVATION_INPUT_FIELDS",
    "PIPELINE_CONFIG_FIELDS",
    "compile_observation_projection_plan",
    "normalize_pipeline_config",
)
