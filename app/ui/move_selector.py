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
            self.sel_label = ctk.CTkLabel(opt, text="已选 0 个分类",
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
        ctk.CTkButton(btn_row, text="确定", width=88,
                      command=self._confirm).pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text="取消", width=88,
                      command=self._cancel).pack(side="right", padx=4)

        # 居中于主窗口
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        x = master.winfo_x() + (master.winfo_width() - w) // 2
        y = master.winfo_y() + (master.winfo_height() - h) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
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

    def _on_select(self, _e=None) -> None:
        if not hasattr(self, "sel_label"):
            return
        self.sel_label.configure(text=f"已选 {len(self.tree.selection())} 个分类")

    def _pick_uncategorized(self):
        if self.mode != "move":
            return
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
