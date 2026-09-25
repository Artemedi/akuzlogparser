"""Synthetic text-classifier microbenchmarks, short versus large messages."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import akuz_log_parser as p
import akuz_analytics as a
BASE="b7bfd5d37f7f6bae144b772088f0a28f0274a001"


def original(tmp,source):
    path=tmp/source
    path.write_bytes(subprocess.check_output(["git","show",BASE+":"+source],cwd=ROOT))
    spec=importlib.util.spec_from_file_location("original_"+source[:-3],path)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def timeit(fn, rows):
    started=perf_counter()
    results=[fn(x) for x in rows]
    return perf_counter()-started,results


def main():
    short=["ordinary message 1234",
           "System.Exception: synthetic 1234",
           "нет ошибок 1234","за 3 ms",
           "обычное служебное сообщение без ошибок"]
    samples=[x for _ in range(4000) for x in short]
    long=[("X"*32768)+x for x in short[:3]]
    with TemporaryDirectory() as td:
        op=original(Path(td),"akuz_log_parser.py")
        oa=original(Path(td),"akuz_analytics.py")
        for label,data in (("short",samples),("long",long*40)):
            for method,old,new in (("classify",op.classify,p.classify),
                                   ("duration",op.extract_duration,p.extract_duration),
                                   ("recognize_error",oa.recognize_error,a.recognize_error)):
                old_s,baseline=timeit(old,data)
                new_s,current=timeit(new,data)
                assert baseline==current,(label,method)
                print(f"{label}.{method} items={len(data)} "
                      f"old_s={old_s:.4f} new_s={new_s:.4f} "
                      f"speedup={old_s/new_s:.2f}x")


if __name__=="__main__":
    main()
