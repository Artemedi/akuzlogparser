# Phase 9 — Same-root multiprocess inventory isolation gate

Status: **IMPLEMENTED CANDIDATE / LINUX SYNTHETIC PASS / WINDOWS GATE OPEN** (2026-09-27). The historical same-root loss is preserved below as evidence. Current candidate adds an OS-backed crash-released lock, whole-operation transactions for app mutations, and optimistic inventory revision conflicts. Distinct from single-process report-ID collision fix `1cea8b1` and physical power-loss durability.

## Reproduction on Bazzite (synthetic only)

Reproducible synthetic driver:
`python -B scripts/probe_phase9_inventory_race.py`. It creates a
private temporary workspace, no raw AKUZ logs, cache or credentials.
One process A loaded inventory containing `initial`, added `A`,
wrote `cache/inventory.json.tmp`, and paused immediately BEFORE
`Path.replace()`. Process B loaded the old committed inventory,
added `B`, wrote the SAME temp path and replaced inventory. A then
continued and raised `FileNotFoundError`: its tmp pathname had been
consumed by B. Reopened index contained `initial` + `B`, NOT `A`.
Driver output: `MULTIWRITER_LOST_UPDATE_REPRODUCED`;
`A_write_failed=True`, `persisted_A=False`, `persisted_B=True`,
`initial_preserved=True`. Test inputs and workspace were disposable.
The SAME public script independently reproduced this exact synthetic
result on DBA-008D / Windows NTFS, 2026-09-27, with its own temporary
workspace, after the unrelated real SHA A/B completed. This is a
confirmed cross-platform current defect, NOT a corrected behavior.

`akuz_store.save_store` writes one fixed temp pathname; there is
no inter-process lock around `load_store`, mutations and `save_store`.
There is also an independent lost-update problem EVEN WITH UNIQUE
TEMP NAMES: two valid store snapshots each omit the other's new
entries; serialized last-write-wins JSON replaces one with the other.
The app's `state.lock` and `akuz_analytics.LOCK` are process-local.

## Mutation sites requiring one common contract

- `akuz_app._perform_build`: load + downloads, recovered/new single
  reports, aliases and combined reports. A `store` is held during
  SSH/local fetching and generation: holding a lock only during
  `save_store` leaves the earlier snapshot stale.
- `akuz_app._publish`/`akuz_publication.recover_report`: directory
  rename / owned intent / inventory adoption must share a transaction
  boundary and retry semantics.
- `akuz_store.cached_report`: persistent `invalidated=integrity`
  quarantine and subsequent rebuild must not erase another process.
- `akuz_analytics.update_source_date`: reload, modify dates,
  save; its `LOCK` is not inter-process and can overlap a build.
- `akuz_store.clear_cache`: tracked deletions and reset inventory
  must not overlap a build; only one process may own these paths.
- External/old application versions using the same app root may not
  honor any newly introduced locking protocol; version transitions
  need an explicit fail-closed or migration rule.

## Proposed bounded architecture (not yet implementation)

1. Use an OS-backed, crash-released exclusive lock on a stable
   app-root-local file; do NOT rely solely on `lockfile.exists()` and
   never delete a lockfile merely because it is old. Windows NTFS
   and Linux need separate tested OS-lock backends.
2. Acquire before reading a store that will be mutated, retain
   ownership until all associated writes/recovery/cleanup complete.
   Define a short, visible `busy in another instance` outcome instead
   of silently waiting for an SSH build lasting minutes.
3. Avoid one fixed temp filename among non-cooperating/legacy writers;
   changing the temp name is hardening, not a substitute for the
   read-modify-write transaction. Preserve old-or-new atomic replace.
4. Define lock ordering with local `state.lock` and analytics `LOCK`
   before coding; do not introduce process/thread deadlocks during
   source-date updates and analytics refresh.
5. Preserve strict source identity and report alias semantics;
   do not naively merge JSON keys after generation or silently turn
   cache clear/invalidated marks into a last-writer-wins union.
6. Document the limits: advisory OS locks cannot protect a cache
   simultaneously modified by older apps that ignore them or by an
   actor with direct write permissions. Physical power loss/fsync is
   an independent gate.

## Required acceptance matrix

- 2 independent processes writing different downloads and reports:
  no lost entries, no shared-temp collision and correct aliases.
- One writer terminated (`os._exit`/kill) while holding the lock,
  before/after temp replace and before/after report rename; another
  instance must proceed without arbitrary report deletion.
- Cache clear and source-date correction concurrent with build;
  guarantee no resurrected deleted report or silent dropped date.
- Same-root Python/frozen instances; Linux synthetic and real NTFS
  Windows process tests; single-writer fresh/warm semantics intact.
- Existing indexed report quarantine and derived-spool recovery
  remain idempotent; no cross-source de-duplication regression.
- Real SSH 23/24/25 exact-SHA report, SQLite, export parity,
  memory/CPU/wall overhead after implementation. No Release without
  owner confirmation.

## Implemented candidate after historical reproduction

- `akuz_store_lock.inventory_transaction(root)` uses one stable
  app-root-local lock file. Linux uses nonblocking `fcntl.flock`;
  Windows uses nonblocking one-byte `msvcrt.locking`. The lock file
  may persist, but the OS lock itself is released when a process exits.
  A second cooperating process/thread fails fast with a user-visible
  busy error rather than waiting through a minutes-long build.
- The lock is process-local reentrant for nested `save_store` calls
  inside one build/clear/source-date transaction.
- `perform_build`, `perform_clear` and
  `akuz_analytics.update_source_date` now hold the transaction from
  inventory read through all associated mutations/saves. This closes
  the stale-snapshot window for normal app entrypoints.
- `load_store` returns an `InventoryStore` carrying SHA-256 of the
  exact committed inventory bytes. `save_store` rechecks that
  revision while holding the OS lock; stale writers raise
  `InventoryConflictError` instead of silently overwriting a newer
  inventory. After a successful atomic replace the in-memory revision
  is advanced.
- Direct/plain dict saves remain compatible for legacy tests/tools but
  do not gain optimistic revision checking. Old application versions
  that ignore the OS lock remain outside the guarantee; this is
  documented, not silently merged.
- The original fixed `inventory.json.tmp` pathname remains safe among
  cooperating current writers because only one can enter save at once.
  Crash cleanup/atomic replace semantics and prior tests are retained.

Linux candidate evidence before Windows authority gate:
focused inventory/crash/report group 28/28 PASS; public synthetic
probe now emits `MULTIWRITER_LOCK_PASS` with initial/A/B all
persisted; child `os._exit(73)` releases the OS lock and next writer
succeeds; a stale sequential snapshot raises explicit conflict then
succeeds after reload. Full Linux discovery: 145 total, 140 PASS,
4 SKIP and the same pre-existing Windows-specific fake-WinError
cleanup ERROR already present before this candidate. No new failure.
