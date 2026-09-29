"""Phase 14 P14-00 real report-serialization baseline.

Build trusted 23/24/25 Sep reports once in an owned TemporaryDirectory and
aggregate only numeric performance counters. This is instrumentation, not an
optimization and not an A/B.

No host, remote path, digest or raw payload is printed or uploaded.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import load_config
from scripts.bench_phase13_catalog_error_index import _setup
from scripts.phase9_memory import sample


def _parse(line: str):
    out = {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            out[key] = value
    return out


def _num(row, key, integer=False):
    value = row.get(key)
    if value is None:
        return 0
    return int(value) if integer else float(value)


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase14-serialization-",
            dir=app_root / "diagnostics") as temp:
        root = Path(temp)
        cfg = replace(
            load_config(config_path, app_root),
            local_dest=root / "downloads",
            compression=False,
        )
        setup = _setup(root, cfg)

        trace_path = root / "diagnostics" / "performance.txt"
        lines = trace_path.read_text("utf-8").splitlines()
        serial = [
            _parse(line.split(" stage=", 1)[1])
            for line in lines
            if " stage=generate.serialization status=summary" in line
        ]
        parse_done = [
            _parse(line.split(" stage=", 1)[1])
            for line in lines
            if " stage=generate.parse status=done" in line
        ]
        asset_done = [
            _parse(line.split(" stage=", 1)[1])
            for line in lines
            if " stage=generate.assets status=done" in line
        ]
        if len(serial) != 4 or len(parse_done) != 4 or len(asset_done) != 4:
            raise AssertionError("Expected four report-generation summaries")

        fields_float = (
            "shard_json_s", "shard_escape_s", "shard_io_s",
            "shard_total_s", "catalog_json_s", "catalog_escape_s",
            "catalog_io_s",
        )
        fields_int = (
            "events", "shards", "shard_bytes", "catalog_bytes",
            "report_files", "report_output_bytes",
        )
        totals = {
            key: round(sum(_num(row, key) for row in serial), 6)
            for key in fields_float
        }
        totals.update({
            key: sum(_num(row, key, True) for row in serial)
            for key in fields_int
        })
        totals["parse_elapsed_s"] = round(sum(
            _num(row, "elapsed_s") for row in parse_done), 6)
        totals["generate_elapsed_s"] = round(sum(
            _num(row, "elapsed_s") for row in asset_done), 6)

        if totals["shard_bytes"] <= 0 or totals["catalog_bytes"] <= 0:
            raise AssertionError("Serialization byte counters are empty")
        if totals["report_output_bytes"] < (
                totals["shard_bytes"] + totals["catalog_bytes"]):
            raise AssertionError("Report byte counters are inconsistent")
        parts = (
            totals["shard_json_s"] + totals["shard_escape_s"]
            + totals["shard_io_s"])
        if parts > totals["shard_total_s"] + 0.02:
            raise AssertionError("Shard subphases exceed total")

        memory = sample(os.getpid())
        if memory is None:
            raise AssertionError("Windows memory sample unavailable")
        public_memory = {
            "working_set_bytes": int(memory["working_set_bytes"]),
            "private_bytes": int(memory["private_bytes"]),
            "peak_working_set_bytes": int(memory["peak_working_set_bytes"]),
            "peak_pagefile_bytes": int(memory["peak_pagefile_bytes"]),
        }
        return dict(
            status="PHASE14_SERIALIZATION_BASELINE",
            setup=setup,
            totals=totals,
            memory=public_memory,
            compression=False,
            phase11_process_prefetch=False,
            phase12_delta=False,
            raw_payload_retained=False,
            app_cache_changed=False,
            release_changed=False,
        )


def _public(result):
    return {
        "source_bytes": result["setup"]["source_bytes"],
        "reports": result["setup"]["reports"],
        "totals": result["totals"],
        "memory": result["memory"],
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
                "Refusing to overwrite Phase 14 baseline evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE14_SERIALIZATION_BASELINE=PASS")
        print("PUBLIC_SUMMARY", json.dumps(_public(result), sort_keys=True))
        print("INSTRUMENTATION_ONLY=YES")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("APP_CACHE_CHANGED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE14_SERIALIZATION_BASELINE=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
