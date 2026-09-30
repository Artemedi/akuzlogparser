"""Phase 16 real-source smoke: cache clear must preserve report reuse safely."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import tempfile
from unittest.mock import patch

import akuz_app
from akuz_app import State, perform_build_current, perform_clear, perform_list
from akuz_fetch import load_config
from akuz_store import load_store, report_intact


TARGET = "20260923_server.log"


def run(config_path: Path, app_root: Path):
    diag = app_root / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="akuz-phase16-real-", dir=diag) as tmp:
        root = Path(tmp)
        cfg = replace(
            load_config(config_path, app_root),
            local_dest=root / "downloads",
            compression=False,
        )
        state = State()
        with patch.object(akuz_app, "source_config", return_value=cfg):
            perform_list(root, state, source="linux")
            targets = [row for row in state.listing if row["name"] == TARGET]
            if len(targets) != 1:
                raise AssertionError("trusted Phase 16 target missing or duplicated")
            selected = [{"id": targets[0]["id"], "date": "2026-09-23"}]
            perform_build_current(root, state, selected)
            first = state.result["reports"][0]
            if first.get("reused"):
                raise AssertionError("fresh disposable Phase 16 report unexpectedly reused")
            first_id = first["id"]
            before = load_store(root)
            if len(before["downloads"]) != 1 or len(before["reports"]) != 1:
                raise AssertionError("unexpected fresh Phase 16 inventory shape")
            if not report_intact(before["reports"][first_id], root):
                raise AssertionError("fresh Phase 16 report not intact")
            revision_before_clear = state.listing_revision

            perform_clear(root, state, False)
            cleanup = state.result["cleanup"]
            after_clear = load_store(root)
            if cleanup["downloads_removed"] != 1:
                raise AssertionError("Phase 16 clear did not remove trusted snapshot")
            if cleanup.get("downloads_retained") != 0:
                raise AssertionError("Phase 16 real cache unexpectedly retained snapshot")
            if after_clear["downloads"]:
                raise AssertionError("Phase 16 clear left indexed trusted download")
            if set(after_clear["reports"]) != {first_id}:
                raise AssertionError("Phase 16 clear changed report inventory")
            if not report_intact(after_clear["reports"][first_id], root):
                raise AssertionError("Phase 16 clear damaged preserved report")
            if state.listing_revision <= revision_before_clear:
                raise AssertionError("Phase 16 clear did not invalidate stale listing")

            perform_list(root, state, source="linux")
            target = next(row for row in state.listing if row["name"] == TARGET)
            perform_build_current(
                root, state,
                [{"id": target["id"], "date": "2026-09-23"}],
            )
            second = state.result["reports"][0]
            if not second.get("reused") or second["id"] != first_id:
                raise AssertionError("Phase 16 preserved report did not reuse after clear")
            final = load_store(root)
            if final["downloads"]:
                raise AssertionError("single-report reuse unexpectedly refetched snapshot")

        return {
            "target": TARGET,
            "report_reused_after_clear": True,
            "downloads_removed": cleanup["downloads_removed"],
            "downloads_retained": cleanup.get("downloads_retained", 0),
            "reports_preserved": len(final["reports"]),
            "listing_revision": state.listing_revision,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config, args.app_root)
    print(
        "PHASE16_REAL_SMOKE=PASS "
        f"downloads_removed={result['downloads_removed']} "
        f"downloads_retained={result['downloads_retained']} "
        f"reports_preserved={result['reports_preserved']} "
        f"report_reused_after_clear={int(result['report_reused_after_clear'])} "
        f"listing_revision={result['listing_revision']}"
    )
    print("RAW_PAYLOAD_RETAINED=NO")
    print("PUBLIC_RELEASE_CHANGED=NO")


if __name__ == "__main__":
    main()
