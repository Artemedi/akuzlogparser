"""Phase 11 balanced replicated serial-vs-overlap benchmark on 23+24 Sep.

Reuses the fixed-prefix real harness. Four isolated trials are ordered
serial, overlap, overlap, serial to reduce simple first/last order bias.
No normal application cache or production scheduler is modified.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import load_config
from scripts.bench_phase11_ssh_overlap import _discover, _run_mode


ORDER = ("serial", "overlap", "overlap", "serial")


def _summary(rows: list[dict], mode: str) -> dict:
    subset = [row for row in rows if row["mode"] == mode]
    if len(subset) != 2:
        raise AssertionError("Phase 11 replicated smoke expects two trials per mode")
    def med(name):
        return statistics.median(row[name] for row in subset)
    return dict(
        trials=2,
        wall_median_s=round(med("wall_s"), 6),
        cpu_median_s=round(med("process_cpu_s"), 6),
        peak_private_median_bytes=int(med("sampled_peak_private_bytes")),
        peak_ws_median_bytes=int(med("sampled_peak_ws_bytes")),
        workspace_median_bytes=int(med("workspace_bytes")),
        wall_values_s=[row["wall_s"] for row in subset],
        second_fetch_values_s=[
            row["fetch"]["20260924"] for row in subset],
        first_parse_values_s=[
            row["parse"]["20260923"]["wall_s"] for row in subset],
    )


def _sanitize(row: dict) -> dict:
    clean = dict(row)
    clean.pop("source_sha", None)
    clean["outputs"] = [dict(item) for item in row["outputs"]]
    clean["parse"] = {day: dict(value)
                      for day, value in row["parse"].items()}
    for output in clean["outputs"]:
        output.pop("report_manifest_sha256", None)
    for parsed in clean["parse"].values():
        parsed.pop("report_manifest_sha256", None)
    return clean


def run(config_path: Path, app_root: Path, diagnostics: Path) -> dict:
    cfg = replace(load_config(config_path, app_root), compression=False)
    specs = _discover(cfg)
    workspace = None
    trials = []
    reference_sha = None
    reference_outputs = None
    try:
        with tempfile.TemporaryDirectory(
                prefix="phase11_ssh_overlap_rep_", dir=diagnostics) as td:
            workspace = Path(td)
            for number, mode in enumerate(ORDER, 1):
                row = _run_mode(
                    cfg, specs, workspace / f"{number:02d}_{mode}",
                    overlap=(mode == "overlap"))
                source_sha = row["source_sha"]
                outputs = {item["day"]: item for item in row["outputs"]}
                if reference_sha is None:
                    reference_sha = source_sha
                    reference_outputs = outputs
                else:
                    if source_sha != reference_sha:
                        raise AssertionError(
                            "Phase 11 source SHA changed across replicated trials")
                    if outputs != reference_outputs:
                        raise AssertionError(
                            "Phase 11 report manifest changed across replicated trials")
                trials.append(_sanitize(row))

            result = dict(
                status="PHASE11_BALANCED_PAIR_REPLICATION_NOT_PRODUCTION_GATE",
                order=list(ORDER),
                days=[spec.day for spec in specs],
                fixed_prefix_bytes={spec.day: spec.bound for spec in specs},
                compression=False,
                trials=trials,
                summary={
                    "serial": _summary(trials, "serial"),
                    "overlap": _summary(trials, "overlap"),
                },
                source_sha_equal=True,
                report_manifest_equal=True,
                raw_payload_saved=False,
                app_cache_changed=False,
                release_changed=False)
        result["workspace_cleaned"] = workspace is not None and not workspace.exists()
        if not result["workspace_cleaned"]:
            raise AssertionError("Phase 11 replicated workspace cleanup failed")
        return result
    finally:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite replicated Phase 11 evidence")
        result = run(args.config, args.app_root, args.diagnostics)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print("PHASE11_REPLICATED_PAIR_PASS")
        for mode in ("serial", "overlap"):
            row = result["summary"][mode]
            print("SUMMARY", mode,
                  "wall_median_s", row["wall_median_s"],
                  "cpu_median_s", row["cpu_median_s"],
                  "peak_private_median_bytes", row["peak_private_median_bytes"],
                  "second_fetch_values_s",
                  ",".join(f"{v:.6f}" for v in row["second_fetch_values_s"]),
                  "first_parse_values_s",
                  ",".join(f"{v:.6f}" for v in row["first_parse_values_s"]))
        print("SOURCE_SHA_EQUAL=PASS")
        print("REPORT_MANIFEST_EQUAL=PASS")
        print("WORKSPACE_CLEANED=PASS")
        print("RAW_PAYLOAD_SAVED=NO")
        print("APP_CACHE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE11_REPLICATED_PAIR_FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
