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
