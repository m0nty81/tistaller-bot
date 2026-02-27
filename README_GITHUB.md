# TInstaller Update Server

Flask API server and Telegram bot for managing and distributing Android TV applications.

## Features

- 🚀 **Flask API** - Serve APK files with rate limiting
- 🤖 **Telegram Bot** - Update apps by sending APK files directly to the bot
- 🔄 **Auto-updater** - Scheduled script to fetch latest versions from external sources
- 📊 **Smart Matching** - Automatically matches APK files to apps by name
- 🔒 **Admin-only Access** - Only authorized users can upload updates

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/tinstaller.git
cd tinstaller

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

1. Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

2. Edit `config/apps.json` to add your applications:
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

### Running the Server

```bash
# Development
python app.py

# Production with Gunicorn
gunicorn -c gunicorn.conf.py app:app
```

### Running the Telegram Bot

```bash
python telegram_bot.py
```

### Auto-updater Script

```bash
# Manual run
bash scripts/update_apps.sh

# Add to crontab (daily at 2:00)
0 2 * * * /path/to/tinstaller/scripts/update_apps.sh
```

## Project Structure

```
tinstaller/
├── app.py              # Flask API server
├── telegram_bot.py     # Telegram bot for updates
├── gunicorn.conf.py    # Gunicorn configuration
├── requirements.txt    # Python dependencies
├── config/
│   └── apps.json       # Applications configuration
├── scripts/
│   └── update_apps.sh  # Auto-update script
├── service/
│   ├── tinstaller.service      # systemd service for API
│   └── tinstaller-bot.service  # systemd service for bot
└── logs/               # Application logs
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | List all applications |
| `GET /apks/<filename>` | Download APK file |
| `GET /health` | Health check |

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot |
| `/apps` | List all applications |
| `/status` | Server status (CPU, RAM, disk, services) |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `ADMIN_ID` | Your Telegram user ID (admin access only) |

## systemd Services

### Install Services

```bash
sudo cp service/tinstaller.service /etc/systemd/system/
sudo cp service/tinstaller-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tinstaller tinstaller-bot
sudo systemctl start tinstaller tinstaller-bot
```

### Manage Services

```bash
# Check status
sudo systemctl status tinstaller
sudo systemctl status tinstaller-bot

# View logs
sudo journalctl -u tinstaller -f
sudo journalctl -u tinstaller-bot -f

# Restart
sudo systemctl restart tinstaller
sudo systemctl restart tinstaller-bot
```

## Requirements

- Python 3.8+
- Ubuntu 20.04+ / Debian 11+
- `aapt` (for APK version extraction)
- `jq` (for JSON parsing in scripts)

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

This project is designed for Android TV application distribution and management.
