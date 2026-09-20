"""全局热键：直接调用 Win32 RegisterHotKey，不依赖任何第三方库。"""

import ctypes
import time
from ctypes import wintypes

from PyQt5.QtCore import QAbstractNativeEventFilter, Qt
from PyQt5.QtGui import QKeySequence

WM_HOTKEY = 0x0312
HOTKEY_ID = 0xA17C

# 同一次按键偶尔会被 Qt 派发多次，用最小间隔去重
MIN_INTERVAL = 0.2

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_user32 = ctypes.windll.user32


def _build_key_map():
    table = {}

    # 字母与数字
    for code in range(ord("A"), ord("Z") + 1):
        table[code] = code
    for code in range(ord("0"), ord("9") + 1):
        table[code] = code
    # 功能键
    for index in range(24):
        table[Qt.Key_F1 + index] = 0x70 + index

    table.update(
        {
            Qt.Key_Space: 0x20,
            Qt.Key_Tab: 0x09,
            Qt.Key_Backspace: 0x08,
            Qt.Key_Return: 0x0D,
            Qt.Key_Enter: 0x0D,
            Qt.Key_Escape: 0x1B,
            Qt.Key_Insert: 0x2D,
            Qt.Key_Delete: 0x2E,
            Qt.Key_Home: 0x24,
            Qt.Key_End: 0x23,
            Qt.Key_PageUp: 0x21,
            Qt.Key_PageDown: 0x22,
            Qt.Key_Left: 0x25,
            Qt.Key_Up: 0x26,
            Qt.Key_Right: 0x27,
            Qt.Key_Down: 0x28,
            Qt.Key_Print: 0x2C,
            Qt.Key_Pause: 0x13,
            Qt.Key_CapsLock: 0x14,
            Qt.Key_ScrollLock: 0x91,
            Qt.Key_NumLock: 0x90,
            Qt.Key_Menu: 0x5D,
            Qt.Key_Minus: 0xBD,
            Qt.Key_Equal: 0xBB,
            Qt.Key_BracketLeft: 0xDB,
            Qt.Key_BracketRight: 0xDD,
            Qt.Key_Backslash: 0xDC,
            Qt.Key_Semicolon: 0xBA,
            Qt.Key_Apostrophe: 0xDE,
            Qt.Key_Comma: 0xBC,
            Qt.Key_Period: 0xBE,
            Qt.Key_Slash: 0xBF,
            Qt.Key_QuoteLeft: 0xC0,
            # 带 Shift 的符号统一映射回物理键位
            Qt.Key_Exclam: 0x31,
            Qt.Key_At: 0x32,
            Qt.Key_NumberSign: 0x33,
            Qt.Key_Dollar: 0x34,
            Qt.Key_Percent: 0x35,
            Qt.Key_AsciiCircum: 0x36,
            Qt.Key_Ampersand: 0x37,
            Qt.Key_Asterisk: 0x38,
            Qt.Key_ParenLeft: 0x39,
            Qt.Key_ParenRight: 0x30,
            Qt.Key_Underscore: 0xBD,
            Qt.Key_Plus: 0xBB,
            Qt.Key_BraceLeft: 0xDB,
            Qt.Key_BraceRight: 0xDD,
            Qt.Key_Bar: 0xDC,
            Qt.Key_Colon: 0xBA,
            Qt.Key_QuoteDbl: 0xDE,
            Qt.Key_Less: 0xBC,
            Qt.Key_Greater: 0xBE,
            Qt.Key_Question: 0xBF,
            Qt.Key_AsciiTilde: 0xC0,
        }
    )
    return table


KEY_MAP = _build_key_map()
_KEY_MASK = 0x01FFFFFF
_MOD_MASK = 0xFE000000


def parse_hotkey(text):
    """把 "Ctrl+Alt+D" 解析为 (Win32 修饰键, 虚拟键码)，无法识别时返回 None。"""
    if not text:
        return None
    sequence = QKeySequence(text)
    if sequence.isEmpty():
        return None
    combo = int(sequence[0])
    vk = KEY_MAP.get(combo & _KEY_MASK)
    if not vk:
        return None
    return _to_win_modifiers(combo & _MOD_MASK), vk


def _to_win_modifiers(modifiers):
    value = 0
    if modifiers & Qt.SHIFT:
        value |= MOD_SHIFT
    if modifiers & Qt.CTRL:
        value |= MOD_CONTROL
    if modifiers & Qt.ALT:
        value |= MOD_ALT
    if modifiers & Qt.META:
        value |= MOD_WIN
    return value


def format_hotkey(modifiers, key):
    """把 Qt 的修饰键与按键组合成可持久化的文本。"""
    combo = int(modifiers) | int(key)
    return QKeySequence(combo).toString(QKeySequence.NativeText)


class HotkeyManager(QAbstractNativeEventFilter):
    """注册系统级热键，触发时回调。"""

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self._registered = False
        self._text = ""
        self._last_trigger = -1.0
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self)

    @property
    def text(self):
        return self._text

    def register(self, text):
        """注册热键，失败（被占用或无法识别）返回 False，并保留原有热键。"""
        parsed = parse_hotkey(text)
        if parsed is None:
            return False
        modifiers, vk = parsed
        self.unregister()
        ok = bool(
            _user32.RegisterHotKey(
                None, HOTKEY_ID, modifiers | MOD_NOREPEAT, vk
            )
        )
        if ok:
            self._registered = True
            self._text = text
            return True
        if self._text:
            self._register_raw(self._text)
        return False

    def _register_raw(self, text):
        parsed = parse_hotkey(text)
        if parsed is None:
            return False
        modifiers, vk = parsed
        if _user32.RegisterHotKey(None, HOTKEY_ID, modifiers | MOD_NOREPEAT, vk):
            self._registered = True
            self._text = text
            return True
        return False

    def unregister(self):
        if self._registered:
            _user32.UnregisterHotKey(None, HOTKEY_ID)
            self._registered = False

    def nativeEventFilter(self, event_type, message):
        if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                now = time.monotonic()
                if now - self._last_trigger >= MIN_INTERVAL:
                    self._last_trigger = now
                    self._callback()
        return False, 0