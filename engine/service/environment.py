"""Validated Environment definition queries for outer use-case composition."""

from __future__ import annotations

import copy

from engine.authority import module_definition as module_definition_authority
from engine.compiler.environment import validate_environment_definition_authority
from engine.repository import graph_resources
from engine.repository import module_definitions


def _module_authorities(config, definitions):
    references = []
    for definition in definitions:
        references.extend(
            module_definitions.module_references(definition.get("instances"))
        )
    _records, evidence = module_definitions.load_definition_versions(
        config, references
    )
    return module_definition_authority.module_definition_authorities_from_record_location_evidence(
        config["releaseRoot"], evidence
    )


def environment_definitions(config):
    definitions = []
    records = graph_resources.load_repository(config, "environment")
    authorities = _module_authorities(config, records.values())
    for definition in records.values():
        definitions.append(
            validate_environment_definition_authority(definition, authorities)
        )
    return [copy.deepcopy(definition) for definition in definitions]


def get_environment_definition(
    config,
    environment_id,
    version="",
):
    definition = graph_resources.load_version(
        config,
        "environment",
        environment_id,
        version,
    )
    authorities = _module_authorities(config, (definition,))
    return validate_environment_definition_authority(definition, authorities)


__all__ = (
    "environment_definitions",
    "get_environment_definition",
)
