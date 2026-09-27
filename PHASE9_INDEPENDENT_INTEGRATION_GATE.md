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

## NTFS junction red-to-green extension

The exact c701162 Windows synthetic probe created an NTFS junction
without elevated symlink permissions: `Path.is_symlink()` returned
False, `Path.resolve()` followed the redirected target and
`lstat().st_file_attributes` reported FILE_ATTRIBUTE_REPARSE_POINT.
Thus a symlink-only check is NOT sufficient for Windows portable.
New common helper `akuz_path_guard.is_redirected_path` rejects both
pre-existing ordinary symlinks and Windows reparse paths; invoked
by BOTH `inventory_transaction` and `exclusive_instance`.
The regression creates a disposable Windows junction and verifies
neither lock file is created in its external destination. Linux
focused suite after this change: 17 cases, 16 PASS, 1 expected
Windows-only SKIP. Windows exact-final-SHA gate pending.
This remains a check-then-open guard; a hostile simultaneous path
swap and OS-level physical power loss are NOT proven safe.

## Complete same-SHA 4f2445c cross-platform suite

Exact `4f2445c4389148ce5dd8a0287456688d1134b15d`
Windows full `python -B -m unittest discover -s tests -q`:
159 total, 157 PASS, 2 ordinary symlink permission SKIP,
53.800 seconds. NTFS junction test PASSED. `git diff --check` PASS.
The Linux full 159-case discovery at 4f had exactly ONE known
Windows-fake-WinError fixture error (5 platform skips), not a new
application assertion failure. Ported ONLY its previously isolated
test fix from `955e9f3`: assign synthetic `.winerror=5` explicitly
on Linux in `tests/test_phase9_frozen_cleanup.py`. After that:
Linux full 159 total, 154 PASS, 5 SKIP, 0 FAIL/ERROR (6.052 s).
No production module changed by this test-only normalization.
Authoritative final Windows SHA and real Python/frozen gate for the
test-normalized commit remain separate.

## Exact final `ee699af` independent Windows + Linux gates

The candidate after c701162 and 4f2445c (junction checks) received
a test-only WinError fixture normalization in ee699af; full
Windows suite at exact
`ee699afa283f3ccf4aaf6d83ee268b7b1258884c`: 159 tests,
157 PASS, 2 permission-based symlink SKIP, 53.722 s, exit 0,
`git diff --check` PASS. Exact Linux suite: 159 tests,
154 PASS, 5 platform SKIP, 6.118 s, exit 0, diff-check PASS.
Windows junction case PASSED; no cross-platform fake-WinError ERROR
remains. Integration chain is additive from `d47710a`, not a squash.
Portable diagnostic ZIP contains `BUILD_INFO.commit=ee699af` and
smoke PASS. ZIP SHA-256
`53fb7669c424c5f2419b6bb31116ff5bd9e2f24c9fa83b1085f2f2de43bf2254`.
On one SHA-gated real 23/24/25 September snapshot (956,307,242 B),
Python fresh wall/CPU 215.271/206.078 s, frozen
203.118/201.421875 s; all inventory, reports, SQLite, analytics
exports and single event checks PASS. Both real runtime samplers have
zero unreadable CPU/memory samples. The separate portable smoke had
one unreadable child-memory sample, explicitly recorded.
Original user log/cache/report directories and GitHub Release remain
untouched. A fresh SHA-gated real dataset run for all concurrency
fault boundaries is NOT claimed: synthetic process-exit/lock/clear
and real single-writer byte parity are separate evidence classes.

## Exact 8cb7985 — Fable follow-up + cross-platform regression

Fable's useful review on ee699af was MODIFY; two isolated changes
are in commit `8cb7985`: preserve genuine non-contention OS errno,
and `normcase()` registry keys on Windows. Negative EBADF/EACCES tests,
reacquisition and Windows alternate-case identity added. The Fable
review itself was on ee699af, not on 8cb; see
PHASE9_FABLE_LOCK_FINAL_REVIEW.md for the attributed disposition.

8cb Linux 162 tests OK (6 expected skips), Windows 162 tests OK
(2 ordinary symlink privilege skips). PyInstaller ZIP BUILD_INFO
commit 8cb7985; diagnostic portable smoke PASS (0 unreadable samples).
No GitHub Release update or upload of real .log files. The final
SHA-gated real Python/frozen parity remains an independent gate.

## Completed exact-SHA 8cb7985 real Python/frozen gate

All three real SSH snapshot dates 2026-09-23/24/25 were gated by
their saved hashes and sizes: 956,307,242 bytes total. On exact
`8cb7985c97a4c9d12cda11673f69d0b35d26aaec` the Python and
frozen runs agreed on all five normalized comparisons (inventory,
reports, SQLite, analytics exports, single events). Python fresh
wall/CPU 214.679/205.516 s; frozen 203.114/201.03125 s; both
memory and CPU samplers zero unreadable observations. Raw payload
not persisted, owned workspace cleaned. Numeric private evidence
SHA-256 `5a8977e034da46e674ae03d6cb5fc42274f83bf40b5a42cb73bffca5b6411ddf`.
This closes the formerly pending exact-SHA content gate; it does NOT
prove physical power-loss durability, adversarial path swap safety or
compatibility with old apps ignoring advisory file locks.
