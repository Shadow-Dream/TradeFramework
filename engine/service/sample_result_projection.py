"""Service-facing Sample Result projection operations."""

from __future__ import annotations

from engine.repository import module_definitions
from engine.repository import sample_results
from engine.service import projection_cache
from engine.service import projection_prewarm
from engine.runtime.projection_worker import (
    write_sample_result_projection_in_worker,
)


def write_sample_result_slice_cached(
    config,
    sample_result_id,
    paths,
    temporary_modules,
    destination_path,
    *,
    projection_format="rows",
    window=None,
    priority="interactive",
):
    if not isinstance(temporary_modules, list):
        raise ValueError("Sample Result slice temporaryModules must be an array.")
    evidence = sample_results.load_sample_result_evidence(
        config,
        sample_result_id,
        verify_digest=False,
    )
    definitions = {}
    if temporary_modules:
        references = [
            (module.get("kind"), module.get("moduleId"), module.get("version"))
            for module in temporary_modules
        ]
        definitions, _evidence = module_definitions.load_definition_versions(
            config,
            references,
        )
    identity = projection_cache.projection_identity(
        kind="sample",
        result_id=sample_result_id,
        result_content_digest=evidence["contentDigest"],
        paths=paths,
        temporary_modules=temporary_modules,
        module_definitions=definitions,
        projection_format=projection_format,
        window=window,
    )
    cache = projection_cache.write_cached_projection(
        config,
        identity,
        destination_path,
        lambda staged: write_sample_result_projection_in_worker(
            evidence,
            paths,
            temporary_modules,
            definitions,
            staged,
            projection_format=projection_format,
            window=window,
            priority=priority,
        ),
    )
    if cache["hit"]:
        projection_prewarm.prepare_projection_source(
            "sample", evidence, interactive=priority == "interactive"
        )
    return {"path": destination_path, "cache": cache}


def write_sample_result_slice(*args, **kwargs):
    return write_sample_result_slice_cached(*args, **kwargs)["path"]


__all__ = ("write_sample_result_slice", "write_sample_result_slice_cached")
