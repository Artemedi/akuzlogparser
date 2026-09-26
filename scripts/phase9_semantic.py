"""Phase 9 test-only semantic fingerprints; NEVER serialize real analytics payloads.

Normalize only report-generated IDs, the indexed catalog mtime_ns, and
SQL's unspecified tie order in error-detail items. Preserve catalog size,
provenance proof, event IDs, error contents, dates, FP and counts.
"""
from __future__ import annotations
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from akuz_analytics import read_js
from akuz_store import load_store
from scripts.bench_phase9_baseline import canonical_hash, sql_fingerprint


def _report_aliases(root: Path):
    entries = load_store(root)["reports"].values()
    return {row["id"]: row["key"] for row in entries}


def semantic_sql(root: Path) -> dict[str, str]:
    """Override ONLY the indexed.stamp volatile mtime in exact SQL digests."""
    aliases = _report_aliases(root)
    old = sql_fingerprint(root)
    path = root / "cache" / "error_analytics.sqlite"
    with closing(sqlite3.connect(path)) as db:
        cols = [x[1] for x in db.execute("PRAGMA table_info(indexed)")]
        if cols != ["id", "stamp"]:
            raise AssertionError("Unexpected indexed schema; cannot normalize")
        rows = []
        for rid, stamp in db.execute("SELECT id,stamp FROM indexed"):
            if rid not in aliases:
                raise AssertionError("Unknown report id in indexed")
            fields = stamp.split(":")
            if (len(fields) != 3 or not fields[0].isdigit()
                or not fields[1].isdigit()
                or not re.fullmatch(r"[0-9a-f]{64}", fields[2])):
                raise AssertionError("Unrecognized catalog stamp layout")
            rows.append([aliases[rid], fields[0], fields[2]])
    old["indexed"] = canonical_hash(sorted(rows))
    return old


def semantic_exports(root: Path) -> dict[str, str]:
    """Compare full parsed export contents except ephemeral report IDs.

    Rows with equal date+clock use SQL's report-id tie order, which has
    no invariant across separately generated report UUIDs. Their complete
    normalized item dictionaries are sorted; no fields are dropped.
    """
    aliases = _report_aliases(root)
    folder = root / "data"
    paths = list(folder.glob("*.js"))
    if not paths or not (folder / "analytics.js").is_file():
        raise AssertionError("Missing analytics exports")
    output = {}
    for file in paths:
        prefix = ("window.AKUZ_ANALYTICS=" if file.name == "analytics.js"
                  else "window.AKUZ_ERROR_DETAIL=")
        if (file.name != "analytics.js"
            and not re.fullmatch(r"(error|type|family)_[a-f0-9]{24}\.js",
                                 file.name)):
            raise AssertionError("Unknown analytics export shape")
        payload = read_js(file, prefix)
        if file.name != "analytics.js":
            if not isinstance(payload.get("items"), list):
                raise AssertionError("Unexpected detail items schema")
            for item in payload["items"]:
                rid = item.get("report_id")
                if rid not in aliases:
                    raise AssertionError("Unknown report id in export")
                item["report_id"] = aliases[rid]
            payload["items"] = sorted(payload["items"], key=lambda item:
                 json.dumps(item, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")))
        output[file.name] = canonical_hash(payload)
    return output
