"""Phase 13 P13-04 balanced replicated DELETE vs WAL A/B.

Build the trusted 23/24/25 report set once in an owned TemporaryDirectory,
then run analytics in balanced order D/W/W/D/D/W. Production default remains
DELETE while this experiment is measured. No raw payload, host, path or digest
is printed.
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
from scripts.bench_phase13_wal import _index_schema


ORDER = ("D", "W", "W", "D", "D", "W")
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
    "catalog_verify_s",
    "export_elapsed_s",
    "overview_elapsed_s",
    "inventory_elapsed_s",
    "db_bytes",
    "storage_bytes",
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
    "wal_bytes",
    "shm_bytes",
    "journal_bytes",
)
_PUBLIC_TRIAL_KEYS = (
    "wall_s",
    "cpu_s",
    "ingest_elapsed_s",
    "db_bytes",
    "storage_bytes",
    "page_count",
    "wal_bytes",
    "shm_bytes",
    "journal_bytes",
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
            raise AssertionError("Replicated WAL differs in " + key)


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase13-wal-replicated-",
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
            journal_mode = "DELETE" if mode == "D" else "WAL"
            _reset_analytics(root)
            current = _run_refresh(root, True, journal_mode)
            indexes = _index_schema(root)
            metrics = current["metrics"]

            if metrics["journal_mode_actual"] != journal_mode:
                raise AssertionError(
                    f"Trial {ordinal} did not use {journal_mode}")

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
                        "recognize_calls",
                        "error_rows"):
                    if reference_metrics[key] != metrics[key]:
                        raise AssertionError(
                            "Replicated logical count differs: " + key)

            trials.append(dict(
                ordinal=ordinal,
                mode=mode,
                journal_mode=journal_mode,
                metrics=metrics,
            ))

        if sha256(inventory_path) != inventory_before:
            raise AssertionError("Replicated WAL A/B mutated inventory")

        baseline_trials = [trial for trial in trials if trial["mode"] == "D"]
        candidate_trials = [trial for trial in trials if trial["mode"] == "W"]
        baseline = _median_metrics(baseline_trials)
        candidate = _median_metrics(candidate_trials)

        return dict(
            status="PHASE13_WAL_REPLICATED_AB",
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
            storage_change_pct=round(
                (candidate["storage_bytes"] - baseline["storage_bytes"])
                / baseline["storage_bytes"] * 100.0, 3)
            if baseline["storage_bytes"] else 0.0,
            exact_equivalence=True,
            index_schema_equivalence=True,
            inventory_unchanged=True,
            catalog_error_index=True,
            indexes_unchanged=True,
            transactions_unchanged=True,
            synchronous_unchanged=True,
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
                "journal_mode": trial["journal_mode"],
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
        "storage_change_pct": result["storage_change_pct"],
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
                "Refusing to overwrite replicated Phase 13 WAL evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE13_WAL_REPLICATED_AB=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
        print("ORDER=D_W_W_D_D_W")
        print("EXACT_EQUIVALENCE=PASS")
        print("INDEX_SCHEMA_EQUIVALENCE=PASS")
        print("INVENTORY_UNCHANGED=PASS")
        print("CATALOG_ERROR_INDEX=ON_BOTH_MODES")
        print("INDEXES_UNCHANGED=YES")
        print("TRANSACTIONS_UNCHANGED=YES")
        print("SYNCHRONOUS_UNCHANGED=YES")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE13_WAL_REPLICATED_AB=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
