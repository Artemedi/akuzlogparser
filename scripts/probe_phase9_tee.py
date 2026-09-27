"""Isolated Phase 9.2 A In-Flight Tee proof, synthetic files ONLY.

Not called by the production app and does not publish inventory entries.
One event_stream() per source, one derived value per event, bounded queue
with detached per-report event dictionaries. Failure cancels the consumer.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from queue import Empty, Full, Queue
import threading

from akuz_app import _iter_combined_sources
from akuz_html_explorer import generate, read_input
from scripts.bench_phase9_baseline import create_sources, file_manifest


class TeeCancelled(RuntimeError):
    pass


def tee_generate(sources: list[Path], individual: list[Path],
                 combined: Path, scratch: Path, *, queue_size: int = 64,
                 fail_combined_at: int | None = None):
    """Prototype ONLY: input sources already resolved and sorted by date.

    All files are synthetic in this harness; production cache/ownership,
    collision, partial publication and mixed-cache gates are separate.
    """
    if len(sources) < 2 or len(sources) != len(individual):
        raise ValueError('Require at least two sources and matching writers')
    if queue_size < 1:
        raise ValueError('Queue size must be positive')
    dates = [date.fromisoformat(p.name[:4]+'-'+p.name[4:6]+'-'+p.name[6:8])
             for p in sources]
    if dates != sorted(dates):
        raise ValueError('Sources must be sorted by first date')
    base = dates[0]
    byte_count = sum(p.stat().st_size for p in sources)
    queue: Queue[object] = Queue(maxsize=queue_size)
    cancel = threading.Event()
    end = object()
    count = 0
    line_offset = 0
    parser_calls = []
    produced = []

    def receive():
        seen = 0
        while True:
            try:
                item = queue.get(timeout=.1)
            except Empty:
                if cancel.is_set():
                    raise TeeCancelled('Producer cancelled')
                continue
            if item is end:
                return
            seen += 1
            if fail_combined_at is not None and seen == fail_combined_at:
                raise ValueError('injected combined tee failure')
            yield item

    def send(item):
        while True:
            if cancel.is_set():
                raise TeeCancelled('Combined writer failed')
            try:
                queue.put(item, timeout=.1)
                return
            except Full:
                continue

    def run_combined():
        try:
            return generate(scratch, combined, base, 10, 35,
                            event_source=receive(), input_bytes=byte_count,
                            derived_hook=lambda ev: ev.pop('_phase9_derived'))
        except BaseException:
            cancel.set()
            raise

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='akuz-tee') as pool:
        future = pool.submit(run_combined)
        try:
            single_metas = []
            for index, (source, output, first_date) in enumerate(
                    zip(sources, individual, dates)):
                stats = Counter()
                parser_calls.append(source.name)

                def sink(ev, value):
                    nonlocal count
                    count += 1
                    clone = dict(ev)
                    original_start, original_end = ev['start_line'], ev['end_line']
                    clone['original_start_line'] = original_start
                    clone['original_end_line'] = original_end
                    clone['source_event_id'] = ev['event_id']
                    clone['source_file'] = source.name+' · '+first_date.isoformat()
                    clone['event_id'] = count
                    clone['day_offset'] = (first_date-base).days + ev['day_offset']
                    clone['date'] = (base+timedelta(days=clone['day_offset'])).isoformat()
                    clone['start_line'] = line_offset + original_start
                    clone['end_line'] = line_offset + original_end
                    clone['_phase9_derived'] = value
                    send(clone)
                    produced.append(value)

                meta = generate(source, output, first_date, 10, 35,
                                event_source=read_input(source, first_date,
                                    stats, defer_classify=True),
                                derived_sink=sink,
                                input_bytes=source.stat().st_size)
                line_offset += meta['physical_lines']
                single_metas.append(meta)
            send(end)
            combined_meta = future.result(timeout=30)
        except BaseException:
            cancel.set()
            try:
                future.result(timeout=5)
            except BaseException:
                pass
            raise
    return dict(individual=single_metas, combined=combined_meta,
                parser_calls=parser_calls, derived_events=len(produced),
                event_count=count, queue_bound=queue_size)


def baseline_generate(sources: list[Path], individual: list[Path],
                      combined: Path, scratch: Path):
    first_dates = [date.fromisoformat(p.name[:4]+'-'+p.name[4:6]+'-'+p.name[6:8])
                   for p in sources]
    for source, output, first_date in zip(sources, individual, first_dates):
        generate(source, output, first_date, 10, 35)
    from akuz_store import sha256
    selections = [dict(local=p,sha=sha256(p),date=d.isoformat(),
                       remote=dict(name=p.name,path=str(p),mtime=i))
                  for i,(p,d) in enumerate(zip(sources,first_dates))]
    return generate(scratch, combined, first_dates[0], 10, 35,
                    event_source=_iter_combined_sources(selections, first_dates[0]),
                    input_bytes=sum(p.stat().st_size for p in sources))
