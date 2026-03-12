# File Management Feature - Implementation Summary

## Overview
Added functionality to host arbitrary files on the server with static HTTPS links, separate from APK files.

## Changes Made

### 1. Directory Structure
- Created `/opt/web-serv/files/` - storage for uploaded files
- Created `/opt/web-serv/config/files.json` - registry of uploaded files

### 2. Backend (server.py)

#### New Configuration
- Added `FILES_CONFIG_PATH` and `FILES_DIR` constants

#### New Helper Functions
- `load_files()` - Load files.json registry
- `save_files(data)` - Save files.json registry (sorted by filename)

#### New Flask Endpoints
- `GET /files` - List all uploaded files (JSON)
- `GET /files/<filename>` - Download any file with proper MIME-type detection
  - Supports: HTML, CSS, JS, JSON, images (PNG, JPG, GIF, SVG), PDF, TXT, XML

#### New Telegram Bot Commands

**`/files`** - List all uploaded files
- Shows: original name, filename, size, upload date, download link
- Empty state message with hint to use `/upload`

**`/upload`** - Upload file wizard (2 steps)
1. Send file or direct URL (max 100MB)
2. Optional rename (enter new name or "нет" to keep original)
- Sends static link after successful upload
- Handles duplicate filenames by adding numeric suffix

**`/delfile`** - Delete file with confirmation
- Inline keyboard with file list
- Confirmation dialog before deletion
- Removes from both filesystem and files.json

#### Integration
- Updated `get_main_keyboard()` - added file management buttons
- Updated `handle_document()` - routes to upload wizard when active
- Updated `handle_text_input()` - routes to upload wizard when active
- Updated `cancel_command()` - cancels upload wizard and cleans temp files
- Updated `handle_callback()` - routes delfile inline callbacks

### 3. Documentation

#### .env.example
- Added comment about files configuration

#### README.md
- Updated project structure (files/, files.json)
- Added new API endpoints (/files, /files/<filename>)
- Added 3 new bot commands to command table
- Added "File Management" section with detailed command descriptions
- Added nginx configuration for /files/ location

## Features

### File Upload
- Upload via Telegram document (any file type)
- Upload via direct URL
- Optional renaming after upload
- Automatic duplicate filename handling (adds _1, _2, etc.)
- Size limit: 100MB
- Temp file cleanup on error/cancel

### File Serving
- Public HTTPS access (requires nginx config)
- Proper MIME-type detection for common formats
- Rate limiting: 30 requests/minute

### File Management
- List all files with metadata
- Delete with confirmation
- JSON registry for tracking

## Security
- Admin-only access (ADMIN_ID check)
- Filename validation (no path traversal)
- Rate limiting on endpoints

## nginx Configuration Required

Add to nginx config for HTTPS access:

```nginx
location /files/ {
    alias /opt/web-serv/files/;
    default_type application/octet-stream;
}
```

## Testing Checklist

- [ ] `/files` command - list empty state
- [ ] `/upload` command - upload file via document
- [ ] `/upload` command - upload file via URL
- [ ] `/upload` command - rename file
- [ ] `/upload` command - keep original name
- [ ] `/files` command - list with files
- [ ] `/delfile` command - select and delete file
- [ ] `/cancel` command - cancel upload wizard
- [ ] HTTPS access to uploaded files
- [ ] Duplicate filename handling

## Files Modified
- `/opt/web-serv/server.py` - main implementation
- `/opt/web-serv/README.md` - documentation
- `/opt/web-serv/.env.example` - added comment

## Files Created
- `/opt/web-serv/files/` - directory
- `/opt/web-serv/config/files.json` - registry
