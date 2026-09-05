"""Compose Sample Result projection compilation and bounded execution."""

from __future__ import annotations

from engine.compiler.result_projection import compile_temporary_module_plan
from engine.runtime.sample_result_projection import (
    write_verified_sample_result_projection,
    write_verified_sample_result_projection_from_frames,
)


def project_sample_result(
    evidence,
    paths,
    temporary_modules,
    module_definitions,
    destination_path,
    *,
    capture_cycle=None,
    capture_metadata=None,
    projection_format="rows",
    window=None,
):
    temporary_plan = None
    if temporary_modules:
        temporary_plan = compile_temporary_module_plan(
            {"dataKeys": evidence["dataKeys"]},
            temporary_modules,
            module_definitions,
        )
    return write_verified_sample_result_projection(
        evidence,
        paths,
        destination_path,
        temporary_plan=temporary_plan,
        capture_cycle=capture_cycle,
        capture_metadata=capture_metadata,
        projection_format=projection_format,
        window=window,
    )


def project_sample_result_frames(
    evidence,
    frames,
    paths,
    temporary_modules,
    module_definitions,
    destination_path,
    *,
    projection_format="rows",
    window=None,
):
    temporary_plan = None
    if temporary_modules:
        temporary_plan = compile_temporary_module_plan(
            {"dataKeys": evidence["dataKeys"]},
            temporary_modules,
            module_definitions,
        )
    return write_verified_sample_result_projection_from_frames(
        evidence,
        frames,
        paths,
        destination_path,
        temporary_plan=temporary_plan,
        projection_format=projection_format,
        window=window,
    )


__all__ = ("project_sample_result", "project_sample_result_frames")
