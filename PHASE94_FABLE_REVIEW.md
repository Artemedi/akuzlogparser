# Phase 9.4 — Fable 5.1 bounded independent code review (2026-09-27)

Checkpoint reviewed: 4743f9a, benchmark and real-source unit tests.
Two read-only Fable 5.1 review calls completed via private Clean APIs.
No user .log, SSH credentials or .env content entered prompts.
Full responses are retained locally in ignored diagnostics:
private_phase94_fable_review.txt and private_phase94_fable_review_followup.txt.

## Initial review — bounded findings

- Standalone combined-only-miss is NOT normal application
  cached_report/inventory/SQLite, and NOT a production acceptance gate.
- One real trial/mode cannot establish dispersion. The nine-trial
  alternated same-SHA run is the next bounded performance step.
- Hardlink samefile and before/after SHA were checked in smoke.
  External mid-read mutation remains possible, not reproduced.
- Windows OS peak Working Set and sampled Private Bytes are distinct;
  combined-child lifetime memory excludes single-setup memory.
- Disposable B-lite still persists potentially sensitive derived patterns.

## Follow-up review — corrected after examining omitted implementation

The initial reviewer had not received file_manifest, SidecarWriter,
VerifiedSidecar or independent corruption/retry test files.
Follow-up examined bench_phase9_baseline.py, probe_phase9_sidecar.py,
probe_phase9_sidecar_fallback.py and test_phase9_sidecar_fallback.py.

- file_manifest SHA-hashes every other output file literally.
  Explicit exceptions: provenance.json generated timestamp is dropped;
  ONE known volatile merged JSONL source string in data/catalog.js
  is normalized. These are bounded deterministic output comparisons,
  not a claim of literal identity for the two volatile fields.
- derived.jsonl stores event ID, start/end line, category, normalized
  pattern, duration, error fingerprint, replacement chars; NEVER
  the raw/message/headline fields. Manifest binds source SHA, size,
  host/path/date, code revision and body SHA/bytes. Derived patterns
  and source identity may still be sensitive clinical metadata.
- Existing synthetic tests cover missing sidecar, injected late
  sixth-record replay failure, forged sixth coordinates with rehashed
  body manifest, disk-full propagation, source mutation, existing
  target and unchanged completed single reports. A missing benchmark
  mode is NOT an absent standalone safety test.
- Mid-read external mutation TOCTOU remains a potential risk.
  No independently reproduced critical bug was found in these files.

## Next gate, without architecture selection

Run nine isolated combined-only Windows trials on exactly the same
23/24/25 Sep local source SHA, three/mode. Demand unchanged source
SHA and hardlink identity, normalized combined manifest parity,
unchanged ready singles, clean temporary workspace and zero unreadable
memory samples. Report all nine wall/CPU observations plus
min/median/max, setup costs and sidecar bytes. The existing fixed
balanced order remains for reproducibility; changing order would be
a separate methodology change.

This does not test production app cache, retention authorization,
restart/failure matrix, setup RAM, power-loss or any GitHub Release.

## Bounded manifest audit regression (separate workstream)

The verified, narrowly scoped follow-up adds
tests/test_phase94_manifest_scope.py, without modifying file_manifest
or the runtime. Three deterministic tests check: generated timestamp
alone is normalized but source host identity is not; exactly ONE
temporary catalog source is normalized but a changed second occurrence
is not silently hidden; and changed raw_000.js bytes fail parity.
Bazzite focused 3/3 PASS, full suite 184 OK (6 OS skips), diff-check
PASS. These tests establish the current manifest's intended scope,
not a new arbitrary whole-directory-byte claim. Windows still needs
exact new-SHA full suite once the ongoing older-SHA real run finishes.

## Standalone fallback extension — truncated body and revision mismatch

Two additional RED/green-independent synthetic scenarios now assert
fail-closed fresh-derive fallback when the body is truncated without
updating its signed size/SHA or when the stored code-revision marker
differs. In both cases completed singles remain unchanged, combined
matches normalized deterministic control, and no partial staging
directories remain. No runtime path or sidecar format changed.
Bazzite focused 7 tests PASS, full suite 186 OK (6 OS skips),
git diff --check PASS. Windows full exact-SHA regression is separate.
