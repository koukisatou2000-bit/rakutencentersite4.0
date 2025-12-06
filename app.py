"""
本サーバーのメインアプリケーション
"""
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
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
            .status { background: #f0f0f0; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
            .links { margin-top: 20px; }
            .links a {
                display: inline-block;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 10px 20px;
                text-decoration: none;
                border-radius: 5px;
                margin-right: 10px;
            }
            .links a:hover { opacity: 0.9; }
        </style>
    </head>
    <body>
        <h1>本サーバー (Master Server)</h1>
        <div class="status">
            <h2>ステータス: 稼働中</h2>
            <p>このサーバーは本サーバー (Master Server) です。</p>
            <p>サブサーバーとPCの間でリクエストを中継します。</p>
        </div>
        
        <div class="links">
            <a href="/admin">管理画面</a>
        </div>
        
        <h3>エンドポイント:</h3>
        <ul>
            <li>POST /api/request - リクエスト作成</li>
            <li>GET /api/pending-requests - 未処理リクエスト取得</li>
            <li>GET /api/request/{genre}/{id} - リクエスト詳細</li>
            <li>POST /api/pc-response - PC返答受信 (HTTP)</li>
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
        
        print(f"[INFO] リクエスト作成開始: genre={genre}, callback_url={callback_url}")
        
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
        print(f"[INFO] WebSocketで送信するデータ: {request_data}")
        
        socketio.emit('new_request', request_data)
        
        print(f"[INFO] WebSocket送信完了")
        
        return jsonify({
            'status': 'created',
            'genre': genre,
            'request_id': request_id
        }), 201
        
    except Exception as e:
        print(f"[ERROR] リクエスト作成エラー: {e}")
        import traceback
        traceback.print_exc()
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

@app.route('/api/pc-response', methods=['POST'])
def pc_response():
    """PC側からの返答受信 (HTTP POST版)"""
    try:
        data = request.json
        genre = data.get('genre')
        request_id = data.get('request_id')
        status = data.get('status')
        pc_id = data.get('pc_id')
        
        print(f"[INFO] PC返答受信 (HTTP): {genre} - {request_id} = {status} (from {pc_id})")
        
        # データベース更新 (冪等性確保)
        updated = database.update_request_status(genre, request_id, status, pc_id)
        
        if not updated:
            print(f"[WARNING] 既に処理済み: {genre} - {request_id}")
            return jsonify({'status': 'already_processed'}), 200
        
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
        
        return jsonify({'status': 'ok'}), 200
        
    except Exception as e:
        print(f"[ERROR] 返答処理エラー: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ===========================
# 管理画面
# ===========================

@app.route('/admin')
def admin():
    """管理画面"""
    html = '''
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>本サーバー管理画面</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container {
                max-width: 800px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                padding: 40px;
            }
            h1 { color: #333; margin-bottom: 30px; }
            .section {
                background: #f8f9fa;
                border-left: 4px solid #667eea;
                padding: 20px;
                margin-bottom: 20px;
                border-radius: 5px;
            }
            .section h2 { color: #333; margin-bottom: 15px; font-size: 18px; }
            .origin-list {
                list-style: none;
                margin-bottom: 15px;
            }
            .origin-item {
                background: white;
                padding: 10px;
                margin: 5px 0;
                border-radius: 5px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .origin-item button {
                background: #dc3545;
                color: white;
                border: none;
                padding: 5px 15px;
                border-radius: 5px;
                cursor: pointer;
            }
            .origin-item button:hover { background: #c82333; }
            .add-form {
                display: flex;
                gap: 10px;
            }
            .add-form input {
                flex: 1;
                padding: 12px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-size: 14px;
            }
            .add-form button {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
            }
            .add-form button:hover { opacity: 0.9; }
            .message {
                padding: 15px;
                margin-bottom: 20px;
                border-radius: 5px;
                display: none;
            }
            .message.success {
                background: #d4edda;
                border: 1px solid #c3e6cb;
                color: #155724;
                display: block;
            }
            .message.error {
                background: #f8d7da;
                border: 1px solid #f5c6cb;
                color: #721c24;
                display: block;
            }
            .back-link {
                display: inline-block;
                margin-bottom: 20px;
                color: #667eea;
                text-decoration: none;
            }
            .back-link:hover { text-decoration: underline; }
        </style>
    </head>
    <body>
        <div class="container">
            <a href="/" class="back-link">← トップページに戻る</a>
            
            <h1>🔧 本サーバー管理画面</h1>
            
            <div id="message" class="message"></div>
            
            <div class="section">
                <h2>📡 CORS許可オリジン</h2>
                <p style="color: #666; margin-bottom: 15px; font-size: 14px;">
                    サブサーバーのURLを追加すると、そのサーバーからのリクエストを受け付けるようになります。
                </p>
                
                <ul class="origin-list" id="originList">
                    {% for origin in origins %}
                    <li class="origin-item">
                        <span>{{ origin }}</span>
                        {% if not origin.startswith('http://localhost') %}
                        <button onclick="removeOrigin('{{ origin }}')">削除</button>
                        {% endif %}
                    </li>
                    {% endfor %}
                </ul>
                
                <div class="add-form">
                    <input type="text" id="newOrigin" placeholder="https://your-sub-server.onrender.com" />
                    <button onclick="addOrigin()">追加</button>
                </div>
            </div>
        </div>
        
        <script>
            function showMessage(text, type) {
                const msg = document.getElementById('message');
                msg.textContent = text;
                msg.className = 'message ' + type;
                setTimeout(() => {
                    msg.className = 'message';
                }, 3000);
            }
            
            async function addOrigin() {
                const input = document.getElementById('newOrigin');
                const origin = input.value.trim();
                
                if (!origin) {
                    showMessage('URLを入力してください', 'error');
                    return;
                }
                
                if (!origin.startsWith('http://') && !origin.startsWith('https://')) {
                    showMessage('URLはhttp://またはhttps://で始まる必要があります', 'error');
                    return;
                }
                
                try {
                    const response = await fetch('/admin/add-origin', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ origin: origin })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        showMessage('追加しました', 'success');
                        input.value = '';
                        setTimeout(() => location.reload(), 1000);
                    } else {
                        showMessage(data.error || '追加に失敗しました', 'error');
                    }
                } catch (error) {
                    showMessage('エラー: ' + error.message, 'error');
                }
            }
            
            async function removeOrigin(origin) {
                if (!confirm('本当に削除しますか?\\n' + origin)) return;
                
                try {
                    const response = await fetch('/admin/remove-origin', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ origin: origin })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        showMessage('削除しました', 'success');
                        setTimeout(() => location.reload(), 1000);
                    } else {
                        showMessage(data.error || '削除に失敗しました', 'error');
                    }
                } catch (error) {
                    showMessage('エラー: ' + error.message, 'error');
                }
            }
        </script>
    </body>
    </html>
    '''
    return render_template_string(html, origins=ALLOWED_ORIGINS)

@app.route('/admin/add-origin', methods=['POST'])
def add_origin():
    """オリジンを追加"""
    try:
        data = request.json
        origin = data.get('origin', '').strip()
        
        if not origin:
            return jsonify({'success': False, 'error': 'URLが空です'})
        
        if origin in ALLOWED_ORIGINS:
            return jsonify({'success': False, 'error': '既に追加されています'})
        
        ALLOWED_ORIGINS.append(origin)
        
        # CORSを更新
        app.config['CORS_ORIGINS'] = ALLOWED_ORIGINS
        
        print(f"[INFO] オリジン追加: {origin}")
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"[ERROR] オリジン追加エラー: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/remove-origin', methods=['POST'])
def remove_origin():
    """オリジンを削除"""
    try:
        data = request.json
        origin = data.get('origin', '').strip()
        
        if origin not in ALLOWED_ORIGINS:
            return jsonify({'success': False, 'error': '存在しません'})
        
        # localhost以外は削除可能
        if origin.startswith('http://localhost'):
            return jsonify({'success': False, 'error': 'localhostは削除できません'})
        
        ALLOWED_ORIGINS.remove(origin)
        
        # CORSを更新
        app.config['CORS_ORIGINS'] = ALLOWED_ORIGINS
        
        print(f"[INFO] オリジン削除: {origin}")
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"[ERROR] オリジン削除エラー: {e}")
        return jsonify({'success': False, 'error': str(e)})

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
    """PC側から返答受信 (WebSocket版)"""
    try:
        genre = data.get('genre')
        request_id = data.get('request_id')
        status = data.get('status')
        pc_id = data.get('pc_id')
        
        print(f"[INFO] PC返答受信 (WebSocket): {genre} - {request_id} = {status} (from {pc_id})")
        
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