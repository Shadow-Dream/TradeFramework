"""Authority-bound execution runtime for one causal Cycle Graph."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from engine.authority.graph import compiled_graph_authority_plan
from engine.authority.graph_cycle import compiled_cycle_graph_authority_material
from engine.authority.runtime_data import (
    require_observation_projection_authority,
)
from engine.contracts.data_path import (
    canonical_data_key_order,
    get_data_segments,
    set_data_segments,
    split_data_path,
)
from engine.contracts.graph import compiled_graph_definition
from engine.contracts.graph_cycle import (
    DECISION_TIME_DATA_KEY,
    PREVIOUS_CYCLE_ROOT,
)
from engine.runtime.graph import ModuleGraphRuntime
from engine.runtime.data_proof import seal_validated_observation


__all__ = (
    "AnalysisGraphRuntime",
    "CycleGraphRuntime",
    "EnvironmentGraphRuntime",
)


class CycleGraphRuntime:
    """Run a versioned Graph against explicit, schema-compiled cycle inputs."""

    def __init__(self, *_args, **_kwargs):
        raise TypeError(
            "Cycle Graph Runtime requires an Engine-owned compiled authority."
        )

    @classmethod
    def _from_compiled_authority(
        cls,
        compiled_authority,
        *,
        allowed_kind,
        graph_label,
        namespace,
        identity_field,
        runtime_type,
        execution_root=None,
    ):
        """Create a Cycle Runtime from a composition plan verified in this stack."""
        runtime = cls.__new__(cls)
        runtime._initialize_compiled_authority(
            compiled_authority,
            allowed_kind=allowed_kind,
            graph_label=graph_label,
            namespace=namespace,
            identity_field=identity_field,
            runtime_type=runtime_type,
            execution_root=execution_root,
        )
        return runtime

    def _initialize_compiled_authority(
        self,
        compiled_authority,
        *,
        allowed_kind,
        graph_label,
        namespace,
        identity_field,
        runtime_type,
        execution_root,
    ):
        definition, module_graph_authority = compiled_cycle_graph_authority_material(
            compiled_authority,
            allowed_kind=allowed_kind,
            graph_label=graph_label,
            identity_field=identity_field,
            runtime_type=runtime_type,
        )
        self._compiled_authority = compiled_authority
        self._definition = deepcopy(definition)
        self._identity_field = identity_field
        self._runtime_type = runtime_type
        self._identity = definition[identity_field]
        self._version = definition["version"]
        self._compiled_graph = compiled_graph_authority_plan(module_graph_authority)
        self._runtime = ModuleGraphRuntime.from_compiled_authority(
            module_graph_authority,
            execution_root=execution_root,
            namespace=namespace,
            _causal_previous_root_optional=True,
        )
        prefix = PREVIOUS_CYCLE_ROOT + "."
        self._input_data_keys = tuple(
            dict.fromkeys(
                boundary["dataKey"]
                for _boundary_id, boundary in sorted(
                    self._compiled_graph["inputs"].items(),
                    key=lambda item: canonical_data_key_order(
                        item[1]["dataKey"], item[0]
                    ),
                )
                if "source" not in boundary
            )
        )
        self._source_data_keys = {
            source: tuple(
                dict.fromkeys(
                    boundary["dataKey"]
                    for _boundary_id, boundary in sorted(
                        self._compiled_graph["inputs"].items(),
                        key=lambda item: canonical_data_key_order(
                            item[1]["dataKey"], item[0]
                        ),
                    )
                    if boundary.get("source") == source
                )
            )
            for source in self._compiled_graph.get("inputSources", {})
        }
        self._input_plan = tuple(
            (
                boundary.get("source")
                or (
                    "decision"
                    if boundary["dataKey"] == DECISION_TIME_DATA_KEY
                    else "previous"
                    if boundary["dataKey"].startswith(prefix)
                    else "sample"
                ),
                (
                    ()
                    if boundary["dataKey"] == DECISION_TIME_DATA_KEY
                    and "source" not in boundary
                    else split_data_path(boundary["dataKey"].removeprefix(prefix))
                    if "source" not in boundary
                    and boundary["dataKey"].startswith(prefix)
                    else split_data_path(boundary["dataKey"])
                ),
                boundary["source"] if "source" in boundary else None,
                split_data_path(boundary["dataKey"]),
            )
            for _boundary_id, boundary in sorted(
                self._compiled_graph["inputs"].items(),
                key=lambda item: canonical_data_key_order(
                    item[1]["dataKey"], item[0]
                ),
            )
        )
        self._output_data_keys = tuple(
            boundary["dataKey"]
            for boundary in compiled_graph_definition(self._compiled_graph)[
                "outputs"
            ].values()
        )
        self._observation_projection_authority = None

    def bind_observation_projection_authority(self, authority):
        """Install the composition-owned Observation projection proof."""
        if type(self) is not EnvironmentGraphRuntime:
            raise TypeError(
                "Only an Environment Graph may produce an Observation."
            )
        require_observation_projection_authority(
            authority,
            environment_authority=self._compiled_authority,
        )
        if self._observation_projection_authority is not None:
            raise RuntimeError("Observation projection authority is already bound.")
        self._observation_projection_authority = authority

    @property
    def definition(self):
        return deepcopy(self._definition)

    @property
    def compiled_graph(self):
        return deepcopy(self._compiled_graph)

    @property
    def identity_field(self):
        return self._identity_field

    @property
    def runtime_type(self):
        return self._runtime_type

    @property
    def runtime(self):
        raise AttributeError("Cycle Graph Module Runtime is Engine-owned.")

    @property
    def input_contracts(self):
        return self._runtime.input_contracts

    @property
    def output_contracts(self):
        return self._runtime.output_contracts

    @property
    def output_data_keys(self):
        return self._output_data_keys

    @property
    def input_data_keys(self):
        return self._input_data_keys

    @property
    def previous_data_keys(self):
        prefix = PREVIOUS_CYCLE_ROOT + "."
        return tuple(
            data_key.removeprefix(prefix)
            for data_key in self.input_data_keys
            if data_key.startswith(prefix)
        )

    @property
    def source_data_keys(self):
        return {
            source: tuple(data_keys)
            for source, data_keys in self._source_data_keys.items()
        }

    def _materialize_input(
        self,
        sample,
        previous_data,
        decision_time,
        source_data,
    ):
        if not isinstance(sample, Mapping):
            raise ValueError("Current cycle Sample must be an object.")
        if not isinstance(previous_data, Mapping):
            raise ValueError("Previous cycle Data Dict must be an object.")
        if PREVIOUS_CYCLE_ROOT in sample:
            raise ValueError(
                "Sampler output may not use reserved cycle root DataKey "
                f"'{PREVIOUS_CYCLE_ROOT}'."
            )
        if DECISION_TIME_DATA_KEY in sample:
            raise ValueError(
                "Sampler output may not use reserved cycle DataKey "
                f"'{DECISION_TIME_DATA_KEY}'."
            )
        if not isinstance(decision_time, str) or not decision_time.strip():
            raise ValueError("Cycle decisionTime must be a non-empty string.")
        if source_data is None:
            source_data = {}
        if not isinstance(source_data, Mapping):
            raise ValueError("Cycle Graph input sources must be an object.")
        declared_sources = set(self._source_data_keys)
        if set(source_data) != declared_sources:
            missing_sources = sorted(declared_sources - set(source_data))
            unexpected_sources = sorted(set(source_data) - declared_sources)
            details = []
            if missing_sources:
                details.append("missing " + ", ".join(missing_sources))
            if unexpected_sources:
                details.append("unexpected " + ", ".join(unexpected_sources))
            raise ValueError(
                "Cycle Graph input sources must exactly match its compiled sources: "
                + "; ".join(details)
            )
        for source, data in source_data.items():
            if not isinstance(data, Mapping):
                raise ValueError(
                    f"Cycle Graph input source '{source}' must be a Data Dict object."
                )
        graph_input = {}
        missing = object()
        for (
            source,
            source_segments,
            external_source,
            target_segments,
        ) in self._input_plan:
            if external_source is not None:
                # Named sources are read exactly once by ModuleGraphRuntime.
                # Their shape and exact source set were validated above.
                continue
            if source == "decision":
                value = decision_time
            elif source == "previous":
                value = get_data_segments(previous_data, source_segments, missing)
            else:
                value = get_data_segments(sample, source_segments, missing)
            if value is not missing:
                # ModuleGraphRuntime reads named sources directly. The default
                # cycle input remains one ordinary Data Dict.
                set_data_segments(graph_input, target_segments, value)
        return graph_input

    def execute(self, sample, previous_data, decision_time, *, source_data=None):
        self._require_executable()
        graph_input = self._materialize_input(
            sample, previous_data, decision_time, source_data
        )
        return self._runtime.execute_outputs(
            graph_input,
            input_sources=source_data,
        )

    def execute_observation(
        self,
        sample,
        previous_data,
        decision_time,
        *,
        source_data=None,
    ):
        """Synchronously seal one fully proved Environment Observation."""
        if type(self) is not EnvironmentGraphRuntime:
            raise TypeError(
                "Only an Environment Graph may produce an Observation."
            )
        if self._observation_projection_authority is None:
            raise RuntimeError(
                "Environment is not bound to an Observation projection authority."
            )
        data = self.execute(
            sample,
            previous_data,
            decision_time,
            source_data=source_data,
        )
        return seal_validated_observation(
            self._observation_projection_authority,
            data,
        )

    def execute_into(
        self,
        sample,
        previous_data,
        decision_time,
        target,
        *,
        source_data=None,
    ):
        """Execute the Cycle Graph, write target and return isolated outputs."""
        self._require_executable()
        if not isinstance(target, dict):
            raise ValueError("Cycle Graph export target must be a Data Dict object.")
        graph_input = self._materialize_input(
            sample, previous_data, decision_time, source_data
        )
        return self._runtime.execute_outputs_into(
            graph_input,
            target,
            input_sources=source_data,
        )

    def finalize(self):
        return self._runtime.finalize()

    def _require_executable(self):
        if self._runtime.closed:
            raise RuntimeError("Cannot execute a closed Cycle Graph.")
        if self._runtime.finalized:
            raise RuntimeError("Cannot execute a finalized Cycle Graph.")

    def metadata(self):
        return {
            **self._runtime.metadata(),
            self._identity_field: self._identity,
            "version": self._version,
            "type": self._runtime_type,
        }

    def close(self):
        self._runtime.close()


class EnvironmentGraphRuntime(CycleGraphRuntime):
    """Run one Environment Graph from an Engine-owned compiled authority."""

    @classmethod
    def from_compiled_authority(
        cls, compiled_authority, *, execution_root=None
    ):
        return cls._from_compiled_authority(
            compiled_authority,
            allowed_kind="Environment",
            graph_label="Environment Graph",
            namespace="environment-graph",
            identity_field="environmentId",
            runtime_type="EnvironmentGraph",
            execution_root=execution_root,
        )


class AnalysisGraphRuntime(CycleGraphRuntime):
    """Run Analysis once with prior-cycle and completed-Pipeline inputs."""

    @classmethod
    def from_compiled_authority(
        cls, compiled_authority, *, execution_root=None
    ):
        return cls._from_compiled_authority(
            compiled_authority,
            allowed_kind="Analyzer",
            graph_label="Analysis Graph",
            namespace="analysis-graph",
            identity_field="analysisId",
            runtime_type="AnalysisGraph",
            execution_root=execution_root,
        )
