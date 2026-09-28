from __future__ import annotations

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


def job_child(pid_file, destination, sender):
    Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    Path(destination).write_bytes(b"owned partial")
    time.sleep(60)
    sender.close()


def main():
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    pid_file = root / "child.pid"
    destination = root / "owned.log"
    op = ProcessFetch(
        get_job_bound_spawn_context(),
        job_child, (str(pid_file),), destination,
        poll_timeout_s=30, join_timeout_s=5, kill_timeout_s=5,
        expected_listed_bytes=len(b"owned partial"),
        require_kill_job=True, safe_ipc=True)
    op.start()
    deadline = time.time() + 15
    while not pid_file.is_file() and time.time() < deadline:
        time.sleep(.05)
    if not pid_file.is_file():
        raise SystemExit(2)
    # Deliberately bypass all Python cleanup. Closing the parent process must
    # close the Job Object handle and terminate the assigned child tree.
    os._exit(79)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
