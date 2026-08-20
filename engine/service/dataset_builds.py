#!/usr/bin/env python3
"""Dataset Build submission, publication, recovery, and Process orchestration."""

from __future__ import annotations

import os
import logging
import secrets
import shutil
import stat
import sys
from pathlib import Path

from engine.archive import dataset as dataset_archive
from engine.contracts import strict_json
from engine.contracts.dataset_workspace import (
    BUILD_COMPLETION_EVIDENCE_NAME,
    BUILD_REQUEST_FIELDS,
    ENGINE_SCRIPT_NAME,
    ENGINE_WORKSPACE_DIRECTORY,
    MAX_EXECUTION_WORKSPACE_BYTES,
    MIN_HOST_FREE_BYTES,
    PROCESS_REQUEST_FIELDS,
    require_request_fields,
    script_digest,
)
from engine.control import database as engine_database
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.repository import dataset_build_jobs as build_jobs
from engine.repository import dataset_build_paths as build_paths
from engine.repository import dataset_publication
from engine.repository import dataset_recipes
from engine.repository import dataset_staging
from engine.repository import dataset_workspaces as workspace_repository
from engine.repository import datasets
from engine.runtime import dataset_build as build_runtime
from engine.service import dataset_workspaces as workspace_service

# Named dependencies remain module-local orchestration seams for deterministic
# failure injection; their implementations are owned by repository/runtime.
get_build_job = build_jobs.get_build_job
completed_build_evidence = build_jobs.completed_build_evidence
discard_execution_workspace = build_jobs.discard_execution_workspace
_mark_build_completed = build_jobs.mark_build_completed
_mark_build_failed = build_jobs.mark_build_failed
assert_output_dataset_available = build_jobs.assert_output_dataset_available
get_workspace = workspace_repository.get_workspace
create_workspace = workspace_repository.create_workspace
verify_workspace_bindings = workspace_repository.verify_workspace_bindings
materialize_source_links = workspace_repository.materialize_source_links
verify_source_links = workspace_repository.verify_source_links
resolve_archived_recipe = dataset_recipes.resolve_archived_recipe
execute_in_sandbox = build_runtime.execute_in_sandbox
execution_process_authority = build_runtime.execution_process_authority
enforce_workspace_safety = build_runtime.enforce_workspace_safety
delete_workspace = workspace_service.delete_workspace


LOGGER = logging.getLogger(__name__)


def _require_execution_workspace(
    config, job_id, execution_workspace, *, required=False
):
    """Bind a caller path to the Job's canonical, symlink-free path authority."""

    canonical = build_jobs.require_build_execution_workspace(
        config, job_id, required=required
    )
    supplied = Path(execution_workspace)
    if not supplied.is_absolute() or supplied != canonical:
        raise ValueError(
            "Dataset Build execution Workspace does not match its Job authority."
        )
    return canonical


def reconcile_build_job_directories(config):
    """Run repository scratch recovery before any Build lifecycle recovery."""

    build_jobs.reconcile_build_job_directories(config)


def _discard_execution_workspace(config, job_id, execution_workspace):
    if not discard_execution_workspace(config, job_id, execution_workspace):
        raise RuntimeError(
            f"Dataset Build scratch cleanup remains incomplete: {job_id}"
        )


def _discard_committed_execution_workspace(
    config, job_id, execution_workspace
):
    """Best-effort cleanup after immutable output and lifecycle commit.

    Once publication evidence and the terminal database state agree, scratch
    removal is observational.  A failed removal must leave the committed Build
    successful so startup reconciliation can retry it later.
    """

    try:
        _discard_execution_workspace(config, job_id, execution_workspace)
    except Exception:
        LOGGER.warning(
            "Dataset Build %s committed with scratch cleanup pending",
            job_id,
            exc_info=True,
        )


def output_files(execution_workspace, bindings):
    enforce_workspace_safety(execution_workspace)
    excluded = {binding["alias"] for binding in bindings} | {
        ".tmp", ENGINE_WORKSPACE_DIRECTORY,
    }
    files = []
    capabilities = {}
    for path in sorted(execution_workspace.rglob("*")):
        relative = path.relative_to(execution_workspace)
        if relative.parts and relative.parts[0] in excluded:
            continue
        if path.is_symlink():
            raise ValueError(f"Dataset output may not contain symlinks: {relative}")
        if path.is_file():
            if relative.as_posix() == dataset_archive.CAPABILITIES_DECLARATION_NAME:
                try:
                    declaration = strict_json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    raise ValueError("Dataset capability declaration is invalid JSON.") from exc
                if not isinstance(declaration, dict) or set(declaration) != {
                    "schemaVersion", "capabilities",
                } or declaration["schemaVersion"] != 1:
                    raise ValueError("Dataset capability declaration has an invalid schema.")
                capabilities = dataset_archive.normalize_capabilities(
                    declaration["capabilities"]
                )
            else:
                files.append((path, relative))
        elif not path.is_dir():
            raise ValueError(f"Dataset output contains unsupported filesystem entry: {relative}")
    if not files:
        raise ValueError("Submitted Dataset script produced no output files in its Workspace.")
    return files, capabilities


def _build_output_evidence(execution_workspace, bindings):
    files, capabilities = output_files(execution_workspace, bindings)
    outputs = []
    for source, relative in files:
        source_stat = source.stat(follow_symlinks=False)
        outputs.append({
            "path": relative.as_posix(),
            "size": source_stat.st_size,
            "digest": dataset_archive.sha256_file(source),
        })
    return outputs, capabilities


def write_build_completion_evidence(
    job, execution_workspace, arguments, stdout, stderr
):
    """Persist exact script-completion evidence before Dataset publication."""
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ValueError("Dataset Build completion logs must be strings.")
    outputs, capabilities = _build_output_evidence(
        execution_workspace, job["sources"]
    )
    payload = {
        "schemaVersion": 1,
        "jobId": job["jobId"],
        "scriptDigest": job["scriptDigest"],
        "arguments": list(arguments),
        "sourceBindings": job["sources"],
        "outputs": outputs,
        "capabilities": capabilities,
        "stdout": stdout,
        "stderr": stderr,
    }
    engine_directory = Path(execution_workspace) / ENGINE_WORKSPACE_DIRECTORY
    path = engine_directory / BUILD_COMPLETION_EVIDENCE_NAME
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(
            strict_json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(engine_directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return payload


def load_build_completion_evidence(job, execution_workspace):
    path = (
        Path(execution_workspace)
        / ENGINE_WORKSPACE_DIRECTORY
        / BUILD_COMPLETION_EVIDENCE_NAME
    )
    try:
        payload = strict_json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Dataset Build completion evidence is unreadable.") from exc
    require_request_fields(
        payload,
        allowed={
            "schemaVersion", "jobId", "scriptDigest", "arguments",
            "sourceBindings", "outputs", "capabilities", "stdout", "stderr",
        },
        required={
            "schemaVersion", "jobId", "scriptDigest", "arguments",
            "sourceBindings", "outputs", "capabilities", "stdout", "stderr",
        },
        label="Dataset Build completion evidence",
    )
    outputs, capabilities = _build_output_evidence(
        execution_workspace, job["sources"]
    )
    expected = {
        "schemaVersion": 1,
        "jobId": job["jobId"],
        "scriptDigest": job["scriptDigest"],
        "arguments": job["arguments"],
        "sourceBindings": job["sources"],
        "outputs": outputs,
        "capabilities": capabilities,
        "stdout": payload["stdout"],
        "stderr": payload["stderr"],
    }
    if not isinstance(payload["stdout"], str) or not isinstance(payload["stderr"], str):
        raise ValueError("Dataset Build completion evidence logs are invalid.")
    if payload != expected:
        raise ValueError(
            "Dataset Build completion evidence does not match its Job or outputs."
        )
    return payload


def enforce_publication_capacity(staging, files):
    required_bytes = 0
    for source, _relative in files:
        try:
            source_stat = source.stat(follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"Dataset output cannot be inspected before publication: {exc}") from exc
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(f"Dataset output changed before publication: {source}")
        required_bytes += source_stat.st_size
        if required_bytes > MAX_EXECUTION_WORKSPACE_BYTES:
            raise RuntimeError(
                "Dataset output exceeded the Engine Workspace byte safety invariant before publication."
            )
    try:
        free_bytes = shutil.disk_usage(staging).free
    except OSError as exc:
        raise RuntimeError(f"Dataset publication capacity cannot be inspected: {exc}") from exc
    if free_bytes - required_bytes < MIN_HOST_FREE_BYTES:
        raise RuntimeError(
            "Dataset publication refused because copying the Workspace output would leave less than "
            f"{MIN_HOST_FREE_BYTES} bytes free."
        )


def make_tree_read_only(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)




def reconcile_interrupted_builds(config):
    """Resolve active Build Jobs from sealed evidence during Engine startup."""
    with engine_database.connect_database(config) as conn:
        active = conn.execute(
            """
            SELECT job_id, workspace_id, execution_workspace_path
            FROM dataset_build_jobs
            WHERE status IN ('submitted', 'running')
            ORDER BY submitted_at, job_id
            """
        ).fetchall()
    for row in active:
        execution_workspace = _require_execution_workspace(
            config,
            row["job_id"],
            row["execution_workspace_path"],
            required=False,
        )
        if execution_process_authority(execution_workspace):
            # A retained subreaper is still the only authority able to prove
            # descendant quiescence.  Recovery may not inspect, publish, or
            # terminalize files while that writer authority exists.
            continue
        evidence = completed_build_evidence(config, row["job_id"])
        if evidence is not None:
            _mark_build_completed(
                config, row["job_id"], row["workspace_id"], evidence
            )
            path = Path(row["execution_workspace_path"])
            if path.is_dir():
                make_tree_read_only(path)
            continue
        job = get_build_job(config, row["job_id"])
        evidence_path = (
            execution_workspace
            / ENGINE_WORKSPACE_DIRECTORY
            / BUILD_COMPLETION_EVIDENCE_NAME
        )
        if not evidence_path.is_file():
            _mark_build_failed(
                config,
                row["job_id"],
                row["workspace_id"],
                RuntimeError("Engine stopped before this Dataset Build completed."),
            )
            continue
        try:
            completion = load_build_completion_evidence(
                job, execution_workspace
            )
            publish_dataset(
                config,
                job,
                execution_workspace,
                completion["arguments"],
                completion["stdout"],
                completion["stderr"],
            )
            recovered = completed_build_evidence(config, row["job_id"])
            if recovered is None:
                raise RuntimeError(
                    "Dataset Build recovery returned without sealed output evidence."
                )
        except Exception as exc:
            # A persisted script-completion document proves only that the Script
            # exited successfully.  It is not publication evidence.  One startup
            # publication attempt resolves a crash between directory sealing and
            # the DB commit; a deterministic rejection must close the Job instead
            # of making every later Engine startup retry it forever.
            committed = completed_build_evidence(config, row["job_id"])
            if committed is not None:
                current = get_build_job(config, row["job_id"])
                workspace = get_workspace(config, row["workspace_id"])
                if (
                    current["status"] != "completed"
                    or current["outputVersionId"] != committed["datasetVersionId"]
                    or workspace["status"] != "published"
                    or workspace["submittedJobId"] != row["job_id"]
                ):
                    raise RuntimeError(
                        "Dataset Build committed evidence conflicts with its lifecycle state."
                    ) from exc
                continue
            current = get_build_job(config, row["job_id"])
            if current["status"] not in {"submitted", "running"}:
                raise RuntimeError(
                    "Dataset Build recovery failure conflicts with its terminal state."
                ) from exc
            _mark_build_failed(
                config, row["job_id"], row["workspace_id"], exc
            )


def reconcile_terminal_build_workspaces(config):
    """Discard terminal scratch only after writer quiescence is proven."""

    for item in build_jobs.terminal_build_workspaces(config):
        execution_workspace = _require_execution_workspace(
            config,
            item["jobId"],
            item["executionWorkspacePath"],
            required=False,
        )
        if execution_process_authority(execution_workspace):
            continue
        _discard_execution_workspace(
            config, item["jobId"], execution_workspace
        )
def publish_dataset(config, job, execution_workspace, arguments, stdout, stderr):
    # Seal the Script evidence before publication.  This keeps every operation
    # after the atomic Dataset/Job DB commit observational only; a chmod failure
    # can no longer turn a committed Build into an apparent failed Build.
    execution_workspace = _require_execution_workspace(
        config, job["jobId"], execution_workspace, required=True
    )
    if execution_process_authority(execution_workspace):
        raise RuntimeError(
            "Dataset Build publication requires proven writer termination."
        )
    authoritative_job = get_build_job(config, job["jobId"])
    workspace = get_workspace(config, job["workspaceId"])
    if (
        authoritative_job != job
        or job["status"] != "running"
        or workspace["status"] != "submitted"
        or workspace["submittedJobId"] != job["jobId"]
    ):
        raise RuntimeError(
            "Dataset Build Job and Workspace evidence is not publication-authoritative."
        )
    make_tree_read_only(execution_workspace)
    completion = load_build_completion_evidence(job, execution_workspace)
    if (
        list(arguments) != completion["arguments"]
        or stdout != completion["stdout"]
        or stderr != completion["stderr"]
    ):
        raise ValueError(
            "Dataset publication does not match its script-completion evidence."
        )
    bindings = job["sources"]
    verify_source_links(execution_workspace, bindings)
    files, capabilities = output_files(execution_workspace, bindings)
    dataset_id = job["outputDatasetId"]
    staging = dataset_staging.create_dataset_staging(config, dataset_id)
    try:
        enforce_publication_capacity(staging.path, files)
        for source, relative in files:
            destination = staging.path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        lineage = [
            {
                "alias": binding["alias"],
                "datasetId": binding["datasetId"],
                "datasetVersionId": binding["datasetVersionId"],
                "contentHash": binding["contentHash"],
            }
            for binding in bindings
        ]
        build = {
            "jobId": job["jobId"],
            "scriptDigest": job["scriptDigest"],
            "invocation": {"type": "python-script", "arguments": arguments},
            "runtime": {
                "python": sys.version,
                "sandbox": "bubblewrap",
                "network": "unshared",
                "writableRoot": "/workspace",
            },
        }
        now = engine_clock.utc_now()
        with dataset_publication.dataset_publication_transaction(
            config,
            dataset={
                "datasetId": dataset_id,
                "name": job["outputDatasetName"],
                "source": {"type": "derived-script", "details": {}},
                "metadata": {
                    "kind": "derived-container",
                    "sourceDatasetIds": [item["datasetId"] for item in bindings],
                },
            },
            staging=staging,
            capabilities=capabilities,
            version_source={
                "type": "derived-script",
                "details": {"jobId": job["jobId"], "scriptDigest": job["scriptDigest"]},
            },
            lineage=lineage,
            build=build,
            build_job_id=job["jobId"],
        ) as publication:
            version_id = publication["datasetVersionId"]
            conn = publication["connection"]
            job_update = conn.execute(
                """
                UPDATE dataset_build_jobs
                SET status = 'completed', completed_at = ?, output_version_id = ?, stdout_text = ?, stderr_text = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (now, version_id, stdout, stderr, job["jobId"]),
            )
            workspace_update = conn.execute(
                "UPDATE dataset_workspaces SET status = 'published', updated_at = ? "
                "WHERE workspace_id = ? AND status = 'submitted' "
                "AND submitted_job_id = ?",
                (now, job["workspaceId"], job["jobId"]),
            )
            if job_update.rowcount != 1 or workspace_update.rowcount != 1:
                raise RuntimeError(
                    "Dataset Build Job or Workspace changed before publication commit."
                )
    finally:
        dataset_staging.discard_dataset_staging(staging)
    return version_id


def submit_build(config, request):
    require_request_fields(
        request,
        allowed=BUILD_REQUEST_FIELDS,
        required={"workspaceId", "recipeId", "recipeVersion"},
        label="Dataset Build request",
    )
    workspace = get_workspace(config, request.get("workspaceId"))
    if workspace["status"] != "draft":
        raise ValueError(f"Dataset Workspace '{workspace['workspaceId']}' is already {workspace['status']}.")
    requested_output_id = str(request.get("outputDatasetId") or "").strip()
    if not requested_output_id and not str(request.get("outputDatasetName") or "").strip():
        raise ValueError("Output Dataset name is required.")
    output_dataset_id = (
        resource_ids.normalize_resource_id(requested_output_id)
        if requested_output_id
        else resource_ids.new_resource_id("dataset")
    )
    assert_output_dataset_available(config, output_dataset_id)
    script_text, arguments, recipe_id, recipe_version = resolve_archived_recipe(config, request)
    bindings = verify_workspace_bindings(config, workspace["sources"])
    timeout_seconds = request.get("timeoutSeconds", 300)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= 3600
    ):
        raise ValueError("Dataset Build timeoutSeconds must be an integer from 1 to 3600.")
    requested_job_id = str(request.get("jobId") or "").strip()
    job_id = (
        resource_ids.normalize_resource_id(requested_job_id)
        if requested_job_id
        else resource_ids.new_resource_id("job")
    )
    root = None
    try:
        root = build_paths.dataset_build_job_directory(config, job_id, create=True)
        execution_workspace = root / "workspace"
        materialize_source_links(execution_workspace, bindings)
        engine_directory = execution_workspace / ENGINE_WORKSPACE_DIRECTORY
        engine_directory.mkdir()
        script_path = engine_directory / ENGINE_SCRIPT_NAME
        with script_path.open("w", encoding="utf-8") as handle:
            handle.write(script_text)
            handle.flush()
            os.fsync(handle.fileno())
        for directory in (
            engine_directory, execution_workspace, root, root.parent
        ):
            build_paths.fsync_directory(directory)
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        cleanup_error = None
        if root is not None:
            try:
                _discard_execution_workspace(
                    config, job_id, root / "workspace"
                )
            except BaseException as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise primary_error.with_traceback(primary_traceback) from cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    now = engine_clock.utc_now()
    submission = {
        "jobId": job_id,
        "workspaceId": workspace["workspaceId"],
        "outputDatasetId": output_dataset_id,
        "outputDatasetName": request.get("outputDatasetName") or output_dataset_id,
        "executionWorkspacePath": str(execution_workspace),
        "recipeId": recipe_id,
        "recipeVersion": recipe_version,
        "scriptPath": str(script_path),
        "scriptDigest": script_digest(script_text),
        "arguments": arguments,
        "sources": bindings,
        "submittedAt": now,
    }
    operation_nonce = secrets.token_hex(32)
    try:
        build_jobs.commit_build_submission(config, submission, operation_nonce)
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        state = build_jobs.build_submission_commit_state(
            config, submission, operation_nonce
        )
        if state == "committed":
            pass
        elif state == "absent":
            try:
                _discard_execution_workspace(
                    config, job_id, execution_workspace
                )
            except BaseException as cleanup_error:
                raise primary_error.with_traceback(primary_traceback) from cleanup_error
            raise primary_error.with_traceback(primary_traceback)
        else:
            raise RuntimeError(
                "Dataset Build submission commit could not be reconciled; "
                "scratch is retained for exact recovery."
            ) from primary_error
    try:
        build_jobs.mark_build_running(config, job_id)
        execution_workspace = _require_execution_workspace(
            config, job_id, execution_workspace, required=True
        )
        stdout, stderr = execute_in_sandbox(
            execution_workspace,
            script_path,
            arguments,
            bindings,
            timeout_seconds,
        )
        execution_workspace = _require_execution_workspace(
            config, job_id, execution_workspace, required=True
        )
        if execution_process_authority(execution_workspace):
            raise RuntimeError(
                "Dataset Build execution returned without writer termination proof."
            )
        job = get_build_job(config, job_id)
        write_build_completion_evidence(
            job, execution_workspace, arguments, stdout, stderr
        )
        execution_workspace = _require_execution_workspace(
            config, job_id, execution_workspace, required=True
        )
        if execution_process_authority(execution_workspace):
            raise RuntimeError(
                "Dataset Build writer authority reappeared before publication."
            )
        publish_dataset(config, job, execution_workspace, arguments, stdout, stderr)
    except BaseException as exc:
        primary_traceback = exc.__traceback__
        # A failed supervision driver is not a terminal build result.  Keep
        # both the running row and scratch directory while the exact retained
        # authority can still write; shutdown/recovery may retry termination.
        try:
            execution_workspace = _require_execution_workspace(
                config, job_id, execution_workspace, required=False
            )
        except BaseException as authority_error:
            raise exc.with_traceback(primary_traceback) from authority_error
        if execution_process_authority(execution_workspace):
            raise exc.with_traceback(primary_traceback)
        evidence = completed_build_evidence(config, job_id)
        if evidence is not None:
            current = get_build_job(config, job_id)
            if current["status"] in {"submitted", "running"}:
                _mark_build_completed(
                    config, job_id, workspace["workspaceId"], evidence
                )
            elif (
                current["status"] != "completed"
                or current["outputVersionId"] != evidence["datasetVersionId"]
            ):
                raise RuntimeError(
                    "Dataset Build terminal state conflicts with its sealed output evidence."
                ) from exc
            response = {
                "job": get_build_job(config, job_id),
                "dataset": datasets.get_dataset(config, output_dataset_id),
            }
            execution_workspace = _require_execution_workspace(
                config, job_id, execution_workspace, required=False
            )
            if execution_process_authority(execution_workspace):
                raise exc.with_traceback(primary_traceback)
            _discard_committed_execution_workspace(
                config, job_id, execution_workspace
            )
            return response
        try:
            execution_workspace = _require_execution_workspace(
                config, job_id, execution_workspace, required=False
            )
            _mark_build_failed(config, job_id, workspace["workspaceId"], exc)
            if execution_process_authority(execution_workspace):
                raise RuntimeError(
                    "Dataset Build writer authority appeared during terminalization."
                )
            _discard_execution_workspace(config, job_id, execution_workspace)
        except BaseException as cleanup_error:
            raise exc.with_traceback(primary_traceback) from cleanup_error
        raise exc.with_traceback(primary_traceback)
    response = {
        "job": get_build_job(config, job_id),
        "dataset": datasets.get_dataset(config, output_dataset_id),
    }
    execution_workspace = _require_execution_workspace(
        config, job_id, execution_workspace, required=False
    )
    _discard_committed_execution_workspace(
        config, job_id, execution_workspace
    )
    return response


def process_recipe(config, request):
    """Run one submitted Script against selected immutable Datasets.

    The temporary Workspace is an implementation detail and is hidden from the
    user Workspace repository.  The normal build path remains the sole publisher,
    so lineage, read-only source links, sandboxing and immutable Dataset versions
    are identical to an explicitly submitted Workspace build.
    """
    require_request_fields(
        request,
        allowed=PROCESS_REQUEST_FIELDS,
        required={"recipeId", "recipeVersion", "datasetIds"},
        label="Dataset Process request",
    )
    dataset_ids = request["datasetIds"]
    if not isinstance(dataset_ids, list) or not dataset_ids:
        raise ValueError("Process requires at least one source Dataset.")
    normalized = []
    seen = set()
    for index, value in enumerate(dataset_ids):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Process datasetIds[{index}] must be a non-empty string.")
        dataset_id = value.strip()
        if dataset_id in seen:
            raise ValueError(f"Process datasetIds contains duplicate ID: {dataset_id}")
        seen.add(dataset_id)
        normalized.append(dataset_id)
    workspace_id = resource_ids.new_resource_id("workspace")
    sources = [
        {"datasetId": dataset_id, "alias": f"dataset{index + 1}"}
        for index, dataset_id in enumerate(normalized)
    ]
    workspace = create_workspace(config, {
        "workspaceId": workspace_id,
        "name": f"Process {request.get('recipeId') or 'script'}",
        "sources": sources,
    }, internal=True)
    build_request = {
        field: value
        for field, value in request.items()
        if field in BUILD_REQUEST_FIELDS and field != "workspaceId"
    }
    build_request["workspaceId"] = workspace["workspaceId"]
    try:
        result = submit_build(config, build_request)
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        cleanup_error = None
        try:
            current = get_workspace(config, workspace["workspaceId"])
            # A submitted Workspace can still have a retained ProcessSession.
            # Its exact authority and scratch must survive for shutdown/recovery.
            if current["status"] != "submitted":
                delete_workspace(config, workspace["workspaceId"])
        except BaseException as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            raise primary_error.with_traceback(primary_traceback) from cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    delete_workspace(config, workspace["workspaceId"])
    return result
