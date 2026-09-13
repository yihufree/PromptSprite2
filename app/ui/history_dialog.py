# -*- coding: utf-8 -*-
"""
history_dialog.py - 删除历史/回收站 与 新增历史（2026-09-07 第2/3条改进）

- RecycleBinDialog   ：详情区底部"♻ 删除历史 / 回收站"。
                      列出被删条目（含删除时间/来源/原位置），支持【恢复】到原位置
                      （原分类已被删除则恢复为未分类），以及【彻底删除】【清空】。
- RecentAdditionsDialog：详情区底部"🕒 新增历史"。
                      按 今日/近3天/近7天/近30天/全部 查看最近新增条目，可一键复制提示词。

说明：
  锁定态下两个窗口均只读（回收站的 恢复/彻底删除/清空 被禁用，仅可查看）。
"""
from datetime import datetime, timedelta

import customtkinter as ctk
import pyperclip

from tkinter import messagebox


def _center(win, master) -> None:
    win.update_idletasks()
    x = master.winfo_rootx() + (master.winfo_width() - win.winfo_width()) // 2
    y = master.winfo_rooty() + (master.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    win.lift()


def _copy(text: str, status_cb) -> None:
    if not text.strip():
        status_cb("内容为空，未复制")
        return
    try:
        pyperclip.copy(text)
        status_cb("✅ 已复制")
    except Exception:
        status_cb("复制失败，请检查剪贴板", error=True)


# --------------------------------------------------------------------------- #
# 删除历史 / 回收站
# --------------------------------------------------------------------------- #
class RecycleBinDialog(ctk.CTkToplevel):
    def __init__(self, master, db, locked: bool = False):
        super().__init__(master)
        self.db = db
        self._locked = locked
        self._master = master
        self.title("删除历史 / 回收站")
        self.geometry("1000x580")
        self.minsize(760, 400)
        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(self, text="回收站：被删除的条目可在此恢复（彻底删除后不可恢复）",
                     font=("Microsoft YaHei", 13, "bold"),
                     text_color="#6b7280").pack(padx=16, pady=(14, 2), anchor="w")
        self.count_lbl = ctk.CTkLabel(self, text="", text_color="gray",
                                      font=("Microsoft YaHei", 11))
        self.count_lbl.pack(padx=18, anchor="w")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="#f5f7fa",
                                             corner_radius=10)
        self.scroll.pack(fill="both", expand=True, padx=14, pady=(6, 4))

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(foot, text="🛈 恢复后会保留原内容/收藏/位置；原分类已删除则回到「未分类」。",
                     text_color="gray", font=("Microsoft YaHei", 11)
                     ).pack(side="left")
        self.clear_btn = ctk.CTkButton(foot, text="🗑 清空回收站", width=130,
                                       fg_color="#D9534F", hover_color="#b33a35",
                                       state="disabled" if self._locked else "normal",
                                       command=self._clear_all)
        self.clear_btn.pack(side="right", padx=4)
        ctk.CTkButton(foot, text="关闭", width=80,
                      command=self.destroy).pack(side="right", padx=4)

        self.status_lbl = ctk.CTkLabel(self, text="", text_color="#2E8B57",
                                       font=("Microsoft YaHei", 11))
        self.status_lbl.pack(anchor="w", padx=18, pady=(0, 6))
        self._render()
        _center(self, master)

    # ---- 内部 ----
    def _status(self, msg: str, error: bool = False) -> None:
        self.status_lbl.configure(text=msg,
                                  text_color=("#D9534F" if error else "#2E8B57"))

    def _render(self) -> None:
        for w in self.scroll.winfo_children():
            w.destroy()
        items = self.db.list_trash()
        self.count_lbl.configure(text=f"共 {len(items)} 条（按删除时间倒序）")
        if not items:
            ctk.CTkLabel(self.scroll, text="（回收站为空，没有可恢复的条目）",
                         text_color="gray").pack(pady=40)
            return
        for it in items:
            self._add_row(it)

    def _add_row(self, it: dict) -> None:
        payload = it.get("payload") or {}
        name = it.get("name") or "（未命名）"
        chain = payload.get("chain") or []
        loc = " › ".join(chain) if chain else "未分类"
        reason = it.get("reason") or "手动删除"
        deleted = it.get("deleted_at") or ""
        intro = (payload.get("intro") or "").strip() or "（无介绍）"

        row = ctk.CTkFrame(self.scroll, fg_color="#ffffff", corner_radius=8,
                           border_width=1, border_color="#dde4ec")
        row.pack(fill="x", padx=4, pady=3)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=10, pady=6)
        ctk.CTkLabel(left, text=f"{name}　（原位置：{loc}）",
                     font=("Microsoft YaHei", 12, "bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(left, text=f"{intro[:64]}{'…' if len(intro) > 64 else ''}",
                     text_color="gray", anchor="w", justify="left", wraplength=620,
                     font=("Microsoft YaHei", 11)).pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(left, text=f"删除于 {deleted}　·　来源：{reason}",
                     text_color="#9aa4b1", anchor="w",
                     font=("Microsoft YaHei", 10)).pack(fill="x", pady=(2, 0))

        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.pack(side="right", padx=8, pady=6)
        if not self._locked:
            ctk.CTkButton(btns, text="↩ 恢复", width=74, height=28,
                          fg_color="#2E8B57", hover_color="#256e46",
                          command=lambda tid=it["id"]: self._restore_one(tid)
                          ).pack(side="left", padx=3)
            ctk.CTkButton(btns, text="彻底删除", width=84, height=28,
                          fg_color="#D9534F", hover_color="#b33a35",
                          command=lambda tid=it["id"]: self._purge_one(tid)
                          ).pack(side="left", padx=3)
        else:
            ctk.CTkLabel(btns, text="（锁定中：只读）", text_color="gray",
                         font=("Microsoft YaHei", 11)).pack(side="left", padx=6)

    def _restore_one(self, trash_id: int) -> None:
        rows = self.db.list_trash()
        row = next((t for t in rows if t["id"] == trash_id), None)
        name = row["name"] if row else "该条目"
        if not messagebox.askyesno("恢复确认", f"确定恢复【{name}】吗？", parent=self):
            return
        try:
            new_id = self.db.restore_from_trash(trash_id)
        except Exception as exc:
            self._status(f"恢复失败：{exc}", error=True)
            return
        if new_id is None:
            self._status("恢复失败：记录不存在", error=True)
            return
        self._render()
        self._status(f"✅ 已恢复「{name}」（如主视图未显示，可切换/刷新对应分类）")
        try:  # 通知主窗口刷新当前视图
            self._master._restore_view()
        except Exception:
            pass

    def _purge_one(self, trash_id: int) -> None:
        row = next((t for t in self.db.list_trash() if t["id"] == trash_id), None)
        name = row["name"] if row else "该条目"
        if not messagebox.askyesno(
                "彻底删除", f"彻底删除【{name}】？\n此操作不可恢复，关联图片一并释放。",
                parent=self):
            return
        try:
            self.db.purge_trash(trash_id)
        except Exception as exc:
            self._status(f"删除失败：{exc}", error=True)
            return
        self._render()
        self._status(f"已彻底删除「{name}」")

    def _clear_all(self) -> None:
        n = self.db.count_trash()
        if n == 0:
            return
        if not messagebox.askyesno(
                "清空回收站",
                f"将彻底删除回收站中全部 {n} 条（不可恢复，关联图片一并释放）。\n确定清空？",
                parent=self):
            return
        if not messagebox.askyesno("再次确认", "🚨 清空后不可恢复，请再次确认。", parent=self):
            return
        self.db.clear_trash()
        self._render()
        self._status(f"回收站已清空（{n} 条）")


# --------------------------------------------------------------------------- #
# 新增历史
# --------------------------------------------------------------------------- #
class RecentAdditionsDialog(ctk.CTkToplevel):
    """查看最近一定时间范围内新增的条目（可按 今日/近3/7/30天/全部）。"""

    _RANGES = (("今日", 0), ("近 3 天", 3), ("近 7 天", 7),
               ("近 30 天", 30), ("全部", -1))

    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self._master = master
        self._range = 0   # 今日
        self.title("新增历史")
        self.geometry("1000x580")
        self.minsize(760, 400)
        self.transient(master)
        self.grab_set()

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(top, text="新增历史：最近新增/创建的提示词条目",
                     font=("Microsoft YaHei", 13, "bold")).pack(side="left")
        self.seg = ctk.CTkSegmentedButton(top, values=[r[0] for r in self._RANGES],
                                          command=self._on_range)
        self.seg.set("今日")
        self.seg.pack(side="right")

        self.count_lbl = ctk.CTkLabel(self, text="", text_color="gray",
                                      font=("Microsoft YaHei", 11))
        self.count_lbl.pack(padx=18, anchor="w")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="#f5f7fa",
                                             corner_radius=10)
        self.scroll.pack(fill="both", expand=True, padx=14, pady=(6, 4))

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(foot, text="提示：复制请使用右侧 中文/英文/全部 按钮",
                     text_color="gray", font=("Microsoft YaHei", 11)
                     ).pack(side="left")
        ctk.CTkButton(foot, text="关闭", width=80,
                      command=self.destroy).pack(side="right")
        self.status_lbl = ctk.CTkLabel(self, text="", text_color="#2E8B57",
                                       font=("Microsoft YaHei", 11))
        self.status_lbl.pack(anchor="w", padx=18, pady=(0, 6))
        self._render()
        _center(self, master)

    def _status(self, msg: str, error: bool = False) -> None:
        self.status_lbl.configure(text=msg,
                                  text_color=("#D9534F" if error else "#2E8B57"))

    def _since(self) -> str:
        days = self._RANGES[self._range][1]
        if days == 0:
            return datetime.now().strftime("%Y-%m-%d 00:00:00")
        if days < 0:
            return "1970-01-01 00:00:00"
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    def _on_range(self, label: str) -> None:
        for i, (name, _d) in enumerate(self._RANGES):
            if name == label:
                self._range = i
                break
        self._render()

    def _render(self) -> None:
        for w in self.scroll.winfo_children():
            w.destroy()
        items = self.db.list_entries_added_since(self._since())
        self.count_lbl.configure(text=f"共 {len(items)} 条（按新增时间倒序）")
        if not items:
            ctk.CTkLabel(self.scroll, text="（该时间范围内暂无新增条目）",
                         text_color="gray").pack(pady=40)
            return
        for e in items:
            self._add_row(e)

    def _add_row(self, e: dict) -> None:
        name = e.get("name") or "（未命名）"
        loc = self.db.category_path(e.get("category_id"))
        created = e.get("created_at") or ""
        intro = (e.get("intro") or "").strip() or "（无介绍）"

        row = ctk.CTkFrame(self.scroll, fg_color="#ffffff", corner_radius=8,
                           border_width=1, border_color="#dde4ec")
        row.pack(fill="x", padx=4, pady=3)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=10, pady=6)
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=f"{name}", font=("Microsoft YaHei", 12, "bold"),
                     anchor="w").pack(side="left")
        ctk.CTkLabel(head, text=f"　{loc}", text_color="#25639c",
                     font=("Microsoft YaHei", 11), anchor="w").pack(side="left")
        ctk.CTkLabel(left, text=f"{intro[:64]}{'…' if len(intro) > 64 else ''}",
                     text_color="gray", anchor="w", justify="left", wraplength=600,
                     font=("Microsoft YaHei", 11)).pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(left, text=f"新增于 {created}", text_color="#9aa4b1",
                     anchor="w", font=("Microsoft YaHei", 10)).pack(fill="x")

        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.pack(side="right", padx=8, pady=6)
        for txt, mode in (("复制中文", "cn"), ("复制英文", "en"), ("复制全部", "all")):
            ctk.CTkButton(btns, text=txt, width=76, height=26, fg_color="#6b7280",
                          hover_color="#575e68",
                          command=lambda t=txt, m=mode, ent=e: self._copy_prompt(ent, m)
                          ).pack(side="left", padx=2)

    def _copy_prompt(self, e: dict, mode: str) -> None:
        cn = (e.get("prompt_cn") or "").strip()
        en = (e.get("prompt_en") or "").strip()
        if mode == "cn":
            text = cn
        elif mode == "en":
            text = en
        else:
            text = f"{cn}\n\n{en}".strip()
        _copy(text, self._status)
