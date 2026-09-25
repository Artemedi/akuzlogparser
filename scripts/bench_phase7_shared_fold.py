"""Differential Phase 6 vs Phase 7 benchmark with byte-identical report checks."""
import argparse
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import akuz_log_parser
from akuz_html_explorer import generate as current_generate
from bench_classify_pipeline import create_log, compare_files

BASELINE = "96b0ea52db094fa957006fd81c12d4a80396c5cb"


def old_module(path, name, commit_path):
    content = subprocess.check_output(
        ["git", "show", BASELINE + ":" + commit_path], cwd=ROOT)
    path.write_bytes(content)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(events, mode, repeat):
    with TemporaryDirectory(prefix="akuz_phase7_fold_") as d:
        root = Path(d)
        source = root / "20260925_synthetic.log"
        create_log(source, events, mode)
        old_html = old_module(root / "old_html.py", "old_html_phase7",
                              "akuz_html_explorer.py")
        old_html.__file__ = str(ROOT / "akuz_html_explorer.py")
        old_parser = old_module(root / "old_parser.py", "old_parser_phase7",
                                "akuz_log_parser.py")
        print(f"mode={mode} events={events} input_bytes={source.stat().st_size}",
              flush=True)
        results = {"before": [], "after": []}
        for round_number in range(repeat):
            sequence = ("before", "after") if round_number % 2 == 0 else ("after", "before")
            for name in sequence:
                out = root / (name + "_" + str(round_number))
                start = perf_counter()
                if name == "before":
                    with patch.object(akuz_log_parser, "classify", old_parser.classify), \
                         patch.object(akuz_log_parser, "extract_duration",
                                      old_parser.extract_duration):
                        metadata = old_html.generate(source, out, None, 1000, 35)
                else:
                    metadata = current_generate(source, out, None, 1000, 35)
                elapsed = perf_counter() - start
                results[name].append(elapsed)
                print(f"round={round_number} mode={name} elapsed_s={elapsed:.4f} "
                      f"events={metadata['events']}", flush=True)
                if name == "before":
                    before_meta = metadata
                else:
                    after_meta = metadata
            assert before_meta == after_meta, (before_meta, after_meta)
            files, size = compare_files(root / ("before_" + str(round_number)),
                                        root / ("after_" + str(round_number)))
            print(f"round={round_number} BYTE_EQUIVALENCE=PASS "
                  f"files={files} output_bytes={size}", flush=True)
        print("BASELINE=" + BASELINE)
        print("BEFORE_S=" + repr(results["before"]))
        print("AFTER_S=" + repr(results["after"]))


if __name__ == "__main__":
    arg = argparse.ArgumentParser()
    arg.add_argument("--events", type=int, default=900)
    arg.add_argument("--mode", choices=["mixed", "heavy"], default="heavy")
    arg.add_argument("--repeat", type=int, default=2)
    opts = arg.parse_args()
    main(opts.events, opts.mode, opts.repeat)
