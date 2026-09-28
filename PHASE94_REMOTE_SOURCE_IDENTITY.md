# Phase 9.4 — remote source identity gate

Status: **SYNTHETIC NORMAL-APP TESTS ADDED; WINDOWS CI AND REAL SSH
METADATA PROBE PENDING at this checkpoint. No architecture selection.**

Scope is source identity and cache reconciliation only. This document does
not authorize Tee/B-lite integration, derived-metadata persistence, or a
GitHub Release.

## Existing SSH adapter guarantees already in code

The Linux SSH inventory key is derived from trusted configuration plus
server-side metadata:

- SSH host, port and username;
- full remote path under the configured log directory;
- size and raw mtime;
- device and inode when GNU `find` supplies them.

`fetch_selected()` does not trust browser-supplied arbitrary paths. It
re-resolves the previously listed path server-side, then checks
device/inode before transfer. It takes a bounded prefix, hashes bytes while
receiving them, performs another remote `stat` after transfer, rejects
rotation (device/inode change) and truncation, and trims only an incomplete
tail for an actively growing log. These are snapshot/rotation safeguards;
they are not remote cryptographic attestation of an unchanged file.

The Windows UNC adapter similarly includes host/path/size/mtime/device/inode
in inventory identity and verifies file identity around its bounded local
read. Existing tests already covered growth, rotation and same-content
different paths on the Windows adapter.

## New normal-app synthetic SSH identity matrix

`tests/test_phase94_remote_source_identity.py` uses deterministic synthetic
AKUZ bytes behind a fake SSH adapter boundary. There is **no network,
ConnectConf.cfg, real log payload or credential access**. The real
application transaction still executes: report publication, inventory,
ephemeral derived spool and analytics.

Added cases:

1. Equal bytes on two distinct remote paths remain two independent single
   reports and one combined report; warm reuse must retain both identities.
2. Reusing an OLD browser selection after one remote source grows must
   reconcile against refreshed server inventory: exactly that single and
   the combined report rebuild; unchanged single reuses.
3. Reusing an OLD browser selection after device/inode rotation on the same
   path must fail before fetch, preserve existing reports/downloads and
   analytics, and leave no staging/spool residue.
4. Same remote path, bytes and metadata under a different configured SSH
   host must be treated as a new source identity; no report IDs may alias
   across hosts.

Acceptance requires the self-hosted Windows regression for exact commit
`b3851b9b76111e5854fed48ed5942a45b315b17e` to finish SUCCESS. Until then
these are test definitions, not a passed gate.

## Real SSH metadata probe

Workflow `.github/workflows/akuz-phase94-ssh-metadata.yml` is intentionally
read-only. On DBA-008D it loads the existing private configuration locally
and performs **two directory listings only** for the known 23/24/25 Sep
AKUZ files. It must confirm that:

- each target is present exactly once;
- device/inode fields are available from the real server;
- id/path/size/mtime/device/inode are identical across the two listings;
- returned paths remain below the configured remote root.

The workflow must not invoke `fetch_selected()`, transfer any log payload,
or print host, path, inode or digest. Only date, byte count and boolean PASS
markers may enter the private Actions log.

The first workflow revision `a35cfb5` failed before job creation because
the embedded Python here-string was not indented as YAML block content.
This is a workflow-format failure, **not** SSH evidence. Commit `7da765e`
only corrects the YAML indentation and is the first executable probe
candidate.

## What this still cannot prove

Even a successful two-list probe is not content attestation. A file can be
rewritten while preserving path/size/mtime/device/inode, and remote SSH
inventory does not calculate a content hash. Full server-side SHA on large
active AKUZ logs would add server CPU/I/O and must not be introduced
silently. Likewise a successful metadata probe does not prove correctness
under network loss, power loss or a process kill during publication.

Remaining separate gates include:

- real SSH mutation/rotation fault injection without exposing payloads;
- an explicit policy for any remote full-content verification, if desired;
- integrated Tee/B-lite publication/restart/cancellation testing after an
  owner architecture choice;
- B-lite derived clinical metadata retention authorization;
- frozen portable verification before any Release change.
