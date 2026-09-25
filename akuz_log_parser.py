#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Streaming parser for AKUZ logs; full JSONL archive + navigable Markdown.

Standard library only, Python 3.9+. The report is an investigation aid, not an
assertion that text-matched messages are actual application log levels.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterator

HEADER = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2}\.\d{3}),(?P<component>[^,]*),(?P<request_id>[^,]*),(?P<tail>.*)$"
)
# Windows TraceLogger: user 'login' HH:mm:ss.fff: message; a TraceListener
# may prepend "Category: ". No request/session GUID is inferred.
WINDOWS_HEADER = re.compile(
    r"^(?:(?P<component>[^:\r\n]{1,100}): )?user '(?P<user>.*?)' "
    r"(?P<time>\d{2}:\d{2}:\d{2}\.\d{3}): (?P<message>.*)$"
)
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
HEX = re.compile(r"\b0x[0-9a-f]{8,}\b", re.I)
LONG_HEX = re.compile(r"\b[0-9a-f]{24,}\b", re.I)
URL = re.compile(r"https?://[^\s\"'<>]+", re.I)
NUM = re.compile(r"(?<![\w.])\d{4,}(?![\w.])")
TIMEVALUE = re.compile(r"\b\d\d:\d\d:\d\d\.\d+\b")
SPACE = re.compile(r"\s+")
DURATION = re.compile(r"\bобщее\s+время\s*:\s*(\d{1,3}:\d{2}:\d{2}\.\d+)\b", re.I)
CACHE_DURATION = re.compile(r"\bза\s+(\d+(?:\.\d+)?)\s*ms\b", re.I)
EXCEPTION = re.compile(r"\b(?:exception|error|failed|failure|fatal|traceback)\b|ошибк", re.I)
TIMEOUT = re.compile(r"\btimeout\b|timed?\s*out|тайм.?аут|истекло\s+время\s+ожидания", re.I)
NOTFOUND = re.compile(r"не\s+найден[аоы]?|not\s+found|no_data_found", re.I)
REJECTED = re.compile(r"\bnack\b|\brejected\b|\brefused\b|отклонен|отклонён|отказано|отказ в доступе", re.I)
BACKTICKS = re.compile(r"`+")


def is_header(line: str) -> re.Match[str] | None:
    match = HEADER.match(line)
    if not match:
        return None
    hh, mm, ss = [int(v) for v in match.group("time")[:8].split(":")]
    return match if hh < 24 and mm < 60 and ss < 60 else None


def clock_ms(time: str) -> int:
    hh, mm, rest = time.split(":")
    ss, millis = rest.split(".")
    return ((int(hh) * 60 + int(mm)) * 60 + int(ss)) * 1000 + int(millis)


def _find_word(hay: str, needle: str) -> bool:
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return False
        j = i + len(needle)
        before = hay[i - 1] if i > 0 else ""
        after = hay[j] if j < len(hay) else ""
        if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
            return True
        start = i + 1


def _skip_ws(hay: str, j: int) -> int:
    while j < len(hay) and hay[j].isspace():
        j += 1
    return j


def _rskip_ws(hay: str, k: int) -> int:
    while k >= 0 and hay[k].isspace():
        k -= 1
    return k


def _timeout_hit(low: str) -> bool:
    if _find_word(low, "timeout"):
        return True
    for needle in ("timed", "time"):
        start = 0
        while True:
            i = low.find(needle, start)
            if i < 0:
                break
            if low.startswith("out", _skip_ws(low, i + len(needle))):
                return True
            start = i + 1
    i = low.find("тайм")
    while i >= 0:
        j = i + 4
        if low.startswith("аут", j):
            return True
        if j < len(low) and low[j] != "\n" and low.startswith("аут", j + 1):
            return True
        i = low.find("тайм", i + 1)
    i = low.find("истекло")
    while i >= 0:
        j = _skip_ws(low, i + 7)
        if j > i + 7 and low.startswith("время", j):
            k = _skip_ws(low, j + 5)
            if k > j + 5 and low.startswith("ожидания", k):
                return True
        i = low.find("истекло", i + 1)
    return False


def _exception_hit(low: str) -> bool:
    if "ошибк" in low:
        return True
    return any(_find_word(low, w) for w in
               ("exception", "error", "failed", "failure", "fatal", "traceback"))


def _rejected_hit(low: str) -> bool:
    if "отклонен" in low or "отклонён" in low or "отказано" in low or "отказ в доступе" in low:
        return True
    return _find_word(low, "nack") or _find_word(low, "rejected") or _find_word(low, "refused")


def _notfound_hit(low: str) -> bool:
    if "no_data_found" in low:
        return True
    start = 0
    while True:
        i = low.find("not", start)
        if i < 0:
            break
        j = _skip_ws(low, i + 3)
        if j > i + 3 and low.startswith("found", j):
            return True
        start = i + 1
    start = 0
    while True:
        i = low.find("найден", start)
        if i < 0:
            return False
        k = _rskip_ws(low, i - 1)
        if k < i - 1 and k >= 1 and low.startswith("не", k - 1):
            return True
        start = i + 1


def _classify_regex(message: str) -> str:
    """Preserve the original rules for strings with length-changing Unicode folds."""
    if TIMEOUT.search(message):
        return "таймаут"
    if EXCEPTION.search(message):
        return "ошибка/исключение"
    if REJECTED.search(message):
        return "отказ/NACK"
    if NOTFOUND.search(message):
        return "не найдено"
    return "прочее"


def classify(message: str, *, diagnostics: Counter[str] | None = None) -> str:
    # Preserve category priority, not the textual order of matching markers.
    # Fast find-based matching for ordinary AKUZ strings; fall back to exact
    # regex semantics for folds like ß -> ss that change character positions.
    lowered = message.casefold().replace("ı", "i").replace("i\u0307", "i")
    # U+0345 casefolds from a non-word combining mark into a word letter,
    # changing Unicode regex word boundaries without changing string length.
    if diagnostics is not None:
        diagnostics["classify_calls"] += 1
    if len(lowered) != len(message) or "\u0345" in message:
        if diagnostics is not None:
            diagnostics["classify_regex_fallback_events"] += 1
        return _classify_regex(message)
    if ("time" in lowered or "тайм" in lowered or "истекло" in lowered) and _timeout_hit(lowered):
        return "таймаут"
    if any(token in lowered for token in ("exception", "error", "failed", "failure", "fatal", "traceback", "ошибк")) and _exception_hit(lowered):
        return "ошибка/исключение"
    if any(token in lowered for token in ("nack", "rejected", "refused", "отклонен", "отклонён", "отказ")) and _rejected_hit(lowered):
        return "отказ/NACK"
    if ("найден" in lowered or "found" in lowered) and _notfound_hit(lowered):
        return "не найдено"
    return "прочее"


def normalize(message: str) -> str:
    newline = message.find("\n", 0, 700)
    first = message[:700 if newline < 0 else newline]
    first = UUID.sub("{UUID}", first)
    first = HEX.sub("{HEX}", first)
    first = LONG_HEX.sub("{HEX}", first)
    first = URL.sub("{URL}", first)
    first = TIMEVALUE.sub("{TIME}", first)
    first = NUM.sub("{N}", first)
    return SPACE.sub(" ", first).strip()[:330] or "(пустое сообщение)"


def to_ms(raw: str) -> float:
    hh, mm, rem = raw.split(":")
    return ((int(hh) * 60 + int(mm)) * 60 + float(rem)) * 1000


def extract_duration(message: str) -> tuple[float, str] | None:
    # Preserve precedence: 'общее время' wins even when 'за N ms' comes first.
    lowered = message.casefold()
    if "общее" in lowered and "время" in lowered:
        match = DURATION.search(message)
        if match:
            return to_ms(match.group(1)), "общее время"
    if "за" in lowered and "ms" in lowered:
        match = CACHE_DURATION.search(message)
        if match:
            return float(match.group(1)), "кэш"
    return None


def md_cell(value: object, width: int = 115) -> str:
    out = str(value).replace("\r", " ").replace("\n", " ").replace("\x00", "\\0")
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = out.replace("|", "\\|").replace("`", "ˋ")
    return out[:width] + ("…" if len(out) > width else "")


def markdown_code(raw: str) -> str:
    longest = max((len(m.group()) for m in BACKTICKS.finditer(raw)), default=0)
    fence = "`" * max(4, longest + 1)
    return f"{fence}text\n{raw.replace(chr(0), '[NUL]')}\n{fence}\n"


def render_time(day: int, time: str, base: date | None) -> str:
    return f"{base + timedelta(days=day)} {time}" if base else f"D+{day} {time}"


def event_stream(source: Path, stats: Counter[str]) -> Iterator[dict[str, Any]]:
    """Yield one event per timestamped header, preserving continuation lines."""
    current: dict[str, Any] | None = None
    raw_lines: list[str] = []
    message_lines: list[str] = []
    prev_ms: int | None = None
    day = 0
    event_id = 0
    with source.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            stats["physical_lines"] += 1
            stats["replacement_chars"] += line.count("\ufffd")
            clean = line.rstrip("\r\n")
            match = is_header(clean)
            windows = WINDOWS_HEADER.match(clean) if match is None else None
            if windows is not None:
                hh, mm, ss = map(int, windows.group("time")[:8].split(":"))
                if hh >= 24 or mm >= 60 or ss >= 60:
                    windows = None
            if match or windows:
                if current is not None:
                    current["raw"] = "\n".join(raw_lines)
                    if len(message_lines) > 1:
                        current["message"] = "\n".join(message_lines)
                    current["end_line"] = line_number - 1
                    yield current
                event_id += 1
                event_time = (match or windows).group("time")
                ms = clock_ms(event_time)
                if prev_ms is not None and ms < prev_ms:
                    if prev_ms - ms > 12 * 60 * 60 * 1000:
                        day += 1
                        stats["midnight_rollovers"] += 1
                    else:
                        stats["out_of_order_timestamps"] += 1
                prev_ms = ms
                if windows:
                    user = windows.group("user")
                    message = windows.group("message")
                    component = windows.group("component") or "Windows Trace"
                    request_id = ""
                    stats["windows_trace_events"] += 1
                else:
                    tail = match.group("tail")
                    user, separator, message = tail.partition(": ")
                    if not separator:
                        # Keep original tail; no fabricated user if schema is unusual.
                        user, message = "", tail
                        stats["missing_user_separator"] += 1
                    component = match.group("component")
                    request_id = match.group("request_id")
                current = {
                    "event_id": event_id,
                    "day_offset": day,
                    "time": event_time,
                    "component": component,
                    "request_id": request_id,
                    "user": user,
                    "message": message,
                    "start_line": line_number,
                }
                raw_lines = [clean]
                message_lines = [message]
            else:
                stats["continuation_lines"] += 1
                if current is None:
                    event_id += 1
                    current = {
                        "event_id": event_id, "day_offset": 0, "time": "",
                        "component": "(до первой записи)", "request_id": "",
                        "user": "", "message": clean, "start_line": line_number,
                    }
                    raw_lines = [clean]
                    message_lines = [clean]
                    stats["preamble_events"] += 1
                else:
                    raw_lines.append(clean)
                    message_lines.append(clean)
        if current is not None:
            current["raw"] = "\n".join(raw_lines)
            if len(message_lines) > 1:
                current["message"] = "\n".join(message_lines)
            current["end_line"] = stats["physical_lines"]
            yield current


class PageWriter:
    def __init__(self, out: Path, size: int, md_limit: int, base: date | None):
        self.root = out / "events"
        self.root.mkdir(parents=True, exist_ok=True)
        self.size = size
        self.md_limit = md_limit
        self.base = base
        self.key: tuple[int, str] | None = None
        self.page_num = 0
        self.count = 0
        self.handle: Any = None
        self.relative = ""
        self.pages: list[dict[str, Any]] = []

    def write(self, ev: dict[str, Any]) -> str:
        key = (ev["day_offset"], ev["time"][:2] if ev["time"] else "unknown")
        if key != self.key:
            self.page_num = 0
            self.key = key
        if self.handle is None or self.count >= self.size or self.pages[-1]["key"] != key:
            self._new_page(key)
        self.count += 1
        self.pages[-1]["count"] += 1
        anchor = f"event-{ev['event_id']}"
        location = f"{self.relative}#{anchor}"
        category = md_cell(ev["category"])
        label = render_time(ev["day_offset"], ev["time"], self.base) if ev["time"] else "до первой метки времени"
        self.handle.write(f"\n<a id=\"{anchor}\"></a>\n")
        self.handle.write(f"### #{ev['event_id']} · {md_cell(label)} · {md_cell(ev['component'], 70)}\n\n")
        self.handle.write(
            f"**Признак:** {category} · **Строки:** {ev['start_line']}–{ev['end_line']} "
            f"· **Request ID:** `{md_cell(ev['request_id'], 100)}` "
            f"· **Пользователь:** `{md_cell(ev['user'], 85)}`\n\n"
        )
        raw = ev["raw"]
        if self.md_limit and len(raw) > self.md_limit:
            raw = raw[: self.md_limit]
            self.handle.write(
                f"> Отображены первые {self.md_limit:,} символов из {len(ev['raw']):,}. "
                "Полная запись есть в `events.jsonl.gz` (или `events.jsonl`).\n\n"
            )
        self.handle.write("<details>\n<summary>Раскрыть исходную запись</summary>\n\n")
        self.handle.write(markdown_code(raw))
        self.handle.write("\n</details>\n")
        return location

    def _new_page(self, key: tuple[int, str]) -> None:
        self.close()
        self.key = key
        self.page_num += 1
        name = f"day{key[0]:02d}_{key[1]}_p{self.page_num:03d}.md"
        self.relative = f"events/{name}"
        self.handle = (self.root / name).open("w", encoding="utf-8", newline="\n")
        self.count = 0
        self.pages.append({"key": key, "path": self.relative, "count": 0})
        self.handle.write(
            f"# AKUZ · День {key[0]} · час {key[1]} · страница {self.page_num}\n\n"
            "[← К сводному отчёту](../report.md) · "
            "Полные события, отсортированные в порядке исходного файла.\n\n"
        )

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def save_report(
    dest: Path, source: Path, base: date | None, stats: Counter[str],
    categories: Counter[str], components: Counter[str], hours: Counter[tuple[int, str]],
    hourly_categories: dict[tuple[int, str], Counter[str]],
    patterns: Counter[tuple[str, str, str]],
    pattern_first: dict[tuple[str, str, str], tuple[str, str, str]],
    users: Counter[str], request_counts: Counter[str],
    durations: list[tuple[float, str, str, str, str]],
    pages: list[dict[str, Any]], examples: dict[str, list[tuple[str, str]]],
    top: int, archive_name: str,
) -> None:
    with (dest / "report.md").open("w", encoding="utf-8", newline="\n") as out:
        def p(line: str = "") -> None:
            out.write(line + "\n")

        p("# AKUZ · Разбор журнала событий")
        p()
        p(f"> **Источник:** `{md_cell(source.name, 140)}` · **Событий:** {stats['events']:,} "
          f"· **Строк:** {stats['physical_lines']:,} · **Продолжений:** {stats['continuation_lines']:,}")
        p()
        p("**Как читать:** этот отчёт — индекс полного разбора. Каждая строка журнала "
          "отнесена к событию. Детали разбиты на страницы и раскрываются по клику. "
          f"Архив всех событий: [`{archive_name}`]({archive_name})" if archive_name else
          "**Как читать:** каждая строка журнала отнесена к событию; детали разбиты на страницы.")
        p()
        p("**Важно:** исходный формат НЕ содержит уровня INFO/WARN/ERROR. Все категории ниже — "
          "текстовые признаки, а не подтверждённая серьёзность или первопричина.")
        p("**Дата:** " + (f"задана оператором как `{base}`; переход через полночь учитывается" if base
                         else "в журнале отсутствует; `D+0` — первый день, `D+1` — следующий и т. д."))
        p(f"**Кодировка:** чтение UTF-8 с заменой повреждённых символов; символов U+FFFD: "
          f"{stats['replacement_chars']:,}. Для побайтовой точности храните оригинальный `.log`.")
        p()
        p("## Навигация")
        p()
        p("- [Частота по часам](#частота-по-часам)")
        p("- [Текстовые признаки](#текстовые-признаки)")
        p("- [Повторяющиеся сообщения](#повторяющиеся-сообщения)")
        p("- [Компоненты](#компоненты)")
        p("- [Времена выполнения](#времена-выполнения)")
        p("- [Полный журнал по страницам](#полный-журнал-по-страницам)")
        p()
        p("## Контроль целостности разбора")
        p()
        p("| Показатель | Значение |")
        p("|---|---:|")
        for name, value in (
            ("Физических строк", stats["physical_lines"]),
            ("Распознанных событий", stats["events"]),
            ("Строк продолжения", stats["continuation_lines"]),
            ("Преамбул до первого заголовка", stats["preamble_events"]),
            ("Переходов через полночь (>12 ч назад)", stats["midnight_rollovers"]),
            ("Небольших нарушений хронологии", stats["out_of_order_timestamps"]),
            ("Нет разделителя `имя: сообщение`", stats["missing_user_separator"]),
            ("Замещённых символов кодировки", stats["replacement_chars"]),
            ("Страниц деталей", len(pages)),
            ("Уникальных компонентов (включая пустой)", len(components)),
        ):
            p(f"| {name} | {value:,} |")
        p()
        p("## Частота по часам")
        p()
        p("| День / час | События | Ошибка / исключение | Таймаут | Отказ / NACK | Не найдено |")
        p("|---|---:|---:|---:|---:|---:|")
        for key, cnt in sorted(hours.items()):
            c = hourly_categories[key]
            p(f"| D+{key[0]} {key[1]}:xx | {cnt:,} | {c['ошибка/исключение']:,} "
              f"| {c['таймаут']:,} | {c['отказ/NACK']:,} | {c['не найдено']:,} |")
        p()
        p("## Текстовые признаки")
        p()
        p("| Категория | Событий | Доля |")
        p("|---|---:|---:|")
        for cat, cnt in categories.most_common():
            p(f"| {md_cell(cat)} | {cnt:,} | {cnt / max(stats['events'], 1):.1%} |")
        p()
        for cat in ("ошибка/исключение", "таймаут", "отказ/NACK", "не найдено"):
            entries = examples.get(cat, [])
            if not entries:
                continue
            p(f"### Примеры: {cat}")
            p()
            for href, line in entries:
                p(f"- [{md_cell(line, 145)}]({href})")
            p()
        p("## Повторяющиеся сообщения")
        p()
        p("Сообщения сгруппированы по компоненту, категории и **первой строке**, "
          "с заменой UUID, длинных чисел, временных значений и URL. "
          "Параметры, различающиеся только в продолжениях, здесь могут объединяться.")
        p()
        p(f"### Наиболее частые ({min(top, len(patterns))})")
        p()
        p("| Количество | Компонент | Категория | Шаблон / первое вхождение |")
        p("|---:|---|---|---|")
        for key, cnt in patterns.most_common(top):
            href, first, last = pattern_first[key]
            p(f"| {cnt:,} | {md_cell(key[0], 48)} | {md_cell(key[1], 35)} "
              f"| [{md_cell(key[2], 140)}]({href}) · {md_cell(first, 20)}–{md_cell(last, 20)} |")
        p()
        p(f"### Частые признаки ошибок ({min(top, len(patterns))} максимум)")
        p()
        p("| Количество | Компонент | Признак | Шаблон / пример |")
        p("|---:|---|---|---|")
        n = 0
        for key, cnt in patterns.most_common():
            if key[1] not in ("ошибка/исключение", "таймаут", "отказ/NACK"):
                continue
            href, _, _ = pattern_first[key]
            p(f"| {cnt:,} | {md_cell(key[0], 45)} | {key[1]} | [{md_cell(key[2], 140)}]({href}) |")
            n += 1
            if n >= top:
                break
        p()
        p("## Компоненты")
        p()
        p("| Компонент | Событий | Доля |")
        p("|---|---:|---:|")
        for component, count in components.most_common():
            p(f"| {md_cell(component or '(пустой)', 75)} | {count:,} | {count / max(stats['events'],1):.1%} |")
        p()
        p("## Времена выполнения")
        p()
        p("Извлекаются **только** явно помеченные `общее время: HH:MM:SS.ffff` "
          "и `за Nms`. Остальные числовые значения и RPC-duration без единиц "
          "не интерпретируются как миллисекунды.")
        p()
        if durations:
            values = sorted(v[0] for v in durations)
            p(f"Распознано: **{len(values):,}** · p95: **{values[int(.95 * (len(values)-1))]:,.1f} мс** "
              f"· max: **{values[-1]:,.1f} мс**. Это статистика *только распознанных* длительностей.")
            p()
            p("| Длительность, мс | Компонент | Тип | Событие |")
            p("|---:|---|---|---|")
            for millis, component, kind, link, headline in sorted(durations, reverse=True)[:top]:
                p(f"| {millis:,.1f} | {md_cell(component, 45)} | {kind} "
                  f"| [{md_cell(headline, 100)}]({link}) |")
        else:
            p("Распознанных длительностей нет.")
        p()
        p("## Идентификаторы обращений")
        p()
        p(f"Непустых уникальных Request ID: **{len(request_counts):,}**. "
          "Следующий список показывает наиболее многословные обращения, но не ошибки.")
        p()
        p("| Request ID | Событий |")
        p("|---|---:|")
        for req, cnt in request_counts.most_common(15):
            p(f"| `{md_cell(req, 90)}` | {cnt:,} |")
        p()
        p("## Полный журнал по страницам")
        p()
        p("Каждая страница содержит исходную запись, включая многострочные stack trace. "
          "Порядок событий — порядок строк исходного файла, **не сортировка** по времени.")
        p()
        p("| День / час | Страница | Событий |")
        p("|---|---|---:|")
        for page in pages:
            day, hour = page["key"]
            p(f"| D+{day} {hour}:xx | [{page['path']} ]({page['path']}) | {page['count']:,} |")
        p()
        p("---")
        p("**Конфиденциальность:** исходный лог может содержать ФИО, GUID пациента, "
          "адреса служб и содержимое запросов. Markdown и JSONL сохраняют эти данные; "
          "не публикуйте каталог отчёта в открытом доступе.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Полный разбор AKUZ .log → report.md + страницы событий + JSONL.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("log", type=Path, help="Исходный текстовый лог AKUZ")
    ap.add_argument("-o", "--out", type=Path, help="Каталог отчёта (по умолчанию рядом с логом)")
    ap.add_argument("--date", type=date.fromisoformat, help="Дата первой записи, YYYY-MM-DD; если неизвестна — не указывать")
    ap.add_argument("--page-size", type=int, default=1000, help="Максимум событий на Markdown-страницу")
    ap.add_argument("--md-limit", type=int, default=16000,
                    help="Максимум символов одной записи в Markdown; 0 — без ограничения, полный текст всегда есть в JSONL")
    ap.add_argument("--top", type=int, default=35, help="Строк в рейтингах повторов и длительностей")
    ap.add_argument("--jsonl", choices=("gz", "plain", "none"), default="gz",
                    help="Формат полного архива распознанных событий")
    args = ap.parse_args(argv)
    if not args.log.is_file():
        ap.error(f"Не найден файл: {args.log}")
    if args.page_size <= 0 or args.md_limit < 0 or args.top <= 0:
        ap.error("--page-size и --top должны быть >0; --md-limit >=0")
    if args.md_limit and args.jsonl == "none":
        ap.error("С --jsonl none необходим --md-limit 0, иначе часть текста не будет сохранена")
    dest = args.out or args.log.with_name(args.log.stem + "_report")
    dest = dest.resolve()
    src = args.log.resolve()
    if src.is_relative_to(dest):
        ap.error("Каталог результата не должен содержать исходный лог")
    dest.mkdir(parents=True, exist_ok=True)
    writer = PageWriter(dest, args.page_size, args.md_limit, args.date)
    stats: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    components: Counter[str] = Counter()
    users: Counter[str] = Counter()
    requests: Counter[str] = Counter()
    hours: Counter[tuple[int, str]] = Counter()
    hourly_categories: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    patterns: Counter[tuple[str, str, str]] = Counter()
    pattern_first: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    examples: dict[str, list[tuple[str, str]]] = defaultdict(list)
    durations: list[tuple[float, str, str, str, str]] = []
    archive_name = "events.jsonl.gz" if args.jsonl == "gz" else "events.jsonl" if args.jsonl == "plain" else ""
    if args.jsonl == "gz":
        archive = gzip.open(dest / archive_name, "wt", encoding="utf-8", compresslevel=5)
    elif args.jsonl == "plain":
        archive = (dest / archive_name).open("w", encoding="utf-8")
    else:
        archive = None
    try:
        for ev in event_stream(src, stats):
            ev["category"] = classify(ev["message"])
            if args.date is not None:
                ev["date"] = str(args.date + timedelta(days=ev["day_offset"]))
            href = writer.write(ev)
            if archive is not None:
                archive.write(json.dumps(ev, ensure_ascii=False) + "\n")
            stats["events"] += 1
            category = ev["category"]
            categories[category] += 1
            comp = ev["component"]
            components[comp] += 1
            users[ev["user"]] += 1
            if ev["request_id"]:
                requests[ev["request_id"]] += 1
            hour = (ev["day_offset"], ev["time"][:2] if ev["time"] else "unknown")
            hours[hour] += 1
            hourly_categories[hour][category] += 1
            key = (comp, category, normalize(ev["message"]))
            patterns[key] += 1
            displaytime = render_time(ev["day_offset"], ev["time"], args.date) if ev["time"] else "без времени"
            if key not in pattern_first:
                pattern_first[key] = (href, displaytime, displaytime)
            else:
                first = pattern_first[key]
                pattern_first[key] = (first[0], first[1], displaytime)
            if len(examples[category]) < 5:
                headline = ev["message"].split("\n", 1)[0]
                examples[category].append((href, f"{displaytime} · {comp or '(пустой)'}: {headline}"))
            duration = extract_duration(ev["message"])
            if duration is not None:
                millis, kind = duration
                durations.append((millis, comp, kind, href, ev["message"].split("\n", 1)[0]))
    finally:
        writer.close()
        if archive is not None:
            archive.close()
    save_report(dest, src, args.date, stats, categories, components, hours,
                hourly_categories, patterns, pattern_first, users, requests,
                durations, writer.pages, examples, args.top, archive_name)
    print(f"OK: events={stats['events']:,}, physical_lines={stats['physical_lines']:,}, "
          f"continuations={stats['continuation_lines']:,}, pages={len(writer.pages)}, "
          f"replacement_chars={stats['replacement_chars']:,}")
    print(f"Report: {dest / 'report.md'}")
    if archive_name:
        print(f"Archive: {dest / archive_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
