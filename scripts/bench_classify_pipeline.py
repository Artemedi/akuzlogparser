"""Reproducible real generator benchmark + byte-for-byte output comparison.

Self-contained synthetic records only. Compares original c57f7a1 regex classify
against find-based classify on the SAME input, HTML/JS files and metadata.
"""
import hashlib
import importlib.util
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import akuz_log_parser
from akuz_html_explorer import generate

BASELINE = "c57f7a13776ae4943004c5a350a0d166ea39ddc3"


def original(root):
    source = subprocess.check_output(
        ["git", "show", BASELINE + ":akuz_log_parser.py"], cwd=ROOT)
    path = root / "old_parser.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("regex_parser_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.classify


def create_log(path, total, mode="mixed"):
    if mode == "heavy":
        # Long multi-line records with category markers late in the message:
        # a bounded synthetic approximation of heavy AKUZ stack/XML events.
        payload = "X" * 65536
        with path.open("w", encoding="utf-8", newline="") as out:
            for i in range(total):
                out.write(f"12:00:{i%60:02d}.000,AKUZ,req{i},user: synthetic\n")
                out.write(payload + (" timeout" if i % 2 else " System.Exception: synthetic") + "\n")
        return
    rng = random.Random(42)
    words = ["пациент", "запрос", "данные", "сервер", "AKUZ.Service", "обработка"]
    with path.open("w", encoding="utf-8", newline="") as out:
        for i in range(total):
            out.write(
                f"12:{(i//600)%60:02d}:{i%60:02d}.123,AKUZ.Component{i%17},"
                f"req-{i:08d},user{i%40}: сообщение {i} обработка данных\n")
            fraction = rng.random()
            if fraction < 0.70:
                continue
            if fraction < 0.90:
                out.write((" XML блок " * 250) + "\n")
            else:
                out.write(
                    "System.Runtime.Serialization.SerializationException: "
                    "сбой сериализации объекта Patient\n")
                for depth in range(80):
                    out.write(f"   at AKUZ.Service.Layer{depth}.Method{depth%9}"
                              f"(Int32 idx, String value)\n")


def compare_files(old, new):
    left = {f.relative_to(old).as_posix(): f for f in old.rglob("*")
            if f.is_file()}
    right = {f.relative_to(new).as_posix(): f for f in new.rglob("*")
             if f.is_file()}
    assert left.keys() == right.keys(), (left.keys() - right.keys(),
                                         right.keys() - left.keys())
    total = 0
    for name, first in left.items():
        second = right[name]
        a = hashlib.sha256(first.read_bytes()).hexdigest()
        b = hashlib.sha256(second.read_bytes()).hexdigest()
        assert a == b, f"Output mismatch: {name}"
        total += first.stat().st_size
    return len(left), total


def main(events=50000, mode="mixed"):
    with tempfile.TemporaryDirectory(prefix="akuz_bench_find_") as td:
        root = Path(td)
        source = root / "20260925_synthetic.log"
        create_log(source, events, mode=mode)
        original_classify = original(root)
        print(f"events_requested={events} input_bytes={source.stat().st_size}",
              flush=True)
        times = []
        for name, implementation in (
            ("regex", original_classify), ("find", akuz_log_parser.classify)):
            with patch.object(akuz_log_parser, "classify", implementation):
                start = perf_counter()
                meta = generate(source, root / name, None, 1000, 35)
                elapsed = perf_counter() - start
            times.append(elapsed)
            print(f"{name}_s={elapsed:.4f} events={meta['events']} "
                  f"shards={meta['chunks']}", flush=True)
            if name == "regex":
                baseline = meta
            else:
                assert baseline == meta, f"metadata mismatch: {baseline} != {meta}"
        files, size = compare_files(root / "regex", root / "find")
        print(f"full_output_byte_equivalence=PASS files={files} bytes={size}",
              flush=True)
        print(f"pipeline_speedup={times[0] / times[1]:.3f}x "
              f"baseline_s={times[0]:.3f} optimized_s={times[1]:.3f}",
              flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 50000,
         sys.argv[2] if len(sys.argv) > 2 else "mixed")
