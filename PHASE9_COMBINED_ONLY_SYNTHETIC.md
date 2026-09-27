# Phase 9.4 — combined-only-miss standalone synthetic benchmark

Status: **bounded evidence, NOT ordinary application cache, no owner
architecture selection**. Based on `dd710370e755f90ee61b75ddc4df0499e1bc2166`.
This experiment complements the already published all-fresh
`PHASE9_ARCHITECTURE_SYNTHETIC_COMPARE.md`; never mix their medians.

## Experiment construction

Three identical fixed AKUZ-shaped synthetic inputs, 3000/3000/3001
events with BOM, invalid-UTF8 replacement and mixed line endings.
Nine trial workspaces, ORDER control/B-lite/missing-sidecar;
missing-sidecar/control/B-lite; B-lite/missing-sidecar/control.
Per trial, build all THREE single reports before measuring combined
in a NEW child Python process. Assert combined output is absent.
Controller validates original single report file SHA manifests and
source content SHA before/after each measured combined build.

- `control`: ordinary combined `event_stream` + fresh derive.
- `b_lite`: singles built with verified source-bound JSONL sidecars;
  combined verifies source+sidecar and reuses derived fields.
- `missing_sidecar`: pre-existing singles generated without sidecars;
  helper sees missing manifest and freshly derives combined without
  rebuilding or rewriting singles.

Single creation/setup wall+CPU is recorded SEPARATELY from combined
wall+CPU. There is no app inventory, report-intent, state lock or SQL
analytics in any trial. Source sha256 resolution before combined
is excluded equally from child wall; B-lite's further verification
SHA reads inside its prototype are included. Each child is a fresh
process; OS high-water memory is child lifetime, not isolated to
combined.generate alone. Sidecar is private in owned TemporaryDirectory
and deleted after all nine trials; no real AKUZ content is retained.

## Linux Bazzite measured proof

9/9 source SHA checks, ready-single checks and exact four-report
manifest comparisons PASS; owned workspace cleaned. Median of three
fresh child builds per mode:

| Mode | Single setup wall s | Combined wall s | Combined CPU s | MaxRSS KiB | Extra derived sidecar B |
|---|---:|---:|---:|---:|---:|
| Ordinary | 0.34418 | 0.37031 | 0.36762 | 40,364 | 0 |
| B-lite | 0.42324 | 0.22878 | 0.22627 | 41,416 | 827,976 |
| Missing sidecar fallback | 0.35025 | 0.37629 | 0.37335 | 39,596 | 0 |

This synthetic timing suggests reuse can reduce a *subsequent*
combined-only rebuild on already prepared B-lite metadata, at
extra upfront sidecar generation/disk/privacy cost. It does NOT
show throughput of full 956 MB source, long-lived app cache policy,
or safe reuse of old app reports across process restarts. Missing
sidecar correctly falls back, with no ready single regenerated.

Private per-trial numeric/hash evidence:
`diagnostics/private_phase94_combined_only_linux_v1.json`
(ignored; no log event text). Windows results, full exact-SHA
suites and final integration will be appended when independently
verified. GitHub Release unchanged.

## Windows DBA-008D same-SHA combined-only-miss matrix

Exact clean detached `0722b480d0df6ae003de267f4fbad767be10ef11`,
9 independent combined-only child processes and 9 separate owned
single-report setup workspaces; all source SHA, unchanged ready
single manifests, exact combined bytes, fallback route and owned
workspace cleanup PASS. Each Windows variant's sidecar writes are
limited to a disposable synthetic root. No user `.log` was read.

| Mode | Median setup wall s | Combined wall s | CPU s | OS peak WS B | Sampled peak Private B | Extra sidecar B |
|---|---:|---:|---:|---:|---:|---:|
| Ordinary | 0.59080 | 0.60524 | 0.60938 | 48,918,528 | 36,052,992 | 0 |
| B-lite | 0.72444 | 0.39331 | 0.39062 | 50,044,928 | 37,208,064 | 827,979 |
| Old single missing sidecar | 0.59573 | 0.61662 | 0.60938 | 49,106,944 | 36,200,448 | 0 |

The minimum sampled Private-memory observations per Windows trial
were 32, 34 and 33 for ordinary/B-lite/fallback respectively;
0 unreadable readings in all nine trials. OS peak Working Set is
a process high-water mark; sampled Private is only a lower bound.
Linux KiB maxRSS is not directly interchangeable with Windows WS.
The experiment measures combined generation in a NEW process;
single setup time is charged separately, and extra B-lite cost
at first single creation is not concealed. Combined-only replay
has no Tee mode, because there are no two active fresh writers.
The normal AKUZ app's `cached_report`/inventory, SQL/exports and
production privacy decision are STILL OPEN; these microseconds-scale
synthetic numbers do not predict 956-MB workload savings.

Private ignored Windows result:
`diagnostics/private_phase94_combined_only_win32_v1.json`.
Full exact-SHA Windows suite is a separate gate.

## Exact-SHA 0722b48 full cross-platform regression

Linux Bazzite detached experimental branch: full
`python3 -B -m unittest discover -s tests -q`: **178 tests OK,
6 platform SKIP**, 7.611 s, diff-check PASS.
Windows DBA-008D CLEAN detached exact `0722b48` worktree: full
`python -B -m unittest discover -s tests -q`: **178 tests OK,
2 platform SKIP**, 80.485 s, diff-check PASS.
These suites are on the tracked benchmark experiment; no standalone
Python/frozen real-load or new Release gate was implied.


## Phase 9.4 next gate: disposable local-real combined-only benchmark (2026-09-27)

An OFF-by-default CLI mode was added to
`scripts/bench_phase94_combined_only.py`:
`--real-sources LOCAL_DOWNLOAD_DIR [--smoke]`. It is limited to
Windows and the three uniquely named 2026-09-23/24/25 AKUZ
`*_server.log` local snapshots. It never fetches SSH, reads credentials,
writes the original .log, modifies normal application inventory/cache,
or enables persistent B-lite. Inputs are NTFS hardlinked under the
benchmark's own disposable root (same-volume required, no copy fallback).
The path and event payload are excluded from private results; date,
size and SHA are retained locally. An original-to-link same-file and SHA
gate runs before and after EACH trial. If sources change, the run fails.

Three independent child-process combined-only measurements PER mode
are required by the full run. `--smoke` performs only ONE per mode;
it must NOT be presented as a replicated A/B gate. Per-trial report
folders are deleted after normalized deterministic manifest and
unchanged-single checks; this bounds scratch disk usage for real logs.
Only the benchmark-owned temporary parent is removed; user downloads,
reports, caches and ConnectConf.cfg are never cleanup targets.

The manifest explicitly normalizes the known volatile
`provenance.generated` timestamp and disposable catalog source name;
it proves byte identity of all other reported deterministic files,
NOT literal whole-directory byte identity. App inventory/SQLite/exports,
report-intent integration, authentic host/path schema and persistent
clinical-data privacy authorization remain OUT OF SCOPE.

Linux initial 3-mode synthetic smoke: PASS; 181 unit tests OK,
6 OS skips; diff-check PASS. The new real-source hardlink/duplicate/
missing-date unit tests are included. Windows real smoke / repeated
real runs and Windows full regression are PENDING at this checkpoint.
The earlier 9-trial synthetic data above are not new real results.
