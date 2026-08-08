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

from client.api_client import PrintClient
from common.config import APP_NAME, APP_VERSION, load_config, save_config, get_config, set_config
from common.theme import get_theme, get_all_themes


class ClientGUI:
    def __init__(self):
        self.client = PrintClient()
        self._tray = None
        self._tray_visible = False
        self._real_quitting = False
        self.pages = {}
        self.current_page = None
        self.sidebar_buttons = {}
        self.sidebar_frames = {}
        self._theme_list = get_all_themes()
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
        self._switch_page("virtual")

    def _create_sidebar_menu(self):
        theme = get_theme(get_config("theme", "tech_blue"))
        self.sidebar_buttons = {}
        self.sidebar_frames = {}
        menu_items = [
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

        # 自动打印开关
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
                     font=FONT_NORMAL, fg="#909399", bg="#ffffff",
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
                                font=FONT_SMALL, fg=status_color.get(job_status, "#909399"), bg="#ffffff")
            st_label.pack(side=tk.LEFT, padx=(0, 8))

            size_str = self._format_size(job['file_size'])
            size_label = tk.Label(info_frame, text=size_str, font=FONT_SMALL,
                                  fg="#909399", bg="#ffffff")
            size_label.pack(side=tk.LEFT, padx=(0, 10))

            time_label = tk.Label(info_frame, text=job['captured_time'], font=FONT_SMALL,
                                  fg="#909399", bg="#ffffff")
            time_label.pack(side=tk.LEFT, padx=(0, 10))

            btn_frame = tk.Frame(info_frame, bg="#ffffff")
            btn_frame.pack(side=tk.RIGHT)

            if job_status == 'failed':
                tk.Button(btn_frame, text="重试", command=lambda j=idx: self._auto_submit_virtual_job(j),
                         font=FONT_NORMAL, fg="white", bg="#409eff",
                         relief="solid", bd=1, padx=12, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=3)
            tk.Button(btn_frame, text="预览", command=lambda j=idx: self._preview_virtual_job(j),
                     font=FONT_NORMAL, fg="#606266", bg="#ffffff",
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

    def _create_tasks_tab(self):
        tab = self._create_page_container()
        self.pages["tasks"] = tab

        content = tk.Frame(tab, bg="#ffffff", padx=15, pady=12)
        content.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        tk.Button(btn_frame, text="刷新", command=self.refresh_tasks,
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

        table_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("task_id", "printer", "file", "pages", "status", "time")
        self.task_tree = ttk.Treeview(table_frame, columns=columns, show="headings")

        headings = [("task_id", "任务ID", 150), ("printer", "打印机", 150), ("file", "文件名", 180), ("pages", "页数", 60), ("status", "状态", 80), ("time", "提交时间", 140)]
        for col, text, width in headings:
            self.task_tree.heading(col, text=text, anchor="center")
            self.task_tree.column(col, width=width, anchor="center")

        self.task_tree.pack(fill=tk.BOTH, expand=True)
        self.task_tree.tag_configure('even', background="#ffffff")
        self.task_tree.tag_configure('odd', background="#fafbfc")

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
        tk.Label(theme_header, text="皮肤设置", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=8)
        theme_body = tk.Frame(theme_frame, bg="#ffffff", padx=20, pady=12)
        theme_body.pack(fill=tk.X)
        tk.Label(theme_body, text="选择皮肤:", font=FONT_NORMAL, fg="#606266", bg="#ffffff").grid(row=0, column=0, sticky=tk.W, pady=6)
        current_theme_key = get_config("theme", "tech_blue")
        current_theme_name = get_theme(current_theme_key)["name"]
        self.theme_var = tk.StringVar(value=current_theme_name)
        theme_display = [name for _, name in self._theme_list]
        theme_combo = ttk.Combobox(theme_body, textvariable=self.theme_var, values=theme_display, state="readonly", width=14)
        theme_combo.grid(row=0, column=1, sticky=tk.W, padx=10, pady=6)
        theme_combo.bind("<<ComboboxSelected>>", self._on_theme_change)

        server_frame = tk.Frame(content, bg="#ffffff", highlightbackground="#ebeef5", highlightthickness=1)
        server_frame.pack(fill=tk.X, pady=(0, 12))
        server_header = tk.Frame(server_frame, bg="#f5f7fa", height=36)
        server_header.pack(fill=tk.X)
        server_header.pack_propagate(False)
        tk.Label(server_header, text="服务端设置", font=FONT_NORMAL,
                fg="#303133", bg="#f5f7fa").pack(side=tk.LEFT, padx=15, pady=8)
        server_body = tk.Frame(server_frame, bg="#ffffff", padx=20, pady=12)
        server_body.pack(fill=tk.X)
        tk.Label(server_body, text="服务端IP地址:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=0, column=0, sticky=tk.W, pady=6)
        self.settings_ip_var = tk.StringVar()
        if self.client.server_url:
            ip = self.client.server_url.replace('http://', '').split(':')[0]
            self.settings_ip_var.set(ip)
        ttk.Entry(server_body, textvariable=self.settings_ip_var, width=18).grid(row=0, column=1, sticky=tk.W, padx=10, pady=6)
        tk.Label(server_body, text="端口:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=1, column=0, sticky=tk.W, pady=6)
        self.settings_port_var = tk.StringVar(value="8989")
        if self.client.server_url:
            port = self.client.server_url.split(':')[-1]
            self.settings_port_var.set(port)
        ttk.Entry(server_body, textvariable=self.settings_port_var, width=18).grid(row=1, column=1, sticky=tk.W, padx=10, pady=6)
        tk.Label(server_body, text="访问码:", font=FONT_NORMAL, fg="#303133", bg="#ffffff").grid(row=2, column=0, sticky=tk.W, pady=6)
        self.settings_code_var = tk.StringVar(value=self.client.access_code or "")
        ttk.Entry(server_body, textvariable=self.settings_code_var, width=18).grid(row=2, column=1, sticky=tk.W, padx=10, pady=6)
        tk.Label(server_body, text="提示: 访问码可在服务端概览页查看", font=FONT_NORMAL, fg="#909399", bg="#ffffff").grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=4)
        from common.autostart import is_auto_start_enabled
        self.auto_start_var = tk.BooleanVar(value=is_auto_start_enabled("云印宝客户端"))
        ttk.Checkbutton(server_body, text="开机自启动", variable=self.auto_start_var).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=6)

        btn_frame = tk.Frame(content, bg="#ffffff")
        btn_frame.pack(fill=tk.X, pady=8)
        tk.Button(btn_frame, text="保存并连接", command=self._save_settings_page,
                 font=FONT_NORMAL, fg="white", bg="#409eff",
                 relief="solid", bd=1, padx=22, pady=5, cursor="hand2").pack()

    def _save_settings_page(self):
        ip = self.settings_ip_var.get().strip()
        port = self.settings_port_var.get().strip()
        code = self.settings_code_var.get().strip()

        if not ip or not port or not code:
            messagebox.showwarning("提示", "请填写完整信息")
            return

        def do_connect():
            success, msg = self.client.connect_server(ip, port, code)
            self.root.after(0, lambda: self._after_save_settings(success, msg, ip, port))

        threading.Thread(target=do_connect, daemon=True).start()

    def _after_save_settings(self, success, msg, ip, port):
        if success:
            self._update_connection_status(True, msg, f"http://{ip}:{port}")
            from common.autostart import set_auto_start
            set_auto_start("云印宝客户端", self.auto_start_var.get())
            messagebox.showinfo("成功", "连接成功！")
        else:
            messagebox.showerror("失败", msg)

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
            self.server_label.config(text=server_url)
            self._update_printer_list()
            self.refresh_tasks()
        else:
            self.status_label.config(text="● 未连接", fg="#f56c6c")
            self.server_label.config(text="")

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

    def _format_size(self, size):
        if size < 1024:
            return f"{size} B"
        elif size < 1024*1024:
            return f"{size/1024:.1f} KB"
        else:
            return f"{size/(1024*1024):.1f} MB"

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
            size = 64
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rectangle((4, 4, size-4, size-4), fill='#409eff')
            draw.rectangle((16, 12, size-16, 20), fill='white')
            draw.rectangle((14, 22, size-14, 50), fill='white')
            draw.rectangle((18, 30, size-18, 42), fill='#409eff')
            draw.rectangle((20, 54, size-20, 58), fill='white')
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
