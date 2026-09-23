# -*- coding: utf-8 -*-
"""
source_tag_dialog.py - 「来源标注」对话框（阶段 1-3，2026-09-23）

用途（对应施工报告 1-3，用户决策 19 / 20）：
  区分"来自外部的资料"与"自己创造"的提示词，为条目标注三个来源字段：
    · source_type：未标定 / 外部 / 自建（取值白名单见 database.SOURCE_TYPES）
    · source_name：来源名称（下拉已有 + 允许新建）
    · source_time：来源时间（获取/入库时间，约定 YYYY-MM-DD HH:MM:SS）

界面：
  左侧：项目类别 / 根目录 / 分类 的**树形选择**（另有「🌐 全库」根项）——决定右侧的"整支"范围；
  右侧：① 统一样式（类型 / 名称 / 时间）；② 统计（库内 + 当前范围的已标 / 未标）；
        ③ 条目列表（显示当前范围条目，可**逐条修正**）。

两种写入方式：
  1. 「⚡ 整支批量设置」：按整支范围批量写入（口径与 database.batch_set_source_by_scope 一致）。
     执行前**强制备份数据库**，**失败即中止**（用户决策 20）；
  2. 「应用到选中条目」：只对列表中选中的**单条**写入（用于逐条修正）。

隔离性：本模块只读写条目的来源字段，不改动分类 / 标签 / 字段 / 备份等既有功能。
"""
import os
from datetime import datetime

import customtkinter as ctk
from tkinter import messagebox, ttk

from .. import backup
from ..database import SOURCE_TYPES, SOURCE_TYPE_LABELS
from .ui_common import C_DANGER as _C_DANGER, C_OK as _C_OK, C_WARN as _C_WARN
from .ui_common import widget_scaling  # 2026-09-23：窗口宽度按"源码像素"折算时用

# 右侧条目列表的**显示上限**（范围很大时只渲染前 N 条；统计与批量设置仍作用于全部范围）
_LIST_LIMIT = 300

_TYPE_TO_LABEL = dict(SOURCE_TYPE_LABELS)                  # external → 外部 …
_LABEL_TO_TYPE = {v: k for k, v in SOURCE_TYPE_LABELS.items()}   # 外部 → external …
# 下拉显示顺序（与施工报告一致：未标定 / 外部 / 自建）
_TYPE_OPTIONS = [_TYPE_TO_LABEL.get(t, t) for t in ("unspecified", "external", "original")]
_KIND_CN = {"project": "项目类别", "domain": "根目录", "cat": "分类"}


class SourceTagDialog(ctk.CTkToplevel):
    """「来源标注」主对话框（左侧选范围 + 右侧设置/统计/逐条修正）。"""

    def __init__(self, master, db, db_path: str = "", preset_kind: str = "", preset_id=None):
        super().__init__(master)
        self.db = db                                   # 当前库（软件自带 data\\prompts.db）
        self.master = master
        # 强制备份的目标库：优先用连接自身记录的路径，保证"备份的就是正在写的库"
        self._db_path = db_path or getattr(db, "db_path", "") \
            or os.path.join(config_data_dir(), config_db_file())
        # 2026-09-23 16:05（阶段 1-3 接线，用户确认）：右键菜单入口**预选该节点范围**。
        #   kind ∈ project/domain/cat；非法或缺省 ⇒ 保持默认「🌐 全库」。
        self._preset_kind = preset_kind if preset_kind in _KIND_CN else ""
        self._preset_id = preset_id

        self.title("🏷 来源标注（整支 / 逐条）")
        self.transient(master)
        self.grab_set()
        # 尺寸按屏幕自适应（与批量打标对话框同一思路：小屏也不越出工作区）
        try:
            _sw, _sh = self.winfo_screenwidth(), self.winfo_screenheight()
        except Exception:
            _sw, _sh = 1280, 900
        _w = max(880, min(1040, int(_sw * 0.86 / max(widget_scaling(self), 1.0))))
        self.geometry("%dx%d" % (_w, max(560, min(700, int(_sh * 0.85)))))
        try:
            self.minsize(min(880, _w), 560)
        except Exception:
            pass

        self._iid_node = {}            # 范围树：iid → (kind, id)；kind ∈ all/project/domain/cat
        self._cur_kind = "all"
        self._cur_id = None
        self._sel_entry_id = None      # 条目列表当前选中项（刷新后尽量保持）
        self._build()
        self._build_tree()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        pad = 12
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # ---------------- 左：范围树 ----------------
        left = ctk.CTkFrame(self)
        left.grid(row=0, column=0, sticky="nsew", padx=(pad, 6), pady=pad)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="① 选择范围（整支）", font=("Microsoft YaHei", 12, "bold")
                     ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        tree_frame = ctk.CTkFrame(left, fg_color="transparent")
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        # 2026-09-23 15:10 修复：ttk.Treeview 无 -width 构造选项（会报 unknown option "-width"），
        #   改为建好后用 column("#0", width=...) 设定树列宽度（与 copy_move_dialog 一致）。
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self.tree.column("#0", width=250, minwidth=180, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        _sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        _sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=_sb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_scope_select)
        ctk.CTkLabel(left, text="选中的节点＝整支范围（含整棵子树）；\n"
                               "「全库」作用于全部条目（含未分类）。",
                     font=("Microsoft YaHei", 10), text_color="#9aa4b1", justify="left"
                     ).grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))

        # ---------------- 右：设置 / 统计 / 列表 ----------------
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, pad), pady=pad)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(5, weight=1)

        # ② 统一样式
        frm = ctk.CTkFrame(right)
        frm.grid(row=0, column=0, sticky="ew")
        frm.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frm, text="② 来源标注内容", font=("Microsoft YaHei", 12, "bold")
                     ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 2))
        ctk.CTkLabel(frm, text="来源类型", font=("Microsoft YaHei", 12)
                     ).grid(row=1, column=0, sticky="w", padx=(10, 8), pady=6)
        self.om_type = ctk.CTkOptionMenu(frm, width=180, values=_TYPE_OPTIONS,
                                         font=("Microsoft YaHei", 12))
        self.om_type.set(_TYPE_TO_LABEL["unspecified"])
        self.om_type.grid(row=1, column=1, sticky="w", pady=6)
        ctk.CTkLabel(frm, text="来源名称", font=("Microsoft YaHei", 12)
                     ).grid(row=2, column=0, sticky="w", padx=(10, 8), pady=6)
        self.cb_name = ctk.CTkComboBox(frm, width=280, values=self._name_options(),
                                       font=("Microsoft YaHei", 12))
        self.cb_name.set("")
        self.cb_name.grid(row=2, column=1, sticky="w", pady=6)
        ctk.CTkLabel(frm, text="来源时间", font=("Microsoft YaHei", 12)
                     ).grid(row=3, column=0, sticky="w", padx=(10, 8), pady=6)
        self.ent_time = ctk.CTkEntry(frm, width=280, font=("Microsoft YaHei", 12))
        self.ent_time.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.ent_time.grid(row=3, column=1, sticky="w", pady=6)
        ctk.CTkLabel(frm, text="格式 YYYY-MM-DD HH:MM:SS（获取/入库时间，默认当前时间）；"
                               "「来源名称」可直接输入新名称。",
                     font=("Microsoft YaHei", 10), text_color="#9aa4b1", justify="left"
                     ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))

        # 操作按钮
        bar = ctk.CTkFrame(right, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 2))
        self.btn_batch = ctk.CTkButton(bar, text="⚡ 整支批量设置（先备份）", width=190,
                                       fg_color=_C_DANGER, command=self._on_batch)
        self.btn_batch.pack(side="left")
        self.btn_single = ctk.CTkButton(bar, text="应用到选中条目", width=140,
                                        fg_color="#25639c", command=self._on_apply_selected)
        self.btn_single.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(bar, text="提示：逐条修正请先在下方列表选中一条。",
                     font=("Microsoft YaHei", 10), text_color="#9aa4b1"
                     ).pack(side="left", padx=(10, 0))

        # ③ 统计
        self.lbl_stats = ctk.CTkLabel(right, text="", font=("Microsoft YaHei", 11),
                                      anchor="w", justify="left")
        self.lbl_stats.grid(row=3, column=0, sticky="ew", pady=(4, 2))

        # ④ 条目列表
        self.lbl_list = ctk.CTkLabel(right, text="④ 条目列表", font=("Microsoft YaHei", 12, "bold"),
                                     anchor="w")
        self.lbl_list.grid(row=4, column=0, sticky="ew", pady=(2, 2))
        tbl_frame = ctk.CTkFrame(right, fg_color="transparent")
        tbl_frame.grid(row=5, column=0, sticky="nsew")
        tbl_frame.grid_rowconfigure(0, weight=1)
        tbl_frame.grid_columnconfigure(0, weight=1)
        cols = ("idx", "name", "stype", "sname", "stime")
        self.tbl = ttk.Treeview(tbl_frame, columns=cols, show="headings", selectmode="browse")
        for cid, txt, w in (("idx", "序号", 50), ("name", "条目名称", 240),
                            ("stype", "来源类型", 80), ("sname", "来源名称", 150),
                            ("stime", "来源时间", 150)):
            self.tbl.heading(cid, text=txt)
            self.tbl.column(cid, width=w, anchor=("w" if cid in ("name", "sname") else "center"),
                            stretch=(cid in ("name", "sname")))
        self.tbl.grid(row=0, column=0, sticky="nsew")
        _sb2 = ttk.Scrollbar(tbl_frame, orient="vertical", command=self.tbl.yview)
        _sb2.grid(row=0, column=1, sticky="ns")
        self.tbl.configure(yscrollcommand=_sb2.set)
        self.tbl.bind("<<TreeviewSelect>>", self._on_row_select)

        # 状态行（占底）
        self.status = ctk.CTkLabel(right, text="", font=("Microsoft YaHei", 11),
                                   text_color="#5b6b7c", anchor="w", justify="left")
        self.status.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        ctk.CTkButton(right, text="关闭", width=90, command=self.destroy
                      ).grid(row=7, column=0, sticky="e", pady=(4, 0))

    # ------------------------------------------------------------------ #
    # 范围树
    # ------------------------------------------------------------------ #
    def _build_tree(self) -> None:
        """建范围树：🌐 全库 → 项目类别 → 根目录 → 一级/二级分类（与 database 范围口径对齐）。"""
        all_iid = self.tree.insert("", "end", text="🌐 全库（全部条目）", open=True)
        self._iid_node[all_iid] = ("all", None)
        for p in self.db.list_projects():
            pid = self.tree.insert("", "end", text=p["name"], open=True)
            self._iid_node[pid] = ("project", p["id"])
            for d in self.db.list_domains(project_id=p["id"]):
                self._insert_domain(pid, d)
        # 未分配根目录（无归属，置于树根）
        for d in self.db.list_unassigned_domains():
            self._insert_domain("", d, suffix="（未分配）")
        # 2026-09-23 16:05（阶段 1-3 接线）：右键菜单预选节点优先；否则默认选中「🌐 全库」
        #   （selection_set 会触发 _on_scope_select，从而刷新统计与列表）。
        _sel = self._find_iid(self._preset_kind, self._preset_id) if self._preset_kind else None
        _sel = _sel or all_iid
        self.tree.selection_set(_sel)
        try:
            self.tree.see(_sel)
        except Exception:
            pass

    def _find_iid(self, kind: str, sid):
        """按 (kind, id) 反查范围树 iid（右键菜单预选用）；未找到返回 None。"""
        for iid, node in self._iid_node.items():
            if node == (kind, sid):
                return iid
        return None

    def _insert_domain(self, parent, d: dict, suffix: str = "") -> None:
        """插入一个根目录节点及其一级/二级分类子树。

        注：一级（l1）与二级（l2）分类在 database 的范围口径里**同为 kind="cat"**
        （见 `_scope_category_ids`：节点令牌 → 覆盖全部分类含整棵子树），故此处统一记为 "cat"。
        """
        did = self.tree.insert(parent, "end", text=f"{d['name']}{suffix}", open=True)
        self._iid_node[did] = ("domain", d["id"])
        for l1 in self.db.list_categories(domain_id=d["id"], parent_id=None):
            l1_id = self.tree.insert(did, "end", text=l1["name"], open=True)
            self._iid_node[l1_id] = ("cat", l1["id"])
            for l2 in self.db.list_categories(parent_id=l1["id"]):
                l2_id = self.tree.insert(l1_id, "end", text=l2["name"])
                self._iid_node[l2_id] = ("cat", l2["id"])

    def _on_scope_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        kind, sid = self._iid_node.get(sel[0], (None, None))
        if kind is None:
            return
        self._cur_kind, self._cur_id = kind, sid
        self._sel_entry_id = None
        self._refresh_all()
        self.status.configure(text="已切换范围，可「整支批量设置」或在下表逐条修正。",
                              text_color="#5b6b7c")

    def _scope_desc(self) -> str:
        if self._cur_kind == "all":
            return "🌐 全库（全部条目，含未分类）"
        sel = self.tree.selection()
        txt = self.tree.item(sel[0], "text") if sel else ""
        return "%s【%s】（含整棵子树）" % (_KIND_CN.get(self._cur_kind, ""), txt)

    # ------------------------------------------------------------------ #
    # 范围 → SQL（口径与 database.batch_set_source_by_scope **完全一致**）
    # ------------------------------------------------------------------ #
    def _scope_ids(self):
        """返回范围覆盖的分类 id 列表；None＝全库；[]＝空范围。"""
        return self.db._scope_category_ids(self._cur_kind, self._cur_id)

    @staticmethod
    def _scope_where(ids):
        """由分类 id 列表构造 WHERE 子句与参数（镜像 batch_set_source_by_scope 的 UPDATE 条件）。"""
        ph = ",".join("?" * len(ids))
        where = (" WHERE (category_id IN (" + ph + ") OR id IN"
                 " (SELECT entry_id FROM entry_links WHERE category_id IN (" + ph + ")))")
        return where, tuple(ids) + tuple(ids)

    def _scope_where_params(self):
        """返回 (where, params, empty)；empty=True 表示空范围（调用方直接按 0 条处理）。"""
        ids = self._scope_ids()
        if ids is None:
            return "", (), False
        if not ids:
            return None, (), True
        where, params = self._scope_where(ids)
        return where, params, False

    def _scope_count(self) -> int:
        where, params, empty = self._scope_where_params()
        if empty:
            return 0
        return self.db.conn.execute(
            "SELECT COUNT(*) FROM entries" + where, params).fetchone()[0]

    # ------------------------------------------------------------------ #
    # 刷新：范围标题 / 统计 / 条目列表
    # ------------------------------------------------------------------ #
    def _refresh_all(self) -> None:
        self._refresh_stats()
        self._refresh_list()

    def _refresh_stats(self) -> None:
        g = self.db.source_stats()                      # 库内统计（database 侧口径唯一）
        where, params, empty = self._scope_where_params()
        sc = {t: 0 for t in SOURCE_TYPES}
        n_scope = 0
        if not empty:
            n_scope = self.db.conn.execute(
                "SELECT COUNT(*) FROM entries" + where, params).fetchone()[0]
            for r in self.db.conn.execute(
                    "SELECT COALESCE(NULLIF(TRIM(source_type), ''), 'unspecified') AS st,"
                    " COUNT(*) AS n FROM entries" + where + " GROUP BY st", params):
                _st = str(r["st"] or "").strip().lower()
                sc[_st if _st in SOURCE_TYPES else "unspecified"] += int(r["n"] or 0)

        def _fmt(ex, og, un):
            return "外部 %d ｜ 自建 %d ｜ 未标定 %d（已标 %d）" % (ex, og, un, ex + og)

        self.lbl_stats.configure(
            text="库内合计 %d 条 —— %s\n当前范围「%s」共 %d 条 —— %s"
                 % (g["total"],
                    _fmt(g["external"], g["original"], g["unspecified"]),
                    self._scope_desc(), n_scope,
                    _fmt(sc["external"], sc["original"], sc["unspecified"])))

    def _refresh_list(self) -> None:
        for iid in self.tbl.get_children():
            self.tbl.delete(iid)
        where, params, empty = self._scope_where_params()
        if empty:
            self.lbl_list.configure(text="④ 条目列表（当前范围无条目）")
            return
        rows = self.db.conn.execute(
            "SELECT id, name, source_type, source_name, source_time FROM entries" + where
            + " ORDER BY updated_at DESC, id LIMIT ?",
            params + (_LIST_LIMIT,)).fetchall()
        for i, r in enumerate(rows, 1):
            _st = str(r["source_type"] or "").strip().lower()
            _st = _st if _st in SOURCE_TYPES else "unspecified"
            self.tbl.insert("", "end", iid=str(r["id"]),
                            values=(str(i), (r["name"] or "")[:40], _TYPE_TO_LABEL[_st],
                                    r["source_name"] or "", r["source_time"] or ""))
        self.lbl_list.configure(
            text="④ 条目列表（本页显示 %d 条，最多 %d 条；统计与批量设置作用于**全部**范围）"
                 % (len(rows), _LIST_LIMIT))
        # 刷新后尽量保持原选中项（便于连续逐条修正）
        if self._sel_entry_id is not None and self.tbl.exists(str(self._sel_entry_id)):
            self.tbl.selection_set(str(self._sel_entry_id))
            self.tbl.see(str(self._sel_entry_id))

    def _on_row_select(self, _event=None) -> None:
        """选中列表某行 → 把该条目**当前的来源值**回填到右侧，便于修改后应用。"""
        sel = self.tbl.selection()
        if not sel:
            return
        self._sel_entry_id = int(sel[0])
        vals = self.tbl.item(sel[0], "values")
        if not vals or len(vals) < 5:
            return
        self.om_type.set(vals[2] if vals[2] in _LABEL_TO_TYPE else _TYPE_TO_LABEL["unspecified"])
        self.cb_name.set(vals[3] or "")
        self.ent_time.delete(0, "end")
        self.ent_time.insert(0, vals[4] or "")

    # ------------------------------------------------------------------ #
    # 取值 / 写入
    # ------------------------------------------------------------------ #
    def _name_options(self) -> list:
        """来源名称下拉候选：库中已有名称（去重、非空）。"""
        try:
            return list(self.db.list_source_names() or [])
        except Exception:
            return []

    def _values(self):
        """读取右侧当前设置 → (source_type, source_name, source_time)。"""
        st = _LABEL_TO_TYPE.get(self.om_type.get(), "unspecified")
        return st, self.cb_name.get().strip(), self.ent_time.get().strip()

    def _refresh_name_options(self) -> None:
        try:
            self.cb_name.configure(values=self._name_options())
        except Exception:
            pass

    def _notify_master(self, n: int) -> None:
        """告知主窗口（仅提示，不触发重建，避免影响其他界面功能）。"""
        try:
            self.master.toast("🏷 来源标注已保存：%d 条" % n)
        except Exception:
            pass

    def _on_batch(self) -> None:
        """整支批量设置：二次确认 → **强制备份（失败即中止）** → 单事务写入 → 刷新。"""
        n = self._scope_count()
        if n == 0:
            messagebox.showinfo("当前范围无条目", "所选范围下没有条目，无需设置。", parent=self)
            return
        st, name, tm = self._values()
        if not messagebox.askyesno(
                "确认整支批量设置来源标注",
                f"范围：{self._scope_desc()}\n\n"
                f"即将把 **{n} 条**条目的来源标注统一设置为：\n"
                f"　· 来源类型：{self.om_type.get()}\n"
                f"　· 来源名称：{name or '（空）'}\n"
                f"　· 来源时间：{tm or '（空）'}\n\n"
                "· 执行前会「自动备份数据库」（备份失败则中止，不写入任何数据）；\n"
                f"· 这 {n} 条会被计入「今日变更包」（换机同步时会随包带上）。\n\n"
                "确定继续吗？", parent=self):
            return
        # —— 强制备份（用户决策 20）：失败即中止，不写入任何数据 ——
        snap = backup.source_snapshot(self._db_path)
        if not snap.get("ok"):
            messagebox.showwarning("已中止",
                                   f"来源标注前备份失败：{snap.get('error')}\n\n未写入任何数据。",
                                   parent=self)
            self.status.configure(text="⚠ 备份失败，已中止（未写入）", text_color=_C_DANGER)
            return
        self.status.configure(text="正在写入…（请稍候，勿关闭窗口）", text_color=_C_WARN)
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            cnt = self.db.batch_set_source_by_scope(self._cur_kind, self._cur_id, st, name, tm)
        except Exception as exc:
            messagebox.showwarning("执行失败", str(exc), parent=self)
            self.status.configure(text=f"⚠ 执行失败：{exc}", text_color=_C_DANGER)
            return
        self._refresh_name_options()
        self._refresh_all()
        self.status.configure(
            text="✅ 整支批量设置完成：%d 条（备份：%s）"
                 % (cnt, os.path.basename(snap.get("path") or "")), text_color=_C_OK)
        self._notify_master(cnt)

    def _on_apply_selected(self) -> None:
        """逐条修正：把右侧当前设置写入列表中**选中的那一条**。"""
        sel = self.tbl.selection()
        if not sel:
            messagebox.showinfo("未选中条目", "请先在下方「④ 条目列表」中选中一条条目。", parent=self)
            return
        try:
            eid = int(sel[0])
        except (TypeError, ValueError):
            return
        st, name, tm = self._values()
        try:
            self.db.set_entry_source(eid, st, name, tm)
        except Exception as exc:
            messagebox.showwarning("写入失败", str(exc), parent=self)
            self.status.configure(text=f"⚠ 写入失败：{exc}", text_color=_C_DANGER)
            return
        self._sel_entry_id = eid
        self._refresh_name_options()
        self._refresh_all()
        self.status.configure(text="✅ 已更新选中条目的来源标注（1 条）", text_color=_C_OK)
        self._notify_master(1)


# ---------------------------------------------------------------------- #
# 本地兜底（避免因 import 顺序造成顶层依赖）
# ---------------------------------------------------------------------- #
def config_data_dir() -> str:
    from .. import config
    return config.data_dir()


def config_db_file() -> str:
    from .. import config
    return config.DB_FILE_NAME