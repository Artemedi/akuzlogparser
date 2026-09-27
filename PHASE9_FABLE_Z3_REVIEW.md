# Phase 9.0z-03 — independent Fable review and owner-side triage

Date 2026-09-27; reviewed **clean SHA**
`1cea8b1c472bb9555ef9fed3b02d123a5b3897cc`, not the concurrently
dirty Windows checkout. Fable 5.1 on user's Bazzite via Clean APIs;
24,691 prompt chars of five line-numbered tracked files, HTTP 200,
finish_reason=stop, 4,982 response chars. Original response kept
verbatim in `PHASE9_FABLE_Z3_RAW_REVIEW.md`. No credentials, real
application logs, SSH configuration or private cache bytes supplied.

**Independent result: APPROVE for narrow single-writer ID allocation**.
Fable found no new demonstrated incorrect deletion, marker adoption
or incorrect suffix handling. The existing four-path reservation
checks final report, `.building`, `.json` intent, and `.json.tmp`
intent draft. `exists()` + `is_symlink()` recognizes even dangling
symlink names as reserved; it does NOT constitute atomic multiprocess
protection or a handle-based symlink containment guarantee.

Owner-side red-to-green repro: fixed timestamp and token series
caused 3/3 new collision tests to fail as `FileExistsError` before
patch; after patch the focused Bazzite crash/index suite was 25/25
PASS. Exact-SHA clean detached DBA-008D full Windows suite: 137/137
PASS, 54.644 s; `git diff --check` PASS. Existing old markers and
unfinished evidence are never silently deleted.

| Finding | Disposition |
|---|---|
| Pre-existing intent and draft collision | Reproduced and fixed; no cleanup of foreign/abandoned marker. |
| Fixed ID repeated ten times | Correct bounded `FetchError` before generation; source and index unchanged. |
| Dangling symlinks | Reserved by `is_symlink()` even when `exists()` is false; tests for live/dangling symlink are optional future hardening, not claimed executed. |
| Race after `_fresh_report_id` check | OPEN: two processes may reserve the same ID between check and exclusive intent creation. New change is single-writer only. |
| Report-id `with_suffix('.json.tmp')` | Correct for the current strictly specified `rid.json` format; targeted draft collision test passes. |
| Physically durable inventory and report | Still OPEN, unrelated to ID reservation and not established by process-exit tests. |

No production parser/generated report bytes changed, no new Release.
The separate real six-trial A/B cost gate compares exact `75d8226`
and `d0132fd` only and must not be mislabeled as performance evidence
for this later `1cea8b1` ID-allocator patch.
