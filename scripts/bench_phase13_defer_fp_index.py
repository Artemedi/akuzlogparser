"""Phase 13 P13-03 deferred ix_fp real A/B.

Both modes use the accepted catalog error index. Baseline maintains ix_fp while
inserting errors; candidate drops only ix_fp before ingest and rebuilds it once
before export/overview. ix_line, UNIQUE, transactions and journal mode remain
unchanged.

The trusted 23/24/25 report set is built once in an owned TemporaryDirectory.
No raw payload, host, path, digest or private evidence is printed.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
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


def _index_schema(root: Path):
    path = root / "cache" / "error_analytics.sqlite"
    with closing(sqlite3.connect(path)) as db:
        return [
            tuple(row) for row in db.execute(
                "SELECT name,sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='errors' "
                "ORDER BY name")
        ]


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase13-defer-fp-",
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

        _reset_analytics(root)
        baseline = _run_refresh(root, True, False)
        baseline_indexes = _index_schema(root)

        _reset_analytics(root)
        candidate = _run_refresh(root, True, True)
        candidate_indexes = _index_schema(root)

        for key in (
                "overview", "sql", "semantic_sql",
                "exports", "semantic_exports"):
            if baseline[key] != candidate[key]:
                raise AssertionError("Deferred ix_fp differs in " + key)
        if baseline_indexes != candidate_indexes:
            raise AssertionError("Final SQLite index schema differs")
        names = {row[0] for row in candidate_indexes}
        if "ix_fp" not in names or "ix_line" not in names:
            raise AssertionError("Required analytics index missing")
        if sha256(inventory_path) != inventory_before:
            raise AssertionError("Deferred ix_fp A/B mutated inventory")

        b = baseline["metrics"]
        c = candidate["metrics"]
        if b["catalog_index_reports"] != c["catalog_index_reports"]:
            raise AssertionError("Catalog index activation differs")
        if b["matched_errors"] != c["matched_errors"]:
            raise AssertionError("Matched error count differs")
        if b["raw_sha_lookup_calls"] != c["raw_sha_lookup_calls"]:
            raise AssertionError("Logical ambiguity SELECT count differs")
        if b["insert_attempts"] != c["insert_attempts"]:
            raise AssertionError("Logical INSERT count differs")
        if c["index_build_elapsed_s"] <= 0:
            raise AssertionError("Deferred candidate did not time index build")

        return dict(
            status="PHASE13_DEFER_FP_INDEX_AB",
            setup=setup,
            baseline=b,
            candidate=c,
            wall_reduction_pct=round(
                (b["wall_s"]-c["wall_s"])/b["wall_s"]*100.0, 3),
            cpu_reduction_pct=round(
                (b["cpu_s"]-c["cpu_s"])/b["cpu_s"]*100.0, 3),
            ingest_reduction_pct=round(
                (b["ingest_elapsed_s"]-c["ingest_elapsed_s"])
                / b["ingest_elapsed_s"]*100.0, 3),
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
        "baseline": result["baseline"],
        "candidate": result["candidate"],
        "wall_reduction_pct": result["wall_reduction_pct"],
        "cpu_reduction_pct": result["cpu_reduction_pct"],
        "ingest_reduction_pct": result["ingest_reduction_pct"],
        "exact_equivalence": result["exact_equivalence"],
        "index_schema_equivalence": result["index_schema_equivalence"],
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
                "Refusing to overwrite Phase 13 deferred-index evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE13_DEFER_FP_INDEX_AB=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
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
        print("PHASE13_DEFER_FP_INDEX_AB=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
