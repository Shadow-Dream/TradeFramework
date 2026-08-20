"""Module Graph validation for Environment definitions."""

from engine.compiler.graph import (
    compile_module_graph,
    compile_verified_module_graph,
)
from engine.contracts.environment import normalize_environment


def _compile_environment(definition, module_definitions, *, archived):
    normalized = normalize_environment(definition, archived=archived)
    if module_definitions is None:
        raise ValueError(
            "Environment validation requires the Archived Environment Module repository."
        )
    plan = compile_module_graph(
        normalized["graph"],
        normalized["instances"],
        module_definitions,
        {},
        allowed_kinds={"Environment"},
        label="Environment Graph",
        strict_sources=False,
    )
    return normalized, plan


def validate_environment_definition_authority(
    definition, module_definition_authorities
):
    """Validate one archived Environment from nominal Module proofs."""
    normalized = normalize_environment(definition, archived=True)
    compile_verified_module_graph(
        normalized["graph"],
        normalized["instances"],
        module_definition_authorities,
        {},
        allowed_kinds={"Environment"},
        label="Environment Graph",
        strict_sources=False,
    )
    return normalized


def compile_environment_draft_authority(
    definition, module_definition_authorities
):
    """Compile one draft from exact managed Module authorities."""
    normalized = normalize_environment(definition, archived=False)
    return compile_verified_module_graph(
        normalized["graph"],
        normalized["instances"],
        module_definition_authorities,
        {},
        allowed_kinds={"Environment"},
        label="Environment Graph",
        strict_sources=False,
    )


def validate_environment_draft(definition, module_definitions=None):
    return _compile_environment(definition, module_definitions, archived=False)[0]


def compile_environment_draft(definition, module_definitions=None):
    """Compile one draft with Environment's unresolved cycle boundary."""
    return _compile_environment(definition, module_definitions, archived=False)[1]


def validate_environment_definition(definition, module_definitions=None):
    return _compile_environment(definition, module_definitions, archived=True)[0]


__all__ = (
    "compile_environment_draft_authority",
    "compile_environment_draft",
    "validate_environment_definition",
    "validate_environment_definition_authority",
    "validate_environment_draft",
)
