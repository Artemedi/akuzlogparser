# Phase 10 — SSH compression A/B

Status: **BOUNDED SMOKE PASS; REPLICATED 3x3 A/B IN PROGRESS; NOT ACCEPTED FOR DEFAULT ENABLEMENT**.

No application default or GitHub Release is changed by this phase.

## Why this phase exists

The SSH adapter already has an opt-in `compression=true` setting. Phase 10
asks whether SSH compression provides a reproducible end-to-end transfer
benefit for AKUZ text logs without unacceptable client/server CPU cost or
snapshot-identity risk.

All Phase 10 experiments retain the existing source-safety rules:
trusted server inventory, fixed device/inode identity, bounded reads,
client SHA of transferred bytes, no remote temporary files and no server
mutation.

## Bounded smoke — 23 September source

Harness: `scripts/bench_phase10_ssh_compression_smoke.py`.
Windows DBA-008D run:
[Actions #36383788889](https://github.com/Artemedi/akuzlogparser/actions/runs/36383788889).

The source was the stable 2026-09-23 file, 171,378,567 bytes. One control
and one compressed fetch produced the same client SHA. Fetched payloads
were kept only in an owned temporary directory and removed at the end.

| Mode | Whole fetch wall, s | Client CPU, s | transfer elapsed, s | Logical payload MiB/s |
|---|---:|---:|---:|---:|
| control | 36.148708 | 4.218750 | 34.546 | 4.731 |
| compressed | 7.181173 | 2.562500 | 5.475 | 29.853 |

This is a **single observation per mode**. Compression improved logical
payload throughput by roughly 6.3x in this smoke, but this cannot be used
as Phase 10 acceptance because:

- actual socket-layer compressed bytes were not measured;
- server `sshd` CPU was not measured;
- server filesystem page cache was not controlled;
- only one run per mode was taken.

The first smoke workflow revision `d71b60f` failed before SSH because the
new script was directly invoked from `scripts/` without adding repository
root to `sys.path` (`ModuleNotFoundError: akuz_fetch`). Commit `bfd8721`
fixed only direct script invocation. Exact `bfd8721` then passed
207 Python tests (2 Windows skips), Node controls and diff-check. The
successful smoke is exact workflow commit `ff2604d`.

## Replicated A/B instrumentation

`scripts/bench_phase10_ssh_compression_ab.py` adds:

- fixed byte prefix chosen once from trusted remote inventory;
- balanced order:
  `control, compressed, compressed, control, control, compressed`;
- SHA-256 comparison of all six logical payloads;
- client wall + process CPU;
- socket RX/TX byte accounting at the Paramiko socket boundary;
- parent `sshd` user+system CPU ticks measured around the fixed-prefix
  `head -c` command;
- device/inode and non-truncation checks before/after every transfer;
- no payload persistence and no source path/host/inode/SHA in stdout.

Socket byte counts represent bytes received by the client socket including
SSH protocol/encryption overhead, excluding IP/TCP headers. They are not a
packet capture.

Contract tests were added in
`tests/test_phase10_ssh_compression_ab.py`. Exact `ab45694` passed
212 Python tests (2 Windows skips), Node controls and diff-check on
DBA-008D:
[Actions #36384234697](https://github.com/Artemedi/akuzlogparser/actions/runs/36384234697).

The first replicated run,
[Actions #36384569840](https://github.com/Artemedi/akuzlogparser/actions/runs/36384569840),
completed SUCCESS against the fixed 23-Sep prefix (171,378,567 B). All 6/6
transfers produced the same client SHA.

| Mode | Median wall, s | Median client CPU, s | Median parent sshd CPU, s | Median socket RX, B | RX / logical |
|---|---:|---:|---:|---:|---:|
| control | 19.617381 | 4.281250 | 0.730 | 171,726,336 | 1.002029 |
| compressed | 5.681805 | 2.125000 | 4.100 | 42,752,000 | 0.249459 |

On this repeated 23-Sep workload, compression reduced median wall by about
71.0% and client socket RX by about 75.1% (roughly 4.0x fewer received
socket bytes), while measured parent-`sshd` CPU increased from 0.73 s to
4.10 s (about 5.6x). The client process CPU median was lower in the compressed
runs, but this should not be generalized before the larger-file series.

This remains partial Phase 10: server page cache was natural/uncontrolled.
The next required large-source series uses the current 25-Sep fixed prefix
of 644,567,384 B.

## Acceptance boundary

Even a successful replicated run is still **partial Phase 10** because the
production server page cache is not forcibly dropped or controlled. Doing
so on an application server would be operationally intrusive and is not
authorized. Therefore results must be described as alternating repeated
trials under natural cache state, not “cold-cache” measurements.

Compression remains disabled by default until replicated evidence is
reviewed. No Phase 10 experiment changes report bytes, parser semantics,
cache schema, Tee/B-lite choice or Release artifacts.
