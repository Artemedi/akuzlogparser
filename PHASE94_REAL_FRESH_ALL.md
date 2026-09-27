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
