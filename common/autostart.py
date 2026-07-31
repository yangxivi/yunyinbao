import os
import sys
import winreg

REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

def _get_app_path(app_name):
    """获取应用程序路径（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(sys.argv[0])

def is_auto_start_enabled(app_name):
    """检查是否已启用开机自启动"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, app_name)
        winreg.CloseKey(key)
        return True
    except WindowsError:
        return False

def set_auto_start(app_name, enabled):
    """设置开机自启动"""
    try:
        if enabled:
            app_path = _get_app_path(app_name)
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_WRITE)
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
            winreg.CloseKey(key)
            return True
        else:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_WRITE)
            winreg.DeleteValue(key, app_name)
            winreg.CloseKey(key)
            return True
    except WindowsError as e:
        return False
