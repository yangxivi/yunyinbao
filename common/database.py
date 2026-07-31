import sqlite3
import json
import os
import time
import threading
from contextlib import contextmanager
from .config import DB_PATH

_db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    _db_lock.acquire()
    try:
        conn = get_db()
        c = conn.cursor()
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            real_name TEXT,
            department TEXT,
            daily_limit INTEGER DEFAULT 0,
            today_pages INTEGER DEFAULT 0,
            last_reset_date TEXT,
            status TEXT DEFAULT 'active',
            created_at REAL,
            updated_at REAL
        );

        CREATE TABLE IF NOT EXISTS printers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            connection_type TEXT DEFAULT 'USB',
            is_shared INTEGER DEFAULT 1,
            is_default INTEGER DEFAULT 0,
            paper_size TEXT DEFAULT 'A4',
            color_mode TEXT DEFAULT 'black',
            duplex TEXT DEFAULT 'simplex',
            status TEXT DEFAULT 'online',
            status_detail TEXT,
            created_at REAL,
            updated_at REAL
        );

        CREATE TABLE IF NOT EXISTS print_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE NOT NULL,
            user_id INTEGER,
            username TEXT,
            device_id TEXT,
            device_name TEXT,
            printer_id INTEGER,
            printer_name TEXT,
            file_name TEXT,
            file_path TEXT,
            file_size INTEGER,
            pages INTEGER DEFAULT 0,
            copies INTEGER DEFAULT 1,
            color_mode TEXT DEFAULT 'black',
            duplex TEXT DEFAULT 'simplex',
            paper_size TEXT DEFAULT 'A4',
            page_range TEXT,
            orientation TEXT DEFAULT 'portrait',
            margin_top REAL DEFAULT 0,
            margin_bottom REAL DEFAULT 0,
            margin_left REAL DEFAULT 0,
            margin_right REAL DEFAULT 0,
            center_horizontal INTEGER DEFAULT 0,
            center_vertical INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at REAL,
            started_at REAL,
            completed_at REAL,
            expires_at REAL
        );

        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT UNIQUE NOT NULL,
            device_name TEXT,
            ip_address TEXT,
            mac_address TEXT,
            os_info TEXT,
            last_online REAL,
            status TEXT DEFAULT 'online',
            blocked INTEGER DEFAULT 0,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            user_id INTEGER,
            action TEXT,
            detail TEXT,
            ip_address TEXT,
            created_at REAL
        );
        ''')

        # ===== 数据库迁移：补充缺失的列 =====
        try:
            cols = [row[1] for row in c.execute("PRAGMA table_info(print_tasks)").fetchall()]
            if 'device_id' not in cols:
                c.execute("ALTER TABLE print_tasks ADD COLUMN device_id TEXT")
            for col in ['orientation', 'margin_top', 'margin_bottom', 'margin_left', 'margin_right', 'center_horizontal', 'center_vertical']:
                if col not in cols:
                    c.execute(f"ALTER TABLE print_tasks ADD COLUMN {col} TEXT")
        except Exception:
            pass

        conn.commit()
        conn.close()
    finally:
        _db_lock.release()

def now_ts():
    return time.time()

def date_str(ts=None):
    if ts is None:
        ts = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def clear_tasks(status_filter=None):
    """清空打印任务
    status_filter: None=清空可安全删除的任务, 或指定状态如 'completed', 'failed', 'cancelled', 'expired'
    注意：不会删除 'pending'(等待中) 和 'printing'(打印中) 的任务
    """
    _db_lock.acquire()
    try:
        conn = get_db()
        c = conn.cursor()
        safe_statuses = ('completed', 'failed', 'cancelled', 'expired')
        if status_filter:
            if status_filter in safe_statuses:
                c.execute("DELETE FROM print_tasks WHERE status = ?", (status_filter,))
            else:
                conn.close()
                return 0
        else:
            placeholders = ','.join('?' * len(safe_statuses))
            c.execute(f"DELETE FROM print_tasks WHERE status IN ({placeholders})", safe_statuses)
        count = c.rowcount
        conn.commit()
        conn.close()
        return count
    finally:
        _db_lock.release()

def get_task_count():
    """获取任务总数"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM print_tasks")
        result = c.fetchone()
        conn.close()
        return result[0] if result else 0
    except:
        return 0

def get_printer_count():
    """获取打印机数量"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM printers")
        result = c.fetchone()
        conn.close()
        return result[0] if result else 0
    except:
        return 0

def get_device_count():
    """获取设备数量"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM devices")
        result = c.fetchone()
        conn.close()
        return result[0] if result else 0
    except:
        return 0
