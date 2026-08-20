#!/usr/bin/env python3
"""Environment repository facade for the unified Module SDK."""

from .module_sdk import Module, handle_module_command, run_module, serve_module


class EnvironmentModule(Module):
    kind = "Environment"


__all__ = [
    "EnvironmentModule",
    "handle_module_command",
    "run_module",
    "serve_module",
]
