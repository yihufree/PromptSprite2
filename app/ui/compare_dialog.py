# -*- coding: utf-8 -*-
"""
compare_dialog.py - 数据比对（2026-09-08 V1.7.0 新增，只读诊断）

与一个备份库（*.db，含自动备份/手动快照）对比当前库，直观显示差异：
  - 分类差异（备份独有 = 当前已删；当前新增）
  - 根目录/项目类别差异
  - 条目差异（按"详情内容键 content_key"：
      备份独有 = 当前库中已不存在；当前独有 = 备份后新增）
全程只读：备份库先复制到临时文件再打开，绝不写当前主库。
可"导出差异报告（HTML）"另存。
"""
import os
import shutil
import tempfile
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from ..database import Database


# ---------------------------------------------------------------------- #
# 差异计算（只读）
# ---------------------------------------------------------------------- #
# 2026-09-11 09:27（用户要求 3）：比对增强的三项
#  - A：识别"条目内容已修改 / 分类位置变动 / 分类疑为改名"，减少"删除+新增"噪音；
#  - B：比对 项目类别/根目录/一级分类/二级分类 四个层级的相对顺序差异；
#  - C：把条目多位置关联（entry_links）变化纳入比对。
# 参与"内容修改"判定的字段（中文名；与 Database._CONTENT_FIELDS 对应，另含收藏状态）
_DIFF_FIELDS = (
    ("intro", "介绍"), ("origin", "溯源"), ("features", "核心特征"),
    ("scenes", "应用场景"), ("works", "代表作"), ("image_desc", "代表高清配图"),
    ("prompt_cn", "中文提示词"), ("prompt_en", "英文提示词"),
    ("image_plan", "图像获取方案"), ("is_favorite", "收藏"),
)
_UNASSIGNED = "（未分配）"   # "未分配"视图在顺序比对中的父级标签
_NO_CAT = "未分类"           # 条目无任何位置时的占位


def _category_chain_map(db) -> dict:
    """全部分类：分类 id → 完整名称链文本（[一级…自身]；2026-09-09 P2-15：改以 id 为键，
    避免不同父级下的同名分类互相覆盖导致比对失真）"""
    rows = db.conn.execute("SELECT * FROM categories").fetchall()
    rows = {r["id"]: dict(r) for r in rows}
    out = {}
    for cid, c in rows.items():
        chain = []
        cur = c
        while cur:
            chain.append(cur["name"])
            cur = rows.get(cur["parent_id"])
        out[cid] = " / ".join(chain[::-1])
    return out


def _chain_text_of(chain_map: dict, category_id) -> str:
    """由"分类 id → 链文本"映射取链文本（2026-09-11 09:27：改为一次性读表后再查，避免逐条查询）"""
    return chain_map.get(category_id, "") if category_id else ""


def _entry_rows(db, chain_map: dict) -> dict:
    """条目：content_key → (名称, 主位置链文本, 位置链集合, 位置 id 集合, 条目行)。

    2026-09-11 09:27（用户要求 3-C）：新增"位置链集合 + 位置 id 集合"，用于比对多位置关联变化；
    两者配合可区分"关联位置真的变了"（id 变了）与"仅所在分类改名导致链文本变了"（id 未变）。
    """
    out = {}
    for e in db.list_all_entries():
        path = _chain_text_of(chain_map, e.get("category_id")) or _NO_CAT
        cids = db.list_entry_locations(e["id"])
        locs = {(_chain_text_of(chain_map, c) or _NO_CAT) for c in cids}
        out[Database.content_key(e)] = (e["name"], path, frozenset(locs or {_NO_CAT}),
                                       frozenset(cids), e)
    return out


def _changed_fields(old_row, new_row) -> str:
    """两行条目中发生变化的字段中文名（供"内容已修改"备注；无变化返回空串）"""
    def _val(row, key):
        v = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
        return "" if v is None else str(v)
    return "、".join(label for key, label in _DIFF_FIELDS
                     if _val(old_row, key) != _val(new_row, key))


def _order_groups(db) -> dict:
    """各层级界面的可见顺序：{(层级, 父级标签): [名称, ...]}（2026-09-11 09:27 用户要求 3-B）"""
    groups = {}
    projects = db.list_projects()
    groups[("项目类别", "")] = [p["name"] for p in projects]
    for p in projects:
        names = [d["name"] for d in db.list_domains(project_id=p["id"])]
        if names:
            groups[("根目录", p["name"])] = names
    names = [d["name"] for d in db.list_unassigned_domains()]
    if names:
        groups[("根目录", _UNASSIGNED)] = names
    for d in db.list_domains():
        names = [c["name"] for c in db.list_categories(domain_id=d["id"], parent_id=None)]
        if names:
            groups[("一级分类", d["name"])] = names
    chain_map = _category_chain_map(db)
    for row in db.conn.execute("SELECT id FROM categories").fetchall():
        names = [c["name"] for c in db.list_categories(parent_id=row["id"])]
        if names:
            groups[("二级分类", _chain_text_of(chain_map, row["id"]))] = names
    return groups


def _split_parent(chain: str):
    """拆分"父链 / 末级名"；一级分类父链为空串"""
    parts = chain.split(" / ")
    return " / ".join(parts[:-1]), parts[-1]


def _cat_rows(cur_cats: set, bak_cats: set) -> list:
    """分类差异：先配对"位置变动 / 疑为改名"，剩余才报删除与新增（2026-09-11 09:27 要求 3-A）"""
    rows = []
    rem_bak, rem_cur = sorted(bak_cats - cur_cats), sorted(cur_cats - bak_cats)
    # 1) 位置变动：末级名相同、完整链不同（同名同链属"相同"，不会进入本列表）
    for b in list(rem_bak):
        leaf = _split_parent(b)[1]
        cands = [c for c in rem_cur if _split_parent(c)[1] == leaf]
        if len(cands) == 1:
            rows.append(("分类（位置变动）", leaf, f"{b} → {cands[0]}"))
            rem_bak.remove(b)
            rem_cur.remove(cands[0])
    # 2) 疑为改名：同一父级下"备份独有 1 个 + 当前独有 1 个"时才配对（避免猜错）
    bak_p_cnt, cur_p_cnt = {}, {}
    for chain in rem_bak:
        bak_p_cnt[_split_parent(chain)[0]] = bak_p_cnt.get(_split_parent(chain)[0], 0) + 1
    for chain in rem_cur:
        cur_p_cnt[_split_parent(chain)[0]] = cur_p_cnt.get(_split_parent(chain)[0], 0) + 1
    for b in list(rem_bak):
        parent = _split_parent(b)[0]
        if bak_p_cnt.get(parent) != 1 or cur_p_cnt.get(parent) != 1:
            continue
        cands = [c for c in rem_cur if _split_parent(c)[0] == parent]
        if len(cands) == 1:
            rows.append(("分类（疑为改名，仅供参考）", f"{_split_parent(b)[1]} → {cands[0].split(' / ')[-1]}",
                         parent or "（一级分类）"))
            rem_bak.remove(b)
            rem_cur.remove(cands[0])
    # 3) 剩余：确认为删除 / 新增
    for b in rem_bak:
        rows.append(("分类（备份有/当前无）", _split_parent(b)[1], b))
    for c in rem_cur:
        rows.append(("分类（当前新增）", _split_parent(c)[1], c))
    return rows


def compare_databases(db_current, db_backup) -> dict:
    """对比当前库与备份库，返回结构化差异。"""
    cur_map, bak_map = _category_chain_map(db_current), _category_chain_map(db_backup)
    cur_cats = set(cur_map.values())
    bak_cats = set(bak_map.values())
    # 根目录/项目类别
    cur_doms = {d["name"] for d in db_current.list_domains()}
    bak_doms = {d["name"] for d in db_backup.list_domains()}
    cur_prj = {p["name"] for p in db_current.list_projects()}
    bak_prj = {p["name"] for p in db_backup.list_projects()}

    cur_entries = _entry_rows(db_current, cur_map)
    bak_entries = _entry_rows(db_backup, bak_map)
    key_cur = set(cur_entries)
    key_bak = set(bak_entries)
    # "名称 + 主位置" → content_key（用于识别"同一位置同名条目内容被改"）
    bak_by_name = {(nm, path): k for k, (nm, path, _l, _i, _e) in bak_entries.items()}

    def _row(kind, name, extra):
        return (kind, name, extra)

    rows = []
    # 分类（含位置变动/疑为改名配对）
    rows.extend(_cat_rows(cur_cats, bak_cats))
    for name in sorted(bak_doms - cur_doms):
        rows.append(_row("根目录（备份有/当前无）", name, ""))
    for name in sorted(cur_doms - bak_doms):
        rows.append(_row("根目录（当前新增）", name, ""))
    for name in sorted(bak_prj - cur_prj):
        rows.append(_row("项目类别（备份有/当前无）", name, ""))
    for name in sorted(cur_prj - bak_prj):
        rows.append(_row("项目类别（当前新增）", name, ""))
    # 条目：C) 内容相同但位置集合不同 → 位置关联变化
    #   条件要求"位置 id 集合"也变化：仅因所在分类改名/移动导致链文本变化时（id 未变）不误报。
    for key in sorted(key_cur & key_bak):
        nm, path, locs_cur, ids_cur, _e = cur_entries[key]
        _nm2, _p2, locs_bak, ids_bak, _e2 = bak_entries[key]
        if locs_cur != locs_bak and ids_cur != ids_bak:
            rows.append(_row("条目（位置关联变化）", nm,
                             f"位置：{'、'.join(sorted(locs_bak))} → {'、'.join(sorted(locs_cur))}"))
    # 条目：A) 名称一致但内容不同 → 内容已修改（避免报成删除+新增）
    #   先按"名称+主位置"精确配对；若主位置链也变了（如所在分类改名/移动），
    #   仅在"该名称在两侧的独有集合中各只出现 1 次"时才兜底配对，避免误配。
    used_bak = set()
    cur_only_keys = sorted(key_cur - key_bak)
    bak_only_keys = sorted(key_bak - key_cur)
    cur_name_cnt, bak_name_cnt = {}, {}
    for k in cur_only_keys:
        cur_name_cnt[cur_entries[k][0]] = cur_name_cnt.get(cur_entries[k][0], 0) + 1
    for k in bak_only_keys:
        bak_name_cnt[bak_entries[k][0]] = bak_name_cnt.get(bak_entries[k][0], 0) + 1

    def _pair_backup(nm, path):
        bk = bak_by_name.get((nm, path))
        if bk and bk not in used_bak:
            return bk
        if cur_name_cnt.get(nm) != 1 or bak_name_cnt.get(nm) != 1:
            return None
        cands = [k for k in bak_only_keys if bak_entries[k][0] == nm and k not in used_bak]
        return cands[0] if len(cands) == 1 else None

    for key in cur_only_keys:
        nm, path, _l, _i, e = cur_entries[key]
        bk = _pair_backup(nm, path)
        if bk is not None:
            used_bak.add(bk)
            changes = _changed_fields(bak_entries[bk][4], e)
            path_bak = bak_entries[bk][1]
            loc = path if path_bak == path else f"{path_bak} → {path}"
            rows.append(_row("条目（内容已修改）", nm,
                             f"{loc}｜变化字段：{changes or '内容字段'}"))
        else:
            rows.append(_row("条目（当前新增）", nm, path))
    for key in bak_only_keys:
        if key in used_bak:
            continue
        nm, path, _l, _i, _e = bak_entries[key]
        rows.append(_row("条目（备份有/当前无）", nm, path))
    # B) 顺序差异：只比两库共有的项，避免与新增/删除混淆
    cur_groups, bak_groups = _order_groups(db_current), _order_groups(db_backup)
    for (level, parent), names_cur in cur_groups.items():
        names_bak = bak_groups.get((level, parent))
        if not names_bak:
            continue
        common_cur = [n for n in names_cur if n in set(names_bak)]
        common_bak = [n for n in names_bak if n in set(names_cur)]
        if len(common_cur) >= 2 and common_cur != common_bak:
            rows.append(_row(f"顺序差异（{level}）", parent or "（顶层）",
                             f"共同项 {len(common_cur)} 个，顺序不一致"))

    cnt = {}
    for kind, _n, _e in rows:
        cnt[kind] = cnt.get(kind, 0) + 1
    return {
        "rows": rows,
        "total_current_entries": len(cur_entries),
        "total_backup_entries": len(bak_entries),
        "cat_del": cnt.get("分类（备份有/当前无）", 0),
        "cat_add": cnt.get("分类（当前新增）", 0),
        "cat_mov": cnt.get("分类（位置变动）", 0) + cnt.get("分类（疑为改名，仅供参考）", 0),
        "dom_del": cnt.get("根目录（备份有/当前无）", 0),
        "dom_add": cnt.get("根目录（当前新增）", 0),
        "entry_del": cnt.get("条目（备份有/当前无）", 0),
        "entry_add": cnt.get("条目（当前新增）", 0),
        "entry_mod": cnt.get("条目（内容已修改）", 0),
        "entry_loc": cnt.get("条目（位置关联变化）", 0),
        "order_diff": sum(v for k, v in cnt.items() if k.startswith("顺序差异")),
    }


# ---------------------------------------------------------------------- #
# 对话框
# ---------------------------------------------------------------------- #
class CompareDialog(ctk.CTkToplevel):
    """数据比对对话框：选备份库 → 显示差异 → 可导出 HTML 报告。"""

    def __init__(self, master, db):
        super().__init__(master)
        self.master = master
        self.db = db
        self.result = None
        self._tmp_dir = None
        self._rows = []
        self._report_meta = {}

        self.title("数据比对（与备份 *.db）")
        self.geometry("900x560")
        self.transient(master)
        self.grab_set()

        self._build()
        self._choose_file()

    def _build(self) -> None:
        pad = 14
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkLabel(self, text="数据比对（只读，不修改任何数据）",
                            font=("Microsoft YaHei", 15, "bold"))
        head.grid(row=0, column=0, padx=pad, pady=(14, 4), sticky="w")
        self.lbl_summary = ctk.CTkLabel(self, text="请选择要对比的备份库文件…",
                                        font=("Microsoft YaHei", 13), justify="left",
                                        anchor="w", text_color="#1f2937")
        self.lbl_summary.grid(row=1, column=0, padx=pad, pady=(0, 6), sticky="ew")

        # 明细表
        cols = ("kind", "name", "path")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        self.tree.heading("kind", text="差异类型")
        self.tree.heading("name", text="名称")
        self.tree.heading("path", text="位置 / 备注")
        self.tree.column("kind", width=210, anchor="w")
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("path", width=340, anchor="w")
        self.tree.grid(row=2, column=0, padx=pad, pady=(0, 8), sticky="nsew")
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        vsb.grid(row=2, column=1, sticky="ns", pady=(0, 8))
        self.tree.configure(yscrollcommand=vsb.set)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=2, sticky="e", padx=pad, pady=(0, 14))
        ctk.CTkButton(btns, text="导出差异报告(HTML)", width=150,
                      command=self._export_html).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="重新选择备份…", width=120,
                      fg_color="#8a94a6", command=self._choose_file).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="关闭", width=90,
                      command=self._close).pack(side="left", padx=6)

    # ------------------------------------------------------------------ #
    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要对比的备份库（*.db）", parent=self,
                                          filetypes=[("SQLite 数据库", "*.db"), ("所有文件", "*.*")])
        if not path:
            if not self._rows:
                self._close()
            return
        self._run_compare(path)

    def _run_compare(self, backup_path: str) -> None:
        # 复制到临时文件再只读打开，避免占用冲突与意外写入
        try:
            if self._tmp_dir:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = tempfile.mkdtemp(prefix="ps_compare_")
            tmp = os.path.join(self._tmp_dir, "backup_copy.db")
            shutil.copy2(backup_path, tmp)
            db2 = Database(tmp)
            try:
                diff = compare_databases(self.db, db2)
            finally:
                db2.close()
        except Exception as exc:
            messagebox.showerror("比对失败", f"无法读取备份库：{exc}", parent=self)
            return
        self._rows = diff["rows"]
        # 2026-09-11 09:27（用户要求 3）：新增 修改/位置变化/移动改名/顺序差异 计数；
        # 并修复既有键名冲突——原先"backup"同时存放文件名与备份条目总数，导致摘要里文件名被数字覆盖。
        self._report_meta = {
            "backup_file": os.path.basename(backup_path),
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "current": diff["total_current_entries"],
            "backup_total": diff["total_backup_entries"],
            "cat_del": diff["cat_del"], "cat_add": diff["cat_add"], "cat_mov": diff["cat_mov"],
            "dom_del": diff["dom_del"], "dom_add": diff["dom_add"],
            "entry_del": diff["entry_del"], "entry_add": diff["entry_add"],
            "entry_mod": diff["entry_mod"], "entry_loc": diff["entry_loc"],
            "order_diff": diff["order_diff"],
        }
        m = self._report_meta
        self.lbl_summary.configure(text=(
            f"对比备份库：{m['backup_file']}    生成时间：{m['at']}\n"
            f"条目总数 当前 {m['current']} / 备份 {m['backup_total']}\n"
            f"条目差异：新增 {m['entry_add']}、删除 {m['entry_del']}、"
            f"内容修改 {m['entry_mod']}、位置关联变化 {m['entry_loc']}\n"
            f"分类：新增 {m['cat_add']} / 删除 {m['cat_del']} / 移动或改名 {m['cat_mov']}；"
            f"根目录：新增 {m['dom_add']} / 删除 {m['dom_del']}；"
            f"顺序差异 {m['order_diff']} 处（共 {len(self._rows)} 行）"))
        for item in self.tree.get_children():
            self.tree.delete(item)
        for kind, name, path in self._rows:
            self.tree.insert("", "end", values=(kind, name, path))

    def _export_html(self) -> None:
        if not self._rows:
            messagebox.showinfo("提示", "当前没有可导出的差异。", parent=self)
            return
        out = filedialog.asksaveasfilename(
            title="另存为差异报告", parent=self, defaultextension=".html",
            initialfile="data_diff_report.html", filetypes=[("HTML", "*.html")])
        if not out:
            return
        m = self._report_meta
        tr = "".join(
            f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>"
            for a, b, c in self._rows)
        html = (
            "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            "<title>数据比对报告</title>"
            "<style>body{font-family:'Microsoft YaHei';margin:24px}"
            "table{border-collapse:collapse;width:100%}"
            "th,td{border:1px solid #ccc;padding:4px 8px;font-size:13px;text-align:left}"
            "th{background:#eef2f7}</style></head><body>"
            f"<h2>PromptSprite 数据比对报告</h2>"
            f"<p>生成时间：{m['at']}<br>对比备份库：{m['backup_file']}"
            f"<br>条目总数：当前 {m['current']} / 备份 {m['backup_total']}"
            f"<br>条目差异：新增 {m['entry_add']}、删除 {m['entry_del']}、"
            f"内容修改 {m['entry_mod']}、位置关联变化 {m['entry_loc']}"
            f"<br>分类：新增 {m['cat_add']} / 删除 {m['cat_del']} / 移动或改名 {m['cat_mov']}；"
            f"根目录：新增 {m['dom_add']} / 删除 {m['dom_del']}；"
            f"顺序差异 {m['order_diff']} 处（共 {len(self._rows)} 行）</p>"
            "<table><tr><th>差异类型</th><th>名称</th><th>位置/备注</th></tr>"
            f"{tr}</table></body></html>")
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.toast_msg = messagebox.askyesno(
            "导出成功", f"差异报告已保存：{out}\n是否立即打开查看？", parent=self)
        if self.toast_msg:
            try:
                webbrowser.open(out)
            except Exception:
                pass

    def _close(self) -> None:
        if self._tmp_dir:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
