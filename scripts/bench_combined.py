"""Synthetic two-source combined report benchmark, no production data."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akuz_app import _combine_sources
from akuz_html_explorer import generate


def main():
    with TemporaryDirectory() as td:
        root=Path(td)
        (root/"cache").mkdir()
        chosen=[]
        for i in range(2):
            source=root/f"2026092{i+4}_synthetic.log"
            with source.open("w",encoding="utf-8",newline="") as out:
                for n in range(500):
                    out.write(f"12:00:00.000,AKUZ,req,user: synthetic {n}\n")
                    if n%7==0:
                        out.write("System.Runtime.Serialization.SerializationException: synthetic\n")
                    out.write("X"*32668+"\n")
            chosen.append(dict(date=f"2026-09-2{i+4}",local=source,
                               remote=dict(name=source.name,mtime=i)))
        scratch=root/"cache"/"combined.jsonl"
        started=perf_counter()
        count,lines=_combine_sources(chosen,scratch,date(2026,9,24))
        merged=perf_counter()-started
        started=perf_counter()
        meta=generate(scratch,root/"combined_report",date(2026,9,24),1000,35)
        generated=perf_counter()-started
        print(f"events={count} lines={lines} jsonl_bytes={scratch.stat().st_size} "
              f"merge_s={merged:.4f} combined_generate_s={generated:.4f} "
              f"report_events={meta['events']}")


if __name__=="__main__":
    main()
