"""User-managed virtual folders shared by TradeEngine repositories.

Folders are metadata only. Immutable repository object IDs and physical release
paths never change when an item is classified or moved.
"""

from __future__ import annotations

import os
import fcntl
import re
import secrets
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any, Mapping, Optional

from engine.contracts import strict_json


SCHEMA_VERSION = 9
STATE_FILE = "repository-folders.json"
REPOSITORIES = (
    "modules",
    "analysis-modules",
    "environment-modules",
    "data",
    "backtest",
    "datasets",
    "samplers",
    "pipelines",
    "environments",
    "analyses",
    "backtests",
    "scripts",
    "workspaces",
)
MODULE_FIXED_FOLDERS = (
    "Universe",
    "Signal",
    "Target",
    "Constraint",
)
MODULE_KINDS_BY_REPOSITORY = {
    "modules": MODULE_FIXED_FOLDERS,
    "analysis-modules": ("Analyzer",),
    "environment-modules": ("Environment",),
}
FLAT_MODULE_REPOSITORIES = frozenset({
    "analysis-modules",
    "environment-modules",
})
_STATE_LOCK_LOCAL = threading.local()
_STATE_THREAD_LOCKS_GUARD = threading.Lock()


class _ThreadReadWriteLock:
    """Writer-preferring process-local counterpart to the file lock."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextmanager
    def shared(self):
        with self._condition:
            while self._writer or self._waiting_writers:
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if not self._readers:
                    self._condition.notify_all()

    @contextmanager
    def exclusive(self):
        with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._condition.wait()
                self._writer = True
            finally:
                self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()


_STATE_THREAD_LOCKS: dict[str, _ThreadReadWriteLock] = {}


def _thread_state_lock(lock_identity: str) -> _ThreadReadWriteLock:
    with _STATE_THREAD_LOCKS_GUARD:
        state_lock = _STATE_THREAD_LOCKS.get(lock_identity)
        if state_lock is None:
            state_lock = _ThreadReadWriteLock()
            _STATE_THREAD_LOCKS[lock_identity] = state_lock
        return state_lock


def _reset_state_locks_after_fork() -> None:
    """Discard inherited thread bookkeeping in a forked child."""
    global _STATE_LOCK_LOCAL, _STATE_THREAD_LOCKS_GUARD, _STATE_THREAD_LOCKS
    _STATE_LOCK_LOCAL = threading.local()
    _STATE_THREAD_LOCKS_GUARD = threading.Lock()
    _STATE_THREAD_LOCKS = {}


os.register_at_fork(after_in_child=_reset_state_locks_after_fork)


def _state_path(config) -> Path:
    return Path(config["controlRoot"]) / STATE_FILE


@contextmanager
def _state_lock(config, *, exclusive: bool):
    """Hold one re-entrant local/file transaction over repository state."""
    lock_path = Path(config["controlRoot"]) / ".repository-folders.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_identity = str(lock_path.resolve())
    transactions = getattr(_STATE_LOCK_LOCAL, "transactions", None)
    if transactions is None:
        transactions = {}
        _STATE_LOCK_LOCAL.transactions = transactions
    transaction = transactions.get(lock_identity)
    if transaction is not None:
        if exclusive and transaction["mode"] != "exclusive":
            raise RuntimeError(
                "A repository-folder shared snapshot cannot be upgraded to a mutation."
            )
        transaction["depth"] += 1
        try:
            yield
        finally:
            transaction["depth"] -= 1
        return

    local_lock = _thread_state_lock(lock_identity)
    thread_lock = (
        local_lock.exclusive() if exclusive else local_lock.shared()
    )
    file_mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    with thread_lock, lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), file_mode)
        transactions[lock_identity] = {
            "depth": 1,
            "mode": "exclusive" if exclusive else "shared",
        }
        try:
            yield
        finally:
            del transactions[lock_identity]
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def repository_read_snapshot(config):
    """Keep compound repository reads on one shared state snapshot."""
    with _state_lock(config, exclusive=False):
        yield


@contextmanager
def _mutation_lock(config):
    """Serialize one complete repository-folder read/modify/write transaction."""
    with _state_lock(config, exclusive=True):
        yield


def _serialized_read(function):
    @wraps(function)
    def invoke(config, *args, **kwargs):
        with repository_read_snapshot(config):
            return function(config, *args, **kwargs)
    return invoke


def _serialized_mutation(function):
    @wraps(function)
    def invoke(config, *args, **kwargs):
        with _mutation_lock(config):
            return function(config, *args, **kwargs)
    return invoke


def _empty_state() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "folders": {repository: {} for repository in REPOSITORIES},
        "assignments": {repository: {} for repository in REPOSITORIES},
    }


def _fixed_folder_id(name: str) -> str:
    return "modules-fixed-" + re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def _builtin_folder_id(name: str) -> str:
    return _fixed_folder_id(name) + "-builtin"


def _repository_builtin_folder_id(repository: str) -> str:
    return f"{repository}-fixed-builtin"


def fixed_folders(repository: str) -> dict[str, dict[str, Any]]:
    if repository == "modules":
        folders = {}
        for name in MODULE_FIXED_FOLDERS:
            root_id = _fixed_folder_id(name)
            folders[root_id] = {
                "folderId": _fixed_folder_id(name),
                "name": name,
                "parentId": "",
                "fixed": True,
            }
            builtin_id = _builtin_folder_id(name)
            folders[builtin_id] = {
                "folderId": builtin_id,
                "name": "BuiltIn",
                "parentId": root_id,
                "fixed": True,
            }
        return folders
    if repository in FLAT_MODULE_REPOSITORIES:
        builtin_id = _repository_builtin_folder_id(repository)
        return {
            builtin_id: {
                "folderId": builtin_id,
                "name": "BuiltIn",
                "parentId": "",
                "fixed": True,
            },
        }
    return {}


def shared_item_id(source_repository: str, source_item_id: str) -> str:
    """Return a collision-safe ID for an item projected into a shared scope."""
    return f"{str(source_repository).strip().lower()}::{str(source_item_id)}"


def _migrated_stable_item_id(repository: str, item_id: str) -> tuple[str, int | None]:
    if repository in MODULE_KINDS_BY_REPOSITORY:
        parts = item_id.split("/")
        if len(parts) == 3 and parts[2].isdigit():
            return "/".join(parts[:2]), int(parts[2])
    if repository in {"environments", "analyses"}:
        identity, separator, version = item_id.rpartition("::")
        if separator and identity and version.isdigit():
            return identity, int(version)
    return item_id, None


def _migrate_v8_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != {
        "schemaVersion",
        "folders",
        "assignments",
    }:
        raise ValueError("Repository folder state fields do not match schemaVersion 8.")
    if payload.get("schemaVersion") != 8:
        raise ValueError("Repository folder state is not schemaVersion 8.")
    folders_by_repository = payload.get("folders")
    assignments_by_repository = payload.get("assignments")
    if (
        not isinstance(folders_by_repository, Mapping)
        or set(folders_by_repository) != set(REPOSITORIES)
        or not isinstance(assignments_by_repository, Mapping)
        or set(assignments_by_repository) != set(REPOSITORIES)
    ):
        raise ValueError(
            "Repository folder schemaVersion 8 must contain every repository exactly once."
        )
    migrated = deepcopy(payload)
    migrated["schemaVersion"] = SCHEMA_VERSION
    flat_kinds = {
        "analysis-modules": "Analyzer",
        "environment-modules": "Environment",
    }
    for repository, kind in flat_kinds.items():
        folders = migrated["folders"].get(repository)
        if not isinstance(folders, Mapping):
            raise ValueError(f"Repository '{repository}' folders must be an object.")
        old_parents = {_fixed_folder_id(kind), _builtin_folder_id(kind)}
        for folder in folders.values():
            if isinstance(folder, Mapping) and folder.get("parentId") in old_parents:
                folder["parentId"] = ""

    for repository in REPOSITORIES:
        assignments = migrated["assignments"].get(repository)
        if not isinstance(assignments, Mapping):
            raise ValueError(f"Repository '{repository}' assignments must be an object.")
        candidates = {}
        for item_id, folder_id in assignments.items():
            if not isinstance(item_id, str):
                raise ValueError(f"Repository '{repository}' contains an invalid item assignment.")
            stable_id, version = _migrated_stable_item_id(repository, item_id)
            priority = float("inf") if version is None else version
            current = candidates.get(stable_id)
            if current is None or priority > current[0]:
                candidates[stable_id] = (priority, folder_id)
        normalized = {}
        old_default_folders = set()
        if repository in FLAT_MODULE_REPOSITORIES:
            kind = flat_kinds[repository]
            old_default_folders = {_fixed_folder_id(kind), _builtin_folder_id(kind)}
        for item_id, (_priority, folder_id) in candidates.items():
            if folder_id not in old_default_folders:
                normalized[item_id] = folder_id
        migrated["assignments"][repository] = normalized
    return validate_state(migrated)


def validate_state(payload: Any) -> dict[str, Any]:
    """Require the current complete folder-state contract without migrating it."""
    if not isinstance(payload, Mapping):
        raise ValueError("Repository folder state must be an object.")
    if set(payload) != {"schemaVersion", "folders", "assignments"}:
        raise ValueError("Repository folder state fields do not match the current schema.")
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(
            f"Repository folder state requires schemaVersion {SCHEMA_VERSION}."
        )
    folders_by_repository = payload.get("folders")
    assignments_by_repository = payload.get("assignments")
    if not isinstance(folders_by_repository, Mapping) or set(folders_by_repository) != set(REPOSITORIES):
        raise ValueError("Repository folder state must contain every repository folder map exactly once.")
    if not isinstance(assignments_by_repository, Mapping) or set(assignments_by_repository) != set(REPOSITORIES):
        raise ValueError("Repository folder state must contain every repository assignment map exactly once.")

    state = _empty_state()
    for repository in REPOSITORIES:
        folders = folders_by_repository[repository]
        assignments = assignments_by_repository[repository]
        if not isinstance(folders, Mapping):
            raise ValueError(f"Repository '{repository}' folders must be an object.")
        if not isinstance(assignments, Mapping):
            raise ValueError(f"Repository '{repository}' assignments must be an object.")

        checked_folders = {}
        for folder_id, folder in folders.items():
            if not isinstance(folder_id, str) or not folder_id:
                raise ValueError(f"Repository '{repository}' contains an invalid folder ID.")
            if not isinstance(folder, Mapping) or set(folder) != {"folderId", "name", "parentId", "fixed"}:
                raise ValueError(f"Repository folder '{folder_id}' does not match the current schema.")
            if folder.get("folderId") != folder_id:
                raise ValueError(f"Repository folder identity mismatch: {folder_id}")
            if folder.get("fixed") is not False:
                raise ValueError("Fixed repository folders are Engine definitions and cannot be stored as mutable state.")
            checked_folders[folder_id] = {
                "folderId": folder_id,
                "name": _validate_name(folder.get("name")),
                "parentId": str(folder.get("parentId") or ""),
                "fixed": False,
            }

        all_repository_folders = {**fixed_folders(repository), **checked_folders}
        _folder_paths(all_repository_folders)
        sibling_names = set()
        for folder in all_repository_folders.values():
            sibling = (
                str(folder.get("parentId") or ""),
                str(folder.get("name") or "").casefold(),
            )
            if sibling in sibling_names:
                raise ValueError(
                    f"Repository '{repository}' contains duplicate sibling folder names."
                )
            sibling_names.add(sibling)
        if repository == "modules":
            fixed_ids = set(fixed_folders(repository))
            for folder_id in checked_folders:
                if not any(_is_descendant(folder_id, root_id, all_repository_folders) for root_id in fixed_ids):
                    raise ValueError(f"Module folder '{folder_id}' is outside its fixed type tree.")
        elif repository in FLAT_MODULE_REPOSITORIES:
            builtin_id = _repository_builtin_folder_id(repository)
            for folder_id in checked_folders:
                if _is_descendant(folder_id, builtin_id, all_repository_folders):
                    raise ValueError(
                        f"Module folder '{folder_id}' is inside the reserved BuiltIn folder."
                    )

        checked_assignments = {}
        for item_id, folder_id in assignments.items():
            if not isinstance(item_id, str) or not item_id or not isinstance(folder_id, str):
                raise ValueError(f"Repository '{repository}' contains an invalid item assignment.")
            if folder_id and folder_id not in all_repository_folders:
                raise ValueError(
                    f"Repository item '{item_id}' references missing folder '{folder_id}'."
                )
            if repository in {"modules", "analysis-modules", "environment-modules"}:
                kind = item_id.split("/", 1)[0]
                expected_kinds = MODULE_KINDS_BY_REPOSITORY[repository]
                if kind not in expected_kinds or not folder_id:
                    raise ValueError(f"Module assignment '{item_id}' has no valid type folder.")
                if repository == "modules":
                    root_id = _fixed_folder_id(kind)
                    if not _is_descendant(folder_id, root_id, all_repository_folders):
                        raise ValueError(
                            f"Module assignment '{item_id}' is outside its {kind} folder."
                        )
                elif _is_descendant(
                    folder_id,
                    _repository_builtin_folder_id(repository),
                    all_repository_folders,
                ):
                    raise ValueError(
                        f"Module assignment '{item_id}' is inside the reserved BuiltIn folder."
                    )
            checked_assignments[item_id] = folder_id
        state["folders"][repository] = checked_folders
        state["assignments"][repository] = checked_assignments
    return state


@_serialized_read
def load_state(config) -> dict[str, Any]:
    path = _state_path(config)
    if not path.is_file():
        return _empty_state()
    with path.open(encoding="utf-8") as handle:
        payload = strict_json.load(handle)
    return validate_state(payload)


@_serialized_mutation
def prepare(config) -> dict[str, Any]:
    """Prepare the exact folder schema, migrating the one prior durable format."""
    path = _state_path(config)
    if not path.is_file():
        return _empty_state()
    with path.open(encoding="utf-8") as handle:
        payload = strict_json.load(handle)
    if isinstance(payload, Mapping) and payload.get("schemaVersion") == SCHEMA_VERSION:
        return validate_state(payload)
    if isinstance(payload, Mapping) and payload.get("schemaVersion") == 8:
        state = _migrate_v8_state(payload)
        save_state(config, state)
        return state
    raise ValueError(
        f"Repository folder state requires schemaVersion {SCHEMA_VERSION} or migratable schemaVersion 8."
    )


@_serialized_mutation
def save_state(config, state) -> None:
    state = validate_state(state)
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(strict_json.dumps(state, indent=2, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def require_repository(repository: str) -> str:
    repository = str(repository or "").strip().lower()
    if repository not in REPOSITORIES:
        raise ValueError(f"Unknown repository: {repository}")
    return repository


@_serialized_read
def all_folders(config, repository: str) -> dict[str, dict[str, Any]]:
    repository = require_repository(repository)
    state = load_state(config)
    return {**fixed_folders(repository), **deepcopy(state["folders"][repository])}


def _folder_paths(folders: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    paths = {"": "/"}
    visiting = set()

    def resolve(folder_id):
        if folder_id in paths:
            return paths[folder_id]
        if folder_id in visiting or folder_id not in folders:
            raise ValueError(f"Repository folder hierarchy is invalid at '{folder_id}'.")
        visiting.add(folder_id)
        folder = folders[folder_id]
        parent_path = resolve(str(folder.get("parentId") or ""))
        name = str(folder.get("name") or "").strip()
        path = f"/{name}" if parent_path == "/" else f"{parent_path}/{name}"
        visiting.remove(folder_id)
        paths[folder_id] = path
        return path

    for folder_id in folders:
        resolve(folder_id)
    return paths


@_serialized_read
def repository_tree(config, repository: str) -> dict[str, Any]:
    repository = require_repository(repository)
    state = load_state(config)
    folders = {
        **fixed_folders(repository),
        **deepcopy(state["folders"][repository]),
    }
    paths = _folder_paths(folders)
    rows = [
        {**deepcopy(folder), "path": paths[folder_id]}
        for folder_id, folder in folders.items()
    ]
    rows.sort(key=lambda item: (item["path"].casefold(), item["folderId"]))
    return {
        "repository": repository,
        "folders": rows,
        "assignments": deepcopy(state["assignments"][repository]),
    }


def _validate_name(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Folder name is required.")
    if len(name) > 80:
        raise ValueError("Folder name must be 80 characters or fewer.")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("Folder name cannot contain path separators.")
    return name


@_serialized_mutation
def create_folder(config, repository: str, name: str, parent_id: str = "") -> dict[str, Any]:
    repository = require_repository(repository)
    name = _validate_name(name)
    parent_id = str(parent_id or "")
    folders = all_folders(config, repository)
    if parent_id and parent_id not in folders:
        raise ValueError(f"Parent folder does not exist: {parent_id}")
    if repository == "modules" and not parent_id:
        raise ValueError("Module top-level folders are fixed; create folders inside a module type.")
    if repository in FLAT_MODULE_REPOSITORIES and parent_id and _is_descendant(
        parent_id,
        _repository_builtin_folder_id(repository),
        folders,
    ):
        raise ValueError("User folders cannot be created inside the BuiltIn folder.")
    siblings = [folder for folder in folders.values() if str(folder.get("parentId") or "") == parent_id]
    if any(str(folder.get("name") or "").casefold() == name.casefold() for folder in siblings):
        raise ValueError(f"Folder '{name}' already exists under the selected parent.")
    folder_id = "folder-" + secrets.token_hex(12)
    folder = {"folderId": folder_id, "name": name, "parentId": parent_id, "fixed": False}
    state = load_state(config)
    state["folders"][repository][folder_id] = folder
    save_state(config, state)
    return {**folder, "path": _folder_paths({**folders, folder_id: folder})[folder_id]}


@_serialized_mutation
def rename_folder(config, repository: str, folder_id: str, name: str) -> dict[str, Any]:
    repository = require_repository(repository)
    name = _validate_name(name)
    state = load_state(config)
    folder = state["folders"][repository].get(folder_id)
    if not folder:
        if folder_id in fixed_folders(repository):
            raise ValueError("Fixed repository folders cannot be renamed.")
        raise ValueError(f"Folder does not exist: {folder_id}")
    parent_id = str(folder.get("parentId") or "")
    folders = all_folders(config, repository)
    if any(
        candidate_id != folder_id
        and str(candidate.get("parentId") or "") == parent_id
        and str(candidate.get("name") or "").casefold() == name.casefold()
        for candidate_id, candidate in folders.items()
    ):
        raise ValueError(f"Folder '{name}' already exists under the selected parent.")
    folder["name"] = name
    save_state(config, state)
    return next(item for item in repository_tree(config, repository)["folders"] if item["folderId"] == folder_id)


@_serialized_mutation
def move_folder(config, repository: str, folder_id: str, parent_id: str = "") -> dict[str, Any]:
    """Move a user folder without changing any repository object identities."""
    repository = require_repository(repository)
    folder_id = str(folder_id or "")
    parent_id = str(parent_id or "")
    fixed = fixed_folders(repository)
    if folder_id in fixed:
        raise ValueError("Fixed repository folders cannot be moved.")
    state = load_state(config)
    folder = state["folders"][repository].get(folder_id)
    if not folder:
        raise ValueError(f"Folder does not exist: {folder_id}")
    folders = all_folders(config, repository)
    if parent_id and parent_id not in folders:
        raise ValueError(f"Parent folder does not exist: {parent_id}")
    if parent_id == folder_id or _is_descendant(parent_id, folder_id, folders):
        raise ValueError("A folder cannot be moved inside itself or one of its descendants.")
    if repository == "modules":
        current_root = next(
            (fixed_id for fixed_id in fixed if _is_descendant(folder_id, fixed_id, folders)),
            "",
        )
        if not current_root or not parent_id or not _is_descendant(parent_id, current_root, folders):
            raise ValueError("Module folders must remain inside their fixed type folder.")
    elif repository in FLAT_MODULE_REPOSITORIES and parent_id and _is_descendant(
        parent_id,
        _repository_builtin_folder_id(repository),
        folders,
    ):
        raise ValueError("User folders cannot be moved inside the BuiltIn folder.")
    siblings = [
        candidate
        for candidate_id, candidate in folders.items()
        if candidate_id != folder_id and str(candidate.get("parentId") or "") == parent_id
    ]
    if any(str(candidate.get("name") or "").casefold() == str(folder.get("name") or "").casefold() for candidate in siblings):
        raise ValueError(f"Folder '{folder.get('name')}' already exists under the selected parent.")
    folder["parentId"] = parent_id
    save_state(config, state)
    return next(item for item in repository_tree(config, repository)["folders"] if item["folderId"] == folder_id)


@_serialized_mutation
def delete_folder(config, repository: str, folder_id: str) -> dict[str, Any]:
    repository = require_repository(repository)
    if folder_id in fixed_folders(repository):
        raise ValueError("Fixed repository folders cannot be deleted.")
    state = load_state(config)
    if folder_id not in state["folders"][repository]:
        raise ValueError(f"Folder does not exist: {folder_id}")
    folders = all_folders(config, repository)
    if any(str(folder.get("parentId") or "") == folder_id for folder in folders.values()):
        raise ValueError("Folder is not empty; move or delete its child folders first.")
    if folder_id in state["assignments"][repository].values():
        raise ValueError("Folder contains repository items; move them before deleting it.")
    removed = state["folders"][repository].pop(folder_id)
    save_state(config, state)
    return deepcopy(removed)


def default_module_folder(
    repository: str,
    item_id: str,
    item: Optional[Mapping[str, Any]] = None,
) -> str:
    item = item or {}
    kind = str(item.get("kind") or "")
    if kind not in MODULE_KINDS_BY_REPOSITORY.get(repository, ()):
        return ""
    if repository == "modules":
        return _builtin_folder_id(kind) if item.get("builtin") else _fixed_folder_id(kind)
    return _repository_builtin_folder_id(repository) if item.get("builtin") else ""


def default_item_folder(repository: str, item_id: str, item: Optional[Mapping[str, Any]] = None) -> str:
    item = item or {}
    if repository in {"modules", "analysis-modules", "environment-modules"}:
        return default_module_folder(repository, item_id, item)
    return ""


def _is_descendant(folder_id: str, ancestor_id: str, folders: Mapping[str, Mapping[str, Any]]) -> bool:
    current = folder_id
    seen = set()
    while current and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        current = str((folders.get(current) or {}).get("parentId") or "")
    return False


@_serialized_mutation
def assign_item(
    config,
    repository: str,
    item_id: str,
    folder_id: str,
    *,
    module_definition: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    repository = require_repository(repository)
    item_id = str(item_id or "").strip()
    folder_id = str(folder_id or "")
    if not item_id:
        raise ValueError("Repository itemId is required.")
    folders = all_folders(config, repository)
    if folder_id and folder_id not in folders:
        raise ValueError(f"Folder does not exist: {folder_id}")
    item_definition = module_definition or {}
    module_repositories = {"modules", "analysis-modules", "environment-modules"}
    if repository in module_repositories:
        fixed_parent = default_item_folder(repository, item_id, item_definition)
        if item_definition.get("builtin") and (
            not fixed_parent or folder_id != fixed_parent
        ):
            raise ValueError("Built-in Modules are fixed inside their type's BuiltIn folder.")
        if repository == "modules" and (
            not fixed_parent
            or not folder_id
            or not _is_descendant(folder_id, fixed_parent, folders)
        ):
            raise ValueError("Modules must remain inside their fixed type folder.")
        if repository in FLAT_MODULE_REPOSITORIES and not item_definition.get("builtin"):
            builtin_id = _repository_builtin_folder_id(repository)
            if folder_id and _is_descendant(folder_id, builtin_id, folders):
                raise ValueError("User Modules cannot be moved inside the BuiltIn folder.")
    state = load_state(config)
    default = default_item_folder(repository, item_id, item_definition)
    if repository not in {"data", "backtest"} and folder_id == default:
        state["assignments"][repository].pop(item_id, None)
    else:
        state["assignments"][repository][item_id] = folder_id
    save_state(config, state)
    paths = _folder_paths(folders)
    return {"repository": repository, "itemId": item_id, "folderId": folder_id, "folderPath": paths[folder_id]}


def resolve_item_folder(
    tree: Mapping[str, Any],
    item_id: str,
    item: Optional[Mapping[str, Any]] = None,
) -> dict[str, str]:
    """Resolve one item against an already-materialized repository snapshot."""
    repository = require_repository(tree.get("repository"))
    assignments = tree.get("assignments")
    folders = tree.get("folders")
    if not isinstance(assignments, Mapping) or not isinstance(folders, list):
        raise ValueError("Repository tree does not match the current snapshot contract.")
    item_id = str(item_id)
    if repository in {"modules", "analysis-modules", "environment-modules"} and (
        item or {}
    ).get("builtin"):
        folder_id = default_item_folder(repository, item_id, item)
    elif item_id in assignments:
        folder_id = assignments[item_id]
    else:
        folder_id = default_item_folder(repository, item_id, item)
    paths = {"": "/"}
    paths.update({row["folderId"]: row["path"] for row in folders})
    if folder_id not in paths:
        raise ValueError(
            f"Repository item '{item_id}' references missing folder '{folder_id}'."
        )
    return {"folderId": folder_id, "folderPath": paths[folder_id]}


@_serialized_read
def item_folder(config, repository: str, item_id: str, item: Optional[Mapping[str, Any]] = None) -> dict[str, str]:
    repository = require_repository(repository)
    tree = repository_tree(config, repository)
    return resolve_item_folder(tree, item_id, item)


@_serialized_mutation
def remove_item_assignment(config, repository: str, item_id: str) -> None:
    repository = require_repository(repository)
    state = load_state(config)
    if str(item_id) in state["assignments"][repository]:
        state["assignments"][repository].pop(str(item_id), None)
        save_state(config, state)
