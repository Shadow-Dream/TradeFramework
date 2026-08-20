"""Canonical source identity for one TradeEngine build."""

from __future__ import annotations

import hashlib
from pathlib import Path


_RUNTIME_PACKAGES = (
    "builtin_implementations",
    "dataset_adapters",
    "engine",
    "strategy_devkit",
)


def _iter_runtime_sources(root: Path):
    """Yield every Python source in the Engine runtime roots, never a hand list."""

    # A root-level Python module is importable regardless of its filename.
    # Completeness cannot depend on a test naming convention: a production
    # module named ``test_*.py`` is still executable substrate and must change
    # the build identity.
    yield from sorted(root.glob("*.py"))
    for package in _RUNTIME_PACKAGES:
        yield from sorted((root / package).rglob("*.py"))


def _source_entries(root: Path):
    """Read the build source set once and retain the exact bytes hashed."""

    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in _iter_runtime_sources(root)
    )


def _build_id_from_entries(entries) -> str:
    digest = hashlib.sha256()
    for relative, payload in entries:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return "engine:" + digest.hexdigest()[:20]


def build_id(root: Path | None = None) -> str:
    """Return the canonical identity of all importable Engine runtime sources."""

    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    return _build_id_from_entries(_source_entries(root))


def source_manifest(root: Path | None = None) -> dict:
    """Return auditable per-file evidence for the exact build input set."""

    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    source_entries = _source_entries(root)
    entries = []
    digest = hashlib.sha256()
    for relative, payload in source_entries:
        entry = {
            "path": relative,
            "size": len(payload),
            "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }
        entries.append(entry)
        for value in (entry["path"], str(entry["size"]), entry["sha256"]):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
    return {
        "engineBuildId": _build_id_from_entries(source_entries),
        "fileCount": len(entries),
        "sha256": "sha256:" + digest.hexdigest(),
        "files": entries,
    }


ENGINE_BUILD_ID = build_id()


__all__ = ("ENGINE_BUILD_ID", "build_id", "source_manifest")
