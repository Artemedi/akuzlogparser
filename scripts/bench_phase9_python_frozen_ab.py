"""Private Phase 9 Python/frozen A/B: one SHA-gated fetch, six local builds.

Three trials per runtime, AB/BA/AB order on identical source bytes.
No raw payload is written to the result or sent outside DBA-008D.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.bench_phase9_baseline import canonical_hash
from scripts.bench_phase9_frozen_real import (
    DIAG, MARKER, cleanup_owned_workspace, frozen_build, frozen_snapshots,
    python_build_isolated,
)
from scripts.bench_phase9_local_ab import cleanup_trial

ORDER = ("python", "frozen", "frozen", "python", "python", "frozen")
OUTPUT = DIAG / "phase9_python_frozen_ab_private.json"


def validate_order(order):
    if tuple(order) != ORDER:
        raise ValueError("Runtime A/B requires fixed AB, BA, AB order")
def compare_signatures(reference, candidate):
    checks = {key: candidate.get(key) == value
              for key, value in reference.items()}
    if set(candidate) != set(reference) or not all(checks.values()):
        raise AssertionError("Python/frozen deterministic or semantic parity FAILED")
    return checks


def trial_series(home: Path, sources: Path, archive: Path, order=ORDER):
    validate_order(order)
    reference = None
    trials = []
    for index, runtime in enumerate(order, 1):
        root = home / f"trial_{index}_{runtime}"
        if runtime == "python":
            sig, metrics = python_build_isolated(root, sources)
        else:
            sig, metrics = frozen_build(root, sources, archive)
        if reference is None:
            reference = sig
        checks = compare_signatures(reference, sig)
        trials.append(dict(number=index, runtime=runtime,
                           signature_sha256=canonical_hash(sig),
                           single_events=sig["single_events"],
                           checks=checks, metrics=metrics))
        cleanup_trial(root, home, index, runtime)
        print("RUNTIME_TRIAL_PASS", index, runtime,
              metrics["wall_s"], metrics["cpu_s"], flush=True)
    summary = {}
    for runtime in ("python", "frozen"):
        group = [t["metrics"] for t in trials if t["runtime"] == runtime]
        summary[runtime] = dict(
            wall_median_s=median(x["wall_s"] for x in group),
            cpu_median_s=median(x["cpu_s"] for x in group),
            wall_s=[x["wall_s"] for x in group],
            cpu_s=[x["cpu_s"] for x in group],
            working_set_bytes=[x["memory"]["working_set_bytes"] for x in group],
            private_bytes=[x["memory"]["private_bytes"] for x in group])
    return dict(order=list(order), trials=trials, summary=summary,
                signature_sha256=canonical_hash(reference),
                single_events=reference["single_events"])


def git_head():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def require_clean_tree():
    if subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise RuntimeError("Commit runtime A/B instrumentation before real run")
def main():
    if os.name != "nt":
        raise SystemExit("Windows-only Python/frozen A/B benchmark")
    require_clean_tree()
    sha = git_head()
    archive = ROOT / "dist" / "AKUZLogExplorer-windows-x64.zip"
    from scripts.bench_phase9_portable import manifest
    package = manifest(archive)
    if package["build_git_sha"] != sha:
        raise RuntimeError("Rebuild diagnostic portable ZIP from current HEAD")
    DIAG.mkdir(exist_ok=True)
    if OUTPUT.exists():
        raise FileExistsError("Back up previous private runtime A/B result first")
    home = Path(tempfile.mkdtemp(prefix="phase9_frozen_", dir=DIAG))
    (home / MARKER).write_text("disposable\n", encoding="ascii")
    try:
        sources, snapshots = frozen_snapshots(home)
        print("RUNTIME_AB_SNAPSHOT_SHA_GATE_PASS", flush=True)
        results = trial_series(home, sources, archive)
        if results["single_events"] != 657738:
            raise AssertionError("Real event-count gate FAILED")
        if git_head() != sha:
            raise RuntimeError("Git HEAD changed during runtime A/B")
        require_clean_tree()
        record = dict(git_sha=sha, zip_sha256=package["zip_sha256"],
                      snapshots=snapshots, raw_payload_saved=False,
                      disposable_workspace_cleaned=True, **results)
    finally:
        cleanup_owned_workspace(home)
    OUTPUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print("RUNTIME_AB_PARITY_PASS", results["single_events"], flush=True)
    print("RUNTIME_AB_SUMMARY", results["summary"], flush=True)
    print("RUNTIME_AB_WORKSPACE_CLEANED", True, flush=True)


if __name__ == "__main__":
    main()
