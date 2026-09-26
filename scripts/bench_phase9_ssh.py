"""Private Phase 9 control: fresh SSH snapshots from 23/24/25 Sep 2026.
Only sanitized counters and digests persist. Real raw stays inside disposable workspace.
Never load a test source from the application's existing downloads/cache/reports.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
from time import perf_counter, process_time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from akuz_fetch import load_config
from akuz_store import load_store, sha256
from scripts.bench_phase9_baseline import (
    canonical_hash, disk_bytes, inventory_manifest, last_trace, sql_fingerprint)
from scripts.phase9_memory import sample

DAYS = ("20260923", "20260924", "20260925")
DIAG = ROOT / "diagnostics"
MARKER = ".akuz_phase9_ssh_disposable"
RESULT = DIAG / "phase9_ssh_private.json"
RESULT_SPOOL = DIAG / "phase9_ssh_spool_private.json"


def scrub_stale_temp():
    DIAG.mkdir(exist_ok=True)
    for p in DIAG.glob("phase9_ssh_*"):
        if p.is_dir() and (p / MARKER).read_text(encoding="ascii") == "disposable\n" if (p / MARKER).is_file() else False:
            shutil.rmtree(p)


def build_once(workspace: Path, use_derived_spool=False):
    import akuz_app
    from akuz_app import State, perform_build_current, perform_list
    from akuz_analytics import read_js
    cfg = replace(load_config(ROOT / "ConnectConf.cfg", ROOT),
                  local_dest=workspace / "downloads")
    state = State()
    with patch.object(akuz_app, "source_config", return_value=cfg):
        listed_start = perf_counter()
        perform_list(workspace, state, source="linux")
        listing_s = perf_counter() - listed_start
        targets = {}
        for row in state.listing:
            for day in DAYS:
                if row["name"] == day + "_server.log":
                    if day in targets:
                        raise AssertionError("Duplicate day in remote listing")
                    targets[day] = row
        if tuple(sorted(targets)) != DAYS:
            raise AssertionError("Exact remote 23, 24, 25 Sep logs missing")
        selected = [dict(id=targets[day]["id"],
                         date=date(int(day[:4]), int(day[4:6]), int(day[6:])).isoformat())
                    for day in DAYS]
        stop = threading.Event()
        memory = dict(samples=0, sampled_peak_working_set_bytes=0,
                      sampled_peak_private_bytes=0,
                      os_peak_working_set_bytes=0, os_peak_pagefile_bytes=0)
        def watch():
            while True:
                point = sample(os.getpid())
                if point:
                    memory["samples"] += 1
                    for output, metric in (
                        ("sampled_peak_working_set_bytes", "working_set_bytes"),
                        ("sampled_peak_private_bytes", "private_bytes"),
                        ("os_peak_working_set_bytes", "peak_working_set_bytes"),
                        ("os_peak_pagefile_bytes", "peak_pagefile_bytes")):
                        memory[output] = max(memory[output], point[metric])
                if stop.wait(.03):
                    break
        monitor = threading.Thread(target=watch, daemon=True)
        monitor.start()
        wall_start, cpu_start = perf_counter(), process_time()
        try:
            perform_build_current(workspace, state, selected,
                                  use_derived_spool=use_derived_spool)
        finally:
            elapsed, cpu_s = perf_counter()-wall_start, process_time()-cpu_start
            stop.set()
            monitor.join()
    if state.result["analytics_warning"]:
        raise AssertionError("Analytics warning during SSH control")
    if state.result["reused"]:
        raise AssertionError("Fresh workspace unexpectedly reused reports")
    store = load_store(workspace)
    if len(store["downloads"]) != 3 or len(store["reports"]) != 4:
        raise AssertionError("Expected exactly three downloads and four reports")
    snapshot = []
    for day in DAYS:
        file = targets[day]
        row = store["downloads"][file["id"]]
        path = Path(row["path"])
        if path.resolve().parent != (workspace / "downloads").resolve():
            raise AssertionError("Snapshot escaped disposable workspace")
        if sha256(path) != row["sha256"] or row["size"] != path.stat().st_size:
            raise AssertionError("Downloaded snapshot SHA/size mismatch")
        if row["snapshot"].get("active"):
            raise AssertionError("Remote file changed during controlled download")
        snapshot.append(dict(date=day, bytes=path.stat().st_size,
                             sha256=row["sha256"], listed_bytes=file["size"]))
    reports = {}
    for row in store["reports"].values():
        p = workspace / "reports" / row["id"]
        data = read_js(p / "data" / "catalog.js", "window.AKUZ_DATA=")
        if data["meta"]["events"] != row["events"]:
            raise AssertionError("Catalog event count mismatch")
        label = ("combined" if row["kind"] == "combined"
                 else next(s["date"].replace("-", "") for s in row["sources"]))
        reports[label] = dict(events=row["events"], chunks=data["meta"]["chunks"],
                              physical_lines=data["meta"]["physical_lines"],
                              replacement_chars=data["meta"]["replacement_chars"])
    if reports["combined"]["events"] != sum(reports[d]["events"] for d in DAYS):
        raise AssertionError("Combined lost or duplicated event")
    inventory = inventory_manifest(workspace)
    offsets = last_trace(workspace, 0)
    traces = [line for line in offsets if
              line.startswith("build.summary status=done") or
              line.startswith("generate.parse status=done") or
              line.startswith("report.generate status=done") or
              line.startswith("source.ssh.transfer status=summary") or
              line.startswith("analytics.refresh status=done") or
              line.startswith("derived.spool status=summary")]
    fresh = dict(wall_s=round(elapsed, 3), cpu_s=round(cpu_s, 3),
                 listing_s=round(listing_s, 3), build_memory=memory,
                 report_counts=reports, snapshot=snapshot,
                 inventory_sha256=canonical_hash(inventory),
                 report_hashes={key: canonical_hash(val["files"])
                                for key,val in inventory["reports"].items()},
                 sql_hashes=sql_fingerprint(workspace),
                 analytics_export={p.name:sha256(p)
                    for p in (workspace/"data").glob("*.js")},
                 bytes_by_dir={folder:disk_bytes(workspace, folder)
                     for folder in ("downloads", "reports", "cache", "data")},
                 trace=traces)
    with patch.object(akuz_app, "source_config", return_value=cfg):
        started, cpu = perf_counter(), process_time()
        perform_build_current(workspace, state, selected,
                              use_derived_spool=use_derived_spool)
        warm = dict(wall_s=round(perf_counter()-started, 3),
                    cpu_s=round(process_time()-cpu, 3),
                    reused=state.result["reused"])
    if not warm["reused"]:
        raise AssertionError("Warm no-op cache reuse FAILED")
    if canonical_hash(inventory_manifest(workspace)) != fresh["inventory_sha256"]:
        raise AssertionError("Warm no-op mutated deterministic output")
    if sql_fingerprint(workspace) != fresh["sql_hashes"]:
        raise AssertionError("Warm no-op changed logical SQL content")
    if {p.name:sha256(p) for p in (workspace/"data").glob("*.js")} != fresh["analytics_export"]:
        raise AssertionError("Warm no-op changed analytics exports")
    return dict(fresh=fresh,warm=warm)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path,
                        help="Previously generated local-private JSON to compare")
    parser.add_argument("--derived-spool", action="store_true",
                        help="EXPERIMENTAL: temporary derived spool on fresh singles")
    args=parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Windows working-set instrumentation required")
    scrub_stale_temp()
    with tempfile.TemporaryDirectory(prefix="phase9_ssh_",dir=DIAG) as folder:
        home = Path(folder)
        (home / MARKER).write_text("disposable\n",encoding="ascii")
        run = build_once(home / "workspace", args.derived_spool)
        # All DB snapshots are closed; collect any remaining cursor cycles on
        # Windows before removing the private ~3.3 GB workspace.
        import gc
        gc.collect()
    sha = subprocess.check_output(["git","rev-parse","HEAD"],
                                  cwd=ROOT,text=True).strip()
    record = dict(git_sha=sha,git_dirty=bool(subprocess.check_output(
        ["git","status","--porcelain"],cwd=ROOT,text=True).strip()),
        platform=platform.platform(),python=sys.version.split()[0],
        root_isolated=True,temp_workspace_deleted=True,
        raw_payload_saved=False,derived_spool=args.derived_spool,**run)
    if args.reference:
        old=json.loads(args.reference.read_text(encoding="utf8"))
        previous=old["fresh"]
        if previous["snapshot"]!=run["fresh"]["snapshot"]:
            raise AssertionError("SSH SNAPSHOT SHA CHANGED: cannot A/B")
        checks={key:previous[key]==run["fresh"][key] for key in
                ("inventory_sha256","report_hashes","sql_hashes","analytics_export",
                 "report_counts")}
        record["reference_comparison"]=checks
        if not checks["report_hashes"] or not checks["inventory_sha256"]:
            raise AssertionError("REAL REPORT BYTE EQUIVALENCE FAILED")
    (RESULT_SPOOL if args.derived_spool else RESULT).write_text(
        json.dumps(record,ensure_ascii=False,indent=2),encoding="utf8")
    print("PHASE9_SSH_PASS")
    print("INPUT_SHA256",[(s["date"],s["bytes"],s["sha256"])
                          for s in run["fresh"]["snapshot"]])
    print("REPORTS",run["fresh"]["report_counts"])
    print("WALL_CPU",run["fresh"]["wall_s"],run["fresh"]["cpu_s"])
    print("MEMORY",run["fresh"]["build_memory"])
    print("WARM",run["warm"])
    print("REFERENCE",record.get("reference_comparison","none"))
    print("TRACE",run["fresh"]["trace"])
    print("WORKSPACE_CLEANED",True)


if __name__ == "__main__":
    main()
