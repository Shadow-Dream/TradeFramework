"""Backtest plan composition, frozen artifact binding, and Runtime assembly."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field

from engine.authority import graph as graph_authority
from engine.authority import pipeline as pipeline_authority
from engine.authority import sampler as sampler_authority
from engine.authority.runtime_data import (
    bind_observation_projection_authority,
)
from engine.authority.graph_cycle import (
    bind_verified_compiled_cycle_graph_authority,
)
from engine.compiler import graph as graph_compiler
from engine.compiler import pipeline as pipeline_compiler
from engine.contracts import backtest as backtest_contracts
from engine.contracts import backtest_composition as composition_contracts
from engine.contracts import dataset as dataset_contracts
from engine.contracts import strict_json
from engine.contracts.contract_expansion import (
    expand_contracts,
    expanded_contract_root_paths,
)
from engine.contracts.contract_reducer import apply_expanded_contract_writes
from engine.contracts.graph import compiled_graph_output_writes
from engine.contracts.graph_cycle import (
    CURRENT_PIPELINE_SOURCE,
    cycle_input_contract_state,
)
from engine.runtime import graph_cycle as graph_cycle_runtime
from engine.runtime import lifecycle as runtime_lifecycle
from engine.runtime import pipeline as pipeline_runtime


_VALIDATED_ARTIFACT_TOKEN = object()
_VERIFIED_COMPOSITION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _ValidatedBacktestCompositionArtifact:
    """Nominal proof that one frozen artifact passed its exact shape/hash gate."""

    _artifact_json: str
    _pipeline_plan: object
    _token: object = field(repr=False, compare=False)
    _artifact_digest: str = field(init=False, repr=False, compare=False)
    _pipeline_plan_digest: str = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if self._token is not _VALIDATED_ARTIFACT_TOKEN:
            raise TypeError("Validated Backtest composition artifact is Engine-owned.")
        if not isinstance(self._artifact_json, str):
            raise TypeError("Validated Backtest composition artifact is invalid.")
        pipeline_authority.require_validated_pipeline_plan(self._pipeline_plan)
        object.__setattr__(
            self,
            "_artifact_digest",
            hashlib.sha256(self._artifact_json.encode("utf-8")).hexdigest(),
        )
        object.__setattr__(
            self,
            "_pipeline_plan_digest",
            pipeline_authority.validated_pipeline_plan_digest(
                self._pipeline_plan
            ),
        )

    def _require_integrity(self, *, materialize_artifact=False):
        if (
            not isinstance(self._artifact_json, str)
            or hashlib.sha256(self._artifact_json.encode("utf-8")).hexdigest()
            != self._artifact_digest
            or pipeline_authority.validated_pipeline_plan_digest(
                self._pipeline_plan
            )
            != self._pipeline_plan_digest
        ):
            raise ValueError(
                "Validated Backtest composition artifact does not match "
                "its canonical authority."
            )
        if not materialize_artifact:
            return None
        artifact = strict_json.loads(self._artifact_json)
        embedded_plan_json = strict_json.dumps(
            artifact["pipelinePlan"],
            sort_keys=True,
            separators=(",", ":"),
        )
        if embedded_plan_json != self._pipeline_plan._plan_json:
            raise ValueError(
                "Validated Pipeline plan does not belong to its composition artifact."
            )
        return artifact

    def _material(self):
        return self._require_integrity(materialize_artifact=True)

    def __copy__(self):
        raise TypeError("Validated Backtest composition artifact cannot be copied.")

    def __deepcopy__(self, _memo):
        raise TypeError("Validated Backtest composition artifact cannot be copied.")

    def __reduce__(self):
        raise TypeError(
            "Validated Backtest composition artifact cannot be serialized."
        )


@dataclass(frozen=True, slots=True)
class _VerifiedBacktestComposition:
    """Nominal same-stack proof that every frozen authority agrees."""

    _pipeline_authority: object
    _environment_authority: object
    _analysis_authority: object
    _sampler_contracts: dict
    _sampler_required_roots: tuple
    _cycle_contracts: dict
    _cycle_required_roots: tuple
    _result_contracts: dict
    _result_required_roots: tuple
    _token: object = field(repr=False, compare=False)

    def __post_init__(self):
        if self._token is not _VERIFIED_COMPOSITION_TOKEN:
            raise TypeError("Verified Backtest composition is Engine-owned.")
        try:
            pipeline_authority.require_compiled_pipeline_authority(
                self._pipeline_authority
            )
        except TypeError as exc:
            raise TypeError(
                "Verified Backtest composition requires compiled Pipeline authority."
            ) from exc

    def _contract_material(self):
        return (
            copy.deepcopy(self._sampler_contracts),
            frozenset(self._sampler_required_roots),
            copy.deepcopy(self._cycle_contracts),
            frozenset(self._cycle_required_roots),
            copy.deepcopy(self._result_contracts),
            frozenset(self._result_required_roots),
        )


def dataset_field_schema(dataset_version):
    records = dataset_version["capabilities"].get(
        dataset_contracts.RECORDS_CAPABILITY
    )
    if records is None:
        return {}
    fields = records["descriptor"]["valueSchema"].get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Dataset records valueSchema.fields must be an object.")
    return copy.deepcopy(fields)


def apply_compiled_graph_contracts(plan, contracts=None, required_roots=None):
    """Apply a Graph's public boundary writes using runtime order exactly."""

    complete = expand_contracts(contracts or {})
    roots = frozenset(required_roots or ())
    return apply_expanded_contract_writes(
        complete,
        roots,
        (
            (data_key, schema, required)
            for _boundary_id, data_key, schema, required
            in compiled_graph_output_writes(plan)
        ),
    )


def require_canonical_value_match(actual, expected, *, label):
    if strict_json.dumps(actual, sort_keys=True) != strict_json.dumps(
        expected,
        sort_keys=True,
    ):
        raise ValueError(
            f"{label} does not match its verified composition authority."
        )


def compose_backtest_plans(
    *,
    pipeline_contract_template,
    sampler_contracts,
    sampler_required_roots,
    environment_definition,
    environment_module_authorities,
    analysis_definition,
    analysis_module_authorities,
):
    """Compile the unique fixed point joining all Backtest resource boundaries."""

    preliminary_environment = graph_compiler.compile_verified_module_graph(
        environment_definition["graph"],
        environment_definition["instances"],
        environment_module_authorities,
        sampler_contracts,
        allowed_kinds={"Environment"},
        label="Environment Graph",
        strict_sources=False,
        required_roots=sampler_required_roots,
    )
    before_pipeline, before_required_roots = apply_compiled_graph_contracts(
        preliminary_environment
    )
    seen_compositions = set()
    while True:
        state_digest = strict_json.dumps(
            [before_pipeline, sorted(before_required_roots)],
            sort_keys=True,
        )
        if state_digest in seen_compositions:
            raise ValueError(
                "Backtest component contracts do not reach a stable composition."
            )
        seen_compositions.add(state_digest)
        bound_pipeline_plan = pipeline_compiler.bind_pipeline_contract_plan(
            pipeline_contract_template,
            before_pipeline,
            initial_required_roots=before_required_roots,
        )
        pipeline_plan = pipeline_authority.bound_pipeline_contract_plan(
            bound_pipeline_plan
        )
        through_pipeline = pipeline_plan["allContracts"]
        through_required_roots = frozenset(pipeline_plan["allRequiredRoots"])
        cycle_contracts, cycle_required_roots = cycle_input_contract_state(
            sampler_contracts,
            through_pipeline,
        )
        environment_plan = graph_authority.compiled_graph_authority_plan(
            graph_compiler.compile_verified_module_graph_authority(
                environment_definition["graph"],
                environment_definition["instances"],
                environment_module_authorities,
                cycle_contracts,
                allowed_kinds={"Environment"},
                label="Environment Graph",
                required_roots=cycle_required_roots,
            )
        )
        next_before, next_required_roots = apply_compiled_graph_contracts(
            environment_plan
        )
        if (
            next_before == before_pipeline
            and next_required_roots == before_required_roots
        ):
            break
        before_pipeline = next_before
        before_required_roots = next_required_roots

    analysis_plan = graph_authority.compiled_graph_authority_plan(
        graph_compiler.compile_verified_module_graph_authority(
            analysis_definition["graph"],
            analysis_definition["instances"],
            analysis_module_authorities,
            cycle_contracts,
            allowed_kinds={"Analyzer"},
            label="Analysis Graph",
            required_roots=cycle_required_roots,
            source_contracts={CURRENT_PIPELINE_SOURCE: through_pipeline},
            source_required_roots={
                CURRENT_PIPELINE_SOURCE: through_required_roots,
            },
        )
    )
    result_contracts, result_required_roots = apply_compiled_graph_contracts(
        analysis_plan,
        through_pipeline,
        through_required_roots,
    )
    return {
        "samplerContracts": expand_contracts(sampler_contracts),
        "samplerRequiredRoots": sorted(sampler_required_roots),
        "environmentPlan": environment_plan,
        "pipelinePlan": pipeline_plan,
        "cycleContracts": expand_contracts(cycle_contracts),
        "cycleRequiredRoots": sorted(cycle_required_roots),
        "analysisPlan": analysis_plan,
        "resultContracts": expand_contracts(result_contracts),
        "resultRequiredRoots": sorted(result_required_roots),
    }


def build_backtest_composition_artifact(resolved):
    """Freeze the resolver's final fixed point as one mandatory artifact."""

    artifact = {
        "schemaVersion": composition_contracts.BACKTEST_COMPOSITION_ARTIFACT_SCHEMA_VERSION,
        "pipelinePlan": copy.deepcopy(resolved["pipelinePlan"]),
        "environmentPlan": copy.deepcopy(resolved["environmentPlan"]),
        "analysisPlan": copy.deepcopy(resolved["analysisPlan"]),
        "samplerContracts": copy.deepcopy(resolved["samplerContracts"]),
        "samplerRequiredRoots": copy.deepcopy(resolved["samplerRequiredRoots"]),
        "cycleContracts": copy.deepcopy(resolved["cycleContracts"]),
        "cycleRequiredRoots": copy.deepcopy(resolved["cycleRequiredRoots"]),
        "resultContracts": copy.deepcopy(resolved["resultContracts"]),
        "resultRequiredRoots": copy.deepcopy(resolved["resultRequiredRoots"]),
    }
    artifact["artifactHash"] = backtest_contracts.backtest_evidence_digest(
        artifact
    )
    return composition_contracts.require_artifact(artifact)


def validate_backtest_composition_artifact(artifact):
    """Issue a nominal proof after exact artifact shape and hash validation."""

    validated = composition_contracts.require_artifact(artifact)
    return _ValidatedBacktestCompositionArtifact(
        strict_json.dumps(
            validated,
            sort_keys=True,
            separators=(",", ":"),
        ),
        pipeline_authority.seal_validated_pipeline_plan_authority(
            validated["pipelinePlan"]
        ),
        _VALIDATED_ARTIFACT_TOKEN,
    )


def validated_backtest_artifact_material(validated_artifact):
    """Return detached material from an exact validated artifact proof."""

    if type(validated_artifact) is not _ValidatedBacktestCompositionArtifact:
        raise TypeError(
            "Backtest artifact access requires a validated composition artifact."
        )
    return validated_artifact._material()


def validated_backtest_artifact_pipeline_plan(validated_artifact):
    """Return a plan already covered by the artifact's exact shape/hash gate."""

    if type(validated_artifact) is not _ValidatedBacktestCompositionArtifact:
        raise TypeError(
            "Backtest artifact access requires a validated composition artifact."
        )
    validated_artifact._require_integrity()
    return validated_artifact._pipeline_plan


def bind_frozen_backtest_composition(
    validated_artifact,
    *,
    pipeline_definition_authority,
    pipeline_contract_template,
    sampler_runtime_authority,
    sampler_parameters,
    dataset_schema,
    environment_definition,
    environment_definition_authority,
    environment_module_definition_authorities,
    analysis_definition,
    analysis_definition_authority,
    analysis_module_definition_authorities,
):
    """Bind frozen semantic authorities into one nominal execution proof."""

    if type(validated_artifact) is not _ValidatedBacktestCompositionArtifact:
        raise TypeError(
            "Backtest composition verification requires a validated artifact."
        )
    artifact = validated_artifact._material()
    validated_pipeline_plan = validated_artifact._pipeline_plan
    try:
        pipeline_authority.require_pipeline_contract_template(
            pipeline_contract_template
        )
    except TypeError as exc:
        raise TypeError(
            "Backtest composition verification requires a verified Pipeline template."
        ) from exc

    sampler_contracts = expand_contracts(
        sampler_authority.resolve_verified_sampler_output_contracts(
            sampler_runtime_authority,
            sampler_parameters,
            dataset_schema,
        )
    )
    sampler_required_roots = frozenset(
        expanded_contract_root_paths(sampler_contracts)
    )
    require_canonical_value_match(
        artifact["samplerContracts"],
        sampler_contracts,
        label="Backtest composition artifact samplerContracts",
    )
    require_canonical_value_match(
        artifact["samplerRequiredRoots"],
        sorted(sampler_required_roots),
        label="Backtest composition artifact samplerRequiredRoots",
    )

    before_pipeline, before_required_roots = apply_compiled_graph_contracts(
        artifact["environmentPlan"]
    )
    bound_pipeline_plan = pipeline_compiler.bind_validated_pipeline_contract_plan(
        pipeline_contract_template,
        validated_pipeline_plan,
        before_pipeline,
        initial_required_roots=before_required_roots,
        label="Backtest composition artifact Pipeline plan",
    )
    compiled_pipeline_authority = (
        pipeline_authority.bind_compiled_pipeline_authority(
            pipeline_definition_authority,
            pipeline_contract_template,
            bound_pipeline_plan,
        )
    )
    pipeline_plan = pipeline_authority.validated_pipeline_plan_material(
        validated_pipeline_plan
    )
    through_pipeline = pipeline_plan["allContracts"]
    through_required_roots = frozenset(pipeline_plan["allRequiredRoots"])
    cycle_contracts, cycle_required_roots = cycle_input_contract_state(
        sampler_contracts,
        through_pipeline,
    )
    require_canonical_value_match(
        artifact["cycleContracts"],
        cycle_contracts,
        label="Backtest composition artifact cycleContracts",
    )
    require_canonical_value_match(
        artifact["cycleRequiredRoots"],
        sorted(cycle_required_roots),
        label="Backtest composition artifact cycleRequiredRoots",
    )

    environment_graph_authority = (
        graph_authority.bind_frozen_composition_graph_authority(
            artifact["environmentPlan"],
            environment_definition["graph"],
            environment_definition["instances"],
            environment_module_definition_authorities,
            cycle_contracts,
            allowed_kinds={"Environment"},
            label="Backtest composition artifact environmentPlan",
            required_roots=cycle_required_roots,
        )
    )
    environment_authority = bind_verified_compiled_cycle_graph_authority(
        environment_definition_authority,
        environment_graph_authority,
        allowed_kind="Environment",
        graph_label="Environment Graph",
        identity_field="environmentId",
        runtime_type="EnvironmentGraph",
    )
    analysis_graph_authority = (
        graph_authority.bind_frozen_composition_graph_authority(
            artifact["analysisPlan"],
            analysis_definition["graph"],
            analysis_definition["instances"],
            analysis_module_definition_authorities,
            cycle_contracts,
            allowed_kinds={"Analyzer"},
            label="Backtest composition artifact analysisPlan",
            required_roots=cycle_required_roots,
            source_contracts={CURRENT_PIPELINE_SOURCE: through_pipeline},
            source_required_roots={
                CURRENT_PIPELINE_SOURCE: through_required_roots,
            },
        )
    )
    analysis_authority = bind_verified_compiled_cycle_graph_authority(
        analysis_definition_authority,
        analysis_graph_authority,
        allowed_kind="Analyzer",
        graph_label="Analysis Graph",
        identity_field="analysisId",
        runtime_type="AnalysisGraph",
    )
    result_contracts, result_required_roots = apply_compiled_graph_contracts(
        artifact["analysisPlan"],
        through_pipeline,
        through_required_roots,
    )
    require_canonical_value_match(
        artifact["resultContracts"],
        result_contracts,
        label="Backtest composition artifact resultContracts",
    )
    require_canonical_value_match(
        artifact["resultRequiredRoots"],
        sorted(result_required_roots),
        label="Backtest composition artifact resultRequiredRoots",
    )
    return _VerifiedBacktestComposition(
        _pipeline_authority=compiled_pipeline_authority,
        _environment_authority=environment_authority,
        _analysis_authority=analysis_authority,
        _sampler_contracts=copy.deepcopy(artifact["samplerContracts"]),
        _sampler_required_roots=tuple(artifact["samplerRequiredRoots"]),
        _cycle_contracts=copy.deepcopy(artifact["cycleContracts"]),
        _cycle_required_roots=tuple(artifact["cycleRequiredRoots"]),
        _result_contracts=copy.deepcopy(artifact["resultContracts"]),
        _result_required_roots=tuple(artifact["resultRequiredRoots"]),
        _token=_VERIFIED_COMPOSITION_TOKEN,
    )


def verified_backtest_contract_material(verified_composition):
    """Return detached contract material from one exact nominal proof."""

    if type(verified_composition) is not _VerifiedBacktestComposition:
        raise TypeError("Backtest contract access requires a verified composition.")
    return verified_composition._contract_material()


def create_backtest_graph_runtimes(*, execution_root, verified_composition):
    """Create every authority-bound Graph Runtime for one frozen composition."""

    if type(verified_composition) is not _VerifiedBacktestComposition:
        raise TypeError("Backtest Graph build requires a verified composition.")
    pipeline = None
    environment = None
    analysis = None
    try:
        pipeline = pipeline_runtime.BacktestPipelineRuntime.from_compiled_authority(
            verified_composition._pipeline_authority,
            execution_root=execution_root,
        )
        environment = (
            graph_cycle_runtime.EnvironmentGraphRuntime.from_compiled_authority(
                verified_composition._environment_authority,
                execution_root=execution_root,
            )
        )
        analysis = graph_cycle_runtime.AnalysisGraphRuntime.from_compiled_authority(
            verified_composition._analysis_authority,
            execution_root=execution_root,
        )
        observation_projection_authority = bind_observation_projection_authority(
            verified_composition._environment_authority,
            verified_composition._pipeline_authority,
        )
        environment.bind_observation_projection_authority(
            observation_projection_authority
        )
        pipeline.bind_observation_projection_authority(
            observation_projection_authority
        )
        (
            _sampler_contracts,
            _sampler_required_roots,
            _cycle_contracts,
            _cycle_required_roots,
            all_contracts,
            all_required_roots,
        ) = verified_composition._contract_material()
        return (
            pipeline,
            environment,
            analysis,
            all_contracts,
            all_required_roots,
        )
    except BaseException:
        runtime_lifecycle.invoke_all(
            tuple(
                resource
                for resource in (analysis, environment, pipeline)
                if resource is not None
            ),
            "close",
            suppress_errors=True,
        )
        raise


__all__ = (
    "apply_compiled_graph_contracts",
    "bind_frozen_backtest_composition",
    "build_backtest_composition_artifact",
    "compose_backtest_plans",
    "create_backtest_graph_runtimes",
    "dataset_field_schema",
    "require_canonical_value_match",
    "validated_backtest_artifact_material",
    "validated_backtest_artifact_pipeline_plan",
    "validate_backtest_composition_artifact",
    "verified_backtest_contract_material",
)
