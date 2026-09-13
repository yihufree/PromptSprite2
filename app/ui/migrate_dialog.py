# -*- coding: utf-8 -*-
"""
migrate_dialog.py - 老版本数据迁移向导（2026-08-29 施工新增，M5）

流程（用户决策 1/2）：
  1. 展示"未归属根目录 → 项目类别"完整映射表（默认值取自 PROJECT_DOMAIN_MAPPING）；
  2. 用户逐项核对/调整目标项目类别；
  3. 执行迁移：已选择 → 归入所选项目；默认"未明确分类" → 兜底归入；
  4. 取消/关闭：不移动任何数据（保留在"未分配"，可稍后通过菜单再次打开向导）。

关闭后读取 result：'ok'=已执行迁移；'cancel'=未执行。
"""
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from .. import config


class MigrateDialog(ctk.CTkToplevel):
    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.result = "cancel"
        self._rows = []          # [{'domain': dict, 'var': tk.StringVar}]
        self.title("数据迁移向导")
        self.geometry("600x560")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self._build()
        self._center(master)

    # ------------------------------------------------------------------ #
    def _default_project(self, domain_name: str) -> str:
        """按映射表取默认项目类别；未命中 → "未明确分类"（用户决策 1 兜底）"""
        for pname, doms in config.PROJECT_DOMAIN_MAPPING.items():
            if domain_name in doms:
                return pname
        return config.PROJECT_FALLBACK

    def _build(self) -> None:
        pad = 16
        ctk.CTkLabel(self, text="老版本数据迁移向导",
                     font=("Microsoft YaHei", 15, "bold")
                     ).pack(padx=pad, pady=(14, 2), anchor="w")
        ctk.CTkLabel(self,
                     text="以下根目录尚未归入“项目类别”，请核对/调整目标后点击“执行迁移”；\n"
                          "未匹配映射的默认归入“未明确分类”。取消则不做任何改动。",
                     text_color="gray", justify="left", font=("Microsoft YaHei", 11)
                     ).pack(padx=pad, pady=(0, 8), anchor="w")

        unassigned = self.db.list_unassigned_domains()
        if not unassigned:
            ctk.CTkLabel(self, text="✅ 所有根目录均已归入项目类别，无需迁移。",
                         font=("Microsoft YaHei", 13)).pack(pady=30)
            ctk.CTkButton(self, text="关闭", width=96, command=self._close
                          ).pack(pady=(0, 16))
            return

        # 表头
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=pad)
        ctk.CTkLabel(head, text="根目录", width=220,
                     font=("Microsoft YaHei", 12, "bold")).pack(side="left", padx=(10, 4))
        ctk.CTkLabel(head, text="归入项目类别", width=260,
                     font=("Microsoft YaHei", 12, "bold")).pack(side="left", padx=4)

        # 滚动区
        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=pad, pady=4)
        project_names = [p["name"] for p in self.db.list_projects()]
        if config.PROJECT_FALLBACK not in project_names:
            project_names.append(config.PROJECT_FALLBACK)
        for d in unassigned:
            row = ctk.CTkFrame(self.scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=d["name"], width=220, anchor="w",
                         font=("Microsoft YaHei", 12)).pack(side="left", padx=(10, 4))
            var = tk.StringVar(value=self._default_project(d["name"]))
            ctk.CTkOptionMenu(row, values=project_names, variable=var, width=260
                              ).pack(side="left", padx=4)
            self._rows.append({"domain": d, "var": var})

        # 按钮
        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.pack(fill="x", padx=pad, pady=(6, 14))
        ctk.CTkButton(btn, text="执行迁移", width=120, fg_color="#2E8B57",
                      command=self._do_migrate).pack(side="right", padx=4)
        ctk.CTkButton(btn, text="取消", width=96,
                      command=self._close).pack(side="right", padx=4)

    # ------------------------------------------------------------------ #
    def _do_migrate(self) -> None:
        """执行迁移：按每行选择归入对应项目类别（默认"未明确分类"兜底）"""
        moved, fallback = 0, 0
        try:
            for r in self._rows:
                chosen = r["var"].get().strip()
                if chosen and chosen != config.PROJECT_FALLBACK:
                    pid = self.db.ensure_project(chosen)
                    self.db.move_domain_to_project(r["domain"]["id"], pid)
                    moved += 1
                else:
                    pid = self.db.ensure_project(config.PROJECT_FALLBACK)
                    self.db.move_domain_to_project(r["domain"]["id"], pid)
                    fallback += 1
        except Exception as exc:
            messagebox.showerror("迁移失败", f"迁移过程出错：\n{exc}", parent=self.master)
            self._close()
            return
        self.result = "ok"
        self._close()
        messagebox.showinfo(
            "迁移完成",
            f"✅ 已归入所选项目类别 {moved} 个；\n"
            f"归入“{config.PROJECT_FALLBACK}” {fallback} 个。\n"
            f"数据未丢失，可在主界面按项目类别浏览。",
            parent=self.master)

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _center(self, master) -> None:
        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_y() + (master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()
