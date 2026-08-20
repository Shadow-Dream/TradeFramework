#!/usr/bin/env python3
"""Prepare isolated user-preview state without copying production sessions."""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import shlex
import sqlite3
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from engine.control import auth


PREVIEW_ROOT = (PROJECT / ".runtime" / "preview").resolve()
PREVIEW_MINING_ROOT = (PROJECT.parent / ".trade-engine-preview-mining").resolve()
PREVIEW_CONFIG = {
    "controlRoot": str(PREVIEW_ROOT / "control"),
    "releaseRoot": str(PREVIEW_ROOT / "releases"),
    "liveRoot": str(PREVIEW_ROOT / "live"),
    "miningRoot": str(PREVIEW_MINING_ROOT),
    "miningAutoStart": True,
    "miningExposeTestProvider": False,
    "miningHttpTimeout": 20,
    "miningMaxPageBytes": 67_108_864,
    "miningMaxPagesPerRun": 25,
    "miningStandbyRetrySeconds": 15,
    "allowInsecureAuth": True,
}
AGENT_ENV = PREVIEW_ROOT / "agent-web.env"
PREVIEW_VENV = PREVIEW_ROOT / "venv"
WORKSPACE_REQUIREMENTS = PROJECT / "requirements-workspace.txt"
WORKSPACE_REQUIREMENTS_MARKER = PREVIEW_ROOT / "workspace-requirements.sha256"


def ensure_preview_workspace_runtime() -> None:
    digest = hashlib.sha256(WORKSPACE_REQUIREMENTS.read_bytes()).hexdigest()
    python = PREVIEW_VENV / "bin" / "python"
    installed = (
        python.is_file()
        and not python.is_symlink()
        and WORKSPACE_REQUIREMENTS_MARKER.is_file()
        and not WORKSPACE_REQUIREMENTS_MARKER.is_symlink()
        and WORKSPACE_REQUIREMENTS_MARKER.read_text(encoding="ascii").strip() == digest
    )
    if installed:
        return
    if PREVIEW_VENV.exists() and (PREVIEW_VENV.is_symlink() or not PREVIEW_VENV.is_dir()):
        raise RuntimeError("preview Workspace runtime must be a real directory")
    venv.EnvBuilder(with_pip=True, clear=PREVIEW_VENV.exists()).create(PREVIEW_VENV)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(WORKSPACE_REQUIREMENTS)],
        cwd=PROJECT,
        check=True,
    )
    temporary = WORKSPACE_REQUIREMENTS_MARKER.with_suffix(".tmp")
    temporary.write_text(digest + "\n", encoding="ascii")
    temporary.replace(WORKSPACE_REQUIREMENTS_MARKER)


def prepare_agent_environment() -> None:
    existing_token = ""
    existing_projects = ""
    if AGENT_ENV.is_file() and not AGENT_ENV.is_symlink():
        for line in AGENT_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("TRADE_AGENT_BRIDGE_TOKEN="):
                existing_token = line.partition("=")[2].strip()
            elif line.startswith("TRADE_AGENT_PROJECTS_JSON="):
                parsed = shlex.split(line, comments=False, posix=True)
                if len(parsed) != 1 or not parsed[0].startswith(
                    "TRADE_AGENT_PROJECTS_JSON="
                ):
                    raise RuntimeError("Agent Project configuration is malformed")
                existing_projects = parsed[0].partition("=")[2]
    token = existing_token if len(existing_token) >= 32 else secrets.token_urlsafe(48)
    build = datetime.now(timezone.utc).strftime("dev-%Y%m%dT%H%M%SZ")
    projects = os.environ.get("TRADE_AGENT_PROJECTS_JSON", "").strip() or existing_projects
    lines = [
        "TRADE_ENGINE_ROOT=/file/share/data_jyz/trade",
        "TRADE_ENGINE_PUBLIC_URL=http://10.130.130.66:30809",
        "AGENT_PUBLIC_URL=http://10.130.130.66:30810",
        f"TRADE_AGENT_BRIDGE_TOKEN={token}",
        f"TRADE_AGENT_BUILD={build}",
    ]
    if projects:
        lines.append(f"TRADE_AGENT_PROJECTS_JSON={shlex.quote(projects)}")
    payload = "\n".join((*lines, ""))
    AGENT_ENV.write_text(payload, encoding="utf-8")
    AGENT_ENV.chmod(0o600)


def production_users() -> list[sqlite3.Row]:
    configured = os.environ.get("TRADE_ENGINE_SOURCE_AUTH_DB", "").strip()
    if not configured:
        return []
    source_auth = Path(configured)
    if not source_auth.is_absolute():
        raise RuntimeError("TRADE_ENGINE_SOURCE_AUTH_DB must be absolute")
    source_auth = source_auth.resolve()
    if not source_auth.is_file() or source_auth.is_symlink():
        raise RuntimeError("source authentication database is unavailable")
    connection = sqlite3.connect(
        f"file:{source_auth}?mode=ro", uri=True, timeout=10
    )
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """SELECT user_id,email,password_hash,role,status,created_at,updated_at
            FROM users ORDER BY user_id"""
        ).fetchall()
    finally:
        connection.close()


def preview_user_id(production_user_id: str) -> str:
    digest = hashlib.sha256(production_user_id.encode("utf-8")).hexdigest()[:32]
    return f"preview-{digest}"


def sync_preview_users(connection, users) -> None:
    """Refresh account facts without invalidating still-live preview sessions."""
    if not users:
        raise RuntimeError("production authentication database has no users")
    preview_rows = [
        (
            preview_user_id(row["user_id"]),
            row["email"],
            row["password_hash"],
            row["role"],
            row["status"],
            row["created_at"],
            row["updated_at"],
        )
        for row in users
    ]
    preview_ids = [row[0] for row in preview_rows]
    connection.execute("BEGIN IMMEDIATE")
    connection.executemany(
        """INSERT INTO users
        (user_id,email,password_hash,role,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
          email=excluded.email,
          password_hash=excluded.password_hash,
          role=excluded.role,
          status=excluded.status,
          updated_at=excluded.updated_at""",
        preview_rows,
    )
    placeholders = ",".join("?" for _ in preview_ids)
    connection.execute(
        f"DELETE FROM users WHERE user_id NOT IN ({placeholders})",
        preview_ids,
    )
    connection.commit()


def ensure_builtin_resources() -> int:
    """Install only Engine-owned BuiltIns in the isolated preview."""

    from builtin_implementations import resources as builtin_resources
    from engine.control import database as engine_database
    from engine.control import schema as control_schema
    from engine.repository import control_state
    from engine.service import control_api

    config = control_api.load_config(
        PROJECT / "deploy" / "user" / "strategy-control-preview.json"
    )
    with control_state.control_state_lock(config):
        control_schema.prepare(config)
        engine_database.prepare_database(config)
        installed = builtin_resources.install(config)
    return len(installed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    project = PROJECT.resolve()
    if project not in PREVIEW_ROOT.parents or PREVIEW_ROOT.name != "preview":
        raise RuntimeError("preview runtime root is outside the expected project path")
    PREVIEW_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    PREVIEW_ROOT.chmod(0o700)
    for name in ("control", "releases", "live"):
        path = PREVIEW_ROOT / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    ensure_preview_workspace_runtime()
    if PROJECT in PREVIEW_MINING_ROOT.parents or PREVIEW_MINING_ROOT == PROJECT:
        raise RuntimeError("preview Mining root overlaps the source repository")
    PREVIEW_MINING_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    PREVIEW_MINING_ROOT.chmod(0o700)
    prepare_agent_environment()

    builtin_count = ensure_builtin_resources()
    users = production_users()
    with auth.connect(PREVIEW_CONFIG) as connection:
        if users:
            sync_preview_users(connection, users)
        elif connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            raise RuntimeError(
                "preview has no account; set TRADE_ENGINE_SOURCE_AUTH_DB for initial setup"
            )
    print(
        "prepared isolated Engine/Agent preview; "
        f"installed {builtin_count} Engine BuiltIn resource(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
