# Phase 9.0x — Independent Fable 5.1 review / owner-side triage

Date: 2026-09-27. Source snapshot: clean `main` at
`6f15b3de7419fe4ede3e6fc28778ec3bdaad43a9` (implementation
`be38fa1cdafb07523b5bdb586bc3f7e4cc057f82`). Reviewer:
Claude Fable 5.1 via user's Clean APIs on Bazzite; separate from
implementation author. Reviewer did NOT receive credentials, raw
application logs, SSH configuration or machine access. Nine tracked
code/test/design files were submitted with exact line numbers;
review response had `finish_reason=stop`, 12,999 characters.
The untouched reviewer text is preserved in `PHASE9_FABLE_RAW_REVIEW.md`.
This triage checks claims against the actual tracked code and additional
synthetic local probes; it is NOT reviewer agreement or release approval.

## Reproduction and disposition

| # | Fable finding | Verified scope / owner-side disposition |
|---|---|---|
| 1 | Intent and staging persist after pre-rename `os._exit` | CONFIRMED and already documented policy. Affects accumulation and linear glob scan. No blind TTL deletion: age does not establish ownership or safe disposal. OPEN explicit recovery/quarantine design. |
| 2 | Symlink / TOCTOU swap | POTENTIAL when an adversary concurrently controls the local cache. Current checks are not atomic. Do not treat `lstat()` + `resolve()` as atomic; portable Windows-safe containment design OPEN. |
| 3 | Indexed `cached_report` accepts missing/corrupt `catalog.js` | REPRODUCED. `akuz_store.cached_report` checks only `index.html`. Predates `be38fa1`, but is a real disparity between cached and recovered report paths. OPEN separate indexed-cache integrity/repair policy. |
| 4 | Same-size corrupted raw shard recovered | REPRODUCED. Candidate records raw paths/sizes, not raw hashes. Newly relevant to interrupted-report recovery, already disclosed as a bounded limitation. OPEN content verification vs I/O overhead decision. |
| 5 | Missing `fsync` / physical power-loss durability | CONFIRMED absence, NOT a failed process-exit test; explicitly OPEN in design. `os._exit` on NTFS proves neither flush durability nor power-loss safety. |
| 6 | `_fresh_report_id` and dangling symlink | POSSIBLE `Path.exists()` blind spot with a precisely colliding random id; impact and NTFS rename behavior need targeted evidence. OPEN lower-priority hardening. |
| 7 | Inventory-vs-intent conflict raises repeatedly | CONFIRMED control flow; deliberately fail-closed on contradictory ownership. Reject unconditional reviewer suggestion to silently `continue` until an explicit conflict policy exists. |
| 8 | Parallel writers / marker collision | OPEN pre-existing same-root inventory lost-update risk, independent of random-id collision. Do not add a token-only retry and claim multiprocess consistency; requires locking/transaction design and tests. |
| 9 | `with_suffix('.json.tmp')` is incorrect | REJECTED as a defect: with a strictly validated report ID, `rid.json` -> `rid.json.tmp` is intentional and correct. Future hypothetical ID formats do not establish a present bug. |
| 10 | `retire_intent` swallows unlink error | CONFIRMED intentional post-commit best-effort semantics: cleanup must not roll back or misreport a successfully indexed report. Diagnostic telemetry for repeated failures is a possible separate improvement; do not chmod arbitrary user paths. |

## New executable evidence

Bazzite, clean `6f15b3d`: 17/17 pre-existing focused
crash/inventory/spool tests PASS. Two synthetic direct probes verified:
`INDEXED_CORRUPT_CATALOG_REUSED=True` and
`UNINDEXED_SAME_SIZE_RAW_REUSED=True` (same original report ID).
Added `tests/test_phase9_review_limits.py` with two **expected failures**
for the desired future behavior. Focused suite: 19 tests total,
17 ordinary PASS + 2 expected failures. These are known OPEN gaps,
not acceptance passes and not proof of a production fix.

No code path changed by this review cycle; the accepted real-log
Python/frozen SHA gate still belongs to `be38fa1` and remains valid
for its exact, bounded claims. Re-run synthetic and SHA-gated real
matrix after any implementation change. No Release update.

## Next bounded gates

1. Specify separately how an indexed-but-incomplete report is marked,
   excluded from reuse, re-generated and persistently de-duplicated;
   leave unknown report directories untouched. Test missing/corrupt
   catalog, provenance, raw shard, cache aliases, warm and mixed cache.
2. Quantify SHA-256 cost for every generated raw shard with and without
   derived spool before choosing all-file hashes, a generator-fed hash,
   or documented limited integrity protection. Include same-size damage.
3. Add explicit ownership-safe staging/intent diagnostic enumeration;
   do not sweep merely by age. Preserve interrupted evidence.
4. Define same-root multiprocess exclusion or proper inventory
   transaction/locking, then test crash during lock ownership and retry.
5. Keep power-loss durability as a distinct, separately approved gate;
   process-exit tests must not be relabeled as power-cut evidence.

API transport note: Python urllib client was rejected with HTTP 403 /
error 1010; previously verified curl transport succeeded. Initial
Fable reply was empty with `finish_reason=length` at 5,800 output
tokens and was NOT treated as a completed review. The successful
request used 16,000 max output tokens and returned a complete text
reply; no credentials or log contents were recorded in Git.

Cross-platform caveat: unfiltered Linux/Python 3.14 full suite produced
128 tests: four skips, two expected failures and one pre-existing
Windows-only cleanup-test error. That test injects `PermissionError`
with Windows-specific error 5 but Linux does not populate `.winerror`;
the production cleanup intentionally retries only `.winerror in (5,32)`.
Do NOT report a Linux full-suite PASS; Windows suite is the appropriate
full-run gate for this Windows/NTFS-specific code.

Final Windows gate: after exact original review commit `3b8900d`
was fast-forwarded onto DBA-008D and pushed, complete Windows
`python -B -m unittest discover -s tests -q` finished with
129 tests: 127 PASS, 2 **expected failures**, no other failures,
45.800 s. Full suite is green only in that qualified sense:
the two open integrity problems remain reproducible. `git diff --check`
PASS; `main` and `origin/main` matched. Separate uncommitted
category-isolation source/test were not staged, committed or altered.

## Follow-up 9.0z-01 — accepted Fable issue #3

Indexed-report reuse now persists hashes of the three required
identity files at report publication and checks them for all ordinary
cache hits, preserving newer valid replacements. Newly found invalid
rows are persistently quarantined without deleting their report
folders, are marked in library output and excluded from analytics.
Source metadata from pre-manifest historical reports remains
structurally checked but cannot be retroactively hash-proven.
The former catalog-review expectedFailure was promoted to a real
assertion; raw same-size recovery expectedFailure stays OPEN until
its separate workstream passes SHA-gated real performance tests.
