"""Strict HTTP-facing service for the standalone mining subsystem."""

from __future__ import annotations

import re
import math
import uuid
from typing import Any

from .providers import enabled_provider_ids, get_provider, public_providers
from .store import MiningResourceNotFound, MiningStore, SAFE_ID


def _require_object(
    payload: Any, label: str = "Mining request body"
) -> dict[str, Any]:
    if type(payload) is not dict:
        raise ValueError(f"{label} must be a JSON object.")
    return payload


def _reject_unknown(payload: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Unknown mining request fields: {', '.join(unknown)}")


def _strict_query(query: dict[str, list[str]], allowed: set[str]) -> None:
    unknown = sorted(set(query) - allowed)
    if unknown:
        raise ValueError(f"Unknown mining query fields: {', '.join(unknown)}")
    repeated = sorted(key for key, values in query.items() if len(values) != 1)
    if repeated:
        raise ValueError(f"Mining query fields must appear exactly once: {', '.join(repeated)}")


def _query_limit(query: dict[str, list[str]], default: int, maximum: int) -> int:
    value = (query.get("limit") or [str(default)])[0]
    if type(value) is not str or re.fullmatch(r"[0-9]+", value, re.ASCII) is None:
        raise ValueError("Mining limit must be an ASCII decimal integer.")
    parsed = int(value)
    if not 1 <= parsed <= maximum:
        raise ValueError(f"Mining limit must be between 1 and {maximum}.")
    return parsed


def _job_id(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("Invalid mining jobId.")
    job_id = value
    if not SAFE_ID.fullmatch(job_id):
        raise ValueError("Invalid mining jobId.")
    return job_id


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _new_job_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]
    if len(slug) < 3:
        slug = "mining-job"
    return f"{slug}-{uuid.uuid4().hex[:8]}"


class MiningApi:
    def __init__(self, config: dict[str, Any], supervisor: Any = None):
        self.config = dict(config)
        self.store = MiningStore(config)
        self.supervisor = supervisor

    def handle_get(
        self, path: str, query: dict[str, list[str]]
    ) -> tuple[int, dict[str, Any]]:
        try:
            return self._handle_get(path, query)
        except MiningResourceNotFound as exc:
            return 404, {"error": str(exc)}

    def _handle_get(
        self, path: str, query: dict[str, list[str]]
    ) -> tuple[int, dict[str, Any]]:
        parts = [part for part in path.split("/") if part]
        if path == "/api/mining/providers":
            _strict_query(query, set())
            return 200, {"providers": public_providers(
                include_test=self.config.get("miningExposeTestProvider") is True
            )}
        if path == "/api/mining/health":
            _strict_query(query, set())
            health = self.store.health()
            if self.supervisor is not None:
                health["supervisor"] = self.supervisor.status()
            return 200, health
        if path == "/api/mining/jobs":
            _strict_query(query, {"limit"})
            limit = _query_limit(query, 200, 500)
            jobs = self.store.list_jobs(limit)
            return 200, {"jobs": jobs, "total": len(jobs)}
        if path == "/api/mining/events":
            _strict_query(query, {"limit"})
            limit = _query_limit(query, 100, 500)
            events = self.store.events(limit)
            return 200, {"events": events, "total": len(events)}
        if len(parts) == 4 and parts[:3] == ["api", "mining", "jobs"]:
            _strict_query(query, set())
            job_id = _job_id(parts[3])
            return 200, {
                "job": self.store.get_job(job_id),
                "gaps": self.store.list_gaps(job_id),
                "records": self.store.list_records(job_id, 50),
            }
        if len(parts) == 5 and parts[:3] == ["api", "mining", "jobs"]:
            job_id, resource = _job_id(parts[3]), parts[4]
            if resource == "records":
                _strict_query(query, {"limit"})
                limit = _query_limit(query, 100, 500)
                return 200, {"jobId": job_id, "records": self.store.list_records(job_id, limit)}
            if resource == "gaps":
                _strict_query(query, {"includeResolved"})
                include_resolved = (query.get("includeResolved") or [""])[0].lower() in {"1", "true", "yes"}
                if "includeResolved" in query and query["includeResolved"][0].lower() not in {"0", "1", "false", "true", "no", "yes"}:
                    raise ValueError("includeResolved must be a boolean value.")
                return 200, {"jobId": job_id, "gaps": self.store.list_gaps(job_id, include_resolved=include_resolved)}
            if resource == "manifest":
                _strict_query(query, set())
                return 200, self.store.manifest(job_id)
        return 404, {"error": "not found"}

    def handle_post(
        self,
        path: str,
        payload: Any,
        query: dict[str, list[str]] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        _strict_query(query if query is not None else {}, set())
        payload = _require_object(payload)
        try:
            return self._handle_post(path, payload, query)
        except MiningResourceNotFound as exc:
            return 404, {"accepted": False, "error": str(exc)}

    def _handle_post(
        self,
        path: str,
        payload: Any,
        query: dict[str, list[str]] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        parts = [part for part in path.split("/") if part]
        if path == "/api/mining/jobs":
            _reject_unknown(payload, {
                "jobId", "name", "provider", "providerConfig", "scheduleSeconds",
                "overlapRecords", "continuityStep",
            })
            raw_name = payload.get("name")
            if type(raw_name) is not str:
                raise ValueError("Mining job name must be a string.")
            name = raw_name.strip()
            if not 1 <= len(name) <= 160:
                raise ValueError("Mining job name must contain 1-160 characters.")
            provider_id = payload.get("provider")
            if type(provider_id) is not str:
                raise ValueError("Mining provider ID must be a string.")
            if not 1 <= len(provider_id) <= 80:
                raise ValueError("Mining provider ID must contain 1-80 characters.")
            provider_class = get_provider(provider_id)
            if provider_id not in enabled_provider_ids(
                include_test=self.config.get("miningExposeTestProvider") is True
            ):
                raise ValueError(f"Mining provider is not enabled: {provider_id}")
            raw_provider_config = (
                payload["providerConfig"] if "providerConfig" in payload else {}
            )
            provider_config = provider_class.validate_config(
                _require_object(raw_provider_config, "Mining providerConfig")
            )
            continuity = payload.get("continuityStep")
            if continuity is not None:
                if isinstance(continuity, bool) or not isinstance(continuity, (int, float)):
                    raise ValueError("continuityStep must be a finite number.")
                continuity = float(continuity)
                if not math.isfinite(continuity) or not 0 < continuity <= 1e15:
                    raise ValueError("continuityStep must be finite and between 0 and 1e15.")
            job = self.store.create_job(
                job_id=_job_id(
                    payload["jobId"] if "jobId" in payload else _new_job_id(name)
                ),
                name=name,
                provider=provider_class.provider_id,
                provider_config=provider_config,
                initial_cursor=provider_class.initial_cursor(provider_config),
                schedule_seconds=_bounded_int(payload.get("scheduleSeconds", 60), "scheduleSeconds", 1, 31_536_000),
                overlap_records=_bounded_int(payload.get("overlapRecords", 2), "overlapRecords", 0, 1_000_000),
                continuity_step=continuity,
            )
            return 201, {"accepted": True, "job": job}
        if len(parts) == 5 and parts[:3] == ["api", "mining", "jobs"]:
            _reject_unknown(payload, set())
            job_id, action = _job_id(parts[3]), parts[4]
            if action == "pause":
                return 200, {"accepted": True, "job": self.store.pause_job(job_id)}
            if action == "resume":
                return 200, {"accepted": True, "job": self.store.resume_job(job_id)}
            if action == "run-now":
                return 202, {"accepted": True, "job": self.store.run_now(job_id)}
        if (
            len(parts) == 7
            and parts[:3] == ["api", "mining", "jobs"]
            and parts[4] == "gaps"
            and parts[6] == "refill"
        ):
            _reject_unknown(payload, set())
            job_id, gap_id = _job_id(parts[3]), parts[5]
            if not re.fullmatch(r"[0-9a-f]{24}", gap_id):
                raise ValueError("Invalid mining gapId.")
            job = self.store.get_job(job_id, internal=True)
            gap = next(
                (item for item in self.store.list_gaps(job_id) if item["gapId"] == gap_id),
                None,
            )
            if not gap:
                raise MiningResourceNotFound(
                    f"Open mining gap does not exist: {gap_id}"
                )
            provider = get_provider(job["provider"])
            cursor = provider.cursor_for_gap(
                job["providerConfig"], gap["missingStart"], gap["missingEnd"]
            )
            refill = self.store.queue_refill(job_id, gap_id, cursor)
            return 202, {"accepted": True, "refill": refill}
        return 404, {"accepted": False, "error": "not found"}


class DisabledMiningApi:
    """Fail-closed API state that never creates storage without miningRoot."""

    error = "Mining is disabled; configure an independent miningRoot to enable it."

    def handle_get(
        self, path: str, query: dict[str, list[str]]
    ) -> tuple[int, dict[str, Any]]:
        if path == "/api/mining/providers":
            _strict_query(query, set())
            return 200, {"providers": public_providers()}
        if path == "/api/mining/health":
            _strict_query(query, set())
            return 200, {"status": "disabled", "workerAlive": False, "jobs": 0, "metrics": {}, "reason": self.error}
        if path == "/api/mining/jobs":
            _strict_query(query, {"limit"})
            if "limit" in query:
                _query_limit(query, 200, 500)
            return 200, {"jobs": [], "total": 0, "disabled": True}
        return 503, {"error": self.error}

    def handle_post(
        self,
        path: str,
        payload: Any,
        query: dict[str, list[str]] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        _strict_query(query if query is not None else {}, set())
        _require_object(payload)
        return 503, {"accepted": False, "error": self.error}
