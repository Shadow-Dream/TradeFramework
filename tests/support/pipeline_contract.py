from unittest import mock

from engine.compiler.graph import compile_module_graph


__all__ = ("compiled_graph", "definition", "instance")


def definition(module_id, *, inputs=None, outputs=None, kind="Signal"):
    normalized_inputs = {
        name: {**spec, "required": spec.get("required", True)}
        for name, spec in (inputs or {}).items()
    }
    normalized_outputs = {
        name: {**spec, "required": spec.get("required", True)}
        for name, spec in (outputs or {}).items()
    }
    return {
        "kind": kind,
        "moduleId": module_id,
        "name": module_id,
        "activationMode": "PythonModule",
        "parameters": {},
        "version": "1",
        "status": "archived",
        "builtin": False,
        "description": "Archived Module compiler fixture.",
        "ports": {
            "inputs": normalized_inputs,
            "outputs": normalized_outputs,
        },
        "configSchema": {"type": "object", "additionalProperties": False},
        "archive": {
            "resourceType": "module",
            "resourceId": f"{kind}/{module_id}",
        },
    }


def instance(instance_id, module_id, *, inputs=None, outputs=None, kind="Signal"):
    return {
        "instanceId": instance_id,
        "kind": kind,
        "moduleId": module_id,
        "version": "1",
        "config": {},
        "inputs": inputs or {},
        "outputs": outputs or {},
    }


def compiled_graph(graph, instances, definitions, initial=None):
    with mock.patch(
        "engine.archive.version.verify_record",
        side_effect=lambda record: record,
    ):
        return compile_module_graph(
            graph,
            instances,
            definitions,
            initial or {},
            allowed_kinds={"Signal"},
            label="Signal Graph",
        )
