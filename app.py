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
