"""Disposable same-SHA Windows fresh-all comparison; NOT application integration.

Three real AKUZ local .log snapshots, no SSH or normal cache/inventory.
Child per mode measures singles + combined together. Original sources read-only;
hardlinks and outputs live exclusively in an owned TemporaryDirectory.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from akuz_store import sha256
from scripts.bench_phase9_architecture_synthetic import trial
from scripts.bench_phase94_combined_only import link_real_sources, REAL_DAYS

ORDER=('control','tee','b_lite',
       'b_lite','control','tee',
       'tee','b_lite','control')
SMOKE_ORDER=ORDER[:3]


def source_guard(folder, originals, expected):
    for item in expected:
        day=item['date']
        linked=folder/(day+'_server.log')
        original=originals[day]
        if (linked.stat().st_size!=item['bytes']
                or not os.path.samefile(original,linked)
                or sha256(linked)!=item['sha256']
                or sha256(original)!=item['sha256']):
            raise AssertionError('Original or linked AKUZ snapshot changed')


def summarize(trials):
    metrics=('wall_s','cpu_s','report_bytes','sidecar_bytes',
             'peak_ws_bytes','sampled_private_peak_bytes')
    result={}
    for mode in ('control','tee','b_lite'):
        rows=[x for x in trials if x['mode']==mode]
        result[mode]={key:dict(
            min=min(x[key] for x in rows),
            median=median(x[key] for x in rows),
            max=max(x[key] for x in rows))
            for key in metrics}
        result[mode]['min_memory_samples']=min(
            x['memory_samples'] for x in rows)
        result[mode]['unreadable_memory_samples']=sum(
            x['unreadable_memory_samples'] for x in rows)
    return result


def main(args):
    if len(args)==4 and args[0]=='--child':
        mode,source,home=args[1],Path(args[2]),Path(args[3])
        if mode not in ('control','tee','b_lite'):
            raise ValueError('Unknown fresh-all candidate')
        home.mkdir(parents=True,exist_ok=False)
        print('TRIAL_JSON='+json.dumps(trial(mode,source,home),
            ensure_ascii=False,separators=(',',':')),flush=True)
        return
    smoke='--smoke' in args
    rest=[x for x in args if x!='--smoke']
    if (args.count('--smoke')>1 or len(rest)!=2
            or rest[0]!='--real-sources' or sys.platform!='win32'):
        raise SystemExit('Windows: --real-sources LOCAL_DOWNLOAD_DIR [--smoke]')
    if subprocess.check_output(['git','status','--porcelain'],
            cwd=ROOT,text=True).strip():
        raise RuntimeError('Benchmark requires committed, clean Git checkout')
    sha=subprocess.check_output(['git','rev-parse','HEAD'],
                                cwd=ROOT,text=True).strip()
    diag=ROOT/'diagnostics'
    diag.mkdir(exist_ok=True)
    out=diag/('private_phase94_fresh_all_real_win32_'+
              ('smoke_' if smoke else '')+'v1.json')
    if out.exists():
        raise FileExistsError('Refuse overwriting earlier private evidence')
    order=SMOKE_ORDER if smoke else ORDER
    records=[]
    with TemporaryDirectory(prefix='phase94_fresh_all_',dir=diag) as td:
        root=Path(td)
        source=root/'sources'
        evidence,originals=link_real_sources(Path(rest[1]),source)
        if [x['date'] for x in evidence]!=list(REAL_DAYS):
            raise AssertionError('Incorrect fixed dated source selection')
        source_guard(source,originals,evidence)
        reference=None
        for i,mode in enumerate(order,1):
            source_guard(source,originals,evidence)
            home=root/('trial_'+str(i)+'_'+mode)
            worker=subprocess.run([sys.executable,'-B',str(Path(__file__)),
                     '--child',mode,str(source),str(home)],
                cwd=ROOT,text=True,capture_output=True,check=True,
                timeout=1200)
            line=next((x for x in worker.stdout.splitlines()
                       if x.startswith('TRIAL_JSON=')),None)
            if line is None:
                raise AssertionError('Missing worker result')
            record=json.loads(line.partition('=')[2])
            if reference is None:
                reference=record['reports']
            elif record['reports']!=reference:
                raise AssertionError('Individual/combined deterministic mismatch')
            source_guard(source,originals,evidence)
            if (record['unreadable_memory_samples']!=0
                    or record['memory_samples']<20
                    or record['peak_ws_bytes'] is None
                    or record['sampled_private_peak_bytes'] is None):
                raise AssertionError('Incomplete Windows memory gate')
            del record['reports']
            records.append(dict(number=i,**record,byte_parity=True))
            print('FRESH_ALL_REAL_PASS',i,mode,record['wall_s'],
                  record['cpu_s'],record['peak_ws_bytes'],
                  record['sidecar_bytes'],flush=True)
            if home.parent!=root or home.is_symlink():
                raise AssertionError('Refuse unowned trial cleanup')
            shutil.rmtree(home)
            if home.exists():
                raise AssertionError('Trial workspace cleanup failed')
        digest=__import__('hashlib').sha256(json.dumps(
            reference,sort_keys=True).encode('utf8')).hexdigest()
    record=dict(
        status=('real_fresh_all_smoke_NOT_AB_gate' if smoke
                else 'real_fresh_all_standalone_NOT_app_cache_gate'),
        platform=sys.platform,code_sha=sha,source_count=3,
        sources=evidence,source_bytes=sum(x['bytes'] for x in evidence),
        order=list(order),trials_per_mode=1 if smoke else 3,
        output_manifest_sha256=digest,byte_parity=True,
        raw_payload_saved=False,workspace_cleaned=True,
        memory_definition='child OS peak WS; sampled Private lower bound',
        trials=records,summary=summarize(records))
    if subprocess.check_output(['git','rev-parse','HEAD'],
            cwd=ROOT,text=True).strip()!=sha:
        raise AssertionError('Git HEAD moved during experiment')
    if subprocess.check_output(['git','status','--porcelain'],
            cwd=ROOT,text=True).strip():
        raise AssertionError('Git working tree changed')
    out.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',
                   encoding='utf8')
    print('FRESH_ALL_REAL_SUMMARY',
          json.dumps(record['summary'],ensure_ascii=False),flush=True)
    print('FRESH_ALL_REAL_OWNED_WORKSPACE_CLEANED',True,flush=True)


if __name__=='__main__':
    main(sys.argv[1:])
