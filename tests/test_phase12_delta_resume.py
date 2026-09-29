"""Phase 12 delta/resume contract probe; no production runtime integration.

This suite models the minimum correctness contract before fetch_selected() may
reuse an older SSH snapshot.  All data is synthetic.  The deliberately strict
prototype requires a cryptographic proof of the old remote prefix and of the
new published prefix; performance/segment sampling is a later gate.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest


class ResumeRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteMeta:
    device: int
    inode: int
    size: int
    mtime: int


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(64 * 1024), b""):
            h.update(block)
    return h.hexdigest()


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
    """Strict Phase-12 prototype contract.

    The final file is a complete immutable snapshot, not a delta artifact.
    Nothing is published until all identity, length and SHA proofs pass.
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

    def copy_checked(src, dst):
        nonlocal written
        while True:
            block = src.read(64 * 1024)
            if not block:
                return
            if fail_after_written is not None and written + len(block) > fail_after_written:
                allowed = max(0, fail_after_written - written)
                if allowed:
                    dst.write(block[:allowed])
                    written += allowed
                raise OSError(errno.ENOSPC, "synthetic disk full")
            dst.write(block)
            written += len(block)

    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        with part.open("xb") as out, previous.open("rb") as old, delta.open("rb") as tail:
            copy_checked(old, out)
            copy_checked(tail, out)
        if written != transfer_bound or part.stat().st_size != transfer_bound:
            raise ResumeRejected("assembled transfer length mismatch")

        if after.device != before.device or after.inode != before.inode:
            raise ResumeRejected("remote identity changed during transfer")
        if after.size < transfer_bound:
            raise ResumeRejected("remote source shrank during transfer")

        # The active source can end with an unfinished physical line.  The
        # caller publishes only the verified complete prefix and resumes from
        # that exact stored byte count next time.
        if publish_size != transfer_bound:
            with part.open("r+b") as stream:
                stream.truncate(publish_size)

        published_sha = digest_file(part)
        if published_sha != remote_published_prefix_sha256:
            raise ResumeRejected("new remote prefix proof does not match assembled snapshot")

        part.replace(final)
        return publish_size, published_sha
    except BaseException:
        part.unlink(missing_ok=True)
        raise


class Phase12DeltaResumeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="akuz_phase12_delta_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.previous = self.root / "previous.log"
        self.delta = self.root / "delta.bin"
        self.final = self.root / "next.log"
        self.device = 77
        self.inode = 101

    def run_contract(
        self,
        old: bytes,
        remote: bytes,
        *,
        publish_size: int | None = None,
        before: RemoteMeta | None = None,
        after: RemoteMeta | None = None,
        remote_old: bytes | None = None,
        remote_published: bytes | None = None,
        delta_bytes: bytes | None = None,
        fail_after_written: int | None = None,
    ):
        self.previous.write_bytes(old)
        publish_size = len(remote) if publish_size is None else publish_size
        before = before or RemoteMeta(self.device, self.inode, len(remote), 200)
        after = after or RemoteMeta(self.device, self.inode, len(remote), 200)
        remote_old = old if remote_old is None else remote_old
        remote_published = (
            remote[:publish_size] if remote_published is None else remote_published
        )
        delta_bytes = remote[len(old):] if delta_bytes is None else delta_bytes
        self.delta.write_bytes(delta_bytes)
        return assemble_delta_contract(
            self.previous,
            previous_sha256=digest_bytes(old),
            previous_device=self.device,
            previous_inode=self.inode,
            before=before,
            remote_previous_prefix_sha256=digest_bytes(remote_old),
            delta=self.delta,
            transfer_bound=len(remote),
            publish_size=publish_size,
            after=after,
            remote_published_prefix_sha256=digest_bytes(remote_published),
            final=self.final,
            fail_after_written=fail_after_written,
        )

    def assert_failure_preserves_previous(self, old: bytes):
        self.assertEqual(self.previous.read_bytes(), old)
        self.assertFalse(self.final.exists())
        self.assertFalse(self.final.with_name(self.final.name + ".part").exists())

    def test_append_publishes_byte_exact_full_snapshot(self):
        old = b"event-1\nevent-2\n"
        remote = old + b"event-3\nevent-4\n"
        size, sha = self.run_contract(old, remote)
        self.assertEqual(size, len(remote))
        self.assertEqual(sha, digest_bytes(remote))
        self.assertEqual(self.final.read_bytes(), remote)
        self.assertEqual(self.previous.read_bytes(), old)

    def test_rotation_before_transfer_fails_closed(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        with self.assertRaisesRegex(ResumeRejected, "identity changed before"):
            self.run_contract(
                old,
                remote,
                before=RemoteMeta(self.device, self.inode + 1, len(remote), 200),
            )
        self.assert_failure_preserves_previous(old)

    def test_truncation_before_transfer_fails_closed(self):
        old = b"event-1\nevent-2\n"
        remote = b"event-1\n"
        self.previous.write_bytes(old)
        self.delta.write_bytes(b"")
        with self.assertRaisesRegex(ResumeRejected, "truncated"):
            assemble_delta_contract(
                self.previous,
                previous_sha256=digest_bytes(old),
                previous_device=self.device,
                previous_inode=self.inode,
                before=RemoteMeta(self.device, self.inode, len(remote), 200),
                remote_previous_prefix_sha256=digest_bytes(old),
                delta=self.delta,
                transfer_bound=len(remote),
                publish_size=len(remote),
                after=RemoteMeta(self.device, self.inode, len(remote), 200),
                remote_published_prefix_sha256=digest_bytes(remote),
                final=self.final,
            )
        self.assert_failure_preserves_previous(old)

    def test_same_inode_in_place_prefix_change_is_detected_cryptographically(self):
        old = b"event-A\nevent-B\n"
        changed_prefix = b"event-X\nevent-B\n"
        remote = changed_prefix + b"event-C\n"
        with self.assertRaisesRegex(ResumeRejected, "saved prefix no longer matches"):
            self.run_contract(old, remote, remote_old=changed_prefix)
        self.assert_failure_preserves_previous(old)

    def test_short_network_transfer_is_never_published(self):
        old = b"event-1\n"
        remote = old + b"event-2\nevent-3\n"
        short_delta = remote[len(old):-3]
        with self.assertRaisesRegex(ResumeRejected, "delta length"):
            self.run_contract(old, remote, delta_bytes=short_delta)
        self.assert_failure_preserves_previous(old)

    def test_disk_full_during_assembly_cleans_part_and_keeps_old_snapshot(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        with self.assertRaises(OSError) as caught:
            self.run_contract(old, remote, fail_after_written=len(old) + 2)
        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assert_failure_preserves_previous(old)

    def test_mutation_during_transfer_is_detected_by_final_prefix_proof(self):
        old = b"event-1\n"
        fetched = old + b"event-2\n"
        actual_after = old + b"event-X\n"
        with self.assertRaisesRegex(ResumeRejected, "new remote prefix proof"):
            self.run_contract(
                old,
                fetched,
                remote_published=actual_after,
                after=RemoteMeta(self.device, self.inode, len(fetched), 201),
            )
        self.assert_failure_preserves_previous(old)

    def test_rotation_after_transfer_is_not_accepted_even_when_bytes_match(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        with self.assertRaisesRegex(ResumeRejected, "identity changed during"):
            self.run_contract(
                old,
                remote,
                after=RemoteMeta(self.device, self.inode + 99, len(remote), 201),
            )
        self.assert_failure_preserves_previous(old)

    def test_resume_offset_is_stored_bytes_not_old_capture_bound(self):
        # Prior active capture ended inside UTF-8 and an unfinished physical
        # line.  Existing fetch semantics keep only the previous newline.
        old_stored = b"12:00 old event\n"
        old_transfer_bound = old_stored + b"partial " + b"\xd0"
        remote = old_stored + "partial ж\n13:00 next\n".encode("utf-8")
        self.assertGreater(len(old_transfer_bound), len(old_stored))
        size, sha = self.run_contract(old_stored, remote)
        self.assertEqual(size, len(remote))
        self.assertEqual(sha, digest_bytes(remote))
        self.assertEqual(self.final.read_bytes(), remote)
        self.assertIn("ж".encode("utf-8"), self.final.read_bytes())

    def test_active_unfinished_tail_publishes_only_complete_remote_prefix(self):
        old = b"12:00 old event\n"
        remote = old + b"13:00 complete\n14:00 unfinished"
        publish_size = remote.rfind(b"\n") + 1
        size, sha = self.run_contract(old, remote, publish_size=publish_size)
        expected = remote[:publish_size]
        self.assertEqual(size, len(expected))
        self.assertEqual(sha, digest_bytes(expected))
        self.assertEqual(self.final.read_bytes(), expected)
        self.assertFalse(self.final.read_bytes().endswith(b"unfinished"))

    def test_replayed_delta_from_another_version_is_rejected(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        replay = b"event-X\n"
        self.assertEqual(len(replay), len(remote) - len(old))
        with self.assertRaisesRegex(ResumeRejected, "new remote prefix proof"):
            self.run_contract(old, remote, delta_bytes=replay)
        self.assert_failure_preserves_previous(old)

    def test_existing_target_is_never_overwritten(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        self.final.write_bytes(b"operator-owned")
        with self.assertRaisesRegex(ResumeRejected, "target already exists"):
            self.run_contract(old, remote)
        self.assertEqual(self.final.read_bytes(), b"operator-owned")
        self.assertEqual(self.previous.read_bytes(), old)


if __name__ == "__main__":
    unittest.main()
