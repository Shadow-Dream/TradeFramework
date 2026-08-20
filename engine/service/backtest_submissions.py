"""Engine-owned one-shot authorities for prepared Backtest submissions."""

from __future__ import annotations

import copy
import hashlib
import secrets
import threading
import time
from collections import OrderedDict

from engine.contracts import backtest as backtest_contracts
from engine.contracts import strict_json
from engine.contracts import digest as digest_contracts
from engine.contracts.digest import canonical_json_digest


_PREPARED_SUBMISSION_TOKEN = object()
_PREPARED_SUBMISSION_ISSUER = object()


class _PreparedBacktestSubmission:
    """Unforgeable proof retained only by the Engine service process."""

    __slots__ = (
        "_expires_at",
        "_frozen_request_digest",
        "_frozen_request_json",
        "_request_digest",
        "_session_identity",
        "_token_digest",
    )

    def __init__(
        self,
        *,
        expires_at,
        frozen_request_digest,
        frozen_request_json,
        request_digest,
        session_identity,
        token_digest,
        _token,
    ):
        if _token is not _PREPARED_SUBMISSION_TOKEN:
            raise TypeError("Prepared Backtest submissions are Engine-owned.")
        self._expires_at = expires_at
        self._frozen_request_digest = frozen_request_digest
        self._frozen_request_json = frozen_request_json
        self._request_digest = request_digest
        self._session_identity = session_identity
        self._token_digest = token_digest


class _CachedBacktestBuild:
    """Bounded reusable freeze result; never an execution authority."""

    __slots__ = (
        "expires_at",
        "frozen_request_digest",
        "frozen_request_json",
        "request_digest",
        "session_identity",
    )

    def __init__(
        self,
        *,
        expires_at,
        frozen_request_digest,
        frozen_request_json,
        request_digest,
        session_identity,
    ):
        self.expires_at = expires_at
        self.frozen_request_digest = frozen_request_digest
        self.frozen_request_json = frozen_request_json
        self.request_digest = request_digest
        self.session_identity = session_identity


def _request_digest(request):
    normalized = backtest_contracts.normalize_backtest_request(request)
    return "sha256:" + canonical_json_digest(normalized)


def _token_digest(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _session_identity(value):
    if not isinstance(value, str) or not value:
        raise ValueError(
            "Prepared Backtest submission session identity is required."
        )
    return value


class PreparedBacktestSubmissionStore:
    """Own exact one-shot proofs plus bounded reusable Build snapshots.

    The opaque client token is only an index.  The frozen request never leaves
    this service-owned store and a process restart deliberately invalidates
    every outstanding proof and cached Build.  Reusing a Build still mints a
    fresh one-shot execution proof, so the cache cannot replay a Run.
    """

    def __init__(
        self,
        *,
        max_entries=64,
        lifetime_seconds=300.0,
        build_cache_lifetime_seconds=1800.0,
        clock=None,
    ):
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries < 1
        ):
            raise ValueError(
                "Prepared Backtest submission capacity must be positive."
            )
        if (
            isinstance(lifetime_seconds, bool)
            or not isinstance(lifetime_seconds, (int, float))
            or lifetime_seconds <= 0
        ):
            raise ValueError(
                "Prepared Backtest submission lifetime must be positive."
            )
        if (
            isinstance(build_cache_lifetime_seconds, bool)
            or not isinstance(build_cache_lifetime_seconds, (int, float))
            or build_cache_lifetime_seconds <= 0
        ):
            raise ValueError(
                "Prepared Backtest build cache lifetime must be positive."
            )
        self._max_entries = max_entries
        self._lifetime_seconds = float(lifetime_seconds)
        self._build_cache_lifetime_seconds = float(
            build_cache_lifetime_seconds
        )
        self._clock = time.monotonic if clock is None else clock
        if not callable(self._clock):
            raise TypeError(
                "Prepared Backtest submission clock must be callable."
            )
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._build_cache = OrderedDict()

    def _purge_expired_locked(self, now):
        expired = [
            token
            for token, authority in self._entries.items()
            if authority._expires_at <= now
        ]
        for token in expired:
            self._entries.pop(token, None)
        expired_builds = [
            key
            for key, build in self._build_cache.items()
            if build.expires_at <= now
        ]
        for key in expired_builds:
            self._build_cache.pop(key, None)

    @staticmethod
    def _build_cache_key(session_identity, request_digest):
        material = f"{session_identity}\0{request_digest}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _get_cached_build(self, request, *, session_identity):
        session_identity = _session_identity(session_identity)
        request_digest = _request_digest(request)
        cache_key = self._build_cache_key(session_identity, request_digest)
        now = self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            build = self._build_cache.get(cache_key)
            if build is None:
                return None
            if not secrets.compare_digest(
                build.session_identity,
                session_identity,
            ) or not secrets.compare_digest(
                build.request_digest,
                request_digest,
            ):
                self._build_cache.pop(cache_key, None)
                return None
            if not secrets.compare_digest(
                build.frozen_request_digest,
                hashlib.sha256(
                    build.frozen_request_json.encode("utf-8")
                ).hexdigest(),
            ):
                self._build_cache.pop(cache_key, None)
                return None
            self._build_cache.move_to_end(cache_key)
            return (
                strict_json.loads(build.frozen_request_json),
                max(0.0, build.expires_at - now),
            )

    def _cache_build(self, request, frozen_request, *, session_identity):
        session_identity = _session_identity(session_identity)
        request_digest = _request_digest(request)
        frozen_request_json = strict_json.dumps(
            copy.deepcopy(frozen_request),
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = self._build_cache_key(session_identity, request_digest)
        now = self._clock()
        build = _CachedBacktestBuild(
            expires_at=now + self._build_cache_lifetime_seconds,
            frozen_request_digest=hashlib.sha256(
                frozen_request_json.encode("utf-8")
            ).hexdigest(),
            frozen_request_json=frozen_request_json,
            request_digest=request_digest,
            session_identity=session_identity,
        )
        with self._lock:
            self._purge_expired_locked(now)
            self._build_cache[cache_key] = build
            self._build_cache.move_to_end(cache_key)
            while len(self._build_cache) > self._max_entries:
                self._build_cache.popitem(last=False)
        return self._build_cache_lifetime_seconds

    def consume(self, token, request, *, session_identity):
        session_identity = _session_identity(session_identity)
        if not isinstance(token, str) or not token:
            raise ValueError("Prepared Backtest submission token is required.")
        request_digest = _request_digest(request)
        now = self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            authority = self._entries.pop(token, None)
            if authority is None:
                raise ValueError(
                    "Prepared Backtest submission is unknown, expired, or "
                    "already consumed."
                )
            if not secrets.compare_digest(
                authority._token_digest,
                _token_digest(token),
            ):
                raise ValueError("Prepared Backtest submission token is invalid.")
            if not secrets.compare_digest(
                authority._session_identity,
                session_identity,
            ):
                raise PermissionError(
                    "Prepared Backtest submission belongs to another session."
                )
            if not secrets.compare_digest(
                authority._request_digest,
                request_digest,
            ):
                raise ValueError(
                    "Prepared Backtest submission does not match the exact request."
                )
            if not secrets.compare_digest(
                authority._frozen_request_digest,
                hashlib.sha256(
                    authority._frozen_request_json.encode("utf-8")
                ).hexdigest(),
            ):
                raise ValueError(
                    "Prepared Backtest submission material changed."
                )
            frozen_request = strict_json.loads(
                authority._frozen_request_json
            )
        return frozen_request


def _issue_prepared_submission(
    store,
    request,
    frozen_request,
    *,
    session_identity,
    _issuer,
):
    """Single internal mint after the complete freeze boundary."""

    if _issuer is not _PREPARED_SUBMISSION_ISSUER:
        raise TypeError(
            "Prepared Backtest submissions require the authoritative issuer."
        )
    if type(store) is not PreparedBacktestSubmissionStore:
        raise TypeError(
            "Prepared Backtest submission store must be Engine-owned."
        )
    session_identity = _session_identity(session_identity)
    request_digest = _request_digest(request)
    frozen = copy.deepcopy(frozen_request)
    if not isinstance(frozen, dict) or not isinstance(
        frozen.get("executionSnapshot"), dict
    ):
        raise ValueError(
            "Prepared Backtest submission requires a frozen request."
        )
    if not strict_json.exact_equal(
        frozen["executionSnapshot"].get("executionInputs"),
        backtest_contracts.backtest_execution_inputs(request),
    ):
        raise ValueError(
            "Prepared Backtest request does not match its frozen snapshot."
        )
    frozen_request_json = strict_json.dumps(
        frozen,
        sort_keys=True,
        separators=(",", ":"),
    )
    frozen_request_digest = hashlib.sha256(
        frozen_request_json.encode("utf-8")
    ).hexdigest()
    now = store._clock()
    with store._lock:
        store._purge_expired_locked(now)
        session_tokens = [
            token
            for token, authority in store._entries.items()
            if secrets.compare_digest(
                authority._session_identity,
                session_identity,
            )
        ]
        if not session_tokens and len(store._entries) >= store._max_entries:
            raise RuntimeError(
                "Prepared Backtest submission capacity is full."
            )
        token = secrets.token_urlsafe(32)
        while token in store._entries:
            token = secrets.token_urlsafe(32)
        authority = _PreparedBacktestSubmission(
            expires_at=now + store._lifetime_seconds,
            frozen_request_digest=frozen_request_digest,
            frozen_request_json=frozen_request_json,
            request_digest=request_digest,
            session_identity=session_identity,
            token_digest=_token_digest(token),
            _token=_PREPARED_SUBMISSION_TOKEN,
        )
        for previous_token in session_tokens:
            store._entries.pop(previous_token, None)
        store._entries[token] = authority
        store._entries.move_to_end(token)
    return {
        "preparedSubmissionToken": token,
        "requestDigest": request_digest,
        "snapshotHash": frozen["executionSnapshot"].get("snapshotHash"),
        "expiresInSeconds": store._lifetime_seconds,
    }


def prepare_backtest_submission(
    config,
    request,
    store,
    *,
    session_identity,
):
    """Reuse or freeze one exact Build, then issue a fresh Run authority."""

    if type(store) is not PreparedBacktestSubmissionStore:
        raise TypeError(
            "Prepared Backtest submission store must be Engine-owned."
        )
    # Import here to keep the store's lifecycle mechanism independent from the
    # Backtest resolver while mechanically restricting authority issuance to
    # this complete freeze boundary.
    from engine.service import backtests as backtest_service

    cached = store._get_cached_build(
        request,
        session_identity=session_identity,
    )
    if cached is None:
        frozen = backtest_service.freeze_backtest_request(config, request)
        cache_hit = False
        build_cache_expires_in = None
    else:
        frozen, build_cache_expires_in = cached
        cache_hit = True
    snapshot = frozen.get("executionSnapshot") if isinstance(frozen, dict) else None
    if not isinstance(snapshot, dict):
        raise ValueError(
            "Authoritative Backtest freeze returned no execution snapshot."
        )
    snapshot_hash = snapshot.get("snapshotHash")
    unsigned_snapshot = {
        key: copy.deepcopy(value)
        for key, value in snapshot.items()
        if key != "snapshotHash"
    }
    if (
        not digest_contracts.is_sha256_digest(snapshot_hash)
        or snapshot_hash
        != backtest_contracts.backtest_evidence_digest(unsigned_snapshot)
    ):
        raise ValueError(
            "Authoritative Backtest freeze returned an invalid snapshot hash."
        )
    prepared = _issue_prepared_submission(
        store,
        request,
        frozen,
        session_identity=session_identity,
        _issuer=_PREPARED_SUBMISSION_ISSUER,
    )
    if not cache_hit:
        build_cache_expires_in = store._cache_build(
            request,
            frozen,
            session_identity=session_identity,
        )
    artifact = frozen["executionSnapshot"]["compositionArtifact"]
    return {
        "valid": True,
        "cacheHit": cache_hit,
        "buildCacheExpiresInSeconds": build_cache_expires_in,
        "pipelineTopology": artifact["pipelinePlan"]["topology"],
        "environmentTopology": artifact["environmentPlan"]["topology"],
        "analysisTopology": artifact["analysisPlan"]["topology"],
        "resultContracts": artifact["resultContracts"],
        **prepared,
    }


__all__ = (
    "PreparedBacktestSubmissionStore",
    "prepare_backtest_submission",
)
