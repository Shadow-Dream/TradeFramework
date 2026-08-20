#!/usr/bin/env python3
"""Exact immutable-container authority shared by every Dataset publisher."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engine.archive import version as version_archive
from engine.contracts import dataset as dataset_contracts
from engine.contracts import strict_json
from engine.contracts import digest as digest_contracts
from engine.contracts.data import compile_normalized_json_validator
from engine.contracts.data_model import normalize_schema
from engine.contracts.exact_fields import require_exact_fields
from engine.core import resource_ids


MANIFEST_NAME = "_dataset.json"
CAPABILITIES_DECLARATION_NAME = "_trade_dataset_capabilities.json"
RESERVED_ROOT_FILES = {
    MANIFEST_NAME,
    CAPABILITIES_DECLARATION_NAME,
    "_trade_dataset_export.json",
}
MANIFEST_FIELDS = frozenset({
    "schemaVersion", "datasetId", "datasetVersionId", "contentHash", "status",
    "dataset", "storage", "source", "capabilities", "files", "createdAt", "lineage",
    "build", "manifestDigest",
})
DATASET_DESCRIPTOR_FIELDS = frozenset({"datasetId", "source", "metadata"})
FILE_FIELDS = frozenset({"path", "size", "sha256"})
CAPABILITY_FIELDS = frozenset({"protocol", "descriptor"})
LINEAGE_FIELDS = frozenset({"alias", "datasetId", "datasetVersionId", "contentHash"})
RECORDS_DESCRIPTOR_FIELDS = frozenset({
    "path", "recordCount", "eventTimeField", "availableTimeField", "valueSchema",
})
RECORD_VALUE_SCHEMA_FIELDS = frozenset({"fields", "entityKeys", "sortKeys"})
VISUALIZATION_DESCRIPTOR_FIELDS = frozenset({"timeZone"})


def repository_root(release_root):
    return Path(release_root) / "_data"


def _manifest_job_ids(manifest):
    job_ids = []

    def visit(value, path):
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key == "jobId":
                    if not isinstance(child, str) or not child:
                        raise ValueError(
                            f"Dataset manifest {child_path} must be a non-empty string."
                        )
                    job_ids.append(child)
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(manifest, "manifest")
    if len(set(job_ids)) > 1:
        raise ValueError("Dataset manifest jobId authorities do not match.")
    return job_ids


def require_manifest_build_job_id(manifest, build_job_id):
    if not isinstance(build_job_id, str):
        raise ValueError("Dataset buildJobId must be a string.")
    job_ids = _manifest_job_ids(manifest)
    if build_job_id:
        if not job_ids or job_ids[0] != build_job_id:
            raise ValueError(
                "Dataset manifest jobId authority does not match buildJobId."
            )
    elif job_ids:
        raise ValueError(
            "Dataset manifest contains jobId authority without buildJobId."
        )
    return build_job_id


def validate_sealed_version_descriptor(version):
    """Validate one indexed Dataset Version against its sealed manifest descriptor."""
    require_exact_fields(
        version,
        allowed={
            "datasetVersionId",
            "datasetId",
            "contentHash",
            "status",
            "source",
            "capabilities",
            "createdAt",
            "storage",
            "manifest",
            "manifestDigest",
            "buildJobId",
        },
        required={
            "datasetVersionId",
            "datasetId",
            "contentHash",
            "status",
            "source",
            "capabilities",
            "createdAt",
            "storage",
            "manifest",
            "manifestDigest",
            "buildJobId",
        },
        label="Dataset Version",
    )
    if version["status"] != "sealed":
        raise ValueError(
            f"Dataset version is not sealed: {version['datasetVersionId']}"
        )
    for field in (
        "datasetVersionId",
        "datasetId",
        "contentHash",
        "createdAt",
        "manifestDigest",
        "buildJobId",
    ):
        if not isinstance(version[field], str):
            raise ValueError(f"Dataset Version {field} must be a string.")
    if (
        not version["datasetId"]
        or resource_ids.normalize_resource_id(version["datasetId"])
        != version["datasetId"]
        or version["datasetVersionId"]
        != f"{version['datasetId']}@{version['contentHash']}"
        or not digest_contracts.is_sha256_digest(version["contentHash"])
        or not digest_contracts.is_sha256_digest(version["manifestDigest"])
        or not version["createdAt"]
    ):
        raise ValueError("Dataset Version identity or digest is invalid.")
    require_exact_fields(
        version["storage"],
        allowed={"type", "uri"},
        required={"type", "uri"},
        label="Dataset Version storage",
    )
    if version["storage"]["type"] != "directory":
        raise ValueError("Dataset versions must use immutable directory storage.")
    dataset_contracts.normalize_dataset_source(version["source"])
    normalized_capabilities = normalize_capabilities(version["capabilities"])
    if normalized_capabilities != version["capabilities"]:
        raise ValueError("Dataset Version capabilities are not normalized.")
    manifest = validate_manifest(version["manifest"])
    require_manifest_build_job_id(manifest, version["buildJobId"])
    if (
        manifest["datasetVersionId"] != version["datasetVersionId"]
        or manifest["datasetId"] != version["datasetId"]
        or manifest["contentHash"] != version["contentHash"]
        or manifest["source"] != version["source"]
        or manifest["capabilities"] != version["capabilities"]
        or manifest["createdAt"] != version["createdAt"]
        or manifest["storage"] != version["storage"]
        or manifest["manifestDigest"] != version["manifestDigest"]
    ):
        raise ValueError("Dataset Version index does not match its sealed manifest.")
    return version


def resolve_version_storage_root(release_root, version):
    """Resolve a validated Version only inside its Engine-managed Dataset archive."""
    version = validate_sealed_version_descriptor(version)
    expected_storage = version_archive.resolve_managed_path(
        release_root,
        repository_root(release_root)
        / resource_ids.normalize_resource_id(version["datasetId"])
        / "versions"
        / version["contentHash"].split(":", 1)[1]
        / "container",
        label="Dataset Version storage",
    )
    storage_uri = version["storage"]["uri"]
    storage_root = Path(storage_uri).expanduser()
    if (
        storage_uri != str(expected_storage)
        or storage_root.is_symlink()
        or storage_root.resolve() != expected_storage
    ):
        raise ValueError(
            "Dataset Version storage is outside its Engine-managed archive root."
        )
    return expected_storage


def verify_version_storage(release_root, version):
    """Verify one indexed Version descriptor, managed location, and sealed bytes."""
    version = validate_sealed_version_descriptor(version)
    storage_root = resolve_version_storage_root(release_root, version)
    verify_sealed_container(
        storage_root,
        version["manifest"],
        version["contentHash"],
    )
    return version


def _require_json(value, *, path="value"):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers.")
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_json(item, path=f"{path}[{index}]")
        return value
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} must use string object keys.")
            _require_json(item, path=f"{path}.{key}")
        return value
    raise ValueError(f"{path} contains a non-JSON value.")


def _safe_relative_path(value, *, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty relative path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a safe relative path.")
    return path


def container_files(root):
    root = Path(root)
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(
                f"Dataset containers may not contain symbolic links: {relative.as_posix()}"
            )
        if path.is_file():
            if len(relative.parts) == 1 and relative.name == MANIFEST_NAME:
                continue
            if len(relative.parts) == 1 and relative.name in RESERVED_ROOT_FILES:
                raise ValueError(
                    f"Dataset staging contains reserved root file: {relative.name}"
                )
            files.append((path, relative))
        elif not path.is_dir():
            raise ValueError(
                f"Dataset contains an unsupported filesystem entry: {relative.as_posix()}"
            )
    if not files:
        raise ValueError("Dataset container contains no data files.")
    return files


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def normalize_capabilities(capabilities):
    if not isinstance(capabilities, Mapping):
        raise ValueError("Dataset capabilities must be an object.")
    normalized = {}
    for name, capability in capabilities.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Dataset capabilities contain an invalid name.")
        if not isinstance(capability, Mapping) or set(capability) != CAPABILITY_FIELDS:
            raise ValueError(f"Dataset capability '{name}' has an invalid envelope.")
        protocol = capability["protocol"]
        descriptor = capability["descriptor"]
        if not isinstance(protocol, str) or not protocol:
            raise ValueError(f"Dataset capability '{name}' protocol is invalid.")
        if not isinstance(descriptor, Mapping):
            raise ValueError(f"Dataset capability '{name}' descriptor must be an object.")
        descriptor = dict(descriptor)
        _require_json(descriptor, path=f"Dataset capabilities.{name}.descriptor")
        if name == dataset_contracts.RECORDS_CAPABILITY:
            if (
                protocol != dataset_contracts.RECORDS_PROTOCOL
                or set(descriptor) != RECORDS_DESCRIPTOR_FIELDS
            ):
                raise ValueError("Dataset records capability contract is invalid.")
            _safe_relative_path(descriptor["path"], label="Dataset records capability path")
            count = descriptor["recordCount"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("Dataset records capability recordCount must be a non-negative integer.")
            for field in ("eventTimeField", "availableTimeField"):
                if not isinstance(descriptor[field], str) or not descriptor[field]:
                    raise ValueError(f"Dataset records capability {field} is invalid.")
            time_fields = {
                descriptor["eventTimeField"], descriptor["availableTimeField"]
            }
            if len(time_fields) != 2 or time_fields & {"sequence", "values"}:
                raise ValueError("Dataset records capability time fields must be distinct.")
            value_schema = descriptor["valueSchema"]
            if not isinstance(value_schema, Mapping) or set(value_schema) != RECORD_VALUE_SCHEMA_FIELDS:
                raise ValueError("Dataset records capability valueSchema has an invalid schema.")
            fields = value_schema["fields"]
            if not isinstance(fields, Mapping) or any(
                not isinstance(field, str) or not field for field in fields
            ):
                raise ValueError("Dataset records capability valueSchema.fields is invalid.")
            normalized_fields = {
                field: normalize_schema(schema) for field, schema in fields.items()
            }
            if normalized_fields != fields:
                raise ValueError("Dataset records capability field schemas are not normalized.")
            for list_field in ("entityKeys", "sortKeys"):
                values = value_schema[list_field]
                if (
                    not isinstance(values, list)
                    or any(not isinstance(value, str) or not value for value in values)
                    or len(values) != len(set(values))
                    or any(value not in fields for value in values)
                ):
                    raise ValueError(
                        f"Dataset records capability valueSchema.{list_field} is invalid."
                    )
        elif name == dataset_contracts.VISUALIZATION_CAPABILITY:
            if (
                protocol != dataset_contracts.VISUALIZATION_PROTOCOL
                or set(descriptor) != VISUALIZATION_DESCRIPTOR_FIELDS
            ):
                raise ValueError("Dataset visualization capability contract is invalid.")
            time_zone = descriptor["timeZone"]
            if not isinstance(time_zone, str) or not time_zone.strip():
                raise ValueError(
                    "Dataset visualization capability timeZone must be a non-empty IANA time zone."
                )
            try:
                ZoneInfo(time_zone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(
                    "Dataset visualization capability timeZone must be a valid IANA time zone."
                ) from exc
        normalized[name] = {"protocol": protocol, "descriptor": descriptor}
    return normalized


def visualization_time_zone(capabilities) -> str:
    """Return the declared display timezone for one verified Dataset version."""

    normalized = normalize_capabilities(capabilities)
    capability = normalized.get(dataset_contracts.VISUALIZATION_CAPABILITY)
    if capability is None:
        raise ValueError(
            "Dataset Version must declare the visualization capability before it can be used "
            "to create a default Result visualization."
        )
    return capability["descriptor"]["timeZone"]


def content_hash(files, capabilities):
    files = list(files)
    capabilities = normalize_capabilities(capabilities)
    identity = {
        "schemaVersion": 2,
        "files": file_manifest(files),
        "capabilities": capabilities,
    }
    return "sha256:" + hashlib.sha256(
        version_archive.canonical_json(identity)
    ).hexdigest()


def file_manifest(files):
    return [
        {
            "path": relative.as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path, relative in files
    ]


def normalize_lineage(lineage):
    if not isinstance(lineage, list):
        raise ValueError("Dataset lineage must be an array.")
    normalized = []
    aliases = set()
    for index, item in enumerate(lineage):
        if not isinstance(item, Mapping) or set(item) != LINEAGE_FIELDS:
            raise ValueError(f"Dataset lineage[{index}] has an invalid schema.")
        item = dict(item)
        for field in LINEAGE_FIELDS:
            if not isinstance(item[field], str) or not item[field]:
                raise ValueError(f"Dataset lineage[{index}].{field} is invalid.")
        if not digest_contracts.is_sha256_digest(item["contentHash"]):
            raise ValueError(f"Dataset lineage[{index}].contentHash is invalid.")
        if item["alias"] in aliases:
            raise ValueError("Dataset lineage aliases must be unique.")
        aliases.add(item["alias"])
        normalized.append(item)
    return sorted(normalized, key=lambda item: item["alias"])


def build_manifest(
    root,
    *,
    dataset_id,
    dataset,
    version_id="",
    source,
    created_at,
    storage_uri,
    capabilities,
    lineage=None,
    build=None,
):
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("Dataset ID must be a non-empty string.")
    if resource_ids.normalize_resource_id(dataset_id) != dataset_id:
        raise ValueError("Dataset ID must already be normalized.")
    if not isinstance(dataset, Mapping) or set(dataset) != {
        "datasetId", "name", "source", "metadata",
    }:
        raise ValueError("Dataset publication descriptor has an invalid schema.")
    dataset = dict(dataset)
    if dataset["datasetId"] != dataset_id:
        raise ValueError("Dataset publication descriptor identity is inconsistent.")
    dataset_source = dataset["source"]
    if (
        not isinstance(dataset_source, Mapping)
        or set(dataset_source) != {"type", "details"}
        or not isinstance(dataset_source["type"], str)
        or not dataset_source["type"]
        or not isinstance(dataset_source["details"], Mapping)
    ):
        raise ValueError("Dataset publication source descriptor is invalid.")
    if not isinstance(dataset["metadata"], Mapping):
        raise ValueError("Dataset publication metadata must be an object.")
    dataset["source"] = {
        "type": dataset_source["type"],
        "details": dict(dataset_source["details"]),
    }
    dataset["metadata"] = dict(dataset["metadata"])
    dataset.pop("name")
    _require_json(dataset["source"]["details"], path="Dataset publication source.details")
    _require_json(dataset["metadata"], path="Dataset publication metadata")
    if not isinstance(version_id, str):
        raise ValueError("Dataset Version ID must be a string.")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("Dataset createdAt must be a non-empty string.")
    if not isinstance(storage_uri, (str, Path)):
        raise ValueError("Dataset storage URI must be a path string or Path.")
    files = container_files(root)
    capabilities = normalize_capabilities(capabilities)
    digest = content_hash(files, capabilities)
    if not isinstance(source, Mapping) or set(source) != {"type", "details"}:
        raise ValueError("Dataset source descriptor must contain exactly type and details.")
    source = dict(source)
    if not isinstance(source["type"], str) or not source["type"]:
        raise ValueError("Dataset source type is invalid.")
    if not isinstance(source["details"], Mapping):
        raise ValueError("Dataset source details must be an object.")
    source["details"] = dict(source["details"])
    _require_json(source["details"], path="Dataset source.details")
    lineage = normalize_lineage([] if lineage is None else lineage)
    if build is not None and not isinstance(build, Mapping):
        raise ValueError("Dataset build descriptor must be an object or null.")
    build = dict(build) if build is not None else None
    _require_json(build, path="Dataset build")
    manifest = {
        "schemaVersion": 4,
        "datasetId": dataset_id,
        "datasetVersionId": version_id or f"{dataset_id}@{digest}",
        "contentHash": digest,
        "status": "sealed",
        "dataset": dataset,
        "storage": {"type": "directory", "uri": str(storage_uri)},
        "source": source,
        "capabilities": capabilities,
        "files": file_manifest(files),
        "createdAt": created_at,
        "lineage": lineage,
        "build": build,
    }
    manifest["manifestDigest"] = version_archive.content_digest(manifest)
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise ValueError("Dataset archive manifest has an invalid schema.")
    if manifest["schemaVersion"] != 4 or manifest["status"] != "sealed":
        raise ValueError("Dataset archive manifest schemaVersion/status is invalid.")
    for field in ("datasetId", "datasetVersionId", "createdAt"):
        if not isinstance(manifest[field], str) or not manifest[field]:
            raise ValueError(f"Dataset archive manifest field '{field}' is invalid.")
    if resource_ids.normalize_resource_id(manifest["datasetId"]) != manifest["datasetId"]:
        raise ValueError("Dataset archive Dataset ID must already be normalized.")
    try:
        created_at = datetime.fromisoformat(manifest["createdAt"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Dataset archive createdAt is invalid.") from exc
    if created_at.tzinfo is None or created_at.utcoffset() != timezone.utc.utcoffset(created_at):
        raise ValueError("Dataset archive createdAt must be an absolute UTC timestamp.")
    for field in ("contentHash", "manifestDigest"):
        if not digest_contracts.is_sha256_digest(manifest[field]):
            raise ValueError(f"Dataset archive manifest field '{field}' is invalid.")
    if manifest["datasetVersionId"] != f"{manifest['datasetId']}@{manifest['contentHash']}":
        raise ValueError("Dataset archive Version ID is not content-addressed by its Dataset.")
    dataset = manifest["dataset"]
    if not isinstance(dataset, dict) or set(dataset) != DATASET_DESCRIPTOR_FIELDS:
        raise ValueError("Dataset archive publication descriptor has an invalid schema.")
    if dataset["datasetId"] != manifest["datasetId"]:
        raise ValueError("Dataset archive publication descriptor identity is inconsistent.")
    dataset_source = dataset["source"]
    if (
        not isinstance(dataset_source, dict)
        or set(dataset_source) != {"type", "details"}
        or not isinstance(dataset_source["type"], str)
        or not dataset_source["type"]
        or not isinstance(dataset_source["details"], dict)
        or not isinstance(dataset["metadata"], dict)
    ):
        raise ValueError("Dataset archive publication descriptor is invalid.")
    _require_json(dataset_source["details"], path="Dataset publication source.details")
    _require_json(dataset["metadata"], path="Dataset publication metadata")
    expected_digest = version_archive.content_digest({
        key: value for key, value in manifest.items() if key != "manifestDigest"
    })
    if manifest["manifestDigest"] != expected_digest:
        raise ValueError("Dataset archive manifest digest is invalid.")
    storage = manifest["storage"]
    if (
        not isinstance(storage, dict)
        or set(storage) != {"type", "uri"}
        or storage["type"] != "directory"
        or not isinstance(storage["uri"], str)
        or not storage["uri"]
        or not Path(storage["uri"]).is_absolute()
    ):
        raise ValueError("Dataset archive storage descriptor is invalid.")
    source = manifest["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"type", "details"}
        or not isinstance(source["type"], str)
        or not source["type"]
        or not isinstance(source["details"], dict)
    ):
        raise ValueError("Dataset archive source descriptor is invalid.")
    _require_json(source["details"], path="Dataset source.details")
    capabilities = normalize_capabilities(manifest["capabilities"])
    if capabilities != manifest["capabilities"]:
        raise ValueError("Dataset capabilities are not normalized.")
    lineage = normalize_lineage(manifest["lineage"])
    if lineage != manifest["lineage"]:
        raise ValueError("Dataset lineage is not normalized.")
    if manifest["build"] is not None and not isinstance(manifest["build"], dict):
        raise ValueError("Dataset build descriptor must be an object or null.")
    _require_json(manifest["build"], path="Dataset build")
    _manifest_job_ids(manifest)
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("Dataset archive file manifest is invalid.")
    paths = []
    for item in files:
        if not isinstance(item, dict) or set(item) != FILE_FIELDS:
            raise ValueError("Dataset archive file manifest entry is invalid.")
        _safe_relative_path(item["path"], label="Dataset archive file path")
        if item["path"] in RESERVED_ROOT_FILES:
            raise ValueError("Dataset archive file manifest contains a reserved root file.")
        if isinstance(item["size"], bool) or not isinstance(item["size"], int) or item["size"] < 0:
            raise ValueError("Dataset archive file size is invalid.")
        if not digest_contracts.is_sha256_digest(item["sha256"]):
            raise ValueError("Dataset archive file digest is invalid.")
        paths.append(item["path"])
    if paths != sorted(set(paths)):
        raise ValueError("Dataset archive file paths must be sorted and unique.")
    records = capabilities.get(dataset_contracts.RECORDS_CAPABILITY)
    if records and records["descriptor"]["path"] not in paths:
        raise ValueError("Dataset records capability path is not in the sealed file manifest.")
    return manifest


def write_manifest(root, manifest):
    validate_manifest(manifest)
    path = Path(root) / MANIFEST_NAME
    if path.exists():
        raise ValueError(f"Dataset staging already contains reserved file {MANIFEST_NAME}.")
    path.write_text(
        strict_json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _verify_records_capability(root, capability):
    descriptor = capability["descriptor"]
    path = Path(root) / Path(*PurePosixPath(descriptor["path"]).parts)
    value_schema = descriptor["valueSchema"]
    validate_values = compile_normalized_json_validator(
        {
            "type": "object",
            "properties": value_schema["fields"],
            "additionalProperties": False,
        },
        path="Dataset record values",
    )
    count = 0
    event_time_field = descriptor["eventTimeField"]
    available_time_field = descriptor["availableTimeField"]
    record_fields = {"sequence", event_time_field, available_time_field, "values"}
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise ValueError("Dataset records capability file cannot be opened.") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = strict_json.loads(line)
            except ValueError as exc:
                raise ValueError(
                    f"Dataset records capability line {line_number} is invalid JSON."
                ) from exc
            if not isinstance(record, dict) or set(record) != record_fields:
                raise ValueError(
                    f"Dataset records capability line {line_number} has an invalid schema."
                )
            if isinstance(record["sequence"], bool) or record["sequence"] != count:
                raise ValueError("Dataset record sequence must be contiguous from zero.")
            dataset_contracts.parse_record_instant(
                record[event_time_field],
                label=f"Dataset record line {line_number} {event_time_field}",
            )
            dataset_contracts.parse_record_instant(
                record[available_time_field],
                label=f"Dataset record line {line_number} {available_time_field}",
            )
            validate_values(record["values"])
            count += 1
    if count != descriptor["recordCount"]:
        raise ValueError("Dataset records capability count does not match its descriptor.")


_CAPABILITY_SEMANTIC_VERIFIERS = {
    dataset_contracts.RECORDS_CAPABILITY: _verify_records_capability}


def semantic_capabilities_to_verify(manifest, semantic_capabilities):
    """Resolve the content-level capability validators required by one consumer."""

    declared = frozenset(manifest["capabilities"])
    if semantic_capabilities is None:
        return declared & frozenset(_CAPABILITY_SEMANTIC_VERIFIERS)
    collection_types = (set, frozenset, tuple, list)
    if isinstance(semantic_capabilities, (str, bytes)) or not isinstance(
        semantic_capabilities, collection_types
    ) or any(not isinstance(item, str) or not item for item in semantic_capabilities):
        raise ValueError(
            "Dataset semantic capabilities must be a collection of non-empty strings."
        )
    requested = frozenset(semantic_capabilities)
    undeclared = sorted(requested - declared)
    if undeclared:
        raise ValueError("Dataset semantic verification requires undeclared "
                         "capability/capabilities: " + ", ".join(undeclared))
    unsupported = sorted(requested - frozenset(_CAPABILITY_SEMANTIC_VERIFIERS))
    if unsupported:
        raise ValueError("Dataset semantic verification has no registered verifier for "
                         "capability/capabilities: " + ", ".join(unsupported))
    return requested


def _verify_container(
    root,
    manifest,
    expected_content_hash,
    expected_storage_root,
    *,
    semantic_capabilities=None,
    allow_writable_root=False,
):
    root = Path(root).expanduser()
    expected_storage_root = Path(expected_storage_root).expanduser()
    if root.is_symlink() or expected_storage_root.is_symlink():
        raise ValueError("Dataset archive root may not be a symbolic link.")
    root = root.resolve()
    expected_storage_root = expected_storage_root.resolve()
    if not root.is_dir():
        raise ValueError(f"Dataset archive directory does not exist: {root}")
    validate_manifest(manifest)
    if manifest["storage"]["uri"] != str(expected_storage_root):
        raise ValueError("Dataset archive storage URI does not match its declared destination.")
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"Dataset archive is missing {MANIFEST_NAME}: {root}")
    stored = strict_json.loads(manifest_path.read_text(encoding="utf-8"))
    if stored != manifest:
        raise ValueError(f"Dataset archive manifest file does not match its index: {root}")
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            raise ValueError(f"Dataset archives may not contain symlinks: {path}")
        if path.stat().st_mode & 0o222 and not (
            allow_writable_root and path == root
        ):
            raise ValueError(f"Dataset archive is writable: {path}")
    actual_files = container_files(root)
    if file_manifest(actual_files) != manifest["files"]:
        raise ValueError(f"Dataset archive file verification failed: {root}")
    actual_hash = content_hash(actual_files, manifest["capabilities"])
    if expected_content_hash:
        if (
            not isinstance(expected_content_hash, str)
            or not digest_contracts.is_sha256_digest(expected_content_hash)
        ):
            raise ValueError("Expected Dataset content hash is invalid.")
        expected = expected_content_hash
    else:
        expected = manifest["contentHash"]
    if actual_hash != expected or manifest["contentHash"] != actual_hash:
        raise ValueError(f"Dataset archive content hash mismatch: {root}")
    semantic_capabilities = semantic_capabilities_to_verify(
        manifest,
        semantic_capabilities,
    )
    for capability_name in sorted(semantic_capabilities):
        verifier = _CAPABILITY_SEMANTIC_VERIFIERS[capability_name]
        verifier(root, manifest["capabilities"][capability_name])
    return manifest


def verify_staging_container(
    root,
    manifest,
    expected_destination,
    expected_content_hash="",
    *,
    allow_writable_root=False,
):
    return _verify_container(
        root,
        manifest,
        expected_content_hash,
        expected_destination,
        allow_writable_root=allow_writable_root,
    )


def verify_sealed_container(
    root,
    manifest,
    expected_content_hash="",
    *,
    semantic_capabilities=None,
):
    root = Path(root).expanduser()
    if root.is_symlink():
        raise ValueError("Dataset archive root may not be a symbolic link.")
    return _verify_container(
        root,
        manifest,
        expected_content_hash,
        root,
        semantic_capabilities=semantic_capabilities,
    )


def seal_staging_container(root, manifest, expected_destination, expected_content_hash=""):
    """Finalize and verify Dataset bytes before entering the index transaction."""

    write_manifest(root, manifest)
    # Keep the staging root owner-writable until publication.  Some supported
    # filesystems reject moving a read-only directory across parent
    # directories, even though POSIX rename permissions are normally decided
    # by the parents.  The bytes and every descendant directory are already
    # immutable here; the root is finalized immediately after the rename.
    make_tree_read_only(root, writable_root=True)
    return verify_staging_container(
        root,
        manifest,
        expected_destination,
        expected_content_hash,
        allow_writable_root=True,
    )


def publish_sealed_container(
    staging,
    destination,
    manifest,
    *,
    managed_root,
    expected_content_hash="",
):
    """Durably publish a prepared Dataset container and verify its final identity."""

    destination = version_archive.publish_staging_directory(
        staging,
        destination,
        managed_root=managed_root,
    )
    make_tree_read_only(destination)
    version_archive.fsync_tree(destination)
    return verify_sealed_container(destination, manifest, expected_content_hash)


def make_tree_read_only(root, *, writable_root=False):
    root = Path(root)
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise ValueError(f"Dataset archives may not contain symlinks: {path}")
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o755 if writable_root else 0o555)
