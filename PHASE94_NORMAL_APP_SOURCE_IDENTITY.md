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
power loss. Exact-SHA DBA-008D
[Windows Actions #36356901468](https://github.com/Artemedi/akuzlogparser/actions/runs/36356901468)
completed SUCCESS on `f8a5cbd293b83cfa9d65836698f24d99134241e2`:
194 Python tests, 2 platform skips, 97.558 s, Node
browser controls PASS, diff-check PASS. A handled replay
exception with fresh interpreter recovery is NOT a hard
process kill, power-loss or production candidate acceptance.

## Strict local-origin SHA: opt-in gate (2026-09-28)

New optional environment variable
`AKUZ_VERIFY_LOCAL_SOURCE_SHA=1` enables one complete SHA-256
read of each previously cached ORIGINAL local source before
reusing its report. Default remains off: the normal fast stat
path and SSH/SMB behaviour are unchanged. Accepted true
values: 1/true/yes/on; false: 0/false/no/off/empty; a
malformed value for a local source fails closed.

The expected digest comes from the report's existing source
proof, or from its indexed download if no report is yet
present. When downloads have been cleared but reports
preserved, the report's source proof is still checked. The
check rejects symlinks, changes observed during read
(size/mtime/device/inode) and full-content SHA mismatch;
it does NOT alter, reindex or delete the historical report
or original source, and it does NOT silently accept the
unchanged-stat rewrite. Users must obtain a NEW file
identity by new mtime/name before rebuilding the changed
source. The option is not an automatic content-addressed
source-id migration or a new cache schema.

Five synthetic normal-app tests now cover same-size,
same-mtime, same-device/inode rewritten contents; valid
strict warm reuse; saved-report SHA after downloads clear;
malformed option fail-closed; and explicit proof that the
default warm path does NOT call original-source SHA. Original source and
analytics remain preserved on rejected reuse.
Implementation files: `akuz_local.py` and `akuz_app.py`,
tests: `tests/test_phase94_normal_app_source_identity.py`.
[DBA-008D regression #36357691962](https://github.com/Artemedi/akuzlogparser/actions/runs/36357691962)
on exact SHA `968b4983c704fd5bee12d0ad6403f550108e6654`:
198 Python tests PASS (2 Windows skips, 101.562 s);
Node browser controls PASS, diff-check PASS.

### Read-only I/O cost observation, NOT a normal-app benchmark

[DBA-008D SHA probe #36357886696](https://github.com/Artemedi/akuzlogparser/actions/runs/36357886696)
verified one full SHA pass on the SAME three frozen local
AKUZ snapshots previously checked by Phase 9.4 source SHAs:

| Date | Input bytes | One-read wall, s | CPU, s |
|---|---:|---:|---:|
| 2026-09-23 | 171,378,567 | 0.38700 | 0.37500 |
| 2026-09-24 | 140,361,291 | 0.31489 | 0.31250 |
| 2026-09-25 | 644,034,993 | 1.46551 | 1.43750 |
| Total | 955,774,851 | 2.16740 | 2.12500 |

These are **one** sequential Python SHA read per already
downloaded snapshot; they are NOT an app warm-cache A/B,
not original live server log verification, not cold-cache
disk measurements and not a statistical performance
guarantee. The original content and SHA digests were
never printed or uploaded by the probe. Full verification
adds an O(total input bytes) I/O pass on each opted-in
warm selection. The check narrows the stat-spoofing gap
but cannot guarantee that a separate writer will not
mutate a source AFTER verification (TOCTOU). Active
log tail truncation means an indexed snapshot and its
live original may intentionally differ; fail-closed
is expected in strict mode.
No published portable Release has this source change.

### Default fast-path follow-up

[DBA-008D Actions #36358007205](https://github.com/Artemedi/akuzlogparser/actions/runs/36358007205)
on exact SHA `d331853403fb8eda736a289c9e5a713568234b6e`
completed SUCCESS: 199 Python tests (2 Windows skips,
103.067 s), Node browser controls and diff-check PASS.
The incremental test asserts no invocation of the strict
original SHA helper when `AKUZ_VERIFY_LOCAL_SOURCE_SHA=0`,
and unchanged report identities, SQLite/JS exports and
ephemeral spool lifecycle on warm reuse. This is
regression evidence for the default path, not a timing
measurement of a live server.
