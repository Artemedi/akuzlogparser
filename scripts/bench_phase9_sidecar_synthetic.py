"""Private synthetic format measurements; NO production cache or real logs."""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
from time import perf_counter,process_time
import json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.bench_phase9_baseline import create_sources,file_manifest
from scripts.probe_phase9_sidecar import (SidecarWriter,encode_binary_frame,
                                          decode_binary_frame)
from akuz_html_explorer import generate

(ROOT/'diagnostics').mkdir(exist_ok=True)
with TemporaryDirectory(prefix='akuz_phase93_formats_',dir=ROOT/'diagnostics') as td:
    home=Path(td)
    folder=home/'sources'
    create_sources(folder,3000,256)
    sidecar_root=home/'sidecars';sidecar_root.mkdir()
    rows=[]
    for idx,source in enumerate(sorted(folder.glob('*.log'))):
        first=date.fromisoformat(source.name[:4]+'-'+source.name[4:6]+'-'+source.name[6:8])
        t0,c0=perf_counter(),process_time()
        ref=home/'baseline'/str(idx)
        generate(source,ref,first,1000,35)
        baseline=(perf_counter()-t0,process_time()-c0)
        candidate=home/'candidate'/str(idx)
        folder_out=sidecar_root/str(idx)
        t0,c0=perf_counter(),process_time()
        with SidecarWriter(folder_out,source,'synthetic-host',
                           '/synthetic/'+source.name,first.isoformat()) as sink:
            generate(source,candidate,first,1000,35,derived_sink=sink)
        with_sidecar=(perf_counter()-t0,process_time()-c0)
        assert file_manifest(ref)==file_manifest(candidate)
        body=(folder_out/'derived.jsonl').read_bytes()
        t0=perf_counter();packet=encode_binary_frame(body)
        encode_wall=perf_counter()-t0
        t0=perf_counter();assert decode_binary_frame(packet)==body
        decode_wall=perf_counter()-t0
        row=dict(day=source.name[:8],source_bytes=source.stat().st_size,
            rows=sink.count,jsonl_bytes=len(body),binary_bytes=len(packet),
            baseline_wall_s=round(baseline[0],5),
            sidecar_wall_s=round(with_sidecar[0],5),
            baseline_cpu_s=round(baseline[1],5),
            sidecar_cpu_s=round(with_sidecar[1],5),
            encode_wall_s=round(encode_wall,5),
            decode_wall_s=round(decode_wall,5),byte_parity=True)
        rows.append(row)
        print('SIDECAR_SYNTHETIC_FORMAT_PASS',row,flush=True)
    result=dict(source_kind='synthetic_only',raw_payload_saved=False,
                workspace_cleaned=True,files=rows)
    output=ROOT/'diagnostics'/'private_phase93_formats_result.json'
    if output.exists():raise FileExistsError('Refuse overwrite')
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n','utf8')
print('SIDECAR_SYNTHETIC_WORKSPACE_CLEANED',True,flush=True)
