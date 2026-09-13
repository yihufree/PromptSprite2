# -*- coding: utf-8 -*-
"""
import_wizard_dialog.py - T1 离线"网上资源结构化"向导（第 4 期 4-c）
创建日期：2026-09-13

三步向导（模态）：
  第 1 步 选择源：粘贴文本（自动探测分隔符）/ 本地 CSV·TSV·TXT / HTML 表格 / Markdown 表格；
  第 2 步 映射：**4 列层级映射**（项目类别/根目录/一级/二级，空单元格继承上一行，允许跳层）
               + **字段映射**（内置 10 字段 / 自定义字段 / 标签，标签列可多值）；
  第 3 步 预览与出口：树预览 + 新增/复用/判重统计 + 警告 →
               「保存数据文件（JSON v5）」/「保存并导入」（导入前自动快照 + 失败可一键回滚）。

数据层全部复用 `app/parser/hierarchy_import.py`（结构化）与 `app/parser/json_io.py`（导入），
本文件只负责界面与流程编排。
"""
import os
import shutil
import tempfile
from datetime import datetime

import customtkinter as ctk
from tkinter import filedialog, messagebox

from .. import backup
from ..database import Database
from ..parser import excel_io
from ..parser import hierarchy_import as himp
from ..parser import fetcher
from ..parser import json_io
from .field_defs_diff_dialog import confirm_field_defs_file  # 2026-09-13：字段定义差异逐项确认
from .github_batch_dialog import GithubBatchDialog          # 2026-09-14：5-c 批量多选
from .progress_dialog import ProgressDialog

_IGNORE = himp.IGNORE_TARGET
_NO_MAP = "（不映射）"

_LAYER_HINTS = (("project", ("项目类别", "项目", "类别")),
                ("domain", ("根目录", "领域", "目录")),
                ("l1", ("一级",)),
                ("l2", ("二级",)))
_FIELD_HINTS = (("name", ("名称", "标题", "案例名", "条目")),
                ("prompt_cn", ("提示词", "中文提示", "prompt")),
                ("prompt_en", ("英文提示", "english")),
                ("intro", ("介绍", "简介", "说明")),
                ("origin", ("溯源", "来源", "出处", "链接")),
                ("features", ("特征", "特点", "风格")),
                ("image_plan", ("获取方案", "图片链接", "图片url", "配图url")))


class _NewFieldDialog(ctk.CTkToplevel):
    """向导内"新建自定义字段"（名称 + 类型）；`result = (name, field_type)` 或 None"""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.result = None
        self.title("新建自定义字段")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        from .field_manager_dialog import TYPE_LABELS, _place_top_centered

        pad = 18
        ctk.CTkLabel(self, text="新建自定义字段", font=("Microsoft YaHei", 14, "bold")
                     ).grid(row=0, column=0, columnspan=2, padx=pad, pady=(14, 2), sticky="w")
        ctk.CTkLabel(self, text="建好后可在『字段映射』里把它选为某列的目标。",
                     text_color="gray", font=("Microsoft YaHei", 11)
                     ).grid(row=1, column=0, columnspan=2, padx=pad, pady=(0, 8), sticky="w")
        ctk.CTkLabel(self, text="显示名称", font=("Microsoft YaHei", 12)
                     ).grid(row=2, column=0, padx=pad, pady=6, sticky="w")
        self.name_entry = ctk.CTkEntry(self, width=240, placeholder_text="如：来源链接")
        self.name_entry.grid(row=2, column=1, padx=pad, pady=6, sticky="w")
        ctk.CTkLabel(self, text="字段类型", font=("Microsoft YaHei", 12)
                     ).grid(row=3, column=0, padx=pad, pady=6, sticky="w")
        self.type_menu = ctk.CTkOptionMenu(self, width=240,
                                           values=[lbl for _k, lbl in TYPE_LABELS])
        self.type_menu.grid(row=3, column=1, padx=pad, pady=6, sticky="w")
        self.type_menu.set(TYPE_LABELS[0][1])
        self._type_map = {lbl: k for k, lbl in TYPE_LABELS}

        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.grid(row=4, column=0, columnspan=2, sticky="e", padx=pad, pady=(8, 14))
        ctk.CTkButton(btn, text="创建", width=92, fg_color="#2E8B57",
                      command=self._ok).pack(side="left", padx=4)
        ctk.CTkButton(btn, text="取消", width=92, command=self.destroy).pack(side="left", padx=4)
        self.name_entry.bind("<Return>", lambda _e=None: self._ok())
        _place_top_centered(self)

    def _ok(self) -> None:
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入字段显示名称。", parent=self)
            return
        self.result = (name, self._type_map.get(self.type_menu.get(), "text"))
        self.destroy()


class _L2RulesDialog(ctk.CTkToplevel):
    """关键词规则编辑（5-d-1）：每行 `二级分类名 = 关键词1, 关键词2`。

    `result` = 规则文本（确认）或 None（取消）；保存前做语法校验并列出错误行。
    """

    _SAMPLE = ("# 每行一条：二级分类名 = 关键词1, 关键词2（先匹配先得，未命中归『其他』）\n"
               "写实人像 = 写实, 摄影, 人像\n胶卷质感 = 胶片, 菲林, 颗粒\n")

    def __init__(self, master, text: str = "") -> None:
        super().__init__(master)
        self.result = None
        self.title("关键词规则 → 二级分类")
        self.geometry("760x520")
        self.minsize(660, 420)
        self.transient(master)
        self.grab_set()

        pad = 16
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(self, text="关键词规则（自动分『二级分类』）",
                     font=("Microsoft YaHei", 14, "bold"), anchor="w"
                     ).grid(row=0, column=0, sticky="ew", padx=pad, pady=(14, 2))
        ctk.CTkLabel(self, text="格式：二级分类名 = 关键词1, 关键词2　（`#` 开头为注释；"
                                "规则按行**先匹配先得**；命中即归该分类，未命中归『其他』）"
                                "　匹配范围为**整行文本**（名称、分类、各字段值任一命中即可）。",
                     text_color="gray", font=("Microsoft YaHei", 11), anchor="w",
                     justify="left", wraplength=700
                     ).grid(row=1, column=0, sticky="ew", padx=pad)

        self.box = ctk.CTkTextbox(self, font=("Consolas", 12))
        self.box.grid(row=2, column=0, sticky="nsew", padx=pad, pady=(6, 4))
        self.box.insert("1.0", text if text.strip() else self._SAMPLE)

        self.msg_lbl = ctk.CTkLabel(self, text="", anchor="w", justify="left",
                                    wraplength=700, font=("Microsoft YaHei", 11))
        self.msg_lbl.grid(row=3, column=0, sticky="ew", padx=pad)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=pad, pady=(4, 14))
        ctk.CTkButton(foot, text="检查规则", width=96, fg_color="#8a94a6",
                      command=self._check).pack(side="left")
        ctk.CTkButton(foot, text="取消", width=88, command=self.destroy).pack(side="right")
        ctk.CTkButton(foot, text="确定", width=88, fg_color="#2E8B57",
                      command=self._ok).pack(side="right", padx=(0, 8))
        ctk.CTkButton(foot, text="清空", width=72, fg_color="#8a94a6",
                      command=lambda: self.box.delete("1.0", "end")).pack(side="left",
                                                                        padx=(6, 0))

        self._center()

    def _rules(self):
        return himp.parse_l2_rules(self.box.get("1.0", "end"))

    def _check(self) -> bool:
        rules, errors = self._rules()
        if errors:
            self.msg_lbl.configure(text_color="#D9534F",
                                   text="发现 %d 处问题：%s" % (len(errors), "；".join(errors[:3])))
            return False
        if not rules:
            self.msg_lbl.configure(text_color="#D08A00", text="还没有任何有效规则。")
            return False
        self.msg_lbl.configure(text_color="#2E8B57",
                               text=f"✔ 共 {len(rules)} 条规则，语法正确。")
        return True

    def _ok(self) -> None:
        if not self._check():
            messagebox.showwarning("规则有误", "请按提示修正规则后再确定。", parent=self)
            return
        self.result = self.box.get("1.0", "end").strip()
        self.destroy()

    def _center(self) -> None:
        """屏幕水平居中 + 上边距 50（与字段管理系列对话框统一）"""
        from .field_manager_dialog import _place_top_centered
        _place_top_centered(self)


class ImportWizardDialog(ctk.CTkToplevel):
    """离线"网上资源结构化"向导（模态）。"""

    def __init__(self, master, db) -> None:
        super().__init__(master)
        self.master = master
        self.db = db
        self.imported = False
        self.rolled_back = False

        # 状态
        self.source = {}          # {headers, rows, delimiter, total_rows, kind, tables_found}
        self.layer_map = {"project": None, "domain": None, "l1": None, "l2": None}
        self.field_map = {}       # {列下标: 目标}
        self.payload = None
        self.warnings = []
        self._step = 1
        # 2026-09-14（5-b）：网址模式状态
        self._tables = []         # 抓取到的多张表（选表下拉用）
        self._fetch_base = ""     # 抓取页最终 URL（链接补全基准）
        self.link_stat = {}       # {"completed": n, "base": url}
        self.saved_path = ""      # 已保存的数据文件（"先存后导"：未保存不让导入）
        self.saved_xlsx = ""      # 与数据文件同时导出的 Excel（5-d-4）
        # 2026-09-14（5-d）增强状态
        self.filter_stat = {}     # 行筛选统计
        self.split_stat = {}      # 中英分流统计
        self.l2_rules_text = ""   # 关键词规则原文（"二级分类名 = 关键词1, 关键词2"）
        self.batch_warnings = []  # 批量抓取的合并告警（5-c）

        self.title("网上资源结构化向导")
        self.geometry("880x680")
        self.minsize(820, 620)
        self.transient(master)
        self.grab_set()

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 顶部：步骤标题
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        self.step_lbl = ctk.CTkLabel(head, text="", font=("Microsoft YaHei", 15, "bold"))
        self.step_lbl.pack(side="left")
        self.hint_lbl = ctk.CTkLabel(head, text="", text_color="gray",
                                     font=("Microsoft YaHei", 11))
        self.hint_lbl.pack(side="left", padx=(12, 0))

        # 中部：三步容器
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        self.frame1 = ctk.CTkFrame(self.body, fg_color="transparent")
        self.frame2 = ctk.CTkFrame(self.body, fg_color="transparent")
        self.frame3 = ctk.CTkFrame(self.body, fg_color="transparent")
        for f in (self.frame1, self.frame2, self.frame3):
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._build_step1()
        self._build_step2()
        self._build_step3()

        # 底部：导航
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 12))
        self.prev_btn = ctk.CTkButton(foot, text="← 上一步", width=96,
                                      command=self._go_prev, state="disabled")
        self.prev_btn.pack(side="left")
        self.close_btn = ctk.CTkButton(foot, text="关闭", width=88, fg_color="#8a94a6",
                                       command=self.destroy)
        self.close_btn.pack(side="right")
        self.next_btn = ctk.CTkButton(foot, text="下一步 →", width=110,
                                      command=self._go_next)
        self.next_btn.pack(side="right", padx=(0, 8))
        # 2026-09-14（用户要求：结构化后**先保存成数据文件、再导入**）
        self.import_btn = ctk.CTkButton(foot, text="② 导入该文件", width=124,
                                        fg_color="#2E8B57", state="disabled",
                                        command=self._import_saved)
        self.save_btn = ctk.CTkButton(foot, text="① 保存数据文件…", width=138,
                                      command=self._save_file)
        self.saved_lbl = ctk.CTkLabel(foot, text="", text_color="gray",
                                      font=("Microsoft YaHei", 10))

        self._show_step(1)
        self._center()

    # ------------------------------------------------------------------ #
    # 第 1 步：选择源（粘贴 / 文件 / 网址）
    # ------------------------------------------------------------------ #
    def _build_step1(self) -> None:
        f = self.frame1
        f.grid_columnconfigure(0, weight=1)

        self.src_mode = ctk.StringVar(value="paste")
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        ctk.CTkRadioButton(row, text="粘贴文本", variable=self.src_mode,
                           value="paste", command=self._on_src_mode,
                           font=("Microsoft YaHei", 12)).pack(side="left")
        ctk.CTkRadioButton(row, text="本地文件（CSV / TSV / TXT / HTML / MD）",
                           variable=self.src_mode, value="file", command=self._on_src_mode,
                           font=("Microsoft YaHei", 12)).pack(side="left", padx=(14, 0))
        ctk.CTkRadioButton(row, text="网址（网页 / GitHub 文件 / GitHub 目录批量）",
                           variable=self.src_mode, value="url", command=self._on_src_mode,
                           font=("Microsoft YaHei", 12)).pack(side="left", padx=(14, 0))

        # ---- 网址模式：URL + 抓取 ----
        self.url_row = ctk.CTkFrame(f, fg_color="transparent")
        self.url_row.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        ctk.CTkLabel(self.url_row, text="网址", font=("Microsoft YaHei", 12)).pack(side="left")
        self.url_entry = ctk.CTkEntry(self.url_row,
                                      placeholder_text="https://…（GitHub blob 链接会自动转 raw）")
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.url_entry.bind("<Return>", lambda _e=None: self._fetch_source())
        self.fetch_btn = ctk.CTkButton(self.url_row, text="抓取", width=76,
                                       command=self._fetch_source)
        self.fetch_btn.pack(side="left")

        self.fetch_lbl = ctk.CTkLabel(f, text="", text_color="#2f6fb0", anchor="w",
                                      justify="left", wraplength=820,
                                      font=("Microsoft YaHei", 11))
        self.fetch_lbl.grid(row=2, column=0, sticky="ew")

        # ---- 选表（多表页面）----
        self.table_row = ctk.CTkFrame(f, fg_color="transparent")
        self.table_row.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        ctk.CTkLabel(self.table_row, text="选用表格", font=("Microsoft YaHei", 12)
                     ).pack(side="left")
        self.table_var = ctk.StringVar(value="")
        self.table_menu = ctk.CTkOptionMenu(self.table_row, width=560, variable=self.table_var,
                                            values=["（无）"], command=self._on_table_pick)
        self.table_menu.pack(side="left", padx=(8, 0))
        self.table_row.grid_remove()

        # ---- 高级（自定义请求头 / 超时 / 上限）----
        self.adv_var = ctk.StringVar(value="0")
        self.adv_toggle = ctk.CTkCheckBox(f, text="高级（自定义请求头 / 超时 / 体积上限）",
                                          variable=self.adv_var, onvalue="1", offvalue="0",
                                          command=self._on_adv_toggle,
                                          font=("Microsoft YaHei", 11))
        self.adv_toggle.grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.adv_frame = ctk.CTkFrame(f, fg_color="transparent")
        self.adv_frame.grid(row=5, column=0, sticky="ew", pady=(2, 0))
        self.adv_ua = self._adv_entry("User-Agent", 2, width=560)
        self.adv_ref = self._adv_entry("Referer", 3, width=560,
                                      hint="部分站点（如 CSDN）需 Referer/Cookie 才放行")
        self.adv_cookie = self._adv_entry("Cookie", 4, width=560)
        num = ctk.CTkFrame(self.adv_frame, fg_color="transparent")
        num.grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ctk.CTkLabel(num, text="超时(秒)", font=("Microsoft YaHei", 11)
                     ).pack(side="left")
        self.adv_timeout = ctk.CTkEntry(num, width=60)
        self.adv_timeout.insert(0, "15")
        self.adv_timeout.pack(side="left", padx=(6, 16))
        ctk.CTkLabel(num, text="体积上限(MB)", font=("Microsoft YaHei", 11)
                     ).pack(side="left")
        self.adv_maxmb = ctk.CTkEntry(num, width=60)
        self.adv_maxmb.insert(0, "10")
        self.adv_maxmb.pack(side="left", padx=(6, 0))
        self.adv_frame.grid_remove()

        # ---- 粘贴 / 文件模式 ----
        self.opts_row = ctk.CTkFrame(f, fg_color="transparent")
        self.opts_row.grid(row=6, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(self.opts_row, text="分隔符", font=("Microsoft YaHei", 12)).pack(side="left")
        self.delim_var = ctk.StringVar(value="自动")
        ctk.CTkOptionMenu(self.opts_row, width=110, variable=self.delim_var,
                          values=["自动", "Tab", "逗号", "分号", "竖线"]
                          ).pack(side="left", padx=(8, 16))
        self.file_var = ctk.StringVar(value="")
        self.file_lbl = ctk.CTkLabel(self.opts_row, text="（未选择文件）", text_color="gray",
                                     font=("Microsoft YaHei", 11), anchor="w")
        self.file_lbl.pack(side="left", fill="x", expand=True)
        self.pick_file_btn = ctk.CTkButton(self.opts_row, text="选择文件…", width=96,
                                           command=self._pick_file, state="disabled")
        self.pick_file_btn.pack(side="left", padx=(8, 0))
        ctk.CTkButton(self.opts_row, text="解析", width=76, command=self._parse_source
                      ).pack(side="left", padx=(8, 0))

        self.paste_hint = ctk.CTkLabel(f, text="粘贴区（首行＝表头；网页表格可直接 Ctrl+V）",
                                       font=("Microsoft YaHei", 11), text_color="gray",
                                       anchor="w")
        self.paste_hint.grid(row=7, column=0, sticky="ew")
        self.paste_box = ctk.CTkTextbox(f, height=130)
        self.paste_box.grid(row=8, column=0, sticky="nsew", pady=(2, 6))
        self.parse_lbl = ctk.CTkLabel(f, text="", text_color="#2f6fb0",
                                      font=("Microsoft YaHei", 11), anchor="w")
        self.parse_lbl.grid(row=9, column=0, sticky="ew")
        ctk.CTkLabel(f, text="解析结果预览（前 10 行）", font=("Microsoft YaHei", 11),
                     text_color="gray", anchor="w").grid(row=10, column=0, sticky="ew")
        self.preview_box = ctk.CTkTextbox(f, height=190, font=("Consolas", 11))
        self.preview_box.grid(row=11, column=0, sticky="nsew", pady=(2, 0))
        self.preview_box.configure(state="disabled")
        f.grid_rowconfigure(11, weight=1)
        self._on_src_mode()

    def _adv_entry(self, label: str, r: int, width: int = 520, hint: str = ""):
        row = ctk.CTkFrame(self.adv_frame, fg_color="transparent")
        row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1)
        ctk.CTkLabel(row, text=label, width=84, anchor="w", font=("Microsoft YaHei", 11)
                     ).pack(side="left")
        ent = ctk.CTkEntry(row, width=width)
        ent.pack(side="left")
        if hint:
            ctk.CTkLabel(row, text=hint, text_color="gray", font=("Microsoft YaHei", 10)
                         ).pack(side="left", padx=(6, 0))
        return ent

    def _on_adv_toggle(self) -> None:
        if self.adv_var.get() == "1":
            self.adv_frame.grid()
        else:
            self.adv_frame.grid_remove()

    _URL_HINT = ("提示（两段式抓取）：抓文档仓库时建议先抓「索引页小样」（README / gallery 等）"
                 "确认分类与结构，再抓正文页；抓到的内容先看下方「解析结果预览」与第 3 步质量自检，"
                 "确认无误再保存导入。")

    def _on_src_mode(self) -> None:
        mode = self.src_mode.get()
        is_url, is_file = mode == "url", mode == "file"
        (self.url_row.grid() if is_url else self.url_row.grid_remove())
        (self.fetch_lbl.grid() if is_url else self.fetch_lbl.grid_remove())
        # 2026-09-14（审核补充 P4）：网址模式首次进入时给出"两段式抓取"提示（方案 4.2-1）
        if is_url and not str(self.fetch_lbl.cget("text") or "").strip():
            self.fetch_lbl.configure(text=self._URL_HINT, text_color="#8a94a6")
        (self.adv_toggle.grid() if is_url else self.adv_toggle.grid_remove())
        if not is_url:
            self.adv_frame.grid_remove()
        elif self.adv_var.get() == "1":
            self.adv_frame.grid()
        if self._tables and is_url and len(self._tables) > 1:
            self.table_row.grid()
        else:
            self.table_row.grid_remove()
        for w in (self.opts_row, self.paste_hint, self.paste_box):
            (w.grid() if not is_url else w.grid_remove())
        self.pick_file_btn.configure(state=("normal" if is_file else "disabled"))
        self.paste_box.configure(state=("disabled" if is_file else "normal"))

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择源文件", parent=self,
            filetypes=[("表格/文本", "*.csv *.tsv *.txt *.html *.htm *.md"),
                       ("全部文件", "*.*")])
        if not path:
            return
        self.file_var.set(path)
        self.file_lbl.configure(text=os.path.basename(path))
        self._parse_source()

    def _delimiter(self):
        return {"自动": None, "Tab": "\t", "逗号": ",", "分号": ";", "竖线": "|"}[
            self.delim_var.get()]

    # ---- 网址模式：抓取（2026-09-14，5-b）---- #
    def _fetch_headers(self) -> dict:
        hdrs = {}
        ua = self.adv_ua.get().strip()
        ref = self.adv_ref.get().strip()
        ck = self.adv_cookie.get().strip()
        if ua:
            hdrs["User-Agent"] = ua
        if ref:
            hdrs["Referer"] = ref
        if ck:
            hdrs["Cookie"] = ck
        return hdrs

    def _fetch_limits(self):
        try:
            t = float(self.adv_timeout.get().strip() or 15)
        except Exception:
            t = 15.0
        try:
            mb = float(self.adv_maxmb.get().strip() or 10)
        except Exception:
            mb = 10.0
        return max(1.0, t), int(max(0.1, mb) * 1024 * 1024)

    def _fetch_source(self) -> None:
        """抓取网址并解析为结构化源（复用 T1 的解析/预览/映射链路）"""
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先填写网址。", parent=self)
            return
        timeout, max_bytes = self._fetch_limits()
        hdrs = self._fetch_headers()
        self.batch_warnings = []
        # 2026-09-14（5-c）：GitHub **目录** → 列举 + 多选 + 批量抓取 + 合并
        if fetcher.looks_like_github_dir(url):
            self._fetch_github_batch(url, hdrs, timeout, max_bytes)
            return
        self.fetch_lbl.configure(text="⏳ 正在抓取…", text_color="#2f6fb0")
        self.update_idletasks()
        try:
            owner, repo, ref, path = fetcher.github_owner_repo(url)
            if owner and repo and path:
                r = fetcher.fetch_github_file(owner, repo, path, ref or "main", headers=hdrs,
                                              timeout=timeout, max_bytes=max_bytes)
            else:
                r = fetcher.fetch_url(url, headers=hdrs, timeout=timeout, max_bytes=max_bytes)
        except Exception as exc:                     # noqa: BLE001
            self.fetch_lbl.configure(text=f"❌ 抓取失败：{exc}", text_color="#D9534F")
            return
        if not r.get("ok"):
            tip = ("　建议：① 检查网址与网络；② 换用「粘贴文本」；"
                   "③ 若为反爬站点，展开「高级」填写 Referer / Cookie 后重试。")
            self.fetch_lbl.configure(
                text=f"❌ 抓取失败（{r.get('error_kind') or 'unknown'}）：{r.get('error')}{tip}",
                text_color="#D9534F")
            return
        note = f"通道={r.get('channel') or 'direct'}"
        # 2026-09-14（审核补充 P4）：降级抓取时把"通道链"显示出来（方案 4.2-2 要求）
        tried = list(r.get("tried") or [])
        if r.get("channel") in ("contents", "blob") and tried:
            note += "　降级链：" + " → ".join(tried) + f" → {r.get('channel')}"
        if r.get("blocked_hint"):
            note += "　⚠ 疑似被反爬拦截页（可在「高级」填 Referer/Cookie 后重试）"
        self._source_from_text(r.get("text") or "", r.get("final_url") or url,
                              r.get("content_type") or "", note=note,
                              size=r.get("bytes") or 0, status=r.get("status"))

    # ---- 5-c：GitHub 目录批量抓取 ---- #
    def _fetch_github_batch(self, url: str, hdrs: dict, timeout: float,
                            max_bytes: int) -> None:
        """GitHub 目录 → 列举文件 → 多选（限额校验）→ 逐个抓取 → 合并为一个源"""
        owner, repo, ref, path = fetcher.github_owner_repo(url)
        if not (owner and repo):
            self.fetch_lbl.configure(text="❌ 无法解析该 GitHub 链接（应形如 "
                                          "https://github.com/<用户>/<仓库>/tree/<分支>/<目录>）。",
                                     text_color="#D9534F")
            return
        ref = ref or "main"
        self.fetch_lbl.configure(text="⏳ 正在列举目录…", text_color="#2f6fb0")
        self.update_idletasks()
        try:
            items = fetcher.github_list_dir(owner, repo, path, ref, timeout=timeout)
        except Exception as exc:                      # noqa: BLE001
            if path:                                  # 可能是文件 → 退回单文件抓取
                r = fetcher.fetch_github_file(owner, repo, path, ref, headers=hdrs,
                                              timeout=timeout, max_bytes=max_bytes)
                if r.get("ok"):
                    note = f"通道={r.get('channel') or 'direct'}"
                    tried = list(r.get("tried") or [])
                    if r.get("channel") in ("contents", "blob") and tried:
                        note += "　降级链：" + " → ".join(tried) + f" → {r.get('channel')}"
                    self._source_from_text(r.get("text") or "", r.get("final_url") or url,
                                           r.get("content_type") or "", note=note,
                                           size=r.get("bytes") or 0, status=r.get("status"))
                    return
            self.fetch_lbl.configure(
                text=f"❌ {exc}　建议：① 检查链接/网络；② 该地址若为文件请用 raw 链接；"
                     "③ 或改用「粘贴文本」。", text_color="#D9534F")
            return
        if not fetcher.github_dir_files(items):
            self.fetch_lbl.configure(text="⚠ 该目录下没有文件（只有子目录）。"
                                          "可进入某个子目录后重试。", text_color="#D08A00")
            return
        dlg = GithubBatchDialog(self, items, repo_label=f"{owner}/{repo}/{path or ''}")
        try:
            self.wait_window(dlg)
        except Exception:
            pass
        if not dlg.selected:
            self.fetch_lbl.configure(text="（已取消批量抓取）", text_color="gray")
            return
        self._fetch_batch_files(owner, repo, ref, path, dlg.selected, hdrs, timeout, max_bytes)

    def _fetch_batch_files(self, owner: str, repo: str, ref: str, path: str, paths: list,
                           hdrs: dict, timeout: float, max_bytes: int) -> None:
        """逐个抓取已勾选文件（进度条按文件）；失败文件跳过并列表说明（不整体中止）"""
        limit = min(max_bytes, fetcher.BATCH_MAX_FILE_BYTES)
        prog = ProgressDialog(self, total=len(paths), message="正在抓取 GitHub 文件…")
        parts, failed = [], []
        for i, p in enumerate(paths, 1):
            name = p.rsplit("/", 1)[-1]
            try:
                prog.update_progress(i, len(paths), name)
            except Exception:
                pass
            self.update_idletasks()
            try:
                r = fetcher.fetch_github_file(owner, repo, p, ref, headers=hdrs,
                                              timeout=timeout, max_bytes=limit)
            except Exception as exc:                  # noqa: BLE001
                failed.append((name, str(exc)))
                continue
            if r.get("ok"):
                parts.append({"name": name, "text": r.get("text") or ""})
            else:
                failed.append((name, r.get("error") or "抓取失败"))
        try:
            prog.finish()
        except Exception:
            pass
        if not parts:
            self.fetch_lbl.configure(
                text="❌ 所选文件全部抓取失败：" + "；".join(f"{n}：{e}" for n, e in failed[:3]),
                text_color="#D9534F")
            return
        merged = himp.merge_batch_parts(parts)
        self.batch_warnings = list(merged.get("warnings") or [])
        base = f"{fetcher.GITHUB_RAW}/{owner}/{repo}/{ref}/" + (f"{path}/" if path else "")
        note = f"GitHub 批量：成功 {len(parts)}/{len(paths)} 个文件"
        if failed:
            note += ("；失败 " + "、".join(f"{n}（{e}）" for n, e in failed[:3])
                     + ("…" if len(failed) > 3 else ""))
        if merged.get("warnings"):
            note += "　⚠ " + "；".join(merged["warnings"])
        if merged.get("merged") and merged.get("tables"):
            t = merged["tables"][0]
            self._fetch_base = base
            self._tables = merged["tables"]
            self.table_row.grid_remove()
            self.source = {"headers": t["headers"], "rows": t["rows"], "delimiter": "",
                           "total_rows": t["total_rows"], "kind": "url-table",
                           "tables_found": 1}
            self.parse_lbl.configure(
                text=f"✅ 批量合并：{len(parts)} 个文件表头一致 → 合并为 1 张表"
                     f"（共 {t['total_rows']} 行、{len(t['headers'])} 列）（来源：GitHub 批量）")
            self._fill_preview(t["rows"])
            self._autofill_mapping()
            self.fetch_lbl.configure(
                text=f"✅ 批量抓取完成：{note}\n　　{base}", text_color="#2E8B57")
            return
        self._source_from_text(merged.get("text") or "", base, "text/markdown", note=note)

    _KIND_LABELS = {"html": "HTML 表格页", "markdown": "Markdown", "csv": "CSV / 文本",
                    "json": "JSON", "text": "纯文本"}

    @classmethod
    def _stat_segment(cls, status, content_type: str, kb: str, kind: str) -> str:
        """抓取状态行首段（2026-09-14，审核补充 P4）。

        形如 `HTTP 200 · text/html · 42.1KB · 识别为 HTML 表格页`（方案 4.2-2 要求）；
        批量合并等无 HTTP 语义的场景传 status=None，则省去 HTTP 段。
        """
        ct = (content_type or "").split(";")[0].strip() or "—"
        parts = []
        if status:
            parts.append(f"HTTP {status}")
        parts += [ct, kb, f"识别为 {cls._KIND_LABELS.get(kind, kind)}"]
        return " · ".join(parts)

    def _source_from_text(self, text: str, base_url: str, content_type: str,
                          note: str = "", size: int = 0, status=None) -> bool:
        """把抓到的文本解析为结构化源；返回是否成功"""
        kind = fetcher.detect_source_kind(base_url, content_type, text)
        kb = f"{size / 1024:.1f}KB" if size else "—"
        stat = self._stat_segment(status, content_type, kb, kind)
        self._fetch_base = base_url
        self._tables = []
        self.table_row.grid_remove()          # 换源后先收起"选表"行（仅多表时再显示）
        note_line = f"\n　　{note}" if note else ""
        if kind == "json":
            self.fetch_lbl.configure(
                text=f"ℹ {stat} ——JSON 源请使用「⇩ 导入 → 导入 JSON 备份」直接导入；"
                     f"本向导负责把『表格类』内容结构化。{note_line}", text_color="#D08A00")
            return False
        if kind in ("html", "markdown"):
            self._tables = (himp.list_html_tables(text) if kind == "html"
                            else himp.list_md_tables(text))
            if not self._tables:
                self.fetch_lbl.configure(
                    text=f"⚠ {stat}，但未找到可用表格"
                         "（表格需首行为表头、≥2 列）。可改用「粘贴文本」，或改用 raw Markdown 链接。"
                         f"{note_line}",
                    text_color="#D08A00")
                return False
            idx = self._fill_table_menu()
            self._apply_table(idx)
            extra = (f"，共 {len(self._tables)} 张表（默认选最大一张：表{idx + 1}，可切换）"
                     if len(self._tables) > 1 else "")
            self.fetch_lbl.configure(
                text=f"✅ 抓取成功：{base_url}\n　　{stat} · {note}{extra}",
                text_color="#2E8B57")
            return True
        # csv / text：按分隔符直接解析
        src = himp.split_delimited(text, None)
        src["kind"] = "url-csv"
        src["tables_found"] = 1
        if not src.get("headers") or not src.get("rows") or len(src["headers"]) < 2:
            self.fetch_lbl.configure(
                text=f"⚠ {stat}，但未识别出『表格结构』（首行须为表头、≥2 列）。"
                     f"可改用「粘贴文本」并手工指定分隔符。{note_line}", text_color="#D08A00")
            return False
        self.source = src
        detail = f"共 {src['total_rows']} 行、{len(src['headers'])} 列，分隔符＝{src.get('delimiter') or '自动'}"
        self.parse_lbl.configure(text=f"✅ 解析结果：{detail}（来源：网址）")
        self._fill_preview(src["rows"])
        self._autofill_mapping()
        self.fetch_lbl.configure(
            text=f"✅ 抓取成功：{base_url}\n　　{stat} · {note}", text_color="#2E8B57")
        return True

    def _fill_table_menu(self) -> int:
        """填充"选表"下拉；返回默认选中下标（**最大的表**，与方案一致）"""
        vals = [himp.table_brief(i + 1, t) for i, t in enumerate(self._tables)]
        self.table_menu.configure(values=vals or ["（无）"])
        best = himp.pick_largest_table(self._tables)
        idx = self._tables.index(best) if best in self._tables else 0
        if vals:
            self.table_var.set(vals[idx])
        if len(self._tables) > 1 and self.src_mode.get() == "url":
            self.table_row.grid()
        else:
            self.table_row.grid_remove()
        return idx

    def _on_table_pick(self, _value=None) -> None:
        idx = 0
        try:
            idx = self.table_menu.configure("values").index(self.table_var.get())
        except Exception:
            idx = 0
        self._apply_table(idx)

    def _apply_table(self, idx: int) -> None:
        """把选中的表设为当前源（选表下拉切换时调用）"""
        if not self._tables:
            return
        t = self._tables[max(0, min(idx, len(self._tables) - 1))]
        self.source = {"headers": t["headers"], "rows": t["rows"], "delimiter": "",
                       "total_rows": t["total_rows"], "kind": "url-table",
                       "tables_found": len(self._tables)}
        detail = f"共 {t['total_rows']} 行、{len(t['headers'])} 列（第 {idx + 1} 张表，共 {len(self._tables)} 张）"
        self.parse_lbl.configure(text=f"✅ 解析结果：{detail}（来源：网址）")
        self._fill_preview(t["rows"])
        self._autofill_mapping()

    def _parse_source(self) -> None:
        try:
            if self.src_mode.get() == "file":
                path = self.file_var.get()
                if not path:
                    messagebox.showwarning("提示", "请先选择源文件。", parent=self)
                    return
                self.source = himp.parse_file(path)
                self._fetch_base = ""
            else:
                text = self.paste_box.get("1.0", "end")
                if not text.strip():
                    messagebox.showwarning("提示", "请先粘贴要结构化的文本。", parent=self)
                    return
                self.source = himp.split_delimited(text, self._delimiter())
                self.source["kind"] = "paste"
                self.source["tables_found"] = 1
                self._fetch_base = ""
        except Exception as exc:
            messagebox.showerror("解析失败", str(exc), parent=self)
            return
        src = self.source
        if not src.get("headers") or not src.get("rows"):
            self.parse_lbl.configure(text="解析结果：未得到数据（首行须为表头，至少 1 行数据）")
            self._fill_preview([])
            return
        detail = f"共 {src['total_rows']} 行、{len(src['headers'])} 列"
        if src.get("delimiter"):
            shown = {"\t": "Tab", ",": "逗号", ";": "分号", "|": "竖线"}.get(src["delimiter"],
                                                                          src["delimiter"])
            detail += f"，分隔符＝{shown}"
        if src.get("kind") in ("html", "md"):
            detail += f"，共找到 {src.get('tables_found', 1)} 张表（取最大一张）"
        self.parse_lbl.configure(text="✅ 解析结果：" + detail)
        self._fill_preview(src["rows"])
        self._autofill_mapping()

    def _fill_preview(self, rows: list) -> None:
        hdr = self.source.get("headers") or []
        self.preview_box.configure(state="normal")
        self.preview_box.delete("1.0", "end")
        if hdr:
            self.preview_box.insert("end", " | ".join(str(h) for h in hdr[:12]) + "\n")
            self.preview_box.insert("end", "-" * 60 + "\n")
            for r in (rows or [])[:10]:
                self.preview_box.insert("end",
                                        " | ".join(str(c) for c in r[:12]) + "\n")
        self.preview_box.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 第 2 步：层级映射 + 字段映射
    # ------------------------------------------------------------------ #
    def _build_step2(self) -> None:
        f = self.frame2
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(f, text="① 层级映射：把源列指到四个层级（未映射的层自动按兜底名补齐；"
                            "空单元格继承上一行）",
                     font=("Microsoft YaHei", 12, "bold"), anchor="w"
                     ).grid(row=0, column=0, sticky="ew")
        lay = ctk.CTkFrame(f)
        lay.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        self._layer_menus = {}
        for i, key in enumerate(himp.LAYER_KEYS):
            ctk.CTkLabel(lay, text=himp.LAYER_LABELS[key], font=("Microsoft YaHei", 12)
                         ).grid(row=0, column=i * 2, padx=(10 if i == 0 else 4, 4), pady=6)
            var = ctk.StringVar(value=_NO_MAP)
            om = ctk.CTkOptionMenu(lay, width=150, variable=var, values=[_NO_MAP])
            om.grid(row=0, column=i * 2 + 1, padx=(0, 6), pady=6)
            self._layer_menus[key] = (var, om)
        fb = ctk.CTkFrame(lay, fg_color="transparent")
        fb.grid(row=1, column=0, columnspan=8, sticky="ew", padx=10, pady=(0, 8))
        ctk.CTkLabel(fb, text="跳层兜底名：", font=("Microsoft YaHei", 11),
                     text_color="gray").pack(side="left")
        self.fb_vars = {}
        for key, default in (("project", himp.DEFAULT_FALLBACK_PROJECT),
                             ("domain", himp.DEFAULT_FALLBACK_DOMAIN),
                             ("l1", himp.DEFAULT_FALLBACK_L1)):
            ctk.CTkLabel(fb, text=himp.LAYER_LABELS[key], font=("Microsoft YaHei", 11)
                         ).pack(side="left", padx=(10, 2))
            v = ctk.StringVar(value=default)
            ctk.CTkEntry(fb, width=120, textvariable=v).pack(side="left")
            self.fb_vars[key] = v

        head = ctk.CTkFrame(f, fg_color="transparent")
        head.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        ctk.CTkLabel(head, text="② 字段映射：每个源列对应到内置字段 / 自定义字段 / 标签"
                                "（默认已按表头猜测，可改）",
                     font=("Microsoft YaHei", 12, "bold")).pack(side="left")
        ctk.CTkButton(head, text="全部忽略", width=84, height=26, fg_color="#8a94a6",
                      command=self._ignore_all).pack(side="right")
        ctk.CTkButton(head, text="＋ 新建字段…", width=104, height=26, fg_color="#2f6fb0",
                      command=self._new_field).pack(side="right", padx=(0, 8))

        # 2026-09-14（5-b）：链接补全开关（网址来源时默认开；其它来源无基准 URL，自动禁用）
        link_row = ctk.CTkFrame(f, fg_color="transparent")
        link_row.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.link_fix_var = ctk.StringVar(value="1")
        self.link_fix_chk = ctk.CTkCheckBox(
            link_row, text="自动补全相对链接（映射到 ⑩ 获取方案 / ③ 溯源 的\"像链接\"值补成绝对 URL）",
            variable=self.link_fix_var, onvalue="1", offvalue="0",
            font=("Microsoft YaHei", 11))
        self.link_fix_chk.pack(side="left")
        self.link_base_lbl = ctk.CTkLabel(link_row, text="", text_color="gray",
                                          font=("Microsoft YaHei", 10))
        self.link_base_lbl.pack(side="left", padx=(10, 0))

        # 2026-09-14（5-d）：增强三件套（行筛选 / 关键词规则分二级 / 中英提示词分流）
        ctk.CTkLabel(f, text="③ 可选增强（5-d）：行筛选 / 关键词规则分二级 / 中英提示词分流",
                     font=("Microsoft YaHei", 12, "bold"), anchor="w"
                     ).grid(row=5, column=0, sticky="ew", pady=(8, 0))
        enh = ctk.CTkFrame(f)
        enh.grid(row=6, column=0, sticky="ew", pady=(2, 0))
        r1 = ctk.CTkFrame(enh, fg_color="transparent")
        r1.pack(fill="x", padx=8, pady=(8, 2))
        ctk.CTkLabel(r1, text="行筛选：包含", font=("Microsoft YaHei", 11)).pack(side="left")
        self.filter_inc = ctk.CTkEntry(r1, width=170,
                                       placeholder_text="任一命中才保留（逗号分隔）")
        self.filter_inc.pack(side="left", padx=(6, 12))
        ctk.CTkLabel(r1, text="排除", font=("Microsoft YaHei", 11)).pack(side="left")
        self.filter_exc = ctk.CTkEntry(r1, width=170,
                                       placeholder_text="任一命中即剔除（逗号分隔）")
        self.filter_exc.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(r1, text="（对整行文本匹配；第 3 步『数量对账』可见筛选结果）",
                     text_color="gray", font=("Microsoft YaHei", 10)).pack(side="left",
                                                                         padx=(10, 0))
        r2 = ctk.CTkFrame(enh, fg_color="transparent")
        r2.pack(fill="x", padx=8, pady=2)
        self.l2_rule_var = ctk.StringVar(value="0")
        self.l2_rule_chk = ctk.CTkCheckBox(
            r2, text="按关键词规则自动分『二级分类』（先匹配先得，未命中归『其他』）",
            variable=self.l2_rule_var, onvalue="1", offvalue="0",
            font=("Microsoft YaHei", 11))
        self.l2_rule_chk.pack(side="left")
        ctk.CTkButton(r2, text="编辑规则…", width=96, height=26, fg_color="#2f6fb0",
                      command=self._edit_l2_rules).pack(side="left", padx=(10, 8))
        self.l2_rule_lbl = ctk.CTkLabel(r2, text="（未设置规则）", text_color="gray",
                                       font=("Microsoft YaHei", 10))
        self.l2_rule_lbl.pack(side="left")
        r3 = ctk.CTkFrame(enh, fg_color="transparent")
        r3.pack(fill="x", padx=8, pady=(2, 8))
        self.split_lang_var = ctk.StringVar(value="0")
        ctk.CTkCheckBox(r3, text="提示词自动分流中/英（映射到 ⑧ 的列若判定『以英文为主』 → 写入 ⑨）",
                        variable=self.split_lang_var, onvalue="1", offvalue="0",
                        font=("Microsoft YaHei", 11)).pack(side="left")

        self.map_scroll = ctk.CTkScrollableFrame(f, height=300)
        self.map_scroll.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        f.grid_rowconfigure(3, weight=1)

    def _target_options(self) -> list:
        opts = [("（忽略）", _IGNORE)]
        for k in himp.BUILTIN_ENTRY_KEYS:
            opts.append((himp.BUILTIN_LABELS[k], k))
        opts.append(("🏷 标签（可多值：逗号/顿号/分号分隔）", himp.TAG_TARGET))
        try:
            for d in self.db.list_field_defs():
                if not d.get("is_builtin"):
                    opts.append((f"自定义：{d['display_name']}", d["field_key"]))
        except Exception:
            pass
        return opts

    def _layer_values(self) -> list:
        hdr = self.source.get("headers") or []
        return [_NO_MAP] + [f"{i + 1}. {h}" for i, h in enumerate(hdr)]

    @staticmethod
    def _guess_layer(header: str):
        h = (header or "").lower()
        for key, hints in _LAYER_HINTS:
            for hint in hints:
                if hint.lower() in h:
                    return key
        return None

    @staticmethod
    def _guess_field(header: str):
        h = (header or "").lower()
        if "标签" in h or "tag" in h:
            return himp.TAG_TARGET
        for key, hints in _FIELD_HINTS:
            for hint in hints:
                if hint.lower() in h:
                    return key
        return _IGNORE

    def _autofill_mapping(self) -> None:
        """按表头名自动预填层级映射与字段映射（用户可在界面上改）"""
        hdr = self.source.get("headers") or []
        self._refresh_step2()
        for i, h in enumerate(hdr):
            key = self._guess_layer(h)
            if key and self._layer_menus[key][0].get() == _NO_MAP:
                self._layer_menus[key][0].set(f"{i + 1}. {h}")
        for i, h in enumerate(hdr):
            self.field_map[i] = self._guess_field(h)
        self._render_field_rows()

    def _refresh_step2(self) -> None:
        vals = self._layer_values()
        for key, (var, om) in self._layer_menus.items():
            om.configure(values=vals)
            if var.get() not in vals:
                var.set(_NO_MAP)
        # 2026-09-14（5-b）：链接补全（仅网址来源有基准 URL）
        if self._fetch_base:
            self.link_base_lbl.configure(text=f"基准：{self._fetch_base}")
            self.link_fix_chk.configure(state="normal")
        else:
            self.link_base_lbl.configure(text="（非网址来源：无基准 URL，本项不生效）")
            self.link_fix_chk.configure(state="disabled")

    def _render_field_rows(self) -> None:
        for w in self.map_scroll.winfo_children():
            w.destroy()
        hdr = self.source.get("headers") or []
        rows = self.source.get("rows") or []
        opts = self._target_options()
        labels = [lbl for lbl, _k in opts]
        key_of = {lbl: k for lbl, k in opts}
        self._field_vars = {}
        if not hdr:
            ctk.CTkLabel(self.map_scroll, text="（请先在第 1 步解析源数据）",
                         text_color="#9aa4b1", font=("Microsoft YaHei", 11)).pack(anchor="w")
            return
        for i, h in enumerate(hdr):
            row = ctk.CTkFrame(self.map_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            sample = ""
            for r in rows:
                if i < len(r) and (r[i] or "").strip():
                    sample = (r[i] or "").strip().replace("\n", " ")[:18]
                    break
            ctk.CTkLabel(row, text=f"{i + 1}. {h}", width=200, anchor="w",
                         font=("Microsoft YaHei", 12)).pack(side="left")
            ctk.CTkLabel(row, text=(f"示例：{sample}" if sample else "（空）"),
                         text_color="gray", width=180, anchor="w",
                         font=("Microsoft YaHei", 10)).pack(side="left")
            cur = self.field_map.get(i, _IGNORE)
            lbl = next((l for l, k in opts if k == cur), "（忽略）")
            var = ctk.StringVar(value=lbl)
            ctk.CTkOptionMenu(row, width=250, variable=var, values=labels,
                              command=lambda v, idx=i: self._set_field(idx, key_of.get(v, _IGNORE))
                              ).pack(side="left")
            self._field_vars[i] = var

    def _set_field(self, idx: int, target: str) -> None:
        self.field_map[idx] = target

    def _ignore_all(self) -> None:
        for i in list(self.field_map):
            self.field_map[i] = _IGNORE
        self._render_field_rows()

    def _new_field(self) -> None:
        """新建一个自定义字段定义（建好后立即出现在各列的"目标"下拉里，供选用）"""
        dlg = _NewFieldDialog(self)
        try:
            self.wait_window(dlg)
        except Exception:
            pass
        if not dlg.result:
            return
        name, ftype = dlg.result
        try:
            key = self.db.add_field_def(name, ftype)
        except Exception as exc:
            messagebox.showerror("新建字段失败", str(exc), parent=self)
            return
        self._render_field_rows()      # 选项列表刷新（各列已选目标保持不变）
        self.toast(f"已新建自定义字段：{name}（请在下方把目标改选为它）")
        self._last_new_key = key

    # ---- 5-d-1：关键词规则 → 二级分类 ---- #
    def _edit_l2_rules(self) -> None:
        """编辑"关键词规则"文本（`二级分类名 = 关键词1, 关键词2`）"""
        dlg = _L2RulesDialog(self, self.l2_rules_text)
        try:
            self.wait_window(dlg)
        except Exception:
            pass
        if dlg.result is None:
            return
        self.l2_rules_text = dlg.result
        rules, _errs = himp.parse_l2_rules(self.l2_rules_text)
        if rules:
            self.l2_rule_var.set("1")
            self.l2_rule_lbl.configure(text=f"已启用 {len(rules)} 条规则（未命中归『其他』）",
                                       text_color="#2E8B57")
        else:
            self.l2_rule_var.set("0")
            self.l2_rule_lbl.configure(text="（未设置规则）", text_color="gray")

    def _l2_rules(self):
        """当前生效的关键词规则（未勾选或无有效规则 → None）"""
        if self.l2_rule_var.get() != "1":
            return None
        rules, _errs = himp.parse_l2_rules(self.l2_rules_text)
        return rules or None

    def _collect_layer_map(self) -> dict:
        hdr = self.source.get("headers") or []
        out = {}
        for key, (var, _om) in self._layer_menus.items():
            v = var.get()
            out[key] = None
            if v and v != _NO_MAP:
                try:
                    out[key] = int(v.split(".", 1)[0]) - 1
                except Exception:
                    out[key] = None
                if out[key] is not None and not (0 <= out[key] < len(hdr)):
                    out[key] = None
        return out

    # ------------------------------------------------------------------ #
    # 第 3 步：预览与出口
    # ------------------------------------------------------------------ #
    def _build_step3(self) -> None:
        f = self.frame3
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=3)
        f.grid_rowconfigure(7, weight=2)
        self.stat_lbl = ctk.CTkLabel(f, text="", justify="left", anchor="w",
                                     font=("Microsoft YaHei", 12, "bold"),
                                     text_color="#2E8B57")
        self.stat_lbl.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        ctk.CTkLabel(f, text="层级树预览", font=("Microsoft YaHei", 11),
                     text_color="gray", anchor="w").grid(row=1, column=0, sticky="ew")
        self.tree_box = ctk.CTkTextbox(f, font=("Consolas", 11))
        self.tree_box.grid(row=2, column=0, sticky="nsew", pady=(2, 6))
        self.tree_box.configure(state="disabled")
        # 2026-09-14（5-b）：质量自检面板（源自历史教训：整列空 / 落未分类 / 同名共享 / 数量对账）
        ctk.CTkLabel(f, text="质量自检（导入前先看这里；有告警也可继续，但建议先核对）",
                     font=("Microsoft YaHei", 11), text_color="gray", anchor="w"
                     ).grid(row=3, column=0, sticky="ew")
        self.quality_box = ctk.CTkTextbox(f, height=104, font=("Microsoft YaHei", 11))
        self.quality_box.grid(row=4, column=0, sticky="nsew", pady=(2, 6))
        self.quality_box.configure(state="disabled")
        ctk.CTkLabel(f, text="警告 / 跳过说明（前 12 条）", font=("Microsoft YaHei", 11),
                     text_color="gray", anchor="w").grid(row=6, column=0, sticky="ew")
        self.warn_box = ctk.CTkTextbox(f, height=96, font=("Microsoft YaHei", 11))
        self.warn_box.grid(row=7, column=0, sticky="nsew", pady=(2, 0))
        self.warn_box.configure(state="disabled")

    def _tree_text(self, payload: dict) -> str:
        ents = payload.get("entries", [])
        per = {}

        def add(path, n=1):
            per[tuple(path)] = per.get(tuple(path), 0) + n

        for ep in ents:
            p = ep.get("path") or []
            for k in range(1, len(p) + 1):
                add(p[:k])
        lines = []
        cat_children = {}
        for c in payload.get("categories", []):
            cat_children.setdefault(c.get("parent"), []).append(c["name"])
        proj_domains = {}
        for d in payload.get("domains", []):
            proj_domains.setdefault(payload.get("domain_projects", {}).get(d["name"], ""),
                                    []).append(d["name"])
        for p in payload.get("projects", []):
            lines.append(f"📁 {p['name']}")
            for dname in proj_domains.get(p["name"], []):
                lines.append(f"    📂 {dname}")
                for l1 in payload.get("domain_links", {}).get(dname, []):
                    n1 = per.get((l1,), 0)
                    lines.append(f"        🗂 {l1}（{n1} 条）")
                    for l2 in cat_children.get(l1, []):
                        n2 = per.get((l1, l2), 0)
                        lines.append(f"            ▸ {l2}（{n2} 条）")
        return "\n".join(lines) or "（无内容）"

    # ------------------------------------------------------------------ #
    # 步骤切换与执行
    # ------------------------------------------------------------------ #
    def _show_step(self, step: int) -> None:
        self._step = step
        titles = {1: "第 1 / 3 步　选择源",
                  2: "第 2 / 3 步　层级映射 与 字段映射",
                  3: "第 3 / 3 步　预览与出口"}
        hints = {1: "支持粘贴文本、CSV/TSV/TXT、HTML 表格、Markdown 表格、网址"
                    "（网页 / GitHub 文件 / GitHub 目录批量）；"
                    "抓文档仓库建议先抓索引页小样确认结构",
                 2: "空单元格继承上一行；允许跳层（按兜底名补齐）",
                 3: "先保存 JSON v5 数据文件（同时导出一份 Excel），再选择是否导入该 JSON"}
        self.step_lbl.configure(text=titles[step])
        self.hint_lbl.configure(text=hints[step])
        for i, f in enumerate((self.frame1, self.frame2, self.frame3), 1):
            (f.lift if i == step else f.lower)()
        self.prev_btn.configure(state=("normal" if step > 1 else "disabled"))
        self.next_btn.configure(state=("normal" if step < 3 else "disabled"))
        if step == 3:
            self.saved_lbl.pack(side="right", padx=(0, 10))
            self.save_btn.pack(side="right", padx=(0, 8))
            self.import_btn.pack(side="right", padx=(0, 8))
            if not self.saved_path:
                self.import_btn.configure(state="disabled")
                self.saved_lbl.configure(text="请先「① 保存数据文件…」，保存成功后才可导入")
            else:
                self.saved_lbl.configure(text=f"已保存：{os.path.basename(self.saved_path)}")
        else:
            self.save_btn.pack_forget()
            self.import_btn.pack_forget()
            self.saved_lbl.pack_forget()

    def _go_prev(self) -> None:
        self._show_step(max(1, self._step - 1))

    def _go_next(self) -> None:
        if self._step == 1:
            if not self.source.get("rows"):
                messagebox.showwarning("提示", "请先解析源数据（点「解析」）。", parent=self)
                return
            self._refresh_step2()
            self._render_field_rows()
            self._show_step(2)
            return
        if self._step == 2:
            if not self._build_payload():
                return
            self._show_step(3)

    def _build_payload(self) -> bool:
        """按当前映射生成载荷并统计（失败返回 False）"""
        self.layer_map = self._collect_layer_map()
        if self.layer_map.get("l1") is None and self.layer_map.get("l2") is None:
            messagebox.showwarning("提示",
                                   "请至少映射『一级分类』或『二级分类』（否则条目无处安放）。",
                                   parent=self)
            return False
        fallbacks = {k: v.get().strip() for k, v in self.fb_vars.items()}
        # 5-d-2 行筛选：整行文本按"包含/排除关键词"过滤（统计写入质量自检"数量对账"）
        rows_all = self.source.get("rows") or []
        try:
            rows, self.filter_stat = himp.filter_rows(
                rows_all, self.filter_inc.get().strip(), self.filter_exc.get().strip())
        except Exception:                              # noqa: BLE001
            rows, self.filter_stat = rows_all, {}
        # 5-d-1 关键词规则：对**整行文本**匹配（名称、分类、各字段值任一命中即可）
        l2_rules = self._l2_rules()
        try:
            records, warns = himp.structure_rows(
                rows, self.layer_map, True, fallbacks, l2_rules=l2_rules)
        except Exception as exc:
            messagebox.showerror("结构化失败", str(exc), parent=self)
            return False
        if not records:
            messagebox.showwarning("提示", "没有任何有效数据行（请检查层级映射）。", parent=self)
            return False
        try:
            self.link_stat = {"completed": 0, "base": self._fetch_base}
            use_link = (self.link_fix_var.get() == "1" and bool(self._fetch_base))
            # 5-d-3：中英提示词自动分流（映射到 ⑧ 的列若判定"以英文为主" → 写入 ⑨）
            self.split_stat = {"moved": 0}
            payload, warns2 = himp.build_v5_payload(
                self.db, records, self.field_map,
                source_name=os.path.basename(self.file_var.get()) or "粘贴文本",
                link_base=(self._fetch_base if use_link else ""),
                link_stat=self.link_stat,
                split_lang=(self.split_lang_var.get() == "1"),
                split_stat=self.split_stat)
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc), parent=self)
            return False
        self.payload = payload
        self.warnings = list(warns) + list(warns2)
        self.records = records
        try:
            self.stats = himp.preview_stats(self.db, payload)
        except Exception:
            self.stats = {}
        self.stat_lbl.configure(
            text=("📊 " + himp.summarize(self.stats)) if self.stats else "（统计不可用）")
        self.tree_box.configure(state="normal")
        self.tree_box.delete("1.0", "end")
        self.tree_box.insert("end", self._tree_text(payload))
        self.tree_box.configure(state="disabled")
        # 2026-09-14（5-b）：质量自检面板
        try:
            custom_names = {d["field_key"]: d["display_name"] for d in self.db.list_field_defs()}
        except Exception:
            custom_names = {}
        rep = himp.quality_report(self.db, payload, records=records,
                                 rows_total=len(rows_all),
                                 stats=self.stats, link_stat=self.link_stat,
                                 filter_stat=self.filter_stat, split_stat=self.split_stat,
                                 l2_rule_on=bool(l2_rules))
        self.quality_box.configure(state="normal")
        self.quality_box.delete("1.0", "end")
        self.quality_box.insert("end", himp.format_quality_report(rep, custom_names))
        self.quality_box.configure(state="disabled")
        self.warn_box.configure(state="normal")
        self.warn_box.delete("1.0", "end")
        self.warn_box.insert("end", "\n".join(self.warnings[:12]) or "（无）")
        self.warn_box.configure(state="disabled")
        # 载荷已重建 → 之前保存的文件作废，需重新保存后才能导入（先存后导）
        self._reset_saved()
        return True

    def _reset_saved(self) -> None:
        """载荷变化后使"已保存文件"失效（先存后导硬约束）"""
        self.saved_path = ""
        self.saved_xlsx = ""
        try:
            self.import_btn.configure(state="disabled")
            self.saved_lbl.configure(text="请先「① 保存数据文件…」，保存成功后才可导入")
        except Exception:
            pass

    def _default_filename(self) -> str:
        return f"结构化资源_{datetime.now().strftime('%Y-%m-%d')}.json"

    def _fresh_payload(self) -> bool:
        return bool(self.payload) or self._build_payload()

    def _save_file(self, ask=True) -> str:
        """保存 JSON v5 数据文件；返回路径（取消返回 ""）

        2026-09-14（用户要求）：出口流程为 **先保存数据文件 → 再导入该文件**；
        因此这里保存成功后即启用「② 导入该文件」。
        """
        if not self._fresh_payload():
            return ""
        if ask:
            path = filedialog.asksaveasfilename(
                title="保存结构化数据文件（JSON v5）", parent=self,
                defaultextension=".json", initialfile=self._default_filename(),
                filetypes=[("JSON", "*.json")])
            if not path:
                return ""
        else:
            path = os.path.join(tempfile.gettempdir(),
                                f"ps_wizard_{datetime.now().strftime('%H%M%S_%f')}.json")
        try:
            himp.write_v5_file(self.payload, path)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return ""
        self.saved_path = path
        # 5-d-4：**同时**导出 Excel（同目录同名 .xlsx，列与"导出 Excel"一致、可被重新导入）
        self.saved_xlsx = self._save_excel_too(path)
        try:
            self.import_btn.configure(state="normal")
            self.saved_lbl.configure(
                text=f"已保存：{os.path.basename(path)}"
                     + (f"｜已同时导出 Excel" if self.saved_xlsx else ""))
        except Exception:
            pass
        self.toast(f"已保存：{os.path.basename(path)}"
                   + (f"（＋Excel）" if self.saved_xlsx else ""))
        return path

    def _save_excel_too(self, json_path: str) -> str:
        """把同一份载荷导出为 xlsx（与 JSON 同目录同名）；失败不阻断导入，只提示"""
        try:
            xlsx = os.path.splitext(json_path)[0] + ".xlsx"
            meta = [(rec.get("project", ""), rec.get("domain", ""))
                    for _no, rec, _row in (self.records or [])]
            n = excel_io.export_payload_excel(self.payload, xlsx, meta=meta)
            self.toast(f"已同时导出 Excel（{n} 条）：{os.path.basename(xlsx)}")
            return xlsx
        except Exception as exc:                        # noqa: BLE001
            self.toast(f"JSON 已保存；Excel 导出失败：{exc}")
            return ""

    def _import_saved(self) -> None:
        """导入"① 已保存"的数据文件（未保存时按钮为禁用，不可进入）"""
        if not self.saved_path:
            messagebox.showwarning("提示", "请先「① 保存数据文件…」，保存成功后再导入。",
                                   parent=self)
            return
        if not os.path.exists(self.saved_path):
            messagebox.showwarning("提示", "已保存的数据文件不存在（可能被移动/删除），请重新保存。",
                                   parent=self)
            self._reset_saved()
            return
        if not self._do_import(self.saved_path):
            return
        self.imported = True
        self.destroy()

    def _do_import(self, path: str) -> bool:
        """导入（导入前自动快照；失败给出"回滚"选项）"""
        mw = self.master
        db_path = getattr(self.db, "db_path", "")
        snap = backup.preimport_snapshot(db_path) if db_path else {"ok": False}
        try:
            total = json_io.count_json_entries(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return False
        # 2026-09-13：随包字段定义与本地不一致时，先让用户逐项选择（新增/覆盖/跳过）
        proceed, resolver = confirm_field_defs_file(self, self.db, path)
        if not proceed:
            messagebox.showinfo("已取消", "已取消导入（字段定义差异未确认）。", parent=self)
            return False
        dlg = ProgressDialog(self, total=total, message="正在导入结构化资源…")
        try:
            res = json_io.import_json(self.db, path, progress_cb=dlg.update_progress,
                                      field_defs_resolver=resolver)
        except Exception as exc:
            dlg.finish()
            self._offer_rollback(snap, str(exc))
            return False
        finally:
            try:
                dlg.finish()
            except Exception:
                pass
        try:
            mw.refresh_domains()
        except Exception:
            pass
        msg = (f"导入完成：新增条目 {res.get('entries', 0)} 条、判重跳过 {res.get('skipped', 0)} 条；"
               f"分类 {res.get('categories', 0)} 个。")
        if snap.get("ok"):
            msg += f"\n\n导入前快照：{os.path.basename(snap['path'])}"
        messagebox.showinfo("导入完成", msg, parent=self)
        return True

    def _offer_rollback(self, snap: dict, err: str) -> None:
        """导入异常：提示可用"导入前快照"一键回滚"""
        if not snap.get("ok"):
            messagebox.showerror("导入失败", f"{err}\n\n（未能生成导入前快照，无法自动回滚）",
                                 parent=self)
            return
        if not messagebox.askyesno(
                "导入失败",
                f"{err}\n\n是否用『导入前快照』回滚到导入前的数据？\n"
                f"（快照：{os.path.basename(snap['path'])}）", parent=self):
            return
        self._rollback(snap["path"])

    def _rollback(self, snap_path: str) -> None:
        mw = self.master
        db_path = getattr(self.db, "db_path", "")
        try:
            self.db.close()
            shutil.copy2(snap_path, db_path)
            mw.db = Database(db_path)
            self.db = mw.db
            try:
                mw.refresh_domains()
            except Exception:
                pass
            self.rolled_back = True
            messagebox.showinfo("已回滚", "已用导入前快照恢复数据。", parent=self)
        except Exception as exc:
            messagebox.showerror("回滚失败", str(exc), parent=self)

    # ------------------------------------------------------------------ #
    def toast(self, text: str) -> None:
        try:
            self.master.toast(text)
        except Exception:
            messagebox.showinfo("提示", text, parent=self)

    def _center(self) -> None:
        """屏幕水平居中、上边距 60"""
        self.update_idletasks()
        try:
            scale = ctk.ScalingTracker.get_window_scaling(self)
        except Exception:
            scale = 1.0
        w = int(self.winfo_reqwidth() * scale)
        x = max((self.winfo_screenwidth() - w) // 2, 0)
        self.geometry(f"+{x}+60")
        self.lift()
