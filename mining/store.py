"""Crash-safe mining state, raw evidence, and provider-native partitions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .providers.base import canonical_json


ACTIVE_STATUSES = {"leased", "fetching", "committing"}
ALL_STATUSES = ACTIVE_STATUSES | {
    "queued",
    "retry_wait",
    "blocked",
    "paused",
    "succeeded",
}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
MINING_SCHEMA_VERSION = "1"
MINING_SCHEMA_FINGERPRINT = (
    "0adfb2e69b7b950da2e2f7d2144f4d949120562a7e2587a6e0c2d04011c1b4fc"
)


class MiningResourceNotFound(ValueError):
    """A syntactically valid Mining resource identifier has no stored resource."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def mining_root(config: dict[str, Any]) -> Path:
    configured = config.get("miningRoot")
    if not configured:
        raise ValueError("Mining requires an explicit independent miningRoot.")
    root = Path(configured).expanduser().resolve()
    engine_roots = {
        "controlRoot": Path(config["controlRoot"]).expanduser().resolve(),
        "releaseRoot": Path(config["releaseRoot"]).expanduser().resolve() if config.get("releaseRoot") else None,
        "liveRoot": Path(config["liveRoot"]).expanduser().resolve() if config.get("liveRoot") else None,
        "sourceRoot": Path(__file__).resolve().parents[1],
    }
    for label, engine_root in engine_roots.items():
        if engine_root is None:
            continue
        if root == engine_root or root in engine_root.parents or engine_root in root.parents:
            raise ValueError(f"miningRoot must not overlap {label}.")
    return root


def _json_load(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


class MiningStore:
    def __init__(self, config: dict[str, Any]):
        self.config = dict(config)
        self.root = mining_root(config)
        self.db_path = self.root / "mining-state.sqlite"
        self.raw_root = self.root / "raw"
        self.records_root = self.root / "records"
        self.manifest_root = self.root / "manifests"
        self.orphan_root = self.root / "orphaned"
        self.root.mkdir(parents=True, exist_ok=True)
        self.prepare()
        for path in (self.raw_root, self.records_root, self.manifest_root, self.orphan_root):
            path.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _connect_schema_read_only(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{self.db_path.resolve().as_uri()}?mode=ro",
            timeout=30,
            isolation_level=None,
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _require_job_exists(connection: sqlite3.Connection, job_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone() is None:
            raise MiningResourceNotFound(f"Mining job does not exist: {job_id}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def prepare(self) -> None:
        if self.db_path.exists():
            connection = self._connect_schema_read_only()
            try:
                existing_objects = connection.execute(
                    """SELECT 1 FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%' LIMIT 1"""
                ).fetchone()
                if existing_objects is not None:
                    self._require_exact_current_schema(connection)
                    return
            except sqlite3.DatabaseError as exc:
                raise ValueError(
                    "Mining database cannot prove the exact current schema authority."
                ) from exc
            finally:
                connection.close()

        connection = self._connect()
        try:
            existing_objects = connection.execute(
                """SELECT 1 FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%' LIMIT 1"""
            ).fetchone()
            if existing_objects is not None:
                self._require_exact_current_schema(connection)
                return
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT INTO schema_meta(key, value) VALUES ('schemaVersion', '1');

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_config_json TEXT NOT NULL,
                    schedule_seconds INTEGER NOT NULL,
                    overlap_records INTEGER NOT NULL DEFAULT 2,
                    continuity_step REAL,
                    status TEXT NOT NULL,
                    cursor_json TEXT,
                    active_cursor_json TEXT,
                    active_lane TEXT,
                    active_refill_id TEXT,
                    next_run_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    heartbeat_at REAL,
                    pause_requested INTEGER NOT NULL DEFAULT 0,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_success_at TEXT,
                    last_error TEXT,
                    blocked_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(status IN ('queued','leased','fetching','committing','retry_wait','blocked','paused','succeeded'))
                );
                CREATE INDEX IF NOT EXISTS jobs_due_idx ON jobs(status, next_run_at);
                CREATE INDEX IF NOT EXISTS jobs_lease_idx ON jobs(lease_expires_at);

                CREATE TABLE IF NOT EXISTS pages (
                    page_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    lane TEXT NOT NULL,
                    refill_id TEXT,
                    request_cursor_json TEXT,
                    next_cursor_json TEXT,
                    raw_relpath TEXT NOT NULL UNIQUE,
                    records_relpath TEXT NOT NULL UNIQUE,
                    raw_sha256 TEXT NOT NULL,
                    raw_bytes INTEGER NOT NULL,
                    record_count INTEGER NOT NULL,
                    response_status INTEGER NOT NULL,
                    response_headers_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    committed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS pages_job_idx ON pages(job_id, committed_at);

                CREATE TABLE IF NOT EXISTS record_versions (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    identity_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    identity_json TEXT NOT NULL,
                    event_time_json TEXT NOT NULL,
                    event_time_sort REAL,
                    record_hash TEXT NOT NULL,
                    is_final INTEGER NOT NULL,
                    records_relpath TEXT NOT NULL,
                    line_number INTEGER NOT NULL,
                    page_id TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
                    observed_at TEXT NOT NULL,
                    is_current INTEGER NOT NULL,
                    PRIMARY KEY(job_id, identity_hash, revision)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS record_current_idx
                    ON record_versions(job_id, identity_hash) WHERE is_current = 1;
                CREATE INDEX IF NOT EXISTS record_time_idx
                    ON record_versions(job_id, is_current, event_time_sort, identity_hash);

                CREATE TABLE IF NOT EXISTS record_observations (
                    page_id TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
                    line_number INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    identity_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(page_id, line_number)
                );
                CREATE INDEX IF NOT EXISTS observations_job_idx
                    ON record_observations(job_id);

                CREATE TABLE IF NOT EXISTS gaps (
                    gap_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    previous_event_time REAL NOT NULL,
                    next_event_time REAL NOT NULL,
                    missing_start REAL NOT NULL,
                    missing_end REAL NOT NULL,
                    estimated_missing INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    first_detected_at TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL,
                    resolved_at TEXT,
                    UNIQUE(job_id, previous_event_time, next_event_time)
                );
                CREATE INDEX IF NOT EXISTS gaps_job_idx ON gaps(job_id, status, previous_event_time);

                CREATE TABLE IF NOT EXISTS refill_tasks (
                    refill_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    gap_id TEXT REFERENCES gaps(gap_id),
                    cursor_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    CHECK(status IN ('queued','leased','succeeded','failed'))
                );
                CREATE INDEX IF NOT EXISTS refill_due_idx ON refill_tasks(job_id, status, created_at);

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    job_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_time_idx ON events(event_id DESC);

                CREATE TABLE IF NOT EXISTS metrics (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worker_state (
                    worker_id TEXT PRIMARY KEY,
                    pid INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    current_job_id TEXT,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS provider_rate_slots (
                    rate_key TEXT PRIMARY KEY,
                    reserved_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    checked_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO schema_meta(key,value) VALUES('schemaFingerprint',?)",
                (MINING_SCHEMA_FINGERPRINT,),
            )
            self._require_exact_current_schema(connection)
        finally:
            connection.close()

    @staticmethod
    def _schema_fingerprint(connection: sqlite3.Connection) -> str:
        rows = connection.execute(
            """SELECT type,name,tbl_name,sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"""
        ).fetchall()
        material = json.dumps(
            [tuple(row) for row in rows],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @classmethod
    def _require_exact_current_schema(cls, connection: sqlite3.Connection) -> None:
        try:
            metadata = {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key,value FROM schema_meta")
            }
        except sqlite3.DatabaseError as exc:
            raise ValueError(
                "Mining database is missing its schema authority."
            ) from exc
        expected_metadata = {
            "schemaVersion": MINING_SCHEMA_VERSION,
            "schemaFingerprint": MINING_SCHEMA_FINGERPRINT,
        }
        if metadata != expected_metadata:
            raise ValueError(
                "Mining database schema authority does not exactly match the current "
                f"schema: expected version {MINING_SCHEMA_VERSION} and the current "
                "schema fingerprint."
            )
        if cls._schema_fingerprint(connection) != MINING_SCHEMA_FINGERPRINT:
            raise ValueError(
                "Mining database structure does not match its declared schema."
            )

    @staticmethod
    def _row_job(row: sqlite3.Row, *, include_config: bool = True) -> dict[str, Any]:
        result = {
            "jobId": row["job_id"],
            "name": row["name"],
            "provider": row["provider"],
            "scheduleSeconds": row["schedule_seconds"],
            "overlapRecords": row["overlap_records"],
            "continuityStep": row["continuity_step"],
            "status": row["status"],
            "cursor": _json_load(row["cursor_json"]),
            "activeCursor": _json_load(row["active_cursor_json"]),
            "activeLane": row["active_lane"],
            "activeRefillId": row["active_refill_id"],
            "nextRunAt": row["next_run_at"],
            "leaseOwner": row["lease_owner"],
            "leaseExpiresAt": row["lease_expires_at"],
            "heartbeatAt": row["heartbeat_at"],
            "pauseRequested": bool(row["pause_requested"]),
            "attemptCount": row["attempt_count"],
            "consecutiveFailures": row["consecutive_failures"],
            "lastSuccessAt": row["last_success_at"],
            "lastError": row["last_error"],
            "blockedReason": row["blocked_reason"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        if include_config:
            result["providerConfig"] = _json_load(row["provider_config_json"], {})
        return result

    def create_job(
        self,
        *,
        job_id: str,
        name: str,
        provider: str,
        provider_config: dict[str, Any],
        initial_cursor: Any,
        schedule_seconds: int,
        overlap_records: int,
        continuity_step: float | None,
    ) -> dict[str, Any]:
        if type(job_id) is not str:
            raise ValueError("Mining jobId must be a string.")
        if not SAFE_ID.fullmatch(job_id):
            raise ValueError("Mining jobId must be 3-80 lowercase letters, digits, or hyphens.")
        if type(name) is not str:
            raise ValueError("Mining job name must be a string.")
        name = name.strip()
        if not name or len(name) > 160:
            raise ValueError("Mining job name must contain 1-160 characters.")
        if type(provider) is not str or not 1 <= len(provider) <= 80:
            raise ValueError("Mining provider ID must be a 1-80 character string.")
        if type(provider_config) is not dict:
            raise ValueError("Mining provider config must be an exact JSON object.")
        if type(schedule_seconds) is not int:
            raise ValueError("Mining scheduleSeconds must be an integer.")
        if type(overlap_records) is not int:
            raise ValueError("Mining overlapRecords must be an integer.")
        if not 1 <= schedule_seconds <= 31_536_000:
            raise ValueError("Mining scheduleSeconds must be between 1 and 31536000.")
        if not 0 <= overlap_records <= 1_000_000:
            raise ValueError("Mining overlapRecords must be between 0 and 1000000.")
        if continuity_step is not None:
            if type(continuity_step) not in {int, float}:
                raise ValueError("Mining continuityStep must be a finite number.")
            continuity_step = float(continuity_step)
            if not math.isfinite(continuity_step) or not 0 < continuity_step <= 1e15:
                raise ValueError("Mining continuityStep must be finite and between 0 and 1e15.")
        try:
            provider_config_json = canonical_json(provider_config)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Mining provider config must be exact finite JSON.") from exc
        try:
            initial_cursor_json = canonical_json(initial_cursor)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Mining initial cursor must be exact finite JSON.") from exc
        now_text = utc_now()
        now = time.time()
        try:
            with self.transaction() as connection:
                connection.execute(
                    """INSERT INTO jobs(
                        job_id,name,provider,provider_config_json,schedule_seconds,
                        overlap_records,continuity_step,status,cursor_json,next_run_at,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id,
                        name,
                        provider,
                        provider_config_json,
                        schedule_seconds,
                        overlap_records,
                        continuity_step,
                        "queued",
                        initial_cursor_json,
                        now,
                        now_text,
                        now_text,
                    ),
                )
                self._event(connection, "job.created", job_id, {"provider": provider})
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Mining job already exists: {job_id}") from exc
        return self.get_job(job_id)

    def list_jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT j.*,
                    (SELECT COUNT(*) FROM pages p WHERE p.job_id=j.job_id) AS page_count,
                    (SELECT COUNT(*) FROM record_versions r WHERE r.job_id=j.job_id AND r.is_current=1) AS current_records,
                    (SELECT COUNT(*) FROM record_observations o WHERE o.job_id=j.job_id) AS observations,
                    (SELECT COUNT(*) FROM gaps g WHERE g.job_id=j.job_id AND g.status!='resolved') AS open_gaps
                    FROM jobs j ORDER BY j.created_at DESC LIMIT ?""",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            values = []
            for row in rows:
                item = self._row_job(row, include_config=False)
                item.update({
                    "pageCount": row["page_count"],
                    "currentRecords": row["current_records"],
                    "observations": row["observations"],
                    "openGaps": row["open_gaps"],
                })
                values.append(item)
            return values
        finally:
            connection.close()

    def get_job(self, job_id: str, *, internal: bool = False) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise MiningResourceNotFound(f"Mining job does not exist: {job_id}")
            result = self._row_job(row, include_config=internal)
            counts = connection.execute(
                """SELECT
                    (SELECT COUNT(*) FROM pages WHERE job_id=?) page_count,
                    (SELECT COUNT(*) FROM record_versions WHERE job_id=? AND is_current=1) current_records,
                    (SELECT COUNT(*) FROM record_observations WHERE job_id=?) observations,
                    (SELECT COUNT(*) FROM record_versions WHERE job_id=? AND revision>1) revisions,
                    (SELECT COUNT(*) FROM gaps WHERE job_id=? AND status!='resolved') open_gaps""",
                (job_id, job_id, job_id, job_id, job_id),
            ).fetchone()
            result.update({
                "pageCount": counts["page_count"],
                "currentRecords": counts["current_records"],
                "observations": counts["observations"],
                "revisions": counts["revisions"],
                "openGaps": counts["open_gaps"],
            })
            return result
        finally:
            connection.close()

    def pause_job(self, job_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise MiningResourceNotFound(f"Mining job does not exist: {job_id}")
            if row["status"] in ACTIVE_STATUSES:
                connection.execute(
                    "UPDATE jobs SET pause_requested=1,updated_at=? WHERE job_id=?",
                    (utc_now(), job_id),
                )
            else:
                connection.execute(
                    """UPDATE jobs SET status='paused',pause_requested=0,next_run_at=?,
                    lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,updated_at=? WHERE job_id=?""",
                    (time.time(), utc_now(), job_id),
                )
            self._event(connection, "job.pause-requested", job_id, {})
        return self.get_job(job_id)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE jobs SET status='queued',pause_requested=0,next_run_at=?,
                lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
                blocked_reason=NULL,last_error=NULL,updated_at=? WHERE job_id=?""",
                (time.time(), utc_now(), job_id),
            ).rowcount
            if not changed:
                raise MiningResourceNotFound(f"Mining job does not exist: {job_id}")
            self._event(connection, "job.resumed", job_id, {})
        return self.get_job(job_id)

    def run_now(self, job_id: str) -> dict[str, Any]:
        active = False
        with self.transaction() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise MiningResourceNotFound(f"Mining job does not exist: {job_id}")
            if row["status"] in ACTIVE_STATUSES:
                active = True
            elif row["status"] == "paused":
                raise ValueError("Resume the paused mining job before running it.")
            else:
                connection.execute(
                    """UPDATE jobs SET status='queued',next_run_at=?,blocked_reason=NULL,
                    last_error=NULL,updated_at=? WHERE job_id=?""",
                    (time.time(), utc_now(), job_id),
                )
                self._event(connection, "job.run-now", job_id, {})
        return self.get_job(job_id)

    def recover_expired_leases(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT job_id,pause_requested FROM jobs
                WHERE status IN ('leased','fetching','committing')
                AND (lease_expires_at IS NULL OR lease_expires_at < ?)""",
                (now,),
            ).fetchall()
            for row in rows:
                status = "paused" if row["pause_requested"] else "retry_wait"
                connection.execute(
                    """UPDATE jobs SET status=?,next_run_at=?,lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,active_cursor_json=NULL,
                    active_lane=NULL,active_refill_id=NULL,pause_requested=0,
                    last_error='Worker lease expired; work was recovered.',updated_at=? WHERE job_id=?""",
                    (status, now, utc_now(), row["job_id"]),
                )
                connection.execute(
                    "UPDATE refill_tasks SET status='queued',lease_owner=NULL,updated_at=? WHERE job_id=? AND status='leased'",
                    (utc_now(), row["job_id"]),
                )
                self._event(connection, "lease.recovered", row["job_id"], {})
            if rows:
                self._metric(connection, "lease_recoveries", len(rows))
            return len(rows)

    def claim_next(
        self,
        owner: str,
        lease_seconds: float = 45.0,
        *,
        allowed_providers: frozenset[str] | set[str],
    ) -> dict[str, Any] | None:
        if type(allowed_providers) not in {set, frozenset} or any(
            type(provider_id) is not str for provider_id in allowed_providers
        ):
            raise ValueError("Mining allowed providers must be an exact set of IDs.")
        now = time.time()
        with self.transaction() as connection:
            while True:
                row = connection.execute(
                    """SELECT * FROM jobs
                    WHERE status IN ('queued','retry_wait','succeeded')
                    AND next_run_at<=? AND pause_requested=0
                    ORDER BY next_run_at,created_at LIMIT 1""",
                    (now,),
                ).fetchone()
                if row is None or row["provider"] in allowed_providers:
                    break
                reason = (
                    f"Mining provider is not enabled for worker admission: "
                    f"{row['provider']}"
                )
                connection.execute(
                    """UPDATE jobs SET status='blocked',lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,active_cursor_json=NULL,
                    active_lane=NULL,active_refill_id=NULL,blocked_reason=?,last_error=?,
                    updated_at=? WHERE job_id=?""",
                    (reason, reason, utc_now(), row["job_id"]),
                )
                connection.execute(
                    """UPDATE refill_tasks SET status='failed',lease_owner=NULL,
                    last_error=?,updated_at=? WHERE job_id=? AND status='queued'""",
                    (reason, utc_now(), row["job_id"]),
                )
                self._event(
                    connection,
                    "job.blocked",
                    row["job_id"],
                    {"reason": reason},
                )
                self._metric(connection, "blocked_failures", 1)
            if not row:
                return None
            attempt = int(row["attempt_count"]) + 1
            connection.execute(
                """UPDATE jobs SET status='leased',lease_owner=?,lease_expires_at=?,
                heartbeat_at=?,attempt_count=?,active_cursor_json=NULL,active_lane=NULL,
                active_refill_id=NULL,updated_at=? WHERE job_id=?""",
                (owner, now + lease_seconds, now, attempt, utc_now(), row["job_id"]),
            )
            refill = connection.execute(
                """SELECT * FROM refill_tasks WHERE job_id=? AND status='queued'
                ORDER BY created_at LIMIT 1""",
                (row["job_id"],),
            ).fetchone()
            if refill:
                connection.execute(
                    "UPDATE refill_tasks SET status='leased',lease_owner=?,updated_at=? WHERE refill_id=?",
                    (owner, utc_now(), refill["refill_id"]),
                )
            self._event(connection, "job.leased", row["job_id"], {"owner": owner, "attempt": attempt})
            result = self._row_job(
                connection.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
            )
            result["refill"] = dict(refill) if refill else None
            if result["refill"]:
                result["refill"]["cursor"] = _json_load(result["refill"].pop("cursor_json"))
            return result

    def begin_active(
        self,
        job_id: str,
        owner: str,
        *,
        lane: str,
        cursor: Any,
        refill_id: str | None,
        lease_seconds: float = 45.0,
    ) -> None:
        if lane not in {"main", "refill"}:
            raise ValueError(f"Invalid mining lane: {lane}")
        now = time.time()
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE jobs SET active_cursor_json=?,active_lane=?,active_refill_id=?,
                lease_expires_at=?,heartbeat_at=?,updated_at=?
                WHERE job_id=? AND lease_owner=? AND status='leased'""",
                (canonical_json(cursor), lane, refill_id, now + lease_seconds, now, utc_now(), job_id, owner),
            ).rowcount
            if not changed:
                raise RuntimeError("Mining lease was lost before fetch began.")

    def transition(self, job_id: str, owner: str, status: str, lease_seconds: float = 45.0) -> None:
        if status not in {"fetching", "committing", "leased"}:
            raise ValueError(f"Invalid active transition: {status}")
        now = time.time()
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE jobs SET status=?,heartbeat_at=?,lease_expires_at=?,updated_at=?
                WHERE job_id=? AND lease_owner=? AND status IN ('leased','fetching','committing')""",
                (status, now, now + lease_seconds, utc_now(), job_id, owner),
            ).rowcount
            if not changed:
                raise RuntimeError("Mining worker no longer owns this job lease.")

    def heartbeat(self, job_id: str, owner: str, lease_seconds: float = 45.0) -> bool:
        now = time.time()
        with self.transaction() as connection:
            return bool(connection.execute(
                """UPDATE jobs SET heartbeat_at=?,lease_expires_at=?,updated_at=?
                WHERE job_id=? AND lease_owner=? AND status IN ('leased','fetching','committing')""",
                (now, now + lease_seconds, utc_now(), job_id, owner),
            ).rowcount)

    def retry_job(
        self,
        job_id: str,
        owner: str,
        error: str,
        *,
        retry_after: float | None = None,
        jitter: float = 0.0,
    ) -> float:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT consecutive_failures,pause_requested FROM jobs WHERE job_id=? AND lease_owner=?",
                (job_id, owner),
            ).fetchone()
            if not row:
                return 0.0
            failures = int(row["consecutive_failures"]) + 1
            delay = retry_after if retry_after is not None else min(900.0, 2.0 ** min(failures, 9))
            delay = max(0.0, float(delay)) + max(0.0, float(jitter))
            status = "paused" if row["pause_requested"] else "retry_wait"
            connection.execute(
                """UPDATE jobs SET status=?,next_run_at=?,lease_owner=NULL,lease_expires_at=NULL,
                heartbeat_at=NULL,active_cursor_json=NULL,active_lane=NULL,active_refill_id=NULL,
                pause_requested=0,consecutive_failures=?,last_error=?,updated_at=? WHERE job_id=?""",
                (status, time.time() + delay, failures, str(error)[:1000], utc_now(), job_id),
            )
            connection.execute(
                "UPDATE refill_tasks SET status='queued',lease_owner=NULL,last_error=?,updated_at=? WHERE job_id=? AND status='leased'",
                (str(error)[:1000], utc_now(), job_id),
            )
            self._event(connection, "job.retry-wait", job_id, {"delaySeconds": delay, "error": str(error)[:300]})
            self._metric(connection, "retries", 1)
            return delay

    def block_job(self, job_id: str, owner: str, reason: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE jobs SET status='blocked',lease_owner=NULL,lease_expires_at=NULL,
                heartbeat_at=NULL,active_cursor_json=NULL,active_lane=NULL,active_refill_id=NULL,
                blocked_reason=?,last_error=?,updated_at=? WHERE job_id=? AND lease_owner=?""",
                (str(reason)[:1000], str(reason)[:1000], utc_now(), job_id, owner),
            )
            connection.execute(
                "UPDATE refill_tasks SET status='failed',lease_owner=NULL,last_error=?,updated_at=? WHERE job_id=? AND status='leased'",
                (str(reason)[:1000], utc_now(), job_id),
            )
            self._event(connection, "job.blocked", job_id, {"reason": str(reason)[:300]})
            self._metric(connection, "blocked_failures", 1)

    def release_job(self, job_id: str, owner: str) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT pause_requested FROM jobs WHERE job_id=? AND lease_owner=?",
                (job_id, owner),
            ).fetchone()
            if not row:
                return
            status = "paused" if row["pause_requested"] else "queued"
            connection.execute(
                """UPDATE jobs SET status=?,next_run_at=?,lease_owner=NULL,lease_expires_at=NULL,
                heartbeat_at=NULL,active_cursor_json=NULL,active_lane=NULL,active_refill_id=NULL,
                pause_requested=0,updated_at=? WHERE job_id=?""",
                (status, time.time(), utc_now(), job_id),
            )
            connection.execute(
                "UPDATE refill_tasks SET status='queued',lease_owner=NULL,updated_at=? WHERE job_id=? AND status='leased'",
                (utc_now(), job_id),
            )

    def pause_requested(self, job_id: str, owner: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT pause_requested FROM jobs WHERE job_id=? AND lease_owner=?",
                (job_id, owner),
            ).fetchone()
            return bool(row and row["pause_requested"])
        finally:
            connection.close()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_bytes(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
            self._fsync_directory(target.parent)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def commit_page(
        self,
        *,
        job_id: str,
        owner: str,
        lane: str,
        refill_id: str | None,
        request_cursor: Any,
        next_cursor: Any | None,
        raw: bytes,
        response_status: int,
        response_headers: dict[str, str],
        source: str,
        entries: list[dict[str, Any]],
        continue_fetch: bool | None = None,
        lease_seconds: float = 45.0,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)):
            raise ValueError("Mining leaseSeconds must be a finite number.")
        lease_seconds = float(lease_seconds)
        if not math.isfinite(lease_seconds) or lease_seconds < 5:
            raise ValueError("Mining leaseSeconds must be finite and at least 5 seconds.")
        page_id = uuid.uuid4().hex
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_path = self.raw_root / job_id / day / f"{page_id}.response"
        records_path = self.records_root / job_id / day / f"{page_id}.jsonl"
        records_bytes = b"".join(
            (canonical_json(entry["record"]) + "\n").encode("utf-8") for entry in entries
        )
        self._atomic_bytes(raw_path, raw)
        self._atomic_bytes(records_path, records_bytes)
        if fault_stage == "after_files":
            raise RuntimeError("Injected crash after durable files and before SQLite commit.")

        raw_relpath = raw_path.relative_to(self.root).as_posix()
        records_relpath = records_path.relative_to(self.root).as_posix()
        raw_hash = hashlib.sha256(raw).hexdigest()
        committed_at = utc_now()
        revisions = 0
        affected_event_times: set[float] = {
            float(entry["eventTimeSort"])
            for entry in entries
            if entry["eventTimeSort"] is not None
        }
        if continue_fetch is None:
            continue_fetch = next_cursor is not None
        with self.transaction() as connection:
            lease = connection.execute(
                "SELECT * FROM jobs WHERE job_id=? AND lease_owner=? AND status='committing'",
                (job_id, owner),
            ).fetchone()
            if not lease:
                raise RuntimeError("Mining lease was lost before page commit.")
            connection.execute(
                """INSERT INTO pages(
                    page_id,job_id,lane,refill_id,request_cursor_json,next_cursor_json,
                    raw_relpath,records_relpath,raw_sha256,raw_bytes,record_count,
                    response_status,response_headers_json,source,committed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    page_id,
                    job_id,
                    lane,
                    refill_id,
                    canonical_json(request_cursor),
                    canonical_json(next_cursor) if next_cursor is not None else None,
                    raw_relpath,
                    records_relpath,
                    raw_hash,
                    len(raw),
                    len(entries),
                    int(response_status),
                    canonical_json(response_headers),
                    source,
                    committed_at,
                ),
            )
            for line_number, entry in enumerate(entries):
                current = connection.execute(
                    """SELECT * FROM record_versions WHERE job_id=? AND identity_hash=?
                    AND is_current=1""",
                    (job_id, entry["identityHash"]),
                ).fetchone()
                if current is not None and current["event_time_sort"] is not None:
                    affected_event_times.add(float(current["event_time_sort"]))
                if current is None:
                    revision = 1
                    insert_version = True
                elif current["record_hash"] == entry["recordHash"]:
                    revision = int(current["revision"])
                    insert_version = False
                    if entry["isFinal"] and not current["is_final"]:
                        connection.execute(
                            """UPDATE record_versions SET is_final=1
                            WHERE job_id=? AND identity_hash=? AND is_current=1""",
                            (job_id, entry["identityHash"]),
                        )
                else:
                    revision = int(current["revision"]) + 1
                    connection.execute(
                        "UPDATE record_versions SET is_current=0 WHERE job_id=? AND identity_hash=? AND is_current=1",
                        (job_id, entry["identityHash"]),
                    )
                    insert_version = True
                    revisions += 1
                if insert_version:
                    connection.execute(
                        """INSERT INTO record_versions(
                            job_id,identity_hash,revision,identity_json,event_time_json,
                            event_time_sort,record_hash,is_final,records_relpath,line_number,
                            page_id,observed_at,is_current
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                        (
                            job_id,
                            entry["identityHash"],
                            revision,
                            canonical_json(entry["identity"]),
                            canonical_json(entry["eventTime"]),
                            entry["eventTimeSort"],
                            entry["recordHash"],
                            int(entry["isFinal"]),
                            records_relpath,
                            line_number,
                            page_id,
                            committed_at,
                        ),
                    )
                connection.execute(
                    """INSERT INTO record_observations(
                        page_id,line_number,job_id,identity_hash,record_hash,observed_at
                    ) VALUES(?,?,?,?,?,?)""",
                    (page_id, line_number, job_id, entry["identityHash"], entry["recordHash"], committed_at),
                )

            self._recompute_gaps_incremental(
                connection, job_id, lease["continuity_step"], affected_event_times
            )
            paused = bool(lease["pause_requested"])
            queued_refill = connection.execute(
                """SELECT 1 FROM refill_tasks
                WHERE job_id=? AND status='queued' LIMIT 1""",
                (job_id,),
            ).fetchone()
            if paused:
                status = "paused"
                next_run_at = time.time()
                clear_lease = True
            elif lane == "main" and queued_refill is not None:
                # A refill may be queued while this main page is in flight.
                # Yield at the durable page boundary so a long normal schedule
                # cannot postpone that explicitly requested repair.
                status = "queued"
                next_run_at = time.time()
                clear_lease = True
            elif continue_fetch and next_cursor is not None:
                status = "leased"
                next_run_at = lease["next_run_at"]
                clear_lease = False
            elif lane == "refill":
                status = "queued"
                next_run_at = time.time()
                clear_lease = True
            else:
                status = "succeeded"
                next_run_at = time.time() + int(lease["schedule_seconds"])
                clear_lease = True

            if lane == "main" and next_cursor is not None:
                connection.execute(
                    "UPDATE jobs SET cursor_json=? WHERE job_id=?",
                    (canonical_json(next_cursor), job_id),
                )
            if lane == "refill" and refill_id:
                if not continue_fetch or next_cursor is None:
                    connection.execute(
                        """UPDATE refill_tasks SET status='succeeded',lease_owner=NULL,
                        updated_at=?,last_error=NULL WHERE refill_id=?""",
                        (committed_at, refill_id),
                    )
                else:
                    connection.execute(
                        "UPDATE refill_tasks SET cursor_json=?,updated_at=? WHERE refill_id=?",
                        (canonical_json(next_cursor), committed_at, refill_id),
                    )
            connection.execute(
                """UPDATE jobs SET status=?,next_run_at=?,lease_owner=?,lease_expires_at=?,
                heartbeat_at=?,active_cursor_json=?,active_lane=?,active_refill_id=?,
                pause_requested=0,consecutive_failures=0,last_error=NULL,blocked_reason=NULL,
                last_success_at=?,updated_at=? WHERE job_id=?""",
                (
                    status,
                    next_run_at,
                    None if clear_lease else owner,
                    None if clear_lease else time.time() + lease_seconds,
                    None if clear_lease else time.time(),
                    canonical_json(next_cursor) if (not clear_lease and next_cursor is not None) else None,
                    lane if not clear_lease else None,
                    refill_id if not clear_lease else None,
                    committed_at,
                    committed_at,
                    job_id,
                ),
            )
            self._metric(connection, "pages_committed", 1)
            self._metric(connection, "bytes_stored", len(raw) + len(records_bytes))
            self._metric(connection, "records_observed", len(entries))
            self._metric(connection, "revisions_detected", revisions)
            self._event(connection, "page.committed", job_id, {
                "pageId": page_id,
                "lane": lane,
                "records": len(entries),
                "revisions": revisions,
                "rawSha256": raw_hash,
            })
        if fault_stage == "after_db":
            raise RuntimeError("Injected crash after SQLite commit.")
        try:
            self._write_manifest_checkpoint(
                job_id=job_id,
                page_id=page_id,
                cursor=next_cursor if next_cursor is not None else request_cursor,
                status=status,
                committed_at=committed_at,
            )
        except OSError:
            # The checkpoint is a cache. SQLite and durable page files remain
            # authoritative, and explicit manifest reads rebuild it.
            self.metric("manifest_checkpoint_errors", 1)
        return {
            "pageId": page_id,
            "recordCount": len(entries),
            "revisions": revisions,
            "rawSha256": raw_hash,
            "nextCursor": next_cursor,
            "status": status,
        }

    def _recompute_gaps_incremental(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        step: float | None,
        affected_event_times: set[float],
    ) -> None:
        """Re-evaluate only adjacency neighborhoods touched by this page."""
        if step is None or float(step) <= 0 or not affected_event_times:
            return
        step = float(step)
        checked_at = utc_now()
        touched_gaps: dict[tuple[float, float], str] = {}
        current_gap_pairs: set[tuple[float, float]] = set()
        local_pairs: set[tuple[float, float]] = set()
        for event_time in affected_event_times:
            for gap in connection.execute(
                """SELECT gap_id,previous_event_time,next_event_time FROM gaps
                WHERE job_id=? AND status!='resolved'
                AND previous_event_time<=? AND next_event_time>=?""",
                (job_id, event_time, event_time),
            ):
                touched_gaps[(float(gap["previous_event_time"]), float(gap["next_event_time"]))] = gap["gap_id"]
            before = connection.execute(
                """SELECT DISTINCT event_time_sort FROM record_versions
                WHERE job_id=? AND is_current=1 AND event_time_sort IS NOT NULL
                AND event_time_sort<=? ORDER BY event_time_sort DESC LIMIT 2""",
                (job_id, event_time),
            ).fetchall()
            after = connection.execute(
                """SELECT DISTINCT event_time_sort FROM record_versions
                WHERE job_id=? AND is_current=1 AND event_time_sort IS NOT NULL
                AND event_time_sort>=? ORDER BY event_time_sort LIMIT 2""",
                (job_id, event_time),
            ).fetchall()
            neighborhood = sorted({float(row[0]) for row in [*before, *after]})
            local_pairs.update(zip(neighborhood, neighborhood[1:]))

        for previous, following in local_pairs:
            estimated = max(0, int(round((following - previous) / step)) - 1)
            if estimated <= 0 or following - previous <= step * 1.5:
                continue
            current_gap_pairs.add((previous, following))
            gap_id = hashlib.sha256(
                f"{job_id}:{previous:.12g}:{following:.12g}".encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                """INSERT INTO gaps(
                    gap_id,job_id,previous_event_time,next_event_time,missing_start,
                    missing_end,estimated_missing,status,first_detected_at,last_checked_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,previous_event_time,next_event_time) DO UPDATE SET
                    missing_start=excluded.missing_start,missing_end=excluded.missing_end,
                    estimated_missing=excluded.estimated_missing,
                    status=CASE WHEN gaps.status='backfill_queued' THEN gaps.status ELSE 'open' END,
                    last_checked_at=excluded.last_checked_at,resolved_at=NULL""",
                (
                    gap_id,
                    job_id,
                    previous,
                    following,
                    previous + step,
                    following - step,
                    estimated,
                    "open",
                    checked_at,
                    checked_at,
                ),
            )
        for pair, gap_id in touched_gaps.items():
            if pair not in current_gap_pairs:
                connection.execute(
                    "UPDATE gaps SET status='resolved',resolved_at=?,last_checked_at=? WHERE gap_id=?",
                    (checked_at, checked_at, gap_id),
                )

    def list_gaps(self, job_id: str, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            self._require_job_exists(connection, job_id)
            clause = "" if include_resolved else "AND status!='resolved'"
            rows = connection.execute(
                f"SELECT * FROM gaps WHERE job_id=? {clause} ORDER BY previous_event_time",
                (job_id,),
            ).fetchall()
            return [{
                "gapId": row["gap_id"],
                "jobId": row["job_id"],
                "previousEventTime": row["previous_event_time"],
                "nextEventTime": row["next_event_time"],
                "missingStart": row["missing_start"],
                "missingEnd": row["missing_end"],
                "estimatedMissing": row["estimated_missing"],
                "status": row["status"],
                "firstDetectedAt": row["first_detected_at"],
                "lastCheckedAt": row["last_checked_at"],
                "resolvedAt": row["resolved_at"],
            } for row in rows]
        finally:
            connection.close()

    def queue_refill(self, job_id: str, gap_id: str, cursor: Any) -> dict[str, Any]:
        refill_id = uuid.uuid4().hex
        now = utc_now()
        with self.transaction() as connection:
            self._require_job_exists(connection, job_id)
            gap = connection.execute(
                "SELECT * FROM gaps WHERE job_id=? AND gap_id=? AND status!='resolved'",
                (job_id, gap_id),
            ).fetchone()
            if not gap:
                raise MiningResourceNotFound(
                    f"Open mining gap does not exist: {gap_id}"
                )
            existing = connection.execute(
                "SELECT * FROM refill_tasks WHERE gap_id=? AND status IN ('queued','leased')",
                (gap_id,),
            ).fetchone()
            if existing:
                return {
                    "refillId": existing["refill_id"],
                    "jobId": existing["job_id"],
                    "gapId": existing["gap_id"],
                    "cursor": _json_load(existing["cursor_json"]),
                    "status": existing["status"],
                }
            connection.execute(
                """INSERT INTO refill_tasks(
                    refill_id,job_id,gap_id,cursor_json,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (refill_id, job_id, gap_id, canonical_json(cursor), "queued", now, now),
            )
            connection.execute(
                "UPDATE gaps SET status='backfill_queued',last_checked_at=? WHERE gap_id=?",
                (now, gap_id),
            )
            connection.execute(
                """UPDATE jobs SET status=CASE WHEN status IN ('paused','leased','fetching','committing')
                    THEN status ELSE 'queued' END,next_run_at=?,updated_at=? WHERE job_id=?""",
                (time.time(), now, job_id),
            )
            self._event(connection, "gap.refill-queued", job_id, {"gapId": gap_id, "refillId": refill_id})
        return {"refillId": refill_id, "jobId": job_id, "gapId": gap_id, "cursor": cursor, "status": "queued"}

    def list_records(self, job_id: str, limit: int = 100) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            self._require_job_exists(connection, job_id)
            rows = connection.execute(
                """SELECT * FROM record_versions WHERE job_id=? AND is_current=1
                ORDER BY event_time_sort DESC,observed_at DESC LIMIT ?""",
                (job_id, max(1, min(int(limit), 500))),
            ).fetchall()
        finally:
            connection.close()
        handles: dict[str, Any] = {}
        values = []
        try:
            for row in rows:
                relative = row["records_relpath"]
                handle = handles.get(relative)
                if handle is None:
                    handle = (self.root / relative).open(encoding="utf-8")
                    handles[relative] = handle
                handle.seek(0)
                line = ""
                for _ in range(int(row["line_number"]) + 1):
                    line = handle.readline()
                values.append({
                    "identity": _json_load(row["identity_json"]),
                    "eventTime": _json_load(row["event_time_json"]),
                    "revision": row["revision"],
                    "isFinal": bool(row["is_final"]),
                    "recordHash": row["record_hash"],
                    "record": json.loads(line),
                    "observedAt": row["observed_at"],
                })
            return values
        finally:
            for handle in handles.values():
                handle.close()

    def export_records(self, job_id: str, target: str | Path) -> dict[str, Any]:
        self.get_job(job_id, internal=True)
        target = Path(target).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT records_relpath,line_number FROM record_versions
                WHERE job_id=? AND is_current=1
                ORDER BY CASE WHEN event_time_sort IS NULL THEN 1 ELSE 0 END,
                event_time_sort,identity_hash""",
                (job_id,),
            ).fetchall()
        finally:
            connection.close()
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                for row in rows:
                    with (self.root / row["records_relpath"]).open("rb") as source:
                        line = b""
                        for _ in range(int(row["line_number"]) + 1):
                            line = source.readline()
                    output.write(line)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            self._fsync_directory(target.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"jobId": job_id, "path": str(target), "recordCount": len(rows)}

    def recover_orphans(self) -> int:
        connection = self._connect()
        try:
            referenced = set()
            for row in connection.execute("SELECT raw_relpath,records_relpath FROM pages"):
                referenced.add(row["raw_relpath"])
                referenced.add(row["records_relpath"])
        finally:
            connection.close()
        candidates = list(self.raw_root.rglob("*.response")) + list(self.records_root.rglob("*.jsonl"))
        candidates += list(self.raw_root.rglob("*.tmp")) + list(self.records_root.rglob("*.tmp"))
        recovered = 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.orphan_root / stamp
        for path in candidates:
            relative = path.relative_to(self.root).as_posix()
            if relative in referenced:
                continue
            destination.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination / f"{uuid.uuid4().hex}-{path.name}"))
            recovered += 1
        if recovered:
            with self.transaction() as connection:
                self._metric(connection, "orphan_files_recovered", recovered)
                self._event(connection, "orphan.recovered", None, {"files": recovered})
        return recovered

    def _write_manifest_checkpoint(
        self,
        *,
        job_id: str,
        page_id: str,
        cursor: Any,
        status: str,
        committed_at: str,
    ) -> None:
        checkpoint = {
            "format": "trade-engine-mining-checkpoint-v1",
            "jobId": job_id,
            "latestPageId": page_id,
            "cursor": cursor,
            "status": status,
            "committedAt": committed_at,
            "sourceOfTruth": "mining-state.sqlite",
        }
        self._atomic_bytes(
            self.manifest_root / f"{job_id}.json",
            (json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def rebuild_manifest(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id, internal=True)
        connection = self._connect()
        try:
            pages = [dict(row) for row in connection.execute(
                """SELECT page_id,lane,refill_id,request_cursor_json,next_cursor_json,
                raw_relpath,records_relpath,raw_sha256,raw_bytes,record_count,committed_at
                FROM pages WHERE job_id=? ORDER BY committed_at DESC,page_id DESC LIMIT 100""",
                (job_id,),
            )]
        finally:
            connection.close()
        pages.reverse()
        manifest = {
            "format": "trade-engine-mining-provider-native-v1",
            "jobId": job_id,
            "provider": job["provider"],
            "recordContract": "provider-native; no Engine field normalization",
            "cursor": job["cursor"],
            "pageCount": job["pageCount"],
            "currentRecords": job["currentRecords"],
            "observations": job["observations"],
            "revisions": job["revisions"],
            "openGaps": job["openGaps"],
            "pagesTruncated": job["pageCount"] > len(pages),
            "pageIndex": "mining-state.sqlite:pages",
            "pages": [{
                "pageId": page["page_id"],
                "lane": page["lane"],
                "refillId": page["refill_id"],
                "requestCursor": _json_load(page["request_cursor_json"]),
                "nextCursor": _json_load(page["next_cursor_json"]),
                "raw": {"path": page["raw_relpath"], "sha256": page["raw_sha256"], "bytes": page["raw_bytes"]},
                "records": {"path": page["records_relpath"], "count": page["record_count"]},
                "committedAt": page["committed_at"],
            } for page in pages],
            "generatedAt": utc_now(),
        }
        self._atomic_bytes(
            self.manifest_root / f"{job_id}.json",
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        return manifest

    def manifest(self, job_id: str) -> dict[str, Any]:
        # Explicit reads rebuild a bounded view from the SQLite source of truth.
        # The hot commit path only writes a constant-size checkpoint.
        return self.rebuild_manifest(job_id)

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY event_id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            return [{
                "eventId": row["event_id"],
                "type": row["event_type"],
                "jobId": row["job_id"],
                "payload": _json_load(row["payload_json"], {}),
                "createdAt": row["created_at"],
            } for row in rows]
        finally:
            connection.close()

    def metric(self, key: str, amount: int = 1) -> None:
        with self.transaction() as connection:
            self._metric(connection, key, amount)

    def reserve_rate_slot(self, rate_key: str, minimum_interval: float) -> float:
        """Atomically reserve the next provider/host request time across restarts."""
        minimum_interval = float(minimum_interval)
        if not math.isfinite(minimum_interval) or not 0 <= minimum_interval <= 3600:
            raise ValueError("Provider request interval must be finite and between 0 and 3600.")
        if minimum_interval == 0:
            return 0.0
        now = time.time()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT reserved_at FROM provider_rate_slots WHERE rate_key=?", (rate_key,)
            ).fetchone()
            reserved_at = max(now, (float(row["reserved_at"]) + minimum_interval) if row else now)
            connection.execute(
                """INSERT INTO provider_rate_slots(rate_key,reserved_at) VALUES(?,?)
                ON CONFLICT(rate_key) DO UPDATE SET reserved_at=excluded.reserved_at""",
                (rate_key, reserved_at),
            )
        return max(0.0, reserved_at - now)

    @staticmethod
    def _metric(connection: sqlite3.Connection, key: str, amount: int) -> None:
        connection.execute(
            """INSERT INTO metrics(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=value+excluded.value""",
            (key, int(amount)),
        )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        event_type: str,
        job_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO events(event_type,job_id,payload_json,created_at) VALUES(?,?,?,?)",
            (event_type, job_id, canonical_json(payload), utc_now()),
        )

    def worker_heartbeat(
        self,
        worker_id: str,
        *,
        pid: int,
        status: str,
        current_job_id: str | None = None,
        error: str | None = None,
    ) -> None:
        now = time.time()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO worker_state(
                    worker_id,pid,status,started_at,heartbeat_at,current_job_id,last_error
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET pid=excluded.pid,status=excluded.status,
                    heartbeat_at=excluded.heartbeat_at,current_job_id=excluded.current_job_id,
                    last_error=excluded.last_error""",
                (worker_id, pid, status, utc_now(), now, current_job_id, error),
            )

    def run_integrity_check(self) -> dict[str, str]:
        connection = self._connect()
        try:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            connection.close()
        checked_at = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO health_state(key,value,checked_at) VALUES('integrity',?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,checked_at=excluded.checked_at""",
                (result, checked_at),
            )
        return {"value": result, "checkedAt": checked_at}

    def health(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("SELECT 1").fetchone()
            integrity_row = connection.execute(
                "SELECT value,checked_at FROM health_state WHERE key='integrity'"
            ).fetchone()
            workers = [dict(row) for row in connection.execute(
                "SELECT * FROM worker_state ORDER BY heartbeat_at DESC LIMIT 5"
            )]
            metrics = {row["key"]: row["value"] for row in connection.execute("SELECT * FROM metrics")}
            counts = connection.execute(
                """SELECT COUNT(*) jobs,
                SUM(CASE WHEN status IN ('leased','fetching','committing') THEN 1 ELSE 0 END) active,
                SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) blocked,
                SUM(CASE WHEN status='paused' THEN 1 ELSE 0 END) paused
                FROM jobs"""
            ).fetchone()
        finally:
            connection.close()
        now = time.time()
        worker_values = [{
            "workerId": row["worker_id"],
            "pid": row["pid"],
            "status": row["status"],
            "startedAt": row["started_at"],
            "heartbeatAt": row["heartbeat_at"],
            "heartbeatAgeSeconds": max(0.0, now - row["heartbeat_at"]),
            "currentJobId": row["current_job_id"],
            "lastError": row["last_error"],
        } for row in workers]
        latest_alive = any(
            worker["heartbeatAgeSeconds"] < 60 and worker["status"] != "stopped"
            for worker in worker_values
        )
        integrity = integrity_row["value"] if integrity_row else "not_checked"
        integrity_ok = integrity in {"ok", "not_checked"}
        return {
            "status": "ok" if integrity_ok and latest_alive else "degraded",
            "database": "connected",
            "integrity": integrity,
            "integrityCheckedAt": integrity_row["checked_at"] if integrity_row else None,
            "workerAlive": latest_alive,
            "workers": worker_values,
            "jobs": counts["jobs"] or 0,
            "active": counts["active"] or 0,
            "blocked": counts["blocked"] or 0,
            "paused": counts["paused"] or 0,
            "metrics": metrics,
        }
