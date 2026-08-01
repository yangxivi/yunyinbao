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
            from server.printer_manager import PrinterManager
            check("打印机管理器模块", lambda: True)
        except Exception as e:
            errors.append(f"✗ 打印机管理器模块: {str(e)}")

        try:
            from server.api_server import create_app
            check("API服务模块", lambda: True)
        except Exception as e:
            errors.append(f"✗ API服务模块: {str(e)}")

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
            db_module.init_db()
            check("数据库连接", lambda: True)
        except Exception as e:
            errors.append(f"✗ 数据库连接: {str(e)}")

        try:
            printers = PrinterManager(db_module).get_local_printers()
            check(f"系统打印机", lambda: f"检测到 {len(printers)} 台打印机")
        except Exception as e:
            errors.append(f"✗ 系统打印机检测: {str(e)}")

        print("\n=== 云印宝服务端 - 启动自检 ===")
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

    def _auto_start_server(self):
        self.start_server()

    def _schedule_refresh(self):
        try:
            if self.current_page in ("dashboard", "tasks", "devices", "users", "printers"):
                self._refresh_current_page()
        except Exception:
            pass
        self._refresh_timer = self.root.after(5000, self._schedule_refresh)

    def _refresh_current_page(self):
        if not self.printer_mgr:
            return
        if self.current_page == "dashboard":
            self._refresh_dashboard()
        elif self.current_page == "tasks":
            self._refresh_tasks_data()
        elif self.current_page == "devices":
            self._refresh_devices_data()
        elif self.current_page == "users":
            self._refresh_users_data()
        elif self.current_page == "printers":
            self._refresh_printers_data()

    def _setup_style(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        style = ttk.Style()
        style.theme_use('clam')

        font_family = FONT_FAMILY
        font_size = 11
        font_weight = "bold"

        self.root.configure(bg=theme["main_bg"])

        style.configure("TLabel",
                       font=FONT_NORMAL,
                       background=theme["main_bg"],
                       foreground=theme["text_primary"])

        style.configure("TButton",
                       font=FONT_NORMAL,
                       padding=(12, 5),
                       background=theme["button_bg"],
                       foreground=theme["button_text"],
                       borderwidth=1,
                       relief="solid")
        style.map("TButton",
                 background=[("disabled", "#f5f7fa")],
                 foreground=[("disabled", "#c0c4cc")],
                 bordercolor=[("!active", theme["button_border"]), ("disabled", "#e4e7ed")],
                 relief=[("pressed", "solid")])

        style.configure("Primary.TButton",
                       font=FONT_NORMAL,
                       padding=(12, 5),
                       background=theme["primary"],
                       foreground="#ffffff",
                       borderwidth=1,
                       relief="solid")
        style.map("Primary.TButton",
                 background=[("disabled", "#a0cfff")],
                 foreground=[("disabled", "white")])

        style.configure("Danger.TButton",
                       font=FONT_NORMAL,
                       padding=(12, 5),
                       background=theme["danger_button_bg"],
                       foreground=theme["danger_button_text"],
                       borderwidth=1,
                       relief="solid")
        style.map("Danger.TButton",
                 bordercolor=[("!active", theme["danger_button_border"])])

        style.configure("Treeview",
                       font=FONT_TABLE,
                       rowheight=36,
                       background=theme["card_bg"],
                       fieldbackground=theme["card_bg"],
                       foreground=theme["text_secondary"],
                       borderwidth=1,
                       relief="solid",
                       bordercolor=theme["card_border"])
        style.configure("Treeview.Heading",
                       font=FONT_NORMAL,
                       background="#f5f7fa",
                       foreground=theme["text_primary"],
                       padding=8,
                       borderwidth=1,
                       relief="solid",
                       bordercolor=theme["card_border"])
        style.map("Treeview",
                 background=[("selected", "#ecf5ff")],
                 foreground=[("selected", theme["primary"])],
                 bordercolor=[("focus", theme["card_border"])])
        style.map("Treeview.Heading", background=[], foreground=[])

        style.configure("TFrame", background=theme["main_bg"])
        style.configure("Card.TFrame", background=theme["card_bg"], relief="flat")
        style.configure("TopBar.TFrame", background=theme["topbar_bg"], relief="flat")
        style.configure("Sidebar.TFrame", background=theme["sidebar_bg"], relief="flat", highlightthickness=1, highlightbackground=theme["sidebar_bg"])
        style.configure("Content.TFrame", background=theme["main_bg"], relief="flat")

        style.configure("TEntry",
                       font=FONT_NORMAL,
                       fieldbackground=theme["input_bg"],
                       foreground=theme["text_primary"],
                       bordercolor=theme["input_border"],
                       borderwidth=1,
                       padding=5,
                       relief="solid")
        style.map("TEntry",
                 bordercolor=[("focus", theme["primary"])],
                 lightcolor=[("focus", theme["primary"])],
                 darkcolor=[("focus", theme["primary"])])

        style.configure("TCombobox",
                       font=FONT_NORMAL,
                       fieldbackground=theme["input_bg"],
                       background=theme["input_bg"],
                       foreground=theme["text_primary"],
                       bordercolor=theme["input_border"],
                       borderwidth=1,
                       padding=4,
                       arrowcolor=theme["text_secondary"],
                       relief="solid")
        style.map("TCombobox", bordercolor=[("focus", theme["primary"])])

        style.configure("TSpinbox",
                       font=FONT_NORMAL,
                       fieldbackground=theme["input_bg"],
                       foreground=theme["text_primary"],
                       bordercolor=theme["input_border"],
                       borderwidth=1,
                       padding=3,
                       arrowcolor=theme["text_secondary"],
                       relief="solid")

        style.configure("TRadiobutton",
                       font=FONT_NORMAL,
                       background=theme["card_bg"],
                       foreground=theme["text_primary"],
                       padding=(4, 2))
        style.map("TRadiobutton",
                 foreground=[("active", theme["primary"])],
                 indicatorcolor=[("selected", theme["primary"]), ("!selected", theme["input_border"])])

        style.configure("TCheckbutton",
                       font=FONT_NORMAL,
                       background=theme["card_bg"],
                       foreground=theme["text_primary"],
                       padding=(4, 2))
        style.map("TCheckbutton",
                 foreground=[("active", theme["primary"])],
                 indicatorcolor=[("selected", theme["primary"]), ("!selected", theme["input_border"])])

        style.configure("TScrollbar", background="#dcdfe6", troughcolor="#f5f7fa", borderwidth=0, arrowcolor="#c0c4cc", relief="flat")
        style.map("TScrollbar", background=[("active", "#b0b3b8")])

    def _create_widgets(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        self.theme = theme

        main_frame = tk.Frame(self.root, bg=theme["main_bg"])
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_frame = tk.Frame(main_frame, bg=theme["topbar_bg"], height=50)
        top_frame.pack(fill=tk.X)
        top_frame.pack_propagate(False)

        tk.Label(top_frame, text=f"☁  {APP_NAME} - 服务端",
                 font=FONT_TITLE,
                 fg=theme["topbar_text"], bg=theme["topbar_bg"]).pack(side=tk.LEFT, padx=20, pady=10)

        self.status_label = tk.Label(top_frame, text="● 正在启动...",
                                     font=FONT_NORMAL,
                                     fg="#e6a23c", bg=theme["topbar_bg"])
        self.status_label.pack(side=tk.LEFT, padx=5, pady=10)

        self.server_label = tk.Label(top_frame, text="",
                                     font=FONT_NORMAL,
                                     fg=theme["topbar_text"], bg=theme["topbar_bg"])
        self.server_label.pack(side=tk.LEFT, padx=15, pady=10)

        body_frame = tk.Frame(main_frame, bg=theme["main_bg"])
        body_frame.pack(fill=tk.BOTH, expand=True)

        self.sidebar = tk.Frame(body_frame, bg=theme["sidebar_bg"], width=180, highlightthickness=1, highlightbackground=theme["card_border"])
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        self.content_frame = tk.Frame(body_frame, bg=theme["main_bg"])
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=12)

        self._create_sidebar_menu()
        self._create_content_pages()
        self._switch_page("dashboard")

    def _create_sidebar_menu(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        self.sidebar_buttons = {}
        self.sidebar_frames = {}
        menu_items = [
            ("dashboard", "概览"),
            ("printers", "打印机管理"),
            ("tasks", "打印任务"),
            ("devices", "设备管理"),
            ("users", "用户管理"),
            ("settings", "设置"),
        ]

        for idx, (key, text) in enumerate(menu_items):
            item_frame = tk.Frame(self.sidebar, bg=theme["sidebar_bg"], highlightthickness=0)
            item_frame.pack(fill=tk.X, padx=0, pady=0)
            item_frame.pack_propagate(False)
            item_frame.config(height=44)

            text_label = tk.Label(item_frame, text=text,
                                  font=FONT_NORMAL,
                                  bg=theme["sidebar_bg"], fg=theme["sidebar_text"],
                                  anchor="w", cursor="hand2")
            text_label.place(relx=0, rely=0.5, x=20, anchor="w")

            self.sidebar_buttons[key] = {"frame": item_frame, "text": text_label}
            self.sidebar_frames[key] = item_frame

            def _on_click(e, k=key):
                self._switch_page(k)

            def _on_enter(e, k=key):
                if self.current_page != k:
                    th = get_theme(get_config("theme", "tech_blue"))
                    for w in (self.sidebar_buttons[k]["frame"], self.sidebar_buttons[k]["text"]):
                        w.configure(bg=th["sidebar_hover"])

            def _on_leave(e, k=key):
                if self.current_page != k:
                    th = get_theme(get_config("theme", "tech_blue"))
                    for w in (self.sidebar_buttons[k]["frame"], self.sidebar_buttons[k]["text"]):
                        w.configure(bg=th["sidebar_bg"])

            for w in (item_frame, text_label):
                w.bind("<Button-1>", _on_click)
                w.bind("<Enter>", _on_enter)
                w.bind("<Leave>", _on_leave)

    def _create_content_pages(self):
        self.pages = {}
        self.current_page = None

        self._create_dashboard_tab()
        self._create_printers_tab()
        self._create_tasks_tab()
        self._create_devices_tab()
        self._create_users_tab()
        self._create_settings_tab()

    def _switch_page(self, page_key):
        if self.current_page == page_key:
            return

        theme = get_theme(get_config("theme", "tech_blue"))
        for key in self.sidebar_frames:
            widgets = self.sidebar_buttons[key]
            if key == page_key:
                bg = theme["sidebar_active"]
                fg = "#ffffff"
            else:
                bg = theme["sidebar_bg"]
                fg = theme["sidebar_text"]
            widgets["frame"].configure(bg=bg)
            widgets["text"].configure(bg=bg, fg=fg)

        if page_key in self.pages:
            self.pages[page_key].tkraise()
            self.current_page = page_key
            self.root.after(100, self._refresh_current_page)

    def _on_theme_change(self):
        try:
            selected_name = self.theme_var.get()
            theme_key = None
            for key, name in self._theme_list:
                if name == selected_name:
                    theme_key = key
                    break
            if not theme_key:
                return
            set_config("theme", theme_key)
            messagebox.showinfo("皮肤设置", f"已切换为「{selected_name}」皮肤\n重启软件后完全生效")
        except Exception as e:
            messagebox.showerror("错误", f"切换皮肤失败: {e}")

    def _create_page_container(self):
        tab = tk.Frame(self.content_frame, bg="#ffffff", highlightthickness=1, highlightbackground="#ebeef5")
        tab.place(x=0, y=0, relwidth=1, relheight=1)
        return tab

    def _create_dashboard_tab(self):
        tab = self._create_page_container()
        self.pages["dashboard"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=15)
        content.pack(fill=tk.BOTH, expand=True)

        stats_frame = tk.Frame(content, bg="#ffffff")
        stats_frame.pack(fill=tk.X, pady=(0, 12))

        self.stat_widgets = {}
        stats_cards = [
            ("tasks", "任务总数", "📋", "#409eff"),
            ("printers", "打印机", "🖨️", "#67c23a"),
            ("devices", "设备数", "📱", "#e6a23c"),
            ("online", "在线设备", "✅", "#00b894"),
        ]

        for i, (key, title, icon, color) in enumerate(stats_cards):
            card = tk.Frame(stats_frame, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
            card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10) if i < len(stats_cards)-1 else 0)

            card_content = tk.Frame(card, bg="#ffffff", padx=15, pady=12)
            card_content.pack(fill=tk.BOTH, expand=True)

            tk.Label(card_content, text=icon, font=FONT_ICON,
                     fg=color, bg="#ffffff").pack(side=tk.LEFT, padx=(0, 10))

            right = tk.Frame(card_content, bg="#ffffff")
            right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            num_lbl = tk.Label(right, text="0", font=FONT_BIG_NUM,
                     fg="#303133", bg="#ffffff")
            num_lbl.pack(anchor=tk.W)
            tk.Label(right, text=title, font=FONT_NORMAL,
                     fg="#909399", bg="#ffffff").pack(anchor=tk.W)

            self.stat_widgets[key] = num_lbl

        server_info_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        server_info_frame.pack(fill=tk.X, pady=(0, 12))

        server_header = tk.Frame(server_info_frame, bg="#f5f7fa", height=32)
        server_header.pack(fill=tk.X)
        server_header.pack_propagate(False)
        tk.Label(server_header, text="服务信息", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=6)

        server_content = tk.Frame(server_info_frame, bg="#ffffff", padx=15, pady=10)
        server_content.pack(fill=tk.X)

        grid = tk.Frame(server_content, bg="#ffffff")
        grid.pack(fill=tk.X)

        tk.Label(grid, text="服务状态:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=0, column=0, sticky=tk.W, padx=5, pady=4)
        self.dashboard_status_label = tk.Label(grid, text="启动中...", font=FONT_NORMAL, fg="#e6a23c", bg="#ffffff")
        self.dashboard_status_label.grid(row=0, column=1, sticky=tk.W, padx=5, pady=4)

        tk.Label(grid, text="访问码:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=1, column=0, sticky=tk.W, padx=5, pady=4)
        config = load_config()
        access_code = config.get("server", {}).get("access_code", "123456")
        self.dashboard_code_label = tk.Label(grid, text=access_code, font=FONT_NORMAL, fg="#303133", bg="#ffffff")
        self.dashboard_code_label.grid(row=1, column=1, sticky=tk.W, padx=5, pady=4)

        tk.Label(grid, text="本地IP:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=2, column=0, sticky=tk.W, padx=5, pady=4)
        local_ip = self._get_local_ip()
        self.dashboard_ip_label = tk.Label(grid, text=local_ip, font=FONT_NORMAL, fg="#303133", bg="#ffffff")
        self.dashboard_ip_label.grid(row=2, column=1, sticky=tk.W, padx=5, pady=4)

        tk.Label(grid, text="端口:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=3, column=0, sticky=tk.W, padx=5, pady=4)
        port = config.get("server", {}).get("port", 8989)
        self.dashboard_port_label = tk.Label(grid, text=str(port), font=FONT_NORMAL, fg="#303133", bg="#ffffff")
        self.dashboard_port_label.grid(row=3, column=1, sticky=tk.W, padx=5, pady=4)

        btn_frame = tk.Frame(server_content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=(8, 0))

        self.dashboard_start_btn = tk.Button(btn_frame, text="启动服务", command=self.start_server,
                 font=FONT_NORMAL, fg="white", bg="#67c23a",
                 relief="solid", bd=1, padx=20, pady=5, cursor="hand2")
        self.dashboard_start_btn.pack(side=tk.LEFT, padx=5)
        self.dashboard_stop_btn = tk.Button(btn_frame, text="停止服务", command=self.stop_server,
                 font=FONT_NORMAL, fg="white", bg="#f56c6c",
                 relief="solid", bd=1, padx=20, pady=5, cursor="hand2")

        recent_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        recent_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 0))

        recent_header = tk.Frame(recent_frame, bg="#f5f7fa", height=32)
        recent_header.pack(fill=tk.X)
        recent_header.pack_propagate(False)
        tk.Label(recent_header, text="最近任务", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=6)

        self.recent_tasks_frame = tk.Frame(recent_frame, bg="#ffffff")
        self.recent_tasks_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        self.dash_task_tree = ttk.Treeview(self.recent_tasks_frame,
            columns=("task_id", "printer", "file", "status", "time"), show="headings", height=8)
        for col, text, w in [("task_id", "任务ID", 140), ("printer", "打印机", 120), ("file", "文件名", 180), ("status", "状态", 70), ("time", "时间", 130)]:
            self.dash_task_tree.heading(col, text=text, anchor="center")
            self.dash_task_tree.column(col, width=w, anchor="center")
        self.dash_task_tree.pack(fill=tk.BOTH, expand=True)

    def _refresh_dashboard(self):
        try:
            self.stat_widgets["tasks"].configure(text=str(get_task_count()))
            self.stat_widgets["printers"].configure(text=str(get_printer_count()))
            self.stat_widgets["devices"].configure(text=str(get_device_count()))
            online_count = len(self.device_mgr.get_online_devices()) if self.device_mgr else 0
            self.stat_widgets["online"].configure(text=str(online_count))

            for item in self.dash_task_tree.get_children():
                self.dash_task_tree.delete(item)
            tasks = self.task_scheduler.get_all_tasks(limit=10) if self.task_scheduler else []
            status_text = {"pending": "等待中", "printing": "打印中", "completed": "已完成", "failed": "失败", "cancelled": "已取消", "expired": "已过期"}
            for t in tasks[:8]:
                time_str = date_str(t.get('created_at'))[5:] if t.get('created_at') else ""
                self.dash_task_tree.insert("", tk.END, values=(
                    t['task_id'][:16] + "...",
                    t.get('printer_name', ''),
                    t.get('file_name', '')[:20],
                    status_text.get(t.get('status', ''), t.get('status', '')),
                    time_str
                ))
        except Exception as e:
            logger.error(f"刷新概览失败: {e}")

    def _get_local_ip(self):
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def _create_printers_tab(self):
        tab = self._create_page_container()
        self.pages["printers"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=12)
        content.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        tk.Button(btn_frame, text="刷新", command=self.refresh_printers,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2", width=8).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frame, text="添加打印机", command=self.show_add_printer,
                 font=FONT_NORMAL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=3)
        tk.Button(btn_frame, text="删除", command=self.delete_printer,
                 font=FONT_NORMAL, fg="#f56c6c", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2", width=8).pack(side=tk.LEFT, padx=3)

        table_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("id", "name", "status", "type", "default", "shared")
        self.printer_tree = ttk.Treeview(table_frame, columns=columns, show="headings")

        headings = [("id", "ID", 60), ("name", "打印机名称", 220), ("status", "状态", 90), ("type", "类型", 90), ("default", "默认", 60), ("shared", "共享", 60)]
        for col, text, width in headings:
            self.printer_tree.heading(col, text=text, anchor="center")
            self.printer_tree.column(col, width=width, anchor="center")

        self.printer_tree.pack(fill=tk.BOTH, expand=True)
        self.refresh_printers()

    def _refresh_printers_data(self):
        self.refresh_printers()

    def refresh_printers(self):
        if not hasattr(self, 'printer_tree'):
            return
        if not self.printer_mgr:
            return
        for item in self.printer_tree.get_children():
            self.printer_tree.delete(item)
        printers = self.printer_mgr.get_all_printers()
        status_map = {"online": ("在线", "#67c23a"), "offline": ("离线", "#e6a23c"), "error": ("错误", "#f56c6c"), "paper_out": ("缺纸", "#e6a23c"), "toner_low": ("墨粉不足", "#e6a23c")}
        for p in printers:
            status = p.get("status", "online")
            status_text, _ = status_map.get(status, (status, "#909399"))
            is_default = "✓" if p.get("is_default") else ""
            is_shared = "✓" if p.get("is_shared") else ""
            self.printer_tree.insert("", "end", values=(p["id"], p["name"], status_text, p.get("connection_type", "USB"), is_default, is_shared))

    def show_add_printer(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("添加打印机")
        w, h = 540, 400
        dialog.geometry(f"{w}x{h}+{int((self.root.winfo_screenwidth()-w)/2)}+{int((self.root.winfo_screenheight()-h)/2)}")
        dialog.minsize(480, 340)
        dialog.configure(bg="#ffffff")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.state('zoomed')

        container = tk.Frame(dialog, bg="#ffffff", padx=25, pady=18)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(container, text="选择打印机:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=0, column=0, sticky=tk.W, pady=6)
        local_printers = self.printer_mgr.get_local_printers()
        printer_names = [p["name"] for p in local_printers]
        name_var = tk.StringVar()
        if printer_names:
            name_var.set(printer_names[0])
        name_combo = ttk.Combobox(container, textvariable=name_var, values=printer_names, state="readonly", width=35)
        name_combo.grid(row=0, column=1, sticky=tk.W, padx=10, pady=6)
        if not printer_names:
            tk.Label(container, text="未检测到系统打印机", font=FONT_NORMAL, fg="#f56c6c", bg="#ffffff").grid(row=0, column=2, sticky=tk.W, padx=5, pady=6)

        tk.Label(container, text="连接类型:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=1, column=0, sticky=tk.W, pady=6)
        type_var = tk.StringVar(value="USB")
        ttk.Combobox(container, textvariable=type_var, values=["USB", "网络"], state="readonly", width=33).grid(row=1, column=1, sticky=tk.W, padx=10, pady=6)

        shared_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(container, text="共享给客户端使用", variable=shared_var).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=6)

        default_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(container, text="设为默认打印机", variable=default_var).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=6)

        def do_add():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("提示", "请选择或输入打印机名称", parent=dialog)
                return
            conn_type = "USB" if type_var.get() == "USB" else "network"
            is_shared = 1 if shared_var.get() else 0
            is_default = 1 if default_var.get() else 0
            all_printers = self.printer_mgr.get_all_printers()
            for p in all_printers:
                if p["name"] == name:
                    messagebox.showwarning("提示", "该打印机已添加", parent=dialog)
                    return
            try:
                pid = self.printer_mgr.add_printer(name=name, connection_type=conn_type, is_shared=is_shared, is_default=is_default)
                if pid:
                    messagebox.showinfo("成功", "打印机添加成功", parent=dialog)
                    self.refresh_printers()
                    dialog.destroy()
                else:
                    messagebox.showerror("失败", "添加失败，请重试", parent=dialog)
            except Exception as e:
                messagebox.showerror("错误", f"添加失败: {str(e)}", parent=dialog)

        btn_frame = tk.Frame(container, bg="#ffffff")
        btn_frame.grid(row=4, column=0, columnspan=3, pady=15, sticky=tk.EW)
        tk.Button(btn_frame, text="取消", command=dialog.destroy, font=FONT_NORMAL, fg="#606266", bg="#ffffff", relief="solid", bd=1, padx=18, pady=5, width=9, cursor="hand2").pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(btn_frame, text="添加", command=do_add, font=FONT_NORMAL, fg="white", bg="#409eff", relief="solid", bd=1, padx=18, pady=5, width=9, cursor="hand2").pack(side=tk.RIGHT)

    def delete_printer(self):
        selected = self.printer_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择要删除的打印机")
            return
        item = self.printer_tree.item(selected[0])
        printer_id = item["values"][0]
        printer_name = item["values"][1]
        if not messagebox.askyesno("确认删除", f"确定要删除打印机「{printer_name}」吗？"):
            return
        try:
            self.printer_mgr.delete_printer(printer_id)
            messagebox.showinfo("成功", "打印机已删除")
            self.refresh_printers()
        except Exception as e:
            messagebox.showerror("错误", f"删除失败: {str(e)}")

    def _create_tasks_tab(self):
        tab = self._create_page_container()
        self.pages["tasks"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=12)
        content.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        tk.Button(btn_frame, text="刷新", command=self._refresh_tasks_data,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2", width=8).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frame, text="取消任务", command=self.cancel_task,
                 font=FONT_NORMAL, fg="#f56c6c", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=3)
        tk.Button(btn_frame, text="重新打印", command=self.retry_task,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2", width=8).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_frame, text="清空任务", command=self.clear_tasks,
                 font=FONT_NORMAL, fg="white", bg="#f56c6c",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=3)

        self.task_status_var = tk.StringVar(value="全部")
        status_frame = tk.Frame(content, bg="#ffffff")
        status_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Label(status_frame, text="状态筛选:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").pack(side=tk.LEFT, padx=(0, 5))
        for s in ["全部", "等待中", "打印中", "已完成", "失败", "已取消"]:
            tk.Radiobutton(status_frame, text=s, variable=self.task_status_var, value=s,
                          font=FONT_NORMAL, bg="#ffffff", fg="#606266",
                          selectcolor="#ffffff", activebackground="#ffffff", activeforeground="#409eff",
                          command=self._refresh_tasks_data).pack(side=tk.LEFT, padx=3)

        table_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("task_id", "printer", "file", "pages", "copies", "status", "time")
        self.task_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = [("task_id", "任务ID", 140), ("printer", "打印机", 130), ("file", "文件名", 160), ("pages", "页数", 50), ("copies", "份数", 50), ("status", "状态", 70), ("time", "提交时间", 130)]
        for col, text, width in headings:
            self.task_tree.heading(col, text=text, anchor="center")
            self.task_tree.column(col, width=width, anchor="center")
        self.task_tree.pack(fill=tk.BOTH, expand=True)

    def _refresh_tasks_data(self):
        if not hasattr(self, 'task_tree'):
            return
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        if not self.task_scheduler:
            return
        status_map = {"全部": None, "等待中": "pending", "打印中": "printing", "已完成": "completed", "失败": "failed", "已取消": "cancelled"}
        sel = self.task_status_var.get() if hasattr(self, 'task_status_var') else "全部"
        status_filter = status_map.get(sel)
        tasks = self.task_scheduler.get_all_tasks(status=status_filter, limit=200)
        status_text = {"pending": "等待中", "printing": "打印中", "completed": "已完成", "failed": "失败", "cancelled": "已取消", "expired": "已过期"}
        for i, t in enumerate(tasks):
            time_str = date_str(t.get('created_at'))[:16] if t.get('created_at') else ""
            tag = 'even' if i % 2 == 0 else 'odd'
            self.task_tree.insert("", tk.END, iid=t['task_id'], values=(
                t['task_id'][:18], t.get('printer_name', ''), t.get('file_name', '')[:25],
                t.get('pages', 0), t.get('copies', 1),
                status_text.get(t.get('status', ''), t.get('status', '')), time_str
            ), tags=(tag,))
        self.task_tree.tag_configure('even', background="#ffffff")
        self.task_tree.tag_configure('odd', background="#fafbfc")

    def cancel_task(self):
        if not hasattr(self, 'task_tree'):
            return
        selected = self.task_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请选择要取消的任务")
            return
        task_id = selected[0]
        if not messagebox.askyesno("确认", "确定要取消此任务吗？"):
            return
        try:
            success, msg = self.task_scheduler.cancel_task(task_id)
            if success:
                messagebox.showinfo("成功", "任务已取消")
                self._refresh_tasks_data()
            else:
                messagebox.showwarning("提示", msg)
        except Exception as e:
            messagebox.showerror("错误", f"取消失败: {str(e)}")

    def retry_task(self):
        if not hasattr(self, 'task_tree'):
            return
        selected = self.task_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请选择要重新打印的任务")
            return
        task_id = selected[0]
        try:
            success, result = self.task_scheduler.retry_task(task_id)
            if success:
                messagebox.showinfo("成功", f"已重新提交: {result}")
                self._refresh_tasks_data()
            else:
                messagebox.showerror("失败", result)
        except Exception as e:
            messagebox.showerror("错误", f"重试失败: {str(e)}")

    def clear_tasks(self):
        sel = self.task_status_var.get() if hasattr(self, 'task_status_var') else "全部"
        status_map = {"全部": None, "等待中": "pending", "打印中": "printing", "已完成": "completed", "失败": "failed", "已取消": "cancelled"}
        status_filter = status_map.get(sel)
        
        if status_filter in ("pending", "printing"):
            messagebox.showwarning("提示", "不能清空等待中或打印中的任务")
            return
        
        if status_filter:
            msg = f"确定要清空所有「{sel}」状态的任务吗？\n\n此操作不可恢复！"
        else:
            msg = "确定要清空所有已完成/失败/已取消/已过期的任务吗？\n\n正在打印和等待中的任务不会被删除。\n此操作不可恢复！"
        
        if not messagebox.askyesno("确认清空", msg):
            return
        try:
            count = 0
            if status_filter:
                count = db_clear_tasks(status_filter)
            else:
                for st in ("completed", "failed", "cancelled", "expired"):
                    count += db_clear_tasks(st)
            messagebox.showinfo("提示", f"已清空 {count} 条任务")
            self._refresh_tasks_data()
        except Exception as e:
            messagebox.showerror("错误", f"清空失败: {str(e)}")

    def _create_devices_tab(self):
        tab = self._create_page_container()
        self.pages["devices"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=12)
        content.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Button(btn_frame, text="刷新", command=self._refresh_devices_data,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2", width=8).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frame, text="拉黑/解除", command=self.toggle_device_block,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=3)
        tk.Button(btn_frame, text="删除", command=self.delete_device,
                 font=FONT_NORMAL, fg="#f56c6c", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2", width=8).pack(side=tk.LEFT, padx=3)

        table_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("id", "name", "ip", "status", "last_active")
        self.device_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = [("id", "设备ID", 140), ("name", "设备名称", 140), ("ip", "IP地址", 130), ("status", "状态", 80), ("last_active", "最后活跃", 140)]
        for col, text, width in headings:
            self.device_tree.heading(col, text=text, anchor="center")
            self.device_tree.column(col, width=width, anchor="center")
        self.device_tree.pack(fill=tk.BOTH, expand=True)

    def _refresh_devices_data(self):
        if not hasattr(self, 'device_tree'):
            return
        for item in self.device_tree.get_children():
            self.device_tree.delete(item)
        if not self.device_mgr:
            return
        devices = self.device_mgr.get_all_devices()
        for d in devices:
            status = "在线" if d.get('is_online') else ("已拉黑" if d.get('blocked') else "离线")
            last = date_str(d.get('last_online'))[:16] if d.get('last_online') else "-"
            self.device_tree.insert("", tk.END, iid=d['device_id'], values=(
                d['device_id'][:18], d.get('device_name', ''), d.get('ip_address', ''), status, last
            ))

    def toggle_device_block(self):
        selected = self.device_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择设备")
            return
        device_id = selected[0]
        try:
            conn = db_module.get_db()
            row = conn.execute("SELECT blocked, device_name FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            conn.close()
            if row:
                if row['blocked']:
                    self.device_mgr.unblock_device(device_id)
                    messagebox.showinfo("成功", f"已解除拉黑「{row['device_name']}」")
                else:
                    self.device_mgr.block_device(device_id)
                    messagebox.showinfo("成功", f"已拉黑「{row['device_name']}」")
                self._refresh_devices_data()
        except Exception as e:
            messagebox.showerror("错误", f"操作失败: {str(e)}")

    def delete_device(self):
        selected = self.device_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择设备")
            return
        device_id = selected[0]
        try:
            conn = db_module.get_db()
            row = conn.execute("SELECT device_name FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            conn.close()
            if row:
                name = row['device_name']
                if messagebox.askyesno("确认删除", f"确定要删除设备「{name}」吗？\n删除后该设备将需要重新注册。"):
                    self.device_mgr.delete_device(device_id)
                    messagebox.showinfo("成功", f"已删除设备「{name}」")
                    self._refresh_devices_data()
        except Exception as e:
            messagebox.showerror("错误", f"删除失败: {str(e)}")

    def _create_users_tab(self):
        tab = self._create_page_container()
        self.pages["users"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=12)
        content.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Button(btn_frame, text="刷新", command=self._refresh_users_data,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2", width=8).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frame, text="添加用户", command=self.show_add_user,
                 font=FONT_NORMAL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=3)
        tk.Button(btn_frame, text="删除", command=self.delete_user,
                 font=FONT_NORMAL, fg="#f56c6c", bg="#ffffff",
                 relief="solid", bd=1, padx=16, pady=4, cursor="hand2", width=8).pack(side=tk.LEFT, padx=3)

        table_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("id", "username", "role", "real_name", "department", "status", "created_at")
        self.user_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = [("id", "ID", 50), ("username", "用户名", 100), ("role", "角色", 70), ("real_name", "姓名", 100), ("department", "部门", 100), ("status", "状态", 60), ("created_at", "创建时间", 130)]
        for col, text, width in headings:
            self.user_tree.heading(col, text=text, anchor="center")
            self.user_tree.column(col, width=width, anchor="center")
        self.user_tree.pack(fill=tk.BOTH, expand=True)

    def _refresh_users_data(self):
        if not hasattr(self, 'user_tree'):
            return
        for item in self.user_tree.get_children():
            self.user_tree.delete(item)
        if not self.user_mgr:
            return
        users = self.user_mgr.get_all_users()
        role_map = {"admin": "管理员", "user": "普通用户"}
        status_map = {"active": "启用", "disabled": "禁用"}
        for u in users:
            created = date_str(u.get('created_at'))[:16] if u.get('created_at') else "-"
            self.user_tree.insert("", tk.END, iid=u['id'], values=(
                u['id'], u['username'], role_map.get(u.get('role', ''), u.get('role', '')),
                u.get('real_name', ''), u.get('department', ''),
                status_map.get(u.get('status', ''), u.get('status', '')), created
            ))

    def show_add_user(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("添加用户")
        w, h = 460, 380
        dialog.geometry(f"{w}x{h}+{int((self.root.winfo_screenwidth()-w)/2)}+{int((self.root.winfo_screenheight()-h)/2)}")
        dialog.minsize(400, 320)
        dialog.configure(bg="#ffffff")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.state('zoomed')

        container = tk.Frame(dialog, bg="#ffffff", padx=25, pady=18)
        container.pack(fill=tk.BOTH, expand=True)

        fields = [
            ("用户名:", "username", ""),
            ("密码:", "password", ""),
            ("姓名:", "real_name", ""),
            ("部门:", "department", ""),
        ]
        vars_dict = {}
        for i, (label, key, default) in enumerate(fields):
            tk.Label(container, text=label, font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=i, column=0, sticky=tk.W, pady=6)
            v = tk.StringVar(value=default)
            vars_dict[key] = v
            e = ttk.Entry(container, textvariable=v, width=28)
            if key == "password":
                e.configure(show="*")
            e.grid(row=i, column=1, sticky=tk.W, padx=10, pady=6)

        tk.Label(container, text="角色:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=4, column=0, sticky=tk.W, pady=6)
        role_var = tk.StringVar(value="user")
        ttk.Combobox(container, textvariable=role_var, values=["user", "admin"], state="readonly", width=26).grid(row=4, column=1, sticky=tk.W, padx=10, pady=6)

        def do_add():
            username = vars_dict["username"].get().strip()
            password = vars_dict["password"].get().strip()
            if not username or not password:
                messagebox.showwarning("提示", "用户名和密码不能为空", parent=dialog)
                return
            real_name = vars_dict["real_name"].get().strip()
            department = vars_dict["department"].get().strip()
            role = role_var.get()
            try:
                success, result = self.user_mgr.register_user(username, password, real_name, department, role)
                if success:
                    messagebox.showinfo("成功", "用户添加成功", parent=dialog)
                    self._refresh_users_data()
                    dialog.destroy()
                else:
                    messagebox.showerror("失败", str(result), parent=dialog)
            except Exception as e:
                messagebox.showerror("错误", f"添加失败: {str(e)}", parent=dialog)

        btn_frame = tk.Frame(container, bg="#ffffff")
        btn_frame.grid(row=5, column=0, columnspan=2, pady=15, sticky=tk.EW)
        tk.Button(btn_frame, text="取消", command=dialog.destroy, font=FONT_NORMAL, fg="#606266", bg="#ffffff", relief="solid", bd=1, padx=18, pady=5, width=9, cursor="hand2").pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(btn_frame, text="添加", command=do_add, font=FONT_NORMAL, fg="white", bg="#409eff", relief="solid", bd=1, padx=18, pady=5, width=9, cursor="hand2").pack(side=tk.RIGHT)

    def delete_user(self):
        selected = self.user_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择要删除的用户")
            return
        user_id = int(selected[0])
        item = self.user_tree.item(selected[0])
        username = item["values"][1]
        if username == "admin":
            messagebox.showwarning("提示", "不能删除管理员账户")
            return
        if not messagebox.askyesno("确认删除", f"确定要删除用户「{username}」吗？"):
            return
        try:
            self.user_mgr.delete_user(user_id)
            messagebox.showinfo("成功", "用户已删除")
            self._refresh_users_data()
        except Exception as e:
            messagebox.showerror("错误", f"删除失败: {str(e)}")

    def _create_settings_tab(self):
        tab = self._create_page_container()
        self.pages["settings"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=15)
        content.pack(fill=tk.BOTH, expand=True)

        theme_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        theme_frame.pack(fill=tk.X, pady=(0, 12))
        theme_header = tk.Frame(theme_frame, bg="#f5f7fa", height=36)
        theme_header.pack(fill=tk.X)
        theme_header.pack_propagate(False)
        tk.Label(theme_header, text="皮肤设置", font=FONT_NORMAL, fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=8)
        theme_body = tk.Frame(theme_frame, bg="#ffffff", padx=20, pady=12)
        theme_body.pack(fill=tk.X)
        tk.Label(theme_body, text="选择皮肤:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=0, column=0, sticky=tk.W, pady=6)
        current_theme_key = get_config("theme", "tech_blue")
        current_theme_name = get_theme(current_theme_key)["name"]
        self.theme_var = tk.StringVar(value=current_theme_name)
        theme_display = [name for _, name in self._theme_list]
        ttk.Combobox(theme_body, textvariable=self.theme_var, values=theme_display, state="readonly", width=14).grid(row=0, column=1, sticky=tk.W, padx=10, pady=6)
        tk.Button(theme_body, text="应用", command=self._on_theme_change, font=FONT_NORMAL, fg="white", bg="#409eff", relief="solid", bd=1, padx=14, pady=3, cursor="hand2").grid(row=0, column=2, padx=5)

        server_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        server_frame.pack(fill=tk.X, pady=(0, 12))
        server_header = tk.Frame(server_frame, bg="#f5f7fa", height=36)
        server_header.pack(fill=tk.X)
        server_header.pack_propagate(False)
        tk.Label(server_header, text="服务设置", font=FONT_NORMAL, fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=8)
        server_body = tk.Frame(server_frame, bg="#ffffff", padx=20, pady=12)
        server_body.pack(fill=tk.X)
        config = load_config()
        tk.Label(server_body, text="端口:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=0, column=0, sticky=tk.W, pady=6)
        self.settings_port_var = tk.StringVar(value=str(config.get("server", {}).get("port", 8989)))
        ttk.Entry(server_body, textvariable=self.settings_port_var, width=18).grid(row=0, column=1, sticky=tk.W, padx=10, pady=6)
        tk.Label(server_body, text="访问码:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=1, column=0, sticky=tk.W, pady=6)
        self.settings_code_var = tk.StringVar(value=config.get("server", {}).get("access_code", "123456"))
        ttk.Entry(server_body, textvariable=self.settings_code_var, width=18).grid(row=1, column=1, sticky=tk.W, padx=10, pady=6)
        from common.autostart import is_auto_start_enabled
        self.auto_start_var = tk.BooleanVar(value=is_auto_start_enabled("云印宝服务端"))
        ttk.Checkbutton(server_body, text="开机自启动", variable=self.auto_start_var).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=6)

        btn_frame = tk.Frame(content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=8)
        tk.Button(btn_frame, text="保存设置", command=self._save_settings_page,
                 font=FONT_NORMAL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=22, pady=5, cursor="hand2").pack()

    def _save_settings_page(self):
        config = load_config()
        if "server" not in config:
            config["server"] = {}
        try:
            config["server"]["port"] = int(self.settings_port_var.get())
        except:
            config["server"]["port"] = 8989
        config["server"]["access_code"] = self.settings_code_var.get()
        save_config(config)
        from common.autostart import set_auto_start
        set_auto_start("云印宝服务端", self.auto_start_var.get())
        messagebox.showinfo("成功", "设置已保存\n重启服务后生效")

    def start_server(self):
        if not self.printer_mgr:
            self.root.after(100, self.start_server)
            return
        
        config = load_config()
        port = config.get("server", {}).get("port", 8989)
        try:
            from server.api_server import get_server_status
            status = get_server_status()
            if status.get('running'):
                self.status_label.config(text="● 服务已启动", fg="#67c23a")
                self.dashboard_status_label.config(text="运行中", fg="#67c23a")
                self.server_label.config(text=f"http://{self._get_local_ip()}:{port}")
                try:
                    self.dashboard_start_btn.pack_forget()
                except:
                    pass
                try:
                    self.dashboard_stop_btn.pack(side=tk.LEFT, padx=5)
                except:
                    pass
                return
        except:
            pass
        
        existing = {
            'printer_mgr': self.printer_mgr,
            'print_engine': self.print_engine,
            'task_scheduler': self.task_scheduler,
            'user_mgr': self.user_mgr,
            'device_mgr': self.device_mgr,
            'env_checker': self.env_checker,
        }
        start_server(port, existing_managers=existing)
        self.status_label.config(text="● 服务已启动", fg="#67c23a")
        self.dashboard_status_label.config(text="运行中", fg="#67c23a")
        self.server_label.config(text=f"http://{self._get_local_ip()}:{port}")
        try:
            self.dashboard_start_btn.pack_forget()
        except:
            pass
        try:
            self.dashboard_stop_btn.pack(side=tk.LEFT, padx=5)
        except:
            pass

    def stop_server(self):
        from server.api_server import get_server_status
        try:
            status = get_server_status()
            if not status.get('running'):
                return
        except:
            pass
        stop_server()
        self.status_label.config(text="● 服务已停止", fg="#f56c6c")
        self.dashboard_status_label.config(text="已停止", fg="#f56c6c")
        self.server_label.config(text="")
        try:
            self.dashboard_stop_btn.pack_forget()
        except:
            pass
        try:
            self.dashboard_start_btn.pack(side=tk.LEFT, padx=5)
        except:
            pass

    def show_settings(self):
        self._switch_page("settings")

    def on_close(self):
        if self._real_quitting:
            self.root.destroy()
            return
        try:
            self._init_tray()
        except Exception as e:
            logger.error(f"初始化托盘失败: {e}")
            self._real_quit()
            return
        self.root.withdraw()
        self._tray_visible = True
        try:
            if self._tray:
                self._tray.notify("云印宝服务端正在后台运行", "右键托盘图标可退出")
        except Exception:
            pass

    def _init_tray(self):
        if self._tray is not None:
            return
        import pystray
        from PIL import Image, ImageDraw

        def create_icon():
            size = 64
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rectangle((4, 4, size-4, size-4), fill='#1e6fff')
            draw.rectangle((16, 12, size-16, 20), fill='white')
            draw.rectangle((14, 22, size-14, 50), fill='white')
            draw.rectangle((18, 30, size-18, 42), fill='#1e6fff')
            draw.rectangle((20, 54, size-20, 58), fill='white')
            return img

        def on_show(icon, item):
            icon.stop()
            self._tray = None
            self._tray_visible = False
            self.root.after(0, self._show_from_tray)

        def on_quit(icon, item):
            if messagebox.askyesno("退出确认", "确定要退出服务端吗？\n\n退出后客户端将无法连接。"):
                icon.stop()
                self._tray = None
                self._tray_visible = False
                self.root.after(0, self._real_quit)

        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", on_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", on_quit)
        )
        icon_img = create_icon()
        self._tray = pystray.Icon("云印宝服务端", icon_img, "云印宝服务端", menu)
        self._tray.run_detached()

    def _show_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._tray_visible = False

    def _real_quit(self):
        self._real_quitting = True
        if self._refresh_timer:
            try:
                self.root.after_cancel(self._refresh_timer)
            except:
                pass
        try:
            if self._tray:
                try:
                    self._tray.stop()
                except Exception:
                    pass
                self._tray = None
        except Exception:
            pass
        try:
            if self.task_scheduler:
                self.task_scheduler.stop()
        except Exception:
            pass
        try:
            stop_server()
        except Exception:
            pass
        self.root.destroy()
        try:
            self.root.quit()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    if not _check_single_instance():
        messagebox.showwarning("提示", "云印宝服务端已在运行中")
        sys.exit(0)
    app = ServerGUI()
    app.run()


if __name__ == '__main__':
    main()
