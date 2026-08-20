import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from engine.runtime import process_supervision


class ProcessSupervisionIdentityTests(unittest.TestCase):
    def test_bounded_tail_revokes_persistence_even_when_close_is_interrupted(self):
        class DeferredStream:
            def __init__(self):
                self.reading = threading.Event()
                self.release = threading.Event()
                self.finished = threading.Event()
                self.read_count = 0

            def read(self, _size):
                if self.read_count == 0:
                    self.read_count += 1
                    self.reading.set()
                    self.release.wait(5)
                    return b"late-write"
                self.finished.set()
                return b""

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tail.log"
            stream = DeferredStream()
            tail = process_supervision.BoundedStreamTail(
                stream,
                max_bytes=1024,
                persist_path=path,
                persist_interval=0,
            )
            self.assertTrue(stream.reading.wait(2))
            join = tail.thread.join
            with (
                mock.patch.object(
                    tail.thread,
                    "join",
                    side_effect=[KeyboardInterrupt("join"), None],
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "join"),
            ):
                tail.close()
            self.assertTrue(tail._closed)
            closed_evidence = (path.stat().st_ino, path.stat().st_mtime_ns)
            stream.release.set()
            join(timeout=2)
            self.assertTrue(stream.finished.wait(2))
            self.assertEqual(
                (path.stat().st_ino, path.stat().st_mtime_ns),
                closed_evidence,
            )
            self.assertEqual(path.read_bytes(), b"")

    def test_refresh_never_reassigns_a_reused_pid(self):
        old_identity = (123, "old-start")
        new_identity = (123, "new-start")
        known = {123: old_identity}
        with (
            mock.patch.object(
                process_supervision,
                "identity_alive",
                return_value=True,
            ),
            mock.patch.object(
                process_supervision,
                "discover_process_tree",
                return_value={123: new_identity},
            ),
        ):
            process_supervision.refresh_process_tree(known, 123)
        self.assertEqual(known, {123: old_identity})

    def test_contained_refresh_can_defer_the_host_session_scan(self):
        root = (123, "root-start")
        child = (124, "child-start")
        known = {123: root}
        with (
            mock.patch.object(
                process_supervision,
                "identity_alive",
                return_value=True,
            ),
            mock.patch.object(
                process_supervision,
                "discover_process_tree",
                return_value={123: root, 124: child},
            ),
            mock.patch.object(
                process_supervision,
                "discover_process_session",
            ) as discover_session,
        ):
            process_supervision.refresh_process_tree(
                known,
                123,
                root_identity=root,
                session_id=123,
                discover_session_members=False,
            )
        self.assertEqual(known, {123: root, 124: child})
        discover_session.assert_not_called()

    def test_termination_never_signals_a_group_after_root_identity_changes(self):
        old_identity = (123, "old-start")
        process = mock.Mock()
        process.pid = 123
        process.poll.return_value = 0
        with (
            mock.patch.object(
                process_supervision,
                "identity_alive",
                return_value=False,
            ),
            mock.patch.object(
                process_supervision.os,
                "killpg",
            ) as kill_group,
        ):
            process_supervision.terminate_process_tree(
                process,
                {123: old_identity},
                owns_process_group=True,
                terminate_grace=0,
                kill_grace=0,
            )
        kill_group.assert_not_called()

    def test_session_refresh_rejects_a_reused_session_leader(self):
        old_root = (123, "old-start")
        reused_root = (123, "new-start")
        unrelated_child = (124, "child-start")
        known = {123: old_root}
        with (
            mock.patch.object(
                process_supervision,
                "identity_alive",
                return_value=False,
            ),
            mock.patch.object(
                process_supervision,
                "discover_process_session",
                return_value={123: reused_root, 124: unrelated_child},
            ),
        ):
            process_supervision.refresh_process_tree(
                known,
                123,
                root_identity=old_root,
                session_id=123,
                process_group_id=123,
            )
        self.assertEqual(known, {123: (123, "")})

        with mock.patch.object(
            process_supervision,
            "discover_process_session",
            return_value={124: unrelated_child},
        ) as discover_again:
            process_supervision.refresh_process_tree(
                known,
                123,
                root_identity=old_root,
                session_id=123,
                process_group_id=123,
            )
        discover_again.assert_not_called()
        self.assertNotIn(124, known)

    def test_session_refresh_includes_members_in_a_different_process_group(self):
        root = (123, "root-start")
        child = (124, "child-start")
        known = {123: root}
        with (
            mock.patch.object(
                process_supervision,
                "identity_alive",
                return_value=True,
            ),
            mock.patch.object(
                process_supervision,
                "discover_process_tree",
                return_value={123: root},
            ),
            mock.patch.object(
                process_supervision,
                "discover_process_session",
                return_value={123: root, 124: child},
            ) as discover_session,
        ):
            process_supervision.refresh_process_tree(
                known,
                123,
                root_identity=root,
                session_id=123,
                process_group_id=999,
            )
        discover_session.assert_called_once_with(123)
        self.assertEqual(known, {123: root, 124: child})

    def test_pidfd_signal_rechecks_identity_after_open(self):
        identity = (123, "old-start")
        with (
            mock.patch.object(
                process_supervision,
                "identity_can_run",
                return_value=True,
            ),
            mock.patch.object(
                process_supervision.os,
                "pidfd_open",
                return_value=9,
            ),
            mock.patch.object(
                process_supervision,
                "process_identity",
                return_value=(123, "reused-start"),
            ),
            mock.patch.object(
                process_supervision.signal,
                "pidfd_send_signal",
            ) as send_signal,
            mock.patch.object(process_supervision.os, "close") as close,
        ):
            process_supervision._signal_identity(identity, signal.SIGKILL)
        send_signal.assert_not_called()
        close.assert_called_once_with(9)

    def test_hard_sweep_recomputes_survivors_after_final_sigkill(self):
        root = (123, "root-start")
        running = {root: True}
        kill_calls = 0

        def can_run(identity):
            return running.get(identity, False)

        def signal_identities(_identities, signal_number, _root_pid):
            nonlocal kill_calls
            if signal_number == signal.SIGKILL:
                kill_calls += 1
                if kill_calls >= 2:
                    running[root] = False
            return None

        process = mock.Mock(pid=123)
        process.poll.return_value = 0
        process.wait.return_value = 0
        with (
            mock.patch.object(
                process_supervision,
                "refresh_process_tree",
            ),
            mock.patch.object(
                process_supervision,
                "identity_can_run",
                side_effect=can_run,
            ),
            mock.patch.object(
                process_supervision,
                "_signal_identities",
                side_effect=signal_identities,
            ),
        ):
            process_supervision.terminate_process_tree(
                process,
                {123: root},
                terminate_grace=0,
                kill_grace=0,
                session_id=123,
                process_group_id=123,
            )
        self.assertEqual(kill_calls, 2)

    def test_descendant_cleanup_rejects_failed_signals_with_a_live_survivor(self):
        child = (124, "child-start")
        signal_error = RuntimeError("pidfd signal failed")
        with (
            mock.patch.object(
                process_supervision,
                "process_identity",
                return_value=(123, "root-start"),
            ),
            mock.patch.object(process_supervision, "refresh_process_tree"),
            mock.patch.object(
                process_supervision,
                "identity_can_run",
                return_value=True,
            ),
            mock.patch.object(
                process_supervision,
                "_signal_identities",
                return_value=signal_error,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "descendant process tree did not terminate",
            ) as raised:
                process_supervision.terminate_descendants(
                    123,
                    {124: child},
                    terminate_grace=0,
                    kill_grace=0,
                )
        self.assertIs(raised.exception.__cause__, signal_error)

    def test_session_teardown_finds_child_after_its_root_has_exited(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child.pid"
            source = r'''
import os
from pathlib import Path
import sys
import time

child = os.fork()
if child == 0:
    Path(sys.argv[1]).write_text(str(os.getpid()), encoding="ascii")
    time.sleep(30)
    os._exit(0)
os._exit(0)
'''
            process = subprocess.Popen(
                [sys.executable, "-c", source, str(marker)],
                start_new_session=True,
            )
            root_identity = process_supervision.process_identity(process.pid)
            session_id = os.getsid(process.pid)
            process_group_id = os.getpgid(process.pid)
            known = {process.pid: root_identity}
            child_pid = None
            try:
                process.wait(timeout=5)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not marker.exists():
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                child_pid = int(marker.read_text(encoding="ascii"))
                self.assertIsNotNone(
                    process_supervision.process_identity(child_pid)
                )

                process_supervision.terminate_process_tree(
                    process,
                    known,
                    owns_process_group=True,
                    session_id=session_id,
                    process_group_id=process_group_id,
                    terminate_grace=0.2,
                    kill_grace=0.5,
                )

                deadline = time.monotonic() + 2
                while (
                    time.monotonic() < deadline
                    and process_supervision.process_identity(child_pid) is not None
                ):
                    time.sleep(0.01)
                record = Path(f"/proc/{child_pid}/stat")
                if record.exists():
                    state = record.read_text(encoding="ascii").split(")", 1)[1].split()[0]
                    self.assertEqual(state, "Z")
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
