# AKUZ Log Explorer 4.7.0 — Windows 10/11 x64 portable

Релиз анализирует **только файловые журналы AKUZ `.log`**. Системные
журналы Windows/Linux и VCLib не собираются. Portable EXE включает Python,
Paramiko и необходимые зависимости.

## Главное в v4.7.0

- **Быстрее multi-source fresh build:** normal-app использует проверенный
  временный derived spool для свежих single-отчётов и повторно применяет
  уже вычисленные производные поля при построении combined. Spool существует
  только внутри текущей операции и удаляется после завершения/ошибки.
  Постоянный B-lite sidecar и persistent derived clinical metadata не включены.
- **Надёжнее публикация и кэш:** inventory сериализован OS-backed lock,
  один app-root обслуживается одним экземпляром Explorer, отчёты публикуются
  через `.building` + recovery intent. Ошибка combined не должна удалять
  уже успешно готовые single-отчёты.
- **Идентичность источников:** перед reuse SSH-источника stale browser
  selection сверяется с новым server inventory; рост получает новую identity,
  device/inode rotation отклоняется. Для локального файла можно явно включить
  `AKUZ_VERIFY_LOCAL_SOURCE_SHA=1` — полный SHA исходника перед warm reuse.
  По умолчанию дополнительного полного чтения нет.
- **Парсинг:** один `casefold(message)` переиспользуется классификацией и
  извлечением длительности; сохранены прежние Unicode fallback и приоритеты.
  Добавлена numeric-only диагностика распознавания ошибок без текста событий.
- **SSH compression остаётся opt-in.** На replicated fixed-prefix A/B реальных
  журналов 23/24/25 Sep compression уменьшила медианное время передачи примерно
  на 67,6–71,0% и socket RX на 72,1–76,9%, но measured parent `sshd` CPU вырос
  приблизительно в 5,2–6,1 раза. Поэтому `compression=false` остаётся default.
- Экспериментальные Phase 11 thread/process overlap harness **не подключены к
  обычному приложению v4.7.0**. Thread-вариант отклонён; process-вариант
  исследуется отдельно и не меняет runtime scheduler этого релиза.

## Совместимость и обновление

Закройте работающий Explorer. Для обновления существующей установки достаточно
заменить `AKUZLogExplorer.exe`; сохраняйте ваш `ConnectConf.cfg`,
`downloads/`, `reports/`, `cache/` и `data/`. Не распаковывайте
конфиг-пример поверх рабочего конфига.

Старые готовые отчёты и inventory поддерживаются. Для обновления только UI уже
созданных v4-отчётов из исходников существует
`scripts/refresh_report_ui.py`; данные отчётов он не переписывает.

## Проверки

Перед публикацией релизный commit должен пройти полный Python regression,
Node browser controls, `git diff --check`, сборку Windows portable, self-test
EXE и packaged smoke: запуск без Python в child PATH, localhost UI, локальный
`.log` через API, сохранение исходника/конфига и создание inventory/SQLite/
analytics рядом с EXE. `BUILD_INFO.json` содержит точный Git SHA сборки,
а `SHA256SUMS.txt` — SHA-256 EXE и ZIP.

Подробная методика real-source и performance gate:
`PERFORMANCE_NOTES.md`, `ROADMAP_PERFORMANCE.md`,
`PHASE94_OWNER_ARCHITECTURE_GATE.md`, `PHASE10_SSH_COMPRESSION.md` и
`PHASE11_SSH_OVERLAP.md`.

## Файлы выпуска

- `AKUZLogExplorer-windows-x64.zip` — EXE, конфиг-пример,
  `README_PORTABLE.md` и `BUILD_INFO.json`.
- `AKUZLogExplorer.exe` — отдельно для обновления существующей установки.
- `SHA256SUMS.txt` — контрольные суммы EXE и ZIP.

В поставку не входят заполненный `ConnectConf.cfg`, реальные журналы,
медицинские данные, рабочие отчёты, приватные diagnostics или benchmark JSON.
EXE не подписан Authenticode.
