"""Isolated combined-only-miss benchmark; NOT app-cache integration.

Synthetic default; optional READ-ONLY linked real local AKUZ .log snapshots.
All three singles are READY before timing missing combined; no SSH,
normal app inventory or persistent B-lite. Outputs stay disposable.
"""
from __future__ import annotations
from datetime import date
import json
import os
import re
import shutil
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
SMOKE_ORDER=ORDER[:3]  # ONE observation per mode; never a complete A/B gate.
REAL_DAYS=('20260923','20260924','20260925')


def link_real_sources(downloads: Path, dest: Path):
    """Hardlink exactly three pre-existing local .log snapshots; never write them.

    The destination is inside this invocation's OWN TemporaryDirectory.
    Fail if linking across volumes; copying a large input into a timed
    experiment would conceal a different source identity or create pressure.
    """
    if not downloads.is_dir() or downloads.is_symlink():
        raise ValueError('Expected an existing non-symlink local source folder')
    by_day={}
    for file in downloads.iterdir():
        if not file.is_file() or file.is_symlink():
            continue
        matched=re.search(r'(202609(?:23|24|25)_server\.log)$',file.name)
        if matched:
            day=matched.group(1)[:8]
            if day in by_day:
                raise ValueError('Ambiguous duplicate date in local source folder')
            by_day[day]=file
    if set(by_day)!=set(REAL_DAYS):
        raise ValueError('Expected unique local AKUZ .log dates 23/24/25 Sep')
    dest.mkdir()
    evidence=[]
    for day in REAL_DAYS:
        original=by_day[day]
        digest=sha256(original)
        target=dest/(day+'_server.log')
        os.link(original,target)
        if sha256(target)!=digest:
            raise AssertionError('Source changed during hardlink creation')
        evidence.append(dict(date=day,bytes=target.stat().st_size,
                             sha256=digest))
    return evidence, by_day


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
    args=sys.argv[1:]
    smoke='--smoke' in args
    remaining=[x for x in args if x!='--smoke']
    if remaining and (len(remaining)!=2 or remaining[0]!='--real-sources'):
        raise SystemExit('Usage: [--smoke] [--real-sources LOCAL_DOWNLOAD_DIR]')
    if args.count('--smoke')>1:
        raise SystemExit('Duplicate --smoke')
    real=bool(remaining)
    downloads=Path(remaining[1]) if real else None
    if real and sys.platform!='win32':
        raise SystemExit('Real local 23/24/25 AKUZ .log gate is Windows-only')
    (ROOT/'diagnostics').mkdir(exist_ok=True)
    tag=('real_' if real else '')+sys.platform+('_smoke' if smoke else '')
    out=ROOT/'diagnostics'/('private_phase94_combined_only_'+tag+'_v1.json')
    if out.exists():
        raise FileExistsError('Refuse overwriting prior evidence')
    order=SMOKE_ORDER if smoke else ORDER
    trials=[]
    with TemporaryDirectory(prefix='phase94_combined_only_',
                            dir=ROOT/'diagnostics') as td:
        root=Path(td)
        source=root/'sources'
        originals={}
        if real:
            source_evidence,originals=link_real_sources(downloads,source)
        else:
            create_sources(source,3000,256)
            source_evidence=[dict(date=p.name[:8],bytes=p.stat().st_size,
                                  sha256=sha256(p))
                             for p in sorted(source.glob('*.log'))]
        def verify_sources():
            for entry in source_evidence:
                if real:
                    linked=source/(entry['date']+'_server.log')
                else:
                    matches=list(source.glob(entry['date']+'*.log'))
                    if len(matches)!=1:
                        raise AssertionError('Synthetic source date ambiguous')
                    linked=matches[0]
                if (linked.stat().st_size!=entry['bytes'] or
                    sha256(linked)!=entry['sha256']):
                    raise AssertionError('Input source changed during real A/B')
                if real:
                    original=originals[entry['date']]
                    if (not os.path.samefile(original,linked) or
                        sha256(original)!=entry['sha256']):
                        raise AssertionError('Original local snapshot changed')
        verify_sources()
        expected_combined=None
        expected_singles=None
        for no,mode in enumerate(order,1):
            verify_sources()
            home=root/f'trial_{no}_{mode}'
            pre=setup(mode,source,home)
            if expected_singles is None:
                expected_singles=pre['singles']
            elif pre['singles']!=expected_singles:
                raise AssertionError('Preexisting single report byte mismatch')
            child=subprocess.run([sys.executable,'-B',str(Path(__file__)),
                '--child',mode,str(source),str(home)],cwd=ROOT,
                capture_output=True,text=True,timeout=960 if real else 120,
                check=True)
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
            verify_sources()
            del rec['singles'],rec['combined']
            trials.append(dict(number=no,**pre,**rec,byte_parity=True))
            del trials[-1]['singles']
            print('COMBINED_ONLY_TRIAL_PASS',no,mode,
                  rec['wall_s'],rec['cpu_s'],rec['path_taken'],flush=True)
            # Each real trial can create >2 GB; never retain nine builds.
            # Only delete a direct child of this owned TemporaryDirectory.
            if home.parent!=root or home.is_symlink():
                raise AssertionError('Refuse unowned benchmark cleanup')
            shutil.rmtree(home)
            if home.exists():
                raise AssertionError('Trial workspace not cleaned')
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
    result=dict(status=('real_smoke_one_per_mode_NOT_AB_gate' if real and smoke
                  else 'real_standalone_NOT_app_cache_gate' if real
                  else 'synthetic_smoke_NOT_AB_gate' if smoke
                  else 'synthetic_combined_only_not_app_cache_gate'),
        platform=sys.platform,order=list(order),
        trials_per_mode=1 if smoke else 3,
        source_count=3,sources=source_evidence,
        code_sha=subprocess.check_output(['git',
            'rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        byte_parity=True,unchanged_ready_singles=True,
        raw_payload_saved=False,workspace_cleaned=True,
        combined_manifest_sha256=output_digest,trials=trials,
        summary=summary)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',
                   encoding='utf8')
    print('COMBINED_ONLY_SUMMARY',json.dumps(summary),flush=True)
    print('COMBINED_ONLY_WORKSPACE_CLEANED',True,flush=True)


if __name__=='__main__':
    main()
