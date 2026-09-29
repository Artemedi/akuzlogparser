#!/usr/bin/env python3
"""AKUZ Explorer: localhost inventory, dated reports and error analytics."""
from __future__ import annotations
import argparse
import errno
from collections import Counter
from contextlib import ExitStack, nullcontext
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
from time import perf_counter
from urllib.parse import unquote, urlsplit
import webbrowser

from akuz_fetch import (DeltaResumeFallback, FetchError, fetch_selected,
                        list_remote, load_config, selected_snapshot_path)
from akuz_windows import fetch_windows, list_windows, load_windows_config
from akuz_local import fetch_local, list_local, load_local_config, verify_local_source_sha
from akuz_html_explorer import generate
from akuz_log_parser import event_stream
from akuz_publication import (intent_path, recover_report, retire_indexed_intent,
                              retire_intent, write_intent)
from akuz_derived_spool import SpoolWriter, replay, verified_next, verify_exhausted
from akuz_store import (cached_download, cached_report, clear_cache, key_for,
                         load_store, report_summary, save_store, date_from_log_name, sha256)
from akuz_store_lock import inventory_transaction

from akuz_runtime import DOCUMENTS, app_root, prepare_runtime
from akuz_instance_lock import InstanceBusy, exclusive_instance
from akuz_process_fetch import (ProcessFetch, ProcessFetchError,
                                ProcessFetchUnsafeError,
                                owned_snapshot_promotion_guard,
                                remove_owned_snapshot, ssh_fetch_child)
from akuz_win_job_spawn import get_job_bound_spawn_context
from akuz_version import __version__
from akuz_diagnostics import event as perf_event, phase as perf_phase

def _phase11_link_fallback_allowed(exc: OSError) -> bool:
    """Only capability/volume limitations may downgrade promotion to serial."""
    portable = {
        errno.EXDEV,
        getattr(errno, "ENOTSUP", errno.EXDEV),
        getattr(errno, "EOPNOTSUPP", errno.EXDEV),
    }
    if getattr(exc, "errno", None) in portable:
        return True
    if os.name == "nt" and getattr(exc, "winerror", None) in {1, 17, 50}:
        # ERROR_INVALID_FUNCTION / NOT_SAME_DEVICE / NOT_SUPPORTED.
        return True
    return False


ROOT = app_root()
STATIC = {'index.html', 'event.html', 'errors.html', 'errors.js', 'style.css', 'common.js', 'index.js',
          'event.js', 'app_controls.js', 'README_EXPLORER.md', 'README_START_HERE.md', 'README_ANALYTICS.md'} | set(DOCUMENTS)
CONTENT_TYPE = {'.html':'text/html; charset=utf-8', '.js':'application/javascript; charset=utf-8',
                '.css':'text/css; charset=utf-8', '.md':'text/markdown; charset=utf-8'}
MAX_SELECTED = 30


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.busy = False
        self.stage = 'Готов к работе'
        self.error = ''
        self.result = None
        self.listing = []
        self.source = 'linux'
        self.local_path = ''
        self.started = None
        self.notice = ''

    def snapshot(self):
        with self.lock:
            return dict(busy=self.busy, stage=self.stage, error=self.error,
                        result=self.result, listing=self.listing, source=self.source,
                         local_path=self.local_path,
                        started=self.started, notice=self.notice)

    def set_stage(self, stage):
        with self.lock:
            self.stage = stage


STATE = State()


def safe_error(exc, root):
    message = str(exc)
    try:
        cfg = load_config(root/'ConnectConf.cfg', root)
        for secret in (cfg.password, cfg.sudo_password):
            if secret:
                message = message.replace(secret, '***')
    except Exception:
        pass
    return message[:700] or 'Неизвестная ошибка'


def _worker(state, root, action, *args):
    try:
        with perf_phase(root, 'worker.' + action.__name__):
            action(root, state, *args)
    except Exception as exc:
        with state.lock:
            state.error = safe_error(exc, root)
            state.stage = 'Операция не выполнена'
    finally:
        with state.lock:
            state.busy = False


def source_config(root: Path, source: str, local_path: str = ''):
    if source == 'linux':
        return load_config(root/'ConnectConf.cfg', root)
    if source == 'windows':
        return load_windows_config(root/'ConnectConf.cfg', root)
    if source == 'local':
        return load_local_config(local_path, root)
    raise FetchError('Неизвестный источник журналов')


def source_list(cfg, source, notify):
    if source == 'windows':
        return list_windows(cfg, notify)
    if source == 'local':
        return list_local(cfg, notify)
    return list_remote(cfg, notify)


def source_fetch(cfg, source, remote, notify):
    if source == 'windows':
        return fetch_windows(cfg, remote, notify)
    if source == 'local':
        return fetch_local(cfg, remote, notify)
    return fetch_selected(cfg, remote, notify)


def perform_list(root: Path, state: State, list_fn=list_remote, source='linux', local_path=''):
    cfg = source_config(root, source, local_path)
    with perf_phase(root, 'inventory.list'):
        listing = list_fn(cfg, state.set_stage) if source == 'linux' else source_list(cfg, source, state.set_stage)
    perf_event(root, 'inventory.list', 'count', files=len(listing))
    store = load_store(root)
    for file in listing:
        file['date'] = date_from_log_name(file['name'])
        file['cached'] = file['id'] in store['downloads'] and cached_download(store, file['id']) is not None
    with state.lock:
        state.listing = listing
        state.source = source
        state.local_path = cfg.remote_log_dir if source == 'local' else ''
        state.notice = f'Найдено {len(listing)} журналов'
        state.stage = 'Список файлов обновлён'


def _fresh_report_id(root: Path):
    for _ in range(10):
        rid = 'v4_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + secrets.token_hex(4)
        marker = intent_path(root, rid)
        draft = marker.with_suffix('.json.tmp')
        reserved = (root/'reports'/rid, root/'reports'/(rid+'.building'), marker, draft)
        if all(not path.exists() and not path.is_symlink() for path in reserved):
            return rid
    raise FetchError('Не удалось назначить идентификатор отчёта')


def _publish(root, store, key, raw_path, base, sources, label, kind,
             gen_fn=generate, input_bytes=None):
    old = cached_report(store, key, root)
    if old is not None:
        retire_indexed_intent(root, old, key, sources, label, kind)
        return dict(old, url='/reports/'+old['id']+'/index.html', reused=True)
    recovered = recover_report(root, store, key, sources, label, kind)
    if recovered is not None:
        return recovered
    rid = _fresh_report_id(root)
    parent = root/'reports'
    parent.mkdir(exist_ok=True)
    temp = parent/(rid+'.building')
    final = parent/rid
    intent = None
    try:
        with perf_phase(root, 'report.generate',
                        input_bytes=raw_path.stat().st_size if input_bytes is None else input_bytes):
            meta = gen_fn(raw_path, temp, base, 1000, 35)
        perf_event(root, 'report.generate', 'summary', events=meta['events'],
                   chunks=meta.get('chunks', 0))
        if not (temp/'index.html').is_file() or not (temp/'data'/'catalog.js').is_file():
            raise FetchError('Генератор не сохранил необходимые файлы отчёта')
        # Include provenance in a local file; static server deliberately does not serve it.
        (temp/'provenance.json').write_text(json.dumps(dict(sources=sources, kind=kind,
            generated=datetime.now().isoformat(timespec='seconds'), events=meta['events']),
            ensure_ascii=False, indent=2), encoding='utf-8')
        value = dict(id=rid, key=key, label=label, kind=kind, sources=sources,
            events=meta['events'], lines=meta['physical_lines'],
            created=datetime.now().isoformat(timespec='seconds'))
        # Do not trust hashes from an injected/custom generator.
        producer_hashes = meta.pop('_output_hashes', None)
        trusted = gen_fn is generate or getattr(gen_fn, '_akuz_builtin_generator', False)
        intent = write_intent(root, value, temp/'provenance.json',
                              output_hashes=producer_hashes if trusted else None)
        temp.rename(final)
        store['reports'][rid] = value
        try:
            with perf_phase(root, 'report.inventory_save'):
                save_store(root, store)
        except Exception:
            store['reports'].pop(rid, None)
            if final.exists():
                shutil.rmtree(final)
            raise
        return dict(value, url='/reports/'+rid+'/index.html', reused=False)
    finally:
        if intent is not None:
            retire_intent(intent)
        if temp.exists():
            shutil.rmtree(temp)


def _iter_combined_sources(selected, base: date, trace_root: Path | None = None,
                           derived_spools=None):
    """Yield combined events without materializing a second copy of all texts.

    Source identities, dates, original lines and ordering match the historical
    JSONL merger. The optional trace measures end-to-end stream consumption;
    active_s counts only generator-side time (parse + per-event augmentation),
    not the consumer's processing while this generator is suspended.
    """
    total_lines = 0
    count = 0
    started = perf_counter()
    active = 0.0
    resume = perf_counter()
    for index, item in enumerate(sorted(
            selected, key=lambda x:(x['date'], x['remote']['mtime'], x['remote']['name'])), 1):
        d = date.fromisoformat(item['date'])
        source = item['remote']['name'] + ' · ' + item['date']
        stats = Counter()
        before_source = count
        before_active = active
        spool_entry = (derived_spools.get(
            (item['remote']['path'], item['sha'], item['date']))
            if derived_spools else None)
        spool_path, expected_sha = spool_entry if spool_entry else (None, None)
        if spool_path:
            try:
                spool_valid = (
                    Path(spool_path).is_file()
                    and sha256(spool_path) == expected_sha)
            except OSError:
                spool_valid = False
            if not spool_valid:
                # The derived spool is disposable acceleration only. A missing
                # or damaged sidecar must never make combined less reliable
                # than the historical raw event_stream path.
                if trace_root is not None:
                    perf_event(
                        trace_root, 'derived.spool', 'fallback',
                        source_index=index, enabled=0)
                spool_path = None
        with (replay(spool_path) if spool_path else nullcontext(None)) as spooled:
            for ev in event_stream(item['local'], stats):
                if spooled is not None:
                    ev['_phase9_derived'] = verified_next(spooled, ev)
                count += 1
                if trace_root is not None and count % 50000 == 0:
                    perf_event(trace_root, 'combined.stream', 'progress', events=count,
                               elapsed_s=round(perf_counter() - started, 3),
                               active_s=round(active, 3))
                original_start, original_end = ev['start_line'], ev['end_line']
                ev['original_start_line'] = original_start
                ev['original_end_line'] = original_end
                ev['source_event_id'] = ev['event_id']
                ev['source_file'] = source
                ev['event_id'] = count
                ev['day_offset'] = (d-base).days + ev['day_offset']
                ev['date'] = (base + timedelta(days=ev['day_offset'])).isoformat()
                ev['start_line'] = total_lines + original_start
                ev['end_line'] = total_lines + original_end
                active += perf_counter() - resume
                yield ev
                resume = perf_counter()
            if spooled is not None:
                verify_exhausted(spooled)
        total_lines += stats['physical_lines']
        if trace_root is not None:
            perf_event(trace_root, 'combined.source', 'done', source_index=index,
                       source_events=count-before_source, total_events=count,
                       source_active_s=round(active-before_active, 3))
    if count == 0:
        raise FetchError('В выбранных файлах не обнаружены события AKUZ')
    if trace_root is not None:
        perf_event(trace_root, 'combined.stream', 'summary',
                   events=count, lines=total_lines,
                   elapsed_s=round(perf_counter() - started, 3),
                   active_s=round(active, 3))


def _combine_sources(selected, scratch: Path, base: date):
    """Keep the legacy JSONL merger for CLI/tests/custom generators."""
    count = total_lines = 0
    with scratch.open('w', encoding='utf-8', newline='\n') as output:
        for ev in _iter_combined_sources(selected, base, scratch.parent.parent):
            count = ev['event_id']
            total_lines = ev['end_line']
            output.write(json.dumps(ev, ensure_ascii=False, separators=(',', ':'))+'\n')
    return count, total_lines


def _phase11_prefetch_prefix(root: Path) -> str:
    owner = key_for('phase11-prefetch-owner', str(Path(root).resolve()))[:12]
    return '.akuz-phase11-prefetch-' + owner + '-'


def _cleanup_phase11_prefetch_orphans(root: Path, local_dest: Path) -> int:
    """Remove only this app-root's owned stale prefetch workspaces."""
    prefix = _phase11_prefetch_prefix(root)
    removed = 0
    local_dest = Path(local_dest).resolve()
    if not local_dest.is_dir():
        return 0
    for candidate in local_dest.glob(prefix + '*'):
        # Never follow an owned-name symlink outside local_dest. The directory
        # entry itself is ours to remove; its target is not.
        if candidate.is_symlink():
            try:
                candidate.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
            continue
        try:
            candidate.resolve().relative_to(local_dest)
        except (OSError, ValueError):
            continue
        if candidate.is_file():
            try:
                candidate.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
        elif candidate.is_dir():
            try:
                shutil.rmtree(candidate)
                removed += 1
            except OSError:
                pass
    return removed


def _phase12_delta_owner(root: Path) -> str:
    return key_for(
        "phase12-delta-owner", str(Path(root).resolve()))[:12]


def _cleanup_phase12_delta_orphans(root: Path, local_dest: Path) -> int:
    """Remove only temp files owned by this exact app root."""
    prefix = ".akuz-phase12-" + _phase12_delta_owner(root) + "-"
    local_dest = Path(local_dest).resolve()
    if not local_dest.is_dir():
        return 0
    removed = 0
    for candidate in local_dest.glob(prefix + "*"):
        # Production Phase 12 creates files only. An owned-name symlink is safe
        # to unlink as a directory entry; never follow its target.
        if candidate.is_symlink():
            try:
                candidate.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
            continue
        try:
            if candidate.parent.resolve() != local_dest:
                continue
        except OSError:
            continue
        if candidate.is_file():
            try:
                candidate.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


def _phase12_delta_requested(env=None) -> bool:
    """Parse the default-off Phase 12 rollback switch."""
    values = os.environ if env is None else env
    name = 'AKUZ_PHASE12_DELTA_RESUME'
    if name not in values:
        return False
    flag = str(values[name]).strip().lower()
    if flag in ('1', 'true', 'yes', 'on'):
        return True
    if flag in ('0', 'false', 'no', 'off'):
        return False
    raise FetchError('Неверное значение AKUZ_PHASE12_DELTA_RESUME')


def _phase12_resume_candidate(store, cfg, remote):
    """Return the largest proven prior prefix for this exact SSH identity."""
    device, inode = remote.get('device'), remote.get('inode')
    current_size = remote.get('size')
    if (device is None or inode is None
            or not isinstance(current_size, int)
            or isinstance(current_size, bool) or current_size <= 0):
        return None
    try:
        local_root = Path(cfg.local_dest).resolve()
    except OSError:
        return None
    candidates = []
    for entry in store.get('downloads', {}).values():
        if (not isinstance(entry, dict)
                or entry.get('host') != cfg.host
                or entry.get('remote') != remote.get('path')):
            continue
        snapshot = entry.get('snapshot')
        if (not isinstance(snapshot, dict)
                or snapshot.get('delta_proof_version') != 1
                or (snapshot.get('device'), snapshot.get('inode'))
                   != (device, inode)):
            continue
        size = entry.get('size')
        if (not isinstance(size, int) or isinstance(size, bool)
                or size <= 0 or size >= current_size
                or snapshot.get('stored_bytes') != size):
            continue
        digest = entry.get('sha256')
        if (not isinstance(digest, str)
                or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
            continue
        try:
            path = Path(entry['path'])
            resolved = path.resolve(strict=True)
            resolved.relative_to(local_root)
            st = path.lstat()
        except (KeyError, TypeError, OSError, ValueError):
            continue
        if path.is_symlink() or not path.is_file() or st.st_size != size:
            continue
        candidates.append((size, str(path), entry))
    if not candidates:
        return None
    # Prefer the largest proven prefix. Path is only a deterministic tie-breaker.
    return max(candidates, key=lambda row: (row[0], row[1]))[2]


def _phase11_process_requested(env=None) -> bool:
    """Parse the default-on rollback switch; explicit empty is invalid."""
    values = os.environ if env is None else env
    name = 'AKUZ_PHASE11_PROCESS_PREFETCH'
    if name not in values:
        return True
    flag = str(values[name]).strip().lower()
    if flag in ('1', 'true', 'yes', 'on'):
        return True
    if flag in ('0', 'false', 'no', 'off'):
        return False
    raise FetchError('Неверное значение AKUZ_PHASE11_PROCESS_PREFETCH')


def _phase11_process_allowed(process_requested, source, selections,
                             fetch_fn, gen_fn, client_os=None) -> bool:
    """Production Phase 11 policy.

    source == 'linux' is the SSH-to-Linux-server adapter label. The accepted
    multiprocessing runtime is Windows portable only because hard parent-exit
    containment requires a KILL_ON_JOB_CLOSE Job Object.
    """
    platform = os.name if client_os is None else client_os
    return (
        bool(process_requested) and platform == 'nt'
        and source == 'linux' and len(selections) > 1
        and getattr(fetch_fn, '_akuz_process_prefetch_compatible', False)
        and gen_fn is generate)


def perform_build(root: Path, state: State, selections,
                  fetch_fn=fetch_selected, gen_fn=generate, refresh_remote=False,
                  use_derived_spool=None):
    with inventory_transaction(root):
        return _perform_build_transaction_body(
            root, state, selections, fetch_fn, gen_fn, refresh_remote,
            use_derived_spool)


def _perform_build_transaction_body(root: Path, state: State, selections,
                  fetch_fn=fetch_selected, gen_fn=generate, refresh_remote=False,
                  use_derived_spool=None):
    if use_derived_spool is None:
        use_derived_spool = os.environ.get('AKUZ_PHASE9_DERIVED_SPOOL', '1').strip().lower() not in ('0', 'false', 'no', 'off')
    process_requested = _phase11_process_requested()
    delta_resume_requested = _phase12_delta_requested()
    with state.lock:
        source = state.source
        local_path = state.local_path
    process_allowed = (
        not delta_resume_requested
        and _phase11_process_allowed(
            process_requested, source, selections, fetch_fn, gen_fn))

    (root/'cache').mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        spool_root = None
        if use_derived_spool and gen_fn is generate and len(selections) >= 2:
            folder = stack.enter_context(tempfile.TemporaryDirectory(
                prefix='akuz-phase9-derived-', dir=root/'cache'))
            spool_root = Path(folder)

        process_prefetch_root = None
        phase11_cfg = None
        phase11_cleanup_ok = True
        if source == 'linux':
            phase11_cfg = source_config(root, source, local_path)
            try:
                phase11_cfg.local_dest.mkdir(parents=True, exist_ok=True)
                orphan_count = _cleanup_phase11_prefetch_orphans(
                    root, phase11_cfg.local_dest)
                if orphan_count:
                    perf_event(root, 'process.prefetch', 'orphan_cleanup',
                               removed=orphan_count)
                if delta_resume_requested:
                    delta_orphans = _cleanup_phase12_delta_orphans(
                        root, phase11_cfg.local_dest)
                    if delta_orphans:
                        perf_event(
                            root, 'source.ssh.delta', 'orphan_cleanup',
                            removed=delta_orphans)
            except OSError:
                phase11_cleanup_ok = False
                perf_event(root, 'process.prefetch', 'cleanup_failed', enabled=0)
        if process_allowed and phase11_cfg is not None and phase11_cleanup_ok:
            try:
                folder = stack.enter_context(tempfile.TemporaryDirectory(
                    prefix=_phase11_prefetch_prefix(root),
                    dir=phase11_cfg.local_dest))
                process_prefetch_root = Path(folder)
            except OSError:
                perf_event(root, 'process.prefetch', 'setup_fallback', enabled=0)

        return _perform_build(
            root, state, selections, fetch_fn, gen_fn, refresh_remote,
            spool_root, process_prefetch_root,
            delta_resume_requested=delta_resume_requested)


def _perform_build(root, state, selections, fetch_fn, gen_fn,
                   refresh_remote, spool_root, process_prefetch_root=None,
                   delta_resume_requested=False):
    build_started = perf_counter()
    with state.lock:
        source = state.source
        local_path = state.local_path
        listed = {f['id']:f for f in state.listing}
    cfg = source_config(root, source, local_path)
    store = load_store(root)
    if refresh_remote:
        # A cached report based on an old visible inventory must not mask a grown file.
        # Reconcile selections by the trusted previously-listed paths, not browser paths.
        state.set_stage('Проверяю актуальные размеры выбранных журналов…')
        fresh = source_list(cfg, source, state.set_stage)
        for file in fresh:
            file['date'] = date_from_log_name(file['name'])
        by_path = {f['path']:f for f in fresh}
        reconciled = []
        for choice in selections:
            old = listed.get(choice['id'])
            if old is None or old['path'] not in by_path:
                raise FetchError('Выбранный файл исчез или ротирован. Обновите список файлов.')
            fresh_file = by_path[old['path']]
            if old.get('inode') is not None and (old.get('device'), old['inode']) != (fresh_file.get('device'), fresh_file.get('inode')):
                raise FetchError('Выбранный файл ротирован после открытия списка. Обновите список файлов.')
            reconciled.append(dict(id=fresh_file['id'], date=choice.get('date','')))
        selections = reconciled
        with state.lock:
            state.listing = fresh
        listed = {f['id']:f for f in fresh}
    if not listed:
        raise FetchError('Сначала обновите список файлов')
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_SELECTED:
        raise FetchError(f'Выберите от 1 до {MAX_SELECTED} файлов')
    ids = [s['id'] for s in selections]
    if len(set(ids)) != len(ids) or any(fid not in listed for fid in ids):
        raise FetchError('Выбор устарел или содержит повторяющиеся файлы; обновите список')
    dates = {}
    for selected in selections:
        value = selected.get('date', '')
        if not isinstance(value, str):
            raise FetchError('Неверный формат даты')
        value = value or date_from_log_name(listed[selected['id']]['name'])
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise FetchError('Дата первой записи должна быть YYYY-MM-DD') from exc
        dates[selected['id']] = value
    # Full SHA verification of original local .log is deliberately opt-in:
    # hashing each warm source can cost gigabytes of extra disk reads.
    local_sha_flag = os.environ.get('AKUZ_VERIFY_LOCAL_SOURCE_SHA', '0').strip().lower()
    if source == 'local' and local_sha_flag not in (
            '0', 'false', 'no', 'off', '', '1', 'true', 'yes', 'on'):
        raise FetchError('Неверное значение AKUZ_VERIFY_LOCAL_SOURCE_SHA')
    verify_local_origin = (source == 'local' and
                           local_sha_flag in ('1', 'true', 'yes', 'on'))
    reports = []
    files = []
    spools = {}
    skipped = []
    source_seen = set()  # separate paths may contain independent identical events
    active_count = 0
    dropped_bytes = 0
    fresh_downloads = 0
    restore_downloads = 0
    singles_new = 0
    singles_reused = 0
    process_prefetch_downloads = 0
    process_prefetch_child_cpu_s = 0.0
    delta_resume_downloads = 0
    delta_resume_fallbacks = 0
    prefetched_downloads = {}
    process_ctx = (get_job_bound_spawn_context()
                   if process_prefetch_root is not None else None)

    def record_capture(details):
        nonlocal active_count, dropped_bytes
        if details.get('active'):
            active_count += 1
            dropped_bytes += details.get('dropped_tail_bytes', 0)

    def download(remote):
        nonlocal delta_resume_downloads, delta_resume_fallbacks
        with perf_phase(root, 'source.fetch', bytes_expected=remote.get('size', 0),
                        source_kind={'linux': 1, 'windows': 2, 'local': 3}.get(source, 0)):
            if source == 'linux':
                supports_trace = getattr(
                    fetch_fn, '_akuz_process_prefetch_compatible', False)
                supports_delta = getattr(
                    fetch_fn, '_akuz_delta_resume_compatible', False)
                resume = (
                    _phase12_resume_candidate(store, cfg, remote)
                    if delta_resume_requested and supports_delta else None)
                if resume is not None:
                    resume = dict(resume)
                    resume["_delta_owner"] = _phase12_delta_owner(root)

                def call_ssh(resume_entry=None):
                    kwargs = {}
                    if supports_trace:
                        kwargs['trace_root'] = root
                    if resume_entry is not None:
                        kwargs['resume'] = resume_entry
                    return fetch_fn(cfg, remote, state.set_stage, **kwargs)

                if resume is not None:
                    state.set_stage(
                        'Проверяю докачивание только добавленных байтов…')
                    try:
                        result = call_ssh(resume)
                    except DeltaResumeFallback:
                        delta_resume_fallbacks += 1
                        perf_event(
                            root, 'source.ssh.delta', 'fallback_full',
                            enabled=0)
                        state.set_stage(
                            'Delta не доказан; выполняю полную безопасную загрузку…')
                        result = call_ssh()
                    else:
                        delta_resume_downloads += 1
                else:
                    result = call_ssh()
            else:
                result = source_fetch(cfg, source, remote, state.set_stage)
        path, digest = result[:2]
        details = dict(result[2]) if len(result) > 2 else {}
        # Seed proof metadata only while Phase 12 opt-in is active. Default
        # v4.8 inventory bytes/semantics remain unchanged.
        if source == 'linux' and delta_resume_requested:
            device, inode = remote.get('device'), remote.get('inode')
            if (isinstance(device, int) and not isinstance(device, bool)
                    and isinstance(inode, int) and not isinstance(inode, bool)):
                details['device'] = device
                details['inode'] = inode
                details['delta_proof_version'] = 1
        perf_event(root, 'source.fetch', 'summary', bytes_saved=path.stat().st_size,
                   source_kind={'linux': 1, 'windows': 2, 'local': 3}.get(source, 0))
        record_capture(details)
        return path, digest, details

    def start_next_prefetch(current_index):
        if process_prefetch_root is None or current_index >= len(ids):
            return None
        next_fid = ids[current_index]
        if next_fid in store['downloads'] or next_fid in prefetched_downloads:
            return None
        # Defensive against an inventory changing underneath a future caller:
        # process-prefetch is optional and must never surface a raw KeyError.
        # The historical serial/reconciliation path remains authoritative.
        next_remote = listed.get(next_fid)
        if next_remote is None:
            return None
        expected_size = next_remote.get('size')
        if (not isinstance(expected_size, int) or isinstance(expected_size, bool)
                or expected_size < 0):
            # Production prefetch must always be bound to the refreshed
            # inventory size. Fall back to the historical serial fetch when
            # the adapter cannot provide that proof.
            return None
        final = selected_snapshot_path(cfg, next_remote)
        if final.exists():
            return None
        target = process_prefetch_root / (
            f'{current_index:04d}_{next_fid[:18]}.log')
        operation = ProcessFetch(
            process_ctx, ssh_fetch_child, (cfg, next_remote), target,
            poll_timeout_s=300, join_timeout_s=20, kill_timeout_s=10,
            expected_listed_bytes=expected_size,
            require_kill_job=(os.name == 'nt'),
            safe_ipc=True)
        try:
            operation.start()
        except ProcessFetchUnsafeError:
            raise
        except Exception:
            try:
                operation.close()
            except ProcessFetchUnsafeError:
                raise
            except Exception:
                pass
            perf_event(root, 'process.prefetch', 'start_fallback',
                       source_index=current_index + 1, enabled=0)
            return None
        perf_event(root, 'process.prefetch', 'start',
                   source_index=current_index + 1,
                   bytes_expected=next_remote.get('size', 0))
        return next_fid, next_remote, operation

    def finish_prefetch(prefetch):
        nonlocal process_prefetch_downloads, process_prefetch_child_cpu_s
        if prefetch is None:
            return
        next_fid, next_remote, operation = prefetch
        try:
            result = operation.finish()
        except ProcessFetchUnsafeError:
            # A live child or unclosed Windows Job can still write to the
            # owned snapshot. Starting a serial fetch now would create two
            # writers, so this is a hard reliability failure, not fallback.
            raise
        except Exception:
            # Prefetch is an optimization only. Any ordinary child/IPC/
            # validation failure must not make the default path less reliable
            # than the historical serial scheduler. The next loop iteration
            # performs the ordinary fetch and re-applies all source checks.
            try:
                operation.close()
            except ProcessFetchUnsafeError:
                raise
            perf_event(root, 'process.prefetch', 'finish_fallback',
                       source_index=ids.index(next_fid) + 1, enabled=0)
            state.set_stage(
                'Предзагрузка следующего файла не удалась; '
                'продолжаю обычной загрузкой…')
            return
        def cleanup_prefetch_temp_and_operation():
            cleanup_error = None
            try:
                remove_owned_snapshot(
                    result.path, expected_identity=result.cleanup_identity)
            except BaseException as exc:
                cleanup_error = exc
            try:
                operation.close()
            except BaseException as close_exc:
                if cleanup_error is not None:
                    try:
                        close_exc.add_note(
                            'Phase 11 temp cleanup also failed: ' +
                            type(cleanup_error).__name__)
                    except BaseException:
                        pass
                raise
            if cleanup_error is not None:
                raise cleanup_error

        final = selected_snapshot_path(cfg, next_remote)
        if final.exists():
            cleanup_prefetch_temp_and_operation()
            raise FetchError(
                'Снимок следующего файла уже появился вне индекса. Проверьте downloads.')
        details = dict(result.metadata)
        # The child IPC intentionally omits the remote path. Reconstruct it
        # from the trusted refreshed inventory in the parent so persisted
        # snapshot metadata remains byte/semantic-compatible with serial fetch.
        details["remote_path"] = next_remote["path"]

        def rollback_prefetch_inventory(reason):
            store['downloads'].pop(next_fid, None)
            try:
                save_store(root, store)
            except BaseException as exc:
                perf_event(
                    root, 'process.prefetch',
                    'rollback_inventory_save_failed',
                    source_index=ids.index(next_fid) + 1,
                    reason=reason,
                    error_kind=type(exc).__name__,
                    errno=(getattr(exc, 'errno', None) or 0),
                    winerror=(getattr(exc, 'winerror', None) or 0))
                raise ProcessFetchUnsafeError(
                    'Не удалось надёжно сохранить откат индекса '
                    'предзагрузки') from exc

        def rollback_and_cleanup(reason):
            rollback_error = None
            try:
                rollback_prefetch_inventory(reason)
            except BaseException as exc:
                rollback_error = exc
            try:
                cleanup_prefetch_temp_and_operation()
            except BaseException as cleanup_exc:
                if rollback_error is not None:
                    try:
                        cleanup_exc.add_note(
                            'Phase 11 inventory rollback also failed: ' +
                            type(rollback_error).__name__)
                    except BaseException:
                        pass
                raise
            if rollback_error is not None:
                raise rollback_error

        # Commit inventory BEFORE promotion. If the parent hard-exits before
        # promotion, restart sees an indexed missing path and safely refetches.
        store['downloads'][next_fid] = dict(
            path=str(final), sha256=result.digest, size=result.bytes,
            host=cfg.host, remote=next_remote['path'],
            mtime=next_remote['mtime'], snapshot=details)
        try:
            save_store(root, store)
        except BaseException:
            store['downloads'].pop(next_fid, None)
            cleanup_prefetch_temp_and_operation()
            raise

        try:
            # Freeze the exact validated Windows file+parent identity while
            # CreateHardLink resolves the source pathname. The guard omits
            # delete sharing, so the temp path cannot be swapped between SHA
            # validation and this atomic no-overwrite promotion.
            with owned_snapshot_promotion_guard(
                    result.path, result.cleanup_identity):
                os.link(result.path, final)
        except FileExistsError as exc:
            rollback_and_cleanup('final_exists')
            raise FetchError(
                'Снимок следующего файла уже появился вне индекса. '
                'Проверьте downloads.') from exc
        except OSError as exc:
            rollback_and_cleanup('link_failed')
            if _phase11_link_fallback_allowed(exc):
                perf_event(
                    root, 'process.prefetch', 'promotion_fallback',
                    source_index=ids.index(next_fid) + 1, enabled=0,
                    errno=(getattr(exc, 'errno', None) or 0),
                    winerror=(getattr(exc, 'winerror', None) or 0))
                state.set_stage(
                    'Файловая система не поддерживает атомарное принятие '
                    'предзагрузки; продолжаю обычной загрузкой…')
                return
            perf_event(
                root, 'process.prefetch', 'promotion_error',
                source_index=ids.index(next_fid) + 1,
                error_kind=type(exc).__name__,
                errno=(getattr(exc, 'errno', None) or 0),
                winerror=(getattr(exc, 'winerror', None) or 0))
            raise FetchError(
                'Не удалось атомарно принять предзагрузку') from exc

        try:
            remove_owned_snapshot(
                result.path, expected_identity=result.cleanup_identity)
        except Exception as cleanup_exc:
            # final is already atomically linked and indexed. Keep the valid
            # snapshot; app-root-scoped orphan cleanup retries temp removal on
            # the next run rather than discarding a proven final snapshot.
            perf_event(root, 'process.prefetch', 'temp_cleanup_deferred',
                       source_index=ids.index(next_fid) + 1,
                       cleanup_kind=type(cleanup_exc).__name__)
        prefetched_downloads[next_fid] = (final, result.digest, details)
        process_prefetch_downloads += 1
        if result.child_cpu_s is not None:
            process_prefetch_child_cpu_s += result.child_cpu_s
        record_capture(details)
        perf_event(
            root, 'process.prefetch', 'done',
            source_index=ids.index(next_fid) + 1,
            bytes_saved=result.bytes,
            child_fetch_s=(round(result.child_fetch_wall_s, 3)
                           if result.child_fetch_wall_s is not None else 0),
            child_cpu_s=(round(result.child_cpu_s, 3)
                         if result.child_cpu_s is not None else 0),
            ready_s=round(result.ready_latency_s, 3))
        operation.close()
    for idx, fid in enumerate(ids, 1):
        remote = listed[fid]
        chosen = dates[fid]
        state.set_stage(f'{idx}/{len(ids)} · {remote["name"]}: проверяю локальный индекс…')
        # A remote file identity + operator-chosen date gives idempotent report reuse.
        remote_key = key_for('single-remote', cfg.host, cfg.port, cfg.username, fid, chosen)
        prior = cached_report(store, remote_key, root)
        if verify_local_origin:
            # Bind the ORIGINAL path to the SHA of its own indexed snapshot.
            # A previously published report can outlive a cleared download.
            expected_source_sha = next((
                entry.get('sha256') for entry in prior.get('sources', [])
                if entry.get('host') == cfg.host
                and entry.get('remote_path') == remote['path']
            ), None) if prior else None
            if expected_source_sha is None:
                indexed = store['downloads'].get(fid)
                if (indexed and indexed.get('host') == cfg.host
                        and indexed.get('remote') == remote['path']):
                    expected_source_sha = indexed.get('sha256')
            if expected_source_sha is not None:
                state.set_stage('Проверяю полный SHA исходного локального журнала…')
                verify_local_source_sha(remote, expected_source_sha)
        if prior:
            reports.append(dict(prior, url='/reports/'+prior['id']+'/index.html', reused=True))
            singles_reused += 1
            prefetched = prefetched_downloads.pop(fid, None)
            if prefetched is not None:
                restored_path, restored_sha, details = prefetched
                restore_downloads += 1
                cached = (restored_path, restored_sha)
                state.set_stage('Предзагружен для общей выборки: ' + remote['name'])
            else:
                cached = cached_download(store, fid)
            if cached is None and len(ids) > 1:
                state.set_stage('Для общей выборки восстанавливаю исходный файл: '+remote['name'])
                restored_path, restored_sha, details = download(remote)
                restore_downloads += 1
                store['downloads'][fid] = dict(path=str(restored_path), sha256=restored_sha,
                    size=restored_path.stat().st_size, host=cfg.host, remote=remote['path'],
                    mtime=remote['mtime'], snapshot=details)
                save_store(root, store)
                cached=(restored_path, restored_sha)
            source_mark=(cfg.host,remote['path'],cached[1]) if cached else None
            if cached and source_mark not in source_seen:
                files.append(dict(remote=remote, local=cached[0], sha=cached[1], date=chosen))
                source_seen.add(source_mark)
            continue
        prefetched = prefetched_downloads.pop(fid, None)
        if prefetched is not None:
            path, digest, details = prefetched
            fresh_downloads += 1
            state.set_stage('Предзагружен: ' + remote['name'])
        else:
            cached = cached_download(store, fid)
            if cached:
                path, digest = cached
                state.set_stage('Уже загружен: ' + remote['name'])
            else:
                state.set_stage(f'{idx}/{len(ids)} · загружаю {remote["name"]}…')
                path, digest, details = download(remote)
                fresh_downloads += 1
                store['downloads'][fid] = dict(path=str(path), sha256=digest,
                    size=path.stat().st_size, host=cfg.host, remote=remote['path'],
                    mtime=remote['mtime'], snapshot=details)
                save_store(root, store)
        # Same bytes from different server paths may be independent events.
        # Deduplicate only a repeat of one identified source in this batch.
        source_mark=(cfg.host,remote['path'],digest)
        if source_mark in source_seen:
            skipped.append(remote['name'])
            continue
        source_seen.add(source_mark)
        files.append(dict(remote=remote, local=path, sha=digest, date=chosen))
        content_key = key_for('single-content', cfg.host, remote['path'], digest, chosen)
        by_content = cached_report(store, content_key, root)
        if by_content:
            record=store['reports'][by_content['id']]
            record['aliases']=list(set(record.get('aliases',[])+[remote_key]))
            save_store(root,store)
            reports.append(dict(by_content, url='/reports/'+by_content['id']+'/index.html', reused=True))
            singles_reused += 1
            continue
        state.set_stage(f'{idx}/{len(ids)} · разбираю {remote["name"]}…')
        label = remote['name'] + (' · ' + chosen if chosen else ' · дата не задана')
        source_meta = [dict(name=remote['name'], date=chosen, sha256=digest,
                            remote_path=remote['path'], host=cfg.host)]
        prefetch = start_next_prefetch(idx)
        try:
            if spool_root is not None and all(dates.values()):
                # Stable per-selection slot: a recovered single may create an
                # empty writer but must not collide with the next source spool.
                spool_path = spool_root / f'{idx-1:04d}.jsonl'
                with SpoolWriter(spool_path) as sink:
                    def single_gen(raw, out, base, chunk_size, top):
                        meta = generate(raw, out, base, chunk_size, top,
                                        derived_sink=sink)
                        if sink.count != meta['events']:
                            raise ValueError('Derived spool event count mismatch')
                        return meta
                    single_gen._akuz_builtin_generator = True
                    report = _publish(root, store, content_key, path,
                                      date.fromisoformat(chosen) if chosen else None,
                                      source_meta, label, 'single', single_gen)
                # A crash-recovered report did not execute single_gen: its empty
                # writer must never be replayed as a sidecar for combined.
                if not report['reused']:
                    spools[(remote['path'], digest, chosen)] = (spool_path, sink.sha256)
                    perf_event(root, 'derived.spool', 'summary', events=sink.count,
                               bytes_saved=sink.bytes_written)
            else:
                report = _publish(root, store, content_key, path,
                                  date.fromisoformat(chosen) if chosen else None,
                                  source_meta, label, 'single', gen_fn)
            # Store remote alias too, while preserving content-based de-duplication.
            store['reports'][report['id']]['aliases'] = list(set(
                store['reports'][report['id']].get('aliases', []) + [remote_key]))
            save_store(root, store)
            reports.append(report)
            singles_new += 1
        except BaseException as primary:
            if prefetch is not None:
                try:
                    prefetch[2].close()
                except ProcessFetchUnsafeError as cleanup_exc:
                    # Cleanup safety wins because a surviving writer makes any
                    # retry unsafe, but preserve the original publication
                    # failure as explicit exception context.
                    raise cleanup_exc from primary
                except Exception as cleanup_exc:
                    # Child is known stopped: keep the original report error
                    # and attach secondary cleanup diagnostics.
                    try:
                        primary.add_note(
                            'Phase 11 prefetch cleanup also failed: ' +
                            type(cleanup_exc).__name__)
                    except BaseException:
                        pass
            raise
        finish_prefetch(prefetch)
    combined = None
    if len(files) > 1 and all(f['date'] for f in files):
        files.sort(key=lambda f:(f['date'], f['remote']['mtime'], f['remote']['name']))
        multi_key = key_for('combined', cfg.host, [(f['remote']['path'], f['sha'], f['date']) for f in files])
        existing = cached_report(store, multi_key, root)
        if existing:
            combined = dict(existing, url='/reports/'+existing['id']+'/index.html', reused=True)
        else:
            state.set_stage(f'Объединяю {len(files)} разных файлов с привязкой к датам…')
            (root/'cache').mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', prefix='akuz-v4-merge-',
                      dir=root/'cache', delete=False) as tmp:
                scratch = Path(tmp.name)
            try:
                first = date.fromisoformat(files[0]['date'])
                sources = [dict(name=f['remote']['name'], date=f['date'],
                                sha256=f['sha'], remote_path=f['remote']['path'], host=cfg.host) for f in files]
                label = f'Общая выборка · {files[0]["date"]} — {files[-1]["date"]} · {len(files)} файлов'
                if gen_fn is generate:
                    # The normal app path streams joined events directly to the
                    # existing generator; only the disposable marker file stays.
                    source_bytes = sum(f['local'].stat().st_size for f in files)
                    def stream_gen(raw, out, base, chunk_size, top):
                        return generate(raw, out, base, chunk_size, top,
                                        event_source=_iter_combined_sources(files, base, root, spools),
                                        input_bytes=source_bytes,
                                        derived_hook=(lambda ev: ev.pop('_phase9_derived', None))
                                        if spools else None)
                    stream_gen._akuz_builtin_generator = True
                    combined = _publish(root, store, multi_key, scratch, first,
                                        sources, label, 'combined', stream_gen,
                                        input_bytes=source_bytes)
                else:
                    # Custom generators still receive the historical JSONL file.
                    with perf_phase(root, 'combined.merge', files=len(files)):
                        count, physical_lines = _combine_sources(files, scratch, first)
                    perf_event(root, 'combined.merge', 'summary', events=count,
                               lines=physical_lines, bytes_saved=scratch.stat().st_size)
                    combined = _publish(root, store, multi_key, scratch, first,
                                        sources, label, 'combined', gen_fn)
            finally:
                scratch.unlink(missing_ok=True)
    elif len(files) > 1:
        state.set_stage('Отдельные отчёты готовы; для общего отчёта укажите даты первой записи всех файлов')
    analytics_warning = ''
    try:
        from akuz_analytics import refresh as update_analytics
        with perf_phase(root, 'analytics.refresh'):
            update_analytics(root)
    except Exception as exc:
        # The report remains usable even if a derived, rebuildable analytics
        # cache cannot be updated. Do not expose exception details in HTML.
        analytics_warning = safe_error(exc,root)
    perf_event(root, 'build.summary', 'done', selected=len(ids),
               fresh_downloads=fresh_downloads, restore_downloads=restore_downloads,
               singles_new=singles_new, singles_reused=singles_reused,
               skipped_identical=len(skipped), active_snapshots=active_count,
               process_prefetch_downloads=process_prefetch_downloads,
               process_prefetch_child_cpu_s=round(process_prefetch_child_cpu_s, 3),
               delta_resume_downloads=delta_resume_downloads,
               delta_resume_fallbacks=delta_resume_fallbacks,
               combined_status=(0 if combined is None else (1 if combined['reused'] else 2)),
               analytics_warning=bool(analytics_warning),
               elapsed_s=round(perf_counter() - build_started, 3))
    result = dict(reports=reports, combined=combined, skipped_identical=skipped,
                  active_snapshots=active_count, dropped_tail_bytes=dropped_bytes,
                  delta_resume_downloads=delta_resume_downloads,
                  delta_resume_fallbacks=delta_resume_fallbacks,
                  report_url=(combined or (reports[0] if reports else {})).get('url'),
                  analytics_warning=analytics_warning,
                  reused=all(r['reused'] for r in reports) and
                         (combined is None or combined['reused']))
    with state.lock:
        state.result = result
        state.notice = f'Готово: {len(reports)} отдельных отчётов' + (' и общая выборка' if combined else '')
        if skipped:
            state.notice += f'; идентичных файлов пропущено: {len(skipped)}'
        if active_count:
            state.notice += f'; снимков активных логов: {active_count}'
        if analytics_warning:
            state.notice += '; аналитика: '+analytics_warning
        state.stage = state.notice


def perform_build_current(root, state, selections, use_derived_spool=None):
    """For GUI selections, refresh the listing before consulting cached snapshots."""
    return perform_build(root, state, selections, refresh_remote=True,
                         use_derived_spool=use_derived_spool)


def perform_latest(root, state, source='linux', local_path=''):
    perform_list(root, state, source=source, local_path=local_path)
    with state.lock:
        latest = state.listing[0] if state.listing else None
    if latest is None:
        raise FetchError('Файлы по маске не найдены')
    # Use the filename date through perform_build; never infer it from mtime.
    perform_build(root, state, [dict(id=latest['id'], date='')])


def perform_clear(root, state, include_reports):
    with inventory_transaction(root):
        return _perform_clear_transaction_body(root, state, include_reports)


def _perform_clear_transaction_body(root, state, include_reports):
    from types import SimpleNamespace
    configs = [SimpleNamespace(local_dest=(root/'downloads').resolve())]
    for source in ('linux', 'windows'):
        try:
            configs.append(source_config(root, source))
        except FetchError:
            pass
    result = clear_cache(root, configs, load_store(root), include_reports)
    if include_reports:
        from akuz_analytics import refresh as update_analytics
        update_analytics(root)
    with state.lock:
        state.result = dict(cleanup=result)
        state.stage = 'Кэш очищен: скачанных файлов ' + str(result['downloads_removed']) + \
             ', отчётов ' + str(result['reports_removed'])
        state.notice = state.stage
        state.listing = []


def make_handler(root: Path, state: State, port: int):
    accepted_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            payload = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _host_ok(self):
            return self.headers.get('Host','').lower() in accepted_hosts

        def do_GET(self):
            # Serialize analytics reads with job admission. A direct URL or a
            # second tab must not index a partly published batch either.
            path='/'+'/'.join(Path(unquote(urlsplit(self.path).path).lstrip('/')).parts)
            analytics=(path == '/errors.html' or path.startswith('/api/analytics/') or
                       path == '/data/analytics.js' or
                       any(path.startswith('/data/'+prefix) for prefix in
                           ('error_', 'type_', 'family_')))
            if analytics:
                with state.lock:
                    if state.busy:
                        if not self._host_ok():
                            return self._json(403, {'error':'Неверный Host'})
                        return self._json(409, {'error':'Дождитесь завершения обработки всех выбранных журналов, затем откройте аналитику.'})
                    return self._serve_get()
            return self._serve_get()

        def _serve_get(self):
            if not self._host_ok():
                return self._json(403, {'error':'Неверный Host'})
            parsed = urlsplit(self.path)
            parsed = parsed._replace(path='/'+'/'.join(Path(unquote(parsed.path).lstrip('/')).parts))
            if parsed.path == '/api/status':
                return self._json(200, state.snapshot())
            if parsed.path == '/api/reports':
                return self._json(200, {'reports':report_summary(load_store(root),root)})
            if parsed.path == '/api/analytics/sources':
                from akuz_analytics import source_inventory
                return self._json(200, {'sources':source_inventory(root)})
            if parsed.path == '/errors.html':
                try:
                    from akuz_analytics import refresh as update_analytics
                    update_analytics(root)
                except Exception as exc:
                    return self._json(500, {'error':'Не удалось обновить аналитический индекс: '+safe_error(exc,root)})
            if parsed.path.startswith('/api/'):
                return self._json(404, {'error':'Не найдено'})
            # Empty initial catalog for a clean checkout, no synthetic events.
            if parsed.path == '/data/catalog.js' and not (root/'data'/'catalog.js').is_file():
                empty = {
                    'meta': dict(source='Отчёт ещё не выбран · используйте кнопки получения журналов',
                                 events=0, physical_lines=0, continuation_lines=0,
                                 component_count=0, chunks=0, chunk_size=1000,
                                 replacement_chars=0, out_of_order_timestamps=0,
                                 midnight_rollovers=0, base_date=''),
                    'rows':[], 'sources':[], 'categories':[], 'componentNames':[],
                    'components':[], 'hourly':[], 'category_counts':[],
                    'patterns':[], 'durations':[], 'requests':[]
                }
                data = ('window.AKUZ_DATA='+json.dumps(empty, ensure_ascii=False,
                    separators=(',', ':'))+';\n').encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','application/javascript; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            path = parsed.path
            if '\x00' in path or '\\' in path:
                return self._json(404, {'error':'Не найдено'})
            parts = Path(path.lstrip('/')).parts or ('index.html',)
            if any(p in ('..','.') for p in parts):
                return self._json(404, {'error':'Не найдено'})
            file = None
            if len(parts) == 1 and parts[0] in STATIC:
                file = root/parts[0]
            elif len(parts) == 2 and parts[0] == 'data' and parts[1].endswith('.js'):
                file = root/'data'/parts[1]
            elif len(parts) >= 3 and parts[0] == 'reports' and len(parts[1]) < 80:
                if len(parts) == 3 and parts[2] in STATIC:
                    file = root/'reports'/parts[1]/parts[2]
                    if parts[2] == 'app_controls.js' and (root/'app_controls.js').is_file():
                        file = root/'app_controls.js'
                elif len(parts) == 4 and parts[2] == 'data' and parts[3].endswith('.js'):
                    file = root/'reports'/parts[1]/'data'/parts[3]
            if file is None:
                return self._json(404, {'error':'Не найдено'})
            try:
                file = file.resolve(strict=True)
                file.relative_to(root.resolve())
                if not file.is_file():
                    raise FileNotFoundError
            except (OSError, ValueError):
                return self._json(404, {'error':'Не найдено'})
            self.send_response(200)
            self.send_header('Content-Type', CONTENT_TYPE.get(file.suffix, 'application/octet-stream'))
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(file.stat().st_size))
            self.end_headers()
            try:
                with file.open('rb') as f:
                    shutil.copyfileobj(f,self.wfile,256*1024)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {'error':'Неверный Host'})
            if self.headers.get('Origin','') != 'http://' + self.headers.get('Host','').lower():
                return self._json(403, {'error':'Только same-origin запросы'})
            if self.headers.get('Content-Type','').split(';')[0].strip().lower() != 'application/json':
                return self._json(415, {'error':'Нужен application/json'})
            length = self.headers.get('Content-Length','')
            if not length.isdigit() or int(length) > 16000:
                return self._json(413, {'error':'Слишком большой JSON'})
            try:
                payload = json.loads(self.rfile.read(int(length)))
            except (ValueError, UnicodeError):
                return self._json(400, {'error':'Некорректный JSON'})
            if not isinstance(payload,dict):
                return self._json(400, {'error':'Ожидается объект JSON'})
            endpoint = urlsplit(self.path).path
            if endpoint not in ('/api/list','/api/build','/api/fetch','/api/clear',
                                '/api/analytics/source-date'):
                return self._json(404, {'error':'Не найдено'})
            if endpoint == '/api/analytics/source-date':
                with state.lock:
                    if state.busy:
                        return self._json(409, {'error':'Дождитесь завершения загрузки журналов'})
                    from akuz_analytics import update_source_date
                    try:
                        result=update_source_date(root,payload.get('id'),payload.get('date'))
                    except ValueError as exc:
                        return self._json(400, {'error':str(exc)})
                    except Exception:
                        return self._json(500, {'error':'Не удалось обновить аналитические даты'})
                    return self._json(200,result)
            if endpoint in ('/api/list', '/api/fetch'):
                if payload.get('source', 'linux') not in ('linux','windows','local'):
                    return self._json(400, {'error':'Неизвестный источник'})
            if endpoint in ('/api/list', '/api/fetch') and payload.get('source') == 'local':
                local_path = payload.get('local_path')
                if not isinstance(local_path, str) or not local_path.strip() or len(local_path) > 2048:
                    return self._json(400, {'error':'Укажите абсолютный путь к локальному файлу .log или каталогу'})
            else:
                local_path = ''
            if endpoint == '/api/build':
                selected = payload.get('selections')
                with state.lock:
                    ids = {f['id'] for f in state.listing}
                if not isinstance(selected,list) or not 1 <= len(selected) <= MAX_SELECTED:
                    return self._json(400, {'error':f'Выберите от 1 до {MAX_SELECTED} файлов'})
                if any(not isinstance(s,dict) or not isinstance(s.get('id'),str)
                       or s.get('id') not in ids or not isinstance(s.get('date',''),str)
                       for s in selected) or len(set(s['id'] for s in selected)) != len(selected):
                    return self._json(400, {'error':'Некорректный выбор или устаревший список'})
            if endpoint == '/api/clear' and type(payload.get('reports',False)) is not bool:
                return self._json(400, {'error':'reports должен быть логическим значением'})
            with state.lock:
                if state.busy:
                    return self._json(409, {'error':'Операция уже выполняется'})
                state.busy=True
                state.started=datetime.now().isoformat(timespec='seconds')
                state.stage='Подготовка…'
                state.error=''
                state.result=None
                state.notice=''
            if endpoint == '/api/list':
                args=(root, perform_list, list_remote, payload.get('source', 'linux'), local_path)
            elif endpoint == '/api/fetch':
                args=(root, perform_latest, payload.get('source', 'linux'), local_path)
            elif endpoint == '/api/build':
                args=(root, perform_build_current, selected)
            else:
                args=(root, perform_clear, payload.get('reports',False))
            threading.Thread(target=_worker, args=(state,*args),
                             daemon=True, name='akuz-'+endpoint.rsplit('/',1)[-1]).start()
            return self._json(202, {'started':True})

        def log_message(self, fmt, *args):
            pass
    return Handler


def main():
    p=argparse.ArgumentParser(description=f'AKUZ Explorer {__version__} · AKUZ logs and Error Analytics')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--no-browser',action='store_true')
    p.add_argument('--self-test',action='store_true',help=argparse.SUPPRESS)
    args=p.parse_args()
    if args.self_test:
        from akuz_portable_check import run
        run()
        return
    if not 1024 <= args.port <= 65535:
        p.error('port must be 1024..65535')
    # Hold the same app-root OS lock over startup and ALL server requests,
    # independent of the requested HTTP port. A crash releases the OS lock.
    owner = exclusive_instance(ROOT)
    try:
        owner.__enter__()
    except InstanceBusy as exc:
        p.error(str(exc))
    try:
        try:
            prepare_runtime()
        except OSError as exc:
            p.error(f'Cannot prepare application files beside the executable: {exc}')
        try:
            server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(ROOT,STATE,args.port))
        except OSError as exc:
            p.error(f'Cannot start on 127.0.0.1:{args.port}: {exc}')
        print(f'AKUZ Log Explorer {__version__}: http://127.0.0.1:{args.port}/\nОстановка: Ctrl+C',flush=True)
        if not args.no_browser:
            threading.Timer(0.6, lambda:webbrowser.open(f'http://127.0.0.1:{args.port}/')).start()
        try:
            server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()

    finally:
        owner.__exit__(None, None, None)

if __name__=='__main__':
    multiprocessing.freeze_support()
    main()
