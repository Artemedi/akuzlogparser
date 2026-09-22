#!/usr/bin/env python3
"""AKUZ Explorer v4.1: localhost-only inventory, idempotent SSH fetch and dated reports."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
from urllib.parse import unquote, urlsplit
import webbrowser

from akuz_fetch import FetchError, fetch_selected, list_remote, load_config
from akuz_windows import fetch_windows, list_windows, load_windows_config
from akuz_html_explorer import generate
from akuz_log_parser import event_stream
from akuz_store import (cached_download, cached_report, clear_cache, key_for,
                         load_store, report_summary, save_store, date_from_log_name)

ROOT = Path(__file__).resolve().parent
STATIC = {'index.html', 'event.html', 'errors.html', 'errors.js', 'style.css', 'common.js', 'index.js',
          'event.js', 'app_controls.js', 'README_EXPLORER.md', 'README_START_HERE.md', 'README_ANALYTICS.md'}
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
        self.started = None
        self.notice = ''

    def snapshot(self):
        with self.lock:
            return dict(busy=self.busy, stage=self.stage, error=self.error,
                        result=self.result, listing=self.listing, source=self.source,
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
        action(root, state, *args)
    except Exception as exc:
        with state.lock:
            state.error = safe_error(exc, root)
            state.stage = 'Операция не выполнена'
    finally:
        with state.lock:
            state.busy = False


def source_config(root: Path, source: str):
    if source == 'linux':
        return load_config(root/'ConnectConf.cfg', root)
    if source == 'windows':
        return load_windows_config(root/'ConnectConf.cfg', root)
    raise FetchError('Неизвестный источник журналов')


def source_list(cfg, source, notify):
    return list_windows(cfg, notify) if source == 'windows' else list_remote(cfg, notify)


def source_fetch(cfg, source, remote, notify):
    return fetch_windows(cfg, remote, notify) if source == 'windows' else fetch_selected(cfg, remote, notify)


def perform_list(root: Path, state: State, list_fn=list_remote, source='linux'):
    cfg = source_config(root, source)
    listing = source_list(cfg, source, state.set_stage) if source == 'windows' else list_fn(cfg, state.set_stage)
    store = load_store(root)
    for file in listing:
        file['date'] = date_from_log_name(file['name'])
        file['cached'] = file['id'] in store['downloads'] and cached_download(store, file['id']) is not None
    with state.lock:
        state.listing = listing
        state.source = source
        state.notice = f'На сервере найдено {len(listing)} журналов'
        state.stage = 'Список файлов обновлён'


def _fresh_report_id(root: Path):
    for _ in range(10):
        rid = 'v4_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + secrets.token_hex(4)
        if not (root/'reports'/rid).exists() and not (root/'reports'/(rid+'.building')).exists():
            return rid
    raise FetchError('Не удалось назначить идентификатор отчёта')


def _publish(root, store, key, raw_path, base, sources, label, kind, gen_fn=generate):
    old = cached_report(store, key, root)
    if old is not None:
        return dict(old, url='/reports/'+old['id']+'/index.html', reused=True)
    rid = _fresh_report_id(root)
    parent = root/'reports'
    parent.mkdir(exist_ok=True)
    temp = parent/(rid+'.building')
    final = parent/rid
    try:
        meta = gen_fn(raw_path, temp, base, 1000, 35)
        if not (temp/'index.html').is_file() or not (temp/'data'/'catalog.js').is_file():
            raise FetchError('Генератор не сохранил необходимые файлы отчёта')
        # Include provenance in a local file; static server deliberately does not serve it.
        (temp/'provenance.json').write_text(json.dumps(dict(sources=sources, kind=kind,
            generated=datetime.now().isoformat(timespec='seconds'), events=meta['events']),
            ensure_ascii=False, indent=2), encoding='utf-8')
        temp.rename(final)
        value = dict(id=rid, key=key, label=label, kind=kind, sources=sources,
            events=meta['events'], lines=meta['physical_lines'],
            created=datetime.now().isoformat(timespec='seconds'))
        store['reports'][rid] = value
        save_store(root, store)
        return dict(value, url='/reports/'+rid+'/index.html', reused=False)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def _combine_sources(selected, scratch: Path, base: date):
    """Create one JSONL without false 'same message == duplicate' assumptions.

    Each source is independent, first event date is operator-selected. Original
    source event ID and line ranges are retained as auxiliary fields.
    """
    total_lines = 0
    count = 0
    with scratch.open('w', encoding='utf-8', newline='\n') as output:
        for item in sorted(selected, key=lambda x:(x['date'], x['remote']['mtime'], x['remote']['name'])):
            d = date.fromisoformat(item['date'])
            source = item['remote']['name'] + ' · ' + item['date']
            stats = Counter()
            for ev in event_stream(item['local'], stats):
                count += 1
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
                output.write(json.dumps(ev, ensure_ascii=False, separators=(',', ':'))+'\n')
            total_lines += stats['physical_lines']
    if count == 0:
        raise FetchError('В выбранных файлах не обнаружены события AKUZ')
    return count, total_lines


def perform_build(root: Path, state: State, selections,
                  fetch_fn=fetch_selected, gen_fn=generate, refresh_remote=False):
    with state.lock:
        source = state.source
        listed = {f['id']:f for f in state.listing}
    cfg = source_config(root, source)
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
    reports = []
    files = []
    skipped = []
    source_seen = set()  # separate paths may contain independent identical events
    active_count = 0
    dropped_bytes = 0
    def download(remote):
        nonlocal active_count, dropped_bytes
        result = source_fetch(cfg, source, remote, state.set_stage) if source == 'windows' else fetch_fn(cfg, remote, state.set_stage)
        path, digest = result[:2]
        details = result[2] if len(result) > 2 else {}
        if details.get('active'):
            active_count += 1
            dropped_bytes += details.get('dropped_tail_bytes', 0)
        return path, digest, details
    for idx, fid in enumerate(ids, 1):
        remote = listed[fid]
        chosen = dates[fid]
        state.set_stage(f'{idx}/{len(ids)} · {remote["name"]}: проверяю локальный индекс…')
        # A remote file identity + operator-chosen date gives idempotent report reuse.
        remote_key = key_for('single-remote', cfg.host, cfg.port, cfg.username, fid, chosen)
        prior = cached_report(store, remote_key, root)
        if prior:
            reports.append(dict(prior, url='/reports/'+prior['id']+'/index.html', reused=True))
            cached = cached_download(store, fid)
            if cached is None and len(ids) > 1:
                state.set_stage('Для общей выборки восстанавливаю исходный файл: '+remote['name'])
                restored_path, restored_sha, details = download(remote)
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
        cached = cached_download(store, fid)
        if cached:
            path, digest = cached
            state.set_stage('Уже загружен: ' + remote['name'])
        else:
            state.set_stage(f'{idx}/{len(ids)} · загружаю {remote["name"]}…')
            path, digest, details = download(remote)
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
            continue
        state.set_stage(f'{idx}/{len(ids)} · разбираю {remote["name"]}…')
        label = remote['name'] + (' · ' + chosen if chosen else ' · дата не задана')
        report = _publish(root, store, content_key, path,
                          date.fromisoformat(chosen) if chosen else None,
                          [dict(name=remote['name'], date=chosen, sha256=digest,
                                remote_path=remote['path'], host=cfg.host)], label, 'single', gen_fn)
        # Store remote alias too, while preserving content-based de-duplication.
        store['reports'][report['id']]['aliases'] = list(set(
            store['reports'][report['id']].get('aliases', []) + [remote_key]))
        save_store(root, store)
        reports.append(report)
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
                _combine_sources(files, scratch, first)
                sources = [dict(name=f['remote']['name'], date=f['date'],
                                sha256=f['sha'], remote_path=f['remote']['path'], host=cfg.host) for f in files]
                label = f'Общая выборка · {files[0]["date"]} — {files[-1]["date"]} · {len(files)} файлов'
                combined = _publish(root, store, multi_key, scratch, first,
                                    sources, label, 'combined', gen_fn)
            finally:
                scratch.unlink(missing_ok=True)
    elif len(files) > 1:
        state.set_stage('Отдельные отчёты готовы; для общего отчёта укажите даты первой записи всех файлов')
    analytics_warning = ''
    try:
        from akuz_analytics import refresh as update_analytics
        update_analytics(root)
    except Exception as exc:
        # The report remains usable even if a derived, rebuildable analytics
        # cache cannot be updated. Do not expose exception details in HTML.
        analytics_warning = safe_error(exc,root)
    result = dict(reports=reports, combined=combined, skipped_identical=skipped,
                  active_snapshots=active_count, dropped_tail_bytes=dropped_bytes,
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


def perform_build_current(root, state, selections):
    """For GUI selections, refresh the listing before consulting cached snapshots."""
    return perform_build(root, state, selections, refresh_remote=True)


def perform_latest(root, state, source='linux'):
    perform_list(root, state, source=source)
    with state.lock:
        latest = state.listing[0] if state.listing else None
    if latest is None:
        raise FetchError('Файлы по маске не найдены')
    # Use the filename date through perform_build; never infer it from mtime.
    perform_build(root, state, [dict(id=latest['id'], date='')])


def perform_clear(root, state, include_reports):
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
                if payload.get('source', 'linux') not in ('linux','windows'):
                    return self._json(400, {'error':'Неизвестный источник'})
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
                args=(root, perform_list, list_remote, payload.get('source', 'linux'))
            elif endpoint == '/api/fetch':
                args=(root, perform_latest, payload.get('source', 'linux'))
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
    p=argparse.ArgumentParser(description='AKUZ Explorer v4.2.1 · AKUZ logs and Error Analytics')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--no-browser',action='store_true')
    args=p.parse_args()
    if not 1024 <= args.port <= 65535:
        p.error('port must be 1024..65535')
    try:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(ROOT,STATE,args.port))
    except OSError as exc:
        p.error(f'Cannot start on 127.0.0.1:{args.port}: {exc}')
    print(f'AKUZ Log Explorer v4.2.1: http://127.0.0.1:{args.port}/\nОстановка: Ctrl+C',flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda:webbrowser.open(f'http://127.0.0.1:{args.port}/')).start()
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__=='__main__':
    main()
