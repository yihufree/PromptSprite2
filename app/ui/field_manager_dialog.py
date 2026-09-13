# -*- coding: utf-8 -*-
"""
field_manager_dialog.py - 「字段管理」对话框
创建日期：2026-09-13（第 1 期 1-A-4 起逐步扩展）

本版范围（1-A-5 第 1~3 步的上半）：
  - 内置 10 个区块（① 条目名称 ~ ⑩ 图像获取方案）：**改名**（固定保留、不可删除）；
  - **新增自定义字段**：填显示名 + 选类型 → 点「添加」即写入 field_defs（field_key 自动为 custom_N）；
  - **排序**：自定义字段行内「↑ / ↓」在自定义字段之间调整顺序（内置 10 项顺序固定）；
  - **删除 = 归档隐藏**（用户确认的语义）：字段从详情区与列表消失，但**已填写内容全部保留**，
    可在"显示已归档字段"中「恢复」。

尚未包含（留待后续小项）：
  - 已归档字段的"彻底删除"（本步只做归档与恢复）；
  - 自定义字段取值在"复制条目/回收站/变更包/导入导出"中的联动（下一步）。

生效规则：
  - 改名：点「确定」后写库并生效；点「取消」不写库；
  - 新增 / 归档 / 恢复 / 排序：点按钮**立即写库**（列表即时重建）。
"""
import customtkinter as ctk
from tkinter import messagebox, ttk

from ..database import FIELD_TYPES, _ref_token

# 「字段管理」对话框窗口：垂直固定位置＝距屏幕上边 50（2026-09-13 用户要求；
# 便于在窗口较高时仍完整可见，不随主窗口位置上下浮动）
_TOP_MARGIN = 50


def _place_top_centered(win, margin: int = _TOP_MARGIN, cascade=None) -> None:
    """把窗口放到"**屏幕水平居中**（窗口中线对齐屏幕中线）＋ 上边距 margin"的位置。

    2026-09-13（用户口径）：此前按主窗口左边缘推算（"相对主窗口居中"），要的是屏幕中线居中。
    实现要点：CustomTkinter 在高 DPI 下 `geometry("+x+y")` 的位置是**物理像素**，而窗口宽在
    "映射前（请求宽×缩放）"与"映射后（真实物理宽）"两种口径 —— 故先估算定位，
    再用 `after` 回调在映射后校正几次（误差 0~数像素）。三个对话框共用本函数。

    cascade：可选回调 `fn(attempt)`，用于在每次校正时顺带做别的事（一般不用）。
    """
    def _apply() -> None:
        try:
            sw = win.winfo_screenwidth()
            if win.winfo_ismapped():
                w = win.winfo_width()
            else:
                try:
                    scale = ctk.ScalingTracker.get_window_scaling(win)
                except Exception:
                    scale = 1.0
                w = int(win.winfo_reqwidth() * scale)
            if w <= 1:
                return
            win.geometry(f"+{max((sw - w) // 2, 0)}+{margin}")
            win.lift()
            if cascade is not None:
                cascade(0)
        except Exception:
            pass

    try:
        win.update_idletasks()
    except Exception:
        pass
    _apply()
    for delay in (60, 150, 320, 600):
        try:
            win.after(delay, _apply)
        except Exception:
            break


# 类型键 → 中文显示名（下拉可选；按第 1 期已定类型清单）
TYPE_LABELS = (
    ("text", "文本框（单行）"),
    ("textarea", "文本框（多行）"),
    ("link", "链接"),
    ("image", "图像 / 图集"),
    ("list", "列表框"),
    ("number", "数字"),
    ("date", "日期"),
    ("bool", "是 / 否"),
    ("tag", "标签"),
    ("file", "附件"),
    ("audio", "短音频"),
)
_LABEL_TO_TYPE = {lbl: k for k, lbl in TYPE_LABELS}   # 中文显示名 → 类型键（用于下拉取值）


class FieldManagerDialog(ctk.CTkToplevel):
    """字段管理对话框（模态）。

    master : 父窗口（主窗口）
    db     : Database（用于读写 field_defs）
    """

    def __init__(self, master, db) -> None:
        super().__init__(master)
        self.db = db
        self.master = master
        self.changed = False       # 是否已发生"已写库"的改动（供调用方决定刷新详情区）
        self._rows = []            # [(id, field_key, CTkEntry, 原显示名, is_builtin, archived)]

        self.title("🔧 字段管理")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self._build()
        self._rebuild_list()
        self._center()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        pad = 20
        ctk.CTkLabel(self, text="🔧 字段管理", font=("Microsoft YaHei", 15, "bold")
                     ).grid(row=0, column=0, columnspan=3, padx=pad, pady=(16, 2), sticky="w")
        ctk.CTkLabel(
            self,
            text="内置前 10 个区块固定保留、不可删除，仅可改名；可新增自定义字段并排序。\n"
                 "「删除」= 归档隐藏：字段不再显示，但已填写的内容全部保留，可随时恢复。",
            justify="left", text_color="gray", font=("Microsoft YaHei", 11)
        ).grid(row=1, column=0, columnspan=3, padx=pad, pady=(0, 8), sticky="w")

        # ---- 新增自定义字段 ----
        add_row = ctk.CTkFrame(self, fg_color="#f4f0fb", corner_radius=8)
        add_row.grid(row=2, column=0, columnspan=3, padx=pad, pady=(0, 8), sticky="ew")
        ctk.CTkLabel(add_row, text="新增字段：", font=("Microsoft YaHei", 12, "bold"),
                     text_color="#7A4FBF").pack(side="left", padx=(10, 4), pady=8)
        self.new_name = ctk.CTkEntry(add_row, width=200, placeholder_text="字段显示名")
        self.new_name.pack(side="left", padx=4, pady=8)
        self.new_type = ctk.CTkOptionMenu(
            add_row, width=140, values=[lbl for _k, lbl in TYPE_LABELS])
        self.new_type.set("文本框（单行）")
        self.new_type.pack(side="left", padx=4, pady=8)
        ctk.CTkButton(add_row, text="＋ 添加", width=86, fg_color="#7A4FBF",
                      command=self._on_add).pack(side="left", padx=(6, 10), pady=8)

        # ---- 字段列表 ----
        self._body = ctk.CTkScrollableFrame(self, width=660, height=320)
        self._body.grid(row=3, column=0, columnspan=3, padx=pad, pady=(0, 6), sticky="nsew")

        opt_row = ctk.CTkFrame(self, fg_color="transparent")
        opt_row.grid(row=4, column=0, columnspan=3, padx=pad, pady=(0, 2), sticky="w")
        self.show_archived = ctk.CTkCheckBox(
            opt_row, text="显示已归档字段", font=("Microsoft YaHei", 12),
            command=self._rebuild_list)
        self.show_archived.pack(side="left")
        self.hint = ctk.CTkLabel(opt_row, text="", text_color="gray",
                                 font=("Microsoft YaHei", 11))
        self.hint.pack(side="left", padx=(12, 0))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=5, column=0, columnspan=3, sticky="e", padx=pad, pady=(4, 16))
        ctk.CTkButton(btn_row, text="确定", width=96, fg_color="#2E8B57",
                      command=self._apply).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="取消", width=96,
                      command=self.destroy).pack(side="left", padx=4)

    # ------------------------------------------------------------------ #
    def _rebuild_list(self) -> None:
        """按数据库当前内容重建字段列表（内置 + 自定义；可含已归档）。"""
        for w in self._body.winfo_children():
            w.destroy()
        self._rows = []
        with_archived = bool(self.show_archived.get())
        defs = self.db.list_field_defs(include_archived=with_archived)
        builtin_n = sum(1 for d in defs if d.get("is_builtin"))
        custom_n = len(defs) - builtin_n
        try:
            self._body.configure(
                label_text=f"字段列表（内置 {builtin_n} 项 · 自定义 {custom_n} 项）")
        except Exception:
            pass

        for d in defs:
            is_builtin = bool(d.get("is_builtin"))
            archived = bool(d.get("archived"))
            row = ctk.CTkFrame(self._body, fg_color="transparent")
            row.pack(fill="x", pady=2)
            # 左：稳定标识 field_key（不可改）
            ctk.CTkLabel(row, text=d["field_key"], width=78, anchor="w",
                         text_color="gray", font=("Microsoft YaHei", 11)
                         ).pack(side="left")
            # 中：显示名（可改）
            entry = ctk.CTkEntry(row, width=190)
            entry.pack(side="left", padx=(4, 0))
            entry.insert(0, d["display_name"])
            if not is_builtin:
                entry.configure(border_color="#b79ce0")   # 自定义字段：紫色描边区分
            if archived:
                entry.configure(state="disabled", fg_color="#eef0f3")
            # 标记
            if archived:
                tag_text, tag_color = "已归档", "#9aa4b1"
            elif is_builtin:
                tag_text, tag_color = "内置", "#5b6b7c"
            else:
                tag_text, tag_color = "自定义", "#7A4FBF"
            ctk.CTkLabel(row, text=tag_text, width=48, anchor="w",
                         text_color=tag_color, font=("Microsoft YaHei", 11)
                         ).pack(side="left", padx=(4, 0))
            # 2026-09-14（审核补充 P5）：**自定义字段可改类型**（内置仅可改名，保持只读标签）
            if not is_builtin and not archived:
                _tmenu = ctk.CTkOptionMenu(
                    row, width=140, values=[lbl for _k, lbl in TYPE_LABELS],
                    command=lambda lbl, k=d["field_key"]: self._on_type_change(k, lbl),
                    font=("Microsoft YaHei", 11))
                _tmenu.set(self._type_label(d.get("field_type")))
                _tmenu.pack(side="left", padx=(4, 0))
            else:
                ctk.CTkLabel(row, text=self._type_label(d.get("field_type")), width=96,
                             anchor="w", text_color="gray", font=("Microsoft YaHei", 11)
                             ).pack(side="left")
            # 2026-09-13（第 3 期 3-a）：列表框字段提供"数据源…"配置入口
            if d.get("field_type") == "list" and not archived:
                _n = self._source_brief(d["field_key"])
                ctk.CTkButton(row, text="数据源…", width=68, height=24, fg_color="#2f6fb0",
                              command=lambda k=d["field_key"], nm=d["display_name"]:
                                  self._edit_source(k, nm)
                              ).pack(side="left", padx=(6, 0))
                ctk.CTkLabel(row, text=_n, anchor="w", text_color="#2f6fb0",
                             font=("Microsoft YaHei", 10)).pack(side="left", padx=(4, 0))
            # 操作按钮（内置字段不可删除、不参与排序）
            fid = d["id"]
            if not is_builtin and not archived:
                ctk.CTkButton(row, text="↑", width=28, height=24, fg_color="#8a94a6",
                              command=lambda i=fid: self._move(i, -1)
                              ).pack(side="left", padx=(4, 0))
                ctk.CTkButton(row, text="↓", width=28, height=24, fg_color="#8a94a6",
                              command=lambda i=fid: self._move(i, 1)
                              ).pack(side="left", padx=(2, 0))
                ctk.CTkButton(row, text="删除", width=52, height=24, fg_color="#D9534F",
                              command=lambda k=d["field_key"]: self._archive(k)
                              ).pack(side="left", padx=(6, 0))
            elif archived:
                ctk.CTkButton(row, text="恢复", width=52, height=24, fg_color="#2E8B57",
                              command=lambda k=d["field_key"]: self._restore(k)
                              ).pack(side="left", padx=(6, 0))
            self._rows.append((fid, d["field_key"], entry, d["display_name"],
                               is_builtin, archived))

    def _on_type_change(self, field_key: str, label: str) -> None:
        """自定义字段改类型（P5）：立即写库并重建列表（列表型的"数据源…"入口随之出现/消失）。

        只改类型，**不动**已有取值与列表框数据源配置；改完在提示行说明。
        """
        ftype = {lbl: k for k, lbl in TYPE_LABELS}.get(label, "text")
        try:
            self.db.set_field_type(field_key, ftype)
        except Exception as exc:                       # noqa: BLE001
            messagebox.showwarning("无法修改类型", str(exc), parent=self)
            self._rebuild_list()
            return
        self.hint.configure(text=f"已把「{field_key}」的类型改为：{label}"
                                 "（已填写的取值保留不变）")
        self._rebuild_list()

    @staticmethod
    def _type_label(field_type: str) -> str:
        for k, lbl in TYPE_LABELS:
            if k == field_type:
                return lbl
        return field_type or ""

    def _source_brief(self, field_key: str) -> str:
        """列表框字段的数据源简述（列表行右侧小字）"""
        cfg = self.db.list_field_config(field_key)
        st = cfg["source_type"]
        scope = "全部" if cfg["scope"] == "all" else f"指定{len(cfg['node_refs'])}节点"
        if st == "sequence":
            return f"序列 {len(cfg['items'])} 项" + ("·多选" if cfg["multi"] else "")
        if st == "tree_level":
            lvl = dict(_LEVEL_LABELS).get(cfg["level"], cfg["level"])
            return f"层级 {lvl}·{scope}" + ("·多选" if cfg["multi"] else "")
        if st == "entries":
            return f"条目·{scope}" + ("·多选" if cfg["multi"] else "")
        return st

    def _edit_source(self, field_key: str, display_name: str) -> None:
        """打开"数据源配置"对话框（自定义序列 / 目录层级 / 条目 三类均可用）"""
        if self._save_renames() < 0:
            return
        dlg = ListSourceDialog(self, self.db, field_key, display_name)
        self.wait_window(dlg)
        if getattr(dlg, "saved", False):
            self.changed = True
            self.hint.configure(text=f"已保存「{display_name}」的数据源配置。")
            self._rebuild_list()

    # ------------------------------------------------------------------ #
    def _save_renames(self) -> int:
        """校验并写回所有改过的显示名；返回改动条数；校验失败返回 -1。"""
        for _fid, field_key, entry, _old, _bi, archived in self._rows:
            if archived:
                continue
            if not entry.get().strip():
                messagebox.showwarning(
                    "提示", f"字段「{field_key}」的显示名称不能为空。", parent=self)
                entry.focus_set()
                return -1
        changed = 0
        for _fid, field_key, entry, old, _bi, archived in self._rows:
            if archived:
                continue
            new_name = entry.get().strip()
            if new_name != old:
                self.db.rename_field_def(field_key, new_name)
                changed += 1
        return changed

    def _on_add(self) -> None:
        """新增自定义字段：先保存列表中的改名，再写库并重建列表。"""
        name = self.new_name.get().strip()
        if not name:
            messagebox.showwarning("提示", "请填写新字段的显示名称。", parent=self)
            self.new_name.focus_set()
            return
        if self._save_renames() < 0:
            return
        ftype = _LABEL_TO_TYPE.get(self.new_type.get(), "text")
        try:
            key = self.db.add_field_def(name, ftype)
        except ValueError as exc:
            messagebox.showwarning("提示", str(exc), parent=self)
            return
        self.changed = True
        self.new_name.delete(0, "end")
        if ftype == "list":
            self.hint.configure(text=f"已新增「{name}」（{key}）；请点该行「数据源…」配置候选值。")
        else:
            self.hint.configure(text=f"已新增自定义字段「{name}」（{key}）。")
        self._rebuild_list()

    def _move(self, field_id: int, delta: int) -> None:
        """在**自定义字段之间**上移/下移（内置 10 项顺序固定，不参与）。"""
        if self._save_renames() < 0:
            return
        cids = [d["id"] for d in self.db.list_field_defs()
                if not d.get("is_builtin")]
        if not self.db.swap_order("field_defs", cids, field_id, delta):
            return
        self.changed = True
        self._rebuild_list()

    def _archive(self, field_key: str) -> None:
        """删除（归档隐藏）：确认后隐藏该字段，已填内容保留。"""
        if not messagebox.askyesno(
                "删除字段",
                "将从界面移除该自定义字段（不再显示）。\n\n"
                "该字段在各条目中【已填写的内容会全部保留】，\n"
                "可勾选「显示已归档字段」后点「恢复」找回。\n\n确定要删除吗？",
                parent=self):
            return
        if self._save_renames() < 0:
            return
        try:
            self.db.archive_field_def(field_key)
        except ValueError as exc:
            messagebox.showwarning("提示", str(exc), parent=self)
            return
        self.changed = True
        self.hint.configure(text=f"已删除（归档）字段「{field_key}」；内容已保留，可恢复。")
        self._rebuild_list()

    def _restore(self, field_key: str) -> None:
        """恢复已归档的自定义字段。"""
        if self._save_renames() < 0:
            return
        self.db.restore_field_def(field_key)
        self.changed = True
        self.hint.configure(text=f"已恢复字段「{field_key}」。")
        self._rebuild_list()

    def _apply(self) -> None:
        """保存改名并关闭。"""
        changed = self._save_renames()
        if changed < 0:
            return
        if changed > 0:
            self.changed = True
        self.destroy()

    def _center(self) -> None:
        """**屏幕水平居中**（窗口中线对齐屏幕中线）＋ 垂直固定距屏幕上边 _TOP_MARGIN 处。

        2026-09-13 用户口径修正：此前按主窗口左边缘推算（"相对主窗口居中"），现按屏幕居中；
        具体定位与高 DPI 校正见模块级 `_place_top_centered()`。
        """
        _place_top_centered(self)


# ---------------------------------------------------------------------- #
# 列表框"数据源"配置对话框（2026-09-13，第 3 期 3-a / 3-b / 3-c）
#   3-a：自定义序列型；3-b：目录层级型（项目类别/根目录/一级/二级 + 全部节点/指定节点）；
#   3-c：条目型（全部条目 / 指定节点子树内条目）——三类全部可用。
# ---------------------------------------------------------------------- #
_SRC_SEQ = "自定义序列"
_SRC_TREE = "目录层级"
_SRC_ENTRIES = "条目"

# 层级（3-b）：候选节点所在层级
_LEVEL_LABELS = (("project", "项目类别"), ("domain", "根目录"),
                 ("cat1", "一级分类"), ("cat2", "二级分类"))
# 范围（3-b/3-c）：全部 / 指定父节点子树
_SCOPE_LABELS = (("all", "全部节点"), ("nodes", "指定节点"))
_LEVEL_KEY = {lbl: k for k, lbl in _LEVEL_LABELS}
_SCOPE_KEY = {lbl: k for k, lbl in _SCOPE_LABELS}


class _TreeRefPickDialog(ctk.CTkToplevel):
    """父节点选择（"指定节点"范围用）：项目类别 → 根目录 → 一级 → 二级，可多选。

    目录层级型（3-b）与条目型（3-c）共用：所勾选节点的子树即为候选范围。
    """

    def __init__(self, master, db, selected=()) -> None:
        super().__init__(master)
        self.db = db
        self.result = None            # None=取消；否则为令牌列表
        self._iid_token = {}
        self._selected = set(selected or ())

        self.title("选择节点（指定范围）")
        self.geometry("560x580")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self,
                     text="勾选作为候选范围的父节点（Ctrl/Shift 可多选）：\n"
                          "例：选中某根目录，则候选取该根目录子树内的目标对象。",
                     justify="left", text_color="gray", anchor="w",
                     font=("Microsoft YaHei", 11)
                     ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))

        wrap = ctk.CTkFrame(self)
        wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(wrap, show="tree", selectmode="extended")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self._build()
        self._restore_selection()
        self.tree.bind("<<TreeviewSelect>>", lambda _e=None: self._refresh_sel())

        self.sel_label = ctk.CTkLabel(self, text="", text_color="gray", anchor="w",
                                      font=("Microsoft YaHei", 11))
        self.sel_label.grid(row=2, column=0, sticky="ew", padx=14)
        self._refresh_sel()

        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.grid(row=3, column=0, sticky="e", padx=12, pady=(4, 12))
        ctk.CTkButton(btn, text="确定", width=88, fg_color="#2E8B57",
                      command=self._ok).pack(side="left", padx=4)
        ctk.CTkButton(btn, text="取消", width=88,
                      command=self.destroy).pack(side="left", padx=4)
        self._center()

    def _build(self) -> None:
        for p in self.db.list_projects():
            pid = self.tree.insert("", "end", text=p["name"], open=True)
            self._iid_token[pid] = _ref_token("project", p["id"])
            for d in self.db.list_domains(project_id=p["id"]):
                self._insert_domain(pid, d)
        for d in self.db.list_unassigned_domains():
            self._insert_domain("", d, suffix="（未分配）")

    def _insert_domain(self, parent, d: dict, suffix: str = "") -> None:
        did = self.tree.insert(parent, "end", text=f"{d['name']}{suffix}", open=True)
        self._iid_token[did] = _ref_token("domain", d["id"])
        for l1 in self.db.list_categories(domain_id=d["id"], parent_id=None):
            i1 = self.tree.insert(did, "end", text=l1["name"], open=False)
            self._iid_token[i1] = _ref_token("cat", l1["id"])
            for l2 in self.db.list_categories(parent_id=l1["id"]):
                i2 = self.tree.insert(i1, "end", text=l2["name"])
                self._iid_token[i2] = _ref_token("cat", l2["id"])

    def _restore_selection(self) -> None:
        if not self._selected:
            return
        for iid, tok in self._iid_token.items():
            if tok in self._selected:
                self.tree.selection_add(iid)

    def _tokens(self) -> list:
        out = []
        for iid in self.tree.selection():
            t = self._iid_token.get(iid)
            if t and t not in out:
                out.append(t)
        return out

    def _refresh_sel(self) -> None:
        names = [n for n in (self.db.ref_name(t) for t in self._tokens()) if n]
        self.sel_label.configure(text=f"已选 {len(names)} 个节点：" + "、".join(names[:6])
                                      + ("…" if len(names) > 6 else ""))

    def _ok(self) -> None:
        self.result = self._tokens()
        self.destroy()

    def _center(self) -> None:
        """屏幕水平居中 + 上边距 50（与「字段管理」对话框统一，2026-09-13 用户要求）"""
        _place_top_centered(self)


class ListSourceDialog(ctk.CTkToplevel):
    """列表框字段的数据源配置。

    - 自定义序列：选项列表 + 允许新建项（3-a）；
    - 目录层级：层级 + 范围（全部节点/指定节点）+ 显示路径前缀（3-b）；
    - 公共：单选/多选（所有数据源通用）。
    """

    def __init__(self, master, db, field_key: str, display_name: str) -> None:
        super().__init__(master)
        self.db = db
        self.master = master
        self.field_key = field_key
        self.saved = False
        cfg = db.list_field_config(field_key)
        self._cfg = cfg
        self._node_refs = list(cfg["node_refs"])   # "指定节点"范围所选的父节点令牌

        self.title(f"数据源配置 · {display_name}")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        pad = 18
        ctk.CTkLabel(self, text=f"数据源配置 · {display_name}（{field_key}）",
                     font=("Microsoft YaHei", 14, "bold")
                     ).grid(row=0, column=0, columnspan=2, padx=pad, pady=(14, 2), sticky="w")
        ctk.CTkLabel(self, text="候选值来源：自定义序列 / 目录层级（项目类别·根目录·一级·二级）"
                                "/ 条目（全库或指定目录子树内的条目）。",
                     justify="left", text_color="gray", font=("Microsoft YaHei", 11)
                     ).grid(row=1, column=0, columnspan=2, padx=pad, pady=(0, 8), sticky="w")

        # 数据源类型
        ctk.CTkLabel(self, text="数据源类型", font=("Microsoft YaHei", 12)
                     ).grid(row=2, column=0, padx=pad, pady=6, sticky="w")
        self.src = ctk.CTkOptionMenu(self, width=210,
                                     values=[_SRC_SEQ, _SRC_TREE, _SRC_ENTRIES],
                                     command=lambda _v: self._on_src())
        self.src.grid(row=2, column=1, padx=pad, pady=6, sticky="w")
        self.src.set({_SRC_SEQ: _SRC_SEQ, "tree_level": _SRC_TREE,
                      "entries": _SRC_ENTRIES}.get(cfg["source_type"], _SRC_SEQ))
        self.src_hint = ctk.CTkLabel(self, text="", text_color="#D9534F",
                                     font=("Microsoft YaHei", 11))
        self.src_hint.grid(row=3, column=0, columnspan=2, padx=pad, pady=(0, 4), sticky="w")

        # ---- 第 4 行：序列体 / 层级体（互斥显示，同一行位） ---- #
        self.seq_frame = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.seq_frame, text="序列内容\n（每行一个选项）", justify="left",
                     font=("Microsoft YaHei", 12)).pack(side="left", anchor="n", pady=6)
        self.items = ctk.CTkTextbox(self.seq_frame, width=260, height=150)
        self.items.pack(side="left", padx=(24, 0), pady=6)
        if cfg["items"]:
            self.items.insert("1.0", "\n".join(cfg["items"]))

        self.tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        # 候选层级行（仅"目录层级"数据源显示）
        self.level_row = ctk.CTkFrame(self.tree_frame, fg_color="transparent")
        self.level_row.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(self.level_row, text="候选层级", font=("Microsoft YaHei", 12)
                     ).pack(side="left")
        self.level = ctk.CTkOptionMenu(self.level_row, width=150,
                                       values=[lbl for _k, lbl in _LEVEL_LABELS])
        self.level.pack(side="left", padx=(10, 0))
        self.level.set(dict(_LEVEL_LABELS).get(cfg["level"], "一级分类"))
        ctk.CTkLabel(self.level_row, text="候选节点所在层级", text_color="gray",
                     font=("Microsoft YaHei", 10)).pack(side="left", padx=(10, 0))
        # 候选范围行（层级型 / 条目型共用）
        scope_row = ctk.CTkFrame(self.tree_frame, fg_color="transparent")
        scope_row.pack(anchor="w", pady=(10, 0))
        ctk.CTkLabel(scope_row, text="候选范围", font=("Microsoft YaHei", 12)
                     ).pack(side="left")
        self.scope = ctk.CTkOptionMenu(scope_row, width=132,
                                       values=[lbl for _k, lbl in _SCOPE_LABELS],
                                       command=lambda _v: self._on_scope())
        self.scope.pack(side="left", padx=(10, 0))
        self.scope.set(dict(_SCOPE_LABELS).get(cfg["scope"], "全部节点"))
        self.pick_btn = ctk.CTkButton(scope_row, text="选择节点…", width=92, height=27,
                                      fg_color="#2f6fb0", command=self._pick_nodes)
        self.pick_btn.pack(side="left", padx=(8, 0))
        self.node_lbl = ctk.CTkLabel(self.tree_frame, text="", text_color="gray", anchor="w",
                                     justify="left", wraplength=330,
                                     font=("Microsoft YaHei", 10))
        self.node_lbl.pack(anchor="w", pady=(6, 0))

        # 公共能力
        opt = ctk.CTkFrame(self, fg_color="transparent")
        opt.grid(row=5, column=0, columnspan=2, padx=pad, pady=(2, 4), sticky="w")
        self.multi = ctk.CTkCheckBox(opt, text="允许多选", font=("Microsoft YaHei", 12))
        self.multi.pack(side="left")
        if cfg["multi"]:
            self.multi.select()
        self.allow_new = ctk.CTkCheckBox(opt, text="允许新建项", font=("Microsoft YaHei", 12))
        self.allow_new.pack(side="left", padx=(14, 0))
        if cfg["allow_new"]:
            self.allow_new.select()
        self.path_prefix = ctk.CTkCheckBox(opt, text="显示路径前缀", font=("Microsoft YaHei", 12))
        self.path_prefix.pack(side="left", padx=(14, 0))
        if cfg["path_prefix"]:
            self.path_prefix.select()
        ctk.CTkLabel(self, text="提示：「允许新建项」仅对「自定义序列」有效；「显示路径前缀」对"
                                "「目录层级 / 条目」有效（开启＝显示 项目/根目录/一级/二级(+/条目名) "
                                "完整路径，关闭＝只显示名称）。",
                     justify="left", text_color="gray", font=("Microsoft YaHei", 11)
                     ).grid(row=6, column=0, columnspan=2, padx=pad, pady=(0, 2), sticky="w")

        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.grid(row=7, column=0, columnspan=2, sticky="e", padx=pad, pady=(6, 14))
        self.ok_btn = ctk.CTkButton(btn, text="保存", width=92, fg_color="#2E8B57",
                                    command=self._save)
        self.ok_btn.pack(side="left", padx=4)
        ctk.CTkButton(btn, text="取消", width=92, command=self.destroy).pack(side="left", padx=4)

        self._on_src()
        self._center()

    def _on_scope(self) -> None:
        """范围＝指定节点时，节点选择按钮可用"""
        nodes = self.scope.get() == "指定节点"
        self.pick_btn.configure(state="normal" if nodes else "disabled")
        self._refresh_nodes()

    def _refresh_nodes(self) -> None:
        if self.scope.get() != "指定节点":
            self.node_lbl.configure(
                text="（范围＝全部：候选＝全部条目）" if self.src.get() == _SRC_ENTRIES
                else "（范围＝全部节点：候选取该层级全库节点）")
            return
        names = [n for n in (self.db.ref_name(t) for t in self._node_refs) if n]
        if not names:
            self.node_lbl.configure(text="尚未选择父节点（请点「选择节点…」）")
        else:
            self.node_lbl.configure(text=f"已选 {len(names)} 个："
                                         + "、".join(names[:6]) + ("…" if len(names) > 6 else ""))

    def _pick_nodes(self) -> None:
        dlg = _TreeRefPickDialog(self, self.db, self._node_refs)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._node_refs = list(dlg.result)
        self._refresh_nodes()

    def _on_src(self) -> None:
        """按数据源类型切换界面：序列体 /（层级体·条目体）/ 公共提示"""
        v = self.src.get()
        if v == _SRC_SEQ:
            self.tree_frame.grid_remove()
            self.seq_frame.grid(row=4, column=0, columnspan=2, padx=18, sticky="w")
            self.ok_btn.configure(state="normal")
            self.src_hint.configure(text="")
            self.allow_new.configure(state="normal")
        else:
            self.seq_frame.grid_remove()
            self.tree_frame.grid(row=4, column=0, columnspan=2, padx=18, sticky="w")
            self.ok_btn.configure(state="normal")
            if v == _SRC_TREE:
                self.level_row.pack(anchor="w", pady=(6, 0))   # 层级型：显示"候选层级"
                self.src_hint.configure(
                    text="候选值取自既有四级目录（新增/改名目录后自动同步）。")
            else:
                self.level_row.pack_forget()                   # 条目型：无层级概念
                self.src_hint.configure(
                    text="候选＝全库条目，或所选目录节点子树内的条目（条目改名/删除自动同步）。")
            self.allow_new.deselect()
            self.allow_new.configure(state="disabled")
        self._on_scope()

    def _save(self) -> None:
        v = self.src.get()
        if v in (_SRC_TREE, _SRC_ENTRIES):
            scope = "nodes" if self.scope.get() == "指定节点" else "all"
            if scope == "nodes" and not self._node_refs:
                messagebox.showwarning("提示", "范围为「指定节点」时，请至少选择一个父节点。",
                                       parent=self)
                return
            cfg = {
                "source_type": "tree_level" if v == _SRC_TREE else "entries",
                "scope": scope,
                "node_refs": list(self._node_refs),
                "multi": bool(self.multi.get()),
                "path_prefix": bool(self.path_prefix.get()),
                # 保留另一类的层级设置：便于在两种数据源间来回切换时不用重选
                "level": _LEVEL_KEY.get(self.level.get(), "cat1"),
            }
            self.db.set_field_config(self.field_key, cfg)
            self.saved = True
            self.destroy()
            return
        if v != _SRC_SEQ:
            return
        text = self.items.get("1.0", "end")
        items = []
        for line in text.splitlines():
            s = line.strip()
            if s and s not in items:
                items.append(s)
        if not items:
            messagebox.showwarning("提示", "请至少填写一个选项（每行一个）。", parent=self)
            self.items.focus_set()
            return
        self.db.set_field_config(self.field_key, {
            "source_type": "sequence",
            "items": items,
            "multi": bool(self.multi.get()),
            "allow_new": bool(self.allow_new.get()),
            "path_prefix": bool(self.path_prefix.get()),
            # 保留层级型设置：便于在两种数据源间来回切换时不用重选
            "level": _LEVEL_KEY.get(self.level.get(), "cat1"),
            "scope": "nodes" if self.scope.get() == "指定节点" else "all",
            "node_refs": list(self._node_refs),
        })
        self.saved = True
        self.destroy()

    def _center(self) -> None:
        """屏幕水平居中 + 上边距 50（与「字段管理」对话框统一，2026-09-13 用户要求）"""
        _place_top_centered(self)
