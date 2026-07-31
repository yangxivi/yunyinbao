import os
import sys
import socket
import logging

logger = logging.getLogger(__name__)

class EnvChecker:
    def __init__(self):
        self.checks = {}

    def check_all(self):
        results = {}
        results['printer_driver'] = self.check_printer_driver()
        results['internet'] = self.check_internet()
        results['system'] = self.check_system()
        results['all_passed'] = all(r['passed'] for r in results.values())
        return results

    def check_printer_driver(self):
        try:
            try:
                import win32print
                printers = win32print.EnumPrinters(2)
                count = len(printers)
                if count > 0:
                    names = [p[2] for p in printers[:5]]
                    return {
                        "passed": True,
                        "msg": f"检测到 {count} 台打印机: {', '.join(names)}",
                        "count": count
                    }
                else:
                    return {
                        "passed": False,
                        "msg": "未检测到任何打印机，请先安装打印机驱动",
                        "count": 0
                    }
            except ImportError:
                try:
                    import subprocess
                    result = subprocess.run(
                        ['powershell', '-Command', '(Get-Printer).Count'],
                        capture_output=True, text=True, timeout=10
                    )
                    count = int(result.stdout.strip() or 0)
                    if count > 0:
                        return {
                            "passed": True,
                            "msg": f"检测到 {count} 台打印机",
                            "count": count
                        }
                    else:
                        return {
                            "passed": False,
                            "msg": "未检测到任何打印机，请先安装打印机驱动",
                            "count": 0
                        }
                except:
                    return {
                        "passed": False,
                        "msg": "无法检测打印机，请确保已安装打印机驱动",
                        "count": 0
                    }
        except Exception as e:
            return {
                "passed": False,
                "msg": f"打印机检测失败: {e}",
                "count": 0
            }

    def check_internet(self):
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return {"passed": True, "msg": "网络连接正常"}
        except:
            try:
                socket.create_connection(("114.114.114.114", 53), timeout=3)
                return {"passed": True, "msg": "网络连接正常"}
            except:
                return {
                    "passed": False,
                    "msg": "未连接外网，远程打印功能不可用（局域网打印仍可使用）"
                }

    def check_system(self):
        if sys.platform != 'win32':
            return {
                "passed": False,
                "msg": "仅支持 Windows 操作系统"
            }
        
        try:
            import ctypes
            version = sys.getwindowsversion()
            if version.major == 10 and version.build >= 22000:
                os_name = "Windows 11"
            elif version.major == 10:
                os_name = "Windows 10"
            elif version.major == 6 and version.minor == 3:
                os_name = "Windows 8.1"
            elif version.major == 6 and version.minor == 2:
                os_name = "Windows 8"
            elif version.major == 6 and version.minor == 1:
                os_name = "Windows 7"
            else:
                os_name = f"Windows {version.major}.{version.minor}"
            return {
                "passed": True,
                "msg": f"{os_name} 系统正常"
            }
        except:
            return {
                "passed": True,
                "msg": "系统检测通过"
            }

    def test_print(self, printer_name):
        try:
            from server.print_engine import PrintEngine
            engine = PrintEngine()
            success, msg = engine.test_print(printer_name)
            return {"passed": success, "msg": msg}
        except Exception as e:
            return {"passed": False, "msg": f"测试打印失败: {e}"}