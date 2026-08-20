"""Exclusive ownership of one TradeEngine control store.

The web service is the only online execution/control backend. Offline tools may
open a control store only while no service process owns it.
"""

from __future__ import annotations

import fcntl
import os
import secrets
import threading
from pathlib import Path

from engine.contracts import strict_json
from engine.core.build_identity import ENGINE_BUILD_ID
OWNER_FILE = ".engine-owner.json"
LOCK_FILE = ".engine-owner.lock"
DELEGATED_TOKEN_ENV = "TRADE_ENGINE_DELEGATED_OWNER_TOKEN"
DELEGATED_PID_ENV = "TRADE_ENGINE_DELEGATED_OWNER_PID"
_ACTIVE_OWNER_TOKENS = {}
_ACTIVE_OWNER_TOKENS_LOCK = threading.Lock()


def _root_path(config) -> Path:
    return Path(config["controlRoot"]).expanduser().resolve()


def _root(config) -> Path:
    root = _root_path(config)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_owner(root: Path) -> dict:
    path = root / OWNER_FILE
    if not path.is_file():
        return {}
    try:
        value = strict_json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"TradeEngine owner record is unreadable: {path}") from exc
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "pid", "buildId", "token"}:
        raise RuntimeError(f"TradeEngine owner record has an invalid schema: {path}")
    if value.get("schemaVersion") != 1:
        raise RuntimeError(f"TradeEngine owner record schemaVersion 1 is required: {path}")
    if not isinstance(value.get("pid"), int) or value["pid"] <= 0:
        raise RuntimeError(f"TradeEngine owner record has an invalid pid: {path}")
    if not isinstance(value.get("buildId"), str) or not value["buildId"]:
        raise RuntimeError(f"TradeEngine owner record has an invalid buildId: {path}")
    if not isinstance(value.get("token"), str) or not value["token"]:
        raise RuntimeError(f"TradeEngine owner record has an invalid token: {path}")
    return value


def _process_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _control_lock_held(root: Path) -> bool:
    """Probe the lock without ever unlocking another inherited lease copy."""

    lock_path = root / LOCK_FILE
    if not lock_path.is_file():
        return False
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        # Closing releases only this probe's open-file-description.  As with a
        # lease, do not explicitly unlock because another process may hold inherited
        # references to its own description.
        handle.close()


def _active_owner_token(root: Path, token: str) -> bool:
    with _ACTIVE_OWNER_TOKENS_LOCK:
        active = _ACTIVE_OWNER_TOKENS.get(root)
    return bool(active and token and secrets.compare_digest(active, token))


def assert_control_access(config) -> None:
    """Reject direct access while another process owns this control store."""
    # Access checks are semantic gates, not store-creation operations.  The
    # caller creates the root only after every other boundary has accepted its
    # input.
    root = _root_path(config)
    owner = _read_owner(root)
    owner_pid = owner.get("pid")
    if (
        owner_pid
        and int(owner_pid) == os.getpid()
        and _active_owner_token(root, owner.get("token", ""))
    ):
        return
    delegated_token = os.environ.get(DELEGATED_TOKEN_ENV, "")
    delegated_pid = os.environ.get(DELEGATED_PID_ENV, "")
    if (
        delegated_pid == str(owner_pid)
        and os.getppid() == owner_pid
        and isinstance(owner.get("token"), str)
        and delegated_token
        and secrets.compare_digest(delegated_token, owner["token"])
    ):
        return
    if not _control_lock_held(root):
        return
    raise RuntimeError(
        "TradeEngine control state is owned by the running Engine service "
        f"(pid={owner_pid}, build={owner.get('buildId') or 'unknown'}). "
        "Use the authenticated Engine API, or stop the service before running an offline tool."
    )


def delegated_control_child_environment(config) -> dict[str, str]:
    """Authorize one direct disposable child of the current Engine owner."""

    environment = dict(os.environ)
    environment.pop(DELEGATED_TOKEN_ENV, None)
    environment.pop(DELEGATED_PID_ENV, None)
    root = _root(config)
    owner = _read_owner(root)
    if not owner:
        return environment
    if (
        owner["pid"] != os.getpid()
        or not _process_alive(owner["pid"])
        or not _active_owner_token(root, owner["token"])
    ):
        raise RuntimeError("Only the active TradeEngine owner can delegate control access.")
    environment[DELEGATED_TOKEN_ENV] = owner["token"]
    environment[DELEGATED_PID_ENV] = str(owner["pid"])
    return environment


class ControlOwnerLease:
    def __init__(self, root: Path, handle, token: str):
        self.root = root
        self.handle = handle
        self.token = token
        self.closed = False

    def child_pass_fds(self) -> tuple[int, ...]:
        """Return the exact lease descriptor allowed in outer supervisors.

        The descriptor is deliberately not duplicated here.  ``Popen`` creates
        one inherited reference to the same open-file-description, so closing
        the Engine's reference cannot release the ownership lock while an
        outer supervisor is still proving writer quiescence.
        """

        if self.closed or self.handle.closed:
            raise RuntimeError("A closed TradeEngine owner lease cannot be delegated.")
        descriptor = self.handle.fileno()
        os.fstat(descriptor)
        return (descriptor,)

    def close(self) -> None:
        if self.closed:
            return
        owner_path = self.root / OWNER_FILE
        first_error = None
        first_traceback = None
        try:
            owner = _read_owner(self.root)
            if (
                owner.get("token") == self.token
                and int(owner.get("pid") or 0) == os.getpid()
            ):
                owner_path.unlink(missing_ok=True)
        except BaseException as exc:
            first_error = exc
            first_traceback = exc.__traceback__
        close_error = None
        try:
            # Never issue an explicit flock unlock here.  An outer supervisor may hold an
            # inherited reference to this same open-file-description; an
            # explicit unlock through either reference would release the lock
            # before the writer tree has proved quiescence.
            self.handle.close()
        except BaseException as exc:
            close_error = exc
        self.closed = self.handle.closed
        if self.closed:
            with _ACTIVE_OWNER_TOKENS_LOCK:
                if _ACTIVE_OWNER_TOKENS.get(self.root) == self.token:
                    _ACTIVE_OWNER_TOKENS.pop(self.root, None)
        if first_error is not None:
            if close_error is not None:
                raise first_error.with_traceback(first_traceback) from close_error
            raise first_error.with_traceback(first_traceback)
        if close_error is not None:
            raise close_error


def claim_control_owner(config) -> ControlOwnerLease:
    """Claim exclusive online ownership until the returned lease is closed."""
    root = _root(config)
    handle = (root / LOCK_FILE).open("a+", encoding="utf-8")
    if handle.fileno() <= 2:
        duplicated = fcntl.fcntl(handle.fileno(), fcntl.F_DUPFD_CLOEXEC, 3)
        replacement = os.fdopen(duplicated, "a+", encoding="utf-8")
        handle.close()
        handle = replacement
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        owner = _read_owner(root)
        raise RuntimeError(
            "Another TradeEngine process already owns this control store "
            f"(pid={owner.get('pid') or 'unknown'}, build={owner.get('buildId') or 'unknown'})."
        ) from exc
    token = secrets.token_hex(16)
    payload = {
        "schemaVersion": 1,
        "pid": os.getpid(),
        "buildId": ENGINE_BUILD_ID,
        "token": token,
    }
    temporary = root / f".{OWNER_FILE}.{token}.tmp"
    try:
        temporary.write_text(
            strict_json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, root / OWNER_FILE)
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        cleanup_error = None
        try:
            temporary.unlink(missing_ok=True)
        except BaseException as exc:
            cleanup_error = exc
        try:
            # Closing is sufficient and preserves the close-only invariant.
            handle.close()
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise primary_error.with_traceback(primary_traceback) from cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    with _ACTIVE_OWNER_TOKENS_LOCK:
        _ACTIVE_OWNER_TOKENS[root] = token
    return ControlOwnerLease(root, handle, token)
