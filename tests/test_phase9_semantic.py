"""Real-data-safe Phase 9 analytics semantic comparison, synthetic fixture."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest

from akuz_app import State, perform_build_current, perform_list
from akuz_store import load_store
from scripts.bench_phase9_baseline import create_sources, inventory_manifest
from scripts.phase9_semantic import semantic_sql, semantic_exports


def build(root, sources, optimized):
    state = State()
    perform_list(root, state, source="local", local_path=str(sources))
    selected = [dict(id=row["id"], date="") for row in state.listing]
    perform_build_current(root, state, selected, use_derived_spool=optimized)
    if state.result["analytics_warning"]:
        raise AssertionError("Synthetic analytics failed")


class SemanticAnalyticsTests(unittest.TestCase):
    def test_normalizes_only_ephemeral_values_and_preserves_full_items(self):
        with TemporaryDirectory() as td:
            base = Path(td)
            sources = base / "sources"
            create_sources(sources, 8, 256)
            normal, spool = base / "normal", base / "spool"
            build(normal, sources, False)
            build(spool, sources, True)
            self.assertEqual(inventory_manifest(normal),
                             inventory_manifest(spool))
            original_sql = semantic_sql(normal)
            original_js = semantic_exports(normal)
            self.assertGreater(len(original_js), 1)
            self.assertEqual(original_sql, semantic_sql(spool))
            self.assertEqual(original_js, semantic_exports(spool))
            self.assertEqual(set(original_sql),
                             {"errors", "indexed", "meta",
                              "source_dates", "source_files"})
            # Never suppress an actual error payload change.
            name = next(p for p in (spool / "data").glob("error_*.js"))
            text = name.read_text(encoding="utf8")
            self.assertIn("window.AKUZ_ERROR_DETAIL=", text)
            obj = json.loads(text.removeprefix(
                "window.AKUZ_ERROR_DETAIL=").removesuffix(";\n"))
            obj["items"][0]["clock"] = "XX:XX:XX"
            name.write_text("window.AKUZ_ERROR_DETAIL="+json.dumps(obj)
                            +";\n",encoding="utf8")
            self.assertNotEqual(original_js, semantic_exports(spool))
            # Only catalog stamp's nanosecond field can vary.
            db = sqlite3.connect(spool/"cache/error_analytics.sqlite")
            try:
                rid,stamp=db.execute("SELECT id,stamp FROM indexed LIMIT 1").fetchone()
                size,nano,proof=stamp.split(":")
                db.execute("UPDATE indexed SET stamp=? WHERE id=?",
                           (":".join((size,str(int(nano)+10),proof)),rid))
                db.commit()
                self.assertEqual(original_sql, semantic_sql(spool))
                db.execute("UPDATE indexed SET stamp=? WHERE id=?",
                           (":".join((str(int(size)+1),nano,proof)),rid))
                db.commit()
                self.assertNotEqual(original_sql, semantic_sql(spool))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
