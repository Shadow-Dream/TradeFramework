#!/usr/bin/env python3
"""Bounded-memory reader and projector for immutable Engine Result archives."""

from __future__ import annotations

import codecs
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from engine.contracts import strict_json
from engine.contracts.data_path import (
    get_data_segments,
    set_data_segments,
    split_data_path,
)


_MISSING = object()
_RESULT_FIELDS = frozenset({
    "cycles",
    "schemaVersion",
    "dataKeys",
    "metrics",
    "executionChain",
    "sampleFrameContract",
})
_CYCLE_IDENTITY_FIELDS = ("schemaVersion", "cycleId", "decisionTime")
FRAMED_RESULT_PREFIX = b'{"cycles":[\n'
FRAMED_RESULT_METADATA_PREFIX = b'],"schemaVersion":'
MAX_RESULT_VERIFICATION_SHARDS = 8
RESULT_VERIFICATION_TARGET_BYTES = 32 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
COLUMNAR_PROJECTION_FORMAT = "columns-v2"
COLUMNAR_PROJECTION_SCHEMA_VERSION = 2
MAX_COLUMNAR_PROJECTION_COLUMNS = 256


class UniqueTextIndex:
    """Disk-backed exact uniqueness index with memory independent of row count."""

    def __init__(self, *, prefix="trade-result-identities-"):
        self.temporary = tempfile.TemporaryDirectory(prefix=prefix)
        self.path = Path(self.temporary.name) / "identities.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute(
            "CREATE TABLE identities (value TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        self.closed = False

    def claim(self, value):
        if self.closed:
            raise RuntimeError("Unique text index is closed.")
        try:
            self.connection.execute(
                "INSERT INTO identities (value) VALUES (?)", (value,)
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.connection.close()
        self.temporary.cleanup()


class ResultCycleIdentityLedger:
    """Persist cycle identities and local indexes for parent-side merging."""

    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists() or self.path.is_symlink():
            raise ValueError("Result cycle identity ledger must be new.")
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute(
            "CREATE TABLE identities ("
            "value TEXT PRIMARY KEY, "
            "local_index INTEGER NOT NULL UNIQUE"
            ") WITHOUT ROWID"
        )
        self.local_index = None
        self.closed = False

    def select_cycle(self, local_index):
        if self.closed:
            raise RuntimeError("Result cycle identity ledger is closed.")
        if (
            isinstance(local_index, bool)
            or not isinstance(local_index, int)
            or local_index < 0
        ):
            raise ValueError("Result cycle ledger index must be non-negative.")
        self.local_index = local_index

    def claim(self, value):
        if self.closed:
            raise RuntimeError("Result cycle identity ledger is closed.")
        if self.local_index is None:
            raise RuntimeError("Result cycle identity ledger has no selected cycle.")
        try:
            self.connection.execute(
                "INSERT INTO identities (value, local_index) VALUES (?, ?)",
                (value, self.local_index),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def close(self):
        if self.closed:
            return
        self.connection.commit()
        self.connection.close()
        self.closed = True


def _require_file_range(start, end):
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or start < 0
        or isinstance(end, bool)
        or not isinstance(end, int)
        or end <= start
    ):
        raise ValueError("Result verification byte range is invalid.")


def _find_metadata_suffix_start(handle, size):
    offset = size
    while offset:
        start = max(0, offset - _READ_CHUNK_BYTES)
        handle.seek(start)
        chunk = handle.read(offset - start)
        newline = chunk.rfind(b"\n")
        if newline >= 0:
            return start + newline + 1
        offset = start
    raise ValueError("Result archive physical cycle framing is invalid.")


def plan_framed_cycle_ranges(
    path,
    *,
    expected_size,
    max_shards=MAX_RESULT_VERIFICATION_SHARDS,
):
    """Return contiguous byte ranges using newlines only as shard hints."""

    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < len(FRAMED_RESULT_PREFIX) + 1
    ):
        raise ValueError("Result archive size is invalid.")
    if (
        isinstance(max_shards, bool)
        or not isinstance(max_shards, int)
        or max_shards < 1
        or max_shards > MAX_RESULT_VERIFICATION_SHARDS
    ):
        raise ValueError("Result verifier shard count is invalid.")
    result_path = Path(path)
    if not result_path.is_file() or result_path.is_symlink():
        raise ValueError("Result archive path is invalid.")
    if result_path.stat().st_size != expected_size:
        raise ValueError("Result archive size does not match its immutable index.")
    with result_path.open("rb") as handle:
        if handle.read(len(FRAMED_RESULT_PREFIX)) != FRAMED_RESULT_PREFIX:
            raise ValueError("Result archive physical cycle framing is invalid.")
        suffix_start = _find_metadata_suffix_start(handle, expected_size)
        handle.seek(suffix_start)
        if (
            handle.read(len(FRAMED_RESULT_METADATA_PREFIX))
            != FRAMED_RESULT_METADATA_PREFIX
        ):
            raise ValueError("Result archive physical metadata framing is invalid.")
        cycle_start = len(FRAMED_RESULT_PREFIX)
        if suffix_start < cycle_start:
            raise ValueError("Result archive physical cycle framing is invalid.")
        cycle_bytes = suffix_start - cycle_start
        if cycle_bytes == 0:
            return {
                "cycleStart": cycle_start,
                "metadataStart": suffix_start,
                "ranges": (),
            }
        shard_count = min(
            max_shards,
            max(
                1,
                (cycle_bytes + RESULT_VERIFICATION_TARGET_BYTES - 1)
                // RESULT_VERIFICATION_TARGET_BYTES,
            ),
        )
        boundaries = [cycle_start]
        for shard_index in range(1, shard_count):
            target = cycle_start + cycle_bytes * shard_index // shard_count
            handle.seek(target)
            handle.readline()
            boundary = handle.tell()
            if cycle_start < boundary < suffix_start:
                boundaries.append(boundary)
        boundaries.append(suffix_start)
    boundaries = tuple(sorted(set(boundaries)))
    ranges = tuple(
        {
            "start": start,
            "end": end,
            "final": end == suffix_start,
        }
        for start, end in zip(boundaries, boundaries[1:])
        if end > start
    )
    if (
        not ranges
        or ranges[0]["start"] != cycle_start
        or ranges[-1]["end"] != suffix_start
        or any(
            left["end"] != right["start"]
            for left, right in zip(ranges, ranges[1:])
        )
    ):
        raise ValueError("Result verifier ranges do not cover every cycle byte.")
    return {
        "cycleStart": cycle_start,
        "metadataStart": suffix_start,
        "ranges": ranges,
    }


def count_framed_cycle_lines(path, start, end):
    """Count physical cycle lines in one exact range with bounded memory."""

    _require_file_range(start, end)
    remaining = end - start
    count = 0
    with Path(path).open("rb") as handle:
        handle.seek(start)
        while remaining:
            chunk = handle.read(min(_READ_CHUNK_BYTES, remaining))
            if not chunk:
                raise ValueError("Result archive ended inside a verifier range.")
            remaining -= len(chunk)
            count += chunk.count(b"\n")
    if count < 1:
        raise ValueError("Result archive cycle range contains no complete line.")
    return count


def iter_framed_cycle_values(path, start, end, *, final_range):
    """Strictly decode every physical cycle line in one contiguous range."""

    _require_file_range(start, end)
    if not isinstance(final_range, bool):
        raise TypeError("Result verifier final-range marker must be boolean.")
    position = start
    with Path(path).open("rb") as handle:
        handle.seek(start)
        while position < end:
            line = handle.readline(end - position + 1)
            if not line or position + len(line) > end or not line.endswith(b"\n"):
                raise ValueError("Result archive physical cycle framing is invalid.")
            position += len(line)
            is_last_line = position == end
            if is_last_line and final_range:
                if line.endswith(b",\n"):
                    raise ValueError(
                        "Result archive final cycle framing is invalid."
                    )
                encoded = line[:-1]
            else:
                if not line.endswith(b",\n"):
                    raise ValueError(
                        "Result archive cycle separator framing is invalid."
                    )
                encoded = line[:-2]
            if not encoded:
                raise ValueError("Result archive contains an empty cycle line.")
            yield strict_json.loads(encoded)
    if position != end:
        raise ValueError("Result verifier range was not completely consumed.")


def read_framed_result_metadata(path, *, metadata_start, expected_size):
    """Strictly decode the suffix after independently verified cycle lines."""

    if (
        isinstance(metadata_start, bool)
        or not isinstance(metadata_start, int)
        or metadata_start < len(FRAMED_RESULT_PREFIX)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or metadata_start >= expected_size
    ):
        raise ValueError("Result archive metadata range is invalid.")
    with Path(path).open("rb") as handle:
        handle.seek(metadata_start)
        suffix = handle.read(expected_size - metadata_start)
        if handle.read(1):
            raise ValueError("Result archive contains trailing content.")
    if b"\n" in suffix or not suffix.startswith(FRAMED_RESULT_METADATA_PREFIX):
        raise ValueError("Result archive physical metadata framing is invalid.")
    result = strict_json.loads(b'{"cycles":[]' + suffix[1:])
    if not isinstance(result, dict) or result.get("cycles") != []:
        raise ValueError("Result archive metadata suffix is invalid.")
    if set(result) != _RESULT_FIELDS:
        raise ValueError("Result archive top-level fields do not match its schema.")
    return {key: value for key, value in result.items() if key != "cycles"}


def hash_result_archive(path, *, expected_size):
    """Return the SHA-256 evidence for an exact immutable Result byte stream."""

    digest = hashlib.sha256()
    byte_count = 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            byte_count += len(chunk)
            digest.update(chunk)
    if byte_count != expected_size:
        raise ValueError("Result archive size does not match its immutable index.")
    return "sha256:" + digest.hexdigest()


def merge_cycle_identity_ledgers(entries, destination_path):
    """Return the earliest cross-shard duplicate index using only disk state."""

    destination = Path(destination_path)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Merged Result identity ledger must be new.")
    connection = sqlite3.connect(destination)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute(
            "CREATE TABLE identities ("
            "value TEXT PRIMARY KEY, "
            "absolute_index INTEGER NOT NULL UNIQUE"
            ") WITHOUT ROWID"
        )
        for shard_index, entry in enumerate(entries):
            path = Path(entry["path"])
            base_index = entry["baseIndex"]
            if (
                not path.is_file()
                or path.is_symlink()
                or isinstance(base_index, bool)
                or not isinstance(base_index, int)
                or base_index < 0
            ):
                raise ValueError("Result shard identity evidence is invalid.")
            alias = f"shard_{shard_index}"
            connection.execute(f"ATTACH DATABASE ? AS {alias}", (str(path),))
            try:
                duplicate = connection.execute(
                    f"""
                    SELECT s.local_index
                    FROM {alias}.identities s
                    JOIN identities i ON i.value = s.value
                    ORDER BY s.local_index
                    LIMIT 1
                    """
                ).fetchone()
                if duplicate is not None:
                    return base_index + int(duplicate[0])
                connection.execute(
                    f"""
                    INSERT INTO identities (value, absolute_index)
                    SELECT value, local_index + ?
                    FROM {alias}.identities
                    """,
                    (base_index,),
                )
                connection.commit()
            finally:
                connection.execute(f"DETACH DATABASE {alias}")
        return None
    finally:
        connection.close()


class ResultArchiveReader:
    """Read the streamed ``cycles`` array one value at a time.

    Result files are Engine-owned archives written with ``cycles`` first.  A
    single cycle is the largest JSON value retained while the archive digest is
    calculated over the same read, so archive size does not determine RSS.
    """

    def __init__(
        self,
        path,
        *,
        expected_digest,
        expected_size,
        top_level_fields=None,
        chunk_size=1024 * 1024,
    ):
        self.path = Path(path)
        self.expected_digest = expected_digest
        self.expected_size = expected_size
        self.chunk_size = chunk_size
        self.top_level_fields = (
            _RESULT_FIELDS
            if top_level_fields is None
            else frozenset(top_level_fields)
        )
        if "cycles" not in self.top_level_fields:
            raise ValueError("Result archive top-level fields must include cycles.")
        self.handle = None
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.byte_count = 0
        self.digest = hashlib.sha256()
        self.utf8 = codecs.getincrementaldecoder("utf-8")("strict")
        self.json_decoder = strict_json.decoder()
        self.metadata = None
        self._cycles_started = False

    def __enter__(self):
        self.handle = self.path.open("rb")
        self._expect_character("{")
        key = self._decode_value()
        if key != "cycles":
            raise ValueError("Result archive physical format requires cycles first.")
        self._expect_character(":")
        self._expect_character("[")
        self._cycles_started = True
        return self

    def __exit__(self, _kind, _value, _traceback):
        if self.handle is not None:
            self.handle.close()

    def _compact(self):
        if self.position >= self.chunk_size:
            self.buffer = self.buffer[self.position:]
            self.position = 0

    def _fill(self):
        if self.eof:
            return False
        self._compact()
        raw = self.handle.read(self.chunk_size)
        if raw:
            self.byte_count += len(raw)
            self.digest.update(raw)
            self.buffer += self.utf8.decode(raw, final=False)
            return True
        self.buffer += self.utf8.decode(b"", final=True)
        self.eof = True
        return False

    def _skip_whitespace(self):
        while True:
            while self.position < len(self.buffer) and self.buffer[self.position].isspace():
                self.position += 1
            if self.position < len(self.buffer) or self.eof:
                return
            self._fill()

    def _peek_character(self):
        self._skip_whitespace()
        if self.position >= len(self.buffer):
            raise ValueError("Result archive ended unexpectedly.")
        return self.buffer[self.position]

    def _expect_character(self, expected):
        actual = self._peek_character()
        if actual != expected:
            raise ValueError(
                f"Result archive expected '{expected}' but found '{actual}'."
            )
        self.position += 1

    def _decode_value(self):
        self._skip_whitespace()
        while True:
            try:
                value, end = self.json_decoder.raw_decode(self.buffer, self.position)
            except ValueError as exc:
                # Duplicate object keys and non-finite numbers are definitive
                # strict-boundary failures, while JSON syntax may simply be
                # incomplete at the current chunk boundary.
                if not isinstance(exc, json.JSONDecodeError):
                    raise
                if self.eof:
                    raise ValueError("Result archive contains invalid JSON.") from exc
                self._fill()
                continue
            self.position = end
            return value

    def cycles(self):
        if not self._cycles_started:
            raise RuntimeError("Result archive reader is not open.")
        first = True
        while True:
            character = self._peek_character()
            if character == "]":
                self.position += 1
                break
            if not first:
                self._expect_character(",")
            yield self._decode_value()
            first = False

        metadata = {}
        while True:
            character = self._peek_character()
            if character == "}":
                self.position += 1
                break
            self._expect_character(",")
            key = self._decode_value()
            if not isinstance(key, str) or key == "cycles" or key in metadata:
                raise ValueError("Result archive contains an invalid top-level field.")
            self._expect_character(":")
            metadata[key] = self._decode_value()
        self._skip_whitespace()
        while not self.eof:
            self._fill()
            self._skip_whitespace()
        if self.position != len(self.buffer):
            raise ValueError("Result archive contains trailing content.")
        if set(metadata) | {"cycles"} != self.top_level_fields:
            raise ValueError("Result archive top-level fields do not match its schema.")
        if self.byte_count != self.expected_size:
            raise ValueError("Result archive size does not match its immutable index.")
        actual_digest = "sha256:" + self.digest.hexdigest()
        if actual_digest != self.expected_digest:
            raise ValueError("Result archive digest does not match its immutable index.")
        self.metadata = metadata


def normalize_projection_paths(paths, *, top_level_fields=None):
    if not isinstance(paths, list):
        raise ValueError("Result slice paths must be an array.")
    if (
        any(not isinstance(path, str) or not path.strip() for path in paths)
        or len(paths) != len(set(paths))
    ):
        raise ValueError("Result slice paths must be unique non-empty strings.")
    allowed_fields = (
        _RESULT_FIELDS if top_level_fields is None else frozenset(top_level_fields)
    )
    normalized = []
    for path in paths:
        parts = split_data_path(path)
        if parts[0] not in allowed_fields:
            raise ValueError(f"Result slice references unknown path '{path}'.")
        normalized.append((path, parts))
    return tuple(normalized)


def normalize_projection_window(value):
    """Return one exact half-open cycle window without changing execution scope.

    Temporary Modules still execute from the first cycle so state and warm-up
    semantics are identical to a full Result projection.  The window only
    limits rows encoded for the Visualizer transport boundary.
    """

    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "startIndex", "endIndexExclusive",
    }:
        raise ValueError("Result projection window has an invalid exact shape.")
    if "startIndex" not in value or "endIndexExclusive" not in value:
        raise ValueError("Result projection window requires both boundaries.")
    start = value["startIndex"]
    end = value["endIndexExclusive"]
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or start < 0
        or isinstance(end, bool)
        or not isinstance(end, int)
        or end <= start
    ):
        raise ValueError("Result projection window must be a positive half-open range.")
    return {"startIndex": start, "endIndexExclusive": end}


class _ColumnarProjectionSink:
    """Bounded-memory column encoder for the Visualizer projection contract."""

    def __init__(self, root, cycle_paths, window):
        if any(parts == ("cycles",) for _path, parts in cycle_paths):
            raise ValueError(
                "Columnar Result projection requires explicit cycle child paths."
            )
        if len(cycle_paths) > MAX_COLUMNAR_PROJECTION_COLUMNS:
            raise ValueError("Columnar Result projection requests too many columns.")
        self.root = Path(root)
        self.cycle_paths = tuple(cycle_paths)
        self.window = normalize_projection_window(window)
        self.value_handles = []
        self.absent_handles = []
        self.selected_count = 0
        for index, _item in enumerate(self.cycle_paths):
            value_path = self.root / f"value-{index}.jsonl"
            absent_path = self.root / f"absent-{index}.jsonl"
            self.value_handles.append(value_path.open("w", encoding="utf-8"))
            self.absent_handles.append(absent_path.open("w", encoding="ascii"))

    def _selected(self, index):
        if self.window is None:
            return True
        return (
            self.window["startIndex"]
            <= index
            < self.window["endIndexExclusive"]
        )

    def add(self, index, cycle):
        if not self._selected(index):
            return
        output_index = self.selected_count
        for column_index, (_path, parts) in enumerate(self.cycle_paths):
            value = get_data_segments(cycle, parts[1:], _MISSING)
            if value is _MISSING:
                encoded = "null"
                self.absent_handles[column_index].write(str(output_index) + "\n")
            else:
                encoded = strict_json.dumps(
                    value, sort_keys=True, separators=(",", ":")
                )
            self.value_handles[column_index].write(encoded + "\n")
        self.selected_count += 1

    def close(self):
        first_error = None
        for handle in (*self.value_handles, *self.absent_handles):
            try:
                handle.close()
            except BaseException as exc:
                first_error = first_error or exc
        self.value_handles = []
        self.absent_handles = []
        if first_error is not None:
            raise first_error

    @staticmethod
    def _write_array(output, path):
        output.write("[")
        first = True
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                value = line.rstrip("\n")
                if not value:
                    raise ValueError("Columnar Result projection spool is invalid.")
                if not first:
                    output.write(",")
                output.write(value)
                first = False
        output.write("]")

    def write_columns(self, output):
        output.write('"columnOrder":')
        output.write(strict_json.dumps(
            [path for path, _parts in self.cycle_paths],
            separators=(",", ":"),
        ))
        output.write(',"columns":{')
        for index, (path, _parts) in enumerate(self.cycle_paths):
            if index:
                output.write(",")
            output.write(strict_json.dumps(path, separators=(",", ":")))
            output.write(':{"values":')
            self._write_array(output, self.root / f"value-{index}.jsonl")
            output.write(',"absent":')
            self._write_array(output, self.root / f"absent-{index}.jsonl")
            output.write("}")
        output.write("}")


def _columnar_projected_metadata(metadata, metadata_paths):
    projected = {}
    for path, parts in metadata_paths:
        value = get_data_segments(metadata, parts, _MISSING)
        if value is _MISSING:
            raise ValueError(f"Result slice path '{path}' is missing.")
        if parts[0] == "dataKeys":
            continue
        set_data_segments(projected, parts, value)
    return projected


def _write_columnar_document(
    destination,
    *,
    sink,
    data_keys,
    metadata,
    metadata_paths,
    total_count,
):
    start = 0 if sink.window is None else min(
        sink.window["startIndex"], total_count
    )
    requested_end = total_count if sink.window is None else sink.window[
        "endIndexExclusive"
    ]
    end = min(requested_end, total_count)
    projected_metadata = _columnar_projected_metadata(metadata, metadata_paths)
    with Path(destination).open("w", encoding="utf-8") as output:
        output.write('{"projectionSchemaVersion":')
        output.write(str(COLUMNAR_PROJECTION_SCHEMA_VERSION))
        output.write(',"projectionFormat":')
        output.write(strict_json.dumps(COLUMNAR_PROJECTION_FORMAT))
        output.write(',"dataKeys":')
        output.write(strict_json.dumps(
            data_keys, sort_keys=True, separators=(",", ":")
        ))
        output.write(',"totalRowCount":')
        output.write(str(total_count))
        output.write(',"window":{"startIndex":')
        output.write(str(start))
        output.write(',"endIndexExclusive":')
        output.write(str(end))
        output.write('},')
        sink.write_columns(output)
        for key, value in projected_metadata.items():
            output.write(",")
            output.write(strict_json.dumps(key, separators=(",", ":")))
            output.write(":")
            output.write(strict_json.dumps(
                value, sort_keys=True, separators=(",", ":")
            ))
        output.write("}")
        output.flush()
        os.fsync(output.fileno())


def _write_columnar_projection(
    source_path,
    destination_path,
    *,
    normalized,
    data_keys,
    expected_digest,
    expected_size,
    prepare_cycle,
    finalize_cycles,
    validate_metadata,
    top_level_fields,
    capture_cycle,
    capture_metadata,
    window,
):
    cycle_paths = tuple(item for item in normalized if item[1][0] == "cycles")
    metadata_paths = tuple(item for item in normalized if item[1][0] != "cycles")
    destination = Path(destination_path)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)
    cycle_count = 0
    first_cycle_id = None
    last_cycle_id = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="trade-result-columns-", dir=destination.parent
        ) as spool_root:
            sink = _ColumnarProjectionSink(spool_root, cycle_paths, window)
            sink_error = None
            try:
                with ResultArchiveReader(
                    source_path,
                    expected_digest=expected_digest,
                    expected_size=expected_size,
                    top_level_fields=top_level_fields,
                ) as reader:
                    for index, cycle in enumerate(reader.cycles()):
                        if capture_cycle is not None:
                            capture_cycle(copy.deepcopy(cycle))
                        cycle = prepare_cycle(index, cycle)
                        cycle_id = cycle["cycleId"]
                        if first_cycle_id is None:
                            first_cycle_id = cycle_id
                        last_cycle_id = cycle_id
                        cycle_count += 1
                        sink.add(index, cycle)
                    finalize_cycles()
                    metadata = reader.metadata
                    if capture_metadata is not None:
                        capture_metadata(copy.deepcopy(metadata))
                    validate_metadata(
                        metadata,
                        cycle_count=cycle_count,
                        first_cycle_id=first_cycle_id,
                        last_cycle_id=last_cycle_id,
                    )
            except BaseException as exc:
                sink_error = exc
            try:
                sink.close()
            except BaseException as exc:
                if sink_error is None:
                    sink_error = exc
                else:
                    _attach_cleanup_context(sink_error, exc)
            if sink_error is not None:
                raise sink_error
            _write_columnar_document(
                temporary,
                sink=sink,
                data_keys=data_keys,
                metadata=metadata,
                metadata_paths=metadata_paths,
                total_count=cycle_count,
            )
        os.replace(temporary, destination)
        return destination
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        cleanup_error = None
        for path in (temporary, destination):
            try:
                path.unlink(missing_ok=True)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            _attach_cleanup_context(primary_error, cleanup_error)
        raise primary_error.with_traceback(primary_traceback)


def _write_columnar_projection_from_cycles(
    cycles,
    metadata,
    destination_path,
    *,
    normalized,
    data_keys,
    prepare_cycle,
    finalize_cycles,
    validate_metadata,
    copy_cycle,
    window,
):
    cycle_paths = tuple(item for item in normalized if item[1][0] == "cycles")
    metadata_paths = tuple(item for item in normalized if item[1][0] != "cycles")
    destination = Path(destination_path)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)
    cycle_count = 0
    first_cycle_id = None
    last_cycle_id = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="trade-result-columns-", dir=destination.parent
        ) as spool_root:
            sink = _ColumnarProjectionSink(spool_root, cycle_paths, window)
            sink_error = None
            try:
                for index, base_cycle in enumerate(cycles):
                    cycle = prepare_cycle(index, copy_cycle(base_cycle))
                    cycle_id = cycle["cycleId"]
                    if first_cycle_id is None:
                        first_cycle_id = cycle_id
                    last_cycle_id = cycle_id
                    cycle_count += 1
                    sink.add(index, cycle)
                finalize_cycles()
                validate_metadata(
                    metadata,
                    cycle_count=cycle_count,
                    first_cycle_id=first_cycle_id,
                    last_cycle_id=last_cycle_id,
                )
            except BaseException as exc:
                sink_error = exc
            try:
                sink.close()
            except BaseException as exc:
                if sink_error is None:
                    sink_error = exc
                else:
                    _attach_cleanup_context(sink_error, exc)
            if sink_error is not None:
                raise sink_error
            _write_columnar_document(
                temporary,
                sink=sink,
                data_keys=data_keys,
                metadata=metadata,
                metadata_paths=metadata_paths,
                total_count=cycle_count,
            )
        os.replace(temporary, destination)
        return destination
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        cleanup_error = None
        for path in (temporary, destination):
            try:
                path.unlink(missing_ok=True)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            _attach_cleanup_context(primary_error, cleanup_error)
        raise primary_error.with_traceback(primary_traceback)


def _project_cycle(cycle, cycle_paths, *, full_cycles):
    if full_cycles:
        return cycle
    projected = {
        field: cycle[field]
        for field in _CYCLE_IDENTITY_FIELDS
    }
    for _path, parts in cycle_paths:
        relative = parts[1:]
        if not relative or relative[0] in _CYCLE_IDENTITY_FIELDS:
            continue
        value = get_data_segments(cycle, relative, _MISSING)
        # Sequence projection permits a declared optional DataKey to be absent
        # in an individual cycle.  Required presence is enforced by the Result
        # cycle validator before this projection runs.
        if value is not _MISSING:
            set_data_segments(projected, relative, value)
    return projected


def _attach_cleanup_context(primary_error, cleanup_error):
    """Attach the first cleanup failure without replacing an operation error."""
    if cleanup_error is not None and primary_error.__context__ is None:
        cleanup_error.__context__ = None
        primary_error.__context__ = cleanup_error


@contextmanager
def _projection_resources(
    source_path,
    temporary,
    *,
    expected_digest,
    expected_size,
    top_level_fields,
):
    """Own reader/output cleanup while preserving the operation's first error."""
    reader = ResultArchiveReader(
        source_path,
        expected_digest=expected_digest,
        expected_size=expected_size,
        top_level_fields=top_level_fields,
    )
    output = None
    primary_error = None
    primary_traceback = None
    try:
        reader.__enter__()
        output = temporary.open("w", encoding="utf-8")
        yield reader, output
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
    cleanup_error = None
    if output is not None:
        try:
            output.close()
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
    try:
        reader.__exit__(None, None, None)
    except BaseException as exc:
        cleanup_error = cleanup_error or exc
    if primary_error is not None:
        _attach_cleanup_context(primary_error, cleanup_error)
        raise primary_error.with_traceback(primary_traceback)
    if cleanup_error is not None:
        raise cleanup_error


def write_projection(
    source_path,
    destination_path,
    *,
    paths,
    data_keys,
    expected_digest,
    expected_size,
    prepare_cycle,
    finalize_cycles,
    validate_metadata,
    top_level_fields=None,
    capture_cycle=None,
    capture_metadata=None,
    projection_format="rows",
    window=None,
):
    """Validate and project a Result to one atomically published JSON file."""
    normalized = normalize_projection_paths(
        paths,
        top_level_fields=top_level_fields,
    )
    if projection_format == COLUMNAR_PROJECTION_FORMAT:
        return _write_columnar_projection(
            source_path,
            destination_path,
            normalized=normalized,
            data_keys=data_keys,
            expected_digest=expected_digest,
            expected_size=expected_size,
            prepare_cycle=prepare_cycle,
            finalize_cycles=finalize_cycles,
            validate_metadata=validate_metadata,
            top_level_fields=top_level_fields,
            capture_cycle=capture_cycle,
            capture_metadata=capture_metadata,
            window=window,
        )
    if projection_format != "rows" or window is not None:
        raise ValueError("Result projection format or window is unsupported.")
    cycle_paths = tuple(item for item in normalized if item[1][0] == "cycles")
    metadata_paths = tuple(item for item in normalized if item[1][0] != "cycles")
    full_cycles = any(parts == ("cycles",) for _path, parts in cycle_paths)
    destination = Path(destination_path)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)
    cycle_count = 0
    first_cycle_id = None
    last_cycle_id = None
    try:
        with _projection_resources(
            source_path,
            temporary,
            expected_digest=expected_digest,
            expected_size=expected_size,
            top_level_fields=top_level_fields,
        ) as (reader, output):
            output.write('{"dataKeys":')
            output.write(strict_json.dumps(data_keys, sort_keys=True, separators=(",", ":")))
            if cycle_paths:
                output.write(',"cycles":[')
            first_output = True
            for index, cycle in enumerate(reader.cycles()):
                if capture_cycle is not None:
                    capture_cycle(copy.deepcopy(cycle))
                cycle = prepare_cycle(index, cycle)
                cycle_id = cycle["cycleId"]
                if first_cycle_id is None:
                    first_cycle_id = cycle_id
                last_cycle_id = cycle_id
                cycle_count += 1
                if cycle_paths:
                    if not first_output:
                        output.write(",")
                    output.write(strict_json.dumps(
                        _project_cycle(
                            cycle, cycle_paths, full_cycles=full_cycles
                        ),
                        sort_keys=True,
                        separators=(",", ":"),
                    ))
                    first_output = False
            finalize_cycles()
            if cycle_paths:
                output.write("]")
            metadata = reader.metadata
            if capture_metadata is not None:
                capture_metadata(copy.deepcopy(metadata))
            validate_metadata(
                metadata,
                cycle_count=cycle_count,
                first_cycle_id=first_cycle_id,
                last_cycle_id=last_cycle_id,
            )
            projected_metadata = {}
            for path, parts in metadata_paths:
                value = get_data_segments(metadata, parts, _MISSING)
                if value is _MISSING:
                    raise ValueError(f"Result slice path '{path}' is missing.")
                if parts[0] == "dataKeys":
                    # DataKey declarations are always returned in full so the
                    # projection remains self-describing.
                    continue
                set_data_segments(projected_metadata, parts, value)
            for key, value in projected_metadata.items():
                output.write(",")
                output.write(strict_json.dumps(key, separators=(",", ":")))
                output.write(":")
                output.write(strict_json.dumps(value, sort_keys=True, separators=(",", ":")))
            output.write("}")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        return destination
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        cleanup_error = None
        for path in (temporary, destination):
            try:
                path.unlink(missing_ok=True)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            # The archive/projection failure is authoritative.  Keep the first
            # cleanup failure as diagnostic context without allowing it to
            # replace the operation that triggered teardown.
            _attach_cleanup_context(primary_error, cleanup_error)
        raise primary_error.with_traceback(primary_traceback)


def write_projection_from_cycles(
    cycles,
    metadata,
    destination_path,
    *,
    paths,
    data_keys,
    prepare_cycle,
    finalize_cycles,
    validate_metadata,
    top_level_fields=None,
    copy_cycle=copy.deepcopy,
    projection_format="rows",
    window=None,
):
    """Project verified cached Result frames to one atomic JSON document.

    Cached base cycles are copied before temporary Modules execute.  The cache
    therefore never owns or reuses mutable Signal state, while the same cycle
    and metadata validators still run for every projection request.
    """

    if not isinstance(cycles, (list, tuple)) or not isinstance(metadata, dict):
        raise ValueError("Cached Result frames are invalid.")
    normalized = normalize_projection_paths(
        paths,
        top_level_fields=top_level_fields,
    )
    if projection_format == COLUMNAR_PROJECTION_FORMAT:
        return _write_columnar_projection_from_cycles(
            cycles,
            metadata,
            destination_path,
            normalized=normalized,
            data_keys=data_keys,
            prepare_cycle=prepare_cycle,
            finalize_cycles=finalize_cycles,
            validate_metadata=validate_metadata,
            copy_cycle=copy_cycle,
            window=window,
        )
    if projection_format != "rows" or window is not None:
        raise ValueError("Result projection format or window is unsupported.")
    cycle_paths = tuple(item for item in normalized if item[1][0] == "cycles")
    metadata_paths = tuple(item for item in normalized if item[1][0] != "cycles")
    full_cycles = any(parts == ("cycles",) for _path, parts in cycle_paths)
    destination = Path(destination_path)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)
    cycle_count = 0
    first_cycle_id = None
    last_cycle_id = None
    output = None
    try:
        output = temporary.open("w", encoding="utf-8")
        output.write('{"dataKeys":')
        output.write(strict_json.dumps(data_keys, sort_keys=True, separators=(",", ":")))
        if cycle_paths:
            output.write(',"cycles":[')
        first_output = True
        for index, base_cycle in enumerate(cycles):
            cycle = prepare_cycle(index, copy_cycle(base_cycle))
            cycle_id = cycle["cycleId"]
            if first_cycle_id is None:
                first_cycle_id = cycle_id
            last_cycle_id = cycle_id
            cycle_count += 1
            if cycle_paths:
                if not first_output:
                    output.write(",")
                output.write(strict_json.dumps(
                    _project_cycle(cycle, cycle_paths, full_cycles=full_cycles),
                    sort_keys=True,
                    separators=(",", ":"),
                ))
                first_output = False
        finalize_cycles()
        if cycle_paths:
            output.write("]")
        validate_metadata(
            metadata,
            cycle_count=cycle_count,
            first_cycle_id=first_cycle_id,
            last_cycle_id=last_cycle_id,
        )
        projected_metadata = {}
        for path, parts in metadata_paths:
            value = get_data_segments(metadata, parts, _MISSING)
            if value is _MISSING:
                raise ValueError(f"Result slice path '{path}' is missing.")
            if parts[0] == "dataKeys":
                continue
            set_data_segments(projected_metadata, parts, value)
        for key, value in projected_metadata.items():
            output.write(",")
            output.write(strict_json.dumps(key, separators=(",", ":")))
            output.write(":")
            output.write(strict_json.dumps(value, sort_keys=True, separators=(",", ":")))
        output.write("}")
        output.flush()
        os.fsync(output.fileno())
        output.close()
        output = None
        os.replace(temporary, destination)
        return destination
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        cleanup_error = None
        if output is not None:
            try:
                output.close()
            except BaseException as exc:
                cleanup_error = exc
        for path in (temporary, destination):
            try:
                path.unlink(missing_ok=True)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            _attach_cleanup_context(primary_error, cleanup_error)
        raise primary_error.with_traceback(primary_traceback)


__all__ = (
    "COLUMNAR_PROJECTION_FORMAT",
    "COLUMNAR_PROJECTION_SCHEMA_VERSION",
    "FRAMED_RESULT_METADATA_PREFIX",
    "FRAMED_RESULT_PREFIX",
    "MAX_RESULT_VERIFICATION_SHARDS",
    "ResultArchiveReader",
    "ResultCycleIdentityLedger",
    "UniqueTextIndex",
    "count_framed_cycle_lines",
    "hash_result_archive",
    "iter_framed_cycle_values",
    "merge_cycle_identity_ledgers",
    "normalize_projection_paths",
    "normalize_projection_window",
    "plan_framed_cycle_ranges",
    "read_framed_result_metadata",
    "write_projection",
    "write_projection_from_cycles",
)
