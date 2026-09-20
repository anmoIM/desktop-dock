"""隐藏 / 还原系统桌面图标。

只做两件事：
1. 改注册表 HideIcons（等同于桌面右键「查看 → 显示桌面图标」），重启资源管理器后依然有效；
2. 直接隐藏承载图标的窗口，让设置立刻生效。

已经处于目标状态时不做任何操作，因此关闭该功能时不会碰用户的桌面，
也不会触发资源管理器刷新，桌面图标的原有排列保持不变。
"""

import ctypes
import winreg
from ctypes import wintypes

ADVANCED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
VALUE_NAME = "HideIcons"

_user32 = ctypes.windll.user32

_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
_user32.FindWindowExW.restype = wintypes.HWND
_user32.FindWindowExW.argtypes = (
    wintypes.HWND,
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
)
_user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.IsWindowVisible.argtypes = (wintypes.HWND,)

SW_HIDE = 0
SW_SHOW = 5

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def read_hidden():
    """读取注册表里「隐藏桌面图标」的状态。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ADVANCED_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return bool(value)
    except (OSError, FileNotFoundError):
        return False


def _write_hidden(hidden):
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ADVANCED_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_DWORD, 1 if hidden else 0)
        return True
    except OSError:
        return False


def _find_desktop_view():
    """找到承载桌面图标的 SHELLDLL_DefView 窗口。"""
    progman = _user32.FindWindowW("Progman", None)
    if progman:
        view = _user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if view:
            return view

    found = []

    def _visit(hwnd, _lparam):
        view = _user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if view:
            found.append(view)
            return False
        return True

    _user32.EnumWindows(_ENUM_PROC(_visit), 0)
    return found[0] if found else None


def set_hidden(hidden):
    """隐藏或显示桌面图标，成功返回 True。状态一致时不做任何改动。"""
    if read_hidden() != hidden:
        _write_hidden(hidden)
    view = _find_desktop_view()
    if view and bool(_user32.IsWindowVisible(view)) == hidden:
        _user32.ShowWindow(view, SW_HIDE if hidden else SW_SHOW)
    return read_hidden() == hidden