# Phase 9.3 — isolated B-lite late-corruption fallback gate

Status: **SYNTHETIC EXPERIMENT / NOT NORMAL APPLICATION PATH**.
Base commit `2d63b5553e61d301d4369c0f3c611e3aea1510b7` already
contains a standalone source-verified JSONL sidecar and bounded
experimental binary frame. This follow-up is a separate worktree;
the other B-lite owner's dirty/untracked worktree remains untouched.

New standalone `scripts/probe_phase9_sidecar_fallback.py` is NOT
imported by `akuz_app.py`. It verifies source snapshot hashes before
any attempted replay, then opens all sidecars bound to exact SHA,
host, path and date. It builds combined in its own TemporaryDirectory
under an operator-supplied, isolated output parent, and renames the
completed report only after all readers confirm record count/end.
On a VERIFIED sidecar problem, the first staging directory is fully
discarded before a second combined build uses ordinary fresh derive.
Ready single reports are not regenerated and are never deleted.
Crucial exception boundary: `SidecarReplayInvalid` is raised only
by sidecar opening, event-record replay or end-of-reader checks.
A general generator/OS disk-full error propagates immediately and
DOES NOT start a second expensive pass. A pre-existing combined
output refuses overwrite without touching an operator file, and
an input whose content no longer matches its resolved SHA fails
BEFORE generating anything. Output-name race against a hostile
concurrent writer is NOT solved by this standalone helper; only a
real application inventory transaction can establish that contract.

Synthetic tests `tests/test_phase9_sidecar_fallback.py`:
1. Valid sidecar reports equal ordinary combined byte-for-byte.
2. Reader failure on sixth event -> discard partial + fresh retry;
   3 completed singles and original source files stay unchanged.
3. Forged sixth event ordinal even with rewritten body SHA/length
   -> semantic coordinate guard triggers full clean retry.
4. Missing sidecar manifest -> fresh retry without single rebuild.
5. Injected disk-full -> immediate failure, no double build.
6. Existing combined output and changed source snapshot -> fail
   before overwriting or publishing; no stage directory survives.
Bazzite exact sidecar fallback branch new tests: 5/5 PASS;
full `python3 -B -m unittest discover -s tests -q`:
178 tests, OK (6 Linux platform skips), 7.077 s.
The ResourceWarning on synthetic HTTP 403 and argparse expected
startup error messages are not test failures. `git diff --check`
PASS. Windows DBA-008D on CLEAN detached exact `021ebca`: full
178 tests, OK (2 OS privilege skips), 85.621 s, diff-check PASS.
No standalone portable archive or real-log performance gate was
run for this experiment: production runtime files were unchanged.
No SSH, real logs, inventory, persistent cache or GitHub Release
was touched. Sidecars still include potentially sensitive normalized
patterns and logical host/path; this is not authorization to retain
medical production derived data, and output parity in this tiny
synthetic matrix is not measured net speedup.

**Remaining Phase 9.3:** integrate sidecar lifecycle/identity into
actual `_publish` under accepted owner policy; fallback under
cache-only combined miss and source aliases, disk-full/kill on
real application, byte-exact Python/frozen, 3+ real paired
performance, peak process memory and long-term privacy policy.
Phase 9.4 architecture choice remains OPEN.
