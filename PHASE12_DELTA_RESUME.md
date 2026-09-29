# Phase 12 — remote .log delta/resume

**Status:** EXPERIMENTAL / NOT WIRED INTO NORMAL APP  
**Started:** 2026-09-29  
**Release:** unchanged; v4.8.0 runtime does not import or call this prototype.

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

The Windows self-hosted full regression for `fedf4b6` is Actions
`#36547772522`. It is not evidence until the run completes successfully.

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

## Deliberate non-goals at this checkpoint

The prototype is **not** imported by `akuz_fetch.py`, `akuz_app.py`, or the
portable runtime. It does not yet:

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
