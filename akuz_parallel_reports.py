"""Phase 15 isolated single-report generation for the normal application.

Workers only read immutable snapshots and write app-owned temporary staging
directories plus optional derived spools. Inventory, publication intents,
final report directories, analytics and user-visible state remain parent-only.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date
import hashlib
import multiprocessing
import os
from pathlib import Path
from time import perf_counter, process_time

from akuz_derived_spool import SpoolWriter
from akuz_html_explorer import generate
from akuz_store import sha256
from akuz_win_job_spawn import get_job_bound_spawn_context


@dataclass(frozen=True)
class StagedReportJob:
    token: str
    raw_path: str
    base_iso: str
    stage_dir: str
    spool_path: str = ""


def _stage_manifest(folder: Path) -> dict[str, tuple[str, int]]:
    """Hash exact staged bytes without following symlinks outside the stage."""
    resolved = folder.resolve()
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink inside Phase 15 staging")
        if not path.is_file():
            continue
        path.resolve().relative_to(resolved)
        h = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
        result[path.relative_to(folder).as_posix()] = (h.hexdigest(), size)
    return result


def generate_staged_report(job: StagedReportJob) -> dict:
    """Spawn-safe worker. Never touches inventory or final report directories."""
    raw = Path(job.raw_path)
    stage = Path(job.stage_dir)
    if stage.exists() or stage.is_symlink():
        raise FileExistsError("Phase 15 staging already exists")
    if not raw.is_file():
        raise FileNotFoundError("Phase 15 source snapshot missing")
    stage.parent.mkdir(parents=True, exist_ok=True)

    wall0, cpu0 = perf_counter(), process_time()
    spool = None
    if job.spool_path:
        spool_path = Path(job.spool_path)
        spool_path.parent.mkdir(parents=True, exist_ok=True)
        with SpoolWriter(spool_path) as sink:
            meta = generate(
                raw, stage,
                date.fromisoformat(job.base_iso) if job.base_iso else None,
                1000, 35, derived_sink=sink)
            if sink.count != meta["events"]:
                raise ValueError("Phase 15 derived spool event count mismatch")
        spool = {
            "path": str(spool_path),
            "count": sink.count,
            "sha256": sink.sha256,
            "bytes": sink.bytes_written,
        }
    else:
        meta = generate(
            raw, stage,
            date.fromisoformat(job.base_iso) if job.base_iso else None,
            1000, 35)

    output_hashes = meta.pop("_output_hashes", None)
    if not isinstance(output_hashes, dict):
        raise ValueError("Phase 15 built-in generator hashes missing")
    return {
        "token": job.token,
        "events": meta["events"],
        "physical_lines": meta["physical_lines"],
        "chunks": meta.get("chunks", 0),
        "wall_s": perf_counter() - wall0,
        "cpu_s": process_time() - cpu0,
        "output_hashes": output_hashes,
        "manifest": _stage_manifest(stage),
        "spool": spool,
    }


def validate_staged_report(job: StagedReportJob, result: dict) -> None:
    stage = Path(job.stage_dir)
    if result.get("token") != job.token:
        raise ValueError("Phase 15 worker returned wrong token")
    if stage.is_symlink() or not stage.is_dir():
        raise ValueError("Phase 15 staging directory missing")
    manifest = _stage_manifest(stage)
    if manifest != result.get("manifest"):
        raise ValueError("Phase 15 staging changed after worker completion")
    output_hashes = result.get("output_hashes")
    if not isinstance(output_hashes, dict) or set(output_hashes) != set(manifest):
        raise ValueError("Phase 15 producer hash path mismatch")
    for name, pair in output_hashes.items():
        if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                or tuple(pair) != manifest[name]):
            raise ValueError("Phase 15 producer hash mismatch")
    if "index.html" not in manifest or "data/catalog.js" not in manifest:
        raise ValueError("Phase 15 required report files missing")

    spool = result.get("spool")
    if job.spool_path:
        if not isinstance(spool, dict) or spool.get("path") != job.spool_path:
            raise ValueError("Phase 15 derived spool metadata missing")
        spool_path = Path(job.spool_path)
        if (not spool_path.is_file() or spool_path.is_symlink()
                or spool.get("sha256") != sha256(spool_path)):
            raise ValueError("Phase 15 derived spool integrity mismatch")
        if spool.get("count") != result.get("events"):
            raise ValueError("Phase 15 derived spool count mismatch")
    elif spool is not None:
        raise ValueError("Unexpected Phase 15 derived spool")


def run_staged_reports(jobs: list[StagedReportJob], *,
                       max_workers: int = 2) -> list[dict]:
    if not jobs:
        return []
    if max_workers != 2:
        raise ValueError("Phase 15 production scope requires exactly two workers")
    ctx = (get_job_bound_spawn_context()
           if os.name == "nt" else multiprocessing.get_context("spawn"))
    with ProcessPoolExecutor(
            max_workers=max_workers, mp_context=ctx) as pool:
        futures = [pool.submit(generate_staged_report, job) for job in jobs]
        try:
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise
