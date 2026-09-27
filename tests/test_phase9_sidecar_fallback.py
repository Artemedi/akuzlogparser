"""No half-sidecar combined publication in isolated prototype."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_app import _iter_combined_sources
from akuz_html_explorer import generate
from akuz_store import sha256
from scripts.bench_phase9_baseline import create_sources, file_manifest
from scripts.probe_phase9_sidecar import SidecarWriter, VerifiedSidecar
from scripts.probe_phase9_sidecar_fallback import combined_with_fallback


class SidecarFallbackTests(unittest.TestCase):
    def fixture(self, root):
        sources = root/'sources'
        create_sources(sources, 12, 128)
        files = sorted(sources.glob('*.log'))
        selected, folders, manifests = [], {}, []
        for index, source in enumerate(files):
            first = date.fromisoformat(source.name[:4]+'-'+source.name[4:6]
                                        +'-'+source.name[6:8])
            logical = '/synthetic/' + source.name
            label = source.name + ' · ' + first.isoformat()
            folder = root/'sidecar'/str(index)
            individual = root/'single'/str(index)
            with SidecarWriter(folder, source, 'synthetic-host', logical,
                               first.isoformat()) as sink:
                generate(source, individual, first, 10, 35,
                         derived_sink=sink)
            manifests.append(file_manifest(individual))
            selected.append(dict(local=source, sha=sha256(source),
                date=first.isoformat(), remote=dict(name=source.name,
                host='synthetic-host', path=logical, mtime=index)))
            folders[label] = folder
        scratch = root/'scratch.jsonl'
        scratch.write_text('', encoding='utf-8')
        expected = root/'reference'
        generate(scratch, expected, date.fromisoformat(selected[0]['date']),
            10, 35,
            event_source=_iter_combined_sources(selected,
                 date.fromisoformat(selected[0]['date'])),
            input_bytes=sum(p.stat().st_size for p in files))
        return selected, folders, manifests, scratch, file_manifest(expected)

    def test_late_sidecar_error_disposes_partial_combined_and_restarts(self):
        with TemporaryDirectory(prefix='akuz_phase93_fallback_') as td:
            root = Path(td)
            selected, folders, singles, scratch, expected = self.fixture(root)
            valid = root/'ready_sidecar'
            self.assertEqual(combined_with_fallback(
                selected, folders, valid, scratch, chunk_size=10), 'sidecar')
            self.assertEqual(file_manifest(valid), expected)
            original = VerifiedSidecar.take
            calls = 0
            def damaged(reader, event):
                nonlocal calls
                calls += 1
                if calls == 6:
                    raise ValueError('injected sixth record corruption')
                return original(reader, event)
            recovered = root/'recovered'
            with patch.object(VerifiedSidecar, 'take', damaged):
                self.assertEqual(combined_with_fallback(
                    selected, folders, recovered, scratch, chunk_size=10),
                    'fallback')
            self.assertEqual(calls, 6)
            self.assertEqual(file_manifest(recovered), expected)
            self.assertEqual([file_manifest(root/'single'/str(i))
                              for i in range(3)], singles)
            self.assertFalse(list(root.glob('phase9-proto-combined-*')))
            self.assertFalse(list(root.rglob('inventory.json')))

    def test_forged_sixth_coordinate_with_updated_manifest_restarts(self):
        import hashlib
        import json
        with TemporaryDirectory(prefix='akuz_phase93_coord_') as td:
            root = Path(td)
            selected, folders, singles, scratch, expected = self.fixture(root)
            folder = next(iter(folders.values()))
            body = folder/'derived.jsonl'
            records = [json.loads(line) for line in body.read_text('utf-8').splitlines()]
            records[5][0] += 1000000  # content + digest now agree, ordinal does not
            payload = ''.join(json.dumps(item, ensure_ascii=False,
                              separators=(',',':'))+'\n' for item in records)
            body.write_text(payload, encoding='utf-8', newline='\n')
            header_file = folder/'manifest.json'
            header = json.loads(header_file.read_text('utf-8'))
            header['body_sha256'] = hashlib.sha256(body.read_bytes()).hexdigest()
            header['body_bytes'] = body.stat().st_size
            header_file.write_text(json.dumps(header), encoding='utf-8')
            result = root/'fallback_after_forgery'
            self.assertEqual(combined_with_fallback(
                selected, folders, result, scratch, chunk_size=10),
                'fallback')
            self.assertEqual(file_manifest(result), expected)
            self.assertFalse(list(root.glob('phase9-proto-combined-*')))
            self.assertEqual([file_manifest(root/'single'/str(i))
                              for i in range(3)], singles)

    def test_disk_full_does_not_masquerade_as_sidecar_corruption(self):
        from scripts import probe_phase9_sidecar_fallback as prototype
        with TemporaryDirectory(prefix='akuz_phase93_diskfull_') as td:
            root = Path(td)
            selected, folders, singles, scratch, expected = self.fixture(root)
            target = root/'unpublished'
            with patch.object(prototype, 'generate',
                              side_effect=OSError('injected disk full')) as writer:
                with self.assertRaisesRegex(OSError, 'disk full'):
                    combined_with_fallback(selected, folders, target, scratch)
            self.assertEqual(writer.call_count, 1,
                             'Disk full must NOT trigger a second full build')
            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob('phase9-proto-combined-*')))
            self.assertEqual([file_manifest(root/'single'/str(i))
                              for i in range(3)], singles)

    def test_missing_sidecar_manifest_falls_back_without_rebuilding_single(self):
        with TemporaryDirectory(prefix='akuz_phase93_missing_') as td:
            root = Path(td)
            selected, folders, singles, scratch, expected = self.fixture(root)
            (next(iter(folders.values()))/'manifest.json').unlink()
            output = root/'fallback_missing_manifest'
            self.assertEqual(combined_with_fallback(selected, folders,
                output, scratch, chunk_size=10), 'fallback')
            self.assertEqual(file_manifest(output), expected)
            self.assertFalse(list(root.glob('phase9-proto-combined-*')))
            self.assertEqual([file_manifest(root/'single'/str(i))
                              for i in range(3)], singles)

    def test_source_mutation_and_existing_output_refuse_publication(self):
        with TemporaryDirectory(prefix='akuz_phase93_refuse_') as td:
            root = Path(td)
            selected, folders, singles, scratch, expected = self.fixture(root)
            target = root/'already_owned'
            target.mkdir()
            (target/'operator-file.txt').write_text('keep', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                combined_with_fallback(selected, folders, target, scratch)
            self.assertEqual((target/'operator-file.txt').read_text('utf-8'),
                             'keep')
            changed = selected[0]['local']
            raw = changed.read_bytes()
            changed.write_bytes(raw+b'changed')
            with self.assertRaisesRegex(ValueError, 'Source changed'):
                combined_with_fallback(selected, folders,
                                       root/'new_combined', scratch)
            self.assertFalse((root/'new_combined').exists())
            self.assertFalse(list(root.glob('phase9-proto-combined-*')))
            self.assertEqual([file_manifest(root/'single'/str(i))
                              for i in range(3)], singles)


    def test_truncated_body_falls_back_with_ready_singles_untouched(self):
        with TemporaryDirectory(prefix='akuz_phase93_truncated_') as td:
            root = Path(td)
            selected, folders, singles, scratch, expected = self.fixture(root)
            body = next(iter(folders.values()))/'derived.jsonl'
            payload = body.read_bytes()
            self.assertGreater(len(payload), 5)
            body.write_bytes(payload[:-5])
            target = root/'recovered_truncated'
            self.assertEqual(combined_with_fallback(
                selected, folders, target, scratch, chunk_size=10),
                'fallback')
            self.assertEqual(file_manifest(target), expected)
            self.assertEqual([file_manifest(root/'single'/str(i))
                              for i in range(3)], singles)
            self.assertFalse(list(root.glob('phase9-proto-combined-*')))

    def test_wrong_sidecar_code_revision_falls_back_safely(self):
        import json
        with TemporaryDirectory(prefix='akuz_phase93_revision_') as td:
            root = Path(td)
            selected, folders, singles, scratch, expected = self.fixture(root)
            header_path = next(iter(folders.values()))/'manifest.json'
            header = json.loads(header_path.read_text('utf8'))
            header['revision'] = 'wrong-code-revision'
            header_path.write_text(json.dumps(header), encoding='utf8')
            target = root/'recovered_revision'
            self.assertEqual(combined_with_fallback(
                selected, folders, target, scratch, chunk_size=10),
                'fallback')
            self.assertEqual(file_manifest(target), expected)
            self.assertEqual([file_manifest(root/'single'/str(i))
                              for i in range(3)], singles)
            self.assertFalse(list(root.glob('phase9-proto-combined-*')))


if __name__ == '__main__':
    unittest.main()
