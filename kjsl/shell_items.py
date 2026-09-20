"""桌面上的系统图标（此电脑、回收站、网络等非文件条目）。

这类图标不在桌面文件夹里，属于 Shell 命名空间，只能通过 COM 枚举拿到，
显示与否由注册表 HideDesktopIcons 决定（等同于桌面右键「查看」里的勾选项）。
"""

import ctypes
import winreg
from ctypes import POINTER, byref, c_void_p, wintypes
from dataclasses import dataclass

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap

_ole32 = ctypes.windll.ole32
_shell32 = ctypes.windll.shell32
_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

SIGDN_DESKTOPABSOLUTEPARSING = 0x80028000
SHCONTF_FOLDERS = 0x20
SHCONTF_NONFOLDERS = 0x40

SHGFI_ICON = 0x100
SHGFI_DISPLAYNAME = 0x200
SHGFI_SYSICONINDEX = 0x4000
SHGFI_PIDL = 0x8

SHIL_EXTRALARGE = 2
SHIL_JUMBO = 4
ILD_TRANSPARENT = 1
DI_NORMAL = 0x0003

HIDE_KEYS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel",
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\ClassicStartMenu",
)

# 注册表没有记录时，按 Windows 默认只有这两项显示在桌面上
DEFAULT_VISIBLE = {
    "{20D04FE0-3AEA-1069-A2D8-08002B30309D}",  # 此电脑
    "{645FF040-5081-101B-9F08-00AA002F954E}",  # 回收站
}

IID_IIMAGELIST = bytes.fromhex("26596e462e5817409fdfe8998daa0950")


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", ctypes.c_long),
        ("bmWidth", ctypes.c_long),
        ("bmHeight", ctypes.c_long),
        ("bmWidthBytes", ctypes.c_long),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class WIN32_FIND_DATAW(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", FILETIME),
        ("ftLastAccessTime", FILETIME),
        ("ftLastWriteTime", FILETIME),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("dwReserved0", wintypes.DWORD),
        ("dwReserved1", wintypes.DWORD),
        ("cFileName", wintypes.WCHAR * 260),
        ("cAlternateFileName", wintypes.WCHAR * 14),
    ]


# {00021401-0000-0000-C000-000000000046} / {000214F9-...} / {0000010B-...}
CLSID_SHELLLINK = bytes.fromhex("0114020000000000c000000000000046")
IID_ISHELLLINKW = bytes.fromhex("f914020000000000c000000000000046")
IID_IPERSISTFILE = bytes.fromhex("0b01000000000000c000000000000046")
CLSCTX_INPROC_SERVER = 1
STGM_READ = 0
SLGP_RAWPATH = 4

_ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID), c_void_p, wintypes.DWORD,
    ctypes.POINTER(GUID), ctypes.POINTER(c_void_p),
]
_ole32.CoCreateInstance.restype = ctypes.c_long


def resolve_shortcut(path):
    """解析 .lnk 指向的目标路径，失败返回空串。

    用途：取目标图标，这样就不会带快捷方式的小箭头角标。
    """
    try:
        _ole32.CoInitializeEx(None, 0x2)
        clsid = GUID.from_buffer_copy(CLSID_SHELLLINK)
        iid_link = GUID.from_buffer_copy(IID_ISHELLLINKW)
        iid_persist = GUID.from_buffer_copy(IID_IPERSISTFILE)
        link = c_void_p()
        hr = _ole32.CoCreateInstance(
            byref(clsid), None, CLSCTX_INPROC_SERVER, byref(iid_link), byref(link)
        )
        if hr != 0 or not link:
            return ""
        target = ""
        persist = c_void_p()
        # IShellLink::QueryInterface
        hr = _com_call(
            link, 0, ctypes.c_long,
            [ctypes.POINTER(GUID), ctypes.POINTER(c_void_p)],
            byref(iid_persist), byref(persist),
        )
        if hr == 0 and persist:
            # IPersistFile::Load
            hr = _com_call(
                persist, 5, ctypes.c_long, [ctypes.c_wchar_p, wintypes.DWORD],
                path, STGM_READ,
            )
            if hr == 0:
                buffer = ctypes.create_unicode_buffer(1024)
                data = WIN32_FIND_DATAW()
                # IShellLink::GetPath
                if _com_call(
                    link, 3, ctypes.c_long,
                    [ctypes.c_wchar_p, ctypes.c_int,
                     ctypes.POINTER(WIN32_FIND_DATAW), wintypes.DWORD],
                    buffer, 1024, byref(data), SLGP_RAWPATH,
                ) == 0:
                    target = buffer.value
            _com_call(persist, 2, ctypes.c_ulong, [])
        _com_call(link, 2, ctypes.c_ulong, [])
        return target
    except OSError:
        return ""


_shell32.SHGetDesktopFolder.argtypes = [POINTER(c_void_p)]
_shell32.SHGetDesktopFolder.restype = ctypes.c_long
_shell32.SHGetNameFromIDList.argtypes = [
    c_void_p, ctypes.c_int, POINTER(ctypes.c_wchar_p)
]
_shell32.SHGetNameFromIDList.restype = ctypes.c_long
_shell32.SHParseDisplayName.argtypes = [
    wintypes.LPCWSTR,
    c_void_p,
    POINTER(c_void_p),
    wintypes.DWORD,
    POINTER(wintypes.DWORD),
]
_shell32.SHParseDisplayName.restype = ctypes.c_long
_shell32.SHGetFileInfoW.restype = ctypes.c_void_p
_shell32.SHGetImageList.argtypes = [ctypes.c_int, POINTER(GUID), POINTER(c_void_p)]
_shell32.SHGetImageList.restype = ctypes.c_long

_ole32.CoTaskMemFree.argtypes = [c_void_p]
_user32.DestroyIcon.argtypes = [wintypes.HICON]
_user32.DrawIconEx.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
    ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HANDLE, wintypes.UINT,
]
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.GetIconInfo.argtypes = [wintypes.HICON, POINTER(ICONINFO)]
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, POINTER(BITMAPINFO), wintypes.UINT,
    POINTER(c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, c_void_p]
_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
_gdi32.SelectObject.restype = wintypes.HANDLE
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.DeleteDC.argtypes = [wintypes.HDC]


@dataclass
class ShellItem:
    key: str        # "::{GUID}" 大写形式，作为持久化标识
    name: str       # 显示名（此电脑 / 回收站 …）
    path: str       # 可直接交给 ShellExecute 打开


def _com_call(ptr, index, restype, argtypes, *args):
    """按虚表下标调用 COM 方法。"""
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return prototype(vtable[index])(ptr, *args)


def _visibility():
    """读取桌面上各项系统图标的显示开关。"""
    values = {}
    for path in HIDE_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    upper = name.upper()
                    if upper not in values:
                        values[upper] = not bool(value)  # 0 表示显示
        except FileNotFoundError:
            continue
    return values


def _enumerate():
    """枚举桌面命名空间里所有条目的绝对解析路径。"""
    _ole32.CoInitializeEx(None, 0x2)
    desktop = c_void_p()
    if _shell32.SHGetDesktopFolder(byref(desktop)) != 0 or not desktop:
        return []
    names = []
    enum = c_void_p()
    hr = _com_call(
        desktop, 4, ctypes.c_long,
        [wintypes.HWND, ctypes.c_uint, POINTER(c_void_p)],
        None, SHCONTF_FOLDERS | SHCONTF_NONFOLDERS, byref(enum),
    )
    if hr == 0 and enum:
        fetched = ctypes.c_ulong()
        while True:
            fetched.value = 0
            pidl = c_void_p()
            hr = _com_call(
                enum, 3, ctypes.c_long,
                [ctypes.c_ulong, POINTER(c_void_p), POINTER(ctypes.c_ulong)],
                1, byref(pidl), byref(fetched),
            )
            if hr != 0 or fetched.value != 1 or not pidl:
                break
            text = ctypes.c_wchar_p()
            if _shell32.SHGetNameFromIDList(
                pidl, SIGDN_DESKTOPABSOLUTEPARSING, byref(text)
            ) == 0 and text.value:
                names.append(text.value)
            if text:
                _ole32.CoTaskMemFree(text)
            _ole32.CoTaskMemFree(pidl)
        _com_call(enum, 2, ctypes.c_ulong, [])
    _com_call(desktop, 2, ctypes.c_ulong, [])
    return names


def _query(parsing_name):
    """取显示名、系统图标索引与 HICON。"""
    pidl = c_void_p()
    if _shell32.SHParseDisplayName(parsing_name, None, byref(pidl), 0, None) != 0:
        return None, -1, None
    if not pidl:
        return None, -1, None
    try:
        info = SHFILEINFOW()
        flags = (
            SHGFI_PIDL | SHGFI_DISPLAYNAME | SHGFI_SYSICONINDEX | SHGFI_ICON
        )
        if not _shell32.SHGetFileInfoW(
            pidl, 0, byref(info), ctypes.sizeof(info), flags
        ):
            return None, -1, None
        return info.szDisplayName, info.iIcon, info.hIcon
    finally:
        _ole32.CoTaskMemFree(pidl)


def list_items():
    """按桌面实际显示情况，返回需要收录的系统图标。"""
    try:
        names = _enumerate()
    except OSError:
        return []
    visibility = _visibility()
    items = []
    for name in names:
        if not name.startswith("::"):
            continue
        key = name.upper()
        lookup = key[2:]        # 注册表里存的是不带 :: 的 GUID
        if not visibility.get(lookup, lookup in DEFAULT_VISIBLE):
            continue
        display, _, hicon = _query(name)
        if hicon:
            _user32.DestroyIcon(hicon)
        if display:
            items.append(ShellItem(key=key, name=display, path=name))
    items.sort(key=lambda item: item.name)
    return items


def _image_list_icon(index, which):
    if index < 0:
        return None
    iid = GUID.from_buffer_copy(IID_IIMAGELIST)
    image_list = c_void_p()
    if _shell32.SHGetImageList(which, byref(iid), byref(image_list)) != 0:
        return None
    if not image_list:
        return None
    hicon = wintypes.HICON()
    hr = _com_call(
        image_list, 10, ctypes.c_long,
        [ctypes.c_int, ctypes.c_uint, POINTER(wintypes.HICON)],
        index, ILD_TRANSPARENT, byref(hicon),
    )
    _com_call(image_list, 2, ctypes.c_ulong, [])
    return hicon if hr == 0 and hicon else None


def _icon_size(hicon):
    info = ICONINFO()
    if not _user32.GetIconInfo(hicon, byref(info)):
        return 32, 32
    width = height = 32
    if info.hbmColor:
        bitmap = BITMAP()
        if _gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bitmap), byref(bitmap)):
            width = int(bitmap.bmWidth) or 32
            height = int(bitmap.bmHeight) or 32
        _gdi32.DeleteObject(info.hbmColor)
    if info.hbmMask:
        _gdi32.DeleteObject(info.hbmMask)
    return width, height


def _render(hicon, width, height):
    """把 HICON 画进 32 位 DIB，再转成 QImage。"""
    screen_dc = _user32.GetDC(None)
    memory_dc = _gdi32.CreateCompatibleDC(screen_dc)
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height      # 负数表示自上而下
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0       # BI_RGB
    info = BITMAPINFO()
    info.bmiHeader = header
    bits = c_void_p()
    bitmap = _gdi32.CreateDIBSection(screen_dc, byref(info), 0, byref(bits), None, 0)
    if not bitmap or not bits:
        _gdi32.DeleteDC(memory_dc)
        _user32.ReleaseDC(None, screen_dc)
        return None
    previous = _gdi32.SelectObject(memory_dc, bitmap)
    _user32.DrawIconEx(memory_dc, 0, 0, hicon, width, height, 0, None, DI_NORMAL)
    buffer = ctypes.string_at(bits, width * height * 4)
    image = QImage(
        buffer, width, height, QImage.Format_ARGB32_Premultiplied
    ).copy()
    _gdi32.SelectObject(memory_dc, previous)
    _gdi32.DeleteObject(bitmap)
    _gdi32.DeleteDC(memory_dc)
    _user32.ReleaseDC(None, screen_dc)
    return image


def load_icon(parsing_name, size):
    """取系统图标的位图，失败返回 None。"""
    try:
        _, index, hicon = _query(parsing_name)
    except OSError:
        return None
    big = _image_list_icon(index, SHIL_JUMBO)
    if big is None:
        big = _image_list_icon(index, SHIL_EXTRALARGE)
    source = big or hicon
    image = None
    if source:
        width, height = _icon_size(source)
        image = _render(source, width, height)
    if big:
        _user32.DestroyIcon(big)
    if hicon:
        _user32.DestroyIcon(hicon)
    if image is None or image.isNull():
        return None
    return QPixmap.fromImage(image).scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )