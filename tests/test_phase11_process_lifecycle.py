from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_process_fetch import ProcessFetch, ProcessFetchError


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


if __name__ == "__main__":
    unittest.main()
