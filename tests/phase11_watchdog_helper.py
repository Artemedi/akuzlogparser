from pathlib import Path
import multiprocessing
import os
import sys
import time

from akuz_process_fetch import _arm_parent_watchdog


def watched_child(pid_file):
    _arm_parent_watchdog()
    Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    time.sleep(60)


def main():
    pid_file = Path(sys.argv[1])
    child = multiprocessing.get_context("spawn").Process(
        target=watched_child, args=(str(pid_file),),
        name="akuz-phase11-watchdog-proof")
    child.start()
    deadline = time.time() + 15
    while not pid_file.is_file() and time.time() < deadline:
        time.sleep(.05)
    if not pid_file.is_file():
        raise SystemExit(2)
    # Deliberately bypass finally/atexit so only the child's parent watchdog
    # can stop the spawned process.
    os._exit(79)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
