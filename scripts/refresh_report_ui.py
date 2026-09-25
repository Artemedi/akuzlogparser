#!/usr/bin/env python3
"""Refresh UI assets of existing v4 reports without reading or rebuilding log data."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile

ASSETS = ("index.html", "index.js", "app_controls.js", "style.css")
SOURCE = Path(__file__).resolve().parents[1]


def candidates(root: Path):
    library = root / "reports"
    if not library.is_dir():
        raise ValueError(f"Каталог отчётов не найден: {library}")
    for report in sorted(library.iterdir()):
        if (report.is_dir() and not report.is_symlink()
                and report.name.startswith("v4_")
                and (report / "index.html").is_file()
                and (report / "index.js").is_file()
                and (report / "data" / "catalog.js").is_file()):
            yield report
def refresh(root: Path, *, apply: bool = False) -> int:
    for name in ASSETS:
        if not (SOURCE / name).is_file():
            raise ValueError(f"Исходный ресурс отсутствует: {SOURCE / name}")
    selected = list(candidates(root))
    for report in selected:
        if apply:
            for name in ASSETS:
                tmp = None
                try:
                    with tempfile.NamedTemporaryFile(dir=report, prefix=".akuz-ui-",
                                                     delete=False) as target:
                        tmp = Path(target.name)
                        with (SOURCE / name).open("rb") as source:
                            shutil.copyfileobj(source, target)
                    os.replace(tmp, report / name)
                finally:
                    if tmp is not None:
                        tmp.unlink(missing_ok=True)
        print(("Обновлено: " if apply else "Будет обновлено: ") + report.name)
    print(f"Отчётов: {len(selected)}. Каталоги data/, исходные .log и кэш не затронуты.")
    return len(selected)
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path,
                        help="Каталог работающего Explorer, содержащий папку reports/")
    parser.add_argument("--apply", action="store_true",
                        help="Обновить HTML/JS/CSS; без флага выполняется только предпросмотр")
    args = parser.parse_args()
    try:
        refresh(args.root.expanduser().resolve(), apply=args.apply)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
