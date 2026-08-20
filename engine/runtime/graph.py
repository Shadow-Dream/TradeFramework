"""Authority-bound execution runtime for one compiled Module Graph."""

from __future__ import annotations

import copy
from collections.abc import Mapping
import time

from engine.contracts import strict_json
from engine.authority.graph import (
    compiled_graph_authority_material,
    require_compiled_graph_authority,
)
from engine.contracts.data import (
    compile_declared_data_json_proof,
    compile_normalized_json_validator,
)
from engine.contracts.contract_expansion import expanded_contract_path_required
from engine.contracts.data_path import (
    canonical_data_key_order,
    get_data_segments,
    set_data_segments_copy_on_write,
    split_data_path,
)
from engine.contracts.graph import compiled_graph_output_plan
from engine.contracts.graph_cycle import PREVIOUS_CYCLE_ROOT
from engine.contracts.module import (
    PROTOCOL_VERSION,
    definition_key,
)
from engine.runtime import module_invoker as _module_invoker
from engine.runtime.module_invoker import (
    seal_runtime_validated_module_inputs as _seal_runtime_validated_module_inputs,
)


__all__ = ("ModuleGraphRuntime",)


class ModuleGraphRuntime:
    """Execute a compiled Graph while keeping internal wires behind its boundary."""

    def __init__(self, *_args, **_kwargs):
        raise TypeError(
            "Module Graph Runtime requires an Engine-owned compiled authority."
        )

    @classmethod
    def from_compiled_authority(
        cls,
        authority,
        *,
        execution_root=None,
        namespace="module-graph",
        _causal_previous_root_optional=False,
    ):
        """Create a Runtime from the exact plan compiled in this call stack."""
        try:
            require_compiled_graph_authority(authority)
        except TypeError:
            raise TypeError(
                "Module Graph Runtime requires compiled Graph authority."
            ) from None
        (
            plan,
            definitions,
            invocation_authorities,
        ) = compiled_graph_authority_material(authority)
        runtime = cls.__new__(cls)
        runtime._initialize_from_verified_authority(
            plan,
            definitions,
            invocation_authorities,
            execution_root=execution_root,
            namespace=namespace,
            _causal_previous_root_optional=_causal_previous_root_optional,
        )
        return runtime

    def _initialize_from_verified_authority(
        self,
        plan,
        definitions,
        invocation_authorities,
        *,
        execution_root,
        namespace,
        _causal_previous_root_optional=False,
    ):
        self._plan = plan
        self._definitions = definitions
        self._invocation_authorities = invocation_authorities
        self._invokers = {}
        self._missing = object()
        self._wire_slots = {}
        self._wire_validation_plan = ()
        self._input_contract_proof = None
        self._input_source_contract_proofs = {}
        self._slot_wires = []
        self._input_plan = []
        self._output_plan = []
        self._node_plan = []
        self.execution_seconds = 0.0
        self.module_dispatch_seconds = 0.0
        self._has_executed = False
        self._finalized = False
        self._closed = False
        if type(_causal_previous_root_optional) is not bool:
            raise TypeError("Causal previous-root policy must be boolean.")
        self._optional_entry_roots = frozenset(
            {PREVIOUS_CYCLE_ROOT}
            if _causal_previous_root_optional
            and PREVIOUS_CYCLE_ROOT in self._plan["inputContracts"]
            else ()
        )
        try:
            for node_id in self._plan["topology"]:
                invocation_authority = self._invocation_authorities[node_id]
                self._invokers[node_id] = (
                    _module_invoker.ModuleInvoker.from_authority(
                    invocation_authority,
                    execution_root=execution_root,
                    namespace=namespace,
                )
                )
            self._compile_runtime_plan()
        except BaseException:
            cleanup_errors = []
            for invoker in reversed(list(self._invokers.values())):
                try:
                    invoker.close()
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            self._closed = True
            raise

    def _compile_runtime_plan(self):
        """Bind DataKey paths and internal wires once for every later cycle."""
        def boundary_is_required(boundary, contracts, required_roots):
            return expanded_contract_path_required(
                contracts,
                boundary["dataKey"],
                required_roots=required_roots,
            )

        default_boundaries = tuple(
            boundary["dataKey"]
            for boundary in self._plan["inputs"].values()
            if "source" not in boundary
        )
        default_required_roots = (
            set(self._plan["inputRequiredRoots"])
            - self._optional_entry_roots
        )
        self._input_contract_proof = compile_declared_data_json_proof(
            self._plan["inputContracts"],
            required_roots=default_required_roots,
            contracts_expanded=True,
            path="Module Graph input",
            boundary_paths=default_boundaries,
            required_paths=(
                boundary["dataKey"]
                for boundary in self._plan["inputs"].values()
                if "source" not in boundary
                and boundary_is_required(
                    boundary,
                    self._plan["inputContracts"],
                    default_required_roots,
                )
            ),
            conditional_required_paths=(
                boundary["dataKey"]
                for boundary in self._plan["inputs"].values()
                if "source" not in boundary
                and boundary["dataKey"].split(".", 1)[0]
                in self._optional_entry_roots
                and boundary_is_required(
                    boundary,
                    self._plan["inputContracts"],
                    self._optional_entry_roots,
                )
            ),
        )
        self._input_source_contract_proofs = {
            source: compile_declared_data_json_proof(
                state["contracts"],
                required_roots=state["requiredRoots"],
                contracts_expanded=True,
                path=f"Module Graph input source '{source}'",
                boundary_paths=(
                    boundary["dataKey"]
                    for boundary in self._plan["inputs"].values()
                    if boundary.get("source") == source
                ),
                required_paths=(
                    boundary["dataKey"]
                    for boundary in self._plan["inputs"].values()
                    if boundary.get("source") == source
                    and boundary_is_required(
                        boundary,
                        state["contracts"],
                        state["requiredRoots"],
                    )
                ),
            )
            for source, state in sorted(self._plan.get("inputSources", {}).items())
        }
        self._wire_slots = {}
        wire_ids = []

        def add_wire(wire_id):
            if wire_id not in self._wire_slots:
                self._wire_slots[wire_id] = len(wire_ids)
                wire_ids.append(wire_id)

        for boundary in self._plan["inputs"].values():
            add_wire(boundary["wire"])
        for node_id in self._plan["topology"]:
            binding = self._plan["bindings"][node_id]
            for wire_id in binding["inputs"].values():
                add_wire(wire_id)
            for wire_id in binding["outputs"].values():
                add_wire(wire_id)
        for boundary in self._plan["outputs"].values():
            add_wire(boundary["wire"])

        self._slot_wires = wire_ids
        boundary_nodes = set(self._plan["inputs"])
        contract_proven_boundaries = set()
        for boundary_id, boundary in self._plan["inputs"].items():
            source = boundary.get("source")
            contracts = (
                self._plan["inputContracts"]
                if source is None
                else self._plan["inputSources"][source]["contracts"]
            )
            boundary_path = boundary["dataKey"]
            root = split_data_path(boundary_path)[0]
            if (
                root in contracts
                and boundary_path in contracts
            ):
                contract_proven_boundaries.add(boundary_id)
        validation_schemas = [dict() for _wire_id in wire_ids]
        for edge in self._plan["edges"]:
            boundary_id = edge["from"]["node"]
            if boundary_id not in boundary_nodes:
                continue
            if boundary_id in contract_proven_boundaries:
                # The complete root proof covers this boundary path.  Avoid a
                # second traversal of the same value before its first consumer.
                continue
            slot = self._wire_slots[edge["wire"]]
            schema = edge["from"]["schema"]
            schema_key = strict_json.dumps(
                schema,
                sort_keys=True,
                separators=(",", ":"),
            )
            if schema_key in validation_schemas[slot]:
                continue
            consumer = edge["to"]
            path = (
                f"{consumer['node']}.inputs.{consumer['port']}"
                if consumer["node"] in self._plan["bindings"]
                else f"Graph.inputs.{boundary_id}"
            )
            validation_schemas[slot][schema_key] = (
                compile_normalized_json_validator(
                    schema,
                    path=path,
                    trusted_json=False,
                )
            )
        self._wire_validation_plan = tuple(
            tuple(validators.values())
            for validators in validation_schemas
        )
        self._input_plan = [
            (
                boundary.get("source"),
                split_data_path(boundary["dataKey"]),
                self._wire_slots[boundary["wire"]],
                boundary_id in contract_proven_boundaries,
            )
            for boundary_id, boundary in sorted(
                self._plan["inputs"].items(),
                key=lambda item: canonical_data_key_order(
                    item[1]["dataKey"], item[0]
                ),
            )
        ]
        self._output_plan = [
            (
                boundary_id,
                split_data_path(data_key),
                self._wire_slots[wire_id],
                wire_id,
                required,
            )
            for boundary_id, data_key, wire_id, required
            in compiled_graph_output_plan(self._plan)
        ]
        self._node_plan = []
        for node_id in self._plan["topology"]:
            binding = self._plan["bindings"][node_id]
            binding_inputs = binding["inputs"]
            key = definition_key(
                binding["kind"], binding["moduleId"], binding["version"]
            )
            input_ports = self._definitions[key]["ports"]["inputs"]
            input_plan = tuple(
                (
                    port_name,
                    self._wire_slots[wire_id],
                    wire_id,
                    input_ports[port_name]["required"],
                )
                for port_name, wire_id in sorted(binding_inputs.items())
            )
            output_plan = tuple(
                (port_name, self._wire_slots[wire_id], wire_id)
                for port_name, wire_id in sorted(binding["outputs"].items())
            )
            self._node_plan.append((
                node_id,
                self._invokers[node_id],
                input_plan,
                output_plan,
            ))

    @property
    def plan(self):
        return copy.deepcopy(self._plan)

    @property
    def definitions(self):
        return copy.deepcopy(self._definitions)

    @property
    def invokers(self):
        raise AttributeError("Module Graph invokers are Engine-owned resources.")

    @property
    def has_executed(self):
        return self._has_executed

    @property
    def finalized(self):
        return self._finalized

    @property
    def closed(self):
        return self._closed

    @property
    def input_contracts(self):
        return copy.deepcopy(self._plan["inputContracts"])

    @property
    def input_source_contracts(self):
        return copy.deepcopy(self._plan.get("inputSources", {}))

    @property
    def output_contracts(self):
        return copy.deepcopy(self._plan["outputContracts"])

    def _execute_slots(
        self,
        initial_data,
        target=None,
        *,
        collect_outputs=False,
        input_sources=None,
    ):
        if self._closed:
            raise RuntimeError("Cannot execute a closed Module Graph.")
        if self._finalized:
            raise RuntimeError("Cannot execute a finalized Module Graph.")
        if not isinstance(initial_data, Mapping):
            raise ValueError("Module Graph input must be an object.")
        if input_sources is None:
            input_sources = {}
        if not isinstance(input_sources, Mapping):
            raise ValueError("Module Graph input sources must be an object.")
        declared_sources = set(self._plan.get("inputSources", {}))
        if set(input_sources) != declared_sources:
            missing_sources = sorted(declared_sources - set(input_sources))
            unexpected_sources = sorted(set(input_sources) - declared_sources)
            details = []
            if missing_sources:
                details.append("missing " + ", ".join(missing_sources))
            if unexpected_sources:
                details.append("unexpected " + ", ".join(unexpected_sources))
            raise ValueError(
                "Module Graph input sources must exactly match its compiled sources: "
                + "; ".join(details)
            )
        for source, value in input_sources.items():
            if not isinstance(value, Mapping):
                raise ValueError(
                    f"Module Graph input source '{source}' must be a Data Dict object."
                )
        if target is not None and not isinstance(target, dict):
            raise ValueError("Module Graph export target must be a Data Dict object.")
        default_presence, default_values = self._input_contract_proof
        default_presence(initial_data)
        for source, proof in self._input_source_contract_proofs.items():
            proof[0](input_sources[source])
        default_values(initial_data)
        for source, proof in self._input_source_contract_proofs.items():
            proof[1](input_sources[source])
        execution_started = time.perf_counter()
        self._has_executed = True
        module_dispatch_seconds = 0.0
        exports = {} if collect_outputs else None
        missing = self._missing
        values = [missing] * len(self._slot_wires)
        proven = [False] * len(self._slot_wires)

        def prove_wire(slot, value):
            if proven[slot]:
                return
            validators = self._wire_validation_plan[slot]
            if not validators:
                raise RuntimeError(
                    f"Graph wire '{self._slot_wires[slot]}' has no dynamic value proof."
                )
            for validator in validators:
                validator(value)
            proven[slot] = True

        for source, data_path, slot, root_proven in self._input_plan:
            source_data = initial_data if source is None else input_sources[source]
            value = get_data_segments(source_data, data_path, missing)
            wire_id = self._slot_wires[slot]
            if value is missing:
                continue
            values[slot] = value
            proven[slot] = root_proven
        for node_id, invoker, input_plan, output_plan in self._node_plan:
            inputs = {}
            input_values = []
            for port_name, slot, wire_id, required in input_plan:
                value = values[slot]
                if value is missing:
                    if required:
                        raise ValueError(
                            f"Graph Module '{node_id}' input '{port_name}' cannot read wire '{wire_id}'."
                        )
                    continue
                inputs[port_name] = value
                input_values.append((slot, value))
            # Preserve the established error priority: required-wire presence
            # for the whole invocation is resolved before any nested value
            # schema is inspected.
            for slot, value in input_values:
                prove_wire(slot, value)
            dispatch_started = time.perf_counter()
            inputs_authority = _seal_runtime_validated_module_inputs(
                self._invocation_authorities[node_id],
                inputs,
            )
            outputs = invoker.invoke_validated(inputs_authority)
            module_dispatch_seconds += time.perf_counter() - dispatch_started
            for port_name, slot, wire_id in output_plan:
                if port_name in outputs:
                    value = outputs[port_name]
                    values[slot] = value
                    proven[slot] = True
        for boundary_id, data_path, slot, wire_id, required in self._output_plan:
            value = values[slot]
            if value is missing:
                if not required:
                    continue
                raise ValueError(
                    f"Graph Data Output '{boundary_id}' cannot read wire '{wire_id}'."
                )
            prove_wire(slot, value)
            if exports is not None:
                set_data_segments_copy_on_write(exports, data_path, value)
            if target is not None:
                set_data_segments_copy_on_write(target, data_path, value)
        self.module_dispatch_seconds += module_dispatch_seconds
        self.execution_seconds += time.perf_counter() - execution_started
        return values, exports

    def execute_outputs(self, initial_data, *, input_sources=None):
        """Execute one cycle and return only the public Graph Data Outputs."""
        outputs = {}
        self._execute_slots(
            initial_data,
            target=outputs,
            input_sources=input_sources,
        )
        return outputs

    def execute_into(self, initial_data, target, *, input_sources=None):
        """Execute once and apply boundary writes directly to a target Data Dict."""
        self._execute_slots(
            initial_data,
            target=target,
            input_sources=input_sources,
        )
        return target

    def execute_outputs_into(self, initial_data, target, *, input_sources=None):
        """Apply writes to target and also return the Graph's isolated outputs."""
        _values, exports = self._execute_slots(
            initial_data,
            target=target,
            collect_outputs=True,
            input_sources=input_sources,
        )
        return exports

    def execute(self, initial_data, *, input_sources=None):
        """Execute one cycle while preserving the diagnostic workingData result."""
        exports = {}
        values, _unused = self._execute_slots(
            initial_data,
            target=exports,
            input_sources=input_sources,
        )
        working = {}
        for slot, value in enumerate(values):
            if value is not self._missing:
                # Wires are opaque Graph-local IDs, not DataKey paths.  Keep
                # diagnostics as a flat wire map so legal IDs such as ``a:b``
                # or ``a/b`` never enter the DataKey parser.
                working[self._slot_wires[slot]] = value
        return {"workingData": working, "outputs": exports}

    def finalize(self):
        if self._closed:
            raise RuntimeError("Cannot finalize a closed Module Graph.")
        if self._finalized:
            raise RuntimeError("Cannot finalize a finalized Module Graph.")
        self._finalized = True
        first_error = None
        for node_id in reversed(self._plan["topology"]):
            try:
                self._invokers[node_id].finalize()
            except BaseException as exc:
                first_error = first_error or exc
        if first_error:
            raise first_error

    def snapshot(self):
        if self._closed:
            raise RuntimeError("Cannot snapshot a closed Module Graph.")
        if self._finalized:
            raise RuntimeError("Cannot snapshot a finalized Module Graph.")
        return {
            node_id: invoker.snapshot()
            for node_id, invoker in self._invokers.items()
        }

    def restore(self, snapshots):
        if self._closed:
            raise RuntimeError("Cannot restore a closed Module Graph.")
        if self._finalized:
            raise RuntimeError("Cannot restore a finalized Module Graph.")
        if not isinstance(snapshots, dict):
            raise ValueError("Graph snapshot must be an object.")
        if set(snapshots) != set(self._invokers):
            raise ValueError("Graph snapshot must exactly match its Modules.")
        for node_id in self._plan["topology"]:
            self._invokers[node_id].restore(snapshots[node_id])

    def metadata(self):
        module_transports = {}
        for node_id, invoker in self._invokers.items():
            metrics = invoker.transport_metrics()
            transport = {
                "adapter": invoker.adapter_type,
                **metrics,
            }
            if metrics.get("runtimeMode") == "external-process":
                transport["protocolVersion"] = PROTOCOL_VERSION
            module_transports[node_id] = transport
        input_sources = self.input_source_contracts
        data_key_contract = {
            "inputs": self.input_contracts,
            "outputs": self.output_contracts,
        }
        if input_sources:
            data_key_contract["inputSources"] = input_sources
        return {
            "type": "ModuleGraph",
            "topology": list(self._plan["topology"]),
            "edges": copy.deepcopy(self._plan["edges"]),
            "executionSeconds": self.execution_seconds,
            "moduleDispatchSeconds": self.module_dispatch_seconds,
            "graphOverheadSeconds": max(
                0.0, self.execution_seconds - self.module_dispatch_seconds
            ),
            "moduleTransports": module_transports,
            "dataKeyContract": data_key_contract,
        }

    def close(self):
        if self._closed:
            return
        self._closed = True
        first_error = None
        for node_id in reversed(self._plan["topology"]):
            try:
                self._invokers[node_id].close()
            except BaseException as exc:
                first_error = first_error or exc
        if first_error:
            raise first_error
