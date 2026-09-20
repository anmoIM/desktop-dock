"""任务栏背景透明化。

只给任务栏窗口本身套一层"透明渐变"合成策略，图标、时钟、网络状态等
都是任务栏的子窗口，它们各自绘制，不会受到影响。不写注册表、不重启资源管理器。
"""

import ctypes
import winreg
from ctypes import wintypes

WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_TRANSPARENTGRADIENT = 2

ABM_SETSTATE = 0x0000000A
ABS_AUTOHIDE = 0x00000001
ABS_ALWAYSONTOP = 0x00000002

STUCK_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StuckRects3"

TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", ctypes.c_ssize_t),
    ]


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_uint),
        ("AccentFlags", ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),   # ABGR，alpha 为 0 即完全透明
        ("AnimationId", ctypes.c_uint),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attrib", ctypes.c_uint),
        ("pvData", ctypes.c_void_p),
        ("cbData", ctypes.c_size_t),
    ]


_user32 = ctypes.windll.user32
_user32.SetWindowCompositionAttribute.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA),
]
_user32.SetWindowCompositionAttribute.restype = wintypes.BOOL
_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
_user32.FindWindowExW.restype = wintypes.HWND
_user32.FindWindowExW.argtypes = (
    wintypes.HWND,
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
)
_user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
_user32.MonitorFromWindow.restype = wintypes.HMONITOR
_user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.c_void_p)
_shell32 = ctypes.windll.shell32
_shell32.SHAppBarMessage.restype = ctypes.c_size_t


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def taskbar_windows():
    """所有任务栏窗口句柄（主屏 + 副屏）。"""
    handles = []
    for class_name in TASKBAR_CLASSES:
        hwnd = _user32.FindWindowW(class_name, None)
        while hwnd:
            handles.append(int(hwnd))
            hwnd = _user32.FindWindowExW(None, hwnd, class_name, None)
    return handles


def _apply(hwnd, state):
    policy = ACCENT_POLICY(state, 0, 0x00000000, 0)
    data = WINDOWCOMPOSITIONATTRIBDATA(
        WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.byref(policy), ctypes.c_void_p),
        ctypes.sizeof(policy),
    )
    return bool(_user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))


def set_transparent(enabled):
    """开启/关闭任务栏背景透明，返回成功处理的窗口数。"""
    state = ACCENT_ENABLE_TRANSPARENTGRADIENT if enabled else ACCENT_DISABLED
    count = 0
    for hwnd in taskbar_windows():
        if _apply(hwnd, state):
            count += 1
    return count


def _write_autohide_registry(enabled):
    """同步写入注册表，资源管理器重启后依然生效。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STUCK_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE
        ) as key:
            data, kind = winreg.QueryValueEx(key, "Settings")
        buffer = bytearray(data)
        if len(buffer) <= 8:
            return
        if enabled:
            buffer[8] |= 0x01
        else:
            buffer[8] &= ~0x01
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STUCK_KEY) as key:
            winreg.SetValueEx(key, "Settings", 0, kind, bytes(buffer))
    except OSError:
        pass


def set_autohide(enabled):
    """收纳式任务栏：鼠标移到屏幕边缘才滑出，图标/时钟/网络状态照常显示。"""
    ok = False
    for hwnd in taskbar_windows():
        data = APPBARDATA()
        data.cbSize = ctypes.sizeof(APPBARDATA)
        data.hWnd = hwnd
        data.lParam = ABS_AUTOHIDE if enabled else ABS_ALWAYSONTOP
        if _shell32.SHAppBarMessage(ABM_SETSTATE, ctypes.byref(data)):
            ok = True
    _write_autohide_registry(enabled)
    return ok


def is_autohidden():
    """任务栏当前是否处于收起（自动隐藏）状态。"""
    for hwnd in taskbar_windows():
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            continue
        monitor = _user32.MonitorFromWindow(hwnd, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            if rect.top >= info.rcMonitor.bottom - 4:
                return True
    return False