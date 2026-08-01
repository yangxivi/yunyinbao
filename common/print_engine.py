# -*- coding: utf-8 -*-
import os, sys, time, logging, subprocess, ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)

class PrintEngine:
    def __init__(self):
        self.win32api = None
        self.win32print = None
        self._sumatra_pdf_path = None
        self._load_win32_modules()
        self._detect_sumatra_pdf()

    def _load_win32_modules(self):
        try:
            import win32api
            self.win32api = win32api
        except ImportError:
            logger.debug('win32api unavailable')
        try:
            import win32print
            self.win32print = win32print
        except ImportError:
            logger.debug('win32print unavailable')

    def _detect_sumatra_pdf(self):
        try:
            import winreg
            possible = []
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            builtin = os.path.join(base_dir, 'tools', 'SumatraPDF.exe')
            if os.path.exists(builtin):
                possible.append(builtin)
            if getattr(sys, 'frozen', False):
                same = os.path.join(os.path.dirname(sys.executable), 'SumatraPDF.exe')
                if os.path.exists(same): possible.append(same)
            for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                for kp in [r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\SumatraPDF.exe',
                           r'SOFTWARE\SumatraPDF']:
                    try:
                        k = winreg.OpenKey(root, kp)
                        v, _ = winreg.QueryValueEx(k, '')
                        if v and os.path.exists(v): possible.append(v)
                        winreg.CloseKey(k)
                    except Exception:
                        pass
            for p in [r'C:\Program Files\SumatraPDF\SumatraPDF.exe',
                      r'C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe']:
                if os.path.exists(p): possible.append(p)
            if possible:
                self._sumatra_pdf_path = possible[0]
                logger.info('SumatraPDF found: ' + self._sumatra_pdf_path)
            else:
                logger.info('SumatraPDF not found; will fallback to system default')
        except Exception as e:
            logger.warning('SumatraPDF detect failed: ' + str(e))

    def _ctypes_shell_execute(self, verb, file_path, params=None, show_cmd=0):
        try:
            r = ctypes.windll.shell32.ShellExecuteW(None, verb, file_path, params, None, show_cmd)
            if r > 32: return True, None
            return False, 'ShellExecute err ' + str(r)
        except Exception as e:
            return False, str(e)

    def _shell_printto(self, file_path, printer_name):
        pp = '"' + printer_name + '"'
        if self.win32api:
            try:
                self.win32api.ShellExecute(0, 'printto', file_path, pp, '.', 0)
                return True, 'win32api printto'
            except Exception as e:
                logger.warning('win32api printto failed: ' + str(e))
        ok, err = self._ctypes_shell_execute('printto', file_path, pp, 0)
        if ok: return True, 'ctypes printto'
        return False, err

    def _shell_print(self, file_path):
        if self.win32api:
            try:
                self.win32api.ShellExecute(0, 'print', file_path, None, '.', 0)
                return True, 'win32api print'
            except Exception as e:
                logger.warning('win32api print failed: ' + str(e))
        ok, err = self._ctypes_shell_execute('print', file_path, None, 0)
        if ok: return True, 'ctypes print'
        return False, err

    def _set_default_printer(self, printer_name):
        if self.win32print:
            try:
                self.win32print.SetDefaultPrinter(printer_name)
                return True
            except Exception as e:
                logger.warning('SetDefaultPrinter failed: ' + str(e))
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r'Software\Microsoft\Windows NT\CurrentVersion\Windows',
                               0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(k, 'Device', 0, winreg.REG_SZ,
                              printer_name + ',winspool,Ne00:')
            winreg.CloseKey(k)
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            r = ctypes.c_long()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, 'windows',
                SMTO_ABORTIFHUNG, 5000, ctypes.byref(r))
            return True
        except Exception as e:
            logger.warning('registry default printer failed: ' + str(e))
        return False

    def get_printers(self):
        printers = []
        if self.win32print:
            try:
                flags = self.win32print.PRINTER_ENUM_LOCAL | self.win32print.PRINTER_ENUM_CONNECTIONS
                for p in self.win32print.EnumPrinters(flags):
                    name = p[2] if isinstance(p, tuple) else p.get('pPrinterName', str(p))
                    printers.append(name)
                return printers
            except Exception as e:
                logger.error('EnumPrinters win32 failed: ' + str(e))
        try:
            PRINTER_ENUM_LOCAL = 0x00000002
            PRINTER_ENUM_CONNECTIONS = 0x00000004
            flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
            class PI(ctypes.Structure):
                _fields_ = [('Flags', wintypes.DWORD), ('pDescription', wintypes.LPWSTR),
                            ('pName', wintypes.LPWSTR), ('pComment', wintypes.LPWSTR)]
            needed = wintypes.DWORD(0); returned = wintypes.DWORD(0)
            winspool = ctypes.windll.winspool.drv
            winspool.EnumPrintersW(flags, None, 1, None, 0, ctypes.byref(needed), ctypes.byref(returned))
            if needed.value > 0:
                buf = ctypes.create_string_buffer(needed.value)
                if winspool.EnumPrintersW(flags, None, 1, buf, needed, ctypes.byref(needed), ctypes.byref(returned)):
                    for i in range(returned.value):
                        info = PI.from_buffer_copy(buf, ctypes.sizeof(PI) * i)
                        if info.pName: printers.append(info.pName)
        except Exception as e:
            logger.error('ctypes EnumPrinters failed: ' + str(e))
        return printers

    def get_default_printer(self):
        if self.win32print:
            try: return self.win32print.GetDefaultPrinter()
            except Exception: pass
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows NT\CurrentVersion\Windows')
            device, _ = winreg.QueryValueEx(k, 'Device')
            winreg.CloseKey(k)
            return device.split(',')[0] if device else None
        except Exception:
            return None

    def _print_pdf_sumatra(self, file_path, printer_name, copies=1, color_mode='black',
                           duplex='simplex', paper_size='A4', page_range=None, orientation='portrait'):
        if not self._sumatra_pdf_path:
            return False, 'SumatraPDF not available'
        try:
            parts = []
            if copies and copies > 1: parts.append(str(copies) + 'x')
            parts.append('color' if color_mode == 'color' else 'monochrome')
            dm = {'simplex': 'duplex:simplex', 'duplex_long': 'duplex:long', 'duplex_short': 'duplex:short'}
            parts.append(dm.get(duplex, 'duplex:simplex'))
            if paper_size and paper_size != 'A4': parts.append('paper:' + paper_size)
            parts.append('landscape' if orientation == 'landscape' else 'portrait')
            if page_range: parts.append('pages:' + page_range)
            settings = ','.join(parts)
            args = [self._sumatra_pdf_path, '-print-to', printer_name,
                    '-print-settings', settings, '-silent', '-exit-when-done', file_path]
            r = subprocess.run(args, capture_output=True, text=True, timeout=60,
                               creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
            if r.returncode == 0:
                return True, 'SumatraPDF printed ' + str(copies) + 'x'
            return False, 'SumatraPDF rc=' + str(r.returncode)
        except Exception as e:
            return False, 'SumatraPDF exception: ' + str(e)

    def print_pdf(self, file_path, printer_name, copies=1, color_mode='black',
                  duplex='simplex', paper_size='A4', page_range=None, orientation='portrait'):
        logger.info('Print: ' + file_path + ' -> ' + printer_name + ', copies=' + str(copies))
        if not os.path.exists(file_path): return False, 'File not found'
        if not printer_name: return False, 'No printer specified'
        errors = []
        if self._sumatra_pdf_path:
            try:
                ok, msg = self._print_pdf_sumatra(file_path, printer_name, copies, color_mode,
                                                  duplex, paper_size, page_range, orientation)
                if ok: return True, msg
                errors.append('SumatraPDF: ' + str(msg))
            except Exception as e:
                errors.append('SumatraPDF ex: ' + str(e))
        ok, msg = self._shell_printto(file_path, printer_name)
        if ok:
            time.sleep(1)
            for _ in range(copies - 1):
                self._shell_printto(file_path, printer_name)
                time.sleep(0.5)
            return True, 'printto ' + str(copies) + 'x'
        errors.append('printto: ' + str(msg))
        if self._set_default_printer(printer_name):
            time.sleep(0.5)
            ok, msg = self._shell_print(file_path)
            if ok:
                time.sleep(1)
                for _ in range(copies - 1):
                    self._shell_print(file_path)
                    time.sleep(0.5)
                return True, 'default-print '
 + str(copies) + 'x'
            errors.append('default-print: ' + str(msg))
        try:
            ps = ('Start-Process -FilePath "' + file_path + '" '
                  '-Verb Printto -ArgumentList \'"' + printer_name + '"\' -WindowStyle Hidden -ErrorAction Stop')
            r = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                               capture_output=True, text=True, timeout=20,
                               creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
            if r.returncode == 0:
                time.sleep(1)
                for _ in range(copies - 1):
                    subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                                   capture_output=True, text=True, timeout=20,
                                   creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
                    time.sleep(0.5)
                return True, 'powershell printto ' + str(copies) + 'x'
            errors.append('PS: ' + (r.stderr or '').strip()[:200])
        except Exception as e:
            errors.append('PS ex: ' + str(e))
        try:
            os.startfile(file_path, 'print')
            time.sleep(2)
            for _ in range(copies - 1):
                os.startfile(file_path, 'print')
                time.sleep(0.5)
            return True, 'os.startfile print '
 + str(copies) + 'x (default printer)'
        except Exception as e:
            errors.append('os.startfile: ' + str(e))
        return False, 'All methods failed. ' + ' | '.join(errors[-4:])
