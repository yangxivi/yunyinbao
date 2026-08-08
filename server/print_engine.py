import os
import sys
import time
import logging
import subprocess
import ctypes
from ctypes import wintypes
import tempfile

logger = logging.getLogger(__name__)


class PrintEngine:
    def __init__(self):
        self.win32api = None
        self.win32print = None
        self.win32com_client = None
        self._gdiplus = None
        self._sumatra_pdf_path = None
        self._load_win32_modules()
        self._detect_sumatra_pdf()

    def _load_win32_modules(self):
        """尝试加载 pywin32 模块，失败则标记为不可用"""
        try:
            import win32api
            self.win32api = win32api
        except ImportError:
            logger.warning("win32api 不可用，将使用 ctypes 兜底")
        try:
            import win32print
            self.win32print = win32print
        except ImportError:
            logger.warning("win32print 不可用")
        try:
            import win32com.client
            self.win32com_client = win32com.client
        except ImportError:
            logger.warning("win32com.client 不可用，Office COM 打印不可用")

    def _detect_sumatra_pdf(self):
        """检测 SumatraPDF 是否安装（用于PDF静默打印）"""
        try:
            import winreg
            possible_paths = []

            if getattr(sys, 'frozen', False):
                # PyInstaller 单文件打包：数据文件解压到 sys._MEIPASS，
                # sys.executable 也在该临时目录内，二者同目录。
                meipass = getattr(sys, '_MEIPASS', None)
                if meipass:
                    possible_paths.append(os.path.join(meipass, "tools", "SumatraPDF.exe"))
                    possible_paths.append(os.path.join(meipass, "SumatraPDF.exe"))
                exe_dir = os.path.dirname(sys.executable)
                possible_paths.append(os.path.join(exe_dir, "tools", "SumatraPDF.exe"))
                possible_paths.append(os.path.join(exe_dir, "SumatraPDF.exe"))
            else:
                # 源码运行：项目根目录下的 tools/SumatraPDF.exe
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                possible_paths.append(os.path.join(base_dir, "tools", "SumatraPDF.exe"))
            
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
                    except:
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
        """
        使用 ctypes 直接调用 ShellExecuteW，不依赖 pywin32
        verb: "print" 或 "printto"
        params: 当 verb=="printto" 时为打印机名（带引号）
        show_cmd: 0=隐藏, 1=正常显示
        """
        try:
            shell32 = ctypes.windll.shell32
            result = shell32.ShellExecuteW(
                None,
                verb,
                file_path,
                params,
                None,
                show_cmd
            )
            # 返回值 > 32 表示成功
            if result > 32:
                return True, None
            else:
                error_map = {
                    0: "内存不足",
                    2: "文件未找到",
                    3: "路径未找到",
                    5: "拒绝访问",
                    8: "内存不足",
                    26: "共享冲突",
                    27: "关联不完整",
                    28: "DDE超时",
                    29: "DDE事务失败",
                    30: "其他DDE错误",
                    31: "无关联程序",
                    32: "DLL未找到"
                }
                err = error_map.get(result, f"错误码 {result}")
                return False, f"ShellExecute失败: {err}"
        except Exception as e:
            return False, f"ctypes ShellExecute异常: {e}"

    def _shell_printto(self, file_path, printer_name):
        """优先使用 printto verb 直接指定打印机"""
        printer_param = f'"{printer_name}"'
        # 方式1: pywin32 win32api
        if self.win32api:
            try:
                self.win32api.ShellExecute(0, "printto", file_path, printer_param, ".", 0)
                return True, "win32api printto"
            except Exception as e:
                logger.warning(f"win32api printto 失败: {e}")
        # 方式2: ctypes
        ok, err = self._ctypes_shell_execute("printto", file_path, printer_param, 0)
        if ok:
            return True, "ctypes printto"
        logger.warning(f"ctypes printto 失败: {err}")
        return False, err

    def _shell_print(self, file_path):
        """使用默认打印机的 print verb（最后兜底）"""
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
        # ctypes 方式
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
            # 广播设置更改消息
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
        # ctypes 兜底枚举打印机
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

            buf = ctypes.create_string_buffer(needed.value)
            if winspool.EnumPrintersW(flags, None, 1, buf, needed, ctypes.byref(needed), ctypes.byref(returned)):
                for i in range(returned.value):
                    offset = ctypes.sizeof(PRINTER_INFO_1) * i
                    info = PRINTER_INFO_1.from_buffer_copy(buf, offset)
                    if info.pName:
                        printers.append(info.pName)
            return printers
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

    def test_print(self, printer_name):
        """测试打印：向打印机发送测试页"""
        logger.info(f"测试打印 -> {printer_name}")
        try:
            # 创建一个临时测试文本文件
            test_content = (
                f"云印宝 测试页\n"
                f"打印机: {printer_name}\n"
                f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"状态: 测试成功 - 云印宝打印服务正常工作\n"
            )
            tmp_path = os.path.join(tempfile.gettempdir(), f"yunyinbao_test_{int(time.time())}.txt")
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(test_content)

            success, method = self._print_file_any_method(tmp_path, printer_name)
            if success:
                logger.info(f"测试页已发送: {method}")
                try:
                    time.sleep(3)
                    os.remove(tmp_path)
                except Exception:
                    pass
                return True, f"测试页已通过 {method} 发送到 {printer_name}"
            else:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return False, method
        except Exception as e:
            logger.exception("测试打印异常")
            return False, f"测试打印失败: {e}"

    def print_file(self, file_path, printer_name, copies=1, color_mode="black",
                   duplex="simplex", paper_size="A4", page_range=None,
                   orientation="portrait", margin_top=0, margin_bottom=0,
                   margin_left=0, margin_right=0,
                   center_horizontal=0, center_vertical=0):
        ext = os.path.splitext(file_path)[1].lower()
        logger.info(f"开始打印: {file_path} -> 打印机: {printer_name}, 类型: {ext}, 份数: {copies}")

        if not os.path.exists(file_path):
            logger.error(f"打印文件不存在: {file_path}")
            return False, f"文件不存在: {file_path}"

        if not printer_name:
            logger.error("未指定打印机")
            return False, "未指定打印机"

        try:
            result = self._print_file_any_method(
                file_path, printer_name, copies, ext,
                color_mode, duplex, paper_size, page_range, orientation,
                margin_top, margin_bottom, margin_left, margin_right,
                center_horizontal, center_vertical
            )
            logger.info(f"打印结果: {result}")
            return result
        except Exception as e:
            logger.exception(f"打印文件失败 {file_path}")
            return False, str(e)

    def _print_file_any_method(self, file_path, printer_name, copies=1, ext=None,
                               color_mode="black", duplex="simplex", paper_size="A4",
                               page_range=None, orientation="portrait",
                               margin_top=0, margin_bottom=0, margin_left=0, margin_right=0,
                               center_horizontal=0, center_vertical=0):
        """按优先级依次尝试多种打印方式，直到成功。优先使用静默打印方式。"""
        errors = []

        # ===== 第一优先级：PDF 使用 SumatraPDF 静默打印 =====
        if ext == '.pdf' and self._sumatra_pdf_path:
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
                logger.warning(f"SumatraPDF打印失败: {e}")

        # ===== 图片文件转 PDF 后用 SumatraPDF 打印（应用边距/方向） =====
        if ext in ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif') and self._sumatra_pdf_path:
            try:
                pdf_path = self._image_to_pdf_with_margins(
                    file_path, orientation, margin_top, margin_bottom,
                    margin_left, margin_right, center_horizontal, center_vertical, paper_size
                )
                if pdf_path:
                    ok, msg = self._print_pdf_sumatra(
                        pdf_path, printer_name, copies,
                        color_mode, duplex, paper_size, page_range, orientation
                    )
                    try:
                        os.remove(pdf_path)
                    except Exception:
                        pass
                    if ok:
                        return True, msg
                    errors.append(f"Image->SumatraPDF: {msg}")
            except Exception as e:
                errors.append(f"Image->SumatraPDF异常: {e}")
                logger.warning(f"图片转PDF打印失败: {e}")

        # ===== 第二优先级：Office 文件尝试 COM（静默） =====
        if ext in ('.doc', '.docx', '.xls', '.xlsx') and self.win32com_client:
            try:
                ok, msg = self._print_office_com(
                    file_path, printer_name, copies,
                    color_mode, duplex, paper_size, page_range, orientation,
                    margin_top, margin_bottom, margin_left, margin_right,
                    center_horizontal, center_vertical
                )
                if ok:
                    return True, msg
                errors.append(f"COM: {msg}")
            except Exception as e:
                errors.append(f"COM异常: {e}")
                logger.warning(f"COM打印失败: {e}")

        # ===== 第三优先级：printto 指定打印机（静默窗口） =====
        ok, msg = self._shell_printto(file_path, printer_name)
        if ok:
            time.sleep(1)
            # 多份打印：重复提交（copies=1时不需要额外循环）
            for _ in range(copies - 1):
                self._shell_printto(file_path, printer_name)
                time.sleep(0.5)
            return True, f"printto 发送到 {printer_name}（{copies}份）"
        errors.append(f"printto: {msg}")

        # ===== 第三优先级：设置默认打印机后 print =====
        logger.info("printto失败，尝试设置默认打印机后 print")
        if self._set_default_printer(printer_name):
            time.sleep(0.5)
            ok, msg = self._shell_print(file_path)
            if ok:
                time.sleep(1)
                # 多份打印：重复提交（copies=1时不需要额外循环）
                for _ in range(copies - 1):
                    self._shell_print(file_path)
                    time.sleep(0.5)
                return True, f"默认打印机 print（{copies}份）"
            errors.append(f"默认print: {msg}")

        # ===== 第四优先级：PowerShell Start-Process =====
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
                # 多份打印：重复提交
                for _ in range(copies - 1):
                    subprocess.run(
                        ['powershell', '-NoProfile', '-Command', ps_cmd],
                        capture_output=True, text=True, timeout=20,
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                    )
                    time.sleep(0.5)
                return True, f"PowerShell printto（{copies}份）"
            err_text = (result.stderr or result.stdout or "").strip()[:200]
            errors.append(f"PowerShell printto: {err_text}")
        except Exception as e:
            errors.append(f"PowerShell printto异常: {e}")

        # ===== 第五优先级：PowerShell print（默认打印机已设置过）=====
        try:
            ps_cmd = (
                f'Start-Process -FilePath "{file_path}" '
                f'-Verb Print -WindowStyle Hidden -ErrorAction Stop'
            )
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_cmd],
                capture_output=True, text=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            if result.returncode == 0:
                time.sleep(1)
                # 多份打印：重复提交
                for _ in range(copies - 1):
                    subprocess.run(
                        ['powershell', '-NoProfile', '-Command', ps_cmd],
                        capture_output=True, text=True, timeout=20,
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                    )
                    time.sleep(0.5)
                return True, f"PowerShell print（{copies}份）"
            err_text = (result.stderr or result.stdout or "").strip()[:200]
            errors.append(f"PowerShell print: {err_text}")
        except Exception as e:
            errors.append(f"PowerShell print异常: {e}")

        # 注意：不再使用 os.startfile(file_path, "print") 作为兜底，
        # 因为该方式必然拉起关联程序的打印窗口/对话框，破坏静默打印。
        # PDF/图片统一走 SumatraPDF 静默打印，Office 走 COM 静默打印，
        # 其余类型由上面的 ShellExecute/PowerShell（均为隐藏窗口）兜底。

        error_summary = " | ".join(errors[-4:])
        logger.error(f"所有打印方式均失败: {error_summary}")
        return False, f"所有打印方式均失败: {error_summary}"

    def _print_pdf_sumatra(self, file_path, printer_name, copies=1,
                           color_mode="black", duplex="simplex", paper_size="A4",
                           page_range=None, orientation="portrait"):
        """使用 SumatraPDF 静默打印 PDF（不打开任何窗口）"""
        if not self._sumatra_pdf_path:
            return False, "SumatraPDF 未安装"

        try:
            # 构建 -print-settings 字符串
            settings_parts = []
            # 份数
            if copies and copies > 1:
                settings_parts.append(f"{copies}x")
            # 颜色
            settings_parts.append("color" if color_mode == "color" else "monochrome")
            # 双面
            duplex_map = {
                "simplex": "duplex:simplex",
                "duplex_long": "duplex:long",
                "duplex_short": "duplex:short",
            }
            settings_parts.append(duplex_map.get(duplex, "duplex:simplex"))
            # 纸张
            if paper_size and paper_size != "A4":
                settings_parts.append(f"paper:{paper_size}")
            # 方向
            if orientation == "landscape":
                settings_parts.append("landscape")
            else:
                settings_parts.append("portrait")
            # 页面范围
            if page_range:
                settings_parts.append(f"pages:{page_range}")

            print_settings = ",".join(settings_parts)

            # SumatraPDF 命令行静默打印：-print-to "打印机名" -print-settings ... -silent -exit-when-done 文件
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

    def _print_office_com(self, file_path, printer_name, copies=1,
                          color_mode="black", duplex="simplex", paper_size="A4",
                          page_range=None, orientation="portrait",
                          margin_top=0, margin_bottom=0, margin_left=0, margin_right=0,
                          center_horizontal=0, center_vertical=0):
        """使用 COM 自动化打印 Word/Excel（后台静默，不显示窗口）"""
        ext = os.path.splitext(file_path)[1].lower()
        app = None
        doc = None
        try:
            if ext in ('.doc', '.docx'):
                app = self.win32com_client.Dispatch("Word.Application")
            elif ext in ('.xls', '.xlsx'):
                app = self.win32com_client.Dispatch("Excel.Application")
            else:
                return False, "不支持的格式"

            app.Visible = False
            app.DisplayAlerts = 0

            doc = app.Documents.Open(os.path.abspath(file_path))
            try:
                doc.ActivePrinter = printer_name
            except Exception as e:
                logger.warning(f"COM设置ActivePrinter失败: {e}")

            # mm 转 points (1mm = 2.834645669 points)
            mm_to_pt = 2.834645669
            # Word 纸张大小映射
            word_paper_map = {"A4": 7, "A3": 6, "A5": 11, "Letter": 2, "Legal": 4}
            # Excel 纸张大小映射 (xlPaper...)
            excel_paper_map = {"A4": 9, "A3": 8, "A5": 11, "Letter": 1, "Legal": 5}

            if ext in ('.doc', '.docx'):
                ps = doc.PageSetup
                # 页边距
                try:
                    if margin_top:
                        ps.TopMargin = margin_top * mm_to_pt
                    if margin_bottom:
                        ps.BottomMargin = margin_bottom * mm_to_pt
                    if margin_left:
                        ps.LeftMargin = margin_left * mm_to_pt
                    if margin_right:
                        ps.RightMargin = margin_right * mm_to_pt
                except Exception as e:
                    logger.warning(f"COM设置Word页边距失败: {e}")
                # 方向 (0=wdOrientPortrait, 1=wdOrientLandscape)
                try:
                    ps.Orientation = 1 if orientation == "landscape" else 0
                except Exception as e:
                    logger.warning(f"COM设置Word方向失败: {e}")
                # 纸张大小
                try:
                    ps.PaperSize = word_paper_map.get(paper_size, 7)
                except Exception as e:
                    logger.warning(f"COM设置Word纸张大小失败: {e}")
            elif ext in ('.xls', '.xlsx'):
                ws = doc.ActiveSheet
                ps = ws.PageSetup
                try:
                    if margin_top:
                        ps.TopMargin = margin_top * mm_to_pt
                    if margin_bottom:
                        ps.BottomMargin = margin_bottom * mm_to_pt
                    if margin_left:
                        ps.LeftMargin = margin_left * mm_to_pt
                    if margin_right:
                        ps.RightMargin = margin_right * mm_to_pt
                except Exception as e:
                    logger.warning(f"COM设置Excel页边距失败: {e}")
                # 方向 (1=xlPortrait, 2=xlLandscape)
                try:
                    ps.Orientation = 2 if orientation == "landscape" else 1
                except Exception as e:
                    logger.warning(f"COM设置Excel方向失败: {e}")
                # 纸张大小
                try:
                    ps.PaperSize = excel_paper_map.get(paper_size, 9)
                except Exception as e:
                    logger.warning(f"COM设置Excel纸张大小失败: {e}")
                # 居中
                try:
                    ps.CenterHorizontally = bool(center_horizontal)
                    ps.CenterVertically = bool(center_vertical)
                except Exception as e:
                    logger.warning(f"COM设置Excel居中失败: {e}")

            # 打印 (PageRange: Word PrintOut 第3参数 RangeType，可选 wdPrintRangeOfPages 配合 Pages)
            try:
                if page_range:
                    doc.PrintOut(Background=False, Copies=copies, Range=0, Pages=page_range)
                else:
                    doc.PrintOut(Background=False, Copies=copies)
            except Exception as e:
                logger.warning(f"COM PrintOut with params failed: {e}, retry with copies only")
                doc.PrintOut(Background=False, Copies=copies)

            doc.Close(False)
            doc = None
            app.Quit()
            app = None
            return True, f"COM Word/Excel 打印（{copies}份）"
        except Exception as e:
            logger.warning(f"COM打印失败: {e}")
            try:
                if doc is not None:
                    doc.Close(False)
            except Exception:
                pass
            try:
                if app is not None:
                    app.Quit()
            except Exception:
                pass
            return False, str(e)

    def _image_to_pdf_with_margins(self, file_path, orientation="portrait",
                                   margin_top=0, margin_bottom=0, margin_left=0, margin_right=0,
                                   center_horizontal=0, center_vertical=0, paper_size="A4"):
        """将图片文件转为带边距的 PDF（用 PIL + reportlab 不可用时用 PIL 拼接）"""
        try:
            from PIL import Image
        except ImportError:
            logger.warning("PIL 不可用，无法将图片转PDF")
            return None

        try:
            # 纸张尺寸 (mm)，转 points (1mm = 2.834645669 pt)
            paper_mm = {
                "A4": (210, 297),
                "A3": (297, 420),
                "A5": (148, 210),
                "Letter": (216, 279),
                "Legal": (216, 356),
            }
            w_mm, h_mm = paper_mm.get(paper_size, (210, 297))
            # 横向时交换宽高
            if orientation == "landscape":
                w_mm, h_mm = h_mm, w_mm
            mm_to_pt = 2.834645669
            page_w_pt = w_mm * mm_to_pt
            page_h_pt = h_mm * mm_to_pt
            margin_top_pt = (margin_top or 0) * mm_to_pt
            margin_bottom_pt = (margin_bottom or 0) * mm_to_pt
            margin_left_pt = (margin_left or 0) * mm_to_pt
            margin_right_pt = (margin_right or 0) * mm_to_pt

            img = Image.open(file_path)
            img = img.convert("RGB")

            # 可用区域
            avail_w_pt = page_w_pt - margin_left_pt - margin_right_pt
            avail_h_pt = page_h_pt - margin_top_pt - margin_bottom_pt
            if avail_w_pt <= 0 or avail_h_pt <= 0:
                avail_w_pt = page_w_pt
                avail_h_pt = page_h_pt
                margin_top_pt = margin_left_pt = 0

            # 图片缩放到可用区域（保持比例），1pt = 1/72 inch, PIL 用 pixel@72dpi => pt
            img_w_pt = float(img.width)
            img_h_pt = float(img.height)
            scale = min(avail_w_pt / img_w_pt, avail_h_pt / img_h_pt, 1.0)
            new_w = int(img_w_pt * scale)
            new_h = int(img_h_pt * scale)
            if scale < 1.0:
                img = img.resize((new_w, new_h), Image.LANCZOS)

            # 计算位置（居中处理）
            if center_horizontal:
                x_pt = margin_left_pt + (avail_w_pt - new_w) / 2
            else:
                x_pt = margin_left_pt
            if center_vertical:
                y_pt = margin_top_pt + (avail_h_pt - new_h) / 2
            else:
                y_pt = margin_top_pt

            # 创建 A4 大小的白色画布 (px @ 72dpi == pt)
            canvas_w = int(round(page_w_pt))
            canvas_h = int(round(page_h_pt))
            canvas = Image.new("RGB", (canvas_w, canvas_h), "white")
            canvas.paste(img, (int(round(x_pt)), int(round(y_pt))))

            # 保存为 PDF（PIL 直接保存单页 PDF）
            tmp_pdf = os.path.join(
                tempfile.gettempdir(),
                f"yunyinbao_img_{int(time.time() * 1000)}.pdf"
            )
            canvas.save(tmp_pdf, "PDF", resolution=72.0)
            try:
                img.close()
                canvas.close()
            except Exception:
                pass
            return tmp_pdf
        except Exception as e:
            logger.warning(f"图片转PDF失败: {e}")
            return None

    def _print_pdf(self, file_path, printer_name, copies, duplex, paper_size, page_range):
        """PDF打印 - 统一走 _print_file_any_method"""
        return self._print_file_any_method(file_path, printer_name, copies, '.pdf')

    def _print_office(self, file_path, printer_name, copies, duplex, paper_size, page_range):
        return self._print_file_any_method(file_path, printer_name, copies, os.path.splitext(file_path)[1].lower())

    def _print_image(self, file_path, printer_name, copies, paper_size):
        return self._print_file_any_method(file_path, printer_name, copies, os.path.splitext(file_path)[1].lower())

    def _print_txt(self, file_path, printer_name, copies, paper_size):
        return self._print_file_any_method(file_path, printer_name, copies, '.txt')

    def _print_shell(self, file_path, printer_name):
        return self._print_file_any_method(file_path, printer_name, 1, None)
