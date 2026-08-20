"""Immutable streaming Result writer owned by the worker layer."""

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from time import perf_counter

from engine.archive import version as version_archive
from engine.contracts import strict_json
from engine.contracts.module import require_exact_fields
from engine.runtime import result_stream

try:
    import orjson as _orjson
except ImportError:  # The strict stdlib encoder remains the portable baseline.
    _orjson = None


class BacktestResultPublicationUncertain(RuntimeError):
    """The sealed Result was published but its durability ACK was not observed."""

    def __init__(self, publication_error):
        if not isinstance(publication_error, OSError):
            raise TypeError(
                "Backtest Result publication uncertainty requires an OS "
                "durability error."
            )
        super().__init__(
            "Backtest Result publication completed without a durability "
            "acknowledgement."
        )
        self.publication_error = publication_error


class BacktestResultWriter:
    """Write one immutable Result without retaining all cycles in host memory."""

    flush_characters = 1024 * 1024

    def __init__(self, path):
        self.path = Path(path)
        self.archive_directory = self.path.parent
        archive_root = self.archive_directory.parent
        archive_root.mkdir(parents=True, exist_ok=True)
        if self.archive_directory.exists() or self.archive_directory.is_symlink():
            raise ValueError(
                f"Backtest Result destination already exists: {self.archive_directory}"
            )
        self.staging_directory = Path(
            tempfile.mkdtemp(
                prefix=f".{self.archive_directory.name}.staging-",
                dir=archive_root,
            )
        )
        staging_stat = os.stat(self.staging_directory, follow_symlinks=False)
        self._publication_identity = (staging_stat.st_dev, staging_stat.st_ino)
        self._published = False
        self.staging_path = self.staging_directory / self.path.name
        descriptor, temporary = tempfile.mkstemp(
            prefix=".result-", suffix=".json", dir=self.staging_directory
        )
        self.temporary_path = Path(temporary)
        self.handle = os.fdopen(descriptor, "wb")
        self._content_hasher = hashlib.sha256()
        self._result_byte_count = 0
        self._stream_failed = False
        self.count = 0
        self.finished = False
        self.encode_seconds = 0.0
        self.write_seconds = 0.0
        self.encoded_characters = 0
        self.metadata_encode_seconds = 0.0
        self.metadata_write_seconds = 0.0
        self.flush_seconds = 0.0
        self.fsync_seconds = 0.0
        self.close_seconds = 0.0
        self.commit_seconds = 0.0
        self.digest_seconds = 0.0
        self.finish_seconds = 0.0
        self.content_digest = ""
        self.result_size = 0
        self.first_cycle_id = None
        self.last_cycle_id = None
        self._cycle_ids = result_stream.UniqueTextIndex(
            prefix="trade-result-writer-identities-"
        )
        self._pending = []
        self._pending_characters = 0
        self._encoder = self._encode_json
        # Result archives remain ordinary JSON, but every cycle is also one
        # physical line.  A parent verifier may use those newlines only as
        # candidate shard boundaries; each line is still decoded and checked
        # independently before the archive is accepted.
        self._write_result_text('{"cycles":[\n')

    @staticmethod
    def _encode_json(value):
        # Result bytes carry JSON value semantics, not the canonical digest
        # spelling used by contracts.  A native sorted encoder is safe only
        # after the exact finite-JSON success proof; values outside orjson's
        # integer range and every unsupported/deep value fall back to the
        # original strict encoder and its authoritative diagnostics.
        if _orjson is not None and strict_json.is_exact_json(value):
            try:
                return _orjson.dumps(
                    value,
                    option=_orjson.OPT_SORT_KEYS,
                ).decode("utf-8")
            except TypeError:
                pass
        return strict_json.dumps(
            value, sort_keys=True, separators=(",", ":")
        )

    def _json(self, value):
        return self._encoder(value)

    def _require_writable_stream(self):
        if self.finished:
            raise RuntimeError("Cannot write a finalized Backtest Result.")
        if self._stream_failed:
            raise RuntimeError(
                "Cannot continue a Backtest Result after its byte stream failed."
            )

    def _write_result_text(self, text):
        """Write and hash the exact UTF-8 bytes accepted by the Result file."""

        self._require_writable_stream()
        if type(text) is not str:
            raise TypeError("Backtest Result encoded material must be exact text.")
        encoded = text.encode("utf-8")
        offset = 0
        try:
            while offset < len(encoded):
                written = self.handle.write(encoded[offset:])
                remaining = len(encoded) - offset
                if (
                    type(written) is not int
                    or written < 1
                    or written > remaining
                ):
                    raise OSError(
                        "Backtest Result byte stream write made no valid progress."
                    )
                accepted = encoded[offset:offset + written]
                self._content_hasher.update(accepted)
                self._result_byte_count += written
                offset += written
        except BaseException:
            self._stream_failed = True
            raise

    def append(self, cycle):
        """Validate and append an ordinary caller-owned cycle."""
        return self._append_cycle(cycle, self._json)

    def _append_cycle(self, cycle, encoder):
        self._require_writable_stream()
        require_exact_fields(
            cycle,
            allowed={"schemaVersion", "cycleId", "decisionTime", "data"},
            required={"schemaVersion", "cycleId", "decisionTime", "data"},
            label="Streamed Backtest Result cycle",
        )
        if cycle["schemaVersion"] != 3:
            raise ValueError("Streamed Backtest Result cycle schemaVersion 3 is required.")
        cycle_id = cycle["cycleId"]
        if not isinstance(cycle_id, str) or not cycle_id:
            raise ValueError("Streamed Backtest Result cycleId must be a non-empty string.")
        if not self._cycle_ids.claim(cycle_id):
            raise ValueError(
                f"Streamed Backtest Result contains duplicate cycleId '{cycle_id}'."
            )
        if not isinstance(cycle["decisionTime"], str) or not cycle["decisionTime"]:
            raise ValueError(
                "Streamed Backtest Result decisionTime must be a non-empty string."
            )
        if not isinstance(cycle["data"], dict):
            raise ValueError("Streamed Backtest Result data must be an object.")
        encode_started = perf_counter()
        encoded = encoder(cycle)
        self.encode_seconds += perf_counter() - encode_started
        self.encoded_characters += len(encoded)
        chunk = (",\n" if self.count else "") + encoded
        self._pending.append(chunk)
        self._pending_characters += len(chunk)
        if self.first_cycle_id is None:
            self.first_cycle_id = cycle_id
        self.last_cycle_id = cycle_id
        self.count += 1
        if self._pending_characters >= self.flush_characters:
            self._flush_pending()

    def _flush_pending(self):
        if not self._pending:
            return
        chunk = "".join(self._pending)
        write_started = perf_counter()
        self._write_result_text(chunk)
        self.write_seconds += perf_counter() - write_started
        self._pending = []
        self._pending_characters = 0

    def flush_cycles(self):
        """Flush every encoded cycle before its timing snapshot is recorded."""
        if self.finished:
            raise RuntimeError("Cannot flush a finalized Backtest Result.")
        self._flush_pending()

    def finish(self, fields, catalog):
        if self.finished:
            raise RuntimeError("Backtest Result is already finalized.")
        self._require_writable_stream()
        require_exact_fields(
            fields,
            allowed={
                "schemaVersion",
                "dataKeys",
                "metrics",
                "executionChain",
                "sampleFrameContract",
            },
            required={
                "schemaVersion",
                "dataKeys",
                "metrics",
                "executionChain",
                "sampleFrameContract",
            },
            label="Streamed Backtest Result metadata",
        )
        if fields["schemaVersion"] != 8:
            raise ValueError("Streamed Backtest Result schemaVersion 8 is required.")
        require_exact_fields(
            catalog,
            allowed={
                "backtestId",
                "pipelineId",
                "datasetId",
                "name",
                "runner",
                "createdAt",
                "completedAt",
                "request",
                "metrics",
                "visualization",
            },
            required={
                "backtestId",
                "pipelineId",
                "datasetId",
                "name",
                "runner",
                "createdAt",
                "completedAt",
                "request",
                "metrics",
                "visualization",
            },
            label="Backtest Result catalog evidence",
        )
        if catalog["backtestId"] != self.path.parent.name:
            raise ValueError(
                "Backtest Result catalog identity does not match its directory."
            )
        if catalog["metrics"] != fields["metrics"]:
            raise ValueError(
                "Backtest Result catalog metrics do not match Result metrics."
            )
        if (
            not isinstance(fields["metrics"], dict)
            or fields["metrics"].get("cycleCount") != self.count
        ):
            raise ValueError("Streamed Backtest Result metrics.cycleCount is invalid.")
        frame_contract = fields["sampleFrameContract"]
        if (
            not isinstance(frame_contract, dict)
            or frame_contract.get("frameCount") != self.count
            or frame_contract.get("firstCycleId") != self.first_cycle_id
            or frame_contract.get("lastCycleId") != self.last_cycle_id
        ):
            raise ValueError("Streamed Backtest Result frame boundaries are invalid.")
        self._cycle_ids.close()
        finish_started = perf_counter()
        self._flush_pending()
        encode_started = perf_counter()
        # Close the last cycle line before the metadata suffix.  The empty
        # archive already ends its prefix with a newline and needs no second
        # blank line.
        tail = ["\n]" if self.count else "]"]
        for key, value in fields.items():
            if key == "cycles":
                raise ValueError(
                    "Backtest Result metadata may not replace streamed cycles."
                )
            tail.extend((",", self._json(str(key)), ":", self._json(value)))
        tail.append("}")
        encoded_tail = "".join(tail)
        self.metadata_encode_seconds = perf_counter() - encode_started
        write_started = perf_counter()
        self._write_result_text(encoded_tail)
        self.metadata_write_seconds = perf_counter() - write_started
        flush_started = perf_counter()
        self.handle.flush()
        self.flush_seconds = perf_counter() - flush_started
        fsync_started = perf_counter()
        os.fsync(self.handle.fileno())
        self.fsync_seconds = perf_counter() - fsync_started
        close_started = perf_counter()
        self.handle.close()
        self.close_seconds = perf_counter() - close_started
        commit_started = perf_counter()
        os.replace(self.temporary_path, self.staging_path)
        self.commit_seconds = perf_counter() - commit_started
        digest_started = perf_counter()
        self.result_size = self.staging_path.stat().st_size
        if self.result_size != self._result_byte_count:
            raise RuntimeError(
                "Backtest Result byte count changed outside its streaming writer."
            )
        self.content_digest = "sha256:" + self._content_hasher.hexdigest()
        manifest = {
            "schemaVersion": 4,
            "backtestId": self.archive_directory.name,
            "resultFile": self.staging_path.name,
            "contentDigest": self.content_digest,
            "size": self.result_size,
            # finish() encodes the manifest synchronously before returning and
            # never retains either caller-owned tree.  Keeping the references
            # here avoids copying the multi-megabyte frozen request and
            # execution metadata solely to traverse them immediately below.
            "catalog": catalog,
            "resultMetadata": fields,
        }
        manifest_path = self.staging_directory / "result-manifest.json"
        manifest_temporary = manifest_path.with_name(manifest_path.name + ".tmp")
        manifest_temporary.write_text(
            strict_json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(manifest_temporary, manifest_path)
        self.staging_path.chmod(self.staging_path.stat().st_mode & ~0o222)
        manifest_path.chmod(manifest_path.stat().st_mode & ~0o222)
        self.staging_directory.chmod(
            self.staging_directory.stat().st_mode & ~0o222
        )
        try:
            version_archive.publish_staging_directory(
                self.staging_directory,
                self.archive_directory,
                managed_root=self.archive_directory.parent,
            )
            self._published = True
        except BaseException as error:
            # publish_staging_directory may fail after its atomic rename (for
            # example with an OSError while fsyncing the destination parent).
            # Preserve only that filesystem durability uncertainty; an
            # arbitrary application exception after rename remains a failure.
            self._published = self._destination_is_published_staging()
            if self._published and isinstance(error, OSError):
                raise BacktestResultPublicationUncertain(error) from error
            raise
        self.digest_seconds = perf_counter() - digest_started
        self.finished = True
        self.finish_seconds = perf_counter() - finish_started
        return self.completion_metrics()

    def completion_metrics(self):
        if not self.finished:
            raise RuntimeError(
                "Backtest Result completion metrics require a finalized Result."
            )
        return {
            "finishSeconds": self.finish_seconds,
            "metadataEncodeSeconds": self.metadata_encode_seconds,
            "metadataWriteSeconds": self.metadata_write_seconds,
            "flushSeconds": self.flush_seconds,
            "fsyncSeconds": self.fsync_seconds,
            "closeSeconds": self.close_seconds,
            "commitSeconds": self.commit_seconds,
            "digestAndSealSeconds": self.digest_seconds,
            "contentDigest": self.content_digest,
            "resultSize": self.result_size,
        }

    @property
    def published(self):
        """Whether this writer's staging inode reached its final destination."""
        return self._published

    def _destination_is_published_staging(self):
        try:
            destination_stat = os.stat(
                self.archive_directory,
                follow_symlinks=False,
            )
        except OSError:
            return False
        return (
            stat.S_ISDIR(destination_stat.st_mode)
            and (destination_stat.st_dev, destination_stat.st_ino)
            == self._publication_identity
        )

    def discard(self, *, remove_finished=False):
        self._cycle_ids.close()
        if not self.handle.closed:
            self.handle.close()
        self.temporary_path.unlink(missing_ok=True)
        if self.staging_directory.exists():
            version_archive.discard_archive(self.staging_directory)
        if remove_finished and self.archive_directory.exists():
            version_archive.discard_archive(self.archive_directory)

__all__ = ["BacktestResultWriter"]
