"""Conservative compatibility proofs for normalized DataKey schemas."""

from collections.abc import Mapping
from typing import Any

import engine.contracts.data_model as data_model_contract


__all__ = (
    "normalized_schemas_compatible",
    "normalized_schemas_disjoint",
    "schemas_compatible",
)


def schemas_compatible(provided: Any, required: Any) -> bool:
    """Return whether every value accepted by ``provided`` satisfies ``required``."""
    source = data_model_contract.normalize_schema(provided)
    target = data_model_contract.normalize_schema(required)
    return normalized_schemas_compatible(source, target)


def normalized_schemas_compatible(source: Any, target: Any) -> bool:
    """Sound, conservative subset proof for two normalized schemas.

    JSON Schema composition keywords are conjunctions with every sibling
    keyword.  Source clauses deliberately treat ``oneOf`` as the wider
    ``anyOf`` set; target clauses retain the exclusions.  This may decline a
    proof for an unusually complex but valid subset, but it cannot approve a
    producer value that the consumer rejects.
    """
    if data_model_contract.json_values_equal(source, target):
        return True
    if source is False:
        return True
    if target is False:
        return source is False
    if not target:
        return True
    if not source:
        return False

    return _source_context_subset_schema(_source_context(source), target)


def _schema_base(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {
        keyword: value
        for keyword, value in schema.items()
        if keyword not in {"anyOf", "oneOf", "allOf"}
    }


def _source_context(schema):
    """Flatten conjunctions while keeping unions symbolic and bounded."""
    atoms = []
    unions = []

    def add(item):
        if item is False:
            atoms.append(False)
            return
        base = _schema_base(item)
        raw_type = base.get("type")
        if isinstance(raw_type, list):
            # JSON Schema object/array keywords are conditional on the runtime
            # type.  Distribute the complete base across its declared types;
            # treating type-only branches as a sibling union would incorrectly
            # apply object assertions to a legal null/scalar branch.
            branch_base = dict(base)
            branch_base.pop("type")
            unions.append((
                "anyOf",
                tuple({**branch_base, "type": value} for value in raw_type),
            ))
            atoms.append({})
        else:
            atoms.append(base)
        for branch in item.get("allOf", ()):
            add(branch)
        for keyword in ("anyOf", "oneOf"):
            if keyword in item:
                unions.append((keyword, tuple(item[keyword])))

    add(schema)
    return tuple(atoms), tuple(unions)


def _target_context(schema):
    atoms = []
    unions = []

    def add(item):
        if item is False:
            atoms.append(False)
            return
        atoms.append(_schema_base(item))
        for branch in item.get("allOf", ()):
            add(branch)
        for keyword in ("anyOf", "oneOf"):
            if keyword in item:
                unions.append((keyword, tuple(item[keyword])))

    add(schema)
    return tuple(atoms), tuple(unions)


def _source_context_literals(context):
    atoms, unions = context
    for schema in atoms:
        if schema is False:
            return []
        if "const" in schema:
            candidates = [schema["const"]]
            break
        if "enum" in schema:
            candidates = schema["enum"]
            break
    else:
        return None
    return [
        candidate
        for candidate in candidates
        if all(_schema_accepts_normalized_value(schema, candidate) for schema in atoms)
        and all(
            _schema_accepts_normalized_value({keyword: list(branches)}, candidate)
            for keyword, branches in unions
        )
    ]


def _source_context_is_empty(context) -> bool:
    atoms, _unions = context
    if any(schema is False for schema in atoms):
        return True
    literals = _source_context_literals(context)
    if literals is not None:
        return not literals
    return any(
        normalized_schemas_disjoint(left, right)
        for index, left in enumerate(atoms)
        for right in atoms[index + 1:]
    )


def _context_with_branch(atoms, branch):
    branch_atoms, branch_unions = _source_context(branch)
    return atoms + branch_atoms, branch_unions


def _source_context_subset_atom(context, target) -> bool:
    atoms, unions = context
    if _source_context_is_empty(context):
        return True
    literals = _source_context_literals(context)
    if literals is not None:
        return all(_schema_accepts_normalized_value(target, value) for value in literals)
    if any(_plain_schema_subset(source, target) for source in atoms):
        return True

    # Separate type constraints in an allOf can collectively narrow the source.
    target_constraints = set(target) - data_model_contract.ANNOTATION_KEYS
    if target_constraints <= {"type"}:
        possible = set(data_model_contract.JSON_TYPES)
        for source in atoms:
            source_types = data_model_contract.normalized_schema_types(source)
            if "number" in source_types:
                source_types = source_types | {"integer"}
            possible &= source_types
        target_types = data_model_contract.normalized_schema_types(target)
        if "number" in target_types:
            target_types = target_types | {"integer"}
        if possible <= target_types:
            return True

    # For A & (B | C), proving both A&B and A&C against the target is
    # sufficient.  Other sibling unions are omitted from each branch, making
    # this a conservative superset proof without a Cartesian expansion.
    for _keyword, branches in unions:
        if all(
            _source_context_subset_atom(_context_with_branch(atoms, branch), target)
            for branch in branches
        ):
            return True
    return False


def _source_context_disjoint_schema(context, target) -> bool:
    atoms, unions = context
    if _source_context_is_empty(context):
        return True
    literals = _source_context_literals(context)
    if literals is not None:
        return all(not _schema_accepts_normalized_value(target, value) for value in literals)
    if any(normalized_schemas_disjoint(source, target) for source in atoms):
        return True
    return any(
        all(
            _source_context_disjoint_schema(_context_with_branch(atoms, branch), target)
            for branch in branches
        )
        for _keyword, branches in unions
    )


def _source_context_subset_union(context, keyword, branches) -> bool:
    literals = _source_context_literals(context)
    if literals is not None:
        union_schema = {keyword: list(branches)}
        return all(
            _schema_accepts_normalized_value(union_schema, literal)
            for literal in literals
        )

    if keyword == "anyOf":
        if any(_source_context_subset_schema(context, branch) for branch in branches):
            return True
    else:
        for index, branch in enumerate(branches):
            if (
                _source_context_subset_schema(context, branch)
                and all(
                    other_index == index
                    or _source_context_disjoint_schema(context, other)
                    for other_index, other in enumerate(branches)
                )
            ):
                return True

    atoms, unions = context
    for _source_keyword, source_branches in unions:
        if all(
            _source_context_subset_union(
                _context_with_branch(atoms, branch), keyword, branches
            )
            for branch in source_branches
        ):
            return True
    return False


def _source_context_subset_schema(context, target) -> bool:
    target_atoms, target_unions = _target_context(target)
    return (
        all(
            _source_context_subset_atom(context, target_atom)
            for target_atom in target_atoms
        )
        and all(
            _source_context_subset_union(context, keyword, branches)
            for keyword, branches in target_unions
        )
    )


def _plain_schema_subset(source: Any, target: Any) -> bool:
    """Subset comparison for composition-free outer schemas."""
    if data_model_contract.json_values_equal(source, target) or source is False:
        return True
    if target is False:
        return False
    if not target:
        return True
    if not source:
        return False

    source_literals = None
    if "const" in source:
        source_literals = [source["const"]]
    elif "enum" in source:
        source_literals = source["enum"]
    if source_literals is not None:
        return all(
            _schema_accepts_normalized_value(target, literal)
            for literal in source_literals
        )
    if "const" in target or "enum" in target:
        return False

    source_types = data_model_contract.normalized_schema_types(source)
    target_types = data_model_contract.normalized_schema_types(target)
    for source_type in source_types:
        if source_type == "integer" and "number" in target_types:
            continue
        if source_type not in target_types:
            return False

    if "object" in source_types and "object" in target_types:
        source_properties = source.get("properties", {})
        target_properties = target.get("properties", {})
        source_required = set(source.get("required", []))
        source_extra = source.get("additionalProperties", True)
        target_extra = target.get("additionalProperties", True)
        for name in target.get("required", []):
            if name not in source_required:
                return False
            # JSON Schema permits a required property to be typed solely by
            # ``additionalProperties``.  Treat that concrete map value schema
            # exactly like an explicitly named property.
            source_property = source_properties.get(name, source_extra)
            target_property = target_properties.get(name, target_extra)
            if source_property is False or target_property is False:
                return False
            if not normalized_schemas_compatible(
                source_property, target_property
            ):
                return False
        for name, source_property in source_properties.items():
            target_property = target_properties.get(name, target_extra)
            if target_property is False:
                return False
            if target_property is not True and not normalized_schemas_compatible(
                source_property, target_property
            ):
                return False
        if source_extra is not False:
            # An additional-property schema can also produce a key that the
            # target names explicitly, so it must satisfy those contracts too.
            for name, target_property in target_properties.items():
                if name in source_properties:
                    continue
                if source_extra is True or not normalized_schemas_compatible(
                    source_extra, target_property
                ):
                    return False
            # It can additionally produce arbitrary other keys.
            if target_extra is False:
                return False
            if target_extra is not True and (
                source_extra is True
                or not normalized_schemas_compatible(source_extra, target_extra)
            ):
                return False
    if "array" in source_types and "array" in target_types and "items" in target:
        if "items" not in source or not normalized_schemas_compatible(
            source["items"], target["items"]
        ):
            return False
    return True


def _schema_accepts_normalized_value(schema, value):
    try:
        data_model_contract.validate_normalized_json_value(
            value,
            schema,
            path="Schema compatibility",
        )
        return True
    except ValueError:
        return False


def normalized_schemas_disjoint(left, right):
    """Return true only when the supported schema subset proves no shared value."""

    if left is False or right is False:
        return True
    if not left or not right:
        return False
    if "const" in left:
        return not _schema_accepts_normalized_value(right, left["const"])
    if "const" in right:
        return not _schema_accepts_normalized_value(left, right["const"])
    if "enum" in left:
        return all(
            not _schema_accepts_normalized_value(right, value)
            for value in left["enum"]
        )
    if "enum" in right:
        return all(
            not _schema_accepts_normalized_value(left, value)
            for value in right["enum"]
        )
    if "anyOf" in left or "oneOf" in left:
        branches = left.get("anyOf", left.get("oneOf"))
        return all(normalized_schemas_disjoint(branch, right) for branch in branches)
    if "anyOf" in right or "oneOf" in right:
        branches = right.get("anyOf", right.get("oneOf"))
        return all(normalized_schemas_disjoint(left, branch) for branch in branches)
    if "allOf" in left and any(
        normalized_schemas_disjoint(branch, right) for branch in left["allOf"]
    ):
        return True
    if "allOf" in right and any(
        normalized_schemas_disjoint(left, branch) for branch in right["allOf"]
    ):
        return True
    left_types = data_model_contract.normalized_schema_types(left)
    right_types = data_model_contract.normalized_schema_types(right)

    def overlaps(left_type, right_type):
        return (
            left_type == right_type
            or {left_type, right_type} == {"integer", "number"}
        )

    overlapping_type_pairs = [
        (left_type, right_type)
        for left_type in left_types
        for right_type in right_types
        if overlaps(left_type, right_type)
    ]
    if not overlapping_type_pairs:
        return True
    object_is_only_overlap = all(
        left_type == right_type == "object"
        for left_type, right_type in overlapping_type_pairs
    )
    if object_is_only_overlap:
        left_properties = left.get("properties", {})
        right_properties = right.get("properties", {})
        left_required = set(left.get("required", []))
        right_required = set(right.get("required", []))
        for name in left_required & right_required:
            left_property = left_properties.get(name, left.get("additionalProperties", True))
            right_property = right_properties.get(name, right.get("additionalProperties", True))
            if left_property is False or right_property is False:
                return True
            if (
                left_property is not True
                and right_property is not True
                and normalized_schemas_disjoint(left_property, right_property)
            ):
                return True
        for name in left_required - set(right_properties):
            if right.get("additionalProperties", True) is False:
                return True
        for name in right_required - set(left_properties):
            if left.get("additionalProperties", True) is False:
                return True
    return False
