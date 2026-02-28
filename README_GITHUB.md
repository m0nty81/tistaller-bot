# TInstaller Update Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Flask API server and Telegram bot for managing and distributing Android TV applications.

## Features

- 🚀 **Flask API** - Serve APK files with rate limiting
- 🤖 **Telegram Bot** - Update apps by sending APK files directly to the bot
- 🔄 **Auto-updater** - Scheduled task to fetch latest versions from external sources
- 📊 **Smart Matching** - Automatically matches APK files to apps by name
- 🔒 **Admin-only Access** - Only authorized users can upload updates
- ⚙️ **Unified Service** - All components in a single systemd service

## Quick Start

### Installation

```bash
cd /opt/web-serv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

1. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
nano .env
```

2. Edit `config/apps.json`:
```json
{
  "apps": [
    {
      "title": "My App",
      "description": "App description",
      "url": "https://yourdomain.com/apks/MyApp.apk",
      "sourceUpdate": "https://github.com/user/repo/releases/latest",
      "sourceMethod": "github_release",
      "sourceFilter": "arm64",
      "category": "Utilities"
    }
  ]
}
```

### Running

#### Development

```bash
python server.py
```

#### Production (systemd)

```bash
sudo cp service/tinstaller.service.example /etc/systemd/system/tinstaller.service
sudo nano /etc/systemd/system/tinstaller.service  # Edit YOUR_USER
sudo systemctl daemon-reload
sudo systemctl enable tinstaller
sudo systemctl start tinstaller
```

## Project Structure

```
tinstaller/
├── server.py             # Unified server: Flask + Telegram bot + scheduler
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variables template
├── config/
│   └── apps.json         # Applications configuration
├── service/
│   └── tinstaller.service.example  # systemd service
├── logs/                 # Application logs
└── apks/                 # APK files
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | List all applications |
| `GET /apks/<filename>` | Download APK file |
| `GET /health` | Health check |
| `POST /update` | Manual update trigger (requires `X-Auth-Token`) |

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot |
| `/apps` | List all applications |
| `/status` | Server status (CPU, RAM, disk) |
| `/update` | Trigger update check |
| `/forceupdate` | Force update from external sources |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID for notifications |
| `ADMIN_ID` | Admin user ID (only admin can upload) |
| `UPDATE_CHECK_INTERVAL_HOURS` | Update check interval in hours (default: 6) |

## systemd Service

### Install

```bash
sudo cp service/tinstaller.service.example /etc/systemd/system/tinstaller.service
sudo systemctl daemon-reload
sudo systemctl enable tinstaller
sudo systemctl start tinstaller
```

### Manage

```bash
# Status
sudo systemctl status tinstaller

# Logs
sudo journalctl -u tinstaller -f

# Restart
sudo systemctl restart tinstaller
```

## Update Methods

### 1. direct - Direct APK URL
```json
{
  "sourceUpdate": "http://example.com/apps/Aerial_Dream.apk",
  "sourceMethod": "direct"
}
```

### 2. github_release - GitHub Releases API
```json
{
  "sourceUpdate": "https://api.github.com/repos/owner/repo/releases/latest",
  "sourceMethod": "github_release",
  "sourceFilter": "arm64"
}
```

### 3. gitlab_release - GitLab Releases API
```json
{
  "sourceUpdate": "https://gitlab.com/api/v4/projects/ID/releases",
  "sourceMethod": "gitlab_release",
  "sourceFilter": "arm64"
}
```

### 4. custom - Custom Command
```json
{
  "sourceUpdate": "curl -s https://api.example.com/releases | jq -r '.download_url'",
  "sourceMethod": "custom"
}
```

## Update Logic

### Automatic Updates (Scheduler)

- Runs every `UPDATE_CHECK_INTERVAL_HOURS` hours
- Downloads APK from external source
- Version comparison:
  - **New < Old** → Skip (no notification)
  - **New = Old** → Skip (no notification, rebuild)
  - **New > Old** → Update + notification 🔄
  - **New file** → Download + notification 🆕

### Manual Updates (Telegram Bot)

- Send APK file to the bot
- Bot matches app by filename
- If version ≤ current → asks for confirmation
- If version > current → updates immediately

## Requirements

- Python 3.10+
- Ubuntu 20.04+ / Debian 11+
- `aapt` (for APK version extraction)

### Install Dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv aapt
```

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

Designed for Android TV application distribution and management.
