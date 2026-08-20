#!/usr/bin/env python3
"""Own one ProcessRunner tree until its primary process and descendants exit."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.runtime.process_supervision import (  # noqa: E402
    SUPERVISOR_FORCED_DESCENDANT_EXIT_CODE,
    discover_process_tree,
    process_identity,
    terminate_descendants,
    terminate_process_tree,
)


_PR_SET_CHILD_SUBREAPER = 36


def _become_child_subreaper():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _reap_adopted_children():
    while True:
        try:
            process_id, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if process_id == 0:
            return


def _run(command):
    _become_child_subreaper()
    primary = subprocess.Popen(command, close_fds=True)
    primary_identity = process_identity(primary.pid)
    if primary_identity is None:
        primary.kill()
        primary.wait()
        raise RuntimeError("ProcessRunner primary process identity is unavailable.")

    requested_signal = None

    def request_shutdown(signal_number, _frame):
        nonlocal requested_signal
        requested_signal = requested_signal or signal_number

    for signal_number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signal_number, request_shutdown)

    while primary.poll() is None:
        if requested_signal is not None:
            terminate_process_tree(
                primary,
                {primary.pid: primary_identity},
                terminate_grace=0.1,
                kill_grace=0.2,
            )
            break
        time.sleep(0.01)

    try:
        return_code = primary.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        terminate_process_tree(
            primary,
            {primary.pid: primary_identity},
            terminate_grace=0.1,
            kill_grace=0.2,
        )
        return_code = primary.wait()

    # As a subreaper, every orphaned daemon/double-fork/setsid descendant is
    # reparented here instead of escaping to the host's init process.
    descendants = discover_process_tree(os.getpid())
    descendants.pop(os.getpid(), None)
    forced_descendant_cleanup = bool(descendants)
    terminate_descendants(
        os.getpid(),
        descendants,
        terminate_grace=0.1,
        kill_grace=0.2,
    )
    _reap_adopted_children()
    if requested_signal is not None:
        return 128 + requested_signal
    if forced_descendant_cleanup and return_code == 0:
        return SUPERVISOR_FORCED_DESCENDANT_EXIT_CODE
    return return_code


def main(argv=None):
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        raise ValueError("ProcessRunner supervisor requires a command.")
    return _run(command)


if __name__ == "__main__":
    raise SystemExit(main())
