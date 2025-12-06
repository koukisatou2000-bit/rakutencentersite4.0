"""
データベース管理
"""
import sqlite3
from datetime import datetime
from contextlib import contextmanager

DATABASE_FILE = 'requests.db'

@contextmanager
def get_db():
    """データベース接続を取得"""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """データベース初期化"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # requestsテーブル
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                genre TEXT NOT NULL,
                request_id TEXT NOT NULL,
                callback_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                locked_by TEXT,
                locked_at TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(genre, request_id)
            )
        ''')
        
        # countersテーブル
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS counters (
                genre TEXT PRIMARY KEY,
                counter INTEGER NOT NULL DEFAULT 0
            )
        ''')
        
        # 初期ジャンルを追加
        cursor.execute('''
            INSERT OR IGNORE INTO counters (genre, counter) 
            VALUES ('logincheckrequest', 0)
        ''')
        cursor.execute('''
            INSERT OR IGNORE INTO counters (genre, counter) 
            VALUES ('connectioncheck', 0)
        ''')
        
        conn.commit()
        print("[INFO] データベース初期化完了")

def get_next_request_id(genre):
    """次のrequest_idを取得 (アトミック操作)"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # トランザクション開始
        cursor.execute('BEGIN IMMEDIATE')
        
        try:
            # カウンターをインクリメント
            cursor.execute('''
                UPDATE counters 
                SET counter = counter + 1 
                WHERE genre = ?
            ''', (genre,))
            
            # 新しいカウンターを取得
            cursor.execute('''
                SELECT counter FROM counters WHERE genre = ?
            ''', (genre,))
            
            result = cursor.fetchone()
            if result:
                new_counter = result[0]
                conn.commit()
                return f"{new_counter:05d}"
            else:
                conn.rollback()
                raise Exception(f"Genre not found: {genre}")
                
        except Exception as e:
            conn.rollback()
            raise e

def create_request(genre, callback_url):
    """リクエストを作成"""
    request_id = get_next_request_id(genre)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO requests (genre, request_id, callback_url, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        ''', (genre, request_id, callback_url, datetime.now().isoformat()))
        conn.commit()
    
    return request_id

def get_pending_requests():
    """未処理リクエストを取得"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT genre, request_id, callback_url, created_at
            FROM requests
            WHERE status = 'pending'
            ORDER BY created_at ASC
        ''')
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_request_detail(genre, request_id):
    """リクエスト詳細を取得"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM requests
            WHERE genre = ? AND request_id = ?
        ''', (genre, request_id))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_request_status(genre, request_id, status, pc_id=None):
    """リクエストのステータスを更新 (冪等性確保)"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # status='pending'の場合のみ更新
        cursor.execute('''
            UPDATE requests
            SET status = ?, completed_at = ?, locked_by = ?
            WHERE genre = ? AND request_id = ? AND status = 'pending'
        ''', (status, datetime.now().isoformat(), pc_id, genre, request_id))
        
        rows_affected = cursor.rowcount
        conn.commit()
        
        return rows_affected > 0

def lock_request(genre, request_id, pc_id):
    """リクエストをロック"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE requests
            SET locked_by = ?, locked_at = ?
            WHERE genre = ? AND request_id = ? 
            AND status = 'pending' AND locked_by IS NULL
        ''', (pc_id, datetime.now().isoformat(), genre, request_id))
        
        rows_affected = cursor.rowcount
        conn.commit()
        
        return rows_affected > 0

def cleanup_old_requests(days=30):
    """古いリクエストを削除"""
    from datetime import timedelta
    cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM requests
            WHERE status IN ('success', 'failed', 'timeout')
            AND completed_at < ?
        ''', (cutoff_date,))
        deleted = cursor.rowcount
        conn.commit()
        
        print(f"[INFO] 古いリクエスト削除: {deleted}件")
        return deleted

def release_stale_locks(minutes=5):
    """古いロックを解放"""
    from datetime import timedelta
    cutoff_time = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE requests
            SET locked_by = NULL, locked_at = NULL
            WHERE locked_by IS NOT NULL
            AND locked_at < ?
            AND status = 'pending'
        ''', (cutoff_time,))
        released = cursor.rowcount
        conn.commit()
        
        if released > 0:
            print(f"[INFO] 古いロック解放: {released}件")
        return released

def timeout_old_requests(minutes=10):
    """古いリクエストをタイムアウト"""
    from datetime import timedelta
    cutoff_time = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT genre, request_id, callback_url
            FROM requests
            WHERE status = 'pending'
            AND created_at < ?
        ''', (cutoff_time,))
        
        timeout_requests = [dict(row) for row in cursor.fetchall()]
        
        # タイムアウトに更新
        cursor.execute('''
            UPDATE requests
            SET status = 'timeout', completed_at = ?
            WHERE status = 'pending'
            AND created_at < ?
        ''', (datetime.now().isoformat(), cutoff_time))
        
        conn.commit()
        
        if timeout_requests:
            print(f"[INFO] タイムアウトリクエスト: {len(timeout_requests)}件")
        
        return timeout_requests