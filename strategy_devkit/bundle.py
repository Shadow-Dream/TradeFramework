#!/usr/bin/env python3
import base64
from pathlib import Path


SDK_BUNDLE_FILES = (
    "__init__.py",
    "analysis_module_sdk.py",
    "environment_module_sdk.py",
    "module_contract.py",
    "module_sdk.py",
)


def encode_file(path, bundle_path=None, executable=False):
    source = Path(path)
    return {
        "path": bundle_path or source.name,
        "contentBase64": base64.b64encode(source.read_bytes()).decode("ascii"),
        "executable": executable,
    }


def sdk_bundle_files():
    """Return the complete public Module SDK required by every worker archive."""
    root = Path(__file__).resolve().parent
    return [
        encode_file(root / name, f"strategy_devkit/{name}")
        for name in SDK_BUNDLE_FILES
    ]
