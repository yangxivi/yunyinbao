import os
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import logging

logger = logging.getLogger(__name__)

FONT_FAMILY = "Microsoft YaHei UI"
FONT_TITLE = (FONT_FAMILY, 14, "bold")
FONT_NORMAL = (FONT_FAMILY, 10, "normal")
FONT_TABLE = (FONT_FAMILY, 10, "normal")
FONT_SMALL = (FONT_FAMILY, 9, "normal")
FONT_BIG_NUM = (FONT_FAMILY, 26, "normal")
FONT_ICON = (FONT_FAMILY, 20)
FONT_CARD_TITLE = (FONT_FAMILY, 12, "bold")
FONT_CARD_HEADER = (FONT_FAMILY, 11, "normal")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api_server import create_app, start_server, stop_server, get_server_status
from server.printer_manager import PrinterManager
from server.task_scheduler import TaskScheduler
from server.device_manager import DeviceManager
from server.print_engine import PrintEngine
from server.env_check import EnvChecker
from server.user_manager import UserManager
from common.config import APP_NAME, APP_VERSION, load_config, save_config, get_config
from common.theme import get_theme
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
        self.server_thread = None
        self.server_app = None
        self.printer_mgr = None
        self.task_scheduler = None
        self.device_mgr = None
        self.print_engine = None
        self.env_checker = None
        self.user_mgr = None
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

        # 设置窗口图标（任务栏/Alt+Tab 显示 F 图标）
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'F.ico')
        if os.path.exists(ico_path):
            self.root.iconbitmap(ico_path)

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
        self.task_scheduler.register_status_callback(self._on_task_status_changed)
        self.env_checker = EnvChecker()
        config = load_config()
        self.device_mgr = DeviceManager(db_module, config)
        self.user_mgr = UserManager(db_module)
        self._auto_start_server()
        self.root.after(200, self._refresh_current_page)

    def _run_self_check(self):
        results = []
        errors = []

        def check(name, check_func, fix_func=None):
            try:
                result = check_func()
                if result is True:
                    results.append(f"[OK] {name}")
                elif isinstance(result, str):
                    results.append(f"[OK] {name}: {result}" if result else f"[OK] {name}")
                else:
                    errors.append(f"[FAIL] {name}: 检查失败")
            except Exception as e:
                if fix_func:
                    try:
                        fix_func()
                        results.append(f"[OK] {name}: 已修复")
                    except Exception as fe:
                        errors.append(f"[FAIL] {name}: {str(e)} (修复失败: {str(fe)})")
                else:
                    errors.append(f"[FAIL] {name}: {str(e)}")

        check("Python版本", lambda: sys.version_info >= (3, 8))
        check("数据目录", lambda: os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")))

        try:
            from server.printer_manager import PrinterManager
            check("打印机管理器模块", lambda: True)
        except Exception as e:
            errors.append(f"[FAIL] 打印机管理器模块: {str(e)}")

        try:
            from server.api_server import create_app
            check("API服务模块", lambda: True)
        except Exception as e:
            errors.append(f"[FAIL] API服务模块: {str(e)}")

        try:
            import pystray
            check("系统托盘模块", lambda: True)
        except Exception as e:
            errors.append(f"[FAIL] 系统托盘模块: {str(e)}")

        try:
            from PIL import Image
            check("PIL图像模块", lambda: True)
        except Exception as e:
            errors.append(f"[FAIL] PIL图像模块: {str(e)}")

        try:
            import winreg
            check("Windows注册表访问", lambda: True)
        except Exception as e:
            errors.append(f"[FAIL] Windows注册表访问: {str(e)}")

        try:
            db_module.init_db()
            check("数据库连接", lambda: True)
        except Exception as e:
            errors.append(f"[FAIL] 数据库连接: {str(e)}")

        try:
            printers = PrinterManager(db_module).get_local_printers()
            check(f"系统打印机", lambda: f"检测到 {len(printers)} 台打印机")
        except Exception as e:
            errors.append(f"[FAIL] 系统打印机检测: {str(e)}")

        print("\n=== 云印宝服务端 - 启动自检 ===")
        for r in results:
            print(r)
        if errors:
            print("\n错误:")
            for e in errors:
                print(e)
            print("\n警告: 存在错误，部分功能可能无法正常使用")
        else:
            print("\n[OK] 所有检查通过")
        print("=" * 40 + "\n")

    def _auto_start_server(self):
        self.start_server()

    def _schedule_refresh(self):
        try:
            self._refresh_current_page()
        except Exception:
            pass
        self._refresh_timer = self.root.after(5000, self._schedule_refresh)

    def _refresh_current_page(self):
        if not self.printer_mgr:
            return
        for fn in (self._refresh_dashboard, self._refresh_devices_data,
                   self._refresh_printers_data, self._refresh_users_data):
            try:
                fn()
            except Exception:
                pass

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
                       rowheight=30,
                       background=theme["card_bg"],
                       fieldbackground=theme["card_bg"],
                       foreground=theme["text_primary"],
                       borderwidth=1,
                       relief="solid",
                       bordercolor=theme["card_border"])
        style.configure("Treeview.Heading",
                       font=FONT_TABLE,
                       background="#f5f7fa",
                       foreground=theme["text_primary"],
                       padding=5,
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

        # image#2 风格顶部栏：纯白背景，左侧 控制台，右侧 admin | 退出
        top_frame = tk.Frame(main_frame, bg="#ffffff", height=48,
                             highlightbackground="#ebeef5", highlightthickness=1)
        top_frame.pack(fill=tk.X)
        top_frame.pack_propagate(False)

        top_left = tk.Frame(top_frame, bg="#ffffff")
        top_left.pack(side=tk.LEFT, padx=20, pady=10)

        tk.Label(top_left, text="控制台",
                 font=(FONT_FAMILY, 16, "bold"),
                 fg="#303133", bg="#ffffff").pack(side=tk.LEFT)

        # 右侧：状态指示 + 用户信息 + 退出
        top_right = tk.Frame(top_frame, bg="#ffffff")
        top_right.pack(side=tk.RIGHT, padx=20)

        self.status_label = tk.Label(top_right, text="● 正在启动...",
                                     font=FONT_SMALL,
                                     fg="#e6a23c", bg="#ffffff")
        self.status_label.pack(side=tk.LEFT, padx=(0, 16), pady=10)

        tk.Label(top_right, text="admin", font=FONT_SMALL,
                 fg="#303133", bg="#ffffff").pack(side=tk.LEFT, padx=(0, 6), pady=10)

        tk.Label(top_right, text="|", font=FONT_SMALL,
                 fg="#dcdfe6", bg="#ffffff").pack(side=tk.LEFT, padx=2, pady=10)

        exit_btn = tk.Label(top_right, text="退出", font=FONT_SMALL,
                            fg="#303133", bg="#ffffff", cursor="hand2")
        exit_btn.pack(side=tk.LEFT, padx=(6, 0), pady=10)
        exit_btn.bind("<Button-1>", lambda e: self.on_close())
        exit_btn.bind("<Enter>", lambda e: exit_btn.config(fg="#f56c6c"))
        exit_btn.bind("<Leave>", lambda e: exit_btn.config(fg="#303133"))

        body_frame = tk.Frame(main_frame, bg=theme["main_bg"])
        body_frame.pack(fill=tk.BOTH, expand=True)

        # 方案C：单页布局（无侧边栏、无标签切换），所有功能平铺在可滚动单页
        self.content_frame = tk.Frame(body_frame, bg=theme["main_bg"])
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._setup_single_page()
        self._create_content_pages()

    def _setup_single_page(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        outer = tk.Frame(self.content_frame, bg="#f5f7fa")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # 全屏单页：内容直接铺满，无垂直滚动条
        self.single_page = tk.Frame(outer, bg=theme["main_bg"])
        self.single_page.pack(fill=tk.BOTH, expand=True)

    def _create_content_pages(self):
        self.pages = {}
        self.current_page = None

        # 设计稿布局：
        #   第 0 行跨全宽：4 张统计卡片
        #   第 1 行跨全宽：服务信息
        #   第 2 行 2 列等分：打印机管理 | 打印任务
        #   第 3 行 2 列等分：设备管理 | 用户管理
        #   第 4 行跨全宽：系统设置
        self.single_page.grid_columnconfigure(0, weight=1)
        self.single_page.grid_columnconfigure(1, weight=1)
        self.single_page.grid_rowconfigure(0, weight=0)
        self.single_page.grid_rowconfigure(1, weight=0)
        self.single_page.grid_rowconfigure(2, weight=1)
        self.single_page.grid_rowconfigure(3, weight=1)
        self.single_page.grid_rowconfigure(4, weight=0)

        self._build_stats_block(0, 0, columnspan=2)          # 统计卡片
        self._build_service_info_block(1, 0, columnspan=2)    # 服务信息
        self._build_printers_block(2, 0)                      # 打印机管理
        self._build_task_queue_block(2, 1)                     # 打印任务
        self._build_devices_block(3, 0)                        # 设备管理
        self._build_users_block(3, 1)                          # 用户管理
        self._build_settings_block(4, 0, columnspan=2)         # 系统设置

    def _create_page_container(self, row, column, rowspan=1, columnspan=1, min_height=None):
        # 卡片容器无外边框；边框在 content 区单独画（粗一点的灰边框），标题保持纯白干净
        theme = get_theme(get_config("theme", "tech_blue"))
        section = tk.Frame(self.single_page, bg=theme["main_bg"])
        section.grid(row=row, column=column, rowspan=rowspan, columnspan=columnspan,
                     sticky="nsew", padx=6, pady=6)
        if min_height:
            section.configure(height=min_height)
            section.grid_propagate(False)
        return section

    def _build_titled_section(self, row, column, title, rowspan=1, columnspan=1, min_height=None):
        """创建带标题栏的卡片；返回 (content, actions_frame)，actions 放在标题右侧。
        content 是带粗边框的 Frame，内层有 inner（带内边距），子组件应直接 pack/grid 到 content 即可。"""
        theme = get_theme(get_config("theme", "tech_blue"))
        section = self._create_page_container(row, column, rowspan, columnspan, min_height)
        # image#2 风格：标题在左、操作按钮在右，标题更细更淡
        header = tk.Frame(section, bg=theme["main_bg"])
        header.pack(fill=tk.X)
        tk.Label(header, text=title, font=FONT_CARD_TITLE,
                 fg="#303133", bg=theme["main_bg"]).pack(side=tk.LEFT, padx=(8, 8), pady=(2, 6))
        actions = tk.Frame(header, bg=theme["main_bg"])
        actions.pack(side=tk.RIGHT, padx=10, pady=4)
        # 内容区：粗一点的边框（2px 灰），与标题分开
        content = tk.Frame(section, bg="#ffffff",
                           highlightthickness=2, highlightbackground="#dcdfe6")
        content.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 2))
        # 内层带 padding，让子组件不贴边框
        inner = tk.Frame(content, bg="#ffffff")
        inner.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        return inner, actions

    def _build_stats_block(self, row, column, columnspan=1):
        section = self._create_page_container(row, column, columnspan=columnspan)
        content = tk.Frame(section, bg="#ffffff")
        content.pack(fill=tk.X, padx=10, pady=10)
        for c in range(4):
            content.grid_columnconfigure(c, weight=1)

        self.stat_widgets = {}
        # image#2 风格：标题在上、数字在下、无图标
        stats_cards = [
            ("tasks", "任务总数", "#409eff"),
            ("printers", "打印机", "#67c23a"),
            ("devices", "设备数", "#e6a23c"),
            ("online", "在线设备", "#409eff"),
        ]

        for i, (key, title, color) in enumerate(stats_cards):
            card = tk.Frame(content, bg="#ffffff", highlightbackground="#dcdfe6", highlightthickness=2)
            card.grid(row=0, column=i, sticky="nsew", padx=(0, 10) if i < len(stats_cards)-1 else 0, pady=0)
            card.grid_columnconfigure(0, weight=1)

            tk.Label(card, text=title, font=FONT_SMALL,
                     fg="#303133", bg="#ffffff").pack(anchor=tk.W, padx=14, pady=(10, 0))
            num_lbl = tk.Label(card, text="0", font=FONT_NORMAL,
                               fg=color, bg="#ffffff")
            num_lbl.pack(anchor=tk.W, padx=14, pady=(0, 8))

            self.stat_widgets[key] = num_lbl

    def _build_service_info_block(self, row, column, columnspan=1):
        content, actions = self._build_titled_section(row, column, "服务信息", columnspan=columnspan)

        info_frame = tk.Frame(content, bg="#ffffff")
        info_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 4))

        self.dash_svc_status = tk.Label(info_frame, text="启动中...", font=FONT_SMALL, fg="#e6a23c", bg="#ffffff")
        self.dash_svc_code = tk.Label(info_frame, text="", font=FONT_SMALL, fg="#303133", bg="#ffffff")
        self.dash_svc_ip = tk.Label(info_frame, text="", font=FONT_SMALL, fg="#303133", bg="#ffffff")
        self.dash_svc_port = tk.Label(info_frame, text="", font=FONT_SMALL, fg="#303133", bg="#ffffff")

        # image#2 风格：信息项横向均匀分布
        tk.Label(info_frame, text="服务状态：", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(side=tk.LEFT, padx=(0, 4))
        self.dash_svc_status.pack(side=tk.LEFT, padx=(0, 40))
        tk.Label(info_frame, text="访问码：", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(side=tk.LEFT, padx=(0, 4))
        self.dash_svc_code.pack(side=tk.LEFT, padx=(0, 40))
        tk.Label(info_frame, text="本地IP：", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(side=tk.LEFT, padx=(0, 4))
        self.dash_svc_ip.pack(side=tk.LEFT, padx=(0, 40))
        tk.Label(info_frame, text="端口：", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(side=tk.LEFT, padx=(0, 4))
        self.dash_svc_port.pack(side=tk.LEFT)

        # 小型按钮放在标题右侧
        self.dash_svc_stop_btn = tk.Button(actions, text="停止服务", command=self.stop_server,
                 font=FONT_SMALL, fg="white", bg="#f56c6c",
                 relief="solid", bd=1, padx=12, pady=2, cursor="hand2")
        self.dash_svc_stop_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self.dash_svc_start_btn = tk.Button(actions, text="启动服务", command=self.start_server,
                 font=FONT_SMALL, fg="white", bg="#67c23a",
                 relief="solid", bd=1, padx=12, pady=2, cursor="hand2")
        self.dash_svc_start_btn.pack(side=tk.RIGHT, padx=(4, 0))

    def _build_task_queue_block(self, row, column, rowspan=1, columnspan=1):
        content, actions = self._build_titled_section(row, column, "打印任务",
                                                      rowspan=rowspan, columnspan=columnspan)

        # 手动重试 / 取消按钮放在标题右侧
        tk.Button(actions, text="重试", command=self.retry_task,
                 font=FONT_SMALL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(actions, text="取消", command=self.cancel_task,
                 font=FONT_SMALL, fg="#303133", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))

        tree_frame = tk.Frame(content, bg="#ffffff")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.dash_task_tree = ttk.Treeview(tree_frame,
            columns=("task_id", "printer", "file", "status", "time"), show="headings", height=9)
        for col, text, w in [("task_id", "任务ID", 120), ("printer", "打印机", 100), ("file", "文件名", 140), ("status", "状态", 70), ("time", "时间", 100)]:
            self.dash_task_tree.heading(col, text=text, anchor="center")
            self.dash_task_tree.column(col, width=w, anchor="center", stretch=True)
        self.dash_task_tree.pack(fill=tk.BOTH, expand=True)

    def cancel_task(self):
        selected = self.dash_task_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择任务")
            return
        task_id = selected[0]
        if not messagebox.askyesno("确认", "确定要取消此任务吗？"):
            return
        if not self.task_scheduler:
            return
        success, msg = self.task_scheduler.cancel_task(task_id)
        if success:
            messagebox.showinfo("成功", "任务已取消")
        else:
            messagebox.showerror("失败", msg)

    def retry_task(self):
        selected = self.dash_task_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择任务")
            return
        task_id = selected[0]
        if not self.task_scheduler:
            return
        success, result = self.task_scheduler.retry_task(task_id)
        if success:
            messagebox.showinfo("成功", f"已重新提交: {result}")
        else:
            messagebox.showerror("失败", result)

    def _refresh_dashboard(self):
        try:
            self.stat_widgets["tasks"].configure(text=str(get_task_count()))
            self.stat_widgets["printers"].configure(text=str(get_printer_count()))
            self.stat_widgets["devices"].configure(text=str(get_device_count()))
            online_count = len(self.device_mgr.get_online_devices()) if self.device_mgr else 0
            self.stat_widgets["online"].configure(text=str(online_count))

            # 服务信息行
            try:
                from server.api_server import get_server_status
                st = get_server_status()
                if st.get('running'):
                    self.dash_svc_status.configure(text="运行中", fg="#67c23a")
                    self.dash_svc_start_btn.pack_forget()
                    self.dash_svc_stop_btn.pack(side=tk.RIGHT, padx=(6, 0))
                else:
                    self.dash_svc_status.configure(text="已停止", fg="#f56c6c")
                    self.dash_svc_stop_btn.pack_forget()
                    self.dash_svc_start_btn.pack(side=tk.RIGHT, padx=(6, 0))
            except Exception:
                pass
            config = load_config()
            self.dash_svc_code.configure(text=str(config.get("server", {}).get("access_code", "")))
            self.dash_svc_ip.configure(text=self._get_local_ip())
            self.dash_svc_port.configure(text=str(config.get("server", {}).get("port", 8989)))

            for item in self.dash_task_tree.get_children():
                self.dash_task_tree.delete(item)
            tasks = self.task_scheduler.get_all_tasks(limit=10) if self.task_scheduler else []
            status_text = {"pending": "等待中", "printing": "打印中", "completed": "已完成", "failed": "失败", "cancelled": "已取消", "expired": "已过期"}
            for t in tasks[:10]:
                time_str = date_str(t.get('created_at'))[5:] if t.get('created_at') else ""
                self.dash_task_tree.insert("", tk.END, iid=t['task_id'], values=(
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

    def _on_task_status_changed(self, task_id, status, detail=""):
        """任务调度线程回调：打印成功/失败时右下角弹窗提示（5秒自动关闭）"""
        if status not in ("completed", "failed"):
            return
        try:
            task = self.task_scheduler.get_task_status(task_id) if self.task_scheduler else None
            name = task.get('file_name', '') if task else ''
        except Exception:
            name = ''
        self.root.after(0, lambda: self._show_toast(
            "打印成功" if status == "completed" else "打印失败",
            f"{name or task_id}",
            is_error=(status == "failed")
        ))

    def _show_toast(self, title, message, is_error=False):
        """右下角弹窗提示，5 秒后自动关闭"""
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)
            w, h = 320, 76
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            toast.geometry(f"{w}x{h}+{sw - w - 20}+{sh - h - 70}")
            bg = "#f56c6c" if is_error else "#67c23a"
            frame = tk.Frame(toast, bg=bg)
            frame.pack(fill=tk.BOTH, expand=True)
            tk.Label(frame, text=title, font=(FONT_FAMILY, 11, "bold"),
                     fg="white", bg=bg).pack(anchor="w", padx=14, pady=(8, 0))
            tk.Label(frame, text=message, font=FONT_SMALL, fg="white", bg=bg,
                     wraplength=290, justify="left").pack(anchor="w", padx=14, pady=(2, 8))
            toast.after(5000, toast.destroy)
        except Exception as e:
            logger.error(f"弹窗提示失败: {e}")

    def _build_printers_block(self, row, column):
        content, actions = self._build_titled_section(row, column, "打印机管理")

        # image#2 风格按钮：添加蓝色填充，删除/刷新为白色边框灰色文字
        tk.Button(actions, text="刷新", command=self.refresh_printers,
                 font=FONT_SMALL, fg="#303133", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(actions, text="添加", command=self.show_add_printer,
                 font=FONT_SMALL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(actions, text="删除", command=self.delete_printer,
                 font=FONT_SMALL, fg="#f56c6c", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))

        table_frame = tk.Frame(content, bg="#ffffff")
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("id", "name", "status", "type", "default", "shared")
        self.printer_tree = ttk.Treeview(table_frame, columns=columns, show="headings")

        headings = [("id", "ID", 40), ("name", "名称", 90), ("status", "状态", 55), ("type", "类型", 50), ("default", "默认", 40), ("shared", "共享", 40)]
        for col, text, width in headings:
            self.printer_tree.heading(col, text=text, anchor="center")
            self.printer_tree.column(col, width=width, anchor="center", stretch=True)

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
            status_text, _ = status_map.get(status, (status, "#303133"))
            is_default = "[OK]" if p.get("is_default") else ""
            is_shared = "[OK]" if p.get("is_shared") else ""
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
        tk.Button(btn_frame, text="取消", command=dialog.destroy, font=FONT_NORMAL, fg="#303133", bg="#ffffff", relief="solid", bd=1, padx=18, pady=5, width=9, cursor="hand2").pack(side=tk.RIGHT, padx=(8, 0))
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

    def _build_devices_block(self, row, column):
        content, actions = self._build_titled_section(row, column, "设备管理")

        tk.Button(actions, text="刷新", command=self._refresh_devices_data,
                 font=FONT_SMALL, fg="#303133", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(actions, text="拉黑/解除", command=self.toggle_device_block,
                 font=FONT_SMALL, fg="#303133", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(actions, text="删除", command=self.delete_device,
                 font=FONT_SMALL, fg="#f56c6c", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))

        table_frame = tk.Frame(content, bg="#ffffff")
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("id", "name", "ip", "status", "last_active")
        self.device_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = [("id", "设备ID", 80), ("name", "名称", 80), ("ip", "IP", 90), ("status", "状态", 50), ("last_active", "活跃", 90)]
        for col, text, width in headings:
            self.device_tree.heading(col, text=text, anchor="center")
            self.device_tree.column(col, width=width, anchor="center", stretch=True)
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

    def _build_settings_block(self, row, column, columnspan=1):
        content, actions = self._build_titled_section(row, column, "系统设置", columnspan=columnspan)

        # image#2 风格：保存按钮在标题右侧
        tk.Button(actions, text="保存设置", command=self._save_settings_page,
                 font=FONT_SMALL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=14, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))

        config = load_config()

        form = tk.Frame(content, bg="#ffffff")
        form.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        for c in range(3):
            form.grid_columnconfigure(c, weight=1)

        # 端口
        port_frame = tk.Frame(form, bg="#ffffff")
        port_frame.grid(row=0, column=0, sticky="w", padx=(0, 30), pady=6)
        tk.Label(port_frame, text="端口", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(anchor=tk.W)
        self.settings_port_var = tk.StringVar(value=str(config.get("server", {}).get("port", 8989)))
        ttk.Entry(port_frame, textvariable=self.settings_port_var, width=14).pack(anchor=tk.W, pady=(6, 0))

        # 访问码
        code_frame = tk.Frame(form, bg="#ffffff")
        code_frame.grid(row=0, column=1, sticky="w", padx=(0, 30), pady=6)
        tk.Label(code_frame, text="访问码", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(anchor=tk.W)
        self.settings_code_var = tk.StringVar(value=config.get("server", {}).get("access_code", "123456"))
        ttk.Entry(code_frame, textvariable=self.settings_code_var, width=14).pack(anchor=tk.W, pady=(6, 0))

        # 开机自启动
        auto_frame = tk.Frame(form, bg="#ffffff")
        auto_frame.grid(row=0, column=2, sticky="w", padx=(0, 0), pady=6)
        from common.autostart import is_auto_start_enabled
        self.auto_start_var = tk.BooleanVar(value=is_auto_start_enabled("云印宝服务端"))
        ttk.Checkbutton(auto_frame, text="开机自启动", variable=self.auto_start_var).pack(anchor=tk.W, pady=(26, 0))

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

    def _build_users_block(self, row, column):
        content, actions = self._build_titled_section(row, column, "用户管理")

        tk.Button(actions, text="添加", command=self.show_add_user,
                 font=FONT_SMALL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(actions, text="删除", command=self.delete_user,
                 font=FONT_SMALL, fg="#f56c6c", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))

        table_frame = tk.Frame(content, bg="#ffffff")
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("id", "username", "real_name", "department", "role", "daily_limit", "status")
        self.user_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = [("id", "ID", 40), ("username", "用户名", 90), ("real_name", "姓名", 80),
                    ("department", "部门", 90), ("role", "角色", 60), ("daily_limit", "日限额", 60), ("status", "状态", 60)]
        for col, text, width in headings:
            self.user_tree.heading(col, text=text, anchor="center")
            self.user_tree.column(col, width=width, anchor="center", stretch=True)
        self.user_tree.pack(fill=tk.BOTH, expand=True)
        self._refresh_users_data()

    def _refresh_users_data(self):
        if not hasattr(self, 'user_tree') or not self.user_mgr:
            return
        for item in self.user_tree.get_children():
            self.user_tree.delete(item)
        try:
            users = self.user_mgr.get_all_users()
        except Exception:
            users = []
        for u in users:
            self.user_tree.insert("", tk.END, values=(
                u.get("id"), u.get("username"), u.get("real_name", ""),
                u.get("department", ""), u.get("role", ""), u.get("daily_limit", 0),
                u.get("status", "")
            ))

    def show_add_user(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("添加用户")
        w, h = 460, 360
        dialog.geometry(f"{w}x{h}+{int((self.root.winfo_screenwidth()-w)/2)}+{int((self.root.winfo_screenheight()-h)/2)}")
        dialog.configure(bg="#ffffff")
        dialog.transient(self.root)
        dialog.grab_set()

        container = tk.Frame(dialog, bg="#ffffff", padx=25, pady=18)
        container.pack(fill=tk.BOTH, expand=True)

        def _row(r, label, widget):
            tk.Label(container, text=label, font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=r, column=0, sticky=tk.W, pady=6)
            widget.grid(row=r, column=1, sticky=tk.W, padx=10, pady=6)

        un_var = tk.StringVar()
        _row(0, "用户名:", ttk.Entry(container, textvariable=un_var, width=24))
        pw_var = tk.StringVar()
        _row(1, "密码:", ttk.Entry(container, textvariable=pw_var, width=24, show="*"))
        rn_var = tk.StringVar()
        _row(2, "姓名:", ttk.Entry(container, textvariable=rn_var, width=24))
        dp_var = tk.StringVar()
        _row(3, "部门:", ttk.Entry(container, textvariable=dp_var, width=24))
        role_var = tk.StringVar(value="user")
        _row(4, "角色:", ttk.Combobox(container, textvariable=role_var, values=["user", "admin"], state="readonly", width=22))
        dl_var = tk.IntVar(value=0)
        _row(5, "日限额(页):", ttk.Spinbox(container, from_=0, to=9999, textvariable=dl_var, width=22))

        def do_add():
            username = un_var.get().strip()
            password = pw_var.get()
            if not username or not password:
                messagebox.showwarning("提示", "用户名和密码不能为空", parent=dialog)
                return
            ok, msg = self.user_mgr.register_user(
                username=username, password=password,
                real_name=rn_var.get().strip(), department=dp_var.get().strip(),
                role=role_var.get(), daily_limit=dl_var.get())
            if ok:
                messagebox.showinfo("成功", "用户添加成功", parent=dialog)
                self._refresh_users_data()
                dialog.destroy()
            else:
                messagebox.showerror("失败", f"添加失败: {msg}", parent=dialog)

        btn_frame = tk.Frame(container, bg="#ffffff")
        btn_frame.grid(row=6, column=0, columnspan=2, pady=15, sticky=tk.EW)
        tk.Button(btn_frame, text="取消", command=dialog.destroy, font=FONT_NORMAL, fg="#303133", bg="#ffffff", relief="solid", bd=1, padx=18, pady=5, width=9, cursor="hand2").pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(btn_frame, text="添加", command=do_add, font=FONT_NORMAL, fg="white", bg="#409eff", relief="solid", bd=1, padx=18, pady=5, width=9, cursor="hand2").pack(side=tk.RIGHT)

    def delete_user(self):
        if not hasattr(self, 'user_tree'):
            return
        selected = self.user_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择要删除的用户")
            return
        item = self.user_tree.item(selected[0])
        user_id = item["values"][0]
        username = item["values"][1]
        if username == "admin":
            messagebox.showwarning("提示", "不能删除管理员账号")
            return
        if not messagebox.askyesno("确认删除", f"确定要删除用户「{username}」吗？"):
            return
        if self.user_mgr.delete_user(user_id):
            messagebox.showinfo("成功", "用户已删除")
            self._refresh_users_data()
        else:
            messagebox.showerror("错误", "删除失败")

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
                self.dash_svc_status.config(text="运行中", fg="#67c23a")
                try:
                    self.dash_svc_start_btn.pack_forget()
                except:
                    pass
                try:
                    self.dash_svc_stop_btn.pack(side=tk.RIGHT, padx=(6, 0))
                except:
                    pass
                return
        except:
            pass

        existing = {
            'printer_mgr': self.printer_mgr,
            'print_engine': self.print_engine,
            'task_scheduler': self.task_scheduler,
            'device_mgr': self.device_mgr,
            'env_checker': self.env_checker,
        }
        start_server(port, existing_managers=existing)
        self.status_label.config(text="● 服务已启动", fg="#67c23a")
        self.dash_svc_status.config(text="运行中", fg="#67c23a")
        try:
            self.dash_svc_start_btn.pack_forget()
        except:
            pass
        try:
            self.dash_svc_stop_btn.pack(side=tk.RIGHT, padx=(6, 0))
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
        self.dash_svc_status.config(text="已停止", fg="#f56c6c")
        try:
            self.dash_svc_stop_btn.pack_forget()
        except:
            pass
        try:
            self.dash_svc_start_btn.pack(side=tk.RIGHT, padx=(6, 0))
        except:
            pass

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
            # 优先用 F.ico 的 16x16 子图（与 exe 图标一致），失败时画蓝 F 兜底
            ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'F.ico')
            try:
                img = Image.open(ico_path)
                img.size = (16, 16)
                img.load()
                return img
            except Exception:
                img = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.rectangle((0, 0, 15, 15), fill='#1e6fff')
                draw.text((5, 1), 'F', fill='white')
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
