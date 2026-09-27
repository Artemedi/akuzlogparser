# Phase 9 — independent bounded integration review

Fable 5.1 reviewed **actual clean tracked `68305e7`** via Clean APIs,
HTTP 200 / finish_reason stop. Eight line-numbered code/test files;
no user credentials or real log text shared. Original unmodified model
response: `PHASE9_FABLE_INTEGRATION_RAW_REVIEW.md`. Model verdict:
**MODIFY** for combined inventory transaction + instance guard; this
is not an approval to modify GitHub Release or a full Phase 9 verdict.

## Independently checked findings and dispositions

1. `inventory_transaction` did not reject a pre-existing symlinked
   `cache`/`inventory.lock`, unlike server-level `exclusive_instance`.
   Accepted narrow consistency fix: check both before canonicalising
   and opening the OS lock. Dedicated synthetic tests assert no
   redirected lock is created and external file bytes are unchanged.
   Hostile concurrent path replacement (TOCTOU) is **still OPEN**.
2. Fable identified check-then-open TOCTOU in both lock helpers.
   Distinguish hypothetical attacker changing the local cache paths
   from ordinary crash-released lock ownership; no handle-based
   Windows path race guarantee claimed. OPEN, no untested mitigation.
3. Fable alleged `owner.__exit__` could be skipped on `p.error()`
   during startup. **Not supported by current code:** after successful
   `owner.__enter__`, outer `try/finally` surrounds `prepare_runtime`
   and server bind; `SystemExit` still runs its finally clause.
   Added explicit IN-PROCESS `prepare_runtime` and bind OSError tests,
   both reacquire the same root lock after `main()` raises SystemExit.
   The older subprocess-only test was not enough for this claim.
4. Plain dict direct calls to `save_store` remain outside revision
   check (legacy compatibility); supported `load_store` supplies the
   revision. Neither this integration nor the instance guard makes
   old binaries and hostile/non-cooperating writers safe.
5. HTTP build/clear may acknowledge asynchronous work with 202,
   then surface InventoryBusyError via worker state rather than an
   immediate HTTP 409. The supported second server is already
   excluded before startup; external direct writers remain a
   low-priority UX case, not silently called crash-consistency PASS.
## Confirmed current scope

The final integration retains prepublication revision computation,
cross-process/within-process nonblocking transaction, crash-release,
full ownership of normal build, clear and source-date mutations,
and separate app-server lifetime ownership before UI asset writes.
The bounded symlink checks do NOT solve concurrent adversarial file
replacement, power-loss/fsync, old-app interop or orphan retention.
Fable's original `MODIFY` remains attributed to reviewed SHA 68305e7;
this document does not falsely reassign a model verdict to later code.

Linux focused combined tests after extra guard and in-process probe:
16/16 PASS. Windows full exact-final-SHA and real Python/frozen gate
must be recorded separately before final acceptance.
