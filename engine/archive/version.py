#!/usr/bin/env python3
"""One immutable directory archive contract for every executable resource."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from engine.contracts import strict_json
from engine.contracts import digest as digest_contracts


MANIFEST_NAME = ".archive-manifest.json"
RECORD_NAME = ".archive-record.json"
ARCHIVED_STATUS = "archived"
ARCHIVE_MANIFEST_FIELDS = frozenset({
    "schemaVersion", "resourceType", "resourceId", "version", "status",
    "contentDigest", "files", "manifestDigest",
})
RECORD_ARCHIVE_MANIFEST_FIELDS = ARCHIVE_MANIFEST_FIELDS | frozenset({"recordFields"})
COMMON_RECORD_FIELDS = frozenset({
    "version", "status", "contentDigest", "createdAt", "archive",
})
STAGING_PREFIX = ".archive-staging-"


def canonical_json(value) -> bytes:
    return strict_json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def content_digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def next_version(records, *, identity_key: str, identity: str) -> str:
    if not isinstance(identity_key, str) or not identity_key:
        raise ValueError("Archived version identity key must be a non-empty string.")
    if not isinstance(identity, str) or not identity:
        raise ValueError("Archived version identity must be a non-empty string.")
    versions = []
    for record in records:
        if record[identity_key] != identity:
            continue
        raw = record["version"]
        if not raw.isdigit() or int(raw) < 1:
            raise ValueError(
                f"Archived {identity_key} '{identity}' contains non-monotonic version '{raw}'."
            )
        versions.append(int(raw))
    return str(max(versions, default=0) + 1)


def unchanged_version(records, digest: str, *, identity_key: str, identity: str):
    identity_records = [
        record for record in records
        if record[identity_key] == identity
        and record["status"] == ARCHIVED_STATUS
    ]
    if not identity_records:
        return None
    latest = max(identity_records, key=lambda record: int(record["version"]))
    return latest if latest["contentDigest"] == digest else None


def validate_record_collection_material(
    records,
    identity_fields,
    *,
    immutable_fields=(),
):
    records = list(records)
    identity_fields = tuple(identity_fields)
    if isinstance(immutable_fields, (str, bytes)):
        raise ValueError("Archived resource immutable_fields must be a field collection.")
    immutable_fields = tuple(immutable_fields)
    if (
        any(not isinstance(field, str) or not field for field in immutable_fields)
        or len(immutable_fields) != len(set(immutable_fields))
    ):
        raise ValueError(
            "Archived resource immutable_fields must contain unique non-empty field names."
        )
    if not identity_fields:
        raise ValueError("Archived version collection requires identity fields.")
    grouped: dict[tuple[str, ...], list[int]] = {}
    immutable_values = {}
    for record in records:
        identity = tuple(record.get(field) for field in identity_fields)
        if not all(isinstance(value, str) and value.strip() for value in identity):
            raise ValueError(
                "Archived version is missing identity field(s): "
                + ", ".join(identity_fields)
            )
        grouped.setdefault(identity, []).append(int(record["version"]))
        for field in immutable_fields:
            if field not in record:
                raise ValueError(
                    f"Archived version is missing immutable identity field '{field}'."
                )
            key = (identity, field)
            if key in immutable_values and immutable_values[key] != record[field]:
                raise ValueError(
                    f"Archived version history changes immutable field '{field}': "
                    + "/".join(identity)
                )
            immutable_values[key] = record[field]
    for identity, versions in grouped.items():
        expected = list(range(1, max(versions) + 1))
        if sorted(versions) != expected:
            label = "/".join(identity)
            raise ValueError(f"Archived version history is not complete and monotonic: {label}")
    return records


def verify_record_collection(
    records, identity_fields, *, immutable_fields=()
) -> list[dict]:
    """Verify complete monotonic version histories for one or more identities."""
    records = list(records)
    identity_fields = tuple(identity_fields)
    if not isinstance(immutable_fields, (str, bytes)):
        immutable_fields = tuple(immutable_fields)
    validate_record_collection_material(
        (),
        identity_fields,
        immutable_fields=immutable_fields,
    )
    for record in records:
        verify_record(record)
    return validate_record_collection_material(
        records,
        identity_fields,
        immutable_fields=immutable_fields,
    )


def verify_record_index(records, identity_fields, *, immutable_fields=()) -> dict:
    """Verify a JSON object whose key is identity fields plus numeric version."""
    if not isinstance(records, Mapping):
        raise ValueError("Archived version index must be an object.")
    verified = verify_record_collection(
        records.values(), identity_fields, immutable_fields=immutable_fields
    )
    for key, record in zip(records.keys(), verified):
        expected = "/".join([
            *(str(record[field]) for field in identity_fields),
            str(record["version"]),
        ])
        if key != expected:
            raise ValueError(f"Archived version index key mismatch: {key!r} != {expected!r}")
    return dict(records)


def _files(root: Path):
    root = Path(root)
    root_manifest = root / MANIFEST_NAME
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError(f"Archived resources may not contain symbolic links: {path}")
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(
                f"Archived resources may contain only files and directories: {path}"
            )
        if path != root_manifest:
            yield path, path.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def file_manifest(root: Path) -> list[dict]:
    return [
        {
            "path": relative,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "executable": bool(path.stat().st_mode & stat.S_IXUSR),
        }
        for path, relative in _files(Path(root))
    ]


def resolve_managed_path(managed_root, path, *, label="Managed path") -> Path:
    """Resolve a path only after proving its managed subtree has no symlink hop."""

    raw_root = Path(managed_root).expanduser().absolute()
    raw_path = Path(path).expanduser().absolute()
    if raw_root.is_symlink():
        raise ValueError(f"{label} root may not be a symbolic link: {raw_root}")
    resolved_root = raw_root.resolve()
    try:
        relative = raw_path.relative_to(raw_root)
        traversal_root = raw_root
    except ValueError:
        try:
            relative = raw_path.relative_to(resolved_root)
            traversal_root = resolved_root
        except ValueError as exc:
            raise ValueError(f"{label} is outside its managed root: {raw_path}") from exc
    current = traversal_root
    if current.is_symlink():
        raise ValueError(f"{label} may not traverse a symbolic link: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} may not traverse a symbolic link: {current}")
    resolved_path = raw_path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside its managed root: {raw_path}") from exc
    return resolved_path


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(strict_json.dumps(payload, indent=2, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_record_snapshot(staging, record) -> None:
    """Write the authoritative record to its one reserved archive location."""

    _write_json(Path(staging) / RECORD_NAME, record)


def _open_no_follow(path: Path, *, directory: bool) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    return os.open(path, flags)


def _fsync_directory(path: Path) -> None:
    descriptor = _open_no_follow(Path(path), directory=True)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_tree(root) -> None:
    """Persist every file, mode and directory entry in one immutable tree."""

    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Archive sync root must be a real directory: {root}")
    directories = [root]
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError(f"Archived resources may not contain symlinks: {path}")
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            directories.append(path)
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(
                f"Archived resources may contain only files and directories: {path}"
            )
        descriptor = _open_no_follow(path, directory=False)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)


def _fsync_directory_chain(path: Path, managed_root: Path) -> None:
    current = Path(path)
    managed_root = Path(managed_root)
    try:
        current.relative_to(managed_root)
    except ValueError as exc:
        raise ValueError(f"Archive directory is outside its managed root: {current}") from exc
    while True:
        if current.is_symlink() or not current.is_dir():
            raise ValueError(f"Archive parent must be a real directory: {current}")
        _fsync_directory(current)
        if current == managed_root:
            return
        current = current.parent


def publish_staging_directory(staging, destination, *, managed_root) -> Path:
    """Durably rename one prepared directory inside a managed filesystem tree."""

    managed_root = Path(managed_root).expanduser().absolute()
    if managed_root.is_symlink():
        raise ValueError(f"Archive managed root may not be a symbolic link: {managed_root}")
    managed_root = managed_root.resolve()
    staging = resolve_managed_path(
        managed_root, staging, label="Archive staging directory"
    )
    destination = resolve_managed_path(
        managed_root, destination, label="Archived version destination"
    )
    if staging.is_symlink() or not staging.is_dir():
        raise ValueError(f"Archive staging directory does not exist: {staging}")
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Archived version directory already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = resolve_managed_path(
        managed_root, destination, label="Archived version destination"
    )
    source_parent = staging.parent
    destination_parent = destination.parent
    fsync_tree(staging)
    _fsync_directory_chain(source_parent, managed_root)
    if destination_parent != source_parent:
        _fsync_directory_chain(destination_parent, managed_root)
    os.replace(staging, destination)
    _fsync_directory(destination_parent)
    if source_parent != destination_parent:
        _fsync_directory(source_parent)
    return destination


def verify_archive(root, expected=None) -> dict:
    root = Path(root)
    if root.is_symlink():
        raise ValueError(f"Archived resource root may not be a symbolic link: {root}")
    root = root.resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"Archived resource is missing {MANIFEST_NAME}: {root}")
    manifest = strict_json.loads(manifest_path.read_text(encoding="utf-8"))
    allowed_fields = (
        RECORD_ARCHIVE_MANIFEST_FIELDS
        if "recordFields" in manifest
        else ARCHIVE_MANIFEST_FIELDS
    )
    if set(manifest) != allowed_fields:
        raise ValueError(f"Archived resource manifest has an invalid schema: {root}")
    if manifest["schemaVersion"] != 1:
        raise ValueError(f"Archived resource manifest schemaVersion 1 is required: {root}")
    for field in ("resourceType", "resourceId", "version", "contentDigest", "manifestDigest"):
        if not isinstance(manifest[field], str) or not manifest[field]:
            raise ValueError(f"Archived resource manifest field '{field}' is invalid: {root}")
    if not digest_contracts.is_sha256_digest(manifest["contentDigest"]):
        raise ValueError(f"Archived resource contentDigest is invalid: {root}")
    if not digest_contracts.is_sha256_digest(manifest["manifestDigest"]):
        raise ValueError(f"Archived resource manifestDigest is invalid: {root}")
    if "recordFields" in manifest and (
        not isinstance(manifest["recordFields"], list)
        or not manifest["recordFields"]
        or any(not isinstance(field, str) or not field for field in manifest["recordFields"])
        or manifest["recordFields"] != sorted(set(manifest["recordFields"]))
        or not COMMON_RECORD_FIELDS.issubset(manifest["recordFields"])
    ):
        raise ValueError(f"Archived resource recordFields is invalid: {root}")
    files = manifest["files"]
    if not isinstance(files, list) or any(
        not isinstance(item, dict)
        or set(item) != {"path", "size", "sha256", "executable"}
        or not isinstance(item["path"], str)
        or not item["path"]
        or isinstance(item["size"], bool)
        or not isinstance(item["size"], int)
        or item["size"] < 0
        or not isinstance(item["sha256"], str)
        or not digest_contracts.is_sha256_digest(item["sha256"])
        or not isinstance(item["executable"], bool)
        for item in files
    ):
        raise ValueError(f"Archived resource file manifest is invalid: {root}")
    if manifest["status"] != ARCHIVED_STATUS:
        raise ValueError(f"Resource archive is not Archived: {root}")
    actual_files = file_manifest(root)
    if actual_files != manifest["files"]:
        raise ValueError(f"Archived resource verification failed: {root}")
    manifest_core = {
        key: value for key, value in manifest.items()
        if key != "manifestDigest"
    }
    actual_manifest_digest = content_digest(manifest_core)
    if manifest["manifestDigest"] != actual_manifest_digest:
        raise ValueError(f"Archived resource manifest digest failed: {root}")
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            raise ValueError(f"Archived resources may not contain symlinks: {path}")
        if path.stat().st_mode & 0o222:
            raise ValueError(f"Archived resource is writable: {path}")
    if expected:
        for field, value in expected.items():
            if manifest.get(field) != value:
                raise ValueError(
                    f"Archived resource {field} mismatch: expected {value!r}, "
                    f"found {manifest.get(field)!r}."
                )
    return manifest


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise ValueError(f"Archived resources may not contain symlinks: {path}")
        mode = path.stat().st_mode
        if path.is_dir():
            path.chmod((mode & 0o555) | 0o500)
        else:
            path.chmod(mode & ~0o222)
    root.chmod((root.stat().st_mode & 0o555) | 0o500)


def seal_directory(
    staging,
    destination,
    *,
    managed_root,
    resource_type,
    resource_id,
    version,
    digest,
    record_fields=None,
) -> dict:
    staging = resolve_managed_path(
        managed_root, staging, label="Archive staging directory"
    )
    destination = resolve_managed_path(
        managed_root, destination, label="Archived version destination"
    )
    if not staging.is_dir():
        raise ValueError(f"Archive staging directory does not exist: {staging}")
    if destination.exists():
        raise ValueError(f"Archived version directory already exists: {destination}")
    if (staging / MANIFEST_NAME).exists():
        raise ValueError(f"Archive staging may not contain reserved file {MANIFEST_NAME}.")
    if record_fields is None and (staging / RECORD_NAME).exists():
        raise ValueError(f"Archive staging may not contain reserved file {RECORD_NAME}.")
    if record_fields is not None and not (staging / RECORD_NAME).is_file():
        raise ValueError(f"Archived record transaction is missing {RECORD_NAME}.")
    for label, value in (
        ("resource_type", resource_type),
        ("resource_id", resource_id),
        ("version", version),
        ("digest", digest),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"Archive {label} must be a non-empty string.")
    if not digest_contracts.is_sha256_digest(digest):
        raise ValueError("Archive digest must be a sha256 digest.")
    manifest = {
        "schemaVersion": 1,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "version": version,
        "status": ARCHIVED_STATUS,
        "contentDigest": digest,
        "files": file_manifest(staging),
    }
    if record_fields is not None:
        fields = sorted(set(record_fields))
        if not fields or not COMMON_RECORD_FIELDS.issubset(fields):
            raise ValueError("Archived record fields must include the common record contract.")
        manifest["recordFields"] = fields
    manifest["manifestDigest"] = content_digest(manifest)
    _write_json(staging / MANIFEST_NAME, manifest)
    _make_read_only(staging)
    verify_archive(staging, {
        "resourceType": resource_type,
        "resourceId": resource_id,
        "version": version,
        "contentDigest": digest,
    })
    try:
        publish_staging_directory(
            staging,
            destination,
            managed_root=managed_root,
        )
        verified = verify_archive(destination, {
            "resourceType": resource_type,
            "resourceId": resource_id,
            "version": version,
            "contentDigest": digest,
        })
        return verified
    except BaseException:
        if destination.exists():
            discard_archive(destination)
        raise


def staging_directory(parent) -> Path:
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=str(parent)))


def discard_archive(root) -> None:
    """Remove an uncommitted archive after its state transaction failed."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError(f"Uncommitted archive may not be a symbolic link: {root}")
    if not root.exists():
        return
    if not root.is_dir():
        raise ValueError(f"Uncommitted archive must be a directory: {root}")
    parent = root.parent

    # Restore directory permissions top-down before unlinking children.  An
    # unlink needs write permission on the containing directory; changing only
    # the file reported by shutil's onerror callback cannot repair a sealed
    # (0555/0500) parent and causes rollback to mask the original failure.
    root.chmod(root.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        current_path.chmod(
            current_path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        )
        for name in directories:
            path = current_path / name
            if not path.is_symlink():
                path.chmod(
                    path.stat().st_mode
                    | stat.S_IRUSR
                    | stat.S_IWUSR
                    | stat.S_IXUSR
                )
        for name in files:
            path = current_path / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR)

    def make_writable_and_retry(function, path, _error):
        Path(path).chmod(Path(path).stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
        function(path)

    shutil.rmtree(root, onerror=make_writable_and_retry)
    _fsync_directory(parent)


def reconcile_staging_directories(managed_roots, *, committed_markers) -> list[str]:
    """Delete only owned archive staging directories outside committed archives."""

    if isinstance(managed_roots, (str, os.PathLike)):
        managed_roots = (managed_roots,)
    markers = tuple(committed_markers)
    if (
        not markers
        or any(
            not isinstance(marker, str)
            or not marker
            or Path(marker).name != marker
            for marker in markers
        )
    ):
        raise ValueError("Committed archive markers must be non-empty file names.")
    roots = []
    for raw_root in managed_roots:
        root = Path(raw_root).expanduser().absolute()
        if root.is_symlink():
            raise ValueError(f"Archive recovery root may not be a symbolic link: {root}")
        if not root.exists():
            continue
        root = root.resolve()
        if not root.is_dir():
            raise ValueError(f"Archive recovery root must be a directory: {root}")
        if any(root == existing or root.is_relative_to(existing) for existing in roots):
            continue
        roots = [existing for existing in roots if not existing.is_relative_to(root)]
        roots.append(root)

    removed = []

    def walk(directory: Path) -> None:
        marker_paths = [directory / marker for marker in markers]
        if any(marker.is_symlink() for marker in marker_paths):
            raise ValueError(
                f"Committed archive marker may not be a symbolic link: {directory}"
            )
        if any(marker.is_file() for marker in marker_paths):
            return
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            owned_staging = (
                entry.name.startswith(STAGING_PREFIX)
                and len(entry.name) > len(STAGING_PREFIX)
            )
            if entry.is_symlink():
                if owned_staging:
                    raise ValueError(
                        f"Archive staging recovery entry may not be a symbolic link: {entry}"
                    )
                continue
            if owned_staging:
                if not entry.is_dir():
                    raise ValueError(
                        f"Archive staging recovery entry must be a directory: {entry}"
                    )
                discard_archive(entry)
                removed.append(str(entry))
                continue
            if entry.is_dir():
                walk(entry)

    for root in sorted(roots, key=lambda item: item.as_posix()):
        walk(root)
    return removed


def verify_record(record) -> dict:
    if not isinstance(record, dict):
        raise ValueError("Archived version record must be an object.")
    strict_json.validate(record, path="Archived version record")
    missing_common = sorted(COMMON_RECORD_FIELDS - set(record))
    if missing_common:
        raise ValueError(
            "Archived version record is missing common field(s): " + ", ".join(missing_common)
        )
    if record["status"] != ARCHIVED_STATUS:
        raise ValueError("Only Archived versions can participate in execution.")
    version = record["version"]
    if (
        not isinstance(version, str)
        or not version.isdigit()
        or int(version) < 1
        or version != str(int(version))
    ):
        raise ValueError(
            "Archived version must be a positive canonical decimal integer."
        )
    if (
        not isinstance(record["contentDigest"], str)
        or not digest_contracts.is_sha256_digest(record["contentDigest"])
    ):
        raise ValueError("Archived version contentDigest is invalid.")
    if not isinstance(record["createdAt"], str) or not record["createdAt"]:
        raise ValueError("Archived version createdAt is invalid.")
    archive = record["archive"]
    if not isinstance(archive, dict) or set(archive) != {
        "resourceType", "resourceId", "root", "manifestDigest",
    }:
        raise ValueError("Archived version has an invalid archive descriptor.")
    for field in ("resourceType", "resourceId", "root", "manifestDigest"):
        if not isinstance(archive[field], str) or not archive[field]:
            raise ValueError(f"Archived version archive.{field} is invalid.")
    root = archive["root"]
    manifest = verify_archive(root, {
        "resourceType": archive["resourceType"],
        "resourceId": archive["resourceId"],
        "version": version,
        "contentDigest": record["contentDigest"],
        "manifestDigest": archive["manifestDigest"],
    })
    record_fields = manifest["recordFields"]
    if not isinstance(record_fields, list) or set(record) != set(record_fields):
        raise ValueError("Archived version record does not match its declared exact schema.")
    try:
        created_at = datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Archived version createdAt is invalid.") from exc
    if created_at.tzinfo is None or created_at.utcoffset() != timezone.utc.utcoffset(created_at):
        raise ValueError("Archived version createdAt must be an absolute UTC timestamp.")
    archived_record_path = Path(root) / RECORD_NAME
    if not archived_record_path.is_file():
        raise ValueError(f"Archived resource is missing {RECORD_NAME}: {root}")
    archived_record = strict_json.loads(archived_record_path.read_text(encoding="utf-8"))
    indexed_record = strict_json.loads(strict_json.dumps(record))
    indexed_record["archive"].pop("manifestDigest")
    archived_archive = archived_record.get("archive") if isinstance(archived_record, dict) else None
    if not isinstance(archived_archive, dict) or set(archived_archive) != {
        "resourceType", "resourceId", "root",
    }:
        raise ValueError(f"Archived resource record has an invalid archive descriptor: {root}")
    if archived_record != indexed_record:
        raise ValueError(f"Archived resource index record does not match its archive: {root}")
    return manifest


def verify_record_location(record, *, managed_root, expected_root) -> dict:
    """Bind a self-verifying record to its repository's current managed root."""
    if not isinstance(record, dict):
        raise ValueError("Archived version record must be an object.")
    archive = record.get("archive")
    if not isinstance(archive, dict) or set(archive) != {
        "resourceType", "resourceId", "root", "manifestDigest",
    }:
        raise ValueError("Archived version has an invalid archive descriptor.")
    stored_root = archive["root"]
    if not isinstance(stored_root, str) or not stored_root:
        raise ValueError("Archived version archive.root is invalid.")
    stored_path = Path(stored_root)
    if (
        not stored_path.is_absolute()
        or os.path.normpath(stored_root) != stored_root
    ):
        raise ValueError(
            "Archived record location must be its canonical absolute path: "
            f"{stored_root}"
        )
    actual = resolve_managed_path(
        managed_root,
        stored_path,
        label="Archived record location",
    )
    expected = resolve_managed_path(
        managed_root,
        expected_root,
        label="Expected archived record location",
    )
    if stored_root != str(actual):
        raise ValueError(
            "Archived record location must be its canonical absolute path: "
            f"{stored_root}"
        )
    if actual != expected:
        raise ValueError(
            f"Archived record is outside its canonical repository destination: {actual}"
        )
    verify_record(record)
    return record
