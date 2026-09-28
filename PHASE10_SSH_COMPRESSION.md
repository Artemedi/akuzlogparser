# Phase 10 — SSH compression A/B

Status: **REPLICATED 3x3×3 EVIDENCE PASS; DEFAULT REMAINS OFF; OPT-IN BENEFIT DOCUMENTED**.

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

Three replicated fixed-prefix series completed SUCCESS:

- 23 Sep: [Actions #36384569840](https://github.com/Artemedi/akuzlogparser/actions/runs/36384569840), 171,378,567 B;
- 24 Sep: [Actions #36387434198](https://github.com/Artemedi/akuzlogparser/actions/runs/36387434198), 140,361,291 B;
- 25 Sep: [Actions #36384825633](https://github.com/Artemedi/akuzlogparser/actions/runs/36384825633), 644,567,384 B.

Each series ran 3 control + 3 compressed transfers in the same balanced
order and produced the same fixed-prefix SHA in all 6/6 trials. A separate
read-only evidence audit over all three private JSON files completed PASS:
[Actions #36387735730](https://github.com/Artemedi/akuzlogparser/actions/runs/36387735730).
No source path, host, inode, SHA or raw payload was printed by the audit.

| Date | Mode | Median wall, s | Median client CPU, s | Median parent sshd CPU, s | Median socket RX, B | RX / logical |
|---|---|---:|---:|---:|---:|---:|
| 23 Sep | control | 19.617381 | 4.281250 | 0.730 | 171,726,336 | 1.002029 |
| 23 Sep | compressed | 5.681805 | 2.125000 | 4.100 | 42,752,000 | 0.249459 |
| 24 Sep | control | 14.628869 | 3.296875 | 0.630 | 140,646,080 | 1.002029 |
| 24 Sep | compressed | 4.735882 | 1.781250 | 3.280 | 39,288,928 | 0.279913 |
| 25 Sep | control | 66.505320 | 16.281250 | 2.720 | 645,866,512 | 1.002015 |
| 25 Sep | compressed | 19.265598 | 8.312500 | 16.590 | 149,470,912 | 0.231894 |

Across the three date-level medians, compressed transfer reduced wall by
67.6–71.0% and socket RX by 72.1–76.9%. Client process CPU also fell by
46.0–50.4%. The cost moved to the server-side SSH process: measured parent
`sshd` CPU increased by about 5.2–6.1x, reaching 16.59 CPU seconds during
the 19.27-second median compressed transfer of the 644.6 MB prefix.

The three sources did not grow during their respective six-trial series.
Server page cache remained natural/uncontrolled in every run.

## Acceptance boundary

Even a successful replicated run is still **partial Phase 10** because the
production server page cache is not forcibly dropped or controlled. Doing
so on an application server would be operationally intrusive and is not
authorized. Therefore results must be described as alternating repeated
trials under natural cache state, not “cold-cache” measurements.

### Phase 10 decision

The replicated evidence is strong enough to establish that SSH compression
is a real transport optimization on these AKUZ text logs, not a one-off
smoke result. It is **not** strong enough to justify enabling compression by
default on an application server whose production CPU headroom was not
measured. The observed compressed transfers consumed roughly 69–86% of one
CPU core in the measured parent `sshd` process while active.

Therefore Phase 10 closes with:

- keep `compression=false` as the default;
- retain `compression=true` as an explicit opt-in for transfer-bound
  environments with measured server CPU headroom;
- use `source.ssh.transfer` diagnostics and server load when deciding;
- do not claim cold-cache performance: production page cache was not dropped;
- do not rerun remote payload benchmarks merely to obtain a synthetic
  cold-cache condition on the application server.

No Phase 10 experiment changes report bytes, parser semantics, cache schema,
Tee/B-lite choice or Release artifacts.
