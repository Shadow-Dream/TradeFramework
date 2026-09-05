"""Repository access for immutable content-addressed Sample Results."""

from __future__ import annotations

from pathlib import Path

from engine.archive import sample_result as sample_result_archive
from engine.archive import version as version_archive
from engine.contracts import sample_result as sample_result_contracts
from engine.contracts import strict_json


def sample_result_exists(config, sample_result_id):
    directory = sample_result_archive.archive_directory(
        config["releaseRoot"],
        sample_result_id,
        label="Sample Result archive",
    )
    return directory.exists()


def load_sample_result_evidence(config, sample_result_id, *, verify_digest=False):
    directory = sample_result_archive.archive_directory(
        config["releaseRoot"],
        sample_result_id,
        label="Sample Result archive",
    )
    identity = sample_result_archive.sealed_archive_identity(
        directory,
        label="Sample Result sealed archive",
    )
    result_path = directory / sample_result_archive.RESULT_FILE_NAME
    manifest_path = directory / sample_result_archive.MANIFEST_FILE_NAME
    manifest = sample_result_contracts.require_manifest(
        strict_json.loads(manifest_path.read_text(encoding="utf-8")),
        sample_result_id=sample_result_id,
    )
    if result_path.stat().st_size != manifest["size"]:
        raise ValueError("Sample Result size differs from its sealed manifest.")
    if verify_digest:
        actual = version_archive.file_content_digest(
            result_path,
            expected_size=manifest["size"],
        )
        if actual != manifest["contentDigest"]:
            raise ValueError("Sample Result digest differs from its sealed manifest.")
    metadata = manifest["resultMetadata"]
    frame = metadata["sampleFrameContract"]
    sample_result_contracts.require_metadata(
        metadata,
        sample_result_id=sample_result_id,
        cycle_count=frame["frameCount"],
        first_cycle_id=frame["firstCycleId"],
        last_cycle_id=frame["lastCycleId"],
    )
    final_identity = sample_result_archive.sealed_archive_identity(
        directory,
        label="Sample Result sealed archive",
    )
    if final_identity != identity:
        raise ValueError("Sample Result archive changed while reading.")
    return {
        "path": result_path,
        "manifest": manifest,
        "contentDigest": manifest["contentDigest"],
        "resultSize": manifest["size"],
        "dataKeys": metadata["dataKeys"],
        "execution": metadata["execution"],
        "sampleFrameContract": frame,
        "archiveIdentity": identity,
    }


def sample_result_view(config, sample_result_id):
    evidence = load_sample_result_evidence(config, sample_result_id)
    execution = evidence["execution"]
    frame = evidence["sampleFrameContract"]
    return {
        "schemaVersion": 1,
        "sampleResultId": sample_result_id,
        "datasetId": execution["dataset"]["datasetId"],
        "datasetVersionId": execution["dataset"]["datasetVersionId"],
        "sampler": execution["sampler"],
        "dataKeys": evidence["dataKeys"],
        "cycleCount": frame["frameCount"],
        "firstCycleId": frame["firstCycleId"],
        "lastCycleId": frame["lastCycleId"],
        "resultContentDigest": evidence["contentDigest"],
    }


__all__ = (
    "load_sample_result_evidence",
    "sample_result_exists",
    "sample_result_view",
)
