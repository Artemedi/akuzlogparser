"""Bounded, privacy-conscious performance trace for the local Explorer."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import threading
from time import perf_counter

_LOCK = threading.Lock()
MAX_BYTES = 3 * 1024 * 1024


def _write(root: Path, line: str):
    """Close file after every event: Windows can remove temporary test folders."""
    path = (Path(root) / "diagnostics" / "performance.txt").resolve()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size >= MAX_BYTES:
            for i in (2, 1):
                old = path.with_name(path.name + ("." + str(i - 1) if i > 1 else ""))
                new = path.with_name(path.name + "." + str(i))
                if old.exists():
                    old.replace(new)
        with path.open("a", encoding="utf-8") as output:
            output.write(line + "\n")

def event(root: Path, stage: str, status: str, **metrics):
    """Only code-defined stage names and numeric metrics; never raw log text/paths."""
    fields = " ".join(f"{key}={value}" for key, value in metrics.items()
                      if isinstance(value, (int, float, bool)))
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
    line = f"{timestamp} stage={stage} status={status}"
    if fields:
        line += " " + fields
    try:
        _write(root, line)
    except OSError:
        # Instrumentation must not make log processing fail.
        pass


@contextmanager
def phase(root: Path, stage: str, **metrics):
    start = perf_counter()
    event(root, stage, "start", **metrics)
    try:
        yield
    except Exception:
        event(root, stage, "failed", elapsed_s=round(perf_counter() - start, 3),
              **metrics)
        raise
    else:
        event(root, stage, "done", elapsed_s=round(perf_counter() - start, 3),
              **metrics)
