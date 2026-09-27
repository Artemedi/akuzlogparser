# Phase 9.4 — isolated real AKUZ fresh-all architecture gate

Status: **EXPERIMENT ONLY; NO production architecture selection**.

This follows the independently SHA-gated real combined-only trial on
4743f9a, but measures a DIFFERENT workload: start with no reports
and generate three singles plus combined from the same three local
AKUZ .log snapshots. Compare ordinary rederive, bounded Tee,
verified B-lite sidecar. Nothing is imported into normal app route.

Windows-only scripts/bench_phase94_real_fresh_all.py uses existing
link_real_sources and trial functions. Snapshots are hardlinked
into an owned disposable root on same volume; link/original SHA
and samefile checked before and after EACH trial. The benchmark
changes neither original downloads nor reports/cache, inventory,
SSH config, GitHub Release nor credentials. It retains only
date/size/SHA and numeric/file-digest evidence in ignored private
diagnostics on DBA-008D; no original log payload published.

Reproducibility: clean committed checkout, same unique 23/24/25 Sep
local source SHA, three child processes per variant, balanced order
control/Tee/B-lite; B-lite/control/Tee; Tee/B-lite/control.
A --smoke run is ONE per variant and NOT replicated A/B.
No prior evidence may be overwritten. Each trial's GB-sized report
data and sidecars immediately deleted from its owned temporary
directory AFTER exact normalized three-single+combined manifest
comparison. Source hardlinks removed when enclosing root exits.

Measurements: fresh-all child wall, process CPU, OS peak Working
Set, 10-ms sampled Private Bytes LOWER BOUND, report bytes and
sidecar bytes. Memory monitor also runs during post-timing report
hashing; high-water is child lifetime, not only timed generate.
Input source SHA resolved in child before timing; B-lite own
source and sidecar verification is inside timed work.

PASS: 9/9 same-OS deterministic output manifests match, original
SHA and samefile unchanged, zero unreadable memory samples,
at least 20 readings per child, exact code SHA, owned cleanup.
Known volatile provenance.generated and ONE disposable catalog
source name are normalized by existing file_manifest.
Any mismatch => FAIL without a performance claim.

Out of scope: normal app inventory, analytics SQLite and exports,
cache hit/restart, authentic remote host/path identity (synthetic
labels are intentional), privacy/retention authorization,
full publication transaction, mixed-cache, mid-read mutation,
process-kill/power-loss or frozen portable. Default ephemeral
derived spool and GitHub Release stay untouched.

Tee validator corrected to compare event count with sum of actual
single-report metadata instead of hard-coded 9001 synthetic events.
This affects only experimental assertion, not normal application.

## Completed real nine-trial result — independently recovered via Windows Runner

Source of truth: ignored local
`diagnostics/private_phase94_fresh_all_real_win32_v1.json` on
DBA-008D. Experiment executable exact SHA:
`a65007bb66196a9c24c6579151b4d557355cb45e`.
GitHub read-only numeric audit:
[workflow run 36356020771](https://github.com/Artemedi/akuzlogparser/actions/runs/36356020771),
`success`, at 2026-09-27 22:40 UTC. The audit confirmed the
three source date/byte/SHA tuples agree with the earlier local
combined-only dataset of **955,774,851 input bytes**; no raw source
content, private evidence JSON, credentials or source digests were
uploaded to GitHub.

| Route | Fresh-all wall min / median / max, s | Median process CPU, s |
|---|---|---:|
| Ordinary | 221.925 / 222.744 / 222.809 | 222.391 |
| Bounded Tee | 148.656 / 148.767 / 148.805 | 148.062 |
| B-lite | 170.609 / 170.760 / 170.936 | 170.562 |

All 9/9 normalized deterministic individual+combined report
manifest comparisons PASS; sampled-memory validity and no unreadable
samples PASS; benchmark-owned workspace cleanup PASS. This is
**three samples per route**, not general deployment performance.
The Tee median is about 33.2% below ordinary; B-lite about 23.3%
below ordinary **for fresh-all**. Do not mix these values with
the distinct combined-only route, in which previously ready singles
are excluded from timing. B-lite materializes additional derived
sidecar data, subject to a separate owner privacy-retention decision.

Fresh-all tests on Windows were independently run via
[self-hosted regression #36355972972](https://github.com/Artemedi/akuzlogparser/actions/runs/36355972972),
exact workflow SHA `f252eccddc8087bb1d26b7636694e0cebf5f38ea`:
**189 Python tests, 2 platform skips, 87.076 s, PASS**;
Node browser controls PASS; `git diff --check` PASS.
The CI execution SHA is NOT the older benchmark executable SHA.

OPEN despite this PASS: authentic host/path, actual application
inventory and mixed-cache, SQLite/exports equivalence, interruption
and restart, full publication and long-term derived metadata retention.
No runtime integration or Release modification is authorized.
