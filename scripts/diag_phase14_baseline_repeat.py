"""Phase 14 diagnostic: prove repeated stdlib baselines are byte-deterministic."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akuz_fetch import load_config
from akuz_store import load_store
from scripts.bench_phase14_raw_orjson import (
    _setup_seed,
    _run_trial,
    _safe_parity_detail,
)


def run(config_path: Path, app_root: Path):
    with tempfile.TemporaryDirectory(
            prefix="akuz-phase14-baseline-repeat-",
            dir=app_root / "diagnostics") as temp:
        root = Path(temp)
        cfg = replace(
            load_config(config_path, app_root),
            local_dest=root / "downloads",
            compression=False,
        )
        state, selected, downloads_before, setup = _setup_seed(root, cfg)
        first = _run_trial(root, cfg, state, selected, "B")
        if load_store(root)["downloads"] != downloads_before:
            raise AssertionError("first baseline mutated cached snapshots")
        print("P14_BASELINE_REPEAT_TRIAL", json.dumps(
            {"ordinal": 1, "metrics": first["metrics"]}, sort_keys=True))

        second = _run_trial(root, cfg, state, selected, "B")
        if load_store(root)["downloads"] != downloads_before:
            raise AssertionError("second baseline mutated cached snapshots")
        print("P14_BASELINE_REPEAT_TRIAL", json.dumps(
            {"ordinal": 2, "metrics": second["metrics"]}, sort_keys=True))

        for key in ("manifest", "semantic_sql", "exports", "semantic_exports"):
            if second[key] != first[key]:
                detail = _safe_parity_detail(key, first[key], second[key])
                print("P14_BASELINE_REPEAT_MISMATCH", detail)
                raise AssertionError("repeated stdlib baseline differs in " + key)

        return {
            "status": "PHASE14_BASELINE_REPEAT",
            "setup": setup,
            "trials": [first["metrics"], second["metrics"]],
            "exact_equivalence": True,
            "analytics_equivalence": True,
            "downloads_unchanged": True,
            "raw_payload_retained": False,
            "release_changed": False,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.config, args.app_root)
        print("PHASE14_BASELINE_REPEAT=PASS")
        print("PUBLIC_SUMMARY", json.dumps({
            "source_bytes": result["setup"]["source_bytes"],
            "reports": result["setup"]["reports"],
            "trials": result["trials"],
            "exact_equivalence": result["exact_equivalence"],
            "analytics_equivalence": result["analytics_equivalence"],
            "downloads_unchanged": result["downloads_unchanged"],
        }, sort_keys=True))
        print("RAW_PAYLOAD_RETAINED=NO")
        print("RELEASE_CHANGED=NO")
    except BaseException as exc:
        print("PHASE14_BASELINE_REPEAT=FAILED", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
