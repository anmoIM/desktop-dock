"""配置读写：全部设置以 JSON 形式存放在程序目录的 data/config.json。"""

import json
import os
import sys
import tempfile
import uuid

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
    "hide_shortcut_arrow": False,  # 隐藏快捷方式图标左下角的小箭头角标
    "drag_trail": True,       # 拖动图标时是否显示拖尾特效
    "drag_trail_length": 5,   # 拖尾长度档位 1~10，越大尾巴越长
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
    "zones": [],              # 收纳区：每个区是一个独立面板
    "zone_of": {},            # 图标归属 {归一化路径: 收纳区 id}，没记录的归第一个区
}

# 这些设置在收纳区里各自独立，其余设置所有区共用
ZONE_KEYS = (
    "side", "panel_width", "panel_height", "panel_y", "hotkey", "collapsed",
    "opacity", "bg_color", "icon_size", "icon_opacity", "label_opacity",
    "label_color", "label_font_size", "show_label", "snap_to_grid",
    "auto_sort", "drag_trail", "drag_trail_length",
)


def new_zone(name="收纳区", side="right", hotkey="", **extra):
    """新建一个收纳区。"""
    zone = {
        "id": uuid.uuid4().hex[:8],
        "name": str(name or "收纳区"),
        "side": side,
        "hotkey": hotkey,
        "panel_width": 0,
        "panel_height": 0,
        "panel_y": None,
        "collapsed": False,
    }
    zone.update(extra)
    return zone


class Config:
    """极简的字典式配置对象。"""

    def __init__(self, data=None):
        self.data = dict(DEFAULTS)
        if isinstance(data, dict):
            self.data.update(data)
        self._ensure_zones()

    def _ensure_zones(self):
        """保证至少有一个收纳区；旧配置的单面板设置迁移成第一个区。"""
        zones = self.data.get("zones")
        if isinstance(zones, list) and zones:
            for zone in zones:
                if not isinstance(zone, dict):
                    continue
                zone.setdefault("id", uuid.uuid4().hex[:8])
                zone.setdefault("name", "收纳区")
                zone.setdefault("side", "right")
                zone.setdefault("hotkey", "")
                zone.setdefault("panel_width", 0)
                zone.setdefault("panel_height", 0)
                zone.setdefault("panel_y", None)
                zone.setdefault("collapsed", False)
            self.data["zones"] = [zone for zone in zones if isinstance(zone, dict)]
            return
        self.data["zones"] = [
            new_zone(
                name=str(self.data.get("panel_title") or "桌面收录"),
                side=str(self.data.get("side") or "right"),
                hotkey=str(self.data.get("hotkey") or ""),
                panel_width=int(self.data.get("panel_width") or 0),
                panel_height=int(self.data.get("panel_height") or 0),
                panel_y=self.data.get("panel_y"),
            )
        ]

    def zones(self):
        return self.data.get("zones") or []

    def first_zone_id(self):
        zones = self.zones()
        return zones[0]["id"] if zones else ""

    def zone(self, zone_id):
        for zone in self.zones():
            if zone.get("id") == zone_id:
                return zone
        return None

    def zone_of(self, key):
        """图标属于哪个收纳区，没记录过就归第一个区。"""
        mapping = self.data.get("zone_of")
        if isinstance(mapping, dict):
            zone_id = mapping.get(key)
            if zone_id and self.zone(zone_id):
                return zone_id
        return self.first_zone_id()

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
            # 临时文件放在目标同目录，替换才是原子的（跨盘替换会失败）
            target_dir = os.path.dirname(CONFIG_PATH) or DATA_DIR
            os.makedirs(target_dir, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(self.data, fp, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CONFIG_PATH)
        except OSError:
            pass


class ZoneConfig:
    """把某个收纳区自己的设置叠加在全局设置上，面板代码照旧用 get / set 即可。"""

    def __init__(self, config, zone):
        self._config = config
        self.zone = zone

    @property
    def raw(self):
        return self._config

    def get(self, key, default=None):
        if key == "panel_title":
            return self.zone.get("name") or default
        if key in ZONE_KEYS:
            value = self.zone.get(key)
            if value is None:
                # 区里没设过（老配置升级上来的）就沿用全局值
                value = self._config.get(key)
            return default if value is None else value
        return self._config.get(key, default)

    def set(self, key, value):
        if key == "panel_title":
            self.zone["name"] = value
        elif key in ZONE_KEYS:
            self.zone[key] = value
        else:
            self._config.set(key, value)

    def save(self):
        self._config.save()

    def __getattr__(self, name):
        return getattr(self._config, name)