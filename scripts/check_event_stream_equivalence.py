"""Compare new parser with pinned v4.5.0 on generated, non-production inputs."""
from collections import Counter
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import random
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from akuz_log_parser import event_stream as candidate

BASELINE="e317722847ce287f6e279d2c2cd172d9f5a5663a"


def fixture(seed: int) -> bytes:
    rng=random.Random(seed)
    parts=[b"synthetic preamble\r\n",b"unknown header\xff\n"]
    for i in range(80):
        h=i%24
        if i%9==0:
            parts.append(f"user 'tester' {h:02d}:02:00.000: synthetic\n".encode())
        else:
            parts.append(f"{h:02d}:02:00.000,AKUZ,req,operator: synthetic\n".encode())
        for j in range(rng.randrange(0,45)):
            length=rng.randrange(0,2000)
            parts.append(("chunk"+("X"*length)+"\n").encode())
    parts.extend([b"23:59:59.999,AKUZ,req,operator: large\n"])
    parts.extend([b"X"*4096+b"\n"]*1500)
    return b"".join(parts)

def main():
    original=subprocess.check_output(
        ["git","show",BASELINE+":akuz_log_parser.py"],cwd=ROOT)
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        before=root/"akuz_original.py"
        before.write_bytes(original)
        spec=importlib.util.spec_from_file_location("akuz_original",before)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for seed in (3,17,41):
            source=root/f"{seed}_synthetic.log"
            source.write_bytes(fixture(seed))
            old_stats,new_stats=Counter(),Counter()
            previous=list(module.event_stream(source,old_stats))
            current=list(candidate(source,new_stats))
            if previous!=current or old_stats!=new_stats:
                raise AssertionError(f"Mismatch for synthetic seed={seed}")
            print(f"seed={seed} events={len(current)} "
                  f"lines={new_stats['physical_lines']} equivalence=PASS")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
