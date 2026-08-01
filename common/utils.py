import hashlib
import uuid
import time
import random
import string

def md5(s): return hashlib.md5(s.encode('utf-8')).hexdigest()
def sha256(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()

def hash_password(password, salt=None):
    if salt is None:
        salt = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    h = sha256(password + salt + 'YUNYINBAO_SALT_2026')
    return salt + '$' + h

def verify_password(password, hashed):
    try:
        salt, h = hashed.split('$', 1)
        return sha256(password + salt + 'YUNYINBAO_SALT_2026') == h
    except:
        return False

def gen_task_id():
    return 'TASK' + str(int(time.time()*1000)) + str(random.randint(1000,9999))

def gen_device_id():
    return 'DEV' + uuid.uuid4().hex[:12].upper()

def format_size(size_bytes):
    if size_bytes < 1024: return str(size_bytes) + ' B'
    elif size_bytes < 1024*1024: return '{:.1f} KB'.format(size_bytes/1024)
    elif size_bytes < 1024*1024*1024: return '{:.1f} MB'.format(size_bytes/(1024*1024))
    else: return '{:.1f} GB'.format(size_bytes/(1024*1024*1024))
