# Phase 9 — independent combined candidate gate (2026-09-27)

Status: **LOCAL INTEGRATION CANDIDATE, NOT MAIN / RELEASE**.
Distinct from the other contributor's active source/documentary worktrees.

Starting checkpoint for this verification: origin/main `d47710a`,
Bazzite main unpublished `6d5b60a` inventory transaction candidate.
Other worktree `38679a0` contains the separately tested inventory
revision-before-rename repair; `2d0bce1` contains the separate
OS-backed same-root HTTP-server ownership guard.

Created a NEW worktree `diagnostics/private_independent_integration`
at `38679a0`, and applied the 2d0bce1 code/test patch. Its only
three-way merge conflict was in `PERFORMANCE_NOTES.md`; the historical
inventory/producer notes AND app-root ownership notes were both
preserved, not discarded. `akuz_app.py` code merged cleanly.
Local integration commit: `68305e76ec02852b8eeb192825005eb8599ef666`.
This is not a claim that either source branch was already pushed.
## Execution gates

- Bazzite focused combined app-instance, inventory revision and
  multiprocess tests: 13/13 PASS; `git diff --check` PASS.
- Exact Windows detached integration worktree created from a verified
  private Git bundle SHA-256
  `8f94b5f95272c6b0cc3976c202b1d9b79245cd899c13fff25cabebd2ee90920e`.
- Same 13 focused tests: 13/13 PASS on DBA-008D.
- Windows `python -B -m unittest discover -s tests -q` on exact
  `68305e7`: 155/155 PASS, 54.630 s; `git diff --check` PASS.

No real logs or configuration were put in the Git bundle. No user
cache, original snapshots, released executable or unrelated worktrees
were changed. This document does not convert a passing synthetic
suite into real Python/frozen parity or disk power-loss durability.

## Follow-up after bounded Fable review of 68305e7

Original read-only review returned MODIFY. A preexisting symlinked
cache or low-level lock is now rejected before path canonicalization;
this is NOT atomic anti-TOCTOU hardening. New Linux symlink tests and
in-process startup-failure lock-release test are PASS (16/16 combined
focused tests). The model's claimed `p.error()` guard leak was
independently refuted by actual outer `try/finally` and both error
injection cases; details in PHASE9_FABLE_INTEGRATION_REVIEW.md.
Reused the separately reviewed README/README_PORTABLE ownership
instructions from 420ae24; no changes to report formats or parsing.
Exact final SHA will be recorded after these patches are committed.
