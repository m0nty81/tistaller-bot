# TInstaller - Unified APK Distribution Server

Единый сервер для управления и распространения APK-файлов для Android TV.

## Возможности

- 🚀 **Flask API** - Раздача APK файлов с rate limiting
- 🤖 **Telegram бот** - Управление приложениями через Telegram
- 📦 **Добавление/Удаление** - Мастер добавления и удаления приложений через бота
- 🌐 **Поддержка ссылок с редиректами** - прямые ссылки на APK теперь корректно скачиваются через бот
- 🔄 **Автообновление** - Планировщик для автоматической проверки внешних источников
- 📊 **Умный поиск** - Автоматическое сопоставление APK файлов с приложениями по имени
- 🔒 **Только для админа** - Все команды доступны только администратору
- ⚙️ **Один сервис** - Все компоненты в одном systemd сервисе
- 📱 **Обновление по ссылке** - Загрузка APK по прямой ссылке через бота

## Быстрый старт

### Установка

```bash
# Перейти в директорию проекта
cd /opt/web-serv

# Создать виртуальное окружение
python3 -m venv venv
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt
```

### Настройка

1. Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
nano .env
```

2. Отредактируйте `config/apps.json`:

```json
{
  "apps": [
    {
      "title": "My App",
      "description": "Описание приложения",
      "url": "https://yourdomain.com/apks/MyApp.apk",
      "sourceUpdate": "https://github.com/user/repo/releases/latest",
      "sourceMethod": "github_release",
      "sourceFilter": "arm64",
      "category": "Utilities"
    }
  ]
}
```

### Запуск

#### Разработка

```bash
python server.py
```

#### Production (systemd)

```bash
# Скопировать service файл
sudo cp service/tinstaller.service /etc/systemd/system/

# Отредактировать пользователя
sudo nano /etc/systemd/system/tinstaller.service

# Перезагрузить systemd и запустить
sudo systemctl daemon-reload
sudo systemctl enable tinstaller
sudo systemctl start tinstaller
```

### Логирование

Сервис пишет основной файл логов в `logs/server.log`.
Если вы запускаете сервер вручную с перенаправлением вывода, то `logs/server.out` также используется.

Для ротации `server.log`, `server.out` и `update.log` скопируйте конфигурацию логрейтера:

```bash
sudo cp service/tinstaller.logrotate.conf /etc/logrotate.d/tinstaller
sudo logrotate -f /etc/logrotate.d/tinstaller
```

## Структура проекта

```
tinstaller/
├── server.py             # Единый сервер: Flask + Telegram бот + планировщик
├── requirements.txt      # Python зависимости
├── .env.example          # Шаблон переменных окружения
├── config/
│   ├── apps.json         # Конфигурация приложений
│   └── files.json        # Реестр загруженных файлов
├── service/
│   └── tinstaller.service.example  # systemd service
├── logs/                 # Логи приложения
├── apks/                 # APK файлы
└── files/                # Произвольные файлы (HTML, документы и т.д.)
```

## API Endpoints

| Endpoint                | Описание                                                     |
| ----------------------- | ------------------------------------------------------------ |
| `GET /`                 | Список всех приложений                                       |
| `GET /apks/<filename>`  | Скачать APK файл                                             |
| `GET /files`            | Список всех загруженных файлов (JSON)                        |
| `GET /files/<filename>` | Скачать произвольный файл                                    |
| `GET /health`           | Проверка работоспособности                                   |
| `POST /update`          | Ручной запуск проверки обновлений (требуется `X-Auth-Token`) |

## Telegram бот команды

| Команда      | Описание                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------ |
| `/start`     | Запуск бота, приветственное сообщение                                                      |
| `/apps`      | Список всех приложений с версиями и ссылками                                               |
| `/status`    | Статус сервера: CPU, RAM, диск, статус systemd, ссылка на apps.json                        |
| `/updateall` | Обновление всех приложений из внешних источников                                           |
| `/addapp`    | Мастер добавления нового приложения (APK/ссылка → название → описание → категория → метод) |
| `/removeapp` | Удаление приложения с подтверждением                                                       |
| `/updateapp` | Обновление конкретного приложения (выбор → APK/ссылка)                                     |
| `/files`     | Список всех загруженных файлов с ссылками                                                  |
| `/upload`    | Загрузка файла (через файл или ссылку + опция переименования)                              |
| `/delfile`   | Удаление файла с подтверждением                                                            |
| `/cancel`    | Отмена текущей операции (мастера добавления/удаления/обновления/загрузки)                  |

### Подробное описание команд

#### `/addapp` — Добавление нового приложения

Пошаговый мастер (6 шагов):

1. **APK или ссылка** — отправьте APK файл или прямую ссылку на него
   - если на шаге 1 указать GitHub-репозиторий, бот автоматически выберет новый GitHub-источник
2. **Название** — введите название латиницей (используется для имени файла)
3. **Описание** — краткое описание приложения
4. **Категория** — выберите из существующих или введите новую
5. **Метод обновлений**:
   - `manual` — нет внешнего источника, обновления только через бота
   - `direct` — прямая ссылка на APK для автообновлений, поддерживаются ссылки с редиректами (например `http://telegram.org/dl/android/apk-public-beta`)
   - `github` — GitHub Releases по `releases/latest` для периодических автообновлений
6. **Ссылка** (для `direct`) — URL для автоматической проверки обновлений

> Если вы указали ссылку на шаге 1 и затем выбрали `direct` на шаге 5, бот автоматически использует ранее введённый URL и не просит ввести его повторно.

Пример добавления с `manual`:

```json
{
  "title": "MyApp",
  "sourceMethod": "manual",
  "sourceUpdate": null
}
```

#### `/updateapp` — Обновление конкретного приложения

1. Выберите приложение из списка или отправьте APK/ссылку сразу
2. Отправьте APK файл или прямую ссылку
3. Если версия новеe — обновление сразу, иначе запрос подтверждения

**Важно:** `sourceMethod` и `sourceUpdate` не изменяются. Автообновления продолжат работать.

#### `/removeapp` — Удаление приложения

1. Выберите приложение из списка
2. Подтвердите удаление
3. APK файл и запись в `apps.json` удаляются

#### `/status` — Статус сервера

Выводит информацию:

- 📊 CPU — загрузка процессора (%)
- 💾 RAM — использовано/всего MB (%)
- 📁 SSD — использовано/всего (%)
- 🔧 systemd — статус сервиса (✅ active / ❌ inactive)
- 📄 apps.json — ссылка на конфигурационный файл

### Управление файлами

#### `/files` — Список загруженных файлов

Выводит список всех загруженных файлов с информацией:

- Исходное имя файла
- Имя файла на сервере
- Размер
- Дата загрузки
- Прямая ссылка для скачивания

#### `/upload` — Загрузка файла

Пошаговый мастер (2 шага):

1. **Файл или ссылка** — отправьте любой файл или прямую ссылку на него
   - Максимальный размер файла: 100MB
   - Поддерживаются любые типы файлов
2. **Переименование** — опционально введите новое имя файла или напишите "нет" чтобы оставить как есть

После успешной загрузки бот отправит прямую ссылку на файл.

**Пример:**

```
/upload → Отправить файл → "нет" → ✅ Файл загружен!
🔗 https://yourdomain.com/files/document.pdf
```

#### `/delfile` — Удаление файла

1. Выберите файл из списка (инлайн-кнопки)
2. Подтвердите удаление
3. Файл удаляется из файловой системы и `files.json`

**Важно:** Файлы доступны публично по HTTPS. Для доступа добавьте в nginx:

```nginx
location /files/ {
    alias /opt/web-serv/files/;
    default_type application/octet-stream;
}
```

## Переменные окружения

| Переменная                    | Описание                                                  |
| ----------------------------- | --------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`          | Токен бота от @BotFather                                  |
| `TELEGRAM_CHAT_ID`            | Chat ID для уведомлений                                   |
| `ADMIN_ID`                    | ID администратора (только для него доступ)                |
| `UPDATE_CHECK_INTERVAL_HOURS` | Интервал проверки обновлений (часы, по умолчанию 6)       |
| `APPS_JSON_URL`               | URL для доступа к apps.json извне (для команды `/status`) |

Пример `.env`:

```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=-1001234567890
ADMIN_ID=123456789
UPDATE_CHECK_INTERVAL_HOURS=6
APPS_JSON_URL=https://yourdomain.com/apps.json
```

## systemd сервис

### Установка

```bash
sudo cp service/tinstaller.service.example /etc/systemd/system/tinstaller.service
sudo nano /etc/systemd/system/tinstaller.service  # Замените YOUR_USER
sudo systemctl daemon-reload
sudo systemctl enable tinstaller
sudo systemctl start tinstaller
```

### Управление

```bash
# Статус
sudo systemctl status tinstaller

# Логи
sudo journalctl -u tinstaller -f

# Перезапуск
sudo systemctl restart tinstaller
```

> После изменений кода, например добавления поддержки ссылок с редиректами, перезапустите сервис командой `sudo systemctl restart tinstaller`.

## Конфигурация приложений

### Формат apps.json

```json
{
  "apps": [
    {
      "title": "Название",
      "description": "Описание",
      "url": "https://YOUR_DOMAIN/apks/File.apk",
      "sourceUpdate": "https://external.com/app.apk или API URL",
      "sourceMethod": "direct|github|github_release|gitlab_release|custom",
      "sourceFilter": "паттерн для фильтрации (опционально)",
      "category": "Категория",
      "ver": "1.2.3",
      "lastUpdated": "2026-02-26T10:30:00Z",
      "app_review": "https://youtube.com/watch?v=..."
    }
  ]
}
```

### Методы обновлений

1. **manual** — нет внешнего источника, обновления только через бота:

```json
{
  "title": "MyApp",
  "sourceUpdate": null,
  "sourceMethod": "manual"
}
```

2. **direct** - Прямая ссылка на APK:

```json
{
  "title": "Aerial Dream",
  "sourceUpdate": "http://dradler.pp.ru/apps/Aerial_Dream.apk",
  "sourceMethod": "direct"
}
```

2. **github** - GitHub Releases repository source:

```json
{
  "title": "TorrServer",
  "sourceUpdate": "https://github.com/YouROK/TorrServe",
  "sourceMethod": "github",
  "sourceFilter": "universal"
}
```

- Автоматически используется `releases/latest`.
- Если `latest` отсутствует, бот выбирает самый свежий доступный релиз по дате публикации.
- Если найден `universal`, он выбирается; иначе используются `v7a` и `v8a`.

3. **github_release** - GitHub Releases API:

```json
{
  "title": "TorrServer",
  "sourceUpdate": "https://api.github.com/repos/YouROK/TorrServe/releases/latest",
  "sourceMethod": "github_release",
  "sourceFilter": "arm7"
}
```

4. **gitlab_release** - GitLab Releases API:

```json
{
  "title": "My App",
  "sourceUpdate": "https://gitlab.com/api/v4/projects/ID/releases",
  "sourceMethod": "gitlab_release",
  "sourceFilter": "arm64"
}
```

5. **custom** - Кастомная команда bash:

```json
{
  "title": "Custom App",
  "sourceUpdate": "curl -s https://api.example.com/releases | jq -r '.download_url'",
  "sourceMethod": "custom"
}
```

## Логика обновлений

### Автоматическое обновление (планировщик)

- Запускается каждые `UPDATE_CHECK_INTERVAL_HOURS` часов
- Проверяет только приложения с `sourceUpdate != null`
- Скачивает APK из внешнего источника
- Сравнивает версии:
  - **Новая < старой** → Пропуск (без уведомления)
  - **Новая = старой** → Пропуск (без уведомления, пересборка)
  - **Новая > старой** → Обновление + уведомление 🔄
  - **Новый файл** → Загрузка + уведомление 🆕

### Обновление через Telegram бота

#### Обновление существующего приложения

- Отправьте APK файл или ссылку боту
- Бот находит приложение по имени файла
- Сравнивает версию из APK с установленной (из файла, а не из `apps.json`)
- Если версия ≤ существующей → запрашивает подтверждение
- Если версия > существующей → обновляет сразу

#### Добавление нового приложения

- Мастер `/addapp` проводит по всем шагам
- APK сохраняется в `apks/{title}.apk`
- Запись добавляется в `apps.json`
- Для `manual` — автообновления не работают, только через бота

## Требования

- Python 3.10+
- Ubuntu 20.04+ / Debian 11+
- `aapt` (для извлечения версии из APK)

### Установка зависимостей

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv aapt
```

## nginx конфигурация

Для доступа к `apps.json` и файлам по HTTPS добавьте в конфиг nginx:

```nginx
location /apps.json {
    alias /opt/web-serv/config/apps.json;
    default_type application/json;
    add_header Access-Control-Allow-Origin *;
}

location /files/ {
    alias /opt/web-serv/files/;
    default_type application/octet-stream;
}
```

Пример полного конфига — в `/etc/nginx/sites-available/[YOUR_DOMAIN].xyz`.

## Лицензия

MIT License - см. файл [LICENSE](LICENSE).

## Примечания

- Проект предназначен для распространения приложений Android TV
- Рекомендуется использовать отдельный VPS/сервер для production
- Регулярно проверяйте свободное место на диске (APK файлы могут быть большими)
- Настройте резервное копирование `apks/` и `config/apps.json`
- Для команды `/status` необходим доступ к `systemctl`, `free`, `df` (полные пути: `/usr/bin/*`)
