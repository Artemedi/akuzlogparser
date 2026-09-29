"""Phase 13 P13-05 per-report commits vs one ingest transaction.

Both modes use the accepted catalog error index, DELETE journal mode and the
same indexes. Only transaction ownership differs: baseline commits each new
report; candidate commits the whole pending report set once.

Trusted 23/24/25 reports are built once in an owned TemporaryDirectory.
No raw payload, host, path, digest or private evidence is printed.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
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


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase13-single-tx-",
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

        _reset_analytics(root)
        candidate = _run_refresh(root, True, True)

        for key in (
                "overview", "sql", "semantic_sql",
                "exports", "semantic_exports"):
            if baseline[key] != candidate[key]:
                raise AssertionError("Single transaction differs in " + key)
        if sha256(inventory_path) != inventory_before:
            raise AssertionError("Single-transaction A/B mutated inventory")

        b = baseline["metrics"]
        c = candidate["metrics"]
        if b["catalog_index_reports"] != c["catalog_index_reports"]:
            raise AssertionError("Catalog-index activation differs")
        if b["matched_errors"] != c["matched_errors"]:
            raise AssertionError("Matched error count differs")
        if b["raw_sha_lookup_calls"] != c["raw_sha_lookup_calls"]:
            raise AssertionError("Logical ambiguity SELECT count differs")
        if b["insert_attempts"] != c["insert_attempts"]:
            raise AssertionError("Logical INSERT count differs")
        if b["pending_reports"] != c["pending_reports"]:
            raise AssertionError("Pending report count differs")
        if b["transaction_commits"] <= c["transaction_commits"]:
            raise AssertionError("Candidate did not reduce commit count")
        if c["transaction_commits"] != 1:
            raise AssertionError("Candidate did not use exactly one commit")
        if b["db_bytes"] != c["db_bytes"] or b["page_count"] != c["page_count"]:
            raise AssertionError("Final SQLite storage differs")

        return dict(
            status="PHASE13_SINGLE_TRANSACTION_AB",
            setup=setup,
            baseline=b,
            candidate=c,
            wall_reduction_pct=round(
                (b["wall_s"]-c["wall_s"])/b["wall_s"]*100.0, 3),
            cpu_reduction_pct=round(
                (b["cpu_s"]-c["cpu_s"])/b["cpu_s"]*100.0, 3),
            commit_reduction_pct=round(
                (b["transaction_commits"]-c["transaction_commits"])
                / b["transaction_commits"]*100.0, 3),
            exact_equivalence=True,
            inventory_unchanged=True,
            catalog_error_index=True,
            journal_mode="DELETE",
            indexes_unchanged=True,
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
        "commit_reduction_pct": result["commit_reduction_pct"],
        "exact_equivalence": result["exact_equivalence"],
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
                "Refusing to overwrite Phase 13 transaction evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE13_SINGLE_TRANSACTION_AB=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
        print("EXACT_EQUIVALENCE=PASS")
        print("INVENTORY_UNCHANGED=PASS")
        print("CATALOG_ERROR_INDEX=ON_BOTH_MODES")
        print("JOURNAL_MODE=DELETE")
        print("INDEXES_UNCHANGED=YES")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE13_SINGLE_TRANSACTION_AB=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
