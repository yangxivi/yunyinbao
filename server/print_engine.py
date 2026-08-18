import os
import sys
import time
import logging
import subprocess
import ctypes
import io
import zipfile
import urllib.request
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
        # 若本地/系统均找不到 SumatraPDF，则自动下载便携版到本地缓存，
        # 以保证 PDF/图片/文本打印始终走完全静默的 SumatraPDF 通道。
        self._ensure_sumatra_pdf()

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
            
            # 优先级1: 程序内置的 tools/SumatraPDF.exe
            # - 开发/未打包：项目根目录的 tools/ 下
            # - PyInstaller 打包（onedir）：资源被放到 _internal（即 sys._MEIPASS）下的 tools/
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
                meipass = getattr(sys, '_MEIPASS', None)
                if meipass:
                    possible_paths.append(os.path.join(meipass, "tools", "SumatraPDF.exe"))
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

    def _ensure_sumatra_pdf(self):
        """
        若尚未检测到 SumatraPDF，则尝试自动下载便携版到本地缓存目录，
        以确保 PDF / 图片 / 文本始终能通过 SumatraPDF 实现完全静默打印。
        下载失败不会抛异常，仅记录警告，届时 PDF 会回退到隐藏式 printto。
        """
        if self._sumatra_pdf_path:
            return

        cache_dir = self._sumatra_cache_dir()
        cached = os.path.join(cache_dir, "SumatraPDF.exe")
        if os.path.exists(cached):
            self._sumatra_pdf_path = cached
            logger.info(f"使用缓存的 SumatraPDF: {cached}")
            return

        logger.info("未检测到 SumatraPDF，尝试自动下载便携版（首次运行）...")
        try:
            os.makedirs(cache_dir, exist_ok=True)
            # 便携版 zip 内含 SumatraPDF.exe
            urls = [
                "https://github.com/sumatrapdfreader/sumatrapdf/releases/download/3.5.2/SumatraPDF-3.5.2-64.zip",
                "https://www.sumatrapdfreader.org/dl/rel/3.5.2/SumatraPDF-3.5.2-64.zip",
            ]
            import io
            import zipfile
            for url in urls:
                try:
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                    )
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        data = resp.read()
                    zf = zipfile.ZipFile(io.BytesIO(data))
                    # 优先取顶层的 SumatraPDF.exe
                    target = None
                    for name in zf.namelist():
                        if name.lower().endswith("sumatrapdf.exe"):
                            target = name
                            break
                    if not target:
                        continue
                    with open(cached, "wb") as f:
                        f.write(zf.read(target))
                    if os.path.getsize(cached) > 100000:
                        self._sumatra_pdf_path = cached
                        logger.info(f"SumatraPDF 已自动下载至: {cached}")
                        return
                    else:
                        try:
                            os.remove(cached)
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"从 {url} 下载 SumatraPDF 失败: {e}")
                    continue
            logger.warning("SumatraPDF 自动下载失败，PDF 打印将回退到隐藏 printto（个别机型可能闪现窗口）")
        except Exception as e:
            logger.warning(f"SumatraPDF 自动下载流程异常: {e}")

    def _sumatra_cache_dir(self):
        """SumatraPDF 缓存目录：优先 exe 同目录的 tools/，否则 LOCALAPPDATA"""
        try:
            if getattr(sys, 'frozen', False):
                base = os.path.dirname(sys.executable)
                d = os.path.join(base, "tools")
                os.makedirs(d, exist_ok=True)
                return d
        except Exception:
            pass
        local = os.environ.get('LOCALAPPDATA', tempfile.gettempdir())
        d = os.path.join(local, "YunYinBao")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

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
        if ext in ('.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx') and self.win32com_client:
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

        # ===== 4. 纯文本类：渲染成 PDF 后用 SumatraPDF 静默打印 =====
        if ext in ('.txt', '.csv', '.log', '.md', '.text', '.rtf') and self._sumatra_pdf_path:
            try:
                pdf_path = self._text_to_pdf(file_path, paper_size, orientation)
                if pdf_path:
                    try:
                        ok, msg = self._print_pdf_sumatra(
                            pdf_path, printer_name, copies,
                            color_mode, duplex, paper_size, page_range, orientation
                        )
                    finally:
                        try:
                            os.remove(pdf_path)
                        except Exception:
                            pass
                    if ok:
                        return True, msg
                    errors.append(f"Text->SumatraPDF: {msg}")
            except Exception as e:
                errors.append(f"Text->SumatraPDF异常: {e}")
                logger.warning(f"文本转PDF打印失败: {e}")

        # ===== 5. 兜底：仅当 SumatraPDF 缺失时，使用隐藏式 printto（SW_HIDE） =====
        # 正常情况不会走到这里（PDF/图片/文本均已用 SumatraPDF 静默处理）。
        # 若仍走到此，说明静默组件缺失，隐藏式 printto 是最后手段，多数打印机驱动不会弹窗。
        if not self._sumatra_pdf_path:
            logger.warning("SumatraPDF 缺失，退回隐藏式 printto（建议联网后首次运行自动下载）")
            ok, msg = self._shell_printto(file_path, printer_name)
            if ok:
                for _ in range(copies - 1):
                    self._shell_printto(file_path, printer_name)
                    time.sleep(0.3)
                return True, f"printto 发送到 {printer_name}（{copies}份，缺失静默组件）"
            errors.append(f"printto: {msg}")
            error_summary = " | ".join(errors[-4:])
            logger.error(f"所有静默打印方式均失败: {error_summary}")
            return False, f"所有静默打印方式均失败: {error_summary}"

        # 走到这里说明是 SumatraPDF/COM 都不支持的未知类型
        error_summary = " | ".join(errors[-4:]) or "未知文件类型且无可用的静默打印通道"
        logger.error(f"打印失败: {error_summary}")
        return False, f"打印失败: {error_summary}"

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
        """使用 COM 自动化打印 Word/Excel/PowerPoint（后台静默，不显示任何窗口）"""
        ext = os.path.splitext(file_path)[1].lower()
        app = None
        doc = None
        try:
            if ext in ('.doc', '.docx'):
                app = self.win32com_client.Dispatch("Word.Application")
            elif ext in ('.xls', '.xlsx'):
                app = self.win32com_client.Dispatch("Excel.Application")
            elif ext in ('.ppt', '.pptx'):
                app = self.win32com_client.Dispatch("PowerPoint.Application")
            else:
                return False, "不支持的格式"

            # 关键：后台静默，绝不显示任何窗口/对话框
            app.Visible = False
            try:
                app.DisplayAlerts = False
            except Exception:
                pass

            abs_path = os.path.abspath(file_path)
            if ext in ('.doc', '.docx'):
                doc = app.Documents.Open(abs_path)
            elif ext in ('.xls', '.xlsx'):
                doc = app.Workbooks.Open(abs_path)
            else:
                doc = app.Presentations.Open(abs_path, WithWindow=False)

            # 指定目标打印机（不同 Office 组件设置方式略有差异）
            try:
                if ext in ('.xls', '.xlsx'):
                    doc.Activate()
                app.ActivePrinter = printer_name
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

            # 打印（全程后台静默）
            try:
                if ext in ('.ppt', '.pptx'):
                    # PowerPoint.PrintOut(From, To, PrintToFile, Copies, Collate)
                    try:
                        doc.PrintOut(1, 9999, "", copies, True)
                    except Exception:
                        doc.PrintOut(Copies=copies)
                elif ext in ('.xls', '.xlsx'):
                    # Excel.PrintOut(From, To, Copies, Preview, ActivePrinter, ...)
                    doc.PrintOut(1, 1, copies, False, printer_name, False, True)
                else:
                    # Word
                    if page_range:
                        doc.PrintOut(Background=False, Copies=copies, Range=0, Pages=page_range)
                    else:
                        doc.PrintOut(Background=False, Copies=copies)
            except Exception as e:
                logger.warning(f"COM PrintOut 失败，重试: {e}")
                try:
                    if ext in ('.doc', '.docx'):
                        doc.PrintOut(Background=False, Copies=copies)
                    else:
                        doc.PrintOut(1, 1, copies, False, printer_name, False, True)
                except Exception as e2:
                    logger.warning(f"COM PrintOut 重试失败: {e2}")

            # 关闭文档与进程（不弹窗）
            try:
                if ext in ('.ppt', '.pptx'):
                    doc.Close()
                else:
                    doc.Close(False)
            except Exception:
                pass
            doc = None
            try:
                app.Quit()
            except Exception:
                pass
            app = None
            return True, f"COM {ext[1:].upper()} 打印（{copies}份）"
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

    def _text_to_pdf(self, file_path, paper_size="A4", orientation="portrait"):
        """将纯文本渲染为带页边距的 PDF（再用 SumatraPDF 静默打印，零窗口）"""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("PIL 不可用，无法将文本转PDF")
            return None

        try:
            paper_mm = {
                "A4": (210, 297),
                "A3": (297, 420),
                "A5": (148, 210),
                "Letter": (216, 279),
                "Legal": (216, 356),
            }
            w_mm, h_mm = paper_mm.get(paper_size, (210, 297))
            if orientation == "landscape":
                w_mm, h_mm = h_mm, w_mm
            mm_to_pt = 2.834645669
            page_w = int(round(w_mm * mm_to_pt))
            page_h = int(round(h_mm * mm_to_pt))
            margin = int(round(15 * mm_to_pt))  # 页边距 15mm

            # 优先使用系统中文字体，避免中文乱码；失败则退化为默认字体
            font = None
            for cand in ("msyh.ttc", "msyh.ttf", "simsun.ttc", "simhei.ttf",
                         "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
                try:
                    font = ImageFont.truetype(cand, 14)
                    break
                except Exception:
                    continue
            if font is None:
                try:
                    font = ImageFont.load_default()
                except Exception:
                    font = None

            def char_w(ch):
                if font and hasattr(font, "getlength"):
                    try:
                        return max(1, int(font.getlength(ch)))
                    except Exception:
                        return 14
                return 14

            # 估算每行可容纳字符数（按中文字宽估算）
            try:
                avg = char_w("中")
            except Exception:
                avg = 14
            max_chars = max(8, int((page_w - 2 * margin) / avg))

            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    raw_text = f.read()
            except Exception:
                return None

            # 按宽度折行
            lines = []
            for paragraph in raw_text.split("\n"):
                if paragraph == "":
                    lines.append("")
                    continue
                while len(paragraph) > max_chars:
                    lines.append(paragraph[:max_chars])
                    paragraph = paragraph[max_chars:]
                lines.append(paragraph)

            line_height = 22
            lines_per_page = max(1, int((page_h - 2 * margin) / line_height))
            pages = [lines[i:i + lines_per_page] for i in range(0, len(lines), lines_per_page)] or [[""]]

            images = []
            for pg in pages:
                img = Image.new("RGB", (page_w, page_h), "white")
                d = ImageDraw.Draw(img)
                y = margin
                for ln in pg:
                    try:
                        d.text((margin, y), ln, fill="black", font=font)
                    except Exception:
                        pass
                    y += line_height
                images.append(img)

            tmp = os.path.join(tempfile.gettempdir(), f"yunyinbao_txt_{int(time.time() * 1000)}.pdf")
            if len(images) == 1:
                images[0].save(tmp, "PDF", resolution=72.0)
            else:
                images[0].save(tmp, "PDF", resolution=72.0, save_all=True, append_images=images[1:])
            for im in images:
                try:
                    im.close()
                except Exception:
                    pass
            return tmp
        except Exception as e:
            logger.warning(f"文本转PDF失败: {e}")
            return None

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
