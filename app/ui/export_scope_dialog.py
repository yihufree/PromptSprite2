# -*- coding: utf-8 -*-
"""export_scope_dialog.py - 「导出当前分类」范围确认对话框

创建：2026-09-22（用户要求 1）

作用：
  点击「导出当前分类（JSON / Excel / HTML）」后先弹本对话框，用**整个库的结构树**
  （项目类别 → 根目录 → 一级分类 → 二级分类…）呈现导出范围，并：
    · 把用户**已选中的分支项及其全部下属**加重颜色显示（便于"确认无误"）；
    · 给出本次将导出的条目数（口径与导出核心一致，按条目 id 去重）；
    · **未选中任何级别/选项时**给出醒目提醒，并禁用「确认导出」按钮；
    · 用户点「确认导出」后才真正执行导出（返回 result=True）。

说明：本模块只读数据库，不修改任何数据；导出动作仍由主窗口执行（保持功能隔离）。
"""
import tkinter.font as tkfont
from tkinter import ttk

import customtkinter as ctk

from ..parser import json_io
from . import ui_common as _ui_common        # 2026-09-22：用于取控件缩放系数

_KIND_LABEL = {"project": "项目类别", "domain": "根目录", "cat": "分类"}

# 2026-09-22（用户要求）：本窗口**距屏幕顶端固定距离**（实际px）——保证位置恒定、
#   不随主窗口移动，也不会被屏幕下边缘遮住（不足时改为压低窗口高度）。
_SCOPE_TOP_MARGIN = 50

# 加重显示（已选分支及其下属）的配色与字体
_HL_FG = "#1f6feb"          # 蓝色（与主界面高亮同系）
_DIM_FG = "#8a94a6"         # 非选中项灰


class ExportScopeDialog(ctk.CTkToplevel):
    """导出范围确认对话框（模态）。`dlg.result` 为 True 表示用户确认导出。"""

    def __init__(self, master, db, scope: dict, fmt_label: str = "") -> None:
        super().__init__(master)
        self.db = db
        self.scope = dict(scope or {"kind": None, "id": None})
        self.fmt_label = fmt_label or ""
        self.result = False

        self.title("确认导出范围")
        self.geometry("640x620")
        self.resizable(True, True)
        self.transient(master)
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        _kind = self.scope.get("kind")
        _sel_text = self._scope_text()
        has_scope = _kind in ("project", "domain", "cat") and self.scope.get("id")

        # ---- 第 0 行：标题 ----
        ctk.CTkLabel(
            self, anchor="w", font=("Microsoft YaHei", 14, "bold"),
            text=(f"即将导出{('（' + self.fmt_label + '）') if self.fmt_label else ''}"
                  f"，请确认导出范围")
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))

        # ---- 第 1 行：未选提醒 / 已选范围 ----
        if has_scope:
            ctk.CTkLabel(
                self, anchor="w", justify="left", font=("Microsoft YaHei", 12),
                text_color=_HL_FG,
                text=f"✅ 已选层级：{_KIND_LABEL.get(_kind, _kind)}　范围：{_sel_text}\n"
                     f"　　树中【蓝色加粗】部分即本次导出的内容（含其全部下属）。",
            ).grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 4))
        else:
            ctk.CTkLabel(
                self, anchor="w", justify="left", font=("Microsoft YaHei", 12, "bold"),
                text_color="#C0392B",
                text="⚠ 尚未选择导出级别与选项！\n"
                     "　　请先关闭本窗口，在左侧目录中选中「项目类别 / 根目录 / 分类」之一\n"
                     "　　（可点工具栏「导出当前分类」重来），选中后本窗口会把该分支及其\n"
                     "　　全部下属加重显示，供你确认后再导出。",
            ).grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 4))

        # ---- 第 2 行：结构树表头 ----
        ctk.CTkLabel(
            self, anchor="w", font=("Microsoft YaHei", 11), text_color=_DIM_FG,
            text="全库结构树（括号内为该分类本级条目数）："
        ).grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 0))

        # ---- 第 3 行：树（可滚动） ----
        tree_frame = ctk.CTkFrame(self)
        tree_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=4)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="none")
        self.tree.grid(row=0, column=0, sticky="nsew")
        _sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        _sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=_sb.set)
        self._hl_ids = self._scope_cat_ids()          # 需加重显示的分类 id
        self._hl_node = (self.scope.get("kind"), self.scope.get("id"))
        self._dim_tag_ids = []
        self._build_tree()
        self._apply_tags()

        # ---- 第 4 行：范围统计 ----
        if has_scope:
            try:
                _n = self._count_scope_entries()
                _stat = f"本次将导出：{_n} 条条目（范围：{_KIND_LABEL.get(_kind, _kind)}『{_sel_text}』）"
            except Exception as exc:                      # noqa: BLE001
                _stat = f"条目数统计失败（不影响导出）：{exc}"
        else:
            _stat = "未选择范围 —— 无法导出"
        ctk.CTkLabel(self, anchor="w", font=("Microsoft YaHei", 12, "bold"),
                     text_color=(_HL_FG if has_scope else "#C0392B"), text=_stat
                     ).grid(row=4, column=0, sticky="ew", padx=16, pady=(6, 2))
        ctk.CTkLabel(self, anchor="w", font=("Microsoft YaHei", 10), text_color=_DIM_FG,
                     text="提示：全库导出（菜单「导出全部」）不经本窗口，仍是一键直接导出。"
                     ).grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 6))

        # ---- 第 6 行：底部按钮 ----
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.ok_btn = ctk.CTkButton(
            btn_row, text="确认导出", width=110, fg_color="#25639c",
            command=self._confirm, state=("normal" if has_scope else "disabled"))
        self.ok_btn.pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text="取消", width=110, command=self._cancel
                      ).pack(side="right", padx=4)

        # 2026-09-22（用户要求）：**窗口位置固定**——水平居中于屏幕、垂直**距屏幕顶端固定 50px**；
        #   不再"居中于主窗口"（原实现会让本窗口跟随主窗口移动，主窗口靠下时本窗口底部被屏幕遮住，
        #   用户得先把主窗口往上拉才能点到正文按钮，体验差）。
        #   并保证整窗始终在屏幕内：若屏幕高度放不下，则**压低窗口高度**（内部结构树可滚动，
        #   不影响使用），而不是让底部出屏。
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        _w_real = self.winfo_width()
        _h_real = self.winfo_height()
        _x = max((sw - _w_real) // 2, 0)
        _max_h_real = sh - _SCOPE_TOP_MARGIN - 24          # 屏幕内底部再留 24px 余量
        try:
            # 注意：CTkToplevel 本体不"应用"缩放（widget_scaling 会返回 1.0 而算错），
            #   必须取**窗口内子控件**的缩放系数（＝全局控件缩放，与 geometry() 同口径）。
            _scale = _ui_common.widget_scaling(self.ok_btn) or 1.0
        except Exception:                                  # noqa: BLE001
            _scale = 1.0
        if _h_real > _max_h_real:
            self.geometry("%dx%d+%d+%d" % (_w_real / _scale, _max_h_real / _scale,
                                           _x, _SCOPE_TOP_MARGIN))
        else:
            self.geometry("+%d+%d" % (_x, _SCOPE_TOP_MARGIN))
        self.lift()
        try:
            self.grab_set()
        except Exception:                                  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # 范围解析
    # ------------------------------------------------------------------ #
    def _scope_kwargs(self) -> dict:
        _kind = self.scope.get("kind")
        if _kind == "cat":
            return {"category_id": self.scope.get("id")}
        if _kind == "domain":
            return {"domain_id": self.scope.get("id")}
        if _kind == "project":
            return {"project_id": self.scope.get("id")}
        return {}

    def _scope_cat_ids(self) -> set:
        """本次导出范围内的**分类 id 集合**（用于加重显示）"""
        kw = self._scope_kwargs()
        if not kw:
            return set()
        try:
            if "category_id" in kw:
                return set(self.db._collect_category_ids(kw["category_id"]))
            _cats, _scope = json_io._scope_categories(self.db, **kw)
            return set(_scope or [])
        except Exception:                                  # noqa: BLE001
            return set()

    def _scope_text(self) -> str:
        """已选范围的显示名（分类显示完整路径）"""
        try:
            return json_io.scope_title(self.db, **self._scope_kwargs()) or ""
        except Exception:                                  # noqa: BLE001
            return ""

    def _count_scope_entries(self) -> int:
        """本次将导出的条目数（口径与导出核心一致：按条目 id 去重）"""
        kw = self._scope_kwargs()
        if not kw:
            return 0
        _cats, _scope = json_io._scope_categories(self.db, **kw)
        if _scope is None:
            return len(self.db.list_all_entries())
        seen = set()
        for cid in _scope:
            for e in self.db.list_entries(cid):
                seen.add(e["id"])
        return len(seen)

    # ------------------------------------------------------------------ #
    # 结构树
    # ------------------------------------------------------------------ #
    def _direct_counts(self) -> dict:
        """各分类「本级条目数」（主挂靠 + 仅关联，去重口径与位置语义一致）"""
        main = {r[0]: r[1] for r in self.db.conn.execute(
            "SELECT category_id, COUNT(*) FROM entries "
            "WHERE category_id IS NOT NULL GROUP BY category_id").fetchall()}
        link = {r[0]: r[1] for r in self.db.conn.execute(
            "SELECT category_id, COUNT(*) FROM entry_links "
            "GROUP BY category_id").fetchall()}
        out = {}
        for cid in set(main) | set(link):
            out[cid] = main.get(cid, 0) + link.get(cid, 0)
        return out

    def _build_tree(self) -> None:
        self._counts = self._direct_counts()
        self._node_kind = {}          # iid → (kind, oid)，供加重显示判定
        for p in self.db.list_projects():
            _pid = self.tree.insert("", "end", text=self._label("project", p["id"], p["name"]),
                                    open=True)
            self._node_kind[_pid] = ("project", p["id"])
            for d in self.db.list_domains(project_id=p["id"]):
                self._insert_domain(_pid, d)
        for d in self.db.list_unassigned_domains():
            self._insert_domain("", d, suffix="（未分配）")

    def _insert_domain(self, parent, d: dict, suffix: str = "") -> None:
        _did = self.tree.insert(parent, "end",
                                text=self._label("domain", d["id"], f"{d['name']}{suffix}"),
                                open=True)
        self._node_kind[_did] = ("domain", d["id"])
        for l1 in self.db.list_categories(domain_id=d["id"], parent_id=None):
            self._insert_category(_did, l1)

    def _insert_category(self, parent, cat: dict) -> None:
        _cid = self.tree.insert(parent, "end",
                                text=self._label("cat", cat["id"], cat["name"]), open=True)
        self._node_kind[_cid] = ("cat", cat["id"])
        for child in self.db.list_categories(parent_id=cat["id"]):
            self._insert_category(_cid, child)

    def _label(self, kind: str, oid: int, name: str) -> str:
        if kind == "cat":
            _n = self._counts.get(oid, 0)
            return f"{name}（{_n}）" if _n else name
        if kind == "project":
            _n = sum(self._counts.get(c, 0)
                     for d in self.db.list_domains(project_id=oid)
                     for c in self._all_cats_of_domain(d["id"]))
            return f"{name}（{_n}）" if _n else name
        if kind == "domain":
            _n = sum(self._counts.get(c, 0) for c in self._all_cats_of_domain(oid))
            return f"{name}（{_n}）" if _n else name
        return name

    def _all_cats_of_domain(self, domain_id: int) -> list:
        """某根目录下全部分类（一级 + 全部子级，扁平）"""
        out = []
        for l1 in self.db.list_categories(domain_id=domain_id, parent_id=None):
            out.extend(self.db._collect_category_ids(l1["id"]))
        return out

    # ------------------------------------------------------------------ #
    # 加重显示
    # ------------------------------------------------------------------ #
    def _node_highlighted(self, kind: str, oid: int) -> bool:
        """该节点是否属于"已选分支**及其下属**"（祖先不加重，只加重自身与后代）"""
        skind, sid = self._hl_node
        if not skind:
            return False
        if kind == "cat":
            return oid in self._hl_ids
        if skind == "project":
            if kind == "project":
                return oid == sid
            if kind == "domain":
                _d = self.db.get_domain(oid) or {}
                return _d.get("project_id") == sid
            return False
        if skind == "domain":
            # 根目录导出：自身加重；项目类别节点是**祖先**，不加重
            return kind == "domain" and oid == sid
        if skind == "cat":
            # 分类导出：根目录 / 项目类别节点都是祖先，不加重
            return False
        return False

    def _apply_tags(self) -> None:
        """把"已选分支及其下属"标为蓝色加粗，其余置灰"""
        try:
            _bold = tkfont.Font(root=self, family="Microsoft YaHei", size=11, weight="bold")
            self.tree.tag_configure("hl", foreground=_HL_FG, font=_bold)
        except Exception:                                  # noqa: BLE001
            self.tree.tag_configure("hl", foreground=_HL_FG)
        self.tree.tag_configure("dim", foreground=_DIM_FG)
        for iid, (kind, oid) in self._node_kind.items():
            self.tree.item(
                iid, tags=("hl",) if self._node_highlighted(kind, oid) else ("dim",))

    # ------------------------------------------------------------------ #
    # 按钮
    # ------------------------------------------------------------------ #
    def _confirm(self) -> None:
        self.result = True
        self.destroy()

    def _cancel(self) -> None:
        self.result = False
        self.destroy()
