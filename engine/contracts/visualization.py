"""Strict aggregate contract for saved Result visualizations."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engine.contracts.exact_fields import require_exact_fields
from engine.contracts.module import MODULE_INSTANCE_FIELDS


VISUALIZATION_SAVE_REQUEST_FIELDS = frozenset({
    "backtestId",
    "visualizationId",
    "name",
    "spec",
})
VISUALIZATION_RECORD_FIELDS = frozenset({
    "visualizationId",
    "backtestId",
    "name",
    "createdAt",
    "spec",
})


def require_save_request(request):
    """Require the exact caller protocol for saving one Visualization."""

    require_exact_fields(
        request,
        allowed=VISUALIZATION_SAVE_REQUEST_FIELDS,
        required=VISUALIZATION_SAVE_REQUEST_FIELDS,
        label="Visualization save request",
    )
    if not isinstance(request["backtestId"], str) or not request["backtestId"]:
        raise ValueError("Visualization backtestId is required.")
    if (
        not isinstance(request["visualizationId"], str)
        or not request["visualizationId"].strip()
    ):
        raise ValueError("Visualization visualizationId is required.")
    if not isinstance(request["name"], str) or not request["name"].strip():
        raise ValueError("Visualization name is required.")
    require_spec(request["spec"])
    return request


def require_record(record):
    """Require the exact durable/public Visualization record envelope."""

    require_exact_fields(
        record,
        allowed=VISUALIZATION_RECORD_FIELDS,
        required=VISUALIZATION_RECORD_FIELDS,
        label="Visualization record",
    )
    for field, label in (
        ("visualizationId", "visualizationId"),
        ("backtestId", "backtestId"),
        ("name", "name"),
        ("createdAt", "createdAt"),
    ):
        if not isinstance(record[field], str) or not record[field].strip():
            raise ValueError(f"Visualization {label} must be a non-empty string.")
    require_spec(record["spec"])
    return record


def default_spec(dataset_id, time_zone):
    """Build the empty Visualization spec for a verified Dataset identity."""

    spec = {
        "schemaVersion": 3,
        "datasetId": dataset_id,
        "timeZone": time_zone,
        "panes": [],
    }
    return require_spec(spec)


def require_spec(spec):
    require_exact_fields(
        spec,
        allowed={"schemaVersion", "datasetId", "timeZone", "panes", "temporaryModules"},
        required={"schemaVersion", "datasetId", "timeZone", "panes"},
        label="Visualization spec",
    )
    if spec["schemaVersion"] != 3:
        raise ValueError("Visualization schemaVersion 3 is required.")
    if not isinstance(spec["datasetId"], str) or not spec["datasetId"].strip():
        raise ValueError("Visualization datasetId is required.")
    if not isinstance(spec["timeZone"], str) or not spec["timeZone"].strip():
        raise ValueError("Visualization timeZone must be a non-empty IANA time zone.")
    try:
        ZoneInfo(spec["timeZone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Visualization timeZone must be a valid IANA time zone.") from exc
    if not isinstance(spec["panes"], list):
        raise ValueError("Visualization panes must be an array.")
    if "temporaryModules" in spec and not isinstance(spec["temporaryModules"], list):
        raise ValueError("Visualization temporaryModules must be an array.")
    for index, module in enumerate(spec.get("temporaryModules") or []):
        require_exact_fields(
            module,
            allowed=MODULE_INSTANCE_FIELDS,
            required=MODULE_INSTANCE_FIELDS,
            label=f"Visualization temporaryModules[{index}]",
        )
    pane_ids = set()
    for pane_index, pane in enumerate(spec["panes"]):
        require_exact_fields(
            pane,
            allowed={
                "id", "title", "role", "view", "visualizers",
                "temporaryModules", "collapsed",
            },
            required={"id", "title", "role", "view", "visualizers", "temporaryModules"},
            label=f"Visualization panes[{pane_index}]",
        )
        pane_id = pane["id"]
        if not isinstance(pane_id, str) or not pane_id or pane_id in pane_ids:
            raise ValueError("Visualization Pane IDs must be unique non-empty strings.")
        pane_ids.add(pane_id)
        if not all(
            isinstance(pane[field], str) and pane[field]
            for field in ("title", "role")
        ):
            raise ValueError(
                f"Visualization Pane '{pane_id}' title and role must be non-empty strings."
            )
        if "collapsed" in pane and not isinstance(pane["collapsed"], bool):
            raise ValueError(
                f"Visualization Pane '{pane_id}' collapsed must be a boolean."
            )
        require_exact_fields(
            pane["view"],
            allowed={"start", "end", "logScale", "controlsCollapsed"},
            required={"start", "end", "logScale", "controlsCollapsed"},
            label=f"Visualization Pane '{pane_id}' view",
        )
        if not isinstance(pane["view"]["logScale"], bool) or not isinstance(
            pane["view"]["controlsCollapsed"], bool
        ):
            raise ValueError(
                f"Visualization Pane '{pane_id}' view flags must be booleans."
            )
        for field in ("start", "end"):
            value = pane["view"][field]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (str, int, float))
            ):
                raise ValueError(
                    f"Visualization Pane '{pane_id}' view.{field} must be null, "
                    "a string, or a number."
                )
        if not isinstance(pane["visualizers"], list) or not isinstance(
            pane["temporaryModules"], list
        ):
            raise ValueError(f"Visualization Pane '{pane_id}' layers must be arrays.")
        visualizer_ids = set()
        for index, visualizer in enumerate(pane["visualizers"]):
            require_exact_fields(
                visualizer,
                allowed={"id", "callback", "params", "visible"},
                required={"id", "callback", "params"},
                label=f"Visualization Pane '{pane_id}' visualizers[{index}]",
            )
            if not all(
                isinstance(visualizer[field], str) and visualizer[field]
                for field in ("id", "callback")
            ):
                raise ValueError(
                    f"Visualization Pane '{pane_id}' Visualizer identity is invalid."
                )
            if visualizer["id"] in visualizer_ids:
                raise ValueError(
                    f"Visualization Pane '{pane_id}' contains duplicate Visualizer ID "
                    f"'{visualizer['id']}'."
                )
            visualizer_ids.add(visualizer["id"])
            if not isinstance(visualizer["params"], dict):
                raise ValueError(f"Visualizer '{visualizer['id']}' params must be an object.")
            if "visible" in visualizer and not isinstance(visualizer["visible"], bool):
                raise ValueError(f"Visualizer '{visualizer['id']}' visible must be a boolean.")
        for index, module in enumerate(pane["temporaryModules"]):
            require_exact_fields(
                module,
                allowed=MODULE_INSTANCE_FIELDS,
                required=MODULE_INSTANCE_FIELDS,
                label=f"Visualization Pane '{pane_id}' temporaryModules[{index}]",
            )
    return spec


__all__ = (
    "VISUALIZATION_RECORD_FIELDS",
    "VISUALIZATION_SAVE_REQUEST_FIELDS",
    "default_spec",
    "require_record",
    "require_save_request",
    "require_spec",
)
