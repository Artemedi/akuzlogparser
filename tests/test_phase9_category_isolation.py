"""Phase 9.1: report-local category extension state."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_derived import DerivedEvent
from akuz_html_explorer import CAT, generate


def event():
    return dict(event_id=1, day_offset=0, time="12:00:00.000",
                component="AKUZ", request_id="", user="u",
                raw="12:00:00.000,AKUZ,,u: x", message="x",
                start_line=1, end_line=1)


def derived(category):
    return DerivedEvent(category, "pattern", None, None, 0, "x",
                        0.0, 0.0, 0.0, 0.0, 0.0)


def catalog(folder: Path):
    text = (folder / "data" / "catalog.js").read_text(encoding="utf-8")
    prefix = "window.AKUZ_DATA="
    return json.loads(text[len(prefix):].rstrip(";\n"))


class CategoryIsolationTests(unittest.TestCase):
    def test_extra_category_does_not_leak_to_next_report(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "synthetic.log"
            source.write_text("unused", encoding="utf-8")
            first = root / "first"
            second = root / "second"
            generate(source, first, None, 1000, 35,
                     event_source=iter([event()]),
                     derived_hook=lambda ev: derived("future-extra"))
            generate(source, second, None, 1000, 35,
                     event_source=iter([event()]),
                     derived_hook=lambda ev: derived(CAT[0]))
            first_catalog = catalog(first)
            second_catalog = catalog(second)
            self.assertEqual(first_catalog["categories"],
                             list(CAT) + ["future-extra"])
            self.assertEqual(second_catalog["categories"], list(CAT))
            self.assertEqual(first_catalog["rows"][0][4], len(CAT))
            self.assertEqual(second_catalog["rows"][0][4], 0)


if __name__ == "__main__":
    unittest.main()
