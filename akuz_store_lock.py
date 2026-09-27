"""Cross-process, crash-released lock for one AKUZ inventory root."""
from __future__ import annotations
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import threading

from akuz_path_guard import is_redirected_path


class InventoryBusyError(RuntimeError):
    pass


class InventoryConflictError(RuntimeError):
    pass


_REGISTRY_GUARD = threading.Lock()
_REGISTRY = {}


class _RootLock:
    def __init__(self, path: Path):
        self.path = path
        self.local = threading.RLock()
        self.depth = 0
        self.stream = None


def _root_lock(root: Path) -> _RootLock:
    cache = Path(root) / 'cache'
    marker = cache / 'inventory.lock'
    # Match the server-root guard: refuse preexisting redirected cache
    # or lock names rather than silently creating ownership elsewhere.
    # A concurrent hostile path swap is a separate, unproven TOCTOU case.
    if is_redirected_path(cache) or is_redirected_path(marker):
        raise OSError('Refusing redirected inventory lock path')
    path = marker.resolve()
    key = str(path)
    with _REGISTRY_GUARD:
        lock = _REGISTRY.get(key)
        if lock is None:
            lock = _RootLock(path)
            _REGISTRY[key] = lock
        return lock


def _try_os_lock(stream) -> None:
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise InventoryBusyError(
                    'Хранилище занято другой копией AKUZ Log Explorer') from exc
            raise
    else:
        import fcntl
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise InventoryBusyError(
                    'Хранилище занято другой копией AKUZ Log Explorer') from exc
            raise


def _unlock_os(stream) -> None:
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def inventory_transaction(root: Path):
    """Fail fast if another process/thread owns this app-root inventory."""
    lock = _root_lock(Path(root))
    if not lock.local.acquire(blocking=False):
        raise InventoryBusyError(
            'Хранилище занято другой операцией AKUZ Log Explorer')
    try:
        if lock.depth == 0:
            lock.path.parent.mkdir(parents=True, exist_ok=True)
            stream = lock.path.open('a+b', buffering=0)
            try:
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b'\0')
                stream.seek(0)
                _try_os_lock(stream)
            except Exception:
                stream.close()
                raise
            lock.stream = stream
        lock.depth += 1
        try:
            yield
        finally:
            lock.depth -= 1
            if lock.depth == 0:
                stream, lock.stream = lock.stream, None
                try:
                    _unlock_os(stream)
                finally:
                    stream.close()
    finally:
        lock.local.release()
