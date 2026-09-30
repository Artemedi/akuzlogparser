"""Phase 14 P14-01 stdlib json.dumps vs orjson real report-generation A/B.

The trusted 23/24/25 Sep sources are fetched once into an owned disposable
workspace. The seed reports are discarded. Six balanced B/C/C/B/B/C trials
then regenerate all reports from the exact same cached snapshots without
network fetch. The candidate changes only compact JSON encoding; JS escaping,
write/hash, report layout, analytics and source identity remain unchanged.

orjson is an experiment-only dependency installed by the dedicated workflow.
No raw payload, host, remote path or digest is printed.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from statistics import median
import shutil
import sys
import tempfile
from time import perf_counter, process_time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import akuz_app
import akuz_html_explorer
from akuz_app import State, perform_build, perform_build_current, perform_list
from akuz_fetch import load_config
from akuz_store import load_store, save_store
from scripts.bench_phase13_catalog_error_index import (
    DAYS,
    _export_hashes,
    _reset_analytics,
)
from scripts.phase9_semantic import semantic_exports, semantic_sql


ORDER = ("B", "C", "C", "B", "B", "C")
_METRIC_KEYS = (
    "wall_s",
    "cpu_s",
    "generate_elapsed_s",
    "json_elapsed_s",
    "escape_elapsed_s",
    "io_elapsed_s",
    "report_output_bytes",
    "events",
    "reports",
)


def _parse_trace_line(line: str):
    out = {"stage": line.split(maxsplit=1)[0]} if line else {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            out[key] = value
    return out


def _trace_count(root: Path) -> int:
    path = root / "diagnostics" / "performance.txt"
    return len(path.read_text("utf-8").splitlines()) if path.exists() else 0


def _trace_after(root: Path, offset: int):
    path = root / "diagnostics" / "performance.txt"
    if not path.exists():
        return []
    return [
        line.split(" stage=", 1)[1]
        for line in path.read_text("utf-8").splitlines()[offset:]
        if " stage=" in line
    ]


def _num(row, key):
    value = row.get(key)
    return float(value) if value is not None else 0.0


def _orjson_compact(value):
    import orjson
    return orjson.dumps(value).decode("utf-8")


def _report_manifest(root: Path):
    store = load_store(root)
    manifest = {}
    for rid, entry in sorted(store["reports"].items()):
        integrity = entry.get("integrity") or {}
        required = integrity.get("required_sha256") or {}
        all_hashes = integrity.get("all_sha256") or {}
        # provenance.json contains the publication timestamp and is deliberately
        # non-deterministic. Compare every producer-owned report byte instead.
        producer_hashes = {
            name: digest for name, digest in all_hashes.items()
            if name != "provenance.json"
        }
        manifest[rid] = {
            "producer_sha256": dict(sorted(producer_hashes.items())),
            "events": entry.get("events"),
            "kind": entry.get("kind"),
        }
    return manifest


def _reset_reports_keep_downloads(root: Path):
    store = load_store(root)
    for entry in list(store["reports"].values()):
        rid = entry.get("id")
        if isinstance(rid, str):
            shutil.rmtree(root / "reports" / rid, ignore_errors=True)
    store["reports"] = {}
    save_store(root, store)
    _reset_analytics(root)


def _trial_metrics(root: Path, offset: int, wall_s: float, cpu_s: float):
    trace = [_parse_trace_line(line) for line in _trace_after(root, offset)]
    serial = [
        row for row in trace
        if row.get("stage") == "generate.serialization"
        and row.get("status") == "summary"
    ]
    assets = [
        row for row in trace
        if row.get("stage") == "generate.assets"
        and row.get("status") == "done"
    ]
    if len(serial) != 4 or len(assets) != 4:
        raise AssertionError("Expected four report-generation metric groups")
    return {
        "wall_s": round(wall_s, 6),
        "cpu_s": round(cpu_s, 6),
        "generate_elapsed_s": round(sum(_num(row, "elapsed_s") for row in assets), 6),
        "json_elapsed_s": round(sum(
            _num(row, "shard_json_s") + _num(row, "catalog_json_s")
            for row in serial), 6),
        "escape_elapsed_s": round(sum(
            _num(row, "shard_escape_s") + _num(row, "catalog_escape_s")
            for row in serial), 6),
        "io_elapsed_s": round(sum(
            _num(row, "shard_io_s") + _num(row, "catalog_io_s")
            for row in serial), 6),
        "report_output_bytes": int(sum(
            int(row.get("report_output_bytes", "0")) for row in serial)),
        "events": int(sum(int(row.get("events", "0")) for row in serial)),
        "reports": len(serial),
    }


def _median_metrics(trials):
    rows = [trial["metrics"] for trial in trials]
    return {
        key: median(row[key] for row in rows)
        for key in _METRIC_KEYS
    }


def _safe_parity_detail(key, reference, current):
    """Return only structural mismatch detail; never payload text or digests."""
    if key != "manifest":
        return key
    ref_ids = sorted(reference)
    cur_ids = sorted(current)
    if ref_ids != cur_ids:
        return "manifest:report_ids"
    for rid in ref_ids:
        ref = reference[rid]
        cur = current[rid]
        if ref.get("events") != cur.get("events"):
            return f"manifest:{rid}:events"
        if ref.get("kind") != cur.get("kind"):
            return f"manifest:{rid}:kind"
        ref_hashes = ref.get("producer_sha256") or {}
        cur_hashes = cur.get("producer_sha256") or {}
        names = sorted(set(ref_hashes) | set(cur_hashes))
        for name in names:
            if ref_hashes.get(name) != cur_hashes.get(name):
                return f"manifest:{rid}:{name}"
    return "manifest:unknown"


def _setup_seed(root: Path, cfg):
    state = State()
    with patch.object(akuz_app, "source_config", return_value=cfg):
        perform_list(root, state, source="linux")
    targets = {}
    for row in state.listing:
        for day in DAYS:
            if row["name"] == day + "_server.log":
                if day in targets:
                    raise AssertionError("Duplicate trusted day")
                targets[day] = row
    if tuple(sorted(targets)) != DAYS:
        raise AssertionError("Trusted 23/24/25 Sep sources missing")
    selected = [
        {
            "id": targets[day]["id"],
            "date": date(
                int(day[:4]), int(day[4:6]), int(day[6:])
            ).isoformat(),
        }
        for day in DAYS
    ]
    env = {
        "AKUZ_PHASE11_PROCESS_PREFETCH": "0",
        "AKUZ_PHASE12_DELTA_RESUME": "0",
        "AKUZ_PHASE13_CATALOG_ERROR_INDEX": "1",
    }
    import os
    with patch.dict(os.environ, env, clear=False), \
         patch.object(akuz_app, "source_config", return_value=cfg):
        perform_build_current(root, state, selected)
    if state.result["analytics_warning"]:
        raise AssertionError("Analytics warning during P14-01 seed")
    if state.result["active_snapshots"]:
        raise AssertionError("Active snapshot during P14-01 seed")
    store = load_store(root)
    if len(store["downloads"]) != 3 or len(store["reports"]) != 4:
        raise AssertionError("Expected three snapshots and four seed reports")
    downloads = json.loads(json.dumps(store["downloads"], sort_keys=True))
    source_bytes = sum(row["size"] for row in store["downloads"].values())
    return state, selected, downloads, {
        "source_bytes": source_bytes,
        "reports": 4,
    }


def _run_trial(root: Path, cfg, state: State, selected, mode: str):
    _reset_reports_keep_downloads(root)
    offset = _trace_count(root)
    wall0, cpu0 = perf_counter(), process_time()
    encoder_patch = (
        patch.object(akuz_html_explorer, "_json_compact", _orjson_compact)
        if mode == "C" else nullcontext()
    )
    report_ids = [
        f"v4_20990101_00000{index}_{index:08x}"
        for index in range(1, 5)
    ]
    with encoder_patch, \
         patch.object(akuz_app, "_fresh_report_id", side_effect=report_ids), \
         patch.object(akuz_app, "source_config", return_value=cfg):
        perform_build(
            root, state, selected,
            refresh_remote=False,
            use_derived_spool=True,
        )
    wall_s = perf_counter() - wall0
    cpu_s = process_time() - cpu0
    if state.result["analytics_warning"]:
        raise AssertionError("Analytics warning during P14-01 trial")
    if state.result["active_snapshots"]:
        raise AssertionError("Unexpected active snapshot during cached trial")
    return {
        "metrics": _trial_metrics(root, offset, wall_s, cpu_s),
        "manifest": _report_manifest(root),
        "semantic_sql": semantic_sql(root),
        "exports": _export_hashes(root),
        "semantic_exports": semantic_exports(root),
    }


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase14-orjson-",
            dir=app_root / "diagnostics") as temp:
        root = Path(temp)
        cfg = replace(
            load_config(config_path, app_root),
            local_dest=root / "downloads",
            compression=False,
        )
        state, selected, downloads_before, setup = _setup_seed(root, cfg)

        reference = None
        trials = []
        for ordinal, mode in enumerate(ORDER, start=1):
            trial = _run_trial(root, cfg, state, selected, mode)
            store = load_store(root)
            if store["downloads"] != downloads_before:
                raise AssertionError("P14-01 trial mutated cached snapshots")
            public_trial = {
                "ordinal": ordinal,
                "mode": mode,
                "metrics": trial["metrics"],
            }
            print("P14_ORJSON_TRIAL", json.dumps(public_trial, sort_keys=True))
            if reference is None:
                reference = trial
            else:
                for key in (
                        "manifest", "semantic_sql",
                        "exports", "semantic_exports"):
                    if trial[key] != reference[key]:
                        detail = _safe_parity_detail(
                            key, reference[key], trial[key])
                        print("P14_ORJSON_PARITY_MISMATCH", detail)
                        raise AssertionError(
                            "P14-01 candidate differs in " + key)
            trials.append(public_trial)

        baseline_trials = [row for row in trials if row["mode"] == "B"]
        candidate_trials = [row for row in trials if row["mode"] == "C"]
        baseline = _median_metrics(baseline_trials)
        candidate = _median_metrics(candidate_trials)

        import orjson
        return {
            "status": "PHASE14_ORJSON_REPLICATED_AB",
            "setup": setup,
            "order": list(ORDER),
            "trials": trials,
            "baseline_median": baseline,
            "candidate_median": candidate,
            "wall_reduction_pct": round(
                (baseline["wall_s"] - candidate["wall_s"])
                / baseline["wall_s"] * 100.0, 3),
            "cpu_reduction_pct": round(
                (baseline["cpu_s"] - candidate["cpu_s"])
                / baseline["cpu_s"] * 100.0, 3),
            "generate_reduction_pct": round(
                (baseline["generate_elapsed_s"] - candidate["generate_elapsed_s"])
                / baseline["generate_elapsed_s"] * 100.0, 3),
            "json_reduction_pct": round(
                (baseline["json_elapsed_s"] - candidate["json_elapsed_s"])
                / baseline["json_elapsed_s"] * 100.0, 3),
            "exact_equivalence": True,
            "analytics_equivalence": True,
            "downloads_unchanged": True,
            "orjson_version": orjson.__version__,
            "raw_payload_retained": False,
            "release_changed": False,
        }


def _public(result):
    return {
        "source_bytes": result["setup"]["source_bytes"],
        "reports": result["setup"]["reports"],
        "order": result["order"],
        "orjson_version": result["orjson_version"],
        "trials": result["trials"],
        "baseline_median": result["baseline_median"],
        "candidate_median": result["candidate_median"],
        "wall_reduction_pct": result["wall_reduction_pct"],
        "cpu_reduction_pct": result["cpu_reduction_pct"],
        "generate_reduction_pct": result["generate_reduction_pct"],
        "json_reduction_pct": result["json_reduction_pct"],
        "exact_equivalence": result["exact_equivalence"],
        "analytics_equivalence": result["analytics_equivalence"],
        "downloads_unchanged": result["downloads_unchanged"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.config, args.app_root)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        if args.result.exists():
            raise FileExistsError(
                "Refusing to overwrite Phase 14 orjson evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("PHASE14_ORJSON_REPLICATED_AB=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
        print("ORDER=B_C_C_B_B_C")
        print("EXACT_EQUIVALENCE=PASS")
        print("ANALYTICS_EQUIVALENCE=PASS")
        print("DOWNLOADS_UNCHANGED=PASS")
        print("ORJSON_EXPERIMENT_ONLY=YES")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE14_ORJSON_REPLICATED_AB=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
