"""Synthetic B-lite processed sidecar proof; no application cache writes."""
from contextlib import ExitStack
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

from akuz_app import _iter_combined_sources
from akuz_html_explorer import generate
from akuz_store import sha256
from scripts.bench_phase9_baseline import create_sources, file_manifest
from scripts.probe_phase9_sidecar import (SCHEMA, SidecarWriter,
    VerifiedSidecar, code_revision, encode_binary_frame, decode_binary_frame)


class SidecarPrototypeTests(unittest.TestCase):
    def test_reuse_derived_from_fresh_singles_in_combined_byte_exact(self):
        with TemporaryDirectory(prefix='akuz_phase93_') as td:
            root=Path(td)
            sources=root/'sources'
            create_sources(sources,12,128)
            files=sorted(sources.glob('*.log'))
            identities=[]
            for idx, source in enumerate(files):
                first=date.fromisoformat(source.name[:4]+'-'+source.name[4:6]+'-'+source.name[6:8])
                logical='/synthetic/'+source.name
                folder=root/'sidecar'/str(idx)
                with SidecarWriter(folder,source,'synthetic-host',logical,
                                   first.isoformat()) as sidecar:
                    generate(source,root/'single'/str(idx),first,10,35,
                             derived_sink=sidecar)
                self.assertGreater(sidecar.count,0)
                identities.append((source,first,logical,folder))
            scratch=root/'scratch.jsonl'
            scratch.write_text('',encoding='utf-8')
            selections=[dict(local=source,sha=sha256(source),
                date=first.isoformat(),remote=dict(name=source.name,
                path=logical,mtime=i))
                for i,(source,first,logical,folder) in enumerate(identities)]
            expected=root/'combined_no_sidecar'
            actual=root/'combined_sidecar'
            generate(scratch,expected,identities[0][1],10,35,
                event_source=_iter_combined_sources(selections,identities[0][1]),
                input_bytes=sum(p.stat().st_size for p in files))
            with ExitStack() as stack:
                readers={source.name+' · '+first.isoformat():
                    stack.enter_context(VerifiedSidecar(folder,source,
                        'synthetic-host',logical,first.isoformat()))
                    for source,first,logical,folder in identities}
                def hook(ev):
                    return readers[ev['source_file']].take(ev)
                generate(scratch,actual,identities[0][1],10,35,
                    event_source=_iter_combined_sources(selections,identities[0][1]),
                    derived_hook=hook,
                    input_bytes=sum(p.stat().st_size for p in files))
            self.assertEqual(file_manifest(expected),file_manifest(actual))
            for source,first,logical,folder in identities:
                header=json.loads((folder/'manifest.json').read_text('utf-8'))
                self.assertEqual(header['schema'],SCHEMA)
                self.assertEqual(header['revision'],code_revision())
                self.assertEqual(header['body_bytes'],
                                 (folder/'derived.jsonl').stat().st_size)
                rows=(folder/'derived.jsonl').read_text('utf-8')
                self.assertNotIn('"raw":',rows)
                self.assertNotIn('"message":',rows)
                self.assertNotIn('"headline":',rows)
            self.assertFalse(list(root.rglob('inventory.json')))

    def fixture(self, root):
        sources=root/'sources'
        create_sources(sources,5,64)
        source=sorted(sources.glob('*.log'))[0]
        first=source.name[:4]+'-'+source.name[4:6]+'-'+source.name[6:8]
        folder=root/'sidecar'
        with SidecarWriter(folder,source,'test-host',
                           '/fake/'+source.name,first) as sidecar:
            generate(source,root/'report',date.fromisoformat(first),
                     10,35,derived_sink=sidecar)
        return folder,source,first

    def test_exact_identity_and_corrupt_sidecar_fail_closed(self):
        with TemporaryDirectory(prefix='akuz_phase93_invalid_') as td:
            root=Path(td)
            folder,source,first=self.fixture(root)
            def reader(host='test-host',logical=None):
                return VerifiedSidecar(folder,source,host,
                       logical or '/fake/'+source.name,first)
            self.assertEqual(reader().expected,5)
            # No event was consumed: exhaustiveness is part of protocol.
            with self.assertRaisesRegex(ValueError,'count mismatch'):
                with reader():
                    pass
            with self.assertRaisesRegex(ValueError,'source mismatch'):
                reader(host='other-host')
            with self.assertRaisesRegex(ValueError,'source mismatch'):
                reader(logical='/other/'+source.name)
            with patch('scripts.probe_phase9_sidecar.code_revision',
                                      return_value='changed-deriver'):
                with self.assertRaisesRegex(ValueError,'algorithm'):
                    reader()
            body=folder/'derived.jsonl'
            payload=body.read_bytes()
            body.write_bytes(b'{' + payload[1:])
            self.assertEqual(len(payload),body.stat().st_size)
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                reader()
            body.write_bytes(payload)
            raw=source.read_bytes()
            source.write_bytes(raw.replace(b'AKUZ',b'akuz',1))
            self.assertEqual(source.stat().st_size,len(raw))
            with self.assertRaisesRegex(ValueError,'source mismatch'):
                reader()
            source.write_bytes(raw)
            (folder/'manifest.json').unlink()
            with self.assertRaisesRegex(ValueError,'manifest missing'):
                reader()

    def test_versioned_binary_packet_roundtrip_and_invalid_version(self):
        with TemporaryDirectory(prefix='akuz_phase93_binary_') as td:
            root=Path(td)
            folder,source,first=self.fixture(root)
            payload=(folder/'derived.jsonl').read_bytes()
            packet=encode_binary_frame(payload)
            self.assertEqual(decode_binary_frame(packet),payload)
            self.assertEqual(packet[:5],b'AKZS'+bytes([1]))
            with self.assertRaisesRegex(ValueError,'version'):
                decode_binary_frame(packet[:4]+bytes([2])+packet[5:])
            with self.assertRaisesRegex(ValueError,'checksum'):
                decode_binary_frame(packet[:13]+bytes([0])*32+packet[45:])

    def test_partial_writer_failure_never_publishes_manifest(self):
        with TemporaryDirectory(prefix='akuz_phase93_partial_') as td:
            root=Path(td)
            sources=root/'sources'
            create_sources(sources,5,64)
            source=sorted(sources.glob('*.log'))[0]
            first=source.name[:4]+'-'+source.name[4:6]+'-'+source.name[6:8]
            folder=root/'candidate'
            with self.assertRaisesRegex(OSError,'injected sidecar failure'):
                with SidecarWriter(folder,source,'test-host',
                                   '/fake/'+source.name,first) as sink:
                    def fail(ev,item):
                        sink(ev,item)
                        if sink.count==2:
                            raise OSError('injected sidecar failure')
                    generate(source,root/'report',date.fromisoformat(first),
                             10,35,derived_sink=fail)
            self.assertFalse((folder/'manifest.json').exists())
            self.assertFalse((folder/'derived.jsonl.tmp').exists())
            self.assertFalse((folder/'derived.jsonl').exists())
            with self.assertRaisesRegex(ValueError,'manifest missing'):
                VerifiedSidecar(folder,source,'test-host',
                                '/fake/'+source.name,first)


    def test_manifest_schema_and_forged_trailing_record_fail_closed(self):
        from collections import Counter
        from hashlib import sha256 as digest
        from akuz_log_parser import event_stream
        with TemporaryDirectory(prefix='akuz_phase93_trailing_') as td:
            root=Path(td)
            folder,source,first=self.fixture(root)
            manifest=folder/'manifest.json'
            info=json.loads(manifest.read_text('utf-8'))
            info['schema']=2
            manifest.write_text(json.dumps(info),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'schema'):
                VerifiedSidecar(folder,source,'test-host',
                                '/fake/'+source.name,first)
            info['schema']=SCHEMA
            body=folder/'derived.jsonl'
            content=body.read_bytes()
            content+=content.splitlines(keepends=True)[0]
            body.write_bytes(content)
            info['body_sha256']=digest(content).hexdigest()
            info['body_bytes']=len(content)
            manifest.write_text(json.dumps(info),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'trailing'):
                with VerifiedSidecar(folder,source,'test-host',
                                    '/fake/'+source.name,first) as reader:
                    for ev in event_stream(source,Counter()):
                        reader.take(ev)


    def test_binary_frame_rejects_oversize_and_expansion_before_allocation(self):
        import hashlib
        import tracemalloc
        from scripts.probe_phase9_sidecar import MAX_BINARY_FRAME_BYTES
        body=b'x'*2_000_000
        # A tiny compressed payload advertises one output byte but
        # tries to inflate a much larger buffer before length validation.
        malicious=(b'AKZS'+bytes([1])+(1).to_bytes(8,'big')+
                   hashlib.sha256(body).digest()+zlib.compress(body))
        tracemalloc.start()
        try:
            with self.assertRaisesRegex(ValueError,'length or checksum'):
                decode_binary_frame(malicious)
            peak=tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak,1_000_000)
        valid=encode_binary_frame(b'normal')
        oversize=(valid[:5]+(MAX_BINARY_FRAME_BYTES+1).to_bytes(8,'big')+
                  valid[13:])
        with self.assertRaisesRegex(ValueError,'too large'):
            decode_binary_frame(oversize)
        with self.assertRaisesRegex(ValueError,'length or checksum'):
            decode_binary_frame(valid+b'extra')


if __name__ == '__main__':
    unittest.main()
