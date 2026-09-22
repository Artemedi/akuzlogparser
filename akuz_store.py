"""Local v4 inventory, SHA-256 integrity, safe tracked cache clearing."""
from __future__ import annotations
from datetime import date, datetime
import hashlib
import json
import re
from pathlib import Path
import shutil


def date_from_log_name(name):
    """AKUZ naming contract: YYYYMMDD_*.log contains the first event's date."""
    match=re.fullmatch(r"([0-9]{4})([0-9]{2})([0-9]{2})_.*\.log",str(name),re.I)
    if not match:
        return ""
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return ""


def source_date(info):
    if info.get("date") or info.get("date_override"):
        return str(info.get("date") or "")
    return date_from_log_name(info.get("name", ""))


def load_store(root: Path):
    path = root/'cache'/'inventory.json'
    if not path.exists():
        return {'version':4, 'downloads':{}, 'reports':{}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 4:
        raise ValueError('Неизвестная версия cache/inventory.json')
    return data


def save_store(root: Path, data):
    folder = root/'cache'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/'inventory.json'
    tmp = folder/'inventory.json.tmp'
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def sha256(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def key_for(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False,
                                   separators=(',', ':')).encode('utf-8')).hexdigest()


def cached_download(store, fid):
    entry = store['downloads'].get(fid)
    if not entry:
        return None
    path = Path(entry['path'])
    if path.is_file() and path.stat().st_size == entry['size'] and sha256(path) == entry['sha256']:
        return path, entry['sha256']
    return None


def cached_report(store, key: str, root: Path):
    result = next((v for v in store['reports'].values() if v['key'] == key or key in v.get('aliases', [])), None)
    if result and (root/'reports'/result['id']/'index.html').is_file():
        return result
    return None


def report_summary(data, root: Path):
    return sorted((dict(v, url='/reports/'+v['id']+'/index.html')
                   for v in data['reports'].values()
                   if (root/'reports'/v['id']/'index.html').is_file()),
                  key=lambda r:r['created'], reverse=True)


def _safe_tracked(path: Path, base: Path, prefix: str = ''):
    try:
        if not path.is_file() and not path.is_dir():
            return False
        path.resolve().relative_to(base.resolve())
        return path.name.startswith(prefix) and not path.is_symlink()
    except ValueError:
        return False


def clear_cache(root: Path, cfg, store, include_reports: bool):
    """Clear only files created and indexed by v4. Never delete arbitrary local files."""
    cleared = 0
    destinations = [c.local_dest for c in cfg] if isinstance(cfg, (list, tuple)) else [cfg.local_dest]
    for entry in store['downloads'].values():
        path = Path(entry['path'])
        if any(_safe_tracked(path, dest, 'akuz_v4_') for dest in destinations):
            path.unlink()
            cleared += 1
    store['downloads'] = {}
    reports = 0
    if include_reports:
        for entry in store['reports'].values():
            directory = root/'reports'/entry['id']
            if _safe_tracked(directory, root/'reports') and entry['id'].startswith('v4_'):
                shutil.rmtree(directory)
                reports += 1
        store['reports'] = {}
    save_store(root, store)
    return {'downloads_removed':cleared, 'reports_removed':reports,
            'reports_preserved':not include_reports}
