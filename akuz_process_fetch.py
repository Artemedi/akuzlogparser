"""Bounded one-child process lifecycle for Phase 11 prefetch.

This module contains only process ownership/cancellation/cleanup mechanics.
It does not know about AKUZ inventory/cache and is not wired into perform_build.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import json
import os
import pickle
import re
import threading
from multiprocessing import parent_process
from time import monotonic, perf_counter, process_time, sleep

from akuz_fetch import fetch_selected


class ProcessFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessFetchResult:
    path: Path
    digest: str
    bytes: int
    child_cpu_s: float | None
    child_fetch_wall_s: float | None
    ready_latency_s: float
    metadata: dict


class ProcessFetch:
    """Own one spawned fetch child and one destination path.

    The parent may do CPU work after start(). On any parent/child failure the
    child is terminated and the owned destination is removed. Success transfers
    ownership of the completed destination to the caller.
    """

    def __init__(self, ctx, target, args, destination: Path,
                 *, poll_timeout_s=300, join_timeout_s=20, kill_timeout_s=10,
                 name="akuz-phase11-fetch", require_metrics=False,
                 expected_listed_bytes=None, require_kill_job=False,
                 safe_ipc=False):
        self.ctx = ctx
        self.target = target
        self.args = tuple(args)
        self.destination = Path(destination)
        self.poll_timeout_s = poll_timeout_s
        self.join_timeout_s = join_timeout_s
        self.kill_timeout_s = kill_timeout_s
        self.name = name
        self.require_metrics = require_metrics
        self.expected_listed_bytes = expected_listed_bytes
        self.require_kill_job = require_kill_job
        self.safe_ipc = safe_ipc
        if require_kill_job:
            if (not isinstance(expected_listed_bytes, int)
                    or isinstance(expected_listed_bytes, bool)
                    or expected_listed_bytes < 0):
                raise ProcessFetchError(
                    "Production Job prefetch requires listed-size binding")
            if not safe_ipc:
                raise ProcessFetchError(
                    "Production Job prefetch requires safe IPC")
        self.receiver = None
        self._start_gate = None
        self._kill_job = None
        self.child = None
        self.started_at = None
        self._success = False

    def start(self):
        if self.child is not None:
            raise RuntimeError("ProcessFetch already started")
        receiver, sender = self.ctx.Pipe(duplex=False)
        if self.require_kill_job:
            if os.name != "nt":
                receiver.close()
                sender.close()
                raise ProcessFetchError(
                    "Kill-on-close Job Object requires Windows")
            self._start_gate = self.ctx.Event()
            child = self.ctx.Process(
                target=_run_after_parent_gate,
                args=(self.target, self.args, str(self.destination),
                      sender, self._start_gate),
                name=self.name)
        else:
            child = self.ctx.Process(
                target=self.target,
                args=(*self.args, str(self.destination), sender),
                name=self.name)
        self.receiver = receiver
        self.child = child
        self.started_at = perf_counter()
        try:
            child.start()
        except BaseException:
            receiver.close()
            sender.close()
            self.receiver = None
            self.child = None
            self.started_at = None
            remove_owned_snapshot(self.destination)
            raise
        if self.require_kill_job:
            try:
                self._kill_job = _create_kill_on_close_job(child)
                self._start_gate.set()
            except BaseException:
                try:
                    self.abort()
                finally:
                    try:
                        sender.close()
                    except BaseException:
                        pass
                raise
        try:
            sender.close()
        except BaseException:
            try:
                self.abort()
            finally:
                try:
                    sender.close()
                except BaseException:
                    pass
            raise
        return self

    def finish(self) -> ProcessFetchResult:
        if self.child is None or self.receiver is None or self.started_at is None:
            raise RuntimeError("ProcessFetch not started")
        message = None
        try:
            deadline = monotonic() + self.poll_timeout_s
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Process fetch did not return")
                try:
                    if self.receiver.poll(min(0.10, remaining)):
                        break
                except (EOFError, OSError) as exc:
                    raise ProcessFetchError(
                        "Process fetch pipe closed or invalid") from exc
                if not self.child.is_alive():
                    self.child.join(timeout=0)
                    # One short final drain covers a child that flushed IPC
                    # immediately before exiting without depending on the
                    # platform-specific multiprocessing sentinel handle.
                    try:
                        if self.receiver.poll(min(0.10, max(0.0, remaining))):
                            break
                    except (EOFError, OSError) as exc:
                        raise ProcessFetchError(
                            "Process fetch pipe closed or invalid") from exc
                    raise ProcessFetchError(
                        "Process fetch child exited before IPC result")
            try:
                if self.safe_ipc:
                    raw = self.receiver.recv_bytes(maxlength=8192)
                    message = json.loads(raw.decode("utf-8"))
                else:
                    message = self.receiver.recv()
            except (EOFError, OSError, pickle.UnpicklingError,
                    UnicodeDecodeError, ValueError) as exc:
                raise ProcessFetchError(
                    "Process fetch pipe closed or invalid") from exc
            self.child.join(timeout=self.join_timeout_s)
            if self.child.is_alive():
                raise TimeoutError("Process fetch did not exit")
            if self.child.exitcode != 0 or not message or message[0] != "ok":
                kind = message[1] if message and message[0] == "error" else "ChildExit"
                raise ProcessFetchError("Process fetch failed: " + str(kind))
            if len(message) not in (6, 7):
                raise ProcessFetchError("Process fetch IPC shape invalid")
            _, returned_path, digest, count, child_cpu, child_fetch_wall = message[:6]
            metadata = message[6] if len(message) == 7 else {}
            if not isinstance(metadata, dict):
                raise ProcessFetchError("Process fetch metadata invalid")
            returned = Path(returned_path)
            if returned.resolve() != self.destination.resolve():
                raise ProcessFetchError("Process fetch returned unexpected snapshot path")
            if (not isinstance(count, int) or isinstance(count, bool)
                    or count < 0):
                raise ProcessFetchError("Process fetch size invalid")
            if not returned.is_file() or returned.stat().st_size != count:
                raise ProcessFetchError("Process fetch snapshot incomplete")
            if (not isinstance(digest, str) or
                    re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                raise ProcessFetchError("Process fetch digest invalid")
            actual_digest = _sha256_file(returned)
            if actual_digest != digest:
                raise ProcessFetchError("Process fetch snapshot checksum mismatch")
            allowed_metadata = {
                "active", "captured_bytes", "stored_bytes",
                "dropped_tail_bytes", "listed_bytes"}
            if any(key not in allowed_metadata for key in metadata):
                raise ProcessFetchError("Process fetch metadata contains unknown fields")
            if "active" in metadata and not isinstance(metadata["active"], bool):
                raise ProcessFetchError("Process fetch active flag invalid")
            for key in allowed_metadata - {"active"}:
                if key in metadata and (
                        not isinstance(metadata[key], int)
                        or isinstance(metadata[key], bool)
                        or metadata[key] < 0):
                    raise ProcessFetchError("Process fetch metadata invalid")
            if self.expected_listed_bytes is not None:
                expected = self.expected_listed_bytes
                if (not isinstance(expected, int) or isinstance(expected, bool)
                        or expected < 0):
                    raise ProcessFetchError("Process fetch expected size invalid")
                if metadata.get("listed_bytes") != expected:
                    raise ProcessFetchError(
                        "Process fetch listed-size binding mismatch")
                if metadata.get("stored_bytes") != count:
                    raise ProcessFetchError(
                        "Process fetch stored-size binding mismatch")
                captured = metadata.get("captured_bytes")
                if not isinstance(captured, int) or captured < count:
                    raise ProcessFetchError(
                        "Process fetch captured-size binding mismatch")
                if metadata.get("active") is False and captured != count:
                    raise ProcessFetchError(
                        "Process fetch static snapshot size mismatch")
            if self.require_metrics:
                if child_cpu is None or child_cpu <= 0:
                    raise ProcessFetchError("Process fetch CPU evidence unavailable")
                if child_fetch_wall is None or child_fetch_wall <= 0:
                    raise ProcessFetchError("Process fetch wall evidence unavailable")
            result = ProcessFetchResult(
                path=returned,
                digest=digest,
                bytes=count,
                child_cpu_s=(None if child_cpu is None else float(child_cpu)),
                child_fetch_wall_s=(None if child_fetch_wall is None else float(child_fetch_wall)),
                ready_latency_s=perf_counter() - self.started_at,
                metadata=dict(metadata))
            self._success = True
            return result
        finally:
            receiver = self.receiver
            self.receiver = None
            if receiver is not None:
                try:
                    receiver.close()
                except OSError:
                    # Pipe teardown must never skip process/file cleanup.
                    pass
            if not self._success:
                self.abort()

    def abort(self):
        child = self.child
        survivor = False
        try:
            if child is not None:
                if child.is_alive() and self._kill_job is not None:
                    _close_windows_handle(self._kill_job)
                    self._kill_job = None
                    child.join(timeout=self.kill_timeout_s)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=self.kill_timeout_s)
                if child.is_alive():
                    child.kill()
                    child.join(timeout=self.kill_timeout_s)
                survivor = child.is_alive()
                if not survivor:
                    # Reap an already-exited child as well; do not leave a
                    # zombie/process handle solely because is_alive() was false.
                    child.join(timeout=0)
        finally:
            if self._kill_job is not None:
                _close_windows_handle(self._kill_job)
                self._kill_job = None
            self._start_gate = None
            if self.receiver is not None:
                try:
                    self.receiver.close()
                finally:
                    self.receiver = None
        if survivor:
            raise ProcessFetchError("Process fetch child could not be terminated")
        # Only remove the owned file after the child/tree has been reaped and
        # the kill Job Object handle has been closed. On Windows an exiting
        # process may release its file handle a few milliseconds after join;
        # retry rather than silently leaving a partial snapshot behind.
        remove_owned_snapshot(self.destination)

    def close(self):
        if not self._success:
            self.abort()
        if self.receiver is not None:
            self.receiver.close()
            self.receiver = None
        if self._kill_job is not None:
            _close_windows_handle(self._kill_job)
            self._kill_job = None
        self._start_gate = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.close()


def remove_owned_snapshot(path: Path, *, attempts=40, delay_s=0.10):
    """Remove one owned temporary snapshot or fail closed if it survives."""
    target = Path(path)
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            target.unlink(missing_ok=True)
            if not target.exists():
                return
        except OSError as exc:
            last_error = exc
        if attempt + 1 < max(1, attempts):
            sleep(delay_s)
    if target.exists():
        raise ProcessFetchError(
            "Owned process snapshot could not be removed") from last_error


def _run_after_parent_gate(target, args, destination, sender, gate):
    """Do not begin child I/O until the parent has assigned a kill Job Object."""
    parent = parent_process()
    while not gate.wait(0.05):
        if parent is None:
            sender.close()
            os._exit(89)
        try:
            if not parent.is_alive():
                sender.close()
                os._exit(89)
        except BaseException:
            sender.close()
            os._exit(89)
    target(*args, destination, sender)


def _create_kill_on_close_job(child):
    """Assign a Windows child process tree to JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE."""
    if os.name != "nt":
        raise ProcessFetchError("Windows Job Object unavailable")
    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ProcessFetchError("Could not create Windows kill Job Object")
    process_handle = None
    try:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ProcessFetchError(
                "Could not configure Windows kill Job Object")
        # AssignProcessToJobObject requires PROCESS_SET_QUOTA and
        # PROCESS_TERMINATE. Open an explicit handle with those rights rather
        # than assuming multiprocessing's sentinel has a stable access mask.
        process_set_quota = 0x0100
        process_terminate = 0x0001
        process_query_limited_information = 0x1000
        process_handle = kernel32.OpenProcess(
            process_set_quota | process_terminate |
            process_query_limited_information,
            False, int(child.pid))
        if not process_handle:
            raise ProcessFetchError(
                "Could not open prefetch child for Windows Job assignment")
        if not kernel32.AssignProcessToJobObject(handle, process_handle):
            raise ProcessFetchError(
                "Could not assign prefetch child to Windows kill Job Object")
        return int(handle)
    except BaseException:
        _close_windows_handle(handle)
        raise
    finally:
        if process_handle:
            _close_windows_handle(process_handle)


def _close_windows_handle(handle):
    if handle is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(wintypes.HANDLE(int(handle)))


def _arm_parent_watchdog():
    """Hard-exit a Windows spawn child when its multiprocessing parent dies.

    multiprocessing gives a spawned child a parent sentinel. Using that
    sentinel avoids reopening a PID (and the PID-reuse race) and leaves handle
    lifetime to multiprocessing itself.
    """
    if os.name != "nt":
        return None
    parent = parent_process()
    if parent is None:
        raise ProcessFetchError("Parent process watchdog unavailable")

    def watch():
        while True:
            try:
                if not parent.is_alive():
                    os._exit(86)
            except BaseException:
                os._exit(87)
            sleep(0.1)

    thread = threading.Thread(
        target=watch, name="akuz-prefetch-parent-watch", daemon=True)
    thread.start()
    return thread


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ssh_fetch_child(cfg, remote: dict, destination: str, sender) -> None:
    """Spawn-safe SSH fetch into an owned temporary destination.

    The child never writes inventory. fetch_selected writes inside the owned
    destination directory; the completed file is renamed to the exact IPC
    destination before success is reported.
    """
    target = Path(destination)
    fetched = None
    try:
        try:
            _arm_parent_watchdog()
        except BaseException:
            # A process-prefetch child without parent-death supervision is not
            # safe to continue on Windows: it could outlive a hard-exited
            # portable parent. Exit without emitting remote/path details.
            sender.close()
            os._exit(88)
        started = perf_counter()
        cpu_started = process_time()
        child_cfg = replace(cfg, local_dest=target.parent)
        fetched, digest, details = fetch_selected(
            child_cfg, remote, notify=lambda message: None)
        if fetched.resolve() != target.resolve():
            if target.exists():
                raise ProcessFetchError("Owned prefetch destination already exists")
            fetched.replace(target)
            fetched = target
        cpu = process_time() - cpu_started
        # IPC carries only bounded snapshot facts. The parent already owns the
        # trusted remote identity/path and reconstructs it for inventory.
        safe_details = {
            key: details[key] for key in (
                "active", "captured_bytes", "stored_bytes",
                "dropped_tail_bytes", "listed_bytes")
            if key in details
        }
        sender.send_bytes(json.dumps([
            "ok", str(target), digest, target.stat().st_size,
            cpu, perf_counter() - started, safe_details
        ], ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    except BaseException as exc:
        try:
            sender.send_bytes(json.dumps(
                ["error", type(exc).__name__],
                ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8"))
        except BaseException:
            pass
        # Do not re-raise in the child: multiprocessing would print the full
        # exception message/remote path to stderr. The parent fails closed on
        # the sanitized IPC tuple.
        return
    finally:
        sender.close()
