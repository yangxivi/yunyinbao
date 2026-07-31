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

        tk.Label(top_frame, text=f"☁  {APP_NAME} - 客户端",
                 font=FONT_TITLE,
                 fg=theme["topbar_text"], bg=theme["topbar_bg"]).pack(side=tk.LEFT, padx=20, pady=10)

        self.status_label = tk.Label(top_frame, text="● 未连接",
                                     font=FONT_NORMAL,
                                     fg="#f56c6c", bg=theme["topbar_bg"])
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
        self._switch_page("print")

    def _create_sidebar_menu(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        self.sidebar_buttons = {}
        self.sidebar_frames = {}
        menu_items = [
            ("print", "快速打印"),
            ("virtual", "虚拟打印"),
            ("tasks", "我的任务"),
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

        self._create_print_tab()
        self._create_virtual_print_tab()
        self._create_tasks_tab()
        self._create_settings_tab()

    def _create_page_container(self):
        tab = tk.Frame(self.content_frame, bg="#ffffff", highlightthickness=1, highlightbackground="#ebeef5")
        tab.place(x=0, y=0, relwidth=1, relheight=1)
        return tab

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

    def _on_theme_change(self, event=None):
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

    def _create_print_tab(self):
        tab = self._create_page_container()
        self.pages["print"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=15)
        content.pack(fill=tk.BOTH, expand=True)

        file_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        file_frame.pack(fill=tk.X, pady=(0, 12))

        file_header = tk.Frame(file_frame, bg="#f5f7fa", height=32)
        file_header.pack(fill=tk.X)
        file_header.pack_propagate(False)
        tk.Label(file_header, text="选择文件", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=6)

        file_toolbar = tk.Frame(file_header, bg="#f5f7fa")
        file_toolbar.pack(side=tk.RIGHT, padx=10)

        tk.Button(file_toolbar, text="浏览...", command=self.select_file,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=12, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=4)

        tk.Button(file_toolbar, text="清空", command=self.clear_selected_files,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=12, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=4)

        file_content = tk.Frame(file_frame, bg="#ffffff", padx=12, pady=10)
        file_content.pack(fill=tk.X)

        self.file_list_frame = tk.Frame(file_content, bg="#ffffff")
        self.file_list_frame.pack(fill=tk.X, side=tk.LEFT, expand=True)

        self.selected_files = []
        self._update_file_list()

        printer_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        printer_frame.pack(fill=tk.X, pady=(0, 12))

        printer_header = tk.Frame(printer_frame, bg="#f5f7fa", height=32)
        printer_header.pack(fill=tk.X)
        printer_header.pack_propagate(False)
        tk.Label(printer_header, text="选择打印机", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=6)

        printer_content = tk.Frame(printer_frame, bg="#ffffff", padx=12, pady=10)
        printer_content.pack(fill=tk.X)

        self.printer_var = tk.StringVar()
        self.printer_combo = ttk.Combobox(printer_content, textvariable=self.printer_var, state="readonly")
        self.printer_combo.pack(fill=tk.X)
        self.printer_combo.bind("<<ComboboxSelected>>", self._on_quick_printer_changed)

        settings_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        settings_frame.pack(fill=tk.X, pady=(0, 12))

        settings_header = tk.Frame(settings_frame, bg="#f5f7fa", height=32)
        settings_header.pack(fill=tk.X)
        settings_header.pack_propagate(False)
        tk.Label(settings_header, text="打印设置", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=6)

        settings_content = tk.Frame(settings_frame, bg="#ffffff", padx=12, pady=10)
        settings_content.pack(fill=tk.X)

        grid = tk.Frame(settings_content, bg="#ffffff")
        grid.pack(fill=tk.X)

        tk.Label(grid, text="份数:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=0, column=0, sticky=tk.W, padx=5, pady=6)
        self.copies_var = tk.IntVar(value=1)
        copies_spin = ttk.Spinbox(grid, from_=1, to=999, textvariable=self.copies_var, width=10)
        copies_spin.grid(row=0, column=1, sticky=tk.W, padx=5, pady=6)

        tk.Label(grid, text="色彩:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=0, column=2, sticky=tk.W, padx=5, pady=6)
        self.color_var = tk.StringVar(value="彩色")
        color_combo = ttk.Combobox(grid, textvariable=self.color_var, values=["黑白", "彩色"], state="readonly", width=8)
        color_combo.grid(row=0, column=3, sticky=tk.W, padx=5, pady=6)

        tk.Label(grid, text="双面:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=1, column=0, sticky=tk.W, padx=5, pady=6)
        self.duplex_var = tk.StringVar(value="单面")
        duplex_combo = ttk.Combobox(grid, textvariable=self.duplex_var, values=["单面", "双面长边", "双面短边"], state="readonly", width=10)
        duplex_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=6)

        tk.Label(grid, text="纸张:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=1, column=2, sticky=tk.W, padx=5, pady=6)
        self.paper_var = tk.StringVar(value="A4")
        paper_combo = ttk.Combobox(grid, textvariable=self.paper_var, values=["A4", "A3", "A5", "Letter"], state="readonly", width=8)
        paper_combo.grid(row=1, column=3, sticky=tk.W, padx=5, pady=6)

        tk.Label(grid, text="页码范围:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=2, column=0, sticky=tk.W, padx=5, pady=6)
        self.page_range_var = tk.StringVar(value="")
        page_entry = ttk.Entry(grid, textvariable=self.page_range_var, width=15)
        page_entry.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=5, pady=6)
        tk.Label(grid, text="(如: 1-5,8,10)", font=FONT_NORMAL, fg="#909399", bg="#ffffff").grid(row=2, column=3, sticky=tk.W, padx=5, pady=6)

        tk.Label(grid, text="方向:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=3, column=0, sticky=tk.W, padx=5, pady=6)
        self.orientation_var = tk.StringVar(value="纵向")
        ttk.Combobox(grid, textvariable=self.orientation_var, values=["纵向", "横向"], state="readonly", width=8).grid(row=3, column=1, sticky=tk.W, padx=5, pady=6)

        self.center_h_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(grid, text="水平居中", variable=self.center_h_var).grid(row=3, column=2, sticky=tk.W, padx=5, pady=6)
        self.center_v_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(grid, text="垂直居中", variable=self.center_v_var).grid(row=3, column=3, sticky=tk.W, padx=5, pady=6)

        tk.Label(grid, text="边距(mm):", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=4, column=0, sticky=tk.W, padx=5, pady=6)
        margin_frame = tk.Frame(grid, bg="#ffffff")
        margin_frame.grid(row=4, column=1, columnspan=3, sticky=tk.W, padx=5, pady=6)
        tk.Label(margin_frame, text="上", font=FONT_SMALL, fg="#909399", bg="#ffffff").pack(side=tk.LEFT)
        self.margin_top_var = tk.DoubleVar(value=10)
        ttk.Spinbox(margin_frame, from_=0, to=100, textvariable=self.margin_top_var, width=4).pack(side=tk.LEFT, padx=2)
        tk.Label(margin_frame, text="下", font=FONT_SMALL, fg="#909399", bg="#ffffff").pack(side=tk.LEFT)
        self.margin_bottom_var = tk.DoubleVar(value=10)
        ttk.Spinbox(margin_frame, from_=0, to=100, textvariable=self.margin_bottom_var, width=4).pack(side=tk.LEFT, padx=2)
        tk.Label(margin_frame, text="左", font=FONT_SMALL, fg="#909399", bg="#ffffff").pack(side=tk.LEFT)
        self.margin_left_var = tk.DoubleVar(value=10)
        ttk.Spinbox(margin_frame, from_=0, to=100, textvariable=self.margin_left_var, width=4).pack(side=tk.LEFT, padx=2)
        tk.Label(margin_frame, text="右", font=FONT_SMALL, fg="#909399", bg="#ffffff").pack(side=tk.LEFT)
        self.margin_right_var = tk.DoubleVar(value=10)
        ttk.Spinbox(margin_frame, from_=0, to=100, textvariable=self.margin_right_var, width=4).pack(side=tk.LEFT, padx=2)

        btn_frame = tk.Frame(content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=(8, 6))

        self.print_btn = tk.Button(btn_frame, text="开始打印", command=self.start_print,
                                   font=FONT_NORMAL, fg="white", bg="#409eff",
                                   relief="solid", bd=1, width=18, height=1, cursor="hand2")
        self.print_btn.pack(anchor=tk.CENTER)

        self.progress_label = tk.Label(content, text="", font=FONT_NORMAL,
                                       fg="#409eff", bg="#ffffff", height=1)
        self.progress_label.pack(fill=tk.X, pady=(0, 4))

    def _create_virtual_print_tab(self):
        tab = self._create_page_container()
        self.pages["virtual"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=15)
        content.pack(fill=tk.BOTH, expand=True)

        status_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        status_frame.pack(fill=tk.X, pady=(0, 12))

        status_header = tk.Frame(status_frame, bg="#f5f7fa", height=32)
        status_header.pack(fill=tk.X)
        status_header.pack_propagate(False)
        tk.Label(status_header, text="虚拟打印机状态", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=6)

        status_body = tk.Frame(status_frame, bg="#ffffff", padx=20, pady=12)
        status_body.pack(fill=tk.X)

        self.vp_status_label = tk.Label(status_body, text="● 检测中...", font=FONT_NORMAL,
                                        fg="#e6a23c", bg="#ffffff")
        self.vp_status_label.pack(side=tk.LEFT, padx=(0, 15))

        self.vp_install_btn = tk.Button(status_body, text="安装虚拟打印机", command=self._install_virtual_printer,
                                        font=FONT_NORMAL, fg="white", bg="#409eff",
                                        relief="solid", bd=1, padx=16, pady=4, cursor="hand2")
        self.vp_install_btn.pack(side=tk.LEFT, padx=5)

        self.vp_uninstall_btn = tk.Button(status_body, text="卸载虚拟打印机", command=self._uninstall_virtual_printer,
                                          font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                                          relief="solid", bd=1, padx=16, pady=4, cursor="hand2")
        self.vp_uninstall_btn.pack(side=tk.LEFT, padx=5)
        self.vp_uninstall_btn.pack_forget()

        tk.Label(status_body, text="提示: 安装后可在任意软件中选择「云印宝打印机」进行打印",
                 font=FONT_SMALL, fg="#909399", bg="#ffffff").pack(side=tk.LEFT, padx=20)

        self.root.after(500, self._check_virtual_printer_status)

        printer_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        printer_frame.pack(fill=tk.X, pady=(0, 12))

        printer_header = tk.Frame(printer_frame, bg="#f5f7fa", height=32)
        printer_header.pack(fill=tk.X)
        printer_header.pack_propagate(False)
        tk.Label(printer_header, text="选择目标打印机", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=6)

        printer_body = tk.Frame(printer_frame, bg="#ffffff", padx=20, pady=10)
        printer_body.pack(fill=tk.X)

        self._vp_selected_printer_var = tk.StringVar()
        self.vp_printer_combo = ttk.Combobox(printer_body, textvariable=self._vp_selected_printer_var, state="readonly", width=30)
        self.vp_printer_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.vp_printer_combo.bind("<<ComboboxSelected>>", self._on_vp_printer_changed)

        tk.Label(printer_body, text="份数:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").pack(side=tk.LEFT, padx=(10, 5))
        self.vp_copies_var = tk.IntVar(value=1)
        ttk.Spinbox(printer_body, from_=1, to=999, textvariable=self.vp_copies_var, width=6).pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(printer_body, text="色彩:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").pack(side=tk.LEFT, padx=(10, 5))
        self.vp_color_var = tk.StringVar(value="黑白")
        ttk.Combobox(printer_body, textvariable=self.vp_color_var, values=["黑白", "彩色"], state="readonly", width=6).pack(side=tk.LEFT)

        self.vp_auto_var = tk.BooleanVar(value=getattr(self.client, 'virtual_print_auto', False))
        auto_cb = tk.Checkbutton(printer_body, text="自动打印（捕获后直接提交，不再询问）",
                                  variable=self.vp_auto_var, font=FONT_NORMAL,
                                  fg="#606266", bg="#ffffff", activebackground="#ffffff",
                                  selectcolor="#ffffff", command=self._on_vp_auto_changed)
        auto_cb.pack(side=tk.LEFT, padx=(20, 0))

        jobs_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        jobs_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        jobs_header = tk.Frame(jobs_frame, bg="#f5f7fa", height=32)
        jobs_header.pack(fill=tk.X)
        jobs_header.pack_propagate(False)
        tk.Label(jobs_header, text="待打印任务（从虚拟打印机捕获）", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=6)

        jobs_toolbar = tk.Frame(jobs_header, bg="#f5f7fa")
        jobs_toolbar.pack(side=tk.RIGHT, padx=10)

        tk.Button(jobs_toolbar, text="重试失败", command=self._print_all_virtual_jobs,
                 font=FONT_NORMAL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=12, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=4)
        tk.Button(jobs_toolbar, text="清空列表", command=self._clear_virtual_jobs,
                 font=FONT_NORMAL, fg="#606266", bg="#ffffff",
                 relief="solid", bd=1, padx=12, pady=2, cursor="hand2").pack(side=tk.RIGHT, padx=4)

        self._vp_jobs_frame = tk.Frame(jobs_frame, bg="#ffffff", padx=10, pady=10)
        self._vp_jobs_frame.pack(fill=tk.BOTH, expand=True)

        self._update_virtual_jobs_list()