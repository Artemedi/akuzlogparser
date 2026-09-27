"""Isolated combined-only-miss synthetic gate; NOT app-cache integration.

All three individual reports are READY before timing a missing combined.
Modes: ordinary rederive / verified B-lite / missing sidecar fallback.
No SSH, original user data, app inventory, or persistent B-lite.
"""
from __future__ import annotations
from datetime import date
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter,process_time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from akuz_app import _iter_combined_sources
from akuz_html_explorer import generate
from akuz_store import sha256
from scripts.bench_phase9_baseline import create_sources,file_manifest
from scripts.bench_phase9_architecture_synthetic import memory_monitor
from scripts.probe_phase9_sidecar import SidecarWriter
from scripts.probe_phase9_sidecar_fallback import combined_with_fallback

ORDER=('control','b_lite','missing_sidecar',
       'missing_sidecar','control','b_lite',
       'b_lite','missing_sidecar','control')


def selected_sources(folder):
    files=sorted(folder.glob('*.log'))
    if len(files)!=3:
        raise ValueError('Expected 3 immutable synthetic sources')
    return [dict(local=p,sha=sha256(p),date=date.fromisoformat(
            p.name[:4]+'-'+p.name[4:6]+'-'+p.name[6:8]).isoformat(),
            remote=dict(host='synthetic-host',name=p.name,
              path='/synthetic/'+p.name,mtime=i))
        for i,p in enumerate(files)]


def single_manifest(home):
    return {str(i):file_manifest(home/'single'/str(i)) for i in range(3)}


def setup(mode,source,home):
    home.mkdir(parents=True,exist_ok=False)
    rows=selected_sources(source)
    started,cpu=perf_counter(),process_time()
    for i,item in enumerate(rows):
        path=item['local']; first=date.fromisoformat(item['date'])
        if mode=='b_lite':
            with SidecarWriter(home/'sidecars'/str(i),path,
                 'synthetic-host',item['remote']['path'],item['date']) as sink:
                generate(path,home/'single'/str(i),first,1000,35,
                         derived_sink=sink)
        else:
            generate(path,home/'single'/str(i),first,1000,35)
    wall,cpu_s=perf_counter()-started,process_time()-cpu
    scratch=home/'scratch.jsonl'
    scratch.write_text('',encoding='utf8')
    if (home/'combined').exists():
        raise AssertionError('Combined must initially be ABSENT')
    sidecar_bytes=(sum(f.stat().st_size for f in
             (home/'sidecars').rglob('*') if f.is_file())
             if (home/'sidecars').exists() else 0)
    return dict(setup_wall_s=round(wall,5),
        setup_cpu_s=round(cpu_s,5),sidecar_bytes=sidecar_bytes,
        singles=single_manifest(home))


def combined_child(mode,source,home):
    rows=selected_sources(source)
    before=single_manifest(home)
    if (home/'combined').exists():
        raise AssertionError('Combined unexpectedly already published')
    scratch=home/'scratch.jsonl'
    base=date.fromisoformat(rows[0]['date'])
    total=sum(item['local'].stat().st_size for item in rows)
    done_memory=memory_monitor()
    started,cpu=perf_counter(),process_time()
    if mode=='control':
        generate(scratch,home/'combined',base,1000,35,
            event_source=_iter_combined_sources(rows,base),input_bytes=total)
        path_taken='ordinary'
    elif mode in ('b_lite','missing_sidecar'):
        folders={item['remote']['name']+' · '+item['date']:
            home/'sidecars'/str(i) for i,item in enumerate(rows)}
        path_taken=combined_with_fallback(rows,folders,home/'combined',
                                           scratch,chunk_size=1000)
        if path_taken!=('sidecar' if mode=='b_lite' else 'fallback'):
            raise AssertionError('Unexpected verified replay/fallback path')
    else:
        raise ValueError('Invalid benchmark mode')
    wall,cpu_s=perf_counter()-started,process_time()-cpu
    combined=file_manifest(home/'combined')
    if single_manifest(home)!=before:
        raise AssertionError('Ready single output mutated by combined')
    memory=done_memory()
    return dict(mode=mode,path_taken=path_taken,
        wall_s=round(wall,5),cpu_s=round(cpu_s,5),
        combined=combined,singles=before,**memory)


def main():
    if len(sys.argv)==5 and sys.argv[1]=='--child':
        record=combined_child(sys.argv[2],Path(sys.argv[3]),
                              Path(sys.argv[4]))
        print('TRIAL_JSON='+json.dumps(record,ensure_ascii=False,
                            separators=(',',':')),flush=True)
        return
    if len(sys.argv)!=1:
        raise SystemExit('Use no arguments; child mode is private')
    (ROOT/'diagnostics').mkdir(exist_ok=True)
    out=ROOT/'diagnostics'/('private_phase94_combined_only_'+
                            sys.platform+'_v1.json')
    if out.exists():
        raise FileExistsError('Refuse overwriting prior evidence')
    trials=[]
    with TemporaryDirectory(prefix='phase94_combined_only_',
                            dir=ROOT/'diagnostics') as td:
        root=Path(td)
        source=root/'sources'
        create_sources(source,3000,256)
        original_sha=[sha256(p) for p in sorted(source.glob('*.log'))]
        expected_combined=None
        expected_singles=None
        for no,mode in enumerate(ORDER,1):
            home=root/f'trial_{no}_{mode}'
            pre=setup(mode,source,home)
            if expected_singles is None:
                expected_singles=pre['singles']
            elif pre['singles']!=expected_singles:
                raise AssertionError('Preexisting single report byte mismatch')
            child=subprocess.run([sys.executable,'-B',str(Path(__file__)),
                '--child',mode,str(source),str(home)],cwd=ROOT,
                capture_output=True,text=True,timeout=120,check=True)
            line=next((x for x in child.stdout.splitlines()
                       if x.startswith('TRIAL_JSON=')),None)
            if line is None:
                raise AssertionError('Missing combined worker result')
            rec=json.loads(line.partition('=')[2])
            if rec['singles']!=expected_singles:
                raise AssertionError('Combined modified preexisting singles')
            if expected_combined is None:
                expected_combined=rec['combined']
            elif rec['combined']!=expected_combined:
                raise AssertionError('Combined output BYTE parity failed')
            if [sha256(p) for p in sorted(source.glob('*.log'))]!=original_sha:
                raise AssertionError('Input snapshots were modified')
            del rec['singles'],rec['combined']
            trials.append(dict(number=no,**pre,**rec,byte_parity=True))
            # Avoid retaining sensitive-looking synthetic normalized text.
            del trials[-1]['singles']
            print('COMBINED_ONLY_SYNTHETIC_PASS',no,mode,
                  rec['wall_s'],rec['cpu_s'],rec['path_taken'],flush=True)
        output_digest=__import__('hashlib').sha256(json.dumps(
            expected_combined,sort_keys=True).encode('utf8')).hexdigest()
    mem=('peak_ws_bytes','sampled_private_peak_bytes') if sys.platform=='win32' else ('max_rss_kib',)
    summary={}
    for mode in ('control','b_lite','missing_sidecar'):
        chosen=[row for row in trials if row['mode']==mode]
        summary[mode]={key:median(row[key] for row in chosen)
            for key in ('wall_s','cpu_s','setup_wall_s',
                        'setup_cpu_s','sidecar_bytes')+mem}
        if sys.platform=='win32':
            summary[mode]['unreadable_memory_samples']=sum(
                x['unreadable_memory_samples'] for x in chosen)
            summary[mode]['min_memory_samples']=min(
                x['memory_samples'] for x in chosen)
    result=dict(status='synthetic_combined_only_not_app_cache_gate',
        platform=sys.platform,order=list(ORDER),total_events=9001,
        source_count=3,code_sha=subprocess.check_output(['git',
            'rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        byte_parity=True,unchanged_ready_singles=True,
        raw_payload_saved=False,workspace_cleaned=True,
        combined_manifest_sha256=output_digest,trials=trials,
        summary=summary)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',
                   encoding='utf8')
    print('COMBINED_ONLY_SYNTHETIC_SUMMARY',json.dumps(summary),flush=True)
    print('COMBINED_ONLY_WORKSPACE_CLEANED',True,flush=True)


if __name__=='__main__':
    main()
