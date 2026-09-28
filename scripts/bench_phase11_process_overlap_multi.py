"""Phase 11 process-isolated 23+24+25 large-source benchmark.

Benchmark-only continuation of the corrected pair candidate. At most one
spawned child fetch is active while the parent generates the current completed
snapshot. Compression is forced OFF. No normal app/cache/release code changes.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import list_remote, load_config
from scripts.bench_phase11_process_overlap import (
    _TreeMemorySampler, _child_fetch, _parse_snapshot, _sanitize)
from scripts.bench_phase11_ssh_overlap import SourceSpec, _fetch_fixed, _run_mode
from scripts.phase11_overlap import CompletedSnapshot


DAYS = ("20260923", "20260924", "20260925")
ORDER = ("serial", "process", "process", "serial")


def _discover_three(cfg) -> tuple[SourceSpec, ...]:
    listing = list_remote(cfg, notify=lambda message: None)
    result = []
    for day in DAYS:
        matches = [row for row in listing if row["name"] == day + "_server.log"]
        if len(matches) != 1:
            raise AssertionError("Phase 11 multi source missing or ambiguous")
        row = matches[0]
        if row.get("device") is None or row.get("inode") is None:
            raise AssertionError("Phase 11 multi remote device/inode unavailable")
        result.append(SourceSpec(
            day=day, name=row["name"], path=row["path"], bound=row["size"],
            device=row["device"], inode=row["inode"]))
    return tuple(result)


def _recv_child(ctx, cfg, spec: SourceSpec, destination: Path,
                parse_current, fetch_metrics: dict, ready_metrics: dict,
                source_sha: dict):
    receiver, sender = ctx.Pipe(duplex=False)
    child = ctx.Process(
        target=_child_fetch,
        args=(cfg, spec, str(destination), sender),
        name="akuz-phase11-fetch")
    started = perf_counter()
    child.start()
    sender.close()
    message = None
    try:
        parse_current()
        if not receiver.poll(900):
            raise TimeoutError("Phase 11 multi process fetch did not return")
        message = receiver.recv()
        child.join(timeout=30)
        if child.is_alive():
            raise TimeoutError("Phase 11 multi process fetch did not exit")
        if child.exitcode != 0 or not message or message[0] != "ok":
            kind = message[1] if message and message[0] == "error" else "ChildExit"
            raise RuntimeError("Phase 11 multi process fetch failed: " + kind)
        _, returned_path, digest, count, child_cpu, child_fetch_wall = message
        if Path(returned_path).resolve() != destination.resolve():
            raise AssertionError("Phase 11 multi child returned unexpected path")
        snap = CompletedSnapshot(
            item=spec, path=destination, digest=digest, bytes=count)
        if not snap.path.is_file() or snap.path.stat().st_size != snap.bytes:
            raise AssertionError("Phase 11 multi child snapshot incomplete")
        if child_cpu is None or child_cpu <= 0:
            raise AssertionError("Phase 11 multi child CPU unavailable")
        if child_fetch_wall is None or child_fetch_wall <= 0:
            raise AssertionError("Phase 11 multi child fetch wall unavailable")
        fetch_metrics[spec.day] = round(child_fetch_wall, 6)
        ready_metrics[spec.day] = round(perf_counter() - started, 6)
        source_sha[spec.day] = digest
        return snap, child_cpu
    finally:
        receiver.close()
        if child.is_alive():
            child.terminate()
            child.join(timeout=10)
            if child.is_alive():
                child.kill()
                child.join(timeout=10)
            destination.unlink(missing_ok=True)


def _process_multi_mode(cfg, specs: tuple[SourceSpec, ...], root: Path) -> dict:
    if len(specs) != 3:
        raise AssertionError("Phase 11 multi candidate requires exactly three sources")
    snapshots = root / "snapshots"
    reports = root / "reports"
    snapshots.mkdir(parents=True, exist_ok=True)
    fetch_metrics = {}
    ready_metrics = {}
    parse_metrics = {}
    source_sha = {}
    outputs = []
    ctx = multiprocessing.get_context("spawn")
    child_cpu_total = 0.0

    parent_cpu0 = process_time()
    with _TreeMemorySampler() as memory:
        wall0 = perf_counter()
        first0 = perf_counter()
        current = _fetch_fixed(cfg, specs[0], snapshots / specs[0].name)
        fetch_metrics[specs[0].day] = round(perf_counter() - first0, 6)
        ready_metrics[specs[0].day] = fetch_metrics[specs[0].day]
        source_sha[specs[0].day] = current.digest

        for index, spec in enumerate(specs):
            if current.item != spec:
                raise AssertionError("Phase 11 multi source order drift")
            if index + 1 < len(specs):
                next_spec = specs[index + 1]
                holder = {}
                def parse_current():
                    holder["output"] = _parse_snapshot(
                        current, reports, parse_metrics)
                next_snap, child_cpu = _recv_child(
                    ctx, cfg, next_spec, snapshots / next_spec.name,
                    parse_current, fetch_metrics, ready_metrics, source_sha)
                outputs.append(holder["output"])
                child_cpu_total += child_cpu
                current = next_snap
            else:
                outputs.append(_parse_snapshot(current, reports, parse_metrics))

        wall_s = perf_counter() - wall0

    parent_cpu = process_time() - parent_cpu0
    return dict(
        mode="process",
        wall_s=round(wall_s, 6),
        process_cpu_s=round(parent_cpu + child_cpu_total, 6),
        parent_cpu_s=round(parent_cpu, 6),
        child_cpu_s=round(child_cpu_total, 6),
        fetched=3, parsed=3,
        outputs=outputs,
        fetch=fetch_metrics,
        fetch_ready_latency=ready_metrics,
        parse=parse_metrics,
        source_sha=source_sha,
        sampled_peak_ws_bytes=memory.peak_ws,
        sampled_peak_private_bytes=memory.peak_private,
        memory_samples=memory.samples,
        unreadable_memory_samples=memory.unreadable,
        max_sampled_processes=memory.max_processes,
        workspace_bytes=sum(
            p.stat().st_size for p in root.rglob("*") if p.is_file()))


def _summary(rows: list[dict], mode: str) -> dict:
    subset = [row for row in rows if row["mode"] == mode]
    if len(subset) != 2:
        raise AssertionError("Expected two trials per multi-source mode")
    def med(name):
        return statistics.median(row[name] for row in subset)
    return dict(
        trials=2,
        wall_median_s=round(med("wall_s"), 6),
        cpu_median_s=round(med("process_cpu_s"), 6),
        peak_private_median_bytes=int(med("sampled_peak_private_bytes")),
        peak_ws_median_bytes=int(med("sampled_peak_ws_bytes")),
        wall_values_s=[row["wall_s"] for row in subset],
        fetch_values_s={
            day: [row["fetch"][day] for row in subset] for day in DAYS},
        ready_values_s={
            day: [row.get("fetch_ready_latency", row["fetch"])[day]
                  for row in subset] for day in DAYS},
        parse_values_s={
            day: [row["parse"][day]["wall_s"] for row in subset] for day in DAYS},
    )


def run(config_path: Path, app_root: Path, diagnostics: Path) -> dict:
    cfg = replace(load_config(config_path, app_root), compression=False)
    specs = _discover_three(cfg)
    workspace = None
    trials = []
    reference_sha = None
    reference_outputs = None
    with tempfile.TemporaryDirectory(
            prefix="phase11_process_multi_", dir=diagnostics) as td:
        workspace = Path(td)
        for number, mode in enumerate(ORDER, 1):
            trial_root = workspace / f"{number:02d}_{mode}"
            row = (_process_multi_mode(cfg, specs, trial_root)
                   if mode == "process"
                   else _run_mode(cfg, specs, trial_root, overlap=False))
            source_sha = row["source_sha"]
            outputs = {item["day"]: item for item in row["outputs"]}
            if reference_sha is None:
                reference_sha = source_sha
                reference_outputs = outputs
            else:
                if source_sha != reference_sha:
                    raise AssertionError("Phase 11 multi source SHA drift")
                if outputs != reference_outputs:
                    raise AssertionError("Phase 11 multi report manifest drift")
            if row["memory_samples"] < 10 or row["unreadable_memory_samples"]:
                raise AssertionError("Phase 11 multi memory evidence invalid")
            if mode == "process" and row.get("max_sampled_processes", 0) < 2:
                raise AssertionError("Phase 11 multi child absent from tree memory")
            trials.append(_sanitize(row))

        result = dict(
            status="PHASE11_PROCESS_ISOLATION_MULTI_EXPERIMENT_NOT_PRODUCTION",
            order=list(ORDER),
            days=list(DAYS),
            fixed_prefix_bytes={spec.day: spec.bound for spec in specs},
            compression=False,
            trials=trials,
            summary={
                "serial": _summary(trials, "serial"),
                "process": _summary(trials, "process"),
            },
            source_sha_equal=True,
            report_manifest_equal=True,
            raw_payload_saved=False,
            app_cache_changed=False,
            release_changed=False)
    result["workspace_cleaned"] = bool(workspace) and not workspace.exists()
    if not result["workspace_cleaned"]:
        raise AssertionError("Phase 11 multi workspace cleanup failed")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite Phase 11 multi evidence")
        result = run(args.config, args.app_root, args.diagnostics)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PHASE11_PROCESS_MULTI_PASS")
        for mode in ("serial", "process"):
            row = result["summary"][mode]
            print("SUMMARY", mode,
                  "wall_median_s", row["wall_median_s"],
                  "cpu_median_s", row["cpu_median_s"],
                  "peak_private_median_bytes", row["peak_private_median_bytes"],
                  "fetch24_s", ",".join(f"{v:.6f}" for v in row["fetch_values_s"]["20260924"]),
                  "fetch25_s", ",".join(f"{v:.6f}" for v in row["fetch_values_s"]["20260925"]),
                  "ready24_s", ",".join(f"{v:.6f}" for v in row["ready_values_s"]["20260924"]),
                  "ready25_s", ",".join(f"{v:.6f}" for v in row["ready_values_s"]["20260925"]))
        print("SOURCE_SHA_EQUAL=PASS")
        print("REPORT_MANIFEST_EQUAL=PASS")
        print("WORKSPACE_CLEANED=PASS")
        print("RAW_PAYLOAD_SAVED=NO")
        print("APP_CACHE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE11_PROCESS_MULTI_FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
