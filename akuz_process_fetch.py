"""Bounded one-child process lifecycle for Phase 11 prefetch.

This module contains only process ownership/cancellation/cleanup mechanics.
It does not know about AKUZ inventory/cache and is not wired into perform_build.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


class ProcessFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessFetchResult:
    path: Path
    digest: str
    bytes: int
    child_cpu_s: float
    child_fetch_wall_s: float
    ready_latency_s: float


class ProcessFetch:
    """Own one spawned fetch child and one destination path.

    The parent may do CPU work after start(). On any parent/child failure the
    child is terminated and the owned destination is removed. Success transfers
    ownership of the completed destination to the caller.
    """

    def __init__(self, ctx, target, args, destination: Path,
                 *, poll_timeout_s=300, join_timeout_s=20, kill_timeout_s=10,
                 name="akuz-phase11-fetch"):
        self.ctx = ctx
        self.target = target
        self.args = tuple(args)
        self.destination = Path(destination)
        self.poll_timeout_s = poll_timeout_s
        self.join_timeout_s = join_timeout_s
        self.kill_timeout_s = kill_timeout_s
        self.name = name
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
            _, returned_path, digest, count, child_cpu, child_fetch_wall = message
            returned = Path(returned_path)
            if returned.resolve() != self.destination.resolve():
                raise ProcessFetchError("Process fetch returned unexpected snapshot path")
            if not returned.is_file() or returned.stat().st_size != count:
                raise ProcessFetchError("Process fetch snapshot incomplete")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ProcessFetchError("Process fetch digest invalid")
            if child_cpu is None or child_cpu <= 0:
                raise ProcessFetchError("Process fetch CPU evidence unavailable")
            if child_fetch_wall is None or child_fetch_wall <= 0:
                raise ProcessFetchError("Process fetch wall evidence unavailable")
            result = ProcessFetchResult(
                path=returned,
                digest=digest,
                bytes=count,
                child_cpu_s=float(child_cpu),
                child_fetch_wall_s=float(child_fetch_wall),
                ready_latency_s=perf_counter() - self.started_at)
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
