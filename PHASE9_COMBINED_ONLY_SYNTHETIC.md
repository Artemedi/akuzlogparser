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


## Windows real 23/24/25 Sep FIRST SMOKE, NOT replicated A/B

Exact commit 117d09148dd9be3ccfb821fb6329ef92b71823b0,
Windows DBA-008D normal working tree CLEAN. Three previously local
downloaded immutable-read inputs, total 955,774,851 B (different
from earlier 956,307,242 B baseline). Per-date sizes and exact SHA
are ONLY in ignored diagnostics/private_phase94_combined_only_real_win32_smoke_v1.json;
no original log text or original absolute paths were published.
The original-to-disposable-hardlink identity and original/linked
SHA were checked before/after EACH trial. No SSH and no app cache.

One independent child combined-only generation per route:
| Route | Single setup wall s | Combined wall s | Combined CPU s | Child OS peak WS B | Child sampled peak Private B | Extra sidecar B |
|---|---:|---:|---:|---:|---:|---:|
| ordinary | 110.03034 | 112.57222 | 112.42188 | 1,288,069,120 | 979,824,640 | 0 |
| B-lite | 122.13094 | 47.72034 | 47.79688 | 1,349,902,336 | 1,042,169,856 | 125,992,821 |
| old single missing sidecar/fallback | 109.27749 | 114.68456 | 114.51562 | 1,286,651,904 | 978,395,136 | 0 |

3/3 per-mode normalized deterministic combined manifests MATCH;
all ready-single manifests unchanged; per-trial owned report folders
and enclosing source-link root CLEANED; zero unreadable Windows
memory readings. Windows full exact-117d091 suite: 181 tests OK,
2 OS skips, 80.514 s; git diff --check PASS. Experimental
SHA/result status is explicitly real_smoke_one_per_mode_NOT_AB_gate.

The one observed B-lite combined wall is ~57.6% below ordinary,
but its initial single setup is costlier and it creates ~120 MiB
of potentially sensitive derived metadata. Child OS peak Working
Set and sampled Private only describe combined WORKER lifetime;
memory during single setup is NOT measured here. Private sampling
is a lower bound. A single trial/variant with uncontrolled OS
file-cache temperature is NOT a production speedup estimate.

OPEN before any owner choice: 3+ alternated isolated real trials
per mode on EXACT source SHA, production app cache and SQLite/export
matrix, restart, changed-source, interruption, cross-process,
privacy/retention approval, and production publication/rollback.
Do NOT enable persistent sidecars or change GitHub Release.


## Windows real 23/24/25 Sep NINE-trial combined-only gate

Exact code SHA **4743f9a** on DBA-008D. Nine process-isolated
combined-only builds, 3/mode, order control/B-lite/fallback,
fallback/control/B-lite, B-lite/fallback/control.
Sources: same exact three locally downloaded AKUZ snapshots as
first real smoke, total **955,774,851 B**, not old 956,307,242 B.
Original source SHA and NTFS hardlink samefile were checked before
and after each measured trial; ready-single normalized deterministic
file manifests stayed unchanged; all nine combined manifests
matched the first route. Inputs/outputs remain private/disposable.
No real source contents or source absolute paths committed.

| Route | Combined wall 3 samples, s | Wall min / median / max, s | CPU median, s | Single setup median, s |
|---|---|---|---:|---:|
| Ordinary fresh derive | 112.87017 / 112.81800 / 112.73135 | 112.73135 / 112.81800 / 112.87017 | 112.81250 | 108.71882 |
| Verified B-lite | 47.86947 / 48.00877 / 47.72104 | 47.72104 / 47.86947 / 48.00877 | 47.78125 | 121.98579 |
| Missing sidecar -> fresh fallback | 114.63461 / 114.66120 / 114.78986 | 114.63461 / 114.66120 / 114.78986 | 114.53125 | 109.39896 |

Median B-lite combined-only wall was **57.6% lower than ordinary
rederive** on THESE sources and on this isolated route. That does
not describe end-to-end normal application runtime or authorize
permanent sidecar storage. B-lite single setup median was **13.27 s
higher** than ordinary. No SSH/inventory/SQLite/exports/restart/
normal cache was benchmarked; single setup PROCESS MEMORY is not
measured. Three replicates/mode are bounded descriptive evidence,
not a production performance guarantee or an owner decision.

Median combined-child OS Peak Working Set ordinary/B-lite/fallback:
1,284,923,392 / 1,350,811,648 / 1,286,295,552 bytes. Median
sampled Private Bytes (LOWER BOUND): 1,192,718,336 /
1,256,124,416 / 978,046,976 bytes; the control Private
samples varied notably across trials. Median trial memory samples
were all >2,600; ZERO unreadable samples. B-lite created
**125,992,821 bytes** of extra potentially sensitive
derived JSONL/manifest per trial, deleted with owned workspace.
All nine route and normalized deterministic parity gates PASS,
all ready singles unchanged, source SHA stable; 0 owned
trial roots remain. No raw log content saved to metrics.

Ignored private evidence (on DBA-008D ONLY):
diagnostics/private_phase94_combined_only_real_win32_v1.json.
Status from benchmark itself:
real_standalone_NOT_app_cache_gate, trials_per_mode=3.
The benchmark deliberately normalizes provenance.generated and
one disposable catalog source filename; no literal equality
is claimed for those two volatile fields.

Windows exact 9ec38a9 full suite: **186 OK, 2 platform skips,
82.363 s, git diff --check PASS**. Bazzite exact 9ec38a9:
**186 OK, 6 skips, 7.449 s, diff-check PASS**.
These newer test-only commits do not turn the nine real trials,
which ran on 4743f9a, into new-SHA performance measurements.

OPEN: normal application cache+SQLite/analytics matrix, crash/restart,
host/path identity and privacy retention authorization, original-
source mutation mid-read and setup-memory validation; no production
B-lite deployment, no GitHub Release changes.
