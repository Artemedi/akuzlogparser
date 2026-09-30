# AKUZ Log Explorer 4.10.0 — Windows 10/11 x64 portable

## Главное в v4.10.0

### Надёжнее кэш и работа нескольких вкладок — Phase 16

Очистка кэша теперь удаляет и вычёркивает из inventory только те snapshot,
для которых доказано принадлежание текущим разрешённым cache roots.
Исторические/custom-root записи не теряют индекс, а symlink/external/unproven
пути остаются fail-closed.

Сервер публикует монотонный `listing_revision`. Если другая вкладка успела
сменить или очистить текущий список источников, устаревшая вкладка получает
HTTP 409 до запуска build. История одиночных отчётов связывается с
`host + remote_path`, а операторская дата остаётся метаданными.

Принятые проверки:
- mixed/per-folder cache correctness — PASS;
- legacy v4 cache fallback — PASS;
- stale-session 409 — PASS;
- real cache-clear smoke на `20260923_server.log` — PASS;
- preserved report reuse без refetch — PASS.

### Параллельная генерация отчётов — Phase 15, opt-in

На Windows можно явно включить новый ограниченный parallel scheduler:

```powershell
$env:AKUZ_PHASE15_PARALLEL_GENERATION = '1'
.\AKUZLogExplorer.exe
```

Он применяется только при выборе двух и более источников, встроенном
`generate` и выключенном Phase 12 delta. Parent запускает максимум два worker
process, принимает только проверенные staged outputs и публикует их в
детерминированном порядке.

Пока Phase 15 активен, Phase 11 process-prefetch намеренно отключается, чтобы
два независимых process scheduler не конкурировали. Phase 12 delta и Phase 15
одновременно не используются.

Real normal-app B/C/C/B/B/C на 956,307,242 байт / 4 отчётах / 657,738 событиях:
- wall median: **192.514 → 165.655 с** (**-13.95%**);
- CPU median: **194.078 → 197.844 с** (**+1.94%**);
- peak private memory: **+0.051%**;
- exact output parity — PASS;
- analytics parity — PASS;
- downloads/cache invariants — PASS;
- network during trials — NO;
- raw payload retained — NO.

Phase 15 остаётся **default OFF** в v4.10.0.

## Сохранено из v4.9.0

- Phase 13 catalog error-index остаётся default ON; rollback:
  `AKUZ_PHASE13_CATALOG_ERROR_INDEX=0`.
- Phase 14 raw-shard `orjson` остаётся default ON при доступной зависимости;
  rollback: `AKUZ_PHASE14_RAW_ORJSON=0`.
- Phase 12 delta/resume остаётся default-OFF opt-in:
  `AKUZ_PHASE12_DELTA_RESUME=1`.
- Phase 11 SSH process-prefetch остаётся default-on в своём прежнем scope,
  если Phase 12/15 не требуют его отключения.
- Phase 9 ephemeral derived spool и существующие cache/report formats
  сохраняются.

## Release-candidate gates

Перед публикацией v4.10.0 обязательны на одном exact versioned SHA:

- независимое Claude Fable review изменения `v4.9.0..v4.10.0`;
- полный Windows Python + browser regression;
- PyInstaller Windows x64 build;
- frozen `--self-test`;
- packaged offline smoke без Python/pip в child PATH;
- проверка `BUILD_INFO.json` и `SHA256SUMS.txt`.

Публичный GitHub Release создаётся только после успешных gate.

## Обновление

Закройте работающий Explorer. Для существующей portable-папки достаточно
заменить `AKUZLogExplorer.exe`. Сохраните свой `ConnectConf.cfg`,
`downloads/`, `reports/`, `cache/` и `data/`.

В поставку не входят заполненный конфиг, реальные журналы, медицинские данные,
рабочие отчёты или private diagnostics.

## Файлы выпуска

- `AKUZLogExplorer.exe`
- `AKUZLogExplorer-windows-x64.zip`
- `SHA256SUMS.txt`

Архив содержит `BUILD_INFO.json` с точным Git SHA сборки и версиями Python/
пакетов. EXE не подписан Authenticode.
