# Phase 9.4 — normal application source identity and restart gates

**Scope:** the real `akuz_app.perform_build_current` local-source
path, `inventory.json`, snapshot cache, individual and combined
reports, derived ephemeral spool, analytics SQLite and JS exports.
**Input:** small deterministic synthetic AKUZ logs created exclusively
in temporary test workspaces; no SSH, source credentials, clinical
data, user downloads or GitHub Release.

## Baseline already covered before this change

`tests/test_phase9_final_contract_gate.py` tests the normal app
with and without ephemeral derived spool over warm cache,
combined-only miss, single-only miss, mixed miss, SQL and exports
semantic parity, a replay failure and its retry. These tests were
already present. Do not claim them as newly implemented by Phase 9.4.

## Incremental source identity cases

`tests/test_phase94_normal_app_source_identity.py`:

1. Two independent paths containing equal bytes create separate
   individual reports while preserving combined + SQLite + JS
   semantic equivalence to the no-spool implementation. A fresh
   `State` reuses all reports from persisted inventory.
2. Rename one local file without changing its SHA. The old
   report is **not** silently substituted for the new path:
   two unchanged singles reuse, one single and combined rebuild;
   the historical report remains indexed rather than being
   silently deleted. A second run is warm.
3. Change a source's contents while keeping its byte length
   fixed, explicitly changing its `mtime_ns`. Exactly one
   single and the combined rebuild, while historical reports
   remain intact; the next run is warm.
4. Warm cache after a true new Python interpreter process:
   no new singles or combined, identical report IDs,
   existing analytics SQL and JS exports unchanged,
   no temporary derived-spool directories remain.
5. Inject a verified derived-spool replay failure before combined
   publication, then start a genuinely new Python interpreter:
   three completed singles retain their IDs and are reused,
   combined is generated fresh, SQLite/JS outputs match ordinary
   rederive, no partial building or derived-spool directory survives.

All assertions use a disposable local `TemporaryDirectory`
and normal `perform_build_current`, not standalone Tee/B-lite.
No production runtime modification is part of these changes.

## Limits and next gate

The source inventory identity includes path, size, mtime_ns and
filesystem device/inode. The same-byte-size mutation test above
explicitly changes mtime_ns; it does NOT prove detection of
same-size edits that also restore the original mtime and identity.
Proving that case requires an explicit content-verification policy
and performance budget for large active AKUZ logs, rather than
claiming that inexpensive stat-based identities are cryptographic
checksums.

Likewise these are local-file scenarios, not a verified SSH
source host/path integration or real medical-log acceptance.
They do NOT integrate experimental bounded Tee/B-lite into the
normal report transaction, authorize persistent derived metadata
or change the GitHub Release. Pending later gates:
interrupted publication, authentic remote identity, normal-app
integration of a selected candidate and full-size memory/disk
measurement.

## Verified Windows CI checkpoints

- Source identity three-test commit `c62ca532a9f62d8bab90e4e4ca264c6b7b83837f`:
  [DBA-008D run 36356580390](https://github.com/Artemedi/akuzlogparser/actions/runs/36356580390)
  SUCCESS, 192 Python tests / 2 platform skips, 92.528 s,
  Node browser controls and diff hygiene PASS.
- Real new-interpreter restart test commit
  `2c1aa09b4188b6fee674a8cf206ce01df5478946`:
  [DBA-008D run 36356713339](https://github.com/Artemedi/akuzlogparser/actions/runs/36356713339)
  SUCCESS, 193 Python tests / 2 skips, 93.640 s,
  Node controls and diff hygiene PASS.
- Both runs used immutable `GITHUB_SHA` checkout and synthetic
  test fixtures. The documentation-only commit following them
  was not separately performance-benchmarked. No raw real logs,
  private evidence or runtime architecture were changed.

## Fault+process-restart follow-up

The fifth scenario was added as commit `f8a5cbd` on top of the
four-case PASS `2c1aa09`. It targets a normal app exception
(derived replay mismatch) rather than a forced process kill or
power loss. A separate exact-SHA Windows Actions #36356901468
is the acceptance gate for this incremental test; never infer
PASS merely from the test definition.
