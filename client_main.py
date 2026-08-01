import os
import sys
import logging
import traceback
import tempfile
import time

def _get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _check_single_instance():
    try:
        import ctypes
        from ctypes import wintypes
        mutex_name = "Global\\YunYinBao_Client_Mutex_2026"
        CreateMutex = ctypes.windll.kernel32.CreateMutexW
        CreateMutex.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        CreateMutex.restype = wintypes.HANDLE
        GetLastError = ctypes.windll.kernel32.GetLastError
        GetLastError.argtypes = []
        GetLastError.restype = wintypes.DWORD
        mutex = CreateMutex(None, True, mutex_name)
        ERROR_ALREADY_EXISTS = 183
        if GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        return True

def _setup_logging():
    app_dir = _get_app_dir()
    log_dir = os.path.join(app_dir, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        log_dir = tempfile.gettempdir()
    log_file = os.path.join(log_dir, 'client.log')
    handlers = []
    try:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    except Exception:
        pass
    try:
        handlers.append(logging.StreamHandler())
    except Exception:
        pass
    if not handlers:
        logging.basicConfig(level=logging.INFO)
        return
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


def main():
    if not _check_single_instance():
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning("提示", "云印宝客户端已经在运行了！\n\n请查看系统托盘区。")
            root.destroy()
        except:
            print("云印宝客户端已经在运行了！")
        sys.exit(0)
    
    _setup_logging()
    logger = logging.getLogger('client_main')
    logger.info("=" * 50)
    logger.info("云印宝客户端启动")
    logger.info("=" * 50)

    try:
        from client.gui import ClientGUI
        app = ClientGUI()
        app.run()
    except Exception as e:
        logger.error(f"程序启动失败: {e}", exc_info=True)
        try:
            crash_dir = os.path.join(_get_app_dir(), 'logs')
            os.makedirs(crash_dir, exist_ok=True)
            crash_file = os.path.join(crash_dir, f'crash_{time.strftime("%Y%m%d_%H%M%S")}.log')
            with open(crash_file, 'w', encoding='utf-8') as f:
                f.write(f"启动失败时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"错误: {e}\n")
                f.write(traceback.format_exc())
            logger.info(f"崩溃日志已写入: {crash_file}")
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("启动错误", f"程序启动失败:\n{str(e)}\n\n请查看 logs/client.log 获取详情")
            root.destroy()
        except Exception:
            pass


if __name__ == '__main__':
    main()
