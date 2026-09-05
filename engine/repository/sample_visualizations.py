"""CAS repository for Visualizations over immutable Sample Results."""

from __future__ import annotations

import copy

from engine.contracts import sample_visualization as contracts
from engine.repository import control_state


_STATE = {"schemaVersion": 1, "visualizations": []}


class SampleVisualizationRevisionConflict(RuntimeError):
    code = "sample_visualization_revision_conflict"

    def __init__(self, visualization_id, expected_revision, current):
        self.visualization_id = visualization_id
        self.expected_revision = expected_revision
        self.current = copy.deepcopy(current)
        actual = "absent" if current is None else current["revision"]
        super().__init__(
            f"Sample Visualization '{visualization_id}' revision conflict: "
            f"expected {expected_revision}, current {actual}."
        )


def _load(config):
    value = control_state.load_state(config, "sample-visualizations.json", _STATE)
    if type(value) is not dict or set(value) != {"schemaVersion", "visualizations"}:
        raise ValueError("Sample Visualization repository state is invalid.")
    if value["schemaVersion"] != 1 or type(value["visualizations"]) is not list:
        raise ValueError("Sample Visualization repository schema is invalid.")
    records = [copy.deepcopy(contracts.require_record(item)) for item in value["visualizations"]]
    identities = [item["visualizationId"] for item in records]
    if len(identities) != len(set(identities)):
        raise ValueError("Sample Visualization repository contains duplicate IDs.")
    return records


def save_sample_visualization(config, record, *, expected_revision):
    contracts.require_record(record)
    with control_state.control_state_lock(config):
        records = _load(config)
        current = next(
            (item for item in records if item["visualizationId"] == record["visualizationId"]),
            None,
        )
        actual_revision = 0 if current is None else current["revision"]
        if actual_revision != expected_revision:
            raise SampleVisualizationRevisionConflict(
                record["visualizationId"], expected_revision, current
            )
        if current is not None and current["sampleResultId"] != record["sampleResultId"]:
            raise ValueError("Sample Visualization cannot change Sample Result identity.")
        updated = [
            item for item in records if item["visualizationId"] != record["visualizationId"]
        ]
        updated.append(copy.deepcopy(record))
        updated.sort(key=lambda item: (item["createdAt"], item["visualizationId"]), reverse=True)
        control_state.save_state(config, "sample-visualizations.json", {
            "schemaVersion": 1,
            "visualizations": updated,
        })
    return copy.deepcopy(record)


def list_sample_visualizations(config, sample_result_id=""):
    with control_state.control_state_lock(config):
        records = _load(config)
    if sample_result_id:
        records = [item for item in records if item["sampleResultId"] == sample_result_id]
    return records


__all__ = (
    "SampleVisualizationRevisionConflict",
    "list_sample_visualizations",
    "save_sample_visualization",
)
