"""Provider boundary for durable, provider-native data collection.

The mining core never names or reshapes market fields.  A provider is only
responsible for fetching an opaque page and exposing the small amount of
runtime meaning needed for checkpointing and evidence indexing.
"""

from __future__ import annotations

import abc
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable


def canonical_json(value: Any) -> str:
    def require_exact_json(current: Any, path: str) -> None:
        if current is None or type(current) in {str, bool, int}:
            return
        if type(current) is float:
            if not math.isfinite(current):
                raise ValueError(f"Non-finite JSON number at {path}.")
            return
        if type(current) is list:
            for index, item in enumerate(current):
                require_exact_json(item, f"{path}[{index}]")
            return
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str:
                    raise TypeError(f"JSON object key at {path} must be a string.")
                require_exact_json(item, f"{path}.{key}")
            return
        raise TypeError(f"Value at {path} is not an exact JSON type.")

    require_exact_json(value, "$")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def strict_json_loads(raw: bytes | str, *, label: str) -> Any:
    """Decode provider JSON without duplicate keys or non-finite constants."""

    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label} contains a non-finite JSON number: {value}.")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains a non-finite JSON number: {value}.")
        return parsed

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains a duplicate JSON key: {key}.")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            parse_constant=reject_constant,
            parse_float=finite_float,
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON.") from exc


def json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def json_pointer(value: Any, pointer: str) -> Any:
    """Resolve the small RFC 6901 subset needed by configurable providers."""
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("JSON pointer must be empty or start with '/'.")
    current = value
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"JSON pointer index does not exist: {pointer}") from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ValueError(f"JSON pointer path does not exist: {pointer}")
    return current


@dataclass(frozen=True)
class FetchPage:
    raw: bytes
    payload: Any
    records: tuple[Any, ...]
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    source: str = ""


class ProviderError(RuntimeError):
    pass


class RetryableProviderError(ProviderError):
    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class BlockedProviderError(ProviderError):
    pass


class MiningProvider(abc.ABC):
    provider_id: str
    label: str
    description: str
    config_example: dict[str, Any]
    test_only: bool = False

    @classmethod
    @abc.abstractmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Return a validated copy without changing provider record fields."""

    @classmethod
    @abc.abstractmethod
    def initial_cursor(cls, config: dict[str, Any]) -> Any:
        pass

    @classmethod
    def overlap_cursor(cls, cursor: Any, overlap: int, config: dict[str, Any]) -> Any:
        return cursor

    @abc.abstractmethod
    def fetch_page(self, cursor: Any, config: dict[str, Any], client: Any) -> FetchPage:
        pass

    @classmethod
    @abc.abstractmethod
    def validate_page(cls, page: FetchPage, config: dict[str, Any]) -> None:
        pass

    @classmethod
    @abc.abstractmethod
    def next_cursor(cls, cursor: Any, page: FetchPage, config: dict[str, Any]) -> Any | None:
        pass

    @classmethod
    def should_continue(cls, page: FetchPage, next_cursor: Any | None, config: dict[str, Any]) -> bool:
        return next_cursor is not None

    @classmethod
    @abc.abstractmethod
    def record_identity(cls, record: Any, config: dict[str, Any]) -> Any:
        pass

    @classmethod
    @abc.abstractmethod
    def event_time(cls, record: Any, config: dict[str, Any]) -> Any:
        pass

    @classmethod
    @abc.abstractmethod
    def is_final(cls, record: Any, config: dict[str, Any]) -> bool:
        pass

    @classmethod
    def cursor_for_gap(
        cls, config: dict[str, Any], missing_start: float, missing_end: float
    ) -> Any:
        raise ValueError(f"Provider '{cls.provider_id}' does not support automatic gap refill.")

    @classmethod
    def rate_limit_key(cls, config: dict[str, Any]) -> str:
        return cls.provider_id

    @classmethod
    def minimum_request_interval(cls, config: dict[str, Any]) -> float:
        return 0.25

    @classmethod
    def public_descriptor(cls) -> dict[str, Any]:
        return {
            "providerId": cls.provider_id,
            "label": cls.label,
            "description": cls.description,
            "configExample": cls.config_example,
        }


def record_entries(
    provider: type[MiningProvider], records: Iterable[Any], config: dict[str, Any]
) -> list[dict[str, Any]]:
    entries = []
    for record in records:
        identity = provider.record_identity(record, config)
        event_time = provider.event_time(record, config)
        if identity is None:
            raise ValueError("Provider returned a null record identity.")
        if event_time is None:
            raise ValueError("Provider returned a null event time.")
        event_sort = None
        if type(event_time) in {int, float}:
            try:
                event_sort = float(event_time)
            except OverflowError as exc:
                raise ValueError("Provider returned a non-finite numeric event time.") from exc
            if not math.isfinite(event_sort):
                raise ValueError("Provider returned a non-finite numeric event time.")
        entries.append({
            "record": record,
            "identity": identity,
            "identityHash": json_hash(identity),
            "eventTime": event_time,
            "eventTimeSort": event_sort,
            "recordHash": json_hash(record),
            "isFinal": bool(provider.is_final(record, config)),
        })
    return entries
