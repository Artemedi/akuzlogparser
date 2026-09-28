"""Contract tests for Phase 11 process-isolated 23+24+25 benchmark."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_fetch import ConnectConfig
from scripts import bench_phase11_process_overlap_multi as bench
from scripts.bench_phase11_ssh_overlap import SourceSpec


def row(mode, wall, fetch24, fetch25, digest="a"*64, manifest="b"*64):
    base = dict(
        mode=mode, wall_s=wall, process_cpu_s=wall/2,
        fetched=3, parsed=3,
        outputs=[dict(day=d, report_manifest_sha256=manifest)
                 for d in bench.DAYS],
        fetch={
            "20260923": 1.0, "20260924": fetch24, "20260925": fetch25},
        parse={
            d: dict(wall_s=2.0, process_cpu_s=1.0,
                    report_manifest_sha256=manifest)
            for d in bench.DAYS},
        source_sha={d:digest for d in bench.DAYS},
        sampled_peak_ws_bytes=300,
        sampled_peak_private_bytes=250,
        memory_samples=20, unreadable_memory_samples=0,
        workspace_bytes=1000)
    if mode == "process":
        base.update(
            parent_cpu_s=4.0, child_cpu_s=2.0, max_sampled_processes=2,
            fetch_ready_latency={
                "20260923":1.0,
                "20260924":fetch24+3.0,
                "20260925":fetch25+4.0})
    return base


class Phase11ProcessMultiTests(unittest.TestCase):
    def cfg(self, root):
        return ConnectConfig(
            "example.test",22,"reader","","","",
            "/srv/akuz",root/"downloads","*.log","",False)

    def specs(self):
        return tuple(
            SourceSpec(day, day+".log", "/srv/"+day+".log",
                       10+i, 1, 100+i)
            for i,day in enumerate(bench.DAYS))

    def test_summary_preserves_fetch_and_readiness(self):
        rows=[
            row("serial",12,4,8),
            row("process",9,5,6),
            row("process",10,6,7),
            row("serial",14,5,9),
        ]
        s=bench._summary(rows,"serial")
        p=bench._summary(rows,"process")
        self.assertEqual(s["wall_median_s"],13)
        self.assertEqual(p["wall_median_s"],9.5)
        self.assertEqual(p["fetch_values_s"]["20260925"],[6,7])
        self.assertEqual(p["ready_values_s"]["20260925"],[10,11])

    def test_run_balanced_order_and_strips_hashes(self):
        with TemporaryDirectory(prefix="akuz_p11_multi_") as td:
            root=Path(td); diag=root/"diag"; diag.mkdir()
            cfg=self.cfg(root); specs=self.specs()
            serial=[row("serial",12,4,8),row("serial",14,5,9)]
            proc=[row("process",9,5,6),row("process",10,6,7)]
            sc=pc=0; calls=[]
            def fake_serial(config, got, mode_root, *, overlap):
                nonlocal sc
                self.assertFalse(overlap); self.assertEqual(tuple(got),specs)
                calls.append("serial"); value=serial[sc]; sc+=1; return value
            def fake_proc(config, got, mode_root):
                nonlocal pc
                self.assertEqual(tuple(got),specs)
                calls.append("process"); value=proc[pc]; pc+=1; return value
            with patch.object(bench,"load_config",return_value=cfg), \
                 patch.object(bench,"_discover_three",return_value=specs), \
                 patch.object(bench,"_run_mode",side_effect=fake_serial), \
                 patch.object(bench,"_process_multi_mode",side_effect=fake_proc):
                result=bench.run(Path("cfg"),root,diag)
            self.assertEqual(calls,list(bench.ORDER))
            self.assertTrue(result["workspace_cleaned"])
            self.assertTrue(result["source_sha_equal"])
            self.assertTrue(result["report_manifest_equal"])
            self.assertFalse(result["raw_payload_saved"])
            for trial in result["trials"]:
                self.assertNotIn("source_sha",trial)
                self.assertNotIn("report_manifest_sha256",trial["outputs"][0])

    def test_run_rejects_cross_trial_source_drift(self):
        with TemporaryDirectory(prefix="akuz_p11_multi_bad_") as td:
            root=Path(td); diag=root/"diag"; diag.mkdir()
            cfg=self.cfg(root); specs=self.specs()
            seq=[
                row("serial",12,4,8),
                row("process",9,5,6,digest="c"*64),
            ]
            with patch.object(bench,"load_config",return_value=cfg), \
                 patch.object(bench,"_discover_three",return_value=specs), \
                 patch.object(bench,"_run_mode",side_effect=lambda *a,**k: seq.pop(0)), \
                 patch.object(bench,"_process_multi_mode",side_effect=lambda *a,**k: seq.pop(0)):
                with self.assertRaisesRegex(AssertionError,"source SHA"):
                    bench.run(Path("cfg"),root,diag)


if __name__=="__main__":
    unittest.main()
