# -*- coding: utf-8 -*-
"""
云印宝本地打印引擎
负责将PDF文件打印到本地系统打印机
多种打印方式兜底：SumatraPDF -> ShellExecute printto -> PowerShell -> os.startfile
"""
import os
import sys
import time
import logging
import subprocess
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)


class PrintEngine:
    """本地PDF打印引擎"""

    def __init__(self):
        self.win32api = None
        self.win32print = None
        self._sumatra_pdf_path = None
        self._load_win32_modules()
        self._detect_sumatra_pdf()

    def _load_win32_modules(self):
        """尝试加载 pywin32 模块（可选）"""
        try:
            import win32api
            self.win32api = win32api
        except ImportError:
            logger.debug("win32api 不可用，将使用 ctypes 兜底")
        try:
            import win32print
            self.win32print = win32print
        except ImportError:
            logger.debug("win32print 不可用")

    def _detect_sumatra_pdf(self):
        """检测 SumatraPDF 是否安装（用于PDF静默打印）"""
        try:
            import winreg
            possible_paths = []

            # 优先级1: 程序内置的 tools/SumatraPDF.exe
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            builtin_path = os.path.join(base_dir, "tools", "SumatraPDF.exe")
            if os.path.exists(builtin_path):
                possible_paths.append(builtin_path)

            # 优先级2: 与exe同目录
            if getattr(sys, 'frozen', False):
                same_dir = os.path.join(os.path.dirname(sys.executable), "SumatraPDF.exe")
                if os.path.exists(same_dir):
                    possible_paths.append(same_dir)

            # 优先级3: 从注册表查找
            for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                for key_path in [
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\SumatraPDF.exe",
                    r"SOFTWARE\SumatraPDF",
                ]:
                    try:
                        key = winreg.OpenKey(root, key_path)
                        val, _ = winreg.QueryValueEx(key, "")
                        if val and os.path.exists(val):
                            possible_paths.append(val)
                        winreg.CloseKey(key)
                    except Exception:
                        pass

            # 优先级4: 常见安装路径
            for p in [
                r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
                r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
            ]:
                if os.path.exists(p):
                    possible_paths.append(p)

            if possible_paths:
                self._sumatra_pdf_path = possible_paths[0]
                logger.info(f"检测到 SumatraPDF: {self._sumatra_pdf_path}")
            else:
                logger.info("未检测到 SumatraPDF，PDF 将使用系统默认方式打印")
        except Exception as e:
            logger.warning(f"检测 SumatraPDF 失败: {e}")

    def _ctypes_shell_execute(self, verb, file_path, params=None, show_cmd=0):
        """使用 ctypes 直接调用 ShellExecuteW"""
        try:
            shell32 = ctypes.windll.shell32
            result = shell32.ShellExecuteW(None, verb, file_path, params, None, show_cmd)
            if result > 32:
                return True, None
            error_map = {
                0: "内存不足", 2: "文件未找到", 3: "路径未找到",
                5: "拒绝访问", 31: "无关联程序", 32: "DLL未找到"
            }
            err = error_map.get(result, f"错误码 {result}")
            return False, f"ShellExecute失败: {err}"
        except Exception as e:
            return False, f"ctypes ShellExecute异常: {e}"

    def _shell_printto(self, file_path, printer_name):
        """使用 printto verb 直接指定打印机"""
        printer_param = f'"{printer_name}"'
        if self.win32api:
            try:
                self.win32api.ShellExecute(0, "printto", file_path, printer_param, ".", 0)
                return True, "win32api printto"
            except Exception as e:
                logger.warning(f"win32api printto 失败: {e}")
        ok, err = self._ctypes_shell_execute("printto", file_path, printer_param, 0)
        if ok:
            return True, "ctypes printto"
        logger.warning(f"ctypes printto 失败: {err}")
        return False, err

    def _shell_print(self, file_path):
        """使用默认打印机的 print verb"""
        if self.win32api:
            try:
                self.win32api.ShellExecute(0, "print", file_path, None, ".", 0)
                return True, "win32api print(默认打印机)"
            except Exception as e:
                logger.warning(f"win32api print 失败: {e}")
        ok, err = self._ctypes_shell_execute("print", file_path, None, 0)
        if ok:
            return True, "ctypes print(默认打印机)"
        return False, err

    def _set_default_printer(self, printer_name):
        """设置系统默认打印机"""
        if self.win32print:
            try:
                self.win32print.SetDefaultPrinter(printer_name)
                return True
            except Exception as e:
                logger.warning(f"win32print.SetDefaultPrinter 失败: {e}")
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows NT\CurrentVersion\Windows",
                0, winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "Device", 0, winreg.REG_SZ,
                              f"{printer_name},winspool,Ne00:")
            winreg.CloseKey(key)
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            result = ctypes.c_long()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0,
                "windows", SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
            )
            return True
        except Exception as e:
            logger.warning(f"注册表方式设置默认打印机失败: {e}")
        return False

    def get_printers(self):
        """获取系统打印机列表"""
        printers = []
        if self.win32print:
            try:
                flags = self.win32print.PRINTER_ENUM_LOCAL | self.win32print.PRINTER_ENUM_CONNECTIONS
                for p in self.win32print.EnumPrinters(flags):
                    name = p[2] if isinstance(p, tuple) else p.get('pPrinterName', str(p))
                    printers.append(name)
                return printers
            except Exception as e:
                logger.error(f"EnumPrinters失败: {e}")
        # ctypes 兜底
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
                return printers

            buf = ctypes.create_string_buffer(needed.value)
            if winspool.EnumPrintersW(flags, None, 1, buf, needed, ctypes.byref(needed), ctypes.byref(returned)):
                for i in range(returned.value):
                    offset = ctypes.sizeof(PRINTER_INFO_1) * i
                    info = PRINTER_INFO_1.from_buffer_copy(buf, offset)
                    if info.pName:
                        printers.append(info.pName)
        except Exception as e:
            logger.error(f"ctypes EnumPrinters失败: {e}")
        return printers

    def get_default_printer(self):
        """获取默认打印机名称"""
        if self.win32print:
            try:
                return self.win32print.GetDefaultPrinter()
            except Exception:
                pass
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows NT\CurrentVersion\Windows"
            )
            device, _ = winreg.QueryValueEx(key, "Device")
            winreg.CloseKey(key)
            return device.split(',')[0] if device else None
        except Exception:
            return None

    def _print_pdf_sumatra(self, file_path, printer_name, copies=1,
                           color_mode="black", duplex="simplex", paper_size="A4",
                           page_range=None, orientation="portrait"):
        """使用 SumatraPDF 静默打印 PDF"""
        if not self._sumatra_pdf_path:
            return False, "SumatraPDF 未安装"

        try:
            settings_parts = []
            if copies and copies > 1:
                settings_parts.append(f"{copies}x")
            settings_parts.append("color" if color_mode == "color" else "monochrome")
            duplex_map = {
                "simplex": "duplex:simplex",
                "duplex_long": "duplex:long",
                "duplex_short": "duplex:short",
            }
            settings_parts.append(duplex_map.get(duplex, "duplex:simplex"))
            if paper_size and paper_size != "A4":
                settings_parts.append(f"paper:{paper_size}")
            settings_parts.append("landscape" if orientation == "landscape" else "portrait")
            if page_range:
                settings_parts.append(f"pages:{page_range}")

            print_settings = ",".join(settings_parts)

            args = [
                self._sumatra_pdf_path,
                "-print-to", printer_name,
                "-print-settings", print_settings,
                "-silent",
                "-exit-when-done",
                file_path
            ]
            result = subprocess.run(
                args,
                capture_output=True, text=True, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            if result.returncode == 0:
                return True, f"SumatraPDF 静默打印（{copies}份，设置: {print_settings}）"
            else:
                err = (result.stderr or result.stdout or "").strip()[:200]
                return False, f"SumatraPDF返回 {result.returncode}: {err}"
        except Exception as e:
            return False, f"SumatraPDF异常: {e}"

    def print_pdf(self, file_path, printer_name, copies=1, color_mode="black",
                  duplex="simplex", paper_size="A4", page_range=None,
                  orientation="portrait"):
        """打印PDF文件到指定打印机（多方式兜底）"""
        logger.info(f"开始打印: {file_path} -> 打印机: {printer_name}, 份数: {copies}")

        if not os.path.exists(file_path):
            return False, f"文件不存在: {file_path}"
        if not printer_name:
            return False, "未指定打印机"

        errors = []

        # 优先级1: SumatraPDF 静默打印
        if self._sumatra_pdf_path:
            try:
                ok, msg = self._print_pdf_sumatra(
                    file_path, printer_name, copies,
                    color_mode, duplex, paper_size, page_range, orientation
                )
                if ok:
                    return True, msg
                errors.append(f"SumatraPDF: {msg}")
            except Exception as e:
                errors.append(f"SumatraPDF异常: {e}")

        # 优先级2: ShellExecute printto
        ok, msg = self._shell_printto(file_path, printer_name)
        if ok:
            time.sleep(1)
            for _ in range(copies - 1):
                self._shell_printto(file_path, printer_name)
                time.sleep(0.5)
            return True, f"printto 发送到 {printer_name}（{copies}份）"
        errors.append(f"printto: {msg}")

        # 优先级3: 设置默认打印机后 print
        if self._set_default_printer(printer_name):
            time.sleep(0.5)
            ok, msg = self._shell_print(file_path)
            if ok:
                time.sleep(1)
                for _ in range(copies - 1):
                    self._shell_print(file_path)
                    time.sleep(0.5)
                return True, f"默认打印机 print（{copies}份）"
            errors.append(f"默认print: {msg}")

        # 优先级4: PowerShell Start-Process printto
        try:
            ps_cmd = (
                f'Start-Process -FilePath "{file_path}" '
                f'-Verb Printto -ArgumentList \'"{printer_name}"\' '
                f'-WindowStyle Hidden -ErrorAction Stop'
            )
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_cmd],
                capture_output=True, text=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            if result.returncode == 0:
                time.sleep(1)
                for _ in range(copies - 1):
                    subprocess.run(
                        ['powershell', '-NoProfile', '-Command', ps_cmd],
                        capture_output=True, text=True, timeout=20,
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                    )
                    time.sleep(0.5)
                return True, f"PowerShell printto（{copies}份）"
            errors.append(f"PowerShell printto: {(result.stderr or '').strip()[:200]}")
        except Exception as e:
            errors.append(f"PowerShell printto异常: {e}")

        # 优先级5: os.startfile print（仅默认打印机）
        try:
            os.startfile(file_path, "print")
            time.sleep(2)
            for _ in range(copies - 1):
                os.startfile(file_path, "print")
                time.sleep(0.5)
            return True, f"os.startfile print（{copies}份，使用默认打印机）"
        except Exception as e:
            errors.append(f"os.startfile: {e}")

        error_summary = " | ".join(errors[-4:])
        logger.error(f"所有打印方式均失败: {error_summary}")
        return False, f"所有打印方式均失败: {error_summary}"
