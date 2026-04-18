#!/usr/bin/env python3
"""
Единый сервер TInstaller: Flask API + Telegram бот + планировщик обновлений.
"""

import os
import logging
from logging.handlers import RotatingFileHandler
import re
import json
import subprocess
import hashlib
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
import asyncio

from dotenv import load_dotenv

# Загружаем .env файл
load_dotenv(Path(__file__).parent / ".env")

from flask import Flask, jsonify, send_file, abort, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

BASE_DIR = Path("/opt/web-serv")
CONFIG_PATH = BASE_DIR / "config" / "apps.json"
FILES_CONFIG_PATH = BASE_DIR / "config" / "files.json"
APKS_DIR = BASE_DIR / "apks"
FILES_DIR = BASE_DIR / "files"
LOG_FILE = BASE_DIR / "logs" / "server.log"

# Настройка ротации логов
logger = logging.getLogger("tinstaller")
logger.setLevel(logging.INFO)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
handler = RotatingFileHandler(str(LOG_FILE), maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
formatter = logging.Formatter('[%(asctime)s][%(process)d][%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
SERVER_DOMAIN = os.environ.get("SERVER_DOMAIN", "")

# Интервал проверки обновлений (в часах)
UPDATE_CHECK_INTERVAL_HOURS = int(os.environ.get("UPDATE_CHECK_INTERVAL_HOURS", "6"))
RETRY_DOWNLOAD_ATTEMPTS = int(os.environ.get("RETRY_DOWNLOAD_ATTEMPTS", "3"))
RETRY_DOWNLOAD_DELAY = int(os.environ.get("RETRY_DOWNLOAD_DELAY", "5"))

# =============================================================================
# ЛОГИРОВАНИЕ
# =============================================================================

def log(message: str, level: str = "info"):
    """Логирование в файл и консоль с ротацией."""
    log_entry = f"{message}"
    print(f"[LOG][{os.getpid()}] {log_entry}")
    if level == "error":
        logger.error(log_entry)
    elif level == "warning":
        logger.warning(log_entry)
    else:
        logger.info(log_entry)


# =============================================================================
# УВЕДОМЛЕНИЯ TELEGRAM
# =============================================================================

async def send_telegram(message: str, parse_mode: str = "HTML"):
    """Отправить уведомление в Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log(f"Telegram notification skipped (no token/chat_id): {message}")
        return
    
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": parse_mode,
                }
            )
            if response.status_code != 200:
                log(f"Telegram API error: {response.status_code} - {response.text}")
    except Exception as e:
        log(f"Error sending Telegram notification: {e}")


# =============================================================================
# РАБОТА С ВЕРСИЯМИ
# =============================================================================

def parse_version(version_str: str) -> tuple:
    """
    Разобрать версию на компоненты для семантического сравнения.
    Возвращает кортеж чисел.
    """
    if not version_str:
        return (0,)
    
    # Удаляем префиксы типа 'v', 'MatriX.' и т.п.
    clean = re.sub(r"^[a-zA-Z]*\.?", "", str(version_str))
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
        return -1  # v1 < v2 (понижение)
    elif t1 > t2:
        return 1   # v1 > v2 (обновление)
    return 0       # равны


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
        log(f"Error parsing version from APK: {e}")
    return "неизвестно"


def parse_build_from_apk(apk_path: str) -> str:
    """Извлечь versionCode (build) из APK файла."""
    try:
        result = subprocess.run(
            ["/usr/bin/aapt", "dump", "badging", apk_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        match = re.search(r"versionCode='([^']+)'", result.stdout)
        if match:
            return match.group(1)
    except Exception as e:
        log(f"Error parsing build from APK: {e}")
    return ""


def format_version_display(version: str, build: str) -> str:
    if not version or version == "неизвестно":
        return version or "неизвестно"
    if build and build != "неизвестно":
        return f"{version} (build {build})"
    return version


def compare_builds(b1: str, b2: str) -> int:
    if b1 == b2:
        return 0
    if not b2:
        return 1
    if not b1:
        return -1

    if b1.isdigit() and b2.isdigit():
        return 1 if int(b1) > int(b2) else -1

    return 1 if b1 > b2 else -1 if b1 < b2 else 0


def compare_versions_with_build(v1: str, b1: str, v2: str, b2: str) -> int:
    cmp = compare_versions(v1, v2)
    if cmp != 0:
        return cmp
    return compare_builds(b1, b2)


def get_installed_build(app: dict) -> str:
    """Получить build/versionCode из установленного APK или из конфига."""
    target_filename = get_target_filename(app)
    apk_path = APKS_DIR / target_filename

    if apk_path.exists():
        build = parse_build_from_apk(str(apk_path))
        if build:
            return build

    return app.get("build", "")


def get_installed_version_display(app: dict) -> str:
    version = get_installed_version(app)
    build = get_installed_build(app)
    return format_version_display(version, build)


def sha256_file(filepath: str) -> str:
    """Вычислить SHA256 хэш файла."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_filename_from_response(response: httpx.Response, url: str, default_name: str = "temp.apk") -> str:
    """Определить имя файла по ответу HTTP или по исходному URL."""
    content_disposition = response.headers.get("content-disposition", "")
    if content_disposition:
        match = re.search(r"filename\*?=\s*(?:UTF-8''?)?\"?([^\";]+)\"?", content_disposition, re.IGNORECASE)
        if match:
            filename = os.path.basename(match.group(1).strip().strip('"'))
            if filename:
                return filename

    filename = Path(response.url.path).name
    if filename:
        return filename

    filename = Path(url).name
    if filename:
        return filename

    return default_name


async def download_apk_from_url(url: str, temp_dir: str, default_filename: str = "temp.apk") -> tuple[str, str]:
    """Скачать APK по URL с поддержкой редиректов и вернуть путь + имя файла."""
    os.makedirs(temp_dir, exist_ok=True)

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            filename = get_filename_from_response(response, url, default_filename)
            if not filename.lower().endswith(".apk"):
                filename = default_filename

    temp_apk_path = os.path.join(temp_dir, filename)
    await stream_url_to_file_with_retry(url, temp_apk_path)

    if not os.path.exists(temp_apk_path) or os.path.getsize(temp_apk_path) == 0:
        raise ValueError("Downloaded file is empty")

    return temp_apk_path, filename


async def stream_url_to_file_with_retry(url: str, file_path: str, retries: int = RETRY_DOWNLOAD_ATTEMPTS, delay: int = RETRY_DOWNLOAD_DELAY) -> None:
    """Скачать URL в файл с несколькими попытками при сетевых ошибках."""
    last_exception = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
        for attempt in range(1, retries + 1):
            try:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with open(file_path, "wb") as f:
                        async for chunk in response.aiter_bytes():
                            f.write(chunk)
                return
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response is not None and e.response.status_code < 500:
                    log(f"  Ошибка HTTP {e.response.status_code}: {e}")
                    raise
                last_exception = e
                log(f"  Попытка {attempt} не удалась: {e}")
                if attempt < retries:
                    log(f"  Повторная попытка через {delay} секунд...")
                    await asyncio.sleep(delay)
                else:
                    raise
            except Exception as e:
                log(f"  Ошибка при скачивании: {e}")
                raise


def parse_github_repo_url(url: str) -> str | None:
    """Вернуть owner/repo для GitHub URL или API URL."""
    if not url:
        return None

    match = re.search(r"github\.com/([^/]+)/([^/]+)/?", url)
    if match:
        owner = match.group(1)
        repo = match.group(2)
        if repo.endswith(".git"):
            repo = repo[:-4]
        return f"{owner}/{repo}"

    match = re.search(r"api\.github\.com/repos/([^/]+/[^/]+)", url)
    if match:
        repo = match.group(1)
        if repo.endswith(".git"):
            repo = repo[:-4]
        return repo

    return None


def make_github_api_url(repo: str, endpoint: str = "releases/latest") -> str:
    return f"https://api.github.com/repos/{repo}/{endpoint}"


async def fetch_github_releases(repo: str, per_page: int = 5) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.get(make_github_api_url(repo, f"releases?per_page={per_page}"))
            response.raise_for_status()
            return response.json()
    except Exception as e:
        log(f"ERROR: GitHub fetch releases error: {e}")
        return []


async def fetch_github_release_latest(repo: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.get(make_github_api_url(repo, "releases/latest"))
            response.raise_for_status()
            return response.json()
    except Exception as e:
        log(f"ERROR: GitHub fetch latest release error: {e}")
        return None


def find_github_latest_release(releases: list[dict]) -> dict | None:
    return next(
        (r for r in releases if not r.get("draft", False) and not r.get("prerelease", False)),
        None,
    )


def parse_github_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.min


def choose_github_auto_release(releases: list[dict]) -> dict | None:
    if not releases:
        return None

    latest = find_github_latest_release(releases)
    if latest:
        return latest

    valid = [r for r in releases if not r.get("draft", False)]
    if not valid:
        return None

    return max(valid, key=lambda r: parse_github_datetime(r.get("published_at") or r.get("created_at")))


def select_github_assets(assets: list[dict]) -> list[tuple[str, dict]]:
    """Вернуть список выбранных asset-ов GitHub по архитектурам или universal."""
    if not assets:
        return []

    universal = [a for a in assets if a.get("browser_download_url") and re.search(r"universal", a.get("name", ""), re.I)]
    if universal:
        return [("universal", universal[0])]

    selected = []
    for arch, pattern in [("v7a", r"v7a|armeabi-v7a"), ("v8a", r"v8a|arm64-v8a")]:
        asset = next(
            (a for a in assets if a.get("browser_download_url") and re.search(pattern, a.get("name", ""), re.I)),
            None,
        )
        if asset:
            selected.append((arch, asset))

    if selected:
        return selected

    fallback = next(
        (a for a in assets if a.get("browser_download_url") and a.get("name", "").lower().endswith(".apk")),
        None,
    )
    if fallback:
        return [("universal", fallback)]

    return []


def make_arch_source_filter(arch: str, asset_name: str | None = None) -> str:
    if asset_name:
        return re.escape(asset_name)
    if arch == "universal":
        return r"universal"
    if arch == "v7a":
        return r"v7a|armeabi-v7a"
    if arch == "v8a":
        return r"v8a|arm64-v8a"
    return r".*\.apk$"


# =============================================================================
# ЗАГРУЗКА/СОХРАНЕНИЕ КОНФИГА
# =============================================================================

def load_apps() -> dict:
    """Загрузить apps.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_apps(data: dict):
    """Сохранить apps.json, отсортировав по title."""
    if "apps" in data:
        data["apps"] = sorted(data["apps"], key=lambda x: x.get("title", "").lower())

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =============================================================================
# УПРАВЛЕНИЕ ФАЙЛАМИ (FILES)
# =============================================================================

def load_files() -> dict:
    """Загрузить files.json."""
    try:
        with open(FILES_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"files": []}


def save_files(data: dict):
    """Сохранить files.json, отсортировав по filename."""
    if "files" in data:
        data["files"] = sorted(data["files"], key=lambda x: x.get("filename", "").lower())

    FILES_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FILES_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =============================================================================
# ЛОГИКА ОБНОВЛЕНИЯ (из update_apps.sh)
# =============================================================================

async def get_download_url(app: dict) -> str | None:
    """Получить прямую ссылку для скачивания в зависимости от метода."""
    source_update = app.get("sourceUpdate", "")
    source_method = app.get("sourceMethod", "direct")
    source_filter = app.get("sourceFilter", "")
    
    if source_method == "direct":
        return source_update
    
    elif source_method == "github":
        if not source_update:
            log(f"ERROR: sourceUpdate обязателен для github")
            return None
        if not source_filter:
            log(f"ERROR: sourceFilter обязателен для github")
            return None

        repo = parse_github_repo_url(source_update)
        if not repo:
            log(f"ERROR: Неверный github URL: {source_update}")
            return None

        try:
            release = await fetch_github_release_latest(repo)
            if not release:
                releases = await fetch_github_releases(repo, per_page=10)
                release = choose_github_auto_release(releases)

            if not release:
                log(f"ERROR: Не удалось найти релиз GitHub для {repo}")
                return None

            assets = release.get("assets", [])
            for asset in assets:
                name = asset.get("name", "")
                if re.search(source_filter, name, re.I):
                    return asset.get("browser_download_url")

            log(f"ERROR: Не найден asset по фильтру: {source_filter}")
            return None
        except Exception as e:
            log(f"ERROR: GitHub API error: {e}")
            return None

    elif source_method == "github_release":
        if not source_filter:
            log(f"ERROR: sourceFilter обязателен для github_release")
            return None
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.get(source_update)
                response.raise_for_status()
                api_data = response.json()
            
            assets = api_data.get("assets", [])
            for asset in assets:
                name = asset.get("name", "")
                if re.search(source_filter, name):
                    return asset.get("browser_download_url")
            
            log(f"ERROR: Не найден asset по фильтру: {source_filter}")
            return None
        except Exception as e:
            log(f"ERROR: GitHub API error: {e}")
            return None
    
    elif source_method == "gitlab_release":
        if not source_filter:
            log(f"ERROR: sourceFilter обязателен для gitlab_release")
            return None
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.get(source_update)
                response.raise_for_status()
                api_data = response.json()
            
            # GitLab структура может отличаться
            assets = api_data.get("assets", {}).get("assets", [])
            for asset in assets:
                name = asset.get("name", "")
                if re.search(source_filter, name):
                    return asset.get("url")
            
            log(f"ERROR: Не найден asset по фильтру: {source_filter}")
            return None
        except Exception as e:
            log(f"ERROR: GitLab API error: {e}")
            return None
    
    elif source_method == "custom":
        if not source_update:
            log(f"ERROR: sourceUpdate обязателен для custom")
            return None

        try:
            log(f"  Выполнение custom команды...")
            result = subprocess.run(
                source_update,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            # Логируем вывод команды
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            
            log(f"  Custom команда stdout: {stdout[:200] if stdout else 'пусто'}")
            if stderr:
                log(f"  Custom команда stderr: {stderr[:200]}")
            
            # Берём первую строку из stdout
            url = stdout.split("\n")[0] if stdout else ""
            
            # Проверяем, что URL начинается с http/https
            if url and (url.startswith("http://") or url.startswith("https://")):
                log(f"  Custom команда вернула URL: {url[:80]}...")
                return url
            else:
                log(f"ERROR: Custom команда не вернула корректный URL (получено: {url[:50] if url else 'пусто'})")
                return None
        except subprocess.TimeoutExpired:
            log(f"ERROR: Custom команда превысила таймаут (30с)")
            return None
        except Exception as e:
            log(f"ERROR: Custom command error: {e}")
            return None
    
    else:
        log(f"ERROR: Неизвестный sourceMethod: {source_method}")
        return None


async def update_single_app(app_idx: int, data: dict) -> bool:
    """
    Обновить одно приложение.
    Возвращает True если обновление выполнено.
    """
    apps = data.get("apps", [])
    if app_idx >= len(apps):
        return False

    app = apps[app_idx]
    title = app.get("title", "Unknown")
    old_ver = app.get("ver", "")
    target_url = app.get("url", "")

    log(f"Обработка: {title}")
    log(f"  Метод: {app.get('sourceMethod', 'direct')}")
    log(f"  Источник: {app.get('sourceUpdate', '')}")

    # Получаем URL для скачивания
    download_url = await get_download_url(app)
    if not download_url:
        await send_telegram(f"❌ Не определен URL: <b>{title}</b>")
        return False

    # Определяем имя файла из url (нормализуем регистр)
    filename = Path(target_url).name if target_url else f"{title}.apk"
    # Для сравнения хэша и версии используем существующий файл в apks/
    apk_path = APKS_DIR / filename

    # Скачиваем во временный файл
    temp_dir = tempfile.mkdtemp()
    temp_apk = Path(temp_dir) / filename

    try:
        log(f"  Скачивание: {download_url}")
        await stream_url_to_file_with_retry(download_url, str(temp_apk))

        # Проверяем размер
        if not temp_apk.exists() or temp_apk.stat().st_size == 0:
            log(f"  ERROR: Скачанный файл пустой")
            await send_telegram(f"❌ Пустой файл: <b>{title}</b>")
            return False

        # Извлекаем версию и build из нового APK
        new_ver = parse_version_from_apk(str(temp_apk))
        new_build = parse_build_from_apk(str(temp_apk))
        new_ver_display = format_version_display(new_ver, new_build)
        log(f"  Версия из APK: {new_ver_display}")

        # Проверяем хэш только если файл существует
        old_build = app.get("build", "")
        if apk_path.exists():
            old_hash = sha256_file(str(apk_path))
            new_hash = sha256_file(str(temp_apk))

            if new_hash == old_hash:
                log(f"  Пропущено (хэш совпадает)")
                return False

            old_ver_display = format_version_display(old_ver, old_build)

            # Сравниваем версии и build
            cmp_result = compare_versions_with_build(new_ver, new_build, old_ver, old_build)

            if cmp_result == -1:
                # Новая версия < старой - пропускаем БЕЗ уведомления, файл НЕ заменяем
                log(f"  Пропущено: версия понижается ({old_ver_display} → {new_ver_display})")
                return False

            elif cmp_result == 0:
                # Версии и build равны (пересборка) - пропускаем БЕЗ уведомления, файл НЕ заменяем
                log(f"  Пропущено: версии равны ({old_ver_display}), хэш разный (пересборка)")
                return False

            else:
                # Новая версия > старой - заменяем файл
                log(f"  Обновление: {old_ver_display} → {new_ver_display}")
        else:
            # Новый файл (первая загрузка)
            log(f"  Новый файл (первая загрузка), версия: {new_ver_display}")

        # Копируем файл (перезаписываем существующий или создаём новый)
        shutil.move(str(temp_apk), str(apk_path))
        os.chmod(apk_path, 0o644)

        # Обновляем конфиг
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        data["apps"][app_idx]["ver"] = new_ver
        data["apps"][app_idx]["build"] = new_build
        data["apps"][app_idx]["lastUpdated"] = timestamp
        save_apps(data)

        # Отправляем уведомление
        old_ver_display = format_version_display(old_ver, old_build)
        if old_ver:
            await send_telegram(f"🔄 Обновлено: <b>{title}</b>\nВерсия: {old_ver_display} → {new_ver_display}")
        else:
            await send_telegram(f"🆕 Добавлено: <b>{title}</b>\nВерсия: {new_ver_display}")

        log(f"  Успешно обновлено")
        return True

    except Exception as e:
        log(f"  ERROR: {e}")
        await send_telegram(
            f"❌ Ошибка обновления: <b>{title}</b>\n"
            f"Не удалось скачать APK. Проверьте сеть и DNS.\n{e}"
        )
        return False

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def update_all_apps():
    """Проверить и обновить все приложения."""
    log("=== Начало обновления ===")
    
    # Создаем директорию APK
    APKS_DIR.mkdir(parents=True, exist_ok=True)
    
    try:
        data = load_apps()
    except Exception as e:
        log(f"ERROR: Не удалось загрузить конфиг: {e}")
        await send_telegram(f"❌ Ошибка: конфиг не найден\n{e}")
        return
    
    updated_count = 0
    apps = data.get("apps", [])
    
    for i in range(len(apps)):
        # Пропускаем приложения без sourceUpdate
        if not apps[i].get("sourceUpdate"):
            continue
        
        result = await update_single_app(i, data)
        if result:
            updated_count += 1
    
    log(f"Завершено. Обновлено приложений: {updated_count}")
    log("=== Конец обновления ===")


# =============================================================================
# FLASK API
# =============================================================================

app = Flask(__name__)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["60 per minute"]
)


@app.route("/")
@limiter.limit("60 per minute")
def get_apps():
    try:
        data = load_apps()
        return jsonify(data)
    except Exception as e:
        log(f"Error loading apps: {e}")
        abort(500)


@app.route("/apks/<filename>")
@limiter.limit("30 per minute")
def download_apk(filename):
    # Валидация имени файла
    if ".." in filename or "/" in filename or "\\" in filename:
        abort(403)
    
    if not filename.lower().endswith(".apk"):
        abort(403)
    
    filepath = APKS_DIR / filename
    if not filepath.exists():
        abort(404)
    
    return send_file(
        str(filepath),
        mimetype="application/vnd.android.package-archive",
        as_attachment=True,
        download_name=filename
    )


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })


@app.route("/update", methods=["POST"])
async def trigger_update():
    """Ручной запуск проверки обновлений."""
    # Простая авторизация по токену
    token = request.headers.get("X-Auth-Token")
    if token != TELEGRAM_BOT_TOKEN:
        abort(403)
    
    # Запускаем обновление (в фоне)
    asyncio.create_task(update_all_apps())
    
    return jsonify({"status": "started"})


# =============================================================================
# FLASK API - FILES
# =============================================================================

@app.route("/files")
@limiter.limit("60 per minute")
def list_files():
    """Список всех загруженных файлов."""
    try:
        data = load_files()
        return jsonify(data)
    except Exception as e:
        log(f"Error loading files: {e}")
        abort(500)


@app.route("/files/<filename>")
@limiter.limit("30 per minute")
def download_file(filename):
    """Скачать произвольный файл."""
    # Валидация имени файла
    if ".." in filename or "/" in filename or "\\" in filename:
        abort(403)

    filepath = FILES_DIR / filename
    if not filepath.exists():
        abort(404)

    # Определяем MIME-тип
    mimetype = "application/octet-stream"
    if filename.lower().endswith(".html") or filename.lower().endswith(".htm"):
        mimetype = "text/html"
    elif filename.lower().endswith(".css"):
        mimetype = "text/css"
    elif filename.lower().endswith(".js"):
        mimetype = "application/javascript"
    elif filename.lower().endswith(".json"):
        mimetype = "application/json"
    elif filename.lower().endswith(".png"):
        mimetype = "image/png"
    elif filename.lower().endswith(".jpg") or filename.lower().endswith(".jpeg"):
        mimetype = "image/jpeg"
    elif filename.lower().endswith(".gif"):
        mimetype = "image/gif"
    elif filename.lower().endswith(".svg"):
        mimetype = "image/svg+xml"
    elif filename.lower().endswith(".txt"):
        mimetype = "text/plain"
    elif filename.lower().endswith(".xml"):
        mimetype = "application/xml"
    elif filename.lower().endswith(".pdf"):
        mimetype = "application/pdf"

    return send_file(
        str(filepath),
        mimetype=mimetype,
        as_attachment=False,
        download_name=filename
    )


# =============================================================================
# TELEGRAM БОТ
# =============================================================================

async def delete_menu_messages(update: Update, context: ContextTypes.DEFAULT_TYPE, keep_menu: bool = False):
    """Удалить текущее сообщение пользователя и предыдущий меню-сообщение.
    Если keep_menu=True, сохраняем текущее меню и не удаляем menu_message_id."""
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass

    if not keep_menu:
        prev_id = context.user_data.get("menu_message_id")
        if prev_id:
            try:
                await update.message.chat.delete_message(prev_id)
            except Exception:
                pass
            context.user_data.pop("menu_message_id", None)


def get_main_keyboard():
    """Создать основную клавиатуру с разделами."""
    keyboard = [
        [KeyboardButton("📱 Приложения"), KeyboardButton("📁 Файлы")],
        [KeyboardButton("📊 Статус")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_apps_keyboard():
    keyboard = [
        [KeyboardButton("📋 Список приложений"), KeyboardButton("🔄 Проверить все")],
        [KeyboardButton("⚙️ Обновить приложение"), KeyboardButton("➕ Добавить приложение")],
        [KeyboardButton("🗑️ Удалить приложение"), KeyboardButton("⬅️ Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_files_keyboard():
    keyboard = [
        [KeyboardButton("📂 Список файлов"), KeyboardButton("⬆️ Загрузить файл")],
        [KeyboardButton("🗑️ Удалить файл"), KeyboardButton("⬅️ Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_menu_keyboard(context: ContextTypes.DEFAULT_TYPE):
    current_menu = context.user_data.get("current_menu")
    if current_menu == "apps":
        return get_apps_keyboard()
    if current_menu == "files":
        return get_files_keyboard()
    return get_main_keyboard()


def clear_flow_state(context: ContextTypes.DEFAULT_TYPE):
    preserved_keys = {"current_menu", "return_menu", "menu_message_id"}
    preserved = {k: context.user_data[k] for k in preserved_keys if k in context.user_data}
    context.user_data.clear()
    context.user_data.update(preserved)


async def restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu = context.user_data.pop("return_menu", None)
    if menu == "apps":
        await apps_menu_command(update, context)
    elif menu == "files":
        await files_menu_command(update, context)
    else:
        await start_command(update, context)


async def apps_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню приложений."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    clear_flow_state(context)
    await delete_menu_messages(update, context)
    context.user_data["current_menu"] = "apps"
    msg = await update.message.reply_text(
        "📱 <b>Меню приложений</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=get_apps_keyboard()
    )
    context.user_data["menu_message_id"] = msg.message_id


async def files_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню файлов."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    clear_flow_state(context)
    await delete_menu_messages(update, context)
    context.user_data["current_menu"] = "files"
    msg = await update.message.reply_text(
        "📁 <b>Меню файлов</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=get_files_keyboard()
    )
    context.user_data["menu_message_id"] = msg.message_id


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return
    
    clear_flow_state(context)
    await delete_menu_messages(update, context)
    context.user_data["current_menu"] = "main"
    msg = await update.message.reply_text(
        "👋 Привет! Выберите раздел меню:\n"
        "📱 Приложения — управление приложениями и обновлениями.\n"
        "📁 Файлы — управление загруженными файлами.\n"
        "📊 Статус — информация о сервере.",
        reply_markup=get_main_keyboard()
    )
    context.user_data["menu_message_id"] = msg.message_id


async def apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /apps - список всех приложений."""
    try:
        clear_flow_state(context)
        await delete_menu_messages(update, context, keep_menu=True)
        context.user_data["current_menu"] = "apps"
        data = load_apps()
        apps = data.get("apps", [])
        
        if not apps:
            await update.message.reply_text("📭 Список приложений пуст.", reply_markup=get_apps_keyboard())
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
        
        await update.message.reply_text(message, parse_mode="HTML", disable_web_page_preview=True, reply_markup=get_apps_keyboard())
        
    except Exception as e:
        log(f"Error in /apps command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status - информация о хосте и сервисах."""
    try:
        clear_flow_state(context)
        await delete_menu_messages(update, context, keep_menu=True)
        context.user_data["current_menu"] = "main"
        # Загрузка CPU
        cpu_usage = "N/A"
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline()
                if line.startswith("cpu "):
                    parts = line.split()[1:5]
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
            result = subprocess.run(["/usr/bin/free", "-m"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("Mem:"):
                        parts = line.split()
                        if len(parts) >= 3:
                            total = int(parts[1])
                            used = int(parts[2])
                            ram_usage = f"{used}/{total} MB ({used*100//total}%)"
                            break
        except Exception as e:
            log(f"free error: {e}")

        # Загрузка SSD
        disk_usage = "N/A"
        try:
            result = subprocess.run(["/usr/bin/df", "-h", "/"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "/" in line and not line.startswith("Filesystem"):
                        parts = line.split()
                        if len(parts) >= 5:
                            disk_usage = f"{parts[2]}/{parts[1]} ({parts[4]})"
                            break
        except Exception as e:
            log(f"df error: {e}")

        # Статус systemd сервиса
        systemd_status = "N/A"
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "is-active", "tinstaller"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                systemd_status = f"✅ {result.stdout.strip()}"
            else:
                systemd_status = f"❌ {result.stdout.strip() or 'inactive'}"
        except Exception as e:
            log(f"systemctl error: {e}")

        # Ссылка на apps.json
        apps_json_url = os.environ.get("APPS_JSON_URL", f"http://{SERVER_DOMAIN}/apps.json" if SERVER_DOMAIN else "#")

        message = (
            "🖥️ <b>Статус хоста:</b>\n\n"
            f"📊 CPU: {cpu_usage}\n"
            f"💾 RAM: {ram_usage}\n"
            f"📁 SSD: {disk_usage}\n"
            f"🔧 systemd: {systemd_status}\n\n"
            f"📄 <a href=\"{apps_json_url}\">apps.json</a>"
        )

        log(f"/status executed: CPU={cpu_usage}, RAM={ram_usage}, SSD={disk_usage}, systemd={systemd_status}")
        await update.message.reply_text(message, parse_mode="HTML", disable_web_page_preview=True, reply_markup=get_main_keyboard())

    except Exception as e:
        log(f"Error in /status command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def updateall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /updateall - обновление всех приложений из внешних источников."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    await update.message.reply_text("🔄 Запуск обновления всех приложений...\n⏳ Это может занять несколько минут.")
    await update_all_apps()
    await update.message.reply_text("✅ Обновление завершено.")


# =============================================================================
# КОМАНДА /FILES - СПИСОК ФАЙЛОВ
# =============================================================================

async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /files - список всех загруженных файлов."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    try:
        clear_flow_state(context)
        await delete_menu_messages(update, context, keep_menu=True)
        context.user_data["current_menu"] = "files"
        data = load_files()
        files = data.get("files", [])

        if not files:
            await update.message.reply_text(
                "📁 <b>Список файлов пуст</b>\n\n"
                "Используйте /upload для загрузки файла.",
                parse_mode="HTML",
                reply_markup=get_files_keyboard()
            )
            return

        message = "📁 <b>Загруженные файлы:</b>\n\n"

        for i, file in enumerate(files, 1):
            filename = file.get("filename", "Unknown")
            original_name = file.get("original_name", filename)
            size = file.get("size", 0)
            uploaded = file.get("uploaded", "Unknown")
            url = file.get("url", "")

            size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"

            message += f"<b>{i}. {original_name}</b>\n"
            message += f"   📄 Файл: <code>{filename}</code>\n"
            message += f"   📦 Размер: {size_str}\n"
            message += f"   📅 Загружен: {uploaded}\n"
            if url:
                message += f"   🔗 <a href=\"{url}\">Ссылка</a>\n"
            message += "\n"

        await update.message.reply_text(message, parse_mode="HTML", disable_web_page_preview=True, reply_markup=get_files_keyboard())

    except Exception as e:
        log(f"Error in /files command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


# =============================================================================
# КОМАНДА /UPLOAD - ЗАГРУЗКА ФАЙЛА
# =============================================================================

async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /upload - начало загрузки файла."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    await update.message.reply_text(
        "📤 <b>Загрузка файла</b>\n\n"
        "Отправьте файл или прямую ссылку на него.\n"
        "После загрузки можно будет переименовать файл.\n"
        "Для отмены напишите /cancel\n"
        "⏳ Если файл большой, загрузка может занять время.",
        parse_mode="HTML"
    )
    context.user_data["return_menu"] = context.user_data.get("current_menu", "main")
    context.user_data["upload_step"] = 1
    context.user_data["upload_data"] = {}


async def upload_handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода для мастера загрузки файла."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    if "upload_step" not in context.user_data:
        return

    step = context.user_data["upload_step"]
    data = context.user_data.get("upload_data", {})

    # Шаг 1: Получение файла или ссылки
    if step == 1:
        # Проверяем, есть ли документ
        if update.message.document:
            document = update.message.document
            file_name = document.file_name
            file_size = document.file_size

            # Ограничение на размер (опционально, можно убрать)
            if file_size and file_size > 100 * 1024 * 1024:
                await update.message.reply_text(
                    f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f}MB).\n"
                    "Максимальный размер: 100MB."
                )
                return

            temp_dir = tempfile.mkdtemp()
            temp_file_path = os.path.join(temp_dir, file_name)

            try:
                file = await context.bot.get_file(document.file_id)
                file_url = file.file_path

                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    async with client.stream("GET", file_url) as response:
                        response.raise_for_status()
                        with open(temp_apk_path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                f.write(chunk)

                data["temp_file_path"] = temp_file_path
                data["original_name"] = file_name
                data["size"] = os.path.getsize(temp_file_path)

                context.user_data["upload_data"] = data
                context.user_data["upload_step"] = 2

                await update.message.reply_text(
                    f"✅ Файл получен.\n"
                    f"📦 Размер: {data['size'] / 1024 / 1024:.1f} MB\n"
                    f"📄 Исходное имя: {file_name}\n\n"
                    f"Хотите переименовать файл? Отправьте новое имя или напишите 'нет' чтобы оставить как есть.\n"
                    f"Для отмены напишите /cancel",
                    parse_mode="HTML"
                )
                return

            except Exception as e:
                log(f"Error downloading file from Telegram: {e}")
                await update.message.reply_text(f"❌ Ошибка загрузки файла: {e}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                clear_flow_state(context)
                await restore_menu(update, context)
                return

        # Проверяем, есть ли текст (ссылка)
        if update.message.text:
            url = update.message.text.strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                await update.message.reply_text("❌ Это не похоже на URL. Отправьте корректную ссылку или файл.")
                return

            data["temp_url"] = url
            context.user_data["upload_data"] = data
            context.user_data["upload_step"] = 1.5  # Промежуточный шаг для скачивания

            await update.message.reply_text("⏳ Скачивание файла по ссылке...")

            try:
                temp_dir = tempfile.mkdtemp()
                # Пытаемся получить имя файла из URL
                filename = url.rsplit("/", 1)[-1]
                if not filename or "." not in filename:
                    filename = "downloaded_file"
                temp_file_path = os.path.join(temp_dir, filename)

                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        with open(temp_file_path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                f.write(chunk)

                # Проверяем, что файл скачался
                if not os.path.exists(temp_file_path) or os.path.getsize(temp_file_path) == 0:
                    await update.message.reply_text("❌ Файл не скачался или пустой.")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    clear_flow_state(context)
                    await restore_menu(update, context)
                    return

                data["temp_file_path"] = temp_file_path
                data["original_name"] = filename
                data["size"] = os.path.getsize(temp_file_path)

                context.user_data["upload_data"] = data
                context.user_data["upload_step"] = 2

                await update.message.reply_text(
                    f"✅ Файл скачан.\n"
                    f"📦 Размер: {data['size'] / 1024 / 1024:.1f} MB\n"
                    f"📄 Исходное имя: {filename}\n\n"
                    f"Хотите переименовать файл? Отправьте новое имя или напишите 'нет' чтобы оставить как есть.\n"
                    f"Для отмены напишите /cancel",
                    parse_mode="HTML"
                )
                return

            except Exception as e:
                log(f"Error downloading file from URL: {e}")
                await update.message.reply_text(f"❌ Ошибка скачивания файла: {e}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                clear_flow_state(context)
                await restore_menu(update, context)
                return

        await update.message.reply_text("❌ Отправьте файл или прямую ссылку на него.")
        return

    # Шаг 2: Переименование (опционально)
    if step == 2:
        user_input = update.message.text.strip()

        if user_input.lower() in ["нет", "no", "cancel", "отмена"]:
            # Оставляем исходное имя
            filename = data["original_name"]
        else:
            # Используем введенное имя
            filename = user_input

        # Валидация имени файла
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            await update.message.reply_text(
                "❌ Некорректное имя файла. Используйте только латинские буквы, цифры, дефис и подчёркивание.\n"
                "Отправьте новое имя или напишите 'нет' чтобы оставить как есть."
            )
            return

        # Сохраняем файл
        try:
            FILES_DIR.mkdir(parents=True, exist_ok=True)
            dest_path = FILES_DIR / filename

            # Если файл с таким именем уже существует, добавляем суффикс
            if dest_path.exists():
                base, ext = os.path.splitext(filename)
                counter = 1
                while dest_path.exists():
                    filename = f"{base}_{counter}{ext}"
                    dest_path = FILES_DIR / filename
                    counter += 1

            shutil.move(data["temp_file_path"], str(dest_path))
            os.chmod(dest_path, 0o644)

            # Сохраняем в конфиг
            files_data = load_files()
            timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            # Формируем URL
            server_domain = SERVER_DOMAIN or os.environ.get("SERVER_DOMAIN", "")
            if server_domain:
                file_url = f"https://{server_domain}/files/{filename}"
            else:
                file_url = f"/files/{filename}"

            file_entry = {
                "filename": filename,
                "original_name": data["original_name"],
                "size": data["size"],
                "uploaded": timestamp,
                "url": file_url
            }

            files_data["files"].append(file_entry)
            save_files(files_data)

            # Очищаем контекст
            temp_dir = os.path.dirname(data["temp_file_path"])
            shutil.rmtree(temp_dir, ignore_errors=True)
            clear_flow_state(context)

            await update.message.reply_text(
                f"✅ <b>Файл загружен!</b>\n\n"
                f"📄 Имя: {filename}\n"
                f"📦 Размер: {data['size'] / 1024 / 1024:.1f} MB\n"
                f"🔗 Ссылка: <a href=\"{file_url}\">{file_url}</a>",
                parse_mode="HTML"
            )

            log(f"File uploaded: {filename} (original: {data['original_name']}, size: {data['size']})")
            await restore_menu(update, context)
            return

        except Exception as e:
            log(f"Error saving file: {e}")
            await update.message.reply_text(f"❌ Ошибка сохранения файла: {e}")
            temp_dir = os.path.dirname(data.get("temp_file_path", ""))
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            clear_flow_state(context)
            await restore_menu(update, context)
            return


# =============================================================================
# КОМАНДА /DELFILE - УДАЛЕНИЕ ФАЙЛА
# =============================================================================

async def delfile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /delfile - начало удаления файла."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    try:
        data = load_files()
        files = data.get("files", [])

        if not files:
            await update.message.reply_text("📁 Список файлов пуст.")
            return

        # Создаем инлайн-клавиатуру со списком файлов
        keyboard = []
        for i, file in enumerate(files):
            filename = file.get("filename", "Unknown")
            original_name = file.get("original_name", filename)
            keyboard.append([InlineKeyboardButton(f"📄 {original_name}", callback_data=f"delfile_{i}")])

        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="delfile_cancel")])

        await update.message.reply_text(
            "🗑️ <b>Удаление файла</b>\n\n"
            "Выберите файл для удаления:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        log(f"Error in /delfile command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def delfile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора файла для удаления."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.callback_query.answer("❌ Доступ запрещён.")
        return

    query = update.callback_query
    action = query.data

    if action == "delfile_cancel":
        await query.edit_message_text("❌ Удаление файла отменено.")
        return

    if action.startswith("delfile_"):
        try:
            file_idx = int(action.split("_")[1])
            data = load_files()
            files = data.get("files", [])

            if file_idx >= len(files):
                await query.edit_message_text("❌ Файл не найден.")
                return

            file = files[file_idx]
            filename = file.get("filename", "")
            original_name = file.get("original_name", filename)

            # Показываем подтверждение
            keyboard = [
                [InlineKeyboardButton("✅ Да, удалить", callback_data=f"delfile_confirm_{file_idx}")],
                [InlineKeyboardButton("❌ Нет, отмена", callback_data="delfile_cancel_action")]
            ]

            await query.edit_message_text(
                f"🗑️ <b>Подтверждение удаления</b>\n\n"
                f"Файл: <b>{original_name}</b>\n"
                f"Имя: <code>{filename}</code>\n\n"
                f"Вы уверены? Это действие нельзя отменить.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        except ValueError:
            await query.edit_message_text("❌ Ошибка: некорректный индекс файла.")
        except Exception as e:
            log(f"Error in delfile_callback: {e}")
            await query.edit_message_text(f"❌ Ошибка: {e}")


async def delfile_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик подтверждения удаления файла."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.callback_query.answer("❌ Доступ запрещён.")
        return

    query = update.callback_query
    action = query.data

    if action.startswith("delfile_confirm_"):
        try:
            file_idx = int(action.split("_")[2])
            data = load_files()
            files = data.get("files", [])

            if file_idx >= len(files):
                await query.edit_message_text("❌ Файл не найден.")
                return

            file = files[file_idx]
            filename = file.get("filename", "")
            original_name = file.get("original_name", filename)

            # Удаляем файл из файловой системы
            filepath = FILES_DIR / filename
            if filepath.exists():
                os.remove(filepath)

            # Удаляем из конфига
            files.pop(file_idx)
            data["files"] = files
            save_files(data)

            await query.edit_message_text(
                f"✅ <b>Файл удалён!</b>\n\n"
                f"📄 {original_name} ({filename})",
                parse_mode="HTML"
            )

            log(f"File deleted: {filename}")

        except (ValueError, IndexError):
            await query.edit_message_text("❌ Ошибка: файл не найден.")
        except Exception as e:
            log(f"Error deleting file: {e}")
            await query.edit_message_text(f"❌ Ошибка удаления: {e}")


async def delfile_cancel_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик отмены удаления."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.callback_query.answer("❌ Доступ запрещён.")
        return

    await update.callback_query.edit_message_text("❌ Удаление файла отменено.")


# =============================================================================
# КОМАНДА /ADDAPP - ДОБАВЛЕНИЕ НОВОГО ПРИЛОЖЕНИЯ
# =============================================================================

async def addapp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /addapp - начало мастера добавления приложения."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    await update.message.reply_text(
        "📲 <b>Добавление нового приложения</b>\n\n"
        "Шаг 1/6: Отправьте APK файл или прямую ссылку на него.\n"
        "Для отмены напишите /cancel\n"
        "⏳ Если файл большой, загрузка может занять время.",
        parse_mode="HTML"
    )
    context.user_data["return_menu"] = context.user_data.get("current_menu", "main")
    context.user_data["addapp_step"] = 1
    context.user_data["addapp_data"] = {}


async def addapp_handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода для мастера добавления приложения."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    if "addapp_step" not in context.user_data:
        return

    step = context.user_data["addapp_step"]
    data = context.user_data.get("addapp_data", {})

    # Шаг 1: Получение APK или ссылки
    if step == 1:
        # Проверяем, есть ли документ
        if update.message.document:
            document = update.message.document
            file_name = document.file_name

            if not file_name.lower().endswith(".apk"):
                await update.message.reply_text("❌ Это не APK файл. Отправьте APK файл.")
                return

            file_size = document.file_size
            if file_size and file_size > 50 * 1024 * 1024:
                await update.message.reply_text(
                    f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f}MB).\n"
                    "Максимальный размер: 50MB."
                )
                return

            temp_dir = tempfile.mkdtemp()
            temp_apk_path = os.path.join(temp_dir, file_name)

            try:
                file = await context.bot.get_file(document.file_id)
                file_url = file.file_path

                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
                    async with client.stream("GET", file_url) as response:
                        response.raise_for_status()
                        with open(temp_apk_path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                f.write(chunk)

                version = parse_version_from_apk(temp_apk_path)
                build = parse_build_from_apk(temp_apk_path)
                version_display = format_version_display(version, build)
                data["temp_apk_path"] = temp_apk_path
                data["version"] = version
                data["build"] = build
                data["source_method"] = "manual"

                context.user_data["addapp_data"] = data
                context.user_data["addapp_step"] = 2

                await update.message.reply_text(
                    f"✅ Файл получен.\n"
                    f"📦 Версия: {version_display}\n\n"
                    f"Шаг 2/6: Введите название приложения (латиницей, без пробелов и спецсимволов).\n"
                    f"Это название будет использовано для имени файла (например, {version}.apk).\n"
                    f"Для отмены напишите /cancel",
                    parse_mode="HTML"
                )
                return

            except Exception as e:
                log(f"Error downloading file from Telegram: {e}")
                await update.message.reply_text(f"❌ Ошибка загрузки файла: {e}")
                clear_flow_state(context)
                await restore_menu(update, context)
                return

        # Проверяем, есть ли текст (ссылка)
        if update.message.text:
            url = update.message.text.strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                await update.message.reply_text("❌ Это не похоже на URL. Отправьте корректную ссылку или APK файл.")
                return

            github_repo = parse_github_repo_url(url)
            if github_repo:
                releases = await fetch_github_releases(github_repo, per_page=10)
                if not releases:
                    await update.message.reply_text(
                        "❌ Не удалось получить релизы из GitHub. Проверьте ссылку и попробуйте снова."
                    )
                    return

                data["source_method"] = "github"
                data["source_update"] = f"https://github.com/{github_repo}"
                data["source_repo"] = github_repo
                data["is_github_repo"] = True
                data["github_releases"] = releases
                data["github_latest_exists"] = bool(find_github_latest_release(releases))
                context.user_data["addapp_data"] = data
                context.user_data["addapp_step"] = 2

                await update.message.reply_text(
                    f"✅ GitHub репозиторий определен: {github_repo}\n"
                    f"Шаг 2/6: Введите название приложения (латиницей, без пробелов и спецсимволов).\n"
                    f"Это название будет использовано для имени файла.\n"
                    f"Для отмены напишите /cancel",
                    parse_mode="HTML"
                )
                return

            data["temp_url"] = url
            context.user_data["addapp_data"] = data
            context.user_data["addapp_step"] = 1.5  # Промежуточный шаг для скачивания

            await update.message.reply_text("⏳ Скачивание файла по ссылке...")

            try:
                temp_dir = tempfile.mkdtemp()
                temp_apk_path, filename = await download_apk_from_url(url, temp_dir)

                file_size = os.path.getsize(temp_apk_path)
                version = parse_version_from_apk(temp_apk_path)
                build = parse_build_from_apk(temp_apk_path)
                version_display = format_version_display(version, build)
                data["temp_apk_path"] = temp_apk_path
                data["version"] = version
                data["build"] = build
                data["source_method"] = "manual"
                data["temp_url"] = url
                data["source_update"] = url

                context.user_data["addapp_data"] = data
                context.user_data["addapp_step"] = 2

                await update.message.reply_text(
                    f"✅ Файл скачан ({file_size / 1024 / 1024:.1f}MB).\n"
                    f"📦 Версия: {version_display}\n\n"
                    f"Шаг 2/6: Введите название приложения (латиницей, без пробелов и спецсимволов).\n"
                    f"Это название будет использовано для имени файла.\n"
                    f"Для отмены напишите /cancel",
                    parse_mode="HTML"
                )
                return

            except Exception as e:
                log(f"Error downloading file from URL: {e}")
                await update.message.reply_text(f"❌ Ошибка скачивания файла: {e}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                clear_flow_state(context)
                await restore_menu(update, context)
                return

        await update.message.reply_text("❌ Отправьте APK файл или прямую ссылку на него.")
        return

    # Шаг 2: Название приложения
    if step == 2:
        title = update.message.text.strip()
        if not title:
            await update.message.reply_text("❌ Название не может быть пустым.")
            return

        # Проверяем, что название латиницей и без пробелов
        if not re.match(r"^[a-zA-Z0-9_-]+$", title):
            await update.message.reply_text(
                "❌ Название должно содержать только латинские буквы, цифры, дефис и подчёркивание.\n"
                "Введите название ещё раз."
            )
            return

        data["title"] = title
        context.user_data["addapp_data"] = data
        context.user_data["addapp_step"] = 3

        await update.message.reply_text(
            f"✅ Название: {title}\n\n"
            f"Шаг 3/6: Введите описание приложения.\n"
            f"Для отмены напишите /cancel"
        )
        return

    # Шаг 3: Описание
    if step == 3:
        description = update.message.text.strip()
        if not description:
            await update.message.reply_text("❌ Описание не может быть пустым.")
            return

        data["description"] = description
        context.user_data["addapp_data"] = data
        context.user_data["addapp_step"] = 4

        # Получаем существующие категории
        try:
            apps_data = load_apps()
            existing_categories = set()
            for app in apps_data.get("apps", []):
                cat = app.get("category", "Uncategorized")
                existing_categories.add(cat)
        except Exception:
            existing_categories = set()

        # Создаём клавиатуру с категориями
        keyboard = []
        row = []
        for cat in sorted(existing_categories):
            row.append(KeyboardButton(cat))
            if len(row) >= 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([KeyboardButton("Новая категория")])

        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text(
            f"✅ Описание сохранено.\n\n"
            f"Шаг 4/6: Выберите категорию или введите новую.\n"
            f"Для отмены напишите /cancel",
            reply_markup=reply_markup
        )
        return

    # Шаг 4: Категория
    if step == 4:
        category = update.message.text.strip()
        if not category:
            await update.message.reply_text("❌ Категория не может быть пустой.")
            return

        normalized_category = category.lower()
        if normalized_category in ["➕ новая категория", "новая категория"]:
            await update.message.reply_text(
                "Введите название новой категории:",
                reply_markup=ReplyKeyboardRemove()
            )
            context.user_data["addapp_step"] = 4.1
            return

        data["category"] = category
        context.user_data["addapp_data"] = data
        context.user_data["addapp_step"] = 5

        if data.get("is_github_repo"):
            keyboard = [
                [KeyboardButton("manual"), KeyboardButton("github")],
            ]
            update_text = (
                f"Шаг 5/6: Выберите способ обновления:\n"
                f"• <b>manual</b> — скачать выбранную версию из GitHub и больше не обновлять\n"
                f"• <b>github</b> — автопроверка GitHub Releases по latest\n"
            )
        else:
            keyboard = [
                [KeyboardButton("manual"), KeyboardButton("direct")],
            ]
            update_text = (
                f"Шаг 5/6: Выберите способ обновления:\n"
                f"• <b>manual</b> — нет внешнего источника, обновления только через бота\n"
                f"• <b>direct</b> — прямая ссылка на APK (укажите на следующем шаге)\n"
            )

        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text(
            f"✅ Категория: {category}\n\n"
            + update_text
            + "Для отмены напишите /cancel",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return

    # Шаг 4.1: Ввод новой категории
    if step == 4.1:
        category = update.message.text.strip()
        if not category:
            await update.message.reply_text("❌ Категория не может быть пустой.")
            return

        data["category"] = category
        context.user_data["addapp_data"] = data
        context.user_data["addapp_step"] = 5

        if data.get("is_github_repo"):
            keyboard = [
                [KeyboardButton("manual"), KeyboardButton("github")],
            ]
            update_text = (
                f"Шаг 5/6: Выберите способ обновления:\n"
                f"• <b>manual</b> — скачать выбранную версию из GitHub и больше не обновлять\n"
                f"• <b>github</b> — автопроверка GitHub Releases по latest\n"
            )
        else:
            keyboard = [
                [KeyboardButton("manual"), KeyboardButton("direct")],
            ]
            update_text = (
                f"Шаг 5/6: Выберите способ обновления:\n"
                f"• <b>manual</b> — нет внешнего источника, обновления только через бота\n"
                f"• <b>direct</b> — прямая ссылка на APK (укажите на следующем шаге)\n"
            )

        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text(
            f"✅ Категория: {category}\n\n"
            + update_text
            + "Для отмены напишите /cancel",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return

    # Шаг 5: Выбор метода обновления
    if step == 5:
        method = update.message.text.strip().lower()
        if data.get("is_github_repo"):
            if method not in ["manual", "github"]:
                await update.message.reply_text("❌ Выберите manual или github.")
                return

            data["source_method"] = method
            context.user_data["addapp_data"] = data

            if method == "github":
                await finalize_addapp_github(update, context, data)
                return

            releases = data.get("github_releases") or await fetch_github_releases(data.get("source_repo", ""), per_page=10)
            if not releases:
                await update.message.reply_text("❌ Не удалось получить релизы из GitHub. Проверьте ссылку и попробуйте снова.")
                clear_flow_state(context)
                await restore_menu(update, context)
                return

            latest_release = find_github_latest_release(releases)
            if latest_release:
                prereleases = [
                    r for r in releases
                    if r.get("prerelease") and not r.get("draft", False)
                ]
                version_list = [latest_release] + prereleases[:5] if prereleases else [latest_release]
            else:
                valid = [r for r in releases if not r.get("draft", False)]
                version_list = sorted(
                    valid,
                    key=lambda r: parse_github_datetime(r.get("published_at") or r.get("created_at")),
                    reverse=True,
                )[:5]

            if not version_list:
                await update.message.reply_text("❌ Не найдено доступных релизов для загрузки.")
                clear_flow_state(context)
                await restore_menu(update, context)
                return

            context.user_data["addapp_versions"] = version_list
            context.user_data["addapp_step"] = 6

            text = "Выберите версию для загрузки:\n"
            for idx, release in enumerate(version_list, start=1):
                tag = release.get("tag_name", "unknown")
                status = "pre-release" if release.get("prerelease") else "release"
                text += f"{idx}. {tag} ({status})\n"

            await update.message.reply_text(
                "✅ Метод: manual\n\n"
                + text
                + "\nОтправьте номер версии из списка.",
                reply_markup=ReplyKeyboardRemove()
            )
            return

        if method not in ["manual", "direct"]:
            await update.message.reply_text("❌ Выберите manual или direct.")
            return

        data["source_method"] = method
        context.user_data["addapp_data"] = data

        if method == "manual":
            await finalize_addapp(update, context, data)
        else:
            if data.get("temp_url"):
                data["source_update"] = data.get("temp_url")
                context.user_data["addapp_data"] = data
                await update.message.reply_text(
                    f"✅ Метод: direct\n\n"
                    f"Используется ссылка, указанная ранее.\n"
                    f"Приложение будет добавлено с автопроверкой обновлений по этой ссылке.",
                    reply_markup=ReplyKeyboardRemove()
                )
                await finalize_addapp(update, context, data)
            else:
                context.user_data["addapp_step"] = 6
                await update.message.reply_text(
                    f"✅ Метод: direct\n\n"
                    f"Шаг 6/6: Отправьте прямую ссылку на APK файл для обновлений.\n"
                    f"Для отмены напишите /cancel",
                    reply_markup=ReplyKeyboardRemove()
                )
        return

    # Шаг 6: Ссылка для direct или выбор GitHub релиза
    if step == 6:
        if data.get("is_github_repo") and data.get("source_method") == "manual" and context.user_data.get("addapp_versions"):
            choice = update.message.text.strip()
            releases = context.user_data.get("addapp_versions", [])
            selected_release = None

            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(releases):
                    selected_release = releases[index]
            else:
                for release in releases:
                    if release.get("tag_name", "").strip().lower() == choice.lower():
                        selected_release = release
                        break

            if not selected_release:
                await update.message.reply_text("❌ Неверный выбор. Отправьте номер версии из списка.")
                return

            await finalize_addapp_github_manual(update, context, data, selected_release)
            return

        url = update.message.text.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            await update.message.reply_text("❌ Это не похоже на URL. Отправьте корректную ссылку.")
            return

        data["source_update"] = url
        context.user_data["addapp_data"] = data

        await finalize_addapp(update, context, data)
        return


async def download_github_asset_to_file(url: str, target_path: str) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(target_path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)


async def build_github_app_entries(
    data: dict,
    release: dict,
    is_auto: bool,
) -> list[dict]:
    title_base = data["title"]
    description = data.get("description", "")
    category = data.get("category", "Uncategorized")
    repo_url = data.get("source_update")
    apps = []

    assets = release.get("assets", [])
    selected_assets = select_github_assets(assets)
    if not selected_assets:
        raise ValueError("В релизе GitHub не найдены APK assets.")

    APKS_DIR.mkdir(parents=True, exist_ok=True)

    for arch, asset in selected_assets:
        if arch == "universal" and len(selected_assets) == 1:
            filename = f"{title_base}.apk"
            app_title = title_base
        else:
            filename = f"{title_base}_{arch}.apk"
            app_title = filename[:-4]

        temp_dir = tempfile.mkdtemp()
        temp_apk_path = os.path.join(temp_dir, filename)
        await download_github_asset_to_file(asset.get("browser_download_url"), temp_apk_path)

        version = parse_version_from_apk(temp_apk_path)
        build = parse_build_from_apk(temp_apk_path)
        if version == "неизвестно":
            version = release.get("tag_name", "unknown")

        source_filter = make_arch_source_filter(arch, asset.get("name"))
        app_entry = {
            "title": app_title,
            "description": description,
            "url": f"http://{SERVER_DOMAIN}/apks/{filename}" if SERVER_DOMAIN else f"/apks/{filename}",
            "sourceUpdate": repo_url if is_auto else None,
            "sourceMethod": "github" if is_auto else "manual",
            "sourceFilter": source_filter if is_auto else None,
            "category": category,
            "ver": version,
            "build": build,
            "lastUpdated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        target_path = APKS_DIR / filename
        shutil.copy2(temp_apk_path, str(target_path))
        os.chmod(target_path, 0o644)
        shutil.rmtree(temp_dir, ignore_errors=True)

        apps.append(app_entry)

    return apps


async def finalize_addapp_github(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    repo = data.get("source_repo")
    if not repo:
        await update.message.reply_text("❌ Не удалось определить GitHub репозиторий.")
        clear_flow_state(context)
        await restore_menu(update, context)
        return

    release = await fetch_github_release_latest(repo)
    if not release:
        releases = await fetch_github_releases(repo, per_page=10)
        release = choose_github_auto_release(releases)

    if not release:
        await update.message.reply_text("❌ Не удалось получить релиз из GitHub.")
        clear_flow_state(context)
        await restore_menu(update, context)
        return

    try:
        apps = await build_github_app_entries(data, release, is_auto=True)
        apps_data = load_apps()
        apps_data["apps"].extend(apps)
        save_apps(apps_data)

        clear_flow_state(context)

        names = ", ".join([app["title"] for app in apps])
        unique_versions = sorted({app.get("ver", "unknown") for app in apps})
        version_text = unique_versions[0] if len(unique_versions) == 1 else ", ".join(unique_versions)
        categories = sorted({app.get("category", "Uncategorized") for app in apps})
        category = categories[0] if len(categories) == 1 else ", ".join(categories)
        archs = [app["title"].split("_")[-1] if "_" in app["title"] else "universal" for app in apps]
        source_update = data.get("source_update") or data.get("sourceUpdate") or "неизвестно"
        await update.message.reply_text(
            f"✅ Приложения <b>{names}</b> успешно добавлены и настроены на автообновление по GitHub Releases!\n"
            f"📦 Версия: {version_text}\n"
            f"🧩 Архитектуры: {', '.join(archs)}\n"
            f"📁 Категория: {category}\n"
            f"🔄 Метод обновлений: github\n"
            f"🔗 sourceUpdate: {source_update}",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        log(f"Добавлено приложение(я): {names} ver={version_text} archs={','.join(archs)} sourceMethod=github sourceUpdate={source_update}")
        await restore_menu(update, context)
    except Exception as e:
        log(f"Error finalizing GitHub addapp: {e}")
        await update.message.reply_text(f"❌ Ошибка при добавлении из GitHub: {e}")
        clear_flow_state(context)
        await restore_menu(update, context)


async def finalize_addapp_github_manual(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
    release: dict,
):
    try:
        apps = await build_github_app_entries(data, release, is_auto=False)
        apps_data = load_apps()
        apps_data["apps"].extend(apps)
        save_apps(apps_data)

        clear_flow_state(context)

        names = ", ".join([app["title"] for app in apps])
        unique_versions = sorted({app.get("ver", "unknown") for app in apps})
        version_text = unique_versions[0] if len(unique_versions) == 1 else ", ".join(unique_versions)
        categories = sorted({app.get("category", "Uncategorized") for app in apps})
        category = categories[0] if len(categories) == 1 else ", ".join(categories)
        archs = [app["title"].split("_")[-1] if "_" in app["title"] else "universal" for app in apps]
        method = "manual"
        source_update = data.get("source_update") or data.get("sourceUpdate") or "неизвестно"
        await update.message.reply_text(
            f"✅ Приложения <b>{names}</b> успешно добавлены!\n"
            f"📦 Версия: {version_text}\n"
            f"🧩 Архитектуры: {', '.join(archs)}\n"
            f"📁 Категория: {category}\n"
            f"🔄 Метод обновлений: {method}\n"
            f"🔗 sourceUpdate: {source_update}\n"
            f"🔕 Автообновления отключены.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        log(f"Добавлено приложение(я): {names} ver={version_text} archs={','.join(archs)} sourceMethod=manual sourceUpdate={source_update}")
        await restore_menu(update, context)
    except Exception as e:
        log(f"Error finalizing GitHub manual addapp: {e}")
        await update.message.reply_text(f"❌ Ошибка при добавлении из GitHub: {e}")
        clear_flow_state(context)
        await restore_menu(update, context)


async def finalize_addapp(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """Завершение добавления приложения."""
    try:
        title = data["title"]
        version = data["version"]
        temp_apk_path = data["temp_apk_path"]

        build = data.get("build", "")

        # Формируем новую запись
        new_app = {
            "title": title,
            "description": data.get("description", ""),
            "url": f"http://{SERVER_DOMAIN}/apks/{title}.apk" if SERVER_DOMAIN else f"/apks/{title}.apk",
            "sourceUpdate": data.get("source_update") if data.get("source_method") == "direct" else None,
            "sourceMethod": data.get("source_method", "manual"),
            "category": data.get("category", "Uncategorized"),
            "ver": version,
            "build": build,
            "lastUpdated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        }

        # Копируем APK файл
        target_path = APKS_DIR / f"{title}.apk"
        shutil.copy2(temp_apk_path, str(target_path))
        os.chmod(target_path, 0o644)

        # Добавляем в apps.json
        apps_data = load_apps()
        apps_data["apps"].append(new_app)
        save_apps(apps_data)

        # Очищаем временные данные
        temp_dir = os.path.dirname(temp_apk_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        clear_flow_state(context)

        version_display = format_version_display(version, build)
        await update.message.reply_text(
            f"✅ Приложение <b>{title}</b> успешно добавлено!\n\n"
            f"📦 Версия: {version_display}\n"
            f"📁 Категория: {new_app['category']}\n"
            f"🔄 Метод обновлений: {new_app['sourceMethod']}",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )

        log(f"Добавлено приложение: {title} v{version}")
        await restore_menu(update, context)

    except Exception as e:
        log(f"Error finalizing addapp: {e}")
        await update.message.reply_text(f"❌ Ошибка при добавлении: {e}")
        clear_flow_state(context)
        await restore_menu(update, context)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /cancel - отмена текущей операции."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    if "upload_step" in context.user_data:
        # Очищаем временные файлы
        data = context.user_data.get("upload_data", {})
        if "temp_file_path" in data:
            temp_dir = os.path.dirname(data["temp_file_path"])
            shutil.rmtree(temp_dir, ignore_errors=True)
        clear_flow_state(context)
        await update.message.reply_text("❌ Загрузка файла отменена.", reply_markup=ReplyKeyboardRemove())
        await restore_menu(update, context)
    elif "addapp_step" in context.user_data:
        # Очищаем временные файлы
        data = context.user_data.get("addapp_data", {})
        if "temp_apk_path" in data:
            temp_dir = os.path.dirname(data["temp_apk_path"])
            shutil.rmtree(temp_dir, ignore_errors=True)
        clear_flow_state(context)
        await update.message.reply_text("❌ Операция отменена.", reply_markup=ReplyKeyboardRemove())
        await restore_menu(update, context)
    elif "removeapp_step" in context.user_data:
        clear_flow_state(context)
        await update.message.reply_text("❌ Операция отменена.", reply_markup=ReplyKeyboardRemove())
        await restore_menu(update, context)
    elif "updateapp_step" in context.user_data:
        data = context.user_data.get("updateapp_data", {})
        if "temp_apk_path" in data:
            temp_dir = os.path.dirname(data["temp_apk_path"])
            shutil.rmtree(temp_dir, ignore_errors=True)
        clear_flow_state(context)
        await update.message.reply_text("❌ Операция отменена.", reply_markup=ReplyKeyboardRemove())
        await restore_menu(update, context)
    else:
        await update.message.reply_text("Нет активной операции для отмены.")


# =============================================================================
# КОМАНДА /REMOVEAPP - УДАЛЕНИЕ ПРИЛОЖЕНИЯ
# =============================================================================

async def removeapp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /removeapp - начало процесса удаления приложения."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    try:
        data = load_apps()
        apps = data.get("apps", [])

        if not apps:
            await update.message.reply_text("📭 Список приложений пуст.")
            return

        # Создаём клавиатуру с приложениями
        keyboard = []
        row = []
        for i, app in enumerate(apps):
            title = app.get("title", f"App {i}")
            row.append(KeyboardButton(title))
            if len(row) >= 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([KeyboardButton("❌ Отмена")])

        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text(
            "🗑️ <b>Удаление приложения</b>\n\n"
            "Выберите приложение для удаления:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

        context.user_data["return_menu"] = context.user_data.get("current_menu", "main")
        context.user_data["removeapp_step"] = 1

    except Exception as e:
        log(f"Error in /removeapp command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def removeapp_handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода для мастера удаления приложения."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    if "removeapp_step" not in context.user_data:
        return

    step = context.user_data["removeapp_step"]
    text = update.message.text.strip()

    # Шаг 1: Выбор приложения
    if step == 1:
        if text == "❌ Отмена":
            clear_flow_state(context)
            await update.message.reply_text(
                "❌ Операция отменена.",
                reply_markup=ReplyKeyboardRemove()
            )
            await restore_menu(update, context)
            return

        try:
            data = load_apps()
            apps = data.get("apps", [])

            # Ищем приложение по названию
            selected_app = None
            app_idx = None
            for i, app in enumerate(apps):
                if app.get("title", "") == text:
                    selected_app = app
                    app_idx = i
                    break

            if selected_app is None:
                await update.message.reply_text("❌ Приложение не найдено. Выберите из списка.")
                return

            # Сохраняем индекс для удаления
            context.user_data["removeapp_app_idx"] = app_idx
            context.user_data["removeapp_step"] = 2

            # Создаём клавиатуру подтверждения
            keyboard = [
                [KeyboardButton("✅ Удалить"), KeyboardButton("❌ Отмена")],
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

            await update.message.reply_text(
                f"🗑️ Вы уверены, что хотите удалить приложение?\n\n"
                f"<b>{selected_app.get('title', 'Unknown')}</b>\n"
                f"📦 Версия: {selected_app.get('ver', '?')}\n"
                f"📁 Категория: {selected_app.get('category', '?')}",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return

        except Exception as e:
            log(f"Error finding app: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
            clear_flow_state(context)
            await restore_menu(update, context)
            return

    # Шаг 2: Подтверждение удаления
    if step == 2:
        if text == "❌ Отмена":
            clear_flow_state(context)
            await update.message.reply_text(
                "❌ Удаление отменено.",
                reply_markup=ReplyKeyboardRemove()
            )
            await restore_menu(update, context)
            return

        if text != "✅ Удалить":
            await update.message.reply_text("❌ Выберите ✅ Удалить или ❌ Отмена.")
            return

        try:
            app_idx = context.user_data.get("removeapp_app_idx")
            if app_idx is None:
                await update.message.reply_text("❌ Ошибка: приложение не выбрано.")
                clear_flow_state(context)
                await restore_menu(update, context)
                return

            data = load_apps()
            apps = data.get("apps", [])

            if app_idx >= len(apps):
                await update.message.reply_text("❌ Ошибка: приложение не найдено.")
                clear_flow_state(context)
                await restore_menu(update, context)
                return

            app = apps[app_idx]
            title = app.get("title", "Unknown")

            # Удаляем APK файл
            target_filename = get_target_filename(app)
            target_path = APKS_DIR / target_filename
            if target_path.exists():
                os.remove(target_path)
                log(f"Удалён файл: {target_path}")

            # Удаляем запись из apps.json
            apps.pop(app_idx)
            save_apps(data)

            clear_flow_state(context)

            await update.message.reply_text(
                f"✅ Приложение <b>{title}</b> успешно удалено!",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardRemove()
            )

            log(f"Удалено приложение: {title}")
            await restore_menu(update, context)

        except Exception as e:
            log(f"Error removing app: {e}")
            await update.message.reply_text(f"❌ Ошибка при удалении: {e}")
            clear_flow_state(context)
            await restore_menu(update, context)
            return


# =============================================================================
# КОМАНДА /UPDATEAPP - ОБНОВЛЕНИЕ КОНКРЕТНОГО ПРИЛОЖЕНИЯ
# =============================================================================

async def updateapp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /updateapp - начало процесса обновления приложения."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    try:
        data = load_apps()
        apps = data.get("apps", [])

        if not apps:
            await update.message.reply_text("📭 Список приложений пуст.")
            return

        # Создаём клавиатуру с приложениями
        keyboard = []
        row = []
        for i, app in enumerate(apps):
            title = app.get("title", f"App {i}")
            row.append(KeyboardButton(title))
            if len(row) >= 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([KeyboardButton("❌ Отмена")])

        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text(
            "🔄 <b>Обновление приложения</b>\n\n"
            "Выберите приложение для обновления или отправьте APK файл / ссылку.\n"
            "Для отмены напишите /cancel",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

        context.user_data["return_menu"] = context.user_data.get("current_menu", "main")
        context.user_data["updateapp_step"] = 1
        context.user_data["updateapp_data"] = {}

    except Exception as e:
        log(f"Error in /updateapp command: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def updateapp_handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода для мастера обновления приложения."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    if "updateapp_step" not in context.user_data:
        return

    step = context.user_data["updateapp_step"]
    data = context.user_data.get("updateapp_data", {})

    # Шаг 1: Выбор приложения
    if step == 1:
        # Проверяем, есть ли документ (APK)
        if update.message.document:
            document = update.message.document
            file_name = document.file_name

            if not file_name.lower().endswith(".apk"):
                await update.message.reply_text("❌ Это не APK файл. Отправьте APK файл.")
                return

            file_size = document.file_size
            if file_size and file_size > 50 * 1024 * 1024:
                await update.message.reply_text(
                    f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f}MB).\n"
                    "Максимальный размер: 50MB."
                )
                return

            # Сохраняем файл и ищем приложение
            temp_dir = tempfile.mkdtemp()
            temp_apk_path = os.path.join(temp_dir, file_name)

            try:
                file = await context.bot.get_file(document.file_id)
                file_url = file.file_path

                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
                    async with client.stream("GET", file_url) as response:
                        response.raise_for_status()
                        with open(temp_apk_path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                f.write(chunk)

                version = parse_version_from_apk(temp_apk_path)
                build = parse_build_from_apk(temp_apk_path)
                version_display = format_version_display(version, build)
                data["temp_apk_path"] = temp_apk_path
                data["version"] = version
                data["build"] = build
                data["file_name"] = file_name

                context.user_data["updateapp_data"] = data

                # Ищем приложение по имени файла
                matches = find_app_by_filename(file_name, apps)

                if len(matches) == 1:
                    app_idx = matches[0]
                    await process_updateapp_file(update, context, app_idx, temp_apk_path, version, build)
                elif len(matches) > 1:
                    # Несколько совпадений - показываем выбор
                    keyboard = []
                    for i in matches:
                        app = apps[i]
                        keyboard.append([KeyboardButton(app.get("title", f"App {i}"))])
                    keyboard.append([KeyboardButton("❌ Отмена")])
                    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

                    await update.message.reply_text(
                        f"📁 Файл: {file_name}\n"
                        f"📦 Версия: {version_display}\n\n"
                        f"🔍 Найдено совпадений: {len(matches)}\n"
                        "Выберите приложение для обновления:",
                        reply_markup=reply_markup
                    )
                    context.user_data["updateapp_matches"] = matches
                    context.user_data["updateapp_step"] = 1.5
                else:
                    # Нет совпадений - показываем все приложения
                    keyboard = []
                    row = []
                    for i, app in enumerate(apps):
                        title = app.get("title", f"App {i}")
                        row.append(KeyboardButton(title))
                        if len(row) >= 2:
                            keyboard.append(row)
                            row = []
                    if row:
                        keyboard.append(row)
                    keyboard.append([KeyboardButton("❌ Отмена")])
                    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

                    await update.message.reply_text(
                        f"📁 Файл: {file_name}\n"
                        f"📦 Версия: {version_display}\n\n"
                        "⚠️ Не найдено совпадений в списке приложений.\n"
                        "Выберите приложение для обновления:",
                        reply_markup=reply_markup
                    )
                    context.user_data["updateapp_all_apps"] = True
                    context.user_data["updateapp_step"] = 1.5

                return

            except Exception as e:
                log(f"Error downloading file from Telegram: {e}")
                await update.message.reply_text(f"❌ Ошибка загрузки файла: {e}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                clear_flow_state(context)
                await restore_menu(update, context)
                return

            # Если это название приложения
            text = update.message.text.strip()

            if text == "❌ Отмена":
                data = context.user_data.get("updateapp_data", {})
                if "temp_apk_path" in data:
                    temp_dir = os.path.dirname(data["temp_apk_path"])
                    shutil.rmtree(temp_dir, ignore_errors=True)
                clear_flow_state(context)
                await update.message.reply_text(
                    "❌ Операция отменена.",
                    reply_markup=ReplyKeyboardRemove()
                )
                await restore_menu(update, context)
                return

            # Если это ссылка
            if text.startswith("http://") or text.startswith("https://"):
                data["temp_url"] = text
                context.user_data["updateapp_data"] = data
                context.user_data["updateapp_step"] = 1.2

                await update.message.reply_text("⏳ Скачивание файла по ссылке...")

                try:
                    temp_dir = tempfile.mkdtemp()
                    temp_apk_path, filename = await download_apk_from_url(text, temp_dir)

                    version = parse_version_from_apk(temp_apk_path)
                    data["temp_apk_path"] = temp_apk_path
                    data["version"] = version
                    data["file_name"] = filename

                    context.user_data["updateapp_data"] = data

                    # Ищем приложение по имени файла
                    matches = find_app_by_filename(filename, apps)

                    if len(matches) == 1:
                        app_idx = matches[0]
                        await process_updateapp_file(update, context, app_idx, temp_apk_path, version, build)
                    elif len(matches) > 1:
                        keyboard = []
                        for i in matches:
                            app = apps[i]
                            keyboard.append([KeyboardButton(app.get("title", f"App {i}"))])
                        keyboard.append([KeyboardButton("❌ Отмена")])
                        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

                        await update.message.reply_text(
                            f"📁 Файл: {filename}\n"
                            f"📦 Версия: {version}\n\n"
                            f"🔍 Найдено совпадений: {len(matches)}\n"
                            "Выберите приложение для обновления:",
                            reply_markup=reply_markup
                        )
                        context.user_data["updateapp_matches"] = matches
                        context.user_data["updateapp_step"] = 1.5
                    else:
                        keyboard = []
                        row = []
                        for i, app in enumerate(apps):
                            title = app.get("title", f"App {i}")
                            row.append(KeyboardButton(title))
                            if len(row) >= 2:
                                keyboard.append(row)
                                row = []
                        if row:
                            keyboard.append(row)
                        keyboard.append([KeyboardButton("❌ Отмена")])
                        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

                        await update.message.reply_text(
                            f"📁 Файл: {filename}\n"
                            f"📦 Версия: {version}\n\n"
                            "⚠️ Не найдено совпадений в списке приложений.\n"
                            "Выберите приложение для обновления:",
                            reply_markup=reply_markup
                        )
                        context.user_data["updateapp_all_apps"] = True
                        context.user_data["updateapp_step"] = 1.5

                    return

                except Exception as e:
                    log(f"Error downloading file from URL: {e}")
                    await update.message.reply_text(f"❌ Ошибка скачивания файла: {e}")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    clear_flow_state(context)
                    await restore_menu(update, context)
                    return

            # Если это название приложения
            apps_list = data.get("apps", []) if "apps" in data else load_apps().get("apps", [])
            selected_app = None
            app_idx = None
            for i, app in enumerate(apps_list):
                if app.get("title", "") == text:
                    selected_app = app
                    app_idx = i
                    break

            if selected_app:
                context.user_data["updateapp_app_idx"] = app_idx
                context.user_data["updateapp_step"] = 2

                await update.message.reply_text(
                    f"✅ Выбрано: <b>{selected_app.get('title', 'Unknown')}</b>\n\n"
                    f"📦 Текущая версия: {get_installed_version_display(selected_app)}\n\n"
                    f"Отправьте APK файл или ссылку для обновления.\n"
                    f"Для отмены напишите /cancel",
                    parse_mode="HTML",
                    reply_markup=ReplyKeyboardRemove()
                )
                return

            await update.message.reply_text("❌ Приложение не найдено. Выберите из списка.")
            return

    # Шаг 1.2: Обработка ссылки (завершено скачивание)
    if step == 1.2:
        # Этот шаг обрабатывается в блоке выше
        pass

    # Шаг 1.5: Выбор приложения после загрузки файла
    if step == 1.5:
        text = update.message.text.strip()

        if text == "❌ Отмена":
            data = context.user_data.get("updateapp_data", {})
            if "temp_apk_path" in data:
                temp_dir = os.path.dirname(data["temp_apk_path"])
                shutil.rmtree(temp_dir, ignore_errors=True)
            clear_flow_state(context)
            await update.message.reply_text(
                "❌ Операция отменена.",
                reply_markup=ReplyKeyboardRemove()
            )
            await restore_menu(update, context)
            return

        apps_list = load_apps().get("apps", [])
        selected_app = None
        app_idx = None
        for i, app in enumerate(apps_list):
            if app.get("title", "") == text:
                selected_app = app
                app_idx = i
                break

        if selected_app:
            temp_apk_path = data.get("temp_apk_path")
            version = data.get("version", "неизвестно")
            build = data.get("build", "")
            await process_updateapp_file(update, context, app_idx, temp_apk_path, version, build)
        else:
            await update.message.reply_text("❌ Приложение не найдено. Выберите из списка.")
            return

    # Шаг 2: Получение APK или ссылки для выбранного приложения
    if step == 2:
        # Проверяем, есть ли документ
        if update.message.document:
            document = update.message.document
            file_name = document.file_name

            if not file_name.lower().endswith(".apk"):
                await update.message.reply_text("❌ Это не APK файл. Отправьте APK файл.")
                return

            file_size = document.file_size
            if file_size and file_size > 50 * 1024 * 1024:
                await update.message.reply_text(
                    f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f}MB).\n"
                    "Максимальный размер: 50MB."
                )
                return

            temp_dir = tempfile.mkdtemp()
            temp_apk_path = os.path.join(temp_dir, file_name)

            try:
                file = await context.bot.get_file(document.file_id)
                file_url = file.file_path

                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
                    async with client.stream("GET", file_url) as response:
                        response.raise_for_status()
                        with open(temp_apk_path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                f.write(chunk)

                version = parse_version_from_apk(temp_apk_path)
                build = parse_build_from_apk(temp_apk_path)
                app_idx = context.user_data.get("updateapp_app_idx")
                await process_updateapp_file(update, context, app_idx, temp_apk_path, version, build)
                return

            except Exception as e:
                log(f"Error downloading file: {e}")
                await update.message.reply_text(f"❌ Ошибка загрузки файла: {e}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                clear_flow_state(context)
                await restore_menu(update, context)
                return

        # Проверяем ссылку
        if update.message.text:
            text = update.message.text.strip()
            if text.startswith("http://") or text.startswith("https://"):
                await update.message.reply_text("⏳ Скачивание файла по ссылке...")

                try:
                    temp_dir = tempfile.mkdtemp()
                    temp_apk_path, filename = await download_apk_from_url(text, temp_dir)

                    version = parse_version_from_apk(temp_apk_path)
                    build = parse_build_from_apk(temp_apk_path)
                    app_idx = context.user_data.get("updateapp_app_idx")
                    await process_updateapp_file(update, context, app_idx, temp_apk_path, version, build)
                    return

                except Exception as e:
                    log(f"Error downloading file from URL: {e}")
                    await update.message.reply_text(f"❌ Ошибка скачивания файла: {e}")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    clear_flow_state(context)
                    await restore_menu(update, context)
                    return

            await update.message.reply_text("❌ Отправьте APK файл или прямую ссылку на него.")
            return


async def process_updateapp_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    app_idx: int,
    temp_apk_path: str,
    new_version: str,
    new_build: str,
):
    """Обработка файла для обновления приложения."""
    try:
        data = load_apps()
        apps = data.get("apps", [])

        if app_idx >= len(apps):
            await update.message.reply_text("❌ Неверный индекс приложения.")
            return

        app = apps[app_idx]
        title = app.get("title", "Unknown")
        old_version = get_installed_version(app)
        old_build = get_installed_build(app)
        old_version_display = format_version_display(old_version, old_build)
        new_version_display = format_version_display(new_version, new_build)

        log(f"Обновление приложения: {title}")
        log(f"  Старая версия: {old_version_display}")
        log(f"  Новая версия: {new_version_display}")

        cmp_result = compare_versions_with_build(new_version, new_build, old_version, old_build)

        if cmp_result <= 0:
            if cmp_result == 0:
                msg = (
                    f"📦 {title}\n"
                    f"Версии совпадают: {old_version_display}\n\n"
                    "Перезаписать файл?"
                )
            else:
                msg = (
                    f"📦 {title}\n"
                    f"⚠️ Новая версия ({new_version_display}) < старой ({old_version_display})\n\n"
                    "Продолжить?"
                )

            keyboard = [
                [KeyboardButton("✅ Да"), KeyboardButton("❌ Нет")],
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

            context.user_data["updateapp_confirm"] = True
            context.user_data["updateapp_app_idx"] = app_idx
            context.user_data["updateapp_temp_apk_path"] = temp_apk_path
            context.user_data["updateapp_new_version"] = new_version
            context.user_data["updateapp_new_build"] = new_build
            context.user_data["updateapp_old_version"] = old_version
            context.user_data["updateapp_old_build"] = old_build
            context.user_data["updateapp_step"] = 3

            await update.message.reply_text(msg, reply_markup=reply_markup)
            return

        # Версия новее - обновляем сразу
        await do_updateapp(
            update, context, app_idx, temp_apk_path, new_version, new_build, old_version, old_build
        )

    except Exception as e:
        log(f"Error in process_updateapp_file: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")
        clear_flow_state(context)
        await restore_menu(update, context)


async def updateapp_confirm_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик подтверждения для /updateapp."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    if not context.user_data.get("updateapp_confirm"):
        return

    text = update.message.text.strip()

    if text == "❌ Нет":
        temp_apk_path = context.user_data.get("updateapp_temp_apk_path")
        if temp_apk_path:
            temp_dir = os.path.dirname(temp_apk_path)
            shutil.rmtree(temp_dir, ignore_errors=True)
        clear_flow_state(context)
        await update.message.reply_text(
            "❌ Обновление отменено.",
            reply_markup=ReplyKeyboardRemove()
        )
        await restore_menu(update, context)
        return

    if text == "✅ Да":
        app_idx = context.user_data.get("updateapp_app_idx")
        temp_apk_path = context.user_data.get("updateapp_temp_apk_path")
        new_version = context.user_data.get("updateapp_new_version")
        new_build = context.user_data.get("updateapp_new_build", "")
        old_version = context.user_data.get("updateapp_old_version")
        old_build = context.user_data.get("updateapp_old_build", "")

        if not all([app_idx is not None, temp_apk_path, new_version]):
            await update.message.reply_text("❌ Ошибка: данные не найдены.")
            clear_flow_state(context)
            await restore_menu(update, context)
            return

        await do_updateapp(update, context, app_idx, temp_apk_path, new_version, new_build, old_version, old_build)
        return


async def do_updateapp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    app_idx: int,
    temp_apk_path: str,
    new_version: str,
    new_build: str,
    old_version: str,
    old_build: str,
):
    """Выполнить обновление приложения."""
    try:
        data = load_apps()
        apps = data.get("apps", [])
        app = apps[app_idx]
        title = app.get("title", "Unknown")

        target_filename = get_target_filename(app)
        target_path = APKS_DIR / target_filename

        log(f"Копирование файла: {temp_apk_path} -> {target_path}")

        shutil.copy2(temp_apk_path, str(target_path))
        os.chmod(target_path, 0o644)

        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        data["apps"][app_idx]["ver"] = new_version
        data["apps"][app_idx]["build"] = new_build
        data["apps"][app_idx]["lastUpdated"] = timestamp
        save_apps(data)

        old_version_display = format_version_display(old_version, old_build)
        new_version_display = format_version_display(new_version, new_build)
        log(f"Обновлено: {title} {old_version_display} -> {new_version_display}")

        temp_dir = os.path.dirname(temp_apk_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        clear_flow_state(context)

        await update.message.reply_text(
            f"✅ <b>{title}</b> обновлён!\n"
            f"Версия: {old_version_display} → {new_version_display}",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )

        await send_telegram(f"🔄 Обновлено: {title}\nВерсия: {old_version_display} → {new_version_display}")
        await restore_menu(update, context)

    except Exception as e:
        log(f"Error during updateapp: {e}")
        await update.message.reply_text(f"❌ Ошибка обновления: {e}")
        clear_flow_state(context)
        await restore_menu(update, context)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загруженных документов (APK файлов)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    # Если активен мастер /upload — передаём управление туда
    if "upload_step" in context.user_data:
        await upload_handle_input(update, context)
        return

    # Если активен мастер /addapp — передаём управление туда
    if "addapp_step" in context.user_data:
        await addapp_handle_input(update, context)
        return

    # Если активен мастер /updateapp — передаём управление туда
    if "updateapp_step" in context.user_data:
        await updateapp_handle_input(update, context)
        return

    document = update.message.document
    file_name = document.file_name

    if not file_name.lower().endswith(".apk"):
        await update.message.reply_text("❌ Это не APK файл.")
        return
    
    log(f"Получен файл: {file_name}")
    
    file_size = document.file_size
    if file_size and file_size > 50 * 1024 * 1024:
        await update.message.reply_text(
            f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f}MB).\n"
            "Максимальный размер: 50MB."
        )
        return
    
    temp_dir = tempfile.mkdtemp()
    temp_apk_path = os.path.join(temp_dir, file_name)
    
    try:
        try:
            file = await context.bot.get_file(document.file_id)
            file_url = file.file_path
        except Exception as e:
            if "too big" in str(e).lower():
                await update.message.reply_text(
                    f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f}MB).\n"
                    "Telegram ограничивает загрузку файлов через бота до 20MB."
                )
                shutil.rmtree(temp_dir, ignore_errors=True)
                return
            raise
        
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
            async with client.stream("GET", file_url) as response:
                response.raise_for_status()
                with open(temp_apk_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
        
        new_version = parse_version_from_apk(temp_apk_path)
        log(f"Версия из APK: {new_version}")
        
        data = load_apps()
        apps = data.get("apps", [])
        
        # Ищем приложение по имени файла
        matches = find_app_by_filename(file_name, apps)
        
        if not matches:
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
            context.user_data["temp_apk_path"] = temp_apk_path
            context.user_data["file_name"] = file_name
            context.user_data["new_version"] = new_version
            
        elif len(matches) == 1:
            app_idx = matches[0]
            await process_update(update, context, app_idx, temp_apk_path, file_name, new_version)
        else:
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


def find_app_by_filename(filename: str, apps: list) -> list:
    """Найти приложения, название которых есть в имени файла."""
    name_without_ext = filename.rsplit(".", 1)[0] if "." in filename else filename
    normalized = re.sub(r"[_\-]", " ", name_without_ext).lower()
    
    matches = []
    for i, app in enumerate(apps):
        title = app.get("title", "").lower()
        if title in normalized or normalized in title:
            matches.append(i)
        else:
            title_words = set(title.split())
            name_words = set(normalized.split())
            if title_words & name_words:
                matches.append(i)
    
    return matches


def get_target_filename(app: dict) -> str:
    """Получить целевое имя файла из url приложения."""
    url = app.get("url", "")
    if url:
        return url.rsplit("/", 1)[-1]
    title = app.get("title", "unknown")
    return re.sub(r"[^a-zA-Z0-9._-]", "_", title) + ".apk"


def get_installed_version(app: dict) -> str:
    """
    Получить версию из установленного APK файла.
    Если файл не найден — вернуть версию из apps.json.
    """
    target_filename = get_target_filename(app)
    apk_path = APKS_DIR / target_filename

    if apk_path.exists():
        version = parse_version_from_apk(str(apk_path))
        if version != "неизвестно":
            return version

    # Файл не найден или не удалось извлечь версию — берём из конфига
    return app.get("ver", "неизвестно")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопок."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ Доступ запрещён.")
        return

    data = query.data

    # Обработчик удаления файлов
    if data.startswith("delfile_"):
        if data == "delfile_cancel":
            await delfile_callback(update, context)
        elif data.startswith("delfile_confirm_"):
            await delfile_confirm_callback(update, context)
        elif data == "delfile_cancel_action":
            await delfile_cancel_action_callback(update, context)
        else:
            await delfile_callback(update, context)
        return

    if data == "cancel":
        await query.edit_message_text("❌ Отменено.")
        if "temp_apk_path" in context.user_data:
            temp_dir = os.path.dirname(context.user_data["temp_apk_path"])
            shutil.rmtree(temp_dir, ignore_errors=True)
        clear_flow_state(context)
        await restore_menu(update, context)
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

        data_apps = load_apps()
        apps = data_apps.get("apps", [])
        app = apps[app_idx] if app_idx < len(apps) else {}
        old_version = get_installed_version(app) if app else "?"
        old_build = get_installed_build(app) if app else ""
        new_build = parse_build_from_apk(temp_apk_path)

        await do_update(update, context, app_idx, temp_apk_path, file_name, new_version, new_build, old_version, old_build)


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
    old_version = get_installed_version(app)
    old_build = get_installed_build(app)
    new_build = parse_build_from_apk(temp_apk_path)
    old_version_display = format_version_display(old_version, old_build)
    new_version_display = format_version_display(new_version, new_build)
    title = app.get("title", "Unknown")

    log(f"Обработка обновления: {title}")
    log(f"  Старая версия: {old_version_display}")
    log(f"  Новая версия: {new_version_display}")

    cmp_result = compare_versions_with_build(new_version, new_build, old_version, old_build)

    if cmp_result <= 0:
        if cmp_result == 0:
            msg = (
                f"📦 {title}\n"
                f"Версии совпадают: {old_version_display}\n\n"
                "Перезаписать файл?"
            )
        else:
            msg = (
                f"📦 {title}\n"
                f"⚠️ Новая версия ({new_version_display}) < старой ({old_version_display})\n\n"
                "Продолжить?"
            )

        keyboard = [
            [
                InlineKeyboardButton("✅ Да", callback_data=f"confirm_{app_idx}"),
                InlineKeyboardButton("❌ Нет", callback_data="cancel"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        context.user_data["confirm_app_idx"] = app_idx
        context.user_data["temp_apk_path"] = temp_apk_path
        context.user_data["file_name"] = file_name
        context.user_data["new_version"] = new_version
        context.user_data["new_build"] = new_build
        context.user_data["old_version"] = old_version
        context.user_data["old_build"] = old_build

        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        return

    await do_update(update, context, app_idx, temp_apk_path, file_name, new_version, new_build, old_version, old_build)


async def do_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    app_idx: int,
    temp_apk_path: str,
    file_name: str,
    new_version: str,
    new_build: str,
    old_version: str,
    old_build: str,
):
    """Выполнить обновление приложения."""
    data = load_apps()
    apps = data.get("apps", [])
    app = apps[app_idx]
    title = app.get("title", "Unknown")
    
    try:
        target_filename = get_target_filename(app)
        target_path = APKS_DIR / target_filename
        
        log(f"Копирование файла: {temp_apk_path} -> {target_path}")
        
        shutil.copy2(temp_apk_path, str(target_path))
        os.chmod(target_path, 0o644)
        
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        data["apps"][app_idx]["ver"] = new_version
        data["apps"][app_idx]["build"] = new_build
        data["apps"][app_idx]["lastUpdated"] = timestamp
        save_apps(data)
        
        old_version_display = format_version_display(old_version, old_build)
        new_version_display = format_version_display(new_version, new_build)
        log(f"Обновлено: {title} {old_version_display} -> {new_version_display}")
        
        temp_dir = os.path.dirname(temp_apk_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        clear_flow_state(context)
        
        msg = (
            f"🔄 Обновлено: <b>{title}</b>\n"
            f"Версия: {old_version_display} → {new_version_display}\n"
            f"Дата: {timestamp}"
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                f"✅ <b>{title}</b> обновлён!\n{old_version_display} → {new_version_display}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(msg, parse_mode="HTML")
        
        await send_telegram(f"🔄 Обновлено: {title}\nВерсия: {old_version_display} → {new_version_display}")
        await restore_menu(update, context)
        
    except Exception as e:
        log(f"Error during update: {e}")
        if update.callback_query:
            await update.callback_query.edit_message_text(f"❌ Ошибка обновления: {e}")
        else:
            await update.message.reply_text(f"❌ Ошибка обновления: {e}")
        clear_flow_state(context)
        await restore_menu(update, context)


# =============================================================================
# ЗАПУСК
# =============================================================================

import asyncio
from aiohttp import web

# Глобальные переменные для управления
bot_application = None
scheduler = None


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Универсальный обработчик текстовых сообщений.
    Маршрутизирует ввод в соответствующий мастер в зависимости от активного шага.
    """
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    # Проверяем активные шаги и вызываем соответствующий обработчик
    if "upload_step" in context.user_data:
        return await upload_handle_input(update, context)
    elif "addapp_step" in context.user_data:
        return await addapp_handle_input(update, context)
    elif "removeapp_step" in context.user_data:
        return await removeapp_handle_input(update, context)
    elif "updateapp_step" in context.user_data or context.user_data.get("updateapp_confirm"):
        if context.user_data.get("updateapp_confirm"):
            return await updateapp_confirm_handle(update, context)
        return await updateapp_handle_input(update, context)

    text = update.message.text.strip()

    if text == "📱 Приложения":
        return await apps_menu_command(update, context)
    if text == "📁 Файлы":
        return await files_menu_command(update, context)
    if text == "📊 Статус":
        return await status_command(update, context)

    if text == "📋 Список приложений":
        return await apps_command(update, context)
    if text == "🔄 Проверить все":
        return await updateall_command(update, context)
    if text == "⚙️ Обновить приложение":
        return await updateapp_command(update, context)
    if text == "➕ Добавить приложение":
        return await addapp_command(update, context)
    if text == "🗑️ Удалить приложение":
        return await removeapp_command(update, context)

    if text == "📂 Список файлов":
        return await files_command(update, context)
    if text == "⬆️ Загрузить файл":
        return await upload_command(update, context)
    if text == "🗑️ Удалить файл":
        return await delfile_command(update, context)

    if text == "⬅️ Назад":
        return await start_command(update, context)

    # Если нет активного мастера — игнорируем текст (чтобы не спамить ошибками)


async def run_bot():
    """Запуск Telegram бота."""
    global bot_application

    if not TELEGRAM_BOT_TOKEN:
        log("ERROR: TELEGRAM_BOT_TOKEN not set")
        return

    log("Запуск Telegram бота...")

    try:
        bot_application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("apps", apps_command))
        bot_application.add_handler(CommandHandler("apps_menu", apps_menu_command))
        bot_application.add_handler(CommandHandler("status", status_command))
        bot_application.add_handler(CommandHandler("updateall", updateall_command))
        bot_application.add_handler(CommandHandler("addapp", addapp_command))
        bot_application.add_handler(CommandHandler("removeapp", removeapp_command))
        bot_application.add_handler(CommandHandler("updateapp", updateapp_command))
        bot_application.add_handler(CommandHandler("files", files_command))
        bot_application.add_handler(CommandHandler("files_menu", files_menu_command))
        bot_application.add_handler(CommandHandler("upload", upload_command))
        bot_application.add_handler(CommandHandler("delfile", delfile_command))
        bot_application.add_handler(CommandHandler("cancel", cancel_command))
        bot_application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))
        bot_application.add_handler(CallbackQueryHandler(handle_callback))

        # Запускаем polling через web.AppRunner для интеграции с aiohttp
        runner = web.AppRunner(web.Application())
        await runner.setup()

        await bot_application.initialize()
        await bot_application.start()
        await bot_application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        log("Telegram бот запущен (polling активен)")
    except Exception as e:
        log(f"ERROR при запуске бота: {e}")
        import traceback
        log(traceback.format_exc())
        raise


async def stop_bot():
    """Остановка Telegram бота."""
    global bot_application

    if bot_application:
        try:
            await bot_application.updater.stop()
            await bot_application.stop()
            await bot_application.shutdown()
            log("Telegram бот остановлен")
        except Exception as e:
            log(f"Error stopping bot: {e}")


def run_flask():
    """Запуск Flask API."""
    log("Запуск Flask API на 127.0.0.1:8000...")
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)


async def main():
    """Основная функция запуска."""
    global scheduler

    log("=== Запуск TInstaller ===")

    # Создаем директорию APK
    APKS_DIR.mkdir(parents=True, exist_ok=True)

    # Настраиваем планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        update_all_apps,
        "interval",
        hours=UPDATE_CHECK_INTERVAL_HOURS,
        id="update_apps",
        name="Проверка обновлений приложений"
    )
    scheduler.start()
    log(f"Планировщик запущен (интервал: {UPDATE_CHECK_INTERVAL_HOURS}ч)")

    # Запускаем бота
    bot_task = asyncio.create_task(run_bot())

    # Даем боту время на запуск
    await asyncio.sleep(2)

    # Запускаем Flask в отдельном потоке
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Держим процесс запущенным
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        log("Остановка...")
        scheduler.shutdown()
        await stop_bot()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Остановлено пользователем")
