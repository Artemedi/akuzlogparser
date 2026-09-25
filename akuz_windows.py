#!/usr/bin/env python3
"""Read-only Windows AKUZ file-log adapter for a locally running Explorer on Windows.

Uses the current Windows identity and a preconfigured UNC share; no remote commands,
administrator shares, embedded SMB credentials, or remote temporary files.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import getpass
import hashlib
import os
from pathlib import Path
import re

from akuz_fetch import FetchError, _discard_incomplete_tail, _ends_with_newline, _sha_file


@dataclass(frozen=True)
class WindowsConfig:
    host: str
    port: int
    username: str
    password: str
    sudo_password: str
    remote_log_dir: str
    local_dest: Path
    file_pattern: str
    base_date: str = ''


def load_windows_config(path: Path, root: Path) -> WindowsConfig:
    if not path.is_file():
        raise FetchError('Не найден ConnectConf.cfg')
    ini = configparser.ConfigParser(interpolation=None)
    try:
        with path.open('r', encoding='utf-8-sig') as source:
            ini.read_file(source)
        s = ini['windows']
        if not s.getboolean('enabled', fallback=False):
            raise FetchError('Включите [windows] enabled = true в ConnectConf.cfg')
        folder = s.get('log_dir', '').strip()
        pattern = s.get('file_pattern', '*.log').strip()
        dest = s.get('local_dest', '').strip()
    except (configparser.Error, KeyError, ValueError) as exc:
        raise FetchError('Проверьте секцию [windows] в ConnectConf.cfg') from exc
    # Use a shared folder rather than guessing C$ or accepting arbitrary browser input.
    if not folder.startswith('\\\\') or '/' in folder or '\x00' in folder or '\n' in folder or '\r' in folder:
        raise FetchError('windows.log_dir должен быть UNC-путём: \\\\SERVER\\AKUZLogs')
    parts = [p for p in folder[2:].split('\\') if p]
    if len(parts) < 2 or any(p in ('.', '..') for p in parts):
        raise FetchError('Укажите сетевой сервер и общую папку в windows.log_dir')
    if not pattern or '/' in pattern or '\\' in pattern or any(x in pattern for x in ('\x00','\n','\r')):
        raise FetchError('windows.file_pattern — маска имени, например *.log')
    local = Path(os.path.expandvars(os.path.expanduser(dest))) if dest else root / 'downloads'
    if not local.is_absolute():
        local = root / local
    return WindowsConfig(parts[0].lower(), 445, getpass.getuser(), '', '', folder, local.resolve(), pattern)


def _identity(stat):
    """Windows SMB may expose zero inode; then do not claim inode-level assurance."""
    return (stat.st_dev, stat.st_ino) if stat.st_ino else None


def _inventory(directory: Path, cfg: WindowsConfig):
    if not directory.is_dir():
        raise FetchError('Сетевая папка недоступна. Проверьте UNC, учётную запись и права чтения.')
    result = []
    try:
        for p in directory.iterdir():
            if p.is_symlink() or not p.is_file() or not p.name.lower().endswith('.log') or not fnmatch.fnmatch(p.name.lower(), cfg.file_pattern.lower()):
                continue
            st = p.stat()
            if st.st_size < 1:
                continue
            name = str(p)
            mtime_ns = st.st_mtime_ns
            token = '\0'.join((cfg.host, name.lower(), str(st.st_size), str(mtime_ns),
                                str(st.st_dev), str(st.st_ino)))
            fid = hashlib.sha256(token.encode('utf-8')).hexdigest()
            result.append(dict(id=fid, name=p.name, path=name, size=st.st_size,
                mtime=st.st_mtime, mtime_raw=str(mtime_ns), device=st.st_dev,
                inode=st.st_ino if st.st_ino else None,
                modified_utc=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec='seconds'),
                suggested_date=datetime.fromtimestamp(st.st_mtime).date().isoformat()))
    except OSError as exc:
        raise FetchError('Нет доступа к сетевому каталогу Windows: '+str(exc)) from exc
    result.sort(key=lambda f: (f['mtime'], f['name']), reverse=True)
    return result


def list_windows(cfg: WindowsConfig, notify=lambda msg: None):
    if os.name != 'nt':
        raise FetchError('UNC-источник Windows доступен при запуске Explorer на Windows.')
    notify('Читаю сетевую папку Windows (текущая учётная запись)…')
    return _inventory(Path(cfg.remote_log_dir), cfg)


def fetch_windows(cfg: WindowsConfig, selected: dict, notify=lambda msg: None):
    """Take a bounded, read-only copy. Live growth is allowed; rotation/truncation is not."""
    if os.name != 'nt':
        raise FetchError('UNC-источник Windows доступен при запуске Explorer на Windows.')
    directory = Path(cfg.remote_log_dir)
    current = next((f for f in _inventory(directory, cfg) if f['path'] == selected['path']), None)
    if current is None:
        raise FetchError('Windows-журнал исчез или переименован. Обновите список файлов.')
    if selected.get('inode') and (selected['device'], selected['inode']) != (current['device'], current['inode']):
        raise FetchError('Файл заменён после получения списка. Обновите список файлов.')
    source = Path(current['path'])
    cfg.local_dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^\w.\-]+', '_', current['name']).lstrip('.').strip('_')[:100] or 'akuz.log'
    dest = cfg.local_dest / ('akuz_v4_win_' + selected['id'][:18] + '_' + safe)
    part = cfg.local_dest / (dest.name + '.part')
    if dest.exists() or part.exists():
        raise FetchError('Снимок с таким именем уже существует вне индекса. Проверьте downloads.')
    try:
        with source.open('rb') as stream:
            before = os.fstat(stream.fileno())
            if selected.get('inode') and _identity(before) != (selected['device'], selected['inode']):
                raise FetchError('Файл ротирован до открытия снимка')
            bound = before.st_size
            if bound <= 0:
                raise FetchError('Файл пока пустой')
            notify(f'Скачиваю первые {bound} байт файла {current["name"]}…')
            copied = 0
            digest_stream = hashlib.sha256()
            with part.open('xb') as target:
                while copied < bound:
                    block = stream.read(min(256*1024, bound-copied))
                    if not block:
                        raise FetchError('Файл усечён во время чтения')
                    target.write(block)
                    digest_stream.update(block)
                    copied += len(block)
            after = os.fstat(stream.fileno())
        # Path identity is checked *after* closing the handle too.
        pathname = source.stat()
        if _identity(before) is not None and (
            _identity(after) != _identity(before) or _identity(pathname) != _identity(before)
        ):
            raise FetchError('Файл заменён во время чтения')
        if after.st_size < bound or pathname.st_size < bound:
            raise FetchError('Файл усечён во время чтения')
        active = (current['size'] != bound or after.st_size != bound or
                  pathname.st_size != bound or after.st_mtime_ns != before.st_mtime_ns)
        tail = 0
        if active and not _ends_with_newline(part):
            tail = _discard_incomplete_tail(part)
            notify(f'Активный Windows-журнал: исключено {tail} байт незавершённой строки')
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
        raise FetchError('Ошибка чтения сетевого журнала Windows: '+str(exc)) from exc
    finally:
        part.unlink(missing_ok=True)
