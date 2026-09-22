"""Offline smoke checks executed inside the packaged executable in CI."""
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import json


def run():
    # Exercise the bundled native crypto libraries, not just their imports.
    import paramiko
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from akuz_app import _publish
    from akuz_analytics import connect, detail, refresh
    from akuz_runtime import app_root
    from akuz_store import load_store, sha256

    key = Ed25519PrivateKey.generate()
    message = b'AKUZ portable offline check'
    key.public_key().verify(key.sign(message), message)
    client = paramiko.SSHClient()
    client.close()
    with TemporaryDirectory(prefix='akuz-portable-check-') as scratch:
        root = Path(scratch)
        raw = root/'20260923_smoke.log'
        raw.write_text(
            '23:59:00.100,AKUZ,s,user: SerializationException: Failed item 1\n'
            ' at AKUZ.Serialize()\n'
            '00:01:00.100,AKUZ,s,user: SerializationException: Failed item 2\n'
            ' at AKUZ.Serialize()\n', encoding='utf-8')
        report = _publish(root, load_store(root), 'portable-smoke', raw, date(2026,9,23),
                          [dict(name=raw.name, date='2026-09-23', sha256=sha256(raw),
                                host='synthetic', remote_path='/synthetic/'+raw.name)],
                          'Synthetic smoke report', 'single')
        summary = refresh(root)
        if report['events'] != 2 or summary['distinct_errors'] != 2:
            raise RuntimeError('Portable parsing/analytics check failed')
        with closing(connect(root)) as db:
            result = detail(db, summary['groups'][0]['fp'])
            if result['days'] != [('2026-09-23',1), ('2026-09-24',1)]:
                raise RuntimeError('Portable calendar check failed')
    print(json.dumps(dict(ok=True, events=2, errors=2, crypto=True,
                          app_root=str(app_root())), ensure_ascii=True))
