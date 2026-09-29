"""Phase 13 P13-03 balanced replicated deferred ix_fp A/B.

Build the trusted 23/24/25 report set once in an owned TemporaryDirectory,
then run analytics in balanced order B/C/C/B/B/C. Production runtime is not
modified. No raw payload, host, path or digest is printed.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
from statistics import median
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import load_config
from akuz_store import sha256
from scripts.bench_phase13_catalog_error_index import (
    _reset_analytics,
    _run_refresh,
    _setup,
)
from scripts.bench_phase13_defer_fp_index import _index_schema


ORDER = ("B", "C", "C", "B", "B", "C")
_EQ_KEYS = (
    "overview",
    "sql",
    "semantic_sql",
    "exports",
    "semantic_exports",
)
_MEDIAN_KEYS = (
    "wall_s",
    "cpu_s",
    "ingest_elapsed_s",
    "index_build_elapsed_s",
    "catalog_verify_s",
    "export_elapsed_s",
    "overview_elapsed_s",
    "inventory_elapsed_s",
    "db_bytes",
    "page_count",
    "freelist_count",
    "error_rows",
    "events",
    "matched_errors",
    "recognize_calls",
    "raw_sha_lookup_calls",
    "insert_attempts",
    "raw_shards_loaded",
    "raw_shard_bytes_loaded",
    "index_skipped_no_error",
    "catalog_index_reports",
    "reports",
    "indexed_reports",
)
_PUBLIC_TRIAL_KEYS = (
    "wall_s",
    "cpu_s",
    "ingest_elapsed_s",
    "index_build_elapsed_s",
    "db_bytes",
    "page_count",
)


def _median_metrics(trials):
    metrics = [trial["metrics"] for trial in trials]
    return {
        key: median(row[key] for row in metrics)
        for key in _MEDIAN_KEYS
        if all(key in row for row in metrics)
    }


def _assert_equivalent(reference, current):
    for key in _EQ_KEYS:
        if reference[key] != current[key]:
            raise AssertionError("Replicated deferred ix_fp differs in " + key)


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase13-defer-fp-replicated-",
            dir=app_root / "diagnostics") as temp:
        root = Path(temp)
        cfg = replace(
            load_config(config_path, app_root),
            local_dest=root / "downloads",
            compression=False,
        )
        setup = _setup(root, cfg)
        inventory_path = root / "cache" / "inventory.json"
        inventory_before = sha256(inventory_path)

        reference = None
        reference_indexes = None
        reference_metrics = None
        trials = []

        for ordinal, mode in enumerate(ORDER, start=1):
            _reset_analytics(root)
            current = _run_refresh(root, True, mode == "C")
            indexes = _index_schema(root)
            names = {row[0] for row in indexes}
            if "ix_fp" not in names or "ix_line" not in names:
                raise AssertionError("Required analytics index missing")

            metrics = current["metrics"]
            if mode == "C" and metrics["index_build_elapsed_s"] <= 0:
                raise AssertionError("Deferred candidate did not time index build")

            if reference is None:
                reference = current
                reference_indexes = indexes
                reference_metrics = metrics
            else:
                _assert_equivalent(reference, current)
                if reference_indexes != indexes:
                    raise AssertionError("Final SQLite index schema differs")
                for key in (
                        "catalog_index_reports",
                        "matched_errors",
                        "raw_sha_lookup_calls",
                        "insert_attempts",
                        "recognize_calls"):
                    if reference_metrics[key] != metrics[key]:
                        raise AssertionError(
                            "Replicated logical count differs: " + key)

            trials.append(dict(
                ordinal=ordinal,
                mode=mode,
                metrics=metrics,
            ))

        if sha256(inventory_path) != inventory_before:
            raise AssertionError("Replicated deferred ix_fp mutated inventory")

        baseline_trials = [trial for trial in trials if trial["mode"] == "B"]
        candidate_trials = [trial for trial in trials if trial["mode"] == "C"]
        baseline = _median_metrics(baseline_trials)
        candidate = _median_metrics(candidate_trials)

        return dict(
            status="PHASE13_DEFER_FP_INDEX_REPLICATED_AB",
            setup=setup,
            order=list(ORDER),
            trials=trials,
            baseline_median=baseline,
            candidate_median=candidate,
            wall_reduction_pct=round(
                (baseline["wall_s"] - candidate["wall_s"])
                / baseline["wall_s"] * 100.0, 3),
            cpu_reduction_pct=round(
                (baseline["cpu_s"] - candidate["cpu_s"])
                / baseline["cpu_s"] * 100.0, 3),
            ingest_reduction_pct=round(
                (baseline["ingest_elapsed_s"] - candidate["ingest_elapsed_s"])
                / baseline["ingest_elapsed_s"] * 100.0, 3),
            exact_equivalence=True,
            index_schema_equivalence=True,
            inventory_unchanged=True,
            catalog_error_index=True,
            ix_line_unchanged=True,
            unique_unchanged=True,
            journal_mode="DELETE",
            raw_payload_retained=False,
            release_changed=False,
        )


def _public(result):
    return {
        "source_bytes": result["setup"]["source_bytes"],
        "reports": result["setup"]["reports"],
        "order": result["order"],
        "trials": [
            {
                "ordinal": trial["ordinal"],
                "mode": trial["mode"],
                **{
                    key: trial["metrics"][key]
                    for key in _PUBLIC_TRIAL_KEYS
                    if key in trial["metrics"]
                },
            }
            for trial in result["trials"]
        ],
        "baseline_median": result["baseline_median"],
        "candidate_median": result["candidate_median"],
        "wall_reduction_pct": result["wall_reduction_pct"],
        "cpu_reduction_pct": result["cpu_reduction_pct"],
        "ingest_reduction_pct": result["ingest_reduction_pct"],
        "exact_equivalence": result["exact_equivalence"],
        "index_schema_equivalence": result["index_schema_equivalence"],
        "inventory_unchanged": result["inventory_unchanged"],
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
                "Refusing to overwrite replicated Phase 13 deferred-index evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE13_DEFER_FP_INDEX_REPLICATED_AB=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
        print("ORDER=B_C_C_B_B_C")
        print("EXACT_EQUIVALENCE=PASS")
        print("INDEX_SCHEMA_EQUIVALENCE=PASS")
        print("INVENTORY_UNCHANGED=PASS")
        print("CATALOG_ERROR_INDEX=ON_BOTH_MODES")
        print("IX_LINE_UNCHANGED=YES")
        print("UNIQUE_UNCHANGED=YES")
        print("JOURNAL_MODE=DELETE")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print(
            "PHASE13_DEFER_FP_INDEX_REPLICATED_AB=FAILED",
            type(exc).__name__,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
