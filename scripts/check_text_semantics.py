"""Compare text classifiers against pre-optimization code (synthetic inputs)."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import random
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import akuz_log_parser as candidate_parser
import akuz_analytics as candidate_analytics
BASELINE="b7bfd5d37f7f6bae144b772088f0a28f0274a001"


def original(root, filename, module_name):
    path=root/filename
    path.write_bytes(subprocess.check_output(["git","show",BASELINE+":"+filename],cwd=ROOT))
    spec=importlib.util.spec_from_file_location(module_name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def samples():
    fragments=["","plain text","Timeout","timed out","ошибка обработки",
               "System.Exception: broken","SerializationException: synthetic",
               "не найдено","отказ в доступе","nack","no_data_found",
               "общее время: 00:00:01.234","за 123 ms",
               "строка\n at Service.Run()","FAİLED","faılure","faiſure",
               "taймаут","serializAtion error","ошибка сериализации"]
    rng=random.Random(260925)
    for n in range(500):
        parts=[rng.choice(fragments) for _ in range(rng.randrange(1,6))]
        parts.insert(rng.randrange(len(parts)+1), "X"*rng.choice((0,500,25000)))
        yield " ".join(parts)
    for f in fragments:
        for tail in ("","\n"+"X"*64000, "X"*64000):
            yield f+tail

def main():
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        op=original(root,"akuz_log_parser.py","original_parser")
        oa=original(root,"akuz_analytics.py","original_analytics")
        for n,raw in enumerate(samples(),1):
            expected=(op.classify(raw),op.normalize(raw),
                      op.extract_duration(raw),oa.recognize_error(raw))
            actual=(candidate_parser.classify(raw),
                    candidate_parser.normalize(raw),
                    candidate_parser.extract_duration(raw),
                    candidate_analytics.recognize_error(raw))
            if actual!=expected:
                raise AssertionError(f"Text equivalence mismatch at synthetic case {n}: "+
                  str([(i,expected[i],actual[i]) for i in range(4) if expected[i]!=actual[i]]))
        print(f"synthetic_text_cases={n} classifications=4 equivalence=PASS")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
