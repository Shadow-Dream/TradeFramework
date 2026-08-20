#!/usr/bin/env python3
"""Pure identities and URL contracts for managed Engine Workspaces."""

from __future__ import annotations

import hashlib


DEFAULT_JUPYTER_HOST = "127.0.0.1"
DEFAULT_JUPYTER_BASE_URL = "/jupyter/"
WORKSPACE_KINDS = frozenset({"dataset", "module", "sampler"})


def normalize_workspace_kind(workspace_kind):
    if not isinstance(workspace_kind, str) or workspace_kind not in WORKSPACE_KINDS:
        raise ValueError("Jupyter workspace kind must be dataset, module, or sampler.")
    return workspace_kind


def workspace_slug(workspace_id, workspace_kind):
    kind = normalize_workspace_kind(workspace_kind)
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("Jupyter Workspace ID must be a non-empty string.")
    raw = f"{kind}-{workspace_id}"
    readable = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in raw
    ).strip("-")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{readable[:70] or kind}-{digest}"


def jupyter_host(config):
    return str(config.get("jupyterHost", DEFAULT_JUPYTER_HOST))


def jupyter_base_url(config):
    value = str(config.get("jupyterBaseUrl", DEFAULT_JUPYTER_BASE_URL))
    return "/" + value.strip("/") + "/"


def workspace_base_url(config, workspace_id, workspace_kind):
    return (
        jupyter_base_url(config)
        + "w/"
        + workspace_slug(workspace_id, workspace_kind)
        + "/"
    )


__all__ = (
    "DEFAULT_JUPYTER_BASE_URL",
    "DEFAULT_JUPYTER_HOST",
    "WORKSPACE_KINDS",
    "jupyter_base_url",
    "jupyter_host",
    "normalize_workspace_kind",
    "workspace_base_url",
    "workspace_slug",
)
