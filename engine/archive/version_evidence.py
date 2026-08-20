"""Nominal proofs for location-verified immutable version records."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from engine.archive import version as version_archive
from engine.contracts import strict_json


_VERIFIED_RECORD_LOCATION_EVIDENCE_TOKEN = object()
_VERIFIED_RECORD_INDEX_EVIDENCE_TOKEN = object()


class _VerifiedRecordLocationEvidence:
    """Detached proof that one exact record/location passed verification."""

    __slots__ = ("_record_json", "_managed_root", "_expected_root", "_sealed")

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Verified record-location evidence is immutable.")
        object.__setattr__(self, name, value)

    def __init__(self, record, managed_root, expected_root, *, _token):
        if _token is not _VERIFIED_RECORD_LOCATION_EVIDENCE_TOKEN:
            raise TypeError("Verified record-location evidence is Engine-owned.")
        self._record_json = strict_json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._managed_root = str(Path(managed_root).expanduser().resolve())
        self._expected_root = str(Path(expected_root).expanduser().resolve())
        object.__setattr__(self, "_sealed", True)


class _VerifiedRecordIndexEvidence:
    """Nominal proof for one complete, location-verified record index."""

    __slots__ = (
        "_records_json",
        "_identity_fields",
        "_immutable_fields",
        "_location_evidence",
        "_sealed",
    )

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Verified record-index evidence is immutable.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        records,
        identity_fields,
        immutable_fields,
        location_evidence,
        *,
        _token,
    ):
        if _token is not _VERIFIED_RECORD_INDEX_EVIDENCE_TOKEN:
            raise TypeError("Verified record-index evidence is Engine-owned.")
        self._records_json = strict_json.dumps(
            records,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._identity_fields = tuple(identity_fields)
        self._immutable_fields = tuple(immutable_fields)
        self._location_evidence = tuple(location_evidence.items())
        object.__setattr__(self, "_sealed", True)


def verified_record_location_material(evidence):
    """Return detached material from nominal location evidence."""
    if type(evidence) is not _VerifiedRecordLocationEvidence:
        raise TypeError("Verified record-location evidence is Engine-owned.")
    return {
        "record": strict_json.loads(evidence._record_json),
        "managedRoot": evidence._managed_root,
        "expectedRoot": evidence._expected_root,
    }


def verified_record_index_material(evidence):
    """Return detached records and per-record proofs from index evidence."""
    if type(evidence) is not _VerifiedRecordIndexEvidence:
        raise TypeError("Verified record-index evidence is Engine-owned.")
    return {
        "records": strict_json.loads(evidence._records_json),
        "identityFields": evidence._identity_fields,
        "immutableFields": evidence._immutable_fields,
        "locationEvidence": dict(evidence._location_evidence),
    }


def verify_record_location_evidence(record, *, managed_root, expected_root):
    """Verify one exact record/location and return nominal detached evidence."""
    version_archive.verify_record_location(
        record,
        managed_root=managed_root,
        expected_root=expected_root,
    )
    return _VerifiedRecordLocationEvidence(
        record,
        managed_root,
        expected_root,
        _token=_VERIFIED_RECORD_LOCATION_EVIDENCE_TOKEN,
    )


def verify_record_index_location_evidence(
    records,
    identity_fields,
    *,
    managed_root,
    expected_root_for,
    immutable_fields=(),
):
    """Verify every indexed record once and retain detached evidence."""
    if not isinstance(records, Mapping):
        raise ValueError("Archived version index must be an object.")
    identity_fields = tuple(identity_fields)
    if not isinstance(immutable_fields, (str, bytes)):
        immutable_fields = tuple(immutable_fields)
    version_archive.validate_record_collection_material(
        (),
        identity_fields,
        immutable_fields=immutable_fields,
    )
    verified = []
    evidence_by_key = {}
    for key, record in records.items():
        if not isinstance(record, dict):
            raise ValueError("Archived version record must be an object.")
        expected_key = "/".join(
            [
                *(str(record.get(field)) for field in identity_fields),
                str(record.get("version")),
            ]
        )
        if key != expected_key:
            raise ValueError(
                f"Archived version index key mismatch: {key!r} != {expected_key!r}"
            )
        evidence_by_key[key] = verify_record_location_evidence(
            record,
            managed_root=managed_root,
            expected_root=expected_root_for(record),
        )
        verified.append(record)
    version_archive.validate_record_collection_material(
        verified,
        identity_fields,
        immutable_fields=immutable_fields,
    )
    verified_records = dict(records)
    return verified_records, _VerifiedRecordIndexEvidence(
        verified_records,
        identity_fields,
        immutable_fields,
        evidence_by_key,
        _token=_VERIFIED_RECORD_INDEX_EVIDENCE_TOKEN,
    )


def verify_record_index_locations(
    records,
    identity_fields,
    *,
    managed_root,
    expected_root_for,
    immutable_fields=(),
):
    """Verify each indexed record once at its exact repository destination."""
    verified, _evidence = verify_record_index_location_evidence(
        records,
        identity_fields,
        managed_root=managed_root,
        expected_root_for=expected_root_for,
        immutable_fields=immutable_fields,
    )
    return verified


def verified_record_collection_from_index_evidence(
    evidence,
    records,
    identity_fields,
    *,
    immutable_fields=(),
    managed_root,
    expected_root_for,
    resource_type,
    resource_id,
):
    """Select a domain-bound verified subset without rereading an archive."""
    material = verified_record_index_material(evidence)
    identity_fields = tuple(identity_fields)
    immutable_fields = tuple(immutable_fields)
    evidence_identity_fields = tuple(material["identityFields"])
    evidence_immutable_fields = tuple(material["immutableFields"])
    if not set(identity_fields).issubset(evidence_identity_fields):
        raise ValueError(
            "Verified record-index evidence does not cover the requested identity."
        )
    if set((*identity_fields, *immutable_fields)) != set(
        (*evidence_identity_fields, *evidence_immutable_fields)
    ):
        raise ValueError(
            "Verified record-index evidence belongs to a different record domain."
        )

    indexed = {}
    location_evidence = material["locationEvidence"]
    if set(location_evidence) != set(material["records"]):
        raise ValueError("Verified record-index evidence is internally inconsistent.")
    for key, record in material["records"].items():
        encoded = version_archive.canonical_json(record)
        if encoded in indexed:
            raise ValueError("Verified record-index evidence contains duplicate records.")
        indexed[encoded] = (key, record)

    canonical_managed_root = str(Path(managed_root).expanduser().resolve())
    selected = list(records)
    for record in selected:
        covered = indexed.get(version_archive.canonical_json(record))
        if covered is None:
            raise ValueError(
                "Archived record is not covered by its verified index evidence."
            )
        key, indexed_record = covered
        proof = verified_record_location_material(location_evidence[key])
        if version_archive.canonical_json(
            proof["record"]
        ) != version_archive.canonical_json(indexed_record):
            raise ValueError(
                "Verified record-index location evidence covers a different record."
            )
        if proof["managedRoot"] != canonical_managed_root:
            raise ValueError(
                "Verified record-index evidence belongs to a different managed root."
            )
        raw_expected_root = Path(expected_root_for(record)).expanduser().absolute()
        expected_root = version_archive.resolve_managed_path(
            managed_root,
            raw_expected_root,
            label="Verified record destination",
        )
        if str(raw_expected_root) != str(expected_root):
            raise ValueError(
                "Verified record destination must be a canonical absolute path."
            )
        expected_root_text = str(expected_root)
        archive = record.get("archive")
        if (
            proof["expectedRoot"] != expected_root_text
            or not isinstance(archive, dict)
            or archive.get("root") != expected_root_text
        ):
            raise ValueError(
                "Verified record-index evidence belongs to a different destination."
            )
        if (
            archive.get("resourceType") != resource_type
            or archive.get("resourceId") != resource_id
        ):
            raise ValueError(
                "Verified record-index evidence belongs to a different resource."
            )
    return version_archive.validate_record_collection_material(
        selected,
        identity_fields,
        immutable_fields=immutable_fields,
    )


__all__ = (
    "verified_record_collection_from_index_evidence",
    "verified_record_index_material",
    "verified_record_location_material",
    "verify_record_index_location_evidence",
    "verify_record_index_locations",
    "verify_record_location_evidence",
)
