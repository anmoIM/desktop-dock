"""屏幕侧边的淡蓝色半透明收录面板。"""

import ctypes
import math
import os
import time
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
from .config import ZoneConfig
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
COLLAPSED_H = 38        # 折叠后只留标题栏的高度
MIN_WIDTH = 150         # 再窄标题和标题栏上的按钮就挤在一起了
MIN_HEIGHT = 220
WATCH_DELAY = 900       # 桌面有变动后延迟多久重新扫描
TILE_TEXT_PAD_MIN = 20  # 磁贴宽度 = 图标宽度 + 左右留白（显示文字时也不变）
TRAIL_TICK_MS = 16      # 拖尾刷新间隔（约 60 帧）
TRAIL_BASE_MS = 110     # 拖尾 1 档的时长
TRAIL_STEP_MS = 35      # 每加一档多留 35ms
TRAIL_ALPHA = 150       # 最浓的那个残影的不透明度
TRAIL_FRAMES = 5        # 预先生成几档大小的残影
TRAIL_SKIP = 0.12       # 比这个还新的点不画，免得压住图标本体

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


# 文字自适应结果：{(名称, 宽度, 可用高度, 基准字号): (字体, 折行结果)}
_LABEL_FIT_CACHE = {}
LABEL_MIN_PT = 6        # 自适应时最小缩到几号字
LABEL_MAX_LINES = 4     # 自适应时最多折几行


def fit_label(name, base_font, width, height, min_pt=LABEL_MIN_PT):
    """把名字完整塞进格子里：先按原字号折行，放不下就逐级缩小字号，
    字号小了行高也小，格子里能多折一行，所以长名字多半不用省略号。

    返回 (实际用的字体, 折好的行, 该字体的行高)；实在放不下才省略收尾。
    """
    key = (name, width, height, base_font.pointSize())
    cached = _LABEL_FIT_CACHE.get(key)
    if cached is not None:
        return cached

    def attempt(size):
        font = QFont(base_font)
        font.setPointSize(size)
        metrics = QFontMetrics(font)
        line_h = max(1, metrics.height())
        room = max(1, min(LABEL_MAX_LINES, height // line_h))
        return font, metrics, wrap_text(name, metrics, width, room), line_h

    chosen = None
    size = max(int(base_font.pointSize()), min_pt)
    while size >= min_pt:
        font, metrics, lines, line_h = attempt(size)
        if not lines or not lines[-1].endswith("…"):
            chosen = (font, lines, line_h)
            break
        size -= 1
    if chosen is None:
        font, metrics, lines, line_h = attempt(min_pt)
        chosen = (font, lines, line_h)

    if len(_LABEL_FIT_CACHE) > 400:
        _LABEL_FIT_CACHE.clear()
    _LABEL_FIT_CACHE[key] = chosen
    return chosen


_provider = None
# 快捷方式目标解析缓存：{路径: (修改时间, 目标路径或 "")}，没改动就不重复解析
_SHORTCUT_CACHE = {}
# 目标图标位图缓存：{(目标路径, 尺寸): QPixmap}，Shell 取图标偏慢，缓存后刷新即时完成
_TARGET_ICON_CACHE = {}


IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".ico", ".webp")


def file_icon(path, size):
    """取系统文件图标（快捷方式会带上小箭头，与桌面一致）。"""
    global _provider
    if _provider is None:
        _provider = QFileIconProvider()
    pixmap = _provider.icon(QFileInfo(path)).pixmap(size, size)
    if pixmap.width() > size or pixmap.height() > size:
        # Qt 有时会返回比要求更大的位图（例如 48px），会压住图标下方的文字
        pixmap = pixmap.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
    return pixmap


def shortcut_source(path):
    """快捷方式换成它指向的目标，这样取的图标不带左下角的小箭头。"""
    lower = path.lower()
    if lower.endswith(".lnk"):
        target = shell_items.resolve_shortcut(path)
        return target if target and os.path.exists(target) else ""
    if lower.endswith(".url"):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    field, _, value = line.partition("=")
                    if field.strip().lower() == "iconfile":
                        target = os.path.expandvars(value.strip().strip('"'))
                        return target if target and os.path.exists(target) else ""
        except OSError:
            return ""
    return ""


def load_item_icon(item, size, hide_arrow=False):
    """系统图标走 Shell 命名空间，其余走文件图标；自定义图标优先。"""
    if item.icon_path:
        pixmap = load_override_icon(item.icon_path, size)
        if pixmap is not None and not pixmap.isNull():
            return pixmap
    if getattr(item, "is_virtual", False):
        pixmap = shell_items.load_icon(item.path, size)
        if pixmap is not None and not pixmap.isNull():
            return pixmap
    if hide_arrow and not item.is_dir:
        target = shortcut_target(item.path)
        if target:
            pixmap = target_icon(target, size)
            if pixmap is not None and not pixmap.isNull():
                return pixmap
    return file_icon(item.path, size)


def target_icon(target, size):
    """取快捷方式目标自身的图标（不带小箭头），结果缓存。"""
    key = (os.path.normcase(target), size)
    if key in _TARGET_ICON_CACHE:
        return _TARGET_ICON_CACHE[key]
    # Qt 的图标引擎拿不到可执行文件里的图标，走 Shell 才不掉成白纸图标
    pixmap = shell_items.load_icon(target, size)
    if pixmap is not None and not pixmap.isNull():
        if len(_TARGET_ICON_CACHE) > 400:
            _TARGET_ICON_CACHE.clear()
        _TARGET_ICON_CACHE[key] = pixmap
    return pixmap


def shortcut_target(path):
    """带缓存的快捷方式目标查询；快捷方式被改动时会自动重新解析。"""
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return ""
    cached = _SHORTCUT_CACHE.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    target = shortcut_source(path)
    _SHORTCUT_CACHE[path] = (stamp, target)
    return target


class DragTrail(QWidget):
    """拖动图标时的拖尾：一串逐渐变小、淡出的残影跟着鼠标走。

    独立透明窗口，可以画到面板外面（跨收纳区拖动时也看得到），不接收鼠标事件。
    """

    def __init__(self, pixmap, parent=None):
        super().__init__(
            parent,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # 预先按不同大小生成残影，绘制时直接取用，省掉每帧缩放
        self._frames = []
        for index in range(TRAIL_FRAMES):
            scale = 0.55 + 0.45 * (index / float(TRAIL_FRAMES - 1))
            size = max(8, int(pixmap.width() * scale))
            self._frames.append(
                pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        self._pad = pixmap.width() // 2 + 10
        self._samples = []          # [(时间戳, 全局坐标)]
        self._duration = 0.2
        self._timer = QTimer(self)
        self._timer.setInterval(TRAIL_TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self, global_pos, duration):
        self._duration = max(0.05, float(duration))
        self._samples = [(time.monotonic(), QPoint(global_pos))]
        self._timer.start()
        self._tick()

    def push(self, global_pos):
        if self._timer.isActive():
            self._samples.append((time.monotonic(), QPoint(global_pos)))

    def stop(self):
        self._timer.stop()
        self._samples = []
        self.hide()
        self.deleteLater()

    def _tick(self):
        now = time.monotonic()
        self._samples = [
            sample for sample in self._samples if now - sample[0] <= self._duration
        ]
        if not self._samples:
            self.hide()
            return
        xs = [point.x() for _, point in self._samples]
        ys = [point.y() for _, point in self._samples]
        x = min(xs) - self._pad
        y = min(ys) - self._pad
        self.setGeometry(
            int(x), int(y), int(max(xs) - x + self._pad), int(max(ys) - y + self._pad)
        )
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, event):
        now = time.monotonic()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for stamp, point in self._samples:
            fade = 1.0 - (now - stamp) / self._duration
            if fade <= TRAIL_SKIP:
                continue
            frame = self._frames[
                max(0, min(TRAIL_FRAMES - 1, int(fade * (TRAIL_FRAMES - 1))))
            ]
            painter.setOpacity(TRAIL_ALPHA / 255.0 * fade)
            painter.drawPixmap(
                point.x() - self.x() - frame.width() // 2,
                point.y() - self.y() - frame.height() // 2,
                frame,
            )


class DragGhost(QWidget):
    """跨收纳区拖动时跟着鼠标走的幽灵窗口（不接收鼠标事件，纯展示）。"""

    def __init__(self, pixmap, text=""):
        super().__init__(
            None,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._pixmap = pixmap
        self._text = text
        width = max(pixmap.width() + 16, 56)
        height = pixmap.height() + 12 + (16 if text else 0)
        self.resize(width, height)

    def follow(self, global_pos):
        self.move(
            global_pos.x() - self.width() // 2,
            global_pos.y() - self.height() // 2,
        )
        if not self.isVisible():
            self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(0.88)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 9, 9)
        painter.drawPixmap(
            (self.width() - self._pixmap.width()) // 2, 6, self._pixmap
        )
        if self._text:
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                QRect(2, 6 + self._pixmap.height(), self.width() - 4, 16),
                Qt.AlignHCenter | Qt.AlignVCenter,
                self._text,
            )


class IconWidget(QWidget):
    """面板上的单个磁贴：图标在上、文字在下，左键启动，按住拖动调整位置。"""

    launch_requested = pyqtSignal(object)
    remove_requested = pyqtSignal(object)
    customize_requested = pyqtSignal(object, str)   # (item, rename / icon / restore)
    move_zone_requested = pyqtSignal(object, str)   # (item, 目标收纳区 id)
    moved = pyqtSignal()

    def __init__(self, item, icon_size, show_label, hide_arrow=False, parent=None):
        super().__init__(parent)
        self.item = item
        self.moved_by_user = False
        self.draggable = True
        self.grid = None        # (step_x, step_y, origin_x, origin_y)，None 表示自由摆放
        self.icon_opacity = 1.0
        self.label_opacity = 1.0
        self.label_color = theme.TEXT
        self._show_label = show_label
        self._icon = load_item_icon(item, icon_size, hide_arrow)
        self._icon_px = icon_size
        self._hover = False
        self._dragging = False
        self._press_global = None
        self._press_pos = None
        self._ghost = None       # 拖出面板后跟着鼠标的幽灵窗口
        self._trail = None       # 拖动时的拖尾特效
        self._outside = False    # 是否正拖在面板外（可能要落到别的收纳区）
        self.trail_duration = 0.0   # 拖尾时长，0 表示不开拖尾
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

        # 格子里垂直居中：图标在上、文字在下，作为一个整体居中，四周留白均匀
        metrics = QFontMetrics(self.font())
        if self._show_label:
            block_h = self._icon_px + 3 + 2 * metrics.height()
        else:
            block_h = self._icon_px
        block_top = max(2, (self.height() - block_h) // 2)

        icon_x = (self.width() - self._icon.width()) // 2
        icon_y = block_top + max(0, (self._icon_px - self._icon.height()) // 2)
        # 拖到面板外时留在原处的磁贴画淡一点，表明它正跟着鼠标走
        dim = 0.4 if self._outside else 1.0
        painter.setOpacity(_clamp(self.icon_opacity, 0.05, 1.0) * dim)
        painter.drawPixmap(icon_x, icon_y, self._icon)
        if not self._show_label:
            return

        painter.setOpacity(_clamp(self.label_opacity, 0.05, 1.0) * dim)
        # 文字最多用到格子内边 2px：宽一点，长名字更容易完整放下
        text_width = max(20, self.width() - 4)
        # 紧贴图标下方（按图标实际底边算，图标扁的时候也不会留下空档）
        top = icon_y + self._icon.height() + 2
        label_font, lines, line_h = fit_label(
            self.item.display_name, self.font(), text_width, self.height() - top
        )
        painter.setFont(label_font)
        painter.setPen(QColor(self.label_color))
        for index, line in enumerate(lines):
            painter.drawText(
                QRect(2, top + index * line_h, text_width, line_h),
                Qt.AlignHCenter | Qt.AlignTop,
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
        self.update()
        # 自动排序开启时不允许挪动，但仍视为拖动，避免误触发打开
        if not self.draggable:
            return
        self._start_trail(event.globalPos())
        window = self.window()
        if not window.frameGeometry().contains(event.globalPos()):
            self._drag_outside(event.globalPos())
            return
        self._drag_inside(event.globalPos(), delta)

    def _drag_inside(self, global_pos, delta):
        """在面板内拖动：磁贴自由跟随鼠标，松手时才吸附到格子。"""
        self._clear_ghost()
        if not self.isVisible():
            self.show()
        target = self._press_pos + delta
        parent = self.parentWidget()
        target.setX(int(_clamp(target.x(), 0, parent.width() - self.width())))
        target.setY(int(_clamp(target.y(), 0, parent.height() - self.height())))
        self.move(target)

    def _snap_to_grid(self):
        """松手时吸附到最近的格点（拖动过程中不吸附）。"""
        if self.grid is None:
            return
        step_x, step_y, origin_x, origin_y = self.grid
        x = int(origin_x + round((self.x() - origin_x) / float(step_x)) * step_x)
        y = int(origin_y + round((self.y() - origin_y) / float(step_y)) * step_y)
        parent = self.parentWidget()
        x = int(_clamp(x, 0, parent.width() - self.width()))
        y = int(_clamp(y, 0, parent.height() - self.height()))
        self.move(x, y)

    def _drag_outside(self, global_pos):
        """拖出面板：磁贴留在原处并变淡，改用幽灵窗口跟随鼠标，好落到别的收纳区。

        这里不能 hide() 磁贴：按住鼠标期间的事件靠隐式抓取送到磁贴，
        一旦把它隐藏，移动和松手事件就都收不到了，图标会卡住、幽灵窗口留在屏幕上。
        """
        if self._ghost is None:
            self._ghost = DragGhost(
                self._icon, self.item.display_name if self._show_label else ""
            )
        self._ghost.follow(global_pos)
        self._ghost.raise_()
        if not self._outside:
            self._outside = True
            self.update()

    def _clear_ghost(self):
        if self._ghost is not None:
            self._ghost.close()
            self._ghost.deleteLater()
            self._ghost = None
        if self._outside:
            self._outside = False
            self.update()

    def _start_trail(self, global_pos):
        if self.trail_duration <= 0:
            return
        if self._trail is None:
            self._trail = DragTrail(self._icon, self.window())
            self._trail.start(global_pos, self.trail_duration)
        else:
            self._trail.push(global_pos)

    def _stop_trail(self):
        if self._trail is not None:
            self._trail.stop()
            self._trail = None

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._press_global is None:
            return
        dragged = self._dragging
        outside = self._outside
        self._press_global = None
        self._dragging = False
        self._clear_ghost()
        self._stop_trail()
        if not self.isVisible():
            self.show()
        self.update()
        if not dragged:
            self.launch_requested.emit(self.item)
            return
        window = self.window()
        if outside and hasattr(window, "handle_drop_out"):
            if window.handle_drop_out(self, event.globalPos()):
                return      # 已经移入别的收纳区，本面板的磁贴会被重建
        elif not outside:
            self._snap_to_grid()   # 松手才落到格子里
        self.moved.emit()

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
        # 换收纳区：也可以直接把图标拖到别的面板上
        zone_actions = {}
        zones = window.config.raw.zones()
        if len(zones) > 1:
            current = window.config.raw.zone_of(self.item.key)
            submenu = menu.addMenu("移动到收纳区")
            for zone in zones:
                if zone.get("id") == current:
                    continue
                action = submenu.addAction(str(zone.get("name")))
                zone_actions[action] = zone.get("id")
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
        elif chosen in zone_actions:
            self.move_zone_requested.emit(self.item, zone_actions[chosen])


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
    """一个收纳区的面板：停靠在屏幕左/右侧，可拖动、可调宽、可折叠。"""

    message = pyqtSignal(str)
    move_zone_requested = pyqtSignal(object, str)   # 图标要换到别的收纳区
    paths_dropped = pyqtSignal(object, list, object)   # 从桌面/资源管理器拖进来的文件
    content_changed = pyqtSignal(object)   # 图标归属变了，其他面板也要刷新

    def __init__(self, config, zone, on_open_settings, on_drop_out=None):
        super().__init__(None)
        self.config = ZoneConfig(config, zone)
        self.zone = zone
        self._on_open_settings = on_open_settings
        self._on_drop_out = on_drop_out
        self._drop_hover = False     # 有文件正拖在面板上（画个提示边框）

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
        # 接收从桌面 / 资源管理器拖过来的文件（子控件不接收，会自动冒泡到面板）
        self.setAcceptDrops(True)
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
        self.btn_settings = QToolButton(self)
        self.btn_settings.setText("⚙")
        self.btn_settings.setFont(QFont("Segoe UI Symbol", 12))
        self.btn_settings.setToolTip("设置")
        self.btn_settings.clicked.connect(self._open_settings)
        self.btn_collapse = QToolButton(self)
        self.btn_collapse.setFont(QFont("Segoe UI Symbol", 11))
        self.btn_collapse.setToolTip("折叠 / 展开本收纳区")
        self.btn_collapse.clicked.connect(self.toggle_collapsed)
        self.btn_close = QToolButton(self)
        self.btn_close.setText("✕")
        self.btn_close.setFont(QFont("Segoe UI Symbol", 11))
        self.btn_close.setToolTip("收起面板")
        self.btn_close.clicked.connect(self.collapse)
        self._apply_title()

        self.empty_hint = QLabel(self._empty_text(), self)
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
        """重新扫描桌面并重建本收纳区的图标。"""
        zone_id = self.zone.get("id")
        raw = self.config.raw
        self.items = [
            item
            for item in scan(
                self.config.get("excluded"), self.config.get("custom")
            )
            if raw.zone_of(item.key) == zone_id
        ]
        self.empty_hint.setText(self._empty_text())
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
            widget._stop_trail()
            widget.setParent(None)
            widget.deleteLater()
        self.icons = []
        icon_px = int(self.config.get("icon_size"))
        show_label = bool(self.config.get("show_label"))
        font = self.label_font()
        icon_opacity = float(self.config.get("icon_opacity", 1.0))
        label_opacity = float(self.config.get("label_opacity", 1.0))
        label_color = str(self.config.get("label_color") or theme.TEXT)
        hide_arrow = bool(self.config.get("hide_shortcut_arrow"))
        trail_duration = self.trail_duration()
        for item in self.items:
            widget = IconWidget(item, icon_px, show_label, hide_arrow, self._area)
            widget.setFont(font)
            widget.trail_duration = trail_duration
            widget.icon_opacity = icon_opacity
            widget.label_opacity = label_opacity
            widget.label_color = label_color
            widget.installEventFilter(self)
            widget.launch_requested.connect(self._launch)
            widget.remove_requested.connect(self._remove)
            widget.customize_requested.connect(self._customize)
            widget.move_zone_requested.connect(self._on_move_zone)
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

    def set_zone(self, zone):
        """设置界面改完收纳区后，把面板指向最新的那份区数据。"""
        self.zone = zone
        self.config.zone = zone
        self._apply_title()

    def _apply_title(self):
        text = str(self.config.get("panel_title") or "").strip()
        self.title.setText(text or "桌面收录")
        self.btn_collapse.setText(self._collapsed_text())

    # ---------- 收纳区 ----------

    def _collapsed_text(self):
        return "▸" if self.zone.get("collapsed") else "▾"

    def _empty_text(self):
        raw = self.config.raw
        first = raw.first_zone_id()
        if self.zone.get("id") == first or not raw.zones():
            return "桌面暂无可收录的图标"
        return "本收纳区还没有图标\n把图标拖进来，或右键图标选「移动到收纳区」"

    def toggle_collapsed(self):
        """折叠/展开本收纳区，只留标题栏或恢复完整面板。"""
        collapsed = not bool(self.zone.get("collapsed"))
        self.zone["collapsed"] = collapsed
        self.config.save()
        self.btn_collapse.setText(self._collapsed_text())
        self._apply_layout()
        self.message.emit(
            "「%s」已折叠" % self.zone.get("name") if collapsed
            else "「%s」已展开" % self.zone.get("name")
        )

    def handle_drop_out(self, widget, global_pos):
        """磁贴被拖到面板外：交给主控判断有没有落到别的收纳区。"""
        if self._on_drop_out is None:
            return False
        return bool(self._on_drop_out(widget.item, global_pos))

    def _on_move_zone(self, item, zone_id):
        """右键菜单换区，交给主控处理。"""
        self.move_zone_requested.emit(item, zone_id)

    # ---------- 接住从桌面 / 资源管理器拖过来的文件 ----------

    @staticmethod
    def _dropped_paths(mime):
        if mime is None or not mime.hasUrls():
            return []
        paths = []
        for url in mime.urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        return paths

    def dragEnterEvent(self, event):
        if self._dropped_paths(event.mimeData()):
            event.acceptProposedAction()
            self._drop_hover = True
            self.hold_collapse()
            self.update()

    def dragMoveEvent(self, event):
        if self._dropped_paths(event.mimeData()):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop_hover = False
        self.release_collapse()
        self.update()

    def dropEvent(self, event):
        paths = self._dropped_paths(event.mimeData())
        self._drop_hover = False
        self.release_collapse()
        self.update()
        if not paths:
            return
        event.acceptProposedAction()
        if self.zone.get("collapsed"):
            self.toggle_collapsed()   # 折叠状态下也能拖进来，顺手展开给用户看
        self.paths_dropped.emit(self, paths, self.mapToGlobal(event.pos()))

    def drop_point(self, global_pos):
        """屏幕坐标换算成本面板里的落点（按网格对齐并夹在面板内）。"""
        local = self.mapFromGlobal(global_pos)
        x = local.x() - self._tile_w // 2
        y = local.y() + self._scroll - HEADER_H - self._tile_h // 2
        if self.config.get("snap_to_grid"):
            x, y = self._cell_point(*self._cell_at(x, y))
        area = self._area.geometry()
        x = int(_clamp(x, area.x(), max(area.x(), self._w - self._tile_w)))
        return (x, max(HEADER_H + PAD, int(y)))

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

    def _icon_dragging(self):
        """是否有磁贴正在被拖动（跨区拖动时别把面板收起来）。"""
        dragging = [w for w in self.icons if getattr(w, "_dragging", False)]
        if dragging and not QApplication.mouseButtons() & Qt.LeftButton:
            # 松手事件没送到（被系统或别的程序抢走）时清理残留标记，
            # 否则面板会一直以为在拖动，再也收不起来
            for widget in dragging:
                widget._dragging = False
                widget._clear_ghost()
                widget._stop_trail()
                widget.update()
            return False
        return bool(dragging)

    def _check_cursor(self):
        """主动检测光标是否还在面板上，比等待 leaveEvent 更及时。"""
        if self._pinned or self._drag_mode or self._collapse_held or self._drag_hold:
            return
        if self._icon_dragging():
            self._leave_timer.stop()
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
        if self._icon_dragging() or self._cursor_inside():
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
        collapsed = bool(self.zone.get("collapsed"))
        if not collapsed and self._near_inner_edge(pos):
            self._drag_mode = "width"
        elif not collapsed and self._near_bottom_edge(pos):
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
        """选文件/文件夹期间面板不收起，选完把面板亮出来让用户看到结果。"""
        self.hold_collapse()
        try:
            if folder:
                path = QFileDialog.getExistingDirectory(
                    self.window(), "选择要收录的文件夹"
                )
            else:
                path, _ = QFileDialog.getOpenFileName(
                    self.window(),
                    "选择要收录的文件或程序",
                    "",
                    "程序 (*.exe *.bat *.cmd *.lnk);;所有文件 (*.*)",
                )
        finally:
            self.release_collapse()
        if not path:
            return
        self.add_custom(path)
        if not self._revealed:
            self.reveal(pinned=False)

    def add_custom(self, path):
        """把路径收录进本收纳区（桌面之外的路径会记进手动添加列表）。"""
        full = os.path.abspath(path)
        key = os.path.normcase(full)
        raw = self.config.raw
        zone_id = self.zone.get("id")
        custom = list(raw.get("custom") or [])
        excluded = list(raw.get("excluded") or [])
        in_custom = any(os.path.normcase(item) == key for item in custom)
        # 只有在面板上真的看得到（没被移除）才算「已经在面板里」
        if in_custom and raw.zone_of(key) == zone_id and key not in excluded:
            self.message.emit("「%s」已经在面板里了" % os.path.basename(full))
            return
        if not in_custom:
            custom.append(full)
        raw.set("custom", custom)
        # 关键：归到本收纳区，否则会落到第一个区、在当前面板上看不到
        mapping = dict(raw.get("zone_of") or {})
        mapping[key] = zone_id
        raw.set("zone_of", mapping)
        # 之前被移除过的话一并取消排除
        raw.set("excluded", [item for item in excluded if item != key])
        raw.save()
        self.refresh()
        self.content_changed.emit(self)     # 原来在别的区的话，那边也要刷新
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
        # 格子宽度只跟图标有关：显示文字不再把网格横向撑大
        width = icon_px + TILE_TEXT_PAD_MIN
        if self.config.get("show_label"):
            line_h = QFontMetrics(self.label_font()).height()
            # 文字紧贴图标下方，只多出文字本身的高度，四周留白与不显示文字时一致
            return width, icon_px + 3 + 2 * line_h + 16
        return width, icon_px + 16

    def label_font(self):
        size = int(self.config.get("label_font_size") or 9)
        return QFont(theme.FONT_FAMILY, max(6, min(20, size)))

    def trail_duration(self):
        """拖尾时长（秒），0 表示关闭；档位越大尾巴越长。"""
        if not self.config.get("drag_trail"):
            return 0.0
        level = int(_clamp(int(self.config.get("drag_trail_length") or 5), 1, 10))
        return (TRAIL_BASE_MS + (level - 1) * TRAIL_STEP_MS) / 1000.0

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
        if self.zone.get("collapsed"):
            height = COLLAPSED_H   # 折叠后只剩标题栏

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
        collapsed = bool(self.zone.get("collapsed"))
        self._area.setVisible(not collapsed)
        self._scrollbar.setVisible(not collapsed)
        self.empty_hint.setVisible(not collapsed and not self.icons)
        for widget in self.icons:
            widget.setVisible(not collapsed)
        self.move(self._shown_point() if self._revealed else self._hidden_point())
        if self.config.get("edge_hover") or self._revealed:
            self.show()
        else:
            self.hide()

    def _place_chrome(self):
        title_width = max(40, self._w - 3 * PAD - 88)
        self.title.setGeometry(PAD + 4, 0, title_width, HEADER_H)
        button_y = (HEADER_H - 24) // 2
        self.btn_close.setGeometry(self._w - PAD - 24, button_y, 24, 24)
        self.btn_settings.setGeometry(self._w - PAD - 50, button_y, 24, 24)
        self.btn_collapse.setGeometry(self._w - PAD - 76, button_y, 24, 24)
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

    def _cell_point(self, column, row):
        """格子左上角的面板坐标。"""
        return (
            PAD + column * (self._tile_w + GAP),
            HEADER_H + PAD + row * (self._tile_h + GAP),
        )

    def _cell_at(self, x, y):
        """面板坐标落在哪个格子上（就近取整，不越出面板宽度）。"""
        step_x = self._tile_w + GAP
        step_y = self._tile_h + GAP
        column = int(round((x - PAD) / float(step_x)))
        row = int(round((y - HEADER_H - PAD) / float(step_y)))
        return (int(_clamp(column, 0, self._cols - 1)), max(0, row))

    def _nearest_free_cell(self, column, row, used):
        """从指定格子往外一圈圈找最近的空格子，保证挤过去的图标也有位置。"""
        for radius in range(0, 400):
            best = None
            for dc in range(-radius, radius + 1):
                for dr in range(-radius, radius + 1):
                    if max(abs(dc), abs(dr)) != radius:
                        continue
                    c = column + dc
                    r = row + dr
                    if c < 0 or c > self._cols - 1 or r < 0 or (c, r) in used:
                        continue
                    distance = dc * dc + dr * dr
                    if best is None or distance < best[0]:
                        best = (distance, c, r)
            if best is not None:
                return (best[1], best[2])
        return (column, max(0, row))

    def _place_icons(self):
        positions = self.config.get("positions")
        if not isinstance(positions, dict):
            positions = {}
        auto_sort = bool(self.config.get("auto_sort"))
        snap = bool(self.config.get("snap_to_grid"))
        area = self._area.geometry()
        step_x = self._tile_w + GAP
        step_y = self._tile_h + GAP
        for widget in self.icons:
            widget.resize(self._tile_w, self._tile_h)
            widget.draggable = True      # 始终可以自由拖动
            # 网格对齐用的是自动排列的格点（容器坐标）
            widget.grid = (step_x, step_y, PAD - area.x(), PAD) if snap else None

        placed = []      # 已经放好的位置（面板坐标）
        used = set()     # 已经被占用的格子：一个格子只放一个图标
        fixed = []       # (图标, 面板坐标)，最终要落到界面上的结果
        manual = []      # 用户摆过的图标先落位
        pending = []     # 其余图标再补空格子
        for widget in self.icons:
            saved = None if auto_sort else positions.get(widget.item.key)
            if isinstance(saved, (list, tuple)) and len(saved) == 2:
                # 位置是用户摆过的，重新扫描后要保留，不能被当成自动排列清掉
                widget.moved_by_user = True
                if snap:
                    point = self._cell_point(*self._cell_at(int(saved[0]), int(saved[1])))
                else:
                    point = (int(saved[0]), int(saved[1]))
                manual.append((widget, point))
            else:
                pending.append(widget)

        def blocked(column, row, point):
            if snap and (column, row) in used:
                return True
            return _overlaps(point[0], point[1], placed, self._tile_w, self._tile_h)

        for widget, point in manual:
            cell = self._cell_at(point[0], point[1])
            if blocked(cell[0], cell[1], point):
                # 改过图标大小或开关过文字后原来那格可能已被占用，挪到最近的空格子
                cell = self._nearest_free_cell(cell[0], cell[1], used)
                point = self._cell_point(*cell)
            used.add(cell)
            placed.append(point)
            fixed.append((widget, point))

        slot = 0
        for widget in pending:
            point = None
            while slot < self._cols * self._rows:
                column, row = divmod(slot, self._rows)
                slot += 1
                candidate = self._cell_point(column, row)
                if blocked(column, row, candidate):
                    continue
                point = candidate
                used.add((column, row))
                break
            if point is None:
                cell = self._nearest_free_cell(0, 0, used)
                used.add(cell)
                point = self._cell_point(*cell)
            placed.append(point)
            fixed.append((widget, point))

        # 被挤到原规划之外的行时把内容区加高，滚轮才能看到它
        rows_used = max((cell[1] for cell in used), default=0) + 1
        if rows_used > self._rows:
            self._rows = rows_used
            self._content_h = 2 * PAD + self._rows * step_y
            self._scroll = int(_clamp(self._scroll, 0, self.max_scroll()))

        # 面板坐标 -> 容器坐标（容器顶部为内容原点，再叠加滚动偏移）
        for widget, point in fixed:
            x = int(_clamp(point[0] - area.x(), 0, max(0, area.width() - self._tile_w)))
            y = int(point[1] - HEADER_H - self._scroll)
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
        self.content_changed.emit(self)
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
            self._push_aside(sender)
        if self.config.get("auto_sort"):
            # 手动摆过就视为接管排列，自动排序自动关闭，否则位置会被它覆盖掉
            self.config.set("auto_sort", False)
            self.config.save()
            self.message.emit("已手动摆放图标，自动排序已关闭")
        self._save_timer.start()

    def _push_aside(self, dragged):
        """拖到已有图标的格子上时，把被压住的图标挤到最近的空格子。"""
        area = self._area.geometry()

        def panel_point(widget):
            return (widget.x() + area.x(), widget.y() + HEADER_H + self._scroll)

        drop = panel_point(dragged)
        used = {self._cell_at(*panel_point(widget)) for widget in self.icons}
        pushed = []
        for widget in self.icons:
            if widget is dragged:
                continue
            point = panel_point(widget)
            if not _overlaps(drop[0], drop[1], [point], self._tile_w, self._tile_h):
                continue
            cell = self._nearest_free_cell(*self._cell_at(*point), used)
            used.add(cell)
            x, y = self._cell_point(*cell)
            widget.move(x - area.x(), y - HEADER_H - self._scroll)
            widget.moved_by_user = True
            pushed.append(widget.item.display_name)
        if pushed:
            self.message.emit("「%s」已被挤到相邻空位" % "」「".join(pushed))

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
        if self._drag_mode or self._icon_dragging():
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

        if self._drop_hover:
            # 有文件正拖在面板上：套一圈高亮，提示可以松手放进来了
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(58, 123, 255, 220), 3))
            painter.drawPath(path)
            painter.setPen(QPen(QColor(58, 123, 255, 230), 1))
            painter.drawText(
                QRectF(0, self.height() / 2 - 14, self.width(), 28),
                Qt.AlignCenter,
                "松手收进「%s」" % str(self.zone.get("name") or "收纳区"),
            )


def _rgba(rgb, alpha):
    return QColor(rgb[0], rgb[1], rgb[2], int(255 * _clamp(alpha, 0.0, 1.0)))


def _overlaps(x, y, taken, width, height):
    for taken_x, taken_y in taken:
        if abs(x - taken_x) < width - 4 and abs(y - taken_y) < height - 4:
            return True
    return False