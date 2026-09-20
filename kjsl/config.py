"""配置读写：全部设置以 JSON 形式存放在程序目录的 data/config.json。"""

import json
import os
import sys
import tempfile

if getattr(sys, "frozen", False):
    # 打包成 exe 后，配置放在 exe 同目录，不能放进临时解包目录（会被清掉）
    ROOT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULTS = {
    "hotkey": "Ctrl+Alt+D",   # 全局热键
    "side": "right",          # left / right，面板停靠方向
    "panel_title": "桌面收录",  # 面板顶部显示的名称
    "opacity": 0.55,          # 面板透明度 0.2 ~ 1.0
    "bg_color": [216, 238, 255],   # 面板背景色（RGB 三原色）
    "label_color": "#000000",      # 图标下方文字颜色，默认黑色
    "panel_width": 0,         # 面板宽度（像素），0 表示按图标数量自动计算
    "panel_height": 0,        # 面板高度（像素），0 表示按图标数量自动计算
    "panel_y": None,          # 面板纵向位置（拖动后记录），None 表示垂直居中
    "icon_size": 40,          # 图标尺寸（像素）
    "icon_opacity": 1.0,      # 图标本身的透明度
    "label_opacity": 1.0,     # 图标下方文字的透明度
    "show_label": True,       # 是否在图标下方显示文字
    "label_font_size": 9,     # 图标下方文字的字号（pt）
    "snap_to_grid": False,    # 拖动图标时自动对齐到网格
    "auto_sort": False,       # 自动排序：始终按网格顺序排列，忽略手动摆放
    "always_on_top": True,    # 是否窗口置顶
    "edge_hover": True,       # 是否支持鼠标移到屏幕边缘自动滑出
    "edge_peek": 6,           # 收起时露在屏幕内的宽度（像素）
    "hover_margin": 18,       # 面板四周的隐形检测圈：鼠标离开这一圈才收起
    "skip_fullscreen": False,  # 前台有全屏/最大化窗口时不弹出
    "taskbar_transparent": False,  # 任务栏背景透明（不影响图标与时钟）
    "auto_start": False,      # 开机自启动
    "hide_desktop_icons": False,   # 是否隐藏系统桌面图标
    "desktop_icons_original": False,  # 隐藏前桌面图标的原始状态，退出时还原
    "excluded": [],           # 被移除的图标（归一化路径）
    "custom": [],             # 手动添加的外部路径（不在桌面上）
    "aliases": {},            # 自定义显示名 {归一化路径: 名称}，不改动原文件
    "icons": {},              # 自定义图标 {归一化路径: 图标文件}，不改动原文件
    "positions": {},          # 图标位置 {归一化路径: [x, y]}
}


class Config:
    """极简的字典式配置对象。"""

    def __init__(self, data=None):
        self.data = dict(DEFAULTS)
        if isinstance(data, dict):
            self.data.update(data)

    @classmethod
    def load(cls):
        raw = {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError):
            raw = {}
        return cls(raw)

    def get(self, key, default=None):
        if key in self.data:
            return self.data[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        """先写临时文件再替换，避免写入过程中断电导致配置损坏。"""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(self.data, fp, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CONFIG_PATH)
        except OSError:
            pass