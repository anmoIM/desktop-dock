"""开机自启动：写入当前用户的注册表 Run 项，无需管理员权限。"""

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "KJSL_DesktopDock"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _launch_command():
    """返回开机要执行的命令行，优先使用 pythonw 以避免弹出控制台。"""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return '"%s" "%s"' % (exe, os.path.join(ROOT_DIR, "main.py"))


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return bool(value)
    except (OSError, FileNotFoundError):
        return False


def set_enabled(enabled):
    """开启或关闭自启，返回是否成功。"""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(
                    key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command()
                )
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False