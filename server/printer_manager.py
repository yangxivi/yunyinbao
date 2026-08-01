import os
import sys
import time
import threading
import logging
import subprocess
import json
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)


def _try_enum_printers_win32():
    """用 pywin32 枚举打印机"""
    try:
        import win32print
        enum_flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = []
        for p in win32print.EnumPrinters(enum_flags):
            name = p[2] if isinstance(p, tuple) else p.get('pPrinterName', str(p))
            color_mode = _detect_printer_color_mode(name)
            printers.append({"name": name, "status": "online", "color_mode": color_mode})
        return printers
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"win32print 枚举打印机失败: {e}")
        return None


def _detect_printer_color_mode(printer_name):
    """检测打印机是否支持彩色，返回 'color' 或 'black'"""
    try:
        import win32print
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(hPrinter, 2)
            devmode = info.get('pDevMode')
            if devmode and hasattr(devmode, 'Color'):
                # DMCOLOR_COLOR = 2, DMCOLOR_MONOCHROME = 1
                return "color" if devmode.Color == 2 else "black"
        finally:
            win32print.ClosePrinter(hPrinter)
    except Exception as e:
        logger.debug(f"检测打印机 {printer_name} 彩色支持失败: {e}")
    return "black"


def _try_enum_printers_ctypes():
    """用 ctypes 直接调用 EnumPrintersW，不依赖 pywin32"""
    try:
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

        if needed.value == 0:
            return []

        buf = ctypes.create_string_buffer(needed.value)
        printers = []
        if winspool.EnumPrintersW(flags, None, 1, buf, needed, ctypes.byref(needed), ctypes.byref(returned)):
            for i in range(returned.value):
                offset = ctypes.sizeof(PRINTER_INFO_1) * i
                info = PRINTER_INFO_1.from_buffer_copy(buf, offset)
                if info.pName:
                    printers.append({"name": info.pName, "status": "online"})
        return printers
    except Exception as e:
        logger.warning(f"ctypes EnumPrintersW 失败: {e}")
        return None


def _try_enum_printers_powershell():
    """用 PowerShell Get-Printer 枚举打印机"""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-Printer | Select-Object -Property Name,Type,Status | ConvertTo-Json -Compress'],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        res = result.stdout.strip()
        if not res:
            return []
        data = json.loads(res)
        if isinstance(data, dict):
            data = [data]
        printers = []
        for p in data:
            name = p.get("Name", "")
            if name:
                status_str = str(p.get("Status", "Normal"))
                is_online = "Normal" in status_str or status_str in ("0", "正常")
                printers.append({
                    "name": name,
                    "status": "online" if is_online else "offline"
                })
        return printers
    except Exception as e:
        logger.warning(f"PowerShell 获取打印机失败: {e}")
        return None


def _check_printer_status_powershell(printer_name):
    """用 PowerShell 检查打印机状态"""
    try:
        ps = (
            f'$p = Get-Printer -Name "{printer_name}" -ErrorAction SilentlyContinue; '
            f'if ($p) {{ $p.PrinterStatus }} else {{ "NotInstalled" }}'
        )
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        status_text = result.stdout.strip()
        if status_text == "NotInstalled" or not status_text:
            return "error", f"打印机 {printer_name} 未找到"
        if status_text in ("Normal", "正常", "Idle", "Printing"):
            return "online", "正常"
        if "Offline" in status_text or "离线" in status_text:
            return "offline", "离线"
        if "Error" in status_text or "错误" in status_text:
            return "error", "打印机错误"
        if "Paper" in status_text or "OutOfPaper" in status_text:
            return "paper_out", "缺纸"
        if "Toner" in status_text or "NoToner" in status_text:
            return "toner_low", "墨粉不足"
        if "Door" in status_text:
            return "door_open", "门打开"
        return "online", f"状态: {status_text}"
    except Exception as e:
        return "error", f"无法查询打印机状态: {e}"


def _check_printer_status_win32(printer_name):
    """用 win32print 检查打印机状态"""
    try:
        import win32print
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(hPrinter, 2)
            status = info.get('Status', 0)
        finally:
            win32print.ClosePrinter(hPrinter)
        if status == 0:
            return "online", "正常"
        status_map = {
            0x00000008: ("paper_out", "缺纸"),
            0x00000010: ("paper_jam", "卡纸"),
            0x00000080: ("offline", "离线"),
            0x00000100: ("busy", "忙"),
            0x00000002: ("error", "打印机错误"),
            0x00002000: ("toner_low", "墨粉不足"),
            0x00004000: ("no_toner", "无墨粉"),
            0x00200000: ("door_open", "门打开"),
            0x00100000: ("error", "内存不足"),
        }
        for flag, desc in status_map.items():
            if status & flag:
                return desc
        return "online", "正常"
    except Exception as e:
        return None, str(e)


class PrinterManager:
    def __init__(self, db):
        self.db = db
        self.printers_cache = {}
        self._lock = threading.Lock()
        self._load_printers()

    def _load_printers(self):
        try:
            conn = self.db.get_db()
            rows = conn.execute("SELECT * FROM printers ORDER BY id").fetchall()
            for r in rows:
                self.printers_cache[r['id']] = dict(r)
            conn.close()
        except Exception as e:
            logger.error(f"加载打印机失败: {e}")

    def get_local_printers(self):
        """获取本地打印机列表（多方式兜底）"""
        # 方式1: pywin32
        printers = _try_enum_printers_win32()
        if printers is not None:
            return printers
        # 方式2: ctypes
        printers = _try_enum_printers_ctypes()
        if printers is not None:
            return printers
        # 方式3: PowerShell
        printers = _try_enum_printers_powershell()
        if printers is not None:
            return printers
        logger.error("所有方式枚举打印机均失败，返回空列表")
        return []

    def add_printer(self, name, connection_type="USB", is_shared=1, is_default=0,
                    paper_size="A4", color_mode=None, duplex="simplex"):
        with self._lock:
            conn = self.db.get_db()
            now = self.db.now_ts()

            # 如果未指定 color_mode，自动检测打印机是否支持彩色
            if not color_mode:
                color_mode = _detect_printer_color_mode(name)
            
            if is_default:
                conn.execute("UPDATE printers SET is_default = 0 WHERE is_default = 1")
            
            cursor = conn.execute(
                """INSERT INTO printers 
                (name, connection_type, is_shared, is_default, paper_size, color_mode, duplex, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, connection_type, is_shared, is_default, paper_size, color_mode, duplex, "online", now, now)
            )
            pid = cursor.lastrowid
            conn.commit()
            
            row = conn.execute("SELECT * FROM printers WHERE id = ?", (pid,)).fetchone()
            self.printers_cache[pid] = dict(row)
            conn.close()
            return pid

    def update_printer(self, printer_id, **kwargs):
        with self._lock:
            conn = self.db.get_db()
            now = self.db.now_ts()
            
            if kwargs.get('is_default'):
                conn.execute("UPDATE printers SET is_default = 0 WHERE is_default = 1")
            
            fields = []
            values = []
            for k, v in kwargs.items():
                fields.append(f"{k} = ?")
                values.append(v)
            fields.append("updated_at = ?")
            values.append(now)
            values.append(printer_id)
            
            conn.execute(f"UPDATE printers SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
            
            row = conn.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
            if row:
                self.printers_cache[printer_id] = dict(row)
            conn.close()
            return True

    def delete_printer(self, printer_id):
        with self._lock:
            conn = self.db.get_db()
            conn.execute("DELETE FROM printers WHERE id = ?", (printer_id,))
            conn.commit()
            conn.close()
            self.printers_cache.pop(printer_id, None)
            return True

    def get_all_printers(self, shared_only=False):
        conn = self.db.get_db()
        if shared_only:
            rows = conn.execute("SELECT * FROM printers WHERE is_shared = 1 ORDER BY id").fetchall()
        else:
            rows = conn.execute("SELECT * FROM printers ORDER BY id").fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return result

    def get_printer(self, printer_id):
        conn = self.db.get_db()
        row = conn.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_default_printer(self):
        conn = self.db.get_db()
        row = conn.execute("SELECT * FROM printers WHERE is_default = 1 LIMIT 1").fetchone()
        conn.close()
        return dict(row) if row else None

    def check_printer_status(self, printer_name):
        """检查打印机状态（多方式兜底）"""
        # 方式1: win32print
        status, msg = _check_printer_status_win32(printer_name)
        if status is not None and status not in ("error",):
            return status, msg
        # 方式2: PowerShell
        status, msg = _check_printer_status_powershell(printer_name)
        return status, msg
