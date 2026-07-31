import os
import time
import threading
import logging
from common.utils import hash_password, verify_password

logger = logging.getLogger(__name__)

class UserManager:
    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._ensure_admin()

    def _ensure_admin(self):
        try:
            conn = self.db.get_db()
            row = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
            if not row:
                now = self.db.now_ts()
                hashed = hash_password("admin123")
                conn.execute(
                    """INSERT INTO users 
                    (username, password, role, real_name, department, daily_limit, status, created_at, updated_at)
                    VALUES (?, ?, 'admin', '系统管理员', '系统管理', 0, 'active', ?, ?)""",
                    ("admin", hashed, now, now)
                )
                conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"初始化管理员失败: {e}")

    def register_user(self, username, password, real_name="", department="", role="user", daily_limit=0):
        with self._lock:
            try:
                conn = self.db.get_db()
                existing = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
                if existing:
                    conn.close()
                    return False, "用户名已存在"
                hashed = hash_password(password)
                now = self.db.now_ts()
                conn.execute(
                    """INSERT INTO users 
                    (username, password, role, real_name, department, daily_limit, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                    (username, hashed, role, real_name, department, daily_limit, now, now)
                )
                conn.commit()
                uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.close()
                return True, uid
            except Exception as e:
                return False, str(e)

    def authenticate(self, username, password):
        try:
            conn = self.db.get_db()
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            conn.close()
            if not row:
                return None, "用户不存在"
            if row['status'] != 'active':
                return None, "账号已禁用"
            if verify_password(password, row['password']):
                return dict(row), "登录成功"
            return None, "密码错误"
        except Exception as e:
            return None, str(e)

    def get_user(self, user_id):
        try:
            conn = self.db.get_db()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            conn.close()
            return dict(row) if row else None
        except:
            return None

    def get_all_users(self):
        try:
            conn = self.db.get_db()
            rows = conn.execute(
                "SELECT id, username, real_name, department, role, daily_limit, today_pages, status, created_at FROM users ORDER BY id"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except:
            return []

    def update_user(self, user_id, **kwargs):
        with self._lock:
            try:
                conn = self.db.get_db()
                now = self.db.now_ts()
                if 'password' in kwargs:
                    kwargs['password'] = hash_password(kwargs['password'])
                fields = []
                values = []
                for k, v in kwargs.items():
                    fields.append(f"{k} = ?")
                    values.append(v)
                fields.append("updated_at = ?")
                values.append(now)
                values.append(user_id)
                conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                return False

    def delete_user(self, user_id):
        with self._lock:
            try:
                conn = self.db.get_db()
                conn.execute("DELETE FROM users WHERE id = ? AND username != 'admin'", (user_id,))
                conn.commit()
                conn.close()
                return True
            except:
                return False

    def check_daily_limit(self, user_id, pages_needed):
        try:
            conn = self.db.get_db()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            conn.close()
            if not row:
                return False
            if row['daily_limit'] == 0:
                return True
            today = time.strftime("%Y-%m-%d")
            if row['last_reset_date'] != today:
                return True
            return (row['today_pages'] + pages_needed) <= row['daily_limit']
        except:
            return True