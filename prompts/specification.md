# Техническое задание: Веб-сервер для хранения обновлений tinstaller

## 1. Общее описание

Простой веб-сервер без авторизации с поддержкой HTTPS для хранения и распространения APK-файлов приложений для tinstaller. Сервер должен отдавать список приложений в формате JSON и предоставлять возможность скачивания APK-файлов.

### Домен
- Домен: `vugluskr.xyz`
- Порт: 443 (HTTPS)
- SSL-сертификат: Let's Encrypt с автообновлением

---

## 2. Архитектура и технологический стек

### Веб-сервер
- **Язык:** Python 3.9+
- **Фреймворк:** Flask (минималистичный, легкий)
- **WSGI сервер:** Gunicorn (для production)
- **Процесс менеджер:** systemd

### Telegram бот
- **Библиотека:** python-telegram-bot (v22+)
- **Назначение:** Загрузка APK-файлов через Telegram
- **Процесс менеджер:** systemd (отдельный сервис)

### Структура проекта
```
/opt/web-serv/
├── app.py                    # Основное приложение Flask
├── telegram_bot.py           # Telegram бот для загрузки APK
├── config/
│   └── apps.json            # Файл с описанием приложений
├── apks/                    # Символическая ссылка на /var/www/apks/
│   └── -> /var/www/apks/    # APK-файлы хранятся здесь
├── logs/
│   ├── gunicorn_access.log  # Логи доступа Gunicorn
│   ├── gunicorn_error.log   # Логи ошибок Gunicorn
│   ├── bot.log              # Логи Telegram бота
│   └── update.log           # Логи скрипта обновлений
├── scripts/
│   └── update_apps.sh       # Скрипт обновления APK
├── .env                     # Переменные окружения (TELEGRAM_*)
├── service/
│   ├── tinstaller.service   # systemd service для веб-сервера
│   └── tinstaller-bot.service  # systemd service для бота
└── venv/                    # Виртуальное окружение Python
```

**Примечание:** APK-файлы физически хранятся в `/var/www/apks/`, nginx раздаёт их напрямую через `location /apks/ { alias /var/www/apks/; }`.

---

## 3. Формат данных (apps.json)

### Структура JSON
```json
{
  "apps": [
    {
      "title": "Название приложения",
      "description": "Описание приложения",
      "url": "https://vugluskr.xyz/apks/Aerial Dream.apk",
      "sourceUpdate": "https://внешний-источник.com/app.apk",
      "sourceMethod": "direct",
      "sourceFilter": "",
      "category": "Категория",
      "ver": "1.2.3",
      "lastUpdated": "2026-02-26T10:30:00Z",
      "app_review": "https://youtube.com/watch?v=..."
    }
  ]
}
```

### Поля
- `title` (string, обязательное) - название приложения
- `description` (string, обязательное) - описание
- `url` (string, обязательное) - прямая ссылка для скачивания APK с ЭТОГО сервера (для android-клиента)
- `sourceUpdate` (string, обязательное) - ссылка/идентификатор для получения APK из внешнего источника
- `sourceMethod` (string, опциональное) - метод получения ссылки на APK:
  - `direct` (по умолчанию) - `sourceUpdate` содержит прямую ссылку на APK
  - `github_release` - `sourceUpdate` содержит URL API GitHub releases (например: `https://api.github.com/repos/owner/repo/releases/latest`), `sourceFilter` - паттерн для выбора нужного asset (например: `arm7`, `arm64`, `.*\.apk`)
  - `gitlab_release` - аналогично для GitLab
  - `custom` - кастомная команда curl+jq в `sourceUpdate`
- `sourceFilter` (string, опциональное) - фильтр для выбора нужного файла из списка (используется с `github_release`, `gitlab_release`)
- `category` (string, обязательное) - категория приложения
- `ver` (string, опциональное) - версия приложения (извлекается из APK)
- `lastUpdated` (string, опциональное) - дата последнего обновления в ISO 8601 формате
- `app_review` (string, опциональное) - ссылка на обзор приложения (например, YouTube видео)

### Именование APK-файлов
- Имя файла берется из последней части URL в `sourceUpdate`
- Пример: если `sourceUpdate` = `http://example.com/apps/MyApp_v1.2.3.apk`, то имя файла = `MyApp_v1.2.3.apk`
- Имена статические, не меняются при обновлении версии
- Запрещены символы: `/`, `\`, `..`, `:`, `*`, `?`, `"`, `<`, `>`, `|`

---

## 4. API эндпоинты

### 4.1. Список приложений
- **URL:** `/` (корень домена)
- **Метод:** GET
- **Ответ:** JSON со списком приложений
- **Кэширование:** Cache-Control: max-age=3600 (1 час)
- **Пример ответа:**
```json
{
  "apps": [
    {
      "title": "Aerial Dream",
      "description": "Воздушная Мечта...",
      "category": "Заставка",
      "ver": "1.2.3",
      "lastUpdated": "2026-02-26T10:30:00Z",
      "url": "https://vugluskr.xyz/apks/Aerial%20Dream.apk",
      "app_review": "https://youtube.com/watch?v=..."
    }
  ]
}
```

### 4.2. Скачивание APK
- **URL:** `/apks/<filename>`
- **Метод:** GET
- **Параметры:** filename - имя файла (URL encoded)
- **Ответ:** APK-файл с правильными заголовками
- **Заголовки:**
  - `Content-Type: application/vnd.android.package-archive`
  - `Content-Disposition: attachment; filename="{filename}"`
- **Безопасность:**
  - Валидация имени файла (только разрешенные символы)
  - Проверка существования файла в папке `apks/`
  - Запрет на доступ к родительским директориям (`../`)

### 4.3. Статус сервера
- **URL:** `/health`
- **Метод:** GET
- **Ответ:** `{"status": "ok", "timestamp": "2026-02-26T10:30:00Z"}`

---

## 5. Безопасность

### 5.1. Rate Limiting
- Ограничение: 60 запросов в минуту с одного IP
- Использовать: Flask-Limiter

### 5.2. Защита от сканирования
- Запрет доступа к:
  - `/apks/` - только файлы .apk, запрещены директории
  - `/config/`, `/logs/`, `/scripts/`, `/certbot/` - 403 Forbidden
- Отключение листинга директорий

### 5.3. Валидация входных данных
- Проверка имен файлов на безопасность
- Санитизация путей

### 5.4. HTTPS и Nginx

SSL terminates на Nginx, Gunicorn работает через HTTP:

```
Client (HTTPS:443) → Nginx (SSL) → Gunicorn (HTTP:8000)
```

**Конфигурация Nginx:**
```nginx
server {
    listen 443 ssl http2;
    server_name vugluskr.xyz;
    
    ssl_certificate /etc/letsencrypt/live/vugluskr.xyz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vugluskr.xyz/privkey.pem;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /apks/ {
        alias /var/www/apks/;
    }
}
```

---

## 6. Скрипт обновления (update_apps.sh)

### Назначение
- Периодическая проверка обновлений APK-файлов с внешних источников (`sourceUpdate`)
- Скачивание новых версий при изменении хэша
- Извлечение версии из APK-файла с помощью `aapt`
- Уведомление через Telegram бота

### Требования
- Язык: Bash
- Запуск: по cron (раз в сутки)
- Логирование: в `logs/update.log`

---

## 6.1. Telegram бот для загрузки APK

### Назначение
- Загрузка APK-файлов напрямую через Telegram
- Автоматическое обновление приложений в `apps.json`
- Семантическое сравнение версий

### Требования
- **Библиотека:** `python-telegram-bot` (v22+)
- **Запуск:** systemd сервис (`tinstaller-bot.service`)
- **Логирование:** в `logs/bot.log`

### Логика работы
1. Пользователь отправляет APK-файл боту
2. Бот извлекает `versionName` из APK через `aapt`
3. Поиск соответствия в `apps.json` по названию в имени файла:
   - **Одно совпадение** → обновление сразу
   - **Несколько совпадений** → inline-кнопки для выбора
   - **Нет совпадений** → показать все приложения списком
4. Сравнение версий (семантическое):
   - Если новая версия ≤ старой → запросить подтверждение
   - Если новая версия > старой → обновить сразу
5. После подтверждения:
   - Заменить файл в `/var/www/apks/` (по шаблону из `url`)
   - Обновить `apps.json` (`ver`, `lastUpdated`)
   - Отправить уведомление

### Права доступа
- Только администратор (`TELEGRAM_CHAT_ID` из `.env`) может загружать файлы

### Обработка ошибок
- Неверный формат файла → уведомление
- Ошибка извлечения версии → использовать "неизвестно"
- Ошибка записи файла → уведомление + отмена

### Запуск
```bash
# Установка зависимости
pip install python-telegram-bot

# Запуск через systemd
sudo cp service/tinstaller-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tinstaller-bot.service
sudo systemctl start tinstaller-bot.service
```

---

## 6. Скрипт обновления (update_apps.sh)

### Назначение
- Периодическая проверка обновлений APK-файлов с внешних источников (`sourceUpdate`)
- Скачивание новых версий при изменении хэша
- Извлечение версии из APK-файла с помощью `aapt`
- Уведомление через Telegram бота

### Требования
- Язык: Bash
- Запуск: по cron (раз в сутки)
- Логирование: в `logs/update.log`

### Логика работы
1. Прочитать `config/apps.json`
2. Для каждого приложения:
   - Извлечь имя файла из `sourceUpdate` (последняя часть URL)
   - Проверить наличие APK в папке `apks/`
   - Скачать APK по `sourceUpdate`
   - Вычислить SHA256 хэш скачанного файла
   - Если файл существует:
     - Вычислить хэш существующего файла
     - Если хэши совпадают → пропустить (версия не обновилась)
     - Если хэши разные → заменить файл, извлечь версию через `aapt`, обновить `ver` и `lastUpdated`
   - Если файла нет → скачать, извлечь версию, сохранить
3. После успешного обновления любого файла:
   - Отправить уведомление в Telegram
   - Включить в сообщение: название приложения, старая версия, новая версия, дата
4. Обновить `config/apps.json` с новыми значениями `ver` и `lastUpdated`
5. Записать в лог: время, результат для каждого приложения

### Извлечение версии из APK
- Использовать команду: `aapt dump badging <file.apk>`
- Парсить строку: `package: versionName='1.2.3'`
- Извлекать значение `versionName`

### Telegram уведомления
- **Токен бота:** хранится в переменной окружения `TELEGRAM_BOT_TOKEN`
- **Chat ID:** хранится в переменной окружения `TELEGRAM_CHAT_ID`
- **Сообщение:**
```
🔄 Обновление приложения: {title}
Версия: {old_ver} → {new_ver}
Дата: {timestamp}
```

### Обработка ошибок
- При ошибке скачивания → запись в лог + уведомление об ошибке в Telegram
- При ошибке извлечения версии → использовать "неизвестно"
- При ошибке хэша → пропустить приложение, продолжить остальные
- При ошибке записи файла → уведомление + остановка

### Пример crontab
```
0 2 * * * /opt/web-serv/scripts/update_apps.sh >> /opt/web-serv/logs/update.log 2>&1
```
(Запуск каждый день в 2:00)

---

## 7. Логирование

### Логи Gunicorn
- Файл доступа: `logs/gunicorn_access.log`
- Файл ошибок: `logs/gunicorn_error.log`
- Формат: Стандартный лог Gunicorn
- Уровни:
  - INFO: успешные запросы
  - WARNING: подозрительные запросы (rate limit)
  - ERROR: ошибки сервера, недоступность файлов
- Ротация: через logrotate

### Логи скрипта обновления
- Файл: `logs/update.log`
- Формат: `[YYYY-MM-DD HH:MM:SS] Message`
- Содержимое:
  - Начало/окончание обновления
  - Результат для каждого приложения
  - Ошибки

---

## 8. Развертывание

### 8.1. Установка зависимостей
```bash
pip install flask gunicorn flask-limiter
```

### 8.2. Установка aapt (для извлечения версии)
```bash
apt-get install aapt
```

### 8.3. Настройка Nginx и Let's Encrypt

```bash
# Установка Nginx и Certbot
apt-get install -y nginx certbot python3-certbot-nginx

# Создание конфигурации сайта
cat > /etc/nginx/sites-available/vugluskr.xyz << 'EOF'
server {
    listen 80;
    server_name vugluskr.xyz www.vugluskr.xyz;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name vugluskr.xyz www.vugluskr.xyz;
    
    ssl_certificate /etc/letsencrypt/live/vugluskr.xyz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vugluskr.xyz/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    
    location /apks/ {
        alias /var/www/apks/;
        autoindex on;
    }
}
EOF

# Включение сайта
ln -s /etc/nginx/sites-available/vugluskr.xyz /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

# Получение сертификата
certbot --nginx -d vugluskr.xyz -d www.vugluskr.xyz
```

### 8.4. Конфигурация Gunicorn

Создать файл `gunicorn.conf.py`:

```python
bind = "127.0.0.1:8000"
workers = 3
accesslog = "logs/gunicorn_access.log"
errorlog = "logs/gunicorn_error.log"
loglevel = "info"
```

**Важно:** SSL настраивается в Nginx, не в Gunicorn.

### 8.5. systemd service
Файл: `/etc/systemd/system/tinstaller.service`

```ini
[Unit]
Description=Tinstaller Update Server
After=network.target

[Service]
Type=simple
User=m0nty81
WorkingDirectory=/opt/web-serv
Environment="PATH=/opt/web-serv/venv/bin"
ExecStart=/opt/web-serv/venv/bin/gunicorn -c gunicorn.conf.py app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 8.6. Запуск
```bash
# Включение сервиса
systemctl enable tinstaller.service
systemctl start tinstaller.service

# Проверка статуса
systemctl status tinstaller.service

# Просмотр логов
journalctl -u tinstaller.service -f
```

---

## 9. Требования к коду

### 9.1. app.py (Flask приложение)
```python
from flask import Flask, jsonify, send_file, abort, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import json
from datetime import datetime

app = Flask(__name__)

# Rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["60 per minute"]
)

CONFIG_PATH = 'config/apps.json'
APKS_DIR = 'apks'

def load_apps():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # Добавляем URL для скачивания (если нужно, но в JSON уже есть url)
    return data

@app.route('/')
@limiter.limit("60 per minute")
def get_apps():
    try:
        data = load_apps()
        return jsonify(data)
    except Exception as e:
        app.logger.error(f"Error loading apps: {e}")
        abort(500)

@app.route('/apks/<filename>')
@limiter.limit("30 per minute")
def download_apk(filename):
    # Валидация имени файла
    if '..' in filename or '/' in filename or '\\' in filename:
        abort(403)
    
    # Проверяем, что файл имеет расширение .apk
    if not filename.lower().endswith('.apk'):
        abort(403)
    
    filepath = os.path.join(APKS_DIR, filename)
    if not os.path.exists(filepath):
        abort(404)
    
    return send_file(
        filepath,
        mimetype='application/vnd.android.package-archive',
        as_attachment=True,
        download_name=filename
    )

@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=False)
```

### 9.2. update_apps.sh (Bash скрипт)
```bash
#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$WEB_DIR/config/apps.json"
APKS_DIR="$WEB_DIR/apks"
LOG_FILE="$WEB_DIR/logs/update.log"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID}"

# Функция для логирования
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Функция для отправки в Telegram
send_telegram() {
    local message="$1"
    if [[ -n "$TELEGRAM_BOT_TOKEN" && -n "$TELEGRAM_CHAT_ID" ]]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="$TELEGRAM_CHAT_ID" \
            -d text="$message" \
            -d parse_mode="HTML" > /dev/null 2>&1 || true
    fi
}

log "=== Начало обновления ==="

# Создаем временную директорию
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Загружаем JSON
if [[ ! -f "$CONFIG_FILE" ]]; then
    log "ERROR: Конфиг не найден: $CONFIG_FILE"
    send_telegram "❌ Ошибка обновления: конфиг не найден"
    exit 1
fi

# Парсим JSON (требуется jq)
if ! command -v jq &> /dev/null; then
    log "ERROR: jq не установлен"
    send_telegram "❌ Ошибка обновления: jq не установлен"
    exit 1
fi

# Проверяем наличие aapt
if ! command -v aapt &> /dev/null; then
    log "ERROR: aapt не установлен (требуется для извлечения версии)"
    send_telegram "❌ Ошибка обновления: aapt не установлен"
    exit 1
fi

UPDATED_APPS=0

# Получаем количество приложений
APP_COUNT=$(jq '.apps | length' "$CONFIG_FILE")

for i in $(seq 0 $((APP_COUNT-1))); do
    TITLE=$(jq -r ".apps[$i].title" "$CONFIG_FILE")
    SOURCE_URL=$(jq -r ".apps[$i].sourceUpdate" "$CONFIG_FILE")
    OLD_VER=$(jq -r ".apps[$i].ver // \"\"" "$CONFIG_FILE")
    OLD_UPDATED=$(jq -r ".apps[$i].lastUpdated // \"\"" "$CONFIG_FILE")
    
    # Извлекаем имя файла из sourceUpdate URL
    FILENAME=$(basename "$SOURCE_URL")
    APK_PATH="$APKS_DIR/$FILENAME"
    
    log "Обработка: $TITLE"
    log "  Источник: $SOURCE_URL"
    log "  Файл: $FILENAME"
    
    # Создаем временный файл
    TEMP_APK="$TEMP_DIR/$FILENAME"
    
    # Скачиваем APK
    if ! curl -s -L -o "$TEMP_APK" "$SOURCE_URL" 2>/dev/null; then
        log "  ERROR: Не удалось скачать $TITLE"
        send_telegram "❌ Ошибка скачивания: $TITLE"
        continue
    fi
    
    # Проверяем, что файл скачался (размер > 0)
    if [[ ! -s "$TEMP_APK" ]]; then
        log "  ERROR: Скачанный файл пустой: $TITLE"
        send_telegram "❌ Пустой файл: $TITLE"
        continue
    fi
    
    # Вычисляем хэш
    NEW_HASH=$(sha256sum "$TEMP_APK" | awk '{print $1}')
    
    if [[ -f "$APK_PATH" ]]; then
        OLD_HASH=$(sha256sum "$APK_PATH" | awk '{print $1}')
        
        if [[ "$NEW_HASH" == "$OLD_HASH" ]]; then
            log "  Пропущено (хэш совпадает)"
            continue
        fi
        
        # Файл изменился
        OLD_VER_DISPLAY=${OLD_VER:-"неизвестно"}
        log "  Обновление: $OLD_VER_DISPLAY → новая версия"
        
        # Извлекаем версию из APK
        NEW_VER=$(aapt dump badging "$TEMP_APK" 2>/dev/null | grep "versionName" | head -1 | sed "s/.*versionName='\([^']*\)'.*/\1/" || echo "неизвестно")
        log "  Версия из APK: $NEW_VER"
        
        mv "$TEMP_APK" "$APK_PATH"
        chmod 644 "$APK_PATH"
        
        # Обновляем JSON
        TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        jq --argjson idx "$i" \
           --arg ver "$NEW_VER" \
           --arg ts "$TIMESTAMP" \
           '.apps[$idx].ver = $ver | .apps[$idx].lastUpdated = $ts' \
           "$CONFIG_FILE" > "$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
        
        UPDATED_APPS=$((UPDATED_APPS+1))
        
        # Отправляем уведомление
        send_telegram "🔄 Обновлено: <b>$TITLE</b>\nВерсия: $OLD_VER_DISPLAY → $NEW_VER\nДата: $TIMESTAMP"
        
    else
        # Файл не существует, просто копируем
        log "  Новый файл (первая загрузка)"
        
        # Извлекаем версию из APK
        NEW_VER=$(aapt dump badging "$TEMP_APK" 2>/dev/null | grep "versionName" | head -1 | sed "s/.*versionName='\([^']*\)'.*/\1/" || echo "неизвестно")
        log "  Версия из APK: $NEW_VER"
        
        mv "$TEMP_APK" "$APK_PATH"
        chmod 644 "$APK_PATH"
        
        TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        jq --argjson idx "$i" \
           --arg ver "$NEW_VER" \
           --arg ts "$TIMESTAMP" \
           '.apps[$idx].ver = $ver | .apps[$idx].lastUpdated = $ts' \
           "$CONFIG_FILE" > "$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
        
        UPDATED_APPS=$((UPDATED_APPS+1))
        send_telegram "🆕 Добавлено: <b>$TITLE</b>\nВерсия: $NEW_VER\nДата: $TIMESTAMP"
    fi
done

log "Завершено. Обновлено приложений: $UPDATED_APPS"
log "=== Конец обновления ==="

if [[ $UPDATED_APPS -gt 0 ]]; then
    send_telegram "✅ Обновление завершено. Всего обновлено: $UPDATED_APPS"
fi

exit 0
```

---

## 10. Переменные окружения

### Для скрипта обновления
- `TELEGRAM_BOT_TOKEN` - токен бота Telegram
- `TELEGRAM_CHAT_ID` - ID чата для уведомлений

---

## 11. Требования к системе

### Ubuntu/Debian
```bash
apt-get update
apt-get install -y python3 python3-pip python3-venv curl jq aapt
```

### Python зависимости
```bash
python3 -m venv venv
source venv/bin/activate
pip install flask gunicorn flask-limiter
```

---

## 12. Проверка работоспособности

### После развертывания:
1. Проверить доступность: `curl https://vugluskr.xyz/`
2. Проверить скачивание: `curl -I https://vugluskr.xyz/apks/Aerial%20Dream.apk`
3. Проверить health endpoint: `curl https://vugluskr.xyz/health`
4. Проверить логи: `tail -f logs/app.log`
5. Проверить rate limiting: отправить много запросов с одного IP
6. Проверить скрипт обновления вручную: `bash scripts/update_apps.sh`
7. Проверить systemd: `systemctl status tinstaller`

---

## 13. Дополнительные улучшения (опционально)

- [ ] Web UI для управления приложениями
- [ ] Статистика скачиваний
- [ ] Поддержка I18n
- [ ] RSS-лента обновлений
- [ ] Подписка на уведомления по email
- [ ] API ключ для доверенных источников
- [ ] Проверка подписи APK-файлов
- [ ] Кэширование CDN

---

## 14. Примечания

- Все пути должны быть абсолютными в production конфигурации
- Рекомендуется использовать отдельного пользователя для запуска сервиса
- Сертификаты Let's Encrypt автоматически обновляются через certbot
- Рекомендуется настроить мониторинг дискового пространства (APK могут быть большими)
- Рекомендуется регулярный backup `config/apps.json` и папки `apks/`
- Gunicorn работает с SSL напрямую, Nginx не требуется
- Имя APK-файла берется из URL источника (последняя часть пути)
- Версия извлекается из APK с помощью `aapt dump badging`