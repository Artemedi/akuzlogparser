# Phase 11 — overlap SSH download and single-report generation

Status: **THREAD ONE-AHEAD REJECTED; PROCESS-ISOLATED PAIR CANDIDATE REPLICATED PASS; LARGE-SOURCE / PRODUCTION GATES OPEN**.

No normal application scheduler, cache schema, compression default, Phase 9
architecture, or GitHub Release is changed by this phase.

## Hypothesis

The current normal application processes uncached SSH sources sequentially:
download source A completely, generate A, then download B, generate B.
For a multi-source fresh build, some of the SSH wait for source B may be
hidden behind CPU/report-generation work for the already completed source A.

The proposed Phase 11 shape is deliberately narrow:

1. Fetch A to a **completed bounded local snapshot**.
2. Start at most one fetch B in a worker.
3. Parse/generate A on the main thread using only completed A.
4. Join B; only after B has fully returned may parse/generate B start.
5. Continue one source ahead. Never parse bytes still arriving over SSH.

This is not streaming an actively growing remote file into the parser.

## Isolated scheduler contract

Module: `scripts/phase11_overlap.py`.

`run_one_ahead()` owns one fetch worker maximum. The first fetch is
synchronous. It validates that every fetch returned an existing completed
snapshot associated with the exact expected item before the parser receives
it. The output order is the input order.

Synthetic contract tests in `tests/test_phase11_overlap.py` cover:

- fetch(B) really starts while parse(A) is still active;
- parser sees a completed final snapshot, never the caller's `.part`;
- fetch(B) failure preserves successful parse(A) and never starts C;
- parse(A) failure joins an already running fetch(B), never parses B/C;
- output order equals the serial reference;
- invalid snapshot contract fails closed;
- empty input is a no-op.

Exact `9cb36b4806d8c6880a3c353bb84dc500c3590e7a`:
[DBA-008D Actions #36388179105](https://github.com/Artemedi/akuzlogparser/actions/runs/36388179105)
SUCCESS — **219 Python tests, 2 Windows skips, 105.998 s**,
Node browser controls PASS, diff-check PASS.

Important limitation: Python `Future.cancel()` cannot forcibly abort a
fetch that has already started. The prototype therefore joins a running
fetch before returning a parse error. Immediate network cancellation would
need a cooperative cancellation contract in the SSH adapter before
production integration.

## Real SSH smoke harness contract

Harness: `scripts/bench_phase11_ssh_overlap.py`.

It discovers the real 23/24 Sep sources once, freezes each initial byte
bound + device/inode identity, and explicitly forces SSH compression OFF
to isolate Phase 11 from Phase 10. A custom fixed-prefix fetch:

- rechecks trusted server path and device/inode;
- requires current size >= the frozen bound;
- reads exactly `head -c bound`;
- computes SHA while writing an owned local snapshot;
- rechecks device/inode and non-truncation after transfer;
- deletes a partial local destination on failure.

Each completed snapshot is then passed to the existing
`akuz_html_explorer.generate()` for a real standalone single report.
Serial and overlap report directories are compared through deterministic
file manifests and source SHA equality. Only boolean equality is retained
in result evidence; source SHA and per-file report hashes are stripped.
Memory uses sampled process working-set/private bytes during each mode.

Exact harness-contract commit
`6eba8d455798a236caf21d89afe4e0388d21ca82`:
[DBA-008D Actions #36388731847](https://github.com/Artemedi/akuzlogparser/actions/runs/36388731847)
SUCCESS — **223 Python tests, 2 Windows skips, 106.233 s**,
Node controls PASS, diff-check PASS.

## First real smoke

Workflow:
[AKUZ Phase 11 real SSH overlap smoke](https://github.com/Artemedi/akuzlogparser/actions/workflows/akuz-phase11-ssh-overlap-smoke.yml).

Scope is only 23+24 Sep, one serial observation followed by one overlap
observation. All snapshots/reports live under an owned temporary E:-volume
diagnostics workspace and must be deleted before success. The private JSON
may retain numeric timings/memory, but raw log/report payloads are not an
artifact and are not uploaded.

Because mode order is serial then overlap and there is only one observation
per mode, a successful smoke is **not acceptance evidence**. It may justify
a balanced replicated benchmark; it cannot justify production scheduler
changes by itself.

## Real 23+24 smoke result

[Actions #36389149340](https://github.com/Artemedi/akuzlogparser/actions/runs/36389149340)
completed SUCCESS using exact workflow commit `83fb9b9`. Independent
numeric/private-JSON audit
[Actions #36389588713](https://github.com/Artemedi/akuzlogparser/actions/runs/36389588713)
also completed SUCCESS.

| Metric | Serial | One-ahead overlap |
|---|---:|---:|
| Whole pair wall, s | 89.588491 | 87.336109 |
| Process CPU, s | 56.578125 | 56.125000 |
| Sampled peak private, B | 328,351,744 | 384,077,824 |
| Sampled peak working set, B | 349,724,672 | 405,282,816 |
| Workspace bytes | 735,118,485 | 735,118,482 |

Wall improved only about **2.5%** while sampled peak private bytes increased
about **17.0%**.

Stage detail explains the weak result:

| Mode / day | Fetch, s | Generate wall, s | Generate CPU, s |
|---|---:|---:|---:|
| serial 23 | 18.500365 | 24.739814 | 24.671875 |
| serial 24 | 16.139880 | 23.781949 | 23.562500 |
| overlap 23 | 22.274164 | 26.092944 | 26.328125 |
| overlap 24 | 38.221289 | 23.563355 | 23.375000 |

The second fetch, which is the operation meant to be hidden by parse(A),
became **more than twice as slow** under concurrent generation (16.14 s
serial versus 38.22 s in the overlap observation). Parse(A) also slowed.
That resource contention consumed most of the theoretical overlap benefit.

Correctness/privacy gates PASS: identical fixed source SHA between modes,
identical deterministic report manifests, 211,117 + 221,082 events and
297,803 + 293,882 physical lines respectively, zero unreadable memory
samples, owned workspace deleted, no raw payload retained, no app cache
mutation.

This is still one serial then one overlap observation. It does not prove
the 2.5% gain is stable. The next step is a balanced 2x2 order
`serial, overlap, overlap, serial` on the same fixed 23+24 prefixes.
Do **not** escalate to the 644.6 MB source or production integration unless
that replication shows a material and repeatable net benefit after memory
cost.

## Balanced 2x2 real replication result

Balanced workflow:
[Actions #36394213273](https://github.com/Artemedi/akuzlogparser/actions/runs/36394213273),
exact benchmark workflow commit `e065dfa`.
Independent numeric/private-JSON audit:
[Actions #36394295913](https://github.com/Artemedi/akuzlogparser/actions/runs/36394295913),
exact audit workflow commit `49223dd`.
Both completed SUCCESS.

Order was `serial, overlap, overlap, serial` against the same fixed
23+24 Sep source prefixes with SSH compression forced OFF. Source SHA and
deterministic report manifests matched in all four trials; workspace cleanup
PASS; no raw payload persisted and normal app cache/Release were untouched.

| Mode | Wall trials, s | Median wall, s | Median CPU, s | Median private, B |
|---|---|---:|---:|---:|
| serial | 121.787921, 100.138628 | 110.963274 | 58.781250 | 393,189,376 |
| overlap | 136.705397, 125.393428 | 131.049413 | 59.226562 | 357,048,320 |

The balanced result **reverses the smoke's apparent 2.5% wall gain**:
one-ahead thread overlap is about **18.1% slower by median wall** than
serial for this pair. Process CPU differs by only about +0.8% for overlap.
Private-memory direction was not stable versus the original smoke: the
balanced median is about 9.2% lower for overlap, whereas the smoke had
shown about 17% higher private bytes. Therefore memory is not used as the
causal explanation.

The strongest repeatable contention signal is the second fetch:

| Mode | 24-Sep fetch values, s | Mean, s |
|---|---|---:|
| serial | 30.498877, 18.470101 | 24.484489 |
| overlap | 37.418625, 51.568935 | 44.493780 |

The overlap second-fetch mean is about **81.7% slower** in these two
replicated trials. First 23-Sep parse mean also increased modestly from
25.860110 s serial to 26.691686 s overlap (~3.2%). This is consistent with
resource contention when a Python-thread SSH fetch runs concurrently with
CPU-heavy report generation, but the benchmark does **not** isolate GIL
versus CPU, local disk writes, encryption, or scheduler effects. That causal
split remains a hypothesis.

### Decision for the current candidate

The current `ThreadPoolExecutor(max_workers=1)` one-ahead implementation
is **REJECTED as a production candidate** on the measured DBA-008D workload.
Correctness is good, but replicated wall time regressed materially.

Per the previously documented gate, the same thread-based approach is
**not escalated to the 644.6 MB 25-Sep source** and is not integrated into
`perform_build`. A larger run would add load without first resolving the
observed contention.

A future Phase 11 sub-experiment may test isolation of the fetch path from
the CPU-heavy parser (for example a separate process or another bounded
architecture), but it must be a new isolated candidate with its own
snapshot ownership, cancellation, memory and byte-parity contracts. It
must not be presented as an accepted fix for the cause before measurement.

## Process-isolated pair replication

After the threaded candidate regressed, a separate benchmark-only candidate
moved only the next SSH fetch into a Windows spawned Python process while
report generation remained in the parent. It did **not** modify
`perform_build`, inventory, cache schema, compression default or Release.

Implementation/contract commits:
- `df5f762` — process-isolated benchmark with simultaneous process-tree
  memory sampling and parent+child CPU accounting;
- `93b71b2` — five contract tests. Exact DBA-008D regression
  [#36395600470](https://github.com/Artemedi/akuzlogparser/actions/runs/36395600470):
  **231 Python tests PASS, 2 Windows skips, 117.566 s**; Node controls and
  diff-check PASS.

Balanced real workflow:
[Actions #36396123108](https://github.com/Artemedi/akuzlogparser/actions/runs/36396123108),
exact workflow commit `54c305e`. Independent numeric audit:
[Actions #36396165254](https://github.com/Artemedi/akuzlogparser/actions/runs/36396165254),
exact audit commit `95bc620`. Both SUCCESS.

Order was `serial, process, process, serial` against the same fixed 23+24
Sep prefixes, compression forced OFF. Source SHA and deterministic report
manifests matched across all four trials; workspace cleanup PASS; no raw
payload persisted; app cache and Release unchanged. Audit additionally
required process trials to sample at least two processes and non-zero child
CPU.

| Mode | Wall trials, s | Median wall, s | Median total CPU, s | Median process-tree private, B |
|---|---|---:|---:|---:|
| serial | 113.946302, 101.149178 | 107.547740 | 58.726562 | 367,253,504 |
| process | 78.452962, 83.730728 | 81.091845 | 65.875000 | 328,448,000 |

Both process trials were faster than both serial trials. Median wall improved
about **24.6%**, while total measured CPU increased about **12.2%**.
Process-tree private-memory median was about 10.6% lower in this series, but
that direction should not be generalized from two trials.

### Instrumentation correction before large-source escalation

The first process benchmark stored `fetch["20260924"]` as elapsed time from
`Process.start()` until the parent consumed the IPC result **after parse(A)**.
If the child fetch had already completed, that value included parent-side
waiting and was therefore a **snapshot-readiness latency**, not pure child
fetch wall time. The 30.071016 / 29.367467 s values must not be compared to
serial 19.356464 / 18.350266 s as evidence that the fetch itself slowed by
57.6%.

This does **not** invalidate pair wall, total CPU, process-tree memory,
source-SHA parity, report-manifest parity or cleanup above. It invalidates
only the causal statement based on the process candidate's old per-fetch
wall field.

Commits `be7b332` + `57e482f` separate:
- child-measured pure fetch wall;
- parent spawn-to-readiness latency;
and add regression coverage. A corrected pair rerun is required before the
25-Sep large-source/multi-source escalation.

First 23-Sep parse mean in the original run rose from 25.657197 s to
26.271071 s (~2.4%), which remains valid because parse timing is measured
inside the parent generate call.

Child OS CPU evidence was 3.906250 / 3.875000 s; parent CPU was
61.578125 / 62.390625 s for the two process trials. Thus the candidate's
CPU figure is not a parent-only undercount.

### Decision for the process candidate

The process-isolated pair wall result is **promising**, but corrected per-fetch instrumentation must pass a repeated 23+24 pair gate before any large-source/multi-source escalation. It is not production acceptance.
The next experiment should include the current 25-Sep ~644.6 MB fixed
prefix in a realistic one-ahead sequence and retain balanced serial/process
ordering, process-tree RSS/private sampling, full source/report parity and
cleanup. It must also expose spawn/cancellation behavior and avoid carrying
raw payloads outside owned temporary workspaces.

No normal application integration is authorized yet.

## Corrected process-isolation pair rerun

The fetch-timing correction in `be7b332` + `57e482f` was regression-tested
on DBA-008D before another real run:
[Actions #36397172147](https://github.com/Artemedi/akuzlogparser/actions/runs/36397172147),
exact `57e482f8670e8ba8d4220ece6712e563b07e140a`:
**231 Python tests PASS, 2 Windows skips, 109.684 s**, Node controls and
`git diff --check` PASS.

The corrected real benchmark used a new evidence file and did not overwrite
the earlier experiment:
[Actions #36403656948](https://github.com/Artemedi/akuzlogparser/actions/runs/36403656948),
exact workflow commit `27ef63f`.
Independent read-only audit:
[Actions #36403687747](https://github.com/Artemedi/akuzlogparser/actions/runs/36403687747),
exact audit commit `c1e8512`. Both completed SUCCESS.

Order remained `serial, process, process, serial` on the same fixed 23+24
source prefixes, compression OFF. All four trials retained source-SHA and
deterministic report-manifest parity; workspace cleanup PASS; raw payload was
not retained; app cache and Release were not changed.

| Mode | Wall trials, s | Median wall, s | Median total CPU, s | Median process-tree private, B |
|---|---|---:|---:|---:|
| serial | 100.777862, 101.672542 | 101.225202 | 61.351562 | 367,276,032 |
| process | 88.732724, 92.426783 | 90.579754 | 70.390625 | 318,515,200 |

The process candidate is **~10.5% faster by median wall** in this corrected
replication; both process trials are faster than both serial trials. Total
parent+child CPU is **~14.7% higher**. The process-tree private-memory median
is ~13.3% lower in this particular run, but two samples per mode are not
enough to generalize memory direction.

Most importantly, corrected timing separates the next source's child fetch
from parent readiness:

| Metric for 24-Sep next source | Process trial 1, s | Process trial 2, s |
|---|---:|---:|
| pure child fetch wall | 19.953724 | 18.679295 |
| spawn-to-parent-readiness latency | 32.732658 | 31.004642 |

Serial 24-Sep fetch values were 16.589666 / 22.345354 s. Therefore the
corrected evidence **does not show the previously claimed 57.6% fetch
slowdown**. The pure fetch is broadly in the same range as serial; readiness
is later because parse(A) intentionally overlaps it. The old per-fetch causal
claim remains retracted.

### Corrected decision

The process-isolated candidate remains benchmark-only, but the corrected
pair gate confirms a repeatable wall benefit sufficient to proceed to the
already defined **23+24+25 large-source/multi-source experiment**. That next
gate must keep one child maximum, fixed-prefix source identity, full
source/report parity, process-tree memory + parent/child CPU, separate pure
fetch/readiness timing, cleanup and compression OFF. It still does not
authorize `perform_build` integration.

## Three-source 23+24+25 large gate

The corrected process-isolated candidate was extended to exactly three real
sources, still benchmark-only and with **one spawned fetch child maximum**.
Contract commit `89daa2a5c6c3f859729b2d70d71314509efbfbbf` passed
[DBA-008D regression #36406054018](https://github.com/Artemedi/akuzlogparser/actions/runs/36406054018):
**234 Python tests PASS, 2 Windows skips, 116.309 s**, Node browser controls
and diff-check PASS.

Real balanced workflow:
[Actions #36406239299](https://github.com/Artemedi/akuzlogparser/actions/runs/36406239299),
workflow commit `dd6f219`. Independent read-only audit:
[Actions #36406284302](https://github.com/Artemedi/akuzlogparser/actions/runs/36406284302),
audit commit `9ad19ac`. Both completed SUCCESS.

Order was again `serial, process, process, serial`, compression forced OFF.
Fixed prefixes were 171,378,567 + 140,361,291 + 644,567,384 bytes.
All four trials used equal source SHA per fixed prefix and equal deterministic
report manifests; workspace cleanup PASS; no raw payload retained; normal app
cache and Release untouched.

| Mode | Wall trials, s | Median wall, s | Median total CPU, s | Median process-tree private, B |
|---|---|---:|---:|---:|
| serial | 259.258158, 280.110036 | 269.684097 | 138.671875 | 399,509,504 |
| process | 216.670384, 215.096836 | 215.883610 | 162.539062 | 356,929,536 |

The three-source process candidate is **~19.9% faster by median wall** while
measured parent+children CPU is **~17.2% higher**. Both process trials are
faster than both serial trials. Process-tree private median is ~10.7% lower
in this run; as with the pair gate, that memory direction is not generalized
from two observations.

Corrected child timing remains separated from parent readiness:

| Next source | Serial fetch, s | Process pure child fetch, s | Process readiness, s |
|---|---|---|---|
| 24 Sep | 18.345334, 23.470378 | 32.010912, 20.856221 | 32.511390, 29.630600 |
| 25 Sep | 87.478401, 104.360589 | 89.025576, 77.720484 | 89.551881, 78.004371 |

The large-source evidence therefore confirms that one-child process
isolation can hide useful SSH transfer time on this DBA-008D workload
without changing source bytes or generated reports. It does **not** prove
normal-app cache/failure semantics.

### v4.7 release decision

The process candidate is **accepted as a performance research result but
deferred from the v4.7 runtime**. v4.7 keeps the already validated sequential
fetch scheduler + ephemeral Phase 9 derived spool. This avoids introducing a
new multiprocessing/cancellation/cache interaction immediately before the
portable release.

Future integration may resume from this exact evidence, but must first pass
the remaining normal-app fault/mixed-cache/inventory/restart/portable gates
below. The positive benchmark is not silently promoted into `perform_build`.

## Production gates still open

Before integrating one-ahead fetch into `perform_build`:

- balanced replicated real measurements, including the 25-Sep large source;
- process memory and transient disk amplification within limits;
- cooperative cancellation or an explicitly accepted bounded wait policy;
- network failure / remote rotation / source truncation while next fetch is
  in flight;
- inventory transaction interaction and cache rollback;
- mixed fresh/warm source cases (do not refetch a ready source);
- exact single+combined report and analytics semantics;
- interruption/restart and portable Windows validation.

No production integration is authorized by the smoke alone.
