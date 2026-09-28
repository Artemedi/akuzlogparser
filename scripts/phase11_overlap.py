"""Phase 11 test/benchmark-only one-ahead fetch/parse overlap scheduler.

No AKUZ application runtime imports. The caller owns snapshot creation and cleanup.
A parser is invoked only after its corresponding fetch has fully returned.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterable, TypeVar


T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class CompletedSnapshot:
    """Opaque completed snapshot proof passed from fetch to parse."""
    item: object
    path: Path
    digest: str
    bytes: int


@dataclass(frozen=True)
class PipelineResult:
    outputs: tuple
    wall_s: float
    fetched: int
    parsed: int


def _validate_snapshot(item, value) -> CompletedSnapshot:
    if not isinstance(value, CompletedSnapshot):
        raise TypeError("fetch_fn must return CompletedSnapshot")
    if value.item is not item:
        raise ValueError("snapshot item identity mismatch")
    if not isinstance(value.path, Path) or not value.path.is_file():
        raise ValueError("completed snapshot path is unavailable")
    if value.bytes != value.path.stat().st_size or value.bytes < 0:
        raise ValueError("completed snapshot size mismatch")
    if not isinstance(value.digest, str) or len(value.digest) != 64:
        raise ValueError("completed snapshot digest shape mismatch")
    return value


def run_serial(items: Iterable[T],
               fetch_fn: Callable[[T], CompletedSnapshot],
               parse_fn: Callable[[CompletedSnapshot], R]) -> PipelineResult:
    """Reference execution: fetch(i), then parse(i), strictly serial."""
    ordered = tuple(items)
    started = perf_counter()
    outputs = []
    fetched = parsed = 0
    for item in ordered:
        snapshot = _validate_snapshot(item, fetch_fn(item))
        fetched += 1
        outputs.append(parse_fn(snapshot))
        parsed += 1
    return PipelineResult(tuple(outputs), perf_counter() - started,
                          fetched, parsed)


def run_one_ahead(items: Iterable[T],
                  fetch_fn: Callable[[T], CompletedSnapshot],
                  parse_fn: Callable[[CompletedSnapshot], R]) -> PipelineResult:
    """Fetch at most one future source while parsing the current COMPLETE snapshot.

    The first source is fetched synchronously. Before parsing source i, source i+1
    may start in the sole worker. We always join an in-flight fetch before returning
    or re-raising, so a caller can safely clean its owned temporary workspace.
    """
    ordered = tuple(items)
    started = perf_counter()
    if not ordered:
        return PipelineResult((), perf_counter() - started, 0, 0)

    outputs = []
    fetched = parsed = 0
    future: Future | None = None
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="akuz-phase11-fetch")
    try:
        current = _validate_snapshot(ordered[0], fetch_fn(ordered[0]))
        fetched += 1
        for index, item in enumerate(ordered):
            if index + 1 < len(ordered):
                next_item = ordered[index + 1]
                future = executor.submit(fetch_fn, next_item)
            outputs.append(parse_fn(current))
            parsed += 1
            if future is not None:
                next_item = ordered[index + 1]
                current = _validate_snapshot(next_item, future.result())
                fetched += 1
                future = None
        return PipelineResult(tuple(outputs), perf_counter() - started,
                              fetched, parsed)
    finally:
        # cancel_futures stops work that has not started. shutdown(wait=True) also
        # joins a running fetch; production integration will need cooperative
        # network cancellation if immediate abort is a hard requirement.
        if future is not None:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
