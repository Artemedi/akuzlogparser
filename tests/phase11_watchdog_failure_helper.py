from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import akuz_process_fetch


class Sender:
    def send(self, message):
        raise AssertionError("watchdog failure must exit before IPC send")

    def close(self):
        pass


def fail_watchdog():
    raise akuz_process_fetch.ProcessFetchError("watchdog unavailable")


akuz_process_fetch._arm_parent_watchdog = fail_watchdog
akuz_process_fetch.fetch_selected = lambda *args, **kwargs: (
    (_ for _ in ()).throw(AssertionError("fetch must not run")))

# ssh_fetch_child must terminate this helper via os._exit(88).
akuz_process_fetch.ssh_fetch_child(
    None, {}, str(Path(sys.argv[1]) / "never.log"), Sender())
raise SystemExit(99)
