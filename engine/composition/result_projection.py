"""Compose Result projection compilation with bounded runtime execution."""

from __future__ import annotations

from engine.compiler.result_projection import compile_temporary_module_plan
from engine.runtime.result_projection import write_verified_result_projection


def project_result(
    evidence,
    paths,
    temporary_modules,
    module_definitions,
    destination_path,
):
    temporary_plan = None
    if temporary_modules:
        temporary_plan = compile_temporary_module_plan(
            {"dataKeys": evidence["dataKeys"]},
            temporary_modules,
            module_definitions,
        )
    return write_verified_result_projection(
        evidence,
        paths,
        destination_path,
        temporary_plan=temporary_plan,
    )


__all__ = ("project_result",)
