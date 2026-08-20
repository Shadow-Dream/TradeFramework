#!/usr/bin/env python3
"""Analysis repository facade for the unified Module SDK."""

from .module_sdk import Module, handle_module_command, run_module, serve_module


class AnalyzerModule(Module):
    kind = "Analyzer"


__all__ = [
    "AnalyzerModule",
    "handle_module_command",
    "run_module",
    "serve_module",
]
