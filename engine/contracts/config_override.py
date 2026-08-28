"""Backtest-level overrides for resource-owned and inner Module config."""

from __future__ import annotations

import copy

from engine.contracts import strict_json
from engine.contracts.observation_input import normalize_pipeline_config


def normalize_resource_config_override(value, *, label="configOverride"):
    """Return one detached sparse override for resource-owned config."""

    if type(value) is not dict:
        raise ValueError(f"{label} must be an object.")
    if not strict_json.is_exact_json(value, reject_aliases=True):
        raise ValueError(f"{label} must be finite exact JSON data.")
    return strict_json.loads(strict_json.dumps(value))


def normalize_module_config_overrides(value, *, label="moduleConfigOverrides"):
    """Return one detached instanceId -> partial Module config override."""

    if type(value) is not dict:
        raise ValueError(f"{label} must be an object keyed by instanceId.")
    if not strict_json.is_exact_json(value, reject_aliases=True):
        raise ValueError(f"{label} must be finite exact JSON data.")
    normalized = strict_json.loads(strict_json.dumps(value))
    for instance_id, config in normalized.items():
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError(f"{label} instance IDs must be non-empty strings.")
        if type(config) is not dict:
            raise ValueError(f"{label}.{instance_id} must be an object.")
    return normalized


def _merge_config(default, override):
    result = copy.deepcopy(default)
    for key, value in override.items():
        if type(value) is dict and type(result.get(key)) is dict:
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def apply_pipeline_config_override(definition, override, *, label="Pipeline"):
    """Apply Pipeline-owned config without changing its Module composition."""

    normalized = normalize_resource_config_override(
        override,
        label=f"{label}.configOverride",
    )
    if type(definition) is not dict or type(definition.get("config")) is not dict:
        raise ValueError(f"{label} Definition config must be an object.")
    effective = copy.deepcopy(definition)
    effective["config"] = normalize_pipeline_config(
        _merge_config(effective["config"], normalized)
    )
    return effective


def apply_module_config_overrides(definition, override, *, label):
    """Apply only inner Module config changes to one resource definition."""

    normalized = normalize_module_config_overrides(
        override,
        label=f"{label}.moduleConfigOverrides",
    )
    if type(definition) is not dict or type(definition.get("instances")) is not dict:
        raise ValueError(f"{label} Definition instances must be an object.")
    unknown = sorted(set(normalized) - set(definition["instances"]))
    if unknown:
        raise ValueError(
            f"{label}.moduleConfigOverrides references unknown instance(s): "
            + ", ".join(unknown)
        )
    effective = copy.deepcopy(definition)
    for instance_id, partial in normalized.items():
        current = effective["instances"][instance_id].get("config")
        if type(current) is not dict:
            raise ValueError(
                f"{label} Module instance '{instance_id}'.config must be an object."
            )
        effective["instances"][instance_id]["config"] = _merge_config(
            current,
            partial,
        )
    return effective


def effective_module_configs(definition, *, label):
    """Return the complete effective config for every inner Module instance."""

    if type(definition) is not dict or type(definition.get("instances")) is not dict:
        raise ValueError(f"{label} Definition instances must be an object.")
    configs = {}
    for instance_id, instance in sorted(definition["instances"].items()):
        if type(instance) is not dict or type(instance.get("config")) is not dict:
            raise ValueError(
                f"{label} Module instance '{instance_id}'.config must be an object."
            )
        configs[instance_id] = copy.deepcopy(instance["config"])
    return configs


def apply_pipeline_manifest_overrides(
    base_definition,
    base_manifest,
    effective_definition,
):
    """Project effective Pipeline and Module config onto a verified manifest."""

    require_pipeline_override_derivation(
        base_definition,
        effective_definition,
        label="Pipeline",
    )
    if type(base_manifest) is not dict or type(base_manifest.get("modules")) is not list:
        raise ValueError("Pipeline manifest modules must be an array.")
    manifest = copy.deepcopy(base_manifest)
    manifest["config"] = copy.deepcopy(effective_definition["config"])
    instances = effective_definition["instances"]
    module_ids = []
    for module in manifest["modules"]:
        if type(module) is not dict or not isinstance(module.get("key"), str):
            raise ValueError("Pipeline manifest contains an invalid Module binding.")
        instance_id = module["key"]
        if instance_id not in instances:
            raise ValueError(
                f"Pipeline manifest references unknown instance '{instance_id}'."
            )
        module["config"] = copy.deepcopy(instances[instance_id]["config"])
        module_ids.append(instance_id)
    if set(module_ids) != set(instances):
        raise ValueError(
            "Pipeline manifest Modules do not exactly match its Definition instances."
        )
    return manifest


def require_pipeline_manifest_override_derivation(base_manifest, effective_manifest):
    """Prove a Pipeline manifest differs only in owned and Module config values."""

    base = copy.deepcopy(base_manifest)
    effective = copy.deepcopy(effective_manifest)
    if type(base.get("modules")) is not list or type(effective.get("modules")) is not list:
        raise ValueError("Configured Pipeline manifest modules must be arrays.")
    if len(base["modules"]) != len(effective["modules"]):
        raise ValueError("Configured Pipeline manifest modules do not match its Definition.")
    if type(base.get("config")) is not dict or type(effective.get("config")) is not dict:
        raise ValueError("Configured Pipeline manifest config must be an object.")
    effective["config"] = copy.deepcopy(base["config"])
    for base_module, effective_module in zip(base["modules"], effective["modules"]):
        effective_module["config"] = copy.deepcopy(base_module["config"])
    if not strict_json.exact_equal(base, effective):
        raise ValueError(
            "Configured Pipeline manifest may only override Pipeline and inner "
            "Module config."
        )
    return effective_manifest


def require_module_config_only_derivation(
    base_definition,
    effective_definition,
    *,
    label,
):
    """Prove an effective definition differs only in instance config values."""

    if type(base_definition) is not dict or type(effective_definition) is not dict:
        raise ValueError(f"{label} definitions must be objects.")
    base = copy.deepcopy(base_definition)
    effective = copy.deepcopy(effective_definition)
    base_instances = base.get("instances")
    effective_instances = effective.get("instances")
    if type(base_instances) is not dict or type(effective_instances) is not dict:
        raise ValueError(f"{label} Definition instances must be objects.")
    if set(base_instances) != set(effective_instances):
        raise ValueError(f"{label} configured instances do not match its Definition.")
    for instance_id in base_instances:
        if type(base_instances[instance_id]) is not dict or type(
            effective_instances[instance_id]
        ) is not dict:
            raise ValueError(f"{label} Module instances must be objects.")
        if type(base_instances[instance_id].get("config")) is not dict or type(
            effective_instances[instance_id].get("config")
        ) is not dict:
            raise ValueError(
                f"{label} Module instance '{instance_id}'.config must be an object."
            )
        effective_instances[instance_id]["config"] = copy.deepcopy(
            base_instances[instance_id]["config"]
        )
    if not strict_json.exact_equal(base, effective):
        raise ValueError(
            f"{label} configured Definition may only override inner Module config."
        )
    return effective_definition


def require_pipeline_override_derivation(
    base_definition,
    effective_definition,
    *,
    label="Pipeline",
):
    """Prove only Pipeline-owned config and Module configs changed."""

    if type(base_definition) is not dict or type(effective_definition) is not dict:
        raise ValueError(f"{label} definitions must be objects.")
    base = copy.deepcopy(base_definition)
    effective = copy.deepcopy(effective_definition)
    if type(base.get("config")) is not dict or type(effective.get("config")) is not dict:
        raise ValueError(f"{label} Definition config must be an object.")
    effective["config"] = copy.deepcopy(base["config"])
    return require_module_config_only_derivation(
        base,
        effective,
        label=label,
    )


__all__ = (
    "apply_module_config_overrides",
    "apply_pipeline_config_override",
    "apply_pipeline_manifest_overrides",
    "effective_module_configs",
    "normalize_module_config_overrides",
    "normalize_resource_config_override",
    "require_module_config_only_derivation",
    "require_pipeline_manifest_override_derivation",
    "require_pipeline_override_derivation",
)
