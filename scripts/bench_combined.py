"""Synthetic two-source combined report benchmark, no production data."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akuz_app import _combine_sources, _iter_combined_sources
from akuz_html_explorer import generate
from hashlib import sha256


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
        jsonl_bytes=scratch.stat().st_size
        catalog_hash=sha256((root/"combined_report"/"data"/"catalog.js").read_bytes()).hexdigest()
        scratch.write_bytes(b"")
        started=perf_counter()
        stream=generate(scratch,root/"stream_report",date(2026,9,24),1000,35,
            event_source=_iter_combined_sources(chosen,date(2026,9,24)),
            input_bytes=sum(x["local"].stat().st_size for x in chosen))
        streamed=perf_counter()-started
        assert meta==stream
        assert catalog_hash==sha256((root/"stream_report"/"data"/"catalog.js").read_bytes()).hexdigest()
        assert sha256((root/"combined_report"/"data"/"raw_00000.js").read_bytes()).hexdigest()==sha256((root/"stream_report"/"data"/"raw_00000.js").read_bytes()).hexdigest()
        print(f"events={count} lines={lines} jsonl_bytes={jsonl_bytes} "
              f"legacy_merge_s={merged:.4f} legacy_generate_s={generated:.4f} "
              f"direct_generate_s={streamed:.4f} equivalence=PASS")


if __name__=="__main__":
    main()
