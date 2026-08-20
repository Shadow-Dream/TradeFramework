"""Validated DataKey path plans and allocation-conscious Data Dict access."""
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import lru_cache
from typing import Any, Dict


__all__ = (
    "canonical_data_key_order", "compile_data_path_plan",
    "delete_data_segments_copy_on_write",
    "get_data_path", "get_data_segments", "project_compiled_data_paths",
    "project_data_paths", "require_data_path", "require_data_segments",
    "set_data_path", "set_data_segments", "set_data_segments_copy_on_write",
    "split_data_path",
)


_PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_MISSING = object()


def _split_data_path_text(text: str) -> tuple[str, ...]:
    parts = tuple(text.split(".")) if text else ()
    if not parts or any(not part or not _PATH_SEGMENT_PATTERN.fullmatch(part) for part in parts):
        raise ValueError(f"Invalid DataKey path '{text}'.")
    return parts


def split_data_path(path: Any) -> tuple[str, ...]:
    return _split_data_path_text(str(path or "").strip())


def canonical_data_key_order(data_key: Any, identity: Any = "") -> tuple:
    """Order writes that originate in unordered JSON binding objects."""
    if not isinstance(identity, str):
        raise ValueError("DataKey binding identity must be a string.")
    return split_data_path(data_key), identity


def get_data_path(data: Mapping[str, Any], path: Any, default: Any = None) -> Any:
    return get_data_segments(data, split_data_path(path), default)


def get_data_segments(
    data: Mapping[str, Any],
    segments: tuple[str, ...],
    default: Any = None,
) -> Any:
    """Read a DataKey whose validated path segments were compiled earlier."""
    node: Any = data
    for segment in segments:
        if type(node) is dict:
            child = node.get(segment, _MISSING)
            if child is _MISSING:
                return default
            node = child
            continue
        if not isinstance(node, Mapping) or segment not in node:
            return default
        node = node[segment]
    return node


def require_data_path(data: Mapping[str, Any], path: Any) -> Any:
    segments = split_data_path(path)
    return require_data_segments(data, segments, path=str(path))


def require_data_segments(
    data: Mapping[str, Any],
    segments: tuple[str, ...],
    *,
    path: str | None = None,
) -> Any:
    """Require a DataKey whose validated path segments were compiled earlier."""
    value = get_data_segments(data, segments, _MISSING)
    if value is _MISSING:
        label = path if path is not None else ".".join(segments)
        raise ValueError(f"Required DataKey path '{label}' is missing from the data JSON.")
    return value


def set_data_path(data: Dict[str, Any], path: Any, value: Any) -> None:
    parts = split_data_path(path)
    set_data_segments(data, parts, value)


def set_data_segments(
    data: Dict[str, Any],
    parts: tuple[str, ...],
    value: Any,
) -> None:
    """Write a DataKey whose validated path segments were compiled earlier."""
    node = data
    for index, segment in enumerate(parts[:-1]):
        existing = node.setdefault(segment, {})
        if not isinstance(existing, dict):
            raise ValueError(
                f"DataKey path '{'.'.join(parts)}' collides with non-object path "
                f"'{'.'.join(parts[:index + 1])}'."
            )
        node = existing
    leaf = parts[-1]
    existing = node.get(leaf, _MISSING)
    if existing is not _MISSING and isinstance(existing, Mapping) != isinstance(value, Mapping):
        raise ValueError(f"DataKey path '{'.'.join(parts)}' changes JSON structure.")
    node[leaf] = value


def set_data_segments_copy_on_write(
    data: Dict[str, Any],
    parts: tuple[str, ...],
    value: Any,
) -> None:
    """Write one DataKey while detaching every traversed object ancestor.

    This is the narrow ownership boundary used when a current-cycle Data Dict
    may still share projected object values with another lifecycle owner.  It
    copies only dictionaries on the written path, not unrelated subtrees.
    """
    if not isinstance(data, dict):
        raise ValueError("Copy-on-write DataKey destination must be an object.")
    node = data
    for index, segment in enumerate(parts[:-1]):
        existing = node.get(segment, _MISSING)
        if existing is _MISSING:
            copied = {}
        elif type(existing) is dict:
            copied = existing.copy()
        elif isinstance(existing, Mapping):
            copied = dict(existing)
        else:
            raise ValueError(
                f"DataKey path '{'.'.join(parts)}' collides with non-object path "
                f"'{'.'.join(parts[:index + 1])}'."
            )
        node[segment] = copied
        node = copied
    leaf = parts[-1]
    existing = node.get(leaf, _MISSING)
    if existing is not _MISSING and isinstance(existing, Mapping) != isinstance(value, Mapping):
        raise ValueError(f"DataKey path '{'.'.join(parts)}' changes JSON structure.")
    node[leaf] = value


def delete_data_segments_copy_on_write(
    data: Dict[str, Any],
    parts: tuple[str, ...],
) -> None:
    """Delete one selected path without mutating shared Observation objects."""

    if not isinstance(data, dict):
        raise ValueError("Copy-on-write DataKey destination must be an object.")
    node = data
    for segment in parts[:-1]:
        existing = node.get(segment, _MISSING)
        if existing is _MISSING or not isinstance(existing, Mapping):
            return
        copied = existing.copy() if type(existing) is dict else dict(existing)
        node[segment] = copied
        node = copied
    node.pop(parts[-1], None)


def compile_data_path_plan(paths: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Compile and deduplicate a stable set of validated DataKey paths."""
    return tuple(
        (path, split_data_path(path))
        for path in dict.fromkeys(str(item) for item in (paths or ()))
    )


def project_data_paths(
    data: Mapping[str, Any],
    paths: Sequence[Any],
    *,
    isolate_values: bool = True,
) -> Dict[str, Any]:
    """Select requested DataKeys, optionally isolating values for an external owner."""
    return project_compiled_data_paths(
        data,
        compile_data_path_plan(paths),
        isolate_values=isolate_values,
    )


def project_compiled_data_paths(
    data: Mapping[str, Any],
    plan: Sequence[tuple[str, tuple[str, ...]]],
    *,
    isolate_values: bool = True,
) -> Dict[str, Any]:
    """Select DataKeys using a path plan compiled before the cycle loop."""
    result: Dict[str, Any] = {}
    for _path, parts in plan:
        value = get_data_segments(data, parts, _MISSING)
        if value is not _MISSING:
            set_data_segments(result, parts, deepcopy(value) if isolate_values else value)
    return result
