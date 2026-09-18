# -*- coding: utf-8 -*-
"""tag_cleanup_dialog.py —— 「🧹 清理垃圾标签」对话框（2026-09-17 新增）

**用途**：把库里**已经存在**的"垃圾标签"扫出来、让你**先看清单再决定**是否清理。
（今后不再产生该类标签——打标引擎侧已加质量闸门，见 `tagger_engine.is_noise_tag()`。）

两类处理（口径由数据层 `app/tag_cleanup.py` 统一给出）：
    A 类 **真噪声**（纯数字 `12`/`234`、十六进制色值 `000000`/`1A2332`、内部含标点
    `古风/汉服`）→ **删除**标签及其关联；
    B 类 **边缘标点可洗净**（`[世界]` → `世界`）→ **重命名**；若目标名已存在则**合并关联**。

**安全**：
    · 只改 `tags` / `entry_tags` 两表，**不动任何条目内容**；
    · 每次执行**先整库备份**到 `data/backup/prompts_precleanjunk_<时间戳>.db`
      （独立前缀，不参与任何自动清理，可手工回滚）；
    · 执行前二次确认，并如实提示"标签变化会进入当日变更包"。

**入口**：设置 → 标签与词表 页 → 「🧹 清理垃圾标签…」（经主窗口 `_open_tag_cleanup`）。
"""
import customtkinter as ctk

from .. import tag_cleanup
from .ui_common import C_DANGER as _C_DANGER, C_WARN as _C_WARN, C_OK as _C_OK  # 2026-09-17（U-2）：主色常量

_STATE_A = _C_DANGER     # A 类：红（将删除）
_STATE_B = _C_WARN     # B 类：橙（将重命名）


class TagCleanupDialog(ctk.CTkToplevel):
    """垃圾标签清理：扫描 → 预览 → 确认 → 执行（含自动备份）。"""

    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.master = master
        self.title("🧹 清理垃圾标签")
        self.transient(master)
        self.grab_set()
        try:
            _sh = self.winfo_screenheight()
        except Exception:
            _sh = 900
        self.geometry("820x%d" % max(520, min(680, int(_sh * 0.78))))
        try:
            self.minsize(760, 480)
        except Exception:
            pass
        self._data = None
        self._build()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        pad = 14
        self.status = ctk.CTkLabel(self, text="", font=("Microsoft YaHei", 11),
                                   text_color="#5b6b7c", anchor="w", justify="left")
        self.status.pack(side="bottom", fill="x", padx=pad, pady=(0, 8))

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=pad, pady=(10, 2))
        ctk.CTkLabel(top, text="说明：本工具清理**历史遗留**的垃圾标签（今后打标不会再产生）。"
                               "只改标签表，不动条目内容；每次执行前会自动整库备份。",
                     font=("Microsoft YaHei", 11), text_color="#5b6b7c", anchor="w",
                     justify="left", wraplength=760).pack(side="left")

        # 汇总
        self.sum = ctk.CTkLabel(self, text="", font=("Microsoft YaHei", 12, "bold"),
                                anchor="w", justify="left")
        self.sum.pack(fill="x", padx=pad, pady=(6, 4))

        # 列表（滚动）
        head = ctk.CTkFrame(self, fg_color="#eaf1f9")
        head.pack(fill="x", padx=pad, pady=(2, 0))
        for _txt, _w in (("类别", 70), ("标签名", 240), ("关联条目", 80), ("处理 / 原因", 300)):
            ctk.CTkLabel(head, text=_txt, width=_w, font=("Microsoft YaHei", 11, "bold"),
                         text_color="#5a6f88", anchor="w").pack(side="left", padx=4, pady=4)
        self.box = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.box.pack(fill="both", expand=True, padx=pad, pady=(0, 4))

        # 按钮
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=pad, pady=(4, 4))
        ctk.CTkButton(bar, text="🔄 重新扫描", width=110, fg_color="#8a94a6",
                      font=("Microsoft YaHei", 11), command=self._rescan).pack(side="left")
        ctk.CTkButton(bar, text="✕ 关闭", width=90, font=("Microsoft YaHei", 11),
                      command=self.destroy).pack(side="right")
        self.btn_apply = ctk.CTkButton(bar, text="🧹 执行清理", width=130, fg_color=_C_DANGER,
                                       font=("Microsoft YaHei", 11, "bold"),
                                       command=self._apply)
        self.btn_apply.pack(side="right", padx=(0, 8))

        self._rescan()

    # ------------------------------------------------------------------ #
    # 数据 → 界面
    # ------------------------------------------------------------------ #
    def _rescan(self) -> None:
        """扫描当前库并刷新列表（只读）。"""
        try:
            self._data = tag_cleanup.scan(self.db.conn)
        except Exception as exc:
            self._data = None
            self.sum.configure(text="⚠ 扫描失败：%s" % exc, text_color=_STATE_A)
            return
        d = self._data
        n_a, n_b = len(d["drop"]), len(d["rename"])
        self.sum.configure(
            text="当前标签 %d 个 ｜ 标签关联 %d 条　→　需清理：A 类（删除）%d 个、B 类（重命名）%d 个"
                 % (d["total"], d["links"], n_a, n_b),
            text_color=(_C_OK if (n_a or n_b) else "#5b6b7c"))

        for _w in self.box.winfo_children():
            _w.destroy()
        if not n_a and not n_b:
            ctk.CTkLabel(self.box, text="✓ 没有发现垃圾标签（本库很干净）",
                         text_color=_C_OK, font=("Microsoft YaHei", 11)
                         ).pack(anchor="w", padx=8, pady=10)
        for r in d["drop"]:
            self._row("A 删除", r["name"], r["n"], r["reason"], _STATE_A)
        for r in d["rename"]:
            self._row("B 重命名", r["name"], r["n"], "→ %s" % r["to"], _STATE_B)

        try:
            self.btn_apply.configure(state=("normal" if (n_a or n_b) else "disabled"))
        except Exception:
            pass

    def _row(self, kind: str, name: str, n: int, note: str, color: str) -> None:
        row = ctk.CTkFrame(self.box, fg_color="transparent")
        row.pack(fill="x", pady=1)
        ctk.CTkLabel(row, text=kind, width=70, font=("Microsoft YaHei", 11),
                     anchor="w", text_color=color).pack(side="left", padx=4)
        ctk.CTkLabel(row, text=name, width=240, font=("Microsoft YaHei", 11),
                     anchor="w").pack(side="left", padx=4)
        ctk.CTkLabel(row, text=str(n), width=80, font=("Microsoft YaHei", 11),
                     anchor="w").pack(side="left", padx=4)
        ctk.CTkLabel(row, text=note, width=300, font=("Microsoft YaHei", 11),
                     anchor="w", text_color="#5b6b7c").pack(side="left", padx=4)

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def _apply(self) -> None:
        """二次确认 → 自动备份 → 执行清理 → 刷新界面与主窗口。"""
        from tkinter import messagebox
        d = self._data or {}
        n_a, n_b = len(d.get("drop") or []), len(d.get("rename") or [])
        if not (n_a or n_b):
            return
        if not messagebox.askyesno(
                "确认清理垃圾标签",
                "将执行：\n"
                "　· A 类：删除 %d 个垃圾标签及其关联\n"
                "　· B 类：重命名 %d 个（目标名已存在则合并关联）\n\n"
                "【只改标签表，不动任何条目内容】\n"
                "执行前会自动整库备份到 data\\backup\\。\n"
                "（标签变化会进入当日变更包，属正常现象）\n\n确定继续吗？" % (n_a, n_b),
                parent=self):
            return

        # ① 备份（失败即中止，不冒险；backup_before_clean 内部会自建 backup 目录）
        try:
            _bak = tag_cleanup.backup_before_clean(self.db.db_path)
        except Exception as exc:
            messagebox.showerror("备份失败", "清理前备份失败，已中止：\n%s" % exc, parent=self)
            return

        # ② 执行
        try:
            st = tag_cleanup.apply(self.db.conn)
        except Exception as exc:
            messagebox.showerror("清理失败", str(exc), parent=self)
            self._rescan()
            return

        # ③ 刷新本对话框 + 主窗口（标签页 / 条目区标签缓存）
        self._rescan()
        try:
            self._entry_refresh()
        except Exception:
            pass
        self.status.configure(
            text="✅ 完成：删除标签 %d 个 / 关联 %d 条；重命名 %d 个、合并 %d 个。备份：%s"
                 % (st["drop_tags"], st["drop_links"], st["renamed"], st["merged"], _bak),
            text_color=_C_OK)

    def _entry_refresh(self) -> None:
        """通知主窗口刷新受影响的视图（标签页面 + 条目区标签缓存）。"""
        m = self.master
        try:
            m._entry_tags_cache = {}          # 标签已变 → 作废预取缓存
        except Exception:
            pass
        fn = getattr(m, "_refresh_tag_page", None)
        if callable(fn):
            fn()
        fn2 = getattr(m, "refresh_domains", None)
        if callable(fn2):
            fn2(silent=True)
