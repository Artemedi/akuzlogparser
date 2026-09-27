# Phase 9.4 — equal-input synthetic architecture comparison

Status: **BOUNDED STANDALONE FRESH-ALL EVIDENCE, NOT OWNER SELECTION**.
Based on main `b011589ac58cd0069efaf8bc8e537fb05bf60e7d`.
Standalone code only (`scripts/bench_phase9_architecture_synthetic.py`):
normal generator, bounded Tee, B-lite JSONL with verified replay and
fallback helper. No application cache/inventory/index, SSH, real logs
or user report was modified; experiments are OFF in normal execution.

## Exact comparison method

One fixed synthetic source set with 3000/3000/3001 events,
UTF-8 BOM, mixed CRLF and replacement-byte fixture. All three modes
use chunk_size=1000, top=35 and the SAME sorted source identity.
Input SHA checked before/after every trial. Each fresh candidate
runs in its own Python process / owned temporary directory;
order: control, Tee, B-lite; B-lite, control, Tee; Tee, B-lite,
control (3 per variant). No SSH time in the measurement, no
raw payload saved in result. Cross-variant output manifests of all
three single reports and combined compared byte-for-byte on ALL
nine trials; PASS. All trial temp dirs removed successfully.

Control writes three singles and combined using ordinary
`event_stream`; Tee sends detached derived values to a bounded
queue of 16; B-lite writes source-bound JSONL sidecars during
single generation and verifies/replays them into combined. B-lite
source hashing, writer + reader work, and sidecar persistence cost
are included within that mode's timed fresh build. The control
source hash is obtained before timing to normalize input identity.
## Results (median of three independent process samples)

| Mode | Wall s | Process CPU s | MaxRSS KiB | Report bytes | Extra sidecar bytes |
|---|---:|---:|---:|---:|---:|
| ordinary | 0.71672 | 0.70753 | 39,476 | 8,246,435 | 0 |
| Tee | 0.55375 | 0.55450 | 42,204 | 8,246,435 | 0 |
| B-lite | 0.64578 | 0.63668 | 41,700 | 8,246,435 | 827,976 |

Raw per-run wall (s): control 0.70186/0.71672/0.72444;
Tee 0.55375/0.55117/0.56373;
B-lite 0.63904/0.64652/0.64578.
Tee's measured RSS median is 2,728 KiB above control; B-lite
2,224 KiB above control. This is small synthetic evidence: memory
will NOT extrapolate to two full report row arrays on 956 MB logs.
The B-lite extra 827,976 bytes (~10.0% of generated report bytes)
are potentially medically sensitive derived patterns and source
logical host/path metadata, not an acceptable default retention
policy. There is no approved production sidecar lifetime.

Private per-run JSON with numeric/hash evidence:
`diagnostics/private_phase94_synthetic_result.json` (ignored);
no original log text included. This Markdown captures the numbers,
but no noisy synthetic result is claimed to be a real performance A/B.
## Required to close Phase 9.4 architecture decision

- Same 23/24/25 September source SHA on a single Windows host;
  ≥3 process-isolated and alternated real candidate trials,
  wall/CPU, sampled lifetime WS/Private, cache+report disk bytes,
  zero unreadable samples; all normalized inventory/SQLite/exports.
- Full app cache matrix: fresh-all, warm, combined-only miss,
  single-only miss, mixed, restarted old single WITHOUT sidecar,
  changed source/alias and interrupted combined; strict report byte
  parity and safe retained single outcomes.
- Tee must integrate with owned report-intent, inventory transaction
  and reliable bounded cancellation. Temporary standalone Tee does
  not prove these application behaviors.
- B-lite sidecar must have explicit authorized retention/privacy,
  exact owner/host/path schema lifecycle, block format limits,
  process-kill/disk-full and fallback within the real app, not just
  isolated Directory.rename and temporary JSONL.
- If a hybrid is considered, run its OWN acceptance matrix; B-full
  offset/reconstructed raw requires a separate differential corpus.

**Current production execution should remain ephemeral derived-spool.**
Its prior exact real six-trial A/B and Python/frozen content gates
are not comparable to the 0.5–0.7 s synthetic standalone timings.
This comparison does not make the required owner architecture choice,
mark Phase 9.2/9.3 production-ready, or authorize a GitHub Release.

## Cross-platform repeat and corrected Windows memory measurement

After the initial Linux-only experiment `8265544`, the isolated
`fe589408ed04c5fc9fc5030122dfb6dbd3b6e3e4` change removed the
unavailable Windows `resource` import and introduced OS-native
`phase9_memory.sample` (no `psutil`/third-party dependency). Windows
memory reports **OS Peak Working Set** (high-water per process) and
**sampled peak Private Bytes** (lower bound from 10-ms polls),
including unreadable-sample counts. Linux still reports its own
`resource.ru_maxrss` in KiB. The two platform memory counters are
NOT claimed to be directly interchangeable.

Linux full second 9-trial run at exact `fe58940`: all byte parity
and original-source SHA gates PASS, temp workspace CLEANED. Median
wall/CPU s/maxRSS KiB: control 0.72587/0.71603/40,296;
Tee 0.55911/0.55748/41,632;
B-lite 0.67442/0.66572/41,772. Every mode has
8,246,435 report bytes; B-lite adds 827,976 JSONL+manifest bytes.

DBA-008D Windows exact `fe58940` 9-trial run: ALL nine per-platform
single+combined report manifests byte-equal; every input SHA
unchanged; owned workspace cleaned; no raw log text persisted.

| Windows mode | Median wall s | CPU s | OS peak WS B | Sampled peak Private B | Extra sidecar B |
|---|---:|---:|---:|---:|---:|
| Ordinary | 1.21199 | 1.20312 | 46,985,216 | 34,263,040 | 0 |
| Tee | 0.93915 | 0.92188 | 48,861,184 | 36,507,648 | 0 |
| B-lite | 1.12712 | 1.10938 | 47,849,472 | 35,205,120 | 827,979 |

OS peak WS is an actual per-process high-water mark; Private B
values are **sampled lower bounds**. Minimum Windows samples in
one run were ordinary 82, Tee 111, B-lite 87, all nine runs ZERO
unreadable memory samples. All three Windows variants emit the
same 8,251,219 report bytes. The few-byte Linux/Windows output
size difference is cross-OS formatting, not a reported cross-OS
byte-equivalence gate; parity was checked separately within each OS.
Private numeric Windows evidence remains ignored under
`diagnostics/private_phase94_synthetic_result_win32_v2.json`.

The extra Windows thread sampler changes benchmark instrumentation
vs the earlier Linux-only 8265544 trial; prefer same-SHA `fe58940`
results for this comparison. Still NO production app integration,
real 956-MB A/B, user-sidecar retention policy or architecture
owner approval. A full exact-SHA Windows test suite is recorded
separately after its completion, not inferred from these nine trials.

## Exact-SHA regression gates for Windows-ready harness

On tracked `fe589408ed04c5fc9fc5030122dfb6dbd3b6e3e4`, Windows
DBA-008D detached **CLEAN** worktree: full
`python -B -m unittest discover -s tests -q` **178 tests OK,
2 platform skips**, 80.802 seconds; `git diff --check` PASS.
Linux Bazzite same `fe58940`: **178 tests OK, 6 platform
skips**, 7.348 seconds; `git diff --check` PASS.
These suites do not make the standalone benchmark a normal
production route; only benchmark instrumentation + documentation
changed after the earlier b011589 production-runtime checkpoint.
