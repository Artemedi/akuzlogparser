"""Phase 11 real SSH pair smoke: serial fetch+generate vs one-ahead overlap.

Benchmark only. It uses two stable remote AKUZ prefixes, writes all fetched bytes
and generated reports under an owned temporary workspace, compares source SHA and
report manifests between modes, then deletes the workspace. It never changes app
cache, inventory, compression default, or GitHub Release.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import threading
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import (_connect, _listing, _remote_metadata, load_config,
                        sudo_prefix)
from akuz_html_explorer import generate
from scripts.bench_phase9_baseline import canonical_hash, file_manifest
from scripts.phase11_overlap import CompletedSnapshot, run_one_ahead, run_serial


DAYS = ("20260923", "20260924")


@dataclass(frozen=True)
class SourceSpec:
    day: str
    name: str
    path: str
    bound: int
    device: int
    inode: int

    @property
    def base_date(self) -> date:
        return date(int(self.day[:4]), int(self.day[4:6]), int(self.day[6:]))


def _discover(cfg) -> tuple[SourceSpec, ...]:
    from akuz_fetch import list_remote
    listing = list_remote(cfg, notify=lambda message: None)
    result = []
    for day in DAYS:
        matches = [row for row in listing if row["name"] == day + "_server.log"]
        if len(matches) != 1:
            raise AssertionError("Phase 11 source missing or ambiguous")
        row = matches[0]
        if row.get("device") is None or row.get("inode") is None:
            raise AssertionError("Phase 11 remote device/inode unavailable")
        result.append(SourceSpec(
            day=day, name=row["name"], path=row["path"], bound=row["size"],
            device=row["device"], inode=row["inode"]))
    return tuple(result)


def _fetch_fixed(cfg, spec: SourceSpec, destination: Path) -> CompletedSnapshot:
    """Fetch exactly the originally discovered prefix into one owned file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("Phase 11 snapshot destination already exists")
    client = _connect(cfg, notify=lambda message: None)
    try:
        current = next((row for row in _listing(client, cfg)
                        if row["path"] == spec.path), None)
        if current is None:
            raise AssertionError("Phase 11 remote source disappeared")
        if (current.get("device"), current.get("inode")) != (
                spec.device, spec.inode):
            raise AssertionError("Phase 11 remote source rotated before fetch")
        quoted = shlex.quote(spec.path)
        before, error = _remote_metadata(client, cfg, quoted)
        use_sudo = False
        if before is None:
            use_sudo = True
            before, error = _remote_metadata(client, cfg, quoted, True)
        if before is None:
            raise AssertionError("Phase 11 remote stat unavailable")
        if before[:2] != (spec.device, spec.inode):
            raise AssertionError("Phase 11 remote identity changed before fetch")
        if before[2] < spec.bound:
            raise AssertionError("Phase 11 remote source truncated before fetch")

        command = (sudo_prefix(use_sudo, bool(cfg.sudo_password))
                   + f"head -c {spec.bound} -- " + quoted)
        stdin, stdout, stderr = client.exec_command(
            command, timeout=300, get_pty=False)
        if use_sudo and cfg.sudo_password:
            stdin.write(cfg.sudo_password + "\n")
            stdin.flush()
        stdin.channel.shutdown_write()
        digest = hashlib.sha256()
        copied = 0
        try:
            with destination.open("xb") as target:
                while True:
                    block = stdout.read(256 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    if copied > spec.bound:
                        raise AssertionError("Phase 11 server sent beyond fixed prefix")
                    digest.update(block)
                    target.write(block)
            error_text = stderr.read(65536)
            status = stdout.channel.recv_exit_status()
            if status != 0 or copied != spec.bound:
                raise AssertionError("Phase 11 fixed-prefix fetch incomplete")
            after, _ = _remote_metadata(client, cfg, quoted, use_sudo)
            if after is None or after[:2] != before[:2] or after[2] < spec.bound:
                raise AssertionError("Phase 11 source rotated/truncated after fetch")
            return CompletedSnapshot(
                item=spec, path=destination,
                digest=digest.hexdigest(), bytes=copied)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    finally:
        client.close()


def _report_bytes(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*")
               if path.is_file())


class _MemorySampler:
    def __init__(self, interval=.05):
        self.interval = interval
        self.stop = threading.Event()
        self.samples = 0
        self.unreadable = 0
        self.peak_ws = 0
        self.peak_private = 0
        self.thread = None

    def __enter__(self):
        from scripts.phase9_memory import sample
        pid = os.getpid()
        def poll():
            while not self.stop.wait(self.interval):
                row = sample(pid)
                self.samples += 1
                if row is None:
                    self.unreadable += 1
                    continue
                self.peak_ws = max(self.peak_ws, row["working_set_bytes"])
                self.peak_private = max(self.peak_private, row["private_bytes"])
        self.thread = threading.Thread(
            target=poll, name="akuz-phase11-memory", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Phase 11 memory sampler did not stop")


def _run_mode(cfg, specs: tuple[SourceSpec, ...], root: Path,
              *, overlap: bool) -> dict:
    snapshots = root / "snapshots"
    reports = root / "reports"
    fetch_metrics = {}
    parse_metrics = {}
    source_sha = {}

    def fetch(spec: SourceSpec):
        started = perf_counter()
        snap = _fetch_fixed(cfg, spec, snapshots / spec.name)
        fetch_metrics[spec.day] = round(perf_counter() - started, 6)
        source_sha[spec.day] = snap.digest
        return snap

    def parse(snap: CompletedSnapshot):
        spec = snap.item
        out = reports / spec.day
        started = perf_counter()
        cpu0 = process_time()
        meta = generate(snap.path, out, spec.base_date, 1000, 35)
        parse_metrics[spec.day] = dict(
            wall_s=round(perf_counter() - started, 6),
            process_cpu_s=round(process_time() - cpu0, 6),
            events=meta["events"], physical_lines=meta["physical_lines"],
            report_bytes=_report_bytes(out),
            report_manifest_sha256=canonical_hash(file_manifest(out)))
        return dict(day=spec.day,
                    events=meta["events"],
                    physical_lines=meta["physical_lines"],
                    report_manifest_sha256=parse_metrics[spec.day][
                        "report_manifest_sha256"])

    cpu0 = process_time()
    with _MemorySampler() as memory:
        result = (run_one_ahead(specs, fetch, parse)
                  if overlap else run_serial(specs, fetch, parse))
    return dict(
        mode="overlap" if overlap else "serial",
        wall_s=round(result.wall_s, 6),
        process_cpu_s=round(process_time() - cpu0, 6),
        fetched=result.fetched, parsed=result.parsed,
        outputs=list(result.outputs),
        fetch=fetch_metrics, parse=parse_metrics,
        source_sha=source_sha,
        sampled_peak_ws_bytes=memory.peak_ws,
        sampled_peak_private_bytes=memory.peak_private,
        memory_samples=memory.samples,
        unreadable_memory_samples=memory.unreadable,
        workspace_bytes=sum(path.stat().st_size for path in root.rglob("*")
                            if path.is_file()))


def run(config_path: Path, app_root: Path, diagnostics: Path) -> dict:
    cfg = replace(load_config(config_path, app_root), compression=False)
    specs = _discover(cfg)
    workspace = None
    try:
        with tempfile.TemporaryDirectory(
                prefix="phase11_ssh_overlap_", dir=diagnostics) as td:
            workspace = Path(td)
            serial = _run_mode(cfg, specs, workspace / "serial", overlap=False)
            overlap = _run_mode(cfg, specs, workspace / "overlap", overlap=True)

            same_sources = serial["source_sha"] == overlap["source_sha"]
            serial_outputs = {x["day"]: x for x in serial["outputs"]}
            overlap_outputs = {x["day"]: x for x in overlap["outputs"]}
            report_parity = serial_outputs == overlap_outputs
            if not same_sources:
                raise AssertionError("Phase 11 source SHA differs between modes")
            if not report_parity:
                raise AssertionError("Phase 11 report manifest differs between modes")
            if any(row["memory_samples"] < 10 or
                   row["unreadable_memory_samples"] != 0
                   for row in (serial, overlap)):
                raise AssertionError("Phase 11 memory sampling incomplete")

            # Do not persist content hashes; retain only equality result.
            for row in (serial, overlap):
                row.pop("source_sha", None)
                for output in row["outputs"]:
                    output.pop("report_manifest_sha256", None)
                for parsed in row["parse"].values():
                    parsed.pop("report_manifest_sha256", None)
            result = dict(
                status="PHASE11_REAL_PAIR_SMOKE_NOT_ACCEPTANCE",
                days=list(DAYS),
                fixed_prefix_bytes={spec.day: spec.bound for spec in specs},
                compression=False,
                serial=serial, overlap=overlap,
                source_sha_equal=True, report_manifest_equal=True,
                order_bias="serial_then_overlap_single_observation",
                raw_payload_saved=False,
                app_cache_changed=False,
                release_changed=False)
        result["workspace_cleaned"] = workspace is not None and not workspace.exists()
        if not result["workspace_cleaned"]:
            raise AssertionError("Phase 11 workspace cleanup failed")
        return result
    finally:
        # TemporaryDirectory performs authoritative recursive cleanup.
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
            raise FileExistsError("Refusing to overwrite Phase 11 evidence")
        result = run(args.config, args.app_root, args.diagnostics)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print("PHASE11_SSH_OVERLAP_SMOKE_PASS")
        print("SERIAL_WALL_S", result["serial"]["wall_s"])
        print("OVERLAP_WALL_S", result["overlap"]["wall_s"])
        print("SERIAL_CPU_S", result["serial"]["process_cpu_s"])
        print("OVERLAP_CPU_S", result["overlap"]["process_cpu_s"])
        print("SERIAL_PEAK_PRIVATE", result["serial"]["sampled_peak_private_bytes"])
        print("OVERLAP_PEAK_PRIVATE", result["overlap"]["sampled_peak_private_bytes"])
        print("SOURCE_SHA_EQUAL=PASS")
        print("REPORT_MANIFEST_EQUAL=PASS")
        print("WORKSPACE_CLEANED=PASS")
        print("RAW_PAYLOAD_SAVED=NO")
        print("APP_CACHE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE11_SSH_OVERLAP_SMOKE_FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
