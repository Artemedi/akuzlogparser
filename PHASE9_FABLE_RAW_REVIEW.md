Я — Claude Fable 5.1, разработан Anthropic.

### Вердикт: `NEEDS-EVIDENCE` / `MODIFY` для bounded-патча, не для Release

Патч `be38fa1` закрывает воспроизведенный `os._exit` orphan `reports/v4_*` без индекса (`PHASE9_PUBLICATION_RECOVERY.md:20-24`), но доказательства ограничены синтетическим `os._exit` в одном процессе, NTFS, без `fsync`, без параллельных писателей и без физического обрыва питания. Для заявленных границ — допустимо, для закрытия Phase 9.0 — недостаточно.

---

#### 1. [HIGH] Утечка stale-intent после краша `after_intent_creation` — ДОКАЗАНО КОДОМ

*   **Где:** `akuz_publication.py:44-70` + `akuz_app.py:177-193`
*   **Предпосылки:** краш между `write_intent()` и `temp.rename(final)`.
*   **Последовательность:** `write_intent` создает `cache/report_intents/<rid>.json` (`draft.replace(marker)` `akuz_publication.py:63`), затем `os._exit` до `temp.rename`. При повторе `recover_report:84` `not final.is_dir() -> return None`, интент не валидируется и не удаляется. `test_phase9_report_crash.py:114-124` явно ожидает `staging[0].exists()` и `assertFalse(result['reused'])` — т.е. старый интент остается навсегда.
*   **Влияние:** Накопление мусора в `cache/report_intents`. `recover_report:114` делает `sorted(glob('v4_*.json'))` на каждый `_publish` — линейный рост. Не удаляется никогда, т.к. `retire_indexed_intent` требует `final.is_dir()`.
*   **Новый баг патча:** Да, введен `report_intents`.
*   **PROVEN FROM CODE** — тест `test_exit_after_intent_does_not_publish_incomplete_staging` доказывает.
*   **Фикс:** В `recover_report` после `if not final.is_dir():` проверять `if (parent / (rid+'.building')).is_dir(): continue` — оставлять, иначе через TTL (напр. 7 дней по `marker.stat().st_mtime`) удалять stale-intent с логированием. Не свипать `reports/v4_*`.
*   **Тест:** Инжект `os._exit` после `write_intent`, затем второй `_publish` с другим `key`, проверить что `len(list(cache/report_intents/*.json))==1` до TTL и `==0` после TTL-чистки.

#### 2. [HIGH] TOCTOU symlink-swap между `is_symlink()` и `open/replace/resolve` — POTENTIAL, требует теста

*   **Где:** `akuz_publication.py:50-51`, `52-54`, `84-87`, `108-115`, `akuz_store.py:93-99`
*   **Последовательность:** `folder.mkdir(...); if folder.is_symlink(): raise` `akuz_publication.py:50-51`. Атакующий между `mkdir` и `is_symlink` заменяет `cache/report_intents` на symlink на `/tmp/evil`. Далее `draft.open('x')` `akuz_publication.py:61` создаст файл вне `root`. Аналогично `recover_report:115-116` `if marker.is_symlink(): continue` затем `marker.read_text()` — замена после проверки.
*   **Влияние:** Запись/чтение вне `root`, обход `relative_to` проверки.
*   **PROVEN:** Код не использует `O_NOFOLLOW`/`open(..., dir_fd)`. `POTENTIAL` — требует гонки, не покрыто тестами.
*   **Фикс:** После `mkdir` открывать `folder` через `os.open(..., O_DIRECTORY|O_NOFOLLOW)` и проверять `fstat`. Для `marker`/`final` использовать `path.lstat()` + `resolve()` атомарно, или `os.open` с `O_NOFOLLOW` перед `read_text`.
*   **Тест:** Фаззер: в отдельном потоке `while True: swap symlink/file` во время `write_intent` 1000 итераций, assert что `marker.resolve().is_relative_to(root.resolve())`.

#### 3. [HIGH] `cached_report` возвращает поврежденный отчет без валидации — ДОКАЗАНО КОДОМ, новый дисбаланс

*   **Где:** `akuz_store.py:79-83` vs `akuz_publication.py:73-105`
*   **Код:** `cached_report` проверяет только `(root/'reports'/result['id']/'index.html').is_file()` `akuz_store.py:81`. Не проверяет `is_symlink`, `catalog.js`, `provenance.json`, `sha256`, `_file_sizes`.
*   **Последовательность:** Отчет проиндексирован, затем `catalog.js` поврежден (как в `test_corrupt_catalog_cannot_be_adopted_or_deleted:145`). Если `key` совпадает, `_publish:149-152` `old = cached_report(...); if old: return reused` — вернет поврежденный отчет без проверки. `recover_report` же делает полную проверку и `fail closed`.
*   **Влияние:** Пользователь получает битый `index.html`/`catalog.js` как `reused=True`. Обход защиты патча.
*   **PROVEN FROM CODE** — сравнение двух путей.
*   **Фикс:** В `cached_report` добавить те же проверки что в `_validated_candidate` для `index.html`/`catalog.js`/`provenance.json` или вызывать `_validated_candidate` с `record` из `store` (если нет `record` — хотя бы `is_symlink` + `sha256`).
*   **Тест:** Создать валидный отчет, повредить `catalog.js`, вызвать `_publish` с тем же `key` — ожидать `reused==False` и генерацию нового `rid`.

#### 4. [MEDIUM] `_file_sizes` хранит только `st_size` для raw-шардов — ДОКАЗАНО КОДОМ, заявлено как ограничение

*   **Где:** `akuz_publication.py:31-41`, `88-89`
*   **Код:** `result[path.relative_to(folder).as_posix()] = path.stat().st_size` `akuz_publication.py:40`. В `PHASE9_PUBLICATION_RECOVERY.md:38-40` прямо: `raw shard contents are not rehashed`.
*   **Последовательность:** Генератор создал `data/raw_0000.js` 1MB, интент записал `size=1048576`. После краша файл перезаписан мусором того же размера. `_validated_candidate:88` `if _file_sizes(final) != record.get('files'): return None` — пройдет, `sha256` проверяется только для 3 файлов `akuz_publication.py:90-98`.
*   **Влияние:** Тихое восстановление поврежденного отчета, аналитика `534` экспортов может разойтись.
*   **PROVEN FROM CODE**.
*   **Фикс:** Для bounded-патча — документировать как `MEDIUM` риск; для полного — хешировать все файлы или хотя бы `raw_*.js` выборочно, с оценкой overhead (`PERFORMANCE_NOTES.md`).
*   **Тест:** После `crash('after_report_rename')` перезаписать `raw_0000.js` same-size мусором, вызвать `_publish` — сейчас `reused==True` (баг), после фикса `reused==False`.

#### 5. [HIGH] Нет `fsync`/`fdatasync` — durability при power-loss не доказана — POTENTIAL, но критично для заявления

*   **Где:** `akuz_publication.py:61-63`, `akuz_store.py:44-45`
*   **Код:** `draft.open('x') -> json.dump -> draft.replace(marker)` без `flush+os.fsync` и без `fsync` директории. `save_store:44-45` `tmp.write_text -> tmp.replace(path)` аналогично.
*   **Тесты:** `test_phase9_inventory_crash.py:15-35` инжектит `os._exit` до/после `Path.replace`, но `replace` на NTFS уже атомарен в рамках процесса. Физический обрыв до сброса кэша ОС не тестируется. `PHASE9_PUBLICATION_RECOVERY.md:98-100` честно: `No claim of physical power-loss durability`.
*   **Влияние:** При power-loss интент может быть пустым/частичным (`{incomplete`), `inventory.json` — усеченным. `recover_report:118-120` `except (OSError, ValueError): continue` оставит мусор, но `load_store:32` `json.loads(path.read_text())` при усеченном `inventory.json` бросит `ValueError` и весь `store` потерян.
*   **PROVEN FROM CODE** — отсутствие `fsync`.
*   **Фикс:** После `json.dump` делать `stream.flush(); os.fsync(stream.fileno()); draft.replace(marker); os.fsync(folder_fd)`. Аналогично для `save_store`. Замерить overhead.
*   **Тест:** Fault-injection с `os.fsync = lambda fd: raise OSError` + симуляция `power-loss` через `libfaketime`/`dm-flakey` или хотя бы проверка что `tmp` после `write_text` не `fsync` — пометить как `NEEDS-EVIDENCE`.

#### 6. [MEDIUM] `_fresh_report_id` уязвим к dangling symlink — POTENTIAL

*   **Где:** `akuz_app.py:139-144`
*   **Код:** `if not (root/'reports'/rid).exists() and not (root/'reports'/(rid+'.building')).exists(): return rid` `akuz_app.py:142`. `Path.exists()` для dangling symlink возвращает `False`.
*   **Последовательность:** Атакующий создает `reports/v4_20260927_120000_aaaaaaaa -> /nonexistent`. `_fresh_report_id` вернет тот же `rid`, `temp = parent/(rid+'.building')` создастся, `temp.rename(final)` `akuz_app.py:178` заменит symlink на директорию (или упадет с `FileExistsError` на POSIX). На Windows `rename` поверх symlink может следовать по ссылке.
*   **PROVEN FROM CODE** — логика `exists()`.
*   **Фикс:** Использовать `path.lexists()` или `path.is_symlink() or path.exists()` и `mkdir(..., exist_ok=False)` с обработкой `FileExistsError`.
*   **Тест:** Предсоздать dangling symlink `reports/v4_...`, вызвать `_publish` — ожидать `FileExistsError` или новый `rid`, не перезапись.

#### 7. [MEDIUM] `recover_report` `ValueError` на конфликте приводит к livelock — ДОКАЗАНО КОДОМ

*   **Где:** `akuz_publication.py:128-130`
*   **Код:** `if rid in store['reports']: if store['reports'][rid] != value: raise ValueError('Publication intent conflicts with inventory')` `akuz_publication.py:129-130`.
*   **Последовательность:** Интент остался после краша, затем оператор вручную изменил `inventory.json` (или параллельный писатель). Каждый `_publish` будет бросать `ValueError`, интент никогда не удалится (`retire_intent` не вызывается), повтор не помогает.
*   **Влияние:** Denial-of-service для этого `key`.
*   **PROVEN FROM CODE**.
*   **Фикс:** Вместо `raise` — `continue` (fail closed) + логирование в `perf_event`, оставить интент для диагностики, но не блокировать создание нового отчета с новым `rid`.
*   **Тест:** Создать интент, затем `store['reports'][rid]=different_value; save_store`, вызвать `_publish` — ожидать не `ValueError`, а `reused==False` и новый отчет.

#### 8. [MEDIUM] Параллельные писатели не протестированы, `FileExistsError` на интент не обрабатывается — POTENTIAL

*   **Где:** `akuz_publication.py:53-54`, `akuz_app.py:156`
*   **Код:** `if marker.exists() or draft.exists(): raise FileExistsError` `akuz_publication.py:53-54`. `_publish` не ловит `FileExistsError` отдельно, падает в `finally: retire_intent(intent)` где `intent is None`, `temp` удаляется, но `marker` остается от первого писателя.
*   **Последовательность:** Два процесса одновременно `_fresh_report_id` вернули один `rid` (коллизия `secrets.token_hex(4)` 32 бита, 10 попыток). Второй `write_intent` бросит `FileExistsError`, первый успешно опубликует. Второй ретрай сгенерирует новый `rid` — ок, но тест отсутствует.
*   **Влияние:** Потеря производительности, возможный orphan `*.building`.
*   **POTENTIAL** — требует concurrency-теста.
*   **Фикс:** В `_publish` ловить `FileExistsError` от `write_intent` и ретраить `_fresh_report_id` (до 3 раз).
*   **Тест:** Запустить 10 процессов `multiprocessing` одновременно `_publish` с одним `root` и разными `key`, проверить отсутствие `FileExistsError` наружу и `len(reports)==10`.

#### 9. [LOW] `marker.with_suffix('.json.tmp')` семантически неверен — ДОКАЗАНО КОДОМ, но работает

*   **Где:** `akuz_publication.py:52`
*   **Код:** `draft = marker.with_suffix('.json.tmp')` для `rid.json` дает `rid.json.tmp` (замена `.json` на `.json.tmp`). Ожидалось `rid.json.tmp` — совпало случайно, но для `rid` с точкой сломается. Более явно `marker.with_name(marker.name + '.tmp')`.
*   **Влияние:** Путаница, будущий баг если `rid` формат изменится.
*   **PROVEN FROM CODE**.
*   **Фикс:** `draft = marker.with_suffix(marker.suffix + '.tmp')` или `Path(str(marker)+'.tmp')`.
*   **Тест:** Unit-тест `assert intent_path(root,'v4_20200101_000000_aaaaaaaa').with_suffix('.json.tmp').name == 'v4_..._aaaaaaaa.json.tmp'`.

#### 10. [MEDIUM] `retire_intent` глотает `OSError` — тихая утечка при `permission failure` — ДОКАЗАНО КОДОМ

*   **Где:** `akuz_publication.py:22-28`, `akuz_app.py:190-191`
*   **Код:** `try: path.unlink(missing_ok=True) except OSError: pass` `akuz_publication.py:25-28`. `finally: if intent is not None: retire_intent(intent)` `akuz_app.py:190-191` не проверяет успех.
*   **Последовательность:** `save_store` успешен, `retire_intent` падает из-за `chmod 0` на `cache/report_intents`. Отчет проиндексирован, но интент остается. Следующий `recover_report` снова найдет интент, увидит `rid in store` и вернет `reused=True` (идемпотентно), но интент никогда не удалится — вечный `glob`.
*   **PROVEN FROM CODE**.
*   **Фикс:** Логировать `OSError` в `perf_event`, или ретраить `unlink` с `os.chmod`.
*   **Тест:** `chmod 0` на `report_intents`, вызвать `_publish`, проверить что `retire_intent` не бросил, но `marker` остался и второй вызов все еще `reused`.

---

### Что тесты покрывают и что нет

*   **Покрыто (синтетика):** `after_report_rename`, `after_inventory_save`, `after_intent_creation`, `corrupt_catalog`, `forged_traversal`, `recovery_inventory_save_failure`, `rename_failure`, `missing_raw_shard`, `corrupt_intent`, `intent_replace_failure` — все через `os._exit`/`patch.object`.
*   **Не покрыто:** физическое отключение питания, `fsync` директории, параллельные писатели, `disk full`/`ENOSPC` на `json.dump`, `chmod`/`permission`, `hard link`, `case-insensitive` NTFS коллизии, `TemporaryDirectory` cleanup при краше внутри `SpoolWriter`.

### Итог

Патч корректно реализует **ownership-validated recovery** для `os._exit` границы и не свипает чужие `reports/v4_*` — это улучшение относительно `2917d28`. Однако для закрытия Phase 9.0 требуются:

1. `fsync` + директория `fsync` или явный отказ от power-loss гарантий в релиз-нотах,
2. исправление `cached_report` валидации,
3. TTL-чистка stale-intent,
4. concurrency-тест.

До этого — **MODIFY / NEEDS-EVIDENCE**, bounded-гейт `P9-0R-01..P9-0X-02` считать `PASS` только для `os._exit`, не для durability.
