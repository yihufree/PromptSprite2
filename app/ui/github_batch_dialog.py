# -*- coding: utf-8 -*-
"""
github_batch_dialog.py - GitHub 目录批量抓取：文件多选（第 5 期 5-c）
创建日期：2026-09-14

职责（只做"选哪些文件"，不联网、不抓取）：
  - 列出目录内的**文件**（名称 + 体积），默认勾选常见文档类型（`fetcher.BATCH_DEFAULT_EXTS`）；
  - 实时显示"已选 N 个 / 合计体积"，并按限额校验（单文件 ≤2MB、一次 ≤20 个、合计 ≤20MB）；
  - 确认后把勾选的 path 列表交给调用方（向导负责逐个抓取与合并）。

抓取规格与限额常量全部来自 `app/parser/fetcher.py`，本文件不重复定义。
"""
import customtkinter as ctk
from tkinter import messagebox

from ..parser import fetcher


class GithubBatchDialog(ctk.CTkToplevel):
    """GitHub 目录文件多选。`self.selected` = 确认后的 path 列表（取消为 []）"""

    def __init__(self, master, items: list, repo_label: str = "") -> None:
        super().__init__(master)
        self.selected = []
        self.items = fetcher.github_dir_files(items)
        self._vars = {}                     # path -> StringVar（"1"/"0"）
        self._boxes = []

        self.title("选择要批量抓取的文件")
        self.geometry("720x560")
        self.minsize(640, 460)
        self.transient(master)
        self.grab_set()

        pad = 16
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(self, text="GitHub 批量抓取：勾选要抓取的文件",
                     font=("Microsoft YaHei", 14, "bold"), anchor="w"
                     ).grid(row=0, column=0, sticky="ew", padx=pad, pady=(14, 2))
        ctk.CTkLabel(self, text=f"{repo_label or ''}　共 {len(self.items)} 个文件　"
                                f"{fetcher.batch_limit_label()}",
                     text_color="gray", font=("Microsoft YaHei", 11), anchor="w",
                     justify="left", wraplength=680
                     ).grid(row=1, column=0, sticky="ew", padx=pad)

        box = ctk.CTkScrollableFrame(self, height=340)
        box.grid(row=2, column=0, sticky="nsew", padx=pad, pady=(6, 6))
        default = set(fetcher.default_batch_selection(items))
        for it in self.items:
            path = str(it.get("path") or "")
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", pady=1)
            var = ctk.StringVar(value="1" if path in default else "0")
            cb = ctk.CTkCheckBox(row, text=str(it.get("name") or path), variable=var,
                                 onvalue="1", offvalue="0", command=self._refresh,
                                 font=("Microsoft YaHei", 12))
            cb.pack(side="left")
            ctk.CTkLabel(row, text=self._size_text(it.get("size")), width=90, anchor="e",
                         text_color="gray", font=("Microsoft YaHei", 10)).pack(side="right")
            self._vars[path] = var
            self._boxes.append(cb)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=pad, pady=(0, 14))
        ctk.CTkButton(foot, text="全选", width=72, fg_color="#8a94a6",
                      command=lambda: self._set_all(True)).pack(side="left")
        ctk.CTkButton(foot, text="全不选", width=80, fg_color="#8a94a6",
                      command=lambda: self._set_all(False)).pack(side="left", padx=(6, 0))
        ctk.CTkButton(foot, text="恢复默认（常见文档）", width=170, fg_color="#8a94a6",
                      command=self._reset_default).pack(side="left", padx=(6, 0))
        ctk.CTkButton(foot, text="取消", width=88, command=self.destroy).pack(side="right")
        ctk.CTkButton(foot, text="确定并抓取", width=112, fg_color="#2E8B57",
                      command=self._ok).pack(side="right", padx=(0, 8))
        self.sum_lbl = ctk.CTkLabel(foot, text="", font=("Microsoft YaHei", 11), anchor="w")
        self.sum_lbl.pack(side="left", padx=(12, 0))

        self._refresh()
        self._center()
        self.bind("<Return>", lambda _e=None: self._ok())
        self.bind("<Escape>", lambda _e=None: self.destroy())

    # ---- 内部 ---- #
    @staticmethod
    def _size_text(size) -> str:
        try:
            n = int(size or 0)
        except Exception:
            n = 0
        if n <= 0:
            return "—"
        if n < 1024:
            return f"{n}B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f}KB"
        return f"{n / 1024 / 1024:.2f}MB"

    def _picked(self) -> list:
        return [p for p, v in self._vars.items() if v.get() == "1"]

    def _set_all(self, on: bool) -> None:
        for v in self._vars.values():
            v.set("1" if on else "0")
        self._refresh()

    def _reset_default(self) -> None:
        default = set(fetcher.default_batch_selection(self.items))
        for p, v in self._vars.items():
            v.set("1" if p in default else "0")
        self._refresh()

    def _refresh(self) -> None:
        chk = fetcher.check_batch_limits(self.items, self._picked())
        text = (f"已选 {chk['count']} 个 · 合计 {self._size_text(chk['total_bytes'])}"
                f"（限 {fetcher.BATCH_MAX_FILES} 个 / 单文件 2MB / 合计 20MB）")
        try:
            self.sum_lbl.configure(text=text,
                                   text_color=("#D9534F" if not chk["ok"] else "gray"))
        except Exception:
            pass

    def _ok(self) -> None:
        chosen = self._picked()
        chk = fetcher.check_batch_limits(self.items, chosen)
        if not chk["ok"]:
            messagebox.showwarning("超出批量限额",
                                   "\n".join(chk["errors"]) + "\n\n请调整勾选后重试。",
                                   parent=self)
            return
        self.selected = chosen
        self.destroy()

    def _center(self) -> None:
        """屏幕水平居中 + 上边距 50（与字段管理系列对话框统一）"""
        from .field_manager_dialog import _place_top_centered
        _place_top_centered(self)
