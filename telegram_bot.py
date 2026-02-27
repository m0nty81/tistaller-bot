#!/usr/bin/env python3
"""
Telegram бот для загрузки APK-файлов и обновления приложений.
"""

import os
import re
import json
import subprocess
import tempfile
import shutil
import httpx
from datetime import datetime
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# Пути
BASE_DIR = Path("/opt/web-serv")
CONFIG_PATH = BASE_DIR / "config" / "apps.json"
APKS_DIR = Path("/var/www/apks")
LOG_FILE = BASE_DIR / "logs" / "bot.log"

# ID админа
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

# Клавиатура с командами
def get_main_keyboard():
    """Создать основную клавиатуру с командами."""
    keyboard = [
        [KeyboardButton("/apps"), KeyboardButton("/status")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def log(message: str):
    """Логирование в файл."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}\n"
    print(log_entry, end="")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Error writing to log: {e}")


def load_apps() -> dict:
    """Загрузить apps.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_apps(data: dict):
    """Сохранить apps.json, отсортировав по title."""
    # Сортируем apps по title
    if "apps" in data:
        data["apps"] = sorted(data["apps"], key=lambda x: x.get("title", "").lower())
    
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_version_from_apk(apk_path: str) -> str:
    """Извлечь versionName из APK файла."""
    try:
        result = subprocess.run(
            ["/usr/bin/aapt", "dump", "badging", apk_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        match = re.search(r"versionName='([^']+)'", result.stdout)
        if match:
            return match.group(1)
    except Exception as e:
        log(f"Error parsing version: {e}")
    return "неизвестно"


def parse_version(version_str: str) -> tuple:
    """
    Разобрать версию на компоненты для семантического сравнения.
    Возвращает кортеж чисел.
    """
    # Удаляем префиксы типа 'v', 'MatriX.' и т.п.
    clean = re.sub(r"^[a-zA-Z]*\.?", "", version_str)
    # Извлекаем цифры и точки
    parts = re.findall(r"\d+", clean)
    return tuple(int(p) for p in parts) if parts else (0,)


def compare_versions(v1: str, v2: str) -> int:
    """
    Сравнить две версии.
    Возвращает: -1 если v1 < v2, 0 если равны, 1 если v1 > v2
    """
    t1 = parse_version(v1)
    t2 = parse_version(v2)

    if t1 < t2:
        return -1
    elif t1 > t2:
        return 1
    return 0


def find_app_by_filename(filename: str, apps: list) -> list:
    """
    Найти приложения, название которых есть в имени файла.
    Возвращает список индексов подходящих приложений.
    """
    # Удаляем расширение .apk
    name_without_ext = filename.rsplit(".", 1)[0] if "." in filename else filename

    # Нормализуем имя: заменяем _, - на пробелы, убираем спецсимволы
    normalized = re.sub(r"[_\-]", " ", name_without_ext).lower()

    matches = []
    for i, app in enumerate(apps):
        title = app.get("title", "").lower()
        # Проверяем, содержится ли title в имени файла
        if title in normalized or normalized in title:
            matches.append(i)
        # Также проверяем частичное совпадение слов
        else:
            title_words = set(title.split())
            name_words = set(normalized.split())
            if title_words & name_words:  # Есть общие слова
                matches.append(i)

    return matches


def get_target_filename(app: dict) -> str:
    """Получить целевое имя файла из url приложения."""
    url = app.get("url", "")
    if url:
        return url.rsplit("/", 1)[-1]
    # Если url нет, используем title
    title = app.get("title", "unknown")
    return re.sub(r"[^a-zA-Z0-9._-]", "_", title) + ".apk"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    await update.message.reply_text(
        "👋 Привет! Отправь мне APK-файл для обновления приложения.\n"
        "Я найду приложение в списке и предложу обновить его.",
        reply_markup=get_main_keyboard()
    )


async def apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /apps - список всех приложений."""
    try:
        data = load_apps()
        apps = data.get("apps", [])

        if not apps:
            await update.message.reply_text("📭 Список приложений пуст.")
            return

        message = "📦 <b>Доступные приложения:</b>\n\n"

        for i, app in enumerate(apps, 1):
            title = app.get("title", "Unknown")
            ver = app.get("ver", "?")
            url = app.get("url", "")

            message += f"<b>{i}. {title}</b>\n"
            message += f"   🏷️ Версия: {ver}\n"
            if url:
                filename = url.rsplit("/", 1)[-1]
                message += f"   📥 <a href=\"{url}\">{filename}</a>\n"
            message += "\n"

        await update.message.reply_text(message, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        log(f"Error in /apps command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status - информация о хосте и сервисах."""
    try:
        # Загрузка CPU через /proc/stat
        cpu_usage = "N/A"
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline()
                if line.startswith("cpu "):
                    parts = line.split()[1:5]  # user, nice, system, idle
                    values = [int(p) for p in parts]
                    total = sum(values)
                    idle = values[3]
                    usage = 100 - (idle * 100 // total) if total > 0 else 0
                    cpu_usage = f"{usage}%"
        except Exception:
            pass

        # Загрузка RAM
        ram_usage = "N/A"
        try:
            result = subprocess.run(
                ["/usr/bin/free", "-m"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.split("\n"):
                if line.startswith("Mem:"):
                    parts = line.split()
                    total = int(parts[1])
                    used = int(parts[2])
                    ram_usage = f"{used}/{total} MB ({used*100//total}%)"
                    break
        except Exception as e:
            log(f"free error: {e}")

        # Загрузка SSD
        disk_usage = "N/A"
        try:
            result = subprocess.run(
                ["/usr/bin/df", "-h", "/"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.split("\n"):
                if "/" in line and not line.startswith("Filesystem"):
                    parts = line.split()
                    if len(parts) >= 5:
                        disk_usage = f"{parts[2]}/{parts[1]} ({parts[4]})"
                    break
        except Exception as e:
            log(f"df error: {e}")

        # Статус systemd сервисов
        services_status = ""
        for service in ["tinstaller.service", "tinstaller-bot.service"]:
            try:
                result = subprocess.run(
                    ["/usr/bin/systemctl", "is-active", service],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                status = result.stdout.strip()
                icon = "🟢" if status == "active" else "🔴"
                services_status += f"   {icon} {service}: {status}\n"
            except Exception as e:
                log(f"systemctl {service} error: {e}")
                services_status += f"   ⚪ {service}: ошибка\n"

        message = (
            "🖥️ <b>Статус хоста:</b>\n\n"
            f"📊 CPU: {cpu_usage}\n"
            f"💾 RAM: {ram_usage}\n"
            f"📁 SSD: {disk_usage}\n\n"
            f"⚙️ <b>Сервисы:</b>\n{services_status}"
        )

        log(f"/status executed: CPU={cpu_usage}, RAM={ram_usage}, SSD={disk_usage}")
        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        log(f"Error in /status command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загруженных документов (APK файлов)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    document = update.message.document
    file_name = document.file_name

    # Проверяем, что это APK
    if not file_name.lower().endswith(".apk"):
        await update.message.reply_text("❌ Это не APK файл.")
        return

    log(f"Получен файл: {file_name}")

    # Проверяем размер файла
    file_size = document.file_size
    if file_size and file_size > 50 * 1024 * 1024:  # 50MB
        await update.message.reply_text(
            f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f}MB).\n"
            "Максимальный размер: 50MB.\n\n"
            "Для больших файлов используйте скрипт update_apps.sh"
        )
        return

    # Скачиваем файл во временную директорию
    temp_dir = tempfile.mkdtemp()
    temp_apk_path = os.path.join(temp_dir, file_name)

    try:
        # Скачиваем файл напрямую через httpx
        try:
            file = await context.bot.get_file(document.file_id)
            file_url = file.file_path
        except Exception as e:
            if "too big" in str(e).lower():
                await update.message.reply_text(
                    f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f}MB).\n"
                    "Telegram ограничивает загрузку файлов через бота до 20MB.\n\n"
                    "Для больших файлов используйте скрипт update_apps.sh"
                )
                shutil.rmtree(temp_dir, ignore_errors=True)
                return
            raise

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream('GET', file_url) as response:
                response.raise_for_status()
                with open(temp_apk_path, 'wb') as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)

        # Извлекаем версию
        new_version = parse_version_from_apk(temp_apk_path)
        log(f"Версия из APK: {new_version}")

        # Загружаем apps.json
        data = load_apps()
        apps = data.get("apps", [])

        # Ищем совпадения
        matches = find_app_by_filename(file_name, apps)

        if not matches:
            # Нет совпадений - показываем все приложения
            keyboard = []
            for i, app in enumerate(apps):
                keyboard.append(
                    [InlineKeyboardButton(app.get("title", f"App {i}"), callback_data=f"select_{i}")]
                )
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"📁 Файл: {file_name}\n"
                f"📦 Версия: {new_version}\n\n"
                "⚠️ Не найдено совпадений в списке приложений.\n"
                "Выберите приложение для обновления:",
                reply_markup=reply_markup,
            )
            # Сохраняем путь к файлу в context для последующего использования
            context.user_data["temp_apk_path"] = temp_apk_path
            context.user_data["file_name"] = file_name
            context.user_data["new_version"] = new_version
        elif len(matches) == 1:
            # Одно совпадение - обрабатываем сразу
            app_idx = matches[0]
            await process_update(
                update, context, app_idx, temp_apk_path, file_name, new_version
            )
        else:
            # Несколько совпадений - показываем выбор
            keyboard = []
            for i in matches:
                app = apps[i]
                keyboard.append(
                    [InlineKeyboardButton(app.get("title", f"App {i}"), callback_data=f"select_{i}")]
                )
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"📁 Файл: {file_name}\n"
                f"📦 Версия: {new_version}\n\n"
                f"🔍 Найдено совпадений: {len(matches)}\n"
                "Выберите приложение для обновления:",
                reply_markup=reply_markup,
            )
            context.user_data["temp_apk_path"] = temp_apk_path
            context.user_data["file_name"] = file_name
            context.user_data["new_version"] = new_version
            context.user_data["matches"] = matches

    except Exception as e:
        log(f"Error handling document: {e}")
        await update.message.reply_text(f"❌ Ошибка обработки файла: {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопок."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ Доступ запрещён.")
        return

    data = query.data

    if data == "cancel":
        await query.edit_message_text("❌ Отменено.")
        # Очищаем временные данные
        if "temp_apk_path" in context.user_data:
            temp_dir = os.path.dirname(context.user_data["temp_apk_path"])
            shutil.rmtree(temp_dir, ignore_errors=True)
            context.user_data.clear()
        return

    if data.startswith("select_"):
        app_idx = int(data.split("_")[1])

        temp_apk_path = context.user_data.get("temp_apk_path")
        file_name = context.user_data.get("file_name", "unknown.apk")
        new_version = context.user_data.get("new_version", "неизвестно")

        if not temp_apk_path or not os.path.exists(temp_apk_path):
            await query.edit_message_text("❌ Файл не найден. Отправьте APK ещё раз.")
            return

        await process_update(update, context, app_idx, temp_apk_path, file_name, new_version)

    elif data.startswith("confirm_"):
        app_idx = int(data.split("_")[1])

        temp_apk_path = context.user_data.get("temp_apk_path")
        file_name = context.user_data.get("file_name", "unknown.apk")
        new_version = context.user_data.get("new_version", "неизвестно")

        if not temp_apk_path or not os.path.exists(temp_apk_path):
            await query.edit_message_text("❌ Файл не найден. Отправьте APK ещё раз.")
            return

        # Получаем старую версию
        data_apps = load_apps()
        apps = data_apps.get("apps", [])
        old_version = apps[app_idx].get("ver", "неизвестно") if app_idx < len(apps) else "?"

        await do_update(
            update, context, app_idx, temp_apk_path, file_name, new_version, old_version
        )


async def process_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    app_idx: int,
    temp_apk_path: str,
    file_name: str,
    new_version: str,
):
    """Обработка обновления приложения."""
    data = load_apps()
    apps = data.get("apps", [])

    if app_idx >= len(apps):
        await update.callback_query.edit_message_text("❌ Неверный индекс приложения.")
        return

    app = apps[app_idx]
    old_version = app.get("ver", "неизвестно")
    title = app.get("title", "Unknown")

    log(f"Обработка обновления: {title}")
    log(f"  Старая версия: {old_version}")
    log(f"  Новая версия: {new_version}")

    # Сравниваем версии
    cmp_result = compare_versions(new_version, old_version)

    if cmp_result <= 0:
        # Новая версия <= старой - спрашиваем подтверждение
        if cmp_result == 0:
            msg = (
                f"📦 {title}\n"
                f"Версии совпадают: {old_version}\n\n"
                "Перезаписать файл?"
            )
        else:
            msg = (
                f"📦 {title}\n"
                f"⚠️ Новая версия ({new_version}) < старой ({old_version})\n\n"
                "Продолжить?"
            )

        keyboard = [
            [
                InlineKeyboardButton("✅ Да", callback_data=f"confirm_{app_idx}"),
                InlineKeyboardButton("❌ Нет", callback_data="cancel"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Сохраняем данные для подтверждения
        context.user_data["confirm_app_idx"] = app_idx
        context.user_data["temp_apk_path"] = temp_apk_path
        context.user_data["file_name"] = file_name
        context.user_data["new_version"] = new_version

        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        return

    # Новая версия > старой - обновляем сразу
    await do_update(
        update, context, app_idx, temp_apk_path, file_name, new_version, old_version
    )


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик подтверждения обновления."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ Доступ запрещён.")
        return

    data = query.data
    if not data.startswith("confirm_"):
        return

    app_idx = int(data.split("_")[1])

    temp_apk_path = context.user_data.get("temp_apk_path")
    file_name = context.user_data.get("file_name", "unknown.apk")
    new_version = context.user_data.get("new_version", "неизвестно")

    if not temp_apk_path or not os.path.exists(temp_apk_path):
        await query.edit_message_text("❌ Файл не найден. Отправьте APK ещё раз.")
        return

    # Получаем старую версию
    data = load_apps()
    apps = data.get("apps", [])
    old_version = apps[app_idx].get("ver", "неизвестно") if app_idx < len(apps) else "?"

    await do_update(
        update, context, app_idx, temp_apk_path, file_name, new_version, old_version
    )


async def do_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    app_idx: int,
    temp_apk_path: str,
    file_name: str,
    new_version: str,
    old_version: str,
):
    """Выполнить обновление приложения."""
    data = load_apps()
    apps = data.get("apps", [])
    app = apps[app_idx]
    title = app.get("title", "Unknown")

    try:
        # Получаем целевое имя файла
        target_filename = get_target_filename(app)
        target_path = APKS_DIR / target_filename

        log(f"Копирование файла: {temp_apk_path} -> {target_path}")

        # Копируем файл
        shutil.copy2(temp_apk_path, target_path)
        os.chmod(target_path, 0o644)

        # Обновляем apps.json
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        data["apps"][app_idx]["ver"] = new_version
        data["apps"][app_idx]["lastUpdated"] = timestamp
        save_apps(data)

        log(f"Обновлено: {title} {old_version} -> {new_version}")

        # Очищаем временные данные
        temp_dir = os.path.dirname(temp_apk_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        context.user_data.clear()

        # Отправляем уведомление
        msg = (
            f"🔄 Обновлено: <b>{title}</b>\n"
            f"Версия: {old_version} → {new_version}\n"
            f"Дата: {timestamp}"
        )

        if update.callback_query:
            await update.callback_query.edit_message_text(
                f"✅ <b>{title}</b> обновлён!\n{old_version} → {new_version}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(msg, parse_mode="HTML")

        # Отправляем уведомление в Telegram (как в скрипте)
        await send_telegram_notification(
            context.bot, f"🔄 Обновлено: {title}\nВерсия: {old_version} → {new_version}"
        )

    except Exception as e:
        log(f"Error during update: {e}")
        if update.callback_query:
            await update.callback_query.edit_message_text(f"❌ Ошибка обновления: {e}")
        else:
            await update.message.reply_text(f"❌ Ошибка обновления: {e}")


async def send_telegram_notification(bot, message: str):
    """Отправить уведомление в Telegram (админу)."""
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            parse_mode="HTML",
        )
    except Exception as e:
        log(f"Error sending notification: {e}")


def main():
    """Запуск бота."""
    # Получаем токен из .env
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log("ERROR: TELEGRAM_BOT_TOKEN not set")
        return

    log("Запуск бота...")

    # Создаём приложение
    application = Application.builder().token(token).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("apps", apps_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(
        MessageHandler(filters.Document.ALL, handle_document)
    )
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Запускаем
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
