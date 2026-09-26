"""Isolated real SSH snapshots -> equivalent local Python/frozen Windows builds.

No live credential is copied into the frozen application. Never commit diagnostics.
This is NOT a build/release publisher. Own all temporary workspaces.
"""
from __future__ import annotations
from dataclasses import replace
import gc
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from time import monotonic, perf_counter, process_time, sleep
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from akuz_fetch import fetch_selected, list_remote, load_config
from akuz_store import load_store, sha256
from scripts.bench_phase9_baseline import canonical_hash, inventory_manifest
from scripts.bench_phase9_portable import EXPECTED, manifest
from scripts.bench_phase9_ssh import DAYS, DIAG, MARKER
from scripts.phase9_memory import sample, tree_pids
from scripts.phase9_semantic import semantic_sql, semantic_exports
def signature(root: Path):
    inv = inventory_manifest(root)
    return {"inventory": canonical_hash(inv),
            "reports": {k: canonical_hash(v["files"])
                        for k, v in inv["reports"].items()},
            "sql": semantic_sql(root), "exports": semantic_exports(root),
            "single_events": sum(v["events"] for v in inv["reports"].values()
                                 if v["kind"] == "single")}


def frozen_snapshots(home: Path):
    cfg = replace(load_config(ROOT / "ConnectConf.cfg", ROOT),
                  local_dest=home / "incoming")
    listing = list_remote(cfg)
    old = json.loads((DIAG / "phase9_ssh_baseline_private.json").read_text(
        encoding="utf-8"))["fresh"]["snapshot"]
    reference = {row["date"]: row for row in old}
    folder = home / "snapshots"
    folder.mkdir()
    snapshots = []
    for day in DAYS:
        matches = [r for r in listing if r["name"] == day + "_server.log"]
        if len(matches) != 1:
            raise AssertionError("Remote date missing or duplicate: " + day)
        path, digest, details = fetch_selected(cfg, matches[0])
        entry = dict(date=day, bytes=path.stat().st_size, sha256=digest)
        if (details["active"] or day not in reference or
            any(entry[k] != reference[day][k]
                for k in ("date", "bytes", "sha256")) or
            digest != sha256(path)):
            raise AssertionError("SSH snapshot changed or failed integrity gate")
        # NTFS hardlink, not a second mutable source; both consumers are read-only.
        os.link(path, folder / (day + "_server.log"))
        snapshots.append(entry)
    if sum(s["bytes"] for s in snapshots) != 956307242:
        raise AssertionError("Unexpected real workload size")
    return folder, snapshots


def monitor_pid(pid, stop, peak):
    while not stop.wait(.03):
        try:
            ids = tree_pids(pid)
            rows = [sample(x) for x in ids]
            good = [x for x in rows if x]
            peak["samples"] += 1
            peak["working_set_bytes"] = max(peak["working_set_bytes"],
                                            sum(x["working_set_bytes"] for x in good))
            peak["private_bytes"] = max(peak["private_bytes"],
                                        sum(x["private_bytes"] for x in good))
        except (OSError, ValueError, ProcessLookupError):
            continue
def monitor(start_pid):
    stop = threading.Event()
    stats = {"samples": 0, "working_set_bytes": 0, "private_bytes": 0}
    thread = threading.Thread(target=monitor_pid, args=(start_pid, stop, stats),
                              daemon=True)
    thread.start()
    return stop, thread, stats


def python_build(root: Path, folder: Path):
    from akuz_app import State, perform_list, perform_build_current
    state = State()
    perform_list(root, state, source="local", local_path=str(folder))
    selected = [{"id": r["id"], "date": r["date"]}
                for r in sorted(state.listing, key=lambda x: x["name"])]
    if len(selected) != len(DAYS):
        raise AssertionError("Python local listing is not exactly three")
    stop, thread, mem = monitor(os.getpid())
    start, cpu = perf_counter(), process_time()
    try:
        perform_build_current(root, state, selected)
        wall_s, cpu_s = perf_counter()-start, process_time()-cpu
    finally:
        stop.set()
        thread.join()
    if state.result["reused"] or state.result["analytics_warning"]:
        raise AssertionError("Python fresh build failed")
    first = signature(root)
    perform_build_current(root, state, selected)
    if not state.result["reused"] or signature(root) != first:
        raise AssertionError("Python warm cache changed")
    return first, {"wall_s": round(wall_s, 3), "cpu_s": round(cpu_s, 3),
                   "memory": mem}
def wait_for(opener, base, *, busy=None, seconds=960):
    until = monotonic() + seconds
    while monotonic() < until:
        try:
            with opener.open(base + "/api/status", timeout=8) as response:
                status = json.load(response)
            if busy is None or status["busy"] is busy:
                if status.get("error"):
                    raise AssertionError("Frozen runtime reported failure")
                return status
        except urllib.error.HTTPError:
            # An API rejection is not a transient local transport error.
            raise
        except (OSError, urllib.error.URLError):
            # A temporary status-poll failure must not abort a long build.
            # The POST already acknowledged admission before this wait starts.
            pass
        sleep(.3)
    raise TimeoutError("Frozen runtime timed out")


def post(opener, base, route, payload):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + route, data=body, method="POST",
        headers={"Origin": base, "Content-Type": "application/json"})
    with opener.open(request, timeout=10) as response:
        if response.status != 202:
            raise AssertionError("Frozen API rejected test request")
    return wait_for(opener, base, busy=False)
def frozen_build(root: Path, folder: Path, archive: Path):
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist()) != EXPECTED:
            raise AssertionError("Unexpected frozen archive contents")
        z.extractall(root)
    app = root / "AKUZLogExplorer"
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    base = "http://127.0.0.1:" + str(port)
    env = os.environ.copy()
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    env["AKUZ_PHASE9_DERIVED_SPOOL"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    proc = subprocess.Popen(
        [str(app / "AKUZLogExplorer.exe"), "--no-browser", "--port", str(port)],
        cwd=root, env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        wait_for(opener, base)
        status = post(opener, base, "/api/list",
                      {"source": "local", "local_path": str(folder)})
        selected = [{"id": r["id"], "date": r["date"]}
                    for r in sorted(status["listing"], key=lambda x: x["name"])]
        if len(selected) != len(DAYS):
            raise AssertionError("Frozen local listing is not exactly three")
        stop, thread, mem = monitor(proc.pid)
        start = perf_counter()
        try:
            state = post(opener, base, "/api/build", {"selections": selected})
            elapsed = perf_counter() - start
        finally:
            stop.set()
            thread.join()
        if state["result"]["reused"] or state["result"]["analytics_warning"]:
            raise AssertionError("Frozen fresh build failed")
        first = signature(app)
        state = post(opener, base, "/api/build", {"selections": selected})
        if not state["result"]["reused"] or signature(app) != first:
            raise AssertionError("Frozen warm cache changed")
        return first, {"wall_s": round(elapsed, 3), "memory": mem}
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait(timeout=20)
def main():
    if os.name != "nt":
        raise SystemExit("Windows-only full frozen parity benchmark")
    archive = ROOT / "dist" / "AKUZLogExplorer-windows-x64.zip"
    package = manifest(archive)
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if package["build_git_sha"] != git_sha:
        raise AssertionError("Rebuild the frozen diagnostic from current HEAD")
    DIAG.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phase9_frozen_", dir=DIAG) as td:
        home = Path(td)
        (home / MARKER).write_text("disposable\n", encoding="ascii")
        source, snapshots = frozen_snapshots(home)
        print("REAL_SNAPSHOT_SHA_GATE_PASS", flush=True)
        old, python = python_build(home / "python", source)
        print("PYTHON_FULL_BUILD_PASS", python["wall_s"], flush=True)
        new, frozen = frozen_build(home / "frozen", source, archive)
        print("FROZEN_FULL_BUILD_PASS", frozen["wall_s"], flush=True)
        checks = {key: old[key] == new[key] for key in old}
        if not all(checks.values()) or old["single_events"] != 657738:
            raise AssertionError("Python/frozen report or analytics parity failed")
        result = {"git_sha": git_sha, "zip_sha256": package["zip_sha256"],
                  "snapshots": snapshots, "python": python, "frozen": frozen,
                  "checks": checks, "raw_payload_saved": False,
                  "disposable_workspace_cleaned": True}
        gc.collect()
    target = DIAG / "phase9_frozen_real_private.json"
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("REAL_FROZEN_PARITY_PASS", checks, flush=True)
    print("WORKSPACE_CLEANED", True, flush=True)


if __name__ == "__main__":
    main()
