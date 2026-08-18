# 云印宝 YunYinBao

> **让本地打印机变成网络共享打印机** —— 把连接打印机的那台 Windows 电脑变成服务端，其他设备（PC / 手机 / 平板）通过客户端远程提交打印任务，全程零人工介入。

## 一、产品定位

把任意一台 Windows 电脑（无论有没有显示器）变成一个**远程打印服务器**：

- 装在「连接了打印机的电脑」上 → 启动 **云印宝服务端**
- 装在「想打文件的设备」上 → 启动 **云印宝客户端**，填入服务端访问码 → 提交打印
- 后台用 Flask 接收任务 → 用 win32print / win32com / SumatraPDF 完成静默打印
- 文件传过去 → 打印机自动出纸，**完全不需要有人在打印机旁边守着**

支持的场景：
- 家里打印机只连着一台老电脑，但你想在沙发上用手机直接发文件去打印
- 小型办公场景，多个员工向同一台打印机发文档
- Cloudflare 命名隧道 + 服务端固定域名（如 `print.xivi.cc.cd`）打通公网，手机 4G/5G 也能打

## 二、架构

```
┌──────────────┐         HTTP/JSON         ┌──────────────────┐
│  云印宝客户端 │  ──────────────────────▶   │  云印宝服务端     │
│  (tkinter)   │   POST /api/print/submit   │  (Flask + tkinter) │
│              │   GET  /api/tasks         │                    │
│  文件预览     │                           │  ├─ print_engine   │
│  任务状态     │                           │  │   ├─ win32print │
│  设备绑定     │                           │  │   ├─ win32com   │
└──────────────┘                            │  │   └─ SumatraPDF │
                                            │  ├─ task_scheduler│
                                            │  ├─ user_manager  │
                                            │  └─ device_manager│
                                            └────────┬───────────┘
                                                     │ win32print API
                                                     ▼
                                                ┌─────────┐
                                                │ 本地打印机 │
                                                └─────────┘
```

## 三、技术栈

| 层 | 技术 |
|---|---|
| 服务端 Web | Flask 3.x + waitress |
| 客户端/服务端 GUI | tkinter（Python 标准库） |
| 静默打印（Office） | `win32com.client`（Word/Excel/PowerPoint COM 接口） |
| 静默打印（PDF） | `SumatraPDF.exe -silent -exit-when-done` |
| 系统托盘 | `pystray`（16x16x ICO 子图） |
| 数据存储 | SQLite + JSON 配置文件 |
| 打包 | PyInstaller 6.9.0（Python 3.10） |
| 安装包 | NSIS 3.x（安装 + 自动清理旧版 + 写注册表） |
| 公网打通 | Cloudflare 命名隧道（`cloudflared`） |

## 四、目录结构

```
云印宝/
├─ client/             # 客户端 GUI（tkinter）
├─ server/             # 服务端核心（Flask + print_engine）
├─ common/             # 双端共享（config / database / theme / virtual_printer）
├─ web/                # 浏览器 Web 后台（备用）
├─ tools/              # 构建工具（make_icons.py 等）
├─ data/               # 运行时数据库 + config.json（不入 git）
├─ logs/               # 运行日志（不入 git）
├─ uploads/            # 打印任务临时文件（不入 git）
├─ client_main.py      # 客户端入口
├─ server_main.py      # 服务端入口
├─ web_main.py         # Web 后台入口
├─ 云印宝客户端.spec   # PyInstaller 客户端打包配置
├─ 云印宝服务端.spec   # PyInstaller 服务端打包配置
├─ installer.nsi       # NSIS 安装包脚本
├─ build_all.bat      # 一键打包双端
└─ requirements.txt   # Python 依赖
```

## 五、配置 `data/config.json`

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 9527,
    "access_code": "首次启动根据机器码自动生成",
    "max_file_size_mb": 50
  },
  "printer": {
    "default": "EPSON L3253 Series",
    "auto_select": true
  },
  "print": {
    "copies": 1,
    "duplex": false,
    "color": true
  },
  "ui": {
    "theme": "tech_blue",
    "auto_start": false
  }
}
```

## 六、核心链路

### 6.1 打印任务流转
1. 客户端 `POST /api/print/submit`（multipart/form-data：文件 + 打印参数 + access_code）
2. 服务端 `task_scheduler` 校验 access_code → 写入 SQLite → 推入打印队列
3. `print_engine` 按文件类型分支：
   - **PDF**：`SumatraPDF.exe -print-to "打印机名" -silent -exit-when-done file.pdf`
   - **Word/Excel/PowerPoint**：`win32com` 启动 Office → `PrintOut` → `Quit`（静默不弹窗）
   - **图片/文本**：`win32print` 直接 `StartDocPrinter` → 走系统打印处理器
4. 任务完成/失败 → 写回 SQLite → 客户端通过 `/api/tasks` 轮询拿到结果

### 6.2 三层图标统一
- **exe 文件图标**：`PyInstaller spec` 的 `icon='F.ico'/'K.ico'`
- **窗口任务栏图标**：`root.iconbitmap('F.ico'/'K.ico')`
- **系统托盘图标**：`pystray.Image.open('F.ico'/'K.ico')` 取 16x16 子图
- Windows 三层缓存需分别清理：`%LocalAppData%\IconCache.db` + `thumbcache_*.db` + 任务栏固定 `.lnk`

### 6.3 后台刷新（避免主线程卡顿）
所有 I/O（打印机状态轮询、网络请求）都用 `threading.Thread(daemon=True)` 后台执行，通过 `root.after(0, callback)` 回主线程更新 UI。**tkinter 主线程不能跑任何 subprocess / 网络 / 磁盘 IO。**

## 七、打包与发布

### 7.1 打包双端 exe

```bash
# 一次性构建客户端、服务端两个 exe
build_all.bat
```

或者手动：
```bash
"C:\Users\Administrator\.workbuddy\binaries\python\envs\py310_build\Scripts\pyinstaller.exe" \
    --noconfirm 云印宝客户端.spec

"C:\Users\Administrator\.workbuddy\binaries\python\envs\py310_build\Scripts\pyinstaller.exe" \
    --noconfirm 云印宝服务端.spec
```

产物：`dist/云印宝客户端/云印宝客户端.exe` + `dist/云印宝服务端/云印宝服务端.exe`，每个约 22MB。

### 7.2 制作 NSIS 安装包

```bash
"C:\Program Files (x86)\NSIS\makensis.exe" installer.nsi
```

产物：`云印宝安装程序.exe`（23MB，LZMA 压缩）

特性：
- 强制锁定安装目录到 `%LOCALAPPDATA%\Programs\云印宝`（避免用户改到 Program Files 触发权限错误）
- 自动检测注册表旧版并静默清理后重装
- 双击"开始菜单 → 卸载云印宝"一键清理所有残留
- 桌面 + 开始菜单快捷方式，服务端 F 图标 / 客户端 K 图标

### 7.3 公网打通（可选）

用 Cloudflare 命名隧道（`cloudflared`）：
```bash
cloudflared tunnel route dns yunyinbao print.xivi.cc.cd
cloudflared tunnel run yunyinbao
```

客户端填 `print.xivi.cc.cd:9527` 即可在任何有网的地方发打印任务。

## 八、安全说明

- **访问码**：首次启动根据机器码自动生成 8 位 hex（`access_code`），客户端必须填对才能发任务
- **HTTPS**：本地默认走 HTTP，公网建议套 Cloudflare（自带 HTTPS）
- **文件类型白名单**：服务端只接受 `.pdf / .doc / .docx / .xls / .xlsx / .ppt / .pptx / .jpg / .png / .txt`
- **文件大小限制**：默认 50MB，可在 `config.json` 调整

## 九、常见问题

**Q1：服务端启动后客户端连不上？**
A：先在服务端电脑浏览器打开 `http://127.0.0.1:9527/api/status`，看到 `{"status":"ok"}` 说明 Flask 在跑。再检查 Windows 防火墙是否放行了 9527 端口。

**Q2：打印 PDF 时一直弹"另存为"？**
A：检查 `tools/SumatraPDF.exe` 是否被正确嵌入（PyInstaller `datas` 配置）。启动时看日志 `print_engine.py` 是否走到了 SumatraPDF 分支。

**Q3：Office 打印弹窗？**
A：必须用 `win32com` 走 COM 接口，且调用前先 `Dispatch` → 设 `Visible = False` → `PrintOut(Copies=1, ActivePrinter=...)`。直接 `os.startfile` 弹 Word 窗口是错的。

**Q4：安装包安装时报"无法打开要写入的文件"？**
A：v9.1+ 已修复：安装目录强制锁 `%LOCALAPPDATA%\Programs\云印宝`，不再让用户手抖改到 Program Files。

## 十、版本

- v9.1：客户端卡片结构改为"标题 + 内容区粗边框"分层；NSIS 修复 _internal 目录结构；安装路径权限修复；自动检测旧版清理
- v9.0：卡片样式重刷（仿现代 SaaS 白底圆角）；F/K 角标；自动重试；弹窗反馈
- v7：异步刷新统计卡（主线程不再卡顿）
- v6：三层图标统一（F.ico / K.ico）

## 十一、License

MIT