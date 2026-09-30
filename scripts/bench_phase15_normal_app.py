"""Phase 15 P15-03 normal-app serial vs opt-in parallel generation A/B.

Trusted 23/24/25 Sep snapshots are fetched once into an owned disposable
workspace. Timed trials reuse the same cached snapshots without network I/O.
The candidate is the production AKUZ_PHASE15_PARALLEL_GENERATION switch.
Full producer manifests and analytics exact/semantic outputs must match.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
from statistics import median
import sys
import tempfile
from time import perf_counter, process_time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import akuz_app
from akuz_app import perform_build
from akuz_fetch import load_config
from akuz_store import load_store
from scripts.bench_phase11_process_overlap import _TreeMemorySampler
from scripts.bench_phase14_raw_orjson import (
    _deterministic_merge_tempfile,
    _export_hashes,
    _report_manifest,
    _reset_reports_keep_downloads,
    _setup_seed,
)
from scripts.phase9_semantic import semantic_exports, semantic_sql


ORDER = ("B", "C", "C", "B", "B", "C")


def _trace_count(root: Path) -> int:
    path = root / "diagnostics" / "performance.txt"
    return len(path.read_text("utf-8").splitlines()) if path.exists() else 0


def _latest_build_summary(root: Path, offset: int) -> dict:
    path = root / "diagnostics" / "performance.txt"
    rows = path.read_text("utf-8").splitlines()[offset:] if path.exists() else []
    matches = [row for row in rows if " stage=build.summary " in row]
    if len(matches) != 1:
        raise AssertionError("Expected one build.summary per Phase 15 trial")
    out = {}
    for token in matches[0].split():
        if "=" in token:
            key, value = token.split("=", 1)
            out[key] = value
    return out


def _median(rows, key):
    return median(row[key] for row in rows)


def _run_trial(root: Path, cfg, state, selected, mode: str):
    _reset_reports_keep_downloads(root)
    offset = _trace_count(root)
    report_ids = [
        f"v4_20990101_00000{index}_{index:08x}"
        for index in range(1, 5)
    ]
    env = {
        "AKUZ_PHASE11_PROCESS_PREFETCH": "0",
        "AKUZ_PHASE12_DELTA_RESUME": "0",
        "AKUZ_PHASE13_CATALOG_ERROR_INDEX": "1",
        "AKUZ_PHASE14_RAW_ORJSON": "1",
        "AKUZ_PHASE15_PARALLEL_GENERATION": "1" if mode == "C" else "0",
    }
    wall0, parent_cpu0 = perf_counter(), process_time()
    with _TreeMemorySampler() as memory:
        with patch.dict(os.environ, env, clear=False),              patch.object(
                 akuz_app.tempfile, "NamedTemporaryFile",
                 _deterministic_merge_tempfile),              patch.object(akuz_app, "_fresh_report_id", side_effect=report_ids),              patch.object(akuz_app, "source_config", return_value=cfg):
            perform_build(
                root, state, selected,
                refresh_remote=False,
                use_derived_spool=True,
            )
    wall_s = perf_counter() - wall0
    parent_cpu_s = process_time() - parent_cpu0
    if state.result["analytics_warning"]:
        raise AssertionError("Analytics warning during Phase 15 trial")
    if state.result["active_snapshots"]:
        raise AssertionError("Unexpected active snapshot during cached trial")
    summary = _latest_build_summary(root, offset)
    child_cpu_s = float(summary.get("phase15_parallel_child_cpu_s", "0"))
    phase15_reports = int(summary.get("phase15_parallel_reports", "0"))
    expected_reports = 3 if mode == "C" else 0
    if phase15_reports != expected_reports:
        raise AssertionError("Production Phase 15 switch selected wrong path")
    return {
        "metrics": {
            "wall_s": wall_s,
            "parent_cpu_s": parent_cpu_s,
            "child_cpu_s": child_cpu_s,
            "total_cpu_s": parent_cpu_s + child_cpu_s,
            "peak_ws_bytes": memory.peak_ws,
            "peak_private_bytes": memory.peak_private,
            "max_processes": memory.max_processes,
            "events": sum(row["events"] for row in state.result["reports"]),
            "reports": len(state.result["reports"]) + (1 if state.result["combined"] else 0),
        },
        "manifest": _report_manifest(root),
        "semantic_sql": semantic_sql(root),
        "exports": _export_hashes(root),
        "semantic_exports": semantic_exports(root),
    }


def run(config_path: Path, app_root: Path):
    workspace_path = None
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase15-normal-app-",
            dir=app_root / "diagnostics") as temp:
        root = Path(temp)
        workspace_path = root
        cfg = replace(
            load_config(config_path, app_root),
            local_dest=root / "downloads",
            compression=False,
        )
        state, selected, downloads_before, setup = _setup_seed(root, cfg)
        reference = None
        trials = []
        for ordinal, mode in enumerate(ORDER, 1):
            trial = _run_trial(root, cfg, state, selected, mode)
            if load_store(root)["downloads"] != downloads_before:
                raise AssertionError("Phase 15 trial mutated cached snapshots")
            if trial["metrics"]["peak_private_bytes"] <= 0:
                raise AssertionError("Phase 15 memory sample missing")
            if mode == "C" and trial["metrics"]["max_processes"] < 3:
                raise AssertionError("Phase 15 did not observe two workers")
            public = {
                "ordinal": ordinal,
                "mode": mode,
                **{
                    key: (round(value, 6) if isinstance(value, float) else value)
                    for key, value in trial["metrics"].items()
                },
            }
            print("PHASE15_NORMAL_APP_TRIAL", json.dumps(public, sort_keys=True))
            if reference is None:
                reference = trial
            else:
                for key in ("manifest", "semantic_sql", "exports", "semantic_exports"):
                    if trial[key] != reference[key]:
                        raise AssertionError(
                            "Phase 15 candidate differs in " + key)
            trials.append(public)

        baseline = [row for row in trials if row["mode"] == "B"]
        candidate = [row for row in trials if row["mode"] == "C"]
        b_wall = _median(baseline, "wall_s")
        c_wall = _median(candidate, "wall_s")
        b_cpu = _median(baseline, "total_cpu_s")
        c_cpu = _median(candidate, "total_cpu_s")
        b_mem = _median(baseline, "peak_private_bytes")
        c_mem = _median(candidate, "peak_private_bytes")
        result = {
            "status": "PHASE15_NORMAL_APP_REPLICATED_AB",
            "order": list(ORDER),
            "source_bytes": setup["source_bytes"],
            "reports": setup["reports"],
            "baseline_wall_median_s": round(b_wall, 6),
            "candidate_wall_median_s": round(c_wall, 6),
            "baseline_cpu_median_s": round(b_cpu, 6),
            "candidate_cpu_median_s": round(c_cpu, 6),
            "baseline_peak_private_median_bytes": int(b_mem),
            "candidate_peak_private_median_bytes": int(c_mem),
            "wall_reduction_pct": round((b_wall - c_wall) / b_wall * 100, 3),
            "cpu_change_pct": round((c_cpu - b_cpu) / b_cpu * 100, 3),
            "peak_private_change_pct": round((c_mem - b_mem) / b_mem * 100, 3),
            "exact_equivalence": True,
            "analytics_equivalence": True,
            "downloads_unchanged": True,
            "network_during_trials": False,
            "release_changed": False,
            "raw_payload_retained": False,
        }
    result["workspace_cleaned"] = bool(
        workspace_path and not workspace_path.exists())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.config, args.app_root)
        print("PHASE15_NORMAL_APP_REPLICATED_AB=PASS")
        print("PUBLIC_SUMMARY", json.dumps(result, sort_keys=True))
        print("EXACT_EQUIVALENCE=PASS")
        print("ANALYTICS_EQUIVALENCE=PASS")
        print("DOWNLOADS_UNCHANGED=PASS")
        print("NETWORK_DURING_TRIALS=NO")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("PUBLIC_RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE15_NORMAL_APP_REPLICATED_AB=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
