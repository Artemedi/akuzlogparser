"""Confirm streamed/legacy JSONL merger matches the pre-refactor merger."""
from datetime import date
import importlib.util
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from akuz_app import _combine_sources
BASE="6e27adb"


def main():
    with TemporaryDirectory() as td:
        root=Path(td)
        path=root/"akuz_app_baseline.py"
        path.write_bytes(subprocess.check_output(["git","show",BASE+":akuz_app.py"],cwd=ROOT))
        spec=importlib.util.spec_from_file_location("baseline_app",path)
        baseline=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        root.joinpath("cache").mkdir()
        selected=[]
        for index in range(3):
            f=root/f"2026092{index+3}_synthetic.log"
            f.write_text(
                "23:59:59.999,AKUZ,req,user: System.SerializationException: fake\n"
                +("X"*12000)+"\n"+"00:00:01.000,AKUZ,req,user: next\n",
                encoding="utf-8")
            selected.append(dict(local=f,date=f"2026-09-2{index+3}",
                                 remote=dict(name=f.name,mtime=index)))
        old=root/"cache"/"old.jsonl"
        new=root/"cache"/"new.jsonl"
        a=baseline._combine_sources(selected,old,date(2026,9,23))
        b=_combine_sources(selected,new,date(2026,9,23))
        if a!=b or old.read_bytes()!=new.read_bytes():
            raise AssertionError("Combined JSONL mismatch")
        print(f"legacy_combined_equivalence=PASS events={a[0]} lines={a[1]}")


if __name__=="__main__":
    main()
