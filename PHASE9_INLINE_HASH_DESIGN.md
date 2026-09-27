# Phase 9 — Producer-fed report SHA-256 experiment gate

Status: **PROPOSED / NOT IMPLEMENTED** (2026-09-27). The accepted
`d0132fd` v2 recovery protocol requires exact SHA-256 of all output
files, not merely their names and sizes. Re-reading four generated
reports processed 2,275,278,607 B across 1,394 files, taking
21.023 s on the first real Python run and 6.310 s in a warmer frozen
run. These are separate page-cache conditions, not intrinsic Python
versus EXE speed proof. The six-run cross-version real A/B is a
separate, source-SHA-gated test, not evidence for this unbuilt design.

## Exact current output writers

`akuz_html_explorer.generate` writes raw JS shard text at `flush()`,
then `data/catalog.js`, then index/event/style/common/index/event/
controls JS and DOCUMENTS. `_publish` in `akuz_app.py` writes
`provenance.json` afterward. Version-2 `akuz_publication.write_intent`
currently calls `_file_sizes()` and then reopens every file via
`sha256(path)`, requiring a second full file pass on fresh builds.
The generator also runs inside single-report derived-spool and
combined-stream wrappers: the full hash manifest must flow through
returned `meta` without coupling to `gen_fn is generate`.

## Byte-equivalence trap on Windows

**Do NOT substitute** `path.write_bytes(text.encode('utf-8'))`
for existing `Path.write_text(text, encoding='utf-8')` without a
cross-platform byte test. Default text-mode newline translation on
Windows converts every literal LF into the platform output newline;
a naïve byte write may change actual raw JS/catalog/UI bytes,
breaking published deterministic-report hashes. Embedded CRLF and
mixed source strings must be tested, not guessed. UTF-8 error handling
must remain equivalent; no BOM or new normalization introduced.
## Candidate A: bounded-memory writer-integrated hashing

Prototype a single explicit `write_report_text()` path shared by
all generator outputs. Encode in bounded Unicode slices and write
those exact bytes to a binary file, updating SHA-256 and size ONLY
for successfully written bytes. Translation of LF must reproduce
`TextIOWrapper(..., newline=None, encoding='utf-8')` exactly for the
platform; compare against a real `Path.write_text` reference under
Windows, Linux, mixed `\r\n`, standalone CR, non-ASCII, surrogate
handling, empty file and a large catalog. Better still, use a
measured text-wrapper/binary hashing sink if it preserves the exact
text-write contract and flush/partial-write behavior.

The generator may return an internal file-digest manifest along with
unchanged `events`, `physical_lines`, etc. `_publish` adds the small
provenance file's digest; `write_intent` validates every relative
path/size from `_file_sizes()` against the manifest and records
`all_sha256`, without re-reading every raw shard. Missing/untrusted
manifest or any injected custom generator falls back to the existing
safe full-file SHA pass. No report is indexed after a failed write.
`recover_report` MUST still re-read every candidate file when
adopting an interrupted report and fail closed on changed same-size
raw files. A missing historical v1 digest cannot be fabricated.

Potential cost: encoding already-large catalog text can increase peak
memory if duplicated as one giant `bytes` buffer. Avoid holding a
second full-size catalog buffer; measure bounded-slice encoding and
CPU versus current text writer, and keep sha output out of UI/logs.
Do not change raw source content, JSON serialization, event ordering,
category identity, or derived-spool sidecar count/hash semantics.
## Candidate B: parallel hashing within publication only

A smaller isolated experiment can keep generator output unchanged
and use bounded `ThreadPoolExecutor` workers in `write_intent` to
hash independent completed files, sorting/gathering results in the
same deterministic manifest order. This still reads 2.275 GB from
storage again, but may reduce elapsed wall under the right CPU/
file-cache conditions. It can also worsen contention, HDD latency,
Python memory, and in-flight file-open handles; benchmark before
acceptance, with a low worker count and serial fallback. Preserve
ordinary warm-cache behavior and v2 interrupted recovery semantics.

## Required tests before accepting either candidate

1. Red-to-green output-byte parity for Windows/Linux `write_text`
   versus proposed writer across embedded LF/CRLF, UTF-8 and large
   text. Include failure injection on partial write/encoding/rename.
2. New and legacy report manifest/recovery tests: same-size raw
   mutation must fail closed, unknown intent must remain untouched,
   original snapshot SHA and sources unchanged.
3. Fresh/warm/mixed single+combined and derived-spool tests, full
   Windows suite, byte-equal report manifests plus normalized SQLite
   and all analytics exports on the exact 2026-09-23/24/25 SSH
   snapshots and frozen EXE. Output SHA metadata must not include
   raw log text, credentials or local absolute server names.
4. Three independent paired real trials per variant in AB/BA/AB
   order, compare wall, CPU, lifetime sampled Working Set/Private,
   hash stage and disk bytes with 0 unreadable samples. No causal
   performance claim from synthetic microbenchmark or one pair.
5. Each isolated trial must use fresh owned output/cache, a single
   verified source snapshot, and cleanup ONLY its owned workspace.
   Do not modify or delete the user's existing project cache.
6. `akuz_html_explorer.py` is concurrently dirty in the user's
   Windows checkout from a category-isolation workstream. Do not
   commit the generator writer experiment onto that checkout until
   the independent changes are reconciled and retested. No GitHub
   Release upload without explicit confirmation.

## Review / acceptance state

The experiment is now implemented at exact code `14b45d2` and has
passed Windows unit/byte-equivalence gates, real Python/frozen parity,
and a separate six-trial real AB/BA/AB performance gate. Independent
Fable 5.1 review of the ACTUAL clean SHA ultimately returned HTTP 200,
finish_reason=stop and bounded verdict **APPROVE** after earlier
502/524/length failures. See `PHASE9_FABLE_INLINE_REVIEW.md`.
This still does NOT prove multiprocess inventory safety, physical
power-loss/fsync durability or approve a public Release.

## Bounded implementation candidate 9.0z-07

Producer-integrated writer and publication manifest validation are implemented in `14b45d2`. On failure or custom-generator injection, the ordinary full-read code path remains in service. Exact-code Windows and real-content gates plus independent Fable review are complete; final integration is the later category-isolation merge `7e1a6c2` and its clean Windows suite. No published Release change.

## Exact implementation gate results (Phase 9.0z-07)

The standalone producer-hash candidate `14b45d2` passed Windows 141/141 tests, legacy byte-level report/combined checks, diagnostic portable smoke, real SSH Python/frozen SHA-gated parity and semantic SQLite/export checks. Four real reports generated 2.275 GB of output, and only four provenance files (~2.5 KB) needed a second read during publication. Python/frozen hash-phase wall: 0.034/0.005 s. A later six-trial AB/BA/AB against `b6902c8` measured median wall 227.353 -> 215.147 s and CPU 208.156 -> 206.156 s with all signatures equal. Actual-code independent Fable review APPROVE. Full details: PERFORMANCE_NOTES.md P9-0Z-07/08 and PHASE9_FABLE_INLINE_REVIEW.md.
