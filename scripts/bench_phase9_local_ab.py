"""Private Phase 9 local A/B: one SHA-gated SSH fetch, six isolated builds.

Compare explicit no-spool and default derived-spool paths on identical bytes.
No source payload is written to the result or sent outside DBA-008D.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from statistics import median
import subprocess
import sys
import tempfile
from time import sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.bench_phase9_baseline import canonical_hash, disk_bytes
from scripts.bench_phase9_frozen_real import (
    DIAG, MARKER, cleanup_owned_workspace, frozen_snapshots,
    python_build_isolated,
)

ORDER = ("control", "spool", "spool", "control", "control", "spool")
OUTPUT = DIAG / "phase9_local_ab_private.json"


def validate_order(order):
    """Three trials per variant, alternating pair order AB/BA/AB."""
    if tuple(order) != ORDER:
        raise ValueError("A/B requires fixed AB, BA, AB trial order")


def compare_signatures(reference, candidate):
    checks = {key: candidate.get(key) == value
              for key, value in reference.items()}
    if set(candidate) != set(reference) or not all(checks.values()):
        raise AssertionError("A/B deterministic or semantic parity FAILED")
    return checks


def cleanup_trial(root: Path, home: Path, index: int, mode: str):
    """Remove ONLY the just-completed owned trial, bounding Windows unlock retries."""
    if (root.parent.resolve() != home.resolve() or
        root.name != f"trial_{index}_{mode}" or root.is_symlink() or
        not (home / MARKER).is_file() or
        (home / MARKER).read_text(encoding="ascii") != "disposable\n"):
        raise RuntimeError("Refusing unowned A/B trial cleanup")
    for attempt in range(30):
        try:
            shutil.rmtree(root)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32) or attempt == 29:
                raise
            sleep(.5)


def trial_series(home: Path, sources: Path, order=ORDER):
    validate_order(order)
    reference = None
    trials = []
    for index, mode in enumerate(order):
        number = index + 1
        root = home / f"trial_{number}_{mode}"
        sig, metrics = python_build_isolated(
            root, sources, use_derived_spool=(mode == "spool"))
        if reference is None:
            reference = sig
        checks = compare_signatures(reference, sig)
        metrics["disk_bytes"] = {name: disk_bytes(root, name)
                                 for name in ("reports", "cache", "data")}
        trials.append(dict(number=number, mode=mode,
                           signature_sha256=canonical_hash(sig),
                           single_events=sig["single_events"],
                           checks=checks, metrics=metrics))
        cleanup_trial(root, home, number, mode)
        print("AB_TRIAL_PASS", number, mode,
              metrics["wall_s"], metrics["cpu_s"], flush=True)
    summary = {}
    for mode in ("control", "spool"):
        group = [t["metrics"] for t in trials if t["mode"] == mode]
        summary[mode] = dict(
            wall_median_s=median(x["wall_s"] for x in group),
            cpu_median_s=median(x["cpu_s"] for x in group),
            wall_s=[x["wall_s"] for x in group],
            cpu_s=[x["cpu_s"] for x in group])
    return dict(order=list(order), trials=trials, summary=summary,
                signature_sha256=canonical_hash(reference),
                single_events=reference["single_events"])


def git_head():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def require_clean_tree():
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise RuntimeError("Commit benchmark instrumentation before real A/B")


def main():
    if os.name != "nt":
        raise SystemExit("Windows-only process-tree A/B benchmark")
    require_clean_tree()
    sha = git_head()
    DIAG.mkdir(exist_ok=True)
    if OUTPUT.exists():
        raise FileExistsError("Back up previous private A/B result first")
    home = Path(tempfile.mkdtemp(prefix="phase9_frozen_", dir=DIAG))
    (home / MARKER).write_text("disposable\n", encoding="ascii")
    try:
        sources, snapshots = frozen_snapshots(home)
        print("AB_SNAPSHOT_SHA_GATE_PASS", flush=True)
        results = trial_series(home, sources)
        if results["single_events"] != 657738:
            raise AssertionError("Real event-count gate FAILED")
        if git_head() != sha:
            raise RuntimeError("Git HEAD changed during A/B")
        require_clean_tree()
        record = dict(git_sha=sha, snapshots=snapshots,
                      raw_payload_saved=False,
                      disposable_workspace_cleaned=True, **results)
    finally:
        cleanup_owned_workspace(home)
    OUTPUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print("AB_PARITY_PASS", results["single_events"], flush=True)
    print("AB_SUMMARY", results["summary"], flush=True)
    print("AB_WORKSPACE_CLEANED", True, flush=True)


if __name__ == "__main__":
    main()
