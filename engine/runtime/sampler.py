#!/usr/bin/env python3
"""Verified Row-map and isolated Python Script Sampler runtimes."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, List, Optional

from engine.authority.sampler import sampler_runtime_bundle_material
from engine.contracts import strict_json
from engine.contracts import digest as digest_contracts
from engine.contracts.contract_expansion import contract_root_paths, expand_contracts
from engine.contracts.data import compile_data_json_validator
from engine.contracts.data_model import normalize_data_key_schema, validate_json_value
from engine.contracts.dataset import DatasetRecord
from engine.contracts.sampler import (
    DecisionPoint,
    DatasetSample,
    canonical_sampler_parameters,
    compile_row_map_contract,
    parse_sampler_instant,
    require_exact_sampler_fields,
)
from engine.runtime import sampler_process
from engine.runtime.dataset import DatasetHandle


_SAMPLER_RUNTIME_INSTANCE_TOKEN = object()


class SamplerRuntime(ABC):
    """Required runtime protocol shared by every Engine Sampler.

    ``__len__`` deliberately has an exact, generic fallback: exhaust a distinct
    counting Runtime and close it. Runtime implementations with a cheaper exact
    algorithm should override it. The formal Backtest Runtime is never iterated
    or closed by default length discovery.
    """

    @property
    @abstractmethod
    def output_data_keys(self) -> List[str]:
        """Return the declared top-level output DataKeys."""

    @property
    @abstractmethod
    def declared_output_contracts(self) -> Mapping[str, Any]:
        """Return the Sampler's declared output contracts."""

    @property
    @abstractmethod
    def output_schema(self) -> Mapping[str, Any]:
        """Return the expanded output schema used by the runtime."""

    @abstractmethod
    def __iter__(self) -> Iterator[DatasetSample]:
        """Return a fresh iteration over the Sampler's exact output."""

    @abstractmethod
    def fork_for_counting(self) -> "SamplerRuntime":
        """Return a fresh, state-isolated Runtime with identical emissions."""

    def __len__(self) -> int:
        counting_runtime = self.fork_for_counting()
        if counting_runtime is self:
            raise RuntimeError(
                "Sampler counting Runtime must not be the formal Runtime."
            )
        if not isinstance(counting_runtime, SamplerRuntime):
            raise TypeError(
                "Sampler counting fork must implement SamplerRuntime."
            )
        primary_error = None
        try:
            if type(counting_runtime) is not type(self):
                raise TypeError(
                    "Sampler counting Runtime must have the same concrete type."
                )
            return sum(1 for _sample in counting_runtime)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                counting_runtime.close()
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.__context__ = cleanup_error

    @abstractmethod
    def close(self):
        """Release all Sampler runtime authority and resources."""


class RowMappingSampler(SamplerRuntime):
    """Maps a causal Dataset record into a complete typed DataKey dictionary."""

    sampler_type = "row-map"
    __slots__ = (
        "_definition",
        "_dataset",
        "_mapping",
        "_include_unmapped_fields",
        "_unmapped_prefix",
        "_source_schema",
        "_declared_output_contracts",
        "_output_schema",
        "_validate_output",
        "_map_record",
        "_runtime_cleanup",
        "_state",
    )

    def __init__(self, *_args, **_kwargs):
        raise TypeError(
            "RowMappingSampler requires an Engine-verified Sampler runtime bundle."
        )

    @classmethod
    def _from_verified_runtime(
        cls,
        definition,
        dataset,
        source_schema,
        *,
        map_record,
        runtime_cleanup=None,
        _token,
    ):
        if _token is not _SAMPLER_RUNTIME_INSTANCE_TOKEN:
            raise TypeError("Sampler Runtime construction is Engine-owned.")
        runtime = cls.__new__(cls)
        runtime._initialize(
            definition,
            dataset,
            source_schema,
            map_record=map_record,
            runtime_cleanup=runtime_cleanup,
        )
        return runtime

    def _initialize(
        self,
        definition: Mapping[str, Any],
        dataset: DatasetHandle,
        source_schema: Mapping[str, Any] = None,
        *,
        map_record,
        runtime_cleanup=None,
    ):
        if type(dataset) is not DatasetHandle:
            raise TypeError("RowMappingSampler requires an Engine-verified DatasetHandle.")
        self._definition = copy.deepcopy(dict(definition))
        self._dataset = dataset
        compiled = compile_row_map_contract(self._definition, source_schema)
        self._mapping = copy.deepcopy(compiled["mapping"])
        self._include_unmapped_fields = compiled["includeUnmappedFields"]
        self._unmapped_prefix = compiled["unmappedPrefix"]
        self._source_schema = copy.deepcopy(compiled["sourceSchema"])
        self._declared_output_contracts = copy.deepcopy(
            compiled["declaredOutputContracts"]
        )
        self._output_schema = expand_contracts(self._declared_output_contracts)
        self._validate_output = compile_data_json_validator(
            self._output_schema,
            required_paths=contract_root_paths(self._output_schema),
            contracts_expanded=True,
        )
        if not callable(map_record):
            raise ValueError("RowMappingSampler requires a callable archived mapping implementation.")
        self._map_record = map_record
        self._runtime_cleanup = runtime_cleanup
        self._state = "ready"

    @property
    def output_data_keys(self) -> List[str]:
        return list(self._output_schema)

    @property
    def declared_output_contracts(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._declared_output_contracts)

    @property
    def output_schema(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._output_schema)

    def __len__(self) -> int:
        if self._state != "ready":
            raise RuntimeError(
                "RowMappingSampler length requires a ready Runtime."
            )
        records = self._dataset.capabilities.get("records")
        if not isinstance(records, Mapping):
            raise ValueError("Row-map Sampler requires the Dataset records capability.")
        descriptor = records.get("descriptor")
        if not isinstance(descriptor, Mapping):
            raise ValueError("Dataset records capability descriptor must be an object.")
        count = descriptor.get("recordCount")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Dataset records capability recordCount is invalid.")
        return count

    def fork_for_counting(self) -> "RowMappingSampler":
        if self._state != "ready":
            raise RuntimeError(
                "RowMappingSampler counting fork requires a ready Runtime."
            )
        return self._from_verified_runtime(
            self._definition,
            self._dataset,
            self._source_schema,
            map_record=self._map_record,
            _token=_SAMPLER_RUNTIME_INSTANCE_TOKEN,
        )

    def __iter__(self) -> Iterator[DatasetSample]:
        if self._state == "closed":
            raise RuntimeError("Cannot iterate a closed RowMappingSampler.")
        if self._state == "running":
            raise RuntimeError("RowMappingSampler iteration is already running.")
        self._state = "running"
        try:
            yield from self._iterate()
        finally:
            if self._state == "running":
                self._state = "ready"

    def _iterate(self) -> Iterator[DatasetSample]:
        records = tuple(self._dataset.records())
        instants = tuple(parse_sampler_instant(record.available_at) for record in records)
        if any(previous > current for previous, current in zip(instants, instants[1:])):
            raise ValueError("Dataset records must be ordered by non-decreasing available_at.")
        for record in records:
            point = DecisionPoint(record.sequence, record.available_at)
            sample = self._sample(record, point)
            yield DatasetSample(
                data=sample.data,
                provenance=sample.provenance,
                decision_time=point.decision_time,
                sequence=point.sequence,
                contract_validated=True,
            )

    def _sample(self, record: DatasetRecord, point: DecisionPoint) -> DatasetSample:
        if self._state == "closed":
            raise RuntimeError("Cannot sample from a closed RowMappingSampler.")
        if not isinstance(record, DatasetRecord):
            raise ValueError("RowMappingSampler requires DatasetRecord values.")
        if parse_sampler_instant(record.available_at) > parse_sampler_instant(point.decision_time):
            raise ValueError("Sampler attempted to read a record which was not yet available.")
        data, provenance = self._map_record(
            record.values,
            sequence=record.sequence,
            event_time=record.event_time,
            available_at=record.available_at,
            mapping=self._mapping,
            include_unmapped_fields=self._include_unmapped_fields,
            unmapped_prefix=self._unmapped_prefix,
            source_fields=tuple(self._source_schema),
        )
        self._validate_output(data)
        return DatasetSample(
            data=data,
            provenance=provenance,
            contract_validated=True,
        )

    def close(self):
        if self._state == "closed":
            return
        if self._runtime_cleanup is not None:
            self._runtime_cleanup.cleanup()
            self._runtime_cleanup = None
        self._state = "closed"


class PythonScriptSampler(SamplerRuntime):
    """Runs a versioned Python Sampler which iterates an opaque Dataset container."""

    sampler_type = "python-script"
    __slots__ = (
        "_definition",
        "_dataset",
        "_parameters",
        "_source",
        "_entry_point",
        "_declared_output_contracts",
        "_output_schema",
        "_execution_root",
        "_runtime_assets",
        "_runtime_asset_digests",
        "_transport",
        "_state",
    )

    def __init__(self, *_args, **_kwargs):
        raise TypeError(
            "PythonScriptSampler requires an Engine-verified Sampler runtime bundle."
        )

    @classmethod
    def _from_verified_runtime(
        cls,
        definition,
        dataset,
        parameters=None,
        execution_root=None,
        *,
        runtime_assets,
        runtime_asset_digests,
        _token,
    ):
        if _token is not _SAMPLER_RUNTIME_INSTANCE_TOKEN:
            raise TypeError("Sampler Runtime construction is Engine-owned.")
        runtime = cls.__new__(cls)
        runtime._initialize(
            definition,
            dataset,
            parameters,
            execution_root,
            runtime_assets=runtime_assets,
            runtime_asset_digests=runtime_asset_digests,
        )
        return runtime

    def _initialize(
        self,
        definition: Mapping[str, Any],
        dataset: DatasetHandle,
        parameters: Optional[Mapping[str, Any]] = None,
        execution_root: Optional[str] = None,
        *,
        runtime_assets: Mapping[str, Path],
        runtime_asset_digests: Mapping[str, str],
    ):
        if not isinstance(definition, Mapping):
            raise ValueError("Python Script Sampler definition must be an object.")
        if parameters is not None and not isinstance(parameters, Mapping):
            raise ValueError("Python Script Sampler parameters must be an object.")
        if not isinstance(runtime_assets, Mapping):
            raise ValueError("Python Script Sampler runtime assets must be an object.")
        if not isinstance(runtime_asset_digests, Mapping):
            raise ValueError("Python Script Sampler runtime digests must be an object.")
        if type(dataset) is not DatasetHandle:
            raise TypeError("Python Script Sampler requires an Engine-verified DatasetHandle.")
        self._definition = copy.deepcopy(dict(definition))
        self._dataset = dataset
        self._parameters = (
            copy.deepcopy(dict(parameters)) if parameters is not None else {}
        )
        self._source = self._definition.get("source")
        self._entry_point = self._definition.get("entryPoint")
        if not isinstance(self._source, str) or not self._source.strip():
            raise ValueError("Python Script Sampler requires immutable source code.")
        if not isinstance(self._entry_point, str) or not self._entry_point.strip():
            raise ValueError("Python Script Sampler requires an explicit entryPoint.")
        self._entry_point = self._entry_point.strip()
        declared = self._definition.get("outputSchema")
        if not isinstance(declared, Mapping):
            raise ValueError("Python Script Sampler outputSchema must be an object.")
        self._declared_output_contracts = {
            str(path): normalize_data_key_schema(schema, path=str(path))
            for path, schema in declared.items()
        }
        self._output_schema = expand_contracts(self._declared_output_contracts)
        self._execution_root = Path(execution_root).resolve() if execution_root else None
        self._runtime_assets = {
            str(name): Path(path) for name, path in runtime_assets.items()
        }
        self._runtime_asset_digests = {
            str(name): str(digest)
            for name, digest in runtime_asset_digests.items()
        }
        missing_assets = sorted(
            {"sampler_worker.py", "sampler_sdk.py"} - set(self._runtime_assets)
        )
        if missing_assets:
            raise ValueError(
                "Python Script Sampler archive is missing runtime asset(s): "
                + ", ".join(missing_assets)
            )
        if set(self._runtime_asset_digests) != set(self._runtime_assets):
            raise ValueError(
                "Python Script Sampler runtime asset digests are incomplete."
            )
        self._transport = None
        self._state = "ready"

    @property
    def output_data_keys(self) -> List[str]:
        return list(self._output_schema)

    @property
    def declared_output_contracts(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._declared_output_contracts)

    @property
    def output_schema(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._output_schema)

    def fork_for_counting(self) -> "PythonScriptSampler":
        if self._state != "ready":
            raise RuntimeError(
                "PythonScriptSampler counting fork requires a ready Runtime."
            )
        return self._from_verified_runtime(
            self._definition,
            self._dataset,
            self._parameters,
            self._execution_root,
            runtime_assets=self._runtime_assets,
            runtime_asset_digests=self._runtime_asset_digests,
            _token=_SAMPLER_RUNTIME_INSTANCE_TOKEN,
        )

    def __iter__(self) -> Iterator[DatasetSample]:
        if self._state == "closed":
            raise RuntimeError("Cannot iterate a closed PythonScriptSampler.")
        if self._state == "running":
            raise RuntimeError("PythonScriptSampler iteration is already running.")
        self._state = "running"
        try:
            yield from self._iterate()
        finally:
            if self._state == "running":
                self._state = "ready"

    def _prepare_iteration(self, bwrap, runtime_root):
        """Build one request inside a caller-owned unique runtime directory."""
        runtime_root = Path(runtime_root)
        runtime_worker = runtime_root / "sampler_worker.py"
        runtime_sdk = runtime_root / "sampler_sdk.py"
        shutil.copy2(self._runtime_assets["sampler_worker.py"], runtime_worker)
        shutil.copy2(self._runtime_assets["sampler_sdk.py"], runtime_sdk)
        for name, copied in (
            ("sampler_worker.py", runtime_worker),
            ("sampler_sdk.py", runtime_sdk),
        ):
            if digest_contracts.sha256_file_digest(copied) != self._runtime_asset_digests[name]:
                raise ValueError(
                    f"Copied Sampler runtime asset digest mismatch: {name}"
                )
        dataset_path = self._dataset.storage_path
        dataset_root = self._dataset.root
        sandbox_storage_path = Path("/dataset")
        if dataset_path.is_file():
            sandbox_storage_path /= dataset_path.relative_to(dataset_root)
        command = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
        ]
        for system_path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(system_path).exists():
                command.extend(["--ro-bind", system_path, system_path])
        sandbox_python = sys.executable
        if sys.prefix != sys.base_prefix:
            virtualenv_root = Path(sys.prefix).resolve()
            sandbox_virtualenv = Path("/opt/trade-python")
            command.extend([
                "--dir", "/opt",
                "--ro-bind", str(virtualenv_root), str(sandbox_virtualenv),
            ])
            sandbox_python = str(
                sandbox_virtualenv / "bin" / Path(sys.executable).name
            )
        command.extend([
            "--ro-bind", str(runtime_root), "/runtime",
            "--ro-bind", str(dataset_root), "/dataset",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--remount-ro", "/",
            "--chdir", "/tmp",
            "--clearenv",
            "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
            "--setenv", "HOME", "/tmp",
            "--setenv", "TMPDIR", "/tmp",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "PYTHONPATH", "/runtime",
            sandbox_python,
            "-B",
            "/runtime/sampler_worker.py",
        ])
        dataset_descriptor = self._dataset.descriptor()
        dataset_descriptor["storagePath"] = str(sandbox_storage_path)
        dataset_descriptor["root"] = "/dataset"
        request = {
            "source": self._source,
            "entryPoint": self._entry_point,
            "dataset": dataset_descriptor,
            "parameters": copy.deepcopy(self._parameters),
        }
        return command, request

    def _iterate(self) -> Iterator[DatasetSample]:
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise RuntimeError(
                "Python Sampler sandbox is unavailable: bubblewrap is required; execution refused."
            )
        protocol_decoder = strict_json.loads
        owned_root = None
        transport = None
        try:
            parent = None
            prefix = "trade-sampler-"
            if self._execution_root is not None:
                parent = self._execution_root / "sampler"
                parent.mkdir(parents=True, exist_ok=True)
                prefix = "python-script-"
            owned_root = tempfile.TemporaryDirectory(prefix=prefix, dir=parent)
            command, request = self._prepare_iteration(
                bwrap, Path(owned_root.name)
            )
            transport = sampler_process.SamplerProcessTransport(command, request)
            self._transport = transport
            completed = False
            while True:
                raw = transport.read_line()
                if raw is None:
                    break
                try:
                    message = protocol_decoder(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    raise RuntimeError("Python Sampler emitted an invalid runtime message.") from exc
                if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                    raise RuntimeError("Python Sampler emitted an invalid runtime message schema.")
                if completed:
                    raise RuntimeError("Python Sampler emitted data after its completion message.")
                message_type = message["type"]
                if message_type == "sample":
                    try:
                        require_exact_sampler_fields(
                            message,
                            allowed={"type", "sample"},
                            required={"type", "sample"},
                            label="Python Sampler sample message",
                        )
                        payload = require_exact_sampler_fields(
                            message["sample"],
                            allowed={"decisionTime", "data", "provenance", "sequence", "cycleId"},
                            required={"decisionTime", "data", "provenance", "sequence"},
                            label="Python Sampler sample payload",
                        )
                    except ValueError as exc:
                        raise RuntimeError(str(exc)) from exc
                    decision_time = payload["decisionTime"]
                    data = payload["data"]
                    provenance = payload["provenance"]
                    sequence = payload["sequence"]
                    cycle_id = payload.get("cycleId", "")
                    if not isinstance(decision_time, str) or not decision_time.strip():
                        raise RuntimeError("Python Sampler sample decisionTime must be a non-empty string.")
                    if not isinstance(data, dict):
                        raise RuntimeError("Python Sampler sample data must be an object.")
                    if not isinstance(provenance, dict):
                        raise RuntimeError("Python Sampler sample provenance must be an object.")
                    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                        raise RuntimeError(
                            "Python Sampler sample sequence must be a non-negative integer."
                        )
                    if "cycleId" in payload and (
                        not isinstance(cycle_id, str) or not cycle_id.strip()
                    ):
                        raise RuntimeError(
                            "Python Sampler sample cycleId must be a non-empty string when present."
                        )
                    yield DatasetSample(
                        data=data,
                        provenance=provenance,
                        decision_time=decision_time,
                        sequence=sequence,
                        cycle_id=cycle_id,
                    )
                elif message_type == "complete":
                    try:
                        require_exact_sampler_fields(
                            message,
                            allowed={"type"},
                            required={"type"},
                            label="Python Sampler completion message",
                        )
                    except ValueError as exc:
                        raise RuntimeError(str(exc)) from exc
                    completed = True
                elif message_type == "error":
                    try:
                        require_exact_sampler_fields(
                            message,
                            allowed={"type", "error"},
                            required={"type", "error"},
                            label="Python Sampler error message",
                        )
                    except ValueError as exc:
                        raise RuntimeError(str(exc)) from exc
                    error = message["error"]
                    if not isinstance(error, str) or not error.strip():
                        raise RuntimeError("Python Sampler error message must be a non-empty string.")
                    raise RuntimeError(error)
                else:
                    raise RuntimeError(f"Python Sampler emitted unknown message type: {message_type}")
            return_code = transport.wait()
            if return_code != 0 or not completed:
                detail = transport.stderr_detail()[-4000:].strip()
                raise RuntimeError(
                    f"Python Sampler exited with code {return_code}: {detail or 'incomplete output'}"
                )
        finally:
            primary_error = sys.exc_info()[1]
            self._transport = None
            cleanup_error = None
            if transport is not None:
                try:
                    transport.close()
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if owned_root is not None:
                try:
                    owned_root.cleanup()
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                if primary_error is None:
                    raise cleanup_error
                primary_error.__context__ = cleanup_error

    def close(self):
        if self._state == "closed":
            return
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._state = "closed"


def _load_archived_callable(path, entry_point):
    path = Path(path).resolve()
    module_name = "trade_sampler_" + hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load archived Sampler runtime asset: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    callback = getattr(module, entry_point, None)
    if not callable(callback):
        raise ValueError(
            f"Archived Sampler runtime entry point is not callable: {entry_point}"
        )
    return callback


def _create_row_map_runtime(
    authority, dataset, effective_parameters, source_schema, execution_root
):
    definition, runtime, assets, _spec = (
        sampler_runtime_bundle_material(authority)
    )
    effective = copy.deepcopy(dict(definition))
    effective["config"] = dict(effective_parameters)
    owned_root = None
    if execution_root is None:
        owned_root = tempfile.TemporaryDirectory(prefix="trade-row-map-sampler-")
        runtime_root = Path(owned_root.name)
    else:
        runtime_root = Path(execution_root).resolve() / "sampler" / "row-map"
        runtime_root.mkdir(parents=True, exist_ok=False)
    try:
        expected_digests = {
            Path(item["path"]).name: item["sha256"] for item in runtime["assets"]
        }
        copied_assets = {}
        for name, archived_asset in assets.items():
            copied_asset = runtime_root / name
            shutil.copy2(archived_asset, copied_asset)
            if digest_contracts.sha256_file_digest(copied_asset) != expected_digests[name]:
                raise ValueError(f"Copied Sampler runtime asset digest mismatch: {name}")
            copied_assets[name] = copied_asset
        runtime_asset = copied_assets[runtime["entryAsset"]]
        mapper = _load_archived_callable(
            runtime_asset, runtime["entryPoint"]
        )
        return RowMappingSampler._from_verified_runtime(
            effective,
            dataset,
            source_schema,
            map_record=mapper,
            runtime_cleanup=owned_root,
            _token=_SAMPLER_RUNTIME_INSTANCE_TOKEN,
        )
    except BaseException as primary_error:
        if owned_root is not None:
            try:
                owned_root.cleanup()
            except BaseException as cleanup_error:
                primary_error.__context__ = cleanup_error
        raise primary_error.with_traceback(primary_error.__traceback__)


def _create_python_script_runtime(
    authority, dataset, effective_parameters, _source_schema, execution_root
):
    definition, runtime, assets, _spec = (
        sampler_runtime_bundle_material(authority)
    )
    return PythonScriptSampler._from_verified_runtime(
        definition,
        dataset,
        effective_parameters,
        execution_root,
        runtime_assets=assets,
        runtime_asset_digests={
            Path(item["path"]).name: item["sha256"]
            for item in runtime["assets"]
        },
        _token=_SAMPLER_RUNTIME_INSTANCE_TOKEN,
    )



def create_verified_sampler_runtime(
    authority,
    dataset: DatasetHandle,
    parameters: Optional[Mapping[str, Any]] = None,
    *,
    source_schema: Optional[Mapping[str, Any]] = None,
    execution_root: Optional[str] = None,
):
    definition, runtime, _assets, spec = sampler_runtime_bundle_material(authority)
    missing_capabilities = sorted(
        set(spec["requiredCapabilities"]) - set(dataset.capabilities)
    )
    if missing_capabilities:
        raise ValueError(
            "Sampler requires Dataset capability/capabilities: "
            + ", ".join(missing_capabilities)
        )
    dataset.require_semantically_validated_capabilities(
        spec["requiredCapabilities"]
    )
    config = definition["config"]
    if not isinstance(config, Mapping):
        raise ValueError("Sampler config must be an object.")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, Mapping):
        raise ValueError("Sampler parameters must be an object.")
    effective_parameters = {**dict(config), **dict(parameters)}
    validate_json_value(
        effective_parameters,
        definition["parameterSchema"],
        path="sampler.parameters",
    )
    effective_parameters = canonical_sampler_parameters(effective_parameters)
    protocol = runtime["protocol"]
    if protocol == "row-map-in-process-v1":
        factory = _create_row_map_runtime
    elif protocol == "python-script-jsonl-v1":
        factory = _create_python_script_runtime
    else:
        raise ValueError(f"Unsupported archived Sampler protocol: {protocol}")
    return factory(
        authority,
        dataset,
        effective_parameters,
        source_schema,
        execution_root,
    )
