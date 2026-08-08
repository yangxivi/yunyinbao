"""
云印宝虚拟打印机模块
第一阶段: 基于Microsoft Print to PDF + FILE端口的虚拟打印机实现
"""
import os
import sys
import time
import subprocess
import threading
import logging
import tempfile
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

PRINTER_NAME = "云印宝打印机"
DRIVER_NAME = "Microsoft Print To PDF"
SPOOL_DIR_NAME = "yunyinbao_spool"


def _run_powershell(command, timeout=15):
    """执行PowerShell命令"""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             '$OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; ' + command],
            capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        stdout = result.stdout.decode('utf-8', errors='replace').strip()
        stderr = result.stderr.decode('utf-8', errors='replace').strip()
        return result.returncode == 0, stdout, stderr
    except Exception as e:
        return False, "", str(e)


def _get_spool_dir():
    """获取假脱机输出目录（使用本地应用数据目录，避免打包后路径丢失）"""
    base_dir = os.path.join(
        os.environ.get('LOCALAPPDATA', tempfile.gettempdir()),
        'YunYinBao'
    )
    spool_dir = os.path.join(base_dir, SPOOL_DIR_NAME)
    os.makedirs(spool_dir, exist_ok=True)
    return spool_dir


def check_driver_exists():
    """检查Microsoft Print to PDF驱动是否存在"""
    ok, stdout, stderr = _run_powershell(
        'Get-PrinterDriver -Name "Microsoft Print To PDF" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name'
    )
    return ok and stdout == "Microsoft Print To PDF"


def check_printer_exists():
    """检查云印宝虚拟打印机是否已存在"""
    ok, stdout, stderr = _run_powershell(
        f'Get-Printer -Name "{PRINTER_NAME}" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name'
    )
    return ok and stdout == PRINTER_NAME


def create_virtual_printer():
    """
    创建云印宝虚拟打印机
    返回: (成功, 消息)
    """
    if check_printer_exists():
        return True, "虚拟打印机已存在"
    
    if not check_driver_exists():
        return False, "Microsoft Print to PDF驱动未安装，请先启用Windows功能"
    
    ok, stdout, stderr = _run_powershell(
        f'Add-Printer -Name "{PRINTER_NAME}" -DriverName "{DRIVER_NAME}" -PortName "FILE:"'
    )
    
    if ok or check_printer_exists():
        logger.info("虚拟打印机创建成功")
        return True, "虚拟打印机创建成功"
    else:
        logger.error(f"虚拟打印机创建失败: {stderr}")
        return False, f"创建失败: {stderr}"


def remove_virtual_printer():
    """删除云印宝虚拟打印机"""
    ok, stdout, stderr = _run_powershell(
        f'Remove-Printer -Name "{PRINTER_NAME}" -ErrorAction SilentlyContinue'
    )
    if not check_printer_exists():
        logger.info("虚拟打印机已删除")
        return True
    return False


def get_printer_info():
    """获取打印机信息"""
    ok, stdout, stderr = _run_powershell(
        f'Get-Printer -Name "{PRINTER_NAME}" -ErrorAction SilentlyContinue | Select-Object Name, DriverName, PortName, Status, Type | ConvertTo-Json'
    )
    if ok and stdout:
        try:
            import json
            return json.loads(stdout)
        except:
            return None
    return None


class PrintJobMonitor:
    """
    打印任务监控器
    监控云印宝打印机的任务队列
    """
    
    def __init__(self, on_new_job=None):
        """
        初始化监控器
        :param on_new_job: 新任务回调函数(job_id, document_name)
        """
        self.printer_name = PRINTER_NAME
        self.on_new_job = on_new_job
        self._running = False
        self._monitor_thread = None
        self._seen_jobs = set()
        self._stop_event = threading.Event()
        
    def start(self):
        """启动监控"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("打印任务监控已启动")
        
    def stop(self):
        """停止监控"""
        self._running = False
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3)
        logger.info("打印任务监控已停止")
        
    def _monitor_loop(self):
        """监控循环"""
        while self._running and not self._stop_event.is_set():
            try:
                self._check_jobs()
            except Exception as e:
                logger.debug(f"检查打印任务异常: {e}")
            self._stop_event.wait(2)  # 每2秒检查一次
            
    def _check_jobs(self):
        """检查打印任务"""
        try:
            import win32print
            
            hPrinter = win32print.OpenPrinter(self.printer_name)
            try:
                jobs = win32print.EnumJobs(hPrinter, 0, -1, 1)
                
                for job in jobs:
                    job_id = job.get('JobId', 0)
                    document = job.get('pDocument', '未知文档')
                    status = job.get('Status', 0)
                    
                    if job_id not in self._seen_jobs:
                        self._seen_jobs.add(job_id)
                        logger.info(f"检测到新打印任务: JobId={job_id}, 文档={document}")
                        
                        if self.on_new_job:
                            try:
                                self.on_new_job(job_id, document)
                            except Exception as e:
                                logger.error(f"新任务回调异常: {e}")
                
                # 清理已完成的任务
                active_job_ids = {job.get('JobId', 0) for job in jobs}
                self._seen_jobs = self._seen_jobs & active_job_ids
                
            finally:
                win32print.ClosePrinter(hPrinter)
        except ImportError:
            logger.warning("win32print不可用，无法监控打印任务")
            self._running = False
        except Exception as e:
            logger.debug(f"获取打印任务失败: {e}")


class DialogHandler:
    """
    保存对话框自动处理器（持续监听模式）
    在后台持续监听 Microsoft Print to PDF 的"将打印输出另存为"对话框，
    出现后自动填写文件名并保存到指定目录。

    优化点：
    - 递归枚举所有子控件，兼容 ComboBoxEx32 等嵌套 Edit
    - 优先使用 WM_SETTEXT + BM_CLICK；失败时回退到键盘输入
    - 激活窗口前附加线程输入，提升 SetForegroundWindow 成功率
    - 使用 ASCII 输出路径，避免输入法影响
    """

    SAVE_DIALOG_TITLES = {
        "将打印输出另存为", "另存为", "保存", "Save Print Output As",
        "Save As", "Save", "Print to File", "打印到文件",
    }

    SAVE_BUTTON_TEXTS = {
        "保存", "保存(&S)", "Save", "&Save", "OK", "确定", "确定(&O)",
        "&确定", "保存(S)",
    }

    def __init__(self, output_dir=None):
        self.output_dir = output_dir or _get_spool_dir()
        self._handler_thread = None
        self._stop_event = threading.Event()
        self._handled_dialogs = {}  # hwnd -> timestamp，防止重复处理并定期清理
        self._handling = set()      # 正在处理中的对话框
        self._handled_files = []    # 已处理对话框对应的输出文件路径
        self._lock = threading.Lock()
        self._counter = 0
        os.makedirs(self.output_dir, exist_ok=True)

    def get_recent_files(self, clear=True):
        """获取最近由对话框处理产生的文件路径"""
        with self._lock:
            files = list(self._handled_files)
            if clear:
                self._handled_files = []
            return files

    def start(self):
        """启动持续监听线程"""
        if self._handler_thread and self._handler_thread.is_alive():
            return
        self._stop_event.clear()
        self._handler_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._handler_thread.start()
        logger.info("保存对话框监听已启动")

    def stop(self):
        """停止监听"""
        self._stop_event.set()
        if self._handler_thread:
            self._handler_thread.join(timeout=3)
        logger.info("保存对话框监听已停止")

    def _next_filename(self):
        """生成下一个输出文件名"""
        with self._lock:
            self._counter += 1
            counter = self._counter
        timestamp = int(time.time() * 1000)
        return os.path.join(self.output_dir, f"printjob_{timestamp}_{counter}.pdf")

    def _listen_loop(self):
        """后台监听循环"""
        while not self._stop_event.is_set():
            try:
                self._scan_and_handle()
            except Exception as e:
                logger.debug(f"监听对话框异常: {e}")
            self._stop_event.wait(0.3)

    def _cleanup_handled_dialogs(self):
        """清理过期的已处理对话框记录（避免句柄复用导致误跳）"""
        now = time.time()
        expired = [hwnd for hwnd, ts in self._handled_dialogs.items() if now - ts > 60]
        for hwnd in expired:
            self._handled_dialogs.pop(hwnd, None)

    def _scan_and_handle(self):
        """扫描并处理所有保存对话框"""
        try:
            import win32gui
        except ImportError:
            return

        with self._lock:
            self._cleanup_handled_dialogs()

        dialogs = self._find_save_dialogs()
        for hwnd in dialogs:
            with self._lock:
                if hwnd in self._handled_dialogs or hwnd in self._handling:
                    continue
                self._handling.add(hwnd)

            output_path = self._next_filename()
            logger.info(f"发现保存对话框 (hwnd={hwnd})，准备保存到: {output_path}")
            handled = False
            try:
                if self._fill_and_save(hwnd, output_path):
                    logger.info(f"保存对话框已处理: {output_path}")
                    with self._lock:
                        self._handled_files.append(output_path)
                        self._handled_dialogs[hwnd] = time.time()
                    handled = True
                else:
                    logger.warning(f"保存对话框处理失败，可能控件未找到")
            except Exception as e:
                logger.debug(f"处理单个对话框异常: {e}")
            finally:
                with self._lock:
                    self._handling.discard(hwnd)
                    if handled:
                        self._handled_dialogs[hwnd] = time.time()

    def _find_save_dialogs(self):
        """查找所有疑似 Microsoft Print to PDF 保存对话框"""
        import win32gui

        dialogs = []

        def enum_proc(hwnd, result):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return True
            cls = win32gui.GetClassName(hwnd)
            # 标准 Windows 对话框类名 #32770
            if cls != "#32770":
                return True
            # 标题匹配（去除前后空格）
            if title.strip() in self.SAVE_DIALOG_TITLES:
                result.append(hwnd)
                return True
            # 兜底：标题包含关键词
            lower = title.lower()
            if any(k in lower for k in ["将打印输出", "print output", "另存为", "save as", "保存"]):
                result.append(hwnd)
            return True

        win32gui.EnumWindows(enum_proc, dialogs)
        return dialogs

    def _enum_all_children(self, hwnd):
        """递归枚举窗口及其所有后代子控件"""
        import win32gui
        controls = []

        def callback(chwnd, _):
            cls = win32gui.GetClassName(chwnd)
            text = win32gui.GetWindowText(chwnd)
            controls.append((chwnd, cls, text))
            win32gui.EnumChildWindows(chwnd, callback, None)
            return True

        win32gui.EnumChildWindows(hwnd, callback, None)
        return controls

    def _activate_dialog(self, hwnd):
        """将对话框提到前台并激活"""
        import win32gui
        import win32api

        try:
            fg = win32gui.GetForegroundWindow()
            if fg and fg != hwnd:
                cur_tid = win32api.GetCurrentThreadId()
                fg_tid = win32gui.GetWindowThreadProcessId(fg)[0]
                if cur_tid != fg_tid:
                    win32gui.AttachThreadInput(cur_tid, fg_tid, True)
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                        win32gui.BringWindowToTop(hwnd)
                        win32gui.SetActiveWindow(hwnd)
                    finally:
                        win32gui.AttachThreadInput(cur_tid, fg_tid, False)
                else:
                    win32gui.SetForegroundWindow(hwnd)
                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetActiveWindow(hwnd)
        except Exception as e:
            logger.debug(f"激活窗口失败: {e}")

    def _find_filename_edit(self, controls):
        """在控件列表中定位文件名输入框"""
        import win32gui

        candidates = []
        for chwnd, cls, text in controls:
            if cls == "Edit" or cls.endswith("Edit"):
                if win32gui.IsWindowEnabled(chwnd) and win32gui.IsWindowVisible(chwnd):
                    candidates.append(chwnd)
        if not candidates:
            return None

        # 优先选择位于 ComboBox 内且 y 坐标最大的 Edit（文件名输入框在底部）
        combo_box_edits = []
        for chwnd in candidates:
            try:
                parent = win32gui.GetParent(chwnd)
                if parent:
                    parent_cls = win32gui.GetClassName(parent)
                    if "ComboBox" in parent_cls or "combobox" in parent_cls:
                        rect = win32gui.GetWindowRect(chwnd)
                        combo_box_edits.append((chwnd, rect[1]))  # (hwnd, y坐标)
            except Exception:
                pass

        if combo_box_edits:
            combo_box_edits.sort(key=lambda x: x[1])
            return combo_box_edits[-1][0]

        # 回退：选择 y 坐标最大的可见 Edit（文件名输入框总是在底部区域）
        best = None
        max_y = -1
        for chwnd in candidates:
            try:
                rect = win32gui.GetWindowRect(chwnd)
                if rect[1] > max_y:
                    max_y = rect[1]
                    best = chwnd
            except Exception:
                pass

        return best

    def _find_save_button(self, hwnd, controls):
        """定位保存/确定按钮"""
        import win32gui
        import win32con

        # 按文本精确匹配
        for chwnd, cls, text in controls:
            if cls == "Button" and text.strip() in self.SAVE_BUTTON_TEXTS:
                if win32gui.IsWindowEnabled(chwnd) and win32gui.IsWindowVisible(chwnd):
                    return chwnd

        # 按默认按钮样式匹配
        for chwnd, cls, text in controls:
            if cls == "Button":
                try:
                    style = win32gui.GetWindowLong(chwnd, win32con.GWL_STYLE)
                    if style & win32con.BS_DEFPUSHBUTTON:
                        return chwnd
                except Exception:
                    pass

        # 按标准对话框 IDOK 兜底
        try:
            ok_btn = win32gui.GetDlgItem(hwnd, win32con.IDOK)
            if ok_btn:
                return ok_btn
        except Exception:
            pass

        return None

    def _type_path(self, output_path):
        """通过键盘输入文件路径"""
        try:
            from pynput.keyboard import Controller, Key
            keyboard = Controller()
            # 全选已有内容
            keyboard.press(Key.ctrl)
            keyboard.press('a')
            keyboard.release('a')
            keyboard.release(Key.ctrl)
            time.sleep(0.05)
            keyboard.type(output_path)
            time.sleep(0.05)
        except Exception as e:
            logger.debug(f"键盘输入失败: {e}")

    def _press_enter(self):
        """模拟按下回车键"""
        try:
            from pynput.keyboard import Controller, Key
            keyboard = Controller()
            keyboard.press(Key.enter)
            keyboard.release(Key.enter)
            time.sleep(0.05)
        except Exception as e:
            logger.debug(f"回车失败: {e}")

    def _fill_and_save(self, hwnd, output_path):
        """填写文件路径并点击保存按钮"""
        import win32gui
        import win32con

        self._activate_dialog(hwnd)
        time.sleep(0.15)

        controls = self._enum_all_children(hwnd)
        filename_hwnd = self._find_filename_edit(controls)
        save_hwnd = self._find_save_button(hwnd, controls)

        if not filename_hwnd:
            logger.warning("未找到文件名编辑框，尝试直接键盘输入")
            self._type_path(output_path)
            self._press_enter()
            return True

        # 聚焦文件名输入框
        try:
            win32gui.SetFocus(filename_hwnd)
        except Exception:
            pass
        time.sleep(0.05)

        # 尝试直接设置文本（最稳定）
        text_set = False
        try:
            win32gui.SendMessage(filename_hwnd, win32con.WM_SETTEXT, 0, output_path)
            text_set = True
        except Exception as e:
            logger.debug(f"WM_SETTEXT 失败: {e}")

        # 如果 WM_SETTEXT 失败（如控件不接受消息），回退键盘输入
        if not text_set:
            self._type_path(output_path)

        time.sleep(0.1)

        # 点击保存按钮或回车确认
        if save_hwnd:
            try:
                win32gui.SendMessage(save_hwnd, win32con.BM_CLICK, 0, 0)
            except Exception as e:
                logger.debug(f"BM_CLICK 失败: {e}")
                self._press_enter()
        else:
            self._press_enter()

        return True


class VirtualPrinterManager:
    """
    虚拟打印机管理器
    整合打印机管理、任务监控、PDF捕获
    """
    
    def __init__(self, on_pdf_captured=None):
        """
        初始化
        :param on_pdf_captured: PDF捕获回调(pdf_path, document_name)
        """
        self.spool_dir = _get_spool_dir()
        self.on_pdf_captured = on_pdf_captured
        self._monitor = None
        self._dialog_handler = None
        self._pending_jobs = {}
        self._lock = threading.Lock()
        
    def install(self):
        """安装虚拟打印机"""
        return create_virtual_printer()
        
    def uninstall(self):
        """卸载虚拟打印机"""
        self.stop()
        return remove_virtual_printer()
        
    def is_installed(self):
        """检查是否已安装"""
        return check_printer_exists()
        
    def start(self):
        """启动监控"""
        if self._monitor:
            return

        self._dialog_handler = DialogHandler(self.spool_dir)
        self._dialog_handler.start()  # 持续监听保存对话框

        self._monitor = PrintJobMonitor(on_new_job=self._on_new_job)
        self._monitor.start()

        # 启动文件监控线程，检测新生成的PDF
        self._file_monitor_thread = threading.Thread(target=self._file_monitor_loop, daemon=True)
        self._file_monitor_running = True
        self._file_monitor_thread.start()

        logger.info("虚拟打印机管理器已启动")
        
    def stop(self):
        """停止监控"""
        self._file_monitor_running = False
        if self._monitor:
            self._monitor.stop()
            self._monitor = None
        if self._dialog_handler:
            self._dialog_handler.stop()
            self._dialog_handler = None
        logger.info("虚拟打印机管理器已停止")
        
    def _on_new_job(self, job_id, document_name):
        """
        新打印任务回调
        记录任务信息，供文件监控关联PDF时使用
        """
        logger.info(f"处理新打印任务: {job_id} - {document_name}")

        with self._lock:
            self._pending_jobs[job_id] = {
                'document': document_name,
                'start_time': time.time(),
            }
    
    def _file_monitor_loop(self):
        """文件监控循环，检测新生成的PDF文件"""
        last_files = set()
        
        # 初始化文件列表
        if os.path.exists(self.spool_dir):
            last_files = set(os.listdir(self.spool_dir))
        
        while self._file_monitor_running:
            try:
                if os.path.exists(self.spool_dir):
                    current_files = set(os.listdir(self.spool_dir))
                    new_files = current_files - last_files
                    
                    for filename in new_files:
                        if filename.lower().endswith('.pdf'):
                            filepath = os.path.join(self.spool_dir, filename)
                            # 等待文件写入完成
                            if self._wait_file_stable(filepath):
                                logger.info(f"检测到新PDF文件: {filepath}")
                                self._handle_captured_pdf(filepath)
                    
                    last_files = current_files
                    
            except Exception as e:
                logger.debug(f"文件监控异常: {e}")
            
            time.sleep(1)
    
    def _wait_file_stable(self, filepath, timeout=30):
        """等待文件大小稳定（写入完成）"""
        last_size = -1
        stable_count = 0
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                if not os.path.exists(filepath):
                    return False
                current_size = os.path.getsize(filepath)
                if current_size == last_size and current_size > 0:
                    stable_count += 1
                    if stable_count >= 3:  # 连续3秒大小不变
                        return True
                else:
                    stable_count = 0
                    last_size = current_size
            except:
                pass
            time.sleep(1)
        
        return os.path.exists(filepath) and os.path.getsize(filepath) > 0
    
    def _handle_captured_pdf(self, pdf_path):
        """
        处理捕获到的PDF
        """
        try:
            filename = os.path.basename(pdf_path)
            document_name = "未知文档"

            # 1. 尝试从DialogHandler记录的已处理文件确认
            if self._dialog_handler:
                recent_files = self._dialog_handler.get_recent_files(clear=False)
                if pdf_path in recent_files:
                    logger.debug(f"文件 {filename} 已确认由对话框处理")

            # 2. 关联最近的打印任务名称
            with self._lock:
                now = time.time()
                # 清理超过5分钟的过期任务
                expired = [jid for jid, info in self._pending_jobs.items()
                           if now - info.get('start_time', 0) > 300]
                for jid in expired:
                    del self._pending_jobs[jid]

                # 找最近的任务
                if self._pending_jobs:
                    recent_job = min(self._pending_jobs.items(), key=lambda x: x[1].get('start_time', 0))
                    document_name = recent_job[1].get('document', document_name)
                    del self._pending_jobs[recent_job[0]]

            logger.info(f"PDF捕获完成: {filename} (文档: {document_name})")

            if self.on_pdf_captured:
                try:
                    self.on_pdf_captured(pdf_path, document_name)
                except Exception as e:
                    logger.error(f"PDF捕获回调异常: {e}")

        except Exception as e:
            logger.error(f"处理捕获PDF异常: {e}")
