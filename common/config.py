import os
import sys
import json
import uuid
import socket
import hashlib
import urllib.request
import urllib.error

APP_NAME = "云印宝"
APP_VERSION = "1.0.0"

def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = _get_base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "yunyinbao.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

def _ensure_dirs():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
    except Exception:
        pass

DEFAULT_SERVER_PORT = 8989
DEFAULT_WEB_PORT = 8990

def get_machine_code():
    hostname = socket.gethostname()
    try:
        mac = uuid.getnode()
        mac_str = ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
    except:
        mac_str = hostname
    raw = hostname + '-' + mac_str + '-YUNYINBAO'
    return hashlib.md5(raw.encode()).hexdigest()[:8].upper()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        try:
            return socket.gethostbyname(socket.gethostname())
        except:
            return '127.0.0.1'

def load_config():
    _ensure_dirs()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return default_config()

def save_config(config):
    _ensure_dirs()
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def default_config():
    return {
        'server': {'port': DEFAULT_SERVER_PORT, 'web_port': DEFAULT_WEB_PORT,
                   'access_code': get_machine_code(), 'auto_start': False, 'run_background': False},
        'printers': [], 'default_printer': None,
        'security': {'whitelist': [], 'use_whitelist': False, 'encrypt_transfer': True},
        'admin': {'username': 'admin', 'password': 'admin123'},
        'client': {'server_url': '', 'access_code': '', 'device_id': '',
                   'device_name': socket.gethostname(), 'auto_start': False},
        'theme': 'tech_blue',
    }

def get_config(key=None, default=None):
    config = load_config()
    if key is None: return config
    keys = key.split('.')
    val = config
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return default
    return val

def set_config(key, value):
    config = load_config()
    keys = key.split('.')
    obj = config
    for k in keys[:-1]:
        if k not in obj or not isinstance(obj[k], dict): obj[k] = {}
        obj = obj[k]
    obj[keys[-1]] = value
    save_config(config)
    return True
