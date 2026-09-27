### Независимый bounded review SHA `ee699af` — только показанный diff

**Контекст:** Проверяется точный код `akuz_path_guard.py`, `akuz_store_lock.py`, `akuz_instance_lock.py` + тесты `test_phase9_inventory_lock_symlink.py`, `test_phase9_single_instance.py`. Логика `optimistic revision-before-rename / stale snapshot / postcommit cleanup` в показанном diff отсутствует — потеря отчетов по ней в рамках bounded review не верифицируема. Предыдущий вердикт Fable `MODIFY` по `68305e7` (утечка лока на `SystemExit`) в `ee699af` исправлен — `finally` + `with` корректно освобождают `msvcrt.locking`/`fcntl.flock` и `threading.RLock`.

#### Детально по файлам

`akuz_path_guard.py:11-19` — корректно: `is_symlink()` + `lstat().st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT`. Покрывает NTFS junction ( `is_symlink()==False` на Windows) и symlink на Linux. Статический guard, не атомарный — в коде честно задокументировано `This is a static guard, not atomic anti-TOCTOU` — это не баг для bounded изменения.

`akuz_store_lock.py:32-47, 82-115` — `inventory_transaction` корректно: `threading.RLock` + `depth` для реентерабельности, `mkdir` внутри `depth==0`, `a+b` + запись `b'\0'` для `msvcrt.locking`, `try/except: close; raise` при ошибке лока, `try/finally: _unlock_os; close` + `local.release()` в outer `finally` — работает и на `SystemExit`/`KeyboardInterrupt` ( `BaseException` ).

`akuz_instance_lock.py:19-54` — `exclusive_instance` корректно держит `with open('a+b')` весь `yield`, `seek(0)` перед `lock/unlock`, `flush()` после записи первого байта. Крэш процесса освобождает OS lock.

`tests/test_phase9_inventory_lock_symlink.py:19-71` — покрывает symlink cache, symlink file, NTFS junction для обоих локов, проверяет отсутствие `outside/inventory.lock`. `tests/test_phase9_single_instance.py:85-123` — `test_failed_port_bind_releases_root_guard` и `test_in_process_startup_errors_release_owner` ( `patch` `prepare_runtime`/`ThreadingHTTPServer` + `SystemExit` ) — закрывает ложное срабатывание Fable по `68305e7`.

#### MAX 3 проблемы — только новое, не покрытое

**[REAL-1] `akuz_instance_lock.py:38-39, 48-50` — широкая маскировка `OSError` под `InstanceBusy`**
*Суть:* Любая `OSError` от `msvcrt.locking`/`fcntl.flock` мапится в `InstanceBusy`, в отличие от `akuz_store_lock.py:57,66` где проверяются `errno.EACCES/EAGAIN/EDEADLK`.
*Repro:*
```python
# Linux: сделать cache read-only или исчерпать fd
Path(root/"cache").mkdir(parents=True)
os.chmod(root/"cache", 0o500) # или замокать fcntl.flock -> OSError(errno.EBADF)
with exclusive_instance(root): pass # получите InstanceBusy вместо исходной ошибки
# Windows: аналогично EINVAL при битом handle
```
*Эффект:* Диагностика ломается, `read-only FS` / `EBADF` выглядит как "занято другим экземпляром".
*Fix минимальный (как в store_lock):*
```python
except OSError as exc:
    if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, getattr(errno,'EWOULDBLOCK', errno.EAGAIN)):
        raise InstanceBusy(...) from exc
    raise
```
*Тест которого нет:* `test_instance_lock_non_busy_oserror_not_masked` — замокать `fcntl.flock` -> `OSError(EBADF)` и проверить что `InstanceBusy` не поднимается.

**[REAL-2] `akuz_store_lock.py:40-41` — ключ реестра не нормализован для Windows case-insensitive FS**
*Суть:* `key = str(marker.resolve())` чувствителен к регистру. На NTFS `C:\App\cache\inventory.lock` и `c:\app\cache\inventory.lock` — один файл, но два разных `_RootLock`, `threading.RLock` не защищает.
*Repro (Windows):*
```python
root1 = Path("C:/tmp/AKUZ_APP")
root2 = Path("c:/tmp/akuz_app") # тот же каталог
# два потока: with inventory_transaction(root1): sleep(1) и одновременно inventory_transaction(root2) — не блокируется
```
*Fix минимальный:*
```python
path = marker.resolve()
key = os.path.normcase(str(path)) # + normpath при необходимости
```
На Linux `normcase` — no-op, регрессии нет. Это не TOCTOU, а реальная логическая ошибка.

**[HYPOTHETICAL — не требует MODIFY] `akuz_store_lock.py:38-40,91-92` и `akuz_instance_lock.py:21-27` — TOCTOU между `is_redirected_path` и `mkdir/open`**
*Суть:* Проверка `is_redirected_path(cache/marker)` -> `mkdir`/`open`. Конкурентная замена `cache` на symlink/junction в окне — теоретически обходит guard.
*Классификация:* Гипотетический adversarial TOCTOU, явно задекларирован в коде как `static guard, not atomic` и `concurrent hostile path swap is a separate, unproven TOCTOU case`. Для bounded изменения без `O_NOFOLLOW`/`openat2`/`FILE_FLAG_OPEN_REPARSE_POINT` это принятый компромисс. Не путать с реальными багами выше. Старые клиенты, не чтящие advisory `flock`/`locking`, и физическое отключение питания — вне скоупа, как указано в docstring `NOT arbitrary external writers / not physical power loss`.

*Дополнительно (не считается за issue, но для полноты):* `akuz_path_guard.py:16-18` ловит только `FileNotFoundError` от `lstat()`. `PermissionError` пробросится наружу — безопаснее `except OSError: return False`. Низкая серьезность, не блокирует.

#### Вердикт для bounded текущего изменения

**MODIFY** — по двум реальным, легко чинимым проблемам ( `REAL-1` маскировка errno, `REAL-2` нормализация ключа на Windows ). После 2-строчных фиксов + одного негативного теста на `EBADF` — **APPROVE** для bounded скоупа. Публичной рекомендации к релизу нет — `revision-before-rename` / `stale snapshot` / `postcommit cleanup` вне показанного diff и требует отдельного evidence.

*Что НЕ является основанием для MODIFY:* гипотетический TOCTOU, advisory характер `flock` для старых клиентов, потеря данных при `power-loss` — все это вне гарантий текущего guard/lock.
