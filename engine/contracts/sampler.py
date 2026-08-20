#!/usr/bin/env python3
"""Dataset-to-Sampler data contracts and declarative contract compilation."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from engine.contracts import strict_json
from engine.contracts.contract_reducer import write_contract_state
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import (
    infer_schema,
    normalize_data_key_schema,
    normalize_schema,
)


SAMPLER_DRAFT_FIELDS = frozenset({
    "samplerId",
    "name",
    "type",
    "config",
    "parameterSchema",
    "outputSchema",
    "source",
    "entryPoint",
})
SAMPLER_VERSION_FIELDS = SAMPLER_DRAFT_FIELDS | frozenset({
    "runtime",
    "builtin",
    "version",
    "status",
    "contentDigest",
    "createdAt",
    "archive",
})


def infer_sampler_parameter_schema(config_payload):
    """Infer an editable JSON Schema from a Sampler's default configuration."""

    if not isinstance(config_payload, dict):
        raise ValueError("Sampler config must be a JSON object.")

    def infer_value(value, *, root=False):
        if isinstance(value, dict):
            if not root and value:
                child_schemas = [infer_value(child) for child in value.values()]
                structural = [
                    {key: child[key] for key in child if key != "default"}
                    for child in child_schemas
                ]
                if all(child == structural[0] for child in structural[1:]):
                    return {
                        "type": "object",
                        "additionalProperties": structural[0],
                        "default": copy.deepcopy(value),
                    }
            properties = {
                str(name): infer_value(child) for name, child in value.items()
            }
            return {
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
                **({} if root else {"default": copy.deepcopy(value)}),
            }
        schema = infer_schema(value)
        schema["default"] = copy.deepcopy(value)
        return schema

    return normalize_schema(infer_value(config_payload, root=True))

def parse_sampler_instant(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("event_time and available_at must be non-empty ISO timestamps.")
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        if len(value.strip()) != 10:
            raise ValueError("event_time and available_at must include an absolute timezone.")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def require_exact_sampler_fields(value, *, allowed, required=(), label="Object"):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(
            f"{label} contains unsupported field(s): " + ", ".join(unknown)
        )
    missing = sorted(set(required) - set(value))
    if missing:
        raise ValueError(
            f"{label} is missing required field(s): " + ", ".join(missing)
        )
    return value


@dataclass(frozen=True)
class DecisionPoint:
    sequence: int
    decision_time: str


@dataclass(frozen=True)
class DatasetSample:
    data: Mapping[str, Any]
    provenance: Mapping[str, Mapping[str, Any]]
    decision_time: str = ""
    sequence: Optional[int] = None
    cycle_id: str = ""
    contract_validated: bool = False


@dataclass
class SampleFrame:
    cycle_id: str
    decision_time: str
    data: Dict[str, Any]


def compile_row_map_contract(definition, source_schema=None):
    """Compile declarative row mapping without selecting any runtime implementation."""
    if not isinstance(definition, Mapping):
        raise ValueError("RowMappingSampler definition must be an object.")
    if "config" not in definition or "outputSchema" not in definition:
        raise ValueError("RowMappingSampler requires config and outputSchema.")
    config = definition["config"]
    if not isinstance(config, Mapping):
        raise ValueError("RowMappingSampler config must be an object.")
    if set(config) != {"mapping", "includeUnmappedFields", "unmappedPrefix"}:
        raise ValueError(
            "RowMappingSampler config must contain exactly mapping, "
            "includeUnmappedFields and unmappedPrefix."
        )
    mapping = config["mapping"]
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("RowMappingSampler requires a non-empty config.mapping object.")
    if any(
        not isinstance(data_key, str)
        or not data_key
        or not isinstance(field_name, str)
        or not field_name
        for data_key, field_name in mapping.items()
    ):
        raise ValueError(
            "RowMappingSampler config.mapping must map non-empty DataKey strings to field strings."
        )
    # JSON object member order is not an execution authority.  Compile every
    # row-map request to one stable DataKey write order so that freezing the
    # request through canonical JSON cannot change parent/child write semantics.
    mapping = {data_key: mapping[data_key] for data_key in sorted(mapping)}
    include_unmapped_fields = config["includeUnmappedFields"]
    if not isinstance(include_unmapped_fields, bool):
        raise ValueError("RowMappingSampler includeUnmappedFields must be a boolean.")
    unmapped_prefix = config["unmappedPrefix"]
    if not isinstance(unmapped_prefix, str) or not unmapped_prefix:
        raise ValueError("RowMappingSampler unmappedPrefix must be a non-empty string.")
    source_schema_available = source_schema is not None
    if source_schema is None:
        source_schema = {}
    if not isinstance(source_schema, Mapping):
        raise ValueError("RowMappingSampler Dataset field schema must be an object.")
    if any(not isinstance(field_name, str) or not field_name for field_name in source_schema):
        raise ValueError("RowMappingSampler Dataset field schema contains an invalid field name.")
    normalized_source_schema = {
        field_name: normalize_schema(source_schema[field_name])
        for field_name in sorted(source_schema)
    }
    declared_schema = definition["outputSchema"]
    if not isinstance(declared_schema, Mapping):
        raise ValueError("RowMappingSampler requires a typed outputSchema object.")
    if set(declared_schema) != set(mapping):
        missing_contracts = sorted(set(mapping) - set(declared_schema))
        extra_contracts = sorted(set(declared_schema) - set(mapping))
        details = []
        if missing_contracts:
            details.append("missing DataKey(s): " + ", ".join(missing_contracts))
        if extra_contracts:
            details.append("unsupported DataKey(s): " + ", ".join(extra_contracts))
        raise ValueError("Sampler outputSchema must exactly match config.mapping: " + "; ".join(details))
    declared_leaf_contracts = {
        data_key: normalize_data_key_schema(declared_schema[data_key], path=data_key)
        for data_key in mapping
    }
    if source_schema_available:
        missing_fields = sorted(set(mapping.values()) - set(normalized_source_schema))
        if missing_fields:
            raise ValueError(
                "RowMappingSampler mapping references unknown Dataset field(s): "
                + ", ".join(missing_fields)
            )
        incompatible = sorted(
            data_key
            for data_key, field_name in mapping.items()
            if not schemas_compatible(
                normalized_source_schema[field_name], declared_leaf_contracts[data_key]
            )
        )
        if incompatible:
            raise ValueError(
                "RowMappingSampler mapping has incompatible output type(s): "
                + ", ".join(incompatible)
            )
    if include_unmapped_fields:
        if not normalized_source_schema:
            raise ValueError("includeUnmappedFields requires a typed Dataset field schema.")
        mapped_fields = set(mapping.values())
        for field_name, data_type in normalized_source_schema.items():
            if field_name not in mapped_fields:
                data_key = f"{unmapped_prefix}{field_name}"
                if data_key in declared_leaf_contracts:
                    raise ValueError(
                        f"RowMappingSampler generated duplicate DataKey '{data_key}'."
                    )
    output_contracts = {}
    output_required_roots = frozenset()
    for data_key in mapping:
        output_contracts, output_required_roots = write_contract_state(
            output_contracts,
            output_required_roots,
            data_key,
            declared_leaf_contracts[data_key],
            required=True,
        )
    if include_unmapped_fields:
        mapped_fields = set(mapping.values())
        for field_name, data_type in normalized_source_schema.items():
            if field_name in mapped_fields:
                continue
            data_key = f"{unmapped_prefix}{field_name}"
            output_contracts, output_required_roots = write_contract_state(
                output_contracts,
                output_required_roots,
                data_key,
                normalize_data_key_schema(data_type, path=data_key),
                required=True,
            )
    return {
        "mapping": mapping,
        "includeUnmappedFields": include_unmapped_fields,
        "unmappedPrefix": unmapped_prefix,
        "sourceSchema": normalized_source_schema,
        "declaredOutputContracts": output_contracts,
    }
def _validate_row_map_draft(config, output_schema, source, entry_point):
    if not isinstance(config, Mapping):
        raise ValueError("RowMappingSampler config must be an object.")
    validation_config = {**dict(config), "includeUnmappedFields": False}
    compile_row_map_contract({"config": validation_config, "outputSchema": output_schema})
    if source or entry_point:
        raise ValueError("Declarative row-map Samplers do not accept source or entryPoint.")


def _validate_python_script_draft(_config, _output_schema, source, entry_point):
    if not str(source).strip():
        raise ValueError("Python Script Sampler source is required.")
    if len(str(source).encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Python Script Sampler source exceeds 2 MiB.")
    if not str(entry_point).strip():
        raise ValueError("Python Script Sampler entryPoint is required.")
    if not str(entry_point).isidentifier():
        raise ValueError("Python Script Sampler entryPoint must be a Python identifier.")
    try:
        compile(str(source), "<sampler>", "exec")
    except SyntaxError as exc:
        raise ValueError(f"Python Script Sampler source is invalid: {exc.msg}") from exc


def _resolve_row_map_contracts(definition, effective_parameters, source_schema):
    effective = copy.deepcopy(dict(definition))
    effective["config"] = dict(effective_parameters)
    return compile_row_map_contract(effective, source_schema)["declaredOutputContracts"]


def _resolve_declared_contracts(definition, _effective_parameters, _source_schema):
    return {
        str(path): normalize_data_key_schema(schema, path=str(path))
        for path, schema in definition["outputSchema"].items()
    }



_SAMPLER_TYPE_SPECS = {
    "row-map": {
        "protocol": "row-map-in-process-v1",
        "entryAsset": "row_map_sampler_runtime.py",
        "entryPoint": "map_record",
        "assets": ("row_map_sampler_runtime.py",),
        "requiredCapabilities": ("records",),
    },
    "python-script": {
        "protocol": "python-script-jsonl-v1",
        "entryAsset": "sampler_worker.py",
        "entryPoint": "main",
        "assets": ("sampler_worker.py", "sampler_sdk.py"),
        "requiredCapabilities": (),
    },
}
_SAMPLER_PROTOCOL_SPECS = {
    spec["protocol"]: spec for spec in _SAMPLER_TYPE_SPECS.values()
}


def sampler_type_spec(sampler_type):
    sampler_type = str(sampler_type or "").strip()
    spec = _SAMPLER_TYPE_SPECS.get(sampler_type)
    if spec is None:
        raise ValueError(f"Unsupported Sampler type: {sampler_type}")
    return copy.deepcopy(spec)


def sampler_protocol_spec(protocol):
    if not isinstance(protocol, str):
        raise ValueError("Sampler runtime bundle protocol must be a string.")
    spec = _SAMPLER_PROTOCOL_SPECS.get(protocol)
    if spec is None:
        raise ValueError(f"Unsupported archived Sampler protocol: {protocol}")
    return copy.deepcopy(spec)


def validate_sampler_draft_implementation(
    sampler_type, config, output_schema, source, entry_point
):
    sampler_type = str(sampler_type or "").strip()
    if sampler_type == "row-map":
        _validate_row_map_draft(config, output_schema, source, entry_point)
        return
    if sampler_type == "python-script":
        _validate_python_script_draft(config, output_schema, source, entry_point)
        return
    raise ValueError(f"Unsupported Sampler type: {sampler_type}")


def resolve_sampler_output_contracts(
    definition, effective_parameters, source_schema, protocol
):
    if protocol == "row-map-in-process-v1":
        return _resolve_row_map_contracts(
            definition, effective_parameters, source_schema
        )
    if protocol == "python-script-jsonl-v1":
        return _resolve_declared_contracts(
            definition, effective_parameters, source_schema
        )
    sampler_protocol_spec(protocol)
    raise AssertionError("unreachable")


def canonical_sampler_parameters(parameters):
    """Remove JSON object insertion order from every Sampler protocol boundary."""
    return strict_json.loads(
        strict_json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    )
