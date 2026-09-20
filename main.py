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
    QSystemTrayIcon,
)

from kjsl import autostart, desktop_icons, taskbar, theme
from kjsl.desktop import desktop_dirs
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

        self.panels = {}      # 收纳区 id -> 面板
        self.hotkeys = {}     # 收纳区 id -> 热键
        self._build_panels()

        self.tray = QSystemTrayIcon(build_tray_icon(), self)
        self._set_tray_tip()
        self._build_menu()
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

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

        self._setup_hotkeys()

    def _build_panels(self):
        """按配置里的收纳区逐个建面板。"""
        for zone in self.config.zones():
            panel = Panel(
                self.config,
                zone,
                on_open_settings=self.open_settings,
                on_drop_out=self.handle_drop_out,
            )
            panel.message.connect(self.notify)
            panel.move_zone_requested.connect(self.move_item_to_zone)
            panel.paths_dropped.connect(self.add_dropped)
            panel.content_changed.connect(self.refresh_other_panels)
            self.panels[zone["id"]] = panel
        for panel in self.panels.values():
            panel.start()

    def _setup_hotkeys(self):
        """每个收纳区各自的热键；第一个区被占用时自动退到备选组合。"""
        for zone in self.config.zones():
            panel = self.panels.get(zone["id"])
            text = str(zone.get("hotkey") or "")
            if panel is None or not text:
                continue
            manager = HotkeyManager(panel.toggle)
            if manager.register(text):
                self.hotkeys[zone["id"]] = manager
                continue
            if zone["id"] != self.config.first_zone_id():
                self.notify("「%s」的热键 %s 注册失败，请在设置中更换" % (zone.get("name"), text))
                continue
            for candidate in FALLBACK_HOTKEYS:
                if candidate == text:
                    continue
                if manager.register(candidate):
                    zone["hotkey"] = candidate
                    self.config.save()
                    self._set_tray_tip()
                    self.notify(
                        "热键 %s 已被其他程序占用，已自动改用 %s" % (text, candidate)
                    )
                    self.hotkeys[zone["id"]] = manager
                    break
            else:
                self.notify("热键 %s 注册失败，请在设置中更换" % text)

    def _reload_hotkeys(self):
        for manager in self.hotkeys.values():
            manager.unregister()
        self.hotkeys = {}
        self._setup_hotkeys()

    def _set_tray_tip(self):
        zones = self.config.zones()
        texts = [str(zone.get("hotkey") or "") for zone in zones]
        texts = [text for text in texts if text]
        if len(zones) == 1:
            self.tray.setToolTip("桌面收录 · %s 呼出面板" % (texts[0] if texts else ""))
        else:
            self.tray.setToolTip(
                "桌面收录 · %d 个收纳区（%s）" % (len(zones), "、".join(texts))
            )

    # ---------- 收纳区 ----------

    def zone_of(self, item):
        """图标当前属于哪个收纳区。"""
        return self.config.zone_of(item.key)

    def panel_at(self, global_pos):
        """屏幕坐标落在哪个面板上（用于跨收纳区拖动）。"""
        for panel in self.panels.values():
            if panel.isVisible() and panel.frameGeometry().contains(global_pos):
                return panel
        return None

    def handle_drop_out(self, item, global_pos):
        """图标被拖到面板外松手：落到哪个收纳区就改属哪个区。"""
        target = self.panel_at(global_pos)
        if target is None:
            return False
        zone_id = target.zone.get("id")
        if zone_id == self.zone_of(item):
            return False
        point = target.drop_point(global_pos)
        positions = dict(self.config.get("positions") or {})
        positions[item.key] = list(point)
        self.config.set("positions", positions)
        mapping = dict(self.config.get("zone_of") or {})
        mapping[item.key] = zone_id
        self.config.set("zone_of", mapping)
        self.config.save()
        for panel in self.panels.values():
            panel.refresh()
        self.notify("「%s」已移入「%s」" % (item.display_name, target.zone.get("name")))
        return True

    def refresh_other_panels(self, source):
        """图标归属变了：除了发起的面板，其他面板也重新扫一遍。"""
        for zone_id, panel in self.panels.items():
            if panel is not source:
                panel.refresh()

    def add_dropped(self, panel, paths, global_pos):
        """从桌面/资源管理器拖进面板的文件：收进该面板所属的收纳区。"""
        zone_id = panel.zone.get("id")
        excluded = list(self.config.get("excluded") or [])
        custom = list(self.config.get("custom") or [])
        mapping = dict(self.config.get("zone_of") or {})
        positions = dict(self.config.get("positions") or {})
        desktops = {
            os.path.normcase(os.path.abspath(path)) for path in desktop_dirs()
        }
        base_x, base_y = panel.drop_point(global_pos)
        names = []
        for index, path in enumerate(paths):
            full = os.path.abspath(path)
            if not os.path.exists(full):
                continue
            key = os.path.normcase(full)
            if key in excluded:
                excluded.remove(key)          # 之前被移除过，重新收进来
            if os.path.dirname(key) not in desktops and full not in custom:
                custom.append(full)           # 桌面之外的路径记进手动添加
            mapping[key] = zone_id            # 落到哪个区就归哪个区
            positions[key] = [
                base_x + (index % 4) * 14,
                base_y + (index % 4) * 14,
            ]
            names.append(os.path.basename(full))
        if not names:
            self.notify("拖进来的路径不存在，已忽略")
            return
        self.config.set("excluded", excluded)
        self.config.set("custom", custom)
        self.config.set("zone_of", mapping)
        self.config.set("positions", positions)
        self.config.save()
        for item in self.panels.values():
            item.refresh()
        self.notify(
            "已收录「%s」到「%s」" % ("」「".join(names[:3]), panel.zone.get("name"))
            if len(names) <= 3
            else "已收录 %d 个图标到「%s」" % (len(names), panel.zone.get("name"))
        )

    def move_item_to_zone(self, item, zone_id):
        """右键菜单换区：图标落到目标区的空位。"""
        zone = self.config.zone(zone_id)
        if zone is None or zone_id == self.zone_of(item):
            return
        mapping = dict(self.config.get("zone_of") or {})
        mapping[item.key] = zone_id
        self.config.set("zone_of", mapping)
        positions = dict(self.config.get("positions") or {})
        positions.pop(item.key, None)      # 到新收纳区按空位重新排
        self.config.set("positions", positions)
        self.config.save()
        for panel in self.panels.values():
            panel.refresh()
        self.notify("「%s」已移入「%s」" % (item.display_name, zone.get("name")))

    def toggle_zone(self, zone_id):
        panel = self.panels.get(zone_id)
        if panel is not None:
            panel.toggle()

    # ---------- 托盘 ----------

    def _build_menu(self):
        menu = QMenu()
        self.zones_menu = menu.addMenu("收纳区")
        self._sync_zones_menu()
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

    def _sync_zones_menu(self):
        """收纳区子菜单：每个区一个显示/隐藏项，末尾进设置管理。"""
        self.zones_menu.clear()
        for zone in self.config.zones():
            zone_id = zone["id"]
            panel = self.panels.get(zone_id)
            hotkey = str(zone.get("hotkey") or "")
            label = "显示 / 隐藏「%s」" % zone.get("name")
            if hotkey:
                label += "（%s）" % hotkey
            action = self.zones_menu.addAction(label)
            action.triggered.connect(lambda _=False, zid=zone_id: self.toggle_zone(zid))
            action.setEnabled(panel is not None)
        self.zones_menu.addSeparator()
        self.zones_menu.addAction("管理收纳区...").triggered.connect(self.open_settings)

    def _sync_menu(self):
        self._sync_zones_menu()
        self.action_top.setChecked(bool(self.config.get("always_on_top")))
        self.action_start.setChecked(autostart.is_enabled())
        self.action_hide_icons.setChecked(bool(self.config.get("hide_desktop_icons")))
        self.action_fullscreen.setChecked(bool(self.config.get("skip_fullscreen")))

    def _on_tray_activated(self, reason):
        # 只响应双击：单击与双击会先后触发，同时监听会导致切换两次等于没反应
        if reason != QSystemTrayIcon.DoubleClick:
            return
        panel = self.panels.get(self.config.first_zone_id())
        if panel is not None:
            panel.toggle()

    def _each_panel(self):
        return list(self.panels.values())

    def notify(self, text):
        self.tray.showMessage("桌面收录", text, QSystemTrayIcon.Information, 3000)

    # ---------- 菜单动作 ----------

    def rescan(self):
        total = 0
        for panel in self._each_panel():
            panel.refresh()
            total += len(panel.items)
        self.notify("已重新扫描桌面，共收录 %d 个图标" % total)

    def reset_positions(self):
        self.config.set("positions", {})
        self.config.save()
        for panel in self._each_panel():
            for widget in panel.icons:
                widget.moved_by_user = False
            panel.refresh()
        self.notify("图标位置已重置为自动排列")

    def restore_removed(self):
        if not self.config.get("excluded"):
            self.notify("当前没有被移除的图标")
            return
        self.config.set("excluded", [])
        self.config.save()
        for panel in self._each_panel():
            panel.refresh()
        self.notify("已恢复全部被移除的图标")

    def _toggle_top(self):
        self.config.set("always_on_top", self.action_top.isChecked())
        self.config.save()
        for panel in self._each_panel():
            panel.apply_config()

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
        sizes = {
            zone_id: (panel._w, panel._h) for zone_id, panel in self.panels.items()
        }
        dialog = SettingsDialog(self.config, sizes=sizes)
        if dialog.exec_() != QDialog.Accepted:
            return
        values = dialog.values()

        zones = values.pop("zones", None)
        if isinstance(zones, list) and zones:
            old_zones = {zone["id"] for zone in self.config.zones()}
            self.config.set("zones", zones)
            # 收纳区被删掉后，里面的图标回到第一个区，位置也重新排
            valid = {zone["id"] for zone in zones}
            removed = old_zones - valid
            mapping = self.config.get("zone_of") or {}
            gone = {key for key, zone_id in mapping.items() if zone_id in removed}
            self.config.set(
                "zone_of",
                {key: zone_id for key, zone_id in mapping.items() if zone_id in valid},
            )
            positions = dict(self.config.get("positions") or {})
            for key in gone:
                positions.pop(key, None)
            self.config.set("positions", positions)

        old_hide_icons = bool(self.config.get("hide_desktop_icons"))
        old_taskbar = bool(self.config.get("taskbar_transparent"))
        for key, value in values.items():
            self.config.set(key, value)

        if not autostart.set_enabled(bool(values.get("auto_start"))):
            self.notify("修改开机自启动失败，可能被安全软件拦截")

        self.config.save()
        self._sync_panels()
        new_hide_icons = bool(self.config.get("hide_desktop_icons"))
        if new_hide_icons != old_hide_icons:
            self.set_desktop_icons_hidden(new_hide_icons)
        new_taskbar = bool(self.config.get("taskbar_transparent"))
        if new_taskbar != old_taskbar:
            self.apply_taskbar(new_taskbar, notify=True)
        self._sync_menu()

    def _sync_panels(self):
        """按最新配置补齐/移除面板，并重新应用各区的设置。"""
        zones = self.config.zones()
        wanted = {zone["id"] for zone in zones}
        for zone_id in list(self.panels):
            if zone_id not in wanted:
                panel = self.panels.pop(zone_id)
                panel.save_positions()
                panel.hide()
                panel.deleteLater()
        for zone in zones:
            zone_id = zone["id"]
            if zone_id not in self.panels:
                panel = Panel(
                    self.config,
                    zone,
                    on_open_settings=self.open_settings,
                    on_drop_out=self.handle_drop_out,
                )
                panel.message.connect(self.notify)
                panel.move_zone_requested.connect(self.move_item_to_zone)
                panel.paths_dropped.connect(self.add_dropped)
                panel.content_changed.connect(self.refresh_other_panels)
                self.panels[zone_id] = panel
                panel.start()
            else:
                panel = self.panels[zone_id]
                panel.set_zone(zone)      # 区数据是新的对象，指向它才会生效
                panel.apply_config()
        self._reload_hotkeys()
        self._set_tray_tip()

    # ---------- 退出 ----------

    def quit(self):
        self.shutdown()
        self.app.quit()

    def shutdown(self):
        for panel in self._each_panel():
            panel.save_positions()
        for manager in self.hotkeys.values():
            manager.unregister()
        self.hotkeys = {}
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