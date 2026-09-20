"""桌面扫描：枚举桌面（含公共桌面）上的快捷方式、文件与文件夹。"""

import ctypes
import os
from dataclasses import dataclass

from . import shell_items

CSIDL_DESKTOP = 0x0000
CSIDL_COMMON_DESKTOP = 0x0019
SHGFP_TYPE_CURRENT = 0
FILE_ATTRIBUTE_HIDDEN = 0x2

SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
STRIP_SUFFIX = (".lnk", ".url")


@dataclass
class DesktopItem:
    key: str      # 归一化路径，作为位置与移除记录的持久化标识
    name: str     # 显示名（隐藏 .lnk / .url 后缀）
    path: str     # 真实路径
    is_dir: bool
    is_virtual: bool = False   # 系统图标（此电脑、回收站等），路径形如 ::{GUID}
    alias: str = ""            # 面板上自定义的名称，只影响显示
    icon_path: str = ""        # 面板上自定义的图标文件，只影响显示

    @property
    def display_name(self):
        return self.alias or self.name


def _known_folder(csidl):
    buf = ctypes.create_unicode_buffer(260)
    try:
        ret = ctypes.windll.shell32.SHGetFolderPathW(
            None, csidl, None, SHGFP_TYPE_CURRENT, buf
        )
    except OSError:
        return None
    return buf.value if ret == 0 and buf.value else None


def desktop_dirs():
    """返回当前用户桌面与公共桌面目录。"""
    dirs = []
    for csidl in (CSIDL_DESKTOP, CSIDL_COMMON_DESKTOP):
        path = _known_folder(csidl)
        if path and os.path.isdir(path) and path not in dirs:
            dirs.append(path)
    if not dirs:
        fallback = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(fallback):
            dirs.append(fallback)
    return dirs


def _display_name(filename):
    lower = filename.lower()
    for suffix in STRIP_SUFFIX:
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return filename


def _is_hidden(path):
    try:
        return bool(os.stat(path).st_file_attributes & FILE_ATTRIBUTE_HIDDEN)
    except (OSError, AttributeError):
        return False


def _sort_key(name):
    """让 2 排在 10 前面的自然排序。"""
    parts = []
    digits = ""
    for ch in name.lower():
        if ch.isdigit():
            digits += ch
        else:
            if digits:
                parts.append((1, int(digits), ""))
                digits = ""
            parts.append((0, 0, ch))
    if digits:
        parts.append((1, int(digits), ""))
    return parts


def scan(excluded=(), custom=()):
    """扫描桌面并合并手动添加的路径。excluded 中的条目会被跳过。"""
    excluded = set(excluded or ())
    items = []
    files = []
    seen = set()

    # 系统图标（此电脑、回收站等）不属于文件，单独放在最前面
    for entry in shell_items.list_items():
        if entry.key in excluded or entry.key in seen:
            continue
        seen.add(entry.key)
        items.append(
            DesktopItem(
                key=entry.key,
                name=entry.name,
                path=entry.path,
                is_dir=False,
                is_virtual=True,
            )
        )

    # 手动添加的外部路径（非桌面文件），按添加顺序排在桌面条目前面
    for path in custom or ():
        if not path or not os.path.exists(path):
            continue
        full = os.path.abspath(path)
        key = os.path.normcase(full)
        if key in excluded or key in seen:
            continue
        seen.add(key)
        files.append(
            DesktopItem(
                key=key,
                name=_display_name(os.path.basename(full)),
                path=full,
                is_dir=os.path.isdir(full),
            )
        )
    manual_count = len(files)

    for directory in desktop_dirs():
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            full = os.path.join(directory, name)
            key = os.path.normcase(os.path.abspath(full))
            if key in excluded or key in seen:
                continue
            if name.startswith(".") or name.lower() in SKIP_NAMES:
                continue
            if _is_hidden(full) or not os.path.exists(full):
                continue
            seen.add(key)
            files.append(
                DesktopItem(
                    key=key,
                    name=_display_name(name),
                    path=full,
                    is_dir=os.path.isdir(full),
                )
            )
    manual = files[:manual_count]
    desktop_files = files[manual_count:]
    desktop_files.sort(key=lambda item: _sort_key(item.name))
    return items + manual + desktop_files