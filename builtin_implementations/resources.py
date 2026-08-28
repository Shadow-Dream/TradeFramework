"""Install product BuiltIns through Engine's generic immutable archive APIs."""

from __future__ import annotations

import base64
import copy
import importlib
from pathlib import Path

from application_protocols.basic_workflow.manifest import (
    PROTOCOL_ID as BASIC_WORKFLOW_PROTOCOL_ID,
)
from builtin_implementations import analysis_presets
from builtin_implementations import analysis_contracts
from builtin_implementations import environment_contracts
from builtin_implementations import environment_presets
from builtin_implementations.pipeline_contracts import BUILTIN_PIPELINE_MODULES
from builtin_implementations.basic_workflow_contracts import (
    OHLCV_SAMPLER_OUTPUT_SCHEMA,
    SAMPLER_OUTPUT_SCHEMA,
)
from builtin_implementations.sampler.basic_ohlcv_map_sampler import (
    SOURCE as BASIC_OHLCV_SAMPLER_SOURCE,
    ENTRY_POINT as BASIC_OHLCV_SAMPLER_ENTRY_POINT,
)
from builtin_implementations.sampler.basic_price_map_sampler import (
    ENTRY_POINT as BASIC_WORKFLOW_SAMPLER_ENTRY_POINT,
    SOURCE as BASIC_WORKFLOW_SAMPLER_SOURCE,
)
from engine.authority import module_definition as module_definition_authority
from engine.compiler import analysis as analysis_compiler
from engine.compiler import environment as environment_compiler
from engine.repository import graph_resources
from engine.repository import module_definitions
from engine.repository import folders as repository_folders
from engine.repository import samplers
from engine.service import module_publication
from strategy_devkit.bundle import encode_file
from strategy_devkit.module_sdk import Module


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_ROOT = PROJECT_ROOT / "builtin_implementations"
APPLICATION_COMPONENT_ROOT = PROJECT_ROOT / "application_components"
APPLICATION_COMPONENT_FILES = (
    "__init__.py",
    "basic_workflow/__init__.py",
    "basic_workflow/numbers.py",
    "basic_workflow/brokerage.py",
    "basic_workflow/account.py",
)


def _ensure_folder(config, repository, path):
    """Create one virtual classification path without coupling it to storage."""
    path = "/" + "/".join(part for part in str(path).split("/") if part)
    tree = repository_folders.repository_tree(config, repository)
    folders_by_path = {item["path"]: item for item in tree["folders"]}
    if path in folders_by_path:
        return folders_by_path[path]["folderId"]
    parent_id = ""
    current_path = ""
    for name in path.split("/")[1:]:
        current_path += "/" + name
        existing = folders_by_path.get(current_path)
        if existing is None:
            existing = repository_folders.create_folder(
                config, repository, name, parent_id
            )
            folders_by_path[current_path] = existing
        parent_id = existing["folderId"]
    return parent_id


def _place(config, repository, item_id, path, *, definition=None):
    repository_folders.assign_item(
        config,
        repository,
        item_id,
        _ensure_folder(config, repository, path),
        module_definition=definition,
    )


def _implementation_file(repository, module_id):
    filename = str(module_id).replace("-", "_") + ".py"
    source = IMPLEMENTATION_ROOT / repository / filename
    if not source.is_file():
        raise ValueError(
            "BuiltIn Module has no independent implementation source: "
            f"{repository}/{module_id}"
        )
    return source


def _implementation_module(repository, module_id):
    return (
        "builtin_implementations."
        + repository
        + "."
        + str(module_id).replace("-", "_")
    )


def _files(repository, module_id):
    implementation_module = _implementation_module(repository, module_id)
    imported = importlib.import_module(implementation_module)
    implementations = [
        value
        for value in vars(imported).values()
        if (
            isinstance(value, type)
            and issubclass(value, Module)
            and value is not Module
            and value.__module__ == implementation_module
        )
    ]
    if len(implementations) != 1:
        raise ValueError(
            "BuiltIn Module must define exactly one Module class: "
            f"{implementation_module}"
        )
    entry_source = (
        f"from .{implementation_module} import "
        f"{implementations[0].__name__} as MODULE_CLASS\n"
    ).encode("utf-8")
    files = [
        {
            "path": "module.py",
            "contentBase64": base64.b64encode(entry_source).decode("ascii"),
            "executable": False,
        }
    ]
    package_files = [
        (IMPLEMENTATION_ROOT / "__init__.py", "builtin_implementations/__init__.py"),
        (
            IMPLEMENTATION_ROOT / repository / "__init__.py",
            f"builtin_implementations/{repository}/__init__.py",
        ),
    ]
    files.extend(
        encode_file(source, destination) for source, destination in package_files
    )
    files.extend(
        encode_file(
            APPLICATION_COMPONENT_ROOT / relative,
            f"application_components/{relative}",
        )
        for relative in APPLICATION_COMPONENT_FILES
    )
    common = IMPLEMENTATION_ROOT / repository / "common.py"
    if common.is_file():
        files.append(
            encode_file(common, f"builtin_implementations/{repository}/common.py")
        )
    files.append(
        encode_file(
            _implementation_file(repository, module_id),
            "builtin_implementations/"
            f"{repository}/{str(module_id).replace('-', '_')}.py",
        )
    )
    return files


def _sources():
    for definition in BUILTIN_PIPELINE_MODULES:
        yield "pipeline", copy.deepcopy(definition)
    for definition in analysis_contracts.ANALYSIS_MODULES:
        yield "analysis", copy.deepcopy(definition)
    for definition in environment_contracts.ENVIRONMENT_MODULES:
        yield "environment", copy.deepcopy(definition)


def install(config):
    installed = []
    saved_sampler = samplers.save_sampler(
        config,
        {
            "samplerId": "basic-price-map-sampler",
            "name": "Basic",
            "protocolId": BASIC_WORKFLOW_PROTOCOL_ID,
            "type": "python-script",
            "config": {},
            "parameterSchema": {
                "type": "object",
                "properties": {
                    "decisionPeriod": {
                        "type": "string",
                    }
                },
                "additionalProperties": False,
            },
            "outputSchema": copy.deepcopy(SAMPLER_OUTPUT_SCHEMA),
            "source": BASIC_WORKFLOW_SAMPLER_SOURCE,
            "entryPoint": BASIC_WORKFLOW_SAMPLER_ENTRY_POINT,
        },
        engine_owned=True,
    )
    installed.append(saved_sampler)
    _place(
        config,
        "samplers",
        f"{saved_sampler['samplerId']}::{saved_sampler['version']}",
        "/BuiltIn/Basic Workflow",
    )
    saved_v3 = samplers.save_sampler(config, {
        "samplerId": "basic-ohlcv-map-sampler", "name": "Basic OHLCV v3",
        "protocolId": BASIC_WORKFLOW_PROTOCOL_ID, "type": "python-script", "config": {},
        "parameterSchema": {"type": "object", "properties": {"decisionPeriod": {"type": "string"}}, "additionalProperties": False},
        "outputSchema": copy.deepcopy(OHLCV_SAMPLER_OUTPUT_SCHEMA),
        "source": BASIC_OHLCV_SAMPLER_SOURCE, "entryPoint": BASIC_OHLCV_SAMPLER_ENTRY_POINT,
    }, engine_owned=True)
    installed.append(saved_v3)
    _place(config, "samplers", f"{saved_v3['samplerId']}::{saved_v3['version']}", "/BuiltIn/Basic Workflow")
    for repository, source in _sources():
        module_id = source["moduleId"]
        payload = {
            **source,
            "activationMode": "PythonModule",
            "parameters": {},
            "files": _files(repository, module_id),
        }
        payload.pop("version", None)
        result = module_publication.publish_module(
            config,
            payload,
            repository=repository,
            engine_owned=True,
        )
        definition = result["definition"]
        installed.append(definition)
        if module_id.startswith("basic-"):
            catalog_repository = {
                "pipeline": "modules",
                "analysis": "analysis-modules",
                "environment": "environment-modules",
            }[repository]
            prefix = (
                f"/{definition['kind']}/BuiltIn"
                if repository == "pipeline"
                else "/BuiltIn"
            )
            _place(
                config,
                catalog_repository,
                f"{definition['kind']}/{definition['moduleId']}",
                prefix + "/Basic Workflow",
                definition=definition,
            )

    analysis_definitions, analysis_evidence = (
        module_definitions.load_repository_evidence(config, "analysis")
    )
    environment_definitions, environment_evidence = (
        module_definitions.load_repository_evidence(config, "environment")
    )
    graph_sources = [
        *(
            (
                "analysis",
                definition,
                analysis_compiler.validate_analysis_definition_authority,
            )
            for definition in analysis_presets.builtin_analysis_definitions(
                analysis_definitions,
                analysis_evidence,
            ).values()
        ),
        *(
            (
                "environment",
                definition,
                environment_compiler.validate_environment_definition_authority,
            )
            for definition in environment_presets.builtin_environment_definitions(
                environment_definitions,
                environment_evidence,
            ).values()
        ),
    ]
    for resource_type, source, validator in graph_sources:
        payload = {
            key: copy.deepcopy(value)
            for key, value in source.items()
            if key
            not in {
                "version",
                "status",
                "archive",
                "contentDigest",
                "createdAt",
                "builtin",
            }
        }
        _definitions, evidence = module_definitions.load_definition_versions(
            config,
            module_definitions.module_references(payload["instances"]),
        )
        authorities = module_definition_authority.module_definition_authorities_from_record_location_evidence(
            config["releaseRoot"], evidence
        )
        result = graph_resources.archive_if_changed(
            config,
            resource_type,
            payload,
            module_definitions=authorities,
            validate=validator,
            engine_owned=True,
        )
        definition = result["definition"]
        installed.append(definition)
        resource_id = definition[f"{resource_type}Id"]
        category = (
            "Basic Workflow"
            if resource_id.startswith("basic-")
            else "Standard"
            if resource_id.startswith("standard-")
            else "Neutral"
        )
        _place(
            config,
            {"analysis": "analyses", "environment": "environments"}[
                resource_type
            ],
            resource_id,
            f"/BuiltIn/{category}",
        )
    return installed
