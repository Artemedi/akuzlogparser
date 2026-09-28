"""Phase 10 replicated SSH compression A/B with fixed-prefix SHA evidence.

Experimental benchmark only. It never changes application defaults, never writes
the fetched log payload, and never publishes source path/host/inode/SHA to stdout.
Socket RX counts are SSH/TCP payload bytes seen by Paramiko's client socket
(excluding IP/TCP headers), not pcap wire bytes.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import statistics
import sys
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import (_listing, _remote_metadata, load_config,
                        sudo_prefix)


ORDER = ("control", "compressed", "compressed",
         "control", "control", "compressed")


class CountingSocket:
    """Socket proxy counting bytes crossing the Paramiko client boundary."""
    def __init__(self, raw):
        self.raw = raw
        self.rx_bytes = 0
        self.tx_bytes = 0

    def recv(self, size, *args, **kwargs):
        data = self.raw.recv(size, *args, **kwargs)
        self.rx_bytes += len(data)
        return data

    def recv_into(self, buffer, *args, **kwargs):
        count = self.raw.recv_into(buffer, *args, **kwargs)
        self.rx_bytes += count
        return count

    def send(self, data, *args, **kwargs):
        count = self.raw.send(data, *args, **kwargs)
        self.tx_bytes += count
        return count

    def sendall(self, data, *args, **kwargs):
        self.raw.sendall(data, *args, **kwargs)
        self.tx_bytes += len(data)

    def __getattr__(self, name):
        return getattr(self.raw, name)


def _connect_counted(cfg):
    import paramiko
    raw = socket.create_connection((cfg.host, cfg.port), timeout=15)
    counted = CountingSocket(raw)
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        known_hosts = Path.home() / ".ssh" / "known_hosts"
        if known_hosts.is_file():
            client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        kwargs = dict(
            hostname=cfg.host, port=cfg.port, username=cfg.username,
            timeout=15, auth_timeout=25, banner_timeout=20,
            look_for_keys=True, allow_agent=True, compress=cfg.compression,
            sock=counted,
        )
        if cfg.password:
            kwargs["password"] = cfg.password
        if cfg.key_file:
            kwargs["key_filename"] = str(
                Path(os.path.expanduser(cfg.key_file)).expanduser())
        client.connect(**kwargs)
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(30)
        return client, counted
    except Exception:
        client.close()
        counted.close()
        raise


def _parse_server_cpu(stderr: bytes) -> tuple[float, int]:
    text = stderr.decode("utf-8", "replace")
    match = re.search(
        r"AKUZ_PHASE10_SSHD_CPU\s+(-?\d+)\s+(-?\d+)\s+(\d+)",
        text)
    if not match:
        raise AssertionError("Server sshd CPU marker unavailable")
    before, after, hz = map(int, match.groups())
    if before < 0 or after < before or hz <= 0:
        raise AssertionError("Invalid server sshd CPU counters")
    return (after - before) / hz, after - before


def _trial(cfg, selected: dict, bound: int, compression: bool) -> dict:
    cfg = replace(cfg, compression=compression)
    client, counted = _connect_counted(cfg)
    try:
        current = next(
            (row for row in _listing(client, cfg)
             if row["path"] == selected["path"]), None)
        if current is None:
            raise AssertionError("Selected remote source disappeared")
        if (current.get("device"), current.get("inode")) != (
                selected.get("device"), selected.get("inode")):
            raise AssertionError("Selected remote source rotated before trial")
        quoted = shlex.quote(current["path"])
        before, error = _remote_metadata(client, cfg, quoted)
        use_sudo = False
        if before is None:
            use_sudo = True
            before, error = _remote_metadata(client, cfg, quoted, True)
        if before is None:
            raise AssertionError("Remote stat unavailable")
        if before[:2] != (selected["device"], selected["inode"]):
            raise AssertionError("Remote identity changed before transfer")
        if before[2] < bound:
            raise AssertionError("Remote file became shorter than fixed prefix")

        head = (sudo_prefix(use_sudo, bool(cfg.sudo_password))
                + f"head -c {bound} -- " + quoted)
        command = (
            "p=$PPID; "
            "hz=$(getconf CLK_TCK 2>/dev/null || echo 100); "
            "b=$(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null || echo -1); "
            + head +
            "; rc=$?; "
            "a=$(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null || echo -1); "
            "printf '\\nAKUZ_PHASE10_SSHD_CPU %s %s %s\\n' "
            "\"$b\" \"$a\" \"$hz\" >&2; exit $rc"
        )
        rx0, tx0 = counted.rx_bytes, counted.tx_bytes
        wall0, cpu0 = perf_counter(), process_time()
        stdin, stdout, stderr = client.exec_command(
            command, timeout=300, get_pty=False)
        if use_sudo and cfg.sudo_password:
            stdin.write(cfg.sudo_password + "\n")
            stdin.flush()
        stdin.channel.shutdown_write()
        digest = hashlib.sha256()
        logical = 0
        while True:
            block = stdout.read(256 * 1024)
            if not block:
                break
            logical += len(block)
            if logical > bound:
                raise AssertionError("Server sent beyond fixed prefix")
            digest.update(block)
        err = stderr.read(65536)
        status = stdout.channel.recv_exit_status()
        wall_s = perf_counter() - wall0
        cpu_s = process_time() - cpu0
        rx = counted.rx_bytes - rx0
        tx = counted.tx_bytes - tx0
        if status != 0 or logical != bound:
            raise AssertionError("SSH fixed-prefix transfer incomplete")
        server_cpu_s, server_ticks = _parse_server_cpu(err)

        after, _ = _remote_metadata(client, cfg, quoted, use_sudo)
        if after is None or after[:2] != before[:2] or after[2] < bound:
            raise AssertionError("Remote source rotated/truncated after transfer")
        return dict(
            mode="compressed" if compression else "control",
            logical_bytes=logical,
            snapshot_sha256=digest.hexdigest(),
            wall_s=round(wall_s, 6),
            client_cpu_s=round(cpu_s, 6),
            socket_rx_bytes=rx,
            socket_tx_bytes=tx,
            socket_rx_ratio=round(rx / logical, 6),
            server_sshd_cpu_s=round(server_cpu_s, 6),
            server_sshd_cpu_ticks=server_ticks,
            size_before=before[2],
            size_after=after[2],
        )
    finally:
        client.close()


def _summary(rows: list[dict], mode: str) -> dict:
    subset = [row for row in rows if row["mode"] == mode]
    if len(subset) != 3:
        raise AssertionError("Expected exactly three trials per mode")
    def med(name):
        return statistics.median(row[name] for row in subset)
    return dict(
        trials=3,
        wall_median_s=round(med("wall_s"), 6),
        client_cpu_median_s=round(med("client_cpu_s"), 6),
        server_sshd_cpu_median_s=round(med("server_sshd_cpu_s"), 6),
        socket_rx_median_bytes=int(med("socket_rx_bytes")),
        socket_rx_ratio_median=round(med("socket_rx_ratio"), 6),
        wall_values_s=[row["wall_s"] for row in subset],
    )


def run(config_path: Path, app_root: Path, day: str) -> dict:
    cfg = load_config(config_path, app_root)
    # Discovery uses the application's existing strict host-key behaviour.
    from akuz_fetch import list_remote
    listing = list_remote(cfg, notify=lambda message: None)
    matches = [row for row in listing if row["name"] == day + "_server.log"]
    if len(matches) != 1:
        raise AssertionError("Requested remote day missing or ambiguous")
    selected = matches[0]
    if selected.get("device") is None or selected.get("inode") is None:
        raise AssertionError("Server device/inode unavailable")
    bound = selected["size"]
    rows = []
    reference_sha = None
    for mode in ORDER:
        row = _trial(cfg, selected, bound, mode == "compressed")
        if reference_sha is None:
            reference_sha = row["snapshot_sha256"]
        elif row["snapshot_sha256"] != reference_sha:
            raise AssertionError("Fixed remote prefix SHA changed across A/B")
        rows.append(row)
    final_size = max(row["size_after"] for row in rows)
    return dict(
        status="REPLICATED_AB_PARTIAL_PHASE10",
        day=day,
        fixed_prefix_bytes=bound,
        snapshot_sha256=reference_sha,
        order=list(ORDER),
        trials=rows,
        summary={
            "control": _summary(rows, "control"),
            "compressed": _summary(rows, "compressed"),
        },
        source_grew_during_trials=final_size > bound,
        final_observed_size=final_size,
        raw_payload_saved=False,
        socket_bytes_scope="client TCP socket payload incl SSH protocol, excl IP/TCP headers",
        server_cpu_scope="parent sshd user+system ticks around fixed-prefix command",
        server_page_cache_controlled=False,
        cold_cache_claim=False,
        app_runtime_changed=False,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--day", default="20260923",
                        choices=("20260923","20260924","20260925"))
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.config, args.app_root, args.day)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite Phase 10 A/B evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PHASE10_SSH_COMPRESSION_AB_PASS")
        print("DAY", result["day"], "FIXED_PREFIX_BYTES", result["fixed_prefix_bytes"])
        for mode in ("control","compressed"):
            row=result["summary"][mode]
            print("SUMMARY",mode,
                  "wall_median_s",row["wall_median_s"],
                  "client_cpu_median_s",row["client_cpu_median_s"],
                  "server_sshd_cpu_median_s",row["server_sshd_cpu_median_s"],
                  "socket_rx_median_bytes",row["socket_rx_median_bytes"],
                  "socket_rx_ratio_median",row["socket_rx_ratio_median"])
        print("SAME_FIXED_PREFIX_SHA_6_OF_6=PASS")
        print("SERVER_PAGE_CACHE_CONTROLLED=NO")
        print("RAW_PAYLOAD_SAVED=NO")
    except BaseException as exc:
        print("PHASE10_SSH_COMPRESSION_AB_FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
