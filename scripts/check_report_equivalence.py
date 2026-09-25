"""Byte-compare generated v4 catalogs/shards against pre-fast-path source code."""
from contextlib import ExitStack
from datetime import date
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import akuz_log_parser as parser
import akuz_analytics as analytics
from akuz_html_explorer import generate
BASELINE="b7bfd5d37f7f6bae144b772088f0a28f0274a001"


def old(root,name):
    destination=root/name
    destination.write_bytes(subprocess.check_output(
        ["git","show",f"{BASELINE}:{name}"],cwd=ROOT))
    spec=importlib.util.spec_from_file_location("baseline_"+name[:-3],destination)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check():
    with TemporaryDirectory() as td:
        root=Path(td)
        src=root/"20260925_synthetic.log"
        with src.open("w",encoding="utf-8",newline="") as stream:
            for i in range(160):
                if i%5==0:
                    prefix="System.Runtime.Serialization.SerializationException: fake"
                elif i%5==1:
                    prefix="запись: за 12.3 ms; общее время: 00:00:01.234"
                elif i%5==2:
                    prefix="not found then timed out"
                else:
                    prefix="synthetic text"
                stream.write(f"12:00:00.000,AKUZ,req,user: {prefix}\n")
                stream.write("X"*32768+"\n")
        original_parser=old(root,"akuz_log_parser.py")
        original_analytics=old(root,"akuz_analytics.py")
        before=root/"baseline"
        after=root/"optimized"
        with ExitStack() as patches:
            for name in ("classify","normalize","extract_duration"):
                patches.enter_context(patch.object(parser,name,getattr(original_parser,name)))
            patches.enter_context(patch.object(analytics,"recognize_error",
                                               original_analytics.recognize_error))
            baseline=generate(src,before,date(2026,9,25),50,35)
        candidate=generate(src,after,date(2026,9,25),50,35)
        if baseline != candidate:
            raise AssertionError(f"Report metadata differs: {baseline} != {candidate}")
        for name in ("catalog.js",)+tuple(f"raw_{i:05d}.js" for i in range(4)):
            a=(before/"data"/name).read_bytes()
            b=(after/"data"/name).read_bytes()
            if a!=b:
                raise AssertionError("Report bytes differ: "+name)
        print(f"equivalence=PASS events={candidate['events']} "
              f"shards={candidate['chunks']} catalog_and_raw_bytes=IDENTICAL")


if __name__=="__main__":
    check()
