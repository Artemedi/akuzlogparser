"""Phase 9.1 event-derived values independent of report-specific coordinates.

A derived record deliberately excludes event_id, date/day_offset, source index,
line offsets, per-report category/component IDs, first-pattern ID and shard index.
Only optional numeric diagnostics mutate the caller's Counter; output fields
depend solely on raw/message/preclassified category and injected algorithms.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class Derivers:
    classify: Callable[..., str]
    normalize: Callable[[str], str]
    duration: Callable[..., tuple[float, str] | None]
    recognize_error: Callable[..., dict[str, Any] | None]
    classify_diagnostics: bool
    classify_folded: bool
    duration_folded: bool
    error_diagnostics: bool


@dataclass(frozen=True, slots=True)
class DerivedEvent:
    category: str
    normalized_pattern: str
    duration: tuple[float, str] | None
    error_fp: str | None
    replacement_chars: int
    headline: str
    folded_s: float
    classify_s: float
    normalize_s: float
    duration_s: float
    error_s: float


def derive_event(ev: dict[str, Any], stats: Any, fns: Derivers) -> DerivedEvent:
    """Compute once per event; NEVER embed context from individual/combined.

    Diagnostic counters preserve the pre-existing classify/recognize_error
    contracts, including injected callable compatibility.
    """
    text = ev["message"]
    raw = ev["raw"]
    stamp = perf_counter()
    folded = text.casefold()
    fold_s = perf_counter() - stamp

    category = ev.get("category")
    classify_s = 0.0
    if not category:
        stamp = perf_counter()
        category = (fns.classify(text, diagnostics=stats, folded=folded)
                    if fns.classify_diagnostics and fns.classify_folded
                    else fns.classify(text, diagnostics=stats)
                    if fns.classify_diagnostics
                    else fns.classify(text, folded=folded)
                    if fns.classify_folded else fns.classify(text))
        classify_s = perf_counter() - stamp

    stamp = perf_counter()
    norm = fns.normalize(text)
    normalize_s = perf_counter() - stamp

    stamp = perf_counter()
    duration = (fns.duration(text, folded=folded)
                if fns.duration_folded else fns.duration(text))
    duration_s = perf_counter() - stamp

    replacement_chars = raw.count("\ufffd")
    newline = text.find("\n")
    headline = (text if newline < 0 else text[:newline]).strip()[:1400]

    stamp = perf_counter()
    error = (fns.recognize_error(raw, diagnostics=stats)
             if fns.error_diagnostics else fns.recognize_error(raw))
    error_s = perf_counter() - stamp
    return DerivedEvent(
        category, norm, duration, error["fp"] if error else None,
        replacement_chars, headline, fold_s, classify_s, normalize_s,
        duration_s, error_s)
