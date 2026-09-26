"""Phase 9.0: synthetic recognize_error branch/size profiling; no raw persisted."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from akuz_analytics import recognize_error

BRANCHES = ("no_match", "exception", "serial", "firstline")
BANDS = (("small", 128), ("medium", 4096), ("probe_limit", 24000),
         ("truncated", 64000))


def make_raw(branch: str, size: int):
    start = {
        "no_match": "12:00:00.000,AKUZ,req,user: synthetic normal\n",
        "exception": "12:00:00.000,AKUZ,req,user: System.InvalidOperationException: synthetic 1234\n",
        "serial": "12:00:00.000,AKUZ,req,user: ошибка сериализации synthetic\n",
        "firstline": "12:00:00.000,AKUZ,req,user: timed out synthetic\n",
    }[branch]
    raw = start + "X" * max(0, size - len(start))
    return raw


def profile(calls: int, repeats: int):
    if calls < 1 or repeats < 1:
        raise ValueError("calls and repeats must be positive")
    corpus = {(branch, band): make_raw(branch, size)
              for branch in BRANCHES for band, size in BANDS}
    output = {}
    for iteration in range(repeats):
        keys = list(corpus)
        if iteration % 2:
            keys.reverse()
        for branch, band in keys:
            raw = corpus[(branch, band)]
            stats = Counter()
            t0, c0 = perf_counter(), process_time()
            fingerprints = []
            for _ in range(calls):
                matched = recognize_error(raw, diagnostics=stats)
                fingerprints.append(matched["fp"] if matched else None)
            wall = perf_counter() - t0
            cpu = process_time() - c0
            if len(set(fingerprints)) != 1:
                raise AssertionError("Non-deterministic synthetic fingerprints")
            counters = {
                "no_match": "error_no_match_events",
                "exception": "error_exception_events",
                "serial": "error_serial_events",
                "firstline": "error_firstline_events",
            }
            if stats[counters[branch]] != calls:
                raise AssertionError("Synthetic branch routing changed: " + branch)
            key = branch + "/" + band
            output.setdefault(key, []).append(dict(
                wall_s=round(wall, 6), cpu_s=round(cpu, 6),
                raw_chars=len(raw), probe_chars=stats["error_probe_chars"] // calls,
                calls=stats["error_recognize_calls"],
                truncated=stats["error_truncated_events"],
                unicode_probe=calls - stats["error_ascii_probe_events"]))
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                  cwd=ROOT, text=True).strip()
    return dict(phase="9.0.error-branches", git_sha=sha, os=platform.platform(),
                python=sys.version.split()[0], repeats=repeats,
                calls_per_bucket=calls, records=output,
                raw_payload_saved=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--calls", type=int, default=600)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--output", type=Path, help="Sanitized metrics JSON")
    options = p.parse_args()
    result = profile(options.calls, options.repeat)
    value = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(value, encoding="utf-8")
    print(value, end="")


if __name__ == "__main__":
    main()
