#!/usr/bin/env python3
"""Fetch a remote AKUZ log via SSH; no remote temp files, chmod or password in command.

Paramiko is imported lazily so that static preview and local tests need no SSH library.
Remote commands target Linux with GNU find/stat/head and sudo when required.
"""
from __future__ import annotations

import configparser
from contextlib import nullcontext
from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import shlex
import time
from typing import Callable

from akuz_diagnostics import event as perf_event, phase as perf_phase


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectConfig:
    host: str
    port: int
    username: str
    password: str
    sudo_password: str
    key_file: str
    remote_log_dir: str
    local_dest: Path
    file_pattern: str
    base_date: str
    compression: bool = False


def load_config(path: Path, app_root: Path) -> ConnectConfig:
    if not path.is_file():
        raise FetchError('Не найден ConnectConf.cfg. Создайте его по примеру из архива.')
    c = configparser.ConfigParser(interpolation=None)
    try:
        with path.open('r', encoding='utf-8-sig') as f:
            c.read_file(f)
        ssh, logs = c['ssh'], c['logs']
        host = ssh.get('host', '').strip()
        username = ssh.get('username', '').strip()
        remote = logs.get('remote_log_dir', '').strip()
        port = ssh.getint('port', fallback=22)
        password = ssh.get('password', '')
        sudo_password = ssh.get('sudo_password', '') or password
        key_file = ssh.get('key_file', '').strip()
        local = logs.get('local_dest', '').strip()
        pattern = logs.get('file_pattern', '*').strip() or '*'
        base_date = logs.get('base_date', '').strip()
        compression = ssh.getboolean('compression', fallback=False)
    except (configparser.Error, KeyError, ValueError) as exc:
        raise FetchError('Проверьте секции [ssh], [logs] и числовой port в ConnectConf.cfg') from exc
    if not host or not username or not remote:
        raise FetchError('Заполните host, username и remote_log_dir в ConnectConf.cfg')
    if port < 1 or port > 65535:
        raise FetchError('port должен быть от 1 до 65535')
    if not remote.startswith('/') or '\x00' in remote or '\n' in remote or '\r' in remote:
        raise FetchError('remote_log_dir должен быть абсолютным Linux-путём без переводов строк')
    if not pattern or '/' in pattern or '\\' in pattern:
        raise FetchError('file_pattern — маска имени файла, например *.log')
    if any(c in password + sudo_password for c in ('\r', '\n', '\x00')):
        raise FetchError('Пароли не должны содержать перевод строки')
    if base_date:
        from datetime import date
        try:
            date.fromisoformat(base_date)
        except ValueError as exc:
            raise FetchError('base_date должен быть YYYY-MM-DD или пустым') from exc
    dest = Path(os.path.expandvars(os.path.expanduser(local))) if local else app_root/'downloads'
    if not dest.is_absolute():
        dest = app_root/dest
    return ConnectConfig(host, port, username, password, sudo_password,
                         key_file, remote, dest.resolve(), pattern, base_date,
                         compression)


def remote_find_command(directory: str, sudo: bool, has_password: bool) -> str:
    cmd = "find -- " + shlex.quote(directory) + r" -maxdepth 1 -type f -printf '%T@\t%s\t%D\t%i\t%p\0'"
    return sudo_prefix(sudo, has_password) + cmd


def sudo_prefix(sudo: bool, has_password: bool) -> str:
    if not sudo:
        return ''
    return "sudo -S -p '' -- " if has_password else 'sudo -n -- '


def _run_capture(client, command: str, secret: str = '', max_bytes: int = 16 * 1024 * 1024):
    stdin, stdout, stderr = client.exec_command(command, timeout=90, get_pty=False)
    if secret:
        stdin.write(secret + '\n')
        stdin.flush()
    stdin.channel.shutdown_write()
    data = stdout.read(max_bytes + 1)
    if len(data) > max_bytes:
        stdout.channel.close()
        raise FetchError('Слишком большой список файлов в удалённом каталоге')
    error = stderr.read(32768).decode('utf-8', 'replace').strip()
    status = stdout.channel.recv_exit_status()
    return status, data, error


def _latest_from_listing(data: bytes, pattern: str) -> tuple[str, int]:
    best = None
    for item in data.split(b'\0'):
        if not item:
            continue
        try:
            parts = item.split(b'\t', 4)
            mtime, size, path = (parts[0],parts[1],parts[4]) if len(parts) == 5 else item.split(b'\t', 2)
            remote_path = path.decode('utf-8')
            candidate = (float(mtime), remote_path, int(size))
        except (ValueError, UnicodeError) as exc:
            raise FetchError('Недопустимое имя/метаданные файла от удалённого find') from exc
        if remote_path.rsplit('/',1)[-1].lower().endswith('.log') and fnmatch.fnmatchcase(remote_path.rsplit('/', 1)[-1], pattern):
            if best is None or (candidate[0], candidate[1]) > (best[0], best[1]):
                best = candidate
    if best is None:
        raise FetchError('Файлы, подходящие под file_pattern, не найдены')
    if best[2] < 1:
        raise FetchError('Самый свежий файл пустой; скачивание остановлено')
    return best[1], best[2]


def _clean_error(message: str, cfg: ConnectConfig) -> str:
    for secret in (cfg.password, cfg.sudo_password):
        if secret:
            message = message.replace(secret, '***')
    return message[:700]


def fetch_latest(cfg: ConnectConfig, notify: Callable[[str], None] = lambda x: None,
                 client_factory=None) -> tuple[Path, str, int]:
    """Returns (downloaded_path, remote_basename, snapshot_bytes). No remote changes."""
    if client_factory is None:
        try:
            import paramiko
        except ImportError as exc:
            raise FetchError('Установите SSH-зависимость: py -3 -m pip install -r requirements.txt') from exc
        client_factory = paramiko.SSHClient
    else:
        import paramiko  # test may inject a stub or use installed Paramiko
    client = client_factory()
    part = None
    try:
        client.load_system_host_keys()
        known_hosts = Path.home()/'.ssh'/'known_hosts'
        if known_hosts.is_file():
            client.load_host_keys(str(known_hosts))
        # Never accept unknown keys automatically; ssh manually once to confirm fingerprint.
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        kw = dict(hostname=cfg.host, port=cfg.port, username=cfg.username,
                  timeout=15, auth_timeout=25, banner_timeout=20,
                  look_for_keys=True, allow_agent=True,
                  compress=cfg.compression)
        if cfg.password:
            kw['password'] = cfg.password
        if cfg.key_file:
            kw['key_filename'] = str(Path(os.path.expanduser(cfg.key_file)).expanduser())
        notify('Подключение к SSH…')
        client.connect(**kw)
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(30)
        notify('Поиск самого свежего файла…')
        status, listing, error = _run_capture(client, remote_find_command(cfg.remote_log_dir, False, False))
        as_root = status != 0
        if as_root:
            notify('Доступ к каталогу ограничен: выполняю sudo find…')
            status, listing, error = _run_capture(
                client, remote_find_command(cfg.remote_log_dir, True, bool(cfg.sudo_password)),
                cfg.sudo_password)
            if status:
                raise FetchError('Не удалось прочитать каталог (включая sudo): ' + error)
        remote_path, _ = _latest_from_listing(listing, cfg.file_pattern)
        quoted = shlex.quote(remote_path)
        notify('Проверка размера выбранного файла…')
        # A size-bounded snapshot avoids following an actively growing file forever.
        status, size_data, error = _run_capture(client, 'stat -c %s -- ' + quoted)
        if status:
            as_root = True
            status, size_data, error = _run_capture(
                client, sudo_prefix(True, bool(cfg.sudo_password)) + 'stat -c %s -- ' + quoted,
                cfg.sudo_password)
            if status:
                raise FetchError('Не удалось получить размер файла: ' + error)
        try:
            size = int(size_data.strip())
        except ValueError as exc:
            raise FetchError('Удалённый stat вернул некорректный размер') from exc
        if size <= 0:
            raise FetchError('Файл пустой или уже перемещён ротацией')
        cfg.local_dest.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^\w.\-]+', '_', remote_path.rsplit('/', 1)[-1], flags=re.UNICODE).lstrip('.').strip('_')
        if not safe_name:
            safe_name = 'akuz.log'
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        name = f'{timestamp}_{time.time_ns() % 1000000:06d}_{safe_name}'
        final = cfg.local_dest/name
        part = cfg.local_dest/(name + '.part')
        notify('Скачивание по SSH без копии в /tmp на сервере…')
        def transfer(use_sudo: bool) -> tuple[int, int, str]:
            command = sudo_prefix(use_sudo, bool(cfg.sudo_password)) + f'head -c {size} -- ' + quoted
            stdin, stdout, stderr = client.exec_command(command, timeout=120, get_pty=False)
            if use_sudo and cfg.sudo_password:
                stdin.write(cfg.sudo_password + '\n')
                stdin.flush()
            stdin.channel.shutdown_write()
            copied = 0
            with part.open('xb') as output:
                while True:
                    block = stdout.read(256 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    if copied > size:
                        raise FetchError('Получено больше байт, чем ожидалось по stat')
                    output.write(block)
            error = stderr.read(32768).decode('utf-8', 'replace').strip()
            status = stdout.channel.recv_exit_status()
            return status, copied, error

        status, copied, error = transfer(as_root)
        if status and not as_root and copied == 0:
            part.unlink(missing_ok=True)
            notify('Для чтения выбранного файла требуется sudo…')
            status, copied, error = transfer(True)
        if status or copied != size:
            raise FetchError(f'Передача неполная: {copied} из {size} байт. ' + error)
        part.replace(final)
        part = None
        return final, remote_path.rsplit('/', 1)[-1], copied
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError('SSH/передача: ' + _clean_error(str(exc), cfg)) from exc
    finally:
        if part is not None:
            part.unlink(missing_ok=True)
        client.close()

# v4 remote inventory / selected snapshot. v3 fetch_latest remains for CLI compatibility.
import hashlib
from datetime import datetime, timezone


def _connect(cfg: ConnectConfig, notify=lambda msg: None, client_factory=None):
    if client_factory is None:
        try:
            import paramiko
        except ImportError as exc:
            raise FetchError('Требуется Paramiko: py -3 -m pip install -r requirements.txt') from exc
        client_factory = paramiko.SSHClient
    else:
        import paramiko
    client = client_factory()
    try:
        client.load_system_host_keys()
        kh = Path.home()/'.ssh'/'known_hosts'
        if kh.is_file():
            client.load_host_keys(str(kh))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        kw = dict(hostname=cfg.host, port=cfg.port, username=cfg.username,
                  timeout=15, auth_timeout=25, banner_timeout=20,
                  look_for_keys=True, allow_agent=True,
                  compress=cfg.compression)
        if cfg.password:
            kw['password'] = cfg.password
        if cfg.key_file:
            kw['key_filename'] = str(Path(os.path.expanduser(cfg.key_file)).expanduser())
        notify('Подключение к SSH…')
        client.connect(**kw)
        if client.get_transport():
            client.get_transport().set_keepalive(30)
        return client
    except Exception as exc:
        client.close()
        raise FetchError('SSH: ' + _clean_error(str(exc), cfg)) from exc


def _listing(client, cfg: ConnectConfig):
    status, data, error = _run_capture(client, remote_find_command(cfg.remote_log_dir, False, False))
    if status:
        status, data, error = _run_capture(client,
            remote_find_command(cfg.remote_log_dir, True, bool(cfg.sudo_password)), cfg.sudo_password)
    if status:
        raise FetchError('Не удалось получить список файлов: ' + _clean_error(error, cfg))
    results = []
    for record in data.split(b'\0'):
        if not record:
            continue
        try:
            parts = record.split(b'\t', 4)
            if len(parts) == 5:
                mtime_raw, size_raw, device_raw, inode_raw, filename_raw = parts
                device, inode = int(device_raw), int(inode_raw)
            else:  # For backwards-compatible test fixtures / older find output.
                mtime_raw, size_raw, filename_raw = record.split(b'\t', 2)
                device = inode = None
            path = filename_raw.decode('utf-8', 'strict')
            basename = path.rsplit('/', 1)[-1]
            size = int(size_raw)
            when = float(mtime_raw)
            if not basename or not basename.lower().endswith('.log') or size < 1 or not fnmatch.fnmatchcase(basename, cfg.file_pattern):
                continue
            if not path.startswith(cfg.remote_log_dir.rstrip('/') + '/'):
                raise ValueError('Путь вне настроенного каталога')
            token = '\0'.join((cfg.host, str(cfg.port), cfg.username, path,
                                str(size), mtime_raw.decode('ascii'),
                                str(device or ''), str(inode or '')))
            fid = hashlib.sha256(token.encode('utf-8')).hexdigest()
            results.append(dict(id=fid, name=basename, path=path, size=size,
                mtime=when, mtime_raw=mtime_raw.decode('ascii'), device=device, inode=inode,
                modified_utc=datetime.fromtimestamp(when, timezone.utc).isoformat(timespec='seconds'),
                suggested_date=datetime.fromtimestamp(when).date().isoformat()))
        except (ValueError, UnicodeError, OverflowError, OSError) as exc:
            raise FetchError('Некорректные метаданные удалённого каталога') from exc
    results.sort(key=lambda f: (f['mtime'], f['name']), reverse=True)
    return results


def list_remote(cfg: ConnectConfig, notify=lambda msg: None, client_factory=None):
    client = _connect(cfg, notify, client_factory)
    try:
        notify('Читаю каталог журналов…')
        return _listing(client, cfg)
    finally:
        client.close()


def _remote_metadata(client, cfg: ConnectConfig, quoted: str, sudo: bool = False):
    """Read dev/inode/size/mtime in ONE stat; inode detects common rotation races."""
    cmd = sudo_prefix(sudo, bool(cfg.sudo_password)) + "stat -Lc '%d:%i:%s:%Y' -- " + quoted
    status, data, error = _run_capture(client, cmd, cfg.sudo_password if sudo else '')
    if status:
        return None, error
    try:
        dev, inode, size, mtime = map(int, data.decode('ascii').strip().split(':'))
    except (ValueError, UnicodeError) as exc:
        raise FetchError('Удалённый stat вернул неверные метаданные') from exc
    return (dev, inode, size, mtime), ''


def _discard_incomplete_tail(path: Path) -> int:
    """For active captures, never feed an unfinished final physical line to parser."""
    length = path.stat().st_size
    with path.open('r+b') as stream:
        block_size = 65536
        cursor = length
        while cursor:
            start = max(0, cursor-block_size)
            stream.seek(start)
            chunk = stream.read(cursor-start)
            offset = chunk.rfind(b'\n')
            if offset >= 0:
                full = start+offset+1
                stream.truncate(full)
                return length-full
            cursor = start
    raise FetchError('В активном снимке нет завершённой строки: повторите получение позже')


def fetch_selected(cfg: ConnectConfig, selected: dict,
                   notify=lambda msg: None, client_factory=None,
                   *, trace_root: Path | None = None) -> tuple[Path, str, dict]:
    """Download byte-bounded prefix from an append-only live file, or exact static file.

    The selection must come from server-side inventory, NOT untrusted browser paths.
    Returns local path, SHA256 of actually stored bytes, capture details.
    """
    def trace(stage: str, **metrics):
        return perf_phase(trace_root, stage, **metrics) if trace_root is not None else nullcontext()

    with trace('source.ssh.connect'):
        client = _connect(cfg, notify, client_factory)
    part = None
    try:
        # The old id includes mtime and size and is expected to become stale for live logs.
        # Resolve ONLY a trusted path that was already present in server-side inventory.
        with trace('source.ssh.inventory'):
            current = next((f for f in _listing(client, cfg) if f['path'] == selected['path']), None)
        if current is None:
            raise FetchError('Удалённый файл исчез или ротирован. Обновите список файлов.')
        if selected.get('inode') is not None and (selected['device'], selected['inode']) != (current.get('device'),current.get('inode')):
            raise FetchError('Файл заменён ротацией с момента получения списка. Обновите список файлов.')
        quoted = shlex.quote(current['path'])
        notify('Фиксирую границу снимка ' + current['name'] + '…')
        with trace('source.ssh.stat_before'):
            before, err = _remote_metadata(client, cfg, quoted)
            use_sudo = False
            if before is None:
                use_sudo = True
                before, err = _remote_metadata(client, cfg, quoted, True)
        if before is None:
            raise FetchError('Не удалось проверить файл: ' + _clean_error(err, cfg))
        dev, inode, bound, first_mtime = before
        if current.get('inode') is not None and (dev, inode) != (current['device'], current['inode']):
            raise FetchError('Файл заменён ротацией до открытия снимка. Обновите список файлов.')
        if bound <= 0:
            raise FetchError('Файл пустой; завершённых событий пока нет')
        cfg.local_dest.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[^\w.\-]+', '_', current['name']).lstrip('.').strip('_')[:100] or 'akuz.log'
        # Local inventory still uses the trusted selection id; actual bytes are deduped by SHA.
        dest = cfg.local_dest / ('akuz_v4_' + selected['id'][:18] + '_' + safe)
        part = cfg.local_dest / (dest.name + '.part')
        if dest.exists():
            raise FetchError('Снимок с таким именем уже существует вне индекса. Проверьте downloads.')
        if part.exists():
            raise FetchError('Остался незавершённый .part. Проверьте downloads.')
        notify(f'Скачиваю фиксированные {bound} байт из {current["name"]}…')
        def transfer(sudo):
            cmd = sudo_prefix(sudo, bool(cfg.sudo_password)) + f'head -c {bound} -- ' + quoted
            stdin, stdout, stderr = client.exec_command(cmd, timeout=120, get_pty=False)
            if sudo and cfg.sudo_password:
                stdin.write(cfg.sudo_password + '\n')
                stdin.flush()
            stdin.channel.shutdown_write()
            h = hashlib.sha256()
            copied = 0
            transfer_started = time.perf_counter()
            next_progress = 64 * 1024 * 1024
            with part.open('xb') as output:
                while True:
                    block = stdout.read(256 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    if copied > bound:
                        raise FetchError('Сервер отправил больше байт, чем зафиксировано')
                    h.update(block)
                    output.write(block)
                    if trace_root is not None and copied >= next_progress:
                        elapsed = max(time.perf_counter() - transfer_started, 0.001)
                        perf_event(trace_root, 'source.ssh.transfer', 'progress',
                                   bytes_received=copied, bytes_expected=bound,
                                   elapsed_s=round(elapsed, 3),
                                   mib_per_s=round(copied / (1024 * 1024) / elapsed, 3))
                        next_progress = ((copied // (64 * 1024 * 1024)) + 1) * 64 * 1024 * 1024
            error = stderr.read(32768).decode('utf-8', 'replace').strip()
            rc = stdout.channel.recv_exit_status()
            if trace_root is not None:
                elapsed = max(time.perf_counter() - transfer_started, 0.001)
                perf_event(trace_root, 'source.ssh.transfer', 'summary',
                           bytes_received=copied, bytes_expected=bound,
                           elapsed_s=round(elapsed, 3),
                           mib_per_s=round(copied / (1024 * 1024) / elapsed, 3))
            return rc, copied, h.hexdigest(), error
        with trace('source.ssh.transfer', bytes_expected=bound):
            rc, copied, digest, err = transfer(use_sudo)
            if rc and not use_sudo and copied == 0:
                part.unlink(missing_ok=True)
                notify('Чтение с sudo…')
                rc, copied, digest, err = transfer(True)
                use_sudo = True
        if rc or copied != bound:
            raise FetchError(f'Передача неполная: {copied}/{bound} байт. ' + _clean_error(err, cfg))
        with trace('source.ssh.stat_after'):
            after, err = _remote_metadata(client, cfg, quoted, use_sudo)
        if after is None:
            raise FetchError('Файл ротирован или недоступен после чтения: ' + _clean_error(err, cfg))
        if after[:2] != (dev, inode):
            raise FetchError('Файл был заменён ротацией во время передачи. Повторите получение.')
        if after[2] < bound:
            raise FetchError('Файл был усечён во время передачи. Повторите получение.')
        active = (current['size'] != bound or after[2] != bound or after[3] != first_mtime)
        tail = 0
        if active and not _ends_with_newline(part):
            with trace('source.ssh.trim_rehash', bytes_expected=bound):
                tail = _discard_incomplete_tail(part)
                digest = _sha_file(part)
            notify(f'Активный лог: отброшено {tail} байт незавершённой строки')
        if active:
            notify(f'Снимок активного журнала готов: {part.stat().st_size} байт из границы {bound}')
        part.replace(dest)
        part = None
        return dest, digest, dict(active=active, captured_bytes=bound,
                                  stored_bytes=dest.stat().st_size, dropped_tail_bytes=tail,
                                  listed_bytes=selected['size'], remote_path=current['path'])
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError('Передача по SSH: ' + _clean_error(str(exc), cfg)) from exc
    finally:
        if part is not None:
            part.unlink(missing_ok=True)
        client.close()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _ends_with_newline(path: Path) -> bool:
    with path.open('rb') as stream:
        stream.seek(-1, 2)
        return stream.read(1) == b'\n'
