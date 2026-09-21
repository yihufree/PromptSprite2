# -*- coding: utf-8 -*-
"""
import_wizard_dialog.py - T1 离线"网上资源结构化"向导（第 4 期 4-c）
创建日期：2026-09-13

三步向导（模态）：
  第 1 步 选择源：粘贴文本（自动探测分隔符）/ 本地 CSV·TSV·TXT / HTML 表格 / Markdown 表格 / JSON；
  第 2 步 映射：**「目标为主」一张表，四列＝目标位｜取值｜选项名（可改名）｜示例**
               （固定枚举全部目标位：4 层级 + 10 个内置条目字段 + 🏷 标签 + 自定义字段；
                每行反填"取自哪个源列 / 整层固定为某名称 / 忽略"）；
               层级行还可直接选已有选项或输入新名称（＝整层固定 / 新建该选项）；
               层级行"取自源列"后，第三列「选项名」可把源列里不合适的值**逐值改名**
               （如把 "1.act" 改成 "AI生图提示词大全"；改成已有选项名即合并复用）——
               界面层预处理源数据副本，数据层 hierarchy_import.py 零改动；
               表下方另有「源中未安置的列」，可对杂项列就地新建自定义字段并安置；
  第 3 步 预览与出口：树预览 + 新增/复用/判重统计 + 警告 →
               「保存数据文件（JSON v5）」/「保存并导入」（导入前自动快照 + 失败可一键回滚）。

2026-09-20 14:20 改造：① 本地 JSON 源；② 层级映射与字段映射合并为一张「源 → 目标」表，
  层级目标可"选已有选项 / 新建"；③ 删除误加在固定高标题行上的 weight（原字段映射行被遮盖）。

数据层全部复用 `app/parser/hierarchy_import.py`（结构化）与 `app/parser/json_io.py`（导入），
本文件只负责界面与流程编排。
"""
import os
import re
import shutil
import tempfile
from datetime import datetime

import customtkinter as ctk
from tkinter import filedialog, messagebox

from .. import backup
from .. import config            # 2026-09-20 14:20：② 项目类别"已有选项"含出厂预置
from ..database import Database
from ..parser import excel_io
from ..parser import hierarchy_import as himp
from ..parser import fetcher
from ..parser import json_io
from .field_defs_diff_dialog import confirm_field_defs_file  # 2026-09-13：字段定义差异逐项确认
from .github_batch_dialog import GithubBatchDialog          # 2026-09-14：5-c 批量多选
from .progress_dialog import ProgressDialog
from .ui_common import C_OK as _C_OK, C_DANGER as _C_DANGER  # 2026-09-17（U-2）：主色常量

_IGNORE = himp.IGNORE_TARGET
# 2026-09-20：第 2 步改为「目标为主」表（目标位｜取值｜示例）后，取值列用到的三个标签
_IGNORE_LABEL = "（忽略）"        # 取值列：该目标位不取值
_IGNORE_LAYER_LABEL = "（忽略 → 用兜底名）"   # 取值列（层级行）：该层不由源列提供，按兜底名补齐
_COL_PREFIX = "用源列："          # 取值列：取自某个源列（后接"序号. 列名"）
# 2026-09-20：映射表四列的固定最小宽度（表头与每个数据行共用同一套 → 跨行/跨表头对齐）
_COL_MINSIZE = (136, 296, 190)    # 目标位｜取值｜选项名；第 4 列「示例」吸收剩余宽度
# 2026-09-20 18:30：本程序运行于浅色主题，原行/表头底色硬编码为深色（#262d36/#20262d/#2f3944）
#   导致「深底 + 深字」糊成黑块、无法辨认；统一改为浅蓝系（隔行浅蓝 + 细框线 + 浅蓝表头）。
_ROW_BG_ALT = "#e8f0fb"           # 隔行底色（浅蓝）
_ROW_BG_BASE = "#f7fafd"          # 另一行底色（近白）：不写 "transparent"，因滚动区默认底为
                                  #   gray78（#C7C7C7 中灰），继承后与浅蓝混搭会发脏、对比模糊
_ROW_BORDER = "#cfd9e6"           # 行细框线（浅灰蓝）
_HEAD_BG = "#d6e4f7"              # 表头底色（浅蓝）
_HEAD_FG = "#1c3d63"              # 表头文字（深蓝）

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
        ctk.CTkButton(btn, text="创建", width=92, fg_color=_C_OK,
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
        ctk.CTkButton(foot, text="确定", width=88, fg_color=_C_OK,
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
            self.msg_lbl.configure(text_color=_C_DANGER,
                                   text="发现 %d 处问题：%s" % (len(errors), "；".join(errors[:3])))
            return False
        if not rules:
            self.msg_lbl.configure(text_color="#D08A00", text="还没有任何有效规则。")
            return False
        self.msg_lbl.configure(text_color=_C_OK,
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


class _RenameOptionsDialog(ctk.CTkToplevel):
    """层级行「选项名改名」（逐值）：列出该层从源列取到的全部不同取值，逐个填新名。

    2026-09-20 新增：`result` = {原值: 新名}（仅含真正改了的项，可为空 dict）或 None（取消）。
    改名只影响本次导入的落库选项名；填成已有选项名即与其合并复用（不会产生重复选项）。
    """

    def __init__(self, master, layer_label: str, col_name: str,
                 items: list, current: dict) -> None:
        super().__init__(master)
        self.result = None
        self.title("选项名改名")
        self.geometry("720x540")
        self.minsize(620, 420)
        self.transient(master)
        self.grab_set()

        pad = 16
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(self, text=f"『{layer_label}』选项名改名（逐值）",
                     font=("Microsoft YaHei", 14, "bold"), anchor="w"
                     ).grid(row=0, column=0, sticky="ew", padx=pad, pady=(14, 2))
        ctk.CTkLabel(self, text=f"来源列：{col_name}　共 {len(items)} 个不同取值。"
                                "在右侧填新名（留空＝不改）；填成已有选项名即与其合并复用。",
                     text_color="gray", font=("Microsoft YaHei", 11), anchor="w",
                     justify="left", wraplength=660
                     ).grid(row=1, column=0, sticky="ew", padx=pad)
        # 2026-09-20 18:30：表头改浅蓝底 + 深蓝字（原 #2f3944 深底在浅色主题下像黑块）
        head = ctk.CTkFrame(self, fg_color=_HEAD_BG, corner_radius=4)
        head.grid(row=2, column=0, sticky="ew", padx=pad, pady=(8, 2))
        head.grid_columnconfigure(0, minsize=300)
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="源列取值（出现行数）", anchor="w", text_color=_HEAD_FG,
                     font=("Microsoft YaHei", 11, "bold")
                     ).grid(row=0, column=0, sticky="w", padx=(6, 0), pady=4)
        ctk.CTkLabel(head, text="改名为（留空＝不改）", anchor="w", text_color=_HEAD_FG,
                     font=("Microsoft YaHei", 11, "bold")
                     ).grid(row=0, column=1, sticky="w", padx=(6, 0), pady=4)

        box = ctk.CTkScrollableFrame(self)
        box.grid(row=3, column=0, sticky="nsew", padx=pad, pady=(2, 4))
        self._entries = {}
        for n, (val, cnt) in enumerate(items):
            # 2026-09-20 18:30：隔行底色改浅蓝 + 加细框线（原深色在浅色主题下糊成黑块）
            r = ctk.CTkFrame(box, corner_radius=3,
                             fg_color=_ROW_BG_ALT if n % 2 == 0 else _ROW_BG_BASE,
                             border_width=1, border_color=_ROW_BORDER)
            r.pack(fill="x", pady=1)
            r.grid_columnconfigure(0, minsize=300)
            r.grid_columnconfigure(1, weight=1)
            shown = val.replace("\n", " ")
            ctk.CTkLabel(r, text=f"{shown[:40]}{'…' if len(shown) > 40 else ''}（{cnt} 行）",
                         anchor="w", font=("Microsoft YaHei", 11)
                         ).grid(row=0, column=0, sticky="w", padx=(6, 0), pady=3)
            e = ctk.CTkEntry(r, placeholder_text="（不改）")
            e.grid(row=0, column=1, sticky="ew", padx=(6, 6), pady=3)
            if current.get(val):
                e.insert(0, current[val])
            self._entries[val] = e

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=pad, pady=(4, 14))
        ctk.CTkButton(foot, text="取消", width=88, command=self.destroy).pack(side="right")
        ctk.CTkButton(foot, text="保存", width=88, fg_color=_C_OK,
                      command=self._ok).pack(side="right", padx=(0, 8))

        from .field_manager_dialog import _place_top_centered
        _place_top_centered(self)

    def _ok(self) -> None:
        """收集改名（只收"填了且与原值不同"的项）"""
        out = {}
        for val, e in self._entries.items():
            new = e.get().strip()
            if new and new != val:
                out[val] = new
        self.result = out
        self.destroy()


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
        # 2026-09-20：「目标为主」表的主状态（只此一份，其余皆由它派生）
        # 目标位 → 取值文本：
        #   ""／"（忽略）"／"（忽略 → 用兜底名）" ＝ 不取该目标位的值；
        #   "用源列：N. 列名"                     ＝ 取自第 N 个源列；
        #   其它文本（仅层级行）                   ＝ 该层整层固定为该名称（选已有选项 / 直接输入＝新建）
        self.tgt_val = {}
        self.unused_ignored = set()   # 「源中未安置的列」里被点过「忽略」的源列下标
        # 2026-09-20：层级"选项名改名"（第三列）——{层级: {源列原值: 新名}}，仅"取自源列"的层生效
        self.layer_rename = {}
        self.field_map = {}       # 派生态：{列下标: 目标}（由 tgt_val 推出，第 3 步载荷生成用）
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
                                        fg_color=_C_OK, state="disabled",
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
        ctk.CTkRadioButton(row, text="本地文件（CSV / TSV / TXT / HTML / MD / JSON）",
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
            filetypes=[("表格/文本/JSON", "*.csv *.tsv *.txt *.html *.htm *.md *.json"),
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
            self.fetch_lbl.configure(text=f"❌ 抓取失败：{exc}", text_color=_C_DANGER)
            return
        if not r.get("ok"):
            tip = ("　建议：① 检查网址与网络；② 换用「粘贴文本」；"
                   "③ 若为反爬站点，展开「高级」填写 Referer / Cookie 后重试。")
            self.fetch_lbl.configure(
                text=f"❌ 抓取失败（{r.get('error_kind') or 'unknown'}）：{r.get('error')}{tip}",
                text_color=_C_DANGER)
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
                                     text_color=_C_DANGER)
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
                     "③ 或改用「粘贴文本」。", text_color=_C_DANGER)
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
                text_color=_C_DANGER)
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
                text=f"✅ 批量抓取完成：{note}\n　　{base}", text_color=_C_OK)
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
                text_color=_C_OK)
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
            text=f"✅ 抓取成功：{base_url}\n　　{stat} · {note}", text_color=_C_OK)
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
        elif src.get("kind") == "json":     # 2026-09-20 14:20：本地 JSON 源解析提示
            detail += "，来源＝JSON（对象数组 / 二维数组 / 嵌套对象已按规则拍平）"
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
        # 2026-09-20：整段改为「目标为主」表——固定枚举全部目标位为行，逐行反填"取自哪个源列 /
        # 整层固定为某名称 / 忽略"；不再以源列为行主键（与旧「源 → 目标」表是转置关系）。
        # 注意：固定高标题行不要加 weight（曾因争空间被压成 1px → 内容被遮盖）。

        # ---- ① 「目标 → 源」一张表 ----
        ctk.CTkLabel(f, text="① 映射表（目标位｜取值｜选项名）：为每个「目标位」指定取值来源；"
                            "层级行可选已有选项 / 输入新名称＝新建；"
                            "取自源列后可在「选项名」列逐值改名",
                     font=("Microsoft YaHei", 12, "bold"), anchor="w"
                     ).grid(row=0, column=0, sticky="ew")
        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        self.map_stat_lbl = ctk.CTkLabel(bar, text="", text_color="gray",
                                         font=("Microsoft YaHei", 11))
        self.map_stat_lbl.pack(side="left")
        ctk.CTkButton(bar, text="全部忽略", width=84, height=26, fg_color="#8a94a6",
                      command=self._ignore_all).pack(side="right")
        ctk.CTkButton(bar, text="＋ 新建字段…", width=104, height=26, fg_color="#2f6fb0",
                      command=self._new_field).pack(side="right", padx=(0, 8))
        self.only_unset_var = ctk.StringVar(value="0")
        ctk.CTkCheckBox(bar, text="只看未指定", width=98, variable=self.only_unset_var,
                        onvalue="1", offvalue="0", command=self._render_target_rows,
                        font=("Microsoft YaHei", 11)).pack(side="right", padx=(0, 12))

        # 2026-09-20：表头改为四列（与数据行共用 _COL_MINSIZE → 列对齐），并加底色区分
        # 2026-09-20 18:30：表头改浅蓝底 + 深蓝字（原 #2f3944 深底在浅色主题下像黑块）
        head = ctk.CTkFrame(f, fg_color=_HEAD_BG, corner_radius=4)
        head.grid(row=2, column=0, sticky="ew")
        self._grid_cols(head)
        for _c, _t in enumerate(("目标位", "取值（取自源列 / 整层固定 / 忽略）",
                                 "显示名", "示例")):   # 2026-09-20：第三列表头「选项名」→「显示名」（进入目标后的名称）
            ctk.CTkLabel(head, text=_t, anchor="w", text_color=_HEAD_FG,
                         font=("Microsoft YaHei", 11, "bold")
                         ).grid(row=0, column=_c, sticky="w", padx=(6, 0), pady=4)

        self.map_scroll = ctk.CTkScrollableFrame(f, height=250)
        self.map_scroll.grid(row=3, column=0, sticky="nsew", pady=(2, 0))
        f.grid_rowconfigure(3, weight=1)

        # ---- ② 未取到值时的兜底名（某层没取到值时用哪个固定名补齐）----
        fb = ctk.CTkFrame(f, fg_color="transparent")
        fb.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        ctk.CTkLabel(fb, text="未取到值时的兜底名：", font=("Microsoft YaHei", 11),
                     text_color="gray").pack(side="left")
        self.fb_vars = {}
        for key, default in (("project", himp.DEFAULT_FALLBACK_PROJECT),
                             ("domain", himp.DEFAULT_FALLBACK_DOMAIN),
                             ("l1", himp.DEFAULT_FALLBACK_L1),
                             # 2026-09-20：补『二级分类』兜底名（原缺此项，选「忽略 → 用兜底名」时无处填写）
                             ("l2", himp.DEFAULT_FALLBACK_L2)):
            ctk.CTkLabel(fb, text=himp.LAYER_LABELS[key], font=("Microsoft YaHei", 11)
                         ).pack(side="left", padx=(10, 2))
            v = ctk.StringVar(value=default)
            ctk.CTkEntry(fb, width=120, textvariable=v).pack(side="left")
            self.fb_vars[key] = v

        # 2026-09-14（5-b）：链接补全开关（网址来源时默认开；其它来源无基准 URL，自动禁用）
        link_row = ctk.CTkFrame(f, fg_color="transparent")
        link_row.grid(row=5, column=0, sticky="ew", pady=(4, 0))
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
                     ).grid(row=6, column=0, sticky="ew", pady=(8, 0))
        enh = ctk.CTkFrame(f)
        enh.grid(row=7, column=0, sticky="ew", pady=(2, 0))
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
        # 2026-09-20 14:20：删除原函数尾部的旧 map_scroll（与上方 row2 的重复定义）

    # ---- 2026-09-20：「目标为主」表的行定义与取值解析 ---------------- #
    def _row_specs(self) -> list:
        """表的行定义：[(目标位, 类型, 分组标题 或 None), ...]

        类型："layer"＝层级（可整层固定 / 新建）/"field"＝条目字段（含自定义）/"tag"＝标签。
        """
        out = []
        for i, k in enumerate(himp.LAYER_KEYS):
            out.append((k, "layer",
                        "▣ 层级（分类）　取值：取自源列 / 选已有选项 / 直接输入新名称＝新建"
                        if i == 0 else None))
        for i, k in enumerate(himp.BUILTIN_ENTRY_KEYS):
            out.append((k, "field", "▣ 条目字段　取值：取自源列" if i == 0 else None))
        out.append((himp.TAG_TARGET, "tag", None))
        try:
            for d in self.db.list_field_defs():
                if not d.get("is_builtin"):
                    out.append((d["field_key"], "field", None))
        except Exception:                                  # noqa: BLE001
            pass
        return out

    def _target_label(self, key: str) -> str:
        """目标位在表里的显示名（层级 / 条目字段 / 标签 / 自定义字段）"""
        if key in himp.LAYER_LABELS:
            return himp.LAYER_LABELS[key]
        if key == himp.TAG_TARGET:
            return "🏷 标签"
        if key in himp.BUILTIN_LABELS:
            return himp.BUILTIN_LABELS[key]
        try:
            for d in self.db.list_field_defs():
                if d["field_key"] == key:
                    return f"自定义：{d['display_name']}"
        except Exception:                                  # noqa: BLE001
            pass
        return key

    def _col_options(self, hdr: list) -> list:
        """取值候选里的「取自源列」各项（以序号定位，列名变化也不影响解析）"""
        return [f"{_COL_PREFIX}{i + 1}. {h}" for i, h in enumerate(hdr)]

    # ---- 2026-09-20：映射表列对齐 + 层级"选项名逐值改名" ----------------- #
    @staticmethod
    def _grid_cols(box) -> None:
        """给表头 / 某个数据行设置同一套固定列宽（各行是独立 Frame，靠 minsize 跨行对齐）"""
        for i, w in enumerate(_COL_MINSIZE):
            box.grid_columnconfigure(i, minsize=w)
        box.grid_columnconfigure(len(_COL_MINSIZE), weight=1)   # 第 4 列「示例」吸收剩余

    def _layer_col_values(self, key: str) -> list:
        """该层级当前取自源列时，该列出现过的不同取值（首次出现顺序）+ 出现行数。

        2026-09-20 新增：供第三列显示"共几个选项"、以及改名弹窗列清单用。
        非"取自源列"（忽略 / 整层固定）→ 返回空列表。
        """
        col = self._src_col_of_key(key)
        if col is None:
            return []
        order, cnt = [], {}
        for r in (self.source.get("rows") or []):
            v = ((r[col] or "").strip() if col < len(r) else "")
            if not v:
                continue
            if v not in cnt:
                cnt[v] = 0
                order.append(v)
            cnt[v] += 1
        return [(v, cnt[v]) for v in order]

    def _edit_layer_rename(self, key: str) -> None:
        """打开"选项名改名"弹窗并把结果写回 self.layer_rename[key]（逐值改名）"""
        hdr = self.source.get("headers") or []
        col = self._src_col_of_key(key)
        if col is None:
            return
        dlg = _RenameOptionsDialog(self, self._target_label(key), f"{col + 1}. {hdr[col]}",
                                   self._layer_col_values(key),
                                   dict(self.layer_rename.get(key) or {}))
        try:
            self.wait_window(dlg)
        except Exception:                                  # noqa: BLE001
            pass
        if dlg.result is None:
            return
        self.layer_rename[key] = dict(dlg.result)
        n = len(dlg.result)
        self.toast(f"『{self._target_label(key)}』选项名已改 {n} 项" if n
                   else f"『{self._target_label(key)}』已清除全部改名")
        self.after(30, self._render_target_rows)

    def _apply_layer_rename(self, rows: list) -> list:
        """把「层级选项名改名」应用到源数据副本（界面层预处理，数据层零改动）。

        2026-09-20 新增：只对"取自源列"的层级生效——该列单元格值命中改名表 → 换为新名；
        无改名或该层非取自源列 → 原样返回（不拷贝，零开销零副作用）；
        命中行才做浅拷贝，绝不改动 self.source["rows"] 原数据。
        """
        plan = {}
        for key in himp.LAYER_KEYS:
            mp = self.layer_rename.get(key) or {}
            col = self._src_col_of_key(key)
            if mp and col is not None:
                plan[col] = mp
        if not plan:
            return rows
        out = []
        for r in (rows or []):
            r2 = list(r)
            hit = False
            for col, mp in plan.items():
                if col < len(r2):
                    v = (r2[col] or "").strip()
                    if v in mp:
                        r2[col] = mp[v]
                        hit = True
            out.append(r2 if hit else r)
        return out

    def _src_col_of_key(self, key: str):
        """该目标位当前取自哪个源列（未取源列 → None）"""
        v = (self.tgt_val.get(key) or "").strip()
        m = re.match(r"^" + re.escape(_COL_PREFIX) + r"(\d+)\.", v)
        if not m:
            return None
        i = int(m.group(1)) - 1
        hdr = self.source.get("headers") or []
        return i if 0 <= i < len(hdr) else None

    def _used_cols(self) -> set:
        """已被某个目标位取用的源列下标集合"""
        return {c for c in (self._src_col_of_key(k) for k in list(self.tgt_val))
                if c is not None}

    def _unused_cols(self) -> list:
        """「源中未安置的列」：既未被目标位取用、也未被用户忽略的源列下标

        2026-09-20 14:21 新增：单点定义——第 2 步「源中未安置的列」区与第 3 步质量自检汇总共用同一口径。
        """
        hdr = self.source.get("headers") or []
        used = self._used_cols()
        return [i for i in range(len(hdr))
                if i not in used and i not in self.unused_ignored]

    def _ignore_label(self, key: str) -> str:
        """该行「忽略」的显示文本（层级行带「用兜底名」提示）"""
        return _IGNORE_LAYER_LABEL if key in himp.LAYER_LABELS else _IGNORE_LABEL

    def _layer_fixed_name(self, key: str) -> str:
        """该层级被指定的固定名称（未指定 / 取自源列 → 空串）"""
        v = (self.tgt_val.get(key) or "").strip()
        if not v or v == _IGNORE_LAYER_LABEL or v.startswith(_COL_PREFIX):
            return ""
        return v

    def _layer_has_src_hint(self, key: str) -> bool:
        """源表头里是否存在"看起来属于该层级"的列（供情形 3b 的轻量提示判断）

        2026-09-20 新增：情形 3b＝源里没有该级对应的列，也没有合适的已有选项可选，
        此时唯一出路是"直接输入新名称＝新建该级"。第三列就地给出这一提示。
        """
        return any(self._guess_layer(h) == key for h in (self.source.get("headers") or []))

    def _layer_existing(self, key: str) -> list:
        """该层级"已有选项"名称列表；父级能确定时按父级级联过滤（父级取自源列 → 不过滤）"""
        def names(items) -> list:
            return [it["name"] for it in items]

        try:
            if key == "project":
                out = names(self.db.list_projects())
                for n in config.PROJECT_PRESETS:      # 出厂预置（尚未落库也列出）
                    if n not in out:
                        out.append(n)
                return out
            if key == "domain":
                p = self.db.get_project_by_name(self._layer_fixed_name("project"))
                return names(self.db.list_domains(project_id=(p["id"] if p else None)))
            if key == "l1":
                did = None
                for d in self.db.list_domains():
                    if d["name"] == self._layer_fixed_name("domain"):
                        did = d["id"]
                        break
                return names(self.db.list_categories(domain_id=did))
            if key == "l2":
                pid = None
                for c in self.db.list_categories():
                    if c["name"] == self._layer_fixed_name("l1"):
                        pid = c["id"]
                        break
                return names(self.db.list_categories(parent_id=pid))
        except Exception:                                  # noqa: BLE001
            return []
        return []

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
        """按表头名自动预填（目标位 → 取哪个源列），用户可在第 2 步逐行改"""
        hdr = self.source.get("headers") or []
        self.tgt_val = {}
        self.unused_ignored = set()
        used = set()
        for i, h in enumerate(hdr):        # 先按层级关键词占位（每层只取第一个命中的源列）
            key = self._guess_layer(h)
            if key and key not in self.tgt_val:
                self.tgt_val[key] = f"{_COL_PREFIX}{i + 1}. {h}"
                used.add(i)
        for i, h in enumerate(hdr):        # 再按条目字段关键词占位
            if i in used:
                continue
            tgt = self._guess_field(h)
            if tgt != _IGNORE and tgt not in self.tgt_val:
                self.tgt_val[tgt] = f"{_COL_PREFIX}{i + 1}. {h}"
                used.add(i)
        self._refresh_step2()
        self._render_target_rows()

    def _refresh_step2(self) -> None:
        """进入第 2 步时的刷新（链接补全开关；表格由 _render_target_rows 渲染）"""
        # 2026-09-14（5-b）：链接补全（仅网址来源有基准 URL）
        if self._fetch_base:
            self.link_base_lbl.configure(text=f"基准：{self._fetch_base}")
            self.link_fix_chk.configure(state="normal")
        else:
            self.link_base_lbl.configure(text="（非网址来源：无基准 URL，本项不生效）")
            self.link_fix_chk.configure(state="disabled")

    # ---- 2026-09-20：「目标 → 源」表的渲染与逐行交互 ---------------- #
    def _is_unset(self, key: str) -> bool:
        """该目标位是否尚未指定取值（空 / 忽略 → 未指定）"""
        v = (self.tgt_val.get(key) or "").strip()
        return (not v) or v == self._ignore_label(key)

    def _sample_of_col(self, col: int) -> str:
        """某源列的首个非空示例文本（供「示例」列 / 未安置列区显示）"""
        rows = self.source.get("rows") or []
        for r in rows:
            if col < len(r) and (r[col] or "").strip():
                return "示例：" + (r[col] or "").strip().replace("\n", " ")[:22]
        return "（该列无内容）"

    def _sample_of(self, key: str) -> str:
        """该目标位当前取值对应的示例（仅"取自源列"时才有；整层固定 / 忽略 → 空）

        2026-09-20：层级行若已「改名」，示例显示**改名后**的值（与最终入库一致）。
        """
        col = self._src_col_of_key(key)
        if col is None:
            return ""
        mp = self.layer_rename.get(key) or {}
        for r in (self.source.get("rows") or []):
            v = ((r[col] or "").strip() if col < len(r) else "")
            if not v:
                continue
            v = mp.get(v, v)   # 2026-09-20：命中改名表 → 显示改名后的值
            return "示例：" + v.replace("\n", " ")[:22]
        return "（该列无内容）"

    def _update_map_stat(self) -> None:
        """更新工具条上的「已安置 x / y 个源列」"""
        hdr = self.source.get("headers") or []
        self.map_stat_lbl.configure(text=f"已安置 {len(self._used_cols())} / {len(hdr)} 个源列")

    def _render_target_rows(self) -> None:
        """渲染「目标 → 源」表：固定枚举全部目标位为行，逐行指定取值来源"""
        for w in self.map_scroll.winfo_children():
            w.destroy()
        self._row_sample = {}
        self._row_vars = {}          # 2026-09-20：登记各「取值」输入框变量（供点「下一步」时自动收割手输名称）
        self._unused_box = None
        hdr = self.source.get("headers") or []
        if not hdr:
            ctk.CTkLabel(self.map_scroll, text="（请先在第 1 步解析源数据）",
                         text_color="#9aa4b1", font=("Microsoft YaHei", 11)).pack(anchor="w")
            self.map_stat_lbl.configure(text="")
            return
        only_unset = (self.only_unset_var.get() == "1")
        specs = self._row_specs()
        # 分组标题：某组在（「只看未指定」过滤后）一行都不剩时，标题也不显示
        gid, gids, titles = 0, [], {}
        for _key, _kind, group in specs:
            if group:
                gid += 1
                titles[gid] = group
            gids.append(gid)
        vis = [i for i, (key, _k, _g) in enumerate(specs)
               if (not only_unset) or self._is_unset(key)]
        vset, vgids = set(vis), {gids[i] for i in vis}
        col_opts = self._col_options(hdr)
        ln = 0                                             # 2026-09-20：数据行序号（隔行底色用）
        for i, (key, kind, group) in enumerate(specs):
            if group and gids[i] in vgids:
                ctk.CTkLabel(self.map_scroll, text=group, anchor="w", text_color="#5aa0e0",
                             font=("Microsoft YaHei", 11, "bold")).pack(fill="x", pady=(6, 0))
            if i not in vset:
                continue
            ln += 1
            # 2026-09-20：数据行改 grid + 与表头共用 _COL_MINSIZE（列对齐）＋ 隔行底色（行区分）
            # 2026-09-20 18:30：隔行底色改浅蓝 + 加细框线（原深色 #262d36/#20262d 在浅色主题下糊成黑块）
            row = ctk.CTkFrame(self.map_scroll, corner_radius=3,
                               fg_color=_ROW_BG_ALT if ln % 2 else _ROW_BG_BASE,
                               border_width=1, border_color=_ROW_BORDER)
            row.pack(fill="x", pady=1)
            self._grid_cols(row)
            ctk.CTkLabel(row, text=self._target_label(key), anchor="w",
                         font=("Microsoft YaHei", 12)
                         ).grid(row=0, column=0, sticky="w", padx=(6, 0), pady=3)
            cur = (self.tgt_val.get(key) or "").strip() or self._ignore_label(key)
            var = ctk.StringVar(value=cur)
            if kind == "layer":       # 层级行：取自源列 / 选已有选项 / 直接输入新名称＝新建
                w = ctk.CTkComboBox(row, variable=var,
                                    values=[self._ignore_label(key)] + col_opts
                                           + self._layer_existing(key),
                                    font=("Microsoft YaHei", 11),
                                    command=lambda v, k=key: self._set_tgt_val(k, v))
                w.bind("<Return>", lambda _e, k=key, sv=var: self._set_tgt_val(k, sv.get()))
                self._row_vars[key] = var   # 2026-09-20：登记（手输新名称＝整层固定，点「下一步」时自动收割）
            else:                     # 条目字段 / 标签行：只能"取自源列"或"忽略"
                w = ctk.CTkOptionMenu(row, variable=var,
                                      values=[self._ignore_label(key)] + col_opts,
                                      font=("Microsoft YaHei", 11),
                                      command=lambda v, k=key: self._set_tgt_val(k, v))
            w.grid(row=0, column=1, sticky="ew", pady=3)
            # 第三列「显示名」：2026-09-20 新增——
            #   层级行且取自源列 → 「改名…」按钮（逐值改名，显示已改/总个数）；
            #   层级行但整层固定 → 只读显示该固定选项名；其它行不适用 → 「—」
            # 2026-09-20：按钮文案带「层级 · 源列」标识（原来只有「改名…（n 个）」，
            #   多行同名按钮时分不清改的是哪一层、哪一列）
            if kind == "layer" and self._src_col_of_key(key) is not None:
                _n = len(self._layer_col_values(key))
                _nren = len(self.layer_rename.get(key) or {})
                _ci = self._src_col_of_key(key)
                _cn = f"{_ci + 1}. {hdr[_ci]}" if _ci < len(hdr) else ""
                _txt = (f"改名…（{self._target_label(key)} · {_cn}｜已改 {_nren}/{_n}）" if _nren
                        else f"改名…（{self._target_label(key)} · {_cn}｜{_n} 个）")
                ctk.CTkButton(row, text=_txt,
                              height=24, fg_color="#2f6fb0", font=("Microsoft YaHei", 11),
                              command=lambda k=key: self._edit_layer_rename(k)
                              ).grid(row=0, column=2, sticky="ew", padx=(6, 6), pady=3)
            elif kind == "layer":
                _fx = self._layer_fixed_name(key)
                if _fx:
                    _t3, _tc = f"固定：{_fx}", "gray"
                elif not self._layer_has_src_hint(key):
                    # 2026-09-20：情形 3b 轻量提示——源里没有该级对应的列时，就地告知可新建
                    _t3, _tc = "—（源中无此级列 → 可直接输入新名称新建）", "#9aa4b1"
                else:
                    _t3, _tc = "—", "#6d7683"
                ctk.CTkLabel(row, text=_t3, anchor="w",
                             text_color=_tc, font=("Microsoft YaHei", 11)
                             ).grid(row=0, column=2, sticky="w", padx=(6, 0), pady=3)
            else:
                ctk.CTkLabel(row, text="—", anchor="w", text_color="#6d7683",
                             font=("Microsoft YaHei", 11)
                             ).grid(row=0, column=2, sticky="w", padx=(6, 0), pady=3)
            smp = ctk.CTkLabel(row, text=self._sample_of(key), text_color="gray", anchor="w",
                               font=("Microsoft YaHei", 10))
            smp.grid(row=0, column=3, sticky="w", padx=(8, 6), pady=3)
            self._row_sample[key] = smp
        self._update_map_stat()
        self._render_unused(hdr)

    def _render_unused(self, hdr: list) -> None:
        """「源中未安置的列」区：对未被任何目标位取用、且未被忽略的源列就地处置

        ——「＋ 新建字段…」＝新建一个自定义字段并把该列安置到它（避免"字段溢出"）；
        ——「忽略」＝只不再提示该列，不影响已有映射。
        """
        parent = getattr(self, "map_scroll", None)
        if parent is None or not parent.winfo_exists():
            return
        old = getattr(self, "_unused_box", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        # 2026-09-20 14:21：改用单点定义 _unused_cols()（与第 3 步自检同一口径，行为不变）
        rest = self._unused_cols()
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x", pady=(10, 0))
        self._unused_box = box
        ctk.CTkLabel(box, text=f"▣ 源中未安置的列（{len(rest)}）：可「＋ 新建字段」安置，"
                               "或「忽略」不再提示",
                     anchor="w", text_color="#d0a85a",
                     font=("Microsoft YaHei", 11, "bold")).pack(fill="x")
        if not rest:
            ctk.CTkLabel(box, text="（无：所有源列都已安置或已忽略）", text_color="gray",
                         anchor="w", font=("Microsoft YaHei", 10)).pack(fill="x")
            return
        for n, i in enumerate(rest):
            # 2026-09-20：改用 grid + 与主表相同的列宽（列对齐）＋ 隔行底色（行区分）
            # 2026-09-20 18:30：隔行底色改浅蓝 + 加细框线（同主表，浅色主题下可辨认）
            r = ctk.CTkFrame(box, corner_radius=3,
                             fg_color=_ROW_BG_ALT if n % 2 == 0 else _ROW_BG_BASE,
                             border_width=1, border_color=_ROW_BORDER)
            r.pack(fill="x", pady=1)
            self._grid_cols(r)
            ctk.CTkLabel(r, text=f"{i + 1}. {hdr[i]}", anchor="w",
                         font=("Microsoft YaHei", 11)
                         ).grid(row=0, column=0, sticky="w", padx=(6, 0), pady=3)
            ctk.CTkLabel(r, text=self._sample_of_col(i), text_color="gray", anchor="w",
                         font=("Microsoft YaHei", 10)
                         ).grid(row=0, column=1, sticky="w", padx=(6, 0), pady=3)
            btns = ctk.CTkFrame(r, fg_color="transparent")
            btns.grid(row=0, column=3, sticky="e", padx=(0, 6), pady=3)
            ctk.CTkButton(btns, text="＋ 新建字段…", width=104, height=24, fg_color="#2f6fb0",
                          command=lambda idx=i: self._new_field(idx)).pack(side="left",
                                                                          padx=(0, 6))
            ctk.CTkButton(btns, text="忽略", width=56, height=24, fg_color="#8a94a6",
                          command=lambda idx=i: self._ignore_unused(idx)).pack(side="left")

    def _ignore_unused(self, col: int) -> None:
        """在「源中未安置的列」里忽略某列（只是不再提示该列，不影响已有映射）"""
        self.unused_ignored.add(col)
        self._render_unused(self.source.get("headers") or [])

    def _set_tgt_val(self, key: str, label: str) -> None:
        """把某目标位的取值改为 label；同一源列只允许一个目标位取用（后取者占，前者让出）"""
        label = (label or "").strip()
        old_col = self._src_col_of_key(key)     # 2026-09-20：改前的源列（用于判断是否换了源列）
        old_label = (self.tgt_val.get(key) or "").strip()   # 2026-09-20：改前的取值文本（用于判断取值是否变化）
        new_label = "" if label in ("", self._ignore_label(key)) else label
        self.tgt_val[key] = new_label
        col = self._src_col_of_key(key)
        if col != old_col:
            # 2026-09-20：换了源列（或不再取自源列）→ 旧"选项名改名"映射已失效，一并清除
            self.layer_rename.pop(key, None)
        # 2026-09-20：取值文本变化也要重绘（否则第三列「显示名」停留旧状态：按钮不出现 / 改忽略后按钮残留）
        redraw = (new_label != old_label)
        if col is not None:
            for other in list(self.tgt_val):
                if other != key and self._src_col_of_key(other) == col:
                    self.tgt_val[other] = ""      # 让出该源列（回到「忽略」）
                    self.layer_rename.pop(other, None)   # 2026-09-20：其改名映射同步失效
                    redraw = True
        if redraw:
            self.after(30, self._render_target_rows)   # 延迟重绘（避免销毁正在回调的下拉）
            return
        smp = getattr(self, "_row_sample", {}).get(key)
        if smp is not None and smp.winfo_exists():
            smp.configure(text=self._sample_of(key))
        self._update_map_stat()
        self._render_unused(self.source.get("headers") or [])

    def _harvest_row_inputs(self) -> None:
        """把层级行「取值」列里手输但未回车的新名称收割进映射（点「下一步」时自动提交）

        2026-09-20：原设计必须按回车才生效（隐性操作），改为点「下一步」也自动生效。
        仅处理**层级行且文本确有变化**的情况；「（忽略 → 用兜底名）」项已在 _set_tgt_val 里
        被折算为空串，这里按忽略标签跳过，避免把忽略标签误当作新选项名。
        """
        for key, var in list(getattr(self, "_row_vars", {}).items()):
            try:
                txt = (var.get() or "").strip()
            except Exception:                      # noqa: BLE001
                continue
            if not txt or txt == self._ignore_label(key):
                continue                           # 空 / 下拉选的「忽略」→ 无需收割
            if txt == (self.tgt_val.get(key) or "").strip():
                continue                           # 取值未变化 → 不重复提交
            self._set_tgt_val(key, txt)

    def _ignore_all(self) -> None:
        """全部忽略：清空所有目标位的取值与「未安置列」的忽略状态（表回到初始未指定）"""
        self.tgt_val = {}
        self.unused_ignored = set()
        self.layer_rename = {}          # 2026-09-20：改名映射一并清空（回到初始态）
        self._render_target_rows()

    def _new_field(self, col=None) -> None:
        """新建一个自定义字段；col 为源列下标时（来自「未安置的列」），建好后立即安置该列"""
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
        hdr = self.source.get("headers") or []
        if col is not None and 0 <= col < len(hdr):
            self.tgt_val[key] = f"{_COL_PREFIX}{col + 1}. {hdr[col]}"
            self.unused_ignored.discard(col)
            self.toast(f"已新建自定义字段：{name}，并把源列「{hdr[col]}」安置到它")
        else:
            self.toast(f"已新建自定义字段：{name}（请在表里把某行取值改选为它）")
        self.after(30, self._render_target_rows)   # 表体刷新（新增行 / 安置列需重新渲染）

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
                                       text_color=_C_OK)
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
        """由「目标 → 源」表反推 {层级: 源列下标 或 None}（同一层只取一个；整层固定 → None）"""
        return {k: self._src_col_of_key(k) for k in himp.LAYER_KEYS}

    def _collect_field_map(self) -> dict:
        """由「目标 → 源」表反推 {源列下标: 目标位}（供第 3 步 build_v5_payload 沿用）"""
        out = {}
        for key in list(self.tgt_val):
            if key in himp.LAYER_LABELS:            # 层级由 layer_map/consts 承担，不进字段映射
                continue
            col = self._src_col_of_key(key)
            if col is not None:
                out[col] = key
        return out

    def _collect_consts(self) -> dict:
        """被指定为固定名称的层 → {层级: 名称}（整层固定，优先于源列）"""
        out = {}
        for key in himp.LAYER_KEYS:
            name = self._layer_fixed_name(key)
            if name:
                out[key] = name
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
                                     text_color=_C_OK)
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
                  2: "第 2 / 3 步　目标 → 源 映射",
                  3: "第 3 / 3 步　预览与出口"}
        hints = {1: "支持粘贴文本、CSV/TSV/TXT、HTML 表格、Markdown 表格、JSON、网址"
                    "（网页 / GitHub 文件 / GitHub 目录批量）；"
                    "抓文档仓库建议先抓索引页小样确认结构",
                 2: "为左侧每个「目标位」指定取值来源：选一个源列（同一源列只能给一个目标位）；"
                    "层级行还可选该层『已有选项』或直接输入新名称（＝新建该选项）；"
                    "源中没安置的列在表下方可就地「＋ 新建字段」或忽略；"
                    "空单元格继承上一行，允许跳层（按兜底名补齐）",
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
            self._render_target_rows()
            self._show_step(2)
            return
        if self._step == 2:
            self._harvest_row_inputs()   # 2026-09-20：先收割「取值」列手输的新名称（免回车），再校验/生成载荷
            if not self._build_payload():
                return
            self._show_step(3)

    def _build_payload(self) -> bool:
        """按当前映射生成载荷并统计（失败返回 False）"""
        self.layer_map = self._collect_layer_map()
        self.field_map = self._collect_field_map()   # 派生态：由「目标 → 源」表反推
        consts = self._collect_consts()     # 2026-09-20：整层固定（已有选项 / 新建名称）
        if (self.layer_map.get("l1") is None and self.layer_map.get("l2") is None
                and not consts.get("l1") and not consts.get("l2")):
            # 2026-09-20：文案改进——明确「兜底名不能替代层级取值」（校验口径不变）
            messagebox.showwarning("提示",
                                   "条目必须能归入『一级分类』或『二级分类』，"
                                   "请至少给其中之一指定取值：\n"
                                   "　① 选一个源列（取自源列的层级列）；\n"
                                   "　② 或在层级行选一个已有选项 / 直接输入新名称（＝新建）。\n\n"
                                   "注意：下方「未取到值时的兜底名」只在某行该层为空时补齐，"
                                   "不能替代这里的层级取值。",
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
        # 2026-09-20：层级「选项名逐值改名」＝界面层预处理（只改源数据副本，数据层零改动）
        rows = self._apply_layer_rename(rows)
        # 5-d-1 关键词规则：对**整行文本**匹配（名称、分类、各字段值任一命中即可）
        l2_rules = self._l2_rules()
        try:
            records, warns = himp.structure_rows(
                rows, self.layer_map, True, fallbacks, l2_rules=l2_rules, consts=consts)
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
        qtext = himp.format_quality_report(rep, custom_names)
        # 2026-09-20 14:21：第 3 步自检新增「⑧ 源中未安置的列」汇总
        # （纯界面层：取向导已有的 _unused_cols()，未改 hierarchy_import.py 一行）
        rest = self._unused_cols()
        if rest:
            hdr_all = self.source.get("headers") or []
            shown = "、".join(f"{i + 1}. {hdr_all[i]}" for i in rest[:6])
            qtext += (f"\n⑧ 源中未安置的列：{len(rest)}（{shown}"
                      f"{'…' if len(rest) > 6 else ''}）→ 建议回第 2 步处置"
                      "（「＋ 新建字段」安置 或「忽略」）")
        else:
            qtext += "\n⑧ 源中未安置的列：无 ✅（所有源列都已安置或已忽略）"
        self.quality_box.configure(state="normal")
        self.quality_box.delete("1.0", "end")
        self.quality_box.insert("end", qtext)
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
