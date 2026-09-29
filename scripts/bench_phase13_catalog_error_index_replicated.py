"""Phase 13 balanced replicated catalog error-index A/B.

One real 23/24/25 setup creates immutable reports once. Analytics is rebuilt
six times in balanced order B,C,C,B,B,C. No filesystem cache is dropped:
the alternating order intentionally samples the natural Windows cache state.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import tempfile

from akuz_fetch import load_config
from akuz_store import sha256
from scripts.bench_phase13_catalog_error_index import (
    _reset_analytics,
    _run_refresh,
    _setup,
)

ORDER = (False, True, True, False, False, True)


def _median(rows, field):
    return statistics.median(float(row[field]) for row in rows)


def _summary(rows, use_index):
    subset = [row for row in rows if row["use_index"] is use_index]
    if len(subset) != 3:
        raise AssertionError("Expected exactly three trials per mode")
    fields = (
        "wall_s", "cpu_s", "ingest_elapsed_s", "catalog_verify_s",
        "recognize_calls", "raw_shards_loaded", "raw_shard_bytes_loaded",
        "matched_errors", "raw_sha_lookup_calls", "insert_attempts",
        "db_bytes", "page_count",
    )
    result = {"trials": 3}
    for field in fields:
        value = _median(subset, field)
        if field in (
                "recognize_calls", "raw_shards_loaded",
                "raw_shard_bytes_loaded", "matched_errors",
                "raw_sha_lookup_calls", "insert_attempts",
                "db_bytes", "page_count"):
            value = int(value)
        else:
            value = round(value, 6)
        result[field + "_median"] = value
    result["wall_values_s"] = [row["wall_s"] for row in subset]
    result["cpu_values_s"] = [row["cpu_s"] for row in subset]
    return result


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase13-repl-", dir=app_root / "diagnostics") as temp:
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
        rows = []
        for trial_no, use_index in enumerate(ORDER, 1):
            _reset_analytics(root)
            result = _run_refresh(root, use_index)
            if reference is None:
                reference = result
            else:
                for key in (
                        "overview", "sql", "semantic_sql",
                        "exports", "semantic_exports"):
                    if result[key] != reference[key]:
                        raise AssertionError(
                            f"Trial {trial_no} differs in {key}")
            if sha256(inventory_path) != inventory_before:
                raise AssertionError("Analytics trial mutated inventory")
            metrics = dict(result["metrics"])
            metrics["trial"] = trial_no
            metrics["mode"] = "candidate" if use_index else "baseline"
            metrics["use_index"] = use_index
            rows.append(metrics)

        baseline = _summary(rows, False)
        candidate = _summary(rows, True)
        if any(row["catalog_index_reports"] != (4 if row["use_index"] else 0)
               for row in rows):
            raise AssertionError("Catalog index activation mismatch")
        if len({row["matched_errors"] for row in rows}) != 1:
            raise AssertionError("Matched error count differs across trials")
        if len({row["raw_sha_lookup_calls"] for row in rows}) != 1:
            raise AssertionError("Logical SELECT count differs across trials")
        if len({row["insert_attempts"] for row in rows}) != 1:
            raise AssertionError("Logical INSERT count differs across trials")

        b_wall = baseline["wall_s_median"]
        c_wall = candidate["wall_s_median"]
        b_cpu = baseline["cpu_s_median"]
        c_cpu = candidate["cpu_s_median"]
        b_raw = baseline["raw_shard_bytes_loaded_median"]
        c_raw = candidate["raw_shard_bytes_loaded_median"]
        b_rec = baseline["recognize_calls_median"]
        c_rec = candidate["recognize_calls_median"]

        return dict(
            status="PHASE13_REPLICATED_CATALOG_INDEX_AB",
            setup=setup,
            order=["candidate" if x else "baseline" for x in ORDER],
            trials=rows,
            summary={"baseline": baseline, "candidate": candidate},
            wall_reduction_pct=round(
                (b_wall-c_wall)/b_wall*100.0, 3),
            cpu_reduction_pct=round(
                (b_cpu-c_cpu)/b_cpu*100.0, 3),
            raw_read_reduction_pct=round(
                (b_raw-c_raw)/b_raw*100.0 if b_raw else 0.0, 3),
            recognize_reduction_pct=round(
                (b_rec-c_rec)/b_rec*100.0 if b_rec else 0.0, 3),
            exact_equivalence_6_of_6=True,
            inventory_unchanged_6_of_6=True,
            natural_cache_balanced=True,
            filesystem_cache_dropped=False,
            compression=False,
            phase11_process_prefetch=False,
            phase12_delta=False,
            raw_payload_retained=False,
            release_changed=False,
        )


def _public(result):
    return {
        "source_bytes": result["setup"]["source_bytes"],
        "reports": result["setup"]["reports"],
        "order": result["order"],
        "summary": result["summary"],
        "wall_reduction_pct": result["wall_reduction_pct"],
        "cpu_reduction_pct": result["cpu_reduction_pct"],
        "raw_read_reduction_pct": result["raw_read_reduction_pct"],
        "recognize_reduction_pct": result["recognize_reduction_pct"],
        "exact_equivalence_6_of_6": result["exact_equivalence_6_of_6"],
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
            raise FileExistsError("Refusing to overwrite Phase 13 replicated evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE13_REPLICATED_CATALOG_INDEX_AB=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
        for row in result["trials"]:
            public_row = {
                key: row[key] for key in (
                    "trial", "mode", "wall_s", "cpu_s",
                    "ingest_elapsed_s", "catalog_verify_s",
                    "recognize_calls", "raw_shards_loaded",
                    "raw_shard_bytes_loaded", "matched_errors",
                    "raw_sha_lookup_calls", "insert_attempts")
            }
            print("TRIAL", json.dumps(public_row, sort_keys=True))
        print("EXACT_EQUIVALENCE_6_OF_6=PASS")
        print("INVENTORY_UNCHANGED_6_OF_6=PASS")
        print("NATURAL_CACHE_BALANCED=YES")
        print("FILESYSTEM_CACHE_DROPPED=NO")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE13_REPLICATED_CATALOG_INDEX_AB=FAILED",
              type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
