#!/usr/bin/env python3
"""Nominal authority for one Observation-to-Pipeline projection proof."""

from __future__ import annotations

from engine.authority import graph as graph_authority
from engine.authority import graph_cycle as graph_cycle_authority
from engine.authority import pipeline as pipeline_authority
from engine.contracts.graph import compiled_graph_output_writes
from engine.contracts.pipeline import require_canonical_value_match
from engine.contracts.contract_reducer import apply_expanded_contract_writes
from engine.contracts.observation_projection import (
    observation_contract_digest,
    project_observation_contract_state,
)


__all__ = (
    "bind_observation_projection_authority",
    "require_observation_projection_authority",
)


_OBSERVATION_PROJECTION_TOKEN = object()


class _ObservationProjectionAuthority:
    """Bind an Environment Observation to one Pipeline projection plan."""

    __slots__ = (
        "_environment_authority",
        "_pipeline_authority",
        "_sealed",
    )

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Observation projection authority is immutable.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        environment_authority,
        pipeline_authority_value,
        *,
        _token,
    ):
        if _token is not _OBSERVATION_PROJECTION_TOKEN:
            raise TypeError("Observation projection authority is Engine-owned.")
        self._environment_authority = environment_authority
        self._pipeline_authority = pipeline_authority_value
        object.__setattr__(self, "_sealed", True)

    def __copy__(self):
        raise TypeError("Observation projection authority cannot be copied.")

    def __deepcopy__(self, _memo):
        raise TypeError("Observation projection authority cannot be copied.")

    def __reduce__(self):
        raise TypeError("Observation projection authority cannot be serialized.")


def _environment_output_contract_state(environment_authority):
    _definition, module_graph_authority = (
        graph_cycle_authority.compiled_cycle_graph_authority_material(
            environment_authority,
            allowed_kind="Environment",
            graph_label="Environment Graph",
            identity_field="environmentId",
            runtime_type="EnvironmentGraph",
        )
    )
    plan = graph_authority.compiled_graph_authority_plan(module_graph_authority)
    contracts, required_roots = apply_expanded_contract_writes(
        {},
        frozenset(),
        (
            (data_key, schema, required)
            for _boundary_id, data_key, schema, required
            in compiled_graph_output_writes(plan)
        ),
    )
    require_canonical_value_match(
        plan["outputContracts"],
        contracts,
        label="Environment Graph output contracts",
    )
    return contracts, required_roots


def bind_observation_projection_authority(
    environment_authority,
    pipeline_authority_value,
):
    """Prove one Environment Observation and Pipeline projection agree."""

    environment_contracts, environment_required_roots = (
        _environment_output_contract_state(environment_authority)
    )
    (
        _pipeline_id,
        _version,
        _manifest_hash,
        template,
        pipeline_plan,
        _signal_authority,
        _direct_invocation_authorities,
    ) = pipeline_authority.compiled_pipeline_authority_material(
        pipeline_authority_value
    )
    config = pipeline_authority.pipeline_contract_template_material(template)[
        "config"
    ]
    projected_contracts, projected_required_roots = (
        project_observation_contract_state(
            environment_contracts,
            environment_required_roots,
            config,
        )
    )
    require_canonical_value_match(
        pipeline_plan["observationContractDigest"],
        observation_contract_digest(
            environment_contracts,
            environment_required_roots,
        ),
        label="Pipeline Observation contract digest",
    )
    require_canonical_value_match(
        pipeline_plan["inputContracts"],
        projected_contracts,
        label="Pipeline input contracts",
    )
    require_canonical_value_match(
        pipeline_plan["inputRequiredRoots"],
        sorted(projected_required_roots),
        label="Pipeline input required roots",
    )
    require_canonical_value_match(
        pipeline_plan["observationInput"],
        config["observationInput"],
        label="Pipeline Observation input projection",
    )
    return _ObservationProjectionAuthority(
        environment_authority,
        pipeline_authority_value,
        _token=_OBSERVATION_PROJECTION_TOKEN,
    )


def require_observation_projection_authority(
    authority,
    *,
    environment_authority=None,
    pipeline_authority_value=None,
):
    """Require the exact producer/consumer pair used to issue an authority."""

    if type(authority) is not _ObservationProjectionAuthority:
        raise TypeError("Observation projection authority is Engine-owned.")
    if (
        environment_authority is not None
        and authority._environment_authority is not environment_authority
    ):
        raise TypeError(
            "Observation projection authority has the wrong producer."
        )
    if (
        pipeline_authority_value is not None
        and authority._pipeline_authority is not pipeline_authority_value
    ):
        raise TypeError(
            "Observation projection authority has the wrong consumer."
        )
    return authority
