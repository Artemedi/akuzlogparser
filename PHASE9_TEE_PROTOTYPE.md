# Phase 9.2 — bounded In-Flight Tee synthetic prototype

Status: **ISOLATED PROOF, NOT PRODUCTION / NO ARCHITECTURAL APPROVAL**.
Exact starting parent `5b692a8922d3918465bf6540081cdb5a6d479cba`.
Experiment `f7bbc640aeefbe66b44251ca7f11760d2d9f0ae6` changes ONLY
`scripts/probe_phase9_tee.py` and `tests/test_phase9_tee_prototype.py`.
No normal application route, cache/index/recovery, Release or existing
user report is touched. All inputs are synthetic `.log` files under
`TemporaryDirectory`, including a UTF-8 BOM, mixed LF/CRLF and one
invalid UTF-8 byte.

## Proven bounded behavior

Individual `generate()` uses one `read_input(...,defer_classify=True)`
per source and `derived_sink` to send a detached `dict(ev)` plus
immutable `DerivedEvent` through a Queue of maxsize 1 or 16 to a
concurrent combined `generate()`. Combined `derived_hook` consumes
that value without calling `derive_event` again. The actual spy
count is 37 derive calls for 37 source events, not two derive calls
per event; parser_calls has one entry per source.

The producer applies combined-specific global event ID, source label,
day offset, date, start/end line offsets and original coordinates
to its DETACHED clone. Individual writer keeps its original ev and
mutable report-specific category/component/pattern counters.
All three single and one combined file manifests are byte-equal to
the unoptimized `_iter_combined_sources` reference for BOTH bounded
queue sizes. A combined consumer failure is injected at event 5
with maxsize 1; `cancel` breaks producer backpressure, test exits
without hanging. No inventory is written by either test.

## Not yet proven / no false Phase 9.2 closure

The proof assumes all individual reports AND combined are fresh,
pre-validated, date-sorted snapshots. Real application source-resolve,
remote aliases, cache hits, mixed-cache and inventory revision are
intentionally NOT wired. It does not use `_publish`/owned intent, so
any partial prototype output on injected failure remains only in the
owned disposable directory, not in a real indexed report. Normal
application fallback remains the pre-existing temporary derived-spool.

At combined-only miss, all individual reports may already be cached;
A must NOT regenerate them merely to tee. It must parse+derive source
again with only the combined consumer, so the fresh fan-out gain
cannot be extrapolated to warm/mixed-cache. Source mutation during
snapshot capture, remote snapshot identity and the consumer error
when an app lock is held need independent tests. A concurrent single
and combined writer maintain TWO large in-memory report row arrays
and raw-shard buffers. Queue bounds control in-flight event clones,
NOT either writer's catalog memory; Python GIL may reduce CPU gains.

## Before a production proposal

1. Run process-isolated 3+ synthetic and SHA-gated 3+ REAL 23/24/25
   September source A/B against the ordinary no-spool and ephemeral
   spool modes, including CPU, full process-tree Windows WS/Private,
   output bytes, source parse/derive counts, disk output and errors.
2. Preserve all four cache transitions from Phase 9.1, date/alias
   ordering, source changed/rotated, duplicate-source suppression;
   two-phase staging and crash recovery need additional tests.
3. Ensure cross-thread cancellation cannot strand consumers on
   shutdown while the app holds a long-lived inventory transaction.
4. Never expose raw contents, derived headline or patient-related
   normalized patterns in benchmark logs or public artifacts.
5. Prototype remains OFF for normal builds and GitHub Release.
