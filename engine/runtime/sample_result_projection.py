"""Bounded, authority-checked projection of immutable Sample Results."""

from __future__ import annotations

from engine.contracts import sample_result as sample_result_contracts
from engine.contracts import strict_json
from engine.runtime import result_stream
from engine.runtime.result_projection import ResultCycleProcessor


def _strict_json_equal(actual, expected):
    return strict_json.exact_equal(actual, expected)


def _sample_metadata_validator(evidence, processor):
    def validate_metadata(
        metadata,
        *,
        cycle_count,
        first_cycle_id,
        last_cycle_id,
    ):
        if (
            not _strict_json_equal(metadata["dataKeys"], evidence["dataKeys"])
            or not _strict_json_equal(metadata["execution"], evidence["execution"])
            or not _strict_json_equal(
                metadata["sampleFrameContract"],
                evidence["sampleFrameContract"],
            )
        ):
            raise ValueError(
                "Sample Result content does not match its immutable metadata index."
            )
        sample_result_contracts.require_metadata(
            metadata,
            sample_result_id=evidence["execution"]["sampleResultId"],
            cycle_count=cycle_count,
            first_cycle_id=first_cycle_id,
            last_cycle_id=last_cycle_id,
            verified_cycle_validator=processor.base_validator,
        )
        if not _strict_json_equal(
            metadata,
            evidence["manifest"]["resultMetadata"],
        ):
            raise ValueError(
                "Sample Result metadata does not exactly match its sealed manifest."
            )

    return validate_metadata


def write_verified_sample_result_projection(
    evidence,
    paths,
    destination_path,
    *,
    temporary_plan=None,
    capture_cycle=None,
    capture_metadata=None,
    projection_format="rows",
    window=None,
):
    """Verify one sealed Sample Result while streaming its projection."""

    with ResultCycleProcessor(evidence["dataKeys"], temporary_plan) as processor:
        processor.require_projection_paths(
            paths,
            top_level_fields=sample_result_contracts.SAMPLE_RESULT_FIELDS,
        )

        return result_stream.write_projection(
            evidence["path"],
            destination_path,
            paths=paths,
            data_keys=evidence["dataKeys"],
            expected_digest=evidence["contentDigest"],
            expected_size=evidence["resultSize"],
            prepare_cycle=processor.prepare_cycle,
            finalize_cycles=processor.finalize,
            validate_metadata=_sample_metadata_validator(evidence, processor),
            top_level_fields=sample_result_contracts.SAMPLE_RESULT_FIELDS,
            capture_cycle=capture_cycle,
            capture_metadata=capture_metadata,
            projection_format=projection_format,
            window=window,
        )


def write_verified_sample_result_projection_from_frames(
    evidence,
    frames,
    paths,
    destination_path,
    *,
    temporary_plan=None,
    projection_format="rows",
    window=None,
):
    """Project cached verified Sample Result frames with fresh Modules."""

    if not isinstance(frames, dict) or set(frames) != {"cycles", "metadata"}:
        raise ValueError("Cached Sample Result frames are invalid.")
    with ResultCycleProcessor(
        evidence["dataKeys"],
        temporary_plan,
        verified_base_frames=True,
    ) as processor:
        processor.require_projection_paths(
            paths,
            top_level_fields=sample_result_contracts.SAMPLE_RESULT_FIELDS,
        )
        return result_stream.write_projection_from_cycles(
            frames["cycles"],
            frames["metadata"],
            destination_path,
            paths=paths,
            data_keys=evidence["dataKeys"],
            prepare_cycle=processor.prepare_cycle,
            finalize_cycles=processor.finalize,
            validate_metadata=_sample_metadata_validator(evidence, processor),
            top_level_fields=sample_result_contracts.SAMPLE_RESULT_FIELDS,
            copy_cycle=processor.copy_cached_cycle,
            projection_format=projection_format,
            window=window,
        )


__all__ = (
    "write_verified_sample_result_projection",
    "write_verified_sample_result_projection_from_frames",
)
