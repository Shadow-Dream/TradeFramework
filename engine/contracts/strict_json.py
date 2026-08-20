#!/usr/bin/env python3
"""Strict, deterministic JSON boundaries for Engine protocols and sealed data."""

from __future__ import annotations

import json
import math

try:
    import orjson as _orjson
except ImportError:  # The strict standard-library boundary remains portable.
    _orjson = None


_NONE_TYPE = type(None)
_DEEP_EXACT_JSON = object()
# Leave enough recursion headroom to preserve the legacy validator/encoder
# failure boundary for unusually deep, otherwise valid JSON values.
_MAX_FAST_CONTAINER_DEPTH = 64


def _is_exact_json_container(value, container_depth):
    if container_depth >= _MAX_FAST_CONTAINER_DEPTH:
        return _DEEP_EXACT_JSON
    if type(value) is dict:
        for key in value:
            if type(key) is not str:
                return False
        children = value.values()
    else:
        children = value
    for item in children:
        item_type = type(item)
        if (
            item_type is str
            or item_type is int
            or item_type is bool
            or item_type is _NONE_TYPE
        ):
            continue
        if item_type is float:
            if not math.isfinite(item):
                return False
            continue
        if item_type is list or item_type is dict:
            outcome = _is_exact_json_container(item, container_depth + 1)
            if outcome is not True:
                return outcome
            continue
        return False
    return True


def _is_exact_json_tree(value, container_depth, active, all_seen):
    """Prove exact JSON while rejecting cycles and shared containers.

    This stricter traversal is intentionally separate from the established
    fast predicate so ordinary hot-path callers keep their original cost.
    """

    if container_depth >= _MAX_FAST_CONTAINER_DEPTH:
        return _DEEP_EXACT_JSON
    identity = id(value)
    if identity in active or identity in all_seen:
        return False
    active.add(identity)
    all_seen.add(identity)
    try:
        if type(value) is dict:
            for key in value:
                if type(key) is not str:
                    return False
            children = value.values()
        else:
            children = value
        for item in children:
            item_type = type(item)
            if (
                item_type is str
                or item_type is int
                or item_type is bool
                or item_type is _NONE_TYPE
            ):
                continue
            if item_type is float:
                if not math.isfinite(item):
                    return False
                continue
            if item_type is list or item_type is dict:
                outcome = _is_exact_json_tree(
                    item,
                    container_depth + 1,
                    active,
                    all_seen,
                )
                if outcome is not True:
                    return outcome
                continue
            return False
        return True
    finally:
        active.remove(identity)


def _is_exact_json(value):
    """Validate the ordinary exact JSON types without constructing error paths."""
    value_type = type(value)
    if (
        value_type is str
        or value_type is int
        or value_type is bool
        or value_type is _NONE_TYPE
    ):
        return True
    if value_type is float:
        return math.isfinite(value)
    if value_type is list or value_type is dict:
        return _is_exact_json_container(value, 0)
    return False


def is_exact_json(value, *, reject_aliases=False):
    """Return a success proof for ordinary finite built-in JSON values.

    ``False`` is deliberately inconclusive: callers which need authoritative
    diagnostics must still use :func:`validate` or :func:`dumps`.
    """
    try:
        if not reject_aliases:
            return _is_exact_json(value) is True
        value_type = type(value)
        if value_type is list or value_type is dict:
            return _is_exact_json_tree(value, 0, set(), set()) is True
        return _is_exact_json(value) is True
    except RecursionError:
        return False


def exact_equal(left, right):
    """Compare two strict JSON values without numeric type coercion.

    The optional encoder is only used after both trees have passed the exact
    built-in JSON proof.  Unsupported-but-valid values (for example integers
    wider than 64 bits or lone surrogates) and every invalid value fall back to
    the established strict encoder, preserving its left-before-right error
    type, message, and ordering.
    """

    if (
        _orjson is not None
        and is_exact_json(left)
        and is_exact_json(right)
    ):
        try:
            return _orjson.dumps(
                left, option=_orjson.OPT_SORT_KEYS
            ) == _orjson.dumps(
                right, option=_orjson.OPT_SORT_KEYS
            )
        except (TypeError, ValueError, OverflowError, RecursionError):
            pass
    return dumps(
        left, sort_keys=True, separators=(",", ":")
    ) == dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def validate(value, *, path="value"):
    """Preserve detailed strict errors for public and diagnostic validation."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers.")
        return value
    if type(value) is list:
        for index, item in enumerate(value):
            validate(item, path=f"{path}[{index}]")
        return value
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} object keys must be strings.")
            validate(
                item,
                path=f"{path}.{key}" if key else f"{path}.<empty>",
            )
        return value
    raise ValueError(
        f"{path} contains a non-JSON value of type {type(value).__name__}."
    )


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"JSON number is outside the finite range: {value}")
    return result


def _object_from_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def loads(value):
    return json.loads(
        value,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        object_pairs_hook=_object_from_pairs,
    )


def load(handle):
    return json.load(
        handle,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        object_pairs_hook=_object_from_pairs,
    )


def decoder():
    """Create the same strict decoder for incremental JSON boundaries."""
    return json.JSONDecoder(
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        object_pairs_hook=_object_from_pairs,
    )


def dumps(value, **kwargs):
    try:
        exact_json = _is_exact_json(value)
    except RecursionError:
        # Re-run the original validator from the original call depth so deep
        # and recursive values preserve its exact exception behavior.
        exact_json = _DEEP_EXACT_JSON
    deep_value = exact_json is _DEEP_EXACT_JSON
    if exact_json is not True:
        validate(value)
    # The exact traversal or detailed validator has rejected circular values.
    # Preserve an explicit caller choice while avoiding duplicate bookkeeping
    # for the ordinary successful boundary.  Near the recursion limit, retain
    # the old encoder check so its RecursionError text remains unchanged.
    custom_encoder = kwargs.get("cls") is not None
    kwargs.setdefault("check_circular", True if custom_encoder else deep_value)
    return json.dumps(value, allow_nan=False, **kwargs)
