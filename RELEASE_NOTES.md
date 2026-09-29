# AKUZ Log Explorer 4.8.0 — Windows 10/11 x64 portable

## Главное в v4.8.0

### Быстрее обработка нескольких SSH-журналов

На Windows при выборе двух и более файлов источника Linux/SSH Explorer теперь
по умолчанию получает следующий snapshot отдельным process одновременно с
разбором уже скачанного файла. Одновременно работает не более одного
fetch-child. Local и SMB/UNC источники не менялись.

На финальном hardened A/B после version bump, Fable ACCEPT и всех lifecycle
исправлений, на наборе из 857,563,363 байт:

- последовательный normal-app: **356.468 с**;
- process-prefetch: **325.070 с**;
- wall уменьшился примерно на **8.8%**;
- total CPU: **256.734 → 251.703 с** (~2.0% ниже);
- peak private memory: **988,049,408 → 1,077,604,352 B** (~9.1% выше);
- inventory parity — PASS;
- analytics SQL parity — PASS;
- analytics exports parity — PASS;
- workspace cleanup / privacy gate — PASS.

Actions: #36540421782; numeric audit: #36540580395.

Это измерение конкретной рабочей нагрузки DBA-008D, а не гарантированный
процент ускорения на любом сервере.

### Fail-closed Windows process lifecycle

Дочерний процесс создаётся приостановленным, помещается в Windows
`KILL_ON_JOB_CLOSE` Job Object **до начала исполнения**, затем запускается.
Это закрывает окно, в котором child мог пережить аварийное завершение parent.

IPC — ограниченный JSON. Parent заново проверяет скачанный snapshot полным
SHA-256, размером и exact Windows file/parent identity. Hashing, promotion и
удаление owned temp используют reparse-safe HANDLE semantics.

Обычная ошибка предзагрузки возвращает приложение к прежней
последовательной загрузке. Если невозможно доказать безопасное завершение
child/Job Object или корректную очистку, Explorer завершает операцию с
ошибкой вместо запуска второго writer.

Для немедленного отключения оптимизации:

```powershell
$env:AKUZ_PHASE11_PROCESS_PREFETCH = '0'
.\AKUZLogExplorer.exe
```

Удалите переменную, чтобы вернуть default-on режим.

### Cache / crash / promotion

Prefetch child не пишет inventory. Parent принимает только полностью
проверенный snapshot. Promotion — atomic no-overwrite hard-link после
durable inventory intent. Внешний файл назначения не перезаписывается.

Restart после crash проверяет warm download по существованию файла, точному
размеру и полному SHA-256. Отсутствующий final или same-size внешний файл с
неверным SHA не считается валидным кэшем.

Unsupported/cross-volume hard-link может откатить только оптимизацию и
продолжить serial path. Permission, disk-full и другие реальные I/O ошибки
не маскируются повторной загрузкой.

### Независимое ревью и проверки

Финальное независимое Claude Fable review:
Actions #36538397765 — **LIFECYCLE=ACCEPT,
TRANSACTION=ACCEPT, OVERALL=ACCEPT**.

До version bump полный Windows suite:
Actions #36537874019 — **318 Python tests PASS, 3 skips**,
browser controls PASS, `git diff --check` PASS.

Финальные pre-release gates уже пройдены:
- exact versioned SHA regression #36539318506 — 318 Python tests PASS,
  3 skips, browser controls и diff-check PASS;
- real normal-app A/B #36540421782 — PASS;
- numeric evidence audit #36540580395 — PASS;
- frozen portable atomic Job-bound spawn/self-test + packaged smoke
  #36540426274 — PASS.
BUILD_INFO и SHA256SUMS повторно проверяются самим release workflow
непосредственно перед публикацией.

## Остальное поведение

- Phase 9 ephemeral derived spool остаётся production-архитектурой combined.
  Persistent B-lite metadata не создаётся.
- SSH compression остаётся `compression=false` по умолчанию и включается
  только явно.
- Hotfix v4.7.1 status-polling сохранён: transient `Failed to fetch` не
  останавливает UI polling, а POST-команда build автоматически не повторяется.
- Старые reports, inventory, downloads, analytics и ConnectConf.cfg
  совместимы.

## Обновление

Закройте работающий Explorer. Для существующей portable-папки достаточно
заменить `AKUZLogExplorer.exe`. Сохраните свой `ConnectConf.cfg`,
`downloads/`, `reports/`, `cache/` и `data/`.

В поставку не входят заполненный конфиг, реальные журналы, медицинские
данные, рабочие отчёты или private diagnostics.

## Файлы выпуска

- `AKUZLogExplorer.exe`
- `AKUZLogExplorer-windows-x64.zip`
- `SHA256SUMS.txt`

Архив содержит `BUILD_INFO.json` с точным Git SHA сборки. EXE не подписан
Authenticode.
