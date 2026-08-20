#!/usr/bin/env python3
"""In-process adapter for one Engine-frozen Python Module implementation."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import time
import types

from engine.runtime.module_adapter import (
    InvocationAdapter,
    module_adapter_material,
    module_configuration,
    require_initialized_module_configuration,
)
from engine.runtime.reusable_output import (
    issue_reusable_output_receipt,
    reusable_output_receipt_material,
)


__all__ = ("create_python_module_adapter",)


def _unload_python_package(package_name):
    for name in tuple(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            sys.modules.pop(name, None)


def _load_python_module_class(definition, instance_id):
    from strategy_devkit import Module
    from strategy_devkit.module_sdk import validate_module_implementation_class

    root = Path(definition["archive"]["root"]).resolve()
    entry = root / "module.py"
    if not entry.is_file():
        raise ValueError("PythonModule archive is missing module.py.")
    digest = definition["contentDigest"].removeprefix("sha256:")
    instance_token = hashlib.sha256(
        f"{root}\0{instance_id}".encode("utf-8")
    ).hexdigest()[:16]
    package_name = (
        f"_trade_archived_module_{digest.replace('-', '_')}_{instance_token}"
    )
    module_name = f"{package_name}.module"
    loaded = sys.modules.get(module_name)
    if loaded is None:
        package = sys.modules.get(package_name)
        if package is None:
            package = types.ModuleType(package_name)
            package.__path__ = [str(root)]
            package.__package__ = package_name
            sys.modules[package_name] = package
        try:
            spec = importlib.util.spec_from_file_location(module_name, entry)
            if spec is None or spec.loader is None:
                raise ValueError("PythonModule module.py cannot be loaded.")
            loaded = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = loaded
            previous_dont_write_bytecode = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                spec.loader.exec_module(loaded)
            finally:
                sys.dont_write_bytecode = previous_dont_write_bytecode
        except BaseException:
            _unload_python_package(package_name)
            raise
    try:
        implementation = getattr(loaded, "MODULE_CLASS", None)
        if not isinstance(implementation, type) or not issubclass(implementation, Module):
            raise TypeError(
                "PythonModule module.py must export MODULE_CLASS inheriting strategy_devkit.Module."
            )
        if implementation.kind != definition["kind"]:
            raise TypeError(
                f"PythonModule implementation kind '{implementation.kind}' does not "
                f"match Definition kind '{definition['kind']}'."
            )
        validate_module_implementation_class(implementation)
    except BaseException:
        _unload_python_package(package_name)
        raise
    return implementation, package_name


class _InProcessPythonAdapter(InvocationAdapter):
    """Direct adapter for one Engine-frozen SDK Module instance."""

    def __init__(self, authority):
        from strategy_devkit.module_sdk import Module as SdkModule

        binding, definition, _ports, _mode = module_adapter_material(
            authority,
            expected_activation_mode="PythonModule",
        )
        self.binding = binding
        self.sdk_module_class = SdkModule
        implementation, self.package_name = _load_python_module_class(
            definition,
            binding["instanceId"],
        )
        self.module = None
        self.validated_input_issuer = object()
        self.invoke_seconds = 0.0
        self.input_copy_seconds = 0.0
        self.sdk_invoke_seconds = 0.0
        try:
            self.module = implementation()
            initialized = self.sdk_module_class.initialize(
                self.module,
                module_configuration(authority),
            )
            require_initialized_module_configuration(
                authority,
                self.module.configuration,
                actual_version_key=initialized.get("versionKey"),
                actual_identity={
                    "key": self.module.instance_id,
                    "kind": self.module.kind,
                    "moduleId": self.module.module_id,
                    "version": self.module.version,
                },
                actual_config=self.module.config,
                actual_ports={
                    "inputs": self.module._input_ports,
                    "outputs": self.module._output_ports,
                },
                actual_archive=self.module.archive,
            )
            self.sdk_module_class._bind_validated_input_issuer(
                self.module,
                self.validated_input_issuer,
            )
        except BaseException:
            cleanup_errors = []
            try:
                if self.module is not None:
                    self.sdk_module_class.close(self.module)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            finally:
                _unload_python_package(self.package_name)
            # Initialization remains the authoritative failure.  Cleanup is
            # all-attempt and cannot replace it with a secondary exception.
            raise

    def invoke(self, inputs):
        started = time.perf_counter()
        phase_started = time.perf_counter()
        validated_inputs = self.sdk_module_class._issue_validated_engine_inputs(
            self.module,
            inputs,
            _issuer=self.validated_input_issuer,
        )
        module_outputs = self.sdk_module_class._invoke_for_engine(
            self.module,
            inputs,
            _validated_inputs=validated_inputs,
        )
        self.sdk_invoke_seconds += time.perf_counter() - phase_started
        reusable_material = self.sdk_module_class._reusable_output_material(
            self.module,
            module_outputs,
        )
        outputs = (
            issue_reusable_output_receipt(
                self,
                module_outputs,
                reusable_material,
            )
            if reusable_material is not None
            else module_outputs
        )
        self.invoke_seconds += time.perf_counter() - started
        return outputs

    def confirm_reusable_outputs(self, receipt):
        handle, *_material = reusable_output_receipt_material(
            receipt,
            adapter=self,
        )
        self.sdk_module_class._confirm_reusable_outputs(
            self.module,
            handle,
        )

    def finalize(self):
        return copy.deepcopy(self.sdk_module_class.finalize(self.module))

    def snapshot(self):
        return copy.deepcopy(self.sdk_module_class.snapshot(self.module))

    def restore(self, snapshot):
        return copy.deepcopy(
            self.sdk_module_class.restore(self.module, copy.deepcopy(snapshot))
        )

    def close(self):
        result = None
        try:
            result = self.sdk_module_class.close(self.module)
        finally:
            _unload_python_package(self.package_name)
        return copy.deepcopy(result)

    def transport_metrics(self):
        sdk_metrics = self.sdk_module_class.runtime_metrics(self.module)
        return {
            "runtimeMode": "in-process-python",
            "invokeSeconds": self.invoke_seconds,
            "inputCopySeconds": self.input_copy_seconds,
            "sdkInvokeSeconds": self.sdk_invoke_seconds,
            "sdkInputValidationSeconds": sdk_metrics["inputValidationSeconds"],
            "moduleComputeSeconds": sdk_metrics["computeSeconds"],
            "sdkOutputValidationSeconds": sdk_metrics["outputValidationSeconds"],
        }


def create_python_module_adapter(authority):
    """Create the private in-process adapter for a verified Python Module."""
    return _InProcessPythonAdapter(authority)
