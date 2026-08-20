#!/usr/bin/env python3
"""Shared, PID-safe ownership for Engine child process sessions.

The Engine process never owns an application process directly.  It owns a
small subreaper supervisor which remains alive until its primary child and all
adopted descendants have exited.  That extra, persistent parent is what makes
late forks which create a new session observable and therefore prevents a
Dataset build from being published while a writer is still alive.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
from pathlib import Path
import queue
import resource
import signal
import stat
import subprocess
import sys
import threading
import time


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.runtime import process_supervision  # noqa: E402


_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
_SUPERVISOR_ARGUMENT = "--engine-process-supervisor"
_LIMIT_ARGUMENT = "--engine-process-limits"
_SEPARATOR = "--"
_LAUNCHER_LOCK = threading.Lock()
_LAUNCHER_QUEUE = queue.Queue()
_launcher_thread = None


def _launcher_main():
    """Create children from one Engine-lifetime thread.

    Linux parent-death signals are attached to the thread which called clone.
    Request/HTTP threads are short lived, so every outer supervisor must be
    forked by this persistent shared owner instead.
    """
    while True:
        request = _LAUNCHER_QUEUE.get()
        try:
            request["session"]._open_process(
                request["command"], request["options"]
            )
        except BaseException as exc:
            request["error"] = exc
        finally:
            request["ready"].set()


def _ensure_launcher_thread():
    global _launcher_thread
    with _LAUNCHER_LOCK:
        if _launcher_thread is None or not _launcher_thread.is_alive():
            launcher = threading.Thread(
                target=_launcher_main,
                name="engine-process-session-launcher",
                daemon=True,
            )
            launcher.start()
            _launcher_thread = launcher


def minimal_host_environment(*, home=None, extra=None):
    """Return a deterministic host environment with no Engine credentials.

    Callers may add only an explicit small projection.  Dynamic-loader knobs
    and conventional credential names are rejected rather than silently
    inherited into the host-side bubblewrap/supervisor process.
    """
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    if home is not None:
        environment["HOME"] = str(home)
    for raw_name, raw_value in dict(extra or {}).items():
        name = str(raw_name)
        upper = name.upper()
        if (
            upper.startswith(("LD_", "DYLD_"))
            or upper.endswith(("_TOKEN", "_SECRET", "_PASSWORD", "_API_KEY"))
        ):
            raise ValueError(f"Unsafe child host environment field: {name}")
        environment[name] = str(raw_value)
    return environment


def _supervisor_command(command, limits):
    target = [str(item) for item in command]
    if not target:
        raise ValueError("A supervised process requires a command.")
    if limits:
        target = [
            sys.executable,
            str(Path(__file__).resolve()),
            _LIMIT_ARGUMENT,
            str(int(limits.get("cpu_seconds", 0))),
            str(int(limits.get("file_bytes", 0))),
            str(int(limits.get("open_files", 0))),
            _SEPARATOR,
            *target,
        ]
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        _SUPERVISOR_ARGUMENT,
        str(os.getpid()),
        _SEPARATOR,
        *target,
    ]


def _safe_getsid(process_id):
    try:
        return os.getsid(process_id)
    except (ProcessLookupError, PermissionError):
        return None


def _safe_getpgid(process_id):
    try:
        return os.getpgid(process_id)
    except (ProcessLookupError, PermissionError):
        return None


class ProcessSession:
    """One exact supervisor incarnation and its continuously drained output."""

    def __init__(self, key, metadata=None):
        self.key = key
        self.process = None
        self.root_identity = None
        self.session_id = None
        self.process_group_id = None
        self.stdout_tail = None
        self.stderr_tail = None
        self.metadata = dict(metadata or {})
        self._termination_lock = threading.Lock()
        self._closed = False

    def initialize(
        self,
        command,
        *,
        cwd,
        env,
        max_output_bytes,
        stderr_output_bytes=None,
        stdout_path=None,
        stderr_path=None,
        merge_stderr=False,
        limits=None,
        supervisor_pass_fds=(),
    ):
        _ensure_launcher_thread()
        request = {
            "session": self,
            "command": _supervisor_command(command, limits),
            "options": {
                "cwd": str(cwd),
                "env": dict(env),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                "close_fds": True,
                "pass_fds": tuple(supervisor_pass_fds),
                "start_new_session": True,
            },
            "ready": threading.Event(),
        }
        _LAUNCHER_QUEUE.put(request)
        wait_error = None
        wait_traceback = None
        while not request["ready"].is_set():
            try:
                request["ready"].wait()
            except BaseException as exc:
                # Do not abandon a queued Popen after an interrupted wait.  Its
                # placeholder is registered; wait until the launcher returns
                # the exact handle, then let Registry.start clean it up.
                wait_error = wait_error or exc
                wait_traceback = wait_traceback or exc.__traceback__
        launch_error = request.get("error")
        if wait_error is not None:
            if launch_error is not None:
                raise wait_error.with_traceback(wait_traceback) from launch_error
            raise wait_error.with_traceback(wait_traceback)
        if launch_error is not None:
            raise launch_error
        self.root_identity = process_supervision.process_identity(self.process.pid)
        self.session_id = _safe_getsid(self.process.pid)
        self.process_group_id = _safe_getpgid(self.process.pid)
        if (
            self.root_identity is None
            or self.session_id != self.process.pid
            or self.process_group_id != self.process.pid
        ):
            raise RuntimeError("Supervised process identity is unavailable.")
        self.stdout_tail = process_supervision.BoundedStreamTail(
            self.process.stdout,
            max_bytes=max_output_bytes,
            persist_path=stdout_path,
            thread_name=f"engine-session-{self.key}-stdout",
        )
        if not merge_stderr:
            self.stderr_tail = process_supervision.BoundedStreamTail(
                self.process.stderr,
                max_bytes=stderr_output_bytes or max_output_bytes,
                persist_path=stderr_path,
                thread_name=f"engine-session-{self.key}-stderr",
            )
        return self

    def _open_process(self, command, options):
        process_type = subprocess.Popen
        # The placeholder is already registered before this allocation.  Keep
        # the partially initialized object reachable even if __init__ raises
        # after fork, so shutdown retains a retryable authority.
        self.process = process_type.__new__(process_type)
        process_type.__init__(self.process, command, **options)

    def poll(self):
        return None if self.process is None else self.process.poll()

    def wait(self, timeout=None):
        return self.process.wait(timeout=timeout)

    def stdout_text(self):
        return "" if self.stdout_tail is None else self.stdout_tail.text()

    def stderr_text(self):
        return "" if self.stderr_tail is None else self.stderr_tail.text()

    def terminate(self, *, terminate_grace=1.0, kill_grace=1.0):
        with self._termination_lock:
            if self.process is None or getattr(self.process, "pid", None) is None:
                return
            root_identity = self.root_identity or process_supervision.process_identity(
                self.process.pid
            )
            if root_identity is None:
                if self.process.poll() is None:
                    raise RuntimeError("Supervised process identity is unavailable.")
                self.process.wait()
                return
            if process_supervision.identity_can_run(root_identity):
                process_supervision.signal_process_identity(
                    root_identity,
                    signal.SIGTERM,
                )
            # Never SIGKILL the subreaper.  It is the only non-escapable owner
            # of descendants which called setsid.  A timeout is unproven and
            # deliberately leaves this exact authority registered for retry.
            deadline = time.monotonic() + terminate_grace + kill_grace
            while process_supervision.identity_can_run(root_identity):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Supervised process has not proved descendant quiescence."
                    )
                time.sleep(min(0.01, remaining))
            self.process.wait(timeout=max(0.1, kill_grace))

    def close(self):
        if self._closed:
            return
        if (
            process_supervision.identity_can_run(self.root_identity)
            or (
                self.root_identity is None
                and self.process is not None
                and getattr(self.process, "pid", None) is not None
                and self.process.poll() is None
            )
        ):
            raise RuntimeError("A live supervised process cannot be released.")
        first_error = None
        for tail in (self.stdout_tail, self.stderr_tail):
            if tail is not None:
                try:
                    tail.close()
                except BaseException as exc:
                    first_error = first_error or exc
        for tail, stream_name in (
            (self.stdout_tail, "stdout"),
            (self.stderr_tail, "stderr"),
        ):
            stream = None if self.process is None else getattr(
                self.process, stream_name, None
            )
            if tail is None and stream is not None:
                try:
                    stream.close()
                except BaseException as exc:
                    first_error = first_error or exc
        if first_error is not None:
            raise first_error
        self._closed = True


class ProcessSessionRegistry:
    """Atomic Engine-wide map from logical owner key to process authority."""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions = {}
        self._stopping_prefixes = set()
        self._inherited_supervisor_fds = ()

    @staticmethod
    def _descriptor_identity(descriptor):
        if isinstance(descriptor, bool) or not isinstance(descriptor, int):
            raise TypeError("Supervisor file descriptors must be integers.")
        if descriptor < 0:
            raise ValueError("Supervisor file descriptors must be non-negative.")
        descriptor_stat = os.fstat(descriptor)
        return (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
            stat.S_IFMT(descriptor_stat.st_mode),
        )

    def configure_inherited_supervisor_fds(self, descriptors):
        """Set descriptors inherited only by subsequently created supervisors."""

        requested = tuple(descriptors)
        if len(set(requested)) != len(requested):
            raise ValueError("Supervisor file descriptors must be unique.")
        with self._lock:
            if self._sessions:
                raise RuntimeError(
                    "Supervisor file descriptors cannot change while process sessions are active."
                )
            owned = []
            try:
                for descriptor in requested:
                    self._descriptor_identity(descriptor)
                    duplicate = fcntl.fcntl(
                        descriptor,
                        fcntl.F_DUPFD_CLOEXEC,
                        3,
                    )
                    owned.append(
                        (duplicate, self._descriptor_identity(duplicate))
                    )
            except BaseException as primary_error:
                primary_traceback = primary_error.__traceback__
                cleanup_error = self._close_owned_descriptors(owned)
                if cleanup_error is not None:
                    raise primary_error.with_traceback(primary_traceback) from cleanup_error
                raise primary_error.with_traceback(primary_traceback)
            old = self._inherited_supervisor_fds
            self._inherited_supervisor_fds = tuple(owned)
            close_error = self._close_owned_descriptors(old)
            if close_error is not None:
                raise close_error

    @staticmethod
    def _close_owned_descriptors(entries):
        first_error = None
        for descriptor, _identity in entries:
            try:
                os.close(descriptor)
            except BaseException as exc:
                first_error = first_error or exc
        return first_error

    def clear_inherited_supervisor_fds(self):
        """Stop inheritance after every registered supervisor has been released."""

        with self._lock:
            if self._sessions:
                raise RuntimeError(
                    "Supervisor file descriptors cannot change while process sessions are active."
                )
            owned = self._inherited_supervisor_fds
            self._inherited_supervisor_fds = ()
            close_error = self._close_owned_descriptors(owned)
            if close_error is not None:
                raise close_error

    def inherited_supervisor_fds(self):
        """Return the validated configured descriptor numbers for diagnostics."""

        with self._lock:
            return self._validated_inherited_supervisor_fds()

    def _validated_inherited_supervisor_fds(self):
        descriptors = []
        for descriptor, expected_identity in self._inherited_supervisor_fds:
            if self._descriptor_identity(descriptor) != expected_identity:
                raise RuntimeError(
                    "A configured supervisor file descriptor changed identity."
                )
            descriptors.append(descriptor)
        return tuple(descriptors)

    def start(self, key, command, **options):
        key = str(key)
        with self._lock:
            if any(key.startswith(prefix) for prefix in self._stopping_prefixes):
                raise RuntimeError("Engine is stopping and cannot start this process.")
            if key in self._sessions:
                raise RuntimeError(f"Process session already exists: {key}")
            # Register a starting authority before Popen.  Holding this lock
            # through initialization is atomic with shutdown; retaining the
            # placeholder on failed cleanup keeps a retryable authority.
            session = ProcessSession(key, options.pop("metadata", None))
            self._sessions[key] = session
            try:
                return session.initialize(
                    command,
                    supervisor_pass_fds=self._validated_inherited_supervisor_fds(),
                    **options,
                )
            except BaseException as primary_error:
                primary_traceback = primary_error.__traceback__
                cleanup_error = None
                try:
                    session.terminate(terminate_grace=0.1, kill_grace=1.0)
                    session.close()
                except BaseException as exc:
                    cleanup_error = exc
                if cleanup_error is None:
                    if self._sessions.get(key) is session:
                        self._sessions.pop(key)
                    raise primary_error.with_traceback(primary_traceback)
                raise primary_error.with_traceback(primary_traceback) from cleanup_error

    def get(self, key):
        with self._lock:
            return self._sessions.get(str(key))

    def is_current(self, key, session):
        with self._lock:
            return self._sessions.get(str(key)) is session

    def is_stopping(self, prefix):
        prefix = str(prefix)
        with self._lock:
            return any(prefix.startswith(item) or item.startswith(prefix)
                       for item in self._stopping_prefixes)

    def snapshot(self, prefix=None):
        with self._lock:
            return {
                key: session
                for key, session in self._sessions.items()
                if prefix is None or key.startswith(prefix)
            }

    def finish(self, key, session, *, terminate=False):
        """Prove termination, then release the exact registered authority."""
        key = str(key)
        first_error = self._terminate_with_retry(
            session,
            terminate_grace=0.1 if terminate else 0.0,
            kill_grace=1.0,
        )
        close_error = None
        try:
            session.close()
        except BaseException as exc:
            close_error = exc
            first_error = first_error or exc
        if close_error is None:
            with self._lock:
                if self._sessions.get(key) is session:
                    self._sessions.pop(key)
        if first_error is not None:
            raise first_error

    @staticmethod
    def _terminate_with_retry(session, *, terminate_grace, kill_grace):
        first_error = None
        for _attempt in range(2):
            try:
                session.terminate(
                    terminate_grace=terminate_grace,
                    kill_grace=kill_grace,
                )
                return first_error
            except BaseException as exc:
                first_error = first_error or exc
        raise first_error

    def stop(self, key, *, terminate_grace=1.0, kill_grace=1.0):
        key = str(key)
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            return False
        first_error = self._terminate_with_retry(
            session,
            terminate_grace=terminate_grace,
            kill_grace=kill_grace,
        )
        close_error = None
        try:
            session.close()
        except BaseException as exc:
            close_error = exc
            first_error = first_error or exc
        if close_error is None:
            with self._lock:
                if self._sessions.get(key) is session:
                    self._sessions.pop(key)
        if first_error is not None:
            raise first_error
        return True

    def shutdown(self, prefix):
        prefix = str(prefix)
        with self._lock:
            self._stopping_prefixes.add(prefix)
            sessions = tuple(
                (key, session)
                for key, session in self._sessions.items()
                if key.startswith(prefix)
            )
        first_error = None
        for key, session in sessions:
            try:
                termination_error = self._terminate_with_retry(
                    session,
                    terminate_grace=0.5,
                    kill_grace=1.0,
                )
            except BaseException as exc:
                first_error = first_error or exc
                continue
            close_error = None
            try:
                session.close()
            except BaseException as exc:
                close_error = exc
            session_error = termination_error or close_error
            first_error = first_error or session_error
            if close_error is None:
                with self._lock:
                    if self._sessions.get(key) is session:
                        self._sessions.pop(key)
        if first_error is not None:
            raise first_error


PROCESS_SESSIONS = ProcessSessionRegistry()


def _set_parent_death_signal(signal_number):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_PDEATHSIG, int(signal_number), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _become_child_subreaper():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _reap_children():
    """Reap zombies and return only an explicit kernel no-child proof."""

    while True:
        try:
            process_id, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        except InterruptedError:
            continue
        if process_id == 0:
            # At least one child still exists but is not waitable, so it can
            # still execute code.  A /proc discovery miss must never override
            # this kernel-level evidence.
            return False


def _active_descendants():
    descendants = process_supervision.discover_process_tree(os.getpid())
    descendants.pop(os.getpid(), None)
    return descendants, {
        process_id: identity
        for process_id, identity in descendants.items()
        if process_supervision.identity_can_run(identity)
    }


def _run_supervisor(expected_parent_pid, command):
    requested_signal = None

    def request_shutdown(signal_number, _frame):
        nonlocal requested_signal
        requested_signal = requested_signal or signal_number

    for signal_number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signal_number, request_shutdown)
    _become_child_subreaper()
    _set_parent_death_signal(signal.SIGTERM)
    if os.getppid() != expected_parent_pid:
        requested_signal = signal.SIGTERM

    primary_type = subprocess.Popen
    primary = primary_type.__new__(primary_type)
    primary_identity = None
    forced_cleanup = False
    return_code = None
    primary_error = None
    primary_traceback = None
    try:
        # Retaining the allocated object closes the Popen.__init__ failure
        # window.  Even if no usable handle is established, this process is a
        # subreaper and the finally block sweeps every adopted descendant.
        primary_type.__init__(primary, command, close_fds=True)
        primary_identity = process_supervision.process_identity(primary.pid)
        if primary_identity is None:
            raise RuntimeError("Supervised primary identity is unavailable.")
        while primary.poll() is None and requested_signal is None:
            if os.getppid() != expected_parent_pid:
                requested_signal = signal.SIGTERM
                break
            time.sleep(0.01)
        if requested_signal is not None:
            process_supervision.terminate_process_tree(
                primary,
                {primary.pid: primary_identity},
                terminate_grace=0.25,
                kill_grace=1.0,
            )
        return_code = primary.wait()
        descendants, active = _active_descendants()
        forced_cleanup = any(
            process_supervision.identity_can_run(identity)
            for identity in descendants.values()
        )
        process_supervision.terminate_descendants(
            os.getpid(),
            descendants,
            terminate_grace=0.25,
            kill_grace=1.0,
        )
        _reap_children()
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
    finally:
        # All-attempt cleanup also covers exceptions raised by signalling or
        # waiting.  The supervisor must not abandon adopted children.
        cleanup_error = None
        if getattr(primary, "pid", None) is not None:
            try:
                identity = primary_identity or process_supervision.process_identity(
                    primary.pid
                )
                process_supervision.terminate_process_tree(
                    primary,
                    {} if identity is None else {primary.pid: identity},
                    terminate_grace=0.1,
                    kill_grace=1.0,
                )
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        # A subreaper may never exit while an adopted child is still able to
        # run.  Keep refreshing and retrying forever if a signalling driver is
        # temporarily or permanently unavailable; the outer registry then
        # retains this live authority and can request shutdown again.
        while True:
            descendants, active = _active_descendants()
            if not active:
                no_children = _reap_children()
                if not no_children:
                    forced_cleanup = True
                _remaining, active = _active_descendants()
                if not active and no_children and _reap_children():
                    break
            else:
                forced_cleanup = True
            try:
                process_supervision.terminate_descendants(
                    os.getpid(),
                    descendants,
                    terminate_grace=0.1,
                    kill_grace=1.0,
                )
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            _reap_children()
            time.sleep(0.01)
    if primary_error is not None:
        if cleanup_error is not None:
            raise primary_error.with_traceback(primary_traceback) from cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    if cleanup_error is not None:
        raise cleanup_error
    if requested_signal is not None:
        return 128 + requested_signal
    if forced_cleanup and return_code == 0:
        return process_supervision.SUPERVISOR_FORCED_DESCENDANT_EXIT_CODE
    return return_code


def _apply_limits(cpu_seconds, file_bytes, open_files, command):
    if cpu_seconds > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
    if file_bytes > 0:
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    if open_files > 0:
        resource.setrlimit(resource.RLIMIT_NOFILE, (open_files, open_files))
    os.execvpe(command[0], command, os.environ)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == _SUPERVISOR_ARGUMENT:
        separator = arguments.index(_SEPARATOR)
        return _run_supervisor(int(arguments[1]), arguments[separator + 1 :])
    if arguments and arguments[0] == _LIMIT_ARGUMENT:
        separator = arguments.index(_SEPARATOR)
        _apply_limits(
            int(arguments[1]),
            int(arguments[2]),
            int(arguments[3]),
            arguments[separator + 1 :],
        )
        return 127
    raise ValueError("Unknown Engine process-session invocation.")


if __name__ == "__main__":
    raise SystemExit(main())
