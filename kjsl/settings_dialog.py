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
    QFrame,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .config import new_zone
from .hotkey import format_hotkey, parse_hotkey

MIN_WIDTH = 150
MIN_HEIGHT = 220

# 新建收纳区时一并复制过去的外观 / 图标行为项
ZONE_APPEARANCE_KEYS = (
    "opacity", "bg_color", "icon_size", "label_font_size", "icon_opacity",
    "label_opacity", "label_color", "show_label", "snap_to_grid", "auto_sort",
    "drag_trail", "drag_trail_length",
)

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
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QTabWidget::pane {
    border: 1px solid #C4DCF6; border-radius: 8px; background: #F5FAFF; top: -1px;
}
QTabBar::tab {
    background: #E3EFFC; color: #2C5C8F; padding: 6px 18px; margin-right: 4px;
    border: 1px solid #C4DCF6; border-bottom: none;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
    font-family: '%(font)s'; font-size: 13px;
}
QTabBar::tab:selected { background: #F5FAFF; color: #12314F; font-weight: 600; }
QTabBar::tab:hover { background: #EFF6FF; }
QScrollBar:vertical { background: #E3F0FF; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #A8CBEF; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
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
    def __init__(self, config, sizes=None, parent=None):
        super().__init__(parent)
        self.config = config
        # 收纳区用工作副本，点「取消」不会改动已有配置
        self.zones = [dict(zone) for zone in config.zones()]
        self.sizes = sizes or {}
        self._zone_index = 0
        self._page_contents = []
        self.setWindowTitle("桌面收录 · 设置")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # 两类设置分开放：本区设置只作用于选中的收纳区，全局设置所有区共用
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_zone_page(), "本区设置")
        self.tabs.addTab(self._build_global_page(), "全局设置")
        outer.addWidget(self.tabs, 1)

        self._reload_zone_combo()

        buttons = QDialogButtonBox()
        save_button = QPushButton("保存")
        save_button.setObjectName("primary")
        cancel_button = QPushButton("取消")
        buttons.addButton(save_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(cancel_button, QDialogButtonBox.RejectRole)
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(16, 8, 16, 12)
        button_layout.addStretch(1)
        button_layout.addWidget(buttons)
        outer.addWidget(button_row)

        # 屏幕放不下时限制高度，靠滚动区浏览
        screen = QApplication.primaryScreen().availableGeometry()
        self.setMaximumHeight(max(520, screen.height() - 80))
        wanted = max(
            inner.sizeHint().height() for inner in self._page_contents
        ) + self.tabs.tabBar().sizeHint().height() + button_row.sizeHint().height()
        self.resize(
            max(440, self.sizeHint().width()), min(wanted, self.maximumHeight())
        )

    def _make_page(self):
        """一个带滚动区的选项卡页，返回 (页面, 内容布局)。"""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        scroll.setWidget(inner)
        page_layout.addWidget(scroll)
        content = QVBoxLayout(inner)
        content.setContentsMargins(16, 12, 16, 14)
        content.setSpacing(10)
        self._page_contents.append(inner)     # 用来算对话框该开多高
        return page, content

    def _build_zone_page(self):
        """本区设置：只作用于上面选中的那个收纳区，初始值由 _load_zone_fields 填。"""
        page, layout = self._make_page()

        zone_box = QGroupBox("收纳区")
        zone_form = QFormLayout(zone_box)
        zone_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        picker = QWidget()
        picker_layout = QHBoxLayout(picker)
        picker_layout.setContentsMargins(0, 0, 0, 0)
        picker_layout.setSpacing(6)
        self.zone_combo = QComboBox()
        self.zone_combo.currentIndexChanged.connect(self._on_zone_changed)
        self.btn_new_zone = QPushButton("新建")
        self.btn_new_zone.clicked.connect(self._new_zone)
        self.btn_rename_zone = QPushButton("重命名")
        self.btn_rename_zone.clicked.connect(self._rename_zone)
        self.btn_delete_zone = QPushButton("删除")
        self.btn_delete_zone.clicked.connect(self._delete_zone)
        picker_layout.addWidget(self.zone_combo, 1)
        for button in (self.btn_new_zone, self.btn_rename_zone, self.btn_delete_zone):
            picker_layout.addWidget(button)
        zone_form.addRow("编辑哪个区", picker)

        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(16)
        self.title_edit.setPlaceholderText("收纳区名称")
        zone_form.addRow("标题", self.title_edit)

        self.side_combo = QComboBox()
        self.side_combo.addItem("屏幕右侧", "right")
        self.side_combo.addItem("屏幕左侧", "left")
        zone_form.addRow("停靠位置", self.side_combo)

        self.hotkey_edit = HotkeyEdit("")
        zone_form.addRow("呼出热键", self.hotkey_edit)
        hint = QLabel("点击输入框后按下按键即可录制，Esc 清除；留空表示不设热键")
        hint.setObjectName("hint")
        zone_form.addRow("", hint)

        screen_width = QApplication.primaryScreen().availableGeometry().width()
        screen_height = QApplication.primaryScreen().availableGeometry().height()
        self.width_slider = self._add_slider(
            zone_form, "面板宽度", 240, MIN_WIDTH, max(400, int(screen_width * 0.5)), " px"
        )
        self.width_auto = QCheckBox("宽度自动适应图标数量（推荐）")
        self.width_auto.toggled.connect(
            lambda checked: self.width_slider.setEnabled(not checked)
        )
        zone_form.addRow("", self.width_auto)

        self.height_slider = self._add_slider(
            zone_form, "面板高度", 420, MIN_HEIGHT, max(420, screen_height - 60), " px"
        )
        self.height_auto = QCheckBox("高度自动适应图标数量（推荐）")
        self.height_auto.toggled.connect(
            lambda checked: self.height_slider.setEnabled(not checked)
        )
        zone_form.addRow("", self.height_auto)

        drag_hint = QLabel(
            "拖动标题栏可移动位置，松手后自动贴向最近的一侧；"
            "拖动朝向屏幕中央的边可调宽，拖动底边可调高；"
            "标题栏的 ▾ 可折叠该区"
        )
        drag_hint.setObjectName("hint")
        drag_hint.setWordWrap(True)
        zone_form.addRow("", drag_hint)
        layout.addWidget(zone_box)

        panel_box = QGroupBox("外观")
        panel_form = QFormLayout(panel_box)
        panel_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.opacity_slider = self._add_slider(panel_form, "透明度", 55, 20, 100, "%")
        self.icon_slider = self._add_slider(panel_form, "图标大小", 40, 24, 72, " px")
        self.font_slider = self._add_slider(panel_form, "文字大小", 9, 7, 18, " pt")
        self.icon_opacity_slider = self._add_slider(
            panel_form, "图标透明度", 100, 20, 100, "%"
        )
        self.label_opacity_slider = self._add_slider(
            panel_form, "文字透明度", 100, 20, 100, "%"
        )

        # 文字颜色：内置常用色
        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(8)
        self.label_color_combo = QComboBox()
        for name, value in LABEL_COLORS:
            self.label_color_combo.addItem(name, value)
        self.label_swatch = QLabel()
        self.label_swatch.setFixedSize(20, 20)
        color_layout.addWidget(self.label_color_combo, 1)
        color_layout.addWidget(self.label_swatch)
        panel_form.addRow("文字颜色", color_row)

        # 背景色：RGB 三原色
        self.bg_swatch = QLabel()
        self.bg_swatch.setFixedSize(20, 20)
        self.bg_r = self._add_slider(panel_form, "背景 R", 216, 0, 255, "")
        self.bg_g = self._add_slider(panel_form, "背景 G", 238, 0, 255, "")
        self.bg_b = self._add_slider(panel_form, "背景 B", 255, 0, 255, "")
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
        layout.addWidget(panel_box)

        icon_box = QGroupBox("图标")
        icon_layout = QVBoxLayout(icon_box)
        icon_layout.setSpacing(8)
        self.label_check = QCheckBox("在图标下方显示文字")
        self.snap_check = QCheckBox("拖动图标时自动对齐到网格")
        self.sort_check = QCheckBox("自动排序（始终按顺序排列，忽略手动摆放）")
        self.trail_check = QCheckBox("拖动图标时显示拖尾特效")
        icon_layout.addWidget(self.label_check)
        icon_layout.addWidget(self.snap_check)
        icon_layout.addWidget(self.sort_check)
        icon_layout.addWidget(self.trail_check)
        trail_form = QFormLayout()
        trail_form.setContentsMargins(0, 0, 0, 0)
        trail_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.trail_slider = self._add_slider(trail_form, "拖尾长度", 5, 1, 10, " 档")
        self.trail_check.toggled.connect(self.trail_slider.setEnabled)
        icon_layout.addLayout(trail_form)
        layout.addWidget(icon_box)

        layout.addStretch(1)
        return page

    def _build_global_page(self):
        """全局设置：所有收纳区共用。"""
        page, layout = self._make_page()
        config = self.config

        scope_hint = QLabel("以下设置对所有收纳区一起生效")
        scope_hint.setObjectName("hint")
        layout.addWidget(scope_hint)

        icon_box = QGroupBox("图标与桌面")
        icon_layout = QVBoxLayout(icon_box)
        icon_layout.setSpacing(8)
        self.arrow_check = QCheckBox("隐藏快捷方式图标左下角的小箭头角标")
        self.arrow_check.setChecked(bool(config.get("hide_shortcut_arrow")))
        self.hide_desktop_check = QCheckBox("隐藏系统桌面图标（退出本程序时自动恢复）")
        self.hide_desktop_check.setChecked(bool(config.get("hide_desktop_icons")))
        icon_layout.addWidget(self.arrow_check)
        icon_layout.addWidget(self.hide_desktop_check)
        layout.addWidget(icon_box)

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

        layout.addStretch(1)
        return page

    # ---------- 收纳区 ----------

    def _reload_zone_combo(self, select=None):
        index = self._zone_index if select is None else select
        self.zone_combo.blockSignals(True)
        self.zone_combo.clear()
        for zone in self.zones:
            self.zone_combo.addItem(str(zone.get("name") or "收纳区"), zone.get("id"))
        self.zone_combo.setCurrentIndex(max(0, min(index, len(self.zones) - 1)))
        self.zone_combo.blockSignals(False)
        self._zone_index = self.zone_combo.currentIndex()
        self._load_zone_fields()

    def _zone(self):
        index = self.zone_combo.currentIndex()
        if 0 <= index < len(self.zones):
            return self.zones[index]
        return self.zones[0] if self.zones else {}

    def _load_zone_fields(self):
        """把当前收纳区的设置填进控件。"""
        zone = self._zone()
        if not zone:
            return
        self.title_edit.setText(str(zone.get("name") or ""))
        self.side_combo.setCurrentIndex(0 if zone.get("side") != "left" else 1)
        self.hotkey_edit.setText(str(zone.get("hotkey") or ""))
        width = int(zone.get("panel_width") or 0)
        auto_width = width <= 0
        current_width = (self.sizes.get(zone.get("id")) or (0, 0))[0]
        self.width_slider.setValue(int(current_width or 240) if auto_width else width)
        self.width_auto.setChecked(auto_width)
        self.width_slider.setEnabled(not auto_width)
        height = int(zone.get("panel_height") or 0)
        auto_height = height <= 0
        current_height = (self.sizes.get(zone.get("id")) or (0, 0))[1]
        self.height_slider.setValue(int(current_height or 420) if auto_height else height)
        self.height_auto.setChecked(auto_height)
        self.height_slider.setEnabled(not auto_height)
        self.btn_delete_zone.setEnabled(len(self.zones) > 1)
        # 外观
        self.opacity_slider.setValue(int(float(self._zone_value(zone, "opacity", 0.55)) * 100))
        self.icon_slider.setValue(int(self._zone_value(zone, "icon_size", 40)))
        self.font_slider.setValue(int(self._zone_value(zone, "label_font_size", 9)))
        self.icon_opacity_slider.setValue(
            int(float(self._zone_value(zone, "icon_opacity", 1.0)) * 100)
        )
        self.label_opacity_slider.setValue(
            int(float(self._zone_value(zone, "label_opacity", 1.0)) * 100)
        )
        color = str(self._zone_value(zone, "label_color", "#000000") or "#000000").lower()
        index = self.label_color_combo.findData(color)
        self.label_color_combo.setCurrentIndex(index if index >= 0 else 0)
        bg = self._zone_value(zone, "bg_color", [216, 238, 255])
        if not (isinstance(bg, (list, tuple)) and len(bg) == 3):
            bg = (216, 238, 255)
        self.bg_r.setValue(int(bg[0]))
        self.bg_g.setValue(int(bg[1]))
        self.bg_b.setValue(int(bg[2]))
        self._refresh_swatches()
        # 图标行为
        self.label_check.setChecked(bool(self._zone_value(zone, "show_label", True)))
        self.snap_check.setChecked(bool(self._zone_value(zone, "snap_to_grid", False)))
        self.sort_check.setChecked(bool(self._zone_value(zone, "auto_sort", False)))
        trail = bool(self._zone_value(zone, "drag_trail", True))
        self.trail_check.setChecked(trail)
        self.trail_slider.setValue(int(self._zone_value(zone, "drag_trail_length", 5)))
        self.trail_slider.setEnabled(trail)
        self._sync_slider_labels()

    def _zone_value(self, zone, key, default=None):
        """区里没设过就用全局配置里的值（老配置升级上来的情况）。"""
        value = zone.get(key) if isinstance(zone, dict) else None
        if value is None:
            value = self.config.get(key)
        return default if value is None else value

    def _store_zone_fields(self, zone=None):
        """把控件上的值写回指定收纳区（默认当前选中的区）。"""
        zone = self._zone() if zone is None else zone
        if not zone:
            return
        zone["name"] = self.title_edit.text().strip() or "收纳区"
        zone["side"] = self.side_combo.currentData()
        zone["hotkey"] = self.hotkey_edit.text().strip()
        zone["panel_width"] = 0 if self.width_auto.isChecked() else self.width_slider.value()
        zone["panel_height"] = 0 if self.height_auto.isChecked() else self.height_slider.value()
        zone["opacity"] = self.opacity_slider.value() / 100.0
        zone["icon_size"] = self.icon_slider.value()
        zone["label_font_size"] = self.font_slider.value()
        zone["icon_opacity"] = self.icon_opacity_slider.value() / 100.0
        zone["label_opacity"] = self.label_opacity_slider.value() / 100.0
        zone["label_color"] = self.label_color_combo.currentData()
        zone["bg_color"] = [self.bg_r.value(), self.bg_g.value(), self.bg_b.value()]
        zone["show_label"] = self.label_check.isChecked()
        zone["snap_to_grid"] = self.snap_check.isChecked()
        zone["auto_sort"] = self.sort_check.isChecked()
        zone["drag_trail"] = self.trail_check.isChecked()
        zone["drag_trail_length"] = self.trail_slider.value()

    def _on_zone_changed(self, index):
        previous = self._zone_index
        if 0 <= previous < len(self.zones) and previous != index:
            self._store_zone_fields(self.zones[previous])
        self._zone_index = index
        self._load_zone_fields()

    def _new_zone(self):
        self._store_zone_fields()
        name, ok = QInputDialog.getText(self, "新建收纳区", "名称", text="新收纳区")
        if not ok or not name.strip():
            return
        zone = new_zone(name.strip(), side="left", hotkey="")
        # 新区先照抄当前这套外观与图标行为，省得再调一遍
        for key in ZONE_APPEARANCE_KEYS:
            value = self._zone().get(key)
            zone[key] = list(value) if isinstance(value, list) else value
        self.zones.append(zone)
        self._reload_zone_combo(len(self.zones) - 1)

    def _rename_zone(self):
        self._store_zone_fields()
        zone = self._zone()
        if not zone:
            return
        name, ok = QInputDialog.getText(
            self, "重命名收纳区", "名称", text=str(zone.get("name") or "")
        )
        if not ok or not name.strip():
            return
        zone["name"] = name.strip()
        self._reload_zone_combo()

    def _delete_zone(self):
        zone = self._zone()
        if not zone or len(self.zones) <= 1:
            return
        answer = QMessageBox.question(
            self,
            "删除收纳区",
            "删除「%s」后，区里的图标会回到第一个收纳区，确认删除？"
            % zone.get("name"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        index = self.zone_combo.currentIndex()
        self.zones.pop(index)
        self._reload_zone_combo(max(0, index - 1))

    # ---------- 取值 ----------

    def values(self):
        """按区设置全在 zones 里，这里只返回全局设置。"""
        self._store_zone_fields()
        return {
            "zones": self.zones,
            "hide_shortcut_arrow": self.arrow_check.isChecked(),
            "hide_desktop_icons": self.hide_desktop_check.isChecked(),
            "always_on_top": self.top_check.isChecked(),
            "edge_hover": self.edge_check.isChecked(),
            "skip_fullscreen": self.fullscreen_check.isChecked(),
            "taskbar_transparent": self.taskbar_check.isChecked(),
            "auto_start": self.start_check.isChecked(),
        }

    def accept(self):
        self._store_zone_fields()
        for zone in self.zones:
            text = str(zone.get("hotkey") or "").strip()
            if text and parse_hotkey(text) is None:
                QMessageBox.warning(
                    self,
                    "提示",
                    "「%s」不支持作为全局热键，请换一组按键（例如 Ctrl+Alt+D）。" % text,
                )
                return
        super().accept()

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
        slider._value_label = value_label      # 切换区时用来刷新数字
        slider._suffix = suffix
        row_layout.addWidget(slider, 1)
        row_layout.addWidget(value_label)
        form.addRow(label, row)
        return slider

    def _sync_slider_labels(self):
        """滑块被程序改值时不会发信号，这里手动把数字刷一遍。"""
        for slider in self.findChildren(QSlider):
            label = getattr(slider, "_value_label", None)
            if label is not None:
                label.setText(
                    "%d%s" % (slider.value(), getattr(slider, "_suffix", ""))
                )

    