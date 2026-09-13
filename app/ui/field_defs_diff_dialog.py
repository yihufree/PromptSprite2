# -*- coding: utf-8 -*-
"""
field_defs_diff_dialog.py - 导入前"字段定义差异"逐项确认对话框
创建日期：2026-09-13（第 4 期 4-c 收尾 + 用户口径：外来数据包与本地机的差异由使用者逐项选择）

用途：JSON 数据包随包携带 `field_defs`（字段定义）。导入前把"外来包 vs 本机"的差异列出来，
     由使用者逐项选择 **新增 / 覆盖 / 跳过**，避免"整包还原准确"与"不覆盖本机改名"两难。

约定：
  - 只列出 **有差异** 的项（new / diff）；"完全一致（same）"不显示、不需处理；
  - 默认：新增项＝新增；差异项＝**跳过（不覆盖本机）**；
  - 「全部新增 / 全部覆盖 / 全部跳过」可一键改；「取消」＝放弃本次导入（result=None）。
"""
import json
import os

import customtkinter as ctk

_STATUS_LABEL = {"new": "＋ 新增", "diff": "⚠ 差异", "same": "＝ 一致"}
_ACT_ADD = "新增"
_ACT_OVERWRITE = "覆盖本机"
_ACT_SKIP = "跳过"
_ACTS = (_ACT_ADD, _ACT_OVERWRITE, _ACT_SKIP)
_ACT_KEY = {_ACT_ADD: "add", _ACT_OVERWRITE: "overwrite", _ACT_SKIP: "skip"}


class FieldDefsDiffDialog(ctk.CTkToplevel):
    """字段定义差异确认（模态）。

    `result`：None＝取消（放弃导入）；否则 `{field_key: "add"|"overwrite"|"skip"}`。
    """

    def __init__(self, master, rows: list, pack_name: str = "") -> None:
        super().__init__(master)
        self.master = master
        self.result = None
        self._rows = [r for r in (rows or []) if r.get("status") in ("new", "diff")]
        self._vars = {}

        self.title("字段定义差异确认")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        pad = 18
        ctk.CTkLabel(self, text="导入前确认：数据包与本机的字段定义差异",
                     font=("Microsoft YaHei", 15, "bold")
                     ).grid(row=0, column=0, columnspan=2, padx=pad, pady=(14, 2), sticky="w")
        tip = ("数据包携带了字段定义。请逐项选择处理方式：\n"
               "　· 新增：把包内该字段加入本机；\n"
               "　· 覆盖本机：用包内显示名/类型/配置替换本机同名（field_key）字段；\n"
               "　· 跳过：保持本机不变。\n"
               "默认：新字段＝新增；已有但不同＝**跳过（不覆盖本机）**。")
        if pack_name:
            tip = f"数据包：{pack_name}\n" + tip
        ctk.CTkLabel(self, text=tip, justify="left", text_color="gray",
                     font=("Microsoft YaHei", 11)
                     ).grid(row=1, column=0, columnspan=2, padx=pad, pady=(0, 6), sticky="w")

        brow = ctk.CTkFrame(self, fg_color="transparent")
        brow.grid(row=2, column=0, columnspan=2, padx=pad, pady=(0, 4), sticky="w")
        ctk.CTkLabel(brow, text=f"共 {len(self._rows)} 项有差异：",
                     font=("Microsoft YaHei", 11)).pack(side="left")
        ctk.CTkButton(brow, text="全部新增", width=84, height=26,
                      command=lambda: self._set_all(_ACT_ADD)).pack(side="left", padx=(8, 0))
        ctk.CTkButton(brow, text="全部覆盖", width=84, height=26, fg_color="#D08A00",
                      command=lambda: self._set_all(_ACT_OVERWRITE)).pack(side="left", padx=(6, 0))
        ctk.CTkButton(brow, text="全部跳过", width=84, height=26, fg_color="#8a94a6",
                      command=lambda: self._set_all(_ACT_SKIP)).pack(side="left", padx=(6, 0))

        body = ctk.CTkScrollableFrame(self, width=660, height=260)
        body.grid(row=3, column=0, columnspan=2, padx=pad, pady=(0, 6))
        if not self._rows:
            ctk.CTkLabel(body, text="（没有差异，无需处理）", text_color="#9aa4b1",
                         font=("Microsoft YaHei", 11)).pack(anchor="w", padx=4, pady=4)
        for r in self._rows:
            line = ctk.CTkFrame(body, fg_color="transparent")
            line.pack(fill="x", pady=2)
            ctk.CTkLabel(line, text=_STATUS_LABEL.get(r["status"], r["status"]), width=64,
                         anchor="w", font=("Microsoft YaHei", 11),
                         text_color=("#2E8B57" if r["status"] == "new" else "#D08A00")
                         ).pack(side="left")
            name = r["display_name"] or r["field_key"]
            local = (r.get("current") or {}).get("display_name") or "（本机无）"
            ctk.CTkLabel(line, text=f"{name}（{r['field_key']}）　本机：{local}",
                         width=330, anchor="w", font=("Microsoft YaHei", 11)
                         ).pack(side="left")
            default = _ACT_ADD if r["status"] == "new" else _ACT_SKIP
            var = ctk.StringVar(value=default)
            ctk.CTkOptionMenu(line, width=120, variable=var, values=list(_ACTS)
                              ).pack(side="left", padx=(6, 0))
            self._vars[r["field_key"]] = var

        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.grid(row=4, column=0, columnspan=2, sticky="e", padx=pad, pady=(4, 14))
        ctk.CTkButton(btn, text="继续导入", width=104, fg_color="#2E8B57",
                      command=self._ok).pack(side="left", padx=4)
        ctk.CTkButton(btn, text="取消导入", width=104,
                      command=self.destroy).pack(side="left", padx=4)

        self._center()

    def _set_all(self, act: str) -> None:
        """一键设置（保持语义合理）：『新增』不影响差异项；『覆盖』不影响新增项"""
        for k, var in self._vars.items():
            row = next((r for r in self._rows if r["field_key"] == k), {})
            st = row.get("status")
            if act == _ACT_ADD and st == "diff":
                continue                      # 差异项＝本机已有，"新增"无意义
            if act == _ACT_OVERWRITE and st == "new":
                continue                      # 新增项＝本机没有，"覆盖"无意义
            var.set(act)

    def _ok(self) -> None:
        self.result = {k: _ACT_KEY.get(v.get(), "skip") for k, v in self._vars.items()}
        self.destroy()

    def _center(self) -> None:
        """屏幕水平居中 + 上边距 50（与字段管理系列对话框统一）"""
        from .field_manager_dialog import _place_top_centered
        _place_top_centered(self)


# ---------------------------------------------------------------------- #
# 供导入路径复用的便捷函数
# ---------------------------------------------------------------------- #
def confirm_field_defs_diff(master, db, defs: list, pack_name: str = "") -> tuple:
    """按需弹出"字段定义差异"确认（**无差异时不打扰**）。

    返回 `(proceed, resolver)`：proceed=False 表示用户取消 → 调用方应放弃本次导入；
    resolver 为 `None` 表示无需处理（或全部一致）。
    """
    try:
        rows = [r for r in db.field_defs_diff(defs) if r.get("status") in ("new", "diff")]
    except Exception:
        return True, None
    if not rows:
        return True, None
    dlg = FieldDefsDiffDialog(master, rows, pack_name=pack_name)
    try:
        master.wait_window(dlg)
    except Exception:
        pass
    if dlg.result is None:
        return False, None
    decisions = dict(dlg.result)
    return True, (lambda _rows, dec=decisions: dec)


def confirm_field_defs_file(master, db, path: str) -> tuple:
    """从 JSON 文件读取 `field_defs` 并走差异确认；返回 (proceed, resolver)。"""
    try:
        with open(path, encoding="utf-8") as f:
            defs = json.load(f).get("field_defs") or []
    except Exception:
        return True, None
    return confirm_field_defs_diff(master, db, defs, pack_name=os.path.basename(path))
