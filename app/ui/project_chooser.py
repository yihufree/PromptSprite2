# -*- coding: utf-8 -*-
"""
project_chooser.py - 项目类别选择弹窗（共用，2026-08-29 抽取）
来源：由 main_window._choose_project 抽取为公共函数，供主窗口/快速新建等复用，
减少冗余代码（审核报告冗余项优化）。
"""
import customtkinter as ctk


def choose_project(master, db, title: str, message: str,
                   include_unassigned: bool = False):
    """弹窗选择一个项目类别；选中返回 (True, pid)，取消返回 None。

    - include_unassigned=True 时提供"🗂 未分配（清除归属）"选项（pid 为 None）；
    - 无项目类别时返回 None（调用方自行提示）。
    """
    projects = db.list_projects()
    if not projects:
        return None
    result = {"ok": False, "pid": None}
    win = ctk.CTkToplevel(master)
    win.title(title)
    win.resizable(False, False)
    win.transient(master)
    win.grab_set()
    ctk.CTkLabel(win, text=message, font=("Microsoft YaHei", 13, "bold")
                 ).pack(padx=20, pady=(14, 8))
    box = ctk.CTkFrame(win, fg_color="transparent")
    box.pack(padx=20, pady=4)

    def _pick(pid):
        result["ok"] = True
        result["pid"] = pid
        win.destroy()

    for p in projects:
        ctk.CTkButton(box, text=p["name"], width=200, anchor="w",
                      command=lambda pid=p["id"]: _pick(pid)).pack(fill="x", pady=2)
    if include_unassigned:
        ctk.CTkButton(box, text="🗂 未分配（清除归属）", width=200, anchor="w",
                      command=lambda: _pick(None)).pack(fill="x", pady=2)
    ctk.CTkButton(win, text="取消", width=88,
                  command=win.destroy).pack(pady=(8, 14))
    win.update_idletasks()
    x = master.winfo_x() + (master.winfo_width() - win.winfo_width()) // 2
    y = master.winfo_y() + (master.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    win.lift()
    master.wait_window(win)
    return (True, result["pid"]) if result["ok"] else None
