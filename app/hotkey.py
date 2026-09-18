# -*- coding: utf-8 -*-
"""
hotkey.py - 全局热键 + 系统托盘
创建日期：2026-08-12（阶段一创建；阶段七完善系统托盘）

说明：
  - keyboard 库监听全局热键 Ctrl+Shift+P，失败多为未以管理员身份运行/被杀软拦截；
    失败不阻塞主功能，由 main.py 降级提示。
  - pystray 系统托盘：ESC 隐藏后可恢复，托盘菜单含"显示主界面/退出"。
"""
import os
import re
import sys
from typing import Callable

from . import config

# 2026-09-17（需求 S-2）：热键字符串的合法字符集（与 keyboard 库的写法一致，如
#   `ctrl+shift+p` / `alt+f1` / `ctrl+alt+space`）。校验只做"形状"检查，能否真正注册
#   由 keyboard 决定（失败会被调用方降级处理，不影响主功能）。
_HOTKEY_RE = re.compile(r"^[a-z0-9]+(?:\+[a-z0-9]+)*$")


def normalize_hotkey(raw) -> str:
    """规范化热键字符串；非法返回空串（2026-09-17 S-2）。

    - 去空白、转小写、把 ` ` 与 `-` 视作分隔符统一改为 `+`；
    - 只允许 `小写字母/数字` 与 `+`，且至少两段（必须含修饰键，如 `ctrl+p`）。
    """
    s = str(raw or "").strip().lower().replace(" ", "").replace("-", "+")
    s = re.sub(r"\++", "+", s).strip("+")
    if not s or "+" not in s or not _HOTKEY_RE.match(s):
        return ""
    return s


def current_hotkey(db) -> str:
    """取"当前生效的热键"：读 meta → 规范化；无值 / 非法 → 回退 `config.GLOBAL_HOTKEY`。"""
    try:
        return normalize_hotkey(db.get_meta(config.META_HOTKEY)) or config.GLOBAL_HOTKEY
    except Exception:
        return config.GLOBAL_HOTKEY


def register_global_hotkey(callback: Callable[[], None], combo: str = "") -> bool:
    """注册全局热键；`combo` 缺省用 `config.GLOBAL_HOTKEY`。返回是否成功。

    2026-09-17（S-2）：新增 `combo` 参数 —— 供"设置里改热键后立即生效"使用。
    """
    _combo = normalize_hotkey(combo) or config.GLOBAL_HOTKEY
    try:
        import keyboard
        keyboard.add_hotkey(_combo, callback)
        return True
    except Exception:
        return False


def unregister_all() -> None:
    """释放全部已注册热键"""
    try:
        import keyboard
        keyboard.unhook_all()
    except Exception:
        pass


# ---------------------------------------------------------------------- #
# 系统托盘
# ---------------------------------------------------------------------- #
def _icon_path() -> str:
    """源图标路径：打包态从解压目录读取，开发态从项目 Icons/ 读取"""
    if config.is_frozen():
        return os.path.join(sys._MEIPASS, "Icons", "PSicon.png")
    return os.path.join(config.PROJECT_ROOT, "Icons", "PSicon.png")


def _tray_image():
    """托盘图标：优先使用源图标 Icons/PSicon.png，加载失败则回退程序绘制"""
    from PIL import Image
    try:
        path = _icon_path()
        if os.path.isfile(path):
            img = Image.open(path).convert("RGBA")
            img.thumbnail((64, 64))
            return img
    except Exception:
        pass
    # 回退：绘制绿色圆角方块 + P 字母
    from PIL import ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill="#2E8B57")
    d.text((24, 18), "P", fill="white")
    return img


def start_tray(on_show: Callable[[], None], on_quit: Callable[[], None]):
    """启动系统托盘图标（独立线程运行）；返回图标对象供 stop_tray 使用"""
    import pystray
    menu = pystray.Menu(
        pystray.MenuItem("显示主界面", lambda _icon, _item: on_show()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", lambda _icon, _item: on_quit()),
    )
    icon = pystray.Icon("PromptSprite", _tray_image(), "PromptSprite 提示精灵", menu)
    icon.run_detached()
    return icon


def stop_tray(icon) -> None:
    try:
        if icon is not None:
            icon.stop()
    except Exception:
        pass
