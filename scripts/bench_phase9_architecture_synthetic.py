"""Synthetic-only Phase 9.4 fresh fan-out comparison, 3 variants × 3.

No user logs, no SSH, no production cache/inventory, no architecture choice.
One synthetic input snapshot; separate processes and owned temp outputs.
"""
from __future__ import annotations
import json
import os
from datetime import date
from pathlib import Path
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
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


def memory_monitor():
    """Process peak RSS on Linux; OS peak WS plus sampled Private on Windows.

    Sampling a short synthetic trial gives a Private Bytes LOWER BOUND,
    never a guaranteed peak. No extra package is required on Windows.
    """
    if sys.platform != 'win32':
        import resource
        def finish_linux():
            return dict(max_rss_kib=resource.getrusage(
                resource.RUSAGE_SELF).ru_maxrss,
                peak_ws_bytes=None, sampled_private_peak_bytes=None,
                memory_samples=None, unreadable_memory_samples=None)
        return finish_linux

    from scripts.phase9_memory import sample
    stop = Event()
    private = []
    unreadable = [0]
    def poll():
        while not stop.is_set():
            reading = sample(os.getpid())
            if reading is None:
                unreadable[0] += 1
            else:
                private.append(reading['private_bytes'])
            stop.wait(.01)
    worker = Thread(target=poll, name='phase94-memory-sampler', daemon=True)
    worker.start()
    def finish_windows():
        stop.set()
        worker.join(timeout=2)
        reading = sample(os.getpid())
        if reading is None:
            unreadable[0] += 1
        else:
            private.append(reading['private_bytes'])
        return dict(max_rss_kib=None,
            peak_ws_bytes=(reading['peak_working_set_bytes']
                           if reading is not None else None),
            sampled_private_peak_bytes=(max(private) if private else None),
            memory_samples=len(private),
            unreadable_memory_samples=unreadable[0])
    return finish_windows


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
    finish_memory = memory_monitor()
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
        if (result['event_count'] != sum(m['events'] for m in result['individual'])
                or len(result['parser_calls']) != len(files)):
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
    memory = finish_memory()
    return dict(mode=mode,wall_s=round(elapsed,5),
                cpu_s=round(process_cpu,5),
                report_bytes=disk_report,sidecar_bytes=disk_sidecar,
                reports=reports, **memory)


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
    result_path=ROOT/'diagnostics'/(
        'private_phase94_synthetic_result_'+sys.platform+'_v2.json')
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
                  record['max_rss_kib'] if sys.platform!='win32'
                  else record['peak_ws_bytes'],
                  record['sidecar_bytes'],flush=True)
        digest=__import__('hashlib').sha256(json.dumps(reference,
            sort_keys=True).encode('utf8')).hexdigest()
    grouped=defaultdict(list)
    for record in records:
        grouped[record['mode']].append(record)
    memory_keys = (('peak_ws_bytes','sampled_private_peak_bytes')
                   if sys.platform == 'win32' else ('max_rss_kib',))
    summary={mode:{key:median(r[key] for r in grouped[mode])
                  for key in ('wall_s','cpu_s','report_bytes',
                              'sidecar_bytes') + memory_keys}
             for mode in ('control','tee','b_lite')}
    if sys.platform == 'win32':
        for mode in summary:
            summary[mode]['total_unreadable_memory_samples'] = sum(
                r['unreadable_memory_samples'] for r in grouped[mode])
            summary[mode]['min_memory_samples'] = min(
                r['memory_samples'] for r in grouped[mode])
    result=dict(status='synthetic_only_not_architecture_approval',
        platform=sys.platform,
        memory_definition=('OS peak Working Set; sampled Private Bytes lower bound'
             if sys.platform=='win32' else 'Linux ru_maxrss KiB'),
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
