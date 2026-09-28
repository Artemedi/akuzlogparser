"""Bounded one-child process lifecycle for Phase 11 prefetch.

This module contains only process ownership/cancellation/cleanup mechanics.
It does not know about AKUZ inventory/cache and is not wired into perform_build.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import os
import pickle
import re
import threading
from multiprocessing import parent_process
from multiprocessing.connection import wait as wait_connections
from time import perf_counter, process_time, sleep

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
            sentinel = getattr(self.child, "sentinel", None)
            if sentinel is None:
                if not self.receiver.poll(self.poll_timeout_s):
                    raise TimeoutError("Process fetch did not return")
            else:
                ready = wait_connections(
                    [self.receiver, sentinel], timeout=self.poll_timeout_s)
                if not ready:
                    raise TimeoutError("Process fetch did not return")
                if self.receiver not in ready:
                    self.child.join(timeout=self.join_timeout_s)
                    raise ProcessFetchError(
                        "Process fetch child exited before IPC result")
            try:
                message = self.receiver.recv()
            except (EOFError, OSError, pickle.UnpicklingError) as exc:
                raise ProcessFetchError("Process fetch pipe closed or invalid") from exc
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
        survivor = False
        try:
            if child is not None:
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
            self.destination.unlink(missing_ok=True)
        if survivor:
            raise ProcessFetchError("Process fetch child could not be terminated")

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
        sender.send((
            "ok", str(target), digest, target.stat().st_size,
            cpu, perf_counter() - started, safe_details))
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
