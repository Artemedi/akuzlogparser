"""Phase 12 real normal-app full-fetch vs opt-in delta integration gate.

One full control and one delta candidate are enough here because transport
performance was already accepted by the separate balanced 3+3 P12-03 gate.
This gate verifies the real application transaction/report/analytics path.

Real payload stays only in owned TemporaryDirectory workspaces. Stdout never
prints host, remote path, digest, credentials or raw log content.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from time import perf_counter, process_time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import akuz_app
from akuz_app import State, perform_build_current, perform_list
from akuz_fetch import load_config
from akuz_store import key_for, load_store, save_store, sha256
from scripts.bench_phase9_baseline import inventory_manifest
from scripts.phase9_semantic import semantic_exports, semantic_sql


def _target(state: State, day: str):
    rows = [row for row in state.listing if row["name"] == day + "_server.log"]
    if len(rows) != 1:
        raise AssertionError("Requested trusted day missing or ambiguous")
    row = rows[0]
    if row.get("device") is None or row.get("inode") is None:
        raise AssertionError("Remote device/inode unavailable")
    return row


def _prepare(root: Path, cfg, day: str):
    state = State()
    with patch.object(akuz_app, "source_config", return_value=cfg):
        perform_list(root, state, source="linux")
    row = _target(state, day)
    selected = [dict(
        id=row["id"],
        date=date(int(day[:4]), int(day[4:6]), int(day[6:])).isoformat(),
    )]
    return state, row, selected


def _run_build(root: Path, cfg, state: State, selected, *, delta: bool):
    env = {
        "AKUZ_PHASE12_DELTA_RESUME": "1" if delta else "0",
        "AKUZ_PHASE11_PROCESS_PREFETCH": "0",
    }
    wall0, cpu0 = perf_counter(), process_time()
    with patch.dict(os.environ, env, clear=False),          patch.object(akuz_app, "source_config", return_value=cfg):
        perform_build_current(root, state, selected)
    elapsed = perf_counter() - wall0
    cpu = process_time() - cpu0
    if state.result["analytics_warning"]:
        raise AssertionError("Analytics warning in Phase 12 normal-app gate")
    if state.result["active_snapshots"]:
        raise AssertionError("Source changed during Phase 12 normal-app gate")
    return round(elapsed, 6), round(cpu, 6)


def _current_download(root: Path, expected_size: int):
    store = load_store(root)
    rows = [row for row in store["downloads"].values()
            if row.get("size") == expected_size]
    if len(rows) != 1:
        raise AssertionError("Expected exactly one current snapshot")
    row = rows[0]
    path = Path(row["path"])
    if not path.is_file() or path.stat().st_size != expected_size:
        raise AssertionError("Current snapshot missing or wrong size")
    if sha256(path) != row["sha256"]:
        raise AssertionError("Current snapshot SHA mismatch")
    return row, path


def _report_signature(root: Path):
    manifest = inventory_manifest(root)
    reports = manifest["reports"]
    if len(reports) != 1:
        raise AssertionError("Expected exactly one single report")
    return reports


def _seed_previous(candidate_root: Path, cfg, source_row: dict,
                   control_snapshot: Path, previous_bytes: int):
    downloads = candidate_root / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    previous = downloads / "akuz_v4_phase12_previous_20260925_server.log"
    with control_snapshot.open("rb") as src, previous.open("xb") as dst:
        remaining = previous_bytes
        while remaining:
            block = src.read(min(1024 * 1024, remaining))
            if not block:
                raise AssertionError("Control snapshot shorter than previous prefix")
            dst.write(block)
            remaining -= len(block)
    if previous.stat().st_size != previous_bytes:
        raise AssertionError("Previous prefix setup size mismatch")

    digest = sha256(previous)
    store = load_store(candidate_root)
    seed_id = key_for(
        "phase12-real-seed", cfg.host, source_row["path"], previous_bytes)
    store["downloads"][seed_id] = dict(
        path=str(previous),
        sha256=digest,
        size=previous_bytes,
        host=cfg.host,
        remote=source_row["path"],
        mtime=source_row["mtime"],
        snapshot=dict(
            active=False,
            captured_bytes=previous_bytes,
            stored_bytes=previous_bytes,
            dropped_tail_bytes=0,
            listed_bytes=previous_bytes,
            remote_path=source_row["path"],
            device=source_row["device"],
            inode=source_row["inode"],
            delta_proof_version=1,
        ),
    )
    save_store(candidate_root, store)
    return previous


def run(config_path: Path, app_root: Path, day: str, previous_bytes: int):
    with tempfile.TemporaryDirectory(prefix="akuz-phase12-normal-app-") as temp:
        base = Path(temp)
        control_root = base / "control"
        candidate_root = base / "candidate"
        control_root.mkdir()
        candidate_root.mkdir()

        base_cfg = replace(
            load_config(config_path, app_root),
            compression=False,
        )
        control_cfg = replace(base_cfg, local_dest=control_root / "downloads")
        candidate_cfg = replace(base_cfg, local_dest=candidate_root / "downloads")

        control_state, control_listed, control_selected = _prepare(
            control_root, control_cfg, day)
        fixed_size = control_listed["size"]
        fixed_identity = (
            control_listed["device"], control_listed["inode"])
        if previous_bytes <= 0 or previous_bytes >= fixed_size:
            raise AssertionError("Previous prefix not inside current snapshot")

        control_wall, control_cpu = _run_build(
            control_root, control_cfg, control_state, control_selected,
            delta=False)
        control_row, control_snapshot = _current_download(
            control_root, fixed_size)
        control_sha = control_row["sha256"]

        # Re-list before candidate setup. The integration gate requires one
        # immutable source version; it does not silently compare two versions.
        candidate_state, candidate_listed, candidate_selected = _prepare(
            candidate_root, candidate_cfg, day)
        if (candidate_listed["size"] != fixed_size
                or (candidate_listed["device"], candidate_listed["inode"])
                   != fixed_identity):
            raise AssertionError("Remote source version changed between modes")

        _seed_previous(
            candidate_root, candidate_cfg, candidate_listed,
            control_snapshot, previous_bytes)

        candidate_wall, candidate_cpu = _run_build(
            candidate_root, candidate_cfg, candidate_state,
            candidate_selected, delta=True)
        if candidate_state.result.get("delta_resume_downloads") != 1:
            raise AssertionError("Normal app did not use exactly one delta resume")
        if candidate_state.result.get("delta_resume_fallbacks") != 0:
            raise AssertionError("Normal app unexpectedly fell back to full fetch")

        candidate_row, candidate_snapshot = _current_download(
            candidate_root, fixed_size)
        if candidate_row["sha256"] != control_sha:
            raise AssertionError("Full and delta snapshot SHA differ")
        if sha256(candidate_snapshot) != control_sha:
            raise AssertionError("Delta current snapshot bytes differ")

        control_reports = _report_signature(control_root)
        candidate_reports = _report_signature(candidate_root)
        if control_reports != candidate_reports:
            raise AssertionError("Deterministic report manifests differ")
        if semantic_sql(control_root) != semantic_sql(candidate_root):
            raise AssertionError("Semantic analytics SQLite differs")
        if semantic_exports(control_root) != semantic_exports(candidate_root):
            raise AssertionError("Semantic analytics export differs")

        # Final remote listing must still bind to the same fixed version.
        final_state, final_row, _ = _prepare(candidate_root, candidate_cfg, day)
        del final_state
        if (final_row["size"] != fixed_size
                or (final_row["device"], final_row["inode"]) != fixed_identity):
            raise AssertionError("Remote source changed before final acceptance")

        return dict(
            status="PHASE12_NORMAL_APP_REAL",
            day=day,
            fixed_prefix_bytes=fixed_size,
            previous_bytes=previous_bytes,
            delta_bytes=fixed_size - previous_bytes,
            control_wall_s=control_wall,
            control_cpu_s=control_cpu,
            delta_wall_s=candidate_wall,
            delta_cpu_s=candidate_cpu,
            wall_reduction_pct=round(
                (control_wall - candidate_wall) / control_wall * 100.0, 3),
            snapshot_equivalence=True,
            report_equivalence=True,
            semantic_sql_equivalence=True,
            semantic_export_equivalence=True,
            delta_resume_downloads=1,
            delta_resume_fallbacks=0,
            compression=False,
            phase11_process_prefetch=False,
            raw_payload_retained=False,
            app_cache_changed=False,
            release_changed=False,
        )


def _public(result):
    return {
        key: result[key] for key in (
            "fixed_prefix_bytes", "previous_bytes", "delta_bytes",
            "control_wall_s", "control_cpu_s", "delta_wall_s", "delta_cpu_s",
            "wall_reduction_pct", "snapshot_equivalence",
            "report_equivalence", "semantic_sql_equivalence",
            "semantic_export_equivalence", "delta_resume_downloads",
            "delta_resume_fallbacks")
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--day", default="20260925")
    parser.add_argument("--previous-bytes", type=int, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(
            args.config, args.app_root, args.day, args.previous_bytes)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite Phase 12 normal-app evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE12_NORMAL_APP_REAL=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
        print("SNAPSHOT_EQUIVALENCE=PASS")
        print("REPORT_EQUIVALENCE=PASS")
        print("SEMANTIC_SQL_EQUIVALENCE=PASS")
        print("SEMANTIC_EXPORT_EQUIVALENCE=PASS")
        print("DELTA_RESUME_DOWNLOADS=1")
        print("DELTA_RESUME_FALLBACKS=0")
        print("COMPRESSION_FORCED_OFF=PASS")
        print("PHASE11_PROCESS_PREFETCH=OFF")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("APP_CACHE_CHANGED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        # Never surface exception text: it may contain a local/remote path.
        print("PHASE12_NORMAL_APP_REAL=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
