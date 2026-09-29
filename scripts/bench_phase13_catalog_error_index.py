"""Phase 13 real analytics A/B on identical immutable reports.

Setup fetches/builds trusted 23/24/25 Sep reports once inside a disposable
workspace. The benchmark then deletes only rebuildable analytics outputs and
rebuilds them twice from the exact same report bytes:
  baseline scan -> catalog error-index candidate.

Raw logs/reports never leave the owned TemporaryDirectory. The local JSON
result contains only counters and hashes; stdout omits host/path/digest/raw.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from time import perf_counter, process_time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import akuz_app
from akuz_app import State, perform_build_current, perform_list
from akuz_analytics import refresh
from akuz_fetch import load_config
from akuz_store import load_store, sha256
from scripts.bench_phase9_baseline import canonical_hash, sql_fingerprint
from scripts.phase9_semantic import semantic_exports, semantic_sql

DAYS = ("20260923", "20260924", "20260925")


def _trace_count(root: Path) -> int:
    path = root / "diagnostics" / "performance.txt"
    return len(path.read_text("utf-8").splitlines()) if path.exists() else 0


def _trace_after(root: Path, offset: int):
    path = root / "diagnostics" / "performance.txt"
    if not path.exists():
        return []
    lines = path.read_text("utf-8").splitlines()[offset:]
    return [line.split(" stage=", 1)[1]
            for line in lines if " stage=" in line]


def _parse(line: str):
    # _trace_after strips the timestamp and 'stage=' prefix, retaining
    # the stage value as the first token rather than a key=value pair.
    out = {"stage": line.split(maxsplit=1)[0]} if line else {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            out[key] = value
    return out


def _num(value, integer=False):
    if value is None:
        return 0
    return int(value) if integer else float(value)


def _reset_analytics(root: Path):
    cache = root / "cache"
    for name in ("error_analytics.sqlite", "error_analytics.sqlite-journal",
                 "error_analytics.sqlite-wal", "error_analytics.sqlite-shm"):
        (cache / name).unlink(missing_ok=True)
    shutil.rmtree(root / "data", ignore_errors=True)


def _export_hashes(root: Path):
    folder = root / "data"
    return {p.name: sha256(p) for p in sorted(folder.glob("*.js"))}


def _db_metrics(root: Path):
    path = root / "cache" / "error_analytics.sqlite"
    with closing(sqlite3.connect(path)) as db:
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        page_count = db.execute("PRAGMA page_count").fetchone()[0]
        freelist = db.execute("PRAGMA freelist_count").fetchone()[0]
        errors = db.execute("SELECT count(*) FROM errors").fetchone()[0]
        indexed = db.execute("SELECT count(*) FROM indexed").fetchone()[0]
    return dict(
        db_bytes=path.stat().st_size,
        page_size=page_size,
        page_count=page_count,
        freelist_count=freelist,
        error_rows=errors,
        indexed_reports=indexed,
    )


def _run_refresh(root: Path, use_index: bool | None,
                 defer_fp_index: bool = False):
    offset = _trace_count(root)
    wall0, cpu0 = perf_counter(), process_time()
    overview = refresh(
        root, use_catalog_error_index=use_index,
        defer_fp_index=defer_fp_index)
    wall = perf_counter() - wall0
    cpu = process_time() - cpu0
    trace = _trace_after(root, offset)

    summaries = [
        _parse(line) for line in trace
        if line.startswith("analytics.ingest status=summary")
    ]
    done = [_parse(line) for line in trace if " status=done" in line]

    def done_elapsed(stage):
        return sum(_num(x.get("elapsed_s")) for x in done
                   if x.get("stage") == stage)

    metrics = dict(
        wall_s=round(wall, 6),
        cpu_s=round(cpu, 6),
        ingest_elapsed_s=round(sum(
            _num(x.get("elapsed_s")) for x in summaries), 6),
        export_elapsed_s=round(done_elapsed("analytics.export"), 6),
        overview_elapsed_s=round(done_elapsed("analytics.overview"), 6),
        inventory_elapsed_s=round(done_elapsed("analytics.inventory"), 6),
        index_build_elapsed_s=round(done_elapsed("analytics.index_build"), 6),
        reports=len(summaries),
        catalog_index_reports=sum(
            _num(x.get("catalog_error_index"), True) for x in summaries),
        events=sum(_num(x.get("events"), True) for x in summaries),
        index_skipped_no_error=sum(
            _num(x.get("index_skipped_no_error"), True) for x in summaries),
        recognize_calls=sum(
            _num(x.get("recognize_calls"), True) for x in summaries),
        raw_shards_loaded=sum(
            _num(x.get("raw_shards_loaded"), True) for x in summaries),
        raw_shard_bytes_loaded=sum(
            _num(x.get("raw_shard_bytes_loaded"), True) for x in summaries),
        matched_errors=sum(
            _num(x.get("matched_errors"), True) for x in summaries),
        raw_sha_lookup_calls=sum(
            _num(x.get("raw_sha_lookup_calls"), True) for x in summaries),
        insert_attempts=sum(
            _num(x.get("insert_attempts"), True) for x in summaries),
        ambiguous_update_calls=sum(
            _num(x.get("ambiguous_update_calls"), True) for x in summaries),
        catalog_verify_s=round(sum(
            _num(x.get("catalog_verify_s")) for x in summaries), 6),
    )
    metrics.update(_db_metrics(root))
    return dict(
        overview=overview,
        metrics=metrics,
        sql=sql_fingerprint(root),
        semantic_sql=semantic_sql(root),
        exports=_export_hashes(root),
        semantic_exports=semantic_exports(root),
    )


def _setup(root: Path, cfg):
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
        dict(id=targets[day]["id"],
             date=date(int(day[:4]), int(day[4:6]), int(day[6:])).isoformat())
        for day in DAYS
    ]
    env = {
        "AKUZ_PHASE11_PROCESS_PREFETCH": "0",
        "AKUZ_PHASE12_DELTA_RESUME": "0",
        "AKUZ_PHASE13_CATALOG_ERROR_INDEX": "0",
    }
    with patch.dict(os.environ, env, clear=False),          patch.object(akuz_app, "source_config", return_value=cfg):
        perform_build_current(root, state, selected)
    if state.result["analytics_warning"]:
        raise AssertionError("Analytics warning during Phase 13 setup")
    if state.result["active_snapshots"]:
        raise AssertionError("Active snapshot during Phase 13 setup")
    store = load_store(root)
    if len(store["downloads"]) != 3 or len(store["reports"]) != 4:
        raise AssertionError("Expected three snapshots and four reports")
    # New built-in reports must be eligible for the version-gated index.
    if any(row.get("error_fingerprint_version") != 1
           for row in store["reports"].values()):
        raise AssertionError("Missing Phase 13 fingerprint compatibility marker")
    return dict(
        source_bytes=sum(row["size"] for row in store["downloads"].values()),
        reports=len(store["reports"]),
    )


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase13-real-", dir=app_root / "diagnostics") as temp:
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
        baseline = _run_refresh(root, False)

        _reset_analytics(root)
        # Acceptance of the production default, not an explicit forced-on path.
        candidate = _run_refresh(root, None)

        if baseline["overview"] != candidate["overview"]:
            raise AssertionError("Overview differs")
        if baseline["sql"] != candidate["sql"]:
            raise AssertionError("Exact SQL differs")
        if baseline["semantic_sql"] != candidate["semantic_sql"]:
            raise AssertionError("Semantic SQL differs")
        if baseline["exports"] != candidate["exports"]:
            raise AssertionError("Exact analytics JS differs")
        if baseline["semantic_exports"] != candidate["semantic_exports"]:
            raise AssertionError("Semantic analytics JS differs")
        if sha256(inventory_path) != inventory_before:
            raise AssertionError("Analytics A/B mutated inventory")
        if candidate["metrics"]["catalog_index_reports"] == 0:
            raise AssertionError("Candidate did not use trusted catalog index")
        if candidate["metrics"]["matched_errors"] != baseline["metrics"]["matched_errors"]:
            raise AssertionError("Matched error count differs")
        if candidate["metrics"]["raw_sha_lookup_calls"] != baseline["metrics"]["raw_sha_lookup_calls"]:
            raise AssertionError("Logical error SQL lookup count differs")
        if candidate["metrics"]["insert_attempts"] != baseline["metrics"]["insert_attempts"]:
            raise AssertionError("Logical error insert count differs")

        b = baseline["metrics"]
        c = candidate["metrics"]
        return dict(
            status="PHASE13_REAL_DEFAULT_CATALOG_INDEX_AB",
            setup=setup,
            baseline=b,
            candidate=c,
            wall_reduction_pct=round(
                (b["wall_s"] - c["wall_s"]) / b["wall_s"] * 100.0, 3),
            raw_read_reduction_pct=round(
                ((b["raw_shard_bytes_loaded"] - c["raw_shard_bytes_loaded"])
                 / b["raw_shard_bytes_loaded"] * 100.0)
                if b["raw_shard_bytes_loaded"] else 0.0, 3),
            recognize_reduction_pct=round(
                ((b["recognize_calls"] - c["recognize_calls"])
                 / b["recognize_calls"] * 100.0)
                if b["recognize_calls"] else 0.0, 3),
            exact_sql_equivalence=True,
            exact_export_equivalence=True,
            semantic_sql_equivalence=True,
            semantic_export_equivalence=True,
            inventory_unchanged=True,
            compression=False,
            phase11_process_prefetch=False,
            phase12_delta=False,
            raw_payload_retained=False,
            release_changed=False,
            sql_fingerprint_hash=canonical_hash(baseline["sql"]),
            export_fingerprint_hash=canonical_hash(baseline["exports"]),
        )


def _public(result):
    return {
        "source_bytes": result["setup"]["source_bytes"],
        "reports": result["setup"]["reports"],
        "baseline": result["baseline"],
        "candidate": result["candidate"],
        "wall_reduction_pct": result["wall_reduction_pct"],
        "raw_read_reduction_pct": result["raw_read_reduction_pct"],
        "recognize_reduction_pct": result["recognize_reduction_pct"],
        "exact_sql_equivalence": result["exact_sql_equivalence"],
        "exact_export_equivalence": result["exact_export_equivalence"],
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
            raise FileExistsError("Refusing to overwrite Phase 13 evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE13_REAL_DEFAULT_CATALOG_INDEX_AB=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
        print("EXACT_SQL_EQUIVALENCE=PASS")
        print("EXACT_EXPORT_EQUIVALENCE=PASS")
        print("SEMANTIC_SQL_EQUIVALENCE=PASS")
        print("SEMANTIC_EXPORT_EQUIVALENCE=PASS")
        print("INVENTORY_UNCHANGED=PASS")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE13_REAL_CATALOG_INDEX_AB=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
