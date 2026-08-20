"""Generic field contracts shared by versioned Cycle Graph resources."""


GRAPH_RESOURCE_COMMON_DRAFT_FIELDS = frozenset({
    "schemaVersion",
    "name",
    "description",
    "instances",
    "graph",
})
GRAPH_RESOURCE_ARCHIVE_FIELDS = frozenset({
    "version",
    "builtin",
    "status",
    "contentDigest",
    "createdAt",
    "archive",
})


def graph_resource_draft_fields(identity_field):
    if not isinstance(identity_field, str) or not identity_field:
        raise ValueError("Cycle Graph identity field must be a non-empty string.")
    return GRAPH_RESOURCE_COMMON_DRAFT_FIELDS | frozenset({identity_field})


__all__ = (
    "GRAPH_RESOURCE_ARCHIVE_FIELDS",
    "GRAPH_RESOURCE_COMMON_DRAFT_FIELDS",
    "graph_resource_draft_fields",
)
