# -*- coding: utf-8 -*-
import os, sys, time, subprocess, threading, logging, tempfile, shutil
from pathlib import Path

logger = logging.getLogger(__name__)

PRINTER_NAME = "云印宝打印机"
DRIVER_NAME = "Microsoft Print To PDF"
SPOOL_DIR_NAME = "yunyinbao_spool"

def _run_powershell(command, timeout=15):
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command',
                           '$OutputEncoding=[System.Text.Encoding]::UTF8;'
                           '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;'
                           + command],
                          capture_output=True, timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        stdout = r.stdout.decode('utf-8', errors='replace').strip()
        stderr = r.stderr.decode('utf-8', errors='replace').strip()
        return r.returncode == 0, stdout, stderr
    except Exception as e:
        return False, "", str(e)

def _get_spool_dir():
    base = os.path.join(os.environ.get('LOCALAPPDATA', tempfile.gettempdir()), 'YunYinBao')
    d = os.path.join(base, SPOOL_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d

def check_driver_exists():
    ok, out, _ = _run_powershell(
        'Get-PrinterDriver -Name "Microsoft Print To PDF" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name')
    return ok and out == "Microsoft Print To PDF"

def check_printer_exists():
    ok, out, _ = _run_powershell(
        'Get-Printer -Name "' + PRINTER_NAME + '" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name')
    return ok and out == PRINTER_NAME

def create_virtual_printer():
    if check_printer_exists(): return True, "虚拟打印机已存在"
    if not check_driver_exists():
        return False, "Microsoft Print to PDF驱动未安装，请先启用Windows功能"
    ok, out, err = _run_powershell(
        'Add-Printer -Name "' + PRINTER_NAME + '" -DriverName "' + DRIVER_NAME + '" -PortName "FILE:"')
    if ok or check_printer_exists():
        logger.info("虚拟打印机创建成功")
        return True, "虚拟打印机创建成功"
    return False, "创建失败"

def remove_virtual_printer():
    _run_powershell('Remove-Printer -Name "' + PRINTER_NAME + '" -ErrorAction SilentlyContinue')
    return not check_printer_exists()

class PrintJobMonitor:
    def __init__(self, on_new_job=None):
        self.printer_name = PRINTER_NAME
        self.on_new_job = on_new_job
        self._running = False
        self._monitor_thread = None
        self._seen_jobs = set()
        self._stop_event = threading.Event()

    def start(self):
        if self._running: return
        self._running = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3)

    def _monitor_loop(self):
        while self._running and not self._stop_event.is_set():
            try: self._check_jobs()
            except Exception as e: logger.debug('check jobs ex: ' + str(e))
            self._stop_event.wait(2)

    def _check_jobs(self):
        try:
            import win32print
            h = win32print.OpenPrinter(self.printer_name)
            try:
                jobs = win32print.EnumJobs(h, 0, -1, 1)
                for job in jobs:
                    jid = job.get('JobId', 0)
                    doc = job.get('pDocument', 'unknown')
                    if jid not in self._seen_jobs:
                        self._seen_jobs.add(jid)
                        if self.on_new_job:
                            try: self.on_new_job(jid, doc)
                            except Exception as e: logger.error('on_new_job ex: ' + str(e))
                active = {job.get('JobId', 0) for job in jobs}
                self._seen_jobs = self._seen_jobs & active
            finally:
                win32print.ClosePrinter(h)
        except ImportError:
            self._running = False
        except Exception as e:
            logger.debug('EnumJobs ex: ' + str(e))

class DialogHandler:
    SAVE_DIALOG_TITLES = {
        '将打印输出另存为','另存为','保存','Save Print Output As','Save As','Save',
        'Print to File','打印到文件'}
    SAVE_BUTTON_TEXTS = {
        '保存','保存(&S)','Save','&Save','OK','确定','确定(&O)','&确定','保存(S)'}

    def __init__(self, output_dir=None):
        self.output_dir = output_dir or _get_spool_dir()
        self._handler_thread = None
        self._stop_event = threading.Event()
        self._handled_dialogs = {}
        self._handling = set()
        self._handled_files = []
        self._lock = threading.Lock()
        self._counter = 0
        os.makedirs(self.output_dir, exist_ok=True)

    def get_recent_files(self, clear=True):
        with self._lock:
            fs = list(self._handled_files)
            if clear: self._handled_files = []
            return fs

    def start(self):
        if self._handler_thread and self._handler_thread.is_alive(): return
        self._stop_event.clear()
        self._handler_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._handler_thread.start()

    def stop(self):
        self._stop_event.set()
        if self._handler_thread:
            self._handler_thread.join(timeout=3)

    def _next_filename(self):
        with self._lock:
            self._counter += 1
            c = self._counter
        ts = int(time.time() * 1000)
        return os.path.join(self.output_dir, 'printjob_' + str(ts) + '_' + str(c) + '.pdf')

    def _listen_loop(self):
        while not self._stop_event.is_set():
            try: self._scan_and_handle()
            except Exception as e: logger.debug('scan dialog ex: ' + str(e))
            self._stop_event.wait(0.3)

    def _cleanup(self):
        now = time.time()
        expired = [hwnd for hwnd, ts in self._handled_dialogs.items() if now - ts > 60]
        for hwnd in expired:
            self._handled_dialogs.pop(hwnd, None)

    def _scan_and_handle(self):
        try:
            import win32gui
        except ImportError:
            return
        with self._lock:
            self._cleanup()
        dialogs = self._find_save_dialogs()
        for hwnd in dialogs:
            with self._lock:
                if hwnd in self._handled_dialogs or hwnd in self._handling:
                    continue
                self._handling.add(hwnd)
            output_path = self._next_filename()
            handled = False
            try:
                if self._fill_and_save(hwnd, output_path):
                    with self._lock:
                        self._handled_files.append(output_path)
                        self._handled_dialogs[hwnd] = time.time()
                    handled = True
            except Exception as e:
                logger.debug('handle dialog ex: ' + str(e))
            finally:
                with self._lock:
                    self._handling.discard(hwnd)
                    if handled:
                        self._handled_dialogs[hwnd] = time.time()

    def _find_save_dialogs(self):
        import win32gui
        dialogs = []
        def enum_proc(hwnd, result):
            if not win32gui.IsWindowVisible(hwnd): return True
            title = win32gui.GetWindowText(hwnd)
            if not title: return True
            cls = win32gui.GetClassName(hwnd)
            if cls != '#32770': return True
            if title.strip() in self.SAVE_DIALOG_TITLES:
                result.append(hwnd)
                return True
            lower = title.lower()
            if any(k in lower for k in ['将打印输出','print output','另存为','save as','保存']):
                result.append(hwnd)
            return True
        win32gui.EnumWindows(enum_proc, dialogs)
        return dialogs

    def _enum_all_children(self, hwnd):
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
        try:
            import win32gui, win32api
            fg = win32gui.GetForegroundWindow()
            if fg and fg != hwnd:
                cur_tid = win32api.GetCurrentThreadId()
                fg_tid = win32gui.GetWindowThreadProcessId(fg)[0]
                if cur_tid != fg_tid:
                    win32gui.AttachThreadInput(cur_tid, fg_tid, True)
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                        win32gui.BringWindowToTop(hwnd)
                    finally:
                        win32gui.AttachThreadInput(cur_tid, fg_tid, False)
                else:
                    win32gui.SetForegroundWindow(hwnd)
                    win32gui.BringWindowToTop(hwnd)
        except Exception as e:
            logger.debug('activate failed: ' + str(e))

    def _find_filename_edit(self, controls):
        import win32gui
        candidates = [hwnd for hwnd, cls, text in controls
                      if (cls == 'Edit' or cls.endswith('Edit'))
                      and win32gui.IsWindowEnabled(hwnd) and win32gui.IsWindowVisible(hwnd)]
        if not candidates: return None
        combo = []
        for chwnd in candidates:
            try:
                parent = win32gui.GetParent(chwnd)
                if parent:
                    pcls = win32gui.GetClassName(parent)
                    if 'ComboBox' in pcls:
                        rect = win32gui.GetWindowRect(chwnd)
                        combo.append((chwnd, rect[1]))
            except Exception:
                pass
        if combo:
            combo.sort(key=lambda x: x[1])
            return combo[-1][0]
        best = None; max_y = -1
        for chwnd in candidates:
            try:
                rect = win32gui.GetWindowRect(chwnd)
                if rect[1] > max_y:
                    max_y = rect[1]; best = chwnd
            except Exception:
                pass
        return best

    def _find_save_button(self, hwnd, controls):
        import win32gui, win32con
        for chwnd, cls, text in controls:
            if cls == 'Button' and text.strip() in self.SAVE_BUTTON_TEXTS:
                if win32gui.IsWindowEnabled(chwnd) and win32gui.IsWindowVisible(chwnd):
                    return chwnd
        for chwnd, cls, text in controls:
            if cls == 'Button':
                try:
                    style = win32gui.GetWindowLong(chwnd, win32con.GWL_STYLE)
                    if style & win32con.BS_DEFPUSHBUTTON:
                        return chwnd
                except Exception:
                    pass
        try:
            import win32gui, win32con
            ok = win32gui.GetDlgItem(hwnd, win32con.IDOK)
            if ok: return ok
        except Exception:
            pass
        return None

    def _type_path(self, output_path):
        try:
            from pynput.keyboard import Controller, Key
            kb = Controller()
            kb.press(Key.ctrl); kb.press('a'); kb.release('a'); kb.release(Key.ctrl)
            time.sleep(0.05); kb.type(output_path); time.sleep(0.05)
        except Exception as e:
            logger.debug('type_path failed: ' + str(e))

    def _press_enter(self):
        try:
            from pynput.keyboard import Controller, Key
            kb = Controller()
            kb.press(Key.enter); kb.release(Key.enter); time.sleep(0.05)
        except Exception as e:
            logger.debug('enter failed: ' + str(e))

    def _fill_and_save(self, hwnd, output_path):
        import win32gui, win32con
        self._activate_dialog(hwnd)
        time.sleep(0.15)
        controls = self._enum_all_children(hwnd)
        filename_hwnd = self._find_filename_edit(controls)
        save_hwnd = self._find_save_button(hwnd, controls)
        if not filename_hwnd:
            self._type_path(output_path); self._press_enter(); return True
        try: win32gui.SetFocus(filename_hwnd)
        except Exception: pass
        time.sleep(0.05)
        try:
            win32gui.SendMessage(filename_hwnd, win32con.WM_SETTEXT, 0, output_path)
        except Exception:
            self._type_path(output_path)
        time.sleep(0.1)
        if save_hwnd:
            try:
                win32gui.SendMessage(save_hwnd, win32con.BM_CLICK, 0, 0)
            except Exception:
                self._press_enter()
        else:
            self._press_enter()
        return True

class VirtualPrinterManager:
    def __init__(self, on_pdf_captured=None):
        self.spool_dir = _get_spool_dir()
        self.on_pdf_captured = on_pdf_captured
        self._monitor = None
        self._dialog_handler = None
        self._pending_jobs = {}
        self._lock = threading.Lock()

    def install(self): return create_virtual_printer()
    def uninstall(self):
        self.stop(); return remove_virtual_printer()
    def is_installed(self): return check_printer_exists()

    def start(self):
        if self._monitor: return
        self._dialog_handler = DialogHandler(self.spool_dir)
        self._dialog_handler.start()
        self._monitor = PrintJobMonitor(on_new_job=self._on_new_job)
        self._monitor.start()
        self._file_monitor_running = True
        self._file_monitor_thread = threading.Thread(target=self._file_monitor_loop, daemon=True)
        self._file_monitor_thread.start()

    def stop(self):
        self._file_monitor_running = False
        if self._monitor:
            self._monitor.stop(); self._monitor = None
        if self._dialog_handler:
            self._dialog_handler.stop(); self._dialog_handler = None

    def _on_new_job(self, job_id, document_name):
        with self._lock:
            self._pending_jobs[job_id] = {'document': document_name, 'start_time': time.time()}

    def _file_monitor_loop(self):
        last_files = set()
        if os.path.exists(self.spool_dir):
            last_files = set(os.listdir(self.spool_dir))
        while self._file_monitor_running:
            try:
                if os.path.exists(self.spool_dir):
                    current_files = set(os.listdir(self.spool_dir))
                    new_files = current_files - last_files
                    for fn in new_files:
                        if fn.lower().endswith('.pdf'):
                            fp = os.path.join(self.spool_dir, fn)
                            if self._wait_file_stable(fp):
                                self._handle_captured_pdf(fp)
                    last_files = current_files
            except Exception as e:
                logger.debug('file monitor ex: ' + str(e))
            time.sleep(1)

    def _wait_file_stable(self, filepath, timeout=30):
        last_size = -1; stable = 0; start = time.time()
        while time.time() - start < timeout:
            try:
                if not os.path.exists(filepath): return False
                cur = os.path.getsize(filepath)
                if cur == last_size and cur > 0:
                    stable += 1
                    if stable >= 3: return True
                else:
                    stable = 0; last_size = cur
            except Exception:
                pass
            time.sleep(1)
        return os.path.exists(filepath) and os.path.getsize(filepath) > 0

    def _handle_captured_pdf(self, pdf_path):
        try:
            document_name = 'unknown'
            with self._lock:
                now = time.time()
                expired = [jid for jid, info in self._pending_jobs.items()
                           if now - info.get('start_time', 0) > 300]
                for jid in expired:
                    del self._pending_jobs[jid]
                if self._pending_jobs:
                    recent = min(self._pending_jobs.items(), key=lambda x: x[1].get('start_time', 0))
                    document_name = recent[1].get('document', document_name)
                    del self._pending_jobs[recent[0]]
            if self.on_pdf_captured:
                try:
                    self.on_pdf_captured(pdf_path, document_name)
                except Exception as e:
                    logger.error('callback ex: ' + str(e))
        except Exception as e:
            logger.error('handle pdf ex: ' + str(e))
