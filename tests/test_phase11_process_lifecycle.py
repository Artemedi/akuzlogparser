from pathlib import Path
import multiprocessing
import os
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_process_fetch import ProcessFetch, ProcessFetchError, ssh_fetch_child
from akuz_fetch import ConnectConfig


class FakeReceiver:
    def __init__(self, message=None, ready=True):
        self.message = message
        self.ready = ready
        self.closed = False

    def poll(self, timeout):
        return self.ready

    def recv(self):
        if isinstance(self.message, BaseException):
            raise self.message
        return self.message

    def close(self):
        self.closed = True


class FakeSender:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeChild:
    def __init__(self, on_start=None, *, exitcode=0,
                 alive_after_start=False, terminate_stops=True):
        self.on_start = on_start
        self.exitcode = exitcode
        self.alive = False
        self.alive_after_start = alive_after_start
        self.terminate_stops = terminate_stops
        self.terminated = False
        self.killed = False
        self.join_calls = 0

    def start(self):
        self.alive = self.alive_after_start
        if self.on_start:
            self.on_start()

    def join(self, timeout=None):
        self.join_calls += 1

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        if self.terminate_stops:
            self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False


class FakeContext:
    def __init__(self, receiver, child):
        self.receiver = receiver
        self.sender = FakeSender()
        self.child = child
        self.process_args = None

    def Pipe(self, duplex=False):
        if duplex is not False:
            raise AssertionError("pipe must be one-way")
        return self.receiver, self.sender

    def Process(self, *, target, args, name):
        self.process_args = (target, args, name)
        return self.child



def real_spawn_success_child(destination, sender):
    path = Path(destination)
    payload = b"real windows spawned snapshot"
    path.write_bytes(payload)
    sender.send(("ok", str(path), "e" * 64, len(payload), .01, .01,
                 {"active": False, "stored_bytes": len(payload)}))
    sender.close()


def real_spawn_hanging_child(destination, sender):
    Path(destination).write_bytes(b"partial spawned snapshot")
    time.sleep(60)
    sender.close()


class ProcessFetchLifecycleTests(unittest.TestCase):
    def make(self, root, receiver, child):
        ctx = FakeContext(receiver, child)
        op = ProcessFetch(
            ctx, lambda *args: None, ("cfg", "spec"),
            root / "snapshot.log",
            poll_timeout_s=.01, join_timeout_s=.01, kill_timeout_s=.01)
        return op, ctx

    def test_success_transfers_completed_snapshot_ownership(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_ok_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"complete snapshot"
            receiver = FakeReceiver()
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            receiver.message = (
                "ok", str(dest), "a" * 64, len(payload), .25, 1.5)
            op, ctx = self.make(root, receiver, child)
            with op:
                result = op.finish()
            self.assertEqual(result.path, dest)
            self.assertEqual(result.bytes, len(payload))
            self.assertEqual(result.digest, "a" * 64)
            self.assertGreaterEqual(result.ready_latency_s, 0)
            self.assertTrue(dest.is_file())
            self.assertFalse(child.terminated)
            self.assertFalse(child.killed)
            self.assertTrue(receiver.closed)
            self.assertTrue(ctx.sender.closed)

    def test_child_error_removes_partial_snapshot(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_error_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver(("error", "FetchError"))
            child = FakeChild(
                on_start=lambda: dest.write_bytes(b"partial"), exitcode=1)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(ProcessFetchError, "FetchError"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())

    def test_timeout_terminates_child_and_removes_partial_snapshot(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_timeout_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver(ready=False)
            child = FakeChild(
                on_start=lambda: dest.write_bytes(b"partial"),
                alive_after_start=True)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(TimeoutError, "did not return"):
                with op:
                    op.finish()
            self.assertTrue(child.terminated)
            self.assertFalse(child.killed)
            self.assertFalse(dest.exists())

    def test_parent_parse_failure_aborts_running_child(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_parent_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver(ready=False)
            child = FakeChild(
                on_start=lambda: dest.write_bytes(b"partial"),
                alive_after_start=True)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(ValueError, "parse failed"):
                with op:
                    raise ValueError("parse failed")
            self.assertTrue(child.terminated)
            self.assertFalse(dest.exists())

    def test_abort_escalates_to_kill_when_terminate_does_not_stop(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_kill_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver(ready=False)
            child = FakeChild(
                on_start=lambda: dest.write_bytes(b"partial"),
                alive_after_start=True, terminate_stops=False)
            op, _ = self.make(root, receiver, child)
            op.start()
            op.abort()
            self.assertTrue(child.terminated)
            self.assertTrue(child.killed)
            self.assertFalse(dest.exists())

    def test_unexpected_returned_path_fails_closed_without_deleting_foreign_file(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_path_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            foreign = root / "foreign.log"
            payload = b"foreign"
            receiver = FakeReceiver()
            def start():
                dest.write_bytes(b"partial")
                foreign.write_bytes(payload)
            child = FakeChild(on_start=start)
            receiver.message = (
                "ok", str(foreign), "b" * 64, len(payload), .2, .5)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(ProcessFetchError, "unexpected snapshot path"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())
            self.assertEqual(foreign.read_bytes(), payload)

    def test_incomplete_snapshot_fails_closed_and_cleans_owned_path(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_short_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver()
            child = FakeChild(on_start=lambda: dest.write_bytes(b"short"))
            receiver.message = (
                "ok", str(dest), "c" * 64, 999, .2, .5)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(ProcessFetchError, "snapshot incomplete"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())



class CaptureSender:
    def __init__(self):
        self.messages = []
        self.closed = False

    def send(self, message):
        self.messages.append(message)

    def close(self):
        self.closed = True


class SSHChildContractTests(unittest.TestCase):
    def cfg(self, root):
        return ConnectConfig(
            "host", 22, "user", "", "", "", "/srv/akuz",
            root / "downloads", "*.log", "", False)

    def test_child_renames_owned_fetch_and_returns_capture_metadata(self):
        with TemporaryDirectory(prefix="akuz_process_child_") as td:
            root = Path(td)
            target = root / "prefetch" / "next.log"
            target.parent.mkdir()
            produced = target.parent / "akuz_v4_internal.log"
            payload = b"snapshot bytes"
            details = {
                "active": True, "captured_bytes": len(payload),
                "stored_bytes": len(payload), "dropped_tail_bytes": 0,
                "listed_bytes": len(payload), "remote_path": "/srv/akuz/a.log"}
            sender = CaptureSender()

            def fake_fetch(cfg, remote, notify):
                self.assertEqual(cfg.local_dest, target.parent)
                produced.write_bytes(payload)
                return produced, "d" * 64, details

            with patch("akuz_process_fetch._arm_parent_watchdog"), \
                 patch("akuz_process_fetch.fetch_selected", side_effect=fake_fetch), \
                 patch("scripts.phase9_memory.sample",
                       return_value={"cpu_time_s": .5,
                                     "working_set_bytes": 1,
                                     "private_bytes": 1}):
                ssh_fetch_child(
                    self.cfg(root), {"path": "/srv/akuz/a.log"},
                    str(target), sender)

            self.assertTrue(sender.closed)
            self.assertFalse(produced.exists())
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(len(sender.messages), 1)
            message = sender.messages[0]
            self.assertEqual(message[0], "ok")
            self.assertEqual(Path(message[1]), target)
            self.assertEqual(message[2], "d" * 64)
            self.assertEqual(message[3], len(payload))
            self.assertEqual(message[6], details)

    def test_child_failure_reports_only_exception_type(self):
        with TemporaryDirectory(prefix="akuz_process_child_fail_") as td:
            root = Path(td)
            target = root / "prefetch" / "next.log"
            target.parent.mkdir()
            sender = CaptureSender()
            with patch("akuz_process_fetch._arm_parent_watchdog"), \
                 patch(
                    "akuz_process_fetch.fetch_selected",
                    side_effect=RuntimeError("secret payload text")):
                ssh_fetch_child(
                    self.cfg(root), {"path": "/srv/akuz/a.log"},
                    str(target), sender)
            self.assertTrue(sender.closed)
            self.assertEqual(sender.messages, [("error", "RuntimeError")])
            self.assertFalse(target.exists())



class ParentWatchdogTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows process-handle watchdog")
    def test_hard_parent_exit_terminates_spawned_child(self):
        from scripts.phase9_memory import sample

        with TemporaryDirectory(prefix="akuz_process_watchdog_") as td:
            root = Path(td)
            pid_file = root / "child.pid"
            helper = Path(__file__).with_name("phase11_watchdog_helper.py")
            proc = subprocess.run(
                [sys.executable, str(helper), str(pid_file)],
                cwd=Path(__file__).resolve().parents[1],
                timeout=30, check=False)
            self.assertEqual(proc.returncode, 79)
            self.assertTrue(pid_file.is_file())
            child_pid = int(pid_file.read_text("ascii"))
            deadline = time.time() + 10
            while sample(child_pid) is not None and time.time() < deadline:
                time.sleep(.05)
            self.assertIsNone(
                sample(child_pid),
                "spawned prefetch child survived hard parent termination")


class RealSpawnLifecycleTests(unittest.TestCase):
    def test_real_spawn_returns_completed_snapshot(self):
        with TemporaryDirectory(prefix="akuz_process_real_spawn_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            op = ProcessFetch(
                multiprocessing.get_context("spawn"),
                real_spawn_success_child, (), target,
                poll_timeout_s=20, join_timeout_s=10, kill_timeout_s=5)
            with op:
                result = op.finish()
            self.assertEqual(result.path, target)
            self.assertEqual(result.digest, "e" * 64)
            self.assertEqual(result.metadata["active"], False)
            self.assertEqual(target.read_bytes(), b"real windows spawned snapshot")

    def test_real_spawn_parent_abort_terminates_and_cleans_partial(self):
        with TemporaryDirectory(prefix="akuz_process_real_abort_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            op = ProcessFetch(
                multiprocessing.get_context("spawn"),
                real_spawn_hanging_child, (), target,
                poll_timeout_s=20, join_timeout_s=2, kill_timeout_s=5)
            with self.assertRaisesRegex(ValueError, "parent parse failed"):
                with op:
                    deadline = time.time() + 10
                    while not target.exists() and time.time() < deadline:
                        time.sleep(.05)
                    self.assertTrue(target.exists(), "spawned child did not create partial snapshot")
                    raise ValueError("parent parse failed")
            self.assertFalse(target.exists())
            self.assertFalse(op.child.is_alive())


if __name__ == "__main__":
    unittest.main()
