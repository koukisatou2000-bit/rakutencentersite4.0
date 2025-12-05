"""
本サーバーの設定ファイル
"""
import os

# Flask設定
SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# データベース設定
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///requests.db')

# CORS設定 (サブサーバーのドメインを追加)
ALLOWED_ORIGINS = [
    'http://localhost:5001',
    'https://your-sub-server.onrender.com'
]

# タイムアウト設定 (分)
REQUEST_TIMEOUT_MINUTES = 10
LOCK_TIMEOUT_MINUTES = 5