import os
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import logging

logger = logging.getLogger(__name__)

FONT_FAMILY = "Microsoft YaHei UI"
FONT_TITLE = (FONT_FAMILY, 12, "bold")
FONT_NORMAL = (FONT_FAMILY, 11, "bold")
FONT_TABLE = (FONT_FAMILY, 11, "normal")
FONT_SMALL = (FONT_FAMILY, 10, "normal")
FONT_BIG_NUM = (FONT_FAMILY, 24, "bold")
FONT_ICON = (FONT_FAMILY, 22)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.api_client import PrintClient
from common.config import APP_NAME, APP_VERSION, load_config, save_config, get_config, set_config
from common.theme import get_theme, get_all_themes


class ClientGUI:
    def __init__(self):
        self.client = PrintClient()
        self.selected_files = []
        self._tray = None
        self._tray_visible = False
        self._real_quitting = False
        self.pages = {}
        self.current_page = None
        self.sidebar_buttons = {}
        self.sidebar_frames = {}
        self._theme_list = get_all_themes()
        self._preview_print_info = None
        self._preview_confirmed = False
        self._refresh_timer = None
        self._virtual_printer = None
        self._virtual_print_jobs = []
        self._vp_selected_printer_var = None
        self._vp_jobs_frame = None
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} - 打印客户端 v{APP_VERSION}")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        w = int(screen_w * 0.85)
        h = int(screen_h * 0.85)
        self.root.geometry(f"{w}x{h}+{int((screen_w-w)/2)}+{int((screen_h-h)/2)}")
        self.root.minsize(700, 520)
        self.root.state('zoomed')
        self._setup_style()
        self._create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if self.client.server_url and self.client.access_code:
            self.root.after(1000, self._try_auto_connect)
        self.root.after(500, self._run_self_check_async)
        self.root.after(2000, self._start_heartbeat)
        self.root.after(3000, self._schedule_refresh)

    def _start_heartbeat(self):
        try:
            self.client._start_heartbeat()
        except Exception as e:
            logger.debug(f"启动心跳失败: {e}")

    def _run_self_check_async(self):
        import threading
        threading.Thread(target=self._run_self_check, daemon=True).start()

    def _run_self_check(self):
        results = []
        errors = []
        def check(name, check_func, fix_func=None):
            try:
                result = check_func()
                if result is True:
                    results.append(f"✓ {name}")
                elif isinstance(result, str):
                    results.append(f"✓ {name}: {result}" if result else f"✓ {name}")
                else:
                    errors.append(f"✗ {name}: 检查失败")
            except Exception as e:
                if fix_func:
                    try:
                        fix_func()
                        results.append(f"✓ {name}: 已修复")
                    except Exception as fe:
                        errors.append(f"✗ {name}: {str(e)} (修复失败: {str(fe)})")
                else:
                    errors.append(f"✗ {name}: {str(e)}")
        check("Python版本", lambda: sys.version_info >= (3, 8))
        check("数据目录", lambda: os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")))
        try:
            from client.api_client import PrintClient
            check("API客户端模块", lambda: True)
        except Exception as e:
            errors.append(f"✗ API客户端模块: {str(e)}")
        try:
            import pystray
            check("系统托盘模块", lambda: True)
        except Exception as e:
            errors.append(f"✗ 系统托盘模块: {str(e)}")
        try:
            from PIL import Image
            check("PIL图像模块", lambda: True)
        except Exception as e:
            errors.append(f"✗ PIL图像模块: {str(e)}")
        try:
            import winreg
            check("Windows注册表访问", lambda: True)
        except Exception as e:
            errors.append(f"✗ Windows注册表访问: {str(e)}")
        try:
            import fitz
            check("PyMuPDF(PDF预览)", lambda: True)
        except Exception as e:
            errors.append(f"✗ PyMuPDF(PDF预览): {str(e)}")
        try:
            printers = self.client.get_local_printers()
            check(f"系统打印机", lambda: f"检测到 {len(printers)} 台")
        except Exception as e:
            errors.append(f"✗ 系统打印机检测: {str(e)}")
        print("\n=== 云印宝客户端 - 启动自检 ===")
        for r in results:
            print(r)
        if errors:
            print("\n错误:")
            for e in errors:
                print(e)
            print("\n警告: 存在错误，部分功能可能无法正常使用")
        else:
            print("\n✓ 所有检查通过")
        print("=" * 40 + "\n")

    def _schedule_refresh(self):
        try:
            if self.current_page == "tasks" and self.client.is_connected:
                self.refresh_tasks()
        except Exception:
            pass
        self._refresh_timer = self.root.after(5000, self._schedule_refresh)

    def _setup_style(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        style = ttk.Style()
        style.theme_use('clam')
        self.root.configure(bg=theme["main_bg"])
        style.configure("TLabel", font=FONT_NORMAL, background=theme["main_bg"], foreground=theme["text_primary"])
        style.configure("TButton", font=FONT_NORMAL, padding=(12, 5), background=theme["button_bg"], foreground=theme["button_text"], borderwidth=1, relief="solid")
        style.map("TButton", background=[("disabled", "#f5f7fa")], foreground=[("disabled", "#c0c4cc")], bordercolor=[("!active", theme["button_border"]), ("disabled", "#e4e7ed")], relief=[("pressed", "solid")])
        style.configure("Primary.TButton", font=FONT_NORMAL, padding=(12, 5), background=theme["primary"], foreground="#ffffff", borderwidth=1, relief="solid")
        style.map("Primary.TButton", background=[("disabled", "#a0cfff")], foreground=[("disabled", "white")])
        style.configure("Danger.TButton", font=FONT_NORMAL, padding=(12, 5), background=theme["danger_button_bg"], foreground=theme["danger_button_text"], borderwidth=1, relief="solid")
        style.map("Danger.TButton", bordercolor=[("!active", theme["danger_button_border"])])
        style.configure("Treeview", font=FONT_TABLE, rowheight=36, background=theme["card_bg"], fieldbackground=theme["card_bg"], foreground=theme["text_secondary"], borderwidth=1, relief="solid", bordercolor=theme["card_border"])
        style.configure("Treeview.Heading", font=FONT_NORMAL, background="#f5f7fa", foreground=theme["text_primary"], padding=8, borderwidth=1, relief="solid", bordercolor=theme["card_border"])
        style.map("Treeview", background=[("selected", "#ecf5ff")], foreground=[("selected", theme["primary"])], bordercolor=[("focus", theme["card_border"])])
        style.map("Treeview.Heading", background=[], foreground=[])
        style.configure("TFrame", background=theme["main_bg"])
        style.configure("Card.TFrame", background=theme["card_bg"], relief="flat")
        style.configure("TopBar.TFrame", background=theme["topbar_bg"], relief="flat")
        style.configure("Sidebar.TFrame", background=theme["sidebar_bg"], relief="flat", highlightthickness=1, highlightbackground=theme["sidebar_bg"])
        style.configure("Content.TFrame", background=theme["main_bg"], relief="flat")
        style.configure("TEntry", font=FONT_NORMAL, fieldbackground=theme["input_bg"], foreground=theme["text_primary"], bordercolor=theme["input_border"], borderwidth=1, padding=5, relief="solid")
        style.map("TEntry", bordercolor=[("focus", theme["primary"])], lightcolor=[("focus", theme["primary"])], darkcolor=[("focus", theme["primary"])])
        style.configure("TCombobox", font=FONT_NORMAL, fieldbackground=theme["input_bg"], background=theme["input_bg"], foreground=theme["text_primary"], bordercolor=theme["input_border"], borderwidth=1, padding=4, arrowcolor=theme["text_secondary"], relief="solid")
        style.map("TCombobox", bordercolor=[("focus", theme["primary"])])
        style.configure("TSpinbox", font=FONT_NORMAL, fieldbackground=theme["input_bg"], foreground=theme["text_primary"], bordercolor=theme["input_border"], borderwidth=1, padding=3, arrowcolor=theme["text_secondary"], relief="solid")
        style.configure("TRadiobutton", font=FONT_NORMAL, background=theme["card_bg"], foreground=theme["text_primary"], padding=(4, 2))
        style.map("TRadiobutton", foreground=[("active", theme["primary"])], indicatorcolor=[("selected", theme["primary"]), ("!selected", theme["input_border"])])
        style.configure("TCheckbutton", font=FONT_NORMAL, background=theme["card_bg"], foreground=theme["text_primary"], padding=(4, 2))
        style.map("TCheckbutton", foreground=[("active", theme["primary"])], indicatorcolor=[("selected", theme["primary"]), ("!selected", theme["input_border"])])
        style.configure("TScrollbar", background="#dcdfe6", troughcolor="#f5f7fa", borderwidth=0, arrowcolor="#c0c4cc", relief="flat")
        style.map("TScrollbar", background=[("active", "#b0b3b8")])