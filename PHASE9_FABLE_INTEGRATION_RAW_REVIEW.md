**Ревью SHA 68305e7 - bounded change: `inventory.lock` + `pre-publish revision` + `exclusive_instance`**

Ядро корректно: `flock`/`msvcrt` освобождается смертью процесса, реентерабельность через `RLock+depth` верна, `stale revision` -> `InventoryConflictError` fail-closed, `post-replace` чтение `inventory.json` устранено, `port conflict` освобождает `exclusive_instance` через exit процесса. `fsync`/power-loss вне скоупа - не оценивалось.

#### Проверенные новые дефекты этого изменения (max 5)

**1. `akuz_store_lock.py:31,82-83` - нет защиты от symlink для `inventory.lock`, в отличие от `akuz_instance_lock.py:20-24`**
*Предпосылки:* `cache` - symlink на другой каталог или `cache/inventory.lock` - symlink. `inventory_transaction` делает `mkdir(parents=True)` и `open('a+b')` без `is_symlink()` проверки.
*Эффект:* лок ставится в чужом каталоге, изоляция `root` обходится. `exclusive_instance` это ловит, `inventory_transaction` - нет.
*Тест:* `cache.symlink_to(tmp2); with inventory_transaction(root): pass` - не бросает, создает файл в `tmp2`.
*Фикс:* скопировать гард из `akuz_instance_lock.py:20-24` перед `mkdir/open` + проверка `lock.is_symlink()` + `O_NOFOLLOW` или `fstat` после `open`.

**2. `akuz_instance_lock.py:18-25` и `akuz_store_lock.py:82-83` - TOCTOU `is_symlink() -> open()`**
*Предпосылки:* проверка `is_symlink()` делается до `open()`. Атакующий меняет `cache` на symlink в окне.
*Эффект:* обход гарда п.1 даже после фикса. Доказан кодом, не эксплуатацией в тестах.
*Фикс:* `open(..., O_NOFOLLOW)` на Linux, на Windows - `open` + `GetFileInformationByHandle`/`is_symlink` по fd, или `path.resolve()` + сравнение `parent.resolve()`.

**3. `akuz_app.py:801-814` - ручной `owner.__enter__()` без `__exit__` при `p.error()`**
*Предпосылки:* `prepare_runtime()` или `ThreadingHTTPServer(...).bind` бросает `OSError` -> `p.error()` -> `SystemExit(2)`.
*Эффект:* в subprocess-тесте `test_failed_port_bind_releases_root_guard:96` лок освобождается смертью процесса - тест проходит. При in-process вызове `main()` (импорт, повторный вызов) `exclusive_instance` утекает до GC/закрытия fd, второй `main()` в том же процессе получит ложный `InstanceBusy` или наоборот не освободит.
*Фикс:* `with exclusive_instance(ROOT):` вокруг `prepare_runtime`+`serve_forever`, или `try: owner.__enter__() ... finally: owner.__exit__()` перед `p.error`.

**4. `akuz_store.py:64` - обход optimistic concurrency**
```python
if hasattr(data,'_inventory_revision') and expected != current: raise
```
*Предпосылки:* `save_store(root, plain_dict)` без атрибута `_inventory_revision` (старый клиент, ручная сборка `dict`).
*Эффект:* проверка пропускается, `lost update` без `InventoryConflictError`. `load_store` всегда ставит атрибут, но контракт не enforced.
*Тест:* `d={...}; save_store(root,d)` параллельно с другим `save_store` - второй молча перезаписывает.
*Фикс:* `if expected is not None and expected != current` или требовать `hasattr` и падать если нет при существующем `inventory.json`.

**5. `akuz_app.py:761-764` - `HTTP 409` только по `state.busy`, не по `inventory_transaction`**
*Предпосылки:* внешний процесс держит `inventory.lock` (`test_phase9_inventory_multiprocess:61`). HTTP `POST /api/build` проверяет только `state.busy`.
*Эффект:* клиент получает `202 Accepted`, а `InventoryBusyError` всплывает асинхронно в воркере (`state.error`), а не `409` сразу. Функциональные `perform_build/clear/update_source_date:114` корректно бросают `InventoryBusyError`, HTTP - нет.
*Фикс:* пробовать `inventory_transaction` non-blocking в хендлере до `202` или маппить `InventoryBusyError` воркера в `409` при поллинге. Низкий приоритет - не краш-корректность.

#### Гипотетическое / старое - не дефект этого SHA

* `flock` реентерабельность в одном процессе через разные fd - не баг, покрыто `RLock+depth`.
* `Windows msvcrt.LK_NBLCK` на 1 байт - корректно, `EACCES/EAGAIN/EDEADLK` маппинг верен.
* Старые клиенты без `inventory_transaction` - ловятся `revision` проверкой, не OS-локом - by design.
* `port` занят vs `root` занят - раздельные домены, `exclusive_instance` не зависит от порта - by design, тест `test_two_real_servers_different_ports:54` доказывает.
* `clear_cache`/`report_intact` symlink проверки - старый код, не изменен.

#### Вердикт для bounded change

**MODIFY** - краш/concurrency ядро APPROVE, но требуются минорные правки п.1+п.3 (п.2 - ужесточение) до релиза. Без них - изоляция `root` неполна при symlink и утечка гарда при in-process ошибке старта.
