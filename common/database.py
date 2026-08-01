import sqlite3
import os
import time
import threading
from .config import DB_PATH

_db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try: conn.execute('PRAGMA journal_mode=WAL')
    except: pass
    return conn

def init_db():
    _db_lock.acquire()
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
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
            user_id INTEGER, username TEXT,
            device_id TEXT, device_name TEXT,
            printer_id INTEGER, printer_name TEXT,
            file_name TEXT, file_path TEXT, file_size INTEGER,
            pages INTEGER DEFAULT 0, copies INTEGER DEFAULT 1,
            color_mode TEXT DEFAULT 'black', duplex TEXT DEFAULT 'simplex',
            paper_size TEXT DEFAULT 'A4', page_range TEXT,
            orientation TEXT DEFAULT 'portrait',
            status TEXT DEFAULT 'pending', error_msg TEXT,
            created_at REAL, started_at REAL, completed_at REAL,
            retries INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT UNIQUE NOT NULL,
            device_name TEXT, mac_address TEXT, os_info TEXT,
            last_ip TEXT, first_seen REAL, last_seen REAL,
            status TEXT DEFAULT 'offline', blocked INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sys_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT, module TEXT, message TEXT, created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON print_tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_created ON print_tasks(created_at);
        CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
        ''')
        conn.commit()
        c.execute('SELECT COUNT(*) FROM users WHERE username=?', ('admin',))
        if c.fetchone()[0] == 0:
            from .utils import hash_password
            now = time.time()
            c.execute('INSERT INTO users(username,password,role,real_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                      ('admin', hash_password('admin123'), 'admin', 'SystemAdmin', 'active', now, now))
            conn.commit()
        conn.close()
    finally:
        _db_lock.release()

def query(sql, params=()):
    conn = get_db()
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

def execute(sql, params=()):
    _db_lock.acquire()
    try:
        conn = get_db()
        cur = conn.execute(sql, params)
        conn.commit()
        lid = cur.lastrowid
        conn.close()
        return lid
    finally:
        _db_lock.release()
