"""Bounded application-layer contracts for Agent run context and review artifacts.

These contracts deliberately contain references and review text only.  They are
not Engine resources and cannot authorize or perform an Engine mutation.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime


CONTEXT_SCHEMA_VERSION = "1"
REVIEW_ARTIFACT_SCHEMA_VERSION = "1"
MAX_CONTEXT_BYTES = 16 * 1024
MAX_REVIEW_ARTIFACT_BYTES = 32 * 1024
MAX_CONTEXT_REFERENCES = 32

_OPEN_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_RFC3339_UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z$"
)
def _exact_object(
    value: object,
    *,
    allowed: set[str],
    required: set[str],
    label: str,
) -> dict:
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    return value


def _text(
    value: object,
    *,
    label: str,
    maximum: int,
    identifier: bool = False,
) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty string without surrounding whitespace")
    if len(value) > maximum:
        raise ValueError(f"{label} must contain at most {maximum} characters")
    if any(
        ord(character) < 0x20
        or ord(character) == 0x7F
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    ):
        raise ValueError(f"{label} must not contain control characters or invalid Unicode")
    if identifier and not _OPEN_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(
            f"{label} must be an open identifier containing letters, numbers, '.', '_', ':', or '-'"
        )
    return value


def _text_list(
    value: object,
    *,
    label: str,
    maximum_items: int = 16,
    maximum_length: int = 2_000,
) -> list[str]:
    if type(value) is not list or len(value) > maximum_items:
        raise ValueError(f"{label} must be an array with at most {maximum_items} entries")
    return [
        _text(item, label=f"{label}[{index}]", maximum=maximum_length)
        for index, item in enumerate(value)
    ]


def validate_reference(value: object, *, label: str = "reference") -> dict:
    reference = _exact_object(
        value,
        allowed={"kind", "id", "version", "digest", "label"},
        required={"kind", "id"},
        label=label,
    )
    result = {
        "kind": _text(reference["kind"], label=f"{label}.kind", maximum=64, identifier=True),
        "id": _text(reference["id"], label=f"{label}.id", maximum=512),
    }
    for key, maximum in (("version", 256), ("digest", 256), ("label", 256)):
        if key in reference:
            result[key] = _text(
                reference[key], label=f"{label}.{key}", maximum=maximum
            )
    if result.get("version", "").lower() == "latest":
        raise ValueError(f"{label}.version must be an exact version, not 'latest'")
    return result


def _references(
    value: object,
    *,
    label: str,
    minimum: int = 1,
    maximum: int = MAX_CONTEXT_REFERENCES,
) -> list[dict]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError(
            f"{label} must be an array with {minimum}-{maximum} references"
        )
    result: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(value):
        reference = validate_reference(item, label=f"{label}[{index}]")
        identity = (
            reference["kind"],
            reference["id"],
            reference.get("version", ""),
            reference.get("digest", ""),
        )
        if identity in seen:
            raise ValueError(f"{label} contains a duplicate reference")
        seen.add(identity)
        result.append(reference)
    return result


def validate_context(value: object) -> dict:
    """Validate and return the canonical, immutable Context snapshot for one run."""
    context = _exact_object(
        value,
        allowed={"schemaVersion", "sourceView", "capturedAt", "references"},
        required={"schemaVersion", "sourceView", "capturedAt", "references"},
        label="context",
    )
    if context["schemaVersion"] != CONTEXT_SCHEMA_VERSION:
        raise ValueError("context.schemaVersion must be '1'")
    captured_at = _text(
        context["capturedAt"], label="context.capturedAt", maximum=40
    )
    if not _RFC3339_UTC_RE.fullmatch(captured_at):
        raise ValueError("context.capturedAt must be an RFC 3339 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(captured_at[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("context.capturedAt must be a valid RFC 3339 timestamp") from exc
    result = {
        "schemaVersion": CONTEXT_SCHEMA_VERSION,
        "sourceView": _text(
            context["sourceView"],
            label="context.sourceView",
            maximum=64,
            identifier=True,
        ),
        "capturedAt": captured_at,
        "references": _references(
            context["references"], label="context.references", minimum=0
        ),
    }
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CONTEXT_BYTES:
        raise ValueError(f"context must encode to at most {MAX_CONTEXT_BYTES} bytes")
    return result


def _fact(value: object, index: int) -> dict:
    label = f"analysisBrief.confirmedFacts[{index}]"
    fact = _exact_object(
        value,
        allowed={"claim", "references"},
        required={"claim", "references"},
        label=label,
    )
    return {
        "claim": _text(fact["claim"], label=f"{label}.claim", maximum=2_000),
        "references": _references(
            fact["references"], label=f"{label}.references", maximum=16
        ),
    }


def _calculation(value: object, index: int) -> dict:
    label = f"analysisBrief.calculations[{index}]"
    calculation = _exact_object(
        value,
        allowed={"description", "method", "result", "references"},
        required={"description", "method", "result", "references"},
        label=label,
    )
    return {
        "description": _text(
            calculation["description"], label=f"{label}.description", maximum=2_000
        ),
        "method": _text(
            calculation["method"], label=f"{label}.method", maximum=2_000
        ),
        "result": _text(
            calculation["result"], label=f"{label}.result", maximum=2_000
        ),
        "references": _references(
            calculation["references"], label=f"{label}.references", maximum=16
        ),
    }


def validate_analysis_brief(value: object) -> dict:
    brief = _exact_object(
        value,
        allowed={
            "title",
            "summary",
            "confirmedFacts",
            "calculations",
            "interpretation",
            "counterEvidence",
            "falsification",
            "nextStep",
        },
        required={
            "title",
            "summary",
            "confirmedFacts",
            "calculations",
            "interpretation",
            "counterEvidence",
            "falsification",
            "nextStep",
        },
        label="analysisBrief",
    )
    facts = brief["confirmedFacts"]
    calculations = brief["calculations"]
    if type(facts) is not list or len(facts) > 32:
        raise ValueError("analysisBrief.confirmedFacts must be an array with at most 32 entries")
    if type(calculations) is not list or len(calculations) > 32:
        raise ValueError("analysisBrief.calculations must be an array with at most 32 entries")
    return {
        "title": _text(brief["title"], label="analysisBrief.title", maximum=200),
        "summary": _text(brief["summary"], label="analysisBrief.summary", maximum=4_000),
        "confirmedFacts": [_fact(item, index) for index, item in enumerate(facts)],
        "calculations": [
            _calculation(item, index) for index, item in enumerate(calculations)
        ],
        "interpretation": _text_list(
            brief["interpretation"], label="analysisBrief.interpretation"
        ),
        "counterEvidence": _text_list(
            brief["counterEvidence"], label="analysisBrief.counterEvidence"
        ),
        "falsification": _text_list(
            brief["falsification"], label="analysisBrief.falsification"
        ),
        "nextStep": _text(
            brief["nextStep"], label="analysisBrief.nextStep", maximum=4_000
        ),
    }


def validate_proposal(value: object) -> dict:
    proposal = _exact_object(
        value,
        allowed={"title", "summary", "suggestedActions", "references"},
        required={"title", "summary", "suggestedActions", "references"},
        label="proposal",
    )
    return {
        "title": _text(proposal["title"], label="proposal.title", maximum=200),
        "summary": _text(
            proposal["summary"], label="proposal.summary", maximum=4_000
        ),
        "suggestedActions": _text_list(
            proposal["suggestedActions"],
            label="proposal.suggestedActions",
            maximum_items=16,
            maximum_length=1_000,
        ),
        "references": _references(
            proposal["references"], label="proposal.references"
        ),
    }


def validate_review_artifact(value: object) -> dict:
    envelope = _exact_object(
        value,
        allowed={"schemaVersion", "analysisBrief", "proposal"},
        required={"schemaVersion"},
        label="review artifact",
    )
    if envelope["schemaVersion"] != REVIEW_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("review artifact schemaVersion must be '1'")
    if "analysisBrief" not in envelope and "proposal" not in envelope:
        raise ValueError("review artifact must contain analysisBrief or proposal")
    result = {"schemaVersion": REVIEW_ARTIFACT_SCHEMA_VERSION}
    if "analysisBrief" in envelope:
        result["analysisBrief"] = validate_analysis_brief(envelope["analysisBrief"])
    if "proposal" in envelope:
        result["proposal"] = validate_proposal(envelope["proposal"])
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REVIEW_ARTIFACT_BYTES:
        raise ValueError(
            f"review artifact must encode to at most {MAX_REVIEW_ARTIFACT_BYTES} bytes"
        )
    return result


def decode_review_artifact(raw: bytes) -> dict:
    if len(raw) > MAX_REVIEW_ARTIFACT_BYTES:
        raise ValueError(
            f"review artifact must be at most {MAX_REVIEW_ARTIFACT_BYTES} bytes"
        )

    def exact_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"review artifact has duplicate field: {key}")
            result[key] = value
        return result

    def reject_nonfinite(value):
        raise ValueError(f"review artifact contains non-finite number: {value}")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(
                f"review artifact contains a number outside the finite range: {value}"
            )
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=exact_pairs,
            parse_constant=reject_nonfinite,
            parse_float=finite_float,
        )
    except UnicodeDecodeError as exc:
        raise ValueError("review artifact must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("review artifact must contain exactly one JSON value") from exc
    except RecursionError as exc:
        raise ValueError("review artifact nesting is too deep") from exc
    return validate_review_artifact(value)
