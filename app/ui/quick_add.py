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
from ..models import Entry

_HOVER_MS = 200  # 悬停锁定判定时长（秒级换算：0.2 秒）

# "新增"辅助按钮样式 / 选中态样式（2026-09-09 P2-12：与主窗口共用公共样式）
from .ui_common import ADD_BTN_STYLE as _ADD_STYLE
from .ui_common import SEL_BTN_STYLE as _SEL_STYLE
from .ui_common import install_edit_capability as _enable_text_undo  # 2026-09-09：文本框撤销/重做


class _Tip:
    """轻量悬停提示（2026-08-29：窄列长名称查看完整内容，与主窗口一致）"""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _e=None):
        if self._tip is not None:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        tk.Label(self._tip, text=self.text, justify="left", bg="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=420, padx=8, pady=6,
                 font=("Microsoft YaHei", 10)).pack()
        self._tip.update_idletasks()
        w, h = self._tip.winfo_reqwidth(), self._tip.winfo_reqheight()
        sw, sh = self.widget.winfo_screenwidth(), self.widget.winfo_screenheight()
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        if x + w > sw:
            x = max(sw - w - 8, 0)
        if y + h > sh:
            y = max(self.widget.winfo_rooty() - h - 4, 0)
        self._tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def _maybe_tooltip(btn, name: str, budget: int) -> None:
    """长名称悬停提示：名称长度超过列宽预算时附加提示（与主窗口 _attach_nav_tooltip 一致）"""
    if len(name) > budget:
        _Tip(btn, name)

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


class QuickAddWindow(ctk.CTkToplevel):
    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.master = master
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
        self.geometry("1200x700")  # 2026-08-29：增加"项目类别"列，窗口加宽
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

        # 底部按钮区：固定不随输入区滚动
        footer = ctk.CTkFrame(form_root, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew")
        ctk.CTkButton(footer, text="💾 保存", width=110, fg_color="#2E8B57",
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
        ctk.CTkButton(self.p_frame, text="➕ 新增项目类别", height=28, **_ADD_STYLE,
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
        ctk.CTkButton(self.l0_frame, text="➕ 新增根目录", height=28, **_ADD_STYLE,
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
        ctk.CTkButton(self.l1_frame, text="➕ 新增一级分类", height=28, **_ADD_STYLE,
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
        ctk.CTkButton(self.l2_frame, text="➕ 新增二级分类", height=28, **_ADD_STYLE,
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
        self.lock_label.configure(text=f"✔ 已锁定：{name}", text_color="#2E8B57")
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
        return self._active_cat_id

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
        self.db.add_entry(e)
        self._saved_count += 1
        self.master.refresh_domains(silent=True)  # 2026-08-18（P1-2）：静默刷新，不打断主窗口连续录入
        self.lock_label.configure(
            text=f"✅ 已保存 {self._saved_count} 条（最后：{name}）", text_color="#2E8B57")
        self._clear_form()
        self.name_entry.focus_set()

    def _box(self, key) -> str:
        box = self._boxes.get(key)
        return box.get("1.0", "end").strip() if box else ""

    def _clear_form(self) -> None:
        self.name_entry.delete(0, "end")
        for box in self._boxes.values():
            box.delete("1.0", "end")
        # 2026-08-19：清空表单后，⑧/⑨ 提示词文本框恢复默认 6 行高度
        for key in _PROMPT_KEYS:
            box = self._boxes.get(key)
            if box:
                box.configure(height=_PROMPT_EMPTY_H)

    @staticmethod
    def _content_fit_height(box) -> int:
        """文本框恰好显示全部内容的像素高度（含自动换行；最少 1 行）。

        2026-08-19：与主界面同一套算法——用字体测量估算 wrap 后的实际显示行数，
        不依赖控件布局时机（displaylines 在未布局时不可靠）。
        """
        try:
            import tkinter.font as tkfont
            font = tkfont.Font(root=box._textbox, font=box._textbox.cget("font"))
            line_h = font.metrics("linespace") or 20
            text = box._textbox.get("1.0", "end-1c")
            # 文本可用宽度：控件宽扣除内边距/边框/右侧滚动条余量（取偏小值→行数略多，保证不遮挡）
            avail = max(box._textbox.winfo_width() - 14, 80)
            lines = 0
            for para in text.split("\n"):
                w = font.measure(para)
                lines += max(1, -(-w // avail))  # 向上取整：该段落自动换行后的显示行数
            n = max(int(lines), 1)
        except Exception:
            try:  # 兜底：按逻辑行数估算
                n = max(box._textbox.get("1.0", "end-1c").count("\n") + 1, 1)
            except Exception:
                n = 6
            line_h = 20
        return n * line_h + 8  # 8px 余量：上下内边距与边框，确保最后一行完整可见

    def _fit_prompt_box(self, box) -> None:
        """⑧/⑨ 提示词文本框自适应高度：有内容按实际显示行数；无内容恢复 6 行（120px）。

        2026-08-19：输入内容后有多少行就显示多少行；清空后依然显示 6 行。
        """
        if not box:
            return
        has_content = bool(box._textbox.get("1.0", "end-1c").strip())
        box.configure(height=self._content_fit_height(box) if has_content
                      else _PROMPT_EMPTY_H)

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
                                  color="#D9534F")
            except Exception:
                messagebox.showinfo("提示", "未找到链接（请输入 http:// 或 https:// 开头网址）")

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
