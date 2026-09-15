# -*- coding: utf-8 -*-
"""ui_appearance.py - 界面外观（字体/字号/颜色）配置的**数据层**

创建日期：2026-09-15（用户要求"批次 4 · 外观设置"，本文件＝**第 1 步：只做数据层**）

职责（单一、可独立自测）：
  1. 定义"可外观化的 **6 个界面元素**"及其**允许的调节项**（字体族 / 字号 / 文字色 / 背景色）；
  2. `load(db)` / `save(db, data)`：读写 meta 键 `ui_appearance`（JSON 对象字符串）；
  3. `normalize(data)`：**严格校验**——未知组、未知项、越界字号、非法颜色一律丢弃（置 None）；
  4. `font(...)` / `color(...)`：把用户设置**叠加**到各元素原有的"基础字体 / 颜色"上。

设计要点（为什么这样最简、最安全）：
  - 所有取值用 `None` 表示"**不改**"（沿用代码里的原值）⇒ 本模块**不会改变任何默认外观**；
  - 只暴露 6 组 × 2~4 项，字号上限按元素类型分别限制（见 `GROUPS`），避免把界面撑坏；
  - 本模块**不 import** 任何 UI 模块，也不含 UI 逻辑；UI 侧只通过
    `load / save / font / color` 4 个纯函数与它交互 ⇒ **功能与代码隔离**（便于逐项施工与回滚）。

自测：`python -m app.ui_appearance`
"""
import json
import re

from . import config

META_KEY = config.META_UI_APPEARANCE

MIN_SIZE = 8                      # 字号下限（源码字号，与 CTk 的 font size 同口径）
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# 6 个可外观化的界面元素：组名 → {显示名, 允许项, 字号上限}
#   字号上限依据（2026-09-15 施工前实测/评估）：
#     · entry_rows（条目区名称）：20 —— 再大则条目区（列宽固定 ≈16 汉字）截断过多；
#     · name_field（①名称框）：22 —— 输入框高度会随字号自适应，但过大仍会挤占详情区首屏；
#     · 其余（浮层/浮窗/提示词框）：28 —— 均为可滚动的文本区，字号大只会更高/更少字。
GROUPS = {
    "entry_ov":   {"label": "条目名称浮层",     "keys": ("family", "size", "fg", "bg"),
                   "size_max": 28},
    "entry_rows": {"label": "条目区名称",       "keys": ("family", "size"),
                   "size_max": 20},
    "field_tip":  {"label": "字段浮动提示窗",   "keys": ("family", "size", "fg", "bg"),
                   "size_max": 28},
    "prompt_cn":  {"label": "中文版提示词",     "keys": ("family", "size", "fg"),
                   "size_max": 28},
    "prompt_en":  {"label": "英文版提示词",     "keys": ("family", "size", "fg"),
                   "size_max": 28},
    "name_field": {"label": "①条目名称框",      "keys": ("family", "size"),
                   "size_max": 22},
}

COLOR_KEYS = ("fg", "bg")         # 颜色项（值为 "#RRGGBB" 或 None）


def empty() -> dict:
    """返回"全部未设置"的空配置（所有项为 None）——即"与现有界面完全一致"。"""
    return {g: {k: None for k in spec["keys"]} for g, spec in GROUPS.items()}


def normalize(data) -> dict:
    """严格校验并归一化：只保留已知组 / 已知项；非法值一律丢弃（置 None）。"""
    out = empty()
    if not isinstance(data, dict):
        return out
    for g, spec in GROUPS.items():
        src = data.get(g)
        if not isinstance(src, dict):
            continue
        for k in spec["keys"]:
            v = src.get(k)
            if v is None or v == "":
                continue
            if k == "family":
                if isinstance(v, str) and v.strip():
                    out[g][k] = v.strip()
            elif k == "size":
                try:
                    n = int(v)
                except Exception:
                    continue
                if MIN_SIZE <= n <= int(spec["size_max"]):
                    out[g][k] = n
            elif k in COLOR_KEYS:
                if isinstance(v, str) and _COLOR_RE.match(v.strip()):
                    out[g][k] = v.strip().lower()
    return out


def load(db) -> dict:
    """从 meta 读取（缺失 / 非 JSON / 结构非法 → 返回空配置，不抛异常）。"""
    try:
        raw = db.get_meta(META_KEY)
    except Exception:
        return empty()
    if not raw:
        return empty()
    try:
        return normalize(json.loads(raw))
    except Exception:
        return empty()


def save(db, data) -> bool:
    """校验后写回 meta（成功 True；失败 False，不抛异常）。"""
    try:
        db.set_meta(META_KEY, json.dumps(normalize(data), ensure_ascii=False))
        return True
    except Exception:
        return False


def is_default(data) -> bool:
    """是否"用户什么都没设置"（全 None）——设置页用它显示"恢复默认"状态。"""
    return normalize(data) == empty()


def font(group: str, base, data) -> tuple:
    """把设置**逐项叠加**到基础字体上并返回新字体元组。

    `base` 形如 ("Microsoft YaHei", 13, "bold")；未设置 family/size 的项**沿用 base**
    ⇒ 用户没设置时返回的字体与 base 完全相同（零差异）。
    """
    if not isinstance(base, (tuple, list)) or len(base) < 2:
        return base
    fam, size = base[0], base[1]
    bold = base[2] if len(base) > 2 else "normal"
    g = (data or {}).get(group) or {}
    if g.get("family"):
        fam = g["family"]
    if g.get("size"):
        try:
            size = int(g["size"])
        except Exception:
            pass
    return (fam, size, bold)


def color(group: str, data, which: str, default):
    """取某组某颜色项：已设置 → 用户值；未设置 → default（原值）。"""
    g = (data or {}).get(group) or {}
    v = g.get(which)
    return v if v else default


def size(group: str, data):
    """取某组字号：已设置 → 用户字号；未设置 → None（调用方沿用基础字号）。"""
    g = (data or {}).get(group) or {}
    try:
        v = g.get("size")
        return int(v) if v else None
    except Exception:
        return None


# ------------------------------------------------------------------ #
# 自测：python -m app.ui_appearance
# ------------------------------------------------------------------ #
def _selftest() -> None:
    import os
    import shutil
    import tempfile

    from .database import Database

    # 1) 空配置与默认判定
    e = empty()
    assert set(e.keys()) == set(GROUPS.keys()), "组数不符"
    assert is_default(e) and is_default({}) and is_default(None), "空配置应判定为默认"
    print("[1] empty()/is_default() 通过")

    # 2) 校验：合法值全部保留
    good = {
        "entry_ov": {"family": "SimSun", "size": 18, "fg": "#112233", "bg": "#FFFFFF"},
        "entry_rows": {"size": 16},
        "field_tip": {"size": 20, "fg": "#000000"},
        "prompt_cn": {"family": "KaiTi", "fg": "#AB12CD"},
        "prompt_en": {"size": 21},
        "name_field": {"size": 22},
    }
    n = normalize(good)
    assert n["entry_ov"] == {"family": "SimSun", "size": 18, "fg": "#112233", "bg": "#ffffff"}, n["entry_ov"]
    assert n["entry_rows"]["size"] == 16 and n["name_field"]["size"] == 22, n
    print("[2] normalize 保留合法值（颜色统一小写）通过")

    # 3) 校验：非法值被丢弃（越界字号 / 非法颜色 / 未知组 / 未知项）
    bad = {
        "entry_ov": {"size": 3, "fg": "red", "bg": "#GGGGGG", "xx": "1"},     # 全非法 + 未知项
        "entry_rows": {"size": 21},          # 超过该组上限 20
        "field_tip": {"size": 99},           # 超过上限 28
        "prompt_cn": {"family": "   "},      # 空白字体名
        "unknown_group": {"size": 30},       # 未知组
    }
    nb = normalize(bad)
    assert is_default(nb), "非法值应全部被丢弃：%s" % nb
    print("[3] normalize 丢弃非法值（越界/非法颜色/未知组/未知项）通过")

    # 4) 字体叠加：未设置 → 与 base 完全一致；设置 → 逐项覆盖且保留 bold
    base = ("Microsoft YaHei", 13, "bold")
    assert font("entry_rows", base, empty()) == base, "未设置时字体必须与 base 一致"
    assert font("entry_rows", base, n) == ("Microsoft YaHei", 16, "bold"), font("entry_rows", base, n)
    assert font("prompt_cn", base, n) == ("KaiTi", 13, "bold"), font("prompt_cn", base, n)
    print("[4] font() 叠加（未设置=原值、设置=覆盖、bold 保留）通过")

    # 5) 颜色与字号取值
    assert color("entry_ov", empty(), "fg", "#111111") == "#111111"
    assert color("entry_ov", n, "fg", "#111111") == "#112233"
    assert color("entry_ov", n, "bg", "#ffffff") == "#ffffff"
    assert size("entry_rows", empty()) is None and size("entry_rows", n) == 16
    print("[5] color()/size() 通过")

    # 6) 读写往返（真实 Database；含"meta 值损坏"的容错）
    tmp = tempfile.mkdtemp(prefix="ps_appear_")
    try:
        db = Database(os.path.join(tmp, "t.db"))
        assert load(db).keys() == GROUPS.keys(), "首次读取应为空配置"
        assert save(db, good), "保存应成功"
        got = load(db)
        assert got["entry_ov"]["size"] == 18 and got["prompt_cn"]["family"] == "KaiTi", got
        db.set_meta(META_KEY, "{ 这不是 JSON")          # 损坏数据 → 不得抛异常
        assert is_default(load(db)), "损坏的 meta 应回退为空配置"
        assert save(db, bad), "即使全非法也应能保存（归一化后为空）"
        assert is_default(load(db)), "归一化后应写回空配置"
        db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("[6] load/save 往返 + 损坏 meta 容错 通过")

    print("=== UI 外观数据层自测通过 ===")


if __name__ == "__main__":
    _selftest()
