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

from akuz_process_fetch import _run_after_parent_gate
from scripts.phase9_memory import sample


def never_run(destination, sender):
    Path(destination).write_bytes(b"unexpected")
    sender.close()


def main():
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    pid_file = root / "child.pid"
    destination = root / "owned.log"

    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    gate = ctx.Event()
    child = ctx.Process(
        target=_run_after_parent_gate,
        args=(never_run, (), str(destination), sender, gate),
        name="akuz-phase11-pregate-parent-death")
    child.start()
    sender.close()
    deadline = time.time() + 5
    identity = None
    while identity is None and time.time() < deadline:
        identity = sample(child.pid)
        if identity is None:
            time.sleep(.01)
    if identity is None:
        raise SystemExit(3)
    pid_file.write_text(json.dumps({
        "pid": child.pid,
        "creation_time_ticks": identity["creation_time_ticks"],
    }), encoding="ascii")
    # Deliberately exit before assigning a Job Object or opening the gate.
    os._exit(79)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
