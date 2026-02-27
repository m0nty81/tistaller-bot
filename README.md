# tinstaller Update Server - Инструкция по развертыванию и использованию

## Содержание
1. [Предварительные требования](#предварительные-требования)
2. [Установка и настройка](#установка-и-настройка)
3. [Настройка SSL (Let's Encrypt)](#настройка-ssl)
4. [Настройка приложения](#настройка-приложения)
5. [Настройка Nginx](#настройка-nginx)
6. [Запуск сервиса](#запуск-сервиса)
7. [Telegram бот для обновлений](#telegram-бот-для-обновлений)
8. [Настройка автоматических обновлений](#настройка-автоматических-обновлений)
9. [Проверка работоспособности](#проверка-работоспособности)
10. [Управление сервисом](#управление-сервисом)
11. [Устранение неполадок](#устранение-неполадок)

---

## Предварительные требования

### 1.1. Операционная система
- Ubuntu 20.04/22.04 или Debian 11/12
- Доступ к серверу с правами sudo
- Домен `YOUR_DOMAIN` должен указывать на IP-адрес сервера

### 1.2. Установка системных зависимостей
```bash
sudo apt-get update
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    jq \
    aapt \
    certbot \
    python3-certbot-nginx
```

**Пояснение:**
- `python3-venv` - для создания виртуального окружения
- `jq` - для парсинга JSON в bash-скрипте
- `aapt` - для извлечения версии из APK-файлов
- `certbot` - для получения SSL-сертификатов

---

## Установка и настройка

### 2.1. Клонирование/загрузка проекта
```bash
cd /opt/web-serv
# Если проект уже загружен в /opt/web-serv, пропустите этот шаг
```

### 2.2. Создание виртуального окружения
```bash
cd /opt/web-serv
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install flask gunicorn flask-limiter
```

### 2.3. Создание структуры папок (если не создана)
```bash
mkdir -p /opt/web-serv/{config,apks,logs,scripts,service}
```

### 2.4. Настройка директории для APK-файлов

APK-файлы хранятся в `/var/www/apks/` (для доступа nginx), а в проекте создана символическая ссылка:

```bash
# Создание симлинка (если не существует)
ln -s /var/www/apks /opt/web-serv/apks

# Настройка прав доступа
sudo chown -R $USER:$USER /var/www/apks/
chmod 755 /var/www/apks/
```

**Примечание:** Nginx настроен на раздачу файлов из `/var/www/apks/` через `location /apks/ { alias /var/www/apks/; }`.

---

## Настройка SSL

### 3.1. Получение SSL-сертификата Let's Encrypt

**Вариант A: Использование standalone режима (рекомендуется, если Nginx не используется)**

```bash
# Остановите временно все сервисы, слушающие порт 80
sudo systemctl stop tinstaller 2>/dev/null || true

# Получите сертификат
sudo certbot certonly --standalone -d YOUR_DOMAIN

# Сертификаты будут сохранены в:
# /etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem
# /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem
```

**Вариант B: Использование Nginx (если Nginx уже установлен и настроен)**

```bash
# Установите Nginx, если не установлен
sudo apt-get install -y nginx

# Настройте Nginx конфиг (см. ниже)
# Затем получите сертификат
sudo certbot --nginx -d YOUR_DOMAIN
```

### 3.2. Автообновление сертификатов

Let's Encrypt автоматически настраивает cron-задачу для обновления. Проверьте:

```bash
sudo certbot renew --dry-run
```

Если тест проходит успешно, автообновление работает.

---

## Настройка приложения

### 4.1. Настройка конфигурационного файла

Отредактируйте `config/apps.json`:

```bash
nano config/apps.json
```

**Формат:**
```json
{
  "apps": [
    {
      "title": "Aerial Dream",
      "description": "Воздушная Мечта - экранная видео-заставка для Android TV",
      "url": "https://YOUR_DOMAIN/apks/Aerial Dream.apk",
      "sourceUpdate": "http://dradler.pp.ru/apps/Aerial_Dream.apk",
      "category": "Заставка"
    }
  ]
}
```

**Ключевые моменты:**
- `url` - ссылка для скачивания с ВАШЕГО сервера (формируется автоматически, но можно указать явно)
- `sourceUpdate` - ссылка/идентификатор для получения APK из внешнего источника
- `sourceMethod` (опционально) - метод получения ссылки:
  - `direct` (по умолчанию) - `sourceUpdate` содержит прямую ссылку на APK
  - `github_release` - `sourceUpdate` содержит URL GitHub API (например: `https://api.github.com/repos/owner/repo/releases/latest`), требуется `sourceFilter`
  - `gitlab_release` - аналогично для GitLab
  - `custom` - кастомная команда curl+jq в `sourceUpdate`
- `sourceFilter` (опционально) - фильтр (паттерн) для выбора нужного файла из списка (используется с `github_release`, `gitlab_release`). Пример: `arm7`, `arm64`, `.*\.apk`
- `title` - будет использоваться как имя файла (со знаками пробела и специальными символами будут заменены на `_`)
- `app_review` (опционально) - ссылка на обзор приложения (например, YouTube видео)

**Полный список полей:**
```json
{
  "title": "Название",
  "description": "Описание",
  "url": "https://YOUR_DOMAIN/apks/File.apk",
  "sourceUpdate": "https://external.com/app.apk или API URL",
  "sourceMethod": "direct|github_release|gitlab_release|custom",
  "sourceFilter": "паттерн для фильтрации (опционально)",
  "category": "Категория",
  "ver": "1.2.3",
  "lastUpdated": "2026-02-26T10:30:00Z",
  "app_review": "https://youtube.com/watch?v=..."
}
```

**Примеры конфигурации:**

1. **Прямая ссылка (direct):**
```json
{
  "title": "Aerial Dream",
  "sourceUpdate": "http://dradler.pp.ru/apps/Aerial_Dream.apk",
  "sourceMethod": "direct",
  "url": "https://YOUR_DOMAIN/apks/Aerial Dream.apk"
}
```

2. **GitHub Releases (github_release):**
```json
{
  "title": "TorrServer",
  "sourceUpdate": "https://api.github.com/repos/YouROK/TorrServe/releases/latest",
  "sourceMethod": "github_release",
  "sourceFilter": "arm7",
  "url": "https://YOUR_DOMAIN/apks/TorrServer-linux-arm7"
}
```

3. **Кастомная команда (custom):**
```json
{
  "title": "Custom App",
  "sourceUpdate": "curl -s https://api.example.com/releases | jq -r '.download_url'",
  "sourceMethod": "custom",
  "url": "https://YOUR_DOMAIN/apks/CustomApp.apk"
}
```

### 4.2. Настройка переменных окружения для Telegram

Создайте файл `.env` на основе примера:

```bash
cp .env.example .env
nano .env
```

Заполните:
```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjkLMNOPqrSTUvwxYZ1234567890
TELEGRAM_CHAT_ID=123456789
```

**Как получить токен бота:**
1. Напишите @BotFather в Telegram
2. Создайте нового бота: `/newbot`
3. Скопируйте токен

**Как получить Chat ID:**
1. Напишите вашему боту: `/start`
2. Отправьте любое сообщение
3. Перейдите в браузере: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Найдите поле `"chat":{"id":123456789,...}` - это ваш Chat ID

### 4.3. Настройка Gunicorn

Отредактируйте `gunicorn.conf.py` при необходимости:

```python
bind = "127.0.0.1:8000"
workers = 3  # Можно увеличить до (2 * CPU cores) + 1
accesslog = "logs/gunicorn_access.log"
errorlog = "logs/gunicorn_error.log"
loglevel = "info"
```

**Порт:** Gunicorn слушает на `127.0.0.1:8000`. Nginx работает как reverse proxy с SSL на порту 443.

**Важно:** SSL-сертификаты настраиваются в Nginx, а не в Gunicorn.

---

## Настройка Nginx

### 5.1. Конфигурация сервера

Создайте файл `/etc/nginx/sites-available/YOUR_DOMAIN`:

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN www.YOUR_DOMAIN;
    
    # Перенаправление на HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name YOUR_DOMAIN www.YOUR_DOMAIN;
    
    # SSL конфигурация
    ssl_certificate /etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-SHA256:ECDHE-RSA-AES256-SHA384;
    ssl_prefer_server_ciphers off;
    
    # Проксирование на gunicorn
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
    }
    
    # Статические файлы (APK)
    location /apks/ {
        alias /var/www/apks/;
        autoindex on;
    }
}
```

### 5.2. Включение сайта

```bash
# Создайте симлинк
sudo ln -s /etc/nginx/sites-available/YOUR_DOMAIN /etc/nginx/sites-enabled/

# Удалите дефолтный сайт (если есть)
sudo rm /etc/nginx/sites-enabled/default

# Проверьте конфигурацию
sudo nginx -t

# Перезапустите nginx
sudo systemctl restart nginx
```

### 5.3. Получение SSL-сертификата

```bash
# Установите certbot
sudo apt-get install -y certbot python3-certbot-nginx

# Получите сертификат
sudo certbot --nginx -d YOUR_DOMAIN -d www.YOUR_DOMAIN

# Сертификаты будут сохранены в:
# /etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem
# /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem
```

### 5.4. Автообновление сертификатов

Let's Encrypt автоматически настраивает cron-задачу или timer для обновления:

```bash
# Проверка автообновления
sudo certbot renew --dry-run

# Для systemd timer:
sudo systemctl list-timers | grep certbot
```

---

## Запуск сервиса

### 6.1. Установка systemd service

Скопируйте service файл в systemd:

```bash
sudo cp service/tinstaller.service /etc/systemd/system/
```

Отредактируйте при необходимости:
```bash
sudo nano /etc/systemd/system/tinstaller.service
```

Убедитесь, что пути корректны:
```ini
[Service]
User=YOUR_USER
WorkingDirectory=/opt/web-serv
Environment="PATH=/opt/web-serv/venv/bin"
ExecStart=/opt/web-serv/venv/bin/gunicorn -c gunicorn.conf.py app:app
```

### 6.2. Включение и запуск

```bash
# Перезагрузить systemd для подгрузки нового сервиса
sudo systemctl daemon-reload

# Включить автозапуск при загрузке
sudo systemctl enable tinstaller.service

# Запустить сервис
sudo systemctl start tinstaller.service
```

---

## Telegram бот для обновлений

Telegram бот позволяет загружать APK-файлы напрямую через чат и автоматически обновлять приложения.

### 7.1. Установка и запуск бота

```bash
# Установите зависимость
source /opt/web-serv/venv/bin/activate
pip install python-telegram-bot

# Скопируйте service файл
sudo cp service/tinstaller-bot.service /etc/systemd/system/

# Перезагрузите systemd и запустите бота
sudo systemctl daemon-reload
sudo systemctl enable tinstaller-bot.service
sudo systemctl start tinstaller-bot.service
```

### 7.2. Использование бота

1. Откройте чат с ботом в Telegram
2. Отправьте команду `/start`
3. Отправьте APK-файл (как документ)

**Логика работы:**
- Бот извлекает название приложения из имени файла
- Если найдено одно совпадение в `apps.json` → обновляет приложение
- Если найдено несколько совпадений → показывает кнопки для выбора
- Если не найдено совпадений → показывает список всех приложений
- Если версия нового APK ≤ существующей → запрашивает подтверждение
- После обновления: заменяет файл в `/var/www/apks/`, обновляет `apps.json`

### 7.3. Права доступа

Только администратор (Chat ID из `.env`) может загружать файлы через бота.

### 7.4. Логи бота

```bash
# Просмотр логов
tail -f logs/bot.log

# Через systemd
sudo journalctl -u tinstaller-bot.service -f
```

---

## Настройка автоматических обновлений

### 8.1. Добавление в crontab

Откройте crontab текущего пользователя:

```bash
crontab -e
```

Добавьте строку для ежедневного обновления в 2:00 ночи:

```
0 2 * * * /opt/web-serv/scripts/update_apps.sh >> /opt/web-serv/logs/update.log 2>&1
```

**Примечание:** Убедитесь, что переменные окружения `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` доступны в crontab. Можно добавить их в начало crontab:

```
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
0 2 * * * /opt/web-serv/scripts/update_apps.sh >> /opt/web-serv/logs/update.log 2>&1
```

Или используйте `.env` файл и загружайте его в скрипте (добавьте в начало `update_apps.sh`):

```bash
source /opt/web-serv/.env
```

### 8.2. Ручной запуск скрипта обновления

```bash
# Сделайте скрипт исполняемым (если еще не)
chmod +x scripts/update_apps.sh

# Запустите вручную
bash scripts/update_apps.sh

# Или с выводом в реальном времени
./scripts/update_apps.sh
```

---

## Проверка работоспособности

### 9.1. Проверка статуса сервиса

```bash
sudo systemctl status tinstaller.service
```

Ожидаемый вывод: `active (running)`

### 9.2. Проверка логов

```bash
# Логи приложения
sudo journalctl -u tinstaller.service -f

# Или из лог-файла
tail -f logs/gunicorn_error.log
tail -f logs/gunicorn_access.log
```

### 9.3. Проверка API

```bash
# Проверка health endpoint
curl https://YOUR_DOMAIN/health

# Ожидаемый ответ:
# {"status":"ok","timestamp":"2026-02-27T..."}

# Проверка списка приложений
curl https://YOUR_DOMAIN/

# Проверка скачивания APK (замените имя на реальное)
curl -I https://YOUR_DOMAIN/apks/Aerial%20Dream.apk
```

### 9.4. Проверка rate limiting

Отправьте много запросов для проверки ограничения:

```bash
for i in {1..70}; do
  curl -s -o /dev/null -w "%{http_code}\n" https://YOUR_DOMAIN/
done
```

Ожидайте видеть `429` после 60 запросов.

### 9.5. Проверка скрипта обновления

```bash
# Запустите скрипт
bash scripts/update_apps.sh

# Проверьте логи
tail -f logs/update.log

# Проверьте, скачались ли APK-файлы
ls -lh apks/
```

---

## Управление сервисом

### 10.1. Основные команды systemd

```bash
# Запуск
sudo systemctl start tinstaller.service

# Остановка
sudo systemctl stop tinstaller.service

# Перезапуск
sudo systemctl restart tinstaller.service

# Перезагрузка (при обновлении конфига)
sudo systemctl reload tinstaller.service

# Статус
sudo systemctl status tinstaller.service

# Включение/отключение автозапуска
sudo systemctl enable tinstaller.service
sudo systemctl disable tinstaller.service
```

### 10.2. Просмотр логов

```bash
# Все логи сервиса
sudo journalctl -u tinstaller.service

# Только последние 100 строк
sudo journalctl -u tinstaller.service -n 100

# Следить за логами в реальном времени
sudo journalctl -u tinstaller.service -f

# Логи за определенную дату
sudo journalctl -u tinstaller.service --since "2026-02-27 00:00:00"
```

### 10.3. Мониторинг дискового пространства

APK-файлы могут быть большими. Настройте мониторинг:

```bash
# Проверка свободного места
df -h /opt/web-serv

# Поиск больших APK-файлов
find apks/ -type f -size +100M -exec ls -lh {} \;

# Очистка старых логов (опционально)
sudo logrotate -f /etc/logrotate.conf
```

---

## Устранение неполадок

### 11.1. Сервис не запускается

**Проверьте логи:**
```bash
sudo journalctl -u tinstaller.service -n 50
```

**Возможные причины:**
- Не установлены зависимости Python: `pip install flask gunicorn flask-limiter`
- Нет прав на порт 443: `sudo setcap 'cap_net_bind_service=+ep' /opt/web-serv/venv/bin/gunicorn`
- Нет SSL-сертификатов: проверьте пути в `gunicorn.conf.py`
- Неправильные права на папки: `sudo chown -R YOUR_USER:YOUR_USER /opt/web-serv`

### 11.2. Ошибка 502 Bad Gateway

Gunicorn не запущен или слушает на другом порту:

```bash
# Проверьте, слушает ли Gunicorn
sudo netstat -tulpn | grep :8000
# или
sudo ss -tulpn | grep gunicorn
```

### 11.3. Ошибка 403 Forbidden при скачивании

- Проверьте права на папку `apks/`: `ls -ld apks/`
- Проверьте, что файл существует: `ls -l apks/`
- Проверьте валидацию в `app.py` - имя файла должно заканчиваться на `.apk`

### 11.4. Rate limiting слишком агрессивный

Измените в `app.py`:

```python
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["120 per minute"]  # Увеличьте значение
)
```

И перезапустите сервис:
```bash
sudo systemctl restart tinstaller.service
```

### 11.5. Скрипт обновления не работает

**Проверьте зависимости:**
```bash
command -v jq || echo "jq не установлен"
command -v aapt || echo "aapt не установлен"
command -v curl || echo "curl не установлен"
```

**Проверьте переменные окружения:**
```bash
echo $TELEGRAM_BOT_TOKEN
echo $TELEGRAM_CHAT_ID
```

**Запустите скрипт вручную с отладкой:**
```bash
bash -x scripts/update_apps.sh
```

**Проверьте логи:**
```bash
tail -f logs/update.log
```

### 11.6. Telegram уведомления не приходят

1. Проверьте токен и Chat ID в `.env`
2. Убедитесь, что бот запущен и не заблокирован
3. Проверьте, что скрипт может делать HTTP-запросы:
```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getMe"
```

### 11.7. Версия не извлекается из APK

Убедитесь, что `aapt` установлен:

```bash
aapt dump badging somefile.apk | grep versionName
```

Если `aapt` не работает, можно использовать альтернативы:
- `apktool` (более тяжелый)
- `zipinfo` для просмотра `AndroidManifest.xml`
- Или оставить версию пустой

---

## Дополнительные настройки

### 11.8. Настройка logrotate

Создайте `/etc/logrotate.d/tinstaller`:

```
/opt/web-serv/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 YOUR_USER YOUR_USER
    sharedscripts
    postrotate
        systemctl reload tinstaller.service > /dev/null 2>&1 || true
    endscript
}
```

### 11.9. Мониторинг через systemd timer (альтернатива cron)

Создайте `/etc/systemd/system/update-apps.timer`:

```ini
[Unit]
Description=Run update_apps.sh daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

И `/etc/systemd/system/update-apps.service`:

```ini
[Unit]
Description=Update APK files from external sources
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=YOUR_USER
EnvironmentFile=/opt/web-serv/.env
ExecStart=/opt/web-serv/scripts/update_apps.sh
```

Включите:
```bash
sudo systemctl enable update-apps.timer
sudo systemctl start update-apps.timer
```

### 11.10. Бэкап данных

Добавьте в crontab:

```
0 3 * * * tar -czf /backup/tinstaller-$(date +\%Y\%m\%d).tar.gz /opt/web-serv/apks/ /opt/web-serv/config/apps.json
```

---

## Безопасность

### 11.11. Рекомендации

1. **Запуск от отдельного пользователя** (не root):
```bash
sudo useradd -r -s /bin/bash tinstaller
sudo chown -R tinstaller:tinstaller /opt/web-serv
# Настройте systemd service на использование этого пользователя
```

2. **Ограничение доступа к папкам:**
```bash
chmod 750 config logs scripts service
chmod 640 config/apps.json
```

3. **Firewall:** Разрешите только порты 80 (для Let's Encrypt) и 443:
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

4. **Регулярные обновления системы:**
```bash
sudo apt-get update && sudo apt-get upgrade -y
```

---

## Контакты и поддержка

- **Домен:** https://YOUR_DOMAIN
- **Health check:** https://YOUR_DOMAIN/health
- **Логи:** `/opt/web-serv/logs/`

---

## Примечания

- Для production-использования рекомендуется использовать отдельный VPS/сервер
- Регулярно проверяйте свободное место на диске
- Настройте мониторинг (например, через Prometheus + Grafana или простой скрипт)
- Рассмотрите использование CDN для раздачи больших APK-файлов
- Рекомендуется настроить резервное копирование `apks/` и `config/apps.json`

---

## Лицензия

Проект создан для внутреннего использования.
