# Эксплуатация Job Hunter

## Запуск одной иконкой (FR-010)

В корне репозитория лежит `Job Hunter.command`. Двойной клик в Finder:

1. Проверяет, не работает ли уже сервис на `http://127.0.0.1:8777/`.
2. Если работает — открывает его в браузере и завершается (идемпотентно).
3. Если нет — запускает `job-hunter web`, включая планировщик и catch-up (FR-008).

Требуется `uv` и Python 3.13 (см. `README.md` → `uv sync`).

## Запуск и остановка из терминала

```bash
uv run job-hunter launch              # иконка/лаунчер
uv run job-hunter web                 # только web + планировщик
uv run job-hunter web --no-scheduler  # web без фоновых сборов
uv run job-hunter fetch               # ручной сбор (перед ним отработает catch-up)
```

Остановка: в терминале, где запущен процесс, нажмите **Ctrl+C**. Если нужно убить фоновый процесс:

```bash
pkill -f "job-hunter web"
# или, если запущен через launchd:
launchctl unload ~/Library/LaunchAgents/local.job-hunter.plist
```

## Автостарт при логине (launchd)

Создайте `~/Library/LaunchAgents/local.job-hunter.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>local.job-hunter</string>
    <key>ProgramArguments</key>
    <array>
        <string>uv</string>
        <string>run</string>
        <string>job-hunter</string>
        <string>web</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/ВАШ_ПОЛЬЗОВАТЕЛЬ/Job_hunter</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/Users/ВАШ_ПОЛЬЗОВАТЕЛЬ/Library/Logs/job-hunter.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/ВАШ_ПОЛЬЗОВАТЕЛЬ/Library/Logs/job-hunter.error.log</string>
</dict>
</plist>
```

Загрузка и старт:

```bash
launchctl load ~/Library/LaunchAgents/local.job-hunter.plist
launchctl start local.job-hunter
```

`KeepAlive` по желанию: `true` перезапустит упавший процесс, но вместе с `RunAtLoad=true` это создаст экземпляр при каждом логине. Благодаря команде `web` сервис сам привязывается к порту 8777; повторный `launch` откроет существующий UI в браузере, а не запустит второй экземпляр.

## Catch-up после пропуска (FR-008)

Если ноутбук был выключен и запланированные сборы (09:00/18:00) пропущены, при старте web или `fetch` система смотрит таблицу `fetch_runs` по каждому источнику. Если с последнего успешного сбора прошло больше `scheduler.catch_up_hours` (дефолт 3 ч), для просроченных источников запускается догоняющий сбор по пресетам профиля. Это делает поздний старт безопасным: данные актуализируются фоном, не блокируя UI.

Параметр настраивается в `config/settings.yaml`:

```yaml
scheduler:
  catch_up_hours: 3
```
