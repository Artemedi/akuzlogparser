"""Read-only snapshots of AKUZ .log files from an explicitly selected local path."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import hashlib
import os
from pathlib import Path
import re
import socket

from akuz_fetch import FetchError, _discard_incomplete_tail, _ends_with_newline, _sha_file


@dataclass(frozen=True)
class LocalConfig:
    host: str
    port: int
    username: str
    remote_log_dir: str
    local_dest: Path
    file_pattern: str = '*.log'


def load_local_config(raw_path: str, root: Path) -> LocalConfig:
    """The path belongs to the EXE host, not to the browser's computer."""
    if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 2048:
        raise FetchError('Укажите абсолютный локальный путь к .log или папке с журналами')
    value = raw_path.strip()
    if any(ch in value for ch in ('\x00', '\n', '\r')) or value.startswith('\\\\'):
        raise FetchError('Укажите локальный путь; для сетевых UNC используйте Windows · SMB')
    supplied = Path(value)
    if not supplied.is_absolute():
        raise FetchError('Локальный путь должен быть абсолютным')
    if supplied.is_symlink():
        raise FetchError('Символические ссылки как источник не поддерживаются')
    try:
        selected = supplied.resolve(strict=True)
    except OSError as exc:
        raise FetchError('Локальный путь не существует или недоступен') from exc
    if selected.is_file() and selected.suffix.lower() != '.log':
        raise FetchError('Выберите файл .log или каталог с журналами .log')
    if not selected.is_file() and not selected.is_dir():
        raise FetchError('Локальный источник должен быть файлом .log или каталогом')
    return LocalConfig('local:' + socket.gethostname().casefold(), 0, getpass.getuser(),
                       str(selected), (Path(root) / 'downloads').resolve())


def _identity(st):
    return (st.st_dev, st.st_ino) if st.st_ino else None


def _inventory(cfg: LocalConfig):
    selected = Path(cfg.remote_log_dir)
    if selected.is_symlink():
        raise FetchError('Символические ссылки как источник не поддерживаются')
    if not selected.exists():
        raise FetchError('Локальный источник больше не существует; обновите путь')
    entries = [selected] if selected.is_file() else selected.iterdir()
    result = []
    try:
        for p in entries:
            if p.is_symlink() or p.suffix.lower() != '.log' or not p.is_file():
                continue
            st = p.stat()
            if st.st_size < 1:
                continue
            path = str(p.resolve(strict=True))
            token = '\0'.join((cfg.host, path, str(st.st_size),
                                str(st.st_mtime_ns), str(st.st_dev), str(st.st_ino)))
            fid = hashlib.sha256(token.encode('utf-8')).hexdigest()
            result.append(dict(id=fid, name=p.name, path=path, size=st.st_size,
                mtime=st.st_mtime, mtime_raw=str(st.st_mtime_ns), device=st.st_dev,
                inode=st.st_ino if st.st_ino else None,
                modified_utc=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec='seconds'),
                suggested_date=datetime.fromtimestamp(st.st_mtime).date().isoformat()))
    except OSError as exc:
        raise FetchError('Не удалось прочитать локальные журналы: ' + str(exc)) from exc
    result.sort(key=lambda f: (f['mtime'], f['name']), reverse=True)
    return result


def list_local(cfg: LocalConfig, notify=lambda msg: None):
    notify('Читаю выбранные локальные .log (без рекурсивного обхода)…')
    return _inventory(cfg)

def fetch_local(cfg: LocalConfig, selected: dict, notify=lambda msg: None):
    """Copy a bounded prefix into the managed downloads/ directory; never edit input."""
    current = next((f for f in _inventory(cfg) if f['path'] == selected['path']), None)
    if current is None:
        raise FetchError('Локальный файл исчез, заменён или переименован; обновите список')
    if selected.get('inode') and (selected['device'], selected['inode']) != (current['device'], current['inode']):
        raise FetchError('Локальный файл заменён; обновите список')
    source = Path(current['path'])
    if source.is_symlink():
        raise FetchError('Символические ссылки как источник не поддерживаются')
    safe = re.sub(r'[^\w.\-]+', '_', current['name']).lstrip('.').strip('_')[:100] or 'akuz.log'
    cfg.local_dest.mkdir(parents=True, exist_ok=True)
    dest = cfg.local_dest / ('akuz_v4_local_' + selected['id'][:18] + '_' + safe)
    part = cfg.local_dest / (dest.name + '.part')
    if dest.exists() or part.exists():
        raise FetchError('Снимок с таким именем уже существует вне индекса; проверьте downloads')
    try:
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(source, flags)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if selected.get('inode') and _identity(before) != (selected['device'], selected['inode']):
                raise FetchError('Локальный файл заменён перед чтением')
            bound = before.st_size
            if bound < 1:
                raise FetchError('Локальный файл пуст')
            notify('Создаю снимок первых ' + str(bound) + ' байт: ' + current['name'])
            copied = 0
            digest_stream = hashlib.sha256()
            with part.open('xb') as target:
                while copied < bound:
                    block = stream.read(min(256 * 1024, bound - copied))
                    if not block:
                        raise FetchError('Локальный файл усечён во время чтения')
                    target.write(block)
                    digest_stream.update(block)
                    copied += len(block)
            after = os.fstat(stream.fileno())
        if source.is_symlink():
            raise FetchError('Локальный файл заменён символической ссылкой')
        pathname = source.stat()
        if _identity(before) is not None and (
            _identity(after) != _identity(before) or _identity(pathname) != _identity(before)
        ):
            raise FetchError('Локальный файл заменён во время чтения')
        if after.st_size < bound or pathname.st_size < bound:
            raise FetchError('Локальный файл усечён во время чтения')
        active = (current['size'] != bound or after.st_size != bound or
                  pathname.st_size != bound or after.st_mtime_ns != before.st_mtime_ns)
        tail = 0
        if active and not _ends_with_newline(part):
            tail = _discard_incomplete_tail(part)
            notify('Активный локальный журнал: отброшено ' + str(tail) + ' байт незавершённой строки')
        # A static snapshot has already been hashed during the copy.
        # Active snapshots need a second pass only if the tail was truncated.
        digest = _sha_file(part) if tail else digest_stream.hexdigest()
        part.replace(dest)
        return dest, digest, dict(active=active, captured_bytes=bound,
            stored_bytes=dest.stat().st_size, dropped_tail_bytes=tail,
            listed_bytes=selected['size'], remote_path=current['path'])
    except FetchError:
        raise
    except OSError as exc:
        raise FetchError('Не удалось создать снимок локального журнала: ' + str(exc)) from exc
    finally:
        part.unlink(missing_ok=True)
