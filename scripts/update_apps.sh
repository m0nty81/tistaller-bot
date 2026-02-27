#!/bin/bash
source /opt/web-serv/.env
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

# Функция для сравнения версий (семантическое)
# Возвращает: 0 если равны, 1 если v1 > v2, 2 если v1 < v2
compare_versions() {
    local v1="$1"
    local v2="$2"
    
    # Удаляем префиксы и оставляем только цифры и точки
    local clean_v1=$(echo "$v1" | sed 's/^[a-zA-Z]*\.?*//' | grep -oE '[0-9]+(\.[0-9]+)*' | head -1)
    local clean_v2=$(echo "$v2" | sed 's/^[a-zA-Z]*\.?*//' | grep -oE '[0-9]+(\.[0-9]+)*' | head -1)
    
    if [[ -z "$clean_v1" ]]; then clean_v1="0"; fi
    if [[ -z "$clean_v2" ]]; then clean_v2="0"; fi
    
    # Сравниваем покомпонентно
    IFS='.' read -ra V1_PARTS <<< "$clean_v1"
    IFS='.' read -ra V2_PARTS <<< "$clean_v2"
    
    local max_len=${#V1_PARTS[@]}
    if [[ ${#V2_PARTS[@]} -gt $max_len ]]; then
        max_len=${#V2_PARTS[@]}
    fi
    
    for ((i=0; i<max_len; i++)); do
        local p1=${V1_PARTS[i]:-0}
        local p2=${V2_PARTS[i]:-0}
        
        if [[ $p1 -gt $p2 ]]; then
            echo "1"
            return
        elif [[ $p1 -lt $p2 ]]; then
            echo "2"
            return
        fi
    done
    
    echo "0"
}

# Проверяем права доступа к директории APK
if [[ ! -d "$APKS_DIR" ]]; then
    # Если директория не существует, создаем ее
    mkdir -p "$APKS_DIR"
    if [[ $? -ne 0 ]]; then
        log "ERROR: Не удалось создать директорию: $APKS_DIR"
        send_telegram "❌ Ошибка: не удалось создать директорию: $APKS_DIR"
        exit 1
    fi
fi

# Проверяем права на запись
if [[ ! -w "$APKS_DIR" ]]; then
    log "ERROR: Нет прав на запись в директорию: $APKS_DIR"
    send_telegram "❌ Ошибка: нет прав на запись в директорию: $APKS_DIR"
    exit 1
fi

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
    SOURCE_UPDATE=$(jq -r ".apps[$i].sourceUpdate" "$CONFIG_FILE")
    SOURCE_METHOD=$(jq -r ".apps[$i].sourceMethod // \"direct\"" "$CONFIG_FILE")
    SOURCE_FILTER=$(jq -r ".apps[$i].sourceFilter // \"\"" "$CONFIG_FILE")
    TARGET_URL=$(jq -r ".apps[$i].url" "$CONFIG_FILE")
    OLD_VER=$(jq -r ".apps[$i].ver // \"\"" "$CONFIG_FILE")
    OLD_UPDATED=$(jq -r ".apps[$i].lastUpdated // \"\"" "$CONFIG_FILE")
    
    # Определяем имя файла из поля "url"
    FILENAME=$(basename "$TARGET_URL")
    
    log "Обработка: $TITLE"
    log "  Метод: $SOURCE_METHOD"
    log "  Источник: $SOURCE_UPDATE"
    log "  Целевой файл: $FILENAME"
    
    # Определяем прямую ссылку для скачивания в зависимости от метода
    DOWNLOAD_URL=""
    
    case "$SOURCE_METHOD" in
        "direct")
            # Прямая ссылка на APK
            DOWNLOAD_URL="$SOURCE_UPDATE"
            ;;
        "github_release")
            # GitHub Releases API
            if [[ -z "$SOURCE_FILTER" ]]; then
                log "  ERROR: sourceFilter обязателен для github_release"
                send_telegram "❌ Ошибка: $TITLE - не указан sourceFilter"
                continue
            fi
            # Получаем URL через GitHub API
            API_RESPONSE=$(curl -s "$SOURCE_UPDATE" 2>/dev/null)
            if [[ -z "$API_RESPONSE" ]]; then
                log "  ERROR: Не удалось получить ответ от GitHub API"
                send_telegram "❌ Ошибка API GitHub: $TITLE"
                continue
            fi
            # Ищем asset по фильтру (паттерну в имени файла)
            DOWNLOAD_URL=$(echo "$API_RESPONSE" | jq -r ".assets[] | select(.name | test(\"$SOURCE_FILTER\")) | .browser_download_url" | head -1)
            if [[ -z "$DOWNLOAD_URL" || "$DOWNLOAD_URL" == "null" ]]; then
                log "  ERROR: Не найден asset по фильтру: $SOURCE_FILTER"
                send_telegram "❌ Не найден asset: $TITLE (фильтр: $SOURCE_FILTER)"
                continue
            fi
            ;;
        "gitlab_release")
            # GitLab Releases API (аналогично GitHub)
            if [[ -z "$SOURCE_FILTER" ]]; then
                log "  ERROR: sourceFilter обязателен для gitlab_release"
                send_telegram "❌ Ошибка: $TITLE - не указан sourceFilter"
                continue
            fi
            API_RESPONSE=$(curl -s "$SOURCE_UPDATE" 2>/dev/null)
            if [[ -z "$API_RESPONSE" ]]; then
                log "  ERROR: Не удалось получить ответ от GitLab API"
                send_telegram "❌ Ошибка API GitLab: $TITLE"
                continue
            fi
            DOWNLOAD_URL=$(echo "$API_RESPONSE" | jq -r ".assets.assets[] | select(.name | test(\"$SOURCE_FILTER\")) | .url" | head -1)
            if [[ -z "$DOWNLOAD_URL" || "$DOWNLOAD_URL" == "null" ]]; then
                log "  ERROR: Не найден asset по фильтру: $SOURCE_FILTER"
                send_telegram "❌ Не найден asset: $TITLE (фильтр: $SOURCE_FILTER)"
                continue
            fi
            ;;
        "custom")
            # Кастомная команда для получения ссылки
            # В sourceUpdate должна быть команда, которая выводит URL
            if [[ -z "$SOURCE_UPDATE" ]]; then
                log "  ERROR: sourceUpdate обязателен для custom"
                continue
            fi
            DOWNLOAD_URL=$(eval "$SOURCE_UPDATE" 2>/dev/null | head -1)
            if [[ -z "$DOWNLOAD_URL" ]]; then
                log "  ERROR: Кастомная команда не вернула URL"
                send_telegram "❌ Ошибка custom команды: $TITLE"
                continue
            fi
            ;;
        *)
            log "  ERROR: Неизвестный sourceMethod: $SOURCE_METHOD"
            send_telegram "❌ Неизвестный метод: $SOURCE_METHOD для $TITLE"
            continue
            ;;
    esac
    
    # Если не получили URL - пропускаем
    if [[ -z "$DOWNLOAD_URL" ]]; then
        log "  ERROR: Не удалось определить URL для скачивания"
        send_telegram "❌ Не определен URL: $TITLE"
        continue
    fi
    
    APK_PATH="$APKS_DIR/$FILENAME"
    
    log "  Файл: $FILENAME"
    log "  URL: $DOWNLOAD_URL"
    
    # Создаем временный файл
    TEMP_APK="$TEMP_DIR/$FILENAME"
    
    # Скачиваем файл
    log "  Скачивание файла..."
    if ! curl -L -s "$DOWNLOAD_URL" > "$TEMP_APK"; then
        log "  ERROR: Не удалось скачать файл: $TITLE"
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

        # Извлекаем версию из APK
        NEW_VER=$(aapt dump badging "$TEMP_APK" 2>/dev/null | grep "versionName" | head -1 | sed "s/.*versionName='\([^']*\)'.*/\1/" || echo "неизвестно")
        log "  Версия из APK: $NEW_VER"

        # Сравниваем версии
        OLD_VER_DISPLAY=${OLD_VER:-"неизвестно"}
        CMP_RESULT=$(compare_versions "$NEW_VER" "$OLD_VER_DISPLAY")

        if [[ "$CMP_RESULT" == "2" ]]; then
            # Новая версия < старой
            log "  Пропущено: версия понижается ($OLD_VER_DISPLAY → $NEW_VER)"
#            send_telegram "⚠️ Пропущено: <b>$TITLE</b>"$'\n'"Версия понижается: $OLD_VER_DISPLAY → $NEW_VER"
            continue
        elif [[ "$CMP_RESULT" == "0" ]]; then
            # Версии равны (но хэш разный - пересборка)
            log "  Версии равны ($OLD_VER_DISPLAY), но хэш разный (пересборка)"
        else
            # Новая версия > старой
            log "  Обновление: $OLD_VER_DISPLAY → $NEW_VER"
        fi

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
if [[ -z "$OLD_VER" ]]; then
    # Новое приложение
    send_telegram "🆕 Добавлено: <b>$TITLE</b>"$'\n'"Версия: $NEW_VER"
else
    # Обновление существующего приложения
    send_telegram "🔄 Обновлено: <b>$TITLE</b>"$'\n'"Версия: $OLD_VER_DISPLAY → $NEW_VER"
fi
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
        send_telegram "🆕 Добавлено: <b>$TITLE</b>"$'\n'"Версия: $NEW_VER"
    fi
done

log "Завершено. Обновлено приложений: $UPDATED_APPS"
log "=== Конец обновления ==="

exit 0
