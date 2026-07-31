import os
import sys
import logging
import tempfile

def _get_app_dir():
    """获取程序所在目录（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _check_single_instance():
    """检查是否已有实例在运行，使用Windows Mutex"""
    try:
        import ctypes
        from ctypes import wintypes
        
        mutex_name = "Global\\YunYinBao_Server_Mutex_2026"
        
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

def main():
    if not _check_single_instance():
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning("提示", "云印宝服务端已经在运行了！\n\n请查看系统托盘区。")
            root.destroy()
        except:
            print("云印宝服务端已经在运行了！")
        sys.exit(0)
    
    app_dir = _get_app_dir()
    log_dir = os.path.join(app_dir, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        log_dir = tempfile.gettempdir()
    
    log_file = os.path.join(log_dir, 'server.log')
    try:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
    except:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    logger = logging.getLogger(__name__)
    logger.info("=== 服务端启动 ===")
    logger.info(f"Python: {sys.version}")
    logger.info(f"工作目录: {os.getcwd()}")
    
    try:
        from server.gui import ServerGUI
        app = ServerGUI()
        app.run()
    except Exception as e:
        logger.exception("服务启动失败")
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("启动错误", f"服务启动失败:\n{str(e)}")
            root.destroy()
        except:
            print(f"启动错误: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
