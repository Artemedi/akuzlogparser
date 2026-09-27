"""ISOLATED B-lite proof: abort corrupted combined then fresh derive.

Not imported by the application; no inventory or user cache writes.
The caller owns an otherwise empty, disposable output parent.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from akuz_app import _iter_combined_sources
from akuz_html_explorer import generate
from akuz_store import sha256
from scripts.probe_phase9_sidecar import VerifiedSidecar


class SidecarReplayInvalid(ValueError):
    """Only invalid cached derived data authorizes a fresh retry."""


def combined_with_fallback(selected: list[dict], folders: dict[str, Path],
                           target: Path, scratch: Path, *, chunk_size: int = 1000):
    """Return 'sidecar' or 'fallback'; publish only a COMPLETE candidate.

    selected must contain previously resolved, immutable source snapshots.
    A normal app needs its own cache/alias and inventory transaction gates.
    """
    if not selected or not folders:
        raise ValueError('No resolved sidecar sources')
    if target.exists() or target.is_symlink():
        raise FileExistsError('Refusing to replace existing combined output')
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(selected, key=lambda x: (x['date'], x['remote']['mtime'],
                                             x['remote']['name']))
    for item in ordered:
        if sha256(item['local']) != item['sha']:
            raise ValueError('Source changed before sidecar replay')
    base = date.fromisoformat(ordered[0]['date'])
    total_bytes = sum(item['local'].stat().st_size for item in ordered)

    def attempt(use_sidecar: bool):
        with TemporaryDirectory(prefix='phase9-proto-combined-',
                                dir=target.parent) as work:
            stage = Path(work) / 'combined'
            if use_sidecar:
                with ExitStack() as stack:
                    readers = {}
                    for item in ordered:
                        label = item['remote']['name'] + ' · ' + item['date']
                        try:
                            readers[label] = stack.enter_context(VerifiedSidecar(
                                folders[label], item['local'],
                                item['remote']['host'], item['remote']['path'],
                                item['date']))
                        except (ValueError, KeyError, OSError) as exc:
                            raise SidecarReplayInvalid('Sidecar verification failed') from exc
                    def get_derived(ev):
                        try:
                            return readers[ev['source_file']].take(ev)
                        except (ValueError, KeyError, OSError) as exc:
                            raise SidecarReplayInvalid('Sidecar replay failed') from exc
                    generate(scratch, stage, base, chunk_size, 35,
                        event_source=_iter_combined_sources(ordered, base),
                        derived_hook=get_derived, input_bytes=total_bytes)
                    # Force late count/trailing-record checks before the
                    # candidate is allowed to leave its owned staging dir.
                    for reader in readers.values():
                        try:
                            reader.finish()
                        except (ValueError, OSError) as exc:
                            raise SidecarReplayInvalid('Sidecar end check failed') from exc
            else:
                generate(scratch, stage, base, chunk_size, 35,
                    event_source=_iter_combined_sources(ordered, base),
                    input_bytes=total_bytes)
            if target.exists() or target.is_symlink():
                raise FileExistsError('Combined output appeared during build')
            stage.rename(target)

    try:
        attempt(True)
    except SidecarReplayInvalid:
        # A disk-full or unrelated generator error must propagate; ONLY a
        # verified sidecar problem permits an expensive fresh retry.
        # The owned attempt directory has been completely discarded by
        # TemporaryDirectory BEFORE re-reading originals with fresh derive.
        attempt(False)
        return 'fallback'
    return 'sidecar'
