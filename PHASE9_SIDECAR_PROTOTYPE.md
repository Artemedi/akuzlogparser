# Phase 9.3 — isolated B-lite derived-sidecar prototype

Status: **SYNTHETIC STAND-ALONE PROOF, NOT APP CACHE / NOT APPROVED**.
Parent `9decf9d6783b2914aa6e128128d02247233a32ec`.
An existing other worktree had two UNTRACKED draft files; they were
copied byte-identically into an INDEPENDENT new worktree before
bounded binary-frame hardening. The original untracked files and
all unrelated worktrees were left untouched. No app `akuz_app.py`,
report publication/analytics/inventory, SSH or GitHub Release change.

## Contract and current verified behavior

`scripts/probe_phase9_sidecar.py` streams each event's ordinal,
start/end coordinates and derived category/normalized pattern/
duration/error fingerprint/replacement count to a source-local
JSONL body. It omits raw, message and headline but NORMALIZED
PATTERN CAN STILL INCLUDE PATIENT INFORMATION. The reader rebuilds
the headline from the original `event_stream` message and verifies
coordinates, record count and end-of-body. Source identity is SHA-256
of exact input bytes + byte length + host/logical path/first date;
deriver contract revision hashes four tracked parser/analytics/
derived/generator source files. Any source/schema/code revision
mismatch, same-size corruption, trailing/malformed sidecar row, or
incomplete manifest fails closed in this stand-alone reader.

Writing uses an exclusive owned sidecar folder, `.tmp` JSONL body,
source SHA check on close, then body rename and manifest rename.
An interruption between those independent renames can leave an
orphan body but never makes it a valid sidecar without manifest;
physical power loss is NOT proven. This prototype is not linked
to indexed single reports or the inventory and does not yet specify
a full cleanup/retention or fallback transaction.

## Experimental binary envelope and memory-safety correction

The draft `AKZS` v1 binary frame zlib-compressed a WHOLE JSONL
body. A 2,005-byte malicious synthetic frame advertising 1 byte
expanded to 2,000,000 bytes before the old decoder rejected it;
`tracemalloc` observed ~7.6 MB peak. The separate independent copy
now enforces `MAX_BINARY_FRAME_BYTES=8 MiB` and bounded output
`zlib.decompressobj().decompress(..., declared_size+1)`, rejecting
oversize, incomplete and trailing frames before accepting digest.
The new failure test verifies low traced allocation for the 2 MB
expansion payload, oversized advertised length and trailing data.
This is NOT a complete streaming binary cache: actual larger real
sidecars require MULTIPLE bounded frames and a verified block index.

## Synthetic measurements and test scope

`python -B scripts/bench_phase9_sidecar_synthetic.py` creates a
private temporary set of three 3000/3000/3001-event AKUZ-shaped logs
with BOM/CRLF/invalid UTF-8, then compares no-sidecar vs JSONL
sidecar single report manifests byte-for-byte. Sidecar body sizes:
275,586 / 275,586 / 275,649 bytes. Binary envelope sizes:
22,663 / 22,663 / 22,708 bytes. One-pass generation wall control
0.13471/0.11905/0.11653 s, JSONL candidate
0.14129/0.14998/0.14614 s. These are SINGLE short observations,
not causal throughput/memory estimates; codec sizes are highly
compressible synthetic text, not real medical distribution.
The body is bounded per synthetic file (<8 MiB). Test count:
Bazzite 6/6 targeted PASS with new decompression cap; complete suite
and independent exact-SHA Windows results are separate gates.
Private numeric result is ignored and contains no raw log payload.

## Explicitly OPEN before any persistent or production B-lite

- This prototype is NOT the normal application's `cached_report`
  data and does not register/restore sidecars for existing singles.
- Missing/old/invalid sidecar must fall back to ordinary derive
  WITHOUT recreating an otherwise valid individual report. Combined
  must abort/retry safely on late corruption and never mix half a
  sidecar with half a fresh pass without restart protocol.
- User-authorized storage lifetime/privacy/access and size expansion
  for medically sensitive normalized patterns and manifest host/path.
- Batch/block format, streaming decompressor and per-block digests
  on true 956 MB source snapshots; 3+ paired net-gain vs current
  temporary derived spool across fresh/warm/mixed-cache; CPU/WS/
  Private and disk on Windows plus exact Python/frozen/analytics parity.
- Source rotation, alias/identical bytes different path, old schema,
  disk-full, OS process exit, physical power-loss and adversarial
  concurrent path replacement require separately scoped guarantees.

Do not enable or publish the binary or persistent sidecar as a
production cache merely because the small standalone tests pass.
