# -*- coding: utf-8 -*-
"""
ui_common.py - 主窗口 / 快速新建等共用 UI 小工具（2026-09-09 P2-12 抽取）

消除 main_window 与 quick_add 之间重复的：
  - 辅助按钮配色常量（浅色底 + 深色字）
  - 导航选中态配色常量
  - CTkTextbox "行数→像素" 换算（1 行 ≈ 20px，见 main_window 折叠字段注释）
  - 文本框撤销/重做与"只读键盘防护"（2026-09-09 新增，供详情区与快速新建表单共用）
"""
import tkinter as tk

# "新增/返回"辅助按钮样式：浅色底 + 深色字，保证标签文字清晰可读
ADD_BTN_STYLE = dict(fg_color="#e8ecf1", hover_color="#d5dce5", text_color="#1f2937")

# 导航选中态：比默认按钮颜色稍稍加深，便于识别选中的 根目录→一级→二级 链路
SEL_BTN_STYLE = dict(fg_color="#25639c", hover_color="#1d4f7c", text_color="white")


def rows_to_px(rows) -> int:
    """行数 → 像素（CTkTextbox.height 单位是像素；1 行 ≈ 20px）。

    详情/新增表单字段在 _FIELDS 里以"行数"描述，统一换算避免文本框过矮。
    """
    try:
        n = max(int(rows), 1)
    except (TypeError, ValueError):
        n = 1
    return n * 20


# --------------------------------------------------------------------------- #
# 文本框编辑能力：撤销(Ctrl+Z)/重做(Ctrl+Y) + "浏览只读"键盘防护
# --------------------------------------------------------------------------- #
# 浏览只读时仍允许的导航类按键（允许光标移动 / 复制等，仅屏蔽会改动内容的键）
# 注意：Tab 不放入白名单——tk.Text 默认会把 Tab 当制表符插入，浏览态必须屏蔽
_READONLY_NAV_KEYS = frozenset({
    "Left", "Right", "Up", "Down", "Home", "End",
    "Prior", "Next", "Escape", "Shift_L", "Shift_R",
    "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock",
})


def _internal_of(widget):
    """取 CTkTextbox/CTkEntry（或原生 tk.Text/tk.Entry）的真实输入控件"""
    it = getattr(widget, "_textbox", None)
    if it is not None:
        return it
    it = getattr(widget, "_entry", None)
    if it is not None:
        return it
    if isinstance(widget, (tk.Text, tk.Entry)):
        return widget
    return None


def install_edit_capability(root_widget) -> None:
    """为 root 下所有可编辑文本框（CTkTextbox/CTkEntry）启用撤销/重做与只读防护。

    - 撤销：Ctrl+Z（重做 Ctrl+Y；原生 Text 在 undo=True 后自带类级绑定）。
    - 只读：仅作键盘屏蔽（浏览时可选中复制，屏蔽打字/粘贴/删除等改动）。
    递归扫描；每控件只安装一次；需在初始内容写入完成后调用（自动清空程序化填充）。
    """
    if root_widget is None:
        return
    for child in root_widget.winfo_children():
        internal = _internal_of(child)
        if internal is not None:
            _install_one(child, internal)
            continue
        install_edit_capability(child)


def _install_one(widget, internal) -> None:
    if getattr(widget, "_ps_guard_ok", False):
        return
    widget._ps_guard_ok = True
    widget._ps_readonly = False
    kind = "entry" if isinstance(internal, tk.Entry) else "text"
    try:
        internal.configure(undo=True)
        internal.edit_reset()  # 清掉程序化预填，避免一上来 Ctrl+Z 就清空全部内容
    except tk.TclError:
        pass

    def _guard(event):
        # 浏览只读：仅允许复制/全选与光标移动；屏蔽一切改动类按键（含 Ctrl+V/Z/Y 等）
        if getattr(widget, "_ps_readonly", False):
            keysym = event.keysym or ""
            ctrl = bool(event.state & 0x4)
            if ctrl:
                if keysym in ("c", "C", "a", "A"):
                    return None
                return "break"
            if keysym in _READONLY_NAV_KEYS:
                return None
            return "break"
        # 编辑态：CTkEntry 无类级撤销绑定，这里补 Ctrl+Z / Ctrl+Y
        if kind == "entry" and bool(event.state & 0x4):
            try:
                if event.keysym in ("z", "Z"):
                    internal.edit_undo()
                    return "break"
                if event.keysym in ("y", "Y"):
                    internal.edit_redo()
                    return "break"
            except tk.TclError:
                pass
        return None

    internal.bind("<KeyPress>", _guard, add="+")


def set_boxes_readonly(root_widget, readonly: bool) -> None:
    """递归切换 root 下所有可编辑文本框的"浏览只读"标志（选中复制仍可用）。"""
    if root_widget is None:
        return
    for child in root_widget.winfo_children():
        internal = _internal_of(child)
        if internal is not None:
            child._ps_readonly = bool(readonly)
            continue
        set_boxes_readonly(child, readonly)
