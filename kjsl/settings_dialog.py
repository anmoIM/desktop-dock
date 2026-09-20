"""设置窗口：热键录制、面板外观与行为。"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .hotkey import format_hotkey, parse_hotkey

MIN_WIDTH = 110
MIN_HEIGHT = 220

# 内置文字颜色
LABEL_COLORS = (
    ("黑色", "#000000"),
    ("白色", "#ffffff"),
    ("红色", "#e53935"),
    ("橙色", "#fb8c00"),
    ("黄色", "#fdd835"),
    ("绿色", "#43a047"),
    ("蓝色", "#1e88e5"),
    ("淡蓝色", "#8ecbff"),
    ("紫色", "#8e24aa"),
)

STYLE = """
QDialog { background: #EAF4FF; }
QLabel { color: #123A63; font-family: '%(font)s'; font-size: 13px; }
QLabel#hint { color: #5C86B4; font-size: 12px; }
QGroupBox {
    color: #2C5C8F; font-family: '%(font)s'; font-size: 12px;
    border: 1px solid #C4DCF6; border-radius: 8px; background: #F5FAFF;
    margin-top: 12px; padding: 12px 10px 6px 10px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLineEdit, QComboBox {
    background: #FFFFFF; border: 1px solid #B7D3F2; border-radius: 6px;
    color: #12314F; padding: 5px 8px; font-family: '%(font)s'; font-size: 13px;
}
QLineEdit { font-weight: 600; letter-spacing: 1px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #3A7BFF; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #FFFFFF; color: #12314F; selection-background-color: #D6E8FF;
    selection-color: #12314F; border: 1px solid #B7D3F2; outline: none;
}
QCheckBox { color: #123A63; font-family: '%(font)s'; font-size: 13px; spacing: 8px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid #9CC0E8; background: #FFFFFF;
}
QCheckBox::indicator:checked { background: #3A7BFF; border: 1px solid #3A7BFF; }
QSlider::groove:horizontal { height: 4px; background: #D3E4F8; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #7EB0FF; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 13px; height: 13px; margin: -5px 0; border-radius: 6px; background: #3A7BFF;
}
QSlider::groove:horizontal:disabled { background: #E6EFF9; }
QSlider::sub-page:horizontal:disabled { background: #D6E4F5; }
QSlider::handle:horizontal:disabled { background: #BFD4EC; }
QPushButton {
    background: #FFFFFF; border: 1px solid #B7D3F2; border-radius: 7px;
    color: #1B4A7A; padding: 6px 18px; font-family: '%(font)s'; font-size: 13px;
}
QPushButton:hover { background: #F0F7FF; }
QPushButton:pressed { background: #E2EEFC; }
QPushButton#primary { background: #3A7BFF; border: 1px solid #3A7BFF; color: #FFFFFF; }
QPushButton#primary:hover { background: #4A88FF; }
""" % {"font": theme.FONT_FAMILY}


class HotkeyEdit(QLineEdit):
    """只读输入框，聚焦后按下按键即录制为全局热键。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignCenter)
        self.setPlaceholderText("点击此处，再按下快捷键")
        self.setToolTip("Esc 清除；建议使用 Ctrl / Alt / Shift / Win 组合键")
        self.setMinimumWidth(190)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_unknown):
            return
        if key in (Qt.Key_Escape, Qt.Key_Backspace, Qt.Key_Delete):
            self.clear()
            return
        text = format_hotkey(event.modifiers(), key)
        if text:
            self.setText(text)


class SettingsDialog(QDialog):
    def __init__(self, config, current_width=None, current_height=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("桌面收录 · 设置")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)

        # 快捷键
        hotkey_box = QGroupBox("快捷键")
        hotkey_form = QFormLayout(hotkey_box)
        hotkey_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.hotkey_edit = HotkeyEdit(str(config.get("hotkey")))
        hotkey_form.addRow("显示 / 隐藏面板", self.hotkey_edit)
        hint = QLabel("点击输入框后按下按键即可录制，Esc 清除")
        hint.setObjectName("hint")
        hotkey_form.addRow("", hint)
        layout.addWidget(hotkey_box)

        # 面板
        panel_box = QGroupBox("面板")
        panel_form = QFormLayout(panel_box)
        panel_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.side_combo = QComboBox()
        self.side_combo.addItem("屏幕右侧", "right")
        self.side_combo.addItem("屏幕左侧", "left")
        self.side_combo.setCurrentIndex(0 if config.get("side") != "left" else 1)
        panel_form.addRow("停靠位置", self.side_combo)

        self.title_edit = QLineEdit(str(config.get("panel_title") or "桌面收录"))
        self.title_edit.setMaxLength(16)
        self.title_edit.setPlaceholderText("桌面收录")
        panel_form.addRow("面板标题", self.title_edit)

        self.opacity_slider = self._add_slider(
            panel_form, "透明度", int(float(config.get("opacity")) * 100), 20, 100, "%"
        )
        width = int(config.get("panel_width") or 0)
        auto_width = width <= 0
        screen_width = QApplication.primaryScreen().availableGeometry().width()
        self.width_slider = self._add_slider(
            panel_form, "面板宽度",
            int(current_width or 240) if auto_width else width,
            MIN_WIDTH, max(400, int(screen_width * 0.5)), " px",
        )
        self.width_auto = QCheckBox("宽度自动适应图标数量（推荐）")
        self.width_auto.setChecked(auto_width)
        self.width_slider.setEnabled(not auto_width)
        self.width_auto.toggled.connect(
            lambda checked: self.width_slider.setEnabled(not checked)
        )
        panel_form.addRow("", self.width_auto)

        height = int(config.get("panel_height") or 0)
        auto_height = height <= 0
        screen_height = QApplication.primaryScreen().availableGeometry().height()
        self.height_slider = self._add_slider(
            panel_form, "面板高度",
            int(current_height or 420) if auto_height else height,
            MIN_HEIGHT, max(420, screen_height - 60), " px",
        )
        self.height_auto = QCheckBox("高度自动适应图标数量（推荐）")
        self.height_auto.setChecked(auto_height)
        self.height_slider.setEnabled(not auto_height)
        self.height_auto.toggled.connect(
            lambda checked: self.height_slider.setEnabled(not checked)
        )
        panel_form.addRow("", self.height_auto)

        self.icon_slider = self._add_slider(
            panel_form, "图标大小", int(config.get("icon_size")), 24, 72, " px"
        )
        self.font_slider = self._add_slider(
            panel_form, "文字大小",
            int(config.get("label_font_size") or 9), 7, 18, " pt",
        )
        self.icon_opacity_slider = self._add_slider(
            panel_form, "图标透明度",
            int(float(config.get("icon_opacity", 1.0)) * 100), 20, 100, "%",
        )
        self.label_opacity_slider = self._add_slider(
            panel_form, "文字透明度",
            int(float(config.get("label_opacity", 1.0)) * 100), 20, 100, "%",
        )

        # 文字颜色：内置常用色
        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(8)
        self.label_color_combo = QComboBox()
        for name, value in LABEL_COLORS:
            self.label_color_combo.addItem(name, value)
        current = str(config.get("label_color") or "#000000").lower()
        index = self.label_color_combo.findData(current)
        self.label_color_combo.setCurrentIndex(index if index >= 0 else 0)
        self.label_swatch = QLabel()
        self.label_swatch.setFixedSize(20, 20)
        color_layout.addWidget(self.label_color_combo, 1)
        color_layout.addWidget(self.label_swatch)
        panel_form.addRow("文字颜色", color_row)

        # 背景色：RGB 三原色
        bg = config.get("bg_color")
        if not (isinstance(bg, (list, tuple)) and len(bg) == 3):
            bg = (216, 238, 255)
        self.bg_swatch = QLabel()
        self.bg_swatch.setFixedSize(20, 20)
        self.bg_r = self._add_slider(panel_form, "背景 R", int(bg[0]), 0, 255, "")
        self.bg_g = self._add_slider(panel_form, "背景 G", int(bg[1]), 0, 255, "")
        self.bg_b = self._add_slider(panel_form, "背景 B", int(bg[2]), 0, 255, "")
        bg_row = QWidget()
        bg_layout = QHBoxLayout(bg_row)
        bg_layout.setContentsMargins(0, 0, 0, 0)
        bg_layout.setSpacing(8)
        bg_layout.addWidget(QLabel("预览"))
        bg_layout.addWidget(self.bg_swatch)
        bg_layout.addStretch(1)
        panel_form.addRow("", bg_row)

        for widget in (self.label_color_combo, self.bg_r, self.bg_g, self.bg_b):
            if hasattr(widget, "currentIndexChanged"):
                widget.currentIndexChanged.connect(self._refresh_swatches)
            else:
                widget.valueChanged.connect(self._refresh_swatches)
        self._refresh_swatches()
        drag_hint = QLabel(
            "拖动标题栏可移动位置，松手后自动贴向最近的一侧；"
            "拖动朝向屏幕中央的边可调宽，拖动底边可调高"
        )
        drag_hint.setObjectName("hint")
        drag_hint.setWordWrap(True)
        panel_form.addRow("", drag_hint)
        layout.addWidget(panel_box)

        # 图标
        icon_box = QGroupBox("图标")
        icon_layout = QVBoxLayout(icon_box)
        icon_layout.setSpacing(8)
        self.label_check = QCheckBox("在图标下方显示文字")
        self.label_check.setChecked(bool(config.get("show_label")))
        self.snap_check = QCheckBox("拖动图标时自动对齐到网格")
        self.snap_check.setChecked(bool(config.get("snap_to_grid")))
        self.sort_check = QCheckBox("自动排序（始终按顺序排列，忽略手动摆放）")
        self.sort_check.setChecked(bool(config.get("auto_sort")))
        self.hide_desktop_check = QCheckBox("隐藏系统桌面图标（退出本程序时自动恢复）")
        self.hide_desktop_check.setChecked(bool(config.get("hide_desktop_icons")))
        icon_layout.addWidget(self.label_check)
        icon_layout.addWidget(self.snap_check)
        icon_layout.addWidget(self.sort_check)
        icon_layout.addWidget(self.hide_desktop_check)
        layout.addWidget(icon_box)

        # 行为
        behavior_box = QGroupBox("行为")
        behavior_layout = QVBoxLayout(behavior_box)
        behavior_layout.setSpacing(8)
        self.top_check = QCheckBox("窗口置顶（始终显示在其他窗口上方）")
        self.top_check.setChecked(bool(config.get("always_on_top")))
        self.edge_check = QCheckBox("鼠标移到屏幕边缘时自动滑出")
        self.edge_check.setChecked(bool(config.get("edge_hover")))
        self.fullscreen_check = QCheckBox("前台有全屏或最大化窗口时不要弹出")
        self.fullscreen_check.setChecked(bool(config.get("skip_fullscreen")))
        self.taskbar_check = QCheckBox("任务栏背景透明（不影响图标、时钟与网络状态）")
        self.taskbar_check.setChecked(bool(config.get("taskbar_transparent")))
        self.start_check = QCheckBox("开机自动启动")
        self.start_check.setChecked(bool(config.get("auto_start")))
        behavior_layout.addWidget(self.top_check)
        behavior_layout.addWidget(self.edge_check)
        behavior_layout.addWidget(self.fullscreen_check)
        behavior_layout.addWidget(self.taskbar_check)
        behavior_layout.addWidget(self.start_check)
        layout.addWidget(behavior_box)

        buttons = QDialogButtonBox()
        save_button = QPushButton("保存")
        save_button.setObjectName("primary")
        cancel_button = QPushButton("取消")
        buttons.addButton(save_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(cancel_button, QDialogButtonBox.RejectRole)
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_swatches(self):
        """刷新两个颜色预览小方块。"""
        color = QColor(self.label_color_combo.currentData() or "#000000")
        self.label_swatch.setStyleSheet(
            "background: %s; border: 1px solid #9CC0E8; border-radius: 3px;"
            % color.name()
        )
        bg = QColor(self.bg_r.value(), self.bg_g.value(), self.bg_b.value())
        self.bg_swatch.setStyleSheet(
            "background: %s; border: 1px solid #9CC0E8; border-radius: 3px;"
            % bg.name()
        )

    def _add_slider(self, form, label, value, minimum, maximum, suffix):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(int(value))
        value_label = QLabel()
        value_label.setFixedWidth(46)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value_label.setText("%d%s" % (slider.value(), suffix))
        slider.valueChanged.connect(
            lambda current, lab=value_label, s=suffix: lab.setText("%d%s" % (current, s))
        )
        row_layout.addWidget(slider, 1)
        row_layout.addWidget(value_label)
        form.addRow(label, row)
        return slider

    def values(self):
        return {
            "hotkey": self.hotkey_edit.text().strip(),
            "side": self.side_combo.currentData(),
            "panel_title": self.title_edit.text().strip() or "桌面收录",
            "opacity": self.opacity_slider.value() / 100.0,
            "panel_width": 0 if self.width_auto.isChecked() else self.width_slider.value(),
            "panel_height": 0 if self.height_auto.isChecked() else self.height_slider.value(),
            "icon_size": self.icon_slider.value(),
            "icon_opacity": self.icon_opacity_slider.value() / 100.0,
            "label_opacity": self.label_opacity_slider.value() / 100.0,
            "label_color": self.label_color_combo.currentData(),
            "bg_color": [self.bg_r.value(), self.bg_g.value(), self.bg_b.value()],
            "label_font_size": self.font_slider.value(),
            "show_label": self.label_check.isChecked(),
            "snap_to_grid": self.snap_check.isChecked(),
            "auto_sort": self.sort_check.isChecked(),
            "hide_desktop_icons": self.hide_desktop_check.isChecked(),
            "always_on_top": self.top_check.isChecked(),
            "edge_hover": self.edge_check.isChecked(),
            "skip_fullscreen": self.fullscreen_check.isChecked(),
            "taskbar_transparent": self.taskbar_check.isChecked(),
            "auto_start": self.start_check.isChecked(),
        }

    def accept(self):
        text = self.hotkey_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先录制一个快捷键。")
            return
        if parse_hotkey(text) is None:
            QMessageBox.warning(
                self,
                "提示",
                "「%s」不支持作为全局热键，请换一组按键（例如 Ctrl+Alt+D）。" % text,
            )
            return
        super().accept()