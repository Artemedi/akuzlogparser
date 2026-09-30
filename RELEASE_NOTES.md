# AKUZ Log Explorer 4.9.0 — Windows 10/11 x64 portable

## Главное в v4.9.0

### Быстрее аналитика ошибок — Phase 13

Индекс аналитики теперь использует уже опубликованные и проверенные
`errorFingerprints` из каталога отчёта как отрицательный фильтр. Положительные
события по-прежнему перечитываются из raw-shard и повторно проходят
`recognize_error`, поэтому семантика распознавания не заменена доверием к
кэшу.

На default-path real A/B для 23/24/25 сентября:

- wall: **39.308 → 25.178 с** (**-35.95%**);
- SQL/export и semantic parity — PASS;
- inventory — PASS.

Rollback:
```powershell
$env:AKUZ_PHASE13_CATALOG_ERROR_INDEX = '0'
.\AKUZLogExplorer.exe
```

### Быстрее сериализация raw-shards — Phase 14

Portable включает `orjson 3.12.0` и использует его **только** для массивов
строк `raw_*.js`. Каталог `data/catalog.js` остаётся на штатном
`json.dumps`.

Replicated B/C/C/B/B/C benchmark на 956,307,242 байт / 4 отчётах /
1,315,476 событиях:

- wall: **195.911 → 185.269 с** (**-5.43%**);
- CPU: **187.281 → 177.344 с** (**-5.31%**);
- generation: **161.769 → 151.682 с** (**-6.24%**);
- измеренное JSON-время: **16.852 → 8.409 с** (**-50.10%**);
- полный producer manifest — byte-identical;
- analytics exact/semantic parity — PASS;
- cached downloads — unchanged.

Для немедленного возврата к stdlib:

```powershell
$env:AKUZ_PHASE14_RAW_ORJSON = '0'
.\AKUZLogExplorer.exe
```

При запуске из исходников Python 3.9 остаётся поддержан: `orjson` требует
Python >=3.10, поэтому на Python 3.9 Explorer автоматически использует прежний
stdlib encoder. Portable собирается на Python 3.12 и содержит ускоритель.

### Phase 12 delta/resume остаётся opt-in

Режим докачивания выросшего Linux/SSH журнала включается только явно:

```powershell
$env:AKUZ_PHASE12_DELTA_RESUME = '1'
.\AKUZLogExplorer.exe
```

Принятый snapshot требует доказанный предыдущий SHA/identity и полный SHA-256
удалённого принятого префикса. При обычной ошибке доказательства Explorer
возвращается к полной bounded-загрузке; небезопасные коллизии остаются
fail-closed. Пока Phase 12 включён, Phase 11 process-prefetch намеренно
отключается.

Измерения принятой Phase 12 матрицы:

- transport A/B: **120.067 → 7.215 с** median (**-93.99% wall**);
- normal-app integration: **265.223 → 92.686 с** (**-65.05% wall**);
- snapshot/report/semantic SQL/export parity — PASS.

Default остаётся OFF.

## Сохранено из v4.8.0

- Windows SSH process-prefetch Phase 11 остаётся default-on для двух и более
  SSH-файлов; rollback: `AKUZ_PHASE11_PROCESS_PREFETCH=0`.
- Phase 9 ephemeral derived spool остаётся production-архитектурой combined.
- SSH compression остаётся `compression=false` по умолчанию.
- Transient `Failed to fetch` в status polling не оставляет UI навсегда
  отключённым.
- Старые reports, inventory, downloads, analytics и `ConnectConf.cfg`
  остаются совместимыми.

## Release-candidate gates

Перед публикацией v4.9.0 выполняются на exact versioned SHA:

- полный Windows Python + browser regression;
- production stdlib/orjson real B/C/C/B/B/C на фиксированных snapshot;
- PyInstaller Windows x64 build;
- frozen `--self-test` с фактическим импортом/исполнением orjson;
- packaged offline smoke без Python/pip в child PATH;
- проверка `BUILD_INFO.json` и `SHA256SUMS.txt`.

Публичный GitHub Release не заменяется автоматически: публикация выполняется
отдельно после финального gate.

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
