"""Phase 9.0: isolated, reproducible AKUZ build/cache baseline (no real logs).

Run on Windows: python scripts/bench_phase9_baseline.py --repeat 3
Only synthetic source data is generated; existing app data is never touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import threading
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from akuz_store import load_store, save_store, sha256


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def create_sources(folder: Path, events: int, chars: int):
    """Three fixed-shape AKUZ logs; only names, counts and hashes leave temp."""
    folder.mkdir()
    names = ("20260924_A.log", "20260925_B.log", "20260926_C.log")
    snapshots = []
    for index, name in enumerate(names):
        path = folder / name
        with path.open("wb") as output:
            if index == 1:
                output.write(b"\xef\xbb\xbf")
            for n in range(events):
                message = ("System.InvalidOperationException: synthetic failure 1234"
                           if n % 5 == 0 else
                           "запись: за 12.3 ms; общее время: 00:00:01.234"
                           if n % 5 == 1 else "synthetic event")
                line = f"{23 if n % 7 == 0 else 12}:{n % 60:02}:00.000,AKUZ,req,user: {message}"
                ending = "\r\n" if index == 1 else "\n"
                output.write((line + ending + "X" * chars + ending).encode("utf-8"))
            if index == 2:
                output.write(b"13:00:00.000,AKUZ,req,user: invalid \xff UTF8\n")
        snapshots.append(dict(name=name, sha256=sha256(path),
                              bytes=path.stat().st_size,
                              expected_events=events + int(index == 2)))
    return snapshots


def file_manifest(directory: Path):
    result = {}
    for file in sorted(p for p in directory.rglob("*") if p.is_file()):
        name = file.relative_to(directory).as_posix()
        if name == "provenance.json":
            data = json.loads(file.read_text("utf-8"))
            data.pop("generated", None)  # The only intentionally volatile field.
            # Keep real source identities: only generated is nondeterministic.
            result[name + ":semantic"] = canonical_hash(data)
        elif name == "data/catalog.js":
            import re
            payload = file.read_bytes()
            # Current combined generator uses a random disposable JSONL name
            # as meta.source. Normalize ONLY this known volatile JSON string.
            payload = re.sub(rb'("source":")akuz-v4-merge-[^"]+\.jsonl(")',
                             rb'\1<temporary-merge>.jsonl\2', payload, count=1)
            result[name] = hashlib.sha256(payload).hexdigest()
        else:
            result[name] = sha256(file)
    return result


def inventory_manifest(root: Path):
    store = load_store(root)
    reports = {}
    for entry in store["reports"].values():
        reports[entry["key"]] = dict(kind=entry["kind"],
            events=entry["events"], lines=entry["lines"],
            aliases=sorted(entry.get("aliases", [])),
            sources=[{key: value for key, value in source.items()
                      if key in ("name", "date", "sha256")}
                     for source in entry["sources"]],
            files=file_manifest(root / "reports" / entry["id"]))
    downloads = {key: dict(sha256=value["sha256"], size=value["size"])
                 for key, value in store["downloads"].items()}
    return dict(reports=reports, downloads=downloads)


def sql_fingerprint(root: Path):
    path = root / "cache" / "error_analytics.sqlite"
    if not path.is_file():
        raise AssertionError("Missing analytics SQLite index")
    store = load_store(root)
    aliases = {entry["id"]: entry["key"] for entry in store["reports"].values()}
    # sqlite3.Connection.__exit__ commits but does NOT close the OS file handle.
    # Windows cannot remove a disposable benchmark workspace until it is closed.
    from contextlib import closing
    with closing(sqlite3.connect(path)) as db:
        tables = sorted(row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        digest = {}
        for name in tables:
            rows = []
            for row in db.execute('SELECT * FROM "' + name + '"'):
                rows.append([aliases.get(x, x) if isinstance(x, str) else x for x in row])
            digest[name] = canonical_hash(sorted(rows, key=lambda x: json.dumps(
                x, ensure_ascii=False, default=str)))
    return digest


def disk_bytes(root: Path, name: str):
    folder = root / name
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.exists() else 0


def last_trace(root: Path, offset: int):
    trace = root / "diagnostics" / "performance.txt"
    lines = trace.read_text("utf-8").splitlines()[offset:] if trace.exists() else []
    # The internal trace format only emits predefined stage/status + numeric metrics.
    return [line.split(" stage=", 1)[1] for line in lines if " stage=" in line]


def build_worker(root: Path, sources: Path):
    from akuz_app import State, perform_build_current, perform_list
    root.mkdir(exist_ok=True)
    state = State()
    perform_list(root, state, source="local", local_path=str(sources))
    selected = [dict(id=item["id"], date="") for item in
                sorted(state.listing, key=lambda item: item["name"])]
    trace = root / "diagnostics" / "performance.txt"
    offset = len(trace.read_text("utf-8").splitlines()) if trace.exists() else 0
    start = perf_counter()
    cpu_start = __import__("time").process_time()
    perform_build_current(root, state, selected)
    wall = perf_counter() - start
    cpu = __import__("time").process_time() - cpu_start
    result = state.result
    if result["analytics_warning"]:
        raise AssertionError("Analytics refresh failed")
    inventory = inventory_manifest(root)
    reports = inventory["reports"]
    return dict(wall_s=round(wall, 6), cpu_s=round(cpu, 6),
        reused=result["reused"], reports=len(result["reports"]),
        combined=bool(result["combined"]), events=sum(
            entry["events"] for entry in reports.values() if entry["kind"] == "single"),
        inventory_sha256=canonical_hash(inventory),
        reports_sha256={key: canonical_hash(value["files"])
                        for key, value in reports.items()},
        analytics_sql_sha256=sql_fingerprint(root),
        analytics_output_sha256=canonical_hash({
            file.name: sha256(file) for file in (root / "data").glob("*.js")}),
        size_bytes={key: disk_bytes(root, key)
                    for key in ("downloads", "reports", "cache", "data")},
        trace=last_trace(root, offset))


def remove_report(root: Path, kind: str):
    """Delete only a known synthetic report within this disposable workspace."""
    import shutil
    store = load_store(root)
    record = next(v for v in store["reports"].values() if v["kind"] == kind)
    shutil.rmtree(root / "reports" / record["id"])
    del store["reports"][record["id"]]
    save_store(root, store)


def memory_run(root: Path, sources: Path, interval: float):
    from scripts.phase9_memory import sample, tree_pids
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker",
           "--workspace", str(root), "--source-dir", str(sources)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")
    stop = threading.Event()
    stats = dict(peak_tree_working_set_bytes_sampled=0,
        peak_tree_private_bytes_sampled=0,
        peak_working_set_bytes_per_pid_os={}, peak_pagefile_bytes_per_pid_os={},
        samples=0, unreadable_samples=0)

    def poll():
        while True:
            try:
                ids = tree_pids(proc.pid)
                measured = [sample(pid) for pid in ids]
                valid = [(pid, row) for pid, row in zip(ids, measured) if row]
                stats["samples"] += 1
                stats["unreadable_samples"] += len(ids) - len(valid)
                stats["peak_tree_working_set_bytes_sampled"] = max(
                    stats["peak_tree_working_set_bytes_sampled"],
                    sum(row["working_set_bytes"] for _, row in valid))
                stats["peak_tree_private_bytes_sampled"] = max(
                    stats["peak_tree_private_bytes_sampled"],
                    sum(row["private_bytes"] for _, row in valid))
                for pid, row in valid:
                    for field, metric in (("peak_ws", "peak_working_set_bytes"),
                                          ("peak_pagefile", "peak_pagefile_bytes")):
                        bucket = stats["peak_working_set_bytes_per_pid_os"
                                       if field == "peak_ws" else
                                       "peak_pagefile_bytes_per_pid_os"]
                        bucket[pid] = max(bucket.get(pid, 0), row[metric])
            except OSError:
                stats["unreadable_samples"] += 1
            if stop.wait(interval):
                break

    if sys.platform != "win32":
        proc.kill()
        raise RuntimeError("Phase 9 Windows memory baseline requires Windows")
    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        stdout, stderr = proc.communicate()
    finally:
        stop.set()
        thread.join()
    if proc.returncode:
        raise RuntimeError("Synthetic worker failed:\n" + stderr[-2500:])
    record = json.loads(stdout)
    record["memory"] = dict(stats, peak_working_set_bytes_per_pid_os=
        sorted(stats["peak_working_set_bytes_per_pid_os"].values()),
        peak_pagefile_bytes_per_pid_os=
        sorted(stats["peak_pagefile_bytes_per_pid_os"].values()))
    return record


def run(events: int, chars: int, repeats: int, interval: float):
    if events < 1 or chars < 0 or repeats < 1 or interval <= 0:
        raise ValueError("events/repeats must be positive; chars >= 0; poll > 0")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                  cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"],
                                         cwd=ROOT, text=True).strip())
    with tempfile.TemporaryDirectory(prefix="akuz_phase9_isolated_") as tmp:
        temp = Path(tmp)
        sources = temp / "synthetic_sources"
        snapshots = create_sources(sources, events, chars)
        batches = []
        for number in range(repeats):
            workspace = temp / ("run_" + str(number))
            fresh = memory_run(workspace, sources, interval)
            warm = memory_run(workspace, sources, interval)
            if not warm["reused"]:
                raise AssertionError("Warm build did not reuse reports")
            for key in ("inventory_sha256", "reports_sha256", "analytics_sql_sha256"):
                if warm[key] != fresh[key]:
                    raise AssertionError("Fresh/warm cache content differs: " + key)
            batches.append(dict(fresh=fresh, warm=warm))
        original = batches[-1]["fresh"]
        original_inventory = inventory_manifest(workspace)
        remove_report(workspace, "combined")
        combined_miss = memory_run(workspace, sources, interval)
        if combined_miss["reports_sha256"] != original["reports_sha256"]:
            current_inventory = inventory_manifest(workspace)
            changed = {key: [name for name, value in record["files"].items() if current_inventory["reports"].get(key, {}).get("files", {}).get(name) != value] for key, record in original_inventory["reports"].items() if current_inventory["reports"].get(key, {}).get("files") != record["files"]}
            raise AssertionError("Combined-only miss changed files: " + repr(changed))
        remove_report(workspace, "single")
        single_miss = memory_run(workspace, sources, interval)
        if single_miss["reports_sha256"] != original["reports_sha256"]:
            raise AssertionError("Single-only miss changed deterministic reports")
        import shutil
        duplicate = sources / "20260927_D.log"
        shutil.copyfile(sources / "20260924_A.log", duplicate)
        mixed = memory_run(workspace, sources, interval)
        if mixed["reports"] != 4 or mixed["events"] != original["events"] + events:
            raise AssertionError("Mixed cache lost distinct identical-byte source")
        return dict(phase="9.0", git_sha=sha, git_dirty=dirty,
            platform=platform.platform(), python=sys.version.split()[0],
            executable_kind="frozen" if getattr(sys, "frozen", False) else "python",
            cache_modes=["fresh-all", "warm-no-op", "combined-only-miss",
                         "single-only-miss", "mixed-cache-distinct-source"],
            repeats=repeats, source_snapshots=snapshots,
            duplicate_source_sha256=sha256(duplicate),
            memory_poll_ms=interval * 1000, results=batches,
            cache_scenarios=dict(combined_only_miss=combined_miss,
                                 single_only_miss=single_miss, mixed_cache=mixed),
            raw_payload_saved=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=60)
    parser.add_argument("--chars", type=int, default=768)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--poll-ms", type=float, default=20)
    parser.add_argument("--output", type=Path, help="Optional sanitized JSON, never raw events")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--source-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if (not args.workspace or not args.source_dir
                or args.source_dir.name != "synthetic_sources"
                or args.workspace.parent != args.source_dir.parent
                or not args.workspace.name.startswith("run_")
                or not args.workspace.parent.name.startswith("akuz_phase9_isolated_")):
            parser.error("Worker requires disposable phase9 synthetic workspace")
        print(json.dumps(build_worker(args.workspace, args.source_dir),
                         ensure_ascii=False), flush=True)
    else:
        result = run(args.events, args.chars, args.repeat, args.poll_ms / 1000)
        value = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(value, encoding="utf-8")
        print(value, end="")


if __name__ == "__main__":
    main()
