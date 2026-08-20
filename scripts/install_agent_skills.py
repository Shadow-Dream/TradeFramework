#!/usr/bin/env python3
"""Expose TradeEngine's four canonical skills to Claude Code and Codex.

The content remains solely under ``agent_skills/``.  Provider discovery roots
contain relative directory symlinks, so updates cannot drift into two copies.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


SKILL_NAMES = (
    "strategy-development",
    "dataset-preparation",
    "backtest-investigation",
    "research-verification",
)
DISCOVERY_ROOTS = (Path(".claude/skills"), Path(".agents/skills"))


def _require_canonical_skill(source_root: Path, name: str) -> Path:
    directory = source_root / name
    skill_file = directory / "SKILL.md"
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError(f"Canonical Agent Skill is not a plain directory: {name}")
    if skill_file.is_symlink() or not skill_file.is_file():
        raise RuntimeError(f"Canonical Agent Skill has no plain SKILL.md: {name}")
    return directory.resolve(strict=True)


def install(root: Path, *, check: bool = False) -> list[Path]:
    root = root.resolve(strict=True)
    source_root = root / "agent_skills"
    sources = {
        name: _require_canonical_skill(source_root, name)
        for name in SKILL_NAMES
    }
    installed: list[Path] = []
    for relative_discovery_root in DISCOVERY_ROOTS:
        discovery_root = root / relative_discovery_root
        if check:
            if not discovery_root.is_dir():
                raise RuntimeError(f"Agent Skill discovery root is missing: {relative_discovery_root}")
        else:
            discovery_root.mkdir(parents=True, exist_ok=True)
        for name, source in sources.items():
            destination = discovery_root / name
            if destination.is_symlink():
                if destination.resolve(strict=True) != source:
                    raise RuntimeError(f"Agent Skill link has an unexpected target: {destination}")
                installed.append(destination)
                continue
            if destination.exists():
                raise RuntimeError(f"Agent Skill destination must be a managed symlink: {destination}")
            if check:
                raise RuntimeError(f"Agent Skill link is missing: {destination}")
            relative_target = os.path.relpath(source, start=discovery_root)
            temporary = discovery_root / f".{name}.install-{os.getpid()}"
            temporary.symlink_to(relative_target, target_is_directory=True)
            os.replace(temporary, destination)
            installed.append(destination)
    return installed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="TradeEngine source root (default: this script's repository)",
    )
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    installed = install(arguments.root, check=arguments.check)
    verb = "verified" if arguments.check else "installed"
    print(f"{verb} {len(installed)} provider skill links from {len(SKILL_NAMES)} canonical skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
