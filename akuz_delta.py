"""Strict Phase 12 delta/resume primitives.

This module is intentionally NOT wired into akuz_fetch.fetch_selected yet.
It defines a fail-closed, synthetic-testable contract for assembling a new
immutable snapshot from a previously indexed snapshot plus a downloaded tail.

The first correctness prototype requires cryptographic SHA-256 proof of both
the previously stored remote prefix and the newly published remote prefix.
That is deliberately expensive; segment/checkpoint optimization is a later
benchmark gate, never an assumption.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path


class ResumeRejected(RuntimeError):
    """The delta path cannot prove equivalence to a safe full snapshot."""


@dataclass(frozen=True)
class RemoteMeta:
    device: int
    inode: int
    size: int
    mtime: int


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _copy_checked(src, dst, written: int, fail_after_written: int | None):
    while True:
        block = src.read(1024 * 1024)
        if not block:
            return written
        if fail_after_written is not None and written + len(block) > fail_after_written:
            allowed = max(0, fail_after_written - written)
            if allowed:
                dst.write(block[:allowed])
                written += allowed
            raise OSError(errno.ENOSPC, "synthetic disk full")
        dst.write(block)
        written += len(block)


def assemble_delta_contract(
    previous: Path,
    *,
    previous_sha256: str,
    previous_device: int,
    previous_inode: int,
    before: RemoteMeta,
    remote_previous_prefix_sha256: str,
    delta: Path,
    transfer_bound: int,
    publish_size: int,
    after: RemoteMeta,
    remote_published_prefix_sha256: str,
    final: Path,
    fail_after_written: int | None = None,
) -> tuple[int, str]:
    """Assemble and atomically publish one proven immutable snapshot.

    previous is the last indexed complete prefix. delta must contain exactly
    bytes [len(previous), transfer_bound). publish_size may be smaller than
    transfer_bound when an active file ended with an unfinished physical line;
    the next resume must then start at the stored publish_size, never at the
    earlier transfer bound.

    The caller supplies remote SHA proofs. Production SSH acquisition of those
    proofs is intentionally outside this module until Phase 12 server cost and
    failure matrix have been measured.
    """
    previous = Path(previous)
    delta = Path(delta)
    final = Path(final)
    part = final.with_name(final.name + ".part")

    if final.exists() or final.is_symlink() or part.exists() or part.is_symlink():
        raise ResumeRejected("target already exists")
    if previous.is_symlink() or not previous.is_file():
        raise ResumeRejected("previous snapshot is not a regular file")
    if delta.is_symlink() or not delta.is_file():
        raise ResumeRejected("delta is not a regular file")

    old_size = previous.stat().st_size
    if old_size <= 0:
        raise ResumeRejected("empty previous snapshot")
    if before.device != previous_device or before.inode != previous_inode:
        raise ResumeRejected("remote identity changed before transfer")
    if before.size < old_size:
        raise ResumeRejected("remote source was truncated")
    if transfer_bound != before.size:
        raise ResumeRejected("transfer bound must equal trusted before.size")
    if transfer_bound <= old_size:
        raise ResumeRejected("no append delta to resume")
    if not old_size <= publish_size <= transfer_bound:
        raise ResumeRejected("invalid published prefix boundary")

    local_old_sha = digest_file(previous)
    if local_old_sha != previous_sha256:
        raise ResumeRejected("previous local snapshot failed SHA")
    if remote_previous_prefix_sha256 != previous_sha256:
        raise ResumeRejected("remote saved prefix no longer matches")

    expected_delta = transfer_bound - old_size
    if delta.stat().st_size != expected_delta:
        raise ResumeRejected("delta length is incomplete or replayed")

    written = 0
    linked = False
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        with part.open("xb") as out, previous.open("rb") as old, delta.open("rb") as tail:
            written = _copy_checked(old, out, written, fail_after_written)
            written = _copy_checked(tail, out, written, fail_after_written)

        if written != transfer_bound or part.stat().st_size != transfer_bound:
            raise ResumeRejected("assembled transfer length mismatch")
        if after.device != before.device or after.inode != before.inode:
            raise ResumeRejected("remote identity changed during transfer")
        if after.size < transfer_bound:
            raise ResumeRejected("remote source shrank during transfer")

        if publish_size != transfer_bound:
            with part.open("r+b") as stream:
                stream.truncate(publish_size)

        published_sha = digest_file(part)
        if published_sha != remote_published_prefix_sha256:
            raise ResumeRejected(
                "new remote prefix proof does not match assembled snapshot")

        # No-overwrite publication. If another actor creates final after the
        # early existence check, os.link fails rather than replacing it.
        os.link(part, final)
        linked = True
        # From this point the verified immutable final exists. Failure to
        # remove the second hard-link name is cleanup debt, not publication
        # failure: surfacing it as failure could make a caller start another
        # writer even though the snapshot is already safely published.
        try:
            os.unlink(part)
        except OSError:
            pass
        return publish_size, published_sha
    except BaseException:
        if not linked:
            part.unlink(missing_ok=True)
        raise
