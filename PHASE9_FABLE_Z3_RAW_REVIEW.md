**Read-only review SHA `1cea8b1` - только `report-ID allocation`**

Проверен точный код по строкам. Новый патч - только `akuz_app.py:139-147` + использование в `akuz_app.py:159`. Остальное `akuz_publication.py`, `akuz_store.py` - пресуществующий контекст, не ревьюится как новое.

### 1. Проверка 4-х резерваций включая висячие симлинки - КОРРЕКТНО. Дефекта нет.

```python
# akuz_app.py:145
if all(not path.exists() and not path.is_symlink() for path in reserved):
```
`reserved = (root/reports/rid, root/reports/rid.building, marker, draft)` - `akuz_app.py:144`

* `exists()==False + is_symlink()==True` = висячий симлинк -> считается занятым. Верно.
* `exists()==True` = обычный файл/директория/валидный симлинк -> занято. Верно.
* Покрывает атаку `ln -s /etc/passwd reports/v4_...` и `ln -s /nonexistent cache/report_intents/v4_....json`

Доказано тестами `test_phase9_report_id.py:42-58` - не удаляют чужое. Гипотетический пробел: `root/cache/report_intents` как симлинк не проверяется в `_fresh_report_id`, но ловится позже `akuz_publication.py:51-52` `if folder.is_symlink(): raise`. Это не дефект аллокации, поведение детерминировано - `ValueError`, а не тихая перезапись.

### 2. Ошибочных удалений и захвата чужого маркера - НЕТ. Доказано.

* `_fresh_report_id` только читает, ничего не `unlink`/`rmtree`.
* `write_intent:54-55` `if marker.exists() or draft.exists(): raise FileExistsError` - не удаляет.
* `retire_intent:27` `unlink(missing_ok=True)` вызывается только для `intent` возвращенного `write_intent` текущего `rid` - `akuz_app.py:180,194` - владение доказано.
* `recover_report:145,161,169` и `retire_indexed_intent:181-192` удаляют только после `_validated_candidate` + проверки `is_file() and not is_symlink()` + сверки `sources/kind/events` и хешей. Коррумпированные `*.json` пропускаются `continue` `akuz_publication.py:150` - не удаляются.

Тесты `test_orphan_marker_collision_retries_without_deleting_it:48-49` и `test_orphan_draft_collision_retries_without_deleting_it:57-58` детерминировано проверяют `read_text` до/после.

### 3. Гонка check-then-reserve (TOCTOU) - ГИПОТЕТИЧЕСКИЙ, вне заявленных гарантий. Доказанного дефекта в single-process нет.

`_fresh_report_id` проверяет, возвращает `rid`, резервация происходит только в `write_intent` созданием `draft` `.json.tmp`. Между `145` и `180` другой процесс может занять тот же `rid`.

В single-process (заявленная модель) гонки нет - `secrets.token_hex(4)` 32 бита + `datetime` секундная гранулярность, 10 попыток достаточно. В multiprocess второй процесс получит `FileExistsError` из `write_intent:55` и упадет наружу, а не ретрайнит. Патч это не ухудшает и не заявляет `multiprocess safety` - честно.

*Отделено:* это не доказанный дефект патча, а ограничение дизайна. Если хотите закрыть гипотетический кейс - минимальный фикс:

```python
# akuz_app.py:159-160
for _ in range(10):
    try:
        rid = _fresh_report_id(root)
        intent = write_intent(...)
        break
    except FileExistsError:
        continue
else: raise FetchError
```
Сейчас такого ретрая нет, но и не требуется по ТЗ.

### 4. Детерминизм трех тестов - ДОКАЗАН, скептицизм не подтвердился.

`test_phase9_report_id.py:35-37` `patch('akuz_app.datetime')` + `patch('akuz_app.secrets.token_hex', side_effect=tokens)` - корректные таргеты (`from datetime import datetime`, `import secrets` в `akuz_app`). `STAMP` фиксирован, `token_hex` детерминирован. `test_ten_reserved_ids_fail:65` 10 одинаковых токенов -> 10 коллизий -> `FetchError` детерминирован.

`test_phase9_report_crash.py:29-48` `os._exit` в дочернем процессе оставляет `.building`/`intent`/`inventory` в наблюдаемом состоянии, родительский `load_store` перечитывает. `sorted(glob)` в `recover_report:144` детерминирован. Флаков нет.

Замечание: `marker.with_suffix('.json.tmp')` `akuz_app.py:143` работает т.к. `Path('a.json').with_suffix('.json.tmp') == 'a.json.tmp'` - корректно, но хрупко. Тест на `draft` это покрывает.

### 5. Фиксит ли реальный старый отказ без завышенных заявлений - ДА.

Старый отказ: сиротский `cache/report_intents/v4_*.json` или `.json.tmp` от краша до `rename` приводил к `FileExistsError` в `write_intent` и аборту новой публикации. Патч `139-147` теперь скипает `rid` если любой из 4 путей занят и ретрайнит до 10 раз. Тесты `42-58` это прямо доказывают. Не заявляет атомарности межпроцессно - честно.

**Минимальные доп. тесты/фиксы (не блокируют APPROVE):**
1. Тест на висячий симлинк: `for p in reserved: p.symlink_to('/nonexistent'); assert _fresh_report_id != that rid`
2. Тест на коллизию `reports/rid` и `reports/rid.building` (сейчас покрыты только `intent`/`draft`).
3. Документировать в docstring `_fresh_report_id` что `TOCTOU` вне гарантий single-process.

**Вердикт для узкого изменения: APPROVE**

Патч минимален, не удаляет чужие доказательства, корректно обрабатывает симлинки/висячие симлинки, детерминирован, фиксит реальный краш-консистентный отказ. Многопроцессная гонка - гипотетическая и явно вне скоупа.
