import hashlib
import base64
import uuid
import time
import random
import string

def md5(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()

def sha256(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()

def hash_password(password, salt=None):
    if salt is None:
        salt = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    h = sha256(password + salt + "YUNYINBAO_SALT_2026")
    return f"{salt}${h}"

def verify_password(password, hashed):
    try:
        salt, h = hashed.split('$', 1)
        return sha256(password + salt + "YUNYINBAO_SALT_2026") == h
    except:
        return False

def gen_task_id():
    return f"TASK{int(time.time()*1000)}{random.randint(1000,9999)}"

def gen_device_id():
    return f"DEV{uuid.uuid4().hex[:12].upper()}"

def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024*1024:
        return f"{size_bytes/1024:.1f} KB"
    elif size_bytes < 1024*1024*1024:
        return f"{size_bytes/(1024*1024):.1f} MB"
    else:
        return f"{size_bytes/(1024*1024*1024):.1f} GB"

XOR_KEY = 0x5A

def simple_encrypt(data_bytes):
    return bytes([b ^ XOR_KEY for b in data_bytes])

def simple_decrypt(data_bytes):
    return bytes([b ^ XOR_KEY for b in data_bytes])

def b64_encode(s):
    return base64.b64encode(s.encode('utf-8')).decode('ascii')

def b64_decode(s):
    return base64.b64decode(s.encode('ascii')).decode('utf-8')