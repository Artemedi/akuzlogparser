"""Phase 12 replicated full-fetch vs strict-delta A/B.

Benchmark-only. One previous snapshot is prepared outside timed trials because
that snapshot already exists in the intended resume use case. Six trials run in
balanced order (F,D,D,F,F,D) against one immutable 25-Sep source version.

No raw payload, host/path/inode or digest is printed. Trial payload lives only
inside an owned TemporaryDirectory and is deleted on exit. Application cache,
reports and Release are untouched.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import load_config
from scripts.bench_phase12_delta_resume_smoke import (
    _delta_trial,
    _full_trial,
    _public_row,
    _setup_previous,
)


ORDER = ("full", "strict_delta", "strict_delta",
         "full", "full", "strict_delta")


def _median(rows, field):
    return statistics.median(float(row[field]) for row in rows)


def _summary(rows, mode):
    subset = [row for row in rows if row["mode"] == mode]
    if len(subset) != 3:
        raise AssertionError("Expected exactly three trials per mode")
    result = dict(
        trials=3,
        wall_median_s=round(_median(subset, "wall_s"), 6),
        client_cpu_median_s=round(_median(subset, "client_cpu_s"), 6),
        socket_rx_median_bytes=int(_median(subset, "socket_rx_bytes")),
        logical_transfer_median_bytes=int(
            _median(subset, "logical_transfer_bytes")),
        wall_values_s=[row["wall_s"] for row in subset],
    )
    if mode == "strict_delta":
        result.update(
            old_prefix_sha_wall_median_s=round(
                _median(subset, "old_prefix_sha_wall_s"), 6),
            new_prefix_sha_wall_median_s=round(
                _median(subset, "new_prefix_sha_wall_s"), 6),
            remote_sha_server_cpu_median_s=round(
                _median(subset, "remote_sha_server_cpu_s"), 6),
            assemble_wall_median_s=round(
                _median(subset, "assemble_wall_s"), 6),
            transfer_wall_median_s=round(
                _median(subset, "transfer_wall_s"), 6),
        )
    return result


def run(config_path: Path, app_root: Path, day: str, previous_bytes: int):
    cfg = replace(load_config(config_path, app_root), compression=False)
    with TemporaryDirectory(prefix="akuz-phase12-ab-") as temp:
        workspace = Path(temp)
        selected, previous, previous_sha, device, inode = _setup_previous(
            cfg, day, previous_bytes, workspace)
        expected_identity = (device, inode)
        fixed_bound = selected["size"]
        if fixed_bound <= previous_bytes:
            raise AssertionError("Selected source has no append delta")

        rows = []
        reference_sha = None
        for index, mode in enumerate(ORDER, 1):
            trial = workspace / f"trial_{index:02d}_{mode}"
            trial.mkdir()
            if mode == "full":
                row = _full_trial(cfg, day, expected_identity, trial)
            else:
                row = _delta_trial(
                    cfg, day, previous, previous_sha, previous_bytes,
                    expected_identity, trial)
            if row["bound_bytes"] != fixed_bound:
                raise AssertionError(
                    "Remote size changed; immutable repeated gate aborted")
            if reference_sha is None:
                reference_sha = row["snapshot_sha256"]
            elif row["snapshot_sha256"] != reference_sha:
                raise AssertionError("Snapshot SHA changed across A/B")
            rows.append(row)

        full = _summary(rows, "full")
        delta = _summary(rows, "strict_delta")
        wall_reduction_pct = (
            (full["wall_median_s"] - delta["wall_median_s"])
            / full["wall_median_s"] * 100.0)
        rx_reduction_pct = (
            (full["socket_rx_median_bytes"] - delta["socket_rx_median_bytes"])
            / full["socket_rx_median_bytes"] * 100.0)
        logical_reduction_pct = (
            (full["logical_transfer_median_bytes"]
             - delta["logical_transfer_median_bytes"])
            / full["logical_transfer_median_bytes"] * 100.0)

        return dict(
            status="PHASE12_REPLICATED_AB",
            day=day,
            fixed_prefix_bytes=fixed_bound,
            previous_bytes=previous_bytes,
            delta_bytes=fixed_bound-previous_bytes,
            order=list(ORDER),
            trials=rows,
            summary={"full": full, "strict_delta": delta},
            wall_reduction_pct=round(wall_reduction_pct, 3),
            socket_rx_reduction_pct=round(rx_reduction_pct, 3),
            logical_transfer_reduction_pct=round(logical_reduction_pct, 3),
            byte_equivalence=True,
            source_identity_stable=True,
            compression=False,
            setup_previous_excluded_from_trial_metrics=True,
            remote_sha_server_cpu_measured=True,
            raw_payload_retained=False,
            app_cache_changed=False,
            release_changed=False,
        )


def _public_summary(result):
    return dict(
        fixed_prefix_bytes=result["fixed_prefix_bytes"],
        previous_bytes=result["previous_bytes"],
        delta_bytes=result["delta_bytes"],
        order=result["order"],
        summary=result["summary"],
        wall_reduction_pct=result["wall_reduction_pct"],
        socket_rx_reduction_pct=result["socket_rx_reduction_pct"],
        logical_transfer_reduction_pct=result["logical_transfer_reduction_pct"],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--day", default="20260925")
    parser.add_argument("--previous-bytes", type=int, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(
            args.config, args.app_root, args.day, args.previous_bytes)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite Phase 12 A/B evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE12_REPLICATED_AB=PASS")
        print("PUBLIC_SUMMARY",
              json.dumps(_public_summary(result), sort_keys=True))
        for row in result["trials"]:
            print("TRIAL", json.dumps(_public_row(row), sort_keys=True))
        print("BYTE_EQUIVALENCE_6_OF_6=PASS")
        print("SOURCE_IDENTITY_STABLE=PASS")
        print("REMOTE_SHA_SERVER_CPU_MEASURED=YES")
        print("SETUP_PREVIOUS_EXCLUDED_FROM_TRIAL_METRICS=YES")
        print("COMPRESSION_FORCED_OFF=PASS")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("NO_HOST_PATH_INODE_DIGEST_OR_PAYLOAD_PRINTED=PASS")
        print("APP_CACHE_CHANGED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE12_REPLICATED_AB=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
