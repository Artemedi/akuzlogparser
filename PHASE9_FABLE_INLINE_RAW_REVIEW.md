Ограниченный код SHA 14b45d2 — проверка bounded-части:

**Windows newline byte equivalence — дефектов нет.**
`akuz_report_writer.py:27-29` — `part.replace('\n', os.linesep)` до `encode('utf-8')` поблочно эквивалентно `Path.write_text(encoding='utf-8')` с `newline=None` (`\n` -> `os.linesep`). `\n` — один символ, граница чанка `_CHARS=262144` не влияет. Хеш считается от `payload` после трансляции — байт-эквивалентность подтверждена гейтом.

**UTF-8 / граница чанка — дефектов нет.**
`akuz_report_writer.py:25-29` — нарезка по `len(text)` (кодпоинты), затем `encode` каждого `part`. Разрыв суррогатов/UTF-8 невозможен, хеш `digest.update(payload)` поблочно = хешу целого файла.

**Partial write / close — дефектов нет.**
`akuz_report_writer.py:30-34` — `written != len(payload)` -> `OSError`, `digest.update` только после успешной записи, `size` по `written`. `with path.open('wb')` — ошибка `close()` пробрасывается, `return` после `with`, публикация невозможна. Соответствует требованию "Close errors are propagated".

**Manifest trust / path set — дефектов нет.**
`akuz_publication.py:62-70` — `wanted = set(sizes)-{'provenance.json'}`, строгая проверка `set(output_hashes)==wanted`, формат `re.fullmatch(r'[0-9a-f]{64}')` и `type(x) is int and x==sizes[name]`. `provenance.json` всегда перечитывается `sha256(provenance)` (`akuz_publication.py:88`), custom-генератор -> `output_hashes=None` -> полный перехеш (`akuz_publication.py:78-85`). Подмена пути/размера/дайджеста невозможна.

**Wrappers — дефектов нет.**
`akuz_app.py:182-184, 443, 491` — доверие только `gen_fn is generate` или `getattr(..., '_akuz_builtin_generator', False)`. Флаг ставится только на внутренние обертки `single_gen`/`stream_gen`, внешний injected-генератор без флага уходит в fallback полного чтения. `HYPOTHETICAL` риск установки флага внешним кодом вне модели угроз — не доказан, гейты `141/141` и `byte-equivalence PASS` его не подтверждают.

Доказанных (`PROVEN`) дефектов в bounded-коде в указанных областях не обнаружено.

APPROVE
