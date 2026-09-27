"""OS-backed single-server guard per AKUZ Explorer application root.

Protects supported application instances, NOT arbitrary external writers
that bypass main() and not physical power loss. Never delete a lockfile.
"""
from __future__ import annotations
from contextlib import contextmanager
import os
from pathlib import Path


class InstanceBusy(RuntimeError):
    """Another supported process already owns this app-root workspace."""


@contextmanager
def exclusive_instance(root: Path):
    cache = Path(root) / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    if cache.is_symlink():
        raise OSError('Refusing symlinked cache directory')
    lock = cache / '.akuz-instance.lock'
    if lock.is_symlink():
        raise OSError('Refusing symlinked instance lock')
    with lock.open('a+b') as stream:
        # msvcrt.locking() needs a real first byte in the file.
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise InstanceBusy('Каталог AKUZ Explorer уже занят другим экземпляром') from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise InstanceBusy('Каталог AKUZ Explorer уже занят другим экземпляром') from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
