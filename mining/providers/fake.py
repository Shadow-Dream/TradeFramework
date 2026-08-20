"""Deterministic provider used for recovery and failure-injection tests."""

from __future__ import annotations

import json
import math
from typing import Any

from .base import (
    BlockedProviderError,
    FetchPage,
    MiningProvider,
    RetryableProviderError,
    json_pointer,
)


class DeterministicFakeProvider(MiningProvider):
    provider_id = "deterministic-fake"
    test_only = True
    label = "Deterministic Fake (test only)"
    description = "Provider-native fixture pages with attempt-based failure injection."
    config_example = {
        "records": [[0, "native-a"], [60, "native-b"]],
        "pageSize": 1,
        "identityPointer": "/0",
        "eventTimePointer": "/0",
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "records",
            "pageSize",
            "identityPointer",
            "eventTimePointer",
            "finalPointer",
            "failAttempts",
            "failureKind",
            "retryAfter",
            "attemptRecords",
            "invalidPageAttempts",
        }
        unknown = sorted(set(config) - allowed)
        if unknown:
            raise ValueError(f"Unknown fake provider config fields: {', '.join(unknown)}")
        records = config.get("records")
        if type(records) is not list:
            raise ValueError("Fake provider records must be an array.")
        raw_retry_after = config.get("retryAfter", 0)
        if type(raw_retry_after) not in {int, float}:
            raise ValueError("Fake retryAfter must be a finite number.")
        retry_after = float(raw_retry_after)
        if not math.isfinite(retry_after) or not 0 <= retry_after <= 604800:
            raise ValueError("Fake retryAfter must be between 0 and 604800.")
        raw_page_size = config.get("pageSize", 2)
        if type(raw_page_size) is not int or not 1 <= raw_page_size <= 1000:
            raise ValueError("Fake pageSize must be an integer between 1 and 1000.")
        raw_identity_pointer = config.get("identityPointer", "/0")
        raw_event_time_pointer = config.get("eventTimePointer", "/0")
        if type(raw_identity_pointer) is not str:
            raise ValueError("Fake identityPointer must be a string.")
        if type(raw_event_time_pointer) is not str:
            raise ValueError("Fake eventTimePointer must be a string.")
        raw_fail_attempts = config.get("failAttempts", [])
        raw_invalid_page_attempts = config.get("invalidPageAttempts", [])
        for label, value in (
            ("failAttempts", raw_fail_attempts),
            ("invalidPageAttempts", raw_invalid_page_attempts),
        ):
            if type(value) is not list or any(type(item) is not int or item < 0 for item in value):
                raise ValueError(
                    f"Fake {label} must be an array of non-negative integers."
                )
        raw_failure_kind = config.get("failureKind", "retry")
        if type(raw_failure_kind) is not str or raw_failure_kind not in {"retry", "blocked"}:
            raise ValueError("Fake failureKind must be 'retry' or 'blocked'.")
        raw_attempt_records = config.get("attemptRecords", {})
        if type(raw_attempt_records) is not dict:
            raise ValueError("Fake provider attemptRecords must be an object.")
        result = {
            "records": records,
            "pageSize": raw_page_size,
            "identityPointer": raw_identity_pointer,
            "eventTimePointer": raw_event_time_pointer,
            "failAttempts": sorted(set(raw_fail_attempts)),
            "failureKind": raw_failure_kind,
            "retryAfter": retry_after,
            "attemptRecords": {
                str(int(attempt)): records
                for attempt, records in raw_attempt_records.items()
            },
            "invalidPageAttempts": sorted(set(raw_invalid_page_attempts)),
        }
        if "finalPointer" in config:
            if type(config["finalPointer"]) is not str:
                raise ValueError("Fake finalPointer must be a string.")
            result["finalPointer"] = config["finalPointer"]
        for record in records:
            json_pointer(record, result["identityPointer"])
            json_pointer(record, result["eventTimePointer"])
        for attempt_records in result["attemptRecords"].values():
            if type(attempt_records) is not list:
                raise ValueError("Fake provider attemptRecords values must be arrays.")
            for record in attempt_records:
                json_pointer(record, result["identityPointer"])
                json_pointer(record, result["eventTimePointer"])
        return result

    @classmethod
    def initial_cursor(cls, config: dict[str, Any]) -> dict[str, int]:
        return {"offset": 0}

    @classmethod
    def overlap_cursor(
        cls, cursor: Any, overlap: int, config: dict[str, Any]
    ) -> dict[str, int]:
        return {"offset": max(0, int((cursor or {}).get("offset", 0)) - max(0, overlap))}

    def fetch_page(self, cursor: Any, config: dict[str, Any], client: Any) -> FetchPage:
        attempt = int(config.get("_runtimeAttempt", 0))
        if attempt in config.get("failAttempts", []):
            if config.get("failureKind") == "blocked":
                raise BlockedProviderError(f"Injected blocked failure on attempt {attempt}.")
            raise RetryableProviderError(
                f"Injected retryable failure on attempt {attempt}.",
                retry_after=float(config.get("retryAfter", 0)),
            )
        if attempt in config.get("invalidPageAttempts", []):
            return FetchPage(
                raw=b'{"invalid":"provider-contract"}',
                payload={"invalid": "provider-contract"},
                records=(),
                source="deterministic-fake://invalid",
            )
        offset = max(0, int((cursor or {}).get("offset", 0)))
        source_records = config.get("attemptRecords", {}).get(str(attempt), config["records"])
        records = source_records[offset:offset + int(config["pageSize"])]
        raw = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return FetchPage(
            raw=raw,
            payload=records,
            records=tuple(records),
            headers={"content-type": "application/json", "x-request-id": f"fake-{attempt}-{offset}"},
            source="deterministic-fake://fixture",
        )

    @classmethod
    def validate_page(cls, page: FetchPage, config: dict[str, Any]) -> None:
        if not isinstance(page.payload, list):
            raise ValueError("Fake page must be a list.")
        for record in page.records:
            json_pointer(record, config["identityPointer"])
            json_pointer(record, config["eventTimePointer"])

    @classmethod
    def next_cursor(
        cls, cursor: Any, page: FetchPage, config: dict[str, Any]
    ) -> dict[str, int] | None:
        if not page.records:
            return None
        return {"offset": int((cursor or {}).get("offset", 0)) + len(page.records)}

    @classmethod
    def record_identity(cls, record: Any, config: dict[str, Any]) -> Any:
        return json_pointer(record, config["identityPointer"])

    @classmethod
    def event_time(cls, record: Any, config: dict[str, Any]) -> Any:
        return json_pointer(record, config["eventTimePointer"])

    @classmethod
    def is_final(cls, record: Any, config: dict[str, Any]) -> bool:
        if config.get("finalPointer") is None:
            return True
        return bool(json_pointer(record, config["finalPointer"]))

    @classmethod
    def cursor_for_gap(
        cls, config: dict[str, Any], missing_start: float, missing_end: float
    ) -> dict[str, int]:
        for index, record in enumerate(config["records"]):
            event_time = cls.event_time(record, config)
            if isinstance(event_time, (int, float)) and float(event_time) >= missing_start:
                return {"offset": index}
        return {"offset": len(config["records"])}

    @classmethod
    def minimum_request_interval(cls, config: dict[str, Any]) -> float:
        return 0.0
