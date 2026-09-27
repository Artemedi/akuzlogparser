# Оптимизация разбора AKUZ: план и проверяемые результаты

> История измерений изменений, включённых в v4.6.0 относительно v4.5.0. Ранние записи ниже фиксируют состояние отдельных экспериментальных этапов до релиза; синтетические замеры не являются производственными SLA.
> Рабочие каталоги установленного Explorer, `reports/`, `downloads/`, `cache/`
> и исходные журналы не изменялись.

## Наблюдение в диагностическом журнале 25.09.2026

Обработка двух файлов: 174 752 и 221 082 событий, затем общей выборки
395 834 событий. Отдельные `report.generate` заняли 318.355 и 77.125 с;
повторная генерация общей выборки — 401.718 с; `analytics.refresh` — 111.186 с.
Полная операция: 1095.566 с. `shard_write_s` первого отчёта — 9.069 с
при `generate.parse` 317.268 с.

В первом отчёте группа событий 100 001–150 000 даёт приблизительно 466 МиБ
файлов `raw_*.js`, тогда как первые 50 000 — около 10.9 МиБ.
Это объясняет, почему прогресс «каждые 50 000 событий» не равномерен во времени.
Однако размер текстов сам по себе **не доказывает**, какой вызов CPU наиболее затратен.

## Этап 1. Многострочное сообщение

Старый `event_stream()` добавлял каждую строку в строку, сохранённую
в словаре текущего события: `current["message"] += "\n" + clean`.
Из-за повторного копирования растущей строки обработка одного большого
многострочного сообщения могла вести себя существенно хуже линейной.

Теперь фрагменты `message` сохраняются в списке, а строка собирается через
`join` единожды перед выдачей события. Исходный `raw` по-прежнему
формируется через прежний `raw_lines`, не меняются внешние поля,
классификация, формат каталога, счётчики и номера строк.

### Контрольная точка — только синтетические данные

```powershell
python scripts/bench_event_stream.py --lines 5000 --chars 4096
python scripts/check_event_stream_equivalence.py
python -m unittest discover -s tests -q
node --test tests/test_app_controls.cjs tests/test_hourly_view.cjs
```

На одном событии: 5 000 строк продолжения, 20 485 044 байта входа.
До: 14.1853 с, после: 0.0802 с на данной Windows-машине.
SHA-256 `raw`: `9149205ab42af9d5d2e9ae97d8819f4331a0a6e90b3db7f09b427886fb154990`
до/после одинаковый. Дополнительная дифференциальная проверка
сравнивает **полные словари событий и Counter статистики**, на трёх
детерминированных синтетических входах, с исходным модулем из
`e317722847ce287f6e279d2c2cd172d9f5a5663a`.

Это ускорение **только патологического теста**, а не прогноз ускорения
всей 18-минутной операции. Не запускать бенчмарк на рабочих медицинских логах.

## Диагностическое уточнение

Прогресс `generate.parse` дополнен `raw_chars` и `max_event_chars`:
число символов в исходных записях и максимальный размер одной записи.
Это не `bytes` (UTF-8 кириллица может занимать несколько байтов).
Метки каждые 50 000 событий продолжают измерять `elapsed_s`.
Тексты событий, имена пациентов, пути и секреты в диагностику не пишутся.

## Последующие отдельные шаги

1. На замерах v4.5.0 + этой правки сравнить `elapsed_s`, `raw_chars`,
   `max_event_chars` и размер источника при одинаковой выборке;
   затем инструментировать CPU-вызовы классификатора и распознавания ошибок,
   не меняя их сигнатуры.
2. Отдельно изучить повторный полный проход `combined.merge` +
   `report.generate`. Переиспользование готовых результатов допустимо
   только после проверки provenance, дат, исходных строк и структуры
   `catalog.js` / `raw_*.js`.
3. Отдельно профилировать `analytics.ingest` и SQLite-запросы.
   Ни один из следующих шагов не включён в первый эксперимент.

Критерий приёмки каждого шага: те же события и поля `event_stream`,
исходные тексты, даты, строки и идентичность источников,
та же структура HTML/JS и дедупликация аналитики. Кэш/репорты пользователя
не используются в автоматических тестах и не перезаписываются.

## Этап 2. Избежание ненужных регулярных выражений

Профилирование **синтетических** данных после этапа 1:
500 событий / 16 359 926 байт; `generate` = 6.1776 с без profiler.
При инструментировании cProfile 5.874 с заняли вызовы `re.Pattern.search`.
Выполнялись повторные полнотекстовые проходы в `classify`,
`extract_duration` и `recognize_error`.

Попытка объединить все шаблоны в один `finditer` дала **ухудшение**
(6.85 с под cProfile против 6.29 с до изменения): изменение отброшено.
Принят консервативный вариант: перед каждым существующим regex
проверяется присутствие обязательного литерала в `casefold` текста.
Точный regex, порядок приоритетов категорий и состав fingerprint
не изменены. Для поиска первой строки не создаётся копия огромного
хвоста сообщения.

На том же синтетическом входе после изменений `generate` = 0.7341 с
без profiler; примерно 8.4× по сравнению с 6.1776 с.
На синтетическом объединённом JSONL (65 717 573 байта, 1000 событий)
`combined.merge` = 0.5488 с, `generate` объединённого отчёта = 1.4068 с.
Для 1000 событий / 16 332 090 байт: отдельный `generate` = 0.7508 с,
`analytics.refresh` = 0.5753 с при 200 распознанных ошибках.

**Корректность:** `scripts/check_text_semantics.py` сравнивает все
четыре текстовые функции с исходными из `b7bfd5d` на 560
детерминированных синтетических строках. `scripts/check_report_equivalence.py`
сравнивает словарь metadata и байты `data/catalog.js`, всех
`data/raw_*.js` (160 событий, в том числе длинные, ошибки и длительности):
`IDENTICAL`. Это не полное доказательство на всех допустимых Unicode,
но покрывает основную схему журналов и защищает внешние данные.

Бенчмарк коротких строк также не выявил регресса: синтетические
20 000 вызовов `classify`: 0.1185 → 0.0486 с; `extract_duration`:
0.0208 → 0.0093 с; `recognize_error`: 0.1212 → 0.0893 с.

## Этап 3. Передача объединённых событий без временного JSONL

В штатной пачке `_iter_combined_sources()` теперь передаёт
откорректированные события непосредственно `generate(...,event_source=...)`.
Это убирает запись/чтение промежуточного JSONL, который на исследуемой
пачке составлял 1 668 598 822 байта. При этом итоговые `catalog.js`
и `raw_*.js` остаются прежними по структуре и содержимому.
Пустой временный файл сохраняется только как совместимое имя источника
в metadata и удаляется после генерации.

Старый публичный путь `_combine_sources(selected,scratch,base)`
с записью JSONL сохранён для CLI, внешних тестов и пользовательского
`gen_fn`. Прежний архивный JSONL и новый поток на контролируемом
синтетическом наборе дают побайтово идентичные HTML/JS, `catalog.js`,
все `raw_*.js`, metadata и дату/исходные строки.
`scripts/check_combined_equivalence.py` дополнительно сравнивает
байты JSONL с `_combine_sources()` из предыдущего коммита `6e27adb`.

На синтетической общей выборке в 1000 событий / 65 717 573 байта
промежуточного JSONL: старый `merge` = 0.5538 с и старый
`generate` = 1.4342 с; прямой `generate` = 1.3793 с.
Мерить и складывать `combined.stream` с `report.generate`
теперь нельзя: события формируются **внутри** генератора.
У `report.generate input_bytes` в прямом режиме теперь смысл суммы
размеров выбранных исходных файлов, а не размера JSONL.

### Оставшаяся архитектурная стоимость

Каждый исходный `.log` всё ещё парсится для отдельного отчёта, а затем
вновь парсится для объединённого. Полное переиспользование уже созданных
каталогов и частей требует отдельного доказательства эквивалентности
provenance, дат, нормализации, исходных строк, категорий, шаблонов
и дедупликации. Эта более рискованная оптимизация пока не внесена.

Отдельный реальный прогон новой версии на прежнем объёме пока не
выполнен. Цифры 18-минутного запуска v4.5.0 нельзя механически делить
на синтетический коэффициент ускорения.


## Этап 4. Получение снимка: устранение лишнего I/O и диагностика SSH

Контрольный реальный прогон от 21:29, присланный владельцем, завершился за
320.965 с; две операции `source.fetch` заняли 89.453 и 21.251 с.
Эти этапы включают подключение, проверку файла, транспорт, локальную запись
и финальные проверки. По общему времени **нельзя доказать**, что вся
задержка — это SSH-сеть.

В локальном и Windows/SMB-адаптерах теперь SHA-256 обновляется при каждом
прочитанном блоке, одновременно с записью `.part`. Для неизменённого
снимка больше не требуется второе чтение всего файла через `_sha_file()`.
Если активный файл завершился неполной физической строкой, снимок
обрезается, и SHA-256 повторно считается **только по фактически
сохранённым байтам**. Исходный файл остаётся read-only.

Для встроенного SSH-адаптера уточнены этапы `source.ssh.connect`,
`source.ssh.inventory`, `source.ssh.stat_before`, `source.ssh.transfer`,
`source.ssh.stat_after` и условный `source.ssh.trim_rehash`.
Прогресс передачи — раз в 64 МиБ, финальный `summary` содержит число
байтов и МиБ/с. Это скорость транспортного чтения **вместе с записью
локального снимка**, а не чистое измерение пропускной способности сети.
SSH-файлы уже хешировались во время чтения, поэтому второй проход там
не убирали. Статус каждого этапа не содержит пути, паролей или сырых
строк журнала.

Добавлена конфигурация `ssh.compression` (по умолчанию false).
Её разрешено включить вручную, сравнив нагрузку CPU SSH-сервера и
`source.ssh.transfer elapsed_s`/МиБ/с на сопоставимых снимках.
**Компрессия не была включена в рабочем ConnectConf.cfg и не измерена
на рабочем сервере.** Не заявлять ускорение от неё без реального теста.

Проверки: синтетические SSH-снимки с обычным и усечённым активным
хвостом, SHA-256, замеры без утечки путей; для локального и Windows
адаптеров тесты запрещают вызов `_sha_file` на статическом файле.
Дополнительный сетевой бенчмарк рабочих журналов не запускался.

## Этап 5. Исправленный find-классификатор — отдельный эксперимент

Независимый прототип был проверен против текущей версии `c57f7a1`
и **не был принят без исправлений**. У исходного прототипа были расхождения
`notfound` и `истекловремяожидания`: замена `\\s+` на пропуск
нулевого количества пробелов. Исправлены также длина-меняющие Unicode casefold и U+0345; шаблон
`timed?\\s*out` изначально допускает ноль пробелов и сохраняет
классификацию `timedout` как таймаут.

На Windows в `scripts/check_classify_equivalence.py` проверено
131 860 сообщений, в том числе 1 800 полученных из синтетического
`event_stream` через **ev['message']**, как это делает `generate()`.
Отдельно проверено 80 000 случайно составленных Unicode/whitespace
и adversarial-сообщений на Bazzite: 0 расхождений. Также выявлен
Unicode U+0345: при однобуквенном casefold он превращается из
не-словесного комбинируемого символа в словесный и меняет границы
слова без изменения длины строки. Для него, как и для Unicode-
преобразований с изменением длины, используется исходный regex.
Проверка всех Unicode code points на изменение категории словесности
после однобуквенного casefold выявила только U+0345. Это обширная
проверка, но не математическое доказательство для любого Unicode.

Полная генерация одного и того же синтетического .log на Windows:
50 000 событий / 64 665 884 исходных байта;
regex `generate()` 8.9788 с, исправленный find `generate()` 5.6081 с
(1.601x, снижение примерно на 37.5%). 68 HTML/JS/metadata-файлов,
72 200 385 байт, **побайтово идентичны**.
Команда: `python scripts/bench_classify_pipeline.py 50000`.

Не переносить проценты ускорения с синтетики на 642-МБ реальный файл
без измерения на одной и той же неизменной копии. SSH, analytics и
`js_json` в этом эксперименте не менялись.

### Эксперимент с общим casefold — не принят

Прототип передавал один `folded = message.casefold()` в `classify()`
и `extract_duration()`, не пытаясь использовать его для отличающегося
`raw[:24000]` в `recognize_error()`. При 50 000 событий и
64 665 884 исходных байтах базовый find-генератор: 5.6313 с,
совместный fold: 5.6122 с (1.003x), 68 идентичных выходных файлов.
Разница около 0.34% ниже надёжно интерпретируемой на одном прогоне.
Дополнительные аргументы и связь между функциями не оправданы:
**экспериментальные изменения отменены**, рабочий `generate()`
оставлен в прежнем виде.

### Дополнительная проверка find-based классификации

После найденных контрпримеров `notfound`, `истекловремяожидания` и
`таймßаут` введён строгий fallback на прежний regex для
изменяющих длину Unicode-преобразований. Также проверен символ U+0345:
он превращается из несловесного комбинируемого символа в букву при
`casefold()` без изменения длины, меняя границы `\b`. Для него
также используется прежний regex. В расширенном тесте
`scripts/check_classify_equivalence.py` пройдено 131 860
детерминированных сравнений с `c57f7a1`, включая сообщения из
`event_stream`, без несовпадений.

На Windows, одинаковый синтетический вход 50 000 событий /
64 665 884 байта: regex `generate()` 8.9223 с, find 5.6249 с
(1.586x). Побайтово одинаковые 68 файлов, 72 200 935 байт.
Дополнительный нагрузочный сценарий `bench_classify_pipeline.py 900 heavy`
с 900 длинными событиями (~59 036 290 байт, маркер ошибки ближе к
концу текста) дал regex 3.5116 с, find 1.0364 с (3.388x),
19 побайтово идентичных файлов, 59 253 714 байт.
Последний синтетический сценарий показывает потенциальный выигрыш
на тяжёлых длинных сообщениях, но не доказывает ту же пропорцию на
производственном журнале.

Отдельная идея переиспользовать `casefold(message)` между
`classify` и `extract_duration` испытана на 50 000 событий:
старый путь 5.6317 с, общий fold 5.6671 с (0.994x);
выходные файлы идентичны, но измеряемого выигрыша не было.
Поэтому отдельное изменение отклонено и в исходный код не принято.

Ускорение регулярных выражений классификации не меняет SSH-транспорт,
анализ ошибок в SQLite, повторную обработку исходных файлов для
общей выборки или формат отчёта.

## Этап 6. Телеметрия подфаз разбора и стоимость прогона сборки

Это только диагностическое инструментирование после v4.6.0; формат исходных
снимков, событий, raw-осколков, каталога и аналитической БД не изменён.
Выходные файлы должны побайтово совпадать с версией до инструментирования.

generate.parse progress/done: source_next_s — wall-время получения следующего
события из итератора, включающее чтение, multiline assembly, разбор заголовка,
создание словаря, а для combined — изменение даты/индекса/источника.
Для одиночного log из source_next_s вычтено время classify внутри read_input.
source_next_s НЕ измеряет физический дисковый I/O отдельно.

classify_s — wall-время classify; normalize_s — normalize;
duration_s — extract_duration; errors_s — recognize_error;
shard_write_s — сериализация JS и запись raw-осколков;
other_s — остаток на проверки, накопление и индексацию данных.
Таймеры включают накладные расходы инструментирования; суммы после округления
приблизительно равны elapsed_s. generate.catalog — отдельная фаза ПОСЛЕ parse;
не прибавлять её к other_s. thread_cpu_s — CPU-time ТЕКУЩЕГО потока на всю
generate.parse, не wall-время классификатора и не отдельная добавочная фаза.
Из разности elapsed_s и thread_cpu_s НЕЛЬЗЯ однозначно вывести физический
дисковый I/O: там также планировщик, блокировки и ожидания.

classify_calls — реально выполненные вызовы classify; classify_regex_fallback_events
— выбор исходного regex при Unicode с изменением длины casefold либо U+0345;
classify_literal_path_events = classify_calls - classify_regex_fallback_events.
Literal path включает результат «прочее». Внешние JSONL могут содержать
готовую категорию: для них classify_calls может быть меньше events.

combined.source source_active_s и combined.stream active_s — приближённое
wall-время работы генератора (повторный parse + augmentation) без основного
времени приостановки на yield; elapsed_s включает время потребителя и другие
накладные расходы. active_s частично совпадает с generate.parse.source_next_s,
СКЛАДЫВАТЬ ИХ НЕЛЬЗЯ. elapsed_s - active_s — не точный прогноз выигрыша от
однопроходного алгоритма.

build.summary фиксирует состав: selected, fresh_downloads, restore_downloads,
singles_new, singles_reused, skipped_identical, active_snapshots,
combined_status (0 — нет, 1 — переиспользован, 2 — создан), analytics_warning,
elapsed_s. Это отдельная сводка, не прибавлять к worker.perform_build_current.

Для реального теста используйте новый локально собранный EXE Phase 6;
опубликованный v4.6.0 пока не заменён. Не удаляйте рабочие журналы и кэш
ради проверки метрик без отдельной причины. Эксперимент агента на синтетике
3.377 → 3.450 с (+2.2%) проведён до уточнения метрик и не является
контрольным замером итогового инструментирования.

## Этап 7. Повторное использование casefold(message) в генераторе

Прототип после реального Phase 6 (26 сентября): на общей выборке
`extract_duration` = 20.817 с, `classify` = 18.980 с; на отдельных
больших событиях обе функции повторно преобразовывали весь `message`.

В обычном `generate()` для одного сообщения теперь единожды вызывается
`text.casefold()`, а результат передаётся `classify(..., folded=...)`
и `extract_duration(..., folded=...)`. Публичный интерфейс обеих
функций без `folded` неизменён. Самостоятельный `read_input()`
по-прежнему выдаёт категорию; только вызов из `generate()`
использует `defer_classify=True` и классифицирует в общем цикле.
Оригинальные raw, даты, fingerprints, категории и формат каталога
не изменены. `recognize_error(raw)` НЕ использует этот fold,
поскольку проверяет другие данные: первые 24 000 символов raw.

Phase 7 добавляет `fold_s` в progress/done `generate.parse`.
В этой версии `classify_s` и `duration_s` больше НЕ включают
затраты общего casefold, тогда как в Phase 6 эти затраты относились
к обеим функциям. Для честного сравнения использовать общее время
`generate.parse` / `report.generate`, а не разницу только двух
субфаз. В декомпозиции `fold_s` входит отдельно в `elapsed_s`.

Парный Windows benchmark, синтетика, фиксированный вход:

`python scripts/bench_phase7_shared_fold.py --events 50000 --mode mixed --repeat 2`
= 64 665 884 байта: Phase 6 5.7551 / 5.7310 с;
Phase 7 5.3604 / 5.3873 с; 68 идентичных файлов на каждом
повторе, по 72 207 035 байт на отчёт. Средний выигрыш ~6.4%.
Дополнительный heavy: `--events 900 --mode heavy --repeat 2`,
59 036 290 байт: 1.0721 / 0.9949 с против 0.9699 / 0.9683 с;
19 идентичных файлов на повторе. Это синтетические, не
производственные измерения; коэффициент нельзя переносить на АКУЗ
без реального запуска. Baseline Phase 6: `96b0ea5`.

SSH, SQLite, combined повторный event_stream и caching не менялись.
Опубликованный v4.6.0 не заменён этим экспериментом; для проверки
нужен отдельный диагностический EXE, собранный из точного Phase 7 SHA.

## ???? 8. ??????????? ?????? recognize_error (????? Phase 7)

????? ????????? ?????? Phase 7 (2026-09-26 10:08?10:14) ????? ?????:
\`errors_s=20.280\`, \`classify_s=12.163\`, \`duration_s=14.414\`,
\`fold_s=6.624\`, \`shard_write_s=15.023\`, \`generate.parse=102.025\` ??????.
??? wall-????? ????????? ???????, ?? ?????????? ????????? ?????.
?????? ???????????? ?? \`raw\`, ? ?? ?? \`message\`; ????????? ???? ???
?????????????? ????????????? ????????????? ??????.

????????? ?????? ???????? ???????? \`generate.parse progress/done\`:
- \`error_recognize_calls\`: ??????????? ?????? \`recognize_error\`.
- \`error_probe_chars\`: ???????? ????????????? ??????? ????????? ????????,
  ?? ????? 24 000 ???????? ?? ???????.
- \`error_truncated_events\`: ???????? raw ??????? 24 000 ????????.
- \`error_ascii_probe_events\`: ??????? ??????? ASCII.
- \`error_exception_events\`: ????????? ???????????? ????? EXCEPTION.
- \`error_serial_events\`: ????????? SERIAL ??? ?????????? EXCEPTION.
- \`error_firstline_events\`: ?????????? ?????? ????? ?????? ?????? ??????.
- \`error_no_match_events\`: ????????????? ??????? None.
????????? ?????? ???????? ?????? ????? \`error_recognize_calls\`.
?? raw, ?? ???????, ?? source path ? ??????????????? ???? ?? ?????????.
????? ????????? \`diagnostics\` ???????????; ?????? ????? ??? ??????????
? ???????????????? ??????????????? ??????? \`recognize_error\` ????????.

?????????? ????? ?? ????????????????:
- ???????? ?????? ASCII Unicode-replace ? \`recognize_error\`:
  3 300 ????????????? heavy/Unicode ???????, ?????? ?????? ??????,
  ?????????? ??????????? ?????????? (~0.173?0.180 ?), ?? ?????????????.
- ??????? ???????? ???????? JS replace ?? \`str.translate\`:
  ??? ???????????????? ???????? ?? plain ? ? ????????? ???????
  ?????????? ?? XML-??????; ?? ?????????????.

????????? ??????? ??????????????? ?????????, ?????? Windows benchmark:
\`python scripts/bench_phase8_error_probe.py --events 50000 --mode mixed --repeats 2\`.
64 665 884 ??????? ?????; Phase 7 = 5.3932/5.4106 ?;
Phase 8 = 5.4481/5.5347 ?, ???????? 68 ?????? ????????? ?????????.
Heavy 900 ??????? / 59 036 290 ????: Phase 7 = 0.9403/0.9792 ?,
Phase 8 = 0.9686/0.9743 ?, 19 ?????? ????????? ?????????.
Phase 8 ? ????????????? ??????, ?? ?????????? ?????????:
?? ?????????? ? ?????????? ???????? ????????? ? ??????? ??????.
??????????????? ???????, SQLite, SSH-???????? ? combined ?? ????????.


## Phase 9.0a — isolated Windows baseline harness (2026-09-26)

Parent checkpoint: `fe733a8e79f49a1f549f42112513dc1fb0a5cdd8`.
Only benchmark/test/docs files changed; the generator, parser, store, analytics,
SSH transfer and existing cache layout were not modified.

Commands, from the working repository:
- `python scripts/bench_phase9_baseline.py --events 600 --chars 2048 --repeat 3 --output diagnostics/phase9_synthetic_baseline_600.json`
- `python scripts/bench_phase9_errors.py --calls 600 --repeat 3 --output diagnostics/phase9_errors_600.json`
Outputs are sanitized numeric metrics and hashes under the ignored diagnostics/.
Sources are generated in disposable OS temp directories; no production files,
ConnectConf.cfg or existing downloads/reports/cache are opened or deleted.

Synthetic source sizes, with 600/600/601 parsed events:
1,266,480 / 1,267,683 / 1,266,523 bytes. Three exact SHA-256 values
are recorded in the local benchmark JSON, not substituted by size matches.
Three fresh builds: wall 1.068624 / 1.066880 / 1.067058 seconds;
CPU 0.953125 / 0.921875 / 0.906250 seconds;
sampled peak tree Working Set 43,352,064 / 41,320,448 / 44,539,904 bytes.
Warm builds: wall 0.043947 / 0.044442 / 0.043220 seconds.
These are small Windows/Python synthetic measurements, NOT an AKUZ production
speedup claim and NOT a portable EXE measurement.

Fresh vs warm: report bytes/hashes, normalized inventory and SQLite table
contents equal. Combined-only and single-only misses regenerated the same
deterministic report content; mixed cache kept the copied-byte source from a
different path separate (2,401 single events across four source files).
For independently rebuilt combined reports, current code embeds a random
temporary `akuz-v4-merge-*.jsonl` name in catalog meta.source; comparison
normalizes ONLY that string. Provenance comparison excludes ONLY generated
clock; all source identity, host/path and SHA fields remain checked.
Raw-shard files and all other catalog bytes remain strict byte comparisons.

The Windows sampler uses OS PeakWorkingSetSize per process, samples current
PrivateUsage and simultaneously present process-tree sums at 20-ms intervals,
and records PeakPagefileUsage separately; sampled peaks may miss short spikes.
It enumerates descendants to cover a PyInstaller launcher/worker, but this
run measured a Python child only. tracemalloc is not mislabeled as RSS.

Outstanding Phase 9.0 gates: portable EXE comparison, real snapshot SHA A/B
with no production raw in diagnostics, additional cache/fault/Unicode matrix,
and an alternating old/new 3+-pair experiment after there is a candidate.
No Phase 9.1-derived contract or Phase 9.2/9.3 architecture is approved here.

Synthetic error-branch profile (600 calls per band, three repetitions):
16 branch/size buckets, 48 numeric samples; truncated raw is probed over
exactly 24,000 characters. For truncated synthetic entries, 600-call wall
seconds: no_match 0.0341/0.0329/0.0338, exception 0.6670/0.6606/0.6660,
serial 0.2737/0.2728/0.2728, firstline 0.6845/0.6856/0.6792.
These are branch-conditioned synthetic costs, not measured production shares.

## Phase 9.0b — frozen child-process memory smoke (2026-09-26)

Parent checkpoint: 041471e3308bf7b4bc3c60b60360203b4ea02ac9.
Command: python scripts/bench_phase9_portable.py
dist/phase8/AKUZLogExplorer-windows-x64.zip
--output diagnostics/phase9_portable_phase8.json
The script verifies the archive allowlist, BUILD_INFO version and build SHA,
hashes both EXE and ZIP, and invokes existing smoke_portable.py. The smoke
extracts into an OS temporary folder and tests a one-event synthetic local
log; it does not run the source EXE in its working cache directory.

PASS: local diagnostic Phase 8 portable v4.6.0, build SHA
1ce50a1dc46b1fd6e822a712919b39c9b921de7d. Wall 4.557517 s includes
extracting ZIP, two frozen launches/self-test, localhost and parsing one event.
103 observed frozen-child samples, 20-ms polling. Peak sampled simultaneous
child Working Set 53,309,440 bytes, private 25,763,840 bytes; host Python
smoke monitor excluded. Individual OS per-PID high-water marks are also
recorded in ignored diagnostics, and can be larger than sampled peaks.

This is NOT equivalent-workload Python-vs-EXE performance A/B, NOT 956-MB
snapshot workload, and not a result for a newly rebuilt 9.0 EXE. The
production report code has not changed since this diagnostic Phase 8 build;
the documented Phase 9.0a/b scripts are not packaged into that older EXE.
No published Release, installed reports/cache, secrets or user .log touched.

## Phase 9.0c — real local snapshot control, DBA-008D (2026-09-26)

Parent HEAD: f5c77a92ce468dd0e588b6ecaa69284a6ab88e10.
This was an isolated Python 3.11.9 test of three existing, SHA-verified,
read-only AKUZ .log snapshots; it was NOT a portable or SSH measurement.
Source bytes: 644,034,993 + 140,361,291 + 171,378,567 = 955,774,851.
Exact input SHA-256 values are saved only in ignored local
`diagnostics/phase9_real_control_private.json` (not committed).
The large snapshot differs from the Phase 8 reference (644,567,384 bytes,
225,539 events): no direct before/after speedup claim is supported.

Two independent fresh builds from the same verified snapshots (no
production-code changes): wall 268.099 / 263.781 s, CPU 257.141 /
253.609 s. Corresponding warm/no-op builds: wall 2.348 / 2.352 s.
Second fresh build: three single reports with 221,082 / 222,855 /
211,117 events, combined 655,054 events / 889,531 physical lines,
656 shards. Individual reports together contain 655,054 events.
All four report.generate wall times: 22.662 / 60.015 / 23.499 /
108.604 s (the first three correspond to 140,361,291 / 644,034,993 /
171,378,567 bytes). Combined generate.parse 104.569 s; its parts:
source_next 13.829, classify 12.319, normalize 15.297,
errors 21.506, duration 14.457, fold 6.674,
shard_write 14.018, other 6.470 s. Analytics.refresh 46.529 s.
Whole perform_build_current 263.780 s according to internal trace.
Output disk space in the disposable workspace: reports 2,273,015,590,
cache 32,668,666, copied downloads 955,774,851 and data 59,423,535 bytes.

Memory correction: the original external process-tree sampler measured
post-build hashing and SQLite verification as well as actual building;
its 1.29-GiB sampled Working Set must NOT be called the build peak.
A separate, in-worker 20-ms sampler strictly around perform_build_current
recorded 5,769 samples, sampled Working Set peak 1,199,079,424 bytes,
sampled PrivateUsage peak 1,190,248,448 bytes, and Windows OS
PeakWorkingSetSize 1,360,367,616 bytes (about 1,297.3 MiB).
OS PeakPagefileUsage was 1,624,977,408 bytes; it is NOT sampled RSS.
Warm-only Windows OS PeakWorkingSetSize was 36,605,952 bytes
(about 34.9 MiB). The OS peak can exceed the sampled peak because
short spikes occur between polls.

PASS within each fresh->warm pair: strict deterministic report-file hashes
(with ONLY the disposable combined meta.source normalized), stable
provenance identity after excluding generated clock, normalized inventory,
SQLite logical table hashes and analytics export hashes. Both independent
fresh runs matched in normalized report and inventory digests. SQLite
and exported analytics hashes DIFFERED across independent fresh runs;
the precise cause remains OPEN (report IDs / transient source names
are possible but not yet established). Do not claim cross-fresh
byte-equivalence for analytics until isolated and tested.

Original snapshot SHA-256 values matched before and after; inputs never
written, and the isolated temporary reports/cache/downloads were removed.
No raw event text, production path, credentials or medical content is
committed. GitHub Release unchanged. Phase 9.0 remains IN PROGRESS:
portable full-workload comparison, fault matrix, and alternating old/new
3+-pair A/B require a distinct subsequent evidence gate.

## Phase 9.0d / 9.1 — fresh SSH control and derived-event refactor (2026-09-26)

SSH benchmark harness commit: a08bc35de49fbb39e8cbad7abaa8e3a3b4602884.
No network credentials or raw event content are stored by the benchmark.
`ConnectConf.cfg` is read in memory; its local_dest is overridden to a
new disposable workspace for each run. Every run lists/fetches exactly
`20260923_server.log`, `20260924_server.log`, `20260925_server.log` via SSH.
The Windows sqlite3 fingerprint connection is explicitly closed before
removing the test data. All three snapshot SHA-256 values are stored only
in ignored private diagnostics, not in Git.

Original unmodified application, fresh-SSH reference (a08bc35):
input bytes 171,378,567 + 140,361,291 + 644,567,384 = 956,307,242.
Events: 211,117 + 221,082 + 225,539 = 657,738; combined 657,738.
Fresh wall 387.798 s, process CPU 275.219 s; no-op warm 3.389 s.
Combined generate.parse 104.784 s; analytics.refresh 46.604 s.
Windows OS peak working set 1,385,324,544 bytes; OS peak pagefile
1,639,493,632 bytes. All three sources static throughout their download,
their SHA verified; disposable workspace removed successfully.

Phase 9.1 refactor-only, `akuz_derived.py`: `DerivedEvent` calculates
category, normalized pattern, duration, error fingerprint, replacement
count and headline with optional numeric diagnostics; report-specific
IDs, dates, first occurrence, per-source/line offsets and chunk location
stay in the existing generator. Preserve older injectable callables and
`event_source`/JSONL/CLI interfaces. No tee or sidecar is enabled.
75 Python tests PASS, 8 actual Node UI tests PASS, classifier equivalence
131,860 cases PASS; individual report, combined JSONL/stream and
event_stream equivalence PASS.

Phase 9.1 fresh SSH candidate on EXACT same snapshot SHAs:
fresh wall 374.542 s, process CPU 280.688 s, warm 3.456 s;
combined generate.parse 107.641 s; analytics.refresh 46.623 s.
Windows OS peak working set 1,385,385,984 bytes.
All 4 deterministic report file manifests MATCH exactly against reference,
normalizing only scratch meta.source and provenance.generated as
previously specified; normalized inventory and event/line/shard counters
match. SQL table digests: errors, meta, source_dates and source_files MATCH.
Only SQLite `indexed` differs: its stamp includes fresh catalog mtime_ns.
533 of 534 direct JS export hashes differ across independent runs due to
ephemeral report IDs, even though logically indexed error records match;
a stricter export semantic normalizer remains an OPEN test-infrastructure
improvement, not permission to ignore content mismatches.

Caution: 13.256 s lower end-to-end wall time CANNOT be called a derive
speedup: aggregate SSH transfer was faster in candidate, whereas
combined.generate.parse was 2.857 s slower in this one pair.
No speedup claimed for Phase 9.1; it is a refactor-only prerequisite
for sharing derived results. Source files/user cache/reports untouched;
new disposable workspace and new SSH transfer per significant run.
Release v4.6.0 unchanged. Architecture for Phase 9.2/9.3 unchosen.

Additional Phase 9.1 synthetic 3-repeat full cache matrix PASS:
fresh-all, warm-no-op, combined-only-miss, single-only-miss and
mixed-cache-distinct-source; fresh 1.100303 / 1.086495 / 1.077086 s.
Real remote mixed-cache/fault and three-pair A/B gates remain OPEN.

## Phase 9.3a — ephemeral spool prototype

Real SSH controlled runs: baseline 387.798s, prototype 322.783s.
Combined parse 104.784s -> 38.829s on same three source hashes.
Temporary spool 253634909 bytes; cleaned after each build.
Real reports and inventory matched the reference.
Repeated optimized fresh runs: 324.547s, 321.474s, 322.783s.
Baseline CPU 275.219s; optimized CPU 222.641s in final run.
SSH transfer variation affects whole-build wall measurements.
Windows OS peak Working Set: 1385324544 B baseline; 1450266624 B spool.
The extra transient space and memory must be accounted for in adoption.
Spool SHA-256 is verified before replay; synthetic corruption and retry
and disk-full tests PASS. 80 Python and 8 Node tests PASS.
Cross-fresh SQLite indexed.stamp and JS export report IDs remain volatile.
Experimental feature is opt-in; release artifact not changed.

## Phase 9.3b — normal application default on real fresh SSH (2026-09-26)

Step 1: recovered HEAD 355a739 and existing experimental in-progress edits;
did not overwrite or remove them. Target selection was EXACT remote
20260923_server.log / 20260924_server.log / 20260925_server.log through
the in-memory ConnectConf.cfg adapter. Original production directories and
credentials were never used as a test destination. Isolated disposable
workspace is deleted after the run, including its downloads/reports/cache.

Step 2: ran `python scripts/bench_phase9_ssh.py --default-spool
--reference diagnostics/phase9_ssh_baseline_private.json`.
PASS on the same exact three original SSH snapshot SHA-256 values,
total 956,307,242 input bytes. Fresh wall 321.276 s and CPU 223.906 s;
normal-path combined generate.parse 38.999 s, report.generate 43.041 s,
analytics.refresh 46.356 s. Warm no-op wall 3.325 s, reused=True.
Windows OS PeakWorkingSetSize 1,448,513,536 bytes;
PeakPagefileUsage 1,703,186,432 bytes (not a sampled RSS reading).
Three fresh single reports: 211,117 / 221,082 / 225,539 events.
Combined: 657,738 events, 892,423 physical lines, 658 shards.
Source snapshot integrity, normalized deterministic hashes of all four
reports, inventory SHA and report event counts MATCH baseline.
Independent-run SQLite indexed freshness and raw JS export report-ID
differences remain unresolved for full cross-fresh export equivalence.
Real download/parse telemetry was retained ONLY in ignored diagnostics;
no raw journal, source path, server name or secret was committed.

Step 3: acceptance is limited to the default-Python SSH path and
deterministic report/inventory correctness. The existing published
portable v4.6.0 EXE was NOT rebuilt; full Python/EXE parity and
3+ alternated A/B remain OPEN. Overall wall delta includes SSH variance;
the combined parse 104.784 -> 38.999 s comparison is stage-specific.
Step 4: regression suite on the default-on implementation: 81 Python
unittest PASS; 8 explicit Node .cjs UI tests PASS; report byte-equivalence,
legacy combined equivalence, three event-stream differential seeds and
131,860 classifier equivalence checks PASS. `git diff --check` PASS.
Rollback for the Python application: set AKUZ_PHASE9_DERIVED_SPOOL=0
before launch (or call the API with use_derived_spool=False). This does
not alter the already-published standalone EXE.

## Phase 9.0e — exact real SSH analytics semantic equality gate (2026-09-26)

Step 1: introduced scripts/phase9_semantic.py, benchmark-only. SQL's
indexed stamp is `catalog_size:catalog_mtime_ns:provenance_sha256`;
normalize ONLY the middle mtime_ns, preserving size and provenance hash.
Replace generated report IDs in all analytics detail items with their
stable inventory keys; sort the complete normalized rows to remove the
non-semantic SQL tie order by random report IDs. Do not drop any error
fields. Overview arrays retain their original order and full content.
The fingerprints never save real error text, identifiers, or file paths.

Step 2: added synthetic differential tests: pristine Python baseline
vs optimized spool must match every SQL table and analytics export.
Mutating a real field (clock) or catalog size must change its semantic
fingerprint; mutating only the catalog mtime must not. 82 Python tests
PASS after introduction (other previous equivalence/UI gates retained).

Step 3: two entirely NEW SSH reads of exactly the same immutable
20260923/20260924/20260925 source SHA-256 values, no existing local
downloads or cached reports used. Test workspace cleaned after EACH
run. The Python benchmark's control (use_derived_spool=False): wall
378.788s, CPU 280.000s, warm 3.461s, combined parse 107.174s.
Normal default spool path: wall 368.348s, CPU 223.734s, warm 3.431s,
combined parse 39.112s, 1,450,614,784 B Windows OS peak Working Set.
Remote transfer of the largest file: baseline 72.141s vs optimized
116.317s. Whole-build wall therefore conflates variable network speed;
compare combined parse and CPU as well as fresh wall.

Step 4: PASS strict normalized four report manifests, report counts,
inventory hash, semantic digests of EVERY SQLite table, AND semantic
digests of ALL 534 analytics JS exports. Original raw JS hashes differ
due to ephemeral report UUIDs and SQL indexed stamp differs due to
catalog mtime; this is NOT raw byte-equivalence of analytics artifacts.
All test snapshots have unchanged SHA; both isolated workspaces were
removed. The two private sanitized JSON summaries are ignored:
diagnostics/phase9_ssh_private.json (non-spool control) and
diagnostics/phase9_ssh_spool_private.json (normal default).

Gate remaining OPEN: frozen Windows portable EXE full-workload parity;
3+ alternating paired network-noise-controlled A/B; actual on-host
mixed-cache and failure-mode matrix; Phase 9.2 in-flight tee comparison.
Do not interpret this analytics normalizer as permission to ignore
unexplained semantic changes.

## Phase 9 test-harness concurrency safety (2026-09-26)

Recovered main at 8ca38ae (origin/main equal), pre-existing untracked
`scripts/bench_phase9_ssh_cache.py` retained. During a second cache-matrix
invocation, the old global `scrub_stale_temp` tried to remove the disposable
workspace of an ALREADY RUNNING separate benchmark and failed on an open
20260925 `.part` file (WinError 32). This means that interrupted concurrent
run cannot be accepted as clean evidence; other files may have been removed
before Windows refused the deletion. No production downloads/reports/cache
were targeted. Replaced proactive cross-run deletion with per-invocation
`TemporaryDirectory` cleanup only; synthetic guard preserves an active
marker and in-progress part file. This is safety, not a speed optimization.
Never infer abandoned state merely from a disposable marker.

## Phase 9.0f — real SSH partial-cache matrix (2026-09-26)

Step 1: benchmark-only `scripts/bench_phase9_ssh_cache.py` uses the
existing ConnectConf.cfg read in memory, forcibly overrides local_dest,
selects exactly 23/24/25 Sep .log, and creates a fresh disposable
workspace. A prior concurrent test exposed global cleanup interference;
commit 4ece931 removed cross-invocation deletion. This new test started
after that correction. No user cache/reports/downloads were removed.

Step 2: new full SSH fetch + optimized fresh build: 342.961 s wall;
956,307,242 source bytes, 657,738 combined events, 892,423 lines,
658 shards and 39.686 s combined parse. Downloaded source SHA/size
metadata matches EXACTLY both the original non-spool baseline (a08bc35)
and previous spool reference. The private diagnostic JSON stores SHA
hashes/counters only, not raw medical or log content.

Step 3: deliberately invalidate reports ONLY inside this test workspace,
run the normal application default, and compare after EACH scenario:

- combined-only miss: 156.844 s wall / 152.703 s CPU; 3 singles reused, combined new;
  normalized report/inventory/ALL semantic SQL and 534 JS exports PASS.
- single-only miss: 74.300 s wall / 69.969 s CPU; 1 single new, combined reused;
  1 temporary derived spool created (unused, then removed); same PASS.
- mixed single + combined miss: 171.026 s wall / 165.469 s CPU; 1 single + combined new;
  only the new single contributes a spool; both reused singles derive
  normally during combined; same PASS.
- final warm: 3.530 s wall / 2.406 s CPU; no reports recreated; same PASS.

Step 4: all four modes PASS inventory, full normalized deterministic
report manifests, all SQLite tables and all 534 semantic JS exports.
No new SSH transfer was required during intentional warm/cache-miss
substeps; a new SHA-verified SSH transfer happens at the beginning of
EACH independent benchmark invocation. Its within-run partial-cache
transitions deliberately preserve downloads so cache behavior is tested.
The disposable workspace and spools were removed after successful test.
Summary: ignored diagnostics/phase9_ssh_cache_matrix_private.json.

Limits: combined-only miss necessarily rederives all 657,738 events;
that is an expected no-persistent-sidecar tradeoff, not a proof of a
cache bug. Single-only miss presently writes an unnecessary spool when
combined is already cached. No Phase 9.2/9.3 architectural decision
follows automatically from this test. Portable and A/B gates remain OPEN.

## Phase 9.0g — independent Clean APIs Fable review of frozen parity gate (2026-09-26)

Recovery checkpoint: main and origin/main both at
84da1d1e980a5f1d2311f590656d9ede26dbaba9. The pre-existing untracked
`scripts/bench_phase9_frozen_real.py` was inspected read-only and was NOT
overwritten, staged or run on real snapshots in this review. Existing
diagnostic ZIP's BUILD_INFO.json commit matches current HEAD; published GitHub
Release was not modified.

External review: Clean APIs returned model label `claude-fable-5.1`, HTTP 200,
for two completed bounded review passes of sanitized public benchmark code.
Model identity/upstream routing is provider-reported, not independently
verified. Only benchmark source and a narrow `akuz_app.py` endpoint excerpt
were sent as prompt content; no real logs, raw events, SSH configuration,
cache, medical content, or credentials were included in prompts.
Temporary API request payloads on Windows were deleted after each request.
The third-party review is a hypothesis source, NOT accepted evidence of a
test pass or code defect without independent verification.

Verified findings:
- `wait_for(opener, base, busy=False)` immediately propagates a transient
  `urllib.error.URLError`, whereas startup `wait_for(..., busy=None)`
  retries it. An isolated mock with one transient error and then
  `{"busy":false,"error":""}` observed 1 attempt/error versus
  2 attempts/success, respectively. Retry only transient transport errors;
  preserve early failure on HTTP rejection, actual application error and
  deadline. Do not require seeing `busy=True` after HTTP 202.
- Full-workload memory numbers are not yet like-for-like: Python
  `monitor(os.getpid())` includes the benchmark harness while frozen
  `monitor(proc.pid)` covers the standalone process tree. Both are sampled
  at 30 ms and discard the OS high-water fields exposed by
  `scripts/phase9_memory.py`; `bench_phase9_portable.py` uses separate
  child-scoped aggregation at 20 ms. Frozen CPU time is also not measured,
  unlike Python's `process_time()`. The resulting pair cannot establish
  equivalent-workload CPU / peak-memory parity without narrower labeling
  or a process-isolated Python worker with matched instrumentation.
- The frozen crash/timeout path suppresses child stdout/stderr and lacks
  an explicit `proc.poll()` early-exit check in the status wait loop. This
  harms diagnosis of a failed EXE, though no such failure was observed
  during this review. PATH truncation and taskkill behavior are hypotheses,
  not independently demonstrated defects.

Rejected model overclaims after verification and a second review:
- No claimed pre-admission `busy=False` race: the server sets `busy=True`
  under `state.lock` before starting the worker and returning HTTP 202.
  Requiring a separately observed `busy=True` could miss a fast job.
- `os.link` by itself does not mutate sources, and a failed
  `TemporaryDirectory` cleanup raises before the script prints its
  success marker; no demonstrated false `WORKSPACE_CLEANED True`.
- The diagnostic ZIP SHA provenance/HEAD comparison is purposeful; the
  local ZIP BUILD_INFO commit matched HEAD at the review checkpoint.
  Published release is a separate artifact.
- `signature()` already incorporates both `semantic_sql()` and
  `semantic_exports()` (534 exports in prior real gates); claims of their
  omission were false.

Local review verification on DBA-008D:
- `ast.parse` of the untracked frozen benchmark: PASS.
- `python -B -m unittest discover -s tests -q`: 84 tests, PASS
  (25.701 seconds).
- Isolated mocked `wait_for` transport-error check: reproduced behavior
  listed above; no network, SSH, user workspace or original logs involved.
- Local Git status remained clean except for the same pre-existing untracked
  benchmark file. No real portable parity run, no 3+ alternating A/B and
  no real failure-injection gate were performed or accepted in this review.

Next limited workstream: preserve ownership of the existing untracked
benchmark, add targeted transport/retry tests, make equivalent-workload
Python/frozen memory and CPU measurement scopes explicit, then run the
full SHA-gated 23/24/25 September portable parity test in a separate
disposable workspace. Do not declare Phase 9.0 or the portable gate complete
from this review alone.

## Phase 9.0h — bounded frozen HTTP status retry correction (2026-09-26)

Recovered `main == origin/main` at `bc6996ab0950d2721d88169e0aea8c591fa14871`.
Prior untracked `scripts/bench_phase9_frozen_real.py` had SHA-256
`58a579ccdf75c8f87f3a9f83be277c85c4194ea802de9af1d375eeed458956b4`.
Preserved exact bytes under ignored
`diagnostics/phase9_review_work/bench_phase9_frozen_real.before.py`
and verified backup SHA before a bounded in-place change. No other active
benchmark process was observed when the file was inspected.

Change: `wait_for()` now retries temporary `OSError/URLError` regardless
of startup or build-completion mode, while HTTPError is re-raised without
retrying. Existing application-level `status.error` still raises and HTTP 202
admission/idle-only completion protocol is unchanged; in particular it does
NOT wait to observe a possibly already-finished `busy=True` state.
This is a benchmark harness reliability fix, not a parser or cache change.

Evidence: four synthetic tests in
`tests/test_phase9_frozen_status.py` passed, covering transient completion
poll, transient startup, non-retry of HTTP 403 and application worker failure.
Full `python -B -m unittest discover -s tests -q`: 88 PASS (25.339 s).
`git diff --check`: PASS. Tests ran without SSH or real production logs.
The old direct mock reproduction showed busy=None 2 attempts/success and
busy=False 1 attempt/URLError; new dedicated tests assert the corrected path.

Independent model review attempt of the small patch via Clean APIs returned
HTTP 502, so there is NO completed Fable review or approval for this workstream.
Do not misreport the previous independent broad Phase 9.0g review as review of
this specific patch. The patch/test source only was prepared for the request;
no raw log, ConnectConf.cfg, cache, medical data or API credential was
included in the model prompt. The temporary request payload was deleted.

Scope of acceptance: syntax and targeted/full Python regressions only.
The real frozen 23/24/25 September parity gate, like-for-like Python vs EXE
memory/CPU, failure matrix and 3+ alternated A/B remain OPEN.
The locally present diagnostic ZIP has BUILD_INFO SHA
`84da1d1e980a5f1d2311f590656d9ede26dbaba9`; it must be rebuilt
separately from an accepted implementation HEAD before running strict
frozen-parity main() after this commit. GitHub Release unchanged.

## Phase 9.0i — real Python/frozen parity run; Windows EXE cleanup gate (2026-09-26)

Starting checkpoint: main and origin/main at
`1e71bbebf1f685b12c41d2fc4cf173dbbdd8f10f`; clean working tree
at benchmark launch. Windows diagnostic ZIP was rebuilt locally using
`python -B scripts/build_portable.py` (exit 0); BUILD_INFO.json matched HEAD.
ZIP SHA-256:
`243c769b9db6871a9b5c1316368bdf5f0ff805f757b158d383375c9a92f3e55f`.
Executable SHA-256:
`d6a7ceb8d2aac8449957d89f89148fc96a9ed60887811ae28ba088b761ec0f15`.
No GitHub Release mutation.

One NEW SSH read of exactly the original 23/24/25 September logs passed
`REAL_SNAPSHOT_SHA_GATE_PASS` (reference date, size, SHA and inactive
source checks in isolated workspace; no user downloads/reports/cache reused).
Python local fresh and warm build passed, fresh wall = 220.610 s. Windows
portable EXE local fresh and warm build passed, fresh wall = 204.275 s.
After both builds, `TemporaryDirectory.__exit__` failed with
`PermissionError [WinError 5] Access is denied` on the disposable extracted
`AKUZLogExplorer.exe`, so this invocation exited 1 and did NOT write
`diagnostics/phase9_frozen_real_private.json`. Do NOT report full benchmark
PASS, proper automatic cleanup or accepted CPU/RSS parity from this run.

Before removing any failed-test files, a separate read-only check of the
remaining two report workspaces re-ran `signature()` and found:
`inventory=True, reports=True, sql=True, exports=True,
single_events=True`; single event count 657,738. This establishes
deterministic report/inventory and semantic SQL/analytics equivalence for
this exact tested pair only. Analytics files may still differ in raw
generated UUID bytes as documented in 9.0e; this is not strict raw export
byte equality. A single Python vs EXE wall pair is NOT a speedup estimate.
Peak memory scope and frozen CPU parity remain OPEN.

Observed diagnostic EXE had ordinary Archive attributes, not ReadOnly.
The test's new PyInstaller process tree was no longer running after exit;
older unrelated `dist/phase8` processes were not touched. The failed
workspace had been partially traversed by `TemporaryDirectory`, including
deletion of its ownership marker. It was removed ONLY after verifying its
exact run-specific name, known Python+frozen inventory paths, snapshot
directory, and absence of a live test EXE using its path. No global
cross-workspace cleanup or removal of an active workspace.

Bounded harness correction: replace `TemporaryDirectory` automatic
single-attempt cleanup with an explicit owned `mkdtemp` workspace, a
validated disposable marker/parent/prefix, and a 30-attempt x 0.5-second
retry only for Windows WinError 5/32 on the SAME exact workspace.
Validate ownership once before retry because partial rmtree can remove
the marker. Unexpected permission errors still fail. Do not overwrite
the user's downloads/reports/cache or sweep other benchmark invocations.

Synthetic cleanup tests (no SSH, medical/raw logs or production data):
- removes one owned disposable workspace, preserves another active marker;
- rejects a workspace without its marker;
- retries after synthetic partial cleanup removes the marker;
- does not mask unknown ACL failures.
Four targeted tests PASS (0.049 s); full
`python -B -m unittest discover -s tests -q`: 92 PASS (25.598 s).
Eight Node UI tests PASS. `git diff --check` PASS.

Gate still OPEN until this fix is committed, the local diagnostic portable
is rebuilt to match that commit, an entirely new SHA-gated 23/24/25 SSH
transfer is performed into its own workspace and the script terminates
successfully with a private result JSON AND confirmed cleanup. Do not
reuse prior downloaded test snapshots as the fresh-SSH gate.

Additional synthetic frozen integration smoke (same diagnostic ZIP before
this documentation/cleanup commit): independently created three tiny
synthetic AKUZ .log files in a new owned `phase9_frozen_*` directory;
`frozen_build` completed fresh+warm, reported 16 single events and
nonzero memory samples. The new exact-workspace cleanup completed and the
temporary directory no longer exists. Exit 0. No remote SSH, production
data, existing user cache or published Release touched.

## Phase 9.0j — successful fresh real SSH Python/frozen content + cleanup gate (2026-09-26)

Authoritative checkpoint and archive were aligned after Phase 9.0i:
`main == origin/main == c378e2e0f00daf5edde8506400846a65b6966509`
at run start, no dirty tracked files. The local diagnostic ZIP was
rebuilt (exit 0, BUILD_INFO commit matches HEAD), ZIP SHA-256
`9ae29f13623210de943c340d72cbdc7726c1f6ba050264725072a5e7d119338c`.
No published GitHub Release changed.

The full frozen benchmark started a NEW isolated SSH transfer of exactly
23, 24, 25 September 2026 AKUZ .log snapshots; original reference
SHA-256 values were verified inside the ignored private diagnostic, along
with date, size, inactive snapshot and second local SHA check.
Input bytes: 171,378,567 + 140,361,291 + 644,567,384 = 956,307,242.
No working-user report/download/cache directory was a benchmark target.
The runner's own diagnostic output contains no raw medical/log content.

Python local fresh+warm PASS, fresh wall 219.063 s; Python process
`process_time()` 219.844 s. Frozen Windows local fresh+warm PASS,
fresh wall 200.983 s. One pair does NOT establish stable speedup.
Both independently built report workspaces passed complete signature
comparison: inventory, normalized deterministic four report manifests,
semantic SQLite tables, all semantically normalized analytics exports
(534 in prior verified real baseline), and 657,738 single events.
Do not claim independent fresh runs have raw byte-identical analytics JS
or SQLite indexed.stamp: generated report UUID and catalog mtime are the
previously isolated volatile fields, not a content waiver.

Crucially, the full runner exited 0, wrote
`diagnostics/phase9_frozen_real_private.json` (ignored; verified present)
and completed the owned disposable workspace cleanup. The JSON records
`raw_payload_saved=false`, `disposable_workspace_cleaned=true` and
all five content checks `true`. This closes this SINGLE exact
real-snapshot Python/frozen CONTENT + warm-cache + cleanup comparison.
It does NOT close Phase 9.0 as a whole.

Memory measurements captured for diagnostic context ONLY:
Python monitor: 1,149,464,576 B sampled tree Working Set peak,
1,129,725,952 B sampled tree Private peak (1,024 samples).
Frozen monitor: 1,477,435,392 B sampled tree Working Set peak,
1,745,600,512 B sampled tree Private peak (4,300 samples).
These are not strict like-for-like Python/frozen memory metrics:
Python monitor samples its in-process benchmark PID; frozen monitors
the external PyInstaller process tree. OS per-PID high water and frozen
CPU were not recorded by this runner. They are NOT evidence for an
accepted RSS or CPU parity/speedup claim.

The earlier failed Phase 9.0i first real run had equivalent content but
Windows WinError 5 cleanup failure; this distinct post-correction run
demonstrates that bounded owned-workspace cleanup worked on a full
956-MB real snapshot workload. Other preexisting
`diagnostics/phase9_frozen_*` folders must not be scrubbed globally.

Still OPEN: process-isolated like-for-like memory and CPU benchmark,
3+ alternating baseline/spool A/B with network phase separation,
remaining active-file/failure matrix and architecture comparison to
Phase 9.2. No further parser/cache optimization was performed in this
instrumentation-and-verification step.

## Phase 9.0k — isolate Python benchmark process and align memory-sampling scope (2026-09-26)

Recovered `main == origin/main == 9ae3e44e383bcefd105644af7cc3160b3373bf7d`
after accepted full real frozen content/cleanup gate. This is a separate
benchmark instrumentation change; no application parser, derived spool,
download, analytics or cache implementation was changed.

The previous `python_build():monitor(os.getpid())` included the Python
benchmark controller in measured memory, while the frozen run monitored
the separate PyInstaller process tree. Preserved the exact pre-change
benchmark source in an ignored `diagnostics/phase9_review_work/` backup
before bounded edits.

`python_build_isolated()` now starts the ordinary Python report build as
a separate worker process and monitors that PID's process tree, excluding
the benchmark controller. The child validates its parent disposable
workspace marker and records ONLY SHA-derived signatures and numeric
metrics in a temporary private JSON in that workspace; the parent
consumes/deletes it. Python and frozen child now both report
`memory.scope=isolated_process_tree_lifetime` with identical 30-ms
sampler and sampled simultaneous tree Working Set/Private definitions.
The monitor additionally preserves each observed PID's OS high-water
Working Set and Pagefile readings. Per-PID high-water marks are not
added across different times and must NOT be described as a simultaneous
tree RSS peak. Python fresh `cpu_s` remains a worker process_time value;
frozen CPU still requires separate instrumentation.

The existing frozen run monitor has also been extended from fresh-build
only to its full local process lifetime, encompassing startup and warm
verification, to match the isolated Python worker scope. Fresh wall
measurements remain separately bracketed and are NOT compared with
lifetime RSS as though the windows were identical.

Synthetic Windows integration: two independent processes consumed the
same three tiny synthetic .log inputs; all five deterministic/semantic
Python-vs-frozen signatures matched, 16 single events, Python memory
sample count 25 / one observed PID OS peak, frozen count 32 / two
observed PIDs OS peaks. Both scope labels matched, and disposable
workspace cleanup passed. No real log was sent to a third-party model
or committed, no existing user cache touched.

Regression tests in `tests/test_phase9_frozen_memory.py` cover:
- deterministic simultaneous-tree sample and per-PID OS high-water
  aggregation with shuffled source PID set;
- real Windows isolated Python child report build on synthetic logs,
  parent PID excluded and temp result removed.
Targeted: 2 PASS (1.222 s). Full Python unittest suite: 94 PASS
(26.859 s); `git diff --check` PASS.

Still OPEN: this new subprocess instrumentation has NOT YET been run on
fresh SSH 23/24/25 September 956-MB snapshots; no like-for-like real
memory numbers, frozen CPU parity, 3+ alternating A/B or full Phase 9.0
closure can be claimed. Before a new strict real frozen benchmark,
rebuild the LOCAL diagnostic ZIP from the accepted new HEAD; leave
published Release unchanged.

## Phase 9.0l — CPU sampling and real Python/frozen parity (2026-09-26)

Step ID: P9-0L-01; initial HEAD: `9ae3e44e383bcefd105644af7cc3160b3373bf7d`.
During this session a separate commit `691f8c133c23769c7a44ba588cd54199f64ae31c`
landed on main/origin and accepted the preceding isolated-memory workstream.
This CPU extension is based on that new checkpoint; no reset/rebase.
Recovery: local main == origin/main; preserved the pre-existing dirty
`scripts/bench_phase9_frozen_real.py` and untracked
`tests/test_phase9_frozen_memory.py`. No reset or global cleanup.
Goal: comparable process-tree measurement for both Python and frozen;
no production parser/cache changes, no claim of improved performance.
Files: `scripts/bench_phase9_frozen_real.py`, `scripts/phase9_memory.py`,
`tests/test_phase9_frozen_memory.py` and these notes.
Python now runs as a separate worker; controller is excluded. Frozen and
Python monitors both sample their target process trees every 30 ms from
process launch through fresh build, signature and warm-cache validation.
Captured: sampled simultaneous tree Working Set and Private Bytes peaks,
separate per-PID OS PeakWorkingSetSize/PeakPagefileUsage; Windows
GetProcessTimes kernel+user cumulative CPU per observed process.
`sampled_tree_lifetime_cpu_s` sums the last observed cumulative CPU for
each PID; it is a lower bound if processes end between samples, not
fresh-build-only CPU. Python `cpu_s` remains fresh-build-only; frozen
fresh-build-only CPU is NOT yet measured. Never compare these intervals.
Metrics limitation: Windows Working Set != Linux RSS, sampled tree
peaks != sum of per-process OS high-water marks, and PyInstaller startup
and worker lifecycle differ even though the monitoring boundaries agree.
Incomplete/unreadable samples are counted. No 3+ alternating A/B yet.
Tests: targeted Python 2/2 PASS (1.210 s); full Python 94/94 PASS
(26.401 s); five legacy equivalence scripts PASS (classify 131,860,
report 160 events with identical raw/catalog bytes, combined 6,
event-stream 3 seeds, text 560 cases); Node 8/8 PASS;
`git diff --check` PASS.
Standalone synthetic 3-source fresh+warm parity: signature equality PASS,
13 single events; Python wall 0.266 s, sampled WS 36,823,040 B,
private 25,456,640 B, lifecycle CPU lower bound 0.4375 s (24 polls,
1 PID). Frozen wall 0.321 s, WS 39,067,648 B,
private 20,434,944 B, lifecycle CPU lower bound 0.53125 s
(26 polls, 2 PIDs). Both had 0 unreadable memory/CPU samples.
Exactly one owned synthetic workspace removed; existing benchmark
workspaces, live ConnectConf, downloads, reports, cache and Release
were untouched. No raw production payload was sent externally.
Fable independent review: NOT RUN. Bazzite is offline; DBA-008D has
neither token in relevant environment nor local CleanApi.env. Do not
represent this step as externally reviewed or Phase 9.0 closed.

Step ID: P9-0L-02 — independent fresh real SSH/portable diagnostic.
Diagnostic ZIP was rebuilt locally from `691f8c133c23769c7a44ba588cd54199f64ae31c`;
ZIP SHA-256 `e41d54a1f763ef141c3128fbd97c222033c9feffaac0587ea2fe8a277f0ecba0`.
The previous ignored private JSON was separately backed up under
`diagnostics/phase9_frozen_real_9_0j_backup_private.json` before the
new fixed-name JSON was replaced. No Release upload or update.
One NEW isolated SSH fetch: exact 2026-09-23/24/25 application .log
snapshots, 3 reference date/bytes/SHA and inactive-source gates PASS,
956,307,242 bytes total. Individual SHA values remain ONLY in ignored
`diagnostics/phase9_frozen_real_private.json`; no raw data saved to JSON.
Runner returned exit 0 in 597.03 s including SSH/processing/verification:
`REAL_SNAPSHOT_SHA_GATE_PASS`, `PYTHON_FULL_BUILD_PASS`,
`FROZEN_FULL_BUILD_PASS`, `REAL_FROZEN_PARITY_PASS`,
`WORKSPACE_CLEANED True`. No user downloads/cache/report state changed.
Python fresh wall 212.571 s, `process_time` fresh CPU 201.859 s;
frozen fresh wall 200.939 s (NO frozen fresh CPU field).
All five content checks PASS: inventory, four deterministic normalized
report manifests, semantic SQLite, semantic analytics exports and
657,738 individual events. Warm/no-op PASS for both branches.
Like-for-like lifecycle sampler (30 ms, EXCLUDING controller):
- Python: sampled simultaneous tree Working Set 1,387,380,736 B;
  sampled simultaneous tree Private Bytes 1,697,271,808 B;
  observed cumulative lifetime CPU lower bound 222.484375 s;
  5,282 samples, 1 PID, 0 unreadable memory/CPU samples;
  max individual OS peak Working Set 1,431,224,320 B.
- Frozen: sampled simultaneous tree Working Set 1,455,763,456 B;
  sampled simultaneous tree Private Bytes 1,747,968,000 B;
  observed cumulative lifetime CPU lower bound 200.5 s;
  4,657 samples, 2 PIDs, 0 unreadable memory/CPU samples;
  max individual OS peak Working Set 1,480,757,248 B.
These process-lifetime values span startup, fresh, signature and warm,
NOT merely the bracketed fresh wall interval. OS per-PID peaks are NOT
summed; no RSS equivalence asserted. Sampling can miss short-lived PIDs.
There is only ONE real Python/frozen pair; do not claim A/B speedup.
Existing alternate baseline/spool 3+ A/B and failure-mode matrix OPEN.
Fable review was not executed (Bazzite offline, Windows key unavailable);
its absence is an independent-review gate, not synthetic/real PASS.
Next: source-bounded independent review of CPU/monitoring change;
then measure frozen fresh-build CPU on identical phase boundaries,
perform alternated 3+ parity/performance trials, close remaining gates.

## Phase 9.0m — fresh-build frozen process-tree CPU gate (2026-09-26)

Step ID: P9-0M-01; pre-change HEAD `4af59728d256556bae500b3d2bcb951859c7a26e`.
Goal: bracket frozen CPU on the same fresh-build interval as its
wall clock; preserve existing Python worker process_time, lifecycle
CPU/memory monitoring, output signatures and cache validation.
Bounded change: `scripts/bench_phase9_frozen_real.py` samples
GetProcessTimes for each live frozen launcher/child PID immediately
before `/api/build` POST and after successful status completion.
`cpu_s` is sum of process-specific differences including newly born
PIDs. Missing/recycled PIDs, unreadable or backward-moving counters
fail the metric instead of producing a misleading CPU claim.
`cpu_scope=observed_fresh_process_tree_getprocesstimes`; timing
includes status/HTTP boundary overhead. CPU is observed Windows OS
kernel+user duration, not wall or sampled lifecycle CPU.
Test additions in `tests/test_phase9_frozen_memory.py` verify CPU
delta, newly observed PID, missing PID, regression and unreadable CPU.
Targeted 4/4 PASS (1.185 s), full Python 96/96 PASS (26.676 s),
`git diff --check` PASS. Tiny synthetic frozen fresh+warm smoke:
13 events, fresh wall 0.307 s, fresh CPU 0.171875 s,
owned workspace cleaned. Real gate and independent Fable review
remain separate; no application/parser/analytics/cache changes.

Step ID: P9-0M-02; bounded critical review of P9-0M-01 before commit.
The initial CPU delta guarded missing PIDs and backward counters, but
wrongly claimed recycled PID rejection: a new process with the same PID
and greater CPU time could be silently accepted. Corrected Windows
GetProcessTimes sample to include integer creation FILETIME ticks;
fresh checkpoints now pair creation ticks and CPU duration per PID,
and reject reused PID identities even when CPU increases. A process
born and exited entirely between checkpoints remains unobservable;
this is an OS-sampling limitation, not exact process-tree CPU capture.
Changes: scripts/phase9_memory.py, scripts/bench_phase9_frozen_real.py,
tests/test_phase9_frozen_memory.py; instrumentation only.
Targeted Windows tests 4/4 PASS (1.188 s); full Python 96/96 PASS
(27.523 s); git diff --check PASS. Isolated tiny synthetic frozen
fresh/warm smoke 13 events, 0.320 s wall, 0.171875 s fresh CPU,
30 monitor samples; owned workspace cleaned and verified absent.
Independent Fable review NOT RUN: Bazzite offline and no usable token
on DBA-008D as recorded in P9-0L-01. No real SSH rerun or 3+ A/B
is claimed for this identity-guard correction. Commit/push pending.

Step ID: P9-0M-02 — real SSH Python/frozen fresh CPU verification.
New local diagnostic ZIP built at HEAD `4af59728d256556bae500b3d2bcb951859c7a26e`,
SHA-256 `a7693c1a5dd4bbd3f116353dbfad1aa17243936b60cb144023c9b5146ec9eab5`.
Prior ignored private JSON preserved as
`diagnostics/phase9_frozen_real_9_0l_backup_private.json`.
No GitHub Release changed and no live config packaged (example only).
NEW SSH fetch of exactly 23/24/25 September .log: three
original snapshot date/size/SHA and inactive-source checks PASS,
956,307,242 bytes; actual per-source SHA only in ignored private JSON.
Python fresh wall 212.595 s, process_time CPU 201.844 s.
Frozen fresh wall 205.517 s, GetProcessTimes tree CPU 200.625 s.
Python lifecycle CPU sampled lower bound 222.59375 s;
frozen lifecycle CPU sampled lower bound 203.609375 s.
Sampled simultaneous tree WS: Python 1,400,430,592 B;
frozen 1,474,555,904 B. Private: Python 1,697,488,896 B;
frozen 1,741,369,344 B. Samples 5,290 / 4,738;
CPU unreadable samples zero in both runs.
`REAL_SNAPSHOT_SHA_GATE_PASS`, `PYTHON_FULL_BUILD_PASS`,
`FROZEN_FULL_BUILD_PASS`, five content checks PASS (inventory,
deterministic four report manifests, SQLite semantic, analytics
semantic exports, 657,738 single events), warm/no-op both PASS,
`WORKSPACE_CLEANED True`; exit 0 (598.93 s including SSH,
processing, semantic verification and cleanup). Existing user
reports/downloads/cache, unrelated benchmark folders untouched.
This is one strict fresh CPU benchmark pair, not an A/B estimate.
Python CPU is process_time around in-worker fresh; frozen is OS
GetProcessTimes over both live PIDs at equivalent client POST/status
boundaries, with small HTTP/phase-bracketing discrepancy. Neither
CPU figure is wall time or the full lifecycle CPU lower bound.
Fable: NOT RUN (no accessible Clean APIs token; Bazzite offline).
Next gate: independent review; 3+ alternated old/new or mode-specific
A/B with separate SSH and local timings, active-file/failure scenarios,
then Phase 9.0 closure decision. No parser performance change claimed.

Concurrency correction / provenance boundary: while the 4af5972 real
benchmark was executing, another authorized repository session advanced
main/origin to `166631c7733ff2f8b0760a05dee70d5ecd15ddc1`, adding
creation-time PID identity guards. The private result explicitly records
`git_sha=4af5972` and its ZIP SHA matches the earlier 4af5972 build.
Thus the preceding real numbers VERIFY the initial fresh-CPU variant,
NOT a post-166631c full benchmark of the newer recycled-PID guard.
The latter has the separate synthetic and 96-test PASS documented in
P9-0M-02, but its fresh-SSH real gate remains OPEN. Do not retroactively
attribute these figures to 166631c or change its BUILD_INFO provenance.
Because another session is actively writing this same checkout, no
parallel third live benchmark was started on top of its workstream.

## Phase 9.0n — first fresh real SSH Python/frozen CPU parity (2026-09-27)

Step ID: P9-0N-01; starting HEAD `166631c7733ff2f8b0760a05dee70d5ecd15ddc1`.
Diagnostic PyInstaller build exit 0; ZIP `BUILD_INFO.json` Git SHA
matches HEAD. ZIP SHA-256
`977c81f1a927ea182ad272aac8109d6db2db0f25724ada4579cbcd9d5f2d1322`.
Portable isolated smoke PASS, 161 polls and 0 unreadable child samples.
Prior private result copied to ignored
`diagnostics/phase9_frozen_real_166631c_previous_private.json`.
Published GitHub Release unchanged; live config and user cache untouched.
New isolated SSH fetch of 2026-09-23/24/25 application .log snapshots:
date/bytes/reference SHA, inactive source, repeat local SHA PASS;
171,378,567 + 140,361,291 + 644,567,384 = 956,307,242 B.
Actual snapshot digests remain only in ignored private diagnostic.
Full runner exit 0, 594.19 s total including transfer and validation.
Python fresh 212.672 s wall, 201.719 s process_time CPU;
frozen fresh 202.188 s wall, 198.328125 s OS tree CPU.
Python and frozen warm/no-op PASS; all five signature checks PASS:
inventory, normalized deterministic files of four reports,
semantic SQLite, semantically normalized analytics exports and
657,738 individual events. Raw analytics UUIDs/indexed mtime are NOT
claimed byte-identical. Disposable workspace cleanup PASS and private
result JSON confirms `raw_payload_saved=false`.
Like-for-like isolated process-tree lifetime, 30-ms sampler:
Python 5,284 samples, peak WS 1,431,126,016 B,
peak Private 1,697,251,328 B, 1 observed PID;
frozen 4,681 samples, peak WS 1,485,733,888 B,
peak Private 1,744,941,056 B, 2 observed PIDs.
Both had 0 unreadable memory and CPU samples.
Lifecycle cumulative observed CPU: Python 222.34375 s,
frozen 201.375 s; NOT fresh CPU and NOT simultaneous high-water.
Python fresh process_time brackets direct build; frozen OS CPU
brackets HTTP POST/status completion and may include a small amount
of HTTP overhead. Both memory peaks span worker lifetime, not fresh
phase only. No RSS/Working Set equivalence outside Windows claimed.
This is ONE real Python/frozen pair, not a stable speedup estimate or
alternating 3+ A/B. Intermittent descendants may evade two CPU phase
checkpoints; the PID creation-tick guard proves identity only for
processes observed at both endpoints. No independent Fable review
was executed: Bazzite offline and usable Windows token unavailable.
No parser, cache or analytics code modified in this documentary step.
Phase 9.0 remains OPEN: independent review, 3+ alternating A/B,
source-mutation and failure-mode matrix, architectural decision gates.
Next: obtain bounded external review when authorized key is reachable;
then repeat controlled A/B with identical snapshots and phase windows.

## Phase 9.0o — isolated alternating local no-spool/spool A/B harness (2026-09-27)

Step ID: P9-0O-01; initial HEAD c0e0114f24a294b8b8a7071e81b043a9fc903309.
Goal: reproducibly compare explicit no-spool vs derived-spool on the
SAME exact SSH snapshot bytes without measuring network as Python speed.
Bounded instrumentation: scripts/bench_phase9_frozen_real.py now lets
isolated Python workers select AKUZ_PHASE9_DERIVED_SPOOL=0 or 1;
default remains 1. New scripts/bench_phase9_local_ab.py creates ONE
owned SSH workspace, verifies all three historical date/size/SHA
references, runs six separate fresh/warm local builds ordered
control/spool/spool/control/control/spool (AB/BA/AB), checks full
signature across inventory, normalized deterministic four-report
files, semantic SQLite, normalized analytics exports and event count.
Collects per-trial fresh wall/CPU, process-lifetime sampled memory,
output/cache/data disk sizes, then deletes ONLY each completed trial.
Raw logs live only in ignored disposable workspace; final numeric
and SHA-derived private JSON is ignored. Existing user cache, logs,
config and GitHub Release are out of scope.
Step checks: 6/6 targeted Windows tests PASS (9.209 s);
full 102/102 Python tests PASS (35.766 s);
five legacy equivalence scripts PASS (classify 131,860 cases,
combined six events, event stream three seeds, report 160 events
with identical raw/catalog bytes, text semantics 560 cases);
git diff --check PASS. Synthetic full AB/BA/AB: all six independent
Python builds PASS, 13 events per trial; after each run the trial
directory is absent; three synthetic input files preserved until
the owned workspace itself is removed.
Cleanup guard rejects other trial identities; no global cleanup.
Prior unrelated dist/phase8 launcher/child PIDs 20580/24572 were
observed and left untouched. Fable review NOT RUN (Bazzite offline,
no Windows CleanApi token). Synthetic timings are NOT real speedup.
Real six-trial gate and 3+ A/B numerical result pending separate
post-commit run. Baseline and spool share source bytes, not memory
phase bounds: fresh CPU/wall vs full worker-lifetime memory peaks.

## Phase 9.0p — real alternating Python no-spool vs spool gate (2026-09-27)

Step ID: P9-0P-01; clean main == origin/main == e12d3fc349a2f3f6c4f54a2c2bb861e64b7d8b98
at experiment start. No parallel Python benchmark process observed.
Old unrelated phase8 launcher/child were left untouched.
One new owned workspace fetched exactly the 2026-09-23/24/25
application logs. Reference date/size/SHA, inactive-source and
second local SHA gates PASS. Bytes 956,307,242; event count 657,738;
actual snapshot digests stored ONLY in ignored private JSON.
Same read-only hardlinked snapshots fed six fresh+warm isolated Python
builds, independent report/cache/data dirs, ordered AB/BA/AB:
1 control: wall 269.466 s, CPU 258.625 s
2 spool:   wall 214.784 s, CPU 202.547 s
3 spool:   wall 212.221 s, CPU 201.734 s
4 control: wall 269.772 s, CPU 259.047 s
5 control: wall 269.981 s, CPU 258.422 s
6 spool:   wall 212.613 s, CPU 202.266 s
Control medians: wall 269.772 s, CPU 258.625 s.
Spool medians: wall 212.613 s, CPU 202.266 s.
Measured median difference: 57.159 s wall (21.188%),
56.359 s CPU (21.792%), for local fresh builds ONLY.
No SSH time attributed to CPU speed. Six separately generated
signature SHA-256 values identical; all five controls passed EACH
trial: inventory, four normalized deterministic report manifests,
semantic SQL, all semantically normalized analytics exports and
657,738 single events. Raw volatile export/SQLite bytes are NOT
claimed equal. Warm/no-op verified inside every worker.
No source/event payload saved; result JSON is ignored:
diagnostics/phase9_local_ab_private.json.
Runner exit 0 (1770.40 s total including SSH/verification),
all six completed trial dirs and final owned workspace removed.
Other pre-existing diagnostic dirs and live user data unchanged.
Sampled process-lifetime median tree Working Set:
control 1,329,221,632 B; spool 1,405,624,320 B
(+76,402,688 B). Median Private Bytes:
control 1,633,280,000 B; spool 1,699,037,184 B
(+65,757,184 B). Spool memory cost is NOT free.
Per-trial monitor unreadable memory/CPU samples: 0.
All output reports 2,275,278,553 bytes; analytics data
59,456,085 bytes. Cache directory control 32,656,199 B,
spool 32,656,193 B: 6-byte size difference NOT a content
waiver; semantic SQLite hashes passed every trial.
Memory peaks span worker startup, fresh, signature and warm
and are NOT same-window fresh-only wall/CPU measurements.
The comparison applies only to these exact SHA-controlled
2026-09-23/24/25 log snapshots and host/workload conditions.
Independent Fable review NOT RUN (Bazzite offline, no token
available on Windows); remaining active-file/failure matrix,
Python/frozen 3+ A/B, and Phase 9 architecture selection OPEN.
This step adds documentary evidence only; no production code,
published GitHub Release, credentials or real log contents changed.

## Phase 9.0q — SSH source-mutation fail-closed matrix (2026-09-27)

Step ID: P9-0Q-01; starting HEAD `97ca5b8af8f2f982fa6a2f3f97e5710ad085fd30`.
Goal: close bounded SSH source-mutation/failure gaps without touching
production behavior unless a defect is reproduced. Existing coverage
already included short transfer cleanup, active incomplete-tail trim,
spool disk-full rollback and corrupted-spool recovery.
Added synthetic SSH tests for: inode/device rotation detected after the
byte-bounded transfer; remote truncation below the fixed bound detected
after transfer; active snapshot containing no completed physical line.
All three must fail closed, remove the owned .part file and publish no
akuz_v4_* final snapshot. Existing implementation passed unchanged.
Targeted tests/test_fetch_performance.py: 7/7 PASS (0.207 s).
Full Python suite: 105/105 PASS (36.363 s); git diff --check PASS.
No production parser/fetch/cache code changed; no real log, credential,
user download/report/cache or GitHub Release touched. This is synthetic
fault-injection evidence, not a real active-file experiment.
Fable independent review NOT RUN: Bazzite remains offline and no usable
Clean APIs token is present on DBA-008D. Remaining Phase 9.0 closure
work includes independent review plus any still-uncovered cancellation/
publication recovery cases and architecture decision boundaries.

## Phase 9.0r — report publication rollback on inventory-save failure (2026-09-27)

Step ID: P9-0R-01; starting HEAD `fa81c711929530b6bccb250e53c9cc2b60127b1a`.
Audit found a concrete publication recovery defect in `_publish`:
a report directory was renamed from `*.building` to its final `v4_*`
name before `save_store`. If inventory persistence then raised, the
final report directory and in-memory report entry survived even though
the operation failed, allowing orphan/duplicate reports on retry.
A reproducer first FAILED against the old behavior: after injected
`save_store` OSError, `store["reports"]` still contained the new report.
Bounded fix in akuz_app.py: after final rename, inventory-save failure
removes only the just-added report entry and its exact newly published
final directory, then re-raises. Existing `*.building` finally cleanup
is unchanged. No parser, derived-spool algorithm or cache-key change.
Targeted recovery test PASS (1/1, 0.089 s); full Python suite
106/106 PASS (35.807 s); git diff --check PASS.
No production logs/credentials/user cache/Release touched. This is
synthetic fault injection; a process-kill between filesystem operations
is still a separate crash-consistency question. Fable review NOT RUN:
Bazzite remains offline and no usable Clean APIs token is on DBA-008D.

## Phase 9.0s — alternating Python/frozen runtime A/B harness (2026-09-27)

Step ID: P9-0S-01; starting HEAD `426fead3c159714e2f4586fa318d297f88c50444`.
Goal: establish 3+ alternating runtime parity/performance observations
without repeating SSH per trial or mixing network time into local build time.
Added scripts/bench_phase9_python_frozen_ab.py. It performs one owned,
reference-SHA-gated SSH fetch, then six isolated local fresh+warm builds
ordered Python/Frozen, Frozen/Python, Python/Frozen (AB/BA/AB).
Each trial has a separate report/cache/data root, full signature parity
(inventory, normalized deterministic reports, semantic SQLite and
analytics exports, event count), wall/CPU and process-tree memory metrics;
the exact completed trial root is deleted before the next one.
The runner refuses a dirty tree, wrong archive BUILD_INFO Git SHA,
changed HEAD during the experiment, stale private output or wrong order.
No raw payload is exported. Synthetic orchestration tests cover fixed
order, fail-closed signature mismatch and exactly three trials/runtime.
Targeted tests: 3/3 PASS (0.005 s); full Python suite 109/109 PASS
(36.046 s); git diff --check PASS. No real runtime A/B result yet.
Fable review NOT RUN: Bazzite remains offline and no usable Windows token.

## Phase 9.0t — real alternating Python/frozen runtime gate (2026-09-27)

Step ID: P9-0T-01; clean experiment HEAD
`5612560ce936545babf9432d6dbae277ef1dc40c`.
Diagnostic ZIP rebuilt from exact HEAD; portable smoke PASS.
ZIP SHA-256 `8f058d823f086d169dabd90fcb6508cf87396dd78f351ba7c4c57360b71bea59`.
One new owned SSH fetch passed the established 2026-09-23/24/25
reference date/size/SHA and inactive-source gates; 956,307,242 bytes.
Network transfer occurred once and is excluded from per-trial wall/CPU.
Six independent local fresh+warm trials, AB/BA/AB runtime order:
1 Python  wall 212.373 s, CPU 201.156 s
2 frozen  wall 201.451 s, CPU 197.609375 s
3 frozen  wall 202.509 s, CPU 197.828125 s
4 Python  wall 213.156 s, CPU 202.500 s
5 Python  wall 213.496 s, CPU 202.891 s
6 frozen  wall 201.084 s, CPU 197.562500 s
Python medians: wall 213.156 s, CPU 202.500 s.
Frozen medians: wall 201.451 s, CPU 197.609375 s.
Observed median differences for this exact Windows/workload gate:
11.705 s wall (5.49%) and 4.890625 s CPU (2.42%) lower for frozen.
This does NOT prove an intrinsic packaging speed advantage; runtime,
Defender/filesystem/startup/environment differences remain possible.
All six trials produced one identical signature SHA: inventory, four
normalized deterministic report manifests, semantic SQLite, normalized
analytics exports and 657,738 single events PASS; warm/no-op PASS.
Raw volatile analytics/index bytes are not claimed byte-identical.
Median sampled process-lifetime tree Working Set:
Python 1,392,066,560 B; frozen 1,464,082,432 B
(frozen +72,015,872 B, about +5.17%).
Median Private Bytes: Python 1,697,656,832 B; frozen 1,743,097,856 B
(frozen +45,441,024 B, about +2.68%).
Memory spans full worker lifetime, not only fresh wall/CPU interval.
Result stores no raw payload; all six trial roots and owned workspace
were removed. Runner exit 0 after 1540.04 s total.
Published Release/user cache/config remained untouched.
This closes the 3+ alternating Python/frozen runtime parity gate for
these exact snapshots; it is not a general benchmark across machines.
Fable independent review remains BLOCKED by unavailable Bazzite/token.

## Phase 9.0u — inventory temporary-file fault recovery (2026-09-27)

Step ID: P9-0U-01; initial HEAD `db1908db626f193ebe4343b867aa77f5dde2cff4`.
Fault injection demonstrated that failed `Path.replace()` in
`akuz_store.save_store()` leaves `cache/inventory.json.tmp` behind.
Two new tests FAILED before fix on this stale temp assertion.
Bounded fix: remove only `inventory.json.tmp` after caught write/
replace errors; preserve the original exception if unlink also fails.
Never remove the existing `inventory.json` or user's other cache files.
Three synthetic guards PASS after fix: failed replace preserves
original inventory bytes and removes temp; partial JSON write
removes temp; failed `_publish` persistence rolls back new report,
then retry publishes one report with no duplicate/orphan.
Targeted 3/3 PASS (0.075 s), full 113/113 Python tests PASS
(36.072 s), five legacy equivalence scripts PASS (classify 131,860,
raw/catalog 160 events, combined 6 events, event stream 3 seeds,
text semantics 560 cases), `git diff --check` PASS.
No production parser/spool/cache format change; no real SSH transfer,
production payload, credential, user report/download or Release touched.
This covers catchable write/rename errors, NOT sudden process kill or
power-loss durability; shared fixed-name tmp concurrency unchanged.
Independent Fable review NOT RUN: Bazzite offline, Windows token absent.
A concurrent category-isolation workstream modified other files;
this commit must stage ONLY akuz_store.py, the inventory tests and
these notes, preserving those unrelated uncommitted edits.

## Phase 9.0v — synthetic inventory process-exit boundaries (2026-09-27)

Step ID: P9-0V-01; initial HEAD `5b50f1615b6425c48a4afe54c72df38588c701af`.
Goal: test an abrupt child-process exit before and after inventory
atomic rename, not merely a caught Python exception.
Added `tests/test_phase9_inventory_crash.py` with isolated child
Python interpreters and temporary synthetic inventory roots ONLY.
Child deliberately calls `os._exit(61)` immediately before
`inventory.json.tmp.replace(inventory.json)` or `os._exit(62)`
immediately after a successful replace. Parent remains alive.
BEFORE: original inventory bytes and entries intact, abandoned
`inventory.json.tmp` present (no exception/finally runs), then a
fresh load/save replaces stale tmp and keeps original entries.
AFTER: newly saved inventory intact and readable, no tmp present,
then a fresh load/save preserves old/new entries without duplicates.
Targeted 2/2 PASS (0.261 s), full 115/115 Python tests PASS
(36.748 s); `git diff --check` PASS. No app/persistence code
changes, no real SSH, raw logs, credentials or user cache touched.
This proves tested child process-exit ordering at two rename
boundaries only. It does NOT prove power-loss durability, `fsync`,
crash atomicity of report-dir + inventory as a unit, or concurrent
writers' safety with the shared tmp filename. Those gates remain OPEN.
Fable NOT RUN: Bazzite offline, Windows Clean APIs token unavailable.
Unrelated in-progress category-isolation changes left untouched;
commit ONLY new inventory crash test and these notes.

## Phase 9.0w — report directory/inventory crash gap (2026-09-27)

Step ID: P9-0W-01; initial HEAD `fe61057cd073537748bbf3939b67e0393896ff44`.
Goal: probe two distinct _publish persistence boundaries with true
synthetic child-process `os._exit`, no signal sent to real app.
Added tests/test_phase9_report_crash.py; parent owns disposable
workspace, child creates only one synthetic generated report.
Exit 71 immediately AFTER `*.building` directory rename, BEFORE
`save_store`: final report directory exists, inventory has zero
reports. A retry creates a NEW indexed report and the first directory
remains orphaned (2 directories, 1 indexed). This is a REPRODUCED
crash-consistency GAP, not a closed functional gate. It does not
imply analytics counts duplicate events from the orphan: only the
indexed report is discoverable through report_summary.
Exit 72 immediately AFTER successful inventory save: one final
directory and one indexed report; retry reuses it, no new directory.
Targeted reproducer 2/2 PASS (0.506 s), full Python 117/117 PASS
(37.041 s), `git diff --check` PASS. No production change or real
SSH/log tests; no key/config/user cache/Release touched.
Do NOT globally delete unindexed `v4_*` as an apparent quick fix:
no proof of ownership exists for arbitrary directories on user hosts.
Need separate bounded design of a durable, ownership-validated
publication intent/recovery protocol or explicit conservative orphan
reconciliation before claiming whole-report crash consistency.
Independent Fable review NOT RUN (Bazzite offline/token unavailable).
Category-isolation workstream in same checkout remains untouched.

Step ID: P9-0W-02 — bounded recovery design only, no application patch.
Based on reproducible P9-0W-01 crash gap, prepared
`PHASE9_PUBLICATION_RECOVERY.md` with an ownership-validated intent
candidate and explicit fail-closed recovery state matrix. Critically,
`_perform_build` assumes `_publish` executed `single_gen` in its
SpoolWriter branch; merely returning a recovered report without a
valid sidecar can corrupt combined replay. Any implementation must
separate recovery from sidecar registration or use standard fallback.
Unknown unindexed user directories must never be globally removed.
Physical power-loss durability, concurrent cache writers and
independent Fable review remain out of scope and OPEN.
This is DOCUMENTARY DESIGN, not synthetic, real or production PASS.
No production source, SSH credential, user files, Release or
architecture selection altered; unrelated category-isolation work
remains uncommitted and untouched.

## Phase 9.0x — ownership-validated report crash recovery (2026-09-27)

Step ID: P9-0X-01; initial HEAD `4e0eea6bb1a8b315d9d4233d35a4e0a66be5ae5a`.
Reproduced P9-0W-01 orphan changed to exact-report recovery:
`akuz_publication.py` writes a per-report atomic intent BEFORE
`*.building` -> final rename. Intent stores report id/key/kind/label,
source/provenance identity, provenance/index/catalog hashes and sizes
of all output paths, but NOT the raw event contents or passwords.
`_publish` recovers only exact matching final and request; it registers
that existing report into inventory once, then retires owned intent.
An indexed matching report retires its stale intent on reuse.
Corrupt/unknown/mismatched/unindexed directories are never swept.
A crash before final rename leaves a staging dir and intent for
manual diagnosis, NOT an automatically published partial report.

Critical regression found and corrected: recovered `single_gen` did
not run and its empty SpoolWriter occupied `0000.jsonl`; next source
tried the same len(spools)-derived filename and failed FileExistsError.
Only freshly generated singles now register spool entries; spool names
use stable per-selection positions so the next fresh source cannot
collide. Missing sidecar falls back to normal event derivation.
A 3-source subprocess test (1 recovered + 2 fresh) now matches an
independent no-spool inventory/report manifest, combined included.
Targeted crash/spool 12/12 PASS (5.234 s); full Python suite
127/127 PASS (44.851 s); five legacy equivalence scripts PASS:
classify 131,860 cases, report 160 event raw/catalog bytes,
combined 6 events, event stream 3 seeds, text semantics 560 cases.
`git diff --check` PASS. No production SSH logs fetched for THIS
candidate yet: old 2026-09-23/24/25 real evidence belongs to
pre-recovery SHAs. Fresh real Python/frozen parity/CPU/Working Set,
cache fault matrix, packaging and independent Fable review OPEN.
Input/output payloads and credentials never included in these tests.
User reports/downloads/cache and published GitHub Release untouched.
Unrelated category-isolation changes remain unstaged and preserved.
Raw shard file paths/sizes are verified; full per-raw-shard content
hashing and physical power-loss durability are NOT claimed.
Concurrency of multiple app processes writing same inventory remains
OPEN, as does safe cleanup of abandoned, unindexed staging work.

Step ID: P9-0X-02 — new exact-SHA real SSH Python/frozen gate.
Candidate accepted as committed code at HEAD
`be38fa1cdafb07523b5bdb586bc3f7e4cc057f82`.
Because a separate category-isolation workstream kept the main checkout
dirty, created an OWNED detached, CLEAN Git worktree of this exact SHA.
Only local ignored copies of ConnectConf.cfg and prior snapshot SHA
reference were put there; no real config was packaged or committed.
PyInstaller 6.22.3 diagnostic build PASS; portable ZIP BUILD_INFO Git
SHA equals be38fa1. ZIP SHA-256
`3fd9c55b2cc81e3d29531a05b90a702b44d58b95acf66e80b29a9b1aa14d8b2c`.
EXE SHA-256
`3d07fbdfc9508a9fa9ce4ed777a647228871d41e64ec507bb38f6c5b788b15d8`.
Isolated portable ZIP smoke PASS (143 samples, 118 observed child
samples, 1 unreadable child memory sample); this tiny smoke is NOT a
claim of zero loss. GitHub Release unchanged.
Fresh SSH date/bytes/reference SHA, inactivity and second local SHA
PASS for exactly 2026-09-23/24/25 .log; 956,307,242 input bytes.
Actual source digests remain ONLY in ignored private JSON, never Git.
One isolated fresh+warm build per runtime, same read-only hardlinked
snapshots, runner exit 0 (595.58 s including network, checks, cleanup).
Python fresh wall 214.583 s; process_time CPU 203.375 s.
Frozen fresh wall 202.697 s; GetProcessTimes CPU 198.890625 s.
Five normalized signature checks PASS: inventory, four deterministic
report file manifests, SQLite semantic, analytics export semantic and
657,738 single events; warm/no-op parity PASS.
Raw volatile analytic/export bytes are NOT claimed byte-identical.
Python sampled full-lifetime tree: 5,328 samples, peak Working Set
1,411,678,208 B, peak Private 1,699,987,456 B, cumulative observed
lifetime CPU 224.0625 s; zero unreadable memory/CPU samples.
Frozen sampled full-lifetime tree: 4,692 samples, peak Working Set
1,443,442,688 B, peak Private 1,734,922,240 B, cumulative observed
lifetime CPU 201.9375 s; zero unreadable memory/CPU samples.
Full-lifetime memory/CPU and fresh-only wall/CPU are DISTINCT scopes.
No intrinsic Python/EXE speed advantage inferred from one pair.
`raw_payload_saved=false` and `WORKSPACE_CLEANED True`.
Private evidence copied with SHA equality verified into existing
ignored diagnostics: `phase9_x_frozen_real_be38fa1_private.json`,
`phase9_x_portable_smoke_be38fa1_private.json` and
`phase9_x_portable_be38fa1_private.zip`; no raw AKUZ log persisted.
The owned detached worktree, including temporary SSH configuration,
was explicitly removed; original user's cache, reports, downloads,
other benchmark folders and Release were never deleted or rewritten.
Main checkout still contains unrelated uncommitted category-isolation
edits, NOT included in the clean be38fa1 real/portable evidence.
This is a first new-SHA real content/CPU/memory gate, NOT a 3+ new
A/B or a real process-kill-on-production-log trial. Whole-report
power-loss, multiprocess cache concurrency and independent Fable
review remain OPEN; Bazzite offline and Windows token unavailable.

## Phase 9.0y — independent Claude Fable 5.1 bounded review (2026-09-27)

P9-0Y-01: Bazzite Desktop Commander reconnected and direct ping PASS;
`CleanApi.env` detected locally and curl Clean APIs smoke returned
HTTP 200 / `claude-fable-5.1` / OK. Windows `main==origin/main` at
`6f15b3de7419fe4ede3e6fc28778ec3bdaad43a9`, with unrelated
`akuz_html_explorer.py` and category-isolation test dirty; preserved.
Clean Bazzite clone fast-forwarded from `e40902c` to `6f15b3d`.
No secrets/real logs/SSH config sent to Fable: nine line-numbered
tracked code/tests/design files (~70,672 prompt chars), no file
system/network access granted to the reviewing model.
Python urllib HTTP 403 / code 1010; curl corrected the transport.
First completed HTTP 200 response used 5,800 completion tokens but
had empty content and `finish_reason=length`: NOT A REVIEW.
Second HTTP 200 used 16,000 max output, completion 9,591 tokens,
returned 12,999-character response, `finish_reason=stop`.
Original reviewer result: `PHASE9_FABLE_RAW_REVIEW.md`; independent
triage and all ten dispositions: `PHASE9_FABLE_REVIEW.md`.
P9-0Y-02: clean Bazzite focused pre-existing suite 17/17 PASS.
Direct synthetic probes CONFIRMED corrupt indexed `catalog.js` still
reused, and unindexed recovered same-size changed raw shard reused.
`tests/test_phase9_review_limits.py` records both as
`unittest.expectedFailure`, NOT as fixed/accepted behavior.
Focused suite 19 total: 17 PASS + 2 expected failures (1.376 s).
Fable findings included already-declared power-loss/concurrency limits,
intent staging accumulation, potential symlink races, inventory
conflict policy, and `with_suffix` suggestion rejected as false bug.
No production parser, generator, inventory or report code changed here;
be38fa1 real SSH Python/frozen content gate retains exact scope.
Unrelated Windows category-isolation workstream untouched;
GitHub Release unchanged. Next implementation must first resolve
indexed corrupt-cache lifecycle and raw same-size recovery integrity,
then re-run focused tests plus SHA-gated real/frozen parity.
P9-0Y-03 cross-platform validation note: unfiltered full
`python3 -B -m unittest discover -s tests -q` on Bazzite/Python 3.14
ran 128 tests, 4 skips, 2 expected failures, ONE ERROR in pre-existing
Windows-only `test_phase9_frozen_cleanup.FrozenCleanupTests.
test_retry_after_partial_cleanup_removed_marker`.
Its synthetic `PermissionError(..., winerror=5)` does not set
`.winerror` on Linux, while Windows cleanup deliberately checks
`.winerror in (5,32)` and re-raises otherwise. Not caused by Fable
review/test changes; do not record a Linux full-suite PASS.
Exact focused crash/inventory/spool/new-limit suite on Bazzite remains
19 total / 17 PASS / 2 expected failures. Windows full suite requires
separate real Windows run after the docs-only commit.

P9-0Y-04 final Windows gate after review-only commit `3b8900d`:
full `python -B -m unittest discover -s tests -q` on DBA-008D
129 tests in 45.800 s: 127 ordinary PASS, 2 EXPECTED FAILURES,
zero unexpected failures/errors. `git diff --check` PASS.
The 2 expected failures explicitly represent OPEN integrity gaps,
not acceptance of corrupt indexed catalog or same-size raw damage.
`main == origin/main == 3b8900d035e2cc757679f0da6eb04a6c305c8e53`.
Unrelated concurrent uncommitted category isolation files remain
untouched; release unmodified. The test count reflects this checkout;
the prior accepted real SSH Python/frozen gate remains at be38fa1.

## Phase 9.0z-01 — indexed-cache integrity and persistent quarantine

Authoritative starting checkpoint `007947209919011e10a178d54a0ba825e3eb8eb9`.
This workstream addresses independent Fable finding #3. A new report
stores expected SHA-256 of `index.html`, `data/catalog.js` and
`provenance.json` and an all-file size manifest in its OWN inventory
entry. `cached_report` checks all three hashes and known output sizes
on warm reuse, including remote aliases and combined; legacy inventory
entries have no trustworthy baseline hashes and receive structural
required-file checks only, never invented hashes. Search considers
newest valid matching row, so a quarantined old entry cannot shadow
an actual replacement. Failed integrity verification writes the
`invalidated=integrity` flag atomically before regeneration; if this
save fails, the in-memory flag is rolled back and regeneration stops.
Damaged original report directory is NOT deleted, moved or altered;
report library labels it as damaged while analytics and source-date
changes exclude the quarantined row. Replacement receives its own
report ID and its ordinary remote alias; subsequent warm reuse should
return it, not regenerate again.
New synthetic tests cover catalog hash corruption, missing raw shard,
quarantine-save failure preserving old inventory, real-generator
3-source + derived-spool + analytics refresh + repeated warm reuse.
Former review test `test_indexed_report_reuse_rejects_corrupt_catalog`
now passes normally; same-size raw corruption on interrupted report
remains expectedFailure until the second, separate workstream.
Bazzite focused inventory/recovery/spool/analytics group:
46 tests, 45 PASS and 1 expectedFailure (2.930 s).
`git diff --check` PASS. No user real logs, credentials or Release
access used for synthetic checks. Real SSH Python/frozen and full
Windows-suite gate MUST be repeated against this exact patch SHA.
Important bounded limit: new warm cache validates required hashes
and all file SIZES; same-size raw damage on a previously indexed
report is not exhaustively detected on each warm visit, to avoid an
unmeasured full data-shard hash scan on every page interaction.

## Phase 9.0z-02 — content-verified interrupted-report recovery

Separate follow-up to indexed-cache fix `75d8226`. Fable finding #4:
previous intent version 1 checked SHA-256 of three identity files but
only size/path for raw shards, so same-size raw damage could be adopted.
Candidate version 2 now checks SHA-256 for EVERY report output file
BEFORE an incomplete publication is reindexed. Full hashes are
computed while `.building` is owned by the generating process and
stored inside `value.integrity.all_sha256` in the local intent and
inventory, without including raw content itself. A version-2 retry
verifies exact path/size set and exact digest of every output file;
mismatch fails closed and never deletes the unindexed original.
Historical version-1 intents remain recoverable with their old limited
size/critical-hash semantics; the new guarantee does NOT apply
retroactively. Ordinary warm cached reads still check only critical
file hashes and all sizes, not full raw content, to keep warm use
bounded; physical power loss and parallel writers remain OPEN.
Synthetic Linux focused group: 48/48 PASS (3.044 s), including
formerly expectedFailure same-size raw mutation now PASS, legacy
version-1 unchanged recovery PASS, injected raw-hash read OSError
produces no inventory/intent/staging, and recovered one-single plus
two-fresh combined sidecar semantic parity. No production logs touched
by synthetic probes. `git diff --check` PASS.
This SHA requires Windows full suite and separate SHA-gated SSH
Python/frozen semantic + memory gate and an I/O overhead measurement
before declaring the patch accepted for the real workload. Do not
compare one noisy fresh wall sample to old values as causal A/B.

P9-0Z-03 initial real hash-stage evidence (exact clean `d0132fd`
on DBA-008D, same SHA-gated 23/24/25 September .log snapshots):
Python `report.integrity_hash` completed on four real generated
reports: 1,394 output files / 2,275,278,607 output bytes,
21.023 s summed wall across four stages. Individual stages:
225,490,431 B / 231 files / 3.135 s;
197,873,266 B / 241 files / 3.317 s;
714,225,252 B / 245 files / 4.203 s;
1,137,689,658 B / 677 files / 10.368 s.
This is material fresh-build work (roughly 10% of the ~214 s
previous Python fresh wall), unlike 0.011 s in small synthetic A/B.
Do NOT claim synthetic timings prove negligible real overhead or
causal new-SHA wall speedup/slowdown from one uncontrolled pair.
Numeric-only stage metrics were captured into an ignored private
file BEFORE the disposable real-workload directory was removed;
no raw output bytes, identities or SSH secrets in the note.
P9-0Z-04 completed exact-SHA Windows gate for `d0132fd`:
full `python -B -m unittest discover -s tests -q` on DBA-008D
135/135 PASS in 53.088 s and `git diff --check` PASS. Concurrent
category-isolation edits remained uncommitted and untouched; the
separate clean detached d013 worktree passed diagnostic portable
build/smoke and the live Python/frozen exact-SHA source parity gate.
Exact same 2026-09-23/24/25 SSH .log bytes: 956,307,242 input B,
657,738 single events, four deterministic report manifests; Python
vs frozen normalized inventory, reports, SQL and exports all PASS.
Fresh Python wall 226.689 s, process CPU 207.875 s, sampled tree
Working Set peak 1,421,045,760 B and private peak 1,700,147,200 B.
Fresh frozen wall 208.561 s, observed process-tree CPU 205.171875 s,
sampled tree WS peak 1,454,526,464 B, private peak 1,741,524,992 B.
Both runtime samplers had zero unreadable memory samples.
Portable ZIP SHA-256
`40dd0debf130f2207e819e5cbaa94b516e1c18d7daaedbea0bc0ab5c89f83043`;
EXE SHA-256
`239fc42524095281515752be438d684394bf7446399152fb4e9c7bb76e8c39ef`.
Frozen `report.integrity_hash`: 2,275,278,607 B / 1,394 files,
6.310 s sum (0.672 + 0.610 + 1.878 + 3.150). This followed
Python on the same host; do NOT attribute its shorter hashing
stage to PyInstaller/EXE alone: Windows OS file-cache warmth is
uncontrolled. Previous exact `75d8226` gate on these input SHAs:
Python wall/CPU 214.165/203.797 s, frozen 203.329/199.890625 s.
Observed one-pair differences: Python wall +12.524 s; frozen +5.232 s;
these are not causal A/B estimates. Real new-SHA 3+ paired A/B OPEN.
The hash stage's full-file read is a MATERIAL cost despite same
semantic/deterministic results. Future generator-fed SHA could avoid
re-reading 2.275 GB of generated bytes, but touching
`akuz_html_explorer.py` conflicts with another active workstream;
do not conflate workstreams or change uncommitted category code.
Private numeric trace and full benchmark JSON kept in original
ignored Windows diagnostics (copy SHA verified); owned detached
d013 worktree incl temporary SSH cfg removed, no original source
or user cache/report directory touched. No Release update.

P9-0Z-05 exact-SHA z-01 closure detail (historical fix
`75d8226f74ddd9651d35bd9c228b6262f2882591`): isolated
Windows Python/frozen SHA-gated SSH parity PASS for exactly same
2026-09-23/24/25 source snapshots; fresh wall Python 214.165 s,
frozen 203.329 s. CPU 203.797 and 199.890625 s respectively.
All normalized report manifests/inventory/SQLite/export checks and
657,738 events PASS; warm reuse and diagnostic portable smoke PASS.
ZIP SHA-256 `27c71278c663c85eaf21674f8f70935dd44988f28111d1f3f4b34e982a7a20ad`.
Previous isolated z1 source/config worktree removed after private
result copy SHA-verified. z2 exact-SHA results and hash overhead are
reported above. No original reports, input logs, user cache or Release
were altered by either real benchmark.

## Phase 9.0z-03 — collision-safe allocation against existing intents

Starting clean Bazzite/Windows checkpoint `10f6d09c240f945209da7e3ce15edd1db871139c`.
Fable follow-up #2: `_fresh_report_id` checked only final/staging
reports. A stale `cache/report_intents/<rid>.json` or `.json.tmp`
without a corresponding directory can collide with a freshly
sampled same-second random suffix; `write_intent` then raises
`FileExistsError` after generating output. This is a preexisting
error condition independent of the full-file SHA cost.

Added deterministic synthetic tests using a fixed local timestamp
and token sequence: existing `.json` -> retry next suffix without
removing evidence; existing `.json.tmp` -> same; ten reserved tokens
-> bounded `FetchError` without publishing files or mutating marker.
Before code patch three tests FAIL with `FileExistsError`, proving
regression reproduction. `_fresh_report_id` now checks final,
`.building`, marker and marker draft; also considers dangling
symlinks reserved rather than reusing their names. No orphan sweep,
TTL deletion, cache key relaxation or simultaneous-writer lock.
Bazzite focused crash/inventory/recovery/indexed suite: 25/25 PASS;
`git diff --check` PASS. Concurrent Windows `akuz_html_explorer.py`
and category test are untouched, and no real logs or config sent
outside owner machines. A single-process name reservation check
cannot prove two-process atomic allocation or inventory lost-update.

P9-0Z-03 Windows final gate and independent review:
`1cea8b1c472bb9555ef9fed3b02d123a5b3897cc` fast-forwarded
from verified local Git bundle and pushed to origin/main; bundle
SHA-256 `16f0ff6fd867b47015a7e1cc57dcf2f0992f6e9e8282da827885f4ce9ce124f7`.
On DBA-008D, NEW clean detached worktree of this exact SHA ran full
`python -B -m unittest discover -s tests -q`: 137/137 PASS in
54.644 s, `git diff --check` PASS. Owned worktree and transfer
bundle removed after gate. Unrelated main-checkout category work
(`akuz_html_explorer.py`, `tests/test_phase9_category_isolation.py`)
remained uncommitted and untouched. No release or real .log upload.
Independent read-only Claude Fable 5.1 review via user's Bazzite
Clean APIs on actual clean 1cea8b1, five exact line-numbered tracked
code/test files (24,691 prompt chars), HTTP 200 and finish_reason=stop:
APPROVE narrow single-process collision fix; existing multi-process
check/reservation TOCTOU remains OPEN. Original model response and
source-vs-review triage: PHASE9_FABLE_Z3_RAW_REVIEW.md and
PHASE9_FABLE_Z3_REVIEW.md. No raw user logs/config/API key sent.

## Phase 9.0z-04 — deterministic OPEN same-root inventory race

No application code changed. On clean Bazzite main after 1cea8b1,
`python3 -B scripts/probe_phase9_inventory_race.py` reproduced the
single shared `inventory.json.tmp` collision using TWO actual Python
processes and an explicit pause before A's replace. B publishes
`initial` + `B`; A then raises FileNotFoundError and its `A` entry
is lost. Output: MULTIWRITER_LOST_UPDATE_REPRODUCED,
A_write_failed=True, persisted_A=False, persisted_B=True,
initial_preserved=True. Repeat on Bazzite PASS as a **defect repro**,
NOT a multiwriter-safety acceptance pass. All workspace contents
synthetic and removed by TemporaryDirectory; existing cache untouched.

A separate stale-snapshot problem would persist if only temporary
filenames became unique. See `PHASE9_INVENTORY_CONCURRENCY.md` for
actual `load_store` -> mutate -> `save_store` call sites and an
OS-crash-released, whole-transaction same-root locking proposal.
`state.lock` and analytics LOCK apply to one process only. No lock
or fsync code introduced, and neither multiprocess consistency nor
power-cut durability is claimed. Windows/NTFS synthetic reproduction,
lock lifecycle, process-kill, cache-clear and source-date gates OPEN.

## Phase 9.0z-05 — completed real 3+ paired all-file SHA cost gate

A/B on DBA-008D, committed CLEAN exact detached worktrees:
control `75d8226f74ddd9651d35bd9c228b6262f2882591` (indexed
integrity only); candidate `d0132fdc10f9658a7da21e5fe4bfc693be4f76cb`
(version-2 full all-file SHA intent). **This A/B is NOT a benchmark
of later `1cea8b1` report-ID or `c158fbe` probe/doc commits.**
Privately owned runner `diagnostics/private_phase9_zcost_ab.py` on
Windows, synthetic smoke PASS before live test. Downloaded one fresh
read-only, reference SHA/size-verified 2026-09-23/24/25 SSH .log
snapshot for all six local builds, total 956,307,242 input bytes.
No SSH time included in fresh-build timings. Fixed AB/BA/AB order:
control, candidate, candidate, control, control, candidate.
Each variant independently built four reports in an owned isolated
root, checked fresh + warm/no-op and then deleted just that trial.
Every trial matched the first across all FIVE signature keys:
normalized inventory, 4 deterministic report manifests, semantic
SQLite tables, semantic analytics exports, 657,738 single events.
All six: 0 unreadable OS process-tree memory samples and 0 unreadable
CPU samples. Input snapshots unchanged; raw_payload_saved=false.
Controller exit 0; AB_WORKSPACE_CLEANED=True.
Real-trial fresh wall/CPU seconds and candidate SHA-stage wall:
  1 control   214.026 / 203.500 / —
  2 candidate 227.326 / 208.469 / 20.981
  3 candidate 226.297 / 207.969 / 20.929
  4 control   214.924 / 203.828 / —
  5 control   213.633 / 202.969 / —
  6 candidate 226.400 / 207.891 / 20.955
Medians control fresh wall=214.026 s, CPU=203.500 s;
candidate wall=226.400 s, CPU=207.969 s. Observed candidate-control
wall +12.374 s (+5.782%); CPU +4.469 s (+2.196%). New full-hash
stage median 20.955 s reading 2,275,278,553 generated bytes across
1,394 files per trial. Do not equate its 20.955 s wall with total
wall delta 12.374 s; OS file cache/overlap/ambient load matter.
Identical disk reports size 2,275,278,553 B; cache size candidate
32,885,526 B versus control 32,746,221 B (+139,305 B manifest
metadata); per-trial data size 59,456,085 B identical. The earlier
single-run generated bytes differed slightly (volatile metadata);
this six-run series emitted equal total report sizes and equal
normalized deterministic manifests, NOT necessarily byte-identical
volatile `provenance.generated` values.
Median sampled full-lifetime process-tree simultaneous Working Set:
control 1,397,735,424 B; candidate 1,399,595,008 B. Median sampled
Private Bytes control 1,698,959,360 B; candidate 1,698,713,600 B.
These are sampled peaks, not exact global OS maxima or each PID's
individual high-water marks. Per-run WS control 1,396,875,264 /
1,430,818,816 / 1,397,735,424 B; candidate 1,399,595,008 /
1,396,961,280 / 1,420,484,608 B. Do not claim a proven memory
saving from this small sample.
Private full evidence (contains snapshot source digests but NO raw
payload) remains ONLY in ignored Windows
`diagnostics/phase9_zcost_real_ab_private.json`, SHA-256
`ff1df9cda33bc0e498e672aeb63e15da658f9235c843665c1c2b48e6ba4e5a0c`.
Both owned benchmark version worktrees were CLEAN and removed after
result verification. Existing main-checkout `akuz_html_explorer.py`
category-isolation edits/test remained untouched. No Release upload.

P9-0Z-06 Windows/NTFS synthetic multiprocess repro:
`python -B scripts/probe_phase9_inventory_race.py` on DBA-008D
reproduced the same A lost/B retained `inventory.json.tmp` collision
as Bazzite, in disposable TemporaryDirectory. Explicitly OPEN bug,
not a claimed multiprocess-lock PASS. See PHASE9_INVENTORY_CONCURRENCY.md.

## Phase 9.0z-07 — producer-side SHA candidate (implementation stage)

Starting exact clean main `b6902c8884e9afb9a749bf21327fb3582cd09fc8` on Bazzite; Windows main had separate uncommitted `akuz_html_explorer.py` and `tests/test_phase9_category_isolation.py` and those two files were NOT edited by the Windows workflow. Code candidate authored in Bazzite clone only, with isolated Windows worktree gate pending.

`akuz_report_writer.write_report_text` writes bounded 262144-character UTF-8 slices and hashes bytes accepted by the writer, translating LF with native os.linesep to match `Path.write_text(...,encoding='utf-8')` on Windows. `akuz_html_explorer.generate` accumulates an internal name -> (SHA-256, byte size) manifest for every raw shard, catalog and static asset; it remains out of HTML/catalog source metadata. `_publish` trusts this only from builtin `generate` or the TWO declared built-in spool/combined wrappers; all custom/injected generators still use the complete existing file-read fallback. `write_intent` verifies exact expected file-path set, byte sizes and digest format BEFORE publishing; `provenance.json` is hashed after it is written; the v2 recovery path ALWAYS rehashes every final report file when recovering after a crash. Ordinary warm reads retain three critical hashes plus all sizes. Physical power-loss, symlink race and multiprocess inventory remain OPEN.

Bazzite focused producer/crash/spool test group 24/24 PASS; new tests for byte-accurate mixed LF/CRLF + non-ASCII + chunk boundary, invalid surrogate, full output integrity and custom-generator fallback 4/4 PASS. Legacy check_report_equivalence.py PASS (160 events / 4 shards byte-identical), check_combined_equivalence.py PASS, check_event_stream_equivalence.py three seeds PASS, git diff --check PASS. Bazzite all-tests discovery 139 tests had one preexisting Windows-specific frozen-cleanup test failure in Linux fake WinError semantics (`test_retry_after_partial_cleanup_removed_marker`); authoritative full Windows suite must be run on exact candidate SHA and that test result must be recorded rather than concealed.

P9-0Z-07 exact-SHA Windows/real content gate, code `14b45d248e5f5c49b843a0a2ba26fbdc0075538b`:
- Clean detached DBA-008D worktree, Windows Python 3.11.9 `python -B -m unittest discover -s tests -q`: **141/141 PASS in 49.735 s**; original `check_report_equivalence.py` byte-level 160-event/4-shard check PASS, `check_combined_equivalence.py` PASS; `git diff --check` PASS. Do not misreport the separate Linux-only frozen-cleanup test as a Windows failure; Linux focused 26/26 PASS and five prior legacy equivalence scripts PASS.
- Diagnostic PyInstaller 6.22.3 ZIP built from exactly this SHA, ZIP SHA-256 `6b14df5020cbb841e29915cafc416aa428cb3d04dd5cef9db645d49cf0d33955`, EXE SHA-256 `4c8672b2c7432cf7140452c120f1092c6498c1c71ad78f0e66e778ad2721a5e2`; portable smoke PASS, 142 monitor samples, 0 unreadable; **not uploaded to public Release**.
- Fresh SHA/size-gated SSH snapshots for 2026-09-23/24/25 unchanged, 956,307,242 input bytes, 657,738 single events. Python fresh wall/CPU 214.921 / 206.141 s, sampled peak simultaneous process-tree Working Set 1,327,345,664 B, Private 1,347,588,096 B, 5,356 samples and 0 unreadable CPU/memory. Frozen fresh wall/CPU 205.875 / 203.90625 s, sampled peak WS 1,244,794,880 B, Private 1,295,945,728 B, 4,782 samples, 0 unreadable. Fresh/warm Python and frozen PASS, normalized inventory/all reports/all SQLite tables/all analytic exports/657,738 event parity **all PASS**, owned real workspace cleaned.
- Four reports: total output 2,275,278,631 bytes / 1,394 files. Producer supplied SHA for 1,390 files; four provenance.json files were hashed after generation. Publication reread **2,582 bytes** only, `report.integrity_hash` stage 0.034 s Python and 0.005 s frozen. This proves absence of second full raw-shard read in the measured fresh path, not a zero-cost SHA algorithm: digest CPU now occurs while writing in `generate` and is included in its timing. Interrupted v2 crash recovery STILL rereads and validates every finished file. Custom/injected generators still use full-read fallback.
- Prior real six-run control/candidate gate only compared `75d8226` and `d0132fd`; it is NOT directly the A/B for this new writer. A NEW six-run local A/B `b6902c8` vs `14b45d2` is running separately on an isolated SHA-verified real snapshot, no SSH download included in trial wall. Do not interpret the one-pair 214.921s vs prior 226.400s median as an accepted causal gain.
- Windows original main has uncommitted category-isolation changes. Rehearsal of direct fast-forward in a disposable worktree refused because same generator file is dirty; a read-only 3-way merge probe of old base/new producer/category code exited 0 and retained both changes, but original user file is not modified or stashed. Preserve and reconcile only after acceptance; public Release unchanged.
- Exact-code independent Fable review attempted once via Clean APIs and returned HTTP 502; prior two design attempts also 502. There is no independent APPROVE of this SHA writer yet. Resume independent review on actual code when provider returns HTTP 200; never claim a fictional verdict.
