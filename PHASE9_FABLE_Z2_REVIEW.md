# Phase 9.0z-02 — independent Fable 5.1 follow-up and triage

Reviewed exact clean main `d0132fdc10f9658a7da21e5fe4bfc693be4f76cb`
using 10 tracked, line-numbered code/test/design files via user's
Clean APIs on Bazzite. No credentials, real logs or SSH cfg sent.
Model: `claude-fable-5.1`, HTTP 200, finish_reason `stop`,
22,287 prompt tokens, 9,047 completion tokens. Unmodified model
response: `PHASE9_FABLE_Z2_RAW_REVIEW.md`. This document is an
independent code-review triage, not proof of disk durability or
approval to publish a GitHub Release.

| # | Reviewer finding | Independently checked disposition |
|---|---|---|
| 1 | Pre-rename `os._exit` retains an owned intent and `*.building` | CONFIRMED (existing from Phase 9.0x, not introduced by raw hashing). Preserved by deliberate no-global-sweep policy. OPEN owner-validated diagnosis/quarantine; reject age-only deletion. |
| 2 | `_fresh_report_id` does not check intent marker collision | VALID potential bounded flaw; if same second/random suffix coincides with a stale marker, build gets `FileExistsError`. Schedule separate narrow collision check and deterministic test, independent of all-file-hash benchmark. |
| 3 | Symlink TOCTOU during path/hash checks | POTENTIAL only with a concurrent actor able to rewrite local cache; code checks are not atomic. Reviewer suggestion `is_symlink() + resolve()` is NOT itself atomic. OPEN Windows-compatible handle-based design; no proof of exploit or security guarantee. |
| 4 | No fsync/power-loss durability guarantee | CONFIRMED OPEN before and after patch; `os._exit` tests cannot prove physical power-cut semantics. Separate platform-specific approval and measurement required. |
| 5 | Conflicting indexed row vs owned intent raises on every retry | CONFIRMED deliberate fail-closed control, not newly introduced. Reject automatic intent retirement or silent `return None` without ownership; retain evidence and require explicit conflict policy. |
| 6 | Crash abandons derived-spool TemporaryDirectory | CONFIRMED pre-existing (before `d0132fd`), contains potentially sensitive derived text. Preserve unknown cache artifacts; avoid blind 24-hour purge. Define owner-specific cleanup with bounded retention/privacy gate separately. |

## Positive bounded evidence

Version-2 local intent carries `all_sha256` for every generated file;
`_validated_candidate` checks all expected paths and hashes before
re-indexing. Version-1 intents remain limited to historical
size+three-hash checks. Normal warm cache avoids full raw rehashing:
only three critical hashes and sizes of all known output files are
checked. This is NOT adversarial tamper-proof storage: a local actor
who controls both data and inventory/intent can forge both.

Bazzite synthetic recovery/indexed/analytics tests: 48/48 PASS.
Previously expectedFailure same-size raw corruption is now a real
assertion. Injected hash-read OSError leaves no indexed report or
intent, and unchanged historical v1 intent still recovers.

## Performance evidence and remaining gates

First controlled local synthetic A/B (three trials per version,
AB/BA/AB, exactly matching report manifests/event count) compares
`75d8226` with `d0132fd` on Bazzite, 3 synthetic source files with
4,000 events each and 384-character repeated text. Fresh wall medians:
control 1.2282 s, candidate 1.2112 s; candidate summed
`report.integrity_hash` median 0.011 s. No speedup claim from such
small timings. SQL raw hash not compared because indexed mtime is
volatile; full SQL/export semantic gate on exact September 23/24/25
SSH snapshot is separate. Private A/B JSON stays ignored.

**Completed on exact `d0132fd`:** Windows full 135/135 suite, clean
worktree exact-SHA real SSH Python/frozen inventory/report/SQLite/
analytics parity and measured full-file hash stage. The later
SIX-trial real A/B is now COMPLETE and reported below. **Still OPEN:**
generator-fed SHA experiment, same-root multiprocess inventory safety,
path race, abandoned spool/
intent handling, and actual power-loss durability. GitHub Release
remains unchanged.

Real-stage caveat supersedes any casual extrapolation from the small
synthetic A/B: the exact `d0132fd` Python run hashed 2,275,278,607
output bytes across 1,394 files / four reports in 21.023 seconds.
This directly reveals material fresh I/O/CPU cost. Retaining the
bounded corruption safeguard is a correctness/performance tradeoff;
optimized generator-fed SHA, which would avoid the second full-file
read, is OPEN and cannot be implemented by editing the concurrently
modified `akuz_html_explorer.py` workstream without isolation.

### Exact real-gate completion

`75d8226` clean worktree: SSH source SHA gate, diagnostic portable
smoke, fresh/warm Python and frozen, parity inventory/reports/all SQL
and exports/657,738 events PASS. Python wall/CPU 214.165/203.797 s;
frozen wall/CPU 203.329/199.890625 s. No content differences.
`d0132fd` clean worktree: same source SHA gate, portable smoke,
fresh/warm Python/frozen and all five parity dimensions PASS.
Python wall/CPU 226.689/207.875 s; frozen 208.561/205.171875 s.
Full hash stage: Python 21.023 s, frozen 6.310 s, each reading
2,275,278,607 B in 1,394 generated files. Python ran before frozen;
possible OS page-cache warmth invalidates an intrinsic runtime-speed
conclusion. Difference between one build per SHA is NOT causal A/B.
Both disposable source/config worktrees were removed, SHA-verified
private numeric results retained locally; public Release untouched.

## Follow-up 9.0z-03 — report ID collision finding #2

Bounded repair: existing final/staging directories AND local intent
marker/draft now reserve a newly sampled report ID. Three
fixed-clock/token regression tests reproduced pre-fix `FileExistsError`
and passed with the fix. Previously abandoned intent artifacts remain
untouched. This is NOT an atomic multi-process reservation protocol;
missing lock/transaction and TOCTOU risks remain OPEN.

### Six-trial real A/B supersedes one-pair timing conjectures

Control `75d8226` vs candidate `d0132fd`, three separately isolated
fresh/warm local builds per version on exactly one SHA-verified real
2026-09-23/24/25 SSH snapshot. Fixed AB/BA/AB order; all 5 normalized
signature fields equal across all 6; 657,738 events; zero unreadable
CPU/memory samples. Median control wall/CPU 214.026/203.500 s;
candidate 226.400/207.969 s: observed +12.374 s wall (+5.782%)
and +4.469 s CPU (+2.196%). Candidate's standalone full-file
SHA stage median 20.955 s for 2.275 GB / 1,394 output files.
This is an observed bounded correctness/performance tradeoff, not
new data about physical power-loss or multiprocess inventory safety.
No Release update; user category-isolation workstream untouched.
See PERFORMANCE_NOTES.md Phase 9.0z-05 for all six runs and scopes.
