"""Application templates which produce ordinary, user-owned Pipeline drafts."""

from __future__ import annotations

import copy
import math
import re

MODULE_REQUIREMENTS = {
    "universe": ("Universe", "basic-price-map-universe"),
    "signal": ("Signal", "basic-neutral-score-map"),
    "target": ("Target", "basic-score-map-position-target"),
    "constraint": ("Constraint", "basic-absolute-position-map-constraint"),
}

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _definition_index(definitions):
    if type(definitions) not in {list, tuple}:
        raise ValueError("Pipeline scaffold definitions must be an array.")
    indexed = {}
    for definition in definitions:
        if type(definition) is not dict:
            raise ValueError("Pipeline scaffold Module definitions must be objects.")
        kind = definition.get("kind")
        module_id = definition.get("moduleId")
        version = definition.get("version")
        digest = definition.get("contentDigest")
        if (
            type(kind) is not str
            or not kind
            or type(module_id) is not str
            or not module_id
            or type(version) is not str
            or not version.isascii()
            or not version.isdecimal()
            or version != str(int(version))
            or int(version) < 1
            or type(digest) is not str
            or not _SHA256.fullmatch(digest)
        ):
            raise ValueError(
                "Pipeline scaffold Module definitions require exact identity and digest."
            )
        if definition.get("builtin") is not True:
            continue
        key = (kind, module_id)
        current = indexed.get(key)
        if current is not None:
            raise ValueError(f"Pipeline scaffold received duplicate Module: {kind}/{module_id}")
        indexed[key] = definition
    return indexed


def _instance(instance_id, definition, *, config, inputs, outputs):
    return {
        "instanceId": instance_id,
        "kind": definition["kind"],
        "moduleId": definition["moduleId"],
        "version": definition["version"],
        "config": copy.deepcopy(config),
        "inputs": copy.deepcopy(inputs),
        "outputs": copy.deepcopy(outputs),
    }


def build_pipeline_scaffold(
    pipeline_id,
    name,
    definitions,
    *,
    decision_period,
    position_scale=1.0,
    maximum_absolute_position=1.0,
):
    """Return an ordinary Pipeline draft with a replaceable Signal Graph."""

    if type(pipeline_id) is not str or not pipeline_id:
        raise ValueError("Pipeline scaffold pipeline_id must be a non-empty string.")
    if type(name) is not str or not name:
        raise ValueError("Pipeline scaffold name must be a non-empty string.")
    if (
        type(decision_period) is not str
        or not re.fullmatch(r"[A-Za-z0-9_-]+", decision_period)
    ):
        raise ValueError("Pipeline scaffold decision_period is invalid.")
    for label, value in (
        ("position_scale", position_scale),
        ("maximum_absolute_position", maximum_absolute_position),
    ):
        if (
            type(value) not in {int, float}
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ValueError(f"Pipeline scaffold {label} must be numeric.")
        if value < 0:
            raise ValueError(f"Pipeline scaffold {label} cannot be negative.")
    indexed = _definition_index(definitions)
    resolved = {}
    for role, key in MODULE_REQUIREMENTS.items():
        definition = indexed.get(key)
        if definition is None:
            raise ValueError(
                f"Pipeline scaffold requires exact BuiltIn Module {key[0]}/{key[1]}."
            )
        resolved[role] = definition

    instances = {
        "universe": _instance(
            "universe",
            resolved["universe"],
            config={"decisionPeriod": decision_period},
            inputs={"price": "price"},
            outputs={"selection": "universe.selected"},
        ),
        "signal": _instance(
            "signal",
            resolved["signal"],
            config={},
            inputs={"selection": "wire.universe.selection"},
            outputs={"scores": "wire.signal.scores"},
        ),
        "target": _instance(
            "target",
            resolved["target"],
            config={"maximumAbsolutePosition": float(position_scale)},
            inputs={"selection": "universe.selected", "scores": "signal.scores"},
            outputs={"intent": "intent.requested"},
        ),
        "constraint": _instance(
            "constraint",
            resolved["constraint"],
            config={"maximumAbsolutePosition": float(maximum_absolute_position)},
            inputs={"intent": "intent.requested"},
            outputs={"approved": "intent.approved"},
        ),
    }
    return {
        "pipelineId": pipeline_id,
        "name": name,
        "config": {
            "observationInput": {
                "whitelist": [
                    "execution.orders",
                    "price",
                    "portfolio.account",
                    "time",
                ],
                "blacklist": [],
            }
        },
        "instances": instances,
        "stages": {
            "universe": ["universe"],
            "target": ["target"],
            "constraint": ["constraint"],
        },
        "signalGraph": {
            "nodes": ["signal"],
            "inputs": {
                "selection-input": {
                    "dataKey": "universe.selected",
                    "wire": "wire.universe.selection",
                }
            },
            "outputs": {
                "scores-output": {
                    "dataKey": "signal.scores",
                    "wire": "wire.signal.scores",
                }
            },
        },
    }


__all__ = ("MODULE_REQUIREMENTS", "build_pipeline_scaffold")
