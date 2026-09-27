# Phase 9 — Report publication crash recovery: bounded design gate

Status: **BOUNDED IMPLEMENTATION / SYNTHETIC + FIRST REAL GATE PASS**
(2026-09-27). New-SHA real SSH Python/frozen parity and diagnostic
portable smoke passed at be38fa1; independent Fable 5.1 review
completed with MODIFY/NEEDS-EVIDENCE and documented confirmed limits
(`PHASE9_FABLE_REVIEW.md`). Indexed-cache catalog integrity repair
is implemented in Phase 9.0z-01; same-size raw recovery remains OPEN.
3+ new-SHA A/B, remaining accepted review fixes,
concurrent-writer and power-loss gates remain OPEN. Phase 9.2/9.3
architectural approval is separate.
Evidence: `PERFORMANCE_NOTES.md` P9-0R-01, P9-0U-01, P9-0V-01,
P9-0W-01 and P9-0X-01/02; synthetic regressions
`tests/test_phase9_report_crash.py` and `test_phase9_recovery_spool.py`.

## Reproduced state transition

Previously `_publish` (through `2917d28`) generated `rid.building`,
wrote provenance, renamed it to `reports/rid`, added `rid` to the
in-memory store and called `save_store()` (atomic inventory replace).
Caught inventory-save errors roll back the newly published directory.
Abrupt process exit does not execute that rollback.

- Crash before directory rename: staging `rid.building` may remain;
  not a committed or indexed report.
- Crash after directory rename, before inventory save: complete
  `reports/rid` exists, but `inventory.json` does not reference it.
  Retrying generates another `rid`: **orphan directory reproduced**.
- Crash after inventory save: final directory is indexed and reused.
- Crash before/after inventory replacement alone does not corrupt the
  previously committed JSON on the tested NTFS process-exit boundary.

A directory not indexed in inventory is NOT necessarily disposable:
never sweep unknown `reports/v4_*` or user report/cache directories.

## Candidate ownership-validated recovery protocol

The bounded candidate implementation in `akuz_publication.py` persists a
narrowly scoped intent OUTSIDE the report output, associated with the exact
report id and cache key, **before** renaming staging to final. It records
value/provenance metadata, hashes of provenance/index/catalog, and paths
and sizes of all generated files; raw shard contents are not rehashed.
This is an identity and missing/truncated-file guard, not an assertion
of tamper-proof integrity of every raw shard.
Writing an intent must itself have atomic publication and explicit
failure handling; no raw AKUZ event data, password or API token.

On retry, reconcile ONLY the exact matching intent and its exact
report directory. Validate normalized path containment, report-id
syntax, cache key, existing index state, expected provenance and
presence of required `index.html` and `data/catalog.js` before any
recovery. An unknown/corrupt/mismatched intent must fail closed or
be left for explicit diagnosis, not trigger general cleanup.

If final directory exists and inventory lacks the matching report,
consider a verified idempotent inventory registration; if inventory
already contains it, preserve it and discard only the matching intent.
If only `*.building` exists, define a distinct explicit policy; never
silently publish incomplete output. Do not assume a missing final
means a stale file belongs to this process.

## Critical derived-spool interaction

`_perform_build()` may call `_publish` inside `SpoolWriter`, then
immediately register `spools[(path, sha, date)] = (spool_path, sink.sha256)`.
If `_publish` returns a recovered/cached single report without calling
`single_gen`, the sink has zero entries, and blindly registering its
path breaks combined replay. The caller must explicitly distinguish
fresh derive output from recovered report and use safe combined fallback
or regenerate a valid sidecar; do not invent a zero-event sidecar.
The candidate now registers spools ONLY when `single_gen` actually ran,
and uses a per-selection temporary filename to prevent an empty writer
from colliding with the next source's spool. A separate subprocess
integration test checks one recovered plus two fresh sources against a
non-spool baseline. An abandoned spool from the crashed process is NOT
globally cleaned, because its ownership is not established on retry.

## Remaining acceptance matrix before Phase 9 closure / Release

1. Original P9-0W-01 reproducer changes from observed orphan to a
   documented safe recovery without losing source provenance.
2. Inject exit after intent creation, after final rename, before/after
   inventory replacement and before/after intent cleanup.
3. Inject ordinary write/rename/permission/disk-full exceptions at
   each boundary, ensuring original exceptions stay visible.
4. Verify existing reports without an intent remain readable and are
   not swept; unknown, corrupted and malicious path-traversal intents
   must not cause deletion, overwrite or recovery of another report.
5. Fresh, warm/no-op, combined-only-miss, single-only-miss, mixed-cache,
   duplicate source, changed SHA/date and interrupted active SSH input.
6. Specifically check sidecar generation/absence and combined fallback
   when a single report is recovered without re-running its generator.
7. Check deterministic four-report bytes, inventory, provenance,
   SQLite semantic equality and all 534 analytics export equivalences.
8. Run a new isolated SHA-gated real 2026-09-23/24/25 snapshot test,
   then Windows diagnostic EXE rebuilt from exact accepted Git SHA;
   record CPU, wall, Working Set/Private Bytes and disk overhead.
9. Independent bounded review of code and crash state machine; audit
   each model criticism against actual code and test evidence.
10. No GitHub Release change without explicit owner confirmation.

No claim of physical power-loss durability follows from `os._exit`
tests alone; that would also require platform-appropriate flush/fsync
and storage durability assumptions. Keep Phase 9.0 whole-report crash
consistency **OPEN** until a bounded implementation passes its gates.
