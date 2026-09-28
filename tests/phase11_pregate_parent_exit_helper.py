from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from akuz_process_fetch import ProcessFetch
from akuz_win_job_spawn import get_job_bound_spawn_context
from scripts.phase9_memory import sample


def hanging_child(destination, sender):
    Path(destination).write_bytes(b"owned partial")
    time.sleep(60)
    sender.close()


def main():
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    pid_file = root / "child.pid"
    destination = root / "owned.log"
    payload_size = len(b"owned partial")

    op = ProcessFetch(
        get_job_bound_spawn_context(),
        hanging_child, (), destination,
        poll_timeout_s=30, join_timeout_s=5, kill_timeout_s=5,
        expected_listed_bytes=payload_size,
        require_kill_job=True, safe_ipc=True)
    op.start()

    identity = sample(op.child.pid)
    if identity is None:
        raise SystemExit(3)
    pid_file.write_text(json.dumps({
        "pid": op.child.pid,
        "creation_time_ticks": identity["creation_time_ticks"],
    }), encoding="ascii")

    # start() can return only after CreateProcess(CREATE_SUSPENDED), exact
    # Job assignment and ResumeThread. Exit immediately afterwards; the Job
    # handle must close at process teardown and kill the same child identity.
    os._exit(79)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
