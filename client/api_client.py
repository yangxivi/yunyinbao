import os
import sys
import json
import time
import uuid
import socket
import platform
import logging
import urllib.request
import urllib.parse
import urllib.error
import ssl

logger = logging.getLogger(__name__)


class _HTTPClient:
    """极简 HTTP 客户端，替代 requests，避免打包后请求库兼容问题"""
    
    def __init__(self, timeout=5):
        self.timeout = timeout
        self._ssl_ctx = None
        try:
            self._ssl_ctx = ssl.create_default_context()
        except Exception:
            self._ssl_ctx = None
    
    def _request(self, method, url, data=None, files=None, json_data=None, params=None, timeout=None):
        if params:
            query = urllib.parse.urlencode(params)
            sep = '&' if '?' in url else '?'
            url = f"{url}{sep}{query}"
        
        body = None
        headers = {}
        
        if json_data is not None:
            body = json.dumps(json_data, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json; charset=utf-8'
        elif files is not None:
            boundary = f"----YunYinBao{uuid.uuid4().hex}"
            body_list = []
            for key, val in (data or {}).items():
                body_list.append(f"--{boundary}\r\n".encode())
                body_list.append(f'Content-Disposition: form-data; name="{key}"\r\n'.encode())
                body_list.append(b'\r\n')
                body_list.append(str(val).encode('utf-8'))
                body_list.append(b'\r\n')
            for field_name, file_tuple in files.items():
                if isinstance(file_tuple, tuple) and len(file_tuple) >= 2:
                    file_name, file_obj = file_tuple[0], file_tuple[1]
                    file_data = file_obj.read() if hasattr(file_obj, 'read') else file_obj
                    body_list.append(f"--{boundary}\r\n".encode())
                    body_list.append(
                        f'Content-Disposition: form-data; name="{field_name}"; filename="{file_name}"\r\n'.encode()
                    )
                    body_list.append(b'Content-Type: application/octet-stream\r\n')
                    body_list.append(b'\r\n')
                    body_list.append(file_data)
                    body_list.append(b'\r\n')
            body_list.append(f"--{boundary}--\r\n".encode())
            body = b''.join(body_list)
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        elif data is not None:
            body = urllib.parse.urlencode(data).encode('utf-8')
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout or self.timeout, context=self._ssl_ctx)
            return _Response(resp)
        except urllib.error.HTTPError as e:
            return _Response(e, is_error=True)
    
    def post(self, url, json_data=None, data=None, files=None, timeout=None):
        return self._request('POST', url, data=data, files=files, json_data=json_data, timeout=timeout)
    
    def get(self, url, params=None, timeout=None):
        return self._request('GET', url, params=params, timeout=timeout)


class _Response:
    def __init__(self, resp, is_error=False):
        self.status_code = resp.getcode() if hasattr(resp, 'getcode') else 200
        self._body = resp.read() if hasattr(resp, 'read') else b'{}'
        self._is_error = is_error
    
    def json(self):
        try:
            return json.loads(self._body.decode('utf-8', errors='replace'))
        except Exception:
            return {}


# 模拟 requests 异常类，保留兼容性
class _RequestException(Exception):
    pass

class exceptions:
    ConnectionError = _RequestException
    Timeout = _RequestException
    RequestException = _RequestException


class requests:
    """requests 兼容接口，内部用 urllib 实现"""
    exceptions = exceptions
    post = staticmethod(lambda *a, **kw: _HTTPClient().post(*a, **kw))
    get = staticmethod(lambda *a, **kw: _HTTPClient().get(*a, **kw))


class PrintClient:
    def __init__(self, auto_start_heartbeat=False):
        self.server_url = None
        self.access_code = None
        self.device_id = self._get_device_id()
        self.device_name = socket.gethostname()
        self.mac_address = self._get_mac()
        self.os_info = f"{platform.system()} {platform.release()}"
        self.printers = []
        self.is_connected = False
        self.user_id = None
        self.username = None
        self.virtual_print_auto = False
        self.config_file = self._get_config_path()
        self._heartbeat_started = False
        self._load_config()
        if auto_start_heartbeat:
            self._start_heartbeat()

    def _get_device_id(self):
        try:
            import hashlib
            mac = uuid.getnode()
            raw = f"{mac}-YUNYINBAO-CLIENT"
            return "DEV" + hashlib.md5(raw.encode()).hexdigest()[:12].upper()
        except:
            return "DEV" + uuid.uuid4().hex[:12].upper()

    def _get_mac(self):
        try:
            mac = uuid.getnode()
            return ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
        except:
            return ""

    def _get_config_path(self):
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        config_dir = os.path.join(appdata, 'YunYinBaoClient')
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, 'config.json')

    def _load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                self.server_url = config.get('server_url', '')
                self.access_code = config.get('access_code', '')
                self.user_id = config.get('user_id')
                self.username = config.get('username', '')
                self.virtual_print_auto = config.get('virtual_print_auto', False)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")

    def _save_config(self):
        try:
            config = {
                'server_url': self.server_url,
                'access_code': self.access_code,
                'user_id': self.user_id,
                'username': self.username,
                'virtual_print_auto': getattr(self, 'virtual_print_auto', False),
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    def connect_server(self, server_ip, port, access_code):
        self.server_url = f"http://{server_ip}:{port}"
        self.access_code = access_code
        
        try:
            data = {
                'device_id': self.device_id,
                'device_name': self.device_name,
                'mac_address': self.mac_address,
                'os_info': self.os_info,
                'access_code': access_code
            }
            resp = requests.post(f"{self.server_url}/api/device/register", json_data=data, timeout=5)
            result = resp.json()
            
            if result.get('success'):
                self.is_connected = True
                self.printers = result.get('printers', [])
                self._save_config()
                return True, "连接成功"
            else:
                self.is_connected = False
                return False, result.get('msg', '连接失败')
                
        except Exception as e:
            self.is_connected = False
            return False, f"连接失败: {e}"

    def _start_heartbeat(self):
        if self._heartbeat_started:
            return
        self._heartbeat_started = True
        import threading
        def heartbeat_loop():
            fail_count = 0
            while True:
                if not self.is_connected:
                    if self.server_url and self.access_code and fail_count < 10:
                        fail_count += 1
                        try:
                            ip = self.server_url.replace('http://', '').split(':')[0]
                            port = self.server_url.split(':')[-1]
                            ok, _ = self.connect_server(ip, port, self.access_code)
                            if ok:
                                logger.info("自动重连成功")
                                fail_count = 0
                        except:
                            pass
                        time.sleep(10)
                    else:
                        time.sleep(5)
                    continue
                
                try:
                    resp = requests.post(
                        f"{self.server_url}/api/device/heartbeat",
                        json_data={'device_id': self.device_id},
                        timeout=5
                    )
                    if resp.status_code == 200:
                        fail_count = 0
                    else:
                        fail_count += 1
                        if fail_count >= 3:
                            logger.warning(f"心跳连续失败{fail_count}次，标记为断开")
                            self.is_connected = False
                except Exception as e:
                    fail_count += 1
                    logger.debug(f"心跳失败: {e}")
                    if fail_count >= 3:
                        logger.warning(f"心跳连续失败{fail_count}次，标记为断开")
                        self.is_connected = False
                time.sleep(30)
        
        t = threading.Thread(target=heartbeat_loop, daemon=True)
        t.start()

    def get_printers(self):
        if not self.is_connected:
            return []
        
        try:
            resp = requests.get(
                f"{self.server_url}/api/printers",
                params={'device_id': self.device_id},
                timeout=5
            )
            result = resp.json()
            if result.get('success'):
                self.printers = result.get('printers', [])
                return self.printers
        except Exception as e:
            logger.error(f"获取打印机列表失败: {e}")
        
        return self.printers

    def upload_file(self, file_path):
        if not self.is_connected:
            return False, "未连接服务端", None
        
        if not os.path.exists(file_path):
            return False, "文件不存在", None
        
        try:
            file_name = os.path.basename(file_path)
            with open(file_path, 'rb') as f:
                files = {'file': (file_name, f)}
                data = {
                    'device_id': self.device_id,
                    'user_id': str(self.user_id) if self.user_id else '',
                    'username': self.username or '',
                }
                resp = requests.post(
                    f"{self.server_url}/api/upload",
                    files=files,
                    data=data,
                    timeout=60
                )
            
            result = resp.json()
            if result.get('success'):
                return True, "上传成功", result
            else:
                return False, result.get('msg', '上传失败'), None
                
        except Exception as e:
            return False, f"上传失败: {e}", None

    def submit_print(self, printer_id, file_name, file_path, file_size, pages=0,
                     copies=1, color_mode="black", duplex="simplex",
                     paper_size="A4", page_range="", task_id=None,
                     orientation="portrait", margin_top=0, margin_bottom=0,
                     margin_left=0, margin_right=0,
                     center_horizontal=0, center_vertical=0):
        if not self.is_connected:
            return False, "未连接服务端", None

        try:
            data = {
                'device_id': self.device_id,
                'user_id': self.user_id,
                'username': self.username,
                'device_name': self.device_name,
                'printer_id': printer_id,
                'file_name': file_name,
                'file_path': file_path,
                'file_size': file_size,
                'pages': pages,
                'copies': copies,
                'color_mode': color_mode,
                'duplex': duplex,
                'paper_size': paper_size,
                'page_range': page_range,
                'orientation': orientation,
                'margin_top': margin_top,
                'margin_bottom': margin_bottom,
                'margin_left': margin_left,
                'margin_right': margin_right,
                'center_horizontal': center_horizontal,
                'center_vertical': center_vertical,
                'task_id': task_id,
            }
            
            resp = requests.post(
                f"{self.server_url}/api/print/submit",
                json_data=data,
                timeout=10
            )
            
            result = resp.json()
            if result.get('success'):
                return True, "提交成功", result.get('task_id')
            else:
                return False, result.get('msg', '提交失败'), None
                
        except Exception as e:
            return False, f"提交失败: {e}", None

    def get_task_status(self, task_id):
        if not self.is_connected:
            return None
        
        try:
            resp = requests.get(
                f"{self.server_url}/api/task/status",
                params={'task_id': task_id},
                timeout=5
            )
            result = resp.json()
            if result.get('success'):
                return result.get('task')
        except Exception as e:
            logger.error(f"获取任务状态失败: {e}")
        
        return None

    def cancel_task(self, task_id):
        if not self.is_connected:
            return False, "未连接服务端"
        
        try:
            data = {
                'task_id': task_id,
                'user_id': self.user_id
            }
            resp = requests.post(
                f"{self.server_url}/api/task/cancel",
                json_data=data,
                timeout=5
            )
            result = resp.json()
            return result.get('success', False), result.get('msg', '')
        except Exception as e:
            return False, str(e)

    def retry_task(self, task_id):
        if not self.is_connected:
            return False, "未连接服务端", None
        
        try:
            data = {'task_id': task_id}
            resp = requests.post(
                f"{self.server_url}/api/task/retry",
                json_data=data,
                timeout=5
            )
            result = resp.json()
            if result.get('success'):
                return True, "", result.get('new_task_id')
            return False, result.get('msg', ''), None
        except Exception as e:
            return False, str(e), None

    def get_my_tasks(self):
        if not self.is_connected:
            return []
        
        try:
            resp = requests.get(
                f"{self.server_url}/api/device/tasks",
                params={'device_id': self.device_id},
                timeout=5
            )
            result = resp.json()
            if result.get('success'):
                return result.get('tasks', [])
        except Exception as e:
            logger.error(f"获取任务列表失败: {e}")
        
        return []

    def clear_my_tasks(self, status=None):
        if not self.is_connected:
            return 0, "未连接服务端"
        
        try:
            data = {'device_id': self.device_id}
            if status:
                data['status'] = status
            resp = requests.post(
                f"{self.server_url}/api/device/tasks/clear",
                json_data=data,
                timeout=5
            )
            result = resp.json()
            if result.get('success'):
                return result.get('count', 0), ""
            return 0, result.get('msg', '清空失败')
        except Exception as e:
            logger.error(f"清空任务失败: {e}")
            return 0, str(e)

    def login(self, username, password):
        if not self.is_connected:
            return False, "未连接服务端"
        
        try:
            data = {'username': username, 'password': password}
            resp = requests.post(
                f"{self.server_url}/api/user/login",
                json_data=data,
                timeout=5
            )
            result = resp.json()
            if result.get('success'):
                user = result.get('user', {})
                self.user_id = user.get('id')
                self.username = user.get('username', '')
                self._save_config()
                return True, user
            else:
                return False, result.get('msg', '登录失败')
        except Exception as e:
            return False, str(e)

    def quick_print(self, file_path, printer_id=None, copies=1, color_mode="black"):
        if not self.is_connected:
            return False, "未连接服务端"
        
        if printer_id is None and self.printers:
            for p in self.printers:
                if p.get('is_default'):
                    printer_id = p['id']
                    break
            if printer_id is None and self.printers:
                printer_id = self.printers[0]['id']
        
        if printer_id is None:
            return False, "没有可用的打印机"
        
        success, msg, upload_info = self.upload_file(file_path)
        if not success:
            return False, msg
        
        success, msg, task_id = self.submit_print(
            printer_id=printer_id,
            file_name=upload_info['file_name'],
            file_path=upload_info['file_path'],
            file_size=upload_info['file_size'],
            pages=upload_info.get('pages', 0),
            copies=copies,
            color_mode=color_mode
        )
        
        return success, msg if not success else task_id

    def disconnect(self):
        self.is_connected = False
    
    def clear_saved_config(self):
        self.server_url = None
        self.access_code = None
        self.user_id = None
        self.username = None
        self.is_connected = False
        self._save_config()

    def check_connection(self):
        if not self.server_url:
            return False
        try:
            resp = requests.get(f"{self.server_url}/api/health", timeout=3)
            return resp.status_code == 200
        except:
            return False

    def get_local_printers(self):
        printers = []
        try:
            import win32print
            enum_flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            for p in win32print.EnumPrinters(enum_flags):
                name = p[2] if isinstance(p, tuple) else p.get('pPrinterName', str(p))
                printers.append({"name": name})
            return printers
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"win32print 获取打印机失败: {e}")

        try:
            import subprocess
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 'Get-Printer | Select-Object -ExpandProperty Name | ConvertTo-Json -Compress'],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            res = result.stdout.strip()
            if res:
                import json as _json
                data = _json.loads(res)
                if isinstance(data, str):
                    data = [data]
                elif isinstance(data, list):
                    pass
                else:
                    data = [str(data)]
                for name in data:
                    if name:
                        printers.append({"name": name})
            return printers
        except Exception as e:
            logger.debug(f"PowerShell 获取打印机失败: {e}")

        try:
            import ctypes
            from ctypes import wintypes
            PRINTER_ENUM_LOCAL = 0x00000002
            PRINTER_ENUM_CONNECTIONS = 0x00000004
            flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS

            class PRINTER_INFO_1(ctypes.Structure):
                _fields_ = [
                    ("Flags", wintypes.DWORD),
                    ("pDescription", wintypes.LPWSTR),
                    ("pName", wintypes.LPWSTR),
                    ("pComment", wintypes.LPWSTR),
                ]

            needed = wintypes.DWORD(0)
            returned = wintypes.DWORD(0)
            winspool = ctypes.windll.winspool.drv
            winspool.EnumPrintersW(flags, None, 1, None, 0, ctypes.byref(needed), ctypes.byref(returned))

            if needed.value > 0:
                buf = ctypes.create_string_buffer(needed.value)
                if winspool.EnumPrintersW(flags, None, 1, buf, needed, ctypes.byref(needed), ctypes.byref(returned)):
                    for i in range(returned.value):
                        offset = ctypes.sizeof(PRINTER_INFO_1) * i
                        info = PRINTER_INFO_1.from_buffer_copy(buf, offset)
                        if info.pName:
                            printers.append({"name": info.pName})
        except Exception as e:
            logger.debug(f"ctypes 获取打印机失败: {e}")

        return printers