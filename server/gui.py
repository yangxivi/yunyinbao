import os
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox
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

from server.api_server import create_app, start_server, stop_server, get_server_status
from server.printer_manager import PrinterManager
from server.task_scheduler import TaskScheduler
from server.device_manager import DeviceManager
from server.user_manager import UserManager
from server.print_engine import PrintEngine
from server.env_check import EnvChecker
from common.config import APP_NAME, APP_VERSION, load_config, save_config, get_config, set_config
from common.theme import get_theme, get_all_themes
from common import database as db_module
from common.database import init_db, get_task_count, get_printer_count, get_device_count, clear_tasks as db_clear_tasks, date_str


def _check_single_instance():
    import ctypes
    from ctypes import wintypes
    mutex_name = "Global\\YunYinBao_Server_Mutex_2026"
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, wintypes.BOOL(False), mutex_name)
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(mutex)
        return False
    return True


class ServerGUI:
    def __init__(self):
        self.pages = {}
        self.current_page = None
        self.sidebar_buttons = {}
        self._theme_list = get_all_themes()
        self.server_thread = None
        self.server_app = None
        self.printer_mgr = None
        self.task_scheduler = None
        self.device_mgr = None
        self.user_mgr = None
        self.print_engine = None
        self.env_checker = None
        self._tray = None
        self._tray_visible = False
        self._real_quitting = False
        self._refresh_timer = None

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} - 打印服务端 v{APP_VERSION}")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        w = int(screen_w * 0.88)
        h = int(screen_h * 0.85)
        self.root.geometry(f"{w}x{h}+{int((screen_w-w)/2)}+{int((screen_h-h)/2)}")
        self.root.minsize(800, 560)
        self.root.state('zoomed')

        self._setup_style()
        self._create_widgets()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.root.after(50, self._init_backend)
        self.root.after(100, self._run_self_check)
        self.root.after(2000, self._schedule_refresh)

    def _init_backend(self):
        db_module.init_db()
        self.printer_mgr = PrinterManager(db_module)
        self.print_engine = PrintEngine()
        self.task_scheduler = TaskScheduler(db_module, self.printer_mgr, self.print_engine)
        self.user_mgr = UserManager(db_module)
        self.env_checker = EnvChecker()
        config = load_config()
        self.device_mgr = DeviceManager(db_module, config)
        self._auto_start_server()
        self.root.after(200, self._refresh_current_page)

    def _run_self_check(self):
        results = []
        errors = []
        def check(name, check_func, fix_func=None):
            try:
                result = check_func()
                if result is True: results.append(f"✓ {name}")
                elif isinstance(result, str): results.append(f"✓ {name}: {result}" if result else f"✓ {name}")
                else: errors.append(f"✗ {name}: 检查失败")
            except Exception as e:
                if fix_func:
                    try: fix_func(); results.append(f"✓ {name}: 已修复")
                    except Exception as fe: errors.append(f"✗ {name}: {str(e)} (修复失败: {str(fe)})")
                else: errors.append(f"✗ {name}: {str(e)}")
        check("Python版本", lambda: sys.version_info >= (3, 8))
        check("数据目录", lambda: os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")))
        try: from server.printer_manager import PrinterManager; check("打印机管理器模块", lambda: True)
        except Exception as e: errors.append(f"✗ 打印机管理器模块: {str(e)}")
        try: from server.api_server import create_app; check("API服务模块", lambda: True)
        except Exception as e: errors.append(f"✗ API服务模块: {str(e)}")
        try: import pystray; check("系统托盘模块", lambda: True)
        except Exception as e: errors.append(f"✗ 系统托盘模块: {str(e)}")
        try: from PIL import Image; check("PIL图像模块", lambda: True)
        except Exception as e: errors.append(f"✗ PIL图像模块: {str(e)}")
        try: import winreg; check("Windows注册表访问", lambda: True)
        except Exception as e: errors.append(f"✗ Windows注册表访问: {str(e)}")
        try: db_module.init_db(); check("数据库连接", lambda: True)
        except Exception as e: errors.append(f"✗ 数据库连接: {str(e)}")
        try:
            printers = PrinterManager(db_module).get_local_printers()
            check(f"系统打印机", lambda: f"检测到 {len(printers)} 台打印机")
        except Exception as e: errors.append(f"✗ 系统打印机检测: {str(e)}")
        print("\n=== 云印宝服务端 - 启动自检 ===")
        for r in results: print(r)
        if errors:
            print("\n错误:")
            for e in errors: print(e)
            print("\n警告: 存在错误，部分功能可能无法正常使用")
        else: print("\n✓ 所有检查通过")
        print("=" * 40 + "\n")

    def _auto_start_server(self): self.start_server()

    def _schedule_refresh(self):
        try:
            if self.current_page in ("dashboard", "tasks", "devices", "users", "printers"):
                self._refresh_current_page()
        except Exception: pass
        self._refresh_timer = self.root.after(5000, self._schedule_refresh)

    def _refresh_current_page(self):
        if not self.printer_mgr: return
        if self.current_page == "dashboard": self._refresh_dashboard()
        elif self.current_page == "tasks": self._refresh_tasks_data()
        elif self.current_page == "devices": self._refresh_devices_data()
        elif self.current_page == "users": self._refresh_users_data()
        elif self.current_page == "printers": self._refresh_printers_data()

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

    def _create_widgets(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        self.theme = theme
        main_frame = tk.Frame(self.root, bg=theme["main_bg"])
        main_frame.pack(fill=tk.BOTH, expand=True)
        top_frame = tk.Frame(main_frame, bg=theme["topbar_bg"], height=50)
        top_frame.pack(fill=tk.X); top_frame.pack_propagate(False)
        tk.Label(top_frame, text=f"☁  {APP_NAME} - 服务端", font=FONT_TITLE, fg=theme["topbar_text"], bg=theme["topbar_bg"]).pack(side=tk.LEFT, padx=20, pady=10)
        self.status_label = tk.Label(top_frame, text="● 正在启动...", font=FONT_NORMAL, fg="#e6a23c", bg=theme["topbar_bg"])
        self.status_label.pack(side=tk.LEFT, padx=5, pady=10)
        self.server_label = tk.Label(top_frame, text="", font=FONT_NORMAL, fg=theme["topbar_text"], bg=theme["topbar_bg"])
        self.server_label.pack(side=tk.LEFT, padx=15, pady=10)
        body_frame = tk.Frame(main_frame, bg=theme["main_bg"])
        body_frame.pack(fill=tk.BOTH, expand=True)
        self.sidebar = tk.Frame(body_frame, bg=theme["sidebar_bg"], width=180, highlightthickness=1, highlightbackground=theme["card_border"])
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y); self.sidebar.pack_propagate(False)
        self.content_frame = tk.Frame(body_frame, bg=theme["main_bg"])
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=12)
        self._create_sidebar_menu()
        self._create_content_pages()
        self._switch_page("dashboard")

    def _create_sidebar_menu(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        self.sidebar_buttons = {}; self.sidebar_frames = {}
        menu_items = [("dashboard", "概览"), ("printers", "打印机管理"), ("tasks", "打印任务"), ("devices", "设备管理"), ("users", "用户管理"), ("settings", "设置")]
        for idx, (key, text) in enumerate(menu_items):
            item_frame = tk.Frame(self.sidebar, bg=theme["sidebar_bg"], highlightthickness=0)
            item_frame.pack(fill=tk.X, padx=0, pady=0); item_frame.pack_propagate(False); item_frame.config(height=44)
            text_label = tk.Label(item_frame, text=text, font=FONT_NORMAL, bg=theme["sidebar_bg"], fg=theme["sidebar_text"], anchor="w", cursor="hand2")
            text_label.place(relx=0, rely=0.5, x=20, anchor="w")
            self.sidebar_buttons[key] = {"frame": item_frame, "text": text_label}
            self.sidebar_frames[key] = item_frame
            def _on_click(e, k=key): self._switch_page(k)
            def _on_enter(e, k=key):
                if self.current_page != k:
                    th = get_theme(get_config("theme", "tech_blue"))
                    for w in (self.sidebar_buttons[k]["frame"], self.sidebar_buttons[k]["text"]): w.configure(bg=th["sidebar_hover"])
            def _on_leave(e, k=key):
                if self.current_page != k:
                    th = get_theme(get_config("theme", "tech_blue"))
                    for w in (self.sidebar_buttons[k]["frame"], self.sidebar_buttons[k]["text"]): w.configure(bg=th["sidebar_bg"])
            for w in (item_frame, text_label):
                w.bind("<Button-1>", _on_click); w.bind("<Enter>", _on_enter); w.bind("<Leave>", _on_leave)