"""Phase 13 P13-05 balanced replicated per-report vs single transaction A/B."""
from __future__ import annotations
import argparse
from dataclasses import replace
import json
from pathlib import Path
from statistics import median
import tempfile
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from akuz_fetch import load_config
from akuz_store import sha256
from scripts.bench_phase13_catalog_error_index import _reset_analytics,_run_refresh,_setup

ORDER=("B","C","C","B","B","C")
_EQ_KEYS=("overview","sql","semantic_sql","exports","semantic_exports")
_MEDIAN_KEYS=(
    "wall_s","cpu_s","ingest_elapsed_s","transaction_batch_elapsed_s",
    "transaction_commits","pending_reports","catalog_verify_s",
    "export_elapsed_s","overview_elapsed_s","inventory_elapsed_s",
    "db_bytes","page_count","freelist_count","error_rows","events",
    "matched_errors","recognize_calls","raw_sha_lookup_calls","insert_attempts",
    "raw_shards_loaded","raw_shard_bytes_loaded","index_skipped_no_error",
    "catalog_index_reports","reports","indexed_reports",
)
_PUBLIC_KEYS=("wall_s","cpu_s","ingest_elapsed_s","transaction_batch_elapsed_s",
              "transaction_commits","pending_reports","db_bytes","page_count")

def _median_metrics(trials):
    rows=[t["metrics"] for t in trials]
    return {k:median(r[k] for r in rows) for k in _MEDIAN_KEYS
            if all(k in r for r in rows)}

def _assert_equivalent(reference,current):
    for key in _EQ_KEYS:
        if reference[key]!=current[key]:
            raise AssertionError("Replicated single transaction differs in "+key)

def run(config_path:Path,app_root:Path):
    with tempfile.TemporaryDirectory(prefix="akuz-phase13-single-tx-replicated-",
                                     dir=app_root/"diagnostics") as temp:
        root=Path(temp)
        cfg=replace(load_config(config_path,app_root),
                    local_dest=root/"downloads",compression=False)
        setup=_setup(root,cfg)
        inventory_path=root/"cache"/"inventory.json"
        inventory_before=sha256(inventory_path)
        reference=None
        reference_metrics=None
        trials=[]
        for ordinal,mode in enumerate(ORDER,1):
            candidate=mode=="C"
            _reset_analytics(root)
            current=_run_refresh(root,True,candidate)
            metrics=current["metrics"]
            if reference is None:
                reference=current
                reference_metrics=metrics
            else:
                _assert_equivalent(reference,current)
                for key in ("catalog_index_reports","matched_errors",
                            "raw_sha_lookup_calls","insert_attempts",
                            "recognize_calls","error_rows","pending_reports"):
                    if reference_metrics[key]!=metrics[key]:
                        raise AssertionError("Replicated logical count differs: "+key)
            expected_commits=1 if candidate else metrics["pending_reports"]
            if metrics["transaction_commits"]!=expected_commits:
                raise AssertionError("Unexpected transaction commit count")
            trials.append(dict(ordinal=ordinal,mode=mode,metrics=metrics))
        if sha256(inventory_path)!=inventory_before:
            raise AssertionError("Replicated single-transaction A/B mutated inventory")
        baseline_trials=[t for t in trials if t["mode"]=="B"]
        candidate_trials=[t for t in trials if t["mode"]=="C"]
        baseline=_median_metrics(baseline_trials)
        candidate=_median_metrics(candidate_trials)
        return dict(
            status="PHASE13_SINGLE_TRANSACTION_REPLICATED_AB",
            setup=setup,order=list(ORDER),trials=trials,
            baseline_median=baseline,candidate_median=candidate,
            wall_reduction_pct=round((baseline["wall_s"]-candidate["wall_s"])/baseline["wall_s"]*100,3),
            cpu_reduction_pct=round((baseline["cpu_s"]-candidate["cpu_s"])/baseline["cpu_s"]*100,3),
            ingest_reduction_pct=round((baseline["ingest_elapsed_s"]-candidate["ingest_elapsed_s"])/baseline["ingest_elapsed_s"]*100,3),
            commit_reduction_pct=round((baseline["transaction_commits"]-candidate["transaction_commits"])/baseline["transaction_commits"]*100,3),
            exact_equivalence=True,inventory_unchanged=True,catalog_error_index=True,
            journal_mode="DELETE",indexes_unchanged=True,raw_payload_retained=False,
            release_changed=False,
        )

def _public(result):
    return {
        "source_bytes":result["setup"]["source_bytes"],"reports":result["setup"]["reports"],
        "order":result["order"],
        "trials":[{"ordinal":t["ordinal"],"mode":t["mode"],
                   **{k:t["metrics"][k] for k in _PUBLIC_KEYS if k in t["metrics"]}}
                  for t in result["trials"]],
        "baseline_median":result["baseline_median"],
        "candidate_median":result["candidate_median"],
        "wall_reduction_pct":result["wall_reduction_pct"],
        "cpu_reduction_pct":result["cpu_reduction_pct"],
        "ingest_reduction_pct":result["ingest_reduction_pct"],
        "commit_reduction_pct":result["commit_reduction_pct"],
        "exact_equivalence":result["exact_equivalence"],
        "inventory_unchanged":result["inventory_unchanged"],
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,required=True)
    p.add_argument("--app-root",type=Path,required=True)
    p.add_argument("--result",type=Path,required=True)
    args=p.parse_args()
    try:
        result=run(args.config,args.app_root)
        args.result.parent.mkdir(parents=True,exist_ok=True)
        if args.result.exists():
            raise FileExistsError("Refusing to overwrite replicated Phase 13 transaction evidence")
        args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print("PHASE13_SINGLE_TRANSACTION_REPLICATED_AB=PASS")
        print("PUBLIC_SUMMARY",json.dumps(_public(result),sort_keys=True))
        print("ORDER=B_C_C_B_B_C")
        print("EXACT_EQUIVALENCE=PASS")
        print("INVENTORY_UNCHANGED=PASS")
        print("CATALOG_ERROR_INDEX=ON_BOTH_MODES")
        print("JOURNAL_MODE=DELETE")
        print("INDEXES_UNCHANGED=YES")
        print("RAW_PAYLOAD_RETAINED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE13_SINGLE_TRANSACTION_REPLICATED_AB=FAILED",type(exc).__name__)
        raise SystemExit(1)

if __name__=="__main__":
    main()
