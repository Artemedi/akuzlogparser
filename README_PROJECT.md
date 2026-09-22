# akuzlogparser — AKUZ Log Explorer v4.2

Это исходный проект ветки **v4.2**: v4.1 + отдельная аналитика ошибок, размещённый на Bazzite в `/home/Trintos/projects/akuzlogparser`.
Исходные имена скриптов сохранены для совместимости.

## Запуск на Windows

1. Заполнить `ConnectConf.cfg` (или создать его из `ConnectConf.example.cfg`).
2. Запустить `START_EXPLORER.bat` (Python 3.9+; Paramiko устанавливается скриптом при необходимости).
3. Открыть локальный Explorer и выбрать журнал. Сервис должен быть запущен, чтобы работали кнопки загрузки.

## Состав

- `akuz_app.py` — локальный HTTP API и точка входа.
- `akuz_fetch.py` — SSH/SFTP и активные снимки Linux.
- `akuz_windows.py` — read-only доступ к файловым логам Windows через SMB/UNC.
- `akuz_log_parser.py` — парсер AKUZ.
- `akuz_html_explorer.py` — генерация HTML-отчёта.
- `akuz_store.py` — кэш и история.
- `index.html`, `event.html`, `*.js`, `style.css` — интерфейс.
- `README_START_HERE.md`, `README_EXPLORER.md`, `CHANGELOG_v4_1.md` — документация версии.

## Важное

В пакет исходников **не включены** демо-журнал и ранее созданные отчёты с медицинскими данными.
Демонстрационные журналы и готовые отчёты хранятся отдельно от исходников.
Не добавляйте заполненный конфиг и папки `data/`, `downloads/`, `reports/`, `cache/` в Git.

Файловые журналы Windows через UNC реализованы для запуска Explorer на Windows:
в [windows] укажите `enabled=true` и `log_dir=\\SERVER\AKUZLogs`,
затем выберите `Windows · SMB / UNC` в интерфейсе. Используются разрешения текущего
пользователя; для проверки работы на реальной SMB-шаре потребуется Windows-машина.
Windows Event Log, Source-Aware Diagnostics и standalone EXE остаются отдельными задачами.
VCLib в этот самостоятельный проект не включается.


## Чистый первый запуск и форматы

Локальный сервис отдаёт пустой обзор до первого отчёта, если data/catalog.js отсутствует.
Ранее сформированные HTML-отчёты читаются офлайн через file:// даже без сервиса.

Заголовки: Linux (HH:mm:ss.fff,component,session,user: message) и Windows
TraceLogger (user 'name' HH:mm:ss.fff: message), опционально с категорией
перед user. Для иных форматов разбор на отдельные события не гарантируется.


## Error Analytics v4.2

Страница errors.html — только распознанные ошибки приложения AKUZ из уже
созданных и зарегистрированных HTML-отчётов. Даты/часы, семейства, fingerprint,
переход к событию. Индекс cache/error_analytics.sqlite перестраивается,
data/analytics.js и data/error_*.js позволяют открыть аналитику по file://.
Точная методика и ограничения — README_ANALYTICS.md. Нет Windows Event Log,
журналов PostgreSQL/Linux или анализа исходников VCLib.

Для сбора новых файлов нужен локальный Python-сервис, для чтения готовых
HTML-отчётов и сформированной аналитики — нет. Windows EXE пока не собран.
