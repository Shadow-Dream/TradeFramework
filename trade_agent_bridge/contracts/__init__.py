"""Strict, bounded contracts shared by TradeEngine and Agent Web."""

import hashlib
import json

from .review_artifacts import (
    validate_analysis_brief,
    validate_context,
    validate_proposal,
    validate_reference,
    validate_review_artifact,
)


def context_digest(context):
    encoded = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

__all__ = (
    "validate_analysis_brief",
    "context_digest",
    "validate_context",
    "validate_proposal",
    "validate_reference",
    "validate_review_artifact",
)
