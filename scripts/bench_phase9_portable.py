"""Phase 9.0b: isolated Windows portable ZIP smoke with process-tree memory.

The EXISTING ZIP is extracted by smoke_portable.py into OS temp, never
executed in its source directory. No user config, reports or raw are copied.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import threading
from time import perf_counter
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from akuz_store import sha256
from scripts.phase9_memory import sample, tree_pids

EXPECTED = {"AKUZLogExplorer/" + name for name in
            ("AKUZLogExplorer.exe", "ConnectConf.cfg",
             "README_PORTABLE.md", "BUILD_INFO.json")}


def manifest(archive: Path):
    with zipfile.ZipFile(archive) as package:
        members = set(package.namelist())
        if members != EXPECTED:
            raise ValueError("Portable ZIP file allowlist mismatch")
        info = json.loads(package.read("AKUZLogExplorer/BUILD_INFO.json"))
        exe_digest = __import__("hashlib").sha256(
            package.read("AKUZLogExplorer/AKUZLogExplorer.exe")).hexdigest()
    return dict(version=info["version"], build_git_sha=info["commit"],
                build_python=info["python"],
                zip_sha256=sha256(archive), exe_sha256=exe_digest)


def probe(archive: Path, interval: float = .02):
    if sys.platform != "win32":
        raise RuntimeError("Windows-only portable memory probe")
    if not archive.is_file() or interval <= 0:
        raise ValueError("ZIP must exist and interval must be positive")
    package = manifest(archive)
    cmd = [sys.executable, str(ROOT / "scripts" / "smoke_portable.py"),
           str(archive.resolve())]
    started = perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8")
    stop = threading.Event()
    stats = dict(samples=0, child_samples=0, unreadable_samples=0,
        max_sampled_simultaneous_child_ws_bytes=0,
        max_sampled_simultaneous_child_private_bytes=0,
        child_os_peak_working_set_bytes={},
        child_os_peak_pagefile_bytes={})

    def poll():
        while True:
            try:
                child_ids = tree_pids(proc.pid) - {proc.pid}
                measured = [sample(pid) for pid in child_ids]
                valid = [(pid, row) for pid, row in zip(child_ids, measured) if row]
                stats["samples"] += 1
                stats["child_samples"] += len(valid)
                stats["unreadable_samples"] += len(child_ids) - len(valid)
                stats["max_sampled_simultaneous_child_ws_bytes"] = max(
                    stats["max_sampled_simultaneous_child_ws_bytes"],
                    sum(row["working_set_bytes"] for _, row in valid))
                stats["max_sampled_simultaneous_child_private_bytes"] = max(
                    stats["max_sampled_simultaneous_child_private_bytes"],
                    sum(row["private_bytes"] for _, row in valid))
                for pid, row in valid:
                    for field, counter in (
                        ("child_os_peak_working_set_bytes", "peak_working_set_bytes"),
                        ("child_os_peak_pagefile_bytes", "peak_pagefile_bytes")
                    ):
                        bucket = stats[field]
                        bucket[pid] = max(bucket.get(pid, 0), row[counter])
            except OSError:
                stats["unreadable_samples"] += 1
            if stop.wait(interval):
                break

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        stdout, stderr = proc.communicate()
    finally:
        stop.set()
        thread.join()
    if proc.returncode or not stdout.startswith("Portable ZIP passed:"):
        # Never embed local paths or the child's stderr in exported diagnostics.
        raise RuntimeError("Isolated portable smoke failed; run smoke_portable.py separately")
    if not stats["child_samples"]:
        raise RuntimeError("No frozen child process was observed: memory sample invalid")
    result = dict(phase="9.0b", mode="portable-smoke",
        os=platform.platform(), portable=package,
        sample_interval_ms=interval * 1000,
        wall_s=round(perf_counter() - started, 6),
        memory=dict((key, sorted(value.values()) if isinstance(value, dict) else value)
                    for key, value in stats.items()),
        excludes_python_parent=True, uses_disposable_extraction=True,
        raw_payload_saved=False)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("zip", type=Path, help="Existing diagnostic portable ZIP")
    p.add_argument("--poll-ms", type=float, default=20)
    p.add_argument("--output", type=Path, help="Optional sanitized JSON")
    args = p.parse_args()
    result = probe(args.zip, args.poll_ms / 1000)
    value = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(value, encoding="utf-8")
    print(value, end="")


if __name__ == "__main__":
    main()
