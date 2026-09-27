Независимый bounded review SHA 642a083, только фиксы 8cb7985:

**1. Маппинг OSError -> Busy - ИСПРАВЛЕНО:**
`akuz_instance_lock.py:40-42` (nt) и `52-54` (posix) теперь `if exc.errno in (EACCES,EAGAIN,EDEADLK): raise InstanceBusy` иначе `raise`. `akuz_store_lock.py:59-62` (nt) аналогично, `68-71` (posix) `in (EACCES,EAGAIN)`. Любой `EBADF/EINVAL` и т.п. пробрасывается как `OSError`. Подтверждено тестом `tests/test_phase9_lock_errors.py:14-25` - `EBADF` не становится `InstanceBusy` и владелец переиспользуем, `27-33` - `EACCES` -> `InstanceBusy`.

**2. Ненормализованный ключ реестра Windows - ИСПРАВЛЕНО:**
`akuz_store_lock.py:40-43` `path=marker.resolve(); key=os.path.normcase(str(path))` + `_REGISTRY[key]`. Альтернативное написание регистра теперь шарит один `RLock` и OS-лок. Подтверждено `tests/test_phase9_lock_errors.py:36-40` `swapcase` -> `assertIs`.

**Регрессия - НЕТ:**
Патч не вводит регрессию. `inventory_transaction:101-103` корректно закрывает `stream` при `OSError` до инкремента `depth`. `exclusive_instance` не держит лок при пробросе. Несоответствие `EDEADLK` (`akuz_store_lock.py:68` без него, `akuz_instance_lock.py:53` с ним) безвредно - `fcntl.flock` не возвращает `EDEADLK`, для единообразия можно добавить, не критично.

Известные вне скоупа и не заявленные как исправленные: hostile TOCTOU между `is_redirected_path` и `mkdir/open` (`akuz_path_guard.py:1-5`, `akuz_store_lock.py:37` комментарий), физический power-cut/fsync, старые некооперирующиеся версии - оговорены в docstring `akuz_instance_lock.py:1-5`.

**Вердикт: APPROVE** - оба замечания ee699af реально исправлены, регрессий нет.
