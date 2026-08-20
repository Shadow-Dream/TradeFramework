"""Authority-bound runtime for one immutable compiled Pipeline."""

from __future__ import annotations

import copy
from time import perf_counter

from engine.authority import graph as graph_authority
from engine.authority import module_invocation as module_invocation_authority
from engine.authority import pipeline as pipeline_authority
from engine.authority.runtime_data import (
    require_observation_projection_authority,
)
from engine.contracts.data_path import (
    delete_data_segments_copy_on_write,
    get_data_segments,
    project_compiled_data_paths,
    set_data_segments_copy_on_write,
    split_data_path,
)
from engine.contracts.module import MODULE_INSTANCE_FIELDS, PROTOCOL_VERSION
from engine.contracts.pipeline import require_canonical_value_match
from engine.runtime import graph as graph_runtime
from engine.runtime import lifecycle as runtime_lifecycle
from engine.runtime import module_invoker
from engine.runtime.data_proof import consume_validated_observation


class BacktestPipelineRuntime:
    """Execute one compiled Pipeline topology against each cycle Data Dict."""

    def __init__(self, *_args, **_kwargs):
        raise TypeError(
            "Pipeline Runtime requires an Engine-owned verified composition."
        )

    @classmethod
    def from_compiled_authority(
        cls,
        authority,
        *,
        execution_root=None,
    ):
        """Create a Pipeline Runtime only from one Engine-owned authority."""

        pipeline_authority.require_compiled_pipeline_authority(authority)
        (
            pipeline_id,
            version,
            expected_hash,
            contract_template,
            compiled_contract_plan,
            signal_authority,
            direct_invocation_authorities,
        ) = pipeline_authority.compiled_pipeline_authority_material(authority)
        runtime = cls.__new__(cls)
        runtime._compiled_authority = authority
        runtime._initialize(
            pipeline_id,
            version,
            expected_hash,
            contract_template,
            compiled_contract_plan,
            signal_authority,
            direct_invocation_authorities,
            execution_root=execution_root,
        )
        return runtime

    def _initialize(
        self,
        pipeline_id,
        version,
        expected_hash,
        contract_template,
        compiled_contract_plan,
        signal_authority,
        direct_invocation_authorities,
        *,
        execution_root,
    ):
        try:
            pipeline_authority.require_pipeline_contract_template(
                contract_template
            )
        except TypeError as exc:
            raise TypeError(
                "Pipeline Runtime requires a verified Pipeline template."
            ) from exc
        if not isinstance(compiled_contract_plan, dict):
            raise TypeError("Pipeline Runtime requires a compiled Pipeline plan.")
        if not isinstance(direct_invocation_authorities, dict):
            raise TypeError(
                "Pipeline Runtime requires Module invocation authorities."
            )
        self._pipeline_id = pipeline_id
        self._execution_root = str(execution_root) if execution_root else None
        self._bindings = {}
        self._module_definitions = {}
        self._module_invokers = {}
        self._direct_invocation_authorities = {}
        self._signal_runtime = None
        self._graph_boundary_transfers = {}
        self._execution_seconds = 0.0
        self._direct_module_dispatch_seconds = 0.0
        self._node_ids = []
        self._input_contracts = {}
        self._declared_contracts = {}
        self._missing = object()
        self._state = "initializing"
        self._observation_projection_authority = None
        self._contract_template = contract_template
        template_material = pipeline_authority.pipeline_contract_template_material(
            contract_template
        )
        self._manifest = template_material["manifest"]
        self._module_definitions = template_material["moduleDefinitions"]
        self._module_definition_authorities = template_material[
            "moduleDefinitionAuthorities"
        ]
        manifest = self._manifest
        self._pipeline_binding = {
            "pipelineId": self._pipeline_id,
            "name": manifest["name"],
            "version": version,
            "manifestHash": expected_hash,
        }
        self._node_ids = list(manifest["topology"])
        self._bindings = {
            module["key"]: {
                name: copy.deepcopy(module[name])
                for name in MODULE_INSTANCE_FIELDS
            }
            for module in manifest["modules"]
        }
        self._direct_invocation_authorities = dict(
            direct_invocation_authorities
        )
        self._signal_plan = copy.deepcopy(compiled_contract_plan["signalPlan"])
        self._direct_plans = copy.deepcopy(compiled_contract_plan["directPlans"])
        self._node_ids = copy.deepcopy(compiled_contract_plan["topology"])
        self._signal_node_ids = list(self._signal_plan["topology"])
        self._pre_node_ids = [
            plan["nodeId"]
            for plan in self._direct_plans
            if plan["phase"] == "pre"
        ]
        self._post_node_ids = [
            plan["nodeId"]
            for plan in self._direct_plans
            if plan["phase"] == "post"
        ]
        if tuple(self._node_ids) != template_material["topology"]:
            raise ValueError(
                "Compiled Pipeline topology does not match its verified template."
            )
        require_canonical_value_match(
            self._direct_plans,
            list(template_material["directPlans"]),
            label="Compiled Pipeline direct plans",
        )
        required_definition_keys = {
            "/".join(module[name] for name in ("kind", "moduleId", "version"))
            for module in self._bindings.values()
        }
        if set(self._module_definitions) != required_definition_keys:
            raise ValueError(
                "Frozen Pipeline Module definitions must exactly match its manifest bindings."
            )
        self._validate_direct_plans_against_bindings()
        self._validate_direct_invocations()
        require_canonical_value_match(
            graph_authority.compiled_graph_authority_plan(signal_authority),
            self._signal_plan,
            label="Compiled Pipeline Signal Graph authority",
        )
        self._pre_node_plan = self._bind_node_execution_plan(self._pre_node_ids)
        self._post_node_plan = self._bind_node_execution_plan(
            self._post_node_ids
        )
        self._apply_contract_plan(compiled_contract_plan)
        initialized_resources = []
        try:
            for node_id in self._pre_node_ids:
                self._module_invokers[node_id] = (
                    module_invoker.ModuleInvoker.from_authority(
                        self._direct_invocation_authorities[node_id],
                        execution_root=self._execution_root,
                        namespace="pipeline-modules",
                    )
                )
                initialized_resources.append(self._module_invokers[node_id])
            self._signal_runtime = (
                graph_runtime.ModuleGraphRuntime.from_compiled_authority(
                    signal_authority,
                    execution_root=self._execution_root,
                    namespace="signal-graph",
                )
            )
            initialized_resources.append(self._signal_runtime)
            for node_id in self._post_node_ids:
                self._module_invokers[node_id] = (
                    module_invoker.ModuleInvoker.from_authority(
                        self._direct_invocation_authorities[node_id],
                        execution_root=self._execution_root,
                        namespace="pipeline-modules",
                    )
                )
                initialized_resources.append(self._module_invokers[node_id])
            self._state = "running"
        except BaseException:
            runtime_lifecycle.invoke_all(
                reversed(initialized_resources),
                "close",
                suppress_errors=True,
            )
            raise

    def _module_definition(self, binding):
        key = "/".join(
            binding[name] for name in ("kind", "moduleId", "version")
        )
        definition = self._module_definitions.get(key)
        if definition is None:
            raise ValueError(
                f"Frozen Pipeline Module definition is missing: {key}"
            )
        return definition

    def _execute_module(self, node_id, binding, inputs):
        started = perf_counter()
        try:
            return self._module_invokers[node_id].invoke(inputs)
        finally:
            self._direct_module_dispatch_seconds += perf_counter() - started

    def _bind_node_execution_plan(self, node_ids):
        plans = {plan["nodeId"]: plan for plan in self._direct_plans}
        if set(plans) != set((*self._pre_node_ids, *self._post_node_ids)):
            raise ValueError(
                "Frozen Pipeline direct plans must exactly match its direct topology."
            )
        return tuple(
            (
                node_id,
                self._bindings[node_id],
                tuple(
                    (
                        entry["port"],
                        entry["dataKey"],
                        split_data_path(entry["dataKey"]),
                        entry["required"],
                    )
                    for entry in plans[node_id]["inputs"]
                ),
                tuple(
                    (entry["port"], split_data_path(entry["dataKey"]))
                    for entry in plans[node_id]["outputs"]
                ),
            )
            for node_id in node_ids
        )

    def _validate_direct_plans_against_bindings(self):
        expected_nodes = set((*self._pre_node_ids, *self._post_node_ids))
        actual_nodes = [plan["nodeId"] for plan in self._direct_plans]
        if (
            len(actual_nodes) != len(set(actual_nodes))
            or set(actual_nodes) != expected_nodes
        ):
            raise ValueError(
                "Frozen Pipeline direct plans must exactly match its direct topology."
            )
        for plan in self._direct_plans:
            binding = self._bindings[plan["nodeId"]]
            if (
                {
                    entry["port"]: entry["dataKey"]
                    for entry in plan["inputs"]
                }
                != binding["inputs"]
                or {
                    entry["port"]: entry["dataKey"]
                    for entry in plan["outputs"]
                }
                != binding["outputs"]
            ):
                raise ValueError(
                    "Frozen Pipeline direct plan does not match its manifest binding."
                )
            definition = self._module_definition(binding)
            ports = definition["ports"]["inputs"]
            if any(
                entry["required"] != ports[entry["port"]]["required"]
                for entry in plan["inputs"]
            ):
                raise ValueError(
                    "Frozen Pipeline direct input plan does not match its Module Definition."
                )

    def _validate_direct_invocations(self):
        expected_nodes = tuple((*self._pre_node_ids, *self._post_node_ids))
        if set(self._direct_invocation_authorities) != set(expected_nodes):
            raise TypeError(
                "Compiled Pipeline Module invocations must exactly match its direct topology."
            )
        for node_id in expected_nodes:
            binding, definition_authority = (
                module_invocation_authority.module_invocation_material(
                    self._direct_invocation_authorities[node_id]
                )
            )
            expected_binding = self._bindings[node_id]
            key = "/".join(
                expected_binding[name]
                for name in ("kind", "moduleId", "version")
            )
            if binding != expected_binding or (
                definition_authority
                is not self._module_definition_authorities[key]
            ):
                raise TypeError(
                    "Compiled Pipeline Module invocation does not match its plan."
                )

    def _apply_contract_plan(self, plan):
        self._observation_input = copy.deepcopy(plan["observationInput"])
        self._observation_contract_digest = plan["observationContractDigest"]
        self._observation_whitelist_plan = tuple(
            (entry["dataKey"], tuple(entry["segments"]))
            for entry in plan["observationProjection"]["whitelist"]
        )
        self._observation_blacklist_plan = tuple(
            tuple(entry["segments"])
            for entry in plan["observationProjection"]["blacklist"]
        )
        self._input_contracts = copy.deepcopy(plan["inputContracts"])
        self._declared_contracts = copy.deepcopy(plan["outputContracts"])

    def bind_observation_projection_authority(self, authority):
        """Install the composition-owned Observation projection proof."""
        require_observation_projection_authority(
            authority,
            pipeline_authority_value=self._compiled_authority,
        )
        if self._observation_projection_authority is not None:
            raise RuntimeError("Observation projection authority is already bound.")
        self._observation_projection_authority = authority

    @property
    def pipeline_binding(self):
        return copy.deepcopy(self._pipeline_binding)

    @property
    def module_invokers(self):
        raise AttributeError("Pipeline Module invokers are Engine-owned resources.")

    @property
    def signal_runtime(self):
        raise AttributeError("Pipeline Signal Runtime is Engine-owned.")

    def _require_running(self, operation):
        if self._state == "closed":
            raise RuntimeError(f"Cannot {operation} a closed Pipeline Runtime.")
        if self._state == "finalized":
            raise RuntimeError(f"Cannot {operation} a finalized Pipeline Runtime.")
        if self._state != "running":
            raise RuntimeError(
                f"Cannot {operation} an uninitialized Pipeline Runtime."
            )

    def execute_observation(self, proof):
        """Project and execute one same-stack Environment Observation."""
        self._require_running("execute")
        if self._observation_projection_authority is None:
            raise RuntimeError("Pipeline is not bound to an Observation authority.")
        observation = consume_validated_observation(
            self._observation_projection_authority,
            proof,
        )
        data = project_compiled_data_paths(
            observation,
            self._observation_whitelist_plan,
            isolate_values=False,
        )
        for segments in self._observation_blacklist_plan:
            delete_data_segments_copy_on_write(data, segments)
        return self._execute_proven(data)

    def _execute_proven(self, data):
        execution_started = perf_counter()
        for node_id, binding, input_plan, output_plan in self._pre_node_plan:
            inputs = self._read_direct_module_inputs(node_id, data, input_plan)
            outputs = self._execute_module(node_id, binding, inputs)
            for port_name, segments in output_plan:
                if port_name in outputs:
                    set_data_segments_copy_on_write(
                        data,
                        segments,
                        outputs[port_name],
                    )
        if self._signal_runtime is None:
            raise RuntimeError(
                "Pipeline contracts must be compiled before execution."
            )
        self._signal_runtime.execute_into(data, data)
        for boundary_id in self._signal_plan["inputs"]:
            self._graph_boundary_transfers[boundary_id] = (
                self._graph_boundary_transfers.get(boundary_id, 0) + 1
            )
        for boundary_id in self._signal_plan["outputs"]:
            self._graph_boundary_transfers[boundary_id] = (
                self._graph_boundary_transfers.get(boundary_id, 0) + 1
            )
        for node_id, binding, input_plan, output_plan in self._post_node_plan:
            inputs = self._read_direct_module_inputs(node_id, data, input_plan)
            outputs = self._execute_module(node_id, binding, inputs)
            for port_name, segments in output_plan:
                if port_name in outputs:
                    set_data_segments_copy_on_write(
                        data,
                        segments,
                        outputs[port_name],
                    )
        self._execution_seconds += perf_counter() - execution_started
        return data

    def _read_direct_module_inputs(self, node_id, data, input_plan):
        inputs = {}
        for port_name, data_key, segments, required in input_plan:
            value = get_data_segments(data, segments, self._missing)
            if value is self._missing:
                if required:
                    raise ValueError(
                        f"Pipeline Module '{node_id}' input '{port_name}' "
                        f"cannot read DataKey '{data_key}'."
                    )
                continue
            inputs[port_name] = value
        return inputs

    def close(self):
        if self._state == "closed":
            return
        self._state = "closed"
        resources = [
            self._module_invokers[node_id]
            for node_id in reversed(self._post_node_ids)
        ]
        if self._signal_runtime is not None:
            resources.append(self._signal_runtime)
        resources.extend(
            self._module_invokers[node_id]
            for node_id in reversed(self._pre_node_ids)
        )
        runtime_lifecycle.invoke_all(resources, "close")

    def finalize(self):
        self._require_running("finalize")
        self._state = "finalized"
        ordered = [
            self._module_invokers[node_id]
            for node_id in reversed(self._post_node_ids)
        ]
        if self._signal_runtime is not None:
            ordered.append(self._signal_runtime)
        ordered.extend(
            self._module_invokers[node_id]
            for node_id in reversed(self._pre_node_ids)
        )
        runtime_lifecycle.invoke_all(ordered, "finalize")

    def metadata(self):
        graph_module_seconds = (
            self._signal_runtime.module_dispatch_seconds
            if self._signal_runtime is not None
            else 0.0
        )
        module_dispatch_seconds = (
            self._direct_module_dispatch_seconds + graph_module_seconds
        )
        module_transports = {
            node_id: self._module_transport_metadata(node_id, invoker)
            for node_id, invoker in self._module_invokers.items()
        }
        if self._signal_runtime is not None:
            module_transports.update(
                self._signal_runtime.metadata()["moduleTransports"]
            )
        return {
            "pipelineId": self._pipeline_id,
            "nodes": list(self._node_ids),
            "mode": "per-cycle-data-dictionary",
            "dataInterface": "declared-datakey-contracts",
            "dataKeyContract": {
                "inputs": copy.deepcopy(self._input_contracts),
                "outputs": copy.deepcopy(self._declared_contracts),
            },
            "observationInput": copy.deepcopy(self._observation_input),
            "observationContractDigest": self._observation_contract_digest,
            "graphBoundaryTransfers": dict(self._graph_boundary_transfers),
            "executionSeconds": self._execution_seconds,
            "moduleDispatchSeconds": module_dispatch_seconds,
            "graphOverheadSeconds": max(
                0.0,
                self._execution_seconds - module_dispatch_seconds,
            ),
            "moduleTransports": module_transports,
        }

    def _module_transport_metadata(self, node_id, invoker):
        metrics = invoker.transport_metrics()
        transport = {"adapter": invoker.adapter_type, **metrics}
        if metrics.get("runtimeMode") == "external-process":
            transport["protocolVersion"] = PROTOCOL_VERSION
        return transport


__all__ = ("BacktestPipelineRuntime",)
