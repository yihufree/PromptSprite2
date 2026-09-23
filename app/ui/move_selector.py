# -*- coding: utf-8 -*-
"""
move_selector.py - 条目目标分类树形选择器（2026-09-07 阶段2扩展）
创建日期：2026-08-12（阶段三：条目管理）；2026-09-07 扩展为多命令通用

三种模式（mode）：
  - 'move'：单选用；可选"移入未分类"、可选"整体转移（同时解除其它全部关联）"
  - 'link'：多选（Ctrl/Shift 或逐个），把条目"关联到"所选分类（共享，不复制）
  - 'copy'：多选，把条目"复制到"所选分类（每个目标生成独立副本）

关闭后读取结果：
  result          'ok' / 'uncategorized'（仅 move）/ 'cancel'
  selected_cat_ids  选中的分类 id 列表（已自动剔除 exclude 中的分类）
  overall        是否勾选"整体转移"（仅 move 有意义）
"""
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from . import ui_common as _ui_common        # 2026-09-23：用于取控件缩放系数
from .. import config as _config             # 2026-09-23（需求 4）：上次目标节点 meta 键

# 2026-09-23（用户要求 3）：本窗口**距屏幕顶端固定距离**（实际px）——位置恒
#   定、不随主窗口移动，也不会被屏幕下边缘遮住（不足时改为压低窗口高度）。
#   取值与 export_scope_dialog._SCOPE_TOP_MARGIN 保持一致（50）。
_TOP_MARGIN = 50

# 2026-09-23（用户要求 4）：按对话框模式**分别记忆**「上次目标节点」的 meta 键
#   （用户批复「A：按模式分别记忆」）；值为分类 id 的逗号拼接串。
_META_KEYS = {
    "move": _config.META_MOVE_TARGET_MOVE,
    "link": _config.META_MOVE_TARGET_LINK,
    "copy": _config.META_MOVE_TARGET_COPY,
}

_TITLES = {"move": "移动到分类", "link": "关联到分类（可多选）", "copy": "复制到分类（可多选）"}
_HINTS = {
    "move": "选择目标分类后点确定（分类节点可选）",
    "link": "Ctrl/Shift 点击或逐个点击可多选目标分类；已在位置的分类已标（已在）",
    "copy": "Ctrl/Shift 点击或逐个点击可多选目标分类；将为其生成独立副本",
}


class MoveSelector(ctk.CTkToplevel):
    def __init__(self, master, db, mode: str = "move", exclude=(),
                 title: str = ""):
        super().__init__(master)
        self.db = db
        self.mode = mode if mode in ("move", "link", "copy") else "move"
        self.exclude = set(exclude or ())
        self.result = "cancel"            # 'ok' / 'uncategorized' / 'cancel'
        self.selected_cat_ids: list = []
        self.overall = False
        multiselect = self.mode in ("link", "copy")

        self.title(title or _TITLES[self.mode])
        self.geometry("520x580")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 顶部提示
        hint = ctk.CTkLabel(self, text=_HINTS[self.mode], text_color="gray",
                            font=("Microsoft YaHei", 11), anchor="w", justify="left")
        hint.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))

        # 树形控件
        tree_frame = ctk.CTkFrame(self)
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self._iid_to_cat = {}
        self.tree = ttk.Treeview(
            tree_frame, show="tree",
            selectmode="extended" if multiselect else "browse")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self._build_tree()
        # 2026-09-23（用户要求 4）：建树后**定位到上次的目标节点**（无记忆/已失效则不做任何选中）
        self._restore_last_target()

        # 底部操作区
        opt = ctk.CTkFrame(self, fg_color="transparent")
        opt.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 0))
        if self.mode == "move":
            self._overall_var = tk.IntVar(value=0)
            ctk.CTkCheckBox(
                opt, text="整体转移：同时解除该条目其它全部关联（仅保留新位置）",
                font=("Microsoft YaHei", 11), variable=self._overall_var,
                text_color="#555555").pack(side="left", anchor="w")
        if multiselect:
            # 2026-09-23（用户要求 4）：初值取**当前实际选中数**——"上次目标节点"的
            #   自动选中发生在建标签之前，用 0 会与树上选中状态不一致。
            self.sel_label = ctk.CTkLabel(opt, text="已选 %d 个分类" % len(self.tree.selection()),
                                          text_color="gray",
                                          font=("Microsoft YaHei", 11))
            self.sel_label.pack(side="left", padx=6)
            self.tree.bind("<<TreeviewSelect>>", self._on_select)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="ew", padx=12, pady=(4, 12))
        if self.mode == "move":  # 仅"移动到"可移入未分类
            ctk.CTkButton(btn_row, text="📂 移入未分类", width=120,
                          fg_color="#8a94a6",
                          command=self._pick_uncategorized).pack(side="left")
        self._ok_btn = ctk.CTkButton(btn_row, text="确定", width=88,
                                     command=self._confirm)
        self._ok_btn.pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text="取消", width=88,
                      command=self._cancel).pack(side="right", padx=4)

        # 2026-09-23（用户要求 3）：**窗口位置固定**——水平居中于屏幕、垂直距屏幕顶端
        #   固定 _TOP_MARGIN px；不再"居中于主窗口"（原实现使本窗口跟随主窗口移动，
        #   主窗口靠下时本窗口底部会被屏幕遮住）。并保证整窗始终在屏幕内：屏幕高度
        #   放不下时**压低窗口高度**（内部结构树可滚动，不影响使用），而非让底部出屏。
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        _w_real = self.winfo_width()
        _h_real = self.winfo_height()
        _x = max((sw - _w_real) // 2, 0)
        _max_h_real = sh - _TOP_MARGIN - 24                 # 屏幕内底部再留 24px 余量
        try:
            # 注意：CTkToplevel 本体不"应用"缩放（widget_scaling 会返回 1.0 而算错），
            #   必须取**窗口内子控件**的缩放系数（＝全局控件缩放，与 geometry() 同口径）。
            _scale = _ui_common.widget_scaling(self._ok_btn) or 1.0
        except Exception:                                   # noqa: BLE001
            _scale = 1.0
        if _h_real > _max_h_real:
            self.geometry("%dx%d+%d+%d" % (_w_real / _scale, _max_h_real / _scale,
                                           _x, _TOP_MARGIN))
        else:
            self.geometry("+%d+%d" % (_x, _TOP_MARGIN))
        self.lift()

    def _build_tree(self) -> None:
        # 树形目标：项目类别 → 根目录 → 一级 → 二级（仅分类节点可作目标）
        for p in self.db.list_projects():
            pid = self.tree.insert("", "end", text=p["name"], open=True)
            for d in self.db.list_domains(project_id=p["id"]):
                self._insert_domain(pid, d)
        for d in self.db.list_unassigned_domains():
            self._insert_domain("", d, suffix="（未分配）")

    def _insert_domain(self, parent, d: dict, suffix: str = "") -> None:
        did = self.tree.insert(parent, "end", text=f"{d['name']}{suffix}", open=True)
        for l1 in self.db.list_categories(domain_id=d["id"], parent_id=None):
            text = l1["name"] + ("（已在）" if l1["id"] in self.exclude else "")
            l1_id = self.tree.insert(did, "end", text=text, open=True)
            self._iid_to_cat[l1_id] = l1["id"]
            for l2 in self.db.list_categories(parent_id=l1["id"]):
                text2 = l2["name"] + ("（已在）" if l2["id"] in self.exclude else "")
                l2_id = self.tree.insert(l1_id, "end", text=text2)
                self._iid_to_cat[l2_id] = l2["id"]

    # ------------------------------------------------------------------ #
    # 2026-09-23（用户要求 4）：按模式分别记住"上次目标节点"
    # ------------------------------------------------------------------ #
    def _restore_last_target(self) -> None:
        """建树后把选中/滚动位置恢复到**上次使用同一模式时选中的目标节点**。

        记忆按 `self.mode` 分别存放（move / link / copy 各 1 个 meta 键，值为分类
        id 的逗号拼接串，由 `_remember_target` 写入）。以下情形**静默不选中**：
        无记忆、分类已删除（不在树中）、分类已属条目当前位置（在 `self.exclude` 中）。
        本方法只做选中/滚动，**不改动任何结果字段**（result / selected_cat_ids
        保持默认值），因此对"取消"等既有语义零影响。
        """
        ids = self._load_last_target()
        if not ids:
            return
        cat_to_iid = {}
        for iid, cid in self._iid_to_cat.items():
            cat_to_iid.setdefault(cid, iid)
        hits = []
        for cid in ids:
            iid = cat_to_iid.get(cid)
            if iid is None or cid in self.exclude:    # 已删除 / 已在位置 → 跳过
                continue
            hits.append(iid)
        if not hits:
            return
        self.tree.selection_set(hits)
        last = hits[-1]
        parent = self.tree.parent(last)               # 依次展开父级，保证节点可见
        while parent:
            self.tree.item(parent, open=True)
            parent = self.tree.parent(parent)
        self.tree.focus(last)
        self.tree.see(last)                           # 滚动到该节点
        self._on_select()                             # 刷新"已选 N 个分类"（多选模式）

    def _load_last_target(self) -> list:
        """读取本模式的"上次目标节点"→ 分类 id 列表（无记忆/数据异常返回空列表）"""
        key = _META_KEYS.get(self.mode)
        if not key:
            return []
        try:
            raw = self.db.get_meta(key) or ""
        except Exception:                             # noqa: BLE001
            return []
        out = []
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                pass                                  # 脏数据（非整数）直接忽略
        return out

    def _remember_target(self, ids) -> None:
        """把本次确认的目标节点写回本模式的 meta（无 id 不写，不覆盖旧记忆）。"""
        key = _META_KEYS.get(self.mode)
        if not key or not ids:
            return
        try:
            self.db.set_meta(key, ",".join(str(int(i)) for i in ids))
        except Exception:                             # noqa: BLE001
            pass                                      # 记忆写入失败不影响主流程

    def _on_select(self, _e=None) -> None:
        if not hasattr(self, "sel_label"):
            return
        self.sel_label.configure(text=f"已选 {len(self.tree.selection())} 个分类")

    def _pick_uncategorized(self):
        if self.mode != "move":
            return
        # 2026-09-23（用户要求 4）："移入未分类"没有具体输出节点，**不更新记忆**
        #   （用户批复「不清除，保留上次具体分类」）——下次打开仍定位到上次那个分类。
        self.result = "uncategorized"
        self.selected_cat_ids = []
        self.overall = bool(getattr(self, "_overall_var", tk.IntVar(value=0)).get() == 1)
        self._close()

    def _confirm(self):
        sel = self.tree.selection()
        ids = [self._iid_to_cat[i] for i in sel if i in self._iid_to_cat]
        ids = [i for i in ids if i not in self.exclude]  # 已在位置自动剔除
        if self.mode == "move":
            if not ids:
                return  # move 必须单选具体分类（未分类请用左侧按钮）
            ids = ids[:1]
        if not ids:
            return
        self.result = "ok"
        self.selected_cat_ids = ids
        self.overall = bool(getattr(self, "_overall_var", tk.IntVar(value=0)).get() == 1)
        # 2026-09-23（用户要求 4）：记住本次目标节点（供下次同模式打开时自动定位）。
        #   注意写的是**剔除 exclude 后的最终目标**，与返回值口径一致。
        self._remember_target(ids)
        self._close()

    def _cancel(self):
        self.result = "cancel"
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
