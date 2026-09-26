"""Phase 8: compare diagnostic-only generator with Phase 7 and check all bytes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
import argparse
import importlib.util
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import akuz_analytics
from akuz_html_explorer import generate
from bench_classify_pipeline import compare_files, create_log

BASE = "ef310ea34250a7434b2e1b8d4aaf13cf8f6f2de8"


def prior(path, name):
    path.write_bytes(subprocess.check_output(
        ["git", "show", BASE + ":" + name + ".py"], cwd=ROOT))
    spec = importlib.util.spec_from_file_location("phase7_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(events, mode, repeats):
    with TemporaryDirectory(prefix="akuz_phase8_bench_") as temp:
        root = Path(temp)
        source = root / "synthetic.log"
        create_log(source, events, mode)
        previous = prior(root / "old_explorer.py", "akuz_html_explorer")
        original_error = prior(root / "old_analytics.py", "akuz_analytics")
        # Baseline HTML template assets are the committed originals.
        previous.__file__ = str(ROOT / "akuz_html_explorer.py")
        metrics = {"before": [], "after": []}
        print(f"mode={mode} events={events} bytes={source.stat().st_size}",
              flush=True)
        for i in range(repeats):
            order = ("before", "after") if i % 2 == 0 else ("after", "before")
            meta = {}
            for name in order:
                out = root / f"{name}_{i}"
                start = perf_counter()
                if name == "before":
                    with patch.object(akuz_analytics, "recognize_error",
                                      original_error.recognize_error):
                        meta[name] = previous.generate(source, out, None, 1000, 35)
                else:
                    meta[name] = generate(source, out, None, 1000, 35)
                value = perf_counter() - start
                metrics[name].append(value)
                print(f"round={i} {name}_s={value:.4f}", flush=True)
            assert meta["before"] == meta["after"], "metadata changed"
            files, size = compare_files(root / f"before_{i}", root / f"after_{i}")
            print(f"round={i} OUTPUT_IDENTICAL=PASS files={files} "
                  f"bytes={size}", flush=True)
        print("PHASE7_BASELINE=" + BASE)
        print("BEFORE_S=" + repr(metrics["before"]))
        print("AFTER_S=" + repr(metrics["after"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=900)
    parser.add_argument("--mode", choices=("mixed", "heavy"), default="heavy")
    parser.add_argument("--repeats", type=int, default=2)
    options = parser.parse_args()
    run(options.events, options.mode, options.repeats)
