#!/usr/bin/env python3
"""Safe ZIP import and export for immutable directory-backed Datasets."""

import copy
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from engine.control import database as engine_database
from engine.core import resource_ids
from engine.archive import dataset as dataset_archive
from engine.contracts import strict_json
from engine.repository import dataset_publication
from engine.repository import dataset_staging
from engine.repository import datasets


MAX_ARCHIVE_ENTRIES = 100_000
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024 * 1024
RESERVED_ROOT_FILES = dataset_archive.RESERVED_ROOT_FILES


def _archive_member_path(name):
    if not name or "\x00" in name or "\\" in name:
        raise ValueError(f"ZIP contains an unsafe path: {name!r}")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"ZIP contains an unsafe path: {name!r}")
    return Path(*relative.parts)


def _validate_archive(infos):
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ValueError(f"ZIP contains more than {MAX_ARCHIVE_ENTRIES} entries.")
    total_size = 0
    file_count = 0
    for info in infos:
        relative = _archive_member_path(info.filename)
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ValueError(f"ZIP may contain only regular files and directories: {relative.as_posix()}")
        if info.flag_bits & 0x1:
            raise ValueError(f"Encrypted ZIP entries are not supported: {relative.as_posix()}")
        if not info.is_dir():
            total_size += info.file_size
            file_count += 1
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("ZIP uncompressed size exceeds 10 GiB.")
    if not file_count:
        raise ValueError("Uploaded ZIP contains no files.")


def _extract_archive(archive_path, destination):
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise ValueError("Upload must be a valid ZIP archive.") from exc
    with archive:
        infos = archive.infolist()
        _validate_archive(infos)
        capabilities = None
        declarations = {
            info.filename: info
            for info in infos
            if info.filename in {
                dataset_archive.MANIFEST_NAME,
                dataset_archive.CAPABILITIES_DECLARATION_NAME,
            }
        }
        manifest_info = declarations.get(dataset_archive.MANIFEST_NAME)
        if manifest_info is not None:
            if manifest_info.file_size > 16 * 1024 * 1024:
                raise ValueError("Dataset manifest exceeds 16 MiB.")
            with archive.open(manifest_info, "r") as handle:
                manifest = strict_json.loads(handle.read().decode("utf-8"))
            dataset_archive.validate_manifest(manifest)
            capabilities = copy.deepcopy(manifest["capabilities"])
        declaration_info = declarations.get(dataset_archive.CAPABILITIES_DECLARATION_NAME)
        if declaration_info is not None:
            if declaration_info.file_size > 16 * 1024 * 1024:
                raise ValueError("Dataset capability declaration exceeds 16 MiB.")
            with archive.open(declaration_info, "r") as handle:
                declaration = strict_json.loads(handle.read().decode("utf-8"))
            if not isinstance(declaration, dict) or set(declaration) != {
                "schemaVersion", "capabilities",
            } or declaration["schemaVersion"] != 1:
                raise ValueError("Dataset capability declaration has an invalid schema.")
            declared_capabilities = dataset_archive.normalize_capabilities(
                declaration["capabilities"]
            )
            if capabilities is not None and capabilities != declared_capabilities:
                raise ValueError(
                    "Dataset manifest and capability declaration do not match."
                )
            capabilities = declared_capabilities
        if destination.exists():
            if not destination.is_dir() or any(destination.iterdir()):
                raise ValueError("Dataset ZIP staging directory must be empty.")
        else:
            destination.mkdir(parents=True, exist_ok=False)
        for info in infos:
            relative = _archive_member_path(info.filename)
            if len(relative.parts) == 1 and relative.name in RESERVED_ROOT_FILES:
                continue
            target = destination / relative
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        return {} if capabilities is None else capabilities


def import_dataset_zip(config, dataset_id, archive_bytes, *, name="", filename=""):
    if not isinstance(archive_bytes, (bytes, bytearray)) or not archive_bytes:
        raise ValueError("Upload requires a non-empty ZIP archive.")
    with tempfile.NamedTemporaryFile(prefix="trade-dataset-upload-", suffix=".zip", delete=False) as handle:
        archive_path = Path(handle.name)
        handle.write(bytes(archive_bytes))
    try:
        return import_dataset_zip_path(
            config, dataset_id, archive_path, name=name, filename=filename
        )
    finally:
        archive_path.unlink(missing_ok=True)


def import_dataset_zip_stream(config, dataset_id, stream, content_length, *, name="", filename=""):
    try:
        remaining = int(content_length)
    except (TypeError, ValueError) as exc:
        raise ValueError("Dataset ZIP upload requires Content-Length.") from exc
    if remaining <= 0:
        raise ValueError("Upload requires a non-empty ZIP archive.")
    if remaining > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP archive size exceeds 10 GiB.")
    with tempfile.NamedTemporaryFile(prefix="trade-dataset-upload-", suffix=".zip", delete=False) as handle:
        archive_path = Path(handle.name)
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                archive_path.unlink(missing_ok=True)
                raise ValueError("Dataset ZIP upload ended before Content-Length bytes were received.")
            handle.write(chunk)
            remaining -= len(chunk)
    try:
        return import_dataset_zip_path(
            config, dataset_id, archive_path, name=name, filename=filename
        )
    finally:
        archive_path.unlink(missing_ok=True)


def replace_dataset_zip_stream(config, dataset_id, stream, content_length, *, name="", filename=""):
    try:
        remaining = int(content_length)
    except (TypeError, ValueError) as exc:
        raise ValueError("Dataset ZIP replacement requires Content-Length.") from exc
    if remaining <= 0:
        raise ValueError("Replacement requires a non-empty ZIP archive.")
    if remaining > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP archive size exceeds 10 GiB.")
    with tempfile.NamedTemporaryFile(prefix="trade-dataset-replace-", suffix=".zip", delete=False) as handle:
        archive_path = Path(handle.name)
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                archive_path.unlink(missing_ok=True)
                raise ValueError("Dataset ZIP replacement ended before Content-Length bytes were received.")
            handle.write(chunk)
            remaining -= len(chunk)
    try:
        return import_dataset_zip_path(
            config, dataset_id, archive_path, name=name, filename=filename, replace=True
        )
    finally:
        archive_path.unlink(missing_ok=True)


def import_dataset_zip_path(config, dataset_id, archive_path, *, name="", filename="", replace=False):
    archive_path = Path(archive_path)
    if not archive_path.is_file() or archive_path.stat().st_size <= 0:
        raise ValueError("Upload requires a non-empty ZIP archive.")
    if not str(dataset_id or "").strip():
        if replace:
            raise ValueError("Dataset ID is required for replacement.")
        dataset_id = resource_ids.new_resource_id("dataset")
    else:
        dataset_id = resource_ids.normalize_resource_id(dataset_id)
    with engine_database.connect_database(config) as conn:
        exists = conn.execute(
            "SELECT 1 FROM datasets WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
    existing = datasets.get_dataset(config, dataset_id) if exists else None
    if replace:
        if not existing:
            raise ValueError(f"Unknown Dataset: {dataset_id}")
        if existing["status"] != "active":
            raise ValueError(f"Archived Dataset '{dataset_id}' cannot be replaced.")
    elif existing:
        raise ValueError(f"Dataset '{dataset_id}' already exists. Use a new Dataset ID.")

    staging = dataset_staging.create_dataset_staging(config, dataset_id)
    try:
        capabilities = _extract_archive(archive_path, staging.path)
        if replace:
            dataset_descriptor = {
                field: existing[field] for field in ("datasetId", "name", "source", "metadata")
            }
        else:
            dataset_descriptor = {
                "datasetId": dataset_id,
                "name": str(name or dataset_id),
                "source": {"type": "zip-upload", "details": {}},
                "metadata": {"kind": "uploaded-container"},
            }
        dataset = dataset_publication.publish_dataset_version(
            config,
            dataset=dataset_descriptor,
            staging=staging,
            capabilities=capabilities,
            version_source={
                "type": "zip-upload",
                "details": {
                    "filename": str(filename or ""),
                    "archiveSha256": dataset_archive.sha256_file(archive_path),
                },
            },
            append=replace,
        )
        if replace and str(name or "").strip() and name.strip() != dataset["name"]:
            dataset = datasets.rename_dataset(config, dataset_id, name)
        return dataset
    finally:
        dataset_staging.discard_dataset_staging(staging)


def _version_storage_root(config, version):
    raw = version["storage"]["uri"]
    path = Path(raw).expanduser().resolve()
    if path.is_file():
        path = path.parent
    allowed_root = dataset_archive.repository_root(config["releaseRoot"]).resolve()
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("Dataset storage is outside the managed Dataset directory.") from exc
    if not path.is_dir():
        raise ValueError(f"Dataset version storage does not exist: {path}")
    return path


def _latest_version(config, dataset_id):
    versions = datasets.list_dataset_versions(config, dataset_id)
    if not versions:
        raise ValueError(f"Dataset '{dataset_id}' has no sealed Dataset Version.")
    version = versions[0]
    if version.get("status") != "sealed":
        raise ValueError(f"Dataset version is not sealed: {version.get('datasetVersionId')}")
    if version["storage"]["type"] != "directory":
        raise ValueError(
            f"Dataset version storage type must be 'directory': {version['datasetVersionId']}"
        )
    return datasets.verify_dataset_version(config, version)


def _write_dataset_to_zip(archive, config, dataset_id, prefix=""):
    dataset = datasets.get_dataset(config, dataset_id)
    version = _latest_version(config, dataset_id)
    root = _version_storage_root(config, version)
    wrote_manifest = False
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"Dataset contains a symbolic link and cannot be exported: {relative.as_posix()}")
        if path.is_file():
            archive_name = f"{prefix}{relative.as_posix()}"
            archive.write(path, archive_name)
            wrote_manifest = wrote_manifest or relative.as_posix() == "_dataset.json"
        elif not path.is_dir():
            raise ValueError(f"Dataset contains an unsupported entry: {relative.as_posix()}")
    if not wrote_manifest:
        raise ValueError(
            f"Dataset version is missing its mandatory _dataset.json manifest: "
            f"{version['datasetVersionId']}"
        )
    return {"datasetId": dataset["datasetId"], "datasetVersionId": version["datasetVersionId"]}


def build_dataset_archive(config, dataset_ids):
    if not isinstance(dataset_ids, list):
        raise ValueError("Dataset download selection must be an array.")
    requested = []
    seen = set()
    for value in dataset_ids:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Dataset download IDs must be non-empty strings.")
        dataset_id = value.strip()
        if dataset_id in seen:
            raise ValueError(f"Dataset download contains duplicate ID: {dataset_id}")
        requested.append(dataset_id)
        seen.add(dataset_id)
    if not requested:
        raise ValueError("Select at least one Dataset to download.")
    handle = tempfile.NamedTemporaryFile(prefix="trade-datasets-", suffix=".zip", delete=False)
    archive_path = Path(handle.name)
    handle.close()
    try:
        exported = []
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for dataset_id in requested:
                prefix = (
                    ""
                    if len(requested) == 1
                    else f"{resource_ids.normalize_resource_id(dataset_id)}/"
                )
                exported.append(_write_dataset_to_zip(archive, config, dataset_id, prefix))
            if len(requested) > 1:
                archive.writestr(
                    "_trade_dataset_export.json",
                    strict_json.dumps(
                        {"schemaVersion": 1, "datasets": exported},
                        indent=2,
                        sort_keys=True,
                    ),
                )
        filename = (
            f"{resource_ids.normalize_resource_id(requested[0])}.zip"
            if len(requested) == 1
            else "trade-datasets.zip"
        )
        return archive_path, filename
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
