#!/usr/bin/env python3
"""Sandbox profile and process binding for long-lived Jupyter Workspaces."""

from __future__ import annotations

import http.client
import importlib.util
import os
from pathlib import Path
import shutil
import site
import socket
import sys

from engine.runtime import process_session


ISOLATED_WORKSPACE_ROOT = "/tmp/workspace"
ISOLATED_RUNTIME_ROOT = "/tmp/trade-jupyter-runtime"
ENGINE_SITE_ROOT = "/opt/trade-engine-site"


def is_installed():
    return importlib.util.find_spec("jupyterlab") is not None


def available_workspace_port(host):
    with socket.socket() as listener:
        listener.bind((str(host), 0))
        return int(listener.getsockname()[1])


def process_environment(storage):
    virtual_user_base = f"{ISOLATED_RUNTIME_ROOT}/python-user"
    virtual_user_site = (
        f"{virtual_user_base}/lib/"
        f"python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    # This is an explicit allowlist, not a projection of os.environ.  It is
    # safe both for the host supervisor/bubblewrap process and the guest.
    return process_session.minimal_host_environment(
        home=f"{ISOLATED_RUNTIME_ROOT}/home",
        extra={
            "JUPYTER_CONFIG_DIR": f"{ISOLATED_RUNTIME_ROOT}/private",
            "JUPYTER_DATA_DIR": f"{ISOLATED_RUNTIME_ROOT}/data",
            "JUPYTER_RUNTIME_DIR": f"{ISOLATED_RUNTIME_ROOT}/runtime",
            "XDG_CACHE_HOME": f"{ISOLATED_RUNTIME_ROOT}/cache",
            "IPYTHONDIR": f"{ISOLATED_RUNTIME_ROOT}/ipython",
            "PYTHONUSERBASE": virtual_user_base,
            "PIP_USER": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PATH": f"{virtual_user_base}/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": os.pathsep.join((virtual_user_site, ENGINE_SITE_ROOT)),
            "SHELL": "/bin/sh",
            "USER": "trade-workspace",
            "LOGNAME": "trade-workspace",
        },
    )


def _append_parent_directories(command, destination, created):
    current = Path(destination).parent
    parents = []
    while current != current.parent:
        parents.append(current)
        current = current.parent
    for parent in reversed(parents):
        text = str(parent)
        if text not in created:
            command.extend(["--dir", text])
            created.add(text)


def _append_read_only_mount(command, source, destination, created):
    source = Path(source)
    if not source.exists():
        return
    _append_parent_directories(command, destination, created)
    command.extend(["--ro-bind", str(source), str(destination)])
    created.add(str(destination))


def sandbox_prefix(workspace, storage, read_only_roots=()):
    """Build the long-lived Jupyter profile (network and pip remain available)."""
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError(
            "JupyterLab workspace sandbox is unavailable: bubblewrap is required; "
            "startup refused."
        )
    workspace = Path(workspace)
    if workspace.is_symlink():
        raise ValueError("Jupyter Workspace may not be a symbolic link.")
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ValueError("Jupyter Workspace must be a managed directory.")
    for name in ("home", "data", "runtime", "cache", "ipython", "python-user"):
        path = Path(storage[name])
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Jupyter instance storage must be real directories.")
    server_config = Path(storage["serverConfig"])
    command = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
    ]
    created = set()
    for source in ("/usr", "/bin", "/lib", "/lib64"):
        _append_read_only_mount(command, source, source, created)
    for source in (
        "/etc/ssl",
        "/etc/ca-certificates",
        "/etc/hosts",
        "/etc/resolv.conf",
        "/etc/nsswitch.conf",
        "/etc/passwd",
        "/etc/group",
        "/etc/localtime",
        "/etc/mime.types",
    ):
        _append_read_only_mount(command, source, source, created)
    command.extend(["--tmpfs", "/tmp"])
    created.add("/tmp")
    python_prefix = Path(sys.prefix).resolve()
    system_roots = tuple(
        Path(value).resolve() for value in ("/usr", "/bin", "/lib", "/lib64")
    )
    if not any(
        python_prefix == root or python_prefix.is_relative_to(root)
        for root in system_roots
    ):
        if python_prefix == Path("/") or not python_prefix.is_dir():
            raise RuntimeError("Jupyter Python runtime prefix cannot be safely mounted.")
        _append_read_only_mount(command, python_prefix, python_prefix, created)
    engine_user_site = Path(site.getusersitepackages())
    if engine_user_site.is_dir():
        _append_read_only_mount(
            command,
            engine_user_site.resolve(),
            ENGINE_SITE_ROOT,
            created,
        )
    _append_parent_directories(command, ISOLATED_WORKSPACE_ROOT, created)
    command.extend(["--bind", str(workspace), ISOLATED_WORKSPACE_ROOT])
    created.add(ISOLATED_WORKSPACE_ROOT)
    for name in ("home", "data", "runtime", "cache", "ipython", "python-user"):
        destination = f"{ISOLATED_RUNTIME_ROOT}/{name}"
        _append_parent_directories(command, destination, created)
        command.extend(["--bind", str(storage[name]), destination])
        created.add(destination)
    if server_config.is_file() and not server_config.is_symlink():
        destination = f"{ISOLATED_RUNTIME_ROOT}/private/{server_config.name}"
        _append_read_only_mount(command, server_config, destination, created)
    for source in read_only_roots:
        _append_read_only_mount(command, source, source, created)
    command.extend([
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--chdir",
        ISOLATED_WORKSPACE_ROOT,
    ])
    return command


def build_workspace_command(instance, storage):
    server_config = f"{ISOLATED_RUNTIME_ROOT}/private/jupyter_server_config.py"
    jupyter = [
        sys.executable,
        "-m",
        "jupyterlab",
        f"--config={server_config}",
        f"--ServerApp.ip={instance['host']}",
        f"--ServerApp.port={instance['port']}",
        "--ServerApp.port_retries=0",
        "--ServerApp.open_browser=False",
        f"--ServerApp.root_dir={ISOLATED_WORKSPACE_ROOT}",
        f"--ServerApp.base_url={instance['baseUrl']}",
        "--ServerApp.password=",
        "--ServerApp.allow_remote_access=True",
        "--ServerApp.trust_xheaders=True",
        "--ServerApp.allow_root=True",
        "--MappingKernelManager.cull_idle_timeout=3600",
        "--MappingKernelManager.cull_interval=60",
    ]
    return [
        *sandbox_prefix(
            instance["workspacePath"],
            storage,
            instance["readOnlyRoots"],
        ),
        *jupyter,
    ]


def workspace_health(instance, session, timeout=1.0):
    if session is None or session.poll() is not None:
        return False
    connection = http.client.HTTPConnection(
        instance["host"],
        instance["port"],
        timeout=timeout,
    )
    try:
        connection.request(
            "GET",
            instance["baseUrl"] + "api/status",
            headers={"Authorization": f"token {instance['token']}"},
        )
        response = connection.getresponse()
        response.read()
        return response.status == 200
    except OSError:
        return False
    finally:
        connection.close()


def start_workspace_process(instance, storage, log_path, *, max_log_bytes):
    key = f"jupyter:{instance['slug']}"
    return process_session.PROCESS_SESSIONS.start(
        key,
        build_workspace_command(instance, storage),
        cwd=instance["workspacePath"],
        env=process_environment(storage),
        max_output_bytes=max_log_bytes,
        stdout_path=log_path,
        merge_stderr=True,
        metadata={
            key: value
            for key, value in instance.items()
            if key not in {"token"}
        },
    )


__all__ = (
    "ENGINE_SITE_ROOT",
    "ISOLATED_RUNTIME_ROOT",
    "ISOLATED_WORKSPACE_ROOT",
    "available_workspace_port",
    "build_workspace_command",
    "is_installed",
    "process_environment",
    "sandbox_prefix",
    "start_workspace_process",
    "workspace_health",
)
