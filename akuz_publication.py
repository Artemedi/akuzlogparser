"""Narrow ownership-validated recovery for interrupted v4 report publication.

Intent files live only in the local app cache. Never sweep unknown reports.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from akuz_store import sha256, save_store
from akuz_diagnostics import phase as perf_phase

_REPORT_ID = re.compile(r"v4_[0-9]{8}_[0-9]{6}_[0-9a-f]{8}\Z")


def intent_path(root: Path, rid: str) -> Path:
    if not _REPORT_ID.fullmatch(rid):
        raise ValueError("Invalid report ID in publication intent")
    return root / 'cache' / 'report_intents' / (rid + '.json')


def retire_intent(path: Path) -> None:
    # The inventory may already be durable; failed marker cleanup is
    # not a reason to delete the published report or index entry.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _file_sizes(folder: Path) -> dict[str, int]:
    """Capture all generated paths/sizes; hash the required identity files."""
    result = {}
    resolved_folder = folder.resolve()
    for path in folder.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlink in published report')
        if path.is_file():
            path.resolve().relative_to(resolved_folder)
            result[path.relative_to(folder).as_posix()] = path.stat().st_size
    return result


def write_intent(root: Path, value: dict, provenance: Path) -> Path:
    """Publish one exact, checksummed report intent before directory rename."""
    rid = value['id']
    marker = intent_path(root, rid)
    folder = marker.parent
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('Symlinked publication-intent directory')
    draft = marker.with_suffix('.json.tmp')
    if marker.exists() or draft.exists():
        raise FileExistsError('Publication intent already exists')
    stage = root / 'reports' / (rid + '.building')
    sizes = _file_sizes(stage)
    with perf_phase(root, 'report.integrity_hash',
                    input_bytes=sum(sizes.values()), files=len(sizes)):
        required_hashes = {
            name: sha256(stage / name)
            for name in ('provenance.json', 'index.html', 'data/catalog.js')
        }
        # Recovery needs full content integrity, including same-size raw damage.
        # Full hashes are stored but NOT re-read on every ordinary warm hit.
        all_hashes = dict(required_hashes)
        for name in sizes:
            if name not in all_hashes:
                all_hashes[name] = sha256(stage / name)
    value['integrity'] = dict(files=sizes,
                              required_sha256=required_hashes,
                              all_sha256=all_hashes)
    record = dict(version=2, value=value, files=sizes,
                  provenance_sha256=required_hashes['provenance.json'],
                  index_sha256=required_hashes['index.html'],
                  catalog_sha256=required_hashes['data/catalog.js'])
    try:
        with draft.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
        draft.replace(marker)
    except Exception:
        try:
            draft.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return marker


def _validated_candidate(root: Path, marker: Path, record: dict,
                         key: str, sources: list, label: str, kind: str):
    rid = marker.name[:-5]
    value = record.get('value')
    if (not _REPORT_ID.fullmatch(rid) or record.get('version') not in (1, 2)
            or not isinstance(value, dict) or value.get('id') != rid
            or value.get('key') != key or value.get('sources') != sources
            or value.get('kind') != kind or value.get('label') != label):
        return None
    parent = root / 'reports'
    final = parent / rid
    if marker.is_symlink() or final.is_symlink() or not final.is_dir():
        return None
    try:
        final.resolve().relative_to(parent.resolve())
        sizes = _file_sizes(final)
        if sizes != record.get('files'):
            return None
        if record['version'] == 2:
            integrity = value.get('integrity')
            if not isinstance(integrity, dict) or integrity.get('files') != sizes:
                return None
            hashes = integrity.get('all_sha256')
            if not isinstance(hashes, dict) or hashes.keys() != sizes.keys():
                return None
            for name, expected in hashes.items():
                if sha256(final / name) != expected:
                    return None
        # Version 1 intents from older builds have no historical raw hashes;
        # keep their preexisting bounded recovery semantics.
        for name, field in (('provenance.json', 'provenance_sha256'),
                            ('index.html', 'index_sha256'),
                            ('data/catalog.js', 'catalog_sha256')):
            path = final / name
            if path.is_symlink() or not path.is_file():
                return None
            path.resolve().relative_to(final.resolve())
            if sha256(path) != record.get(field):
                return None
        provenance = json.loads((final / 'provenance.json').read_text('utf-8'))
        if (provenance.get('sources') != sources or provenance.get('kind') != kind
                or provenance.get('events') != value.get('events')):
            return None
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return value


def recover_report(root: Path, store: dict, key: str,
                   sources: list, label: str, kind: str):
    """Re-index only an exact owned, complete report matching this request."""
    folder = root / 'cache' / 'report_intents'
    if not folder.is_dir() or folder.is_symlink():
        return None
    for marker in sorted(folder.glob('v4_*.json')):
        if marker.is_symlink():
            continue
        try:
            record = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue  # Do not delete or interpret untrusted/corrupt files.
        if not isinstance(record, dict):
            continue
        value = _validated_candidate(root, marker, record,
                                     key, sources, label, kind)
        if value is None:
            continue
        rid = value['id']
        if rid in store['reports']:
            if store['reports'][rid] != value:
                raise ValueError('Publication intent conflicts with inventory')
            retire_intent(marker)
            return dict(value, url='/reports/'+rid+'/index.html', reused=True)
        store['reports'][rid] = value
        try:
            save_store(root, store)
        except Exception:
            store['reports'].pop(rid, None)
            raise
        retire_intent(marker)
        return dict(value, url='/reports/'+rid+'/index.html', reused=True)
    return None


def retire_indexed_intent(root: Path, report: dict, key: str,
                          sources: list, label: str, kind: str) -> None:
    """Retire only a marker for the exact already-indexed report."""
    rid = report.get('id', '')
    if not isinstance(rid, str) or not _REPORT_ID.fullmatch(rid):
        return
    marker = intent_path(root, rid)
    if not marker.is_file() or marker.is_symlink():
        return
    try:
        record = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    if not isinstance(record, dict):
        return
    value = _validated_candidate(root, marker, record,
                                 key, sources, label, kind)
    if value is not None and value['id'] == rid:
        retire_intent(marker)
