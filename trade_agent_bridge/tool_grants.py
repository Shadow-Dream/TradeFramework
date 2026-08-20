"""Short-lived capability grants for the TradeEngine Agent MCP surface.

Only SHA-256 token hashes are retained.  A grant is bound to one owner, chat,
turn and exact Context digest; it authorizes a fixed subset of the V1
tools and never carries browser credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from copy import deepcopy


TOOL_SCOPES = frozenset(
    {
        "trade_context_get",
        "trade_catalog_find",
        "trade_dataset_inspect",
        "trade_validate",
        "trade_backtest_get",
        "trade_result_query",
        "trade_proposal_create",
        "trade_ui_state_get",
        "trade_ui_document_get",
        "trade_ui_document_patch",
    }
)


class ToolGrantError(PermissionError):
    """Stable authorization failure for the Agent tool boundary."""

    code = "tool_grant_invalid"


class ToolGrantStore:
    def __init__(self, *, ttl_seconds=21600, clock=time.time):
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 30 <= ttl_seconds <= 21600:
            raise ValueError("Agent tool grant TTL must be between 30 and 21600 seconds")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._grants = {}

    @staticmethod
    def _hash(token):
        if not isinstance(token, str) or len(token) != 43:
            raise ToolGrantError("Agent tool grant is invalid or expired")
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def _prune_locked(self, now):
        for digest in tuple(self._grants):
            if self._grants[digest]["expiresAt"] <= now:
                del self._grants[digest]

    def create(self, *, owner_id, chat_id, turn_id, context_digest, context, scopes):
        for label, value in (
            ("ownerId", owner_id),
            ("chatId", chat_id),
            ("turnId", turn_id),
            ("contextDigest", context_digest),
        ):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"Agent tool grant {label} is invalid")
        if not isinstance(scopes, list) or not scopes or len(scopes) != len(set(scopes)):
            raise ValueError("Agent tool grant scopes must be a unique non-empty array")
        if any(scope not in TOOL_SCOPES for scope in scopes):
            raise ValueError("Agent tool grant contains an unsupported scope")
        now = self._clock()
        token = secrets.token_urlsafe(32)
        grant = {
            "ownerId": owner_id,
            "chatId": chat_id,
            "turnId": turn_id,
            "contextDigest": context_digest,
            "context": deepcopy(context),
            "scopes": frozenset(scopes),
            "createdAt": now,
            "expiresAt": now + self._ttl_seconds,
        }
        with self._lock:
            self._prune_locked(now)
            self._grants[self._hash(token)] = grant
        return {"grant": token, "expiresAt": grant["expiresAt"]}

    def authorize(self, token, tool_name):
        if tool_name not in TOOL_SCOPES:
            raise ToolGrantError("Agent tool is not allowlisted")
        digest = self._hash(token)
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            grant = self._grants.get(digest)
            if grant is None:
                raise ToolGrantError("Agent tool grant is invalid or expired")
            if tool_name not in grant["scopes"]:
                raise ToolGrantError("Agent tool grant does not allow this tool")
            return deepcopy(grant)

    def revoke_turn(self, turn_id):
        revoked = 0
        with self._lock:
            for digest in tuple(self._grants):
                if hmac.compare_digest(self._grants[digest]["turnId"], str(turn_id)):
                    del self._grants[digest]
                    revoked += 1
        return revoked
