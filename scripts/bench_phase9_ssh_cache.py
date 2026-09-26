"""Phase 9: isolated real SSH cache matrix, without persisting log payloads."""
from __future__ import annotations
import gc
import json
from pathlib import Path
import shutil
import tempfile
from dataclasses import replace
from time import perf_counter, process_time
from unittest.mock import patch
import akuz_app
from akuz_app import State, perform_list, perform_build_current
from akuz_fetch import load_config
from akuz_store import load_store, save_store
from scripts.bench_phase9_baseline import canonical_hash, inventory_manifest, last_trace
from scripts.bench_phase9_ssh import ROOT, DAYS, DIAG, MARKER, build_once, scrub_stale_temp
from scripts.phase9_semantic import semantic_sql, semantic_exports

def signature(root: Path) -> dict:
    inv = inventory_manifest(root)
    return {
        "inventory": canonical_hash(inv),
        "reports": {key: canonical_hash(row["files"])
                    for key, row in inv["reports"].items()},
        "sql": semantic_sql(root),
        "exports": semantic_exports(root),
    }


def remove_known_report(root: Path, kind: str):
    """Refuse deletion unless parent is a disposable benchmark root."""
    marker = root.parent / MARKER
    if not marker.is_file() or marker.read_text(encoding="ascii") != "disposable\n":
        raise RuntimeError("Not a disposable benchmark workspace")
    store = load_store(root)
    report = next(row for row in store["reports"].values()
                  if row["kind"] == kind)
    folder = (root / "reports" / report["id"]).resolve()
    if folder.parent != (root / "reports").resolve() or not folder.is_dir():
        raise RuntimeError("Unsafe report path")
    shutil.rmtree(folder)
    del store["reports"][report["id"]]
    save_store(root, store)

def rerun(root: Path, expected: dict, label: str):
    cfg = replace(load_config(ROOT / "ConnectConf.cfg", ROOT),
                  local_dest=root / "downloads")
    state = State()
    with patch.object(akuz_app, "source_config", return_value=cfg):
        perform_list(root, state, source="linux")
        selected=[]
        for day in DAYS:
            matching=[r for r in state.listing
                      if r["name"] == day + "_server.log"]
            if len(matching) != 1:
                raise AssertionError("SSH source missing: " + day)
            selected.append({"id":matching[0]["id"],
                             "date":f"{day[:4]}-{day[4:6]}-{day[6:]}"})
        offset=len(last_trace(root, 0))
        begin, cpu=perf_counter(), process_time()
        perform_build_current(root, state, selected)
        wall, processor=perf_counter()-begin, process_time()-cpu
    if state.result["analytics_warning"]:
        raise AssertionError("Analytics warning: " + label)
    actual=signature(root)
    checks={key:actual[key] == expected[key] for key in expected}
    if not all(checks.values()):
        raise AssertionError("Cache semantic mismatch: " + label + repr(checks))
    trace=last_trace(root, offset)
    builds=[x for x in trace if x.startswith("build.summary status=done")]
    parses=[x for x in trace if x.startswith("generate.parse status=done")]
    spools=[x for x in trace if x.startswith("derived.spool status=summary")]
    if len(builds) != 1:
        raise AssertionError("Expected one build summary")
    return {"case":label,"wall_s":round(wall,3),
            "cpu_s":round(processor,3),"checks":checks,
            "build":builds[0],"parsed_reports":len(parses),
            "spooled_sources":len(spools)}


def main():
    scrub_stale_temp()
    with tempfile.TemporaryDirectory(prefix="phase9_ssh_",dir=DIAG) as path:
        home=Path(path)
        (home/MARKER).write_text("disposable\n",encoding="ascii")
        root=home/"workspace"
        reference=build_once(root, use_derived_spool=True)
        original=signature(root)
        rows=[]
        remove_known_report(root,"combined")
        rows.append(rerun(root,original,"combined-only-miss"))
        remove_known_report(root,"single")
        rows.append(rerun(root,original,"single-only-miss"))
        remove_known_report(root,"single")
        remove_known_report(root,"combined")
        rows.append(rerun(root,original,"mixed-single-combined-miss"))
        rows.append(rerun(root,original,"final-warm-no-op"))
        gc.collect()
    result={"reference_sources":reference["fresh"]["snapshot"],
            "fresh_wall_s":reference["fresh"]["wall_s"],
            "cases":rows,"workspace_cleaned":True,
            "payload_saved":False}
    target=DIAG/"phase9_ssh_cache_matrix_private.json"
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),
                      encoding="utf8")
    print("REAL_SSH_CACHE_MATRIX_PASS")
    print("FRESH",result["fresh_wall_s"])
    for row in rows:
        print("CASE",row)
    print("WORKSPACE_CLEANED",True)


if __name__ == "__main__":
    main()
