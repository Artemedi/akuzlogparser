"""Phase 10 bounded SSH compression smoke on one immutable/stable AKUZ source.

This is NOT the full Phase 10 A/B gate. It runs one control and one compressed
fetch of the same listed source, persists only numeric/digest-equality evidence,
and deletes fetched payloads with its owned TemporaryDirectory.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from time import perf_counter, process_time

from akuz_fetch import fetch_selected, list_remote, load_config
from akuz_store import sha256


def _trace_summary(root: Path) -> dict:
    path = root / "diagnostics" / "performance.txt"
    rows = path.read_text("utf-8").splitlines() if path.is_file() else []
    found = [row.split(" stage=", 1)[1] for row in rows
             if " stage=source.ssh.transfer status=summary " in row]
    if len(found) != 1:
        raise AssertionError("Expected exactly one SSH transfer summary")
    result = {}
    for token in found[0].split():
        if "=" in token:
            key, value = token.split("=", 1)
            result[key] = value
    return dict(
        bytes_received=int(result["bytes_received"]),
        bytes_expected=int(result["bytes_expected"]),
        transfer_elapsed_s=float(result["elapsed_s"]),
        payload_mib_s=float(result["mib_per_s"]),
    )


def _trial(base_cfg, selected: dict, compression: bool, owned_root: Path) -> dict:
    trial = owned_root / ("compressed" if compression else "control")
    trial.mkdir()
    cfg = replace(base_cfg, compression=compression,
                  local_dest=trial / "downloads")
    wall0, cpu0 = perf_counter(), process_time()
    path, digest, meta = fetch_selected(
        cfg, selected, notify=lambda message: None, trace_root=trial)
    wall_s = perf_counter() - wall0
    cpu_s = process_time() - cpu0
    if meta.get("active"):
        raise AssertionError("Phase 10 smoke requires a static source")
    if path.stat().st_size != meta["stored_bytes"]:
        raise AssertionError("Stored size mismatch")
    if sha256(path) != digest:
        raise AssertionError("Stored snapshot SHA mismatch")
    trace = _trace_summary(trial)
    if trace["bytes_received"] != path.stat().st_size:
        raise AssertionError("Trace byte count mismatch")
    return dict(
        mode="compressed" if compression else "control",
        wall_s=round(wall_s, 6),
        cpu_s=round(cpu_s, 6),
        bytes=path.stat().st_size,
        digest=digest,
        transfer=trace,
    )


def run(config_path: Path, app_root: Path, day: str) -> dict:
    cfg = load_config(config_path, app_root)
    listing = list_remote(cfg, notify=lambda message: None)
    matches = [row for row in listing if row["name"] == day + "_server.log"]
    if len(matches) != 1:
        raise AssertionError("Requested exact remote day missing or ambiguous")
    selected = matches[0]
    if selected.get("device") is None or selected.get("inode") is None:
        raise AssertionError("Server device/inode identity unavailable")
    with tempfile.TemporaryDirectory(prefix="akuz-phase10-compression-") as td:
        root = Path(td)
        control = _trial(cfg, selected, False, root)
        compressed = _trial(cfg, selected, True, root)
    if control["bytes"] != selected["size"] or compressed["bytes"] != selected["size"]:
        raise AssertionError("Source size changed during smoke")
    if control["digest"] != compressed["digest"]:
        raise AssertionError("Compression modes did not fetch identical source bytes")
    public = dict(
        status="SMOKE_ONLY_NOT_PHASE10_ACCEPTANCE",
        day=day,
        source_bytes=selected["size"],
        same_snapshot_sha=True,
        order=["control", "compressed"],
        control={k:v for k,v in control.items() if k != "digest"},
        compressed={k:v for k,v in compressed.items() if k != "digest"},
        raw_payload_saved=False,
        wire_bytes_measured=False,
        server_sshd_cpu_measured=False,
    )
    return public


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--day", default="20260923",
                        choices=("20260923","20260924","20260925"))
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    result = run(args.config, args.app_root, args.day)
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite Phase 10 smoke evidence")
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    print("PHASE10_SSH_COMPRESSION_SMOKE_PASS")
    print("DAY", result["day"], "BYTES", result["source_bytes"])
    for mode in ("control","compressed"):
        row=result[mode]
        print("MODE",mode,
              "wall_s",row["wall_s"],"cpu_s",row["cpu_s"],
              "transfer_s",row["transfer"]["transfer_elapsed_s"],
              "payload_mib_s",row["transfer"]["payload_mib_s"])
    print("SAME_SNAPSHOT_SHA=PASS")
    print("WIRE_BYTES_MEASURED=NO")
    print("SERVER_SSHD_CPU_MEASURED=NO")
    print("RAW_PAYLOAD_SAVED=NO")


if __name__ == "__main__":
    main()
