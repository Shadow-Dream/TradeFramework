"""Validate, bundle, and publish immutable Module Definitions."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from engine.archive import version as version_archive
from engine.archive import version_transaction
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.json_schema import normalize_module_config_schema
from engine.contracts.module import (
    MODULE_DEFINITION_FIELDS,
    MODULE_DRAFT_FIELDS,
    definition_key,
    normalize_module_parameters,
    normalize_ports,
    require_exact_fields,
    validate_module_definition,
)
from engine.repository import control_state
from engine.repository import module_definitions


def require_safe_relative_bundle_path(path):
    if not isinstance(path, str) or not path or path != path.strip():
        raise ValueError("Bundle file path must be a canonical non-empty string.")
    if "\\" in path or "\x00" in path:
        raise ValueError(f"Invalid bundle file path: {path}")
    if "{{moduleRoot}}" in path:
        raise ValueError("Bundle file path may not contain the reserved Module root token.")
    candidate = Path(path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != path
    ):
        raise ValueError(f"Invalid bundle file path: {path}")
    return candidate


def decode_bundle_files(files):
    if not isinstance(files, list) or not files:
        raise ValueError("Module Draft files must be a non-empty array.")
    decoded = []
    paths = set()
    for index, item in enumerate(files):
        require_exact_fields(
            item,
            allowed={"path", "contentBase64", "sha256", "executable"},
            required={"path", "contentBase64", "executable"},
            label=f"Module Draft files[{index}]",
        )
        relative = require_safe_relative_bundle_path(item["path"])
        relative_text = relative.as_posix()
        if relative_text == "module.json":
            raise ValueError(
                "Module bundle may not define the Engine-owned root module.json."
            )
        if relative_text in paths:
            raise ValueError(
                f"Module bundle contains duplicate file path: {relative_text}"
            )
        paths.add(relative_text)
        if not isinstance(item["contentBase64"], str):
            raise ValueError(
                f"Bundle file '{relative}' contentBase64 must be a string."
            )
        if not isinstance(item["executable"], bool):
            raise ValueError(f"Bundle file '{relative}' executable must be a boolean.")
        try:
            data = base64.b64decode(item["contentBase64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Bundle file '{relative}' contentBase64 is invalid."
            ) from exc
        expected_sha256 = item.get("sha256")
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or expected_sha256.lower() != actual_sha256
        ):
            raise ValueError(
                f"Bundle file '{relative}' sha256 mismatch: expected "
                f"{expected_sha256}, got {actual_sha256}."
            )
        decoded.append((relative, data, item["executable"]))
    return decoded


def _write_bundle_files(release_dir, files):
    written = []
    for relative, data, executable in files:
        target = release_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        actual_sha256 = hashlib.sha256(data).hexdigest()
        target.write_bytes(data)
        if executable:
            target.chmod(target.stat().st_mode | 0o111)
        written.append({
            "path": str(relative),
            "sizeBytes": len(data),
            "sha256": actual_sha256,
            "executable": executable,
        })
    return written


def _substitute_module_root(value, module_dir):
    if isinstance(value, str):
        return value.replace("{{moduleRoot}}", str(module_dir))
    if isinstance(value, list):
        return [_substitute_module_root(item, module_dir) for item in value]
    if isinstance(value, dict):
        return {
            key: _substitute_module_root(item, module_dir)
            for key, item in value.items()
        }
    return value


def _validate_process_runner_bundle_parameters(parameters, decoded_files):
    token = "{{moduleRoot}}"
    file_modes = {
        relative.as_posix(): executable
        for relative, _data, executable in decoded_files
    }
    directories = {""}
    for relative in file_modes:
        parts = Path(relative).parts
        directories.update(
            Path(*parts[:index]).as_posix() for index in range(1, len(parts))
        )

    def relative_bundle_path(value, field, *, allow_root=False):
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"ProcessRunner parameters.{field} must be a Module bundle path."
            )
        if value.count(token) != 1:
            raise ValueError(
                f"ProcessRunner parameters.{field} must contain exactly one "
                "Module root token."
            )
        if value == token and allow_root:
            return ""
        prefix = token + "/"
        if not value.startswith(prefix):
            raise ValueError(
                f"ProcessRunner parameters.{field} must be rooted at {token}."
            )
        suffix = value[len(prefix):]
        candidate = Path(suffix)
        if (
            not suffix
            or candidate.is_absolute()
            or ".." in candidate.parts
            or "\\" in suffix
            or candidate.as_posix() != suffix
        ):
            raise ValueError(
                f"ProcessRunner parameters.{field} has a non-canonical bundle path."
            )
        return candidate.as_posix()

    command = relative_bundle_path(parameters["command"], "command")
    if command not in file_modes:
        raise ValueError(
            "ProcessRunner parameters.command is not present in Module Draft files."
        )
    if not file_modes[command]:
        raise ValueError(
            "ProcessRunner parameters.command must identify an executable bundle file."
        )

    working_directory = parameters.get("workingDirectory")
    if working_directory is not None:
        relative = relative_bundle_path(
            working_directory, "workingDirectory", allow_root=True
        )
        if relative not in directories:
            raise ValueError(
                "ProcessRunner parameters.workingDirectory is not a bundle directory."
            )

    for index, argument in enumerate(parameters["arguments"]):
        if not isinstance(argument, str):
            continue
        candidate = argument.split("=", 1)[-1] if "=" in argument else argument
        if token in candidate:
            relative = relative_bundle_path(
                candidate, f"arguments[{index}]", allow_root=True
            )
            if relative not in file_modes and relative not in directories:
                raise ValueError(
                    f"ProcessRunner parameters.arguments[{index}] is not present "
                    "in the Module bundle."
                )
            continue
        if token in argument:
            raise ValueError(
                f"ProcessRunner parameters.arguments[{index}] has an invalid "
                "Module root reference."
            )
        path = Path(candidate)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"ProcessRunner parameters.arguments[{index}] may not reference "
                "paths outside the Module bundle."
            )


def _publish_module_locked(
    config,
    request,
    *,
    repository=None,
    engine_owned=False,
):
    require_exact_fields(
        request,
        allowed=MODULE_DRAFT_FIELDS,
        required={
            "kind", "moduleId", "name", "activationMode", "parameters",
            "configSchema", "ports", "description", "files",
        },
        label="Module Draft",
    )
    kind = request["kind"]
    module_id = request["moduleId"]
    if (
        not isinstance(kind, str)
        or not kind
        or not isinstance(module_id, str)
        or not module_id
    ):
        raise ValueError("kind and moduleId must be non-empty strings.")
    require_resource_path_segment(kind, label="Module kind")
    require_resource_path_segment(module_id, label="moduleId")
    if not isinstance(engine_owned, bool):
        raise ValueError("Module engine_owned must be a boolean.")
    if not isinstance(request["name"], str) or not request["name"]:
        raise ValueError("Module name must be a non-empty string.")
    if not isinstance(request["description"], str):
        raise ValueError("Module description must be a string.")
    decoded_files = decode_bundle_files(request["files"])
    activation = request["activationMode"]
    normalized_parameters = normalize_module_parameters(
        request["parameters"],
        activation_mode=activation,
        label=f"Module '{module_id}' parameters",
    )
    if activation == "ProcessRunner":
        _validate_process_runner_bundle_parameters(
            normalized_parameters, decoded_files
        )
    normalized_config_schema = normalize_module_config_schema(
        request["configSchema"]
    )
    normalized_ports = normalize_ports(
        request["ports"], label=f"Module '{kind}/{module_id}' ports"
    )
    bundle_paths = {
        relative.as_posix() for relative, _data, _executable in decoded_files
    }
    if activation == "PythonModule":
        if "module.py" not in bundle_paths:
            raise ValueError(
                "PythonModule bundle must contain module.py exporting MODULE_CLASS."
            )
        native_suffixes = {".so", ".dll", ".dylib", ".pyd"}
        native_files = sorted(
            path
            for path in bundle_paths
            if Path(path).suffix.casefold() in native_suffixes
        )
        if native_files:
            raise ValueError(
                "PythonModule bundle may not contain native libraries: "
                + ", ".join(native_files)
            )
        bundled_sdk = sorted(
            path
            for path in bundle_paths
            if path == "strategy_devkit" or path.startswith("strategy_devkit/")
        )
        if bundled_sdk:
            raise ValueError(
                "PythonModule must use the Engine-frozen strategy_devkit and may "
                "not bundle it."
            )

    actual_repository = module_definitions.module_repository_for_kind(kind)
    if repository is not None and repository != actual_repository:
        raise ValueError(
            f"Module kind '{kind}' belongs to the {actual_repository} repository, "
            f"not {repository}."
        )
    repository_spec = module_definitions.MODULE_REPOSITORIES[actual_repository]
    definitions, repository_evidence = (
        module_definitions.load_repository_evidence(
            config, actual_repository
        )
    )
    records = [
        definition
        for definition in definitions.values()
        if definition["kind"] == kind
    ]
    identity_records = [
        record for record in records if record["moduleId"] == module_id
    ]
    if identity_records and identity_records[0]["builtin"] != engine_owned:
        raise ValueError(
            f"Module identity ownership cannot change: {kind}/{module_id}"
        )

    def destination_for_version(version):
        return module_definitions.module_version_dir(
            config, kind, module_id, version
        )

    def prepare_staging(staging, version, module_dir):
        definition = {
            "kind": kind,
            "moduleId": module_id,
            "name": request["name"],
            "activationMode": activation,
            "parameters": _substitute_module_root(
                normalized_parameters, module_dir
            ),
            "configSchema": normalized_config_schema,
            "ports": normalized_ports,
            "description": request["description"],
            "builtin": engine_owned,
            "version": version,
        }
        validate_module_definition(definition)
        written_files = _write_bundle_files(staging, decoded_files)
        semantic_content = {
            "kind": kind,
            "moduleId": module_id,
            "name": definition["name"],
            "activationMode": definition["activationMode"],
            "parameters": normalized_parameters,
            "configSchema": definition["configSchema"],
            "ports": definition["ports"],
            "description": definition["description"],
            "builtin": definition["builtin"],
            "files": version_archive.file_manifest(staging),
        }
        return semantic_content, {
            "definition": definition,
            "writtenFiles": written_files,
        }

    def create_record(_version, context):
        return context["definition"]

    def write_record(staging, definition, _context):
        control_state.atomic_write_json(staging / "module.json", definition)

    def commit_record(definition, _context):
        key = definition_key(kind, module_id, definition["version"])
        definitions[key] = definition
        control_state.save_state(
            config, repository_spec["stateFile"], definitions
        )

    def read_committed_record(definition, _context):
        key = definition_key(kind, module_id, definition["version"])
        return control_state.load_state(
            config, repository_spec["stateFile"], {}
        ).get(key)

    result = version_transaction.archive_if_changed(
        records=identity_records,
        identity_key="moduleId",
        identity=module_id,
        resource_type="module",
        resource_id=f"{kind}/{module_id}",
        managed_root=config["releaseRoot"],
        destination_for_version=destination_for_version,
        prepare_staging=prepare_staging,
        create_record=create_record,
        record_fields=MODULE_DEFINITION_FIELDS,
        write_record=write_record,
        commit_record=commit_record,
        read_committed_record=read_committed_record,
        immutable_fields={"kind", "builtin"},
        verified_records_evidence=repository_evidence,
    )
    definition = result["record"]
    key = definition_key(kind, module_id, definition["version"])
    module_dir = Path(definition["archive"]["root"])
    written_files = result["context"]["writtenFiles"]
    response = {
        "accepted": True,
        "moduleKey": key,
        "moduleDir": str(module_dir),
        "definition": definition,
        "repository": actual_repository,
    }
    if result["unchanged"]:
        return {**response, "unchanged": True}
    control_state.append_history_event(
        config,
        f"{actual_repository}-module.added",
        {
            "repository": actual_repository,
            "moduleKey": key,
            "moduleDir": str(module_dir),
            "definition": definition,
            "filePaths": [item["path"] for item in written_files],
        },
    )
    return response


def publish_module(config, request, *, repository=None, engine_owned=False):
    """Publish one Module under the repository's complete control transaction."""
    with control_state.control_state_lock(config):
        return _publish_module_locked(
            config,
            request,
            repository=repository,
            engine_owned=engine_owned,
        )


__all__ = (
    "decode_bundle_files",
    "publish_module",
    "require_safe_relative_bundle_path",
)
