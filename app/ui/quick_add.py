# -*- coding: utf-8 -*-
"""
quick_add.py - 快捷悬停添加窗口（独创交互）
创建日期：2026-08-12（阶段四：快捷悬停添加）

交互规则：
  1. 鼠标悬停 L0 根目录按钮 0.2 秒 → 自动刷新 L1 列（无需点击）
  2. 悬停 L1 按钮 0.2 秒 → 刷新 L2 列；若无子分类则直接锁定该分类
  3. 悬停 L2 按钮 0.2 秒 → 锁定分类，光标自动跳入"①风格名称"输入框
  4. 窗口角落提供"归入未分类"紧急按钮
  5. 保存后刷新主窗口树，表单清空可连续录入
"""
import re  # 2026-08-19：⑩图像获取方案"打开"按钮提取链接
import tkinter as tk
import webbrowser  # 2026-08-19：⑩图像获取方案"打开"按钮打开网址
from tkinter import messagebox, simpledialog

import customtkinter as ctk

from .. import config  # 2026-08-29（B2 修复）：新增根目录兜底归入"未明确分类"
from .. import tagger, tagger_engine  # 2026-09-14（阶段 3）：录入时自动推荐标签
from ..models import Entry

_HOVER_MS = 200  # 悬停锁定判定时长（秒级换算：0.2 秒）

# "新增"辅助按钮样式 / 选中态样式（2026-09-09 P2-12：与主窗口共用公共样式）
from .ui_common import ADD_BTN_STYLE as _ADD_STYLE
from .ui_common import SEL_BTN_STYLE as _SEL_STYLE
from .ui_common import install_edit_capability as _enable_text_undo  # 2026-09-09：文本框撤销/重做
from .ui_common import tag_color as _tag_color  # 2026-09-14（阶段 3）：标签配色（与主窗口共用）
# 2026-09-17（审核 R-1/R-3/R-6）：悬停提示与"文本框自适应高度"的实现统一到 ui_common，
#   本窗口不再各写一份（原先此处有 `class _Tip` 与 `_content_fit_height` 的副本）。
from .ui_common import Tooltip as _Tip
from .ui_common import content_fit_height as _cfh_common
from .ui_common import fit_prompt_box as _fit_prompt_box_common
from .ui_common import maybe_tooltip as _maybe_tooltip
from .ui_common import C_TAG as _C_TAG, C_OK as _C_OK, C_WARN as _C_WARN, C_DANGER as _C_DANGER  # 2026-09-17（U-2）：主色常量

# ⑧/⑨ 提示词文本框（2026-08-19）：空时默认 6 行（120px）可见；输入内容后按实际行数自适应；
# 无内容时恢复 6 行。CTkTextbox.height 单位为像素，120px ≈ 6 行完整可见。
_PROMPT_KEYS = {"prompt_cn", "prompt_en"}
_PROMPT_EMPTY_H = 120

# 表单字段：(显示名, 数据库字段键, 文本框高度行数)
_FORM_FIELDS = [
    ("② 介绍", "intro", 3),
    ("③ 溯源", "origin", 3),
    ("④ 核心特征", "features", 3),
    ("⑤ 应用场景", "scenes", 3),
    ("⑥ 代表作", "works", 2),
    ("⑦ 代表高清配图", "image_desc", 3),
    ("⑧ 中文版提示词", "prompt_cn", 6),
    ("⑨ 英文版提示词", "prompt_en", 6),
    ("⑩ 图像获取方案", "image_plan", 3),
]

# 2026-09-15（批次 7，用户要求）：快速新建窗口"屏内定位 + 高度收敛"常量
#   · 用户口径：初始窗口距屏幕上边 ≈50，且**任何情况下**整窗（含下边）都在屏幕范围内；
#   · 尺寸一律按**源码值**（CTk 会乘 DPI 缩放系数换算为物理像素），故屏幕可用空间需除以缩放系数。
_QW_SRC_W = 1200          # 期望宽（源码值，沿用原 `geometry("1200x700")` 的宽）
_QW_SRC_H = 700           # 期望高（源码值，沿用原值）
_QW_TOP_MARGIN = 50       # 距屏幕上边（物理像素）
_QW_BOTTOM_MARGIN = 40    # 屏幕下边预留（物理像素）
_QW_MIN_SRC_H = 480       # 高度源码值下限（内容区均为可滚动容器，压矮不丢内容）


class QuickAddWindow(ctk.CTkToplevel):
    def __init__(self, master, db, default_cat_id=None):
        super().__init__(master)
        self.db = db
        self.master = master
        # 2026-09-15（批次 8-A，用户确认）：**默认目标分类**（由主窗口传入"当前视图分类"）。
        #   作用：① 打开窗口后**不悬停任何分类**也能拿到推荐上下文（原先为 None ⇒ 推荐不出）；
        #        ② 作为保存目标的兜底（用户悬停/锁定分类时优先用悬停/锁定的那个，不改变原优先级）。
        self._default_cat_id = default_cat_id
        # 2026-09-15 17:15（批次 8-D，用户确认）：T2"⑧⑨ 输入停止后自动推荐"（设置开关，默认关）。
        #   与主窗口**同一开关**（meta `settings_auto_tag_suggest`）、同一防抖时长 800ms。
        try:
            self._auto_tag_suggest = self.db.get_meta(config.META_AUTO_TAG_SUGGEST) == "1"
        except Exception:
            self._auto_tag_suggest = False
        self._suggest_timer = None   # 防抖定时器句柄
        self._hover_timer = None
        self._cat_id = None          # 锁定分类 id；None = 未分类
        self._domain_id = None       # 当前悬停选中的根目录 id
        self._active_cat_id = None   # 当前二级列展示的父分类 id
        self._saved_count = 0
        # 2026-08-19：选中链路高亮与保存目标
        self._uncat_locked = False   # 显式锁定"未分类"：保存时不再自动归入选中链路的分类
        # 2026-08-29（快速新建四级化同步）：项目类别（最高层级）状态与按钮引用
        self._project_id = None      # 当前项目类别 id；None = 未分配视图
        self._project_ready = False  # 首次导航默认项目是否已确定
        self._p_btns = {}            # 项目类别列按钮引用
        self._p_styles = {}          # 项目类别列按钮原始配色
        self._l0_btns = {}           # 根目录列按钮引用（选中高亮原地更新用）
        self._l1_btns = {}           # 一级分类列按钮引用
        self._l2_btns = {}           # 二级分类列按钮引用
        self._l0_styles = {}         # 根目录列按钮原始配色（恢复高亮用）
        self._l1_styles = {}
        self._l2_styles = {}

        self.title("✚ 快速新建提示词（悬停选定分类，无需点击）")
        # 2026-09-15（批次 7，用户要求）：原固定 `geometry("1200x700")` **只给尺寸、不给位置**，
        #   实际位置由窗口管理器决定（实测偏低 ⇒ 窗口下边出屏）。改为"按屏幕收敛 + 水平居中 +
        #   上边距 50 + 屏内夹取"，尺寸与位置统一由 `_place_on_screen()` 设置（含映射后二次校正）。
        self._place_on_screen()
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(4, weight=1)

        self._build_top()
        self._build_columns()
        self._build_form()
        self._load_projects()

    def _place_on_screen(self) -> None:
        """按屏幕收敛尺寸并定位：水平居中 + 上边距 ≈50，保证整窗在屏幕内（2026-09-15 批次 7）。

        做法（与主窗口同口径，**仅作用于本窗口**，不影响其它对话框）：
          1. 宽/高按屏幕可用空间收敛（源码值 = 物理目标 ÷ DPI 缩放系数）；
          2. 先按估算尺寸定位，再在**映射后**用真实像素宽高校正两次
             （映射前 `winfo_width()` 返回 1，故必须二次校正）。
        用户口径：上边距 50；窗口下边不得超出屏幕（预留 40）；高度不足时压矮（内容区可滚动）。
        """
        try:
            scale = ctk.ScalingTracker.get_window_scaling(self)
        except Exception:
            scale = 1.0
        if not scale or scale <= 0:
            scale = 1.0
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        # 1) 尺寸收敛（源码值口径）：宽高都不超出屏幕可用空间
        max_w_src = int(max(sw - 40, 320) / scale)
        max_h_src = int(max(sh - _QW_TOP_MARGIN - _QW_BOTTOM_MARGIN, 240) / scale)
        w_src = min(_QW_SRC_W, max_w_src)
        h_src = min(_QW_SRC_H, max_h_src)
        if max_h_src >= _QW_MIN_SRC_H:          # 屏幕够高：高度保持在 [下限, 期望] 之间
            h_src = max(h_src, _QW_MIN_SRC_H)
        self.geometry(f"{w_src}x{h_src}")

        def _apply() -> None:
            try:
                if self.winfo_ismapped():
                    w_px, h_px = self.winfo_width(), self.winfo_height()
                else:
                    w_px, h_px = int(w_src * scale), int(h_src * scale)
                if w_px <= 1 or h_px <= 1:
                    return
                x = max((sw - w_px) // 2, 0)
                y = _QW_TOP_MARGIN
                if y + h_px > sh - _QW_BOTTOM_MARGIN:    # 下边出屏 → 上移（但不高于 0）
                    y = max(sh - _QW_BOTTOM_MARGIN - h_px, 0)
                if x + w_px > sw:                        # 右边出屏 → 左移
                    x = max(sw - w_px, 0)
                self.geometry(f"+{x}+{y}")
            except Exception:
                pass

        _apply()
        self.after(60, _apply)     # 映射后校正（DPI 下真实像素与估算可能相差数像素）
        self.after(220, _apply)

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #
    def _build_top(self):
        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, columnspan=5, sticky="ew", padx=8, pady=(8, 4))
        ctk.CTkButton(top, text="📂 归入未分类", width=120, fg_color="#8a94a6",
                      command=self._lock_uncategorized).pack(side="left", padx=4)
        self.lock_label = ctk.CTkLabel(top, text="未锁定分类（请悬停选择）", text_color="gray")
        self.lock_label.pack(side="left", padx=12)

    def _build_columns(self):
        # 2026-08-29（快速新建四级化同步）：项目类别 → 根目录 → 一级 → 二级
        self.p_frame = ctk.CTkScrollableFrame(self, width=112, label_text="项目类别（悬停）")
        self.l0_frame = ctk.CTkScrollableFrame(self, width=112, label_text="根目录（悬停）")
        self.l1_frame = ctk.CTkScrollableFrame(self, width=168, label_text="一级分类（悬停）")
        self.l2_frame = ctk.CTkScrollableFrame(self, width=168, label_text="二级分类（悬停）")
        self.p_frame.grid(row=1, column=0, sticky="nsew", padx=(8, 2), pady=4)
        self.l0_frame.grid(row=1, column=1, sticky="nsew", padx=2, pady=4)
        self.l1_frame.grid(row=1, column=2, sticky="nsew", padx=2, pady=4)
        self.l2_frame.grid(row=1, column=3, sticky="nsew", padx=(2, 8), pady=4)

    def _build_form(self):
        # 固定底部按钮 + 滚动输入区（参考主窗口详情区：按钮始终可见）
        form_root = ctk.CTkFrame(self)
        form_root.grid(row=1, column=4, sticky="nsew", padx=(0, 8), pady=4)
        form_root.grid_rowconfigure(0, weight=1)
        form_root.grid_columnconfigure(0, weight=1)

        form = ctk.CTkScrollableFrame(form_root, label_text="表单输入区")
        form.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(form, text="① 风格名称 *", font=("Microsoft YaHei", 12, "bold"),
                     anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        self.name_entry = ctk.CTkEntry(form, height=34)
        self.name_entry.pack(fill="x", padx=8, pady=(0, 2))

        # 2026-09-14（阶段 3）：🏷 标签（自动推荐；推荐结果直接加入＝"默认全选"，点 × 去掉不要的）
        self._tag_names = []
        self._rec_last_name = ""
        ctk.CTkLabel(form, text="🏷 标签", font=("Microsoft YaHei", 12, "bold"),
                     anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        _tag_row = ctk.CTkFrame(form, fg_color="transparent")
        _tag_row.pack(fill="x", padx=8, pady=(0, 2))
        ctk.CTkButton(_tag_row, text="✨ 推荐标签", width=104, height=24, fg_color=_C_TAG,
                      font=("Microsoft YaHei", 11), command=self._suggest_tags
                      ).pack(side="left")
        self._tag_entry = ctk.CTkEntry(_tag_row, placeholder_text="输入标签，回车添加")
        self._tag_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._tag_entry.bind("<Return>", lambda _e=None: self._add_tag_from_entry())
        # 2026-09-15 18:30（批次 9，用户确认 C18 路线①）：新增「📋 选择…」——
        #   从"库中已用标签 ∪ 词表 ∪ 热点词"候选池多选/新建标签（与主窗口同一交互）
        ctk.CTkButton(_tag_row, text="📋 选择…", width=84, height=24, fg_color="#5B7CC7",
                      font=("Microsoft YaHei", 11), command=self._pick_tags_dialog
                      ).pack(side="left", padx=(6, 0))
        self._tag_chips_row = ctk.CTkFrame(form, fg_color="transparent")
        self._tag_chips_row.pack(fill="x", padx=8, pady=(0, 2))
        self._refresh_tag_chips()
        # ①名称框**回车 / 失焦** → 自动推荐一次标签（T1，无需设置开关）
        self.name_entry.bind("<Return>", self._on_name_committed, add="+")
        self.name_entry.bind("<FocusOut>", self._on_name_committed, add="+")

        self._boxes = {}
        for label, key, height in _FORM_FIELDS:
            ctk.CTkLabel(form, text=label, font=("Microsoft YaHei", 12, "bold"),
                         anchor="w").pack(fill="x", padx=8, pady=(6, 0))
            if key == "image_plan":
                # 2026-08-19，第001条：⑩图像获取方案 与主界面一致——右侧"打开"按钮，
                # 实时读取文本框内容，输入网址后点击即可打开测试（无需先保存）
                row = ctk.CTkFrame(form, fg_color="transparent")
                row.pack(fill="x", padx=8, pady=(0, 2))
                box = ctk.CTkTextbox(row, height=height)
                box.pack(side="left", fill="x", expand=True)
                open_btn = ctk.CTkButton(row, text="打开", width=52, height=28,
                                         command=lambda b=box: self._open_image_plan(b))
                open_btn.pack(side="right", padx=(6, 0))
            else:
                box = ctk.CTkTextbox(form, height=height)
                box.pack(fill="x", padx=8, pady=(0, 2))
            self._boxes[key] = box
            if key in _PROMPT_KEYS:
                # 2026-08-19：⑧/⑨ 提示词——空时默认 6 行（120px），输入后按内容行数自适应
                box.configure(height=_PROMPT_EMPTY_H)
                box.bind("<KeyRelease>",
                         lambda _e=None, b=box: self._fit_prompt_box(b))
                # 2026-09-15 17:15（批次 8-D）：⑧/⑨ 输入停止 800ms 后自动推荐（T2，开关默认关）
                box.bind("<KeyRelease>", self._on_prompt_typed, add="+")

        # 底部按钮区：固定不随输入区滚动
        footer = ctk.CTkFrame(form_root, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew")
        ctk.CTkButton(footer, text="💾 保存", width=110, fg_color=_C_OK,
                      command=self._save).pack(side="left", padx=8, pady=8)
        ctk.CTkButton(footer, text="清空表单", width=100,
                      command=self._clear_form).pack(side="left", padx=4, pady=8)
        # 2026-09-09：表单各文本框启用撤销(Ctrl+Z)/重做(Ctrl+Y)
        _enable_text_undo(form)

    # ------------------------------------------------------------------ #
    # 悬停联动
    # ------------------------------------------------------------------ #
    def _schedule_hover(self, ms: int, fn) -> None:
        self._cancel_hover()
        self._hover_timer = self.after(ms, fn)

    def _cancel_hover(self) -> None:
        if self._hover_timer is not None:
            try:
                self.after_cancel(self._hover_timer)
            except Exception:
                pass
            self._hover_timer = None

    # ------------------------------------------------------------------ #
    # 选中链路高亮（2026-08-19：与主界面一致；2026-08-29 扩展为四列 项目→根目录→一级→二级）
    # ------------------------------------------------------------------ #
    def _clear_nav_btns(self, col: str) -> None:
        """清空某一列按钮引用与原始配色记录（该列重建时调用）"""
        if col == "p":
            self._p_btns, self._p_styles = {}, {}
        elif col == "l0":
            self._l0_btns, self._l0_styles = {}, {}
        elif col == "l1":
            self._l1_btns, self._l1_styles = {}, {}
        else:
            self._l2_btns, self._l2_styles = {}, {}

    @staticmethod
    def _style_nav_btn(btn, selected: bool, orig) -> None:
        """选中态：深蓝底白字（比默认按钮稍稍加深）；未选中：恢复创建时的原始配色"""
        if selected:
            btn.configure(fg_color=_SEL_STYLE["fg_color"],
                          hover_color=_SEL_STYLE["hover_color"],
                          text_color=_SEL_STYLE["text_color"])
        else:
            if orig is None:  # 样式记录缺失时保持当前样式，避免解包崩溃
                return
            fg, hover, text = orig
            btn.configure(fg_color=fg, hover_color=hover, text_color=text)

    def _apply_chain_highlight(self) -> None:
        """原地更新四列选中高亮：项目=_project_id、根目录=_domain_id、一级=_active_cat_id、二级=_cat_id"""
        for pid, btn in self._p_btns.items():
            self._style_nav_btn(btn, pid == self._project_id, self._p_styles.get(pid))
        for cid, btn in self._l0_btns.items():
            self._style_nav_btn(btn, cid == self._domain_id, self._l0_styles.get(cid))
        for cid, btn in self._l1_btns.items():
            self._style_nav_btn(btn, cid == self._active_cat_id, self._l1_styles.get(cid))
        for cid, btn in self._l2_btns.items():
            self._style_nav_btn(btn, cid == self._cat_id, self._l2_styles.get(cid))

    @staticmethod
    def _clear(frame) -> None:
        for child in frame.winfo_children():
            child.destroy()

    # ------------------------------------------------------------------ #
    # 项目类别（2026-08-29 快速新建四级化同步）
    # ------------------------------------------------------------------ #
    def _load_projects(self) -> None:
        """渲染项目类别列（含新增按钮、"未分配"虚拟项），并按当前项目加载根目录列"""
        self._clear(self.p_frame)
        self._clear_nav_btns("p")
        ctk.CTkButton(self.p_frame, text="✚ 新增项目类别", height=28, **_ADD_STYLE,
                      command=self._add_project).pack(fill="x", padx=6, pady=2)
        if not self._project_ready:   # 首次默认：存在未分配根目录则停留"未分配"，否则选第一个项目
            self._project_ready = True
            if self.db.list_unassigned_domains():
                self._project_id = None
            else:
                ps = self.db.list_projects()
                self._project_id = ps[0]["id"] if ps else None
        for p in self.db.list_projects():
            b = ctk.CTkButton(self.p_frame, text=p["name"], anchor="w", height=30)
            b.pack(fill="x", padx=6, pady=2)
            self._p_btns[p["id"]] = b
            self._p_styles[p["id"]] = (b.cget("fg_color"), b.cget("hover_color"),
                                        b.cget("text_color"))
            b.bind("<Enter>",
                   lambda _e=None, pid=p["id"]: self._schedule_hover(_HOVER_MS,
                                                                lambda: self._select_project(pid)))
            b.bind("<Leave>", lambda _e=None: self._cancel_hover())
            _maybe_tooltip(b, p["name"], 6)  # 2026-08-29：长名称悬停提示（与主窗口一致）
        if self.db.list_unassigned_domains():
            b = ctk.CTkButton(self.p_frame, text="🗂 未分配", anchor="w", height=30)
            b.pack(fill="x", padx=6, pady=2)
            self._p_btns[None] = b
            self._p_styles[None] = (b.cget("fg_color"), b.cget("hover_color"),
                                    b.cget("text_color"))
            b.bind("<Enter>",
                   lambda _e=None: self._schedule_hover(_HOVER_MS,
                                                   lambda: self._select_project(None)))
            b.bind("<Leave>", lambda _e=None: self._cancel_hover())
        self._apply_chain_highlight()
        self._load_domains()

    def _select_project(self, project_id) -> None:
        """选择项目类别（None=未分配视图）：切换后重建根目录列并清空子列"""
        if self._project_id == project_id:
            self._apply_chain_highlight()
            return
        self._project_id = project_id
        self._domain_id = None
        self._active_cat_id = None
        self._cat_id = None
        self._uncat_locked = False
        self.lock_label.configure(text="未锁定分类（请悬停选择）", text_color="gray")
        self._clear(self.l1_frame)
        self._clear(self.l2_frame)
        self._clear_nav_btns("l1")
        self._clear_nav_btns("l2")
        self._load_domains()

    def _add_project(self) -> None:
        name = simpledialog.askstring("新增项目类别", "请输入项目类别名称：", parent=self)
        if name and name.strip():
            pid = self.db.add_project(name.strip())
            self._project_ready = True
            self._project_id = pid
            self._load_projects()

    def _load_domains(self) -> None:
        """渲染根目录列（当前项目类别下；"未分配"视图显示无归属根目录）"""
        self._clear(self.l0_frame)
        self._clear_nav_btns("l0")
        ctk.CTkButton(self.l0_frame, text="✚ 新增根目录", height=28, **_ADD_STYLE,
                      command=self._add_domain).pack(fill="x", padx=6, pady=2)
        domains = (self.db.list_unassigned_domains() if self._project_id is None
                   else self.db.list_domains(project_id=self._project_id))
        for d in domains:
            b = ctk.CTkButton(self.l0_frame, text=d["name"], anchor="w", height=30)
            b.pack(fill="x", padx=6, pady=2)
            # 2026-08-19：记录按钮引用与原始配色，供选中链路高亮原地更新
            self._l0_btns[d["id"]] = b
            self._l0_styles[d["id"]] = (b.cget("fg_color"), b.cget("hover_color"),
                                        b.cget("text_color"))
            b.bind("<Enter>",
                   lambda _e=None, did=d["id"]: self._schedule_hover(_HOVER_MS,
                                                                lambda: self._load_l1(did)))
            b.bind("<Leave>", lambda _e=None: self._cancel_hover())
            _maybe_tooltip(b, d["name"], 6)  # 2026-08-29：长名称悬停提示（与主窗口一致）
        self._apply_chain_highlight()

    def _load_l1(self, domain_id: int) -> None:
        self._domain_id = domain_id
        self._active_cat_id = None
        self._cat_id = None
        self._uncat_locked = False  # 2026-08-19：新选择根目录即重建链路，解除"未分类"锁定
        self.lock_label.configure(text="未锁定分类（请悬停选择）", text_color="gray")
        self._clear(self.l1_frame)
        self._clear(self.l2_frame)
        self._clear_nav_btns("l1")
        self._clear_nav_btns("l2")
        ctk.CTkButton(self.l1_frame, text="✚ 新增一级分类", height=28, **_ADD_STYLE,
                      command=self._add_l1).pack(fill="x", padx=6, pady=2)
        cats = self.db.list_categories(domain_id=domain_id, parent_id=None)
        if not cats:
            name = self.db.get_domain(domain_id)["name"]
            self._lock(None, f"领域「{name}」下暂无分类（可点击上方新增）")
            self._apply_chain_highlight()
            return
        for c in cats:
            b = ctk.CTkButton(self.l1_frame, text=c["name"], anchor="w", height=30)
            b.pack(fill="x", padx=6, pady=2)
            # 2026-08-19：记录按钮引用与原始配色，供选中链路高亮原地更新
            self._l1_btns[c["id"]] = b
            self._l1_styles[c["id"]] = (b.cget("fg_color"), b.cget("hover_color"),
                                        b.cget("text_color"))
            b.bind("<Enter>",
                   lambda _e=None, cid=c["id"]: self._schedule_hover(_HOVER_MS,
                                                                lambda: self._load_l2(cid)))
            b.bind("<Leave>", lambda _e=None: self._cancel_hover())
            _maybe_tooltip(b, c["name"], 10)  # 2026-08-29：长名称悬停提示（与主窗口一致）
        self._apply_chain_highlight()

    def _load_l2(self, cat_id: int, force: bool = False) -> None:
        # 2026-08-19：force=True 供"新增二级分类"后强制刷新（原逻辑直接 return 导致新增项不显示）
        if not force and self._active_cat_id == cat_id:
            return  # 已展示同一父分类的子级：避免重复渲染
        self._active_cat_id = cat_id
        self._cat_id = None  # 2026-08-19：切换到新的一级分类时释放旧锁定，保存目标跟随可见链路
        self._uncat_locked = False  # 2026-08-19：选中一级分类即归入链路
        # 2026-08-19：选中一级分类后显示其名称，提示保存将添加至该分类（链条自动添加）
        l1 = self.db.get_category(cat_id)
        if l1:
            self.lock_label.configure(text=f"已选择：{l1['name']}", text_color="#25639c")
        children = self.db.list_categories(parent_id=cat_id)
        self._clear(self.l2_frame)
        self._clear_nav_btns("l2")
        ctk.CTkButton(self.l2_frame, text="✚ 新增二级分类", height=28, **_ADD_STYLE,
                      command=self._add_l2).pack(fill="x", padx=6, pady=2)
        if not children:  # 无子分类 → 悬停即锁定
            self._lock(cat_id)
            self._apply_chain_highlight()
            return
        for c in children:
            b = ctk.CTkButton(self.l2_frame, text=c["name"], anchor="w", height=30)
            b.pack(fill="x", padx=6, pady=2)
            # 2026-08-19：记录按钮引用与原始配色，供选中链路高亮原地更新
            self._l2_btns[c["id"]] = b
            self._l2_styles[c["id"]] = (b.cget("fg_color"), b.cget("hover_color"),
                                        b.cget("text_color"))
            b.bind("<Enter>",
                   lambda _e=None, cid=c["id"]: self._schedule_hover(_HOVER_MS,
                                                                lambda: self._lock(cid)))
            b.bind("<Leave>", lambda _e=None: self._cancel_hover())
            _maybe_tooltip(b, c["name"], 10)  # 2026-08-29：长名称悬停提示（与主窗口一致）
        self._apply_chain_highlight()

    def _lock(self, cat_id, note: str = None) -> None:
        """锁定分类：光标自动跳入名称输入框"""
        self._cat_id = cat_id
        self._uncat_locked = False  # 2026-08-19：锁定具体分类即归入选中链路
        name = note or self.db.get_category(cat_id)["name"]
        self.lock_label.configure(text=f"✔ 已锁定：{name}", text_color=_C_OK)
        self._apply_chain_highlight()  # 2026-08-19：锁定后高亮整条选中链路
        self.name_entry.focus_set()
        self._cancel_hover()

    def _lock_uncategorized(self) -> None:
        # 2026-08-19：显式"归入未分类"——清空选中链路、保存目标固定为未分类
        self._cat_id = None
        self._active_cat_id = None
        self._domain_id = None
        self._uncat_locked = True
        self.lock_label.configure(text="✔ 已锁定：未分类", text_color="#8a94a6")
        self._apply_chain_highlight()
        self.name_entry.focus_set()

    # ------------------------------------------------------------------ #
    # 分类新增（与主界面三列一致）
    # ------------------------------------------------------------------ #
    def _add_domain(self) -> None:
        name = simpledialog.askstring("新增根目录", "请输入根目录名称：", parent=self)
        if not (name and name.strip()):
            return
        # 2026-08-29（B2 修复）：新增根目录弹窗选择项目类别（与主窗口一致），未选择归入"未明确分类"
        from .project_chooser import choose_project
        r = choose_project(self, self.db, "选择项目类别", f"【{name.strip()}】归属项目类别：")
        if r is None:
            project_id = self.db.ensure_project(config.PROJECT_FALLBACK)
        else:
            project_id = r[1]
        self.db.add_domain(name.strip(), project_id=project_id)
        self._load_domains()

    def _add_l1(self) -> None:
        if not self._domain_id:
            messagebox.showwarning("提示", "请先悬停选择根目录", parent=self)
            return
        name = simpledialog.askstring("新增一级分类", "请输入一级分类名称：", parent=self)
        if name and name.strip():
            self.db.add_category(name.strip(), domain_id=self._domain_id)
            self._load_l1(self._domain_id)

    def _add_l2(self) -> None:
        if not self._active_cat_id:
            messagebox.showwarning("提示", "请先悬停选择一级分类", parent=self)
            return
        name = simpledialog.askstring("新增二级分类", "请输入二级分类名称：", parent=self)
        if name and name.strip():
            self.db.add_category(name.strip(), parent_id=self._active_cat_id)
            # 2026-08-19：force=True 强制重建二级列，使新增分类立即显示在选中的一级分类下
            self._load_l2(self._active_cat_id, force=True)

    # ------------------------------------------------------------------ #
    # 保存
    # ------------------------------------------------------------------ #
    def _effective_cat_id(self):
        """保存目标分类（2026-08-19：自动添加到已选中的链条下面）。

        优先级：已锁定分类（二级/无子一级） > 悬停选中的一级分类 > 未分类。
        允许提示词只有一级分类（直接归入一级），或只有根目录（保存为未分类）。
        显式点击"归入未分类"时（_uncat_locked=True）固定保存为未分类。
        """
        if self._cat_id is not None:
            return self._cat_id
        if self._uncat_locked:
            return None
        # 2026-09-15（批次 8-A）：无悬停/锁定分类时，回退"主窗口传入的默认目标分类"
        #   （用户显式点"归入未分类"时上面已提前返回 None，不受影响）
        return self._active_cat_id or self._default_cat_id

    def _save(self) -> None:
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("提示", "请填写① 风格名称", parent=self)
            self.name_entry.focus_set()
            return
        e = Entry(category_id=self._effective_cat_id(), name=name,
                  intro=self._box("intro"), origin=self._box("origin"),
                  features=self._box("features"), scenes=self._box("scenes"),
                  works=self._box("works"), image_desc=self._box("image_desc"),
                  prompt_cn=self._box("prompt_cn"), prompt_en=self._box("prompt_en"),
                  image_plan=self._box("image_plan"))
        _new_id = self.db.add_entry(e)
        # 2026-09-14（阶段 3）：新增保存后写入标签（拿到新 id 才能写）
        #   注：旧版此窗口没有标签输入项，故此前从不写 entry_tags。
        try:
            if self._tag_names:
                self.db.set_entry_tags(_new_id, self._tag_names)
        except Exception:
            pass
        self._saved_count += 1
        self.master.refresh_domains(silent=True)  # 2026-08-18（P1-2）：静默刷新，不打断主窗口连续录入
        self.lock_label.configure(
            text=f"✅ 已保存 {self._saved_count} 条（最后：{name}）", text_color=_C_OK)
        self._clear_form()
        self.name_entry.focus_set()

    def _box(self, key) -> str:
        box = self._boxes.get(key)
        return box.get("1.0", "end").strip() if box else ""

    # ------------------------------------------------------------------ #
    # 录入时自动推荐标签（2026-09-14，阶段 3）
    #   与主窗口同一套引擎与交互：**推荐结果直接加入已选标签（＝"默认全选"）**，
    #   用户点 chip 上的 "×" 去掉不要的即可；已存在的标签不重复添加（只追加、不覆盖）。
    # ------------------------------------------------------------------ #
    def _suggest_ctx_names(self) -> list:
        """推荐用的分类上下文名（本窗口按"已锁定分类"取分类链）。"""
        try:
            index = tagger.build_context_index(self.db)
            return tagger.entry_context_names(index, self._effective_cat_id())
        except Exception:
            return []

    def _suggest_tags(self, from_auto: bool = False) -> None:
        """✨ 按当前表单内容自动推荐标签并**并入**已选。

        from_auto（2026-09-16 批次 12-3）：是否来自 T2 防抖自动推荐——用于「智能自动取词词库」的
        采集范围判断（默认只采集"用户主动点按钮"路径）。
        """
        texts = {"name": self.name_entry.get().strip()}
        for k in ("intro", "features", "image_desc", "prompt_cn", "prompt_en"):
            texts[k] = self._box(k)
        if not any((texts.get(k) or "").strip() for k in texts):
            messagebox.showinfo("提示", "请先填写① 风格名称或提示词，再点「✨ 推荐标签」", parent=self)
            return
        # 2026-09-17（审核 R-2）：与主窗口**共用** `tagger.run_ui_suggest` 的公共流程
        #   （读词表 → 调引擎 → 取词采集 → 来源标注）；口径不变：本窗口属"UI 单条推荐"，
        #   仍启用"全词典兜底 + 字段取词"并传入用户配置的推荐策略顺序与热点词清单。
        #   注：本窗口的取词采集回调少一个 dict_data 参数，故用 lambda 适配。
        try:
            _r = tagger.run_ui_suggest(
                self.db, texts, self._suggest_ctx_names(), from_auto=from_auto,
                collect=lambda _t, _d, _fa: self._collect_auto_words(_t, _fa))
        except Exception as exc:
            messagebox.showwarning("推荐失败", str(exc), parent=self)
            return
        names = _r["names"]
        _auto_note, _note = _r["auto_note"], _r["source_note"]
        added = [n for n in names if n not in self._tag_names]
        for n in added:
            self._tag_names.append(n)
        self._refresh_tag_chips()
        if names:
            self.lock_label.configure(text=f"✨ 已推荐 {len(names)} 个标签{_note}（新增 {len(added)} 个）{_auto_note}",
                                      text_color=_C_TAG)
        else:
            self.lock_label.configure(text="⚠ 未推荐出标签，可补充名称/提示词后再试%s" % _auto_note,
                                      text_color=_C_WARN)

    def _collect_auto_words(self, texts, from_auto: bool = False) -> str:
        """把"未命中词表/热点词"的取词候选记入自动取词词库（2026-09-16 批次 12-3）。

        与主窗口 `MainWindow._collect_auto_words` 同一逻辑（两处均为"UI 单条推荐"路径）；
        返回追加到提示语末尾的一句话；**任何异常都不影响推荐主流程**。
        """
        try:
            from .. import auto_words
            cfg = auto_words.load_cfg(self.db)
            if not cfg.get("enabled") or (from_auto and not cfg.get("include_t2")):
                return ""
            _cands = tagger_engine.field_candidates(
                texts, ("name", "prompt_cn", "prompt_en"), 24,
                tagger_engine.build_vocab(tagger.load_dict(self.db)),
                self.db.list_hotwords())
            if not _cands:
                return ""
            _res = auto_words.record(self.db,
                                     [(c["word"], c["field"]) for c in _cands],
                                     entry_id=None)
            if _res.get("hot"):
                return "；🧠 自动取词 %d 词已升热点词" % len(_res["hot"])
            if _res.get("ready_tag"):
                return "；🧠 %d 词待审核加入词表" % len(_res["ready_tag"])
            return ""
        except Exception:
            return ""

    def _on_name_committed(self, _event=None) -> None:
        """①名称框回车/失焦 → 自动推荐一次（T1；同名不重复推荐）。"""
        try:
            name = self.name_entry.get().strip()
        except Exception:
            return
        if not name or name == getattr(self, "_rec_last_name", ""):
            return
        self._rec_last_name = name
        self._suggest_tags()

    # ---- T2：⑧⑨ 输入停止后自动推荐（2026-09-15 批次 8-D，设置开关默认关）---- #
    def _on_prompt_typed(self, _event=None) -> None:
        """⑧⑨ 输入后防抖自动推荐（与主窗口同一开关与时长）。"""
        if not getattr(self, "_auto_tag_suggest", False):
            return
        try:
            if self._suggest_timer is not None:
                self.after_cancel(self._suggest_timer)
        except Exception:
            pass
        self._suggest_timer = self.after(800, self._suggest_tags_debounced)

    def _suggest_tags_debounced(self) -> None:
        """防抖到点：仅在有内容时推荐（自动路径**不弹提示框**，避免打断录入）。"""
        self._suggest_timer = None
        try:
            if not (self.name_entry.get().strip()
                    or any((self._box(k) or "").strip() for k in self._boxes)):
                return
        except Exception:
            return
        self._suggest_tags(from_auto=True)   # 2026-09-16（批次 12-3）：标记来源为 T2 自动推荐

    def _pick_tags_dialog(self) -> None:
        """「📋 选择…」：从候选池（已用标签 ∪ 词表 ∪ 热点词）多选/新建标签并并入已选。

        2026-09-15 18:30（批次 9，用户确认 C18 路线①）：结果只 append 到 `self._tag_names`
        并重绘 chip；**落库仍走既有 `_save()` → `db.set_entry_tags`**，不新增写库方法。
        """
        from .main_window import _ListPickDialog   # 延迟导入：main_window 已导入本模块，避免循环导入
        try:
            pool = tagger.tag_name_pool(self.db)
        except Exception:
            pool = []
        opts = [{"value": n} for n in pool if n not in (self._tag_names or [])]
        if not opts:
            messagebox.showinfo("提示", "没有可选标签（可点「✨ 推荐标签」或直接在输入框新建）",
                                parent=self)
            return
        dlg = _ListPickDialog(self, "选择 · 标签", opts, [], multi=True, allow_new=True)
        self.wait_window(dlg)
        if not getattr(dlg, "confirmed", False):
            return
        for n in list(dlg.result):
            s = str(n or "").strip()
            if s and s not in self._tag_names:
                self._tag_names.append(s)
        self._refresh_tag_chips()

    def _refresh_tag_chips(self) -> None:
        """重绘已选标签 chip 行（点 "×" 移除）"""
        row = getattr(self, "_tag_chips_row", None)
        if row is None or not row.winfo_exists():
            return
        for w in row.winfo_children():
            w.destroy()
        if not getattr(self, "_tag_names", None):
            ctk.CTkLabel(row, text="（暂无标签；点「✨ 推荐标签」自动推荐）",
                         text_color="#9aa4b1", font=("Microsoft YaHei", 10)).pack(side="left")
            return
        for n in self._tag_names:
            color = _tag_color(n)
            chip = ctk.CTkFrame(row, fg_color=color, corner_radius=11)
            chip.pack(side="left", padx=(0, 6), pady=2)
            ctk.CTkLabel(chip, text=n, text_color="#ffffff", font=("Microsoft YaHei", 10)
                         ).pack(side="left", padx=(8, 2), pady=2)
            ctk.CTkButton(chip, text="×", width=20, height=20, fg_color=color,
                          hover_color="#8a94a6", text_color="#ffffff",
                          font=("Microsoft YaHei", 10),
                          command=lambda s=n: self._remove_tag(s)
                          ).pack(side="left", padx=(0, 3), pady=2)

    def _add_tag_from_entry(self) -> None:
        """标签输入框回车 → 加入"""
        n = self._tag_entry.get().strip()
        if n and n not in self._tag_names:
            self._tag_names.append(n)
        try:
            self._tag_entry.delete(0, "end")
        except Exception:
            pass
        self._refresh_tag_chips()

    def _remove_tag(self, name: str) -> None:
        """点 chip 上的 "×" → 移除该标签"""
        if name in self._tag_names:
            self._tag_names.remove(name)
            self._refresh_tag_chips()

    def _clear_form(self) -> None:
        self.name_entry.delete(0, "end")
        for box in self._boxes.values():
            box.delete("1.0", "end")
        # 2026-09-14（阶段 3）：清空标签与"已按名称推荐过"的记录，便于连续录入
        self._tag_names = []
        self._rec_last_name = ""
        try:
            self._tag_entry.delete(0, "end")
        except Exception:
            pass
        self._refresh_tag_chips()
        # 2026-08-19：清空表单后，⑧/⑨ 提示词文本框恢复默认 6 行高度
        for key in _PROMPT_KEYS:
            box = self._boxes.get(key)
            if box:
                box.configure(height=_PROMPT_EMPTY_H)

    @staticmethod
    def _content_fit_height(box) -> int:
        """文本框恰好显示全部内容的像素高度（含自动换行；最少 1 行）。

        2026-08-19 新增；2026-09-17（审核 R-1）：实现统一到 `ui_common.content_fit_height`
        （与主窗口共用同一份），此处仅保留方法名以兼容既有调用点。
        """
        return _cfh_common(box)

    def _fit_prompt_box(self, box) -> None:
        """⑧/⑨ 提示词文本框自适应高度：有内容按实际显示行数；无内容恢复 6 行（120px）。

        2026-08-19 新增；2026-09-17（审核 R-1）：实现统一到 `ui_common.fit_prompt_box`。
        """
        _fit_prompt_box_common(box, _PROMPT_EMPTY_H)

    def _open_image_plan(self, box) -> None:
        """打开"⑩图像获取方案"文本框中的链接（2026-08-19，与主界面一致）。

        实时读取文本框当前内容，提取第一个 http(s) 链接并用默认浏览器打开；
        未找到链接时给出轻提示。输入网址后点击按钮即可直接打开测试（无需先保存）。
        """
        text = box.get("1.0", "end").strip() if box else ""
        m = re.search(r"https?://[^\s\"'<>]+", text)
        if m:
            webbrowser.open(m.group(0))
        else:
            try:
                self.master.toast("未找到链接（请输入 http:// 或 https:// 开头网址）",
                                  color=_C_DANGER)
            except Exception:
                messagebox.showinfo("提示", "未找到链接（请输入 http:// 或 https:// 开头网址）")

    def _close(self) -> None:
        # 2026-09-15 17:15（批次 8-D）：关闭窗口前取消"未到点"的自动推荐防抖定时器，
        #   避免回调打到已销毁的控件上。
        try:
            if getattr(self, "_suggest_timer", None) is not None:
                self.after_cancel(self._suggest_timer)
        except Exception:
            pass
        self._suggest_timer = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
