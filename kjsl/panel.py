"""屏幕侧边的淡蓝色半透明收录面板。"""

import ctypes
import math
import os
from ctypes import wintypes

from PyQt5.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QFileInfo,
    QFileSystemWatcher,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QFileIconProvider,
    QInputDialog,
    QLabel,
    QMenu,
    QProxyStyle,
    QStyle,
    QToolButton,
    QWidget,
)

from . import foreground, shell_items, theme
from .desktop import desktop_dirs, scan

PAD = 8
GAP = 10
HEADER_H = 34
CORNER = 14
LEAVE_DELAY = 100       # 鼠标移出后多久收起
HOVER_POLL = 40         # 展开状态下的光标检测间隔
SLIDE_IN_MS = 190       # 展开动画时长
SLIDE_OUT_MS = 120      # 收起动画时长（比展开更快，避免挡手）
RESIZE_BAND = 9
MIN_WIDTH = 110
MIN_HEIGHT = 220
WATCH_DELAY = 900       # 桌面有变动后延迟多久重新扫描
TILE_TEXT_PAD = 54      # 磁贴宽度 = 图标宽度 + 文字预留
TILE_TEXT_PAD_MIN = 20  # 不显示文字时的磁贴留白

WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
TOOLTIP_DELAY_MS = 400   # 鼠标悬停多久弹出提示
WINDOW_GUARD_MS = 700    # 检查面板是否被外部最小化/遮挡的间隔
EDGE_POLL_MS = 90        # 主动检测鼠标是否停在屏幕边缘的间隔
EDGE_DWELL = 2           # 连续命中几次才算停留（约 180ms，避免扫过就弹）
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOP = 0
HWND_TOPMOST = ctypes.c_void_p(-1)
HWND_NOTOPMOST = ctypes.c_void_p(-2)

_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
SW_SHOWNOACTIVATE = 4
SW_SHOWNA = 8


def _clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def wrap_text(text, metrics, width, max_lines=2):
    """把文字折成最多 max_lines 行，放不下的部分省略。"""
    lines = []
    rest = text
    while rest and len(lines) < max_lines:
        taken = 0
        while taken < len(rest) and metrics.horizontalAdvance(rest[: taken + 1]) <= width:
            taken += 1
        if taken == 0:
            taken = 1
        if taken < len(rest) and rest[taken] != " ":
            space = rest.rfind(" ", 0, taken)
            if space > 0:
                taken = space + 1
        if taken >= len(rest):
            lines.append(rest)
            rest = ""
        else:
            lines.append(rest[:taken].rstrip())
            rest = rest[taken:]
    if rest and lines:
        lines[-1] = metrics.elidedText(
            (lines[-1] + rest).strip(), Qt.ElideRight, width
        )
    return lines


_provider = None


IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".ico", ".webp")


def file_icon(path, size):
    """取系统文件图标（快捷方式会带上小箭头，与桌面一致）。"""
    global _provider
    if _provider is None:
        _provider = QFileIconProvider()
    return _provider.icon(QFileInfo(path)).pixmap(size, size)


def load_override_icon(path, size):
    """加载用户指定的图标文件：图片直接读，程序/快捷方式取系统图标。"""
    if not path or not os.path.exists(path):
        return None
    if path.lower().endswith(IMAGE_SUFFIX):
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            return pixmap.scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
    return file_icon(path, size)


def load_item_icon(item, size):
    """系统图标走 Shell 命名空间，其余走文件图标；自定义图标优先。"""
    if item.icon_path:
        pixmap = load_override_icon(item.icon_path, size)
        if pixmap is not None and not pixmap.isNull():
            return pixmap
    if getattr(item, "is_virtual", False):
        pixmap = shell_items.load_icon(item.path, size)
        if pixmap is not None and not pixmap.isNull():
            return pixmap
    return file_icon(item.path, size)


class IconWidget(QWidget):
    """面板上的单个磁贴：图标在上、文字在下，左键启动，按住拖动调整位置。"""

    launch_requested = pyqtSignal(object)
    remove_requested = pyqtSignal(object)
    customize_requested = pyqtSignal(object, str)   # (item, rename / icon / restore)
    moved = pyqtSignal()

    def __init__(self, item, icon_size, show_label, parent=None):
        super().__init__(parent)
        self.item = item
        self.moved_by_user = False
        self.draggable = True
        self.grid = None        # (step_x, step_y, origin_x, origin_y)，None 表示自由摆放
        self.icon_opacity = 1.0
        self.label_opacity = 1.0
        self.label_color = theme.TEXT
        self._show_label = show_label
        self._icon = load_item_icon(item, icon_size)
        self._icon_px = icon_size
        self._hover = False
        self._dragging = False
        self._press_global = None
        self._press_pos = None
        # 不显示名称时靠提示辨认图标，所以提示显示名称而不是完整路径
        self.setToolTip(item.display_name if not show_label else "")
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._hover or self._dragging:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 170 if self._dragging else 120))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 9, 9)

        icon_x = (self.width() - self._icon.width()) // 2
        painter.setOpacity(_clamp(self.icon_opacity, 0.05, 1.0))
        painter.drawPixmap(icon_x, 5, self._icon)
        if not self._show_label:
            return

        painter.setOpacity(_clamp(self.label_opacity, 0.05, 1.0))
        metrics = QFontMetrics(self.font())
        line_h = metrics.height()
        text_width = max(20, self.width() - 10)
        lines = wrap_text(self.item.display_name, metrics, text_width)
        painter.setPen(QColor(self.label_color))
        top = 5 + self._icon.height() + 3
        for index, line in enumerate(lines):
            painter.drawText(
                QRect(5, top + index * line_h, text_width, line_h),
                Qt.AlignHCenter | Qt.AlignVCenter,
                line,
            )

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global = event.globalPos()
        self._press_pos = self.pos()
        self._dragging = False
        self.raise_()
        self.update()

    def mouseMoveEvent(self, event):
        if self._press_global is None:
            return
        delta = event.globalPos() - self._press_global
        if not self._dragging and delta.manhattanLength() < 6:
            return
        self._dragging = True
        # 自动排序开启时不允许挪动，但仍视为拖动，避免误触发打开
        if not self.draggable:
            return
        target = self._press_pos + delta
        if self.grid is not None:
            step_x, step_y, origin_x, origin_y = self.grid
            target.setX(int(origin_x + round((target.x() - origin_x) / float(step_x)) * step_x))
            target.setY(int(origin_y + round((target.y() - origin_y) / float(step_y)) * step_y))
        parent = self.parentWidget()
        target.setX(int(_clamp(target.x(), 0, parent.width() - self.width())))
        target.setY(int(_clamp(target.y(), 0, parent.height() - self.height())))
        self.move(target)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._press_global is None:
            return
        dragged = self._dragging
        self._press_global = None
        self._dragging = False
        self.update()
        if dragged:
            self.moved.emit()
        else:
            self.launch_requested.emit(self.item)

    def contextMenuEvent(self, event):
        window = self.window()
        window.raise_()
        window.activateWindow()
        menu = QMenu(self)
        action_open = menu.addAction("打开")
        menu.addSeparator()
        action_rename = menu.addAction("重命名...")
        action_icon = menu.addAction("更换图标...")
        action_restore = None
        if self.item.alias or self.item.icon_path:
            action_restore = menu.addAction("恢复默认名称与图标")
        menu.addSeparator()
        action_remove = menu.addAction("从面板移除")
        window.hold_collapse()
        try:
            chosen = menu.exec_(event.globalPos())
        finally:
            window.release_collapse()
        if chosen is None:
            return
        if chosen is action_open:
            self.launch_requested.emit(self.item)
        elif chosen is action_rename:
            self.customize_requested.emit(self.item, "rename")
        elif chosen is action_icon:
            self.customize_requested.emit(self.item, "icon")
        elif chosen is action_restore:
            self.customize_requested.emit(self.item, "restore")
        elif chosen is action_remove:
            self.remove_requested.emit(self.item)


class TooltipStyle(QProxyStyle):
    """把系统默认的提示弹出延迟改成更快的 0.4 秒。"""

    def styleHint(self, hint, option=None, widget=None, data=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return TOOLTIP_DELAY_MS
        return super().styleHint(hint, option, widget, data)


class ScrollIndicator(QWidget):
    """贴在内容区侧边的细长滚动位置指示条。"""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        panel = self._panel
        limit = panel.max_scroll()
        if limit <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        ratio = self.height() / float(max(self.height(), panel._content_h))
        bar_height = max(30, int(self.height() * ratio))
        offset = int((self.height() - bar_height) * (panel._scroll / float(limit)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(28, 84, 160, 165))
        painter.drawRoundedRect(
            0, offset, self.width(), bar_height, self.width() // 2, self.width() // 2
        )


class Panel(QWidget):
    """停靠在屏幕左/右侧的收录面板，可拖动、可调宽。"""

    message = pyqtSignal(str)

    def __init__(self, config, on_open_settings):
        super().__init__(None)
        self.config = config
        self._on_open_settings = on_open_settings

        self.items = []
        self.icons = []
        self._revealed = False
        self._pinned = False
        self._anim = None

        self._w = 200
        self._h = 320
        self._cols = 1
        self._per_col = 10
        self._tile_w = 76
        self._tile_h = 84
        self._margin = 10

        self._drag_mode = None
        self._press_global = None
        self._press_geom = None
        self._collapse_held = False
        self._drag_hold = False

        self._scroll = 0          # 当前滚动偏移
        self._content_h = 0       # 图标内容总高度
        self._rows = 1            # 自动排列所需的行数

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setWindowTitle("桌面收录")
        self.setStyleSheet(
            "QLabel#title { color: %s; font-size: 13px; font-weight: 600; }"
            "QLabel#empty { color: %s; font-size: 12px; }"
            "QToolButton { border: none; background: transparent; color: %s; }"
            "QToolButton:hover { background: rgba(255, 255, 255, 120); border-radius: 7px; }"
            % (theme.TEXT, theme.TEXT_DIM, theme.TEXT_DIM)
        )

        self.title = QLabel(self)
        self.title.setObjectName("title")
        self._apply_title()
        self.btn_settings = QToolButton(self)
        self.btn_settings.setText("⚙")
        self.btn_settings.setFont(QFont("Segoe UI Symbol", 12))
        self.btn_settings.setToolTip("设置")
        self.btn_settings.clicked.connect(self._open_settings)
        self.btn_close = QToolButton(self)
        self.btn_close.setText("✕")
        self.btn_close.setFont(QFont("Segoe UI Symbol", 11))
        self.btn_close.setToolTip("收起面板")
        self.btn_close.clicked.connect(self.collapse)

        self.empty_hint = QLabel("桌面暂无可收录的图标", self)
        self.empty_hint.setObjectName("empty")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)

        # 图标放在独立容器里，超出可视范围的部分由容器自动裁剪，不会盖住标题栏
        self._area = QWidget(self)
        self._area.setMouseTracking(True)
        self._area.installEventFilter(self)
        self._scrollbar = ScrollIndicator(self)

        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._maybe_collapse)

        # 光标轮询：窗口在滑动时 leaveEvent 不一定及时，主动检测更可靠
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(HOVER_POLL)
        self._hover_timer.timeout.connect(self._check_cursor)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save_positions)

        # 桌面有新增/删除时自动重新收录
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_desktop_changed)
        self._watch_timer = QTimer(self)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.setInterval(WATCH_DELAY)
        self._watch_timer.timeout.connect(self._on_watch_timeout)

        # 看护窗口：被「显示桌面」之类的操作最小化或压到桌面后面时自动纠正
        self._window_guard = QTimer(self)
        self._window_guard.setInterval(WINDOW_GUARD_MS)
        self._window_guard.timeout.connect(self._guard_window)
        self._window_guard.start()

        # 主动检测屏幕边缘：显示桌面后桌面窗口可能压住面板的边缘，
        # 鼠标事件收不到，所以这里直接用光标位置判断，不依赖窗口命中
        self._edge_hits = 0
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(EDGE_POLL_MS)
        self._edge_timer.timeout.connect(self._check_edge)
        self._edge_timer.start()

        self._apply_flags()

    # ---------- 生命周期 ----------

    def start(self):
        self.refresh()

    def refresh(self):
        """重新扫描桌面并重建全部图标。"""
        self.items = scan(
            self.config.get("excluded"), self.config.get("custom")
        )
        self._watch_desktop()
        # 套用自定义名称与图标（只影响显示，不动原文件）
        aliases = self.config.get("aliases")
        icons = self.config.get("icons")
        aliases = aliases if isinstance(aliases, dict) else {}
        icons = icons if isinstance(icons, dict) else {}
        for item in self.items:
            item.alias = str(aliases.get(item.key) or "")
            item.icon_path = str(icons.get(item.key) or "")
        for widget in self.icons:
            widget.setParent(None)
            widget.deleteLater()
        self.icons = []
        icon_px = int(self.config.get("icon_size"))
        show_label = bool(self.config.get("show_label"))
        font = self.label_font()
        icon_opacity = float(self.config.get("icon_opacity", 1.0))
        label_opacity = float(self.config.get("label_opacity", 1.0))
        label_color = str(self.config.get("label_color") or theme.TEXT)
        for item in self.items:
            widget = IconWidget(item, icon_px, show_label, self._area)
            widget.setFont(font)
            widget.icon_opacity = icon_opacity
            widget.label_opacity = label_opacity
            widget.label_color = label_color
            widget.installEventFilter(self)
            widget.launch_requested.connect(self._launch)
            widget.remove_requested.connect(self._remove)
            widget.customize_requested.connect(self._customize)
            widget.moved.connect(self._on_moved)
            widget.show()
            self.icons.append(widget)
        self._apply_layout()

    def apply_config(self):
        """设置变更后重新生效。"""
        self._apply_title()
        self._apply_flags()
        self.refresh()
        self.update()

    def _apply_title(self):
        text = str(self.config.get("panel_title") or "").strip()
        self.title.setText(text or "桌面收录")

    # ---------- 显示与隐藏 ----------

    def reveal(self, pinned=False):
        if self.config.get("skip_fullscreen") and foreground.is_busy():
            return
        self._pinned = pinned
        self._revealed = True
        self._leave_timer.stop()
        if not self.isVisible():
            self.move(self._hidden_point())
            self.show()
        self.raise_()
        self._slide(self._shown_point(), SLIDE_IN_MS)
        if pinned:
            self._hover_timer.stop()
        else:
            self._hover_timer.start()

    def collapse(self):
        self._pinned = False
        self._revealed = False
        self._leave_timer.stop()
        self._hover_timer.stop()
        if self.config.get("edge_hover"):
            self._slide(self._hidden_point(), SLIDE_OUT_MS)
        else:
            self.hide()

    def toggle(self):
        if self._is_shown_now():
            self.collapse()
        else:
            # 状态不对（被系统挪走、被最小化、已经收起）时一律重新展开，
            # 保证热键任何时候都能把面板叫出来
            self.reveal(pinned=True)

    def _is_shown_now(self):
        """是否真的处于展开状态：位置、可见性、最小化状态都要核对。"""
        if not self._revealed:
            return False
        hwnd = int(self.winId())
        if _user32.IsIconic(hwnd) or not _user32.IsWindowVisible(hwnd):
            return False
        return abs(self.x() - self._shown_point().x()) < 8

    def enterEvent(self, event):
        # 鼠标回到面板上，立刻取消待收起的计时
        self._leave_timer.stop()
        if self.config.get("edge_hover") and not self._revealed:
            self.reveal(pinned=False)

    def leaveEvent(self, event):
        if self._pinned or not self.config.get("edge_hover"):
            return
        if not self._leave_timer.isActive():
            self._leave_timer.start(LEAVE_DELAY)

    def _check_cursor(self):
        """主动检测光标是否还在面板上，比等待 leaveEvent 更及时。"""
        if self._pinned or self._drag_mode or self._collapse_held or self._drag_hold:
            return
        if self._cursor_inside():
            self._leave_timer.stop()
            return
        if not self._leave_timer.isActive():
            self._leave_timer.start(LEAVE_DELAY)

    def hold_collapse(self):
        """右键菜单、设置窗口等交互期间不要收起面板。"""
        self._collapse_held = True
        self._leave_timer.stop()

    def release_collapse(self):
        self._collapse_held = False

    def _cursor_inside(self):
        """光标是否还在面板上（含四周一圈隐形检测带，避免擦边就收起）。

        检测带只是判定用的虚拟范围，不改变窗口实际大小，也不影响绘制，
        所以鼠标稍微滑出面板边缘不会被当成离开。
        """
        cursor = QCursor.pos()
        margin = max(0, int(self.config.get("hover_margin") or 0))
        loose = self.rect().adjusted(-margin, -margin, margin, margin)
        if loose.contains(self.mapFromGlobal(cursor)):
            return True
        # 收起后露出的那条边缘带也算，避免展开/收起来回抖动
        return QRect(self._hidden_point(), self.size()).contains(cursor)

    def _maybe_collapse(self):
        if self._pinned or self._drag_mode or self._collapse_held or self._drag_hold:
            return
        if self._cursor_inside():
            return
        self.collapse()

    def _open_settings(self):
        self.hold_collapse()
        try:
            self._on_open_settings()
        finally:
            self.release_collapse()

    # ---------- 拖动移动与拖动调宽 ----------

    def _is_left(self):
        return self.config.get("side") == "left"

    def _near_inner_edge(self, pos):
        """靠近朝向屏幕中央的那条边（用于拖动调宽）。"""
        if self._is_left():
            return pos.x() >= self._w - RESIZE_BAND
        return pos.x() <= RESIZE_BAND

    def _near_bottom_edge(self, pos):
        """靠近底边（用于拖动调高）。"""
        return pos.y() >= self._h - RESIZE_BAND

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.pos()
        if self._near_inner_edge(pos):
            self._drag_mode = "width"
        elif self._near_bottom_edge(pos):
            self._drag_mode = "height"
        elif pos.y() <= HEADER_H:
            self._drag_mode = "move"
        else:
            self._drag_mode = None
            return
        self._drag_hold = True
        self._press_global = event.globalPos()
        self._press_geom = self.geometry()
        if self._drag_mode == "height":
            # 调整高度时固定上边缘，避免面板上下跳动
            self.config.set("panel_y", self._press_geom.y())

    def mouseMoveEvent(self, event):
        if self._drag_mode is None:
            self._update_cursor(event.pos())
            return
        delta = event.globalPos() - self._press_global
        screen = self._screen()
        if self._drag_mode == "move":
            self.move(self._press_geom.topLeft() + delta)
            return
        if self._drag_mode == "height":
            height = int(
                _clamp(
                    self._press_geom.height() + delta.y(),
                    MIN_HEIGHT,
                    screen.height() - 60,
                )
            )
            if height != self._h:
                self.config.set("panel_height", height)
                self._apply_layout()
            return
        # 内侧那条边才是拖动柄：停靠右侧时是左边缘（往左拖变宽），
        # 停靠左侧时是右边缘（往右拖变宽）
        if self._is_left():
            width = self._press_geom.width() + delta.x()
        else:
            width = self._press_geom.width() - delta.x()
        width = int(_clamp(width, MIN_WIDTH, screen.width() * 0.5))
        if width != self._w:
            self.config.set("panel_width", width)
            self._apply_layout()

    def mouseReleaseEvent(self, event):
        if self._drag_mode is None:
            return
        mode = self._drag_mode
        self._drag_mode = None
        self._drag_hold = False
        self._press_global = None
        self._press_geom = None
        if mode == "move":
            self._snap_after_move()
        else:
            self.config.save()

    def nativeEvent(self, event_type, message):
        """吞掉最小化指令：显示桌面（Win+D）会给每个窗口发 SC_MINIMIZE。"""
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if (
                msg.message == WM_SYSCOMMAND
                and (int(msg.wParam) & 0xFFF0) == SC_MINIMIZE
            ):
                return True, 0
        return super().nativeEvent(event_type, message)

    def changeEvent(self, event):
        super().changeEvent(event)
        # 兜底：若被其它途径最小化（例如直接调用 ShowWindow），立刻恢复
        if event.type() == QEvent.WindowStateChange and self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
            # 尺寸也一并按当前设置恢复，避免恢复成旧的小尺寸
            self.resize(self._w, self._h)
            self.move(
                self._shown_point() if self._revealed else self._hidden_point()
            )
            self.show()

    def _guard_window(self):
        """「显示桌面」等外部操作可能最小化或盖住面板，这里主动纠正回来。

        注意：外部调用 ShowWindow 时 Qt 自己的可见性判断可能不同步，
        所以这里以 Win32 的实际状态为准。
        """
        expect_visible = bool(self.config.get("edge_hover")) or self._revealed
        hwnd = int(self.winId())
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
            # 最小化再恢复时系统用的是旧尺寸，必须按当前设置整体重排一次
            self._apply_layout()
            self.raise_()
            return
        if expect_visible and not _user32.IsWindowVisible(hwnd):
            _user32.ShowWindow(hwnd, SW_SHOWNA)
            self._apply_layout()
        # 位置或尺寸被系统改掉（最小化恢复、休眠唤醒、分辨率变化等）时整体重排，
        # 动画进行中不动，避免和滑入滑出打架
        animating = (
            self._anim is not None
            and self._anim.state() == QAbstractAnimation.Running
        )
        if self.isVisible() and not animating:
            target = self._shown_point() if self._revealed else self._hidden_point()
            if self.pos() != target or self.size() != QSize(self._w, self._h):
                self._apply_layout()
        # 桌面窗口压在面板上时（显示桌面后），把面板重新抬到桌面之上，
        # 否则鼠标碰到屏幕边缘会被桌面挡住，边缘悬停失效
        if expect_visible and foreground.desktop_above(hwnd):
            _user32.SetWindowPos(
                hwnd, HWND_TOP, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        # 收起状态只露 6px 的一条边，把它放到最上层，这样任何最大化窗口
        # 都挡不住鼠标碰边缘，悬停呼出才随时可用（一条边不会遮挡内容）
        if expect_visible and not self._revealed:
            _user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        elif expect_visible and not self.config.get("always_on_top"):
            # 展开时按用户设置决定层级，但要保持在普通窗口之上（否则展开也看不见）
            _user32.SetWindowPos(
                hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )

    def _check_edge(self):
        """光标停在屏幕边缘就呼出面板，不依赖 enterEvent。

        「显示桌面」之后桌面窗口会跑到最前面，面板露出的那条边可能被它压住，
        窗口收不到鼠标事件，边缘悬停就失效了，所以自己判断光标位置。
        """
        if self._revealed or not self.config.get("edge_hover"):
            self._edge_hits = 0
            return
        if self.config.get("skip_fullscreen") and foreground.is_busy():
            self._edge_hits = 0
            return
        cursor = QCursor.pos()
        screen = self._screen()
        zone = int(_clamp(int(self.config.get("edge_peek")), 2, 24)) + 3
        if self._is_left():
            hit = cursor.x() <= screen.x() + zone
        else:
            hit = cursor.x() >= screen.x() + screen.width() - zone
        # 纵向要落在面板范围内，避免屏幕其它位置误触发
        top = self._top_y()
        hit = hit and top <= cursor.y() <= top + self._h
        if not hit:
            self._edge_hits = 0
            return
        self._edge_hits += 1
        if self._edge_hits >= EDGE_DWELL:
            self._edge_hits = 0
            self.reveal(pinned=False)

    def eventFilter(self, obj, event):
        """容器与图标不处理滚轮，统一转给面板做滚动。"""
        if event.type() == QEvent.Wheel:
            self.wheelEvent(event)
            return True
        if obj is self._area and event.type() == QEvent.ContextMenu:
            self.show_background_menu(event.globalPos())
            return True
        return super().eventFilter(obj, event)

    def contextMenuEvent(self, event):
        self.show_background_menu(event.globalPos())

    def show_background_menu(self, global_pos):
        """面板空白处右键：可添加桌面之外的任意路径。"""
        menu = QMenu(self)
        action_file = menu.addAction("添加文件或程序...")
        action_dir = menu.addAction("添加文件夹...")
        menu.addSeparator()
        action_rescan = menu.addAction("重新扫描桌面")
        self.hold_collapse()
        try:
            chosen = menu.exec_(global_pos)
        finally:
            self.release_collapse()
        if chosen is action_file:
            self.pick_and_add(folder=False)
        elif chosen is action_dir:
            self.pick_and_add(folder=True)
        elif chosen is action_rescan:
            self.refresh()

    def pick_and_add(self, folder=False):
        if folder:
            path = QFileDialog.getExistingDirectory(None, "选择要收录的文件夹")
        else:
            path, _ = QFileDialog.getOpenFileName(
                None,
                "选择要收录的文件或程序",
                "",
                "程序 (*.exe *.bat *.cmd *.lnk);;所有文件 (*.*)",
            )
        if path:
            self.add_custom(path)

    def add_custom(self, path):
        """把桌面之外的路径收录进面板。"""
        full = os.path.abspath(path)
        key = os.path.normcase(full)
        custom = list(self.config.get("custom") or [])
        if any(os.path.normcase(item) == key for item in custom):
            self.message.emit("「%s」已经在面板里了" % os.path.basename(full))
            return
        custom.append(full)
        self.config.set("custom", custom)
        # 之前被移除过的话一并取消排除
        self.config.set(
            "excluded",
            [item for item in (self.config.get("excluded") or []) if item != key],
        )
        self.config.save()
        self.refresh()
        self.message.emit("已添加「%s」" % os.path.basename(full))

    def wheelEvent(self, event):
        """滚轮滚动查看更多图标。"""
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        self._scroll_by(-int(steps * (self._tile_h + GAP)))
        event.accept()

    def _scroll_by(self, delta):
        limit = self.max_scroll()
        target = int(_clamp(self._scroll + delta, 0, limit))
        if target == self._scroll:
            return
        self._scroll = target
        self._place_icons()
        self._scrollbar.update()

    def _update_cursor(self, pos):
        if self._near_inner_edge(pos):
            self.setCursor(Qt.SizeHorCursor)
        elif self._near_bottom_edge(pos):
            self.setCursor(Qt.SizeVerCursor)
        elif pos.y() <= HEADER_H:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.unsetCursor()

    def _snap_after_move(self):
        """松手后贴向最近的一侧，并把收起位置也改到那一侧。"""
        screen = self._screen()
        geometry = self.geometry()
        side = "left" if geometry.center().x() < screen.center().x() else "right"
        top = int(
            _clamp(geometry.y(), screen.y(), screen.y() + screen.height() - self._h)
        )
        self.config.set("side", side)
        self.config.set("panel_y", top)
        self.config.save()
        self._apply_layout()
        if self._revealed:
            self._slide(self._shown_point())
        self.message.emit(
            "面板已停靠到%s，收起后会从这一侧隐藏" % ("左侧" if side == "left" else "右侧")
        )

    # ---------- 几何与布局 ----------

    def _screen(self):
        return QApplication.primaryScreen().availableGeometry()

    def _apply_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if self.config.get("always_on_top"):
            flags |= Qt.WindowStaysOnTopHint
        visible = self.isVisible()
        self.setWindowFlags(flags)
        if visible:
            self.show()

    def _tile_size(self):
        icon_px = int(self.config.get("icon_size"))
        if self.config.get("show_label"):
            line_h = QFontMetrics(self.label_font()).height()
            return icon_px + TILE_TEXT_PAD, icon_px + 2 * line_h + 17
        return icon_px + TILE_TEXT_PAD_MIN, icon_px + 16

    def label_font(self):
        size = int(self.config.get("label_font_size") or 9)
        return QFont(theme.FONT_FAMILY, max(6, min(20, size)))

    def _compute_geometry(self):
        screen = self._screen()
        count = len(self.items)
        self._tile_w, self._tile_h = self._tile_size()
        step_x = self._tile_w + GAP
        step_y = self._tile_h + GAP

        max_height = screen.height() - 60
        preferred = int(self.config.get("panel_width") or 0)
        if preferred <= 0:
            # 自动：按理想高度估算需要的列数，反推一个合适的宽度
            ideal = int(_clamp(screen.height() * 0.66, 320, max_height))
            rows_ideal = max(1, (ideal - HEADER_H - 2 * PAD) // step_y)
            columns = math.ceil(count / rows_ideal) if count else 1
            width = columns * step_x + PAD
        else:
            width = preferred
        width = int(_clamp(width, MIN_WIDTH, screen.width() * 0.5))
        self._cols = max(1, int((width - PAD) // step_x))

        rows = math.ceil(count / self._cols) if count else 1
        self._rows = max(1, rows)
        # 内容区高度（相对内容区顶部）：放不下时靠滚动查看
        self._content_h = 2 * PAD + self._rows * step_y
        auto_height = HEADER_H + RESIZE_BAND + self._content_h
        preferred_height = int(self.config.get("panel_height") or 0)
        height = preferred_height if preferred_height > 0 else auto_height
        height = int(_clamp(height, MIN_HEIGHT, max_height))

        self._w = width
        self._h = height
        self._row_w = self._tile_w
        self._row_h = self._tile_h
        self._scroll = int(_clamp(self._scroll, 0, self.max_scroll()))

    def max_scroll(self):
        """内容超出可视区时可滚动的最大偏移。"""
        return max(0, self._content_h - self._area.height())

    def _top_y(self):
        screen = self._screen()
        stored = self.config.get("panel_y")
        if isinstance(stored, (int, float)):
            return int(
                _clamp(stored, screen.y(), screen.y() + screen.height() - self._h)
            )
        return screen.y() + (screen.height() - self._h) // 2

    def _shown_point(self):
        screen = self._screen()
        if self._is_left():
            x = screen.x() + self._margin
        else:
            x = screen.x() + screen.width() - self._w - self._margin
        return QPoint(int(x), self._top_y())

    def _hidden_point(self):
        screen = self._screen()
        peek = int(_clamp(int(self.config.get("edge_peek")), 2, 24))
        if self._is_left():
            x = screen.x() - self._w + peek
        else:
            x = screen.x() + screen.width() - peek
        return QPoint(int(x), self._top_y())

    def _apply_layout(self):
        self._compute_geometry()
        if self._anim is not None:
            self._anim.stop()
        self.resize(self._w, self._h)
        self._place_chrome()
        # 尺寸变化后重新夹紧滚动位置
        self._scroll = int(_clamp(self._scroll, 0, self.max_scroll()))
        self._place_icons()
        self.move(self._shown_point() if self._revealed else self._hidden_point())
        if self.config.get("edge_hover") or self._revealed:
            self.show()
        else:
            self.hide()

    def _place_chrome(self):
        title_width = max(40, self._w - 3 * PAD - 62)
        self.title.setGeometry(PAD + 4, 0, title_width, HEADER_H)
        button_y = (HEADER_H - 24) // 2
        self.btn_close.setGeometry(self._w - PAD - 24, button_y, 24, 24)
        self.btn_settings.setGeometry(self._w - PAD - 50, button_y, 24, 24)
        self.empty_hint.setGeometry(PAD, HEADER_H + 20, self._w - 2 * PAD, 60)

        # 内边留出调宽/调高的拖动带，其余区域交给图标容器
        self._area.setGeometry(self.area_rect())
        area = self._area.geometry()
        # 指示条放在内侧那条空白带上，不会压住图标
        bar_x = self._w - 7 if self._is_left() else 3
        self._scrollbar.setGeometry(
            bar_x, area.y() + 2, 4, max(0, area.height() - 4)
        )
        self._scrollbar.raise_()

    def area_rect(self):
        """图标容器几何：避开朝向屏幕中央的边和底边，让拖动带仍归面板处理。"""
        x = 0 if self._is_left() else RESIZE_BAND
        width = max(20, self._w - RESIZE_BAND)
        height = max(20, self._h - HEADER_H - RESIZE_BAND)
        return QRect(x, HEADER_H, width, height)

    def _place_icons(self):
        positions = self.config.get("positions")
        if not isinstance(positions, dict):
            positions = {}
        auto_sort = bool(self.config.get("auto_sort"))
        snap = bool(self.config.get("snap_to_grid"))
        area = self._area.geometry()
        step_x = self._tile_w + GAP
        step_y = self._tile_h + GAP
        taken = []
        slot = 0
        for widget in self.icons:
            widget.resize(self._tile_w, self._tile_h)
            widget.draggable = True      # 始终可以自由拖动
            # 网格对齐用的是自动排列的格点（容器坐标）
            widget.grid = (step_x, step_y, PAD - area.x(), PAD) if snap else None
            saved = None if auto_sort else positions.get(widget.item.key)
            if isinstance(saved, (list, tuple)) and len(saved) == 2:
                x, y = int(saved[0]), int(saved[1])
                # 位置是用户摆过的，重新扫描后要保留，不能被当成自动排列清掉
                widget.moved_by_user = True
            else:
                x = y = None
                while slot < 4000:
                    column, row = divmod(slot, self._rows)
                    slot += 1
                    column = min(column, self._cols - 1)
                    candidate_x = PAD + column * step_x
                    candidate_y = HEADER_H + PAD + row * step_y
                    if not _overlaps(
                        candidate_x, candidate_y, taken, self._tile_w, self._tile_h
                    ):
                        x, y = candidate_x, candidate_y
                        break
                if x is None:
                    x, y = PAD, HEADER_H + PAD
            # 面板坐标 -> 容器坐标（容器顶部为内容原点，再叠加滚动偏移）
            x = int(_clamp(x - area.x(), 0, max(0, area.width() - self._tile_w)))
            y = int(y - HEADER_H - self._scroll)
            taken.append((x + area.x(), y + HEADER_H + self._scroll))
            widget.move(x, y)
        self.empty_hint.setVisible(not self.icons)

    def _slide(self, point, duration=SLIDE_IN_MS):
        if self._anim is None:
            self._anim = QPropertyAnimation(self, b"pos", self)
            self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(point)
        self._anim.start()

    # ---------- 图标行为 ----------

    def _launch(self, item):
        if not item.is_virtual and not os.path.exists(item.path):
            self.refresh()
            self.message.emit("「%s」已不存在，已从面板移除" % item.name)
            return
        try:
            os.startfile(item.path)
        except OSError as exc:
            self.message.emit("无法打开「%s」：%s" % (item.name, exc))

    def _remove(self, item):
        excluded = list(self.config.get("excluded") or [])
        if item.key not in excluded:
            excluded.append(item.key)
        self.config.set("excluded", excluded)
        self.config.save()
        self.refresh()
        self.message.emit("已移除「%s」，可在托盘菜单中恢复" % item.name)

    def _customize(self, item, action):
        """右键改名 / 换图标 / 恢复默认：只写入映射，不改动原文件。"""
        self.hold_collapse()
        try:
            if action == "rename":
                self._rename_item(item)
            elif action == "icon":
                self._change_icon(item)
            else:
                self._restore_item(item)
        finally:
            self.release_collapse()

    def _rename_item(self, item):
        text, ok = QInputDialog.getText(
            None,
            "重命名",
            "面板上显示的名称（留空恢复原名）：",
            text=item.alias or item.name,
        )
        if not ok:
            return
        text = text.strip()
        aliases = dict(self.config.get("aliases") or {})
        if text and text != item.name:
            aliases[item.key] = text
        else:
            aliases.pop(item.key, None)
        self.config.set("aliases", aliases)
        self.config.save()
        self.refresh()

    def _change_icon(self, item):
        current = item.icon_path
        start = os.path.dirname(current) if current else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            None,
            "选择图标",
            start,
            "图片图标 (*.ico *.png *.jpg *.jpeg *.bmp *.gif *.webp);;"
            "程序或快捷方式 (*.exe *.dll *.lnk);;所有文件 (*.*)",
        )
        if not path:
            return
        icons = dict(self.config.get("icons") or {})
        icons[item.key] = os.path.abspath(path)
        self.config.set("icons", icons)
        self.config.save()
        self.refresh()

    def _restore_item(self, item):
        aliases = dict(self.config.get("aliases") or {})
        icons = dict(self.config.get("icons") or {})
        aliases.pop(item.key, None)
        icons.pop(item.key, None)
        self.config.set("aliases", aliases)
        self.config.set("icons", icons)
        self.config.save()
        self.refresh()

    def _on_moved(self):
        sender = self.sender()
        if isinstance(sender, IconWidget):
            sender.moved_by_user = True
        if self.config.get("auto_sort"):
            # 手动摆过就视为接管排列，自动排序自动关闭，否则位置会被它覆盖掉
            self.config.set("auto_sort", False)
            self.config.save()
            self.message.emit("已手动摆放图标，自动排序已关闭")
        self._save_timer.start()

    # ---------- 桌面变动自动收录 ----------

    def _watch_desktop(self):
        watched = set(self._watcher.directories())
        wanted = {path for path in desktop_dirs() if os.path.isdir(path)}
        for path in wanted - watched:
            self._watcher.addPath(path)
        for path in watched - wanted:
            self._watcher.removePath(path)

    def _on_desktop_changed(self, _path):
        self._watch_timer.start()

    def _on_watch_timeout(self):
        # 正在拖动时不重建控件，等下一次变动
        if self._drag_mode or any(
            getattr(widget, "_dragging", False) for widget in self.icons
        ):
            self._watch_timer.start()
            return
        self.refresh()

    def save_positions(self):
        """只记录用户手动拖过的图标，其余继续自动排列，便于随窗口自适应。"""
        if self.config.get("auto_sort"):
            return   # 自动排序时不使用手动位置，保留原有记录以便关掉后恢复
        stored = self.config.get("positions")
        positions = dict(stored) if isinstance(stored, dict) else {}
        area = self._area.geometry()
        for widget in self.icons:
            key = widget.item.key
            if widget.moved_by_user:
                # 容器坐标 -> 面板坐标（与滚动位置无关，滚动后位置不乱跳）
                positions[key] = [
                    widget.x() + area.x(),
                    widget.y() + HEADER_H + self._scroll,
                ]
            else:
                positions.pop(key, None)
        self.config.set("positions", positions)
        self.config.save()

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        alpha = _clamp(float(self.config.get("opacity")), 0.2, 1.0)

        rect = QRectF(self.rect().adjusted(0, 0, -1, -1))
        path = QPainterPath()
        path.addRoundedRect(rect, CORNER, CORNER)

        gradient = QLinearGradient(0, 0, 0, self.height())
        base = self.config.get("bg_color")
        if not (isinstance(base, (list, tuple)) and len(base) == 3):
            base = theme.PANEL_TOP
        base = [int(_clamp(int(c), 0, 255)) for c in base]
        gradient.setColorAt(0, _rgba(base, alpha))
        gradient.setColorAt(1, _rgba([int(c * 0.78) for c in base], alpha))
        painter.fillPath(path, gradient)

        painter.setPen(QPen(_rgba(theme.PANEL_BORDER, alpha), 1))
        painter.drawPath(path)

        painter.setPen(QPen(_rgba(theme.PANEL_BORDER, alpha * 0.8), 1))
        painter.drawLine(PAD, HEADER_H - 1, self.width() - PAD, HEADER_H - 1)


def _rgba(rgb, alpha):
    return QColor(rgb[0], rgb[1], rgb[2], int(255 * _clamp(alpha, 0.0, 1.0)))


def _overlaps(x, y, taken, width, height):
    for taken_x, taken_y in taken:
        if abs(x - taken_x) < width - 4 and abs(y - taken_y) < height - 4:
            return True
    return False