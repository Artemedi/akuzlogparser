# AKUZ Log Explorer — Performance & Architecture Roadmap

**Дата:** 2026-09-26. **Назначение:** авторитетная точка передачи проекта в новый чат/другому агенту; план, а не утверждение уже выполненных улучшений.
**Репозиторий:** https://github.com/Artemedi/akuzlogparser
**Локальный каталог Windows:** `E:\Software\Project\LogAkusExplorer\akuzlogparser`
**Исходный checkpoint:** `1ce50a1dc46b1fd6e822a712919b39c9b921de7d` (Phase 8, основной branch `main`).
**Последний выпущенный GitHub Release:** v4.6.0. Phase 6/7/8 коммитились в `main`; их диагностические EXE не являются заменой опубликованного релиза.
**Главный следующий этап:** Phase 9 — сохранить корректность и кэш, перестать повторно вычислять производные признаки события в combined.
**2026-09-26 Phase 9.0a:** synthetic Windows baseline + memory sampler + isolated cache matrix implemented; production/portable/A-B gate still OPEN. See PERFORMANCE_NOTES.md.
**Статус каждой строки ниже:** OPEN / PROPOSED до отдельной реализации, тестирования и записи доказательств. Предыдущие фазы 6–8 COMPLETED.

## 0. Обязательное начало новой сессии: recovery и границы полномочий

1. Подключиться к Windows-устройству DBA-008D; проверить `git status --short --branch`, `git rev-parse HEAD`, `git ls-remote origin refs/heads/main`, текущие тесты, README и текущий код. Не считать этот документ более авторитетным, чем свежий рабочий репозиторий; не перезаписывать чужие незакоммиченные изменения.
2. Ознакомиться с `akuz_app.py` (`perform_build`, `_iter_combined_sources`, `_combine_sources`), `akuz_html_explorer.py` (`read_input`, `generate`, `CAT_EXTRA`), `akuz_log_parser.py` (`event_stream`, `classify`, `normalize`, `extract_duration`), `akuz_analytics.py` (`recognize_error`, `refresh`), `akuz_store.py`, `akuz_fetch.py`, `PERFORMANCE_NOTES.md`, `CHANGELOG.md` и тестами.
3. Область источников: **только .log приложения АКУЗ**; не добавлять Windows Event Log, системные журналы, VCLib или новый парсер без отдельного решения пользователя.
4. Никогда не публиковать реальные журналы, сырые события, адреса серверов, пароли, `ConnectConf.cfg` или содержимое пользовательского кэша в GitHub, benchmark-файлах и логах диагностики. Для GitHub использовать синтетику и счётчики.
5. Режим изменения: один ограниченный workstream → тесты и byte-equivalence → отдельный commit/push; новый portable EXE только после успешных проверок. GitHub Release не изменять без отдельного подтверждения. Все пробы с неопределённым выигрышем делать вне production-пути и удалять/отклонять, если нет доказательств.
6. При создании отчётов не удалять `downloads/`, `reports/`, `cache/` и действующий `ConnectConf.cfg`. Оригинальные источники только read-only. Не запускать одновременно эксперимент и рабочий клиент на тех же директориях.

## 1. Проверенный baseline, который нельзя подменять оценкой

Контрольный полный прогон **трёх неизменных по размеру и структуре журналов**:

| Источник | bytes | events | physical lines | raw_chars | shards |
|---|---:|---:|---:|---:|---:|
| A — большой | 644567384 | 225539 | 300738 | 634140420 | 226 |
| B | 140361291 | 221082 | 293882 | 130108173 | 222 |
| C | 171378567 | 211117 | 297803 | 162549813 | 212 |
| combined | 956307242 | 657738 | 892423 | 926798406 | 658 |

Это совпадение размеров/счётчиков по логам **не равно доказательству равенства SHA входных файлов**; для будущих A/B сравнений дополнительно сохранять хэши снимков без содержимого.

| Замер | Phase 6 | Phase 7 | Phase 8 |
|---|---:|---:|---:|
| Весь `perform_build_current`, s | 376.294 | 352.608 | 362.049 |
| Все 4 отчёта `report.generate`, s | 223.722 | 210.347 | 210.137 |
| combined `generate.parse`, s | 109.201 | 102.025 | 102.267 |
| combined `report.generate`, s | 113.465 | 106.324 | 106.349 |
| combined `analytics.refresh`, s | 37.678 | 37.563 | 37.581 |

Phase 7 реально сократила суммарную генерацию отчётов приблизительно на 13.38 s. Отличие общего времени полных запусков также зависит от SSH; не относить его автоматически на счёт CPU-оптимизации.

**Phase 8, combined `generate.parse`, wall seconds:** `source_next_s=12.641`, `classify_s=12.204`, `normalize_s=14.992`, `duration_s=14.419`, `errors_s=20.844`, `fold_s=6.578`, `shard_write_s=14.231`, `other_s=6.358`; `thread_cpu_s=100.859` при `elapsed_s=102.267`. Пять производных фаз в сумме = **69.037 s**; `source_next_s` дополнительно 12.641 s. Это измеренная стоимость работы, **не гарантированная будущая экономия**. `source_next_s` включает сборку multiline, декодирование, преобразование событий; `shard_write_s` включает сериализацию JS и запись, это не чистый диск. `combined.stream.active_s=12.248` пересекается с `source_next_s`, **не складывать**.

Phase 8: `error_recognize_calls=657738`; `error_no_match_events=619886`, `error_exception_events=6692`, `error_firstline_events=31160`, `error_serial_events=0`, `error_truncated_events=10984`, `error_probe_chars=401006096`, `error_ascii_probe_events=103584`. Нулевая ветвь SERIAL не означает отсутствия SerializationException: приоритетная ветвь EXCEPTION может её поглотить. 94.25% no-match событий **не означает 94.25% времени**. Максимальное событие `max_event_chars=641195`.

Файлы исходных логов Phase 6/7/8 в диалоге названы `performance.txt`, но сами производственные журналы не передавались и не должны попадать в репозиторий.

## 2. Общие инварианты и проверки КАЖДОГО этапа

- Сохранять заголовки, BOM, CRLF, `utf-8-sig`, `errors="replace"`, многострочные события и точную семантику `raw="\n".join(...)`. Никогда не обещать, что `raw_offset/raw_length` в исходном бинарном файле без преобразований воспроизведут текущий `raw`.
- Сохранять событие и его provenance: порядок, `event_id`, `day_offset`, абсолютную дату, `start_line/end_line`, original source lines, `source_file`, идентичность host/path/snapshot, компоненты, запрос/пользователя, rolled-over timestamps и out-of-order.
- Сохранять `category`, `normalize(message)`, `extract_duration(message)`, `recognize_error(raw)` и `fp`. Не путать `akuz_log_parser.normalize` и внутреннюю `akuz_analytics.normalize` для fingerprint.
- Сохранять категории и их численные ID, порядок первого появления `component_ids`/`source_ids`, `pattern_first`, `rows`, `raw_shard`, `replacement_chars`, количество частей, `catalog.js` и итоговую аналитику.
- `CAT_EXTRA` — потенциально глобальное изменяемое состояние, требует отдельной проверки последовательности генераторов и/или рефакторинга в состояние генератора; доказать неизменность численных category IDs.
- `provenance.json` может содержать `datetime.now()`: фиксировать часы в тесте либо сравнивать лишь точечно исключённое недетерминированное поле. Не исключать целиком файл, если там содержатся значимые ссылки на источники.
- Стандарт проверок: полный Python `unittest discover -s tests -q`; Node UI-тесты; `scripts/check_classify_equivalence.py`, `scripts/check_report_equivalence.py`, `scripts/check_combined_equivalence.py`, `scripts/check_event_stream_equivalence.py`, тесты individual/combined и analytics. Обновлять набор под новые схемы, но не удалять старые guards.
- Выходная идентичность: SHA-256 всех детерминированных `raw_*.js`, `catalog.js`, HTML/JS/assets и значимого provenance; структура и контрольные запросы SQLite; логика `inventory.json`, без потери данных при прерывании. Числовое совпадение `events` не заменяет byte-equivalence.
- Не считать успешным прототип, увеличивший RAM или риск потери данных без отдельного решения; измерять CPU, wall, диск, RSS, размер кэша и восстановление после сбоя.
- Эталонный прогон должен включать cold/fresh-all, warm/no-op, combined-only-miss, single-only-miss, mixed-cache (2 старых + 1 новый), одинаковые bytes с разными host/path, смену даты, изменение активного источника во время чтения, повреждённый/устаревший кэш, некорректный Unicode, CRLF, полночь, большой XML/стек, отсутствие доступа к файлу и отмену сборки.
- Условия отклонения: любые необъяснённые отличия контента, потеря provenance, нарушение кэша, скрытые дубли событий, существенно худший RSS, незафиксированная модель восстановления, ускорение лишь из-за другого SSH/другой нагрузки.

## 3. Phase 9.0 — подготовить доказуемый benchmark и измерение памяти [IN PROGRESS; 9.0a SYNTHETIC PASS]

1. Ввести один воспроизводимый benchmark runner: фиксировать Git SHA, исходные snapshot SHA/bytes/events, ОС, Python/portable, режим кэша, состав build.summary, общий wall, CPU, timings фаз, размер output/cache, условия хранения; НЕ сохранять исходный raw в диагностике.
2. Добавить Windows-измерение пикового Working Set/Private Bytes, учитывая процесс загрузчика/дочерний процесс PyInstaller; `tracemalloc` оставить **дополнительной**, а не заменяющей RSS метрикой. Использовать Windows API либо отдельный проверенный монитор; отдельно сравнить измерения обычного Python и portable EXE.
3. Стабилизировать сравнение детерминированных файлов, корректно обработать `provenance.json` и `CAT_EXTRA`. Добавить сравнение `inventory.json` и результатов `analytics.refresh` в изолированной тестовой директории.
4. Отдельно профилировать `recognize_error`: wall/CPU по ветвям и диапазонам размеров raw/probe, а не только долю no-match. Phase 8 показала частоты, но ещё не разложила 20.844 s по ветвям.
5. Парные A/B по 3+ прогонов с чередованием порядка и одинаковыми SHA снимков, с разделением SSH и локальной генерации. Сохранять отрицательные результаты как часть журнала экспериментов.

**Gate:** можно воспроизвести Phase 8 на синтетике, получить byte-equivalence, корректный пик памяти на Windows и понятную телеметрию. Это инструментальный этап, ускорение не обещается.

**9.0a evidence:** `scripts/bench_phase9_baseline.py`, `scripts/phase9_memory.py`, `scripts/bench_phase9_errors.py`, `tests/test_phase9_baseline.py`; Windows Python synthetic fresh/warm/combined-miss/single-miss/mixed-cache and branch profiles. Details in `PERFORMANCE_NOTES.md`. This is NOT Phase 9.0 closure: portable EXE, unchanged real-snapshot SHA, full fault/corruption cases and old/new alternating A/B remain open.

## 4. Phase 9.1 — выделить контракт Derived/ProcessedEvent [OPEN]

1. Провести точный аудит `generate()`, `read_input()` и `_iter_combined_sources()`; записать «инвариант относительно individual/combined» или «контекст отчёта» для каждого поля. Не объявлять `event_id`, `day_offset`, `source_idx`, `pattern_first`, shard index и source line автоматически инвариантными.
2. Ввести чистый derive-этап `raw/message -> {category, normalized_pattern, duration, error_match, replacement_count}` и отдельный writer-этап, получающий derive + контекст отчёта; это **эскиз контракта**, не обязательные имена API.
3. Сохранить совместимость `generate()`, `read_input()`, CLI, `event_source`, инжектированных `gen_fn` и опциональных параметров Phase 6–8. Не создавать альтернативный разборщик заголовков/многострочности.
4. Проверить, нельзя ли избежать второго полного `raw.count("\ufffd")` после уже имеющегося подсчёта парсера. Сначала доказать, что счётчик относится к той же строке и сохраняет `replacement_chars` для combined.
5. Изолировать `CAT_EXTRA` только если тестами доказана необходимость и точная прежняя семантика, включая порядок категорий.
6. Дифференциально сравнить прежний и новый generator, включая настоящий combined и все четыре режима кэша; тестировать инъекции ошибок и атомарную публикацию.

**Gate:** refactor-only, прежние HTML/JS/catalog/analytics byte-identical; одна функция derive в коде, отсутствие изменений бизнес-семантики; отдельный commit/push только после PASS.

## 5. Phase 9.2 — прототип A, In-Flight Tee [OPEN, НЕ УТВЕРЖДЁН]

1. Только в изолированной ветке/прототипе: один `event_stream()` и derive на свежий источник → individual writer и combined writer.
2. Сначала `resolve` всех источников: идентичность, дедупликация, даты, порядок combined, попадания/промахи кэша и ключи снимков; writer combined открывать/публиковать атомарно лишь по доказанным правилам.
3. При ready individual + missing combined **не пересоздавать individual**, но для этого источника всё равно придётся повторно derive: A не хранит прошлые результаты. Это ключевое ограничение, а не потеря корректности кэша.
4. Два writer не должны обмениваться изменяемыми `ev`/`rows`/`CAT_EXTRA`; проверить source/category/component IDs, `pattern_first`, rollover, shards, публикацию, порядок возврата отчётов.
5. Замерить дополнительный raw_shard и второй каталог, пиковый RSS, GC, `shard_write_s` при чередовании записи двух директорий.
6. Обязательные сценарии fresh-all, mixed-cache, combined-only-miss, single-only-miss, no-op, failure halfway; DIAG spy: derive вызывается ровно один раз **только в случае, когда действительно есть два активных потребителя свежего события**.

**Gate:** побайтовая идентичность и выгодный свежий прогон без неприемлемого пика RSS. A — кандидат, не предрешённая архитектура.

## 6. Phase 9.3 — прототип B-lite, Processed Sidecar + event_stream [OPEN, НЕ УТВЕРЖДЁН]

1. Sidecar хранит только производные характеристики и привязку к ordinal/event identity; исходный `raw` и `message` НЕ сохранять повторно. Для combined использовать существующий `event_stream()` плюс синхронное чтение sidecar; сохранить 12–13 s повторного чтения/склейки ради меньшего риска.
2. Явно версионировать контракт **всех** функций derive: классификатор, нормализатор, длительности, распознаватель/FP, парсер. Заголовок: версия, SHA снимка, host/path identity, count, размер/контрольная сумма, порядок, версия формата, целостность блоков.
3. Сравнить хотя бы JSONL и строго версионированный бинарный/пакетный формат на **одном составе реальных по форме метаданных**. Никаких заранее принятых «JSON 18–25 s», «бинарный быстрее 1 s на 100k», «sidecar ≤20 МБ» без измерения. `marshal` не принимать как постоянный стабильный формат по умолчанию.
4. Писать sidecar потоково во временный файл, полностью валидировать и атомарно публиковать вместе с корректной идентичностью снимка; не добавлять лишний долговременный raw, не писать чувствительные тексты в логи.
5. При старом индивидуальном отчёте без sidecar: гарантировать стандартный fallback, не требовать регенерации готового individual. При повреждении в середине combined безопасно откатить временный combined и пересобрать стандартным способом; не смешивать полусайдкар и полупарсер без доказанного restart-протокола.
6. Проверить invalidation, source alias/identical bytes different host/path, schema upgrade, partial cache, злонамеренные/повреждённые записи, version mismatch, отмену и disk-full, RSS и storage amplification.

**Gate:** побайтовая идентичность, корректный mixed-cache и net gain с учётом записи + чтения sidecar, приемлемый размер/пиковая RAM. Если нет — B-lite не включать в production.

## 7. Phase 9.4 — выбор A / B-lite / гибрида; B-full только по отдельному доказательству [OPEN]

1. Свести прототипы на одной матрице: fresh-all, warm, mixed-cache, missing combined, повторное открытие спустя перезапуск, source changed, crash/rollback; CPU/wall/RSS/cache-disk.
2. A даёт преимущество для свежего веера, B-lite — повторное использование прежних derive при смешанном кэше. Не объявлять один вариант абсолютным победителем до сравнительных данных.
3. Гибрид A+ B-lite — отдельная кандидатура после самостоятельного PASS обеих частей; не вводить в одном непроверяемом рефакторинге.
4. **B-full** с `raw_offset/raw_length` и обходом второго `event_stream()` оценивать только после доказательства точного преобразования исходных байтов в `raw` (BOM, invalid UTF-8 replacement, CRLF, multiline, Windows trace, преамбула). Считать вторым парсером любой код, который заново определяет границы событий; не принимать его без полного differential corpus.
5. Целевой `combined.generate.parse≈46–52 s` из предложения агента №1 и экономию 50–80 s считать **непроверенными сценариями**, а не gate-условием. Важнее устойчивый net gain, correctness и RSS.

**Gate:** документированное решение владельца по выбранной архитектуре. Только затем ограниченное production-внедрение, отдельные коммиты, rollback и Windows portable.

## 8. Следующие независимые улучшения после Phase 9 (НЕ смешивать)

### Phase 10 — SSH compression A/B [OPEN]
1. На одном неподвижном snapshot SHA измерить compression=0/1 по >=3 чередующимся прогонам с холодным/тёплым файловым кэшем; зафиксировать серверный sshd CPU, клиент CPU, bytes over wire, elapsed и throughput.
2. Сравнить особенно 644 567 384-byte источник; отдельно оценить риск CPU-давления на сервере приложений и скорость/сжимаемость XML.
3. При отсутствии стабильного net gain сохранить compression=0 по умолчанию. Не менять семантику snapshot, SHA, stat_before/after.

### Phase 11 — перекрытие SSH download и parse [OPEN]
1. Проверить конвейер A download → parse(A) параллельно download(B), НЕ чтение растущего активного файла без законченного snapshot.
2. Измерять wall, CPU/IO конкуренцию, RSS, SSH стабильность, rollback, отмену, отсутствие разных версий одной и той же копии.
3. Учитывать, что текущий `perform_build` последователен; не вносить scheduler + cache rewrite в один commit.

### Phase 12 — delta/resume для удалённого .log [OPEN, повышенный риск]
1. Разделить append-only активный журнал, удалённый архив, новый файл с тем же именем, truncated/replaced/rotated file.
2. Доказать идентичность сохранённого префикса криптографически/надёжными сегментами, проверять before/after stat, дату, inode/identity по доступности, file size и границу multiline; делать atomic commit offset **только после** успешной фиксации полного снимка/отчёта.
3. Fault-injection для network loss, source mutation, disk full, checksum mismatch, replay, partial UTF-8 и незавершённого события. При сомнении — полный snapshot; никогда не пропускать/не дублировать события.

### Phase 13 — аналитика SQLite и третий проход [OPEN]
1. Измерить `analytics.ingest/export/overview`, SELECT/INSERT, индексы, транзакции, write amplification и повторное распознавание ошибок по raw; baseline refresh ≈37.58 s.
2. Проверить возможность передавать сохранённые error fingerprint/derived без нарушения dedup/source identity, ambiguous dates, relative_day, migration и атомарного экспорта.
3. Исследовать batch inserts и индексы, SQLite `journal_mode=DELETE` vs WAL ТОЛЬКО отдельным A/B; не переносить старый несовместимый модуль `error_events`. Сравнить exact SQL outputs и `analytics.js`.

### Phase 14 — JS serialization, catalog, browser usability [OPEN]
1. Разделить `shard_write_s` на CPU `json.dumps`/escaping и файловую запись, замерить output bytes и RSS.
2. Проверить наличие лишних проходов `raw.count("\ufffd")`, повторных преобразований, стоимости каталога и большого `rows`, не меняя `raw_*.js` и `catalog.js` вслепую.
3. Измерить время первого открытия и интерактивности браузера для 657 738 событий, фильтры, даты, повторяющиеся ошибки, графики, память вкладки. Lazy loading/виртуализация — отдельный проект с проверкой совместимости standalone/offline UI.
4. Предыдущий `str.translate` вместо JS escaping не подтвердил устойчивый выигрыш; не повторять без новой гипотезы.

### Phase 15 — concurrency / multiprocessing [OPEN, низкий приоритет до устранения лишних проходов]
1. После Phase 9 и A/B I/O-измерений проверить, остался ли CPU bottleneck; сравнить процессы против IPC/serialization/memory, Windows spawn и PyInstaller.
2. Не параллелить сериализацию или SQL без понимания ownership, source order, SHA и rollback.
3. При отсутствии выигрыша или удвоении памяти оставить однопроцессную архитектуру.

### Phase 16 — оптимизация локального кэша и UI-связности [OPEN]
1. Аудит clean-cache и per-folder cache, повторные отчёты с одинаковым именем, stale browser session, source identity и одинаковые bytes с разными paths/hosts.
2. Проверить mixed-cache со всеми версиями схемы, отчётные даты, фильтры, архив/активный файл, не переносить независимые файлы логов между источниками.
3. Отдельная UX-метрика: поиск ошибки, график повторений по часам/дням, время переключения отчётов и выгрузки после их построения.

## 9. Выпуск и фиксирование результатов каждого шага

1. Публиковать на каждом этапе: гипотеза, exact HEAD/parent SHA, изменённые файлы, тестовая матрица, список byte-equivalence, ограничения, до/после CPU/wall/RSS/storage, принята/отклонена гипотеза.
2. Каждый принятый ограниченный этап — отдельный commit + push. Если пользователь требует «в любом случае коммит и пуш» — документировать отрицательные эксперименты в `PERFORMANCE_NOTES.md`/roadmap, но НЕ оставлять замедляющий/некорректный код ради отчётности.
3. Сборка: `scripts/build_portable.py` → `scripts/smoke_portable.py` → verify `BUILD_INFO.json` Git SHA, EXE/ZIP `SHA256SUMS.txt`; хранить диагностический EXE в отдельном `dist/phaseX/`, не заменять GitHub Release без разрешения.
4. Реальные результаты Windows-прогона — снимать в отдельном `diagnostics/performance.txt`, передавать агентам **без сырых журналов/секретов**; поддерживать простое сравнение последних baseline по exact snapshot SHA.
5. После выбора архитектуры Phase 9 обновить этот roadmap статусами DONE/MODIFY/BLOCKED, новыми измеренными цифрами и датой. Не трактовать устаревшие оценки как новые факты.

## 10. Короткая инструкция для нового чата

«Продолжай AKUZ Log Explorer по файлу `ROADMAP_PERFORMANCE.md` из `Artemedi/akuzlogparser`. Подключись к Windows DBA-008D, восстанови актуальный Git HEAD и ознакомься с Phase 6–8 и `PERFORMANCE_NOTES.md`. Сначала Phase 9.0: воспроизводимый benchmark + фактический Windows RSS + тестовый baseline; затем Phase 9.1: безопасная экстракция derived-контракта. Не выбирай A/B до изолированных прототипов Phase 9.2/9.3 и не смешивай SSH/SQLite/delta. Обязательны byte-equivalence, mixed-cache, source identity, fail-safe, тесты, commit/push каждого принятого этапа. Не менять Release и не удалять рабочие источники, кэш и конфиг. Прогнозы экономии — только гипотезы до реального Windows-прогона».
