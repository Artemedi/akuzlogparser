"""Synthetic-only Phase 9.4 fresh fan-out comparison, 3 variants × 3.

No user logs, no SSH, no production cache/inventory, no architecture choice.
One synthetic input snapshot; separate processes and owned temp outputs.
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path
import resource
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from akuz_app import _iter_combined_sources
from akuz_html_explorer import generate
from akuz_store import sha256
from scripts.bench_phase9_baseline import create_sources, file_manifest
from scripts.probe_phase9_tee import tee_generate
from scripts.probe_phase9_sidecar import SidecarWriter
from scripts.probe_phase9_sidecar_fallback import combined_with_fallback

ORDER = ('control','tee','b_lite',
         'b_lite','control','tee',
         'tee','b_lite','control')


def trial(mode: str, sources: Path, home: Path):
    files = sorted(sources.glob('*.log'))
    if len(files) != 3:
        raise ValueError('Expected three synthetic dated sources')
    days = [date.fromisoformat(p.name[:4]+'-'+p.name[4:6]+'-'+p.name[6:8])
            for p in files]
    identities = [dict(local=p,sha=sha256(p),date=d.isoformat(),
             remote=dict(name=p.name,host='synthetic-host',
                         path='/synthetic/'+p.name,mtime=i))
                  for i,(p,d) in enumerate(zip(files,days))]
    scratch = home/'scratch.jsonl'
    scratch.write_text('',encoding='utf8')
    targets = [home/'reports'/str(i) for i in range(3)]
    combined = home/'reports'/'combined'
    all_input_bytes = sum(p.stat().st_size for p in files)
    start,cpu = perf_counter(),process_time()
    if mode == 'control':
        for file,out,day in zip(files,targets,days):
            generate(file,out,day,1000,35)
        generate(scratch,combined,days[0],1000,35,
                 event_source=_iter_combined_sources(identities,days[0]),
                 input_bytes=all_input_bytes)
    elif mode == 'tee':
        result = tee_generate(files,targets,combined,scratch,
                              queue_size=16,chunk_size=1000)
        if result['event_count'] != 9001 or len(result['parser_calls']) != 3:
            raise AssertionError('Tee source or event counts changed')
    elif mode == 'b_lite':
        folders={}
        for i,(item,out,day) in enumerate(zip(identities,targets,days)):
            source=item['local']
            label=item['remote']['name']+' · '+item['date']
            folder=home/'sidecars'/str(i)
            with SidecarWriter(folder,source,'synthetic-host',
                    item['remote']['path'],item['date']) as sidecar:
                generate(source,out,day,1000,35,derived_sink=sidecar)
            folders[label]=folder
        if combined_with_fallback(identities,folders,combined,scratch,
                                  chunk_size=1000) != 'sidecar':
            raise AssertionError('Fresh B-lite sidecar unexpectedly invalid')
    else:
        raise ValueError('Unknown candidate')
    elapsed = perf_counter()-start
    process_cpu=process_time()-cpu
    reports={str(i):file_manifest(out)
             for i,out in enumerate(targets)}
    reports['combined']=file_manifest(combined)
    disk_report=sum(f.stat().st_size for f in (home/'reports').rglob('*')
                    if f.is_file())
    disk_sidecar=sum(f.stat().st_size for f in (home/'sidecars').rglob('*')
                     if f.is_file()) if (home/'sidecars').exists() else 0
    return dict(mode=mode,wall_s=round(elapsed,5),
                cpu_s=round(process_cpu,5),
                max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                report_bytes=disk_report,sidecar_bytes=disk_sidecar,
                reports=reports)


def main():
    if len(sys.argv) == 5 and sys.argv[1] == '--child':
        mode,source,home=sys.argv[2],Path(sys.argv[3]),Path(sys.argv[4])
        home.mkdir(parents=True,exist_ok=False)
        print('TRIAL_JSON='+json.dumps(trial(mode,source,home),
             ensure_ascii=False,separators=(',',':')),flush=True)
        return
    if len(sys.argv) != 1:
        raise SystemExit('Usage: python -B scripts/bench_phase9_architecture_synthetic.py')
    from collections import defaultdict
    (ROOT/'diagnostics').mkdir(exist_ok=True)
    result_path=ROOT/'diagnostics'/'private_phase94_synthetic_result.json'
    if result_path.exists():
        raise FileExistsError('Refusing to overwrite earlier A/B evidence')
    records=[]
    with TemporaryDirectory(prefix='phase94_compare_',dir=ROOT/'diagnostics') as td:
        root=Path(td)
        source=root/'sources'
        create_sources(source,3000,256)
        source_sha=[sha256(p) for p in sorted(source.glob('*.log'))]
        reference=None
        for number,mode in enumerate(ORDER,1):
            home=root/f'trial_{number}_{mode}'
            child=subprocess.run([sys.executable,'-B',str(Path(__file__)),
                '--child',mode,str(source),str(home)],cwd=ROOT,
                text=True,capture_output=True,timeout=120,check=True)
            line=next((line for line in child.stdout.splitlines()
                       if line.startswith('TRIAL_JSON=')),None)
            if line is None:
                raise AssertionError('Trial did not emit numeric+hash evidence')
            record=json.loads(line.partition('=')[2])
            if reference is None:
                reference=record['reports']
            if record['reports']!=reference:
                raise AssertionError('Single or combined report BYTES changed')
            if [sha256(p) for p in sorted(source.glob('*.log'))]!=source_sha:
                raise AssertionError('Synthetic source changed')
            del record['reports']
            records.append(dict(number=number,**record,byte_parity=True))
            print('ARCHITECTURE_SYNTHETIC_PASS',number,mode,
                  record['wall_s'],record['cpu_s'],
                  record['max_rss_kib'],record['sidecar_bytes'],flush=True)
        digest=__import__('hashlib').sha256(json.dumps(reference,
            sort_keys=True).encode('utf8')).hexdigest()
    grouped=defaultdict(list)
    for record in records:
        grouped[record['mode']].append(record)
    summary={mode:{key:median(r[key] for r in grouped[mode])
                  for key in ('wall_s','cpu_s','max_rss_kib',
                              'report_bytes','sidecar_bytes')}
             for mode in ('control','tee','b_lite')}
    result=dict(status='synthetic_only_not_architecture_approval',
        code_sha=subprocess.check_output(['git','rev-parse','HEAD'],
            cwd=ROOT,text=True).strip(),
        order=list(ORDER),sources=3,total_events=9001,
        output_manifest_sha=digest,parity=True,workspace_cleaned=True,
        raw_payload_saved=False,trials=records,summary=summary)
    result_path.write_text(json.dumps(result,indent=2,ensure_ascii=False)
                            +'\n',encoding='utf8')
    print('ARCHITECTURE_SYNTHETIC_SUMMARY',
          json.dumps(summary,ensure_ascii=False),flush=True)
    print('ARCHITECTURE_WORKSPACE_CLEANED',True,flush=True)


if __name__ == '__main__':
    main()
