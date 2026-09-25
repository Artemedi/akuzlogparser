"""Reproducible synthetic multiline benchmark: no production AKUZ data."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import argparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from akuz_log_parser import event_stream


def run(lines: int, chars: int):
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/"synthetic.log"
        with path.open("w",encoding="utf-8",newline="") as output:
            output.write("12:00:00.000,AKUZ,synthetic,user: synthetic\n")
            for i in range(lines):
                output.write(f"line-{i:06d}: "+("X"*max(0,chars-13))+"\n")
        start=perf_counter()
        stats=Counter()
        events=list(event_stream(path,stats))
        seconds=perf_counter()-start
        fingerprint=sha256(events[0]["raw"].encode("utf-8")).hexdigest()
        print(f"continuations={lines} input_bytes={path.stat().st_size} "
              f"elapsed_s={seconds:.4f} events={len(events)} "
              f"lines={stats['physical_lines']} sha256={fingerprint}")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--lines",type=int,default=5000)
    parser.add_argument("--chars",type=int,default=4096)
    args=parser.parse_args()
    run(args.lines,args.chars)
