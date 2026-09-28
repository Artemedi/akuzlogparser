from pathlib import Path
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_process_fetch import (ProcessFetch, ProcessFetchError,
                                ProcessFetchUnsafeError,
                                _close_windows_handle,
                                _create_kill_on_close_job,
                                _owned_snapshot_stat,
                                _same_path_lexical, _sha256_owned_snapshot,
                                _windows_handle_value,
                                remove_owned_snapshot, ssh_fetch_child)
from akuz_fetch import ConnectConfig
from akuz_win_job_spawn import (JobBoundSpawnError,
                                get_job_bound_spawn_context)


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

    def recv_bytes(self, maxlength=None):
        if isinstance(self.message, BaseException):
            raise self.message
        if isinstance(self.message, bytes):
            data = self.message
        else:
            data = json.dumps(
                self.message, separators=(",", ":")).encode("utf-8")
        if maxlength is not None and len(data) > maxlength:
            raise OSError("bad message length")
        return data

    def close(self):
        self.closed = True


class CloseErrorReceiver(FakeReceiver):
    def close(self):
        self.closed = True
        raise OSError("injected pipe close failure")


class FakeSender:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeChild:
    def __init__(self, on_start=None, *, exitcode=0,
                 alive_after_start=False, terminate_stops=True,
                 kill_stops=True):
        self.on_start = on_start
        self.exitcode = exitcode
        self.alive = False
        self.alive_after_start = alive_after_start
        self.terminate_stops = terminate_stops
        self.kill_stops = kill_stops
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
        if self.kill_stops:
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
    sender.send_bytes(json.dumps([
        "ok", str(path), hashlib.sha256(payload).hexdigest(),
        len(payload), .01, .01,
        {"active": False, "stored_bytes": len(payload)}
    ], separators=(",", ":")).encode("utf-8"))
    sender.close()


def real_spawn_safe_child(destination, sender):
    path = Path(destination)
    payload = b"real windows spawned snapshot"
    path.write_bytes(payload)
    sender.send_bytes(json.dumps([
        "ok", str(path), hashlib.sha256(payload).hexdigest(),
        len(payload), .01, .01, {
            "active": False,
            "captured_bytes": len(payload),
            "stored_bytes": len(payload),
            "dropped_tail_bytes": 0,
            "listed_bytes": len(payload),
        }
    ], separators=(",", ":")).encode("utf-8"))
    sender.close()


def real_spawn_crash_child(destination, sender):
    Path(destination).write_bytes(b"partial before crash")
    os._exit(91)


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

    def test_production_job_requires_listed_size_and_json_ipc(self):
        with TemporaryDirectory(prefix="akuz_process_prod_contract_") as td:
            root = Path(td)
            receiver = FakeReceiver(ready=False)
            child = FakeChild()
            ctx = FakeContext(receiver, child)
            with self.assertRaisesRegex(
                    ProcessFetchError, "listed-size binding"):
                ProcessFetch(
                    ctx, lambda *args: None, (), root / "x.log",
                    require_kill_job=True)
            with self.assertRaisesRegex(
                    ProcessFetchError, "bounded JSON IPC"):
                ProcessFetch(
                    ctx, lambda *args: None, (), root / "x.log",
                    expected_listed_bytes=1, require_kill_job=True,
                    safe_ipc=False)

    def test_safe_ipc_rejects_malformed_json_and_cleans_snapshot(self):
        with TemporaryDirectory(prefix="akuz_process_json_bad_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver(b"{not-json", ready=True)
            child = FakeChild(on_start=lambda: dest.write_bytes(b"partial"))
            ctx = FakeContext(receiver, child)
            op = ProcessFetch(
                ctx, lambda *args: None, (), dest,
                poll_timeout_s=.01, join_timeout_s=.01, kill_timeout_s=.01,
                safe_ipc=True)
            with self.assertRaisesRegex(
                    ProcessFetchError, "pipe closed or invalid"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())

    def test_safe_ipc_rejects_oversized_message(self):
        with TemporaryDirectory(prefix="akuz_process_json_large_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver(b"x" * 9000, ready=True)
            child = FakeChild(on_start=lambda: dest.write_bytes(b"partial"))
            ctx = FakeContext(receiver, child)
            op = ProcessFetch(
                ctx, lambda *args: None, (), dest,
                poll_timeout_s=.01, join_timeout_s=.01, kill_timeout_s=.01,
                safe_ipc=True)
            with self.assertRaisesRegex(
                    ProcessFetchError, "pipe closed or invalid"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())

    def test_success_finish_releases_job_without_context_manager(self):
        with TemporaryDirectory(prefix="akuz_process_finish_job_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"complete snapshot"
            receiver = FakeReceiver()
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            digest = hashlib.sha256(payload).hexdigest()
            receiver.message = (
                "ok", str(dest), digest, len(payload), .25, 1.5)
            op, _ = self.make(root, receiver, child)
            op._kill_job = 123
            with patch("akuz_process_fetch._close_windows_handle") as close_handle:
                op.start()
                result = op.finish()
            self.assertEqual(result.digest, digest)
            close_handle.assert_called_once_with(123)
            self.assertIsNone(op._kill_job)
            self.assertIsNone(op._start_gate)

    def test_late_ipc_after_child_exit_is_accepted_beyond_100ms(self):
        with TemporaryDirectory(prefix="akuz_process_late_ipc_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"late buffered result"
            digest = hashlib.sha256(payload).hexdigest()

            class LateReceiver(FakeReceiver):
                def poll(self, timeout):
                    # Model Windows scheduler/pipe delivery lag > the old
                    # 100 ms heuristic after the process handle signaled.
                    time.sleep(0.20)
                    self.ready = True
                    return True

            receiver = LateReceiver(
                ("ok", str(dest), digest, len(payload), .2, .5),
                ready=False)
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            child.sentinel = object()
            child.alive = False
            ctx = FakeContext(receiver, child)
            op = ProcessFetch(
                ctx, lambda *args: None, (), dest,
                poll_timeout_s=2.0, join_timeout_s=.01, kill_timeout_s=.01)
            with op:
                result = op.finish()
            self.assertEqual(result.digest, digest)
            self.assertTrue(dest.is_file())


    def test_owned_snapshot_hash_rejects_same_size_identity_swap(self):
        with TemporaryDirectory(prefix="akuz_process_identity_swap_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            replacement = root / "replacement.log"
            target.write_bytes(b"AAAA")
            expected = _owned_snapshot_stat(target)
            replacement.write_bytes(b"BBBB")
            os.replace(replacement, target)
            with self.assertRaisesRegex(
                    ProcessFetchError, "identity changed before hashing"):
                _sha256_owned_snapshot(target, expected)

    def test_windows_path_identity_is_case_insensitive(self):
        with patch("akuz_process_fetch.os.path.normcase",
                   side_effect=lambda value: value.lower()):
            self.assertTrue(_same_path_lexical("C:/Temp/File.log",
                                               "c:/temp/file.log"))

    def test_owned_snapshot_rejects_reparse_file(self):
        with TemporaryDirectory(prefix="akuz_process_reparse_") as td:
            target = Path(td) / "snapshot.log"
            target.write_bytes(b"payload")
            real_lstat = Path.lstat
            def reparse_lstat(path):
                st = real_lstat(path)
                if path == target:
                    class Wrapped:
                        st_mode = st.st_mode
                        st_size = st.st_size
                        st_file_attributes = 0x0400
                    return Wrapped()
                return st
            with patch.object(Path, "lstat", new=reparse_lstat):
                with self.assertRaisesRegex(
                        ProcessFetchError, "reparse point"):
                    _owned_snapshot_stat(target)

    def test_owned_snapshot_rejects_symlink_flag(self):
        with TemporaryDirectory(prefix="akuz_process_symlink_") as td:
            target = Path(td) / "snapshot.log"
            target.write_bytes(b"payload")
            real_is_symlink = Path.is_symlink
            def fake_is_symlink(path):
                return path == target or real_is_symlink(path)
            with patch.object(Path, "is_symlink", new=fake_is_symlink):
                with self.assertRaisesRegex(
                        ProcessFetchError, "symlink"):
                    _owned_snapshot_stat(target)

    def test_windows_handle_value_uses_pointer_value_without_truncation(self):
        class Handle:
            value = 0x12345678ABCDEF01
        self.assertEqual(
            _windows_handle_value(Handle()), 0x12345678ABCDEF01)

    def test_success_transfers_completed_snapshot_ownership(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_ok_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"complete snapshot"
            receiver = FakeReceiver()
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            digest = hashlib.sha256(payload).hexdigest()
            receiver.message = (
                "ok", str(dest), digest, len(payload), .25, 1.5)
            op, ctx = self.make(root, receiver, child)
            with op:
                result = op.finish()
            self.assertEqual(result.path, dest)
            self.assertEqual(result.bytes, len(payload))
            self.assertEqual(result.digest, digest)
            self.assertGreaterEqual(result.ready_latency_s, 0)
            self.assertTrue(dest.is_file())
            self.assertFalse(child.terminated)
            self.assertFalse(child.killed)
            self.assertTrue(receiver.closed)
            self.assertTrue(ctx.sender.closed)

    def test_buffered_ipc_wins_over_already_signaled_child_sentinel(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_buffered_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"buffered result"
            receiver = FakeReceiver()
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            # Receiver data must win even if the child has already
            # exited; finish() no longer depends on a raw process sentinel.
            child.alive = False
            digest = hashlib.sha256(payload).hexdigest()
            receiver.message = (
                "ok", str(dest), digest, len(payload), .2, .5)
            op, _ = self.make(root, receiver, child)
            with op:
                result = op.finish()
            self.assertEqual(result.digest, digest)
            self.assertTrue(dest.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows handle deletion")
    def test_windows_handle_cleanup_deletes_exact_owned_file(self):
        with TemporaryDirectory(prefix="akuz_process_win_delete_") as td:
            target = Path(td) / "snapshot.log"
            target.write_bytes(b"owned")
            remove_owned_snapshot(target, attempts=2, delay_s=.01)
            self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == "nt", "Windows handle deletion")
    def test_windows_handle_cleanup_rejects_path_replacement(self):
        with TemporaryDirectory(prefix="akuz_process_win_swap_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            target.write_bytes(b"owned")
            import akuz_process_fetch as process_fetch
            real_identity = process_fetch._windows_cleanup_identity
            calls = {"count": 0}

            def racing_identity(handle):
                identity = real_identity(handle)
                calls["count"] += 1
                if calls["count"] == 2:
                    # Model a pathname that now resolves to a different file
                    # between the stable anchor and DELETE-handle open. The
                    # replacement itself is intentionally not performed here:
                    # Windows can deny rename while the anchor is open, and
                    # the contract under test is handle-identity mismatch.
                    return (identity[0], identity[1] + 1, identity[2])
                return identity

            with patch(
                    "akuz_process_fetch._windows_cleanup_identity",
                    side_effect=racing_identity):
                with self.assertRaisesRegex(
                        ProcessFetchUnsafeError, "pathname was replaced"):
                    remove_owned_snapshot(
                        target, attempts=2, delay_s=.01)
            self.assertGreaterEqual(calls["count"], 2)
            self.assertEqual(target.read_bytes(), b"owned")

    def test_owned_snapshot_cleanup_failure_is_unsafe(self):
        with TemporaryDirectory(prefix="akuz_process_cleanup_unsafe_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            dest.write_bytes(b"partial")
            if os.name == "nt":
                import akuz_process_fetch as process_fetch
                real_open = process_fetch._windows_open_cleanup_handle

                def blocked_delete_open(path, access):
                    if access & 0x00010000:
                        raise PermissionError("simulated persistent WinError 32")
                    return real_open(path, access)

                with patch(
                        "akuz_process_fetch._windows_open_cleanup_handle",
                        side_effect=blocked_delete_open), \
                     patch("akuz_process_fetch.sleep"):
                    with self.assertRaisesRegex(
                            ProcessFetchUnsafeError, "could not be removed"):
                        remove_owned_snapshot(dest, attempts=3, delay_s=.01)
            else:
                with patch.object(
                        Path, "unlink",
                        side_effect=PermissionError("simulated persistent lock")), \
                     patch("akuz_process_fetch.sleep"):
                    with self.assertRaisesRegex(
                            ProcessFetchUnsafeError, "could not be removed"):
                        remove_owned_snapshot(dest, attempts=3, delay_s=.01)
            self.assertTrue(dest.exists())

    def test_job_close_failure_after_validation_is_not_double_closed(self):
        class FailingOwner:
            def __init__(self):
                self.calls = 0

            def close(self):
                self.calls += 1
                raise ProcessFetchUnsafeError("close failed")

        with TemporaryDirectory(prefix="akuz_process_job_close_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"validated snapshot"
            digest = hashlib.sha256(payload).hexdigest()
            receiver = FakeReceiver(
                ("ok", str(dest), digest, len(payload), .2, .5))
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            op, _ = self.make(root, receiver, child)
            op.start()
            owner = FailingOwner()
            op._kill_job = owner
            with self.assertRaisesRegex(
                    ProcessFetchUnsafeError,
                    "Could not close Windows kill Job Object"):
                op.finish()
            self.assertEqual(owner.calls, 1)
            self.assertIsNone(op._kill_job)
            # Handle teardown failed after the snapshot had already passed
            # size/SHA/metadata validation. Fail closed, but do not destroy
            # that validated temp in the generic abort finally path.
            self.assertTrue(dest.exists())

    @unittest.skipIf(os.name == "nt", "POSIX fallback cleanup race")
    def test_owned_snapshot_cleanup_never_unlinks_replacement(self):
        with TemporaryDirectory(prefix="akuz_process_cleanup_swap_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            replacement = root / "replacement.log"
            dest.write_bytes(b"owned partial")
            replacement.write_bytes(b"external replacement")
            real_unlink = Path.unlink
            injected = {"done": False}

            def racing_unlink(path, *args, **kwargs):
                if path == dest and not injected["done"]:
                    injected["done"] = True
                    os.replace(replacement, dest)
                    raise PermissionError("simulated lock plus replacement")
                return real_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", new=racing_unlink), \
                 patch("akuz_process_fetch.sleep"):
                with self.assertRaisesRegex(
                        ProcessFetchUnsafeError, "pathname was replaced"):
                    remove_owned_snapshot(dest, attempts=3, delay_s=.01)

            self.assertTrue(injected["done"])
            self.assertEqual(dest.read_bytes(), b"external replacement")

    def test_owned_snapshot_cleanup_retries_transient_windows_lock(self):
        with TemporaryDirectory(prefix="akuz_process_cleanup_retry_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            dest.write_bytes(b"partial")
            calls = {"count": 0}
            if os.name == "nt":
                import akuz_process_fetch as process_fetch
                real_open = process_fetch._windows_open_cleanup_handle

                def flaky_open(path, access):
                    if access & 0x00010000 and calls["count"] < 2:
                        calls["count"] += 1
                        raise PermissionError("simulated transient WinError 32")
                    return real_open(path, access)

                with patch(
                        "akuz_process_fetch._windows_open_cleanup_handle",
                        side_effect=flaky_open), \
                     patch("akuz_process_fetch.sleep"):
                    remove_owned_snapshot(dest, attempts=4, delay_s=.01)
            else:
                real_unlink = Path.unlink

                def flaky_unlink(path, *args, **kwargs):
                    if path == dest and calls["count"] < 2:
                        calls["count"] += 1
                        raise PermissionError("simulated transient lock")
                    return real_unlink(path, *args, **kwargs)

                with patch.object(Path, "unlink", new=flaky_unlink), \
                     patch("akuz_process_fetch.sleep"):
                    remove_owned_snapshot(dest, attempts=4, delay_s=.01)
            self.assertEqual(calls["count"], 2)
            self.assertFalse(dest.exists())

    @unittest.skipUnless(os.name == "nt", "Windows validated cleanup identity")
    def test_validated_identity_swap_before_cleanup_preserves_replacement(self):
        with TemporaryDirectory(prefix="akuz_process_validated_swap_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            replacement = root / "replacement.log"
            target.write_bytes(b"AAAA")
            expected = _owned_snapshot_stat(target)
            digest, cleanup_identity = _sha256_owned_snapshot(
                target, expected, return_cleanup_identity=True)
            self.assertEqual(digest, hashlib.sha256(b"AAAA").hexdigest())
            self.assertIsNotNone(cleanup_identity)

            replacement.write_bytes(b"BBBB")
            os.replace(replacement, target)
            with self.assertRaisesRegex(
                    ProcessFetchUnsafeError,
                    "identity changed before cleanup"):
                remove_owned_snapshot(
                    target, attempts=2, delay_s=.01,
                    expected_identity=cleanup_identity)
            self.assertEqual(target.read_bytes(), b"BBBB")

    @unittest.skipUnless(os.name == "nt", "Windows strict Popen handle close")
    def test_jobbound_popen_close_propagates_handle_failure(self):
        import akuz_win_job_spawn as job_spawn

        class FakeFinalizer:
            def __init__(self):
                self.cancelled = False

            def still_active(self):
                return True

            def cancel(self):
                self.cancelled = True

        class FakeOwner:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        popen = object.__new__(job_spawn.JobBoundPopen)
        popen._closed = False
        popen.finalizer = FakeFinalizer()
        popen._akuz_job_owner = FakeOwner()
        popen._handle = 101
        popen._pipe_handle = 102

        calls = []
        def close_handle(value):
            calls.append(value)
            if value == 102:
                raise OSError("injected spawn-pipe close failure")

        with patch(
                "akuz_win_job_spawn._winapi.CloseHandle",
                side_effect=close_handle):
            with self.assertRaisesRegex(
                    JobBoundSpawnError,
                    "Could not close atomic Job-bound spawn handles"):
                popen.close()

        self.assertTrue(popen.finalizer.cancelled)
        self.assertTrue(popen._akuz_job_owner.closed)
        self.assertEqual(calls, [101, 102])
        self.assertTrue(popen._closed)
        self.assertIsNone(popen._handle)
        self.assertIsNone(popen._pipe_handle)

    def test_pipe_oserror_is_normalized_and_cleans_snapshot(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_pipe_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver(OSError("broken pipe"), ready=True)
            child = FakeChild(on_start=lambda: dest.write_bytes(b"partial"))
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(ProcessFetchError, "pipe closed or invalid"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())

    def test_non_hex_digest_is_rejected(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_digest_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"snapshot"
            receiver = FakeReceiver()
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            receiver.message = (
                "ok", str(dest), "Z" * 64, len(payload), .2, .5)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(ProcessFetchError, "digest invalid"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())

    def test_same_size_wrong_checksum_is_rejected_and_cleaned(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_hash_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"complete snapshot"
            receiver = FakeReceiver()
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            receiver.message = (
                "ok", str(dest), hashlib.sha256(b"different").hexdigest(),
                len(payload), .2, .5)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(
                    ProcessFetchError, "checksum mismatch"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())

    def test_parent_listed_size_binding_rejects_child_metadata_drift(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_binding_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            payload = b"complete snapshot"
            receiver = FakeReceiver()
            child = FakeChild(on_start=lambda: dest.write_bytes(payload))
            digest = hashlib.sha256(payload).hexdigest()
            receiver.message = (
                "ok", str(dest), digest, len(payload), .2, .5,
                {
                    "active": False,
                    "captured_bytes": len(payload),
                    "stored_bytes": len(payload),
                    "dropped_tail_bytes": 0,
                    "listed_bytes": len(payload) - 1,
                })
            ctx = FakeContext(receiver, child)
            op = ProcessFetch(
                ctx, lambda *args: None, ("cfg", "spec"), dest,
                poll_timeout_s=.01, join_timeout_s=.01,
                kill_timeout_s=.01,
                expected_listed_bytes=len(payload))
            with self.assertRaisesRegex(
                    ProcessFetchError, "listed-size binding mismatch"):
                with op:
                    op.finish()
            self.assertFalse(dest.exists())

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

    def test_pipe_close_failure_still_aborts_child_and_cleans_snapshot(self):
        with TemporaryDirectory(prefix="akuz_process_pipe_close_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = CloseErrorReceiver(ready=False)
            child = FakeChild(
                on_start=lambda: dest.write_bytes(b"partial"),
                alive_after_start=True)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(TimeoutError, "did not return"):
                with op:
                    op.finish()
            self.assertTrue(receiver.closed)
            self.assertTrue(child.terminated)
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

    def test_context_exit_surfaces_unsafe_cleanup_failure(self):
        with TemporaryDirectory(prefix="akuz_process_unsafe_exit_") as td:
            root = Path(td)
            receiver = FakeReceiver(ready=False)
            child = FakeChild(
                alive_after_start=True,
                terminate_stops=False,
                kill_stops=False)
            op, _ = self.make(root, receiver, child)
            with self.assertRaisesRegex(
                    ProcessFetchUnsafeError, "could not be terminated"):
                with op:
                    raise ValueError("parent parse failed")

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

    def test_abort_reaps_already_exited_child(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_reap_") as td:
            root = Path(td)
            receiver = FakeReceiver(ready=False)
            child = FakeChild(alive_after_start=False)
            op, _ = self.make(root, receiver, child)
            op.start()
            op.abort()
            op.abort()
            self.assertGreaterEqual(child.join_calls, 2)
            self.assertTrue(receiver.closed)
            self.assertIsNone(op.receiver)

    def test_abort_reports_child_that_survives_terminate_and_kill(self):
        with TemporaryDirectory(prefix="akuz_process_fetch_survivor_") as td:
            root = Path(td)
            dest = root / "snapshot.log"
            receiver = FakeReceiver(ready=False)
            child = FakeChild(
                on_start=lambda: dest.write_bytes(b"partial"),
                alive_after_start=True,
                terminate_stops=False,
                kill_stops=False)
            op, _ = self.make(root, receiver, child)
            op.start()
            with self.assertRaisesRegex(
                    ProcessFetchError, "could not be terminated"):
                op.abort()
            self.assertTrue(child.terminated)
            self.assertTrue(child.killed)
            # A child that survived both termination mechanisms may still
            # hold/write the file. Fail closed and leave the app-scoped temp
            # for orphan cleanup rather than unlinking under a live process.
            self.assertTrue(dest.exists())

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

    def send_bytes(self, payload):
        self.messages.append(json.loads(bytes(payload).decode("utf-8")))

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
            self.assertEqual(message[6], {
                "active": True,
                "captured_bytes": len(payload),
                "stored_bytes": len(payload),
                "dropped_tail_bytes": 0,
                "listed_bytes": len(payload),
            })
            self.assertNotIn("remote_path", message[6])

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
            self.assertEqual(sender.messages, [["error", "RuntimeError"]])
            self.assertFalse(target.exists())



class WatchdogStartupFailureTests(unittest.TestCase):
    def test_watchdog_start_failure_hard_exits_before_fetch(self):
        with TemporaryDirectory(prefix="akuz_process_watchdog_fail_") as td:
            root = Path(td)
            helper = Path(__file__).with_name(
                "phase11_watchdog_failure_helper.py")
            proc = subprocess.run(
                [sys.executable, str(helper), str(root)],
                cwd=Path(__file__).resolve().parents[1],
                timeout=20, check=False)
            self.assertEqual(proc.returncode, 88)
            self.assertFalse((root / "never.log").exists())


class PreGateParentDeathTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows parent sentinel watchdog")
    def test_hard_parent_exit_before_gate_kills_child(self):
        from scripts.phase9_memory import sample

        with TemporaryDirectory(prefix="akuz_process_pregate_") as td:
            root = Path(td)
            helper = Path(__file__).with_name(
                "phase11_pregate_parent_exit_helper.py")
            proc = subprocess.run(
                [sys.executable, str(helper), str(root)],
                cwd=Path(__file__).resolve().parents[1],
                timeout=30, check=False)
            self.assertEqual(proc.returncode, 79)
            identity = json.loads(
                (root / "child.pid").read_text("ascii"))
            child_pid = int(identity["pid"])
            child_created = int(identity["creation_time_ticks"])
            deadline = time.time() + 10
            current = sample(child_pid)
            while (current is not None and
                   current.get("creation_time_ticks") == child_created and
                   time.time() < deadline):
                time.sleep(.05)
                current = sample(child_pid)
            self.assertTrue(
                current is None or
                current.get("creation_time_ticks") != child_created,
                "same pre-gate child survived hard parent termination")
            self.assertFalse((root / "owned.log").exists())


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


class WindowsKillJobTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Job Object semantics")
    def test_hard_parent_exit_kills_atomically_bound_child(self):
        from scripts.phase9_memory import sample

        with TemporaryDirectory(prefix="akuz_process_job_") as td:
            root = Path(td)
            helper = Path(__file__).with_name(
                "phase11_job_object_helper.py")
            proc = subprocess.run(
                [sys.executable, str(helper), str(root)],
                cwd=Path(__file__).resolve().parents[1],
                timeout=30, check=False)
            self.assertEqual(proc.returncode, 79)
            pid_file = root / "child.pid"
            self.assertTrue(pid_file.is_file())
            child_pid = int(pid_file.read_text("ascii"))
            deadline = time.time() + 10
            while sample(child_pid) is not None and time.time() < deadline:
                time.sleep(.05)
            self.assertIsNone(
                sample(child_pid),
                "atomically Job-bound child survived hard parent termination")


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
            self.assertEqual(
                result.digest,
                hashlib.sha256(b"real windows spawned snapshot").hexdigest())
            self.assertEqual(result.metadata["active"], False)
            self.assertEqual(target.read_bytes(), b"real windows spawned snapshot")

    @unittest.skipUnless(os.name == "nt", "Windows Job Object semantics")
    def test_regular_spawn_context_is_rejected_before_production_child_start(self):
        with TemporaryDirectory(prefix="akuz_process_regular_ctx_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            op = ProcessFetch(
                multiprocessing.get_context("spawn"),
                real_spawn_hanging_child, (), target,
                poll_timeout_s=5, join_timeout_s=2, kill_timeout_s=2,
                expected_listed_bytes=1,
                require_kill_job=True, safe_ipc=True)
            with self.assertRaisesRegex(
                    ProcessFetchUnsafeError, "atomic Job-bound spawn"):
                op.start()
            self.assertFalse(target.exists())
            self.assertIsNone(op.child)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object semantics")
    def test_atomic_open_osfhandle_failure_closes_raw_pipe_handles(self):
        import akuz_win_job_spawn as job_spawn

        with TemporaryDirectory(prefix="akuz_process_openfd_fail_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            real_create_pipe = job_spawn._winapi.CreatePipe
            real_close = job_spawn._winapi.CloseHandle
            created = {}
            closed = []

            def capture_pipe(*args):
                pair = real_create_pipe(*args)
                created["handles"] = tuple(int(value) for value in pair)
                return pair

            def close_and_record(handle):
                closed.append(int(handle))
                return real_close(handle)

            op = ProcessFetch(
                get_job_bound_spawn_context(),
                real_spawn_hanging_child, (), target,
                poll_timeout_s=5, join_timeout_s=2, kill_timeout_s=2,
                expected_listed_bytes=1,
                require_kill_job=True, safe_ipc=True)
            with patch(
                    "akuz_win_job_spawn._winapi.CreatePipe",
                    side_effect=capture_pipe), \
                 patch(
                    "akuz_win_job_spawn.msvcrt.open_osfhandle",
                    side_effect=OSError("injected open_osfhandle failure")), \
                 patch(
                    "akuz_win_job_spawn._winapi.CloseHandle",
                    side_effect=close_and_record):
                with self.assertRaisesRegex(
                        OSError, "open_osfhandle failure"):
                    op.start()

            self.assertEqual(
                sorted(closed), sorted(created["handles"]))
            self.assertIsNone(op.child)
            self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == "nt", "Windows Job Object semantics")
    def test_atomic_createprocess_failure_closes_empty_job(self):
        class FakeOwner:
            def __init__(self):
                self.handle = 123
                self.closed = False

            def close(self):
                self.closed = True
                self.handle = None

        with TemporaryDirectory(prefix="akuz_process_create_fail_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            owner = FakeOwner()
            op = ProcessFetch(
                get_job_bound_spawn_context(),
                real_spawn_hanging_child, (), target,
                poll_timeout_s=5, join_timeout_s=2, kill_timeout_s=2,
                expected_listed_bytes=1,
                require_kill_job=True, safe_ipc=True)
            with patch(
                    "akuz_win_job_spawn._create_kill_job",
                    return_value=owner), \
                 patch(
                    "akuz_win_job_spawn._winapi.CreateProcess",
                    side_effect=OSError("injected CreateProcess failure")):
                with self.assertRaisesRegex(
                        OSError, "CreateProcess failure"):
                    op.start()
            self.assertTrue(owner.closed)
            self.assertIsNone(op.child)
            self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == "nt", "Windows Job Object semantics")
    def test_atomic_job_assignment_failure_never_resumes_child(self):
        with TemporaryDirectory(prefix="akuz_process_atomic_assign_fail_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            with patch(
                    "akuz_win_job_spawn._assign_job",
                    side_effect=JobBoundSpawnError(
                        "injected atomic assignment failure")):
                op = ProcessFetch(
                    get_job_bound_spawn_context(),
                    real_spawn_hanging_child, (), target,
                    poll_timeout_s=5, join_timeout_s=2, kill_timeout_s=2,
                    expected_listed_bytes=1,
                    require_kill_job=True, safe_ipc=True)
                with self.assertRaisesRegex(
                        JobBoundSpawnError, "assignment failure"):
                    op.start()
            self.assertFalse(target.exists())
            self.assertIsNone(op._kill_job)


    def test_real_spawn_required_kill_job_completes_successfully(self):
        with TemporaryDirectory(prefix="akuz_process_real_job_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            payload = b"real windows spawned snapshot"
            op = ProcessFetch(
                get_job_bound_spawn_context(),
                real_spawn_safe_child, (), target,
                poll_timeout_s=20, join_timeout_s=10, kill_timeout_s=5,
                expected_listed_bytes=len(payload),
                require_kill_job=True, safe_ipc=True)
            with op:
                result = op.finish()
            self.assertEqual(
                result.digest, hashlib.sha256(payload).hexdigest())
            self.assertEqual(target.read_bytes(), payload)
            self.assertTrue(
                getattr(op.child, "_closed", False),
                "successful production finish must close Process/Popen handles")

    def test_real_spawn_child_crash_fails_fast_and_cleans_partial(self):
        with TemporaryDirectory(prefix="akuz_process_real_crash_") as td:
            root = Path(td)
            target = root / "snapshot.log"
            op = ProcessFetch(
                multiprocessing.get_context("spawn"),
                real_spawn_crash_child, (), target,
                poll_timeout_s=20, join_timeout_s=5, kill_timeout_s=5)
            started = time.monotonic()
            with self.assertRaisesRegex(
                    ProcessFetchError,
                    "child exited before IPC|pipe closed or invalid"):
                with op:
                    op.finish()
            self.assertLess(time.monotonic() - started, 10)
            self.assertFalse(target.exists())
            self.assertFalse(op.child.is_alive())

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
