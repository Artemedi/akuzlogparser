"""Local v4 inventory, SHA-256 integrity, safe tracked cache clearing."""
from __future__ import annotations
from datetime import date, datetime
import hashlib
import json
import re
from pathlib import Path
import shutil

from akuz_store_lock import InventoryConflictError, inventory_transaction


class InventoryStore(dict):
    """Inventory mapping carrying the exact on-disk revision it was loaded from."""
    def __init__(self, *args, revision=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._inventory_revision = revision


def _disk_revision(path: Path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


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
        return InventoryStore({'version':4, 'downloads':{}, 'reports':{}},
                              revision=None)
    raw = path.read_bytes()
    data = json.loads(raw.decode('utf-8'))
    if data.get('version') != 4:
        raise ValueError('Неизвестная версия cache/inventory.json')
    return InventoryStore(data, revision=hashlib.sha256(raw).hexdigest())


def save_store(root: Path, data):
    folder = root/'cache'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/'inventory.json'
    tmp = folder/'inventory.json.tmp'
    with inventory_transaction(root):
        expected = getattr(data, '_inventory_revision', None)
        current = _disk_revision(path)
        if hasattr(data, '_inventory_revision') and expected != current:
            raise InventoryConflictError(
                'Индекс изменён другой копией AKUZ Log Explorer; повторите операцию')
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding='utf-8')
            # Prepare the exact on-disk revision BEFORE the irreversible rename.
            # A failed post-rename readback would otherwise make _publish
            # delete a report directory already referenced by inventory.json.
            revision = hashlib.sha256(tmp.read_bytes()).hexdigest()
            tmp.replace(path)
            if hasattr(data, '_inventory_revision'):
                data._inventory_revision = revision
        except Exception:
            # A failed write/replace must not leave a stale partial inventory.
            # Preserve the original persistence error if cleanup also fails.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


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


def report_intact(entry: dict, root: Path) -> bool:
    """Check indexed report identity; old rows cannot gain invented hashes."""
    rid = entry.get('id', '')
    if (not isinstance(rid, str) or
            not re.fullmatch(r'v4_[0-9]{8}_[0-9]{6}_[0-9a-f]{8}', rid)):
        return False
    folder = root/'reports'/rid
    if folder.is_symlink() or not folder.is_dir():
        return False
    required = ('index.html', 'data/catalog.js', 'provenance.json')
    manifest = entry.get('integrity')
    try:
        for name in required:
            file = folder/name
            if file.is_symlink() or not file.is_file():
                return False
            file.resolve().relative_to(folder.resolve())
            if manifest is not None and sha256(file) != manifest['required_sha256'][name]:
                return False
        if manifest is not None:
            # Stat is cheap on warm reuse. Same-size raw corruption remains
            # undetectable for indexed reports until raw hashes are verified.
            for name, size in manifest['files'].items():
                file = folder/name
                if file.is_symlink() or not file.is_file() or file.stat().st_size != size:
                    return False
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
    return True


def cached_report(store, key: str, root: Path):
    # Old damaged rows must not shadow newer valid replacements.
    for result in reversed(list(store['reports'].values())):
        if (result.get('invalidated') or
                (result.get('key') != key and key not in result.get('aliases', []))):
            continue
        if report_intact(result, root):
            return result
        result['invalidated'] = 'integrity'
        try:
            save_store(root, store)
        except Exception:
            result.pop('invalidated', None)
            raise
    return None


def report_summary(data, root: Path):
    # Preserve access to quarantined report bytes for diagnosis, visibly
    # mark them rather than silently delete or hide an operator's report.
    return sorted((dict(v,
                   label=('⚠ Повреждён · ' + str(v.get('label', ''))
                          if v.get('invalidated') else v.get('label', '')),
                   url='/reports/'+v['id']+'/index.html')
                   for v in data['reports'].values()
                   if (root/'reports'/v['id']/'index.html').is_file()),
                  key=lambda r:r['created'], reverse=True)


def _tracked_location(path: Path, base: Path, prefix: str = ''):
    """Return whether a path is inside an allowed cache root, even if missing."""
    try:
        if path.is_symlink() or not path.name.startswith(prefix):
            return False
        path.resolve(strict=False).relative_to(base.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _safe_tracked(path: Path, base: Path, prefix: str = ''):
    try:
        return ((path.is_file() or path.is_dir())
                and _tracked_location(path, base, prefix))
    except OSError:
        return False


def clear_cache(root: Path, cfg, store, include_reports: bool):
    """Clear only cache entries proven to belong to currently allowed roots.

    Entries from a previous/custom local_dest are preserved in inventory when
    that root is no longer configured. Dropping their index while leaving the
    bytes behind would create an untracked orphan and make a later cleanup
    impossible to reason about safely.
    """
    cleared = 0
    retained = 0
    destinations = [c.local_dest for c in cfg] if isinstance(cfg, (list, tuple)) else [cfg.local_dest]
    removable_downloads = []
    for fid, entry in list(store['downloads'].items()):
        try:
            path = Path(entry['path'])
        except (KeyError, TypeError):
            retained += 1
            continue
        allowed = any(_tracked_location(path, dest, 'akuz_v4_')
                      for dest in destinations)
        if not allowed:
            retained += 1
            continue
        if path.exists() or path.is_symlink():
            if not path.is_file() or path.is_symlink():
                retained += 1
                continue
            path.unlink()
            cleared += 1
        # A missing file inside an allowed cache root is a stale index entry,
        # so forget it even though there were no bytes left to delete.
        removable_downloads.append(fid)
    for fid in removable_downloads:
        store['downloads'].pop(fid, None)

    reports = 0
    reports_retained = 0
    if include_reports:
        removable_reports = []
        for rid, entry in list(store['reports'].items()):
            report_id = entry.get('id') if isinstance(entry, dict) else None
            if not isinstance(report_id, str):
                reports_retained += 1
                continue
            directory = root/'reports'/report_id
            allowed = (report_id.startswith('v4_')
                       and _tracked_location(directory, root/'reports'))
            if not allowed:
                reports_retained += 1
                continue
            if directory.exists() or directory.is_symlink():
                if not directory.is_dir() or directory.is_symlink():
                    reports_retained += 1
                    continue
                shutil.rmtree(directory)
                reports += 1
            removable_reports.append(rid)
        for rid in removable_reports:
            store['reports'].pop(rid, None)

    save_store(root, store)
    return {'downloads_removed':cleared,
            'downloads_retained':retained,
            'reports_removed':reports,
            'reports_retained':reports_retained,
            'reports_preserved':not include_reports}
