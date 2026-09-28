"""Bounded one-child process lifecycle for Phase 11 prefetch.

This module contains only process ownership/cancellation/cleanup mechanics.
It does not know about AKUZ inventory/cache and is not wired into perform_build.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import os
import threading
from time import perf_counter, process_time

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
                 name="akuz-phase11-fetch", require_metrics=False):
        self.ctx = ctx
        self.target = target
        self.args = tuple(args)
        self.destination = Path(destination)
        self.poll_timeout_s = poll_timeout_s
        self.join_timeout_s = join_timeout_s
        self.kill_timeout_s = kill_timeout_s
        self.name = name
        self.require_metrics = require_metrics
        self.receiver = None
        self.child = None
        self.started_at = None
        self._success = False

    def start(self):
        if self.child is not None:
            raise RuntimeError("ProcessFetch already started")
        receiver, sender = self.ctx.Pipe(duplex=False)
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
            self.destination.unlink(missing_ok=True)
            raise
        sender.close()
        return self

    def finish(self) -> ProcessFetchResult:
        if self.child is None or self.receiver is None or self.started_at is None:
            raise RuntimeError("ProcessFetch not started")
        message = None
        try:
            if not self.receiver.poll(self.poll_timeout_s):
                raise TimeoutError("Process fetch did not return")
            try:
                message = self.receiver.recv()
            except EOFError as exc:
                raise ProcessFetchError("Process fetch pipe closed") from exc
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
            if not returned.is_file() or returned.stat().st_size != count:
                raise ProcessFetchError("Process fetch snapshot incomplete")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ProcessFetchError("Process fetch digest invalid")
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
            self.receiver.close()
            self.receiver = None
            if not self._success:
                self.abort()

    def abort(self):
        child = self.child
        if child is not None and child.is_alive():
            child.terminate()
            child.join(timeout=self.kill_timeout_s)
            if child.is_alive():
                child.kill()
                child.join(timeout=self.kill_timeout_s)
        self.destination.unlink(missing_ok=True)

    def close(self):
        if not self._success:
            self.abort()
        if self.receiver is not None:
            self.receiver.close()
            self.receiver = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.close()


def _arm_parent_watchdog():
    """On Windows, hard-exit this child when its parent process disappears."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    parent_pid = os.getppid()
    handle = kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        raise ProcessFetchError('Parent process watchdog unavailable')

    def watch():
        while True:
            result = kernel32.WaitForSingleObject(handle, 500)
            if result == wait_timeout:
                continue
            if result == wait_object_0:
                os._exit(86)
            os._exit(87)

    thread = threading.Thread(
        target=watch, name='akuz-prefetch-parent-watch', daemon=True)
    thread.start()
    return thread


def ssh_fetch_child(cfg, remote: dict, destination: str, sender) -> None:
    """Spawn-safe SSH fetch into an owned temporary destination.

    The child never writes inventory. fetch_selected writes inside the owned
    destination directory; the completed file is renamed to the exact IPC
    destination before success is reported.
    """
    target = Path(destination)
    fetched = None
    try:
        _arm_parent_watchdog()
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
        sender.send((
            "ok", str(target), digest, target.stat().st_size,
            cpu, perf_counter() - started, details))
    except BaseException as exc:
        try:
            sender.send(("error", type(exc).__name__))
        except BaseException:
            pass
        # Do not re-raise in the child: multiprocessing would print the full
        # exception message/remote path to stderr. The parent fails closed on
        # the sanitized IPC tuple.
        return
    finally:
        sender.close()
