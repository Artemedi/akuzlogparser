"""Isolated Phase 9.2 tee prototype; NEVER runs on user cache or SSH."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from scripts.bench_phase9_baseline import create_sources, file_manifest
from scripts.probe_phase9_tee import (TeeCancelled, tee_generate,
                                     baseline_generate)
from akuz_derived import derive_event


class TeePrototypeTests(unittest.TestCase):
    def test_fresh_tee_byte_equivalence_bounded_queue_and_single_derive(self):
        with TemporaryDirectory(prefix='akuz_phase92_tee_') as td:
            root = Path(td)
            sources = root/'sources'
            create_sources(sources, 12, 64)
            files = sorted(sources.glob('*.log'))
            scratch = root/'scratch.jsonl'
            scratch.write_text('', encoding='utf-8')
            originals = [root/'baseline'/str(i) for i in range(3)]
            optimized = [root/'tee'/str(i) for i in range(3)]
            control_combined = root/'baseline'/'combined'
            tee_combined = root/'tee'/'combined'
            baseline_meta = baseline_generate(files, originals,
                                              control_combined, scratch)
            for bound in (1, 16):
                if bound == 16:
                    optimized = [root/'tee_16'/str(i) for i in range(3)]
                    tee_combined = root/'tee_16'/'combined'
                with patch('akuz_derived.derive_event',
                           wraps=derive_event) as derive_spy:
                    result = tee_generate(files, optimized, tee_combined,
                                          scratch, queue_size=bound)
                self.assertEqual(derive_spy.call_count,37)
                self.assertEqual(result['parser_calls'], [p.name for p in files])
                self.assertEqual(result['event_count'], 37)
                self.assertEqual(result['derived_events'], 37)
                self.assertEqual(result['queue_bound'], bound)
                self.assertEqual(result['combined']['events'],
                                 baseline_meta['events'])
                for old,new in zip(originals,optimized):
                    self.assertEqual(file_manifest(old),file_manifest(new))
                self.assertEqual(file_manifest(control_combined),
                                 file_manifest(tee_combined))
            self.assertFalse(list(root.rglob('inventory.json')))

    def test_combined_failure_cancels_bounded_queue_without_inventory(self):
        with TemporaryDirectory(prefix='akuz_phase92_fault_') as td:
            root=Path(td)
            sources=root/'sources'
            create_sources(sources, 12, 64)
            files=sorted(sources.glob('*.log'))
            scratch=root/'scratch.jsonl'
            scratch.write_text('',encoding='utf-8')
            with self.assertRaises((ValueError,TeeCancelled)):
                tee_generate(files,[root/'tee'/str(i) for i in range(3)],
                    root/'tee'/'combined',scratch,queue_size=1,
                    fail_combined_at=5)
            self.assertFalse(list(root.rglob('inventory.json')))
            self.assertEqual(len(list(sources.glob('*.log'))),3)


if __name__ == '__main__':
    unittest.main()
