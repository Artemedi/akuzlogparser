"""Real normal-app Phase 11 process-prefetch acceptance benchmark.

Uses only historical 24-27 Sep SSH logs, two disposable E:-volume workspaces,
and compares serial vs opt-in process-prefetch through full report/inventory/
analytics semantics. No raw payload, paths, hostnames or digests are emitted.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from time import perf_counter, process_time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import akuz_app
from akuz_app import State
from akuz_fetch import fetch_selected, list_remote, load_config
from scripts.bench_phase11_process_overlap import _TreeMemorySampler
from scripts.bench_phase9_baseline import inventory_manifest
from scripts.phase9_semantic import semantic_exports, semantic_sql


DAYS = ("20260924", "20260925", "20260926", "20260927")


def _discover(cfg):
    listing = list_remote(cfg, notify=lambda message: None)
    result = []
    for day in DAYS:
        matches = [row for row in listing if row["name"] == day + "_server.log"]
        if len(matches) != 1:
            raise AssertionError("Expected one historical source for each day")
        row = matches[0]
        if row.get("device") is None or row.get("inode") is None:
            raise AssertionError("Remote device/inode unavailable")
        result.append(dict(row))
    return result


def _build_summary(root: Path):
    path = root / "diagnostics" / "performance.txt"
    if not path.is_file():
        raise AssertionError("Missing performance trace")
    line = next((
        row for row in reversed(path.read_text("utf-8").splitlines())
        if "stage=build.summary status=done" in row), None)
    if line is None:
        raise AssertionError("Missing build.summary")
    def metric(name, cast=float, default=None):
        match = re.search(r"(?:^| )" + re.escape(name) + r"=([^ ]+)", line)
        if match is None:
            if default is not None:
                return default
            raise AssertionError("Missing build metric " + name)
        return cast(match.group(1))
    return dict(
        prefetch_downloads=metric("process_prefetch_downloads", int, 0),
        child_cpu_s=metric("process_prefetch_child_cpu_s", float, 0.0),
        fresh_downloads=metric("fresh_downloads", int),
        restore_downloads=metric("restore_downloads", int),
        singles_new=metric("singles_new", int),
        singles_reused=metric("singles_reused", int),
        active_snapshots=metric("active_snapshots", int),
        combined_status=metric("combined_status", int))


def _run_mode(root: Path, base_cfg, frozen_listing, process_enabled: bool):
    root.mkdir(parents=True, exist_ok=True)
    cfg = replace(base_cfg, local_dest=(root / "downloads").resolve())
    state = State()
    state.source = "linux"
    state.listing = [dict(row) for row in frozen_listing]
    selected = [dict(id=row["id"], date="") for row in frozen_listing]

    def current_list(config, source, notify):
        rows = _discover(config)
        if [row["path"] for row in rows] != [row["path"] for row in frozen_listing]:
            raise AssertionError("Remote path set changed")
        return rows

    parent_cpu0 = process_time()
    wall0 = perf_counter()
    with patch.dict(os.environ, {
            "AKUZ_PHASE11_PROCESS_PREFETCH": "1" if process_enabled else "0"}),          patch("akuz_app.source_config", return_value=cfg),          patch("akuz_app.source_list", side_effect=current_list),          _TreeMemorySampler() as memory:
        akuz_app.perform_build(
            root, state, selected, fetch_fn=fetch_selected,
            refresh_remote=True, use_derived_spool=True)
    wall_s = perf_counter() - wall0
    parent_cpu_s = process_time() - parent_cpu0

    result = state.result
    if result is None or result.get("analytics_warning"):
        raise AssertionError("Normal-app result or analytics failed")
    if len(result["reports"]) != len(DAYS) or result["combined"] is None:
        raise AssertionError("Expected four singles plus combined")
    if list((root / "downloads").glob(".akuz-phase11-prefetch-*")):
        raise AssertionError("Prefetch workspace leaked")

    build = _build_summary(root)
    signatures = dict(
        inventory=inventory_manifest(root),
        sql=semantic_sql(root),
        exports=semantic_exports(root))
    return dict(
        wall_s=round(wall_s, 6),
        parent_cpu_s=round(parent_cpu_s, 6),
        child_cpu_s=round(build["child_cpu_s"], 6),
        total_cpu_s=round(parent_cpu_s + build["child_cpu_s"], 6),
        peak_private_bytes=memory.peak_private,
        peak_working_set_bytes=memory.peak_ws,
        memory_samples=memory.samples,
        unreadable_memory_samples=memory.unreadable,
        max_sampled_processes=memory.max_processes,
        build=build,
        reports=len(result["reports"]),
        combined_events=result["combined"]["events"],
        reused=result["reused"],
        signatures=signatures)


def run(config_path: Path, app_root: Path, diagnostics: Path):
    base_cfg = load_config(config_path, app_root)
    frozen = _discover(base_cfg)
    bytes_total = sum(row["size"] for row in frozen)
    if bytes_total < 800_000_000:
        raise AssertionError("Historical acceptance set unexpectedly small")

    workspace = None
    serial_metrics = process_metrics = None
    parity = {}
    with tempfile.TemporaryDirectory(
            prefix="phase11_normal_app_real_", dir=diagnostics) as td:
        workspace = Path(td)
        serial_root = workspace / "serial"
        process_root = workspace / "process"

        serial = _run_mode(serial_root, base_cfg, frozen, False)
        serial_signatures = serial.pop("signatures")
        shutil.rmtree(serial_root)

        process = _run_mode(process_root, base_cfg, frozen, True)
        process_signatures = process.pop("signatures")

        parity = dict(
            inventory=(serial_signatures["inventory"] ==
                       process_signatures["inventory"]),
            analytics_sql=(serial_signatures["sql"] ==
                           process_signatures["sql"]),
            analytics_exports=(serial_signatures["exports"] ==
                               process_signatures["exports"]))
        if not all(parity.values()):
            raise AssertionError("Serial/process normal-app semantics differ")
        if serial["build"]["prefetch_downloads"] != 0:
            raise AssertionError("Serial unexpectedly prefetched")
        if process["build"]["prefetch_downloads"] < 2:
            raise AssertionError("Process path did not exercise one-ahead prefetch")
        if process["max_sampled_processes"] < 2:
            raise AssertionError("Process child was not observed")
        if serial["unreadable_memory_samples"] or process["unreadable_memory_samples"]:
            raise AssertionError("Memory sampling incomplete")

        serial_metrics = serial
        process_metrics = process

    cleaned = bool(workspace) and not workspace.exists()
    if not cleaned:
        raise AssertionError("Acceptance workspace cleanup failed")
    return dict(
        status="PHASE11_NORMAL_APP_REAL_STATIC_ACCEPTANCE",
        days=list(DAYS),
        source_bytes=bytes_total,
        serial=serial_metrics,
        process=process_metrics,
        parity=parity,
        workspace_cleaned=True,
        raw_payload_saved=False,
        source_digests_saved=False,
        app_user_cache_changed=False,
        release_changed=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite Phase 11 acceptance evidence")
        result = run(args.config, args.app_root, args.diagnostics)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PHASE11_NORMAL_APP_REAL_ACCEPTANCE=PASS")
        print("SOURCE_BYTES", result["source_bytes"])
        for mode in ("serial", "process"):
            row = result[mode]
            print("MODE", mode,
                  "wall_s", row["wall_s"],
                  "parent_cpu_s", row["parent_cpu_s"],
                  "child_cpu_s", row["child_cpu_s"],
                  "total_cpu_s", row["total_cpu_s"],
                  "peak_private_bytes", row["peak_private_bytes"],
                  "max_processes", row["max_sampled_processes"],
                  "prefetch_downloads", row["build"]["prefetch_downloads"])
        print("INVENTORY_PARITY=PASS")
        print("ANALYTICS_SQL_PARITY=PASS")
        print("ANALYTICS_EXPORT_PARITY=PASS")
        print("WORKSPACE_CLEANED=PASS")
        print("NO_RAW_PAYLOAD_OR_DIGEST_OUTPUT=PASS")
    except BaseException as exc:
        print("PHASE11_NORMAL_APP_REAL_ACCEPTANCE_FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
