"""判断前台窗口是否处于全屏或最大化状态。

用于「前台有全屏或最大化窗口时不要弹出面板」这一设置。
"""

import ctypes
import os
from ctypes import byref, wintypes

_user32 = ctypes.windll.user32

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowExW.restype = wintypes.HWND
_user32.FindWindowExW.argtypes = [
    wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR
]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.MonitorFromWindow.restype = wintypes.HMONITOR
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]

MONITOR_DEFAULTTONEAREST = 2

# 桌面、任务栏这类外壳窗口不算「占用屏幕的程序」
SHELL_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
}


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _is_own_window(hwnd):
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, byref(pid))
    return pid.value == os.getpid()


DESKTOP_CLASSES = ("Progman", "WorkerW")
GW_HWNDPREV = 3


def desktop_above(hwnd):
    """桌面窗口是否压在指定窗口之上。

    显示桌面后桌面窗口会跑到最前面，此时鼠标碰到屏幕边缘命中的是桌面而不是面板，
    边缘悬停就再也触发不了，所以需要据此把面板重新抬上去。
    """
    desktops = set()
    for class_name in DESKTOP_CLASSES:
        found = _user32.FindWindowW(class_name, None)
        while found:
            desktops.add(int(found))
            found = _user32.FindWindowExW(None, found, class_name, None)
    if not desktops:
        return False
    walker = _user32.GetWindow(hwnd, GW_HWNDPREV)
    steps = 0
    while walker and steps < 300:
        if int(walker) in desktops:
            return True
        walker = _user32.GetWindow(walker, GW_HWNDPREV)
        steps += 1
    return False


def is_desktop_foreground():
    """当前前台是不是桌面（显示桌面后就是这个状态）。"""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False
    class_name = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, class_name, 64)
    return class_name.value in ("Progman", "WorkerW", "SHELLDLL_DefView")


def is_busy(hwnd=None):
    """窗口是否全屏或最大化；hwnd 为 None 时看当前前台窗口。"""
    if hwnd is None:
        hwnd = _user32.GetForegroundWindow()
    if not hwnd or _is_own_window(hwnd):
        return False

    class_name = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, class_name, 64)
    if class_name.value in SHELL_CLASSES:
        return False

    if _user32.IsZoomed(hwnd):
        return True

    # 无边框全屏窗口不会被判定为最大化，用窗口矩形是否覆盖整个显示器来判断
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, byref(rect)):
        return False
    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, byref(info)):
        return False
    screen = info.rcMonitor
    return (
        rect.left <= screen.left
        and rect.top <= screen.top
        and rect.right >= screen.right
        and rect.bottom >= screen.bottom
    )