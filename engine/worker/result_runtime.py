"""Execute one streamed Result projection in a disposable Python Runtime."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from engine.composition.result_projection import project_result
from engine.composition.sample_result_projection import project_sample_result
from engine.contracts import strict_json
from engine.contracts.module import require_exact_fields


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m engine.worker.result_runtime SPEC_PATH")
    spec_path = Path(sys.argv[1]).resolve()
    spec = strict_json.loads(spec_path.read_text(encoding="utf-8"))
    if spec.get("schemaVersion") == 2:
        evidence_field = "resultEvidence"
        evidence_fields = {
            "path", "manifest", "contentDigest", "resultSize", "request",
            "metrics", "dataKeys", "executionChain",
        }
        projector = project_result
    elif spec.get("schemaVersion") == 3:
        evidence_field = "sampleResultEvidence"
        evidence_fields = {
            "path", "manifest", "contentDigest", "resultSize", "dataKeys",
            "execution", "sampleFrameContract",
        }
        projector = project_sample_result
    else:
        raise ValueError(
            "Result Runtime specification schemaVersion 2 or 3 is required."
        )
    common_fields = {
        "schemaVersion", evidence_field, "paths", "temporaryModules",
        "moduleDefinitions", "outputPath",
    }
    require_exact_fields(
        spec,
        allowed=common_fields,
        required=common_fields,
        label="Result Runtime specification",
    )
    evidence = spec[evidence_field]
    require_exact_fields(
        evidence,
        allowed=evidence_fields,
        required=evidence_fields,
        label="Result Runtime archive evidence",
    )
    if not isinstance(spec["paths"], list):
        raise ValueError("Result Runtime paths must be an array.")
    if (
        not isinstance(spec["temporaryModules"], list)
        or not spec["temporaryModules"]
    ):
        raise ValueError("Result Runtime requires temporaryModules.")
    if not isinstance(spec["moduleDefinitions"], dict):
        raise ValueError("Result Runtime moduleDefinitions must be an object.")
    result_path = Path(evidence["path"]).resolve()
    if not result_path.is_file() or result_path.is_symlink():
        raise ValueError("Result Runtime archive path is invalid.")
    output_path = Path(spec["outputPath"]).resolve()
    if output_path == result_path or output_path.is_symlink():
        raise ValueError("Result Runtime output path is invalid.")
    try:
        projector(
            {**evidence, "path": result_path},
            spec["paths"],
            spec["temporaryModules"],
            spec["moduleDefinitions"],
            output_path,
        )
        return 0
    except BaseException as primary_error:
        cleanup_error = None
        try:
            output_path.unlink(missing_ok=True)
        except BaseException as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            primary_error.__context__ = cleanup_error
        traceback.print_exception(primary_error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
