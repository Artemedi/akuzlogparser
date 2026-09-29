"""Offline smoke checks executed inside the packaged executable in CI."""
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import multiprocessing
import os


def _spawn_probe(destination, sender):
    import hashlib

    path = Path(destination)
    payload = b'AKUZ portable spawned child'
    path.write_bytes(payload)
    sender.send_bytes(json.dumps([
        'ok', str(path), hashlib.sha256(payload).hexdigest(), len(payload),
        .01, .01, {
            'active': False,
            'captured_bytes': len(payload),
            'stored_bytes': len(payload),
            'dropped_tail_bytes': 0,
            'listed_bytes': len(payload),
        }
    ], separators=(',', ':')).encode('utf-8'))
    sender.close()


def run():
    # Exercise the bundled native crypto libraries, not just their imports.
    import paramiko
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from akuz_app import _publish
    from akuz_analytics import connect, detail, refresh
    from akuz_runtime import app_root
    from akuz_store import load_store, sha256
    from akuz_process_fetch import ProcessFetch
    from akuz_win_job_spawn import get_job_bound_spawn_context
    from akuz_delta import RemoteMeta, assemble_delta_final_proof

    key = Ed25519PrivateKey.generate()
    message = b'AKUZ portable offline check'
    key.public_key().verify(key.sign(message), message)
    client = paramiko.SSHClient()
    client.close()
    with TemporaryDirectory(prefix='akuz-portable-check-') as scratch:
        root = Path(scratch)
        spawn_target = root/'spawn-probe.bin'
        payload_size = len(b'AKUZ portable spawned child')
        with ProcessFetch(
                get_job_bound_spawn_context(), _spawn_probe, (), spawn_target,
                poll_timeout_s=20, join_timeout_s=10, kill_timeout_s=5,
                expected_listed_bytes=payload_size,
                require_kill_job=(os.name == 'nt'),
                safe_ipc=True) as operation:
            spawned = operation.finish()
        if spawned.path.read_bytes() != b'AKUZ portable spawned child':
            raise RuntimeError('Portable multiprocessing spawn check failed')
        # Exercise the dynamically imported Phase 12 module inside the frozen
        # executable, including same-directory no-overwrite publication.
        previous = root/'phase12-previous.log'
        delta = root/'phase12-delta.bin'
        final = root/'phase12-final.log'
        old = b'12:00 old\n'
        appended = b'12:01 new\n'
        current = old + appended
        previous.write_bytes(old)
        delta.write_bytes(appended)
        import hashlib
        size, digest = assemble_delta_final_proof(
            previous,
            previous_sha256=hashlib.sha256(old).hexdigest(),
            previous_device=1,
            previous_inode=2,
            before=RemoteMeta(1, 2, len(current), 100),
            delta=delta,
            transfer_bound=len(current),
            publish_size=len(current),
            after=RemoteMeta(1, 2, len(current), 100),
            remote_published_prefix_sha256=hashlib.sha256(current).hexdigest(),
            final=final,
            temp_prefix='.phase12-portable-',
        )
        if (size != len(current) or digest != hashlib.sha256(current).hexdigest()
                or final.read_bytes() != current):
            raise RuntimeError('Portable Phase 12 delta check failed')

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
    print(json.dumps(dict(ok=True, events=2, errors=2, crypto=True, spawn=True,
                          phase12_delta=True, app_root=str(app_root())),
                     ensure_ascii=True))
