"""Synthetic end-to-end report benchmark. No production logs or app data."""
from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import argparse
import cProfile
import io
import pstats
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akuz_html_explorer import generate


def run(events: int, chars: int, *, profile: bool):
    with TemporaryDirectory() as td:
        root=Path(td)
        source=root/"20260925_synthetic.log"
        segment="X"*max(0,chars-100)
        with source.open("w",encoding="utf-8",newline="") as out:
            for i in range(events):
                out.write(f"12:00:00.000,AKUZ,req,user: synthetic {i}\n")
                if i%7 == 0:
                    out.write("System.Runtime.Serialization.SerializationException: synthetic\n")
                out.write(segment+"\n")
        profiler=cProfile.Profile()
        start=perf_counter()
        if profile:
            profiler.enable()
        meta=generate(source,root/"output",date(2026,9,25),1000,35)
        if profile:
            profiler.disable()
        elapsed=perf_counter()-start
        print(f"events={meta['events']} input_bytes={source.stat().st_size} "
              f"elapsed_s={elapsed:.4f} chunks={meta['chunks']}",flush=True)
        if profile:
            report=io.StringIO()
            pstats.Stats(profiler,stream=report).sort_stats("cumtime").print_stats(25)
            print(report.getvalue())


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--events",type=int,default=500)
    parser.add_argument("--chars",type=int,default=32768)
    parser.add_argument("--profile",action="store_true")
    args=parser.parse_args()
    run(args.events,args.chars,profile=args.profile)
