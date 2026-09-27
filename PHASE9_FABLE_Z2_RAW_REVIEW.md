**Вердикт: MODIFY / NEEDS-EVIDENCE для bounded-кандидата d0132fd. Для Release - не готов. Основания ниже - PROVEN по коду, часть POTENTIAL требует стенда.**

Патч вводит узкий intent `cache/report_intents/v4_*.json` до `rename .building -> final` и валидированное восстановление только своего `reports/rid`. Идея верна, тесты на `os._exit` покрывают 3 границы. Но сам патч создает новые утечки/гонки, durability питания и параллельные писатели не доказаны, как и заявлено в `PHASE9_PUBLICATION_RECOVERY.md:108-110`.

### 1. [MEDIUM] Утечка orphan intent + `.building` после краша между `write_intent` и `rename` - НОВЫЙ БАГ ПАТЧА - PROVEN

*   **Где:** `akuz_publication.py:45-87` `write_intent` создает `draft.replace(marker)` до `akuz_app.py:178 temp.rename(final)`. `akuz_app.py:189-193 finally: retire_intent(intent); rmtree(temp)` выполняется только для текущего `rid`.
*   **Предпосылки:** Любой `os._exit`/kill после `write_intent` успешно, до `rename`.
*   **Последовательность:**
    1. `_publish` сгенерировал `rid1`, `write_intent(root,value)` -> `cache/report_intents/rid1.json` durable, `reports/rid1.building` полный.
    2. `os._exit(70)` до `temp.rename`.
    3. Retry: `recover_report:90-135` вызывает `_validated_candidate:101 if not final.is_dir(): return None` -> intent `rid1` пропускается, не удаляется.
    4. `_fresh_report_id:139-143` генерирует `rid2 != rid1`, публикует `rid2`. `rid1.json` и `rid1.building` остаются навсегда. `glob v4_*.json` растет.
*   **Влияние:** Засор `cache`, ложные срабатывания диагностики, в тесте `test_phase9_report_crash.py:114-124` `assertTrue(staging[0].exists()) # No global cleanup` это закрепляет, но не проверяет `report_intents` - утечка скрыта.
*   **Фикс:** В `recover_report` после `final.is_dir()==False` и `*.building` существует - явно удалять соответствующий `marker` если `record.files == _file_sizes(building)` и `building` старше N часов, либо в `_publish` при `FileExistsError` чистить `draft`+`building` того же `key`. Минимум: `retire_intent` для невалидного `final` с истекшим TTL.
*   **Регресс:** Крэш-тест `after_intent_creation` + assert `len(list((root/"cache"/"report_intents").glob("*.json")))==1` до retry и `==0` после успешной публикации `rid2` + проверка что `rid1.building` удален/карантин.

### 2. [LOW] Коллизия `rid` из-за проверки только `reports/` - PROVEN

*   **Где:** `akuz_app.py:139-143` `_fresh_report_id` проверяет `not (root/"reports"/rid).exists() and not (root/"reports"/(rid+".building")).exists()` но не `cache/report_intents/rid.json`. `akuz_publication.py:54-55 if marker.exists() or draft.exists(): raise FileExistsError`.
*   **Предпосылки:** Orphan intent из (1) остался, `secrets.token_hex(4)` 32 бита + та же секунда `strftime`.
*   **Последовательность:** Retry генерирует тот же `rid1` -> `write_intent` бросает `FileExistsError` -> `_publish` падает, `temp` удаляется, но orphan intent остается, build не восстанавливается.
*   **Влияние:** Редкий, но детерминированный отказ повторной публикации после краша.
*   **Фикс:** `if (root/"cache"/"report_intents"/(rid+".json")).exists(): continue` в `_fresh_report_id`.
*   **Регресс:** Мок `secrets.token_hex` вернуть фиксированное значение, создать orphan `rid.json`, вызвать `_publish` -> должен сгенерировать `rid`+1 без исключения.

### 3. [MEDIUM] TOCTOU symlink/traversal между `_file_sizes` и `sha256` - POTENTIAL, требует теста

*   **Где:** `akuz_publication.py:32-42 _file_sizes` проверяет `is_symlink()` и `resolve().relative_to()`, затем `akuz_publication.py:60-69` и `115-117` отдельно `sha256(final/name)` без повторной проверки symlink. `akuz_publication.py:51-52 folder.mkdir(...); if folder.is_symlink(): raise` - проверка после `mkdir`.
*   **Предпосылки:** Локальный writer с правами на `cache`/`reports` меняет файл на symlink между двумя системными вызовами.
*   **Последовательность:** `_file_sizes` прошел, атакующий заменяет `reports/rid/data/catalog.js` на symlink -> `sha256` читает за пределами `reports`. В `write_intent` аналогично `folder` может стать symlink после проверки до `draft.open("x")`.
*   **Влияние:** Чтение/запись вне `root`, обход `relative_to`. На практике требует локального доступа, но патч заявляет защиту `PHASE9_PUBLICATION_RECOVERY.md:55-59`.
*   **Фикс:** Перед каждым `sha256` проверять `path.is_symlink()` и `path.resolve().relative_to(folder.resolve())` атомарно; для `write_intent` открывать `draft` через `os.open(O_CREAT|O_EXCL|O_NOFOLLOW)` и `mkdir` без `exist_ok` + `lstat`.
*   **Регресс:** Fault-injection: патч `Path.is_symlink` вернуть `False` в `_file_sizes` и `True` перед `sha256` -> `_validated_candidate` должен вернуть `None`, не `ValueError`.

### 4. [HIGH] Отсутствие `fsync`/`fsync(dir)` для intent и inventory - durability питания не доказана - POTENTIAL, заявлено как OPEN

*   **Где:** `akuz_publication.py:78-80 draft.open("x"); json.dump; draft.replace(marker)` без `flush+fsync` и `fsync(parent)`. `akuz_store.py:44-45 tmp.write_text; tmp.replace(path)` аналогично.
*   **Предпосылки:** Отключение питания/BSOD после `replace` до сброса кэша ОС на NTFS.
*   **Последовательность:** `write_intent` вернул, `rename` еще в кэше -> после ребута ни `marker`, ни `final` не durable -> orphan `*.building` без intent, невосстановим. Или `save_store` после `replace` потерян -> `reports/rid` durable, но `inventory.json` откатился -> recovery сработает, но без `fsync` может быть `inventory.json.tmp` частично записан (тест `test_phase9_inventory_crash.py:57-68` проверяет только `os._exit`, не питание).
*   **Влияние:** Заявленный в `PHASE9_PUBLICATION_RECOVERY.md:108` риск подтвержден кодом. Тесты `test_phase9_inventory_atomicity.py:32-42` мокают `Path.replace`, не реальный сбой диска.
*   **Фикс:** После `json.dump` -> `stream.flush(); os.fsync(stream.fileno()); draft.replace(marker); os.fsync(marker.parent)`; аналогично для `inventory.json`. Документировать требование NTFS `FlushFileBuffers`.
*   **Регресс:** Тест с `os.fsync` мок, проверяющий вызов, + интеграционный тест с `sync` и выдергиванием питания (вне CI).

### 5. [MEDIUM] `recover_report` конфликт `ValueError` абортит build вместо карантина - PROVEN

*   **Где:** `akuz_publication.py:158-161 if rid in store["reports"]: if store["reports"][rid] != value: raise ValueError("Publication intent conflicts with inventory")`.
*   **Предпосылки:** `inventory.json` уже содержит `rid` с другим `value` (ручная правка, предыдущий баг, параллельный writer).
*   **Последовательность:** `recover_report` бросает, `_publish:153 recovered = recover_report(...)` не ловит -> `perform_build` падает, `intent` не удаляется, `reports/rid` остается, повторный retry снова бросает.
*   **Влияние:** DoS для оператора, нет карантина как в `akuz_store.py:118-123` для `cached_report`.
*   **Фикс:** Вместо `raise` - `return None` и логировать, либо помечать `invalidated` и требовать ручной разбор. Минимум: `except ValueError` в `_publish` -> `retire_intent` + генерация нового `rid`.
*   **Регресс:** Создать `inventory` с `rid` и другим `key`, создать валидный `marker`+`final` для того же `rid` -> `_publish` должен не бросить, а создать `rid2` и оставить `rid1` нетронутым.

### 6. [LOW] Утечка derived spool `TemporaryDirectory` после краша - PROVEN, новый код

*   **Где:** `akuz_app.py:281-284 with TemporaryDirectory(dir=root/"cache") as folder: return _perform_build(..., Path(folder))` + `akuz_derived_spool.py:40-42 SpoolWriter.__enter__: path.open("x")`. `akuz_app.py:442 if not report["reused"]: spools[...] = (spool_path, sink.sha256)` корректно не регистрирует пустой spool (фикс `PHASE9_PUBLICATION_RECOVERY.md:71-78`), но сам `folder` при `os._exit(79)` в `test_phase9_recovery_spool.py:24-28` не удаляется.
*   **Предпосылки:** Краш после первого `SpoolWriter` внутри `TemporaryDirectory`.
*   **Последовательность:** `perform_build` создал `cache/akuz-phase9-derived-XXXX/0000.jsonl`, `os._exit` -> `__exit__` `TemporaryDirectory` не вызван -> каталог остается в `cache`.
*   **Влияние:** Мусор в `cache`, содержит `headline` с пациентскими данными (`akuz_derived_spool.py:1-4` предупреждение). Документ признает `PHASE9_PUBLICATION_RECOVERY.md:82` "NOT globally cleaned", но не ограничивает рост.
*   **Фикс:** При старте `perform_build` чистить `cache/akuz-phase9-derived-*` старше 24ч, или писать spool в `reports/rid.building` вместо глобального `cache`.
*   **Регресс:** Крэш-тест `CRASH_ON_FIRST_REPORT` + assert `len(list((actual/"cache").glob("akuz-phase9-derived-*")))==0` после успешного retry.

**Итог по границам:**
- `intent write/replace` `akuz_publication.py:78-80` - атомарен для `os._exit`, но не для питания (4).
- `report rename` `akuz_app.py:178` - атомарен, восстановление `recover_report:101-117` с полной `all_sha256` для v2 - PROVEN, для v1 только `required_sha256` - осознанно ограничено.
- `inventory write/replace` `akuz_store.py:44-45` - атомарен для `os._exit` (тесты `test_phase9_inventory_crash.py` проходят), но без `fsync`.
- `marker cleanup` `akuz_publication.py:23-29` глотает `OSError` - корректно, но оставляет orphan (1).
- `cache identity` `akuz_store.py:79-107 report_intact` проверяет `required_sha256` + размеры, но не `all_sha256` на warm hit - по дизайну, same-size raw corruption для indexed отчетов не ловится до следующего `cached_report` - PROVEN, задокументировано.
- `derived spool` - фикс пустого writer PROVEN тестом `test_phase9_recovery_spool.py:36-57`, но утечка директории остается.

**Рекомендация:** Патч сужает окно потери с orphan-директории до orphan-intent, добавляет проверку `all_sha256` - ценно. Для bounded-применения (синтетика, один writer, NTFS без выдергивания питания) - **MODIFY** с исправлением (1)(2)(5). Для Release - **NEEDS-EVIDENCE**: реальный SSH, frozen EXE, 3+ A/B, конкурентные писатели, `fsync`+power-loss gate остаются OPEN, как указано в `PHASE9_PUBLICATION_RECOVERY.md:10-12`.
