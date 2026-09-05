"""Streaming writer for one immutable Sampler-only Sample Result."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from engine.archive import sample_result as sample_result_archive
from engine.archive import version as version_archive
from engine.contracts import result as result_contracts
from engine.contracts import sample_result as sample_result_contracts
from engine.contracts import strict_json
from engine.runtime import result_stream


class SampleResultWriter:
    """Write cycles first and atomically seal their Sample Result archive."""

    def __init__(self, release_root, sample_result_id, data_keys):
        self.sample_result_id = sample_result_contracts.require_sample_result_id(
            sample_result_id
        )
        self.archive_directory = sample_result_archive.archive_directory(
            release_root,
            sample_result_id,
            label="Sample Result destination",
        )
        root = self.archive_directory.parent
        root.mkdir(parents=True, exist_ok=True)
        if self.archive_directory.exists() or self.archive_directory.is_symlink():
            raise ValueError("Sample Result destination already exists.")
        self.staging_directory = Path(
            tempfile.mkdtemp(
                prefix=f".{self.archive_directory.name}.staging-",
                dir=root,
            )
        )
        self.result_path = self.staging_directory / sample_result_archive.RESULT_FILE_NAME
        self.handle = self.result_path.open("wb")
        self.hasher = hashlib.sha256()
        self.byte_count = 0
        self.count = 0
        self.first_cycle_id = None
        self.last_cycle_id = None
        self.finished = False
        self.published = False
        self.cycle_ids = result_stream.UniqueTextIndex(
            prefix="trade-sample-result-writer-identities-"
        )
        self.validate_cycle_data = result_contracts.compile_cycle_validator(data_keys)
        self._write('{"cycles":[\n')

    def _write(self, text):
        if self.finished:
            raise RuntimeError("Cannot write a finalized Sample Result.")
        encoded = text.encode("utf-8")
        self.handle.write(encoded)
        self.hasher.update(encoded)
        self.byte_count += len(encoded)

    def append(self, cycle):
        result_contracts.require_cycle(
            cycle,
            self.count,
            self.validate_cycle_data,
            self.cycle_ids,
        )
        encoded = strict_json.dumps(cycle, sort_keys=True, separators=(",", ":"))
        if self.count:
            self._write(",\n")
        self._write(encoded)
        self.first_cycle_id = self.first_cycle_id or cycle["cycleId"]
        self.last_cycle_id = cycle["cycleId"]
        self.count += 1

    def finish(self, metadata):
        sample_result_contracts.require_metadata(
            metadata,
            sample_result_id=self.sample_result_id,
            cycle_count=self.count,
            first_cycle_id=self.first_cycle_id,
            last_cycle_id=self.last_cycle_id,
            verified_cycle_validator=self.validate_cycle_data,
        )
        suffix = ["\n]"] if self.count else ["]"]
        for key in ("schemaVersion", "dataKeys", "execution", "sampleFrameContract"):
            suffix.extend((
                ",",
                strict_json.dumps(key),
                ":",
                strict_json.dumps(metadata[key], sort_keys=True, separators=(",", ":")),
            ))
        suffix.append("}")
        self._write("".join(suffix))
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        self.cycle_ids.close()
        size = self.result_path.stat().st_size
        if size != self.byte_count:
            raise RuntimeError("Sample Result byte count changed while writing.")
        content_digest = "sha256:" + self.hasher.hexdigest()
        manifest = sample_result_contracts.require_manifest({
            "schemaVersion": sample_result_contracts.SAMPLE_RESULT_SCHEMA_VERSION,
            "sampleResultId": self.sample_result_id,
            "resultFile": sample_result_archive.RESULT_FILE_NAME,
            "contentDigest": content_digest,
            "size": size,
            "resultMetadata": metadata,
        }, sample_result_id=self.sample_result_id)
        manifest_path = self.staging_directory / sample_result_archive.MANIFEST_FILE_NAME
        manifest_path.write_text(
            strict_json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        self.result_path.chmod(self.result_path.stat().st_mode & ~0o222)
        manifest_path.chmod(manifest_path.stat().st_mode & ~0o222)
        self.staging_directory.chmod(
            self.staging_directory.stat().st_mode & ~0o222
        )
        version_archive.publish_staging_directory(
            self.staging_directory,
            self.archive_directory,
            managed_root=self.archive_directory.parent,
        )
        self.published = True
        self.finished = True
        return {
            "sampleResultId": self.sample_result_id,
            "contentDigest": content_digest,
            "resultSize": size,
            "cycleCount": self.count,
        }

    def discard(self):
        self.cycle_ids.close()
        if not self.handle.closed:
            self.handle.close()
        if self.staging_directory.exists():
            version_archive.discard_archive(self.staging_directory)


__all__ = ("SampleResultWriter",)
