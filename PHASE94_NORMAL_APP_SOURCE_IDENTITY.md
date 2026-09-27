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
