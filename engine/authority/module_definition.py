"""Nominal authority for one verified immutable Module Definition."""

from __future__ import annotations

from pathlib import Path

from engine.archive import version as version_archive
from engine.archive import version_evidence
from engine.authority import execution_records
from engine.contracts import strict_json
from engine.contracts.module import validate_module_definition


__all__ = (
    "require_verified_module_definition_authority",
    "module_definition_authority_from_record_location_evidence",
    "module_definition_authorities_from_record_location_evidence",
    "verified_module_definition_material",
    "verify_managed_module_definition_authority",
    "verify_module_definition_authority",
)


_VERIFIED_MODULE_DEFINITION_TOKEN = object()


class _VerifiedModuleDefinition:
    """Engine-owned proof that one Module record passed archive verification."""

    __slots__ = ("_definition_json", "_sealed", "__weakref__")

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Verified Module Definition authority is immutable.")
        object.__setattr__(self, name, value)

    def __init__(self, definition, *, _token):
        if _token is not _VERIFIED_MODULE_DEFINITION_TOKEN:
            raise TypeError("Verified Module Definition authority is Engine-owned.")
        self._definition_json = strict_json.dumps(
            definition,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(self, "_sealed", True)

    def _material(self):
        return strict_json.loads(self._definition_json)


def _verified_module_definition_authority(definition):
    return _VerifiedModuleDefinition(
        definition,
        _token=_VERIFIED_MODULE_DEFINITION_TOKEN,
    )


def _require_module_record_identity(definition):
    validate_module_definition(definition)
    kind = definition["kind"]
    module_id = definition["moduleId"]
    version = definition["version"]
    if (
        not version.isdigit()
        or version != str(int(version))
        or int(version) < 1
    ):
        raise ValueError("Module version must be a canonical positive integer.")
    archive = definition.get("archive")
    if (
        not isinstance(archive, dict)
        or archive.get("resourceType") != "module"
        or archive.get("resourceId") != f"{kind}/{module_id}"
    ):
        raise ValueError(
            "Archived Module Definition identity does not match its record."
        )
    return definition


def verify_module_definition_authority(definition):
    """Verify one raw archived Module record and capture an owned proof."""

    _require_module_record_identity(definition)
    version_archive.verify_record(definition)
    return _verified_module_definition_authority(definition)


def verify_managed_module_definition_authority(release_root, definition):
    """Verify archive identity/location before reading a frozen Module bundle."""

    _require_module_record_identity(definition)
    execution_records.verify_module_definition_record(release_root, definition)
    return _verified_module_definition_authority(definition)


def module_definition_authority_from_record_location_evidence(
    release_root, evidence
):
    """Issue Module authority from repository evidence without archive rereads."""
    material = version_evidence.verified_record_location_material(evidence)
    definition = material["record"]
    validate_module_definition(definition)
    expected_root = execution_records.module_definition_record_expected_root(
        release_root, definition
    ).expanduser().resolve()
    managed_root = Path(material["managedRoot"])
    evidence_root = Path(material["expectedRoot"])
    if (
        managed_root != Path(release_root).expanduser().resolve()
        or evidence_root != expected_root
    ):
        raise ValueError(
            "Verified Module Definition evidence is outside its canonical repository."
        )
    return _verified_module_definition_authority(definition)


def module_definition_authorities_from_record_location_evidence(
    release_root, evidence_by_key
):
    """Issue an exact authority index from repository location evidence."""
    try:
        index_material = version_evidence.verified_record_index_material(
            evidence_by_key
        )
    except TypeError:
        pass
    else:
        evidence_by_key = index_material["locationEvidence"]
    if not isinstance(evidence_by_key, dict):
        raise TypeError("Module Definition evidence index must be an object.")
    authorities = {}
    for key, evidence in evidence_by_key.items():
        authority = module_definition_authority_from_record_location_evidence(
            release_root, evidence
        )
        definition = verified_module_definition_material(authority)
        expected_key = (
            f"{definition['kind']}/{definition['moduleId']}/{definition['version']}"
        )
        if key != expected_key:
            raise ValueError(
                "Module Definition evidence key does not match its record."
            )
        authorities[key] = authority
    return authorities


def require_verified_module_definition_authority(authority):
    """Require the exact nominal authority type without copying its material."""

    if type(authority) is not _VerifiedModuleDefinition:
        raise TypeError("Verified Module Definition authority is Engine-owned.")
    return authority


def verified_module_definition_material(authority):
    """Return an isolated Definition copy from an Engine-owned authority."""

    require_verified_module_definition_authority(authority)
    return authority._material()
