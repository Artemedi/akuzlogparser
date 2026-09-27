# Phase 9.4 — architecture evidence and owner gate

Status: **EVIDENCE PARTIAL; no owner architecture selection; normal application
still uses validated ephemeral derived spool**. Evidence assembled on
2026-09-28; do not interpret prototype results as deployment authorization.

## Decision context

The immediate production question is whether to eliminate or amortize
the re-derivation of per-event fields when generating combined reports.
Three candidate workflows are not interchangeable:

| Workflow | What is proved | What remains unproved |
|---|---|---|
| Current ephemeral derived spool | Earlier full real app exact-SHA Windows content/performance gates, combined re-derivation reduction and cache correctness at tested code | Not a general fix for all CPU/SSD cost |
| A: bounded in-flight Tee | SMALL synthetic report-byte parity and bounded queue/cancellation prototype; now also real same-source-SHA standalone fresh-all 3/3 normalized report parity with OS child memory readings | Normal app publication transaction, mixed-cache, real-source cancellation/interruption, actual host/path and app-cache replay |
| B-lite sidecar | SMALL synthetic fresh-all/combined-only; real combined-only and real same-source-SHA standalone fresh-all 3/3 normalized report parity, SHA/identity and corruption fallback synthetic tests | Approved sensitive-data retention, normal app inventory/SQLite/exports, real host/path lifecycle, eviction, restart and interrupted publication |

Standalone real combined-only **4743f9a** on local AKUZ log snapshots
23/24/25 September, 955,774,851 input bytes, 3 child processes per
route, normalized deterministic output parity and unchanged singles
PASS 9/9:

| Route | Combined wall min / median / max, s | Median single-setup wall, s | Additional derived sidecar |
|---|---:|---:|---:|
| Fresh derive control | 112.731 / 112.818 / 112.870 | 108.719 | 0 |
| B-lite verified replay | 47.721 / 47.869 / 48.009 | 121.986 | 125,992,821 B |
| Missing-sidecar fallback | 114.635 / 114.661 / 114.790 | 109.399 | 0 |

B-lite's median combined wall is 57.6% lower than control on THIS
isolated combined-only scenario. Its single creation/setup is costlier
and the extra derived JSONL/manifest can hold clinically sensitive
normalized patterns. Median combined-child OS peak Working Set is
~1,350.8 MB for B-lite versus ~1,284.9 MB control. Working Set is
not Private Bytes, and single-setup process memory is not measured.
No SSH, normal application inventory, SQLite, analytics exports,
real host/path identity or process restart is in these measurements.
The local benchmark uses deliberately SYNTHETIC host/path identity
even when its input event payload is a real AKUZ log snapshot.
Its published source SHA belongs to private local evidence, not Git.

## Fresh-all result: same frozen real sources (now PASS as an experiment)

Exact benchmark executable SHA `a65007b`, DBA-008D,
955,774,851 bytes / 23–25 Sep, three independent Windows
processes per mode. Read-only local JSON audit through
[GitHub Actions #36356020771](https://github.com/Artemedi/akuzlogparser/actions/runs/36356020771):
9/9 deterministic three-single+combined normalized parity,
source SHA equality versus the earlier combined-only trial,
valid child memory samples and owned cleanup PASS.

| Route | Fresh-all median wall, s | Median CPU, s |
|---|---:|---:|
| Ordinary | 222.744 | 222.391 |
| Bounded Tee | 148.767 | 148.062 |
| B-lite | 170.760 | 170.562 |

The three-mode comparison establishes bounded measurements of
**isolated fresh-all** on this workload, not a production choice.
Full source-specific min/median/max and caveats:
`PHASE94_REAL_FRESH_ALL.md`.
Distinct normal-app cache and failure/restart gates are NOT met.
The previously OPEN real standalone comparison requirement below
has now been executed; its **process-tree memory, transient peak
disk, cancellation and application-level components remain OPEN**.

## Acceptance gates before an architecture change

1. Real full-size fresh-all A/Tee/B-lite on the same frozen source
   SHA and one Windows host: isolated alternated runs, wall/CPU,
   child/process-tree WS and sampled Private with zero unreadable
   samples, peak disk and exact normalized report parity. This
   comparison must NOT be substituted with synthetic medians or
   combined-only timing.
2. Normal app fresh/warm/combined-only/single-only/mixed/restart
   scenarios, changed source with same size, host/path aliases,
   corrupt metadata, interrupted/cancelled/disk-full publication;
   no lost ready singles or stale inventory; same analytics SQLite
   and JS export semantics, byte parity of deterministic reports.
3. A: bounded queue cancellation and publication under existing
   report-intent and OS-backed inventory transaction.
4. B-lite: explicit owner authorization for whether derived clinical
   metadata may persist at all, source binding, lifecycle and
   retention, secure cleanup boundaries, version invalidation,
   path/host, restart and corrupt/old-sidecar fallback. A one-run
   temporary benchmark is NOT such authorization.
5. Validate frozen portable on exact accepted source revision.
   No release is approved by benchmark, tests, or documentary review.

## Remaining choices to be made by owner, AFTER evidence

- Keep ephemeral spool in production and continue studying both
  candidates without changing the ordinary application.
- Authorize a bounded Tee integration pilot with the acceptance
  matrix above (does not authorize B-lite retention).
- Independently authorize a bounded B-lite integration pilot AND
  specify if/where/how long derived metadata may be retained.
- Request a separate hybrid proposal and acceptance matrix.

No option is selected here. A successful experiment must not be
silently promoted into ordinary cache policy or GitHub Release.

Independent Fable 5.1 initial and follow-up read-only reviews:
PHASE94_FABLE_REVIEW.md. The later optional nine-trial numeric
second-opinion request was unsuccessful; it is NOT counted as an
independently passed review. Main evidence:
PHASE9_COMBINED_ONLY_SYNTHETIC.md and PERFORMANCE_NOTES.md.
