"""Phase 11 process-isolated pair overlap benchmark.

Benchmark-only candidate after the threaded one-ahead design regressed in
balanced real trials. The next SSH fetch runs in a spawned child process while
the parent generates the current completed snapshot. No application runtime
scheduler/cache/release is modified.

Windows process-tree memory is sampled as a simultaneous lower-bound total.
Child CPU is read from the OS in the child before exit so Python spawn/import
cost is included in the candidate's total CPU.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
import multiprocessing
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import load_config
from akuz_html_explorer import generate
from scripts.bench_phase11_ssh_overlap import (
    SourceSpec, _discover, _fetch_fixed, _report_bytes, _run_mode)
from scripts.bench_phase9_baseline import canonical_hash, file_manifest
from scripts.phase11_overlap import CompletedSnapshot


ORDER = ("serial", "process", "process", "serial")


def _child_fetch(cfg, spec: SourceSpec, destination: str, sender) -> None:
    """Spawn-safe worker: fetch one fixed prefix, return only local proof + OS CPU."""
    try:
        snapshot = _fetch_fixed(cfg, spec, Path(destination))
        from scripts.phase9_memory import sample
        own = sample(os.getpid())
        sender.send(("ok", str(snapshot.path), snapshot.digest, snapshot.bytes,
                     own["cpu_time_s"] if own is not None else None))
    except BaseException as exc:
        # Local-only IPC. Never print remote identity or credentials.
        try:
            sender.send(("error", type(exc).__name__))
        except BaseException:
            pass
        raise
    finally:
        sender.close()


class _TreeMemorySampler:
    """Sample simultaneous parent+descendant working/private bytes on Windows."""
    def __init__(self, interval=.05):
        self.interval = interval
        self.stop = threading.Event()
        self.samples = 0
        self.unreadable = 0
        self.peak_ws = 0
        self.peak_private = 0
        self.max_processes = 0
        self.thread = None

    def __enter__(self):
        from scripts.phase9_memory import sample, tree_pids
        parent = os.getpid()

        def poll():
            while not self.stop.wait(self.interval):
                pids = tree_pids(parent)
                ws = private = readable = 0
                for pid in pids:
                    row = sample(pid)
                    if row is None:
                        continue
                    readable += 1
                    ws += row["working_set_bytes"]
                    private += row["private_bytes"]
                self.samples += 1
                if not readable:
                    self.unreadable += 1
                    continue
                self.max_processes = max(self.max_processes, readable)
                self.peak_ws = max(self.peak_ws, ws)
                self.peak_private = max(self.peak_private, private)

        self.thread = threading.Thread(
            target=poll, name="akuz-phase11-tree-memory", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Phase 11 process memory sampler did not stop")


def _parse_snapshot(snapshot: CompletedSnapshot, reports: Path,
                    parse_metrics: dict) -> dict:
    spec: SourceSpec = snapshot.item
    out = reports / spec.day
    started = perf_counter()
    cpu0 = process_time()
    meta = generate(snapshot.path, out, spec.base_date, 1000, 35)
    parse_metrics[spec.day] = dict(
        wall_s=round(perf_counter() - started, 6),
        process_cpu_s=round(process_time() - cpu0, 6),
        events=meta["events"], physical_lines=meta["physical_lines"],
        report_bytes=_report_bytes(out),
        report_manifest_sha256=canonical_hash(file_manifest(out)))
    return dict(
        day=spec.day, events=meta["events"],
        physical_lines=meta["physical_lines"],
        report_manifest_sha256=parse_metrics[spec.day]["report_manifest_sha256"])


def _process_pair_mode(cfg, specs: tuple[SourceSpec, ...], root: Path) -> dict:
    if len(specs) != 2:
        raise AssertionError("Process candidate is intentionally limited to one pair")
    snapshots = root / "snapshots"
    reports = root / "reports"
    snapshots.mkdir(parents=True, exist_ok=True)
    fetch_metrics = {}
    parse_metrics = {}
    source_sha = {}
    outputs = []
    ctx = multiprocessing.get_context("spawn")

    parent_cpu0 = process_time()
    with _TreeMemorySampler() as memory:
        started = perf_counter()
        first_start = perf_counter()
        first = _fetch_fixed(cfg, specs[0], snapshots / specs[0].name)
        fetch_metrics[specs[0].day] = round(perf_counter() - first_start, 6)
        source_sha[specs[0].day] = first.digest

        receiver, sender = ctx.Pipe(duplex=False)
        second_path = snapshots / specs[1].name
        child = ctx.Process(
            target=_child_fetch,
            args=(cfg, specs[1], str(second_path), sender),
            name="akuz-phase11-fetch")
        second_start = perf_counter()
        child.start()
        sender.close()
        child_cpu = None
        message = None
        try:
            outputs.append(_parse_snapshot(first, reports, parse_metrics))
            if not receiver.poll(300):
                raise TimeoutError("Phase 11 process fetch did not return")
            message = receiver.recv()
            child.join(timeout=20)
            if child.is_alive():
                raise TimeoutError("Phase 11 process fetch did not exit")
            if child.exitcode != 0 or not message or message[0] != "ok":
                kind = message[1] if message and message[0] == "error" else "ChildExit"
                raise RuntimeError("Phase 11 process fetch failed: " + kind)
            _, returned_path, digest, count, child_cpu = message
            if Path(returned_path).resolve() != second_path.resolve():
                raise AssertionError("Phase 11 child returned unexpected snapshot path")
            second = CompletedSnapshot(
                item=specs[1], path=second_path, digest=digest, bytes=count)
            if not second.path.is_file() or second.path.stat().st_size != second.bytes:
                raise AssertionError("Phase 11 child snapshot incomplete")
            fetch_metrics[specs[1].day] = round(
                perf_counter() - second_start, 6)
            source_sha[specs[1].day] = second.digest
            outputs.append(_parse_snapshot(second, reports, parse_metrics))
        finally:
            receiver.close()
            if child.is_alive():
                child.terminate()
                child.join(timeout=10)
                if child.is_alive():
                    child.kill()
                    child.join(timeout=10)
                second_path.unlink(missing_ok=True)

        wall_s = perf_counter() - started

    if child_cpu is None:
        raise AssertionError("Phase 11 child CPU evidence unavailable")
    parent_cpu = process_time() - parent_cpu0
    return dict(
        mode="process",
        wall_s=round(wall_s, 6),
        process_cpu_s=round(parent_cpu + child_cpu, 6),
        parent_cpu_s=round(parent_cpu, 6),
        child_cpu_s=round(child_cpu, 6),
        fetched=2, parsed=2, outputs=outputs,
        fetch=fetch_metrics, parse=parse_metrics, source_sha=source_sha,
        sampled_peak_ws_bytes=memory.peak_ws,
        sampled_peak_private_bytes=memory.peak_private,
        memory_samples=memory.samples,
        unreadable_memory_samples=memory.unreadable,
        max_sampled_processes=memory.max_processes,
        workspace_bytes=sum(
            path.stat().st_size for path in root.rglob("*") if path.is_file()))


def _sanitize(row: dict) -> dict:
    clean = dict(row)
    clean.pop("source_sha", None)
    clean["outputs"] = [dict(item) for item in row["outputs"]]
    clean["parse"] = {day: dict(value) for day, value in row["parse"].items()}
    for output in clean["outputs"]:
        output.pop("report_manifest_sha256", None)
    for parsed in clean["parse"].values():
        parsed.pop("report_manifest_sha256", None)
    return clean


def _summary(rows: list[dict], mode: str) -> dict:
    subset = [row for row in rows if row["mode"] == mode]
    if len(subset) != 2:
        raise AssertionError("Expected two trials per process-isolation mode")
    def med(name):
        return statistics.median(row[name] for row in subset)
    return dict(
        trials=2,
        wall_median_s=round(med("wall_s"), 6),
        cpu_median_s=round(med("process_cpu_s"), 6),
        peak_private_median_bytes=int(med("sampled_peak_private_bytes")),
        peak_ws_median_bytes=int(med("sampled_peak_ws_bytes")),
        wall_values_s=[row["wall_s"] for row in subset],
        second_fetch_values_s=[row["fetch"]["20260924"] for row in subset],
        first_parse_values_s=[
            row["parse"]["20260923"]["wall_s"] for row in subset],
    )


def run(config_path: Path, app_root: Path, diagnostics: Path) -> dict:
    cfg = replace(load_config(config_path, app_root), compression=False)
    specs = _discover(cfg)
    workspace = None
    trials = []
    reference_sha = None
    reference_outputs = None
    try:
        with tempfile.TemporaryDirectory(
                prefix="phase11_process_overlap_", dir=diagnostics) as td:
            workspace = Path(td)
            for number, mode in enumerate(ORDER, 1):
                trial_root = workspace / f"{number:02d}_{mode}"
                row = (_process_pair_mode(cfg, specs, trial_root)
                       if mode == "process"
                       else _run_mode(cfg, specs, trial_root, overlap=False))
                source_sha = row["source_sha"]
                outputs = {item["day"]: item for item in row["outputs"]}
                if reference_sha is None:
                    reference_sha = source_sha
                    reference_outputs = outputs
                else:
                    if source_sha != reference_sha:
                        raise AssertionError("Phase 11 process source SHA drift")
                    if outputs != reference_outputs:
                        raise AssertionError("Phase 11 process report manifest drift")
                if row["memory_samples"] < 10 or row["unreadable_memory_samples"]:
                    raise AssertionError("Phase 11 process memory evidence invalid")
                trials.append(_sanitize(row))

            result = dict(
                status="PHASE11_PROCESS_ISOLATION_PAIR_EXPERIMENT_NOT_PRODUCTION",
                order=list(ORDER),
                days=[spec.day for spec in specs],
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
            raise AssertionError("Phase 11 process workspace cleanup failed")
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
            raise FileExistsError("Refusing to overwrite Phase 11 process evidence")
        result = run(args.config, args.app_root, args.diagnostics)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PHASE11_PROCESS_PAIR_PASS")
        for mode in ("serial", "process"):
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
        print("PHASE11_PROCESS_PAIR_FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
