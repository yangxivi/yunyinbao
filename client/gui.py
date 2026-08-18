import os
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.api_client import PrintClient
from common.config import APP_NAME, APP_VERSION, load_config, save_config, get_config
from common.theme import get_theme


class ClientGUI:
    def __init__(self):
        self.client = PrintClient()
        self.selected_files = []
        self._tray = None
        self._tray_visible = False
        self._real_quitting = False
        self._preview_print_info = None
        self._preview_confirmed = False
        self._refresh_timer = None
        self._task_status_cache = {}
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

        # 设置窗口图标（任务栏/Alt+Tab 显示 K 图标）
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'K.ico')
        if os.path.exists(ico_path):
            self.root.iconbitmap(ico_path)

        self._setup_style()
        self._create_widgets()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.client.server_url and self.client.access_code:
            self.root.after(1000, self._try_auto_connect)
        self.root.after(500, self._run_self_check_async)
        self.root.after(2000, self._start_heartbeat)
        self.root.after(3000, self._schedule_refresh)

    def _start_heartbeat(self):
        """延迟启动心跳，不阻塞UI显示"""
        try:
            self.client._start_heartbeat()
        except Exception as e:
            logger.debug(f"启动心跳失败: {e}")

    def _run_self_check_async(self):
        """后台线程运行自检，不阻塞UI"""
        import threading
        threading.Thread(target=self._run_self_check, daemon=True).start()

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
            from client.api_client import PrintClient
            check("API客户端模块", lambda: True)
        except Exception as e:
            errors.append(f"[FAIL] API客户端模块: {str(e)}")

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
            import fitz
            check("PyMuPDF(PDF预览)", lambda: True)
        except Exception as e:
            errors.append(f"[FAIL] PyMuPDF(PDF预览): {str(e)}")

        try:
            printers = self.client.get_local_printers()
            check(f"系统打印机", lambda: f"检测到 {len(printers)} 台")
        except Exception as e:
            errors.append(f"[FAIL] 系统打印机检测: {str(e)}")

        print("\n=== 云印宝客户端 - 启动自检 ===")
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

    def _schedule_refresh(self):
        try:
            # 状态检查放后台线程，避免启动 PowerShell 卡住主线程（输入卡顿根因）
            threading.Thread(target=self._refresh_statistics_async, daemon=True).start()
            if self.client.is_connected:
                # refresh_tasks 内部已自起线程，这里直接调即可
                self.refresh_tasks()
        except Exception:
            pass
        self._refresh_timer = self.root.after(5000, self._schedule_refresh)

    def _refresh_statistics_async(self):
        """后台线程：检查虚拟打印机状态 + 统计计数"""
        try:
            from common.virtual_printer import check_printer_exists
            vp_status = "ok" if check_printer_exists() else "missing"
        except Exception:
            vp_status = "error"
        task_count = len(self._virtual_print_jobs)
        conn = self.client.is_connected
        self.root.after(0, lambda: self._apply_statistics(vp_status, task_count, conn))

    def _refresh_tasks_async(self):
        """后台线程：刷新任务列表"""
        try:
            self.refresh_tasks()
        except Exception:
            pass

    def _apply_statistics(self, vp_status, task_count, connected):
        """主线程：应用统计结果到 UI"""
        try:
            if vp_status == "ok":
                self._stat_vp_label.config(text="● 已安装", fg="#67c23a")
            elif vp_status == "missing":
                self._stat_vp_label.config(text="● 未安装", fg="#f56c6c")
            else:
                self._stat_vp_label.config(text="● 检测失败", fg="#e6a23c")
        except Exception:
            pass
        try:
            self._stat_task_label.config(text=str(task_count))
        except Exception:
            pass
        try:
            if connected:
                self._stat_conn_label.config(text="● 已连接", fg="#67c23a")
            else:
                self._stat_conn_label.config(text="● 未连接", fg="#f56c6c")
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

        # image#2 风格顶部栏：纯白背景，左侧 标题，右侧 状态 + 退出
        top_frame = tk.Frame(main_frame, bg="#ffffff", height=48,
                             highlightbackground="#ebeef5", highlightthickness=1)
        top_frame.pack(fill=tk.X)
        top_frame.pack_propagate(False)

        top_left = tk.Frame(top_frame, bg="#ffffff")
        top_left.pack(side=tk.LEFT, padx=20, pady=10)

        tk.Label(top_left, text="云印宝客户端",
                 font=(FONT_FAMILY, 16, "bold"),
                 fg="#303133", bg="#ffffff").pack(side=tk.LEFT)

        top_right = tk.Frame(top_frame, bg="#ffffff")
        top_right.pack(side=tk.RIGHT, padx=20)

        self.status_label = tk.Label(top_right, text="● 未连接",
                                     font=FONT_SMALL,
                                     fg="#f56c6c", bg="#ffffff")
        self.status_label.pack(side=tk.LEFT, padx=(0, 16), pady=10)

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

        # 3 列等权 + 第 1、2 行（上下两排卡片）均分等高，单屏全显示
        for c in range(3):
            self.single_page.grid_columnconfigure(c, weight=1)
        self.single_page.grid_rowconfigure(0, weight=0)  # 统计行：最小高度
        self.single_page.grid_rowconfigure(1, weight=1)  # 上排：均分剩余高度一半
        self.single_page.grid_rowconfigure(2, weight=1)  # 下排：均分剩余高度一半

    def _create_content_pages(self):
        # 三列等宽布局，填满总宽度，不留右侧空白
        #   第 0 行跨 3 列：统计概览
        #   第 1 行 3 列等分：虚拟打印控制 | 快捷操作 | 系统设置
        #   第 2 行 2 列均分：待打印任务 | 我的任务（用独立子容器实现 2 等分）
        for c in range(3):
            self.single_page.grid_columnconfigure(c, weight=1)

        self._build_statistics_section()                       # 统计卡片（跨 3 列）
        self._build_virtual_print_control(row=1, column=0)     # 虚拟打印控制
        self._build_quick_actions(row=1, column=1)             # 快捷操作
        self._build_settings_section(row=1, column=2)          # 系统设置

        # 第 2 行：用独立子容器做 2 等分均分（避免与第 1 行的 3 列配置冲突）
        theme = get_theme(get_config("theme", "tech_blue"))
        row2 = tk.Frame(self.single_page, bg=theme["main_bg"])
        row2.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=6, pady=6)
        row2.grid_columnconfigure(0, weight=1)
        row2.grid_columnconfigure(1, weight=1)
        row2.grid_rowconfigure(0, weight=1)
        self._build_capture_tasks_section(row=0, column=0, parent=row2)   # 待打印任务
        self._build_my_tasks_section(row=0, column=1, parent=row2)        # 我的任务

    def _create_page_container(self, row, column, rowspan=1, columnspan=1, min_height=None, parent=None):
        # 卡片容器无外边框；边框在 content 区单独画（粗一点的灰边框），标题保持纯白干净
        theme = get_theme(get_config("theme", "tech_blue"))
        if parent is None:
            parent = self.single_page
        section = tk.Frame(parent, bg=theme["main_bg"])
        section.grid(row=row, column=column, rowspan=rowspan, columnspan=columnspan,
                     padx=6, pady=6, sticky="nsew")
        if min_height is not None:
            section.config(height=min_height)
        return section

    def _build_titled_section(self, row, column, title, rowspan=1, columnspan=1, min_height=None, parent=None):
        """创建带标题栏的卡片区块；返回 (content, actions_frame)，actions 放在标题右侧。
        content 是带粗边框的 Frame，内层有 inner（带内边距），子组件应直接 pack/grid 到 content 即可。"""
        theme = get_theme(get_config("theme", "tech_blue"))
        card = self._create_page_container(row, column, rowspan=rowspan, columnspan=columnspan, min_height=min_height, parent=parent)
        # image#2 风格：标题更淡更细
        header = tk.Frame(card, bg=theme["main_bg"])
        header.pack(fill=tk.X)
        tk.Label(header, text=title, font=FONT_CARD_TITLE,
                 fg="#303133", bg=theme["main_bg"]).pack(side=tk.LEFT, padx=(8, 8), pady=(2, 6))
        actions = tk.Frame(header, bg=theme["main_bg"])
        actions.pack(side=tk.RIGHT, padx=10, pady=4)
        # 内容区：粗一点的边框（2px 灰），与标题分开
        content = tk.Frame(card, bg="#ffffff",
                           highlightthickness=2, highlightbackground="#dcdfe6")
        content.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 2))
        # 内层带 padding，让子组件不贴边框
        inner = tk.Frame(content, bg="#ffffff")
        inner.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        return inner, actions

    def _build_statistics_section(self):
        content, actions = self._build_titled_section(row=0, column=0, title="统计概览", columnspan=3)
        cards_frame = tk.Frame(content, bg="#ffffff")
        cards_frame.pack(fill=tk.BOTH, expand=True)

        self._stat_vp_label, _ = self._create_stat_card(cards_frame, "虚拟打印机状态", "● 检测中...", "#e6a23c")
        self._stat_task_label, _ = self._create_stat_card(cards_frame, "待打印任务数", "0", "#409eff")
        self._stat_conn_label, _ = self._create_stat_card(cards_frame, "连接状态", "● 未连接", "#f56c6c")

    def _create_stat_card(self, parent, title, value, color):
        # image#2 风格：白底粗边框，标题在上、数字在下、无图标
        card = tk.Frame(parent, bg="#ffffff", highlightbackground="#dcdfe6", highlightthickness=2)
        card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        tk.Label(card, text=title, font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(anchor=tk.W, padx=14, pady=(10, 0))
        value_label = tk.Label(card, text=value, font=FONT_NORMAL, fg=color, bg="#ffffff")
        value_label.pack(anchor=tk.W, padx=14, pady=(0, 8))
        return value_label, card

    def _build_quick_actions(self, row, column):
        content, actions = self._build_titled_section(row=row, column=column, title="快捷操作")

        tk.Label(content, text="连接服务端后可刷新或清空任务。",
                 font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(anchor=tk.W, padx=2, pady=(4, 0))

        self._quick_conn_btn = tk.Button(actions,
                                         text="连接" if not self.client.is_connected else "断开",
                                         command=self._toggle_connection,
                                         font=FONT_SMALL, fg="white", bg="#409eff",
                                         relief="solid", bd=1, padx=10, pady=2, cursor="hand2")
        self._quick_conn_btn.pack(side=tk.LEFT, padx=4)

        tk.Button(actions, text="刷新任务", command=self.refresh_tasks,
                  font=FONT_SMALL, fg="#303133", bg="#ffffff",
                  relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Button(actions, text="清空任务", command=self.clear_tasks,
                  font=FONT_SMALL, fg="#f56c6c", bg="#ffffff",
                  relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=4)

    def _toggle_connection(self):
        if self.client.is_connected:
            self.client.disconnect()
            self._update_connection_status(False, "已断开服务端", "")
        else:
            self._try_auto_connect()

    def _build_virtual_print_control(self, row, column, columnspan=1):
        content, actions = self._build_titled_section(row=row, column=column, title="虚拟打印控制", columnspan=columnspan)

        # 状态
        status_frame = tk.Frame(content, bg="#ffffff")
        status_frame.pack(fill=tk.X, pady=(0, 6))
        self.vp_status_label = tk.Label(status_frame, text="● 检测中...", font=FONT_SMALL,
                                        fg="#e6a23c", bg="#ffffff")
        self.vp_status_label.pack(side=tk.LEFT, padx=(0, 8))
        self.root.after(500, self._check_virtual_printer_status)

        # 安装/卸载按钮放在标题右侧
        self.vp_install_btn = tk.Button(actions, text="安装", command=self._install_virtual_printer,
                                        font=FONT_SMALL, fg="white", bg="#409eff",
                                        relief="solid", bd=1, padx=10, pady=2, cursor="hand2")
        self.vp_install_btn.pack(side=tk.LEFT, padx=4)
        self.vp_uninstall_btn = tk.Button(actions, text="卸载", command=self._uninstall_virtual_printer,
                                          font=FONT_SMALL, fg="#303133", bg="#ffffff",
                                          relief="solid", bd=1, padx=10, pady=2, cursor="hand2")
        self.vp_uninstall_btn.pack(side=tk.LEFT, padx=4)
        self.vp_uninstall_btn.pack_forget()

        # 目标打印机
        printer_frame = tk.Frame(content, bg="#ffffff")
        printer_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Label(printer_frame, text="目标打印机", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(anchor=tk.W)
        self._vp_selected_printer_var = tk.StringVar()
        self.vp_printer_combo = ttk.Combobox(printer_frame, textvariable=self._vp_selected_printer_var, state="readonly", width=14)
        self.vp_printer_combo.pack(fill=tk.X, pady=(3, 0))
        self.vp_printer_combo.bind("<<ComboboxSelected>>", self._on_vp_printer_changed)

        # 份数
        copies_frame = tk.Frame(content, bg="#ffffff")
        copies_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Label(copies_frame, text="份数", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(anchor=tk.W)
        self.vp_copies_var = tk.IntVar(value=1)
        ttk.Spinbox(copies_frame, from_=1, to=999, textvariable=self.vp_copies_var, width=8).pack(anchor=tk.W, pady=(3, 0))

        # 色彩
        color_frame = tk.Frame(content, bg="#ffffff")
        color_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Label(color_frame, text="色彩", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(anchor=tk.W)
        self.vp_color_var = tk.StringVar(value="黑白")
        ttk.Combobox(color_frame, textvariable=self.vp_color_var, values=["黑白", "彩色"], state="readonly", width=8).pack(anchor=tk.W, pady=(3, 0))

        # 自动打印
        auto_frame = tk.Frame(content, bg="#ffffff")
        auto_frame.pack(fill=tk.X)
        self.vp_auto_var = tk.BooleanVar(value=getattr(self.client, 'virtual_print_auto', False))
        tk.Checkbutton(auto_frame, text="自动打印（捕获后直接提交）",
                       variable=self.vp_auto_var, font=FONT_SMALL,
                       fg="#303133", bg="#ffffff", activebackground="#ffffff",
                       selectcolor="#ffffff", command=self._on_vp_auto_changed).pack(side=tk.LEFT)

    def _build_capture_tasks_section(self, row=2, column=0, columnspan=1, parent=None):
        content, actions = self._build_titled_section(row=row, column=column, title="待打印任务（捕获）", columnspan=columnspan, parent=parent)
        self._vp_jobs_frame = tk.Frame(content, bg="#ffffff")
        self._vp_jobs_frame.pack(fill=tk.BOTH, expand=True)
        self._update_virtual_jobs_list()

    def _build_my_tasks_section(self, row=2, column=1, parent=None):
        content, actions = self._build_titled_section(row=row, column=column, title="我的任务", parent=parent)

        tk.Button(actions, text="刷新", command=self.refresh_tasks,
                 font=FONT_SMALL, fg="#303133", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=4)
        tk.Button(actions, text="重试", command=self.retry_task,
                 font=FONT_SMALL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=4)
        tk.Button(actions, text="取消", command=self.cancel_task,
                 font=FONT_SMALL, fg="#f56c6c", bg="#ffffff",
                 relief="solid", bd=1, padx=10, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=4)

        table_frame = tk.Frame(content, bg="#ffffff")
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("task_id", "printer", "file", "status", "time")
        self.task_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        headings = [("task_id", "任务ID", 80), ("printer", "打印机", 70), ("file", "文件名", 90), ("status", "状态", 50), ("time", "时间", 80)]
        for col, text, width in headings:
            self.task_tree.heading(col, text=text, anchor="center")
            self.task_tree.column(col, width=width, anchor="center", stretch=True)
        self.task_tree.pack(fill=tk.BOTH, expand=True)

    def _build_settings_section(self, row, column, columnspan=1):
        content, actions = self._build_titled_section(row=row, column=column, title="系统设置", columnspan=columnspan)

        # image#2 风格：保存按钮在标题右侧
        tk.Button(actions, text="保存设置", command=self._save_client_settings,
                 font=FONT_SMALL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=14, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))

        config = load_config()

        form = tk.Frame(content, bg="#ffffff")
        form.pack(fill=tk.X, pady=(2, 0))
        form.grid_columnconfigure(1, weight=1)

        # 服务端IP
        r = 0
        tk.Label(form, text="服务端IP", font=FONT_SMALL, fg="#303133", bg="#ffffff").grid(row=r, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.server_ip_var = tk.StringVar(value=config.get("client", {}).get("server_ip", ""))
        ttk.Entry(form, textvariable=self.server_ip_var, width=12).grid(row=r, column=1, columnspan=2, sticky=tk.EW, pady=4)

        # 端口
        r += 1
        tk.Label(form, text="端口", font=FONT_SMALL, fg="#303133", bg="#ffffff").grid(row=r, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.server_port_var = tk.StringVar(value=str(config.get("client", {}).get("server_port", 8989)))
        ttk.Entry(form, textvariable=self.server_port_var, width=12).grid(row=r, column=1, columnspan=2, sticky=tk.EW, pady=4)

        # 访问码
        r += 1
        tk.Label(form, text="访问码", font=FONT_SMALL, fg="#303133", bg="#ffffff").grid(row=r, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.access_code_var = tk.StringVar(value=config.get("client", {}).get("access_code", ""))
        ttk.Entry(form, textvariable=self.access_code_var, width=12).grid(row=r, column=1, columnspan=2, sticky=tk.EW, pady=4)

        # 开机自启动
        r += 1
        from common.autostart import is_auto_start_enabled
        self.auto_start_var = tk.BooleanVar(value=is_auto_start_enabled("云印宝客户端"))
        ttk.Checkbutton(form, text="开机自启动", variable=self.auto_start_var).grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=4)

    def _save_client_settings(self):
        config = load_config()
        if "client" not in config:
            config["client"] = {}
        ip = self.server_ip_var.get().strip()
        try:
            port = int(self.server_port_var.get())
        except:
            port = 8989
        code = self.access_code_var.get().strip()
        config["client"]["server_ip"] = ip
        config["client"]["server_port"] = port
        config["client"]["access_code"] = code
        save_config(config)
        from common.autostart import set_auto_start
        set_auto_start("云印宝客户端", self.auto_start_var.get())
        # 更新客户端连接凭据并自动连接
        self.client.server_url = f"http://{ip}:{port}"
        self.client.access_code = code
        def do_connect():
            try:
                success, msg = self.client.connect_server(ip, port, code)
                self.root.after(0, lambda: self._update_connection_status(success, msg, self.client.server_url))
            except Exception as e:
                logger.error(f"保存后连接失败: {e}")
        threading.Thread(target=do_connect, daemon=True).start()
        messagebox.showinfo("成功", "设置已保存，正在连接服务端...")

    def _check_virtual_printer_status(self):
        try:
            from common.virtual_printer import check_printer_exists
            exists = check_printer_exists()
            if exists:
                self.vp_status_label.config(text="● 已安装", fg="#67c23a")
                self.vp_install_btn.pack_forget()
                self.vp_uninstall_btn.pack(side=tk.LEFT, padx=5)
                self._start_virtual_printer_monitor()
            else:
                self.vp_status_label.config(text="● 未安装", fg="#f56c6c")
                self.vp_install_btn.pack(side=tk.LEFT, padx=5)
                self.vp_uninstall_btn.pack_forget()
        except Exception as e:
            self.vp_status_label.config(text=f"● 检测失败: {e}", fg="#f56c6c")

    def _install_virtual_printer(self):
        def do_install():
            try:
                from common.virtual_printer import create_virtual_printer
                success, msg = create_virtual_printer()
                self.root.after(0, lambda: self._after_vp_install(success, msg))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", f"安装失败: {e}"))
        threading.Thread(target=do_install, daemon=True).start()
        self.vp_install_btn.config(state="disabled", text="安装中...")

    def _after_vp_install(self, success, msg):
        self.vp_install_btn.config(state="normal", text="安装虚拟打印机")
        if success:
            messagebox.showinfo("成功", msg)
            self._check_virtual_printer_status()
        else:
            messagebox.showerror("失败", msg)

    def _uninstall_virtual_printer(self):
        if not messagebox.askyesno("确认", "确定要卸载云印宝打印机吗？"):
            return
        def do_uninstall():
            try:
                from common.virtual_printer import remove_virtual_printer
                success = remove_virtual_printer()
                self.root.after(0, lambda: self._after_vp_uninstall(success))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", f"卸载失败: {e}"))
        threading.Thread(target=do_uninstall, daemon=True).start()

    def _after_vp_uninstall(self, success):
        if success:
            messagebox.showinfo("成功", "虚拟打印机已卸载")
            self._check_virtual_printer_status()
        else:
            messagebox.showerror("失败", "卸载失败，请手动删除")

    def _start_virtual_printer_monitor(self):
        if self._virtual_printer:
            return
        try:
            from common.virtual_printer import VirtualPrinterManager
            self._virtual_printer = VirtualPrinterManager(on_pdf_captured=self._on_pdf_captured)
            self._virtual_printer.start()
            logger.info("虚拟打印机监控已启动")
        except Exception as e:
            logger.error(f"启动虚拟打印机监控失败: {e}")

    def _on_vp_auto_changed(self):
        """自动打印开关变化时保存配置"""
        self.client.virtual_print_auto = self.vp_auto_var.get()
        self.client._save_config()

    def _on_vp_printer_changed(self, event=None):
        """选择目标打印机后，自动切换色彩模式为该打印机的默认值"""
        printer_name = self._vp_selected_printer_var.get()
        if not printer_name:
            return
        for p in self.client.printers:
            if p.get('name') == printer_name:
                default_color = p.get('color_mode', 'black')
                display = "彩色" if default_color == 'color' else "黑白"
                self.vp_color_var.set(display)
                break

    def _on_quick_printer_changed(self, event=None):
        """快速打印页面选择打印机后，自动切换色彩模式"""
        printer_name = self.printer_var.get()
        if not printer_name:
            return
        for p in self.client.printers:
            if p.get('name') == printer_name:
                default_color = p.get('color_mode', 'black')
                display = "彩色" if default_color == 'color' else "黑白"
                self.color_var.set(display)
                break

    def _on_pdf_captured(self, pdf_path, document_name):
        logger.info(f"捕获到PDF: {pdf_path} - {document_name}")

        # 自动打印模式：直接提交，不进入列表
        if self.vp_auto_var.get():
            self._auto_submit_virtual_job(pdf_path=pdf_path, document_name=document_name, silent=True)
            return

        # 手动模式：添加到列表等待确认
        job_info = {
            'pdf_path': pdf_path,
            'document_name': document_name,
            'file_size': os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0,
            'captured_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'status': 'captured',
        }
        self._virtual_print_jobs.append(job_info)
        idx = len(self._virtual_print_jobs) - 1
        self.root.after(0, self._update_virtual_jobs_list)

        # 通知托盘
        try:
            if self._tray_visible and self._tray:
                self._tray.notify("捕获到新打印任务", document_name)
        except:
            pass

        # 自动上传并提交到服务端
        self._auto_submit_virtual_job(idx=idx)

    def _update_virtual_jobs_list(self):
        if not self._vp_jobs_frame:
            return
        for widget in self._vp_jobs_frame.winfo_children():
            widget.destroy()

        if not self._virtual_print_jobs:
            tk.Label(self._vp_jobs_frame, text="暂无待打印任务\n\n在任意软件中选择「云印宝打印机」打印，任务将自动提交到服务端",
                     font=FONT_NORMAL, fg="#303133", bg="#ffffff",
                     justify="center").pack(expand=True, pady=40)
            return

        status_text = {"captured": "已捕获", "uploading": "上传中...", "submitted": "已提交", "failed": "提交失败"}
        status_color = {"captured": "#e6a23c", "uploading": "#409eff", "submitted": "#67c23a", "failed": "#f56c6c"}

        for i, job in enumerate(reversed(self._virtual_print_jobs)):
            idx = len(self._virtual_print_jobs) - 1 - i
            job_status = job.get('status', 'captured')
            job_frame = tk.Frame(self._vp_jobs_frame, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
            job_frame.pack(fill=tk.X, pady=(0, 6))

            info_frame = tk.Frame(job_frame, bg="#ffffff")
            info_frame.pack(fill=tk.X, padx=10, pady=8)

            name_label = tk.Label(info_frame, text=job['document_name'], font=FONT_NORMAL,
                                  fg="#303133", bg="#ffffff", anchor="w")
            name_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

            st_label = tk.Label(info_frame, text=status_text.get(job_status, job_status),
                                font=FONT_SMALL, fg=status_color.get(job_status, "#303133"), bg="#ffffff")
            st_label.pack(side=tk.LEFT, padx=(0, 8))

            size_str = self._format_size(job['file_size'])
            size_label = tk.Label(info_frame, text=size_str, font=FONT_SMALL,
                                  fg="#303133", bg="#ffffff")
            size_label.pack(side=tk.LEFT, padx=(0, 10))

            time_label = tk.Label(info_frame, text=job['captured_time'], font=FONT_SMALL,
                                  fg="#303133", bg="#ffffff")
            time_label.pack(side=tk.LEFT, padx=(0, 10))

            btn_frame = tk.Frame(info_frame, bg="#ffffff")
            btn_frame.pack(side=tk.RIGHT)

            if job_status == 'failed':
                tk.Button(btn_frame, text="重试", command=lambda j=idx: self._auto_submit_virtual_job(j),
                         font=FONT_NORMAL, fg="white", bg="#409eff",
                         relief="solid", bd=1, padx=12, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=3)
            tk.Button(btn_frame, text="预览", command=lambda j=idx: self._preview_virtual_job(j),
                     font=FONT_NORMAL, fg="#303133", bg="#ffffff",
                     relief="solid", bd=1, padx=12, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=3)
            tk.Button(btn_frame, text="删除", command=lambda j=idx: self._delete_virtual_job(j),
                     font=FONT_NORMAL, fg="#f56c6c", bg="#ffffff",
                     relief="solid", bd=1, padx=12, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=3)

    def _auto_submit_virtual_job(self, idx=None, pdf_path=None, document_name=None, silent=False):
        """自动将虚拟打印捕获的PDF上传并提交到服务端

        两种调用方式：
        - idx: 从任务列表中提交（手动/半自动模式）
        - pdf_path + document_name: 静默自动提交（自动打印模式）
        """
        if idx is not None:
            if idx < 0 or idx >= len(self._virtual_print_jobs):
                return
            job = self._virtual_print_jobs[idx]
            pdf_path = job['pdf_path']
            document_name = job['document_name']
        elif pdf_path and document_name:
            job = None
        else:
            logger.warning("_auto_submit_virtual_job 调用参数错误")
            return

        def update_job(status=None, error=None, task_id=None):
            if job:
                if status:
                    job['status'] = status
                if error:
                    job['error'] = error
                if task_id:
                    job['task_id'] = task_id
                self.root.after(0, self._update_virtual_jobs_list)

        if not self.client.is_connected:
            logger.warning("虚拟打印自动提交失败: 未连接服务端")
            if not silent:
                update_job('failed', '未连接服务端')
            return

        # 获取服务端打印机列表
        printers = self.client.get_printers()
        if not printers:
            logger.warning("虚拟打印自动提交失败: 服务端无可用打印机")
            if not silent:
                update_job('failed', '服务端无可用打印机')
            return

        # 优先使用用户选择的目标打印机，否则用默认打印机
        printer_id = None
        printer_name = self._vp_selected_printer_var.get() if self._vp_selected_printer_var else None
        if printer_name:
            for p in printers:
                if p['name'] == printer_name:
                    printer_id = p['id']
                    break
        if not printer_id:
            for p in printers:
                if p.get('is_default'):
                    printer_id = p['id']
                    break
        if not printer_id:
            printer_id = printers[0]['id']

        if not pdf_path or not os.path.exists(pdf_path):
            logger.warning("虚拟打印自动提交失败: 文件不存在")
            if not silent:
                update_job('failed', '文件不存在')
            return

        # 非静默模式：标记为上传中
        if not silent:
            update_job('uploading')

        file_size = os.path.getsize(pdf_path)

        def do_submit():
            try:
                # 上传文件
                success, msg, upload_info = self.client.upload_file(pdf_path)
                if not success:
                    logger.error(f"虚拟打印上传失败: {msg}")
                    if not silent:
                        update_job('failed', f'上传失败: {msg}')
                    return

                # 提交打印任务
                color_mode = "color" if (hasattr(self, 'vp_color_var') and self.vp_color_var.get() == "彩色") else "black"
                copies = self.vp_copies_var.get() if hasattr(self, 'vp_copies_var') else 1

                success, msg, task_id = self.client.submit_print(
                    printer_id=printer_id,
                    file_name=upload_info.get('file_name', os.path.basename(pdf_path)),
                    file_path=upload_info.get('file_path', ''),
                    file_size=upload_info.get('file_size', file_size),
                    pages=upload_info.get('pages', 0),
                    copies=copies,
                    color_mode=color_mode,
                )

                if success:
                    logger.info(f"虚拟打印任务已提交: {task_id}")
                    if not silent:
                        update_job('submitted', task_id=task_id)
                    # 托盘通知
                    try:
                        if self._tray_visible and self._tray:
                            self._tray.notify("打印任务已提交", f"{document_name} -> {task_id}")
                    except:
                        pass
                else:
                    logger.error(f"虚拟打印提交失败: {msg}")
                    if not silent:
                        update_job('failed', f'提交失败: {msg}')
            except Exception as e:
                logger.error(f"虚拟打印自动提交异常: {e}")
                if not silent:
                    update_job('failed', str(e))

        threading.Thread(target=do_submit, daemon=True).start()

    def _print_virtual_job(self, idx):
        """手动触发打印（重试失败的或重新提交已捕获的任务）"""
        if idx < 0 or idx >= len(self._virtual_print_jobs):
            return
        job = self._virtual_print_jobs[idx]
        if job.get('status') == 'submitted':
            messagebox.showinfo("提示", "该任务已提交到服务端")
            return
        # 重置状态并自动提交
        job['status'] = 'captured'
        self._auto_submit_virtual_job(idx)

    def _print_all_virtual_jobs(self):
        """重新提交所有失败的任务"""
        failed_indices = [i for i, j in enumerate(self._virtual_print_jobs) if j.get('status') == 'failed']
        if not failed_indices:
            messagebox.showinfo("提示", "没有需要重试的任务")
            return
        for idx in failed_indices:
            self._virtual_print_jobs[idx]['status'] = 'captured'
            self._auto_submit_virtual_job(idx)

    def _preview_virtual_job(self, idx):
        if idx < 0 or idx >= len(self._virtual_print_jobs):
            return
        job = self._virtual_print_jobs[idx]
        pdf_path = job['pdf_path']
        if not os.path.exists(pdf_path):
            messagebox.showerror("错误", "文件不存在")
            return
        try:
            os.startfile(pdf_path)
        except Exception as e:
            messagebox.showerror("错误", f"打开文件失败: {e}")

    def _delete_virtual_job(self, idx):
        if idx < 0 or idx >= len(self._virtual_print_jobs):
            return
        job = self._virtual_print_jobs.pop(idx)
        try:
            if os.path.exists(job['pdf_path']):
                os.remove(job['pdf_path'])
        except:
            pass
        self._update_virtual_jobs_list()

    def _clear_virtual_jobs(self):
        if not self._virtual_print_jobs:
            return
        if not messagebox.askyesno("确认", "确定要清空所有待打印任务吗？"):
            return
        for job in self._virtual_print_jobs:
            try:
                if os.path.exists(job['pdf_path']):
                    os.remove(job['pdf_path'])
            except:
                pass
        self._virtual_print_jobs = []
        self._update_virtual_jobs_list()

    def _try_auto_connect(self):
        def do_connect():
            try:
                url = self.client.server_url
                ip = url.replace('http://', '').split(':')[0]
                port = url.split(':')[-1]
                success, msg = self.client.connect_server(ip, port, self.client.access_code)
                self.root.after(0, lambda: self._update_connection_status(success, msg, url))
            except Exception as e:
                logger.error(f"自动连接失败: {e}")
                self.root.after(0, lambda: self._update_connection_status(False, str(e), ""))
        threading.Thread(target=do_connect, daemon=True).start()

    def _update_connection_status(self, connected, msg, server_url=""):
        if connected:
            self.status_label.config(text="● 已连接", fg="#67c23a")
            self._update_printer_list()
            self.refresh_tasks()
        else:
            self.status_label.config(text="● 未连接", fg="#f56c6c")

    def _update_printer_list(self):
        printers = self.client.get_printers()
        names = [p['name'] for p in printers]
        if hasattr(self, 'vp_printer_combo') and self.vp_printer_combo:
            self.vp_printer_combo['values'] = names
        if printers:
            default_idx = 0
            for i, p in enumerate(printers):
                if p.get('is_default'):
                    default_idx = i
                    break
            if hasattr(self, '_vp_selected_printer_var') and self._vp_selected_printer_var:
                self._vp_selected_printer_var.set(names[default_idx])
                self._on_vp_printer_changed()

    def _update_file_list(self):
        for widget in self.file_list_frame.winfo_children():
            widget.destroy()

        if not self.selected_files:
            tk.Label(self.file_list_frame, text="未选择文件，点击「浏览...」选择要打印的文件", font=FONT_NORMAL,
                     fg="#303133", bg="#ffffff", anchor="w").pack(side=tk.LEFT, padx=5)
            return

        total_size = sum(os.path.getsize(f) for f in self.selected_files if os.path.exists(f))

        for i, filepath in enumerate(self.selected_files):
            if not os.path.exists(filepath):
                continue
            name = os.path.basename(filepath)
            size = os.path.getsize(filepath)
            size_str = self._format_size(size)

            row_frame = tk.Frame(self.file_list_frame, bg="#ffffff")
            row_frame.pack(fill=tk.X, pady=(0, 2))

            num_label = tk.Label(row_frame, text=f"{i+1}.", font=FONT_NORMAL,
                                 fg="#409eff", bg="#ffffff", width=3)
            num_label.pack(side=tk.LEFT)

            name_label = tk.Label(row_frame, text=f"{name}", font=FONT_NORMAL,
                                  fg="#303133", bg="#ffffff", anchor="w")
            name_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

            size_label = tk.Label(row_frame, text=f"({size_str})", font=FONT_NORMAL,
                                  fg="#303133", bg="#ffffff")
            size_label.pack(side=tk.LEFT, padx=(5, 10))

            tk.Button(row_frame, text="✕", command=lambda f=filepath: self.remove_file(f),
                     font=FONT_NORMAL, fg="#f56c6c", bg="#ffffff",
                     relief="solid", bd=1, width=2, cursor="hand2").pack(side=tk.RIGHT)

        total_label = tk.Label(self.file_list_frame,
                               text=f"\n共 {len(self.selected_files)} 个文件，总计 {self._format_size(total_size)}",
                               font=FONT_NORMAL, fg="#303133", bg="#ffffff", anchor="w")
        total_label.pack(side=tk.LEFT, pady=(5, 0))

    def remove_file(self, filepath):
        if filepath in self.selected_files:
            self.selected_files.remove(filepath)
            self._update_file_list()

    def clear_selected_files(self):
        self.selected_files = []
        self._update_file_list()

    def select_file(self):
        filetypes = [
            ("所有支持的文件", "*.pdf *.doc *.docx *.xls *.xlsx *.txt *.jpg *.jpeg *.png *.bmp *.gif *.tiff"),
            ("PDF文件", "*.pdf"),
            ("Word文档", "*.doc *.docx"),
            ("Excel文档", "*.xls *.xlsx"),
            ("图片文件", "*.jpg *.jpeg *.png *.bmp *.gif *.tiff"),
            ("文本文件", "*.txt"),
            ("所有文件", "*.*"),
        ]
        filenames = filedialog.askopenfilenames(title="选择要打印的文件", filetypes=filetypes)
        if filenames:
            for f in filenames:
                if f not in self.selected_files:
                    self.selected_files.append(f)
            self._update_file_list()

    def _format_size(self, size):
        if size < 1024:
            return f"{size} B"
        elif size < 1024*1024:
            return f"{size/1024:.1f} KB"
        else:
            return f"{size/(1024*1024):.1f} MB"

    def start_print(self):
        if not self.client.is_connected:
            messagebox.showwarning("提示", "请先连接打印服务端")
            self._switch_page("settings")
            return

        if not self.selected_files:
            messagebox.showwarning("提示", "请先选择要打印的文件")
            return

        printer_name = self.printer_var.get()
        if not printer_name:
            messagebox.showwarning("提示", "请选择打印机")
            return

        printer_id = None
        for p in self.client.printers:
            if p['name'] == printer_name:
                printer_id = p['id']
                break

        if not printer_id:
            messagebox.showerror("错误", "无效的打印机，请刷新打印机列表")
            return

        copies = self.copies_var.get()
        color_mode = "color" if self.color_var.get() == "彩色" else "black"
        duplex_map = {"单面": "simplex", "双面长边": "duplex_long", "双面短边": "duplex_short"}
        duplex = duplex_map.get(self.duplex_var.get(), "simplex")
        paper_size = self.paper_var.get()
        page_range = self.page_range_var.get()
        orientation = "landscape" if self.orientation_var.get() == "横向" else "portrait"
        margin_top = self.margin_top_var.get()
        margin_bottom = self.margin_bottom_var.get()
        margin_left = self.margin_left_var.get()
        margin_right = self.margin_right_var.get()
        center_h = 1 if self.center_h_var.get() else 0
        center_v = 1 if self.center_v_var.get() else 0

        if len(self.selected_files) == 1:
            filepath = self.selected_files[0]
            file_name = os.path.basename(filepath)
            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

            print_info = {
                'file_path': filepath,
                'file_name': file_name,
                'file_size': file_size,
                'pages': 0,
                'printer_id': printer_id,
                'printer_name': printer_name,
                'copies': copies,
                'color_mode': color_mode,
                'duplex': duplex,
                'paper_size': paper_size,
                'page_range': page_range,
                'orientation': orientation,
                'margin_top': margin_top,
                'margin_bottom': margin_bottom,
                'margin_left': margin_left,
                'margin_right': margin_right,
                'center_horizontal': center_h,
                'center_vertical': center_v,
            }

            confirmed = self._show_print_preview(print_info)
            if not confirmed:
                return

            self.print_btn.config(state=tk.DISABLED)
            self.progress_label.config(text="正在上传文件...", fg="#409eff")

            def do_single_print():
                try:
                    success, msg, upload_info = self.client.upload_file(filepath)
                    if not success:
                        self.root.after(0, lambda: self._print_error(msg))
                        return

                    success, msg, task_id = self.client.submit_print(
                        printer_id=printer_id,
                        file_name=upload_info['file_name'],
                        file_path=upload_info['file_path'],
                        file_size=upload_info['file_size'],
                        pages=upload_info.get('pages', 0),
                        copies=copies,
                        color_mode=color_mode,
                        duplex=duplex,
                        paper_size=paper_size,
                        page_range=page_range,
                        orientation=orientation,
                        margin_top=margin_top,
                        margin_bottom=margin_bottom,
                        margin_left=margin_left,
                        margin_right=margin_right,
                        center_horizontal=center_h,
                        center_vertical=center_v,
                        task_id=upload_info.get('task_id')
                    )

                    if success:
                        self.root.after(0, lambda: self._print_success(task_id))
                    else:
                        self.root.after(0, lambda: self._print_error(msg))
                except Exception as e:
                    self.root.after(0, lambda: self._print_error(str(e)))

            threading.Thread(target=do_single_print, daemon=True).start()
        else:
            self._batch_print_files(self.selected_files[:], printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v)

    def _batch_print_files(self, files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation="portrait", margin_top=0, margin_bottom=0, margin_left=0, margin_right=0, center_h=0, center_v=0, index=0, results=None):
        if results is None:
            results = []

        if index >= len(files):
            success_count = sum(1 for r in results if r[0])
            fail_count = len(results) - success_count
            error_messages = [r[1] for r in results if not r[0]]
            self._print_batch_result(success_count, fail_count, error_messages)
            return

        filepath = files[index]
        if not os.path.exists(filepath):
            results.append((False, f"{os.path.basename(filepath)}: 文件不存在"))
            self.root.after(50, lambda: self._batch_print_files(files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v, index+1, results))
            return

        file_name = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)

        print_info = {
            'file_path': filepath,
            'file_name': file_name,
            'file_size': file_size,
            'pages': 0,
            'printer_id': printer_id,
            'printer_name': printer_name,
            'copies': copies,
            'color_mode': color_mode,
            'duplex': duplex,
            'paper_size': paper_size,
            'page_range': page_range,
            'orientation': orientation,
            'margin_top': margin_top,
            'margin_bottom': margin_bottom,
            'margin_left': margin_left,
            'margin_right': margin_right,
            'center_horizontal': center_h,
            'center_vertical': center_v,
            'is_batch': True,
            'batch_index': index + 1,
            'batch_total': len(files),
        }

        self.progress_label.config(text=f"预览文件 {index+1}/{len(files)}: {file_name}", fg="#409eff")

        confirmed = self._show_print_preview(print_info)
        if not confirmed:
            if index + 1 < len(files):
                if not messagebox.askyesno("批量打印", f"已跳过 {file_name}，是否继续打印剩余 {len(files) - index - 1} 个文件？"):
                    self.print_btn.config(state=tk.NORMAL)
                    self.progress_label.config(text="批量打印已取消", fg="#e6a23c")
                    return
            else:
                self.print_btn.config(state=tk.NORMAL)
                success_count = sum(1 for r in results if r[0])
                self.progress_label.config(text=f"批量打印完成，成功 {success_count} 个", fg="#67c23a" if success_count > 0 else "#f56c6c")
                return
            self.root.after(50, lambda: self._batch_print_files(files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v, index+1, results))
            return

        self.print_btn.config(state=tk.DISABLED)
        self.progress_label.config(text=f"正在上传 {index+1}/{len(files)}: {file_name}", fg="#409eff")

        def do_upload_and_print():
            try:
                success, msg, upload_info = self.client.upload_file(filepath)
                if not success:
                    self.root.after(0, lambda: self._batch_file_failed(files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v, index, results, f"{file_name}: {msg}"))
                    return

                success, msg, task_id = self.client.submit_print(
                    printer_id=printer_id,
                    file_name=upload_info['file_name'],
                    file_path=upload_info['file_path'],
                    file_size=upload_info['file_size'],
                    pages=upload_info.get('pages', 0),
                    copies=copies,
                    color_mode=color_mode,
                    duplex=duplex,
                    paper_size=paper_size,
                    page_range=page_range,
                    orientation=orientation,
                    margin_top=margin_top,
                    margin_bottom=margin_bottom,
                    margin_left=margin_left,
                    margin_right=margin_right,
                    center_horizontal=center_h,
                    center_vertical=center_v,
                    task_id=upload_info.get('task_id')
                )

                if success:
                    self.root.after(0, lambda: self._batch_file_success(files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v, index, results, task_id))
                else:
                    self.root.after(0, lambda: self._batch_file_failed(files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v, index, results, f"{file_name}: {msg}"))
            except Exception as e:
                self.root.after(0, lambda: self._batch_file_failed(files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v, index, results, f"{file_name}: {str(e)}"))

        threading.Thread(target=do_upload_and_print, daemon=True).start()

    def _batch_file_success(self, files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation="portrait", margin_top=0, margin_bottom=0, margin_left=0, margin_right=0, center_h=0, center_v=0, index=0, results=None, task_id=None):
        results.append((True, task_id))
        self.progress_label.config(text=f"[OK] {os.path.basename(files[index])} 已提交", fg="#67c23a")
        self.refresh_tasks()
        self.root.after(300, lambda: self._batch_print_files(files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v, index+1, results))

    def _batch_file_failed(self, files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation="portrait", margin_top=0, margin_bottom=0, margin_left=0, margin_right=0, center_h=0, center_v=0, index=0, results=None, error_msg=""):
        results.append((False, error_msg))
        file_name = os.path.basename(files[index])
        self.progress_label.config(text=f"[FAIL] {file_name} 失败", fg="#f56c6c")
        if messagebox.askyesno("打印失败", f"{file_name} 打印失败：\n{error_msg}\n\n是否继续打印剩余文件？"):
            self.root.after(300, lambda: self._batch_print_files(files, printer_id, printer_name, copies, color_mode, duplex, paper_size, page_range, orientation, margin_top, margin_bottom, margin_left, margin_right, center_h, center_v, index+1, results))
        else:
            self.print_btn.config(state=tk.NORMAL)
            success_count = sum(1 for r in results if r[0])
            self.progress_label.config(text=f"批量打印中断，成功 {success_count} 个", fg="#e6a23c")

    def _print_success(self, task_id):
        self.print_btn.config(state=tk.NORMAL)
        self.progress_label.config(text=f"[OK] 打印任务已提交: {task_id[:18]}...", fg="#67c23a")
        self.selected_files = []
        self._update_file_list()
        self.refresh_tasks()
        self.root.after(5000, lambda: self.progress_label.config(text="", fg="#409eff"))

    def _print_error(self, msg):
        self.print_btn.config(state=tk.NORMAL)
        self.progress_label.config(text=f"[FAIL] {msg}", fg="#f56c6c")
        messagebox.showerror("打印失败", msg)

    def _print_batch_result(self, success_count, fail_count, errors):
        self.print_btn.config(state=tk.NORMAL)
        self.refresh_tasks()

        if fail_count == 0:
            self.progress_label.config(text=f"[OK] 批量打印完成，共 {success_count} 个文件", fg="#67c23a")
            messagebox.showinfo("批量打印完成", f"成功提交 {success_count} 个打印任务")
        elif success_count == 0:
            self.progress_label.config(text=f"[FAIL] 批量打印失败", fg="#f56c6c")
            msg = f"所有 {fail_count} 个文件打印失败：\n\n"
            msg += "\n".join(errors[:10])
            if len(errors) > 10:
                msg += f"\n... 还有 {len(errors) - 10} 个错误"
            messagebox.showerror("批量打印失败", msg)
        else:
            self.progress_label.config(text=f"批量打印完成，{success_count}成功，{fail_count}失败", fg="#e6a23c")
            msg = f"批量打印完成：\n成功 {success_count} 个\n失败 {fail_count} 个\n\n"
            if errors:
                msg += "失败的文件：\n"
                msg += "\n".join(errors[:5])
                if len(errors) > 5:
                    msg += f"\n... 还有 {len(errors) - 5} 个错误"
            messagebox.showinfo("批量打印完成", msg)

    def _show_print_preview(self, print_info):
        """用操作系统原生软件打开文件预览，同时显示打印设置确认对话框"""
        file_path = print_info.get('file_path', '')
        file_name = print_info.get('file_name', '')
        file_size = print_info.get('file_size', 0)
        printer_name = print_info.get('printer_name', '')
        is_batch = print_info.get('is_batch', False)

        # 打开文件用原生软件预览
        def open_native_preview():
            try:
                os.startfile(file_path)
            except Exception as e:
                logger.warning(f"打开原生预览失败: {e}")
                try:
                    import subprocess
                    if os.name == 'nt':
                        subprocess.Popen(['cmd', '/c', 'start', '', file_path], creationflags=0x08000000)
                    else:
                        subprocess.Popen(['xdg-open', file_path])
                except Exception as e2:
                    messagebox.showwarning("提示", f"无法打开预览: {e2}")

        # 首次自动打开预览
        open_native_preview()

        dialog = tk.Toplevel(self.root)
        if is_batch:
            dialog.title(f"打印确认 ({print_info.get('batch_index', 0)}/{print_info.get('batch_total', 0)})")
        else:
            dialog.title("打印确认")
        dialog.geometry("560x620")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        try:
            dialog.state('zoomed')
        except Exception:
            pass

        # 居中显示
        dialog.update_idletasks()
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        ww = 560
        wh = 620
        x = (sw - ww) // 2
        y = (sh - wh) // 2
        dialog.geometry(f"{ww}x{wh}+{x}+{y}")

        confirmed = [False]

        def on_confirm():
            # 收集对话框中的设置值并更新print_info
            print_info['copies'] = copies_var.get()
            print_info['color_mode'] = "color" if color_var.get() == "彩色" else "black"
            duplex_map = {"单面": "simplex", "双面长边": "duplex_long", "双面短边": "duplex_short"}
            print_info['duplex'] = duplex_map.get(duplex_var.get(), "simplex")
            print_info['paper_size'] = paper_var.get()
            print_info['page_range'] = page_range_var.get()
            print_info['orientation'] = "landscape" if orientation_var.get() == "横向" else "portrait"
            print_info['margin_top'] = margin_top_var.get()
            print_info['margin_bottom'] = margin_bottom_var.get()
            print_info['margin_left'] = margin_left_var.get()
            print_info['margin_right'] = margin_right_var.get()
            print_info['center_horizontal'] = 1 if center_h_var.get() else 0
            print_info['center_vertical'] = 1 if center_v_var.get() else 0
            confirmed[0] = True
            dialog.destroy()

        def on_cancel():
            confirmed[0] = False
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_cancel)

        # 顶部信息栏
        info_frame = tk.Frame(dialog, bg="#f5f7fa", height=50)
        info_frame.pack(fill=tk.X)
        info_frame.pack_propagate(False)

        size_str = self._format_size(file_size)
        info_text = f"  {file_name}  |  {size_str}  |  打印机: {printer_name}"
        if is_batch:
            info_text += f"  |  {print_info.get('batch_index', 0)}/{print_info.get('batch_total', 0)}"
        tk.Label(info_frame, text=info_text, font=FONT_NORMAL, fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=10, pady=12)

        # 预览提示
        preview_frame = tk.Frame(dialog, bg="#ffffff", padx=15, pady=10)
        preview_frame.pack(fill=tk.X)
        tk.Label(preview_frame, text="文件已用系统默认软件打开预览", font=FONT_NORMAL, fg="#409eff", bg="#ffffff").pack(side=tk.LEFT)
        tk.Button(preview_frame, text="重新预览", command=open_native_preview,
                  font=FONT_NORMAL, fg="#409eff", bg="#ffffff",
                  relief="solid", bd=1, padx=15, pady=2, cursor="hand2").pack(side=tk.RIGHT)

        # 设置区域
        settings_frame = tk.Frame(dialog, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1, padx=15, pady=12)
        settings_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 10))

        tk.Label(settings_frame, text="打印设置", font=FONT_NORMAL, fg="#303133", bg="#ffffff").pack(anchor=tk.W, pady=(0, 8))

        grid = tk.Frame(settings_frame, bg="#ffffff")
        grid.pack(fill=tk.X)

        # 从print_info预填值
        copies_var = tk.IntVar(value=print_info.get('copies', 1))
        color_var = tk.StringVar(value="彩色" if print_info.get('color_mode') == "color" else "黑白")
        duplex_map_reverse = {"simplex": "单面", "duplex_long": "双面长边", "duplex_short": "双面短边"}
        duplex_var = tk.StringVar(value=duplex_map_reverse.get(print_info.get('duplex', 'simplex'), "单面"))
        paper_var = tk.StringVar(value=print_info.get('paper_size', 'A4'))
        page_range_var = tk.StringVar(value=print_info.get('page_range', ''))
        orientation_var = tk.StringVar(value="横向" if print_info.get('orientation') == 'landscape' else "纵向")
        margin_top_var = tk.DoubleVar(value=print_info.get('margin_top', 10))
        margin_bottom_var = tk.DoubleVar(value=print_info.get('margin_bottom', 10))
        margin_left_var = tk.DoubleVar(value=print_info.get('margin_left', 10))
        margin_right_var = tk.DoubleVar(value=print_info.get('margin_right', 10))
        center_h_var = tk.BooleanVar(value=bool(print_info.get('center_horizontal', 1)))
        center_v_var = tk.BooleanVar(value=bool(print_info.get('center_vertical', 1)))

        row = 0
        tk.Label(grid, text="份数:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(grid, from_=1, to=999, textvariable=copies_var, width=8).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tk.Label(grid, text="色彩:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=row, column=2, sticky=tk.W, padx=5, pady=5)
        ttk.Combobox(grid, textvariable=color_var, values=["黑白", "彩色"], state="readonly", width=8).grid(row=row, column=3, sticky=tk.W, padx=5, pady=5)

        row += 1
        tk.Label(grid, text="双面:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Combobox(grid, textvariable=duplex_var, values=["单面", "双面长边", "双面短边"], state="readonly", width=10).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tk.Label(grid, text="纸张:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=row, column=2, sticky=tk.W, padx=5, pady=5)
        ttk.Combobox(grid, textvariable=paper_var, values=["A4", "A3", "A5", "Letter"], state="readonly", width=8).grid(row=row, column=3, sticky=tk.W, padx=5, pady=5)

        row += 1
        tk.Label(grid, text="方向:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Combobox(grid, textvariable=orientation_var, values=["纵向", "横向"], state="readonly", width=8).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tk.Label(grid, text="页码范围:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=row, column=2, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(grid, textvariable=page_range_var, width=12).grid(row=row, column=3, sticky=tk.W, padx=5, pady=5)

        row += 1
        tk.Label(grid, text="边距(mm):", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        margin_frame = tk.Frame(grid, bg="#ffffff")
        margin_frame.grid(row=row, column=1, columnspan=3, sticky=tk.W, padx=5, pady=5)
        tk.Label(margin_frame, text="上", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(side=tk.LEFT)
        ttk.Spinbox(margin_frame, from_=0, to=100, textvariable=margin_top_var, width=4).pack(side=tk.LEFT, padx=2)
        tk.Label(margin_frame, text="下", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(side=tk.LEFT)
        ttk.Spinbox(margin_frame, from_=0, to=100, textvariable=margin_bottom_var, width=4).pack(side=tk.LEFT, padx=2)
        tk.Label(margin_frame, text="左", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(side=tk.LEFT)
        ttk.Spinbox(margin_frame, from_=0, to=100, textvariable=margin_left_var, width=4).pack(side=tk.LEFT, padx=2)
        tk.Label(margin_frame, text="右", font=FONT_SMALL, fg="#303133", bg="#ffffff").pack(side=tk.LEFT)
        ttk.Spinbox(margin_frame, from_=0, to=100, textvariable=margin_right_var, width=4).pack(side=tk.LEFT, padx=2)

        row += 1
        ttk.Checkbutton(grid, text="水平居中", variable=center_h_var).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)
        ttk.Checkbutton(grid, text="垂直居中", variable=center_v_var).grid(row=row, column=2, columnspan=2, sticky=tk.W, padx=5, pady=5)

        # 底部按钮
        btn_frame = tk.Frame(dialog, bg="#ffffff", padx=15, pady=10)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        tk.Button(btn_frame, text="取消", command=on_cancel,
                  font=FONT_NORMAL, fg="#303133", bg="#ffffff",
                  relief="solid", bd=1, padx=30, pady=5, cursor="hand2").pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(btn_frame, text="确认打印", command=on_confirm,
                  font=FONT_NORMAL, fg="white", bg="#409eff",
                  relief="solid", bd=1, padx=30, pady=5, cursor="hand2").pack(side=tk.RIGHT)

        dialog.wait_window()
        return confirmed[0]

    def refresh_tasks(self):
        if not self.client.is_connected:
            return

        def do_refresh():
            try:
                tasks = self.client.get_my_tasks()
                self.root.after(0, lambda: self._update_task_list(tasks))
            except Exception as e:
                logger.error(f"刷新任务失败: {e}")

        threading.Thread(target=do_refresh, daemon=True).start()

    def _update_task_list(self, tasks):
        if not hasattr(self, 'task_tree'):
            return

        # 打印成功/失败：仅在状态发生迁移（首次进入完成/失败）时右下角弹窗提示（5秒自动关闭）
        for t in tasks:
            st = t.get('status', '')
            tid = t.get('task_id', '')
            prev = self._task_status_cache.get(tid)
            if st in ('completed', 'failed') and prev is not None and prev != st:
                self._show_toast(
                    "打印成功" if st == "completed" else "打印失败",
                    f"{t.get('file_name', tid)}",
                    is_error=(st == "failed")
                )
            self._task_status_cache[tid] = st

        for item in self.task_tree.get_children():
            self.task_tree.delete(item)

        status_text = {"pending": "等待中", "printing": "打印中", "completed": "已完成", "failed": "失败", "cancelled": "已取消", "expired": "已过期"}

        for i, t in enumerate(tasks):
            time_str = time.strftime("%m-%d %H:%M", time.localtime(t['created_at'])) if t.get('created_at') else ""
            pages = f"{t.get('pages', 0)}x{t.get('copies', 1)}" if t.get('pages') else "-"
            tag = 'even' if i % 2 == 0 else 'odd'
            self.task_tree.insert("", tk.END, iid=t['task_id'], values=(
                t['task_id'][:18], t.get('printer_name', ''), t.get('file_name', '')[:25],
                pages, status_text.get(t.get('status', ''), t.get('status', '')), time_str
            ), tags=(tag,))

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

    def cancel_task(self):
        selected = self.task_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请选择任务")
            return
        task_id = selected[0]
        if not messagebox.askyesno("确认", "确定要取消此任务吗？"):
            return

        def do_cancel():
            success, msg = self.client.cancel_task(task_id)
            self.root.after(0, lambda: self._after_cancel_task(success, msg))
        threading.Thread(target=do_cancel, daemon=True).start()

    def _after_cancel_task(self, success, msg):
        if success:
            self.refresh_tasks()
            messagebox.showinfo("成功", "任务已取消")
        else:
            messagebox.showerror("失败", msg)

    def retry_task(self):
        selected = self.task_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请选择任务")
            return
        task_id = selected[0]

        def do_retry():
            success, msg, new_id = self.client.retry_task(task_id)
            self.root.after(0, lambda: self._after_retry_task(success, msg, new_id))
        threading.Thread(target=do_retry, daemon=True).start()

    def _after_retry_task(self, success, msg, new_id):
        if success:
            self.refresh_tasks()
            messagebox.showinfo("成功", f"已重新提交: {new_id}")
        else:
            messagebox.showerror("失败", msg)

    def clear_tasks(self):
        if not messagebox.askyesno("确认清空", "确定要清空全部任务吗？\n\n正在打印和等待中的任务不会被删除。\n此操作不可恢复！"):
            return

        def do_clear():
            count, msg = self.client.clear_my_tasks()
            self.root.after(0, lambda: self._after_clear_tasks(count, msg))
        threading.Thread(target=do_clear, daemon=True).start()

    def _after_clear_tasks(self, count, msg):
        if msg:
            messagebox.showerror("失败", msg)
        else:
            messagebox.showinfo("提示", f"已清空 {count} 条任务")
            self.refresh_tasks()

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
                self._tray.notify("云印宝客户端正在后台运行", "右键托盘图标可退出")
        except Exception:
            pass

    def _init_tray(self):
        if self._tray is not None:
            return
        import pystray
        from PIL import Image, ImageDraw

        def create_icon():
            # 优先用 K.ico 的 16x16 子图（与 exe 图标一致），失败时画绿 K 兜底
            ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'K.ico')
            try:
                img = Image.open(ico_path)
                img.size = (16, 16)
                img.load()
                return img
            except Exception:
                img = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.rectangle((0, 0, 15, 15), fill='#67c23a')
                draw.text((5, 1), 'K', fill='white')
                return img

        def on_show(icon, item):
            icon.stop()
            self._tray = None
            self._tray_visible = False
            self.root.after(0, self._show_from_tray)

        def on_quit(icon, item):
            if messagebox.askyesno("退出确认", "确定要退出客户端吗？"):
                icon.stop()
                self._tray = None
                self._tray_visible = False
                self.root.after(0, self._real_quit)

        def on_reconnect(icon, item):
            if self.client.server_url and self.client.access_code:
                self.root.after(0, self._try_auto_connect)

        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", on_show, default=True),
            pystray.MenuItem("重新连接服务端", on_reconnect, enabled=lambda item: bool(self.client.server_url)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", on_quit)
        )

        icon_img = create_icon()
        self._tray = pystray.Icon("云印宝客户端", icon_img, "云印宝客户端", menu)
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
            if self._virtual_printer:
                try:
                    self._virtual_printer.stop()
                except Exception:
                    pass
                self._virtual_printer = None
        except Exception:
            pass
        try:
            self.client.disconnect()
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
    app = ClientGUI()
    app.run()


if __name__ == '__main__':
    main()
