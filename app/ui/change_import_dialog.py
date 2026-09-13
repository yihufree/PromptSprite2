# -*- coding: utf-8 -*-
"""
change_import_dialog.py - 变更包导入向导（2026-09-08 V1.7.0；2026-09-09 改为二维清晰布局）

把"执行范围"按两个正交维度拆开，从根上消除歧义：
  维度一（新增/修改数据）：是否合并包内新增/修改条目？         → 复选框
  维度二（删除清单处理）：忽略删除 / 正常应用删除 / 逆向恢复   → 三选一

这样"⑤ 逆向恢复"的语义就是明确的：它只决定删除清单如何处置；
是否同时导入包内"新增/修改"数据由上面的复选框独立决定，互不冲突。

规则：
  - "正常应用删除" / "逆向恢复" 均需输入对应确认短语；
  - "逆向恢复"仅在包内删除项携带完整快照时可用，否则按钮置灰并提示
    "只能靠目标库回收站/历史备份恢复"。

确认后 self.result = (apply_additions: bool, del_mode: str)
del_mode ∈ {"skip","apply","reverse"}。
"""
from tkinter import messagebox

import customtkinter as ctk

from .. import config  # noqa: F401


class ChangeImportDialog(ctk.CTkToplevel):
    """变更包导入预览/选择窗口。确认后 self.result = (apply_additions, del_mode)。"""

    _PHRASE_DELETE = "确认删除"    # 正常应用删除时输入
    _PHRASE_RECOVER = "恢复删除"   # 逆向恢复时输入

    def __init__(self, master, summary: dict, behind: bool = False):
        super().__init__(master)
        self.master = master
        self.summary = summary or {}
        self.behind = bool(behind)
        self.result = None

        self.title("导入变更包")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._del_mode = ctk.StringVar(value="ignore")
        self._has_snap = bool((summary or {}).get("has_snapshots"))

        self._build()
        self._center()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        s = self.summary
        pad = 22

        ctk.CTkLabel(self, text="导入变更包（预览）",
                     font=("Microsoft YaHei", 15, "bold")).grid(
            row=0, column=0, columnspan=2, padx=pad, pady=(16, 6), sticky="w")

        lines = []
        if s.get("computer_code"):
            lines.append(f"来源电脑：{s['computer_code']}   日期：{s.get('day') or '—'}")
        lines.append(f"包内新增/修改：条目 {s.get('add_entries', 0)}、"
                     f"分类 {s.get('add_categories', 0)}、"
                     f"根目录 {s.get('add_domains', 0)}")
        if s.get("del_total"):
            tail = (f"（其中 {s.get('deleted_snapshots', 0)} 条携带快照，可逆向恢复）"
                    if s.get("has_snapshots")
                    else "（未携带快照，删除项无法逆向恢复）")
            lines.append(f"包内删除清单：条目 {s.get('del_entries', 0)}、"
                         f"分类 {s.get('del_categories', 0)}、"
                         f"根目录 {s.get('del_domains', 0)}{tail}")
        else:
            lines.append("本包不含删除清单（纯新增/修改），可安全导入。")
        ctk.CTkLabel(self, text="\n".join(lines), justify="left",
                     font=("Microsoft YaHei", 13),
                     text_color="#1f2937").grid(
            row=1, column=0, columnspan=2, padx=pad, pady=(0, 10), sticky="w")

        if self.behind:
            ctk.CTkLabel(self, text="⚠ 提示：目标库最后同步时间早于本变更包日期，可能尚未同步"
                                    "来源机的历史删除；如无把握请把删除处理选为“忽略删除”。",
                         justify="left", text_color="#D9534F",
                         font=("Microsoft YaHei", 12)).grid(
                row=2, column=0, columnspan=2, padx=pad, pady=(0, 8), sticky="w")

        # ---- 维度一：新增/修改 ----
        ctk.CTkLabel(self, text="① 新增/修改数据",
                     font=("Microsoft YaHei", 13)).grid(
            row=3, column=0, padx=pad, pady=(4, 2), sticky="w")
        self.ck_add = ctk.CTkCheckBox(
            self, text="导入并合并包内新增/修改条目（推荐）",
            onvalue=1, offvalue=0)
        self.ck_add.grid(row=4, column=0, columnspan=2, padx=pad, pady=(0, 6), sticky="w")
        self.ck_add.select()

        # ---- 维度二：删除清单处理（三选一）----
        ctk.CTkLabel(self, text="② 删除清单数据处理（三选一）",
                     font=("Microsoft YaHei", 13)).grid(
            row=5, column=0, padx=pad, pady=(6, 2), sticky="w")
        opt = ctk.CTkFrame(self, fg_color="transparent")
        opt.grid(row=6, column=0, columnspan=2, padx=pad, sticky="w")
        self.rb_ignore = ctk.CTkRadioButton(
            opt, text="忽略删除（不删任何现有数据，推荐）",
            variable=self._del_mode, value="ignore", command=None)
        self.rb_ignore.pack(anchor="w", pady=3)
        self.rb_del = ctk.CTkRadioButton(
            opt, text="正常应用删除（把清单中内容从本库删除，需确认）",
            variable=self._del_mode, value="delete", command=None)
        self.rb_del.pack(anchor="w", pady=3)
        self.rb_rev = ctk.CTkRadioButton(
            opt,
            text=("逆向恢复已删除数据（把清单中携带快照的内容重新导入，需确认）"
                  if self._has_snap else
                  "逆向恢复已删除数据（本包未携带快照，不可用）"),
            variable=self._del_mode, value="reverse", command=None)
        self.rb_rev.pack(anchor="w", pady=3)
        if not self._has_snap:
            self.rb_rev.configure(state="disabled")
        if not s.get("del_total"):
            self.rb_del.configure(state="disabled")
            self.rb_rev.configure(state="disabled")

        if self._has_snap:
            note = "逆向恢复只会把本包删除清单中的内容加回（可包含当初有意删除的数据）；是否同时导入①新增/修改由上面的勾选决定。"
        else:
            note = ("本包删除项未携带完整快照，删除部分无法逆向恢复；如需找回删除数据，"
                    "只能依靠目标库回收站或历史备份。")
        ctk.CTkLabel(self, text="提示：" + note, justify="left",
                     text_color="#8a6d3b", font=("Microsoft YaHei", 11),
                     wraplength=560).grid(
            row=7, column=0, columnspan=2, padx=pad, pady=(2, 6), sticky="w")

        # 按钮
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=8, column=0, columnspan=2, sticky="e", padx=pad, pady=(6, 16))
        ctk.CTkButton(btn_row, text="开始导入", width=110, fg_color="#2E8B57",
                      command=self._confirm).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="取消", width=90,
                      command=self._cancel).pack(side="left", padx=6)

    # ------------------------------------------------------------------ #
    def _confirm(self) -> None:
        apply_additions = bool(self.ck_add.get())
        mode = self._del_mode.get()
        if mode == "ignore":
            del_mode = "skip"
        elif mode == "delete":
            if not self.summary.get("del_total"):
                return
            if not self._ask_phrase(
                    self._PHRASE_DELETE,
                    f"将把清单中的删除同步到本库：条目 {self.summary.get('del_entries', 0)}、"
                    f"分类 {self.summary.get('del_categories', 0)}、"
                    f"根目录 {self.summary.get('del_domains', 0)}。\n"
                    "导入前自动生成快照、被删条目先入回收站（可恢复），"
                    "但分类结构删除后不随回收站恢复。\n\n"
                    f"请输入确认短语“{self._PHRASE_DELETE}”以继续："):
                return
            del_mode = "apply"
        else:  # reverse
            if not self._has_snap:
                messagebox.showwarning("不可用", "本包未携带被删快照，无法逆向恢复。",
                                       parent=self)
                return
            if not self._ask_phrase(
                    self._PHRASE_RECOVER,
                    "将把删除清单中携带快照的内容重新导入（可能包含当初有意删除的数据）。\n\n"
                    f"请输入确认短语“{self._PHRASE_RECOVER}”以继续："):
                return
            del_mode = "reverse"

        self.result = (apply_additions, del_mode)
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _ask_phrase(self, phrase: str, message: str) -> bool:
        from tkinter import simpledialog
        ans = simpledialog.askstring("操作确认", message, parent=self)
        return (ans or "").strip() == phrase

    def _cancel(self) -> None:
        self.result = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _center(self) -> None:
        self.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() - self.winfo_width()) // 2
        y = self.master.winfo_y() + (self.master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()
