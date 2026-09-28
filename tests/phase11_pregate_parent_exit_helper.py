from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from akuz_process_fetch import _run_after_parent_gate


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
    pid_file.write_text(str(child.pid), encoding="ascii")
    # Deliberately exit before assigning a Job Object or opening the gate.
    os._exit(79)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
