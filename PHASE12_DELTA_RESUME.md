# Phase 12 — remote .log delta/resume

**Status:** P12-01..P12-05 PASS for an opt-in normal-app path; default remains OFF  
**Started:** 2026-09-29  
**Release:** unchanged; published v4.8.0 is not modified by this work.

## Goal

Reduce repeated SSH transfer of an append-only AKUZ application `.log` without
ever treating an unproven offset as trustworthy. A successful delta path must
produce the same immutable local snapshot bytes that a safe full bounded fetch
would have produced for the accepted prefix.

This work is intentionally separated from Phase 11 process-prefetch. It does
not change scheduling, cache schema, report generation, analytics, SSH
compression, or the published release.

## Current checkpoint

Initial contract commits on `main`:

- `12fbbc3` — executable fail-closed test matrix.
- `843ccde` — isolated `akuz_delta.py` proof primitive.
- `0c1a5da` — tests bound to the real isolated primitive.
- `963d3c2` — successful no-overwrite publication remains committed even if
  removal of the temporary hard-link name fails.
- `fedf4b6` — publication-race and deferred-cleanup tests.
- `9578bcd` — read-only strict proof-cost smoke harness.
- `5296ee3` — final remote prefix proof bracketed by a second identity stat.
- `5e14bd6` — robust synthetic pre-gate + queued 25-Sep read-only workflow.

The current Windows self-hosted full regression is Actions
`#36548789449` (exact code SHA `5296ee3`). The dedicated read-only 25-Sep
strict-delta smoke is Actions `#36548902058` (workflow SHA `5e14bd6`).
Both are gates, not evidence, until they complete successfully.

## Strict correctness baseline

The prototype deliberately starts with an expensive proof model. Before a
previous snapshot may be extended, it requires all of the following:

1. The previous local snapshot is a regular file and its full SHA-256 matches
   the recorded digest.
2. Remote device/inode still match the previously captured source identity.
3. Remote size has not shrunk below the stored local byte count.
4. The cryptographic SHA-256 of the **remote saved prefix** equals the previous
   snapshot SHA-256. This detects an in-place rewrite even when path,
   device/inode and size look reusable.
5. The downloaded delta length is exactly
   `transfer_bound - previous_stored_bytes`.
6. A second remote metadata check after transfer proves the same device/inode
   and no truncation below the transfer bound.
7. The assembled candidate is restricted to a verified complete
   `publish_size`. For an active file with an unfinished physical line,
   resume state is the **stored complete-prefix byte count**, never the older
   capture bound.
8. SHA-256 of the newly accepted remote prefix must equal SHA-256 of the
   assembled local candidate.
9. Publication is a same-directory no-overwrite hard link. A competing final
   file is never replaced.
10. Failure before publication removes only the owned `.part`; the prior
    snapshot remains untouched.

This is a correctness baseline, not yet a performance design. Two remote
prefix hashes can consume enough server I/O/CPU to erase the network saving.
That cost must be measured before any production integration.

## Synthetic contract matrix

The current tests cover:

- append-only growth -> byte-exact complete snapshot;
- rotation before transfer;
- truncation before transfer;
- same-inode in-place prefix mutation;
- short/network-incomplete delta;
- disk-full during local assembly;
- mutation during transfer detected by final prefix proof;
- rotation after transfer;
- prior active capture whose discarded tail ended inside UTF-8;
- new active snapshot with an unfinished final physical line;
- replay of a different same-length delta;
- pre-existing final path;
- final-path race between precheck and no-overwrite publication;
- failure to remove the temporary hard-link name after successful publication.

All failure cases are intended to be fail-closed: no new unproven snapshot is
published and the previous known-good snapshot remains intact.

## Historical P12-00 non-goals

The list below describes the initial isolated checkpoint before P12-01..05.
It is retained as history; SSH range reads, normal-app opt-in wiring, owner-scoped
temp cleanup and portable inclusion were implemented and gated later.
At P12-00 the prototype did not yet:

- issue SSH range reads;
- acquire remote prefix SHA proofs;
- persist delta-specific resume metadata in `inventory.json`;
- retry or recover a stale owned `.part`;
- handle process termination/power loss across every publish boundary;
- define an owner-scoped orphan cleanup protocol;
- benchmark server hash CPU/I/O versus full transfer;
- compare a delta-built snapshot end-to-end against `fetch_selected()`;
- interact with Phase 11 one-ahead process-prefetch;
- alter any GitHub Release.

## Next gates

### P12-01 — Windows synthetic regression

Full Python suite, browser controls and diff hygiene on exact Phase 12 SHA.
No real SSH payload required.

### P12-02 — read-only SSH proof-cost benchmark

On the already approved 23/24/25 September AKUZ application logs:

- use only trusted paths from server inventory;
- compression forced OFF for an isolated comparison;
- create test prefixes only inside an owned temporary local workspace;
- measure remote saved-prefix SHA wall time, bounded delta transfer wall/bytes,
  final-prefix SHA wall time, local assembly/hash wall and total client CPU;
- verify before/after device/inode and non-truncation;
- never print host, remote path, inode, digest, credentials or payload;
- delete owned temporary payloads after each trial;
- do not modify normal app cache or reports.

A one-off benchmark is not enough for production acceptance.

### P12-03 — full-fetch vs strict-delta replicated A/B

Balanced repeated order on an immutable fixed prefix. Required output:
byte-equivalence, transfer bytes, total wall, client CPU, measured remote proof
cost and cleanup. If strict proof is not a net win, do not integrate it.

### P12-04 — failure/restart contract

Network loss, source mutation, rotation, truncation, checksum mismatch, stale
offset, disk full, abandoned temp, process kill and restart. Every uncertain
case must safely fall back to a full bounded snapshot or fail closed; it must
never silently skip or duplicate bytes.

### P12-05 — only then consider normal-app integration

Integration needs an explicit cache-schema/ownership design, rollback switch,
mixed-cache tests, Phase 11 interaction tests, frozen portable gate and a new
real A/B. No release publication is implied by passing these gates.


## P12-01 / P12-02 evidence — 2026-09-29

**Windows regression PASS.** Exact code SHA `5296ee3`, Actions
`#36548789449`, DBA-008D: **333 Python tests PASS, 3 skipped**; browser
controls PASS; `git diff --check` PASS. No production publish occurred.

**Read-only strict-delta smoke PASS.** Workflow SHA `5e14bd6`, Actions
`#36548902058`, DBA-008D. Source was the trusted 25-Sep AKUZ application
log, compression forced OFF. The tested immutable prefix was **644,567,384 B**.
A previous stored prefix of **644,034,993 B** was reconstructed in an owned
temporary workspace; only the **532,391 B** append was transferred by the
strict-delta candidate.

Measured single-trial comparison:

| Metric | Full bounded fetch | Strict delta |
|---|---:|---:|
| logical SSH payload | 644,567,384 B | 532,391 B |
| socket RX | 645,881,456 B | 535,712 B |
| wall | 180.965705 s | 10.890523 s |
| client CPU | 16.875000 s | 3.171875 s |
| transfer wall | 180.762540 s | 0.477432 s |
| old remote prefix SHA wall | n/a | 3.248178 s |
| new remote prefix SHA wall | n/a | 3.422887 s |
| local assemble/verify wall | n/a | 3.356579 s |

Snapshot SHA equivalence PASS; device/inode identity stable; no raw payload was
uploaded; normal app cache and Release were unchanged. The strict candidate
reduced measured wall by **~93.98%** and logical payload by **~99.92%** in this
one observation. This is deliberately **not acceptance evidence yet**:
`REPLICATED=NO` and server-side SHA CPU was not measured.

Next gate is P12-03: balanced 3+3 full/strict-delta A/B on one fixed prefix,
with server proof CPU accounting added before any runtime integration.


## P12-04 / P12-05 integration boundary — 2026-09-29

The v2 candidate now has a restart-safety workstream on `main`:

- owner-unique same-directory candidates are used instead of one fixed
  `final.part` pathname;
- a hard process exit may leave an orphan, but that orphan is never trusted,
  never blocks the next writer and is not deleted as another writer's state;
- publication remains a no-overwrite hard link after the full-prefix SHA proof;
- the exact Windows regression for this work is still a gate until its Action
  completes successfully.

Normal-app integration, when implemented, is intentionally **opt-in first**:

`AKUZ_PHASE12_DELTA_RESUME=1`

Default v4.8 behavior stays unchanged. A resume candidate is eligible only
when all of these facts already exist in the application's own inventory:
same SSH host/path, local previous snapshot path+SHA+stored byte count, and
remote device/inode captured for that snapshot. Legacy cache rows without
device/inode proof are not guessed or upgraded; they use a full bounded fetch.

The resume offset is `stored_bytes`, not the earlier remote capture bound.
This preserves the existing active-log rule where an unfinished physical tail
is discarded. Any proof/source/network failure before publication falls back
to a newly opened full bounded fetch (or fails closed if even that cannot be
proven). A successful delta snapshot is stored through the same inventory
transaction as a full snapshot.

For the first runtime gate, Phase 11 one-ahead process-prefetch is disabled
whenever Phase 12 delta opt-in is enabled. This is deliberate isolation, not a
performance conclusion. A combined Phase-11+12 scheduler requires a separate
failure/portable/A-B gate before it can be enabled.

No published Release is changed by this design checkpoint.


## P12-03 replicated single-proof A/B — PASS

Actions `#36556610689`, exact workflow/code SHA `8a95a5b`, DBA-008D,
25-Sep trusted AKUZ application log, compression OFF.

Balanced order: `F, D, D, F, F, D`. All six accepted snapshots had the
same SHA; device/inode identity stayed stable. Fixed prefix:
**644,567,384 B**. Previous snapshot: **644,034,993 B**. Delta:
**532,391 B**.

| Median metric | Full bounded fetch | v2 single-proof delta |
|---|---:|---:|
| wall | 120.066999 s | 7.214775 s |
| client CPU | 16.296875 s | 2.937500 s |
| logical SSH payload | 644,567,384 B | 532,391 B |
| socket RX | 645,865,840 B | 535,296 B |
| delta transfer wall | n/a | 0.498503 s |
| final remote prefix SHA wall | n/a | 3.314393 s |
| final remote SHA server CPU | n/a | 3.670000 s |
| local assemble/verify wall | n/a | 2.935482 s |

Measured median reduction: **93.991% wall**, **99.917% logical payload** and
**99.917% socket RX**. Full-fetch wall values were 120.066999, 124.983901 and
99.652259 s; v2 delta wall values were 7.214775, 7.223271 and 6.935688 s.

This closes the replicated proof-cost/performance gate for the tested fixed
prefix. It does **not** by itself enable production runtime. P12-04
hard-exit/restart and full Windows regression of the owner-unique temp change
remain the next gate, followed by opt-in normal-app integration and a new
end-to-end cache/failure/portable gate.

No raw log artifact was uploaded, normal app cache was unchanged, and no
GitHub Release changed.


## P12-04 hard-exit/restart gate — PASS

Exact code SHA `9ef6aed`, Actions `#36557160723`, DBA-008D:
**342 Python tests PASS, 3 skipped**, browser controls PASS, diff hygiene PASS.

The v2 assembler uses an owner-unique same-directory temp candidate. A
subprocess test hard-exits exactly at publication, proving that an abandoned
candidate does not become a trusted final snapshot. A later invocation ignores
that foreign orphan and safely publishes a newly verified snapshot; the orphan
is not silently deleted as another writer's state. Existing network-short,
rotation, truncation, checksum/proof mismatch, disk-full, replay, unfinished
tail and no-overwrite race gates remain green.

This closes the isolated P12-04 restart contract. Normal-app fallback,
inventory metadata and portable integration remain P12-05 work.


## P12-05 opt-in normal-app + portable acceptance — PASS

Exact acceptance SHA `73d194dbe3d5ccf69342032a1e49804e3b24dcae`,
Actions `#36563631303`, DBA-008D.

The production code path is wired behind an explicit, default-OFF rollback
switch:

`AKUZ_PHASE12_DELTA_RESUME=1`

Eligibility is fail-closed. A previous snapshot is used only when its inventory
row already proves the same SSH host/path, local path+SHA+stored byte count and
remote device/inode, with `delta_proof_version=1`. Legacy rows are never
upgraded by guessing. The resume offset is the stored complete prefix, not an
older capture bound. Any ordinary delta proof/transfer rejection reopens a
fresh full bounded fetch. Phase 11 process-prefetch is deliberately disabled
while Phase 12 opt-in is enabled; their combined scheduler remains a separate
future gate.

Owner-scoped temporary names use an app-root-derived token. Startup cleanup
removes only matching Phase 12 temp files under that app's configured
`local_dest`; foreign app-root tokens and arbitrary operator files are left
untouched. A hard-exit before final publication is retryable; a hard-exit after
a verified final but before inventory save remains fail-closed as an unindexed
final, matching the existing conservative full-fetch collision behavior. It is
not silently adopted or overwritten.

### Real normal-app evidence

One full control and one opt-in delta integration run used the same trusted
25-Sep source version:

- fixed snapshot: **644,567,384 B**;
- proven previous prefix: **644,034,993 B**;
- transferred append: **532,391 B**;
- full normal-app wall/CPU: **265.223218 / 96.109375 s**;
- delta normal-app wall/CPU: **92.686492 / 83.437500 s**;
- measured end-to-end wall reduction: **65.053%**;
- `delta_resume_downloads=1`, `delta_resume_fallbacks=0`;
- snapshot SHA equivalence PASS;
- deterministic report manifest equivalence PASS;
- semantic SQLite equivalence PASS;
- semantic analytics export equivalence PASS.

This is an integration observation, not a replacement for P12-03's replicated
transport A/B.

### Portable evidence

The same Action built the Windows x64 portable ZIP. PyInstaller explicitly
includes `akuz_delta`, and the frozen `--self-test` executes
`assemble_delta_final_proof` inside the EXE before the usual native crypto,
spawn, parsing, analytics, localhost UI, config-preservation and Unicode-path
smoke. Portable build and packaged smoke both PASS.

Focused Phase 12 pre-gate: **32 tests PASS**. The last pre-integration full
Windows regression on `9384e4f` passed **352 tests, 3 skipped**, browser and
diff checks. The exact final acceptance SHA also passed the normal Windows workflow:
[Actions #36563631352](https://github.com/Artemedi/akuzlogparser/actions/runs/36563631352), **354 Python tests PASS, 3 skipped**, browser controls PASS and diff-check PASS.

No raw log artifact was uploaded, the user's normal app cache was not used as a
test destination, SSH compression was forced OFF for comparison, and no GitHub
Release was changed.

### Acceptance boundary

Phase 12 is accepted only as an **opt-in, default-OFF** feature on `main`.
It is not approved as default-on and is not part of the already published
v4.8.0 release. Before default-on or a future release decision, separately
gate the Phase-11+12 combined scheduler and decide whether the conservative
unindexed-final crash window should gain an ownership-validated recovery intent.
