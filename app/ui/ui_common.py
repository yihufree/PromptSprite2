# -*- coding: utf-8 -*-
"""
ui_common.py - 主窗口 / 快速新建等共用 UI 小工具（2026-09-09 P2-12 抽取）

消除 main_window 与 quick_add 之间重复的：
  - 辅助按钮配色常量（浅色底 + 深色字）
  - 导航选中态配色常量
  - CTkTextbox "行数→像素" 换算（1 行 ≈ 20px，见 main_window 折叠字段注释）
  - 文本框撤销/重做与"只读键盘防护"（2026-09-09 新增，供详情区与快速新建表单共用）
  - `make_scroll_area()`：**视口高度可控**的滚动区（2026-09-18 新增）
"""
import tkinter as tk

import customtkinter as ctk

# "新增/返回"辅助按钮样式：浅色底 + 深色字，保证标签文字清晰可读
ADD_BTN_STYLE = dict(fg_color="#e8ecf1", hover_color="#d5dce5", text_color="#1f2937")

# 导航选中态：比默认按钮颜色稍稍加深，便于识别选中的 根目录→一级→二级 链路
SEL_BTN_STYLE = dict(fg_color="#25639c", hover_color="#1d4f7c", text_color="white")


def make_scroll_area(parent, height: int, **kwargs):
    """创建**视口高度可控**的滚动区（`CTkScrollableFrame`）——2026-09-18 新增。

    背景（用户实测反馈）：CTk 5.2 的 `CTkScrollableFrame(height=N)` 只设了内部 canvas 的高度，
    其**竖向滚动条的请求高度仍是默认值（≈200×缩放）**，会把外层容器顶高到远超 N
    ⇒ 想"把高度固定在 N"必须**同时**把滚动条高度也设为 N（实测：外层 268 → 148、canvas 252 → 132）。
    私有属性只在本模块访问（沿用 R-6 约定：CTk 私有 API 的唯一访问点在本文件）。
    """
    box = ctk.CTkScrollableFrame(parent, height=height, **kwargs)
    try:
        box._scrollbar.configure(height=height)
    except Exception:
        pass
    return box


# --------------------------------------------------------------------------- #
# 主色常量（2026-09-17，需求 U-2）
#   背景：以下 4 个主色原先以**字符串字面量**散落在 15 个 UI 文件中（共 144 处），
#   改主题/调色需全局搜替。现集中于此，各处统一引用。
#   **注意：只把"字面量"换成常量，颜色值本身一字未改** ⇒ 界面外观与改动前完全相同。
#   命名口径：OK=成功/确认/保存；DANGER=危险/删除；TAG=标签/智能；WARN=警示/待处理。
# --------------------------------------------------------------------------- #
C_OK = "#2E8B57"        # 成功 / 确认 / 保存
C_DANGER = "#D9534F"    # 危险 / 删除
C_TAG = "#7A4FBF"       # 标签 / 智能
C_WARN = "#E08A00"      # 警示 / 待处理

# 2026-09-14（阶段 3）：标签 chip 的柔和配色（按标签名哈希分配，同名永远同色）。
# 由 main_window.py 的 `_tag_color()` 与 quick_add.py 的标签区**共用**（避免两处各写一份）。
TAG_SOFT_COLORS = ("#5b8def", "#c9709a", "#c9971f", "#2E8B57", "#7A4FBF",
                   "#1f6f8f", "#8a6d3b", "#4a7c59", "#a04a6c", "#3d7ea6")


def tag_color(name: str) -> str:
    """按标签名哈希取一个柔和底色（同名永远同色）。"""
    h = 0
    for ch in (name or ""):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return TAG_SOFT_COLORS[h % len(TAG_SOFT_COLORS)]


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


# --------------------------------------------------------------------------- #
# 2026-09-17（审核报告 R-3 / R-6）：把原先在 main_window / quick_add 里
#   **各写一份**的"屏幕工作区 / 悬停提示 / CTk 私有 API 访问"统一收拢到本模块。
#   目的：① 消除重复实现（改一处即可）；② CTk 私有属性只有这里一处访问点，
#         自研扩展库升级时只需复查本文件。
# --------------------------------------------------------------------------- #
def work_area(widget) -> tuple:
    """返回浮窗可用的屏幕工作区 (left, top, right, bottom)。

    2026-09-16（批次 11-1，用户反馈 4"浮窗下边被屏幕遮挡"）：根因是旧代码用
    `winfo_screenheight()`（**整屏**）做夹紧——整屏 1080 时任务栏占底部 48px、
    工作区只有 1032，浮窗底边最多停到 1072，最后约 40px 落进任务栏被遮住。

    优先取 Windows 工作区（已扣任务栏）：`SystemParametersInfoW(SPI_GETWORKAREA=0x0030)`；
    取不到（非 Windows / 异常）时**回退整屏** ⇒ 调用方行为与改动前一致，不会更差。
    （2026-09-17 从 main_window.py 原样搬入本模块，主窗口改为导入使用。）
    """
    try:
        sw, sh = int(widget.winfo_screenwidth()), int(widget.winfo_screenheight())
    except Exception:
        sw, sh = 0, 0
    try:
        import ctypes
        from ctypes import wintypes

        class _RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        rc = _RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0):
            if rc.right - rc.left > 100 and rc.bottom - rc.top > 100:
                return (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))
    except Exception:
        pass
    return (0, 0, sw, sh)


class Tooltip:
    """轻量悬停提示（2026-09-17 R-3：合并 main_window._FieldTooltip 与 quick_add._Tip）。

    鼠标进入控件时在其下方弹出只读小窗显示完整文本（长名称 / 完整内容查看），
    并做**屏幕工作区**夹紧：右侧越界左移、下方越界上翻，避免落到任务栏里被遮挡。
    """

    def __init__(self, widget, text: str, wraplength: int = 460):
        self.widget = widget
        self.text = text
        self.wraplength = int(wraplength)
        self._tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        if self._tip is not None:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        tk.Label(self._tip, text=self.text, justify="left", bg="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=self.wraplength, padx=8, pady=6,
                 font=("Microsoft YaHei", 10)).pack()
        self._tip.update_idletasks()
        w, h = self._tip.winfo_reqwidth(), self._tip.winfo_reqheight()
        _wl, _wt, _wr, _wb = work_area(self.widget)
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        if x + w > _wr:
            x = max(_wr - w - 8, _wl + 8)
        if y + h > _wb - 4:
            y = max(self.widget.winfo_rooty() - h - 4, _wt + 8)
        self._tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def maybe_tooltip(widget, name: str, budget: int, wraplength: int = 460) -> None:
    """长名称悬停提示：名称长度超过列宽预算时挂一个 `Tooltip`（2026-09-17 R-3 统一入口）。

    budget：该列能显示的大致汉字数（项目/根目录≈6~8，一级/二级≈10）。
    """
    if len(str(name or "")) > int(budget):
        Tooltip(widget, name, wraplength=wraplength)


def attach_tooltip_everywhere(widget, text: str) -> Tooltip:
    """在按钮"全部可命中区域"挂接悬停提示（2026-09-13 1-A-4 增强的等价实现）。

    CTkButton.bind 已代理到内部 canvas / 文字标签 / 图标标签（覆盖按钮可视区），
    但**按钮本体（Frame）未被绑定**；若鼠标落在未被子控件覆盖的内边距区域，
    <Enter> 落在按钮本体上而无绑定，提示不会出现。此处补绑按钮本体，闭合该缺口。
    """
    tip = Tooltip(widget, text)
    try:
        tk.Frame.bind(widget, "<Enter>", tip._show, add="+")
        tk.Frame.bind(widget, "<Leave>", tip._hide, add="+")
    except Exception:
        pass
    return tip


def text_widget(widget):
    """取控件内部的真实 tk.Text / tk.Entry（**本模块是 CTk 私有属性唯一的访问点**）。

    2026-09-17（R-6）：外层 UI 一律经本函数（或 `text_of`）访问 `_textbox` / `_entry`，
    避免私有属性散落在各文件里；CTk 升级时只需复查本文件。
    """
    return _internal_of(widget)


def textbox_internal(widget):
    """**仅当** widget 是多行文本框时返回其内部 `tk.Text`，否则返回 None。

    2026-09-17（R-6）：`getattr(w, "_textbox", None)` 这类访问的唯一点——
    与 `text_widget()` 的区别是**不含 CTkEntry**（调用方需要区分"多行文本框"时用本函数）。
    """
    it = getattr(widget, "_textbox", None)
    if it is not None:
        return it
    if isinstance(widget, tk.Text):
        return widget
    return None


def text_of(widget) -> str:
    """读取文本控件内容（CTkTextbox / CTkEntry / 原生 Text·Entry 均可）；失败返回空串。"""
    it = _internal_of(widget)
    if it is None:
        return ""
    try:
        if isinstance(it, tk.Entry):
            return it.get()
        return it.get("1.0", "end-1c")
    except Exception:
        return ""


def widget_scaling(widget) -> float:
    """取 CTk 控件的 DPI 缩放系数（取不到回退 1.0）。

    2026-09-17（R-6）：原先在 settings_dialog 里直接调用 `box._get_widget_scaling()`，
    现统一经本函数访问。
    """
    try:
        return float(widget._get_widget_scaling()) or 1.0
    except Exception:
        return 1.0


def content_fit_height(box) -> int:
    """文本框恰好显示全部内容的像素高度（含自动换行；最少 1 行）。

    2026-08-18 新增（原在 main_window 与 quick_add 各写一份，2026-09-17 R-1 合并到本模块）：
    展开提示词时按"有多少行就显示多少行"自适应高度。用**字体测量**估算 wrap 后的实际显示行数，
    不依赖控件布局时机（`displaylines` 在未布局时不可靠）。
    """
    it = _internal_of(box)
    try:
        import tkinter.font as tkfont
        font = tkfont.Font(root=it, font=it.cget("font"))
        line_h = font.metrics("linespace") or 20
        text = it.get("1.0", "end-1c")
        # 文本可用宽度：控件宽扣除内边距/边框/右侧滚动条余量（取偏小值→行数略多，保证不遮挡）
        avail = max(it.winfo_width() - 14, 80)
        lines = 0
        for para in text.split("\n"):
            w = font.measure(para)
            lines += max(1, -(-w // avail))   # 向上取整：该段落自动换行后的显示行数
        n = max(int(lines), 1)
    except Exception:
        try:  # 兜底：按逻辑行数估算
            n = max(str(it.get("1.0", "end-1c")).count("\n") + 1, 1)
        except Exception:
            n = 6
        line_h = 20
    return n * line_h + 8   # 8px 余量：上下内边距与边框，确保最后一行完整可见


def fit_prompt_box(box, empty_h: int) -> None:
    """⑧⑨ 提示词文本框高度自适应：有内容按实际显示行数；无内容回到 `empty_h`。

    （2026-09-17 R-1：原在 quick_add 内的 `_fit_prompt_box`，与 main_window 的同类逻辑合并。）
    """
    if box is None:
        return
    if text_of(box).strip():
        box.configure(height=content_fit_height(box))
    else:
        box.configure(height=int(empty_h))
