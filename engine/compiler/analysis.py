"""Module Graph validation for Analysis definitions."""

from engine.compiler.graph import (
    compile_module_graph,
    compile_verified_module_graph,
)
from engine.contracts.analysis import normalize_analysis
from engine.contracts.graph_cycle import CURRENT_PIPELINE_SOURCE


def _compile_analysis(definition, module_definitions, *, archived):
    normalized = normalize_analysis(definition, archived=archived)
    if module_definitions is None:
        raise ValueError(
            "Analysis validation requires the Archived Analysis Module repository."
        )
    plan = compile_module_graph(
        normalized["graph"],
        normalized["instances"],
        module_definitions,
        {},
        allowed_kinds={"Analyzer"},
        label="Analysis Graph",
        strict_sources=False,
        source_contracts={CURRENT_PIPELINE_SOURCE: {}},
        source_required_roots={CURRENT_PIPELINE_SOURCE: ()},
    )
    return normalized, plan


def validate_analysis_definition_authority(
    definition, module_definition_authorities
):
    """Validate one archived Analysis from nominal Module proofs."""
    normalized = normalize_analysis(definition, archived=True)
    compile_verified_module_graph(
        normalized["graph"],
        normalized["instances"],
        module_definition_authorities,
        {},
        allowed_kinds={"Analyzer"},
        label="Analysis Graph",
        strict_sources=False,
        source_contracts={CURRENT_PIPELINE_SOURCE: {}},
        source_required_roots={CURRENT_PIPELINE_SOURCE: ()},
    )
    return normalized


def compile_analysis_draft_authority(
    definition, module_definition_authorities
):
    """Compile one draft from exact managed Module authorities."""
    normalized = normalize_analysis(definition, archived=False)
    return compile_verified_module_graph(
        normalized["graph"],
        normalized["instances"],
        module_definition_authorities,
        {},
        allowed_kinds={"Analyzer"},
        label="Analysis Graph",
        strict_sources=False,
        source_contracts={CURRENT_PIPELINE_SOURCE: {}},
        source_required_roots={CURRENT_PIPELINE_SOURCE: ()},
    )


def validate_analysis_draft(definition, module_definitions=None):
    return _compile_analysis(definition, module_definitions, archived=False)[0]


def compile_analysis_draft(definition, module_definitions=None):
    """Compile one draft with Analysis's named-source boundary."""
    return _compile_analysis(definition, module_definitions, archived=False)[1]


def validate_analysis_definition(definition, module_definitions=None):
    return _compile_analysis(definition, module_definitions, archived=True)[0]


__all__ = (
    "compile_analysis_draft_authority",
    "compile_analysis_draft",
    "validate_analysis_definition",
    "validate_analysis_definition_authority",
    "validate_analysis_draft",
)
