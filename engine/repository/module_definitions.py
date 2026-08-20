"""Immutable Module Definition indexes and canonical archive locations."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

from engine.archive import version as version_archive
from engine.archive import version_evidence
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.module import (
    ANALYSIS_MODULE_KINDS,
    ENGINE_MODULE_KINDS,
    ENVIRONMENT_MODULE_KINDS,
    MODULE_RELEASE_DIRECTORIES,
    definition_key,
    validate_module_definition,
)
from engine.repository import control_state


MODULE_REPOSITORIES = {
    "pipeline": {
        "kinds": ENGINE_MODULE_KINDS,
        "stateFile": "modules.json",
    },
    "analysis": {
        "kinds": ANALYSIS_MODULE_KINDS,
        "stateFile": "analysis-modules.json",
    },
    "environment": {
        "kinds": ENVIRONMENT_MODULE_KINDS,
        "stateFile": "environment-modules.json",
    },
}


def _canonical_version(value):
    version = require_resource_path_segment(value, label="Module version")
    if not version.isdigit() or version != str(int(version)) or int(version) < 1:
        raise ValueError("Module version must be a canonical positive integer.")
    return version


def module_repository_for_kind(kind):
    kind = require_resource_path_segment(kind, label="Module kind")
    matches = [
        name for name, spec in MODULE_REPOSITORIES.items() if kind in spec["kinds"]
    ]
    if len(matches) != 1:
        raise ValueError(f"Module kind '{kind}' does not belong to one repository.")
    return matches[0]


def module_version_dir(config, kind, module_id, version):
    repository = module_repository_for_kind(kind)
    module_id = require_resource_path_segment(module_id, label="moduleId")
    version = _canonical_version(version)
    return (
        Path(config["releaseRoot"])
        / MODULE_RELEASE_DIRECTORIES[kind]
        / kind
        / module_id
        / version
    )


def _require_record_identity(definition, *, repository=None, expected=None):
    validate_module_definition(definition)
    kind = require_resource_path_segment(definition.get("kind"), label="Module kind")
    module_id = require_resource_path_segment(
        definition.get("moduleId"), label="moduleId"
    )
    version = _canonical_version(definition.get("version"))
    actual_repository = module_repository_for_kind(kind)
    if repository is not None and actual_repository != repository:
        raise ValueError(
            f"Module '{kind}/{module_id}' is indexed in the wrong repository."
        )
    if expected is not None and (kind, module_id, version) != expected:
        raise ValueError(
            "Module definition index key does not match its record: "
            + definition_key(*expected)
        )
    archive = definition.get("archive")
    if (
        not isinstance(archive, dict)
        or archive.get("resourceType") != "module"
        or archive.get("resourceId") != f"{kind}/{module_id}"
    ):
        raise ValueError(
            "Archived Module Definition identity does not match its record: "
            f"{kind}/{module_id}/{version}"
        )
    return kind, module_id, version


def verify_repository(config, repository, records):
    verified, _evidence = verify_repository_evidence(
        config, repository, records
    )
    return verified


def verify_repository_evidence(config, repository, records):
    """Verify one complete index and retain detached per-record evidence."""
    spec = MODULE_REPOSITORIES.get(repository)
    if spec is None:
        raise ValueError(f"Unknown Module repository: {repository}")
    if not isinstance(records, Mapping):
        raise ValueError(f"{repository.title()} Module version index must be an object.")
    for definition in records.values():
        _require_record_identity(definition, repository=repository)
    return version_evidence.verify_record_index_location_evidence(
        records,
        ("kind", "moduleId"),
        managed_root=config["releaseRoot"],
        expected_root_for=lambda definition: module_version_dir(
            config,
            definition["kind"],
            definition["moduleId"],
            definition["version"],
        ),
        immutable_fields=("builtin",),
    )


def load_repository(config, repository):
    records, _evidence = load_repository_evidence(config, repository)
    return records


def load_repository_evidence(config, repository):
    spec = MODULE_REPOSITORIES.get(repository)
    if spec is None:
        raise ValueError(f"Unknown Module repository: {repository}")
    with control_state.control_state_lock(config):
        records = control_state.load_state(config, spec["stateFile"], {})
        verified, evidence = verify_repository_evidence(
            config, repository, records
        )
        return deepcopy(verified), evidence


def load_pipeline_definitions(config):
    return load_repository(config, "pipeline")


def load_analysis_definitions(config):
    return load_repository(config, "analysis")


def load_environment_definitions(config):
    return load_repository(config, "environment")


def load_all_definitions(config):
    return {
        **load_pipeline_definitions(config),
        **load_analysis_definitions(config),
        **load_environment_definitions(config),
    }


def module_references(instances):
    """Return the unique exact Module versions named by an instance index."""
    if instances is None:
        return ()
    if not isinstance(instances, Mapping):
        raise ValueError("Module instances must be an object.")
    references = []
    for instance in instances.values():
        if not isinstance(instance, Mapping):
            raise ValueError("Module instance must be an object.")
        reference = tuple(instance.get(field) for field in ("kind", "moduleId", "version"))
        if any(not isinstance(value, str) or not value for value in reference):
            raise ValueError(
                "Module instances must contain kind/moduleId/version strings."
            )
        references.append(reference)
    return tuple(dict.fromkeys(references))


def load_definition_versions(config, references):
    """Load exact indexed versions and return detached location evidence."""
    if isinstance(references, (str, bytes)):
        raise ValueError("Module Definition references must be a collection.")
    normalized = []
    for reference in references:
        if (
            not isinstance(reference, (tuple, list))
            or len(reference) != 3
            or any(not isinstance(value, str) or not value for value in reference)
        ):
            raise ValueError(
                "Module Definition references must contain kind/moduleId/version strings."
            )
        kind, module_id, version = reference
        kind = require_resource_path_segment(kind, label="Module kind")
        module_id = require_resource_path_segment(module_id, label="moduleId")
        version = _canonical_version(version)
        module_repository_for_kind(kind)
        normalized.append((kind, module_id, version))
    normalized = tuple(dict.fromkeys(normalized))

    definitions = {}
    evidence = {}
    indexes = {}
    with control_state.control_state_lock(config):
        for kind, module_id, version in sorted(normalized):
            repository = module_repository_for_kind(kind)
            if repository not in indexes:
                records = control_state.load_state(
                    config,
                    MODULE_REPOSITORIES[repository]["stateFile"],
                    {},
                )
                if not isinstance(records, dict):
                    raise ValueError(
                        f"{repository.title()} Module version index must be an object."
                    )
                indexes[repository] = records
            key = definition_key(kind, module_id, version)
            definition = indexes[repository].get(key)
            if not isinstance(definition, dict):
                raise ValueError(f"Module definition does not exist: {key}")
            _require_record_identity(
                definition,
                repository=repository,
                expected=(kind, module_id, version),
            )
            proof = version_evidence.verify_record_location_evidence(
                definition,
                managed_root=config["releaseRoot"],
                expected_root=module_version_dir(
                    config, kind, module_id, version
                ),
            )
            definitions[key] = deepcopy(definition)
            evidence[key] = proof
    return definitions, evidence


def get_definition(definitions, kind, module_id, version):
    key = definition_key(kind, module_id, version)
    definition = definitions.get(key)
    if definition is None:
        raise ValueError(f"Module definition does not exist: {key}")
    return definition


__all__ = (
    "MODULE_REPOSITORIES",
    "get_definition",
    "load_all_definitions",
    "load_analysis_definitions",
    "load_definition_versions",
    "load_environment_definitions",
    "load_pipeline_definitions",
    "load_repository",
    "load_repository_evidence",
    "module_references",
    "module_repository_for_kind",
    "module_version_dir",
    "verify_repository",
    "verify_repository_evidence",
)
