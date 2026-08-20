#!/usr/bin/env python3
"""PID-reuse-safe supervision for Engine-owned child process trees."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path


SUPERVISOR_FORCED_DESCENDANT_EXIT_CODE = 120
_REVOKED_SESSION_STARTTIME = ""


class BoundedStreamTail:
    """Continuously drain a child stream while retaining a fixed byte tail.

    ``persist_path`` is optional diagnostic evidence.  When supplied, the same
    bounded tail is atomically refreshed while the process runs, so neither
    memory nor disk usage can grow with a long-lived or noisy extension.
    """

    def __init__(
        self,
        stream,
        *,
        max_bytes,
        persist_path=None,
        persist_interval=0.25,
        thread_name="engine-child-output",
    ):
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes < 1
        ):
            raise ValueError("Bounded child output size must be a positive integer.")
        self.stream = stream
        self.max_bytes = max_bytes
        self.persist_path = (
            Path(persist_path) if persist_path is not None else None
        )
        self.persist_interval = float(persist_interval)
        self.value = bytearray()
        self.persist_error = None
        self._state_lock = threading.Lock()
        self._closed = False
        if self.persist_path is not None:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist(b"")
        self.thread = threading.Thread(
            target=self._drain,
            name=thread_name,
            daemon=True,
        )
        try:
            self.thread.start()
        except BaseException:
            # start() can be interrupted after the native thread exists.  The
            # constructor cannot return a handle in that case, so revoke disk
            # persistence and close the stream before preserving the error.
            with self._state_lock:
                self._closed = True
            try:
                self.stream.close()
            except BaseException as cleanup_error:
                self.persist_error = self.persist_error or cleanup_error
            raise

    def _persist_locked(self, value):
        if self.persist_path is None:
            return
        temporary = self.persist_path.with_name(self.persist_path.name + ".tmp")
        try:
            temporary.write_bytes(value)
            os.replace(temporary, self.persist_path)
        except OSError as exc:
            self.persist_error = exc
            temporary.unlink(missing_ok=True)

    def _persist(self, value):
        with self._state_lock:
            if self._closed:
                return
            self._persist_locked(value)

    def _drain(self):
        last_persisted = time.monotonic()
        try:
            while chunk := self.stream.read(64 * 1024):
                with self._state_lock:
                    if self._closed:
                        continue
                    self.value.extend(chunk)
                    overflow = len(self.value) - self.max_bytes
                    if overflow > 0:
                        del self.value[:overflow]
                    now = time.monotonic()
                    if (
                        self.persist_path is not None
                        and now - last_persisted >= self.persist_interval
                    ):
                        self._persist_locked(bytes(self.value))
                        last_persisted = now
        except (OSError, ValueError):
            pass
        finally:
            self._persist(bytes(self.value))

    def close(self):
        # Callers terminate/wait for the process tree first, which normally
        # closes the pipe.  The explicit close also handles a leaked inherited
        # descriptor without leaving this diagnostic thread behind.
        first_error = None
        try:
            self.thread.join(timeout=2.0)
        except BaseException as exc:
            first_error = first_error or exc
        try:
            self.stream.close()
        except BaseException as exc:
            first_error = first_error or exc
        try:
            if self.thread.is_alive():
                self.thread.join(timeout=2.0)
        except BaseException as exc:
            first_error = first_error or exc
        try:
            with self._state_lock:
                if not self._closed:
                    try:
                        self._persist_locked(bytes(self.value))
                    finally:
                        self._closed = True
        except BaseException as exc:
            first_error = first_error or exc
        if first_error is not None:
            raise first_error

    def bytes(self):
        with self._state_lock:
            return bytes(self.value)

    def text(self):
        return self.bytes().decode("utf-8", errors="replace").strip()


def process_identity(process_id):
    """Return ``(pid, starttime)`` so a reused PID is never signalled."""
    record = _process_record(process_id)
    return None if record is None else record[0]


def _process_record(process_id):
    """Return identity, process-group ID and session ID from one proc record."""
    try:
        stat = Path(f"/proc/{int(process_id)}/stat").read_text(encoding="ascii")
    except (OSError, ValueError):
        return None
    closing = stat.rfind(")")
    if closing < 0:
        return None
    fields = stat[closing + 2:].split()
    if len(fields) <= 19:
        return None
    try:
        process_id = int(process_id)
        return (
            (process_id, fields[19]),
            int(fields[2]),
            int(fields[3]),
            fields[0],
        )
    except (TypeError, ValueError):
        return None


def identity_alive(identity):
    return identity is not None and process_identity(identity[0]) == identity


def identity_can_run(identity):
    """Return whether the same process incarnation can still execute code."""
    if identity is None:
        return False
    record = _process_record(identity[0])
    return (
        record is not None
        and record[0] == identity
        and record[3] not in {"Z", "X", "x"}
    )


def discover_process_session(session_id, *, process_group_id=None):
    """Discover stable identities still owned by one Linux session.

    A session ID remains allocated while any member exists, even after its
    leader exits.  This lets teardown discover late children without trusting
    a reused root PID.
    """

    session_id = int(session_id)
    group_id = None if process_group_id is None else int(process_group_id)
    identities = {}
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        return identities
    for entry in entries:
        if not entry.name.isdigit():
            continue
        record = _process_record(entry.name)
        if record is None:
            continue
        identity, candidate_group, candidate_session, _state = record
        if candidate_session != session_id:
            continue
        if group_id is not None and candidate_group != group_id:
            continue
        identities[identity[0]] = identity
    return identities


def process_group_alive(
    process_group_id,
    root_identity,
    *,
    session_id=None,
):
    """Return whether the original Engine-owned process group has members."""
    if identity_alive(root_identity):
        try:
            if os.getpgid(int(process_group_id)) != int(process_group_id):
                return False
            if session_id is not None and os.getsid(root_identity[0]) != int(session_id):
                return False
        except (ProcessLookupError, PermissionError):
            return False
        try:
            os.killpg(int(process_group_id), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    if session_id is None:
        return False
    return bool(
        discover_process_session(
            session_id,
            process_group_id=process_group_id,
        )
    )


def discover_process_tree(root_pid):
    """Discover the current Linux descendant tree as stable identities."""
    root_pid = int(root_pid)
    identities = {}
    root_identity = process_identity(root_pid)
    if root_identity is not None:
        identities[root_pid] = root_identity
    seen = {root_pid}
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        try:
            tasks = tuple(Path(f"/proc/{parent}/task").iterdir())
        except OSError:
            continue
        children = set()
        for task in tasks:
            try:
                raw = (task / "children").read_text(encoding="ascii").strip()
            except OSError:
                continue
            children.update(int(item) for item in raw.split() if item.isdigit())
        for child in sorted(children):
            if child in seen:
                continue
            seen.add(child)
            identity = process_identity(child)
            if identity is not None:
                identities[child] = identity
            pending.append(child)
    return identities


def refresh_process_tree(
    known_identities,
    root_pid,
    *,
    root_identity=None,
    session_id=None,
    process_group_id=None,
    discover_session_members=True,
):
    """Add newly observed identities without ever reassigning a reused PID."""
    root_pid = int(root_pid)
    expected_root = root_identity or known_identities.get(root_pid)
    if expected_root is None or identity_alive(expected_root):
        discovered = discover_process_tree(root_pid)
        discovered_root = discovered.get(root_pid)
        if expected_root is None or discovered_root == expected_root:
            for process_id, identity in discovered.items():
                known_identities.setdefault(process_id, identity)
    if session_id is not None and discover_session_members:
        revoked_identity = (int(session_id), _REVOKED_SESSION_STARTTIME)
        if known_identities.get(int(session_id)) == revoked_identity:
            return known_identities
        session_members = discover_process_session(session_id)
        # A live leader with the same numeric SID but a different start time
        # proves that the original session has ended and its PID was reused.
        # Never attach that unrelated session to the old authority.
        session_leader = session_members.get(int(session_id))
        if (
            root_identity is not None
            and session_leader is not None
            and session_leader != root_identity
        ):
            # Revocation is sticky for this authority.  A later scan may see
            # only children of the reused leader; those must never be adopted.
            known_identities[int(session_id)] = revoked_identity
            return known_identities
        for process_id, identity in session_members.items():
            previous = known_identities.get(process_id)
            if previous is None or not identity_alive(previous):
                known_identities[process_id] = identity
    return known_identities


def _signal_identity(identity, signal_number):
    """Signal one exact process incarnation through a pidfd."""
    if not identity_can_run(identity):
        return
    try:
        descriptor = os.pidfd_open(identity[0], 0)
    except ProcessLookupError:
        return
    except (AttributeError, OSError) as exc:
        raise RuntimeError("PID-safe child process signalling is unavailable.") from exc
    try:
        # The numeric PID may have changed incarnation before pidfd_open.  The
        # pidfd remains safe, but only signal it when /proc still proves that it
        # names the authority we intended to terminate.
        if process_identity(identity[0]) != identity:
            return
        signal.pidfd_send_signal(descriptor, signal_number, None, 0)
    except ProcessLookupError:
        return
    finally:
        os.close(descriptor)


def signal_process_identity(identity, signal_number):
    """Signal one exact process incarnation through the shared pidfd gate."""
    _signal_identity(identity, signal_number)


def _signal_identities(identities, signal_number, root_pid):
    # Descendants first: a cooperative supervisor cannot reparent them before
    # they have received the same termination signal.
    process_ids = [pid for pid in reversed(tuple(identities)) if pid != root_pid]
    if root_pid in identities:
        process_ids.append(root_pid)
    first_error = None
    for process_id in process_ids:
        identity = identities[process_id]
        try:
            _signal_identity(identity, signal_number)
        except BaseException as exc:
            first_error = first_error or exc
    return first_error


def terminate_process_tree(
    process,
    known_identities=None,
    *,
    terminate_grace=1.0,
    kill_grace=1.0,
    owns_process_group=False,
    session_id=None,
    process_group_id=None,
):
    """Terminate and prove quiescence of one Engine-owned process session."""
    if process is None:
        return
    root_pid = process.pid
    process_group_id = root_pid if process_group_id is None else int(process_group_id)
    identities = dict(known_identities or {})
    root_identity = identities.get(root_pid)
    if root_identity is None:
        root_identity = process_identity(root_pid)
        if root_identity is not None:
            identities[root_pid] = root_identity
    if root_identity is not None or session_id is not None:
        refresh_process_tree(
            identities,
            root_pid,
            root_identity=root_identity,
            session_id=session_id,
            process_group_id=process_group_id,
        )
        root_identity = root_identity or identities.get(root_pid)
    first_error = None
    active_survivors = {}
    for signal_number, grace in (
        (signal.SIGTERM, terminate_grace),
        (signal.SIGKILL, kill_grace),
    ):
        deadline = time.monotonic() + grace
        while True:
            refresh_process_tree(
                identities,
                root_pid,
                root_identity=root_identity,
                session_id=session_id,
                process_group_id=process_group_id,
            )
            root_identity = root_identity or identities.get(root_pid)
            process.poll()
            active_survivors = {
                pid: identity
                for pid, identity in identities.items()
                if identity_can_run(identity)
            }
            if not active_survivors:
                break
            signal_error = _signal_identities(
                active_survivors,
                signal_number,
                root_pid,
            )
            first_error = first_error or signal_error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.01, remaining))
    # A hard post-wait sweep closes the fork-after-last-snapshot window.  Only
    # exact pidfd authorities are signalled; no raw numeric process-group kill
    # can cross into a reused group.
    hard_deadline = time.monotonic() + kill_grace
    empty_session_scans = 0
    while True:
        refresh_process_tree(
            identities,
            root_pid,
            root_identity=root_identity,
            session_id=session_id,
            process_group_id=process_group_id,
        )
        process.poll()
        active_survivors = {
            pid: identity
            for pid, identity in identities.items()
            if identity_can_run(identity)
        }
        if not active_survivors:
            empty_session_scans += 1
            if empty_session_scans >= 2:
                break
            time.sleep(0)
            continue
        empty_session_scans = 0
        signal_error = _signal_identities(
            active_survivors,
            signal.SIGKILL,
            root_pid,
        )
        first_error = first_error or signal_error
        remaining = hard_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    try:
        process.wait(timeout=kill_grace)
    except (subprocess.TimeoutExpired, ChildProcessError) as exc:
        first_error = first_error or exc
    # Recompute after wait instead of trusting the pre-signal survivor set.
    refresh_process_tree(
        identities,
        root_pid,
        root_identity=root_identity,
        session_id=session_id,
        process_group_id=process_group_id,
    )
    active_survivors = {
        pid: identity
        for pid, identity in identities.items()
        if identity_can_run(identity)
    }
    if active_survivors:
        signal_error = _signal_identities(
            active_survivors,
            signal.SIGKILL,
            root_pid,
        )
        first_error = first_error or signal_error
        time.sleep(0)
        refresh_process_tree(
            identities,
            root_pid,
            root_identity=root_identity,
            session_id=session_id,
            process_group_id=process_group_id,
        )
        active_survivors = {
            pid: identity
            for pid, identity in identities.items()
            if identity_can_run(identity)
        }
    if active_survivors:
        raise RuntimeError(
            "Engine-owned child process session did not terminate."
        ) from first_error
    if first_error is not None:
        raise first_error


def terminate_descendants(
    root_pid,
    known_identities=None,
    *,
    terminate_grace=1.0,
    kill_grace=1.0,
):
    """Terminate and prove every descendant while leaving the supervisor alive."""
    root_pid = int(root_pid)
    identities = dict(known_identities or {})
    root_identity = identities.get(root_pid) or process_identity(root_pid)
    refresh_process_tree(
        identities,
        root_pid,
        root_identity=root_identity,
    )
    identities.pop(root_pid, None)
    first_error = None
    survivors = {}
    for signal_number, grace in (
        (signal.SIGTERM, terminate_grace),
        (signal.SIGKILL, kill_grace),
    ):
        deadline = time.monotonic() + grace
        while True:
            refresh_process_tree(
                identities,
                root_pid,
                root_identity=root_identity,
            )
            identities.pop(root_pid, None)
            survivors = {
                pid: identity
                for pid, identity in identities.items()
                if identity_can_run(identity)
            }
            if not survivors:
                break
            signal_error = _signal_identities(
                survivors,
                signal_number,
                root_pid,
            )
            first_error = first_error or signal_error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.01, remaining))

    # Close the fork-after-last-snapshot window.  Two consecutive empty scans
    # prove quiescence; otherwise continue exact-identity SIGKILL until the
    # hard deadline, then recompute rather than trusting a stale survivor set.
    hard_deadline = time.monotonic() + kill_grace
    empty_scans = 0
    while True:
        refresh_process_tree(
            identities,
            root_pid,
            root_identity=root_identity,
        )
        identities.pop(root_pid, None)
        survivors = {
            pid: identity
            for pid, identity in identities.items()
            if identity_can_run(identity)
        }
        if not survivors:
            empty_scans += 1
            if empty_scans >= 2:
                break
            time.sleep(0)
            continue
        empty_scans = 0
        signal_error = _signal_identities(
            survivors,
            signal.SIGKILL,
            root_pid,
        )
        first_error = first_error or signal_error
        remaining = hard_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))

    refresh_process_tree(
        identities,
        root_pid,
        root_identity=root_identity,
    )
    identities.pop(root_pid, None)
    survivors = {
        pid: identity
        for pid, identity in identities.items()
        if identity_can_run(identity)
    }
    if survivors:
        signal_error = _signal_identities(
            survivors,
            signal.SIGKILL,
            root_pid,
        )
        first_error = first_error or signal_error
        time.sleep(0)
        refresh_process_tree(
            identities,
            root_pid,
            root_identity=root_identity,
        )
        identities.pop(root_pid, None)
        survivors = {
            pid: identity
            for pid, identity in identities.items()
            if identity_can_run(identity)
        }
    if survivors:
        raise RuntimeError(
            "Engine-owned descendant process tree did not terminate."
        ) from first_error
    if first_error is not None:
        raise first_error
