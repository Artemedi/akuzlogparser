"""Synthetic pipeline benchmark, isolated from installed Explorer and medical logs."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import argparse
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akuz_app import _publish
from akuz_analytics import refresh
from akuz_store import load_store, sha256


def main(events, chars):
    with TemporaryDirectory() as td:
        root=Path(td)
        source=root/"20260925_synthetic.log"
        payload="X"*max(0,chars-100)
        with source.open("w",encoding="utf-8",newline="") as out:
            for n in range(events):
                if n%5==0:
                    out.write(f"12:00:00.000,AKUZ,req,user: System.Exception: synthetic failure {n}\n")
                else:
                    out.write(f"12:00:00.000,AKUZ,req,user: synthetic {n}\n")
                out.write(payload+"\n")
        store=load_store(root)
        t=perf_counter()
        report=_publish(root,store,"benchmark",source,date(2026,9,25),
            [dict(name=source.name,date="2026-09-25",sha256=sha256(source),
                  remote_path=str(source),host="synthetic")],
            "synthetic","single")
        generated=perf_counter()-t
        t=perf_counter()
        overview=refresh(root)
        indexed=perf_counter()-t
        print(f"events={events} input_bytes={source.stat().st_size} "
              f"generate_s={generated:.4f} analytics_s={indexed:.4f} "
              f"distinct_errors={overview['distinct_errors']} "
              f"report_events={report['events']}")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--events",type=int,default=1000)
    p.add_argument("--chars",type=int,default=16384)
    args=p.parse_args()
    main(args.events,args.chars)
