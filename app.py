"""
本サーバーのメインアプリケーション
"""
from flask import Flask, request, jsonify, render_template_string
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import requests
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import database
from config import SECRET_KEY, DEBUG, ALLOWED_ORIGINS

# Flaskアプリ初期化
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
CORS(app, origins=ALLOWED_ORIGINS)

# Socket.IO初期化
socketio = SocketIO(app, cors_allowed_origins=ALLOWED_ORIGINS)

# データベース初期化
database.init_db()

# ===========================
# 定期タスク
# ===========================

scheduler = BackgroundScheduler()

@scheduler.scheduled_job('interval', seconds=60)
def scheduled_tasks():
    """定期実行タスク"""
    # 古いロックを解放
    database.release_stale_locks(minutes=5)
    
    # タイムアウト処理
    timeout_requests = database.timeout_old_requests(minutes=10)
    
    # タイムアウトしたリクエストをコールバック
    for req in timeout_requests:
        send_callback(req['callback_url'], {
            'genre': req['genre'],
            'request_id': req['request_id'],
            'status': 'timeout'
        })

@scheduler.scheduled_job('interval', hours=24)
def cleanup_task():
    """日次クリーンアップ"""
    database.cleanup_old_requests(days=30)

# スケジューラー開始
scheduler.start()

# ===========================
# ヘルパー関数
# ===========================

def send_callback(callback_url, data, max_retries=1):
    """サブサーバーにコールバック送信 (リトライ付き)"""
    for attempt in range(max_retries + 1):
        try:
            print(f"[INFO] コールバック送信: {callback_url} - {data}")
            response = requests.post(callback_url, json=data, timeout=5)
            
            if response.status_code == 200:
                print(f"[INFO] コールバック送信成功")
                return True
            else:
                print(f"[WARNING] コールバック送信失敗: status={response.status_code}")
                
        except Exception as e:
            print(f"[ERROR] コールバック送信エラー: {e}")
            
            if attempt < max_retries:
                print(f"[INFO] リトライ {attempt + 1}/{max_retries}")
                import time
                time.sleep(2)
            else:
                print(f"[ERROR] コールバック送信失敗 (最終)")
                return False
    
    return False

# ===========================
# HTTPエンドポイント
# ===========================

@app.route('/')
def index():
    """トップページ"""
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>本サーバー (Master Server)</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
            h1 { color: #333; }
            .status { background: #f0f0f0; padding: 15px; border-radius: 5px; }
        </style>
    </head>
    <body>
        <h1>本サーバー (Master Server)</h1>
        <div class="status">
            <h2>ステータス: 稼働中</h2>
            <p>このサーバーは本サーバー (Master Server) です。</p>
            <p>サブサーバーとPCの間でリクエストを中継します。</p>
        </div>
        <h3>エンドポイント:</h3>
        <ul>
            <li>POST /api/request - リクエスト作成</li>
            <li>GET /api/pending-requests - 未処理リクエスト取得</li>
            <li>GET /api/request/{genre}/{id} - リクエスト詳細</li>
            <li>WebSocket / - PC接続用</li>
        </ul>
    </body>
    </html>
    '''
    return render_template_string(html)

@app.route('/api/request', methods=['POST'])
def create_request():
    """リクエスト作成 (サブサーバーから呼ばれる)"""
    try:
        data = request.json
        genre = data.get('genre')
        callback_url = data.get('callback_url')
        
        if not genre or not callback_url:
            return jsonify({'error': 'genre and callback_url are required'}), 400
        
        # リクエスト作成
        request_id = database.create_request(genre, callback_url)
        
        # WebSocketで全PCに配信
        request_data = {
            'genre': genre,
            'request_id': request_id,
            'url': f"{request.host_url}api/request/{genre}/{request_id}"
        }
        
        print(f"[INFO] 新規リクエスト作成: {genre} - {request_id}")
        socketio.emit('new_request', request_data)
        
        return jsonify({
            'status': 'created',
            'genre': genre,
            'request_id': request_id
        }), 201
        
    except Exception as e:
        print(f"[ERROR] リクエスト作成エラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/pending-requests', methods=['GET'])
def get_pending_requests():
    """未処理リクエスト取得 (PCから呼ばれる)"""
    try:
        pending = database.get_pending_requests()
        
        # URLを追加
        for req in pending:
            req['url'] = f"{request.host_url}api/request/{req['genre']}/{req['request_id']}"
        
        return jsonify(pending), 200
        
    except Exception as e:
        print(f"[ERROR] 未処理リクエスト取得エラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/request/<genre>/<request_id>', methods=['GET'])
def get_request_detail(genre, request_id):
    """リクエスト詳細取得"""
    try:
        detail = database.get_request_detail(genre, request_id)
        
        if detail:
            return jsonify(detail), 200
        else:
            return jsonify({'error': 'Request not found'}), 404
            
    except Exception as e:
        print(f"[ERROR] リクエスト詳細取得エラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/lock-request', methods=['POST'])
def lock_request():
    """リクエストをロック (オプション機能)"""
    try:
        data = request.json
        genre = data.get('genre')
        request_id = data.get('request_id')
        pc_id = data.get('pc_id')
        
        if not all([genre, request_id, pc_id]):
            return jsonify({'error': 'Missing parameters'}), 400
        
        locked = database.lock_request(genre, request_id, pc_id)
        
        return jsonify({'locked': locked}), 200
        
    except Exception as e:
        print(f"[ERROR] ロックエラー: {e}")
        return jsonify({'error': str(e)}), 500

# ===========================
# WebSocketイベント
# ===========================

@socketio.on('connect')
def handle_connect():
    """PC側が接続"""
    print(f'[INFO] PC接続: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    """PC側が切断"""
    print(f'[INFO] PC切断: {request.sid}')

@socketio.on('response')
def handle_response(data):
    """PC側から返答受信 (接続チェックのみ)"""
    try:
        genre = data.get('genre')
        request_id = data.get('request_id')
        status = data.get('status')
        pc_id = data.get('pc_id')
        
        print(f"[INFO] PC返答受信: {genre} - {request_id} = {status} (from {pc_id})")
        
        # データベース更新 (冪等性確保)
        updated = database.update_request_status(genre, request_id, status, pc_id)
        
        if not updated:
            print(f"[WARNING] 既に処理済み: {genre} - {request_id}")
            return
        
        # callback_urlを取得
        request_data = database.get_request_detail(genre, request_id)
        
        if request_data:
            # サブサーバーに通知
            callback_data = {
                'genre': genre,
                'request_id': request_id,
                'status': status,
                'pc_id': pc_id
            }
            
            send_callback(request_data['callback_url'], callback_data)
        
    except Exception as e:
        print(f"[ERROR] 返答処理エラー: {e}")

# ===========================
# メイン処理
# ===========================

if __name__ == '__main__':
    print("=" * 60)
    print("本サーバー (Master Server) 起動")
    print("=" * 60)
    
    # Socket.IOサーバー起動
    socketio.run(app, host='0.0.0.0', port=5000, debug=DEBUG)