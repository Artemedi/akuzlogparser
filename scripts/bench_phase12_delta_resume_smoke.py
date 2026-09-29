"""Phase 12 strict delta/resume proof-cost smoke.

Experimental read-only benchmark only. It does not modify application cache,
reports, source files, runtime defaults or releases. Remote data is written only
inside one owned TemporaryDirectory and is deleted on exit. Stdout never prints
host, remote path, inode, credentials, payload or SHA values.

The strict candidate intentionally performs full remote prefix SHA-256 proofs.
This establishes a correctness/performance baseline; it is not a production
design and is expected to be expensive on the server.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_delta import RemoteMeta, assemble_delta_contract
from akuz_fetch import (_listing, _remote_metadata, load_config,
                        sudo_prefix)


_SHA_LINE = re.compile(rb"^([0-9a-fA-F]{64})\s+-\s*$")


class CountingSocket:
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
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            timeout=15,
            auth_timeout=25,
            banner_timeout=20,
            look_for_keys=True,
            allow_agent=True,
            compress=False,
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


def _trusted_source(client, cfg, day: str):
    rows = [
        row for row in _listing(client, cfg)
        if row["name"] == day + "_server.log"
    ]
    if len(rows) != 1:
        raise AssertionError("Requested remote day missing or ambiguous")
    row = rows[0]
    if row.get("device") is None or row.get("inode") is None:
        raise AssertionError("Remote device/inode unavailable")
    return row


def _metadata(client, cfg, quoted: str):
    value, error = _remote_metadata(client, cfg, quoted)
    use_sudo = False
    if value is None:
        use_sudo = True
        value, error = _remote_metadata(client, cfg, quoted, True)
    if value is None:
        raise AssertionError("Remote stat unavailable")
    return value, use_sudo


def _remote_prefix_sha(client, cfg, quoted: str, size: int, use_sudo: bool):
    command = (
        sudo_prefix(use_sudo, bool(cfg.sudo_password))
        + f"head -c {size} -- "
        + quoted
        + " | sha256sum"
    )
    wall0 = perf_counter()
    cpu0 = process_time()
    stdin, stdout, stderr = client.exec_command(
        command, timeout=600, get_pty=False)
    if use_sudo and cfg.sudo_password:
        stdin.write(cfg.sudo_password + "\n")
        stdin.flush()
    stdin.channel.shutdown_write()
    data = stdout.read(4096)
    error = stderr.read(32768)
    status = stdout.channel.recv_exit_status()
    wall_s = perf_counter() - wall0
    cpu_s = process_time() - cpu0
    match = _SHA_LINE.fullmatch(data.strip())
    if status != 0 or error.strip() or match is None:
        raise AssertionError("Remote bounded prefix SHA failed")
    return match.group(1).decode("ascii").lower(), wall_s, cpu_s


def _transfer_range(client, cfg, quoted: str, start: int, length: int,
                    use_sudo: bool, target: Path):
    if start < 0 or length <= 0:
        raise AssertionError("Invalid transfer range")
    if start == 0:
        source = (
            sudo_prefix(use_sudo, bool(cfg.sudo_password))
            + f"head -c {length} -- "
            + quoted
        )
    else:
        source = (
            sudo_prefix(use_sudo, bool(cfg.sudo_password))
            + f"tail -c +{start + 1} -- "
            + quoted
            + f" | head -c {length}"
        )

    wall0 = perf_counter()
    cpu0 = process_time()
    stdin, stdout, stderr = client.exec_command(
        source, timeout=600, get_pty=False)
    if use_sudo and cfg.sudo_password:
        stdin.write(cfg.sudo_password + "\n")
        stdin.flush()
    stdin.channel.shutdown_write()
    digest = hashlib.sha256()
    copied = 0
    with target.open("xb") as stream:
        while True:
            block = stdout.read(256 * 1024)
            if not block:
                break
            copied += len(block)
            if copied > length:
                raise AssertionError("Remote range exceeded fixed bound")
            digest.update(block)
            stream.write(block)
    error = stderr.read(32768)
    status = stdout.channel.recv_exit_status()
    wall_s = perf_counter() - wall0
    cpu_s = process_time() - cpu0
    if status != 0 or copied != length or error.strip():
        raise AssertionError("Remote range transfer incomplete")
    return digest.hexdigest(), copied, wall_s, cpu_s


def _setup_previous(cfg, day: str, previous_bytes: int, workspace: Path):
    client, _ = _connect_counted(cfg)
    try:
        selected = _trusted_source(client, cfg, day)
        quoted = shlex.quote(selected["path"])
        before, use_sudo = _metadata(client, cfg, quoted)
        if before[:2] != (selected["device"], selected["inode"]):
            raise AssertionError("Remote identity changed before setup")
        if previous_bytes <= 0 or previous_bytes >= before[2]:
            raise AssertionError("previous-bytes must be inside current source")
        target = workspace / "previous.log"
        digest, copied, _, _ = _transfer_range(
            client, cfg, quoted, 0, previous_bytes, use_sudo, target)
        after, _ = _remote_metadata(client, cfg, quoted, use_sudo)
        if after is None or after[:2] != before[:2] or after[2] < before[2]:
            raise AssertionError("Remote source changed unsafely during setup")
        return selected, target, digest, before[0], before[1]
    finally:
        client.close()


def _full_trial(cfg, day: str, expected_identity, workspace: Path):
    client, counted = _connect_counted(cfg)
    try:
        selected = _trusted_source(client, cfg, day)
        quoted = shlex.quote(selected["path"])
        before, use_sudo = _metadata(client, cfg, quoted)
        if before[:2] != expected_identity:
            raise AssertionError("Remote identity changed before full trial")
        bound = before[2]
        rx0, tx0 = counted.rx_bytes, counted.tx_bytes
        target = workspace / "full.log"
        wall0, cpu0 = perf_counter(), process_time()
        digest, copied, transfer_wall_s, transfer_cpu_s = _transfer_range(
            client, cfg, quoted, 0, bound, use_sudo, target)
        after, _ = _remote_metadata(client, cfg, quoted, use_sudo)
        wall_s, cpu_s = perf_counter() - wall0, process_time() - cpu0
        if after is None or after[:2] != before[:2] or after[2] != bound:
            raise AssertionError("Fixed source changed during full trial")
        return dict(
            mode="full",
            bound_bytes=bound,
            logical_transfer_bytes=copied,
            snapshot_sha256=digest,
            wall_s=round(wall_s, 6),
            client_cpu_s=round(cpu_s, 6),
            transfer_wall_s=round(transfer_wall_s, 6),
            transfer_client_cpu_s=round(transfer_cpu_s, 6),
            socket_rx_bytes=counted.rx_bytes-rx0,
            socket_tx_bytes=counted.tx_bytes-tx0,
        )
    finally:
        client.close()


def _delta_trial(cfg, day: str, previous: Path, previous_sha: str,
                 previous_bytes: int, expected_identity, workspace: Path):
    client, counted = _connect_counted(cfg)
    try:
        selected = _trusted_source(client, cfg, day)
        quoted = shlex.quote(selected["path"])
        before_raw, use_sudo = _metadata(client, cfg, quoted)
        if before_raw[:2] != expected_identity:
            raise AssertionError("Remote identity changed before delta trial")
        bound = before_raw[2]
        if bound <= previous_bytes:
            raise AssertionError("No remote append remains for delta trial")

        rx0, tx0 = counted.rx_bytes, counted.tx_bytes
        wall0, cpu0 = perf_counter(), process_time()

        old_remote_sha, old_sha_wall, old_sha_cpu = _remote_prefix_sha(
            client, cfg, quoted, previous_bytes, use_sudo)
        if old_remote_sha != previous_sha:
            raise AssertionError("Saved remote prefix differs from previous snapshot")

        delta_path = workspace / "delta.bin"
        delta_sha, copied, transfer_wall, transfer_cpu = _transfer_range(
            client, cfg, quoted, previous_bytes, bound-previous_bytes,
            use_sudo, delta_path)

        transfer_after, _ = _remote_metadata(client, cfg, quoted, use_sudo)
        if (transfer_after is None or transfer_after[:2] != before_raw[:2]
                or transfer_after[2] != bound):
            raise AssertionError("Fixed source changed during delta transfer")

        new_remote_sha, new_sha_wall, new_sha_cpu = _remote_prefix_sha(
            client, cfg, quoted, bound, use_sudo)

        # The identity check must bracket the FINAL remote prefix proof too.
        # Otherwise a pathname rotation between stat_after and sha256sum could
        # bind proof bytes to a different inode.
        after_raw, _ = _remote_metadata(client, cfg, quoted, use_sudo)
        if after_raw is None or after_raw[:2] != before_raw[:2] or after_raw[2] != bound:
            raise AssertionError("Fixed source changed during final prefix proof")

        final = workspace / "delta_full.log"
        assemble0, assemble_cpu0 = perf_counter(), process_time()
        published_size, published_sha = assemble_delta_contract(
            previous,
            previous_sha256=previous_sha,
            previous_device=expected_identity[0],
            previous_inode=expected_identity[1],
            before=RemoteMeta(*before_raw),
            remote_previous_prefix_sha256=old_remote_sha,
            delta=delta_path,
            transfer_bound=bound,
            publish_size=bound,
            after=RemoteMeta(*after_raw),
            remote_published_prefix_sha256=new_remote_sha,
            final=final,
        )
        assemble_wall = perf_counter()-assemble0
        assemble_cpu = process_time()-assemble_cpu0
        wall_s, cpu_s = perf_counter()-wall0, process_time()-cpu0
        if published_size != bound or published_sha != new_remote_sha:
            raise AssertionError("Delta publication proof mismatch")
        return dict(
            mode="strict_delta",
            bound_bytes=bound,
            previous_bytes=previous_bytes,
            logical_transfer_bytes=copied,
            delta_sha256=delta_sha,
            snapshot_sha256=published_sha,
            wall_s=round(wall_s, 6),
            client_cpu_s=round(cpu_s, 6),
            old_prefix_sha_wall_s=round(old_sha_wall, 6),
            old_prefix_sha_client_cpu_s=round(old_sha_cpu, 6),
            transfer_wall_s=round(transfer_wall, 6),
            transfer_client_cpu_s=round(transfer_cpu, 6),
            new_prefix_sha_wall_s=round(new_sha_wall, 6),
            new_prefix_sha_client_cpu_s=round(new_sha_cpu, 6),
            assemble_wall_s=round(assemble_wall, 6),
            assemble_client_cpu_s=round(assemble_cpu, 6),
            socket_rx_bytes=counted.rx_bytes-rx0,
            socket_tx_bytes=counted.tx_bytes-tx0,
        )
    finally:
        client.close()


def run(config_path: Path, app_root: Path, day: str, previous_bytes: int):
    cfg = load_config(config_path, app_root)
    # Force compression off so Phase 12 transfer saving is not conflated with
    # the independent Phase 10 opt-in.
    from dataclasses import replace
    cfg = replace(cfg, compression=False)

    with TemporaryDirectory(prefix="akuz-phase12-delta-") as temp:
        workspace = Path(temp)
        selected, previous, previous_sha, device, inode = _setup_previous(
            cfg, day, previous_bytes, workspace)
        expected_identity = (device, inode)

        full_dir = workspace / "full_trial"
        delta_dir = workspace / "delta_trial"
        full_dir.mkdir()
        delta_dir.mkdir()

        full = _full_trial(cfg, day, expected_identity, full_dir)
        delta = _delta_trial(
            cfg, day, previous, previous_sha, previous_bytes,
            expected_identity, delta_dir)

        if full["bound_bytes"] != delta["bound_bytes"]:
            raise AssertionError("Full and delta trials used different bounds")
        if full["snapshot_sha256"] != delta["snapshot_sha256"]:
            raise AssertionError("Full and strict-delta snapshot bytes differ")
        if full["bound_bytes"] != selected["size"]:
            # The first listing is part of evidence; abort rather than silently
            # comparing different source versions.
            raise AssertionError("Remote size changed after setup listing")

        result = dict(
            status="PHASE12_STRICT_DELTA_SMOKE",
            day=day,
            fixed_prefix_bytes=full["bound_bytes"],
            previous_bytes=previous_bytes,
            delta_bytes=full["bound_bytes"]-previous_bytes,
            full=full,
            strict_delta=delta,
            byte_equivalence=True,
            source_identity_stable=True,
            compression=False,
            raw_payload_retained=False,
            app_cache_changed=False,
            release_changed=False,
            server_sha_cpu_measured=False,
            replicated=False,
        )
    return result


def _public_row(row):
    allowed = (
        "mode", "bound_bytes", "previous_bytes", "logical_transfer_bytes",
        "wall_s", "client_cpu_s", "old_prefix_sha_wall_s",
        "transfer_wall_s", "new_prefix_sha_wall_s", "assemble_wall_s",
        "socket_rx_bytes", "socket_tx_bytes",
    )
    return {key: row[key] for key in allowed if key in row}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--day", default="20260925")
    parser.add_argument("--previous-bytes", type=int, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(
            args.config, args.app_root, args.day, args.previous_bytes)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite Phase 12 evidence")
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("PHASE12_STRICT_DELTA_SMOKE=PASS")
        print("FULL", json.dumps(_public_row(result["full"]), sort_keys=True))
        print("STRICT_DELTA",
              json.dumps(_public_row(result["strict_delta"]), sort_keys=True))
        print("BYTE_EQUIVALENCE=PASS")
        print("SOURCE_IDENTITY_STABLE=PASS")
        print("COMPRESSION_FORCED_OFF=PASS")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("NO_HOST_PATH_INODE_DIGEST_OR_PAYLOAD_PRINTED=PASS")
        print("APP_CACHE_CHANGED=NO")
        print("RELEASE_CHANGED=NO")
        print("REPLICATED=NO")
        print("SERVER_SHA_CPU_MEASURED=NO")
    except BaseException as exc:
        print("PHASE12_STRICT_DELTA_SMOKE=FAILED",
              type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
