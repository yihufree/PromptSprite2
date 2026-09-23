# -*- coding: utf-8 -*-
"""
copy_move_dialog.py - "复制到/移动到" 目标选择对话框
创建日期：2026-08-21（第004条：各级目录"复制到/移动到"功能）

针对"各级目录的复制到/移动到"需求（语义经用户确认，见 003 条工作记录）：
  - 操作对象：根目录(domain) / 一级分类(l1) / 二级分类(l2)
  - 目标：另一根目录下作一级分类 / 一级分类下作二级分类 / 新建根目录项
  - 名称规则：下移加前缀、上移去前缀、重名加序号（对话框内实时预览）
关闭后读取 result / target_kind / target_id / new_name：
  - target_kind: 'domain_below' | 'l1_to_domain' | 'l1_to_l2' | 'l2_to_domain' | 'l2_to_l2'
  - target_id: 目标 id；None 表示"新建根目录项"
  - new_name: 新建根目录项名称（仅 target_id 为 None 时有意义）
"""
from tkinter import messagebox

import customtkinter as ctk
from tkinter import ttk

from . import ui_common as _ui_common        # 2026-09-23：用于取控件缩放系数

# 2026-09-23（用户要求 3）：本窗口**距屏幕顶端固定距离**（实际px）——位置恒定、
#   不随主窗口移动，也不会被屏幕下边缘遮住（不足时改为压低窗口高度）。
#   取值与 export_scope_dialog._SCOPE_TOP_MARGIN / move_selector._TOP_MARGIN 一致。
_TOP_MARGIN = 50


class CopyMoveDialog(ctk.CTkToplevel):
    def __init__(self, master, db, src_type: str, src_id: int, src_name: str,
                 action: str, from_domain_id: int = None):
        super().__init__(master)
        self.db = db
        self.src_type = src_type              # 'domain' | 'l1' | 'l2'
        self.src_id = src_id
        self.src_name = src_name
        self.action = action                  # 'copy' | 'move'
        self.from_domain_id = from_domain_id  # 一级分类对象所在根目录（移动时解除该关联）

        self.result = "cancel"
        self.target_kind = None
        self.target_id = None
        self.new_name = None
        self._new_domain_mode = False

        verb = "复制" if action == "copy" else "移动"
        self.title(f"{verb}到")
        self.geometry("480x600")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 顶部说明
        ctk.CTkLabel(self, text=f"把【{src_name}】{verb}到：",
                     anchor="w", font=("Microsoft YaHei", 13, "bold")
                     ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))

        # 树形目标选择（根目录→一级→二级）
        tree_frame = ctk.CTkFrame(self)
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, show="tree")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self._iid_node = {}
        self._build_tree()
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 新建根目录项（仅一级/二级分类对象可用）
        self.new_dom_btn = ctk.CTkButton(self, text="🏠 新建根目录项", width=160,
                                         fg_color="#8a94a6",
                                         command=self._pick_new_domain)
        self.new_dom_btn.grid(row=2, column=0, sticky="w", padx=14, pady=4)
        if src_type == "domain":
            self.new_dom_btn.grid_remove()

        # 新根目录名称输入框（新建根目录项时显示）
        self.name_row = ctk.CTkFrame(self, fg_color="transparent")
        self.name_row.grid(row=3, column=0, sticky="ew", padx=14, pady=2)
        self.name_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.name_row, text="新根目录名称："
                     ).grid(row=0, column=0, sticky="w")
        self.name_entry = ctk.CTkEntry(self.name_row)
        self.name_entry.grid(row=0, column=1, sticky="ew")
        self.name_row.grid_remove()

        # 影响预览
        ctk.CTkLabel(self, text="操作影响预览：", anchor="w"
                     ).grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 0))
        self.preview = ctk.CTkTextbox(self, height=130)
        self.preview.grid(row=5, column=0, sticky="nsew", padx=12, pady=4)
        self.preview.configure(state="disabled")

        # 底部按钮
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.btn_ok = ctk.CTkButton(btn_row, text="确定", width=88,
                                    command=self._confirm)
        self.btn_ok.pack(side="right", padx=(4, 0))
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
            _scale = _ui_common.widget_scaling(self.btn_ok) or 1.0
        except Exception:                                   # noqa: BLE001
            _scale = 1.0
        if _h_real > _max_h_real:
            self.geometry("%dx%d+%d+%d" % (_w_real / _scale, _max_h_real / _scale,
                                           _x, _TOP_MARGIN))
        else:
            self.geometry("+%d+%d" % (_x, _TOP_MARGIN))
        self.lift()
        self._set_preview()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_tree(self) -> None:
        for p in self.db.list_projects():
            pid = self.tree.insert("", "end", text=p["name"], open=True)
            self._iid_node[pid] = ("project", p["id"])
            for d in self.db.list_domains(project_id=p["id"]):
                self._insert_domain(pid, d)
        # 未分配根目录（无归属，置于树根）
        for d in self.db.list_unassigned_domains():
            self._insert_domain("", d, suffix="（未分配）")

    def _insert_domain(self, parent, d: dict, suffix: str = "") -> None:
        """在树中插入一个根目录节点及其一级/二级分类子树"""
        did = self.tree.insert(parent, "end", text=f"{d['name']}{suffix}", open=True)
        self._iid_node[did] = ("domain", d["id"])
        for l1 in self.db.list_categories(domain_id=d["id"], parent_id=None):
            l1_id = self.tree.insert(did, "end", text=l1["name"], open=True)
            self._iid_node[l1_id] = ("l1", l1["id"])
            for l2 in self.db.list_categories(parent_id=l1["id"]):
                l2_id = self.tree.insert(l1_id, "end", text=l2["name"])
                self._iid_node[l2_id] = ("l2", l2["id"])

    # ------------------------------------------------------------------ #
    # 目标解析与预览
    # ------------------------------------------------------------------ #
    def _resolve_target(self):
        """返回 (target_kind, target_id, new_domain_name)；无效返回 (None, None, None)"""
        if self._new_domain_mode:
            if self.src_type in ("l1", "l2"):
                new_name = self.name_entry.get().strip()
                if not new_name:
                    return None, None, None
                kind = "l1_to_domain" if self.src_type == "l1" else "l2_to_domain"
                return kind, None, new_name
            return None, None, None
        sel = self.tree.selection()
        if not sel:
            return None, None, None
        kind, tid = self._iid_node.get(sel[0], (None, None))
        if self.src_type == "domain":
            # 目标：另一项目类别（整体移动/复制归属）或另一根目录（作为其新一级分类）
            if kind == "project":
                cur = self.db.get_domain(self.src_id)
                if cur and tid == cur.get("project_id"):
                    return None, None, None  # 已在同一项目类别，无需操作
                return "domain_to_project", tid, None
            if kind == "domain" and tid != self.src_id:
                return "domain_below", tid, None
            return None, None, None
        if self.src_type == "l1":
            if kind == "domain":
                if self.action == "move" and tid == self.from_domain_id:
                    return None, None, None  # 移动：不能留在来源根目录
                return "l1_to_domain", tid, None
            if kind == "l1" and tid != self.src_id:
                return "l1_to_l2", tid, None
            return None, None, None
        # src_type == "l2"
        if kind == "domain":
            return "l2_to_domain", tid, None
        if kind == "l1":
            cat = self.db.get_category(self.src_id)
            if self.action == "move" and cat and cat["parent_id"] == tid:
                return None, None, None  # 移动：不能回到原父级
            return "l2_to_l2", tid, None
        return None, None, None

    def _set_preview(self) -> None:
        tk_, tid, new_name = self._resolve_target()
        lines = []
        if tk_ is None:
            lines.append("请选择有效的目标位置。")
        elif tk_ == "domain_to_project":
            p = self.db.get_project(tid)
            lines.append(f"目标：项目类别【{p['name']}】下")
            if self.action == "move":
                lines.append(f"根目录【{self.src_name}】整体移动到该项目（名称不变，其下分类/条目随动）。")
            else:
                new_name = self.db.unique_domain_name(self.src_name)
                lines.append(f"根目录【{self.src_name}】复制为【{new_name}】挂到该项目"
                             f"（重名自动加序号，源保留，分类/条目深拷贝）。")
        elif tk_ == "domain_below":
            d = self.db.get_domain(tid)
            lines.append(f"目标：根目录【{d['name']}】下，作为其新的一级分类")
            lines.append("以下一级分类将改名并挂到目标根目录下（其下二级分类名称不变）：")
            for l1 in self.db.list_categories(domain_id=self.src_id, parent_id=None):
                lines.append(f"  {l1['name']} → {self.src_name}.{l1['name']}")
            shared = [l1 for l1 in self.db.list_categories(domain_id=self.src_id, parent_id=None)
                      if len(self.db.linked_domains(l1["id"])) > 1]
            if shared:
                lines.append("⚠ 下列分类被多个根目录共享，将按副本方式复制（原分类保留原名）：")
                for l1 in shared:
                    lines.append(f"  {l1['name']}")
        elif tk_ == "l1_to_domain":
            if tid is None:
                lines.append(f"目标：新建根目录项【{new_name}】，作为其新的一级分类")
            else:
                d = self.db.get_domain(tid)
                lines.append(f"目标：根目录【{d['name']}】下，作为其新的一级分类")
            lines.append(f"分类【{self.src_name}】保持原名称（同级平移，仅调整关联）。")
        elif tk_ == "l1_to_l2":
            d = self.db.get_category(tid)
            lines.append(f"目标：一级分类【{d['name']}】下，作为其新的二级分类")
            subs = self.db.list_categories(parent_id=self.src_id)
            if subs:
                lines.append("以下二级分类将改名并挂到目标一级分类下：")
                for s in subs:
                    lines.append(f"  {s['name']} → {self.src_name}.{s['name']}")
            else:
                lines.append("（该一级分类下没有二级分类）")
            lines.append(f"{'移动' if self.action == 'move' else '复制'}后，"
                         f"源一级分类{'将被删除' if self.action == 'move' else '保留'}。")
        elif tk_ == "l2_to_domain":
            if tid is None:
                lines.append(f"目标：新建根目录项【{new_name}】，"
                             f"分类【{self.src_name}】提升为新的一级分类")
            else:
                d = self.db.get_domain(tid)
                lines.append(f"目标：根目录【{d['name']}】下，"
                             f"分类【{self.src_name}】提升为新的一级分类")
            lines.append("该分类下的条目将随分类一并迁移。")
        elif tk_ == "l2_to_l2":
            d = self.db.get_category(tid)
            lines.append(f"目标：一级分类【{d['name']}】下，作为其新的二级分类")
            lines.append(f"分类【{self.src_name}】保持原名称（同级平移）。")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", "\n".join(lines))
        self.preview.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _on_select(self, _ev=None) -> None:
        if self._new_domain_mode:
            self._new_domain_mode = False
            self.name_row.grid_remove()
        self._set_preview()

    def _pick_new_domain(self) -> None:
        self._new_domain_mode = True
        self.tree.selection_remove(self.tree.selection())
        default = self.db.strip_prefix(self.src_name)
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, default)
        self.name_row.grid()
        self._set_preview()

    def _confirm(self) -> None:
        tk_, tid, new_name = self._resolve_target()
        if tk_ is None:
            messagebox.showwarning("提示", "请先选择有效的目标位置。", parent=self)
            return
        self.result = "ok"
        self.target_kind = tk_
        self.target_id = tid
        self.new_name = new_name
        self._close()

    def _cancel(self) -> None:
        self.result = "cancel"
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
