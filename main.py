"""桌面图标收录面板 —— 程序入口。

按热键（默认 Ctrl+Alt+D）在屏幕侧边呼出蓝色半透明收录面板，
桌面上的快捷方式会自动收录进来，图标位置可自由拖动。
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PyQt5.QtCore import QObject, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from kjsl import autostart, desktop_icons, taskbar, theme
from kjsl.config import Config
from kjsl.hotkey import HotkeyManager
from kjsl.panel import Panel, TooltipStyle
from kjsl.settings_dialog import SettingsDialog

MUTEX_NAME = "Local\\KJSL_DesktopDock"
ERROR_ALREADY_EXISTS = 183
_mutex_handle = None

# 默认热键被别的软件占用时，按顺序尝试这些备选
FALLBACK_HOTKEYS = ("Ctrl+Alt+D", "Ctrl+Alt+K", "Ctrl+Alt+Space", "Ctrl+Shift+F9", "F9")


def already_running():
    """通过命名互斥体保证只运行一个实例（自启 + 手动启动时不会开两份）。"""
    global _mutex_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return bool(_mutex_handle) and ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def build_tray_icon():
    """用代码画出托盘图标，避免依赖外部图片资源。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    path = QPainterPath()
    path.addRoundedRect(QRectF(3, 3, 58, 58), 15, 15)
    gradient = QLinearGradient(0, 0, 0, 64)
    gradient.setColorAt(0, QColor(96, 162, 255))
    gradient.setColorAt(1, QColor(28, 82, 190))
    painter.fillPath(path, gradient)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255, 240))
    for cx, cy in ((23, 23), (41, 23), (23, 41), (41, 41)):
        painter.drawRoundedRect(QRectF(cx - 6, cy - 6, 12, 12), 3, 3)
    painter.end()
    return QIcon(pixmap)


class AppController(QObject):
    """把配置、面板、热键、托盘串起来。"""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.config = Config.load()
        self._sync_autostart()

        self.panel = Panel(self.config, on_open_settings=self.open_settings)
        self.panel.message.connect(self.notify)

        self.hotkey = HotkeyManager(self.panel.toggle)

        self.tray = QSystemTrayIcon(build_tray_icon(), self)
        self._set_tray_tip()
        self._build_menu()
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.panel.start()

        # 运行期间持续保持桌面图标隐藏（资源管理器重启等情况下会被重新显示）
        self._icon_guard = QTimer(self)
        self._icon_guard.setInterval(2000)
        self._icon_guard.timeout.connect(self._guard_desktop_icons)
        if self.config.get("hide_desktop_icons"):
            desktop_icons.set_hidden(True)
            self._icon_guard.start()

        # 任务栏透明：资源管理器重启会重建任务栏窗口，需要重新套用
        self._taskbar_applied = []
        self._taskbar_time = 0.0
        self._taskbar_guard = QTimer(self)
        self._taskbar_guard.setInterval(2000)
        self._taskbar_guard.timeout.connect(self._guard_taskbar)
        if self.config.get("taskbar_transparent"):
            self.apply_taskbar(True)

        self._setup_hotkey()

    def _setup_hotkey(self):
        """注册配置里的热键；被占用时自动退到备选组合并告知用户。"""
        configured = str(self.config.get("hotkey"))
        if self.hotkey.register(configured):
            return
        for candidate in FALLBACK_HOTKEYS:
            if candidate == configured:
                continue
            if self.hotkey.register(candidate):
                self.config.set("hotkey", candidate)
                self.config.save()
                self._set_tray_tip()
                self.notify(
                    "热键 %s 已被其他程序占用，已自动改用 %s" % (configured, candidate)
                )
                return
        self.notify("热键 %s 注册失败，请在设置中更换" % configured)

    def _set_tray_tip(self):
        self.tray.setToolTip("桌面收录 · %s 呼出面板" % self.config.get("hotkey"))

    # ---------- 托盘 ----------

    def _build_menu(self):
        menu = QMenu()
        menu.addAction("显示 / 隐藏面板").triggered.connect(self.panel.toggle)
        menu.addSeparator()
        menu.addAction("重新扫描桌面").triggered.connect(self.rescan)
        menu.addAction("重置图标位置").triggered.connect(self.reset_positions)
        menu.addAction("恢复被移除的图标").triggered.connect(self.restore_removed)
        menu.addSeparator()
        self.action_top = menu.addAction("窗口置顶")
        self.action_top.setCheckable(True)
        self.action_top.triggered.connect(self._toggle_top)
        self.action_start = menu.addAction("开机自动启动")
        self.action_start.setCheckable(True)
        self.action_start.triggered.connect(self._toggle_autostart)
        self.action_hide_icons = menu.addAction("隐藏系统桌面图标")
        self.action_hide_icons.setCheckable(True)
        self.action_hide_icons.triggered.connect(self._toggle_hide_desktop_icons)
        self.action_fullscreen = menu.addAction("全屏/最大化时不弹出")
        self.action_fullscreen.setCheckable(True)
        self.action_fullscreen.triggered.connect(self._toggle_fullscreen)
        menu.addSeparator()
        menu.addAction("设置...").triggered.connect(self.open_settings)
        menu.addSeparator()
        menu.addAction("退出").triggered.connect(self.quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self._sync_menu()

    def _sync_menu(self):
        self.action_top.setChecked(bool(self.config.get("always_on_top")))
        self.action_start.setChecked(autostart.is_enabled())
        self.action_hide_icons.setChecked(bool(self.config.get("hide_desktop_icons")))
        self.action_fullscreen.setChecked(bool(self.config.get("skip_fullscreen")))

    def _on_tray_activated(self, reason):
        # 只响应双击：单击与双击会先后触发，同时监听会导致切换两次等于没反应
        if reason == QSystemTrayIcon.DoubleClick:
            self.panel.toggle()

    def notify(self, text):
        self.tray.showMessage("桌面收录", text, QSystemTrayIcon.Information, 3000)

    # ---------- 菜单动作 ----------

    def rescan(self):
        self.panel.refresh()
        self.notify("已重新扫描桌面，共收录 %d 个图标" % len(self.panel.items))

    def reset_positions(self):
        self.config.set("positions", {})
        self.config.save()
        self.panel.refresh()
        self.notify("图标位置已重置为自动排列")

    def restore_removed(self):
        if not self.config.get("excluded"):
            self.notify("当前没有被移除的图标")
            return
        self.config.set("excluded", [])
        self.config.save()
        self.panel.refresh()
        self.notify("已恢复全部被移除的图标")

    def _toggle_top(self):
        self.config.set("always_on_top", self.action_top.isChecked())
        self.config.save()
        self.panel.apply_config()

    def _toggle_autostart(self):
        enabled = self.action_start.isChecked()
        if autostart.set_enabled(enabled):
            self.config.set("auto_start", enabled)
            self.config.save()
            self.notify("已开启开机自动启动" if enabled else "已关闭开机自动启动")
        else:
            self.notify("修改开机自启动失败，可能被安全软件拦截")
        self._sync_menu()

    def _toggle_fullscreen(self):
        self.config.set("skip_fullscreen", self.action_fullscreen.isChecked())
        self.config.save()
        self.notify(
            "全屏/最大化窗口时将不弹出面板"
            if self.action_fullscreen.isChecked()
            else "全屏/最大化窗口时仍会弹出面板"
        )

    def set_desktop_icons_hidden(self, hidden, notify=True):
        """隐藏/还原系统桌面图标，并记住原始状态以便退出时还原。"""
        if hidden and not self.config.get("hide_desktop_icons"):
            self.config.set("desktop_icons_original", desktop_icons.read_hidden())
        if desktop_icons.set_hidden(hidden):
            self.config.set("hide_desktop_icons", hidden)
            self.config.save()
            if hidden:
                self._icon_guard.start()
            else:
                self._icon_guard.stop()
            if notify:
                self.notify(
                    "已隐藏系统桌面图标，图标都收进面板了"
                    if hidden
                    else "已恢复系统桌面图标"
                )
        elif notify:
            self.notify("修改桌面图标显示状态失败，可能被安全软件拦截")
        self._sync_menu()

    def _guard_desktop_icons(self):
        """运行期间持续保持隐藏，直到关闭程序或关掉该设置。"""
        if not self.config.get("hide_desktop_icons"):
            self._icon_guard.stop()
            return
        desktop_icons.set_hidden(True)

    def _toggle_hide_desktop_icons(self):
        self.set_desktop_icons_hidden(self.action_hide_icons.isChecked())

    def apply_taskbar(self, enabled, notify=False):
        """开启/关闭任务栏背景透明（只改背景，不动图标、时钟、网络状态）。"""
        taskbar.set_transparent(enabled)
        self.config.set("taskbar_transparent", enabled)
        self.config.save()
        self._taskbar_applied = taskbar.taskbar_windows() if enabled else []
        self._taskbar_time = time.monotonic()
        if enabled:
            self._taskbar_guard.start()
        else:
            self._taskbar_guard.stop()
        if notify:
            self.notify("任务栏已改为透明背景" if enabled else "任务栏已恢复原样")

    def _guard_taskbar(self):
        if not self.config.get("taskbar_transparent"):
            self._taskbar_guard.stop()
            return
        handles = taskbar.taskbar_windows()
        now = time.monotonic()
        # 任务栏窗口被重建，或隔一段时间兜底重套一次
        if handles != self._taskbar_applied or now - self._taskbar_time > 15:
            taskbar.set_transparent(True)
            self._taskbar_applied = handles
            self._taskbar_time = now

    # ---------- 设置 ----------

    def open_settings(self):
        dialog = SettingsDialog(
            self.config, current_width=self.panel._w, current_height=self.panel._h
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        values = dialog.values()

        old_hotkey = str(self.config.get("hotkey"))
        new_hotkey = values.pop("hotkey")
        if new_hotkey != old_hotkey:
            if self.hotkey.register(new_hotkey):
                self.config.set("hotkey", new_hotkey)
                self._set_tray_tip()
            else:
                QMessageBox.warning(
                    None,
                    "热键注册失败",
                    "「%s」已被其他程序占用，已保留原热键 %s。" % (new_hotkey, old_hotkey),
                )

        old_hide_icons = bool(self.config.get("hide_desktop_icons"))
        old_taskbar = bool(self.config.get("taskbar_transparent"))
        for key, value in values.items():
            self.config.set(key, value)

        if not autostart.set_enabled(bool(values.get("auto_start"))):
            self.notify("修改开机自启动失败，可能被安全软件拦截")

        self.config.save()
        self.panel.apply_config()
        new_hide_icons = bool(self.config.get("hide_desktop_icons"))
        if new_hide_icons != old_hide_icons:
            self.set_desktop_icons_hidden(new_hide_icons)
        new_taskbar = bool(self.config.get("taskbar_transparent"))
        if new_taskbar != old_taskbar:
            self.apply_taskbar(new_taskbar, notify=True)
        self._sync_menu()

    # ---------- 退出 ----------

    def quit(self):
        self.shutdown()
        self.app.quit()

    def shutdown(self):
        self.panel.save_positions()
        self.hotkey.unregister()
        self._icon_guard.stop()
        self._taskbar_guard.stop()
        # 退出时把桌面图标恢复原样，避免关掉程序后桌面上什么都点不到
        if self.config.get("hide_desktop_icons"):
            desktop_icons.set_hidden(bool(self.config.get("desktop_icons_original")))
        # 退出时还原任务栏样式
        if self.config.get("taskbar_transparent"):
            taskbar.set_transparent(False)
        self.config.save()
        self.tray.hide()

    def _sync_autostart(self):
        self.config.set("auto_start", autostart.is_enabled())


def main():
    if already_running():
        return 0

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    # 否则 150% 缩放会被取整成 200%，界面元素会整体偏大
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("桌面收录")
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont(theme.FONT_FAMILY, 9))
    app.setStyle(TooltipStyle())   # 提示更快弹出（0.4 秒）

    controller = AppController(app)
    app.aboutToQuit.connect(controller.shutdown)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())