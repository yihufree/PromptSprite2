# -*- coding: utf-8 -*-
"""
main_window.py - 主窗口：界面布局与交互逻辑
创建日期：2026-08-12（阶段一创建；阶段三~六完善）

布局（五行结构）：
  ┌ 顶部工具栏：🧩PromptSprite2 | 🔒锁定 | 🗂目录隐藏 | 📂无类条目 | ⭐常用 | ✚新建 | ⇩导入 | ⇧导出 | ⚙设置 | [搜索框🔍] ┐
  ├ L0根目录列 | L1一级分类列 | L2二级分类列 | 条目区(卡片/列表) | 详情区(9字段可编辑)         ┤
  └ 底部状态栏（备份失败黄点警告）                                                              ┘

交互逻辑：
  - 点击根目录 → 刷新一级列；点击一级 → 刷新二级列；点击二级 → 刷新条目区
  - 条目卡片/列表切换；点击条目 → 详情区：9 字段可编辑 + 图片关联预览 + 复制(全部/中文/英文) + 收藏 + 删除
  - 切换条目有未保存修改时弹窗：保存/放弃/取消
  - 条目右键：移动到分类(树形选择器)/收藏/复制/删除；分类右键：新增子分类/重命名/删除
  - 🔒锁定：开启后所有删除功能置灰；删除一律二次确认；删除分类其下条目自动转入"未分类"
  - 搜索框实时过滤；清空恢复当前视图
  - 导入：JSON 备份 / Excel / Markdown 手册（均带进度条）；导出：全部或当前分类的 JSON/Excel/HTML
  - ESC 隐藏（托盘恢复）；全局热键呼出时自动聚焦搜索框
"""
import json  # 2026-09-13（第 3 期 3-a-2）：列表框字段多选值的 JSON 存取
import os
import re  # 2026-08-18（第020条，P2-B2 修复）：import re 由 _open_image_plan 函数内上移至模块顶部
import shutil
import tkinter as tk
import tkinter.font as tkfont
import webbrowser  # 2026-08-18（第015条）：详情"⑩图像获取方案"打开链接按钮
from datetime import datetime  # 2026-08-29（M4）：增量备份文件名日期
from tkinter import filedialog, messagebox, simpledialog, ttk  # 2026-09-13（3-c+）：候选列表 Treeview
from typing import List, Optional  # 2026-09-11：List 供分类列排序辅助方法标注类型

import customtkinter as ctk
import pyperclip

from .. import config
from .. import ui_appearance   # 2026-09-15（批次 4）：界面外观（字体/字号/颜色）数据层
from .. import backup as backup_mod  # 2026-09-08（V1.7.0）：导入前快照 preimport_snapshot
from .. import tagger  # 2026-09-14（阶段 0.5）：标签词表存取/校验/导入导出（纯数据层）
from .. import tagger_engine  # 2026-09-14（阶段 3）：录入时自动推荐标签（引擎）
from ..models import Entry
from ..parser import excel_io, html_export, json_io, md_parser
from .change_import_dialog import ChangeImportDialog  # 2026-09-08（V1.7.0）：变更包导入向导
from .batch_tag_dialog import BatchTagDialog  # 2026-09-14（阶段 2）：批量智能自动打标
from .column_visibility_dialog import ColumnVisibilityDialog  # 2026-09-10：目录隐藏/目录显示对话框
from .copy_move_dialog import CopyMoveDialog  # 2026-08-21（第004条）：各级目录"复制到/移动到"
from .field_defs_diff_dialog import confirm_field_defs_file  # 2026-09-13：导入前字段定义差异确认
from .field_manager_dialog import FieldManagerDialog  # 2026-09-13（1-A-4）：字段管理（内置区块改名）
from .export_scope_dialog import ExportScopeDialog  # 2026-09-22（用户要求 1）：导出前范围确认（结构树）
from .import_wizard_dialog import ImportWizardDialog  # 2026-09-13（第 4 期 4-c）：网上资源结构化向导
from .move_selector import MoveSelector
from .progress_dialog import ProgressDialog
from .quick_add import QuickAddWindow
from .settings_dialog import SettingsDialog  # 2026-08-18："设置"入口
from ..incremental_backup import (get_computer_code, incr_dir,  # 2026-08-29（M4）：增量备份
                                  write_incremental, export_incremental_to)
from .ui_common import ADD_BTN_STYLE as _ADD_BTN  # 2026-09-09（P2-12）：与快速新建共用公共样式/工具
from .ui_common import SEL_BTN_STYLE as _SEL_BTN
from .ui_common import rows_to_px as _rows_to_px
from .ui_common import install_edit_capability as _enable_text_undo  # 2026-09-09：文本框撤销/重做
from .ui_common import set_boxes_readonly as _set_boxes_readonly  # 2026-09-09：浏览只读（可选中复制）
from . import ui_common as _ui_common  # 2026-09-14（阶段 3）：标签配色公共实现（quick_add 共用）
from .ui_common import C_OK as _C_OK, C_TAG as _C_TAG, C_WARN as _C_WARN, C_DANGER as _C_DANGER  # 2026-09-17（U-2）：主色常量
# 2026-09-17（审核 R-1/R-3/R-6）：共用实现统一收拢到 ui_common —— 屏幕工作区 / 悬停提示 /
#   文本框自适应高度 / CTk 私有控件访问。本文件一律经这些入口使用，不再各写一份实现。
work_area = _ui_common.work_area


# 详情区字段展示配置：(显示名, 数据库字段键, 文本框高度行数)
# 注：⑧/⑨ 提示词字段的实际高度由 _build_collapsible_field 按像素控制（默认 120px=6 行可见），
#     此处 24 仅为占位值、不参与渲染（2026-08-18 修正：CTkTextbox.height 单位是像素）。
_FIELDS = [
    ("② 介绍", "intro", 3),
    ("③ 溯源", "origin", 3),
    ("④ 核心特征", "features", 3),
    ("⑤ 应用场景", "scenes", 3),
    ("⑥ 代表作", "works", 2),
    ("⑦ 代表高清配图", "image_desc", 3),
    ("⑧ 中文版提示词", "prompt_cn", 24),
    ("⑨ 英文版提示词", "prompt_en", 24),
    ("⑩ 图像获取方案", "image_plan", 3),
]

# 2026-09-10（用户要求 3）：左侧四个分类列的列宽（逻辑px，顺序＝主界面从左到右）
#   项目类别 / 根目录 / 一级分类 / 二级分类；既用于 _build_body 建列，也用于"目录隐藏"后
#   动态计算窗口最小宽度（隐藏 n 列即按前 n 列列宽之和减小最小宽度）。
# 2026-09-22（用户明确批准）：**「项目类别 / 根目录」两列由 112 → 140**（一/二级保持 168）——
#   原因：112 源码px 在当前 15pt 字号下仅可见约 7 个汉字，较长的项目类别 / 根目录名会被截断；
#   140 源码px 可见约 8.8 个汉字，可完整显示 8 字以内名称。
#   ⚠ 该常量**未经用户批准不得修改**（用户已明确要求）；本次已获批准。
#   连带的窗口最小宽度标定同步上调（见下方 _NAV_MIN_WIDTH_BY_HIDDEN）。
#   历史：2026-09-13 时为 (112, 112, 168, 168)（四列合计 560；现为 140+140+168+168 = 616）。
_NAV_COL_WIDTHS = (140, 140, 168, 168)
# 2026-09-22（用户要求 2）：条目区宽度——默认总宽即原先的固定值 224。
#   运行期可拖动条目区右缘的"分隔条"调整宽度（右侧详情区自动让位）；
#   宽度**只存在内存、不写任何设置** ⇒ 无论上次调到多少，下次启动都按默认值 224 显示。
#   ⚠ 分隔条宽度算在 224 之内（滚动列表内容宽 = 224 − 6），以保证**条目区列总宽与改动前
#     完全一致**，从而不改动"窗口最小宽度 / 详情区最小宽"的既有标定。
_ENTRY_COL_W = 224       # 条目区**含右缘分隔条**的总宽默认值（＝改动前的固定值）
_ENTRY_GRIP_W = 6        # 右缘"可拖拽分隔条"宽度
_ENTRY_W_MIN = 150       # 可拖到的最小总宽（源码像素，避免拖成不可用）
_ENTRY_W_MAX = 520       # 可拖到的最大总宽（源码像素，避免把详情区挤没）
# 2026-09-10（用户要求）：四列全显示时的窗口最小宽度由 1360 提高到 1420——
# 详情区第 2 行（⑧/⑨ 复制全部/中文/英文 + 保存/重置）在 1360 宽时缺约 60px，
# Tk 的 pack 会把缺口全部压到最后排入的"重置"上导致其文字被裁切；加宽后该行完整显示。
# 2026-09-13（1-C-3）：工具栏新增"🏷 标签"按钮（约 +76px），最小宽度同步上调，避免缩窗时
# "导入/导出/设置/搜索框"被挤出可视区。
# 2026-09-14（用户明确要求，本轮）：**详情区最小宽度减小五分之一**。
#   详情区宽度 = 窗口宽度 − 16（窗口左右边距） − 560（四级目录） − 224（条目区）；
#   改动前：1496 − 800 = **696px** → 减小 1/5（−139px）→ **557px**
#   → 故 _BASE_MIN_WIDTH 由 1496 改为 **1357**（＝557 + 800）。
#   ⚠ 提醒：2026-09-10 的验证记录是"详情区第 2 行在窗口 1420（详情区 620）时完整显示"，
#   本次按用户要求降到 557 < 620 → **窗口缩到最窄时，详情区第 2 行可能被压/裁**。
#   本轮已把该行的「重置」（72→56）与「编辑/浏览」（总宽 134→120）收窄，可抵消一部分；
#   若仍不理想，请告知（属用户可见行为，需你决定是否再调）。
# 2026-09-14（用户要求"省宽"）：工具栏最小可用宽度 = 各列最小宽求和 + 左右边距
#   = 标题242+标签85+目录隐藏107+无类条目105+无标条目105+常用85+新建102+导入76+导出76
#     +锁定85+设置78+搜索框列(127+10)=137 + 16 = **1299** ← 窗口低于此值工具栏右侧会被裁。
# 2026-09-14（用户明确要求，本轮 3）：
#   · `_MIN_WIDTH_FLOOR` **恢复为你原来的 1104**（我此前擅自改到 1230/1329/1299，现按你要求回退）。
#   · 详情区最小宽度 **+3 个汉字（3×14 = 42px）**：557 → **599** → `_BASE_MIN_WIDTH` 1357 → **1399**。
#     （详情区宽 = 窗口宽 − 16 − 560（四级目录） − 224（条目区））
#   · 注：下限回到 1104 后，窗口可被缩到 1104，此时**顶部工具栏右侧会被裁**（这是你原设计的状态）；
#     四列全显示时的最小宽度为 1399。
# 2026-09-14（用户明确要求，本轮 4）：**把"当前详情区的实际宽度"定为所有状态下详情区的最小宽度**。
#   实测（临时脚本，本机 120% 缩放、四列全显示）：
#     a) 默认窗口（实际 1773 px）下 **详情区 = 685 px**（"目前详情区的宽度"即此值）；
#     b) 且关系恒定：**详情区 = 窗口实际宽度 − 1088**
#        （1088 = nav 四列实际 670 + 条目区实际 268 + 其它固定开销 150）；
#     c) ⚠ 已确认 **CTk 的 `geometry()` 与 `minsize()` 同样按控件缩放(1.2)换算**，
#        故本常量是"源码值"，实际生效 = 源码 × 1.2。标定实测：
#          源码 1478 → 窗口最小 1770 → 详情区 682
#          源码 1496 → 窗口最小 1792 → 详情区 704
#    → 取 **1481**（窗口最小 ≈ 1773 实际，详情区 ≈ 686 px，比"目前 685"略大 1px，方向安全）。
#   隐藏分类列时窗口最小会按列宽下调且不低于 _MIN_WIDTH_FLOOR(1104)，详情区只会更大，
#   故"所有状态下详情区 ≥ 685"恒成立。
# 2026-09-14（用户明确要求，本轮 6）：**详情区最小宽度改为 550px**（继续看效果）。
#   实测关系（本机 120% 缩放）：**详情区 = 窗口实际宽度 − 1088**，窗口实际最小 ≈ 源码 × 1.2 − 3；
#   标定参考：源码 1310 → 窗口 1569 → 详情区 481（源码每 ±1 → 详情区 ±1.2）。
#   550 ⇒ 窗口实际 1638 ⇒ 源码 ≈ **1368**（实测 1368 → 窗口 ≈1638 → 详情区 ≈550）。
# 2026-09-14 20:40（用户决定 B，本轮）：详情区最小宽 **550 → 571**。
#   原因：详情区两行各 3 个按钮改为"统一 96px、上下对齐"后，第二行总需求 = 547px，
#   而详情区 551 时实际可用行宽仅 527px（详情区 − 行/卡内边距 24）→ 最窄时「重置」被压到 43px。
#   标定：详情区 = 源码 × 1.2 − 3 − 1088 ⇒ 571 ⇒ 源码 ≈ (571 + 1088 + 3) / 1.2 = **1385**。
# 2026-09-14 21:05（用户选方案 A）：详情区右侧 4 控件对齐后右块由 113/116 → **142px**
#   ⇒ 第一行内容需求 552px ⇒ 详情区最小宽 **571 → 578** ⇒ 源码 = (578 + 1088 + 3) / 1.2 = **1391**。
# 2026-09-14 21:05（实测复核）：578 时仍差 1~2px（实测 6 按钮 94/96、重置 70/72 被挤）；
#   为"最窄状态下按钮也严格同宽/不裁字"，目标再留余量到 **584** ⇒ 源码 = (584 + 1088) / 1.19797 ≈ **1396**。
# 2026-09-22（用户批准两列加宽 112→140）：`_BASE_MIN_WIDTH` **1396 → 1463**
#   （＝标定表首项同步上调 67，理由见 _NAV_MIN_WIDTH_BY_HIDDEN 上方说明）。
_BASE_MIN_WIDTH = 1463
_BASE_MIN_HEIGHT = 660
# 2026-09-14（用户最终决定，本轮 8）：**窗口最小宽度以"工具栏需要"为准**——
#   两组工具按钮固定在第一行（不再下移），放不下一行时**只把搜索框移到第二行靠右**；
#   故窗口可缩到的下限 = "第一行（标题 + 两组按钮）的需求宽度"（搜索框已移走，不再计入）。
#   实测：第一行需求 ≈1040 实际 + 工具栏左右边距 20 = 1060 ⇒ 源码 = (1060 + 3) / 1.2 ≈ **886**。
_MIN_WIDTH_FLOOR = 886
# 2026-09-14（用户要求）：**按"隐藏分类列数"分别标定窗口最小宽度**（详情区目标 550 与工具栏需求取大者）——
#   依据（实测，本机 120% 缩放）：详情区 = 窗口实际 − 开销，各状态开销 = 1088/928/768/541/314；
#   目标详情区 550 ⇒ 窗口实际 = 550 + 开销 ⇒ 源码 = (窗口 + 3) / 1.2：
#     隐藏0列 → 1638 → 1368 ｜ 隐藏1列 → 1478 → 1234 ｜ 隐藏2列 → 1318 → 1101
#     隐藏3列 → 1091 →  912 ｜ 隐藏4列 → 864 → **886**（受工具栏 1060 限制，实际详情区 ≈746）
# 2026-09-14 20:40（用户决定 B，本轮）：详情区目标 **550 → 571** ⇒ 各状态按同一口径上调
#   （源码 = (571 + 开销 + 3) / 1.2，向上取整）：
#     隐藏0列 → (571+1088+3)/1.2 = 1385 ｜ 隐藏1列 → (571+928+3)/1.2 ≈ 1252
#     隐藏2列 → (571+768+3)/1.2 ≈ 1119 ｜ 隐藏3列 → (571+541+3)/1.2 ≈ 930
#     隐藏4列 → 723 → 仍受工具栏下限 1060 限制 ⇒ **886 不变**（该状态实际详情区 ≈746 已足够）
# 2026-09-14 21:05（用户选方案 A）：目标详情区 571 → **578** ⇒ 各状态同口径上调（向上取整）：
#     隐藏0 → (578+1088+3)/1.2 ≈ 1391 ｜ 隐藏1 → (578+928+3)/1.2 ≈ 1258
#     隐藏2 → (578+768+3)/1.2 ≈ 1125 ｜ 隐藏3 → (578+541+3)/1.2 ≈ 935
# 2026-09-14 21:05（实测复核）：目标再留余量到 **584**（实测 578 时仍差 1~2px）：
#     隐藏0 → 1396 ｜ 隐藏1 → 1263 ｜ 隐藏2 → 1129 ｜ 隐藏3 → 940（隐藏4 仍 886 不变）
# 2026-09-22（用户批准加宽两列后的同步标定）：「项目类别 / 根目录」各 +28 源码px ⇒
#   标定表按"该列仍可见就加上其增量"上调（每列 +34，两列共 +67）：
#     隐藏0列 → 1396+67 = **1463** ｜ 隐藏1列 → 1263+34 = **1297**
#     隐藏2/3/4 列 → 1129 / 940 / 886 **不变**（一二级未改）
_NAV_MIN_WIDTH_BY_HIDDEN = (1463, 1297, 1129, 940, 886)
# 2026-09-14（用户要求，本轮）：工具栏"折成两行"的判定余量（像素，实际值）——
#   窗口变窄到"放不下一行工具栏"时折行；恢复时需比阈值再宽这么多才展开，避免临界宽度反复折/展。
_BAR_UNFOLD_MARGIN = 60
# 2026-09-15（审核 L-7/L-8）：两处新增常量——
#   ① `_SCREEN_BOTTOM_MARGIN`：屏幕夹取时下边保留的像素（`_fit_window_to_screen` 原先直接用字面量 40，
#      此处抽为常量；**数值不变**，`_do_center` 的 20px 暂按原样保留，待你决定是否统一）。
#   ② `_BAR_ROW2_MIN_LEFT`：折行时"第二行首列留白"的下限兜底（源码值；正常情况用实测/缓存值）。
_SCREEN_BOTTOM_MARGIN = 40
_BAR_ROW2_MIN_LEFT = 100
# 2026-09-15（用户要求 8）：标签页搜索框的"防抖"延迟（毫秒）——
#   标签页每输入一个字符都会**全量重算 + 重建标签云/列表**（622 个标签时很慢），故加延迟；
#   刻意**不复用**主搜索框的 1500ms（标签页搜索更"轻"，停顿 0.4s 即刷新）。
_TAG_SEARCH_DEBOUNCE_MS = 400
# 2026-09-15（用户要求 6+7）：「每页」下拉的选项文案（值 0＝不限＝单页显示全部）
_TAG_LIMIT_LABELS = ("50", "100", "200", "500", "不限")


def _geom_warn(where: str, exc: object) -> None:
    """几何/缩放相关"静默异常"的统一出口（2026-09-15 审核 L-7）。

    背景：窗口几何/缩放链路原先有 5 处 `except Exception: pass`，一旦真出错（例如窗口不收缩、
    不做屏幕夹取）用户只看到"窗口位置/大小不对"，而**没有任何日志可查**；打包为无控制台 EXE
    时更无从排查。本函数只**打印一行**（不改任何控制流、不弹窗），供诊断使用。
    """
    try:
        from datetime import datetime as _dt
        print(f"[几何] {_dt.now().strftime('%H:%M:%S')} {where}：{exc!r}")
    except Exception:
        pass
# 2026-09-14（用户要求）：工具栏**第二行容器**的 grid 配置（创建与恢复都用它，避免不一致）——
#   ⚠ 恢复显示时必须带完整参数；写成无参 `row2.grid()` 会把 columnspan/sticky 重置为默认，
#     导致该容器只占第 0 列并把它撑宽 → 第一行按钮整体右移（实测踩坑）。
_BAR_ROW2_GRID = dict(row=1, column=0, columnspan=13, sticky="ew",
                     padx=(2, 6), pady=(2, 0))
# 2026-09-14（用户要求）：**"所有状态下详情区 ≥ 550"** 的补充约束（随目标值 480→550 同步上调）——
#   标签页面打开时左侧被"标签页面"占用（`_body` 第 0 列 minsize = 四列合计 560），
#   若沿用"按隐藏列下调"的最小宽度，实测详情区会被压到 377px（远低于 550）。
#   该状态实测"窗口 − 详情区"的固定开销 = **945**；按"详情区 ≥ 550"标定：
#   源码 1250 → 窗口 ≈1497 → 详情区 ≈552 ✅（原 1190 对应详情区 480）。
#   此项只在标签页面打开时生效（属下限加强，不违反 _MIN_WIDTH_FLOOR=1104）。
# 2026-09-14 20:40（用户决定 B，本轮）：目标详情区 **550 → 571** ⇒ 源码 = (571 + 945 + 3) / 1.2 ≈ **1266**。
# 2026-09-14 21:05（用户选方案 A）：目标 **571 → 578** ⇒ 源码 = (578 + 945 + 3) / 1.2 ≈ **1272**。
# 2026-09-14 21:05（实测复核）：目标再留余量到 **584** ⇒ 源码 = (584 + 945) / 1.19797 ≈ **1277**。
_TAG_PAGE_MIN_WIDTH = 1277
# 2026-09-14 20:35（用户要求，本轮）：详情区**两行各 3 个按钮统一宽度**——
#   目的：让「➜ 移动到 / ↔ 关联到 / ⧉ 复制到」（第一行左起 2~4 个）与
#   「📋 复制全部 / 复制中文 / 复制英文」（第二行左起第 1~3 个）这 6 个按钮
#   **大小一致、上下对齐**（用户："大小一样大、上下对齐"）。
#   取值：6 者中"文字+内边距"需求最大者 = "📋 复制全部" 实际 96px ⇒ 源码 80（本机 120% 缩放，
#   80 × 1.2 = 96）；取其最大值可保证任何按钮的文字都不被裁。
#   ⚠ 这两行原来的宽度各自"刚好显示文字"（82/88/90 与 96/78/78），故宽度不一、列不对齐。
_DETAIL6_BTN_W = 80
# 2026-09-14 21:05（用户选方案 A）：详情区**右侧 4 个控件的两列统一宽度**——
#   第一行「✏️ 编辑 / 🔧」与第二行「重置 / 💾 保存」**逐列对齐、两块总宽一致**：
#     · A 列（左）＝ 编辑 / 重置：编辑文字需求 72px（不可再窄）⇒ 源码 **60**（60×1.2 = 72）；
#       故"重置"由源码 36（46px）加宽为 **60**（72px）——用户已知悉并同意（方案 A）。
#     · B 列（右）＝ 🔧 / 保存：保存文字需求 66px（不可再窄）⇒ 源码 **55**（55×1.2 = 66）；
#       "🔧"（字段管理）随之由 37px 加宽为 **66px**（图标居中，仅内边距变化，文字不变）。
#   两行右块总宽 = 72 + 4 + 66 = **142px**（原 113 / 116）⇒ 详情区最小宽 571 → 578（见下方常量）。
_DETAIL_RCOL_A_W = 60
_DETAIL_RCOL_B_W = 55

# 2026-09-10（用户要求）：搜索框输入防抖——停止输入后再等这么久才真正查询。
# 作用：避免"输入第一个字符就开始全字段检索 + 条目列表重建"造成的持续刷新与卡顿；
# 回车 或 点击右侧"🔍"图标 仍可立即查询。
_SEARCH_DEBOUNCE_MS = 1500

# 2026-09-14（用户要求"无标签条目"入口 方案 C）：搜索框 `#无标签` 语法——
#   `#无标签`（或预留英文别名 `#none`）＝过滤出"没有任何标签"的条目，可与关键词组合
#   （如 `#无标签 电影`）；这两个词为**保留词**，不会被当作真实标签名。
_UNTAGGED_ALIASES = ("无标签", "none")

# 2026-09-07（第5条改进）：详情区 ②~⑩ 字段配色。
# 每个字段独立成"浅色圆角卡片块"：标签用各自主题色文字、块底淡彩、内容框白底同色细边，
# 与① 条目名称的深色卡呼应成统一层次，字段与字段之间有颜色区分、观感更舒服。
_FIELD_STYLE = {
    # key: (标签文字色, 字段块底色, 输入框底色, 输入框描边色)
    "intro":      ("#8a5a00", "#fbf6e9", "#ffffff", "#e8dcc0"),
    "origin":     ("#9c4a1f", "#fcf2e8", "#ffffff", "#ead2ba"),
    "features":   ("#5b46a0", "#f4f0fb", "#ffffff", "#dcd3ee"),
    "scenes":     ("#0f7588", "#eaf5f8", "#ffffff", "#cde3ea"),
    "works":      ("#ad4a63", "#fbf0f3", "#ffffff", "#e9cdd5"),
    "image_desc": ("#a03d3d", "#fbeeee", "#ffffff", "#e7cdcd"),
    "prompt_cn":  ("#1f7a50", "#edf6f0", "#ffffff", "#d0e3d8"),
    "prompt_en":  ("#2565b0", "#ecf3fb", "#ffffff", "#ccdcea"),
    "image_plan": ("#7d46a0", "#f6eefa", "#ffffff", "#e2d0ea"),
}
_FIELD_DEFAULT_STYLE = ("#1f4e79", "#f2f5f9", "#ffffff", "#d7e0ea")


def _field_style(key: str):
    """取某字段的配色；(标签色, 块底色, 输入框底色, 描边色)，未配置字段用统一默认"""
    return _FIELD_STYLE.get(key, _FIELD_DEFAULT_STYLE)


# 2026-09-13（1-C-2）：标签 chip 的柔和配色（按标签名哈希分配，界面小样已确认不提供用户自定）
# 2026-09-14（阶段 3）：配色与算法已抽到 ui_common（供 quick_add.py 的标签区共用），此处仅保留调用名
_TAG_SOFT_COLORS = _ui_common.TAG_SOFT_COLORS


def _tag_color(name: str) -> str:
    """按标签名哈希取一个柔和底色（同名永远同色）"""
    return _ui_common.tag_color(name)


# 2026-09-14：标签云的宽度估算常量（实测校准）
#   实测发现：CTkButton **默认 width=140**，故不传 width 时短标签的控件宽度恒为 **168px**
#   （"长曝光 (6)" 文字仅 86px 也占 168px）→ 原先按"文字宽+20"估算会**严重低估**，
#   于是判定"一行还能再放一个"，实际放不下 → 最后一个被父容器**裁剪**
#   （用户反馈："很多是显示了三个长标签，然后右侧有一个小回形标签"）。
#   修复：① 按钮显式 `width=1` → CTk 按文字自适应（实测 100→104、86→94）；
#        ② 估算用"文字宽 + 32"（实测最坏情况：控件比文字宽 24px【如 "YY" 22→46】，
#           再加 padx 4×2 = 8，故取 32 才**保证估算 ≥ 实际**，宁可每行少放也绝不裁剪）；
#        ③ 可用宽度再扣 40px（滚动条 + wrap 内边距 + 余量）。
_CLOUD_BTN_PAD = 32            # 每个标签按钮的额外占位（文字宽之外；实测校准值）
_CLOUD_RESERVED = 40           # 行容器可用宽度需扣减的固定值

# 2026-09-14：文本像素宽度测量（供"标签云自动换行"用；按 (字号,粗体) 缓存 Font 对象）
_FONT_MEASURE_CACHE = {}


def _text_px(text: str, size: int, bold: bool = False) -> int:
    """测量文本像素宽度；测量失败时按"字符数 × 字号"兜底估算。

    背景：标签云原先所有按钮 `pack(side="left")` 平铺，而 tkinter 的 pack **不会自动换行**
    → 标签一多就横向溢出被裁剪（用户实测"只看到 3 个标签"）。改为按实测宽度分行。
    """
    try:
        key = (int(size), bool(bold))
    except (TypeError, ValueError):
        key = (12, False)
    f = _FONT_MEASURE_CACHE.get(key)
    if f is None and key not in _FONT_MEASURE_CACHE:
        try:
            f = tkfont.Font(font=("Microsoft YaHei", key[0],
                                  "bold" if key[1] else "normal"))
        except Exception:
            f = None
        _FONT_MEASURE_CACHE[key] = f
    if f is None:
        return len(str(text)) * key[0]
    try:
        return int(f.measure(str(text)))
    except Exception:
        return len(str(text)) * key[0]


# 2026-09-14（性能优化，用户确认）：条目区与标签页的**渲染上限**
#   实测：每条例目渲染约 54ms、每个标签列表行约 68ms、标签云每项约 27ms；瓶颈在"逐个创建
#   UI 控件"，纯 SQL 查询仅 3ms。故加上限 + "显示更多"，把首屏时间降到秒级。
#   2026-09-14 13:40（用户要求进一步提高速度）：
#     条目区默认 200 → **50**（约 3 秒，"显示更多"步长同步 200 → 50，节奏一致）；
#     标签页默认 100 → **50**（实测前 50 个标签已覆盖 89.7% 的标签使用量，前 100 仅 91.9%）。
#     另：集合超过 ENTRY_ALL_CONFIRM_THRESHOLD 时，「全部显示」先二次确认（避免误点后长时间卡死）。
ENTRY_RENDER_LIMIT = 50        # 条目区首屏渲染条数（0 = 全部）
ENTRY_RENDER_STEP = 50         # 每次"显示更多"追加条数
TAG_PAGE_LIMIT = 50            # 标签页（云/列表）首屏显示个数（0 = 全部）
ENTRY_ALL_CONFIRM_THRESHOLD = 500   # 「全部显示」超过该条数时先二次确认

_COPY_ALL = 0
_COPY_CN = 1
_COPY_EN = 2

_HOVER_SELECT_MS = 200  # 悬停选中延迟（毫秒）：导航与条目区采用"鼠标悬浮即选择"
# 2026-09-07（第2条改进）：300ms→200ms，逐级悬浮选择的响应更跟手；
# 配合"列选中改用原地高亮、不再整列重建"，切换列时明显更流畅。

# 2026-08-18：提示词字段可折叠。注意：CTkTextbox 的 height 单位是【像素】而非行数！
# 实测（默认字体）行高约 20px：
#   - 有内容默认 _COLLAPSED_H=120px（6 行文字完整可见，不被遮盖）
#   - 点击"展开"→ 高度自适应为内容实际显示行数（有多少行就显示多少行，避免空白行）
#   - 无内容时只显示 1 行空行（_EMPTY_H=24px），且不显示"展开"按钮
_COLLAPSIBLE_KEYS = {"prompt_cn", "prompt_en"}
_EMPTY_H = 24          # 无内容：1 行空行（≈20px 行高 + 少量余量）
_COLLAPSED_H = 120     # 有内容默认：6 行完整可见（实测 120px 时约 6.5 行可见）

# 2026-09-06：主界面就地"新增条目"——③-⑦ 补充信息（溯源/核心特征/应用场景/代表作/代表高清配图）
# 在新增表单中默认折叠为一组，需要时点"展开"逐条填写（不依赖根目录显隐策略）。
_ADD_FOLD_KEYS = set(config.DETAIL_HIDDEN_KEYS)

# 2026-09-09：详情区 ②~⑦（介绍/溯源/核心特征/应用场景/代表作/代表高清配图）默认折叠组。
# 浏览已存条目与"＋新增条目"两处统一：默认仅显示 1 行标题，点"展开"才显示全部字段，
# 折叠后不再各字段各自占行（此前折叠态 ③-⑦ 仍占较大竖向空间）。
_INFO_GROUP_KEYS = ("intro", "origin", "features", "scenes", "works", "image_desc")

# 2026-09-11 08:52（用户要求 1）：条目列悬停"条目名称一览"浮层在原宽度上增加的像素值。
# 目的：条目列宽窄导致名称显示不全，需借浮层看全名，故加宽约 150px（≈10-11 个汉字）；
# 定位时浮层右边与条目列的相对位置保持不变，增加的部分全部向左扩展（见 _show_entry_overview）。
_ENTRY_OV_EXTRA_W = 150

# 2026-09-11 09:27（用户要求 3）：浮层"条目名称一览"配色——默认配色 +"光标所在条目"深色高亮
_ENTRY_OV_BG = "#ffffff"       # 默认底色
_ENTRY_OV_FG = "#111111"       # 默认文字色
_ENTRY_OV_HL_BG = "#25639c"    # 高亮底色（与浮层标题栏同色）
_ENTRY_OV_HL_FG = "#ffffff"    # 高亮文字色
# 2026-09-12（用户要求 1）：条目区"条目名称"字体——悬停浮层"条目名称一览"内文字字号与之保持一致
_ENTRY_NAME_FONT_CARD = ("Microsoft YaHei", 13, "bold")   # 卡片视图（默认）条目名称
_ENTRY_NAME_FONT_LIST = ("Microsoft YaHei", 12, "bold")   # 列表视图条目名称
_ENTRY_OV_MIN_ROWS = 25        # 2026-09-12（用户要求）：悬停浮层固定最小高度＝可显示 25 行文本
_ENTRY_OV_MAX_ROWS = 25        # 上限与最小一致 → 浮层恒为 25 行高（条目更多时用右侧滚动条）
# 2026-09-12（用户要求 3）：详情区左下"🗂 打开/关闭四级目录"按钮两态配色
_DIR_OPEN_BG = _C_OK       # 目录打开（四列显示）→ 绿色
_DIR_OPEN_HOVER = "#256e46"
_DIR_CLOSED_BG = "#E0A800"     # 目录关闭（四列隐藏）→ 黄色
_DIR_CLOSED_HOVER = "#B98A00"
# 2026-09-13（1-C-3）：标签页面两态配色（打开＝紫、关闭＝橙）
_TAG_OPEN_BG = _C_TAG
_TAG_OPEN_HOVER = "#66409f"
_TAG_CLOSED_BG = _C_WARN
_TAG_CLOSED_HOVER = "#b87000"

# 2026-09-12（用户要求 2）：详情区"字段内容"浮动提示窗口。
# 定位：整体在详情区【左侧】、右边紧贴详情区左边界并向左展开；顶部与被悬停字段顶部对齐。
# 宽度：取详情区左侧可用空间，上限 _TIP_MAX_W —— 因此点"🗂 目录隐藏"隐藏四级目录、
#       详情区变宽后，浮窗会自动压窄（左侧只剩条目列），始终不遮盖详情区正文。
_TIP_MAX_W = 610         # 浮窗最大宽度（详情区左侧空间充足时使用；2026-09-12 追加要求：由 560 再 +50）
_TIP_MIN_LINES = 18      # 浮窗内容区固定最小显示行数（2026-09-12 追加要求：保证最少可见 18 行）
_TIP_MAX_LINES = 30      # 浮窗内容区最多显示行数（超出用右侧滚动条；文本多时由本上限封顶）
_TIP_MARGIN = 8          # 浮窗与屏幕/左侧边界的安全间距
_TIP_GAP = 4             # 浮窗右边与详情区左边界之间的间隙
_TIP_SHOW_MS = 350       # 悬停多久后弹出
_TIP_HIDE_MS = 250       # 离开字段后延时多久判断是否关闭（给"移向浮窗"留出行程）
_TIP_POLL_MS = 150       # 光标仍在浮窗/阅读区内时的轮询间隔
_TIP_HL_BG = "#cfe2f5"   # 光标所在行加深底色
_TIP_HL_FG = "#0b3b66"   # 光标所在行加深文字色
_TIP_FONT = ("Microsoft YaHei", 14, "normal")   # 浮窗文字字号：固定 14pt（2026-09-12 用户要求）


class _FieldTooltip:
    """字段悬停提示：鼠标移到字段上显示完整内容（不受滚动框裁剪影响）

    2026-09-17（审核 R-3）：实现已统一到 `ui_common.Tooltip`（屏幕工作区夹紧、避免落进任务栏），
    此处保留类名与既有调用点不变，仅继承公共实现。
    """

    def __init__(self, widget, text: str):
        self._impl = _ui_common.Tooltip(widget, text, wraplength=460)

    def _show(self, _event=None):
        self._impl._show()

    def _hide(self, _event=None):
        self._impl._hide()


def _attach_tooltip(widget, text: str) -> "_FieldTooltip":
    """在按钮"全部可命中区域"挂接悬停提示（2026-09-13 1-A-4 增强，仅用于 🔧 字段管理按钮）。

    背景：CTkButton.bind 已代理到内部 canvas / 文字标签 / 图标标签（覆盖按钮可视区），
    但**按钮本体（Frame）未被绑定**；若鼠标落在未被子控件覆盖的内边距区域，
    <Enter> 落在按钮本体上而无绑定，提示不会出现。此处补绑按钮本体，闭合该缺口。
    不改动 _FieldTooltip 既有实现，也不影响其它按钮的提示行为。
    """
    tip = _FieldTooltip(widget, text)
    try:
        tk.Frame.bind(widget, "<Enter>", tip._show, add="+")
        tk.Frame.bind(widget, "<Leave>", tip._hide, add="+")
    except Exception:
        pass
    return tip


_PICK_SEARCH_MIN = 40     # 选项数超过此值即切换为"搜索过滤 + Treeview 渲染"大列表模式（3-c）
_PICK_FILTER_MS = 250     # 过滤输入防抖（避免每敲一键就整体重建）


class _ListPickDialog(ctk.CTkToplevel):
    """列表框字段的"选择值"对话框（2026-09-13，第 3 期 3-a-2 / 3-c / 3-c+）。

    - 单选 / 多选由字段的数据源配置决定；
    - `allow_new=True` 时可输入并「＋ 加入」一个新项（立即出现在候选列表并选中）；
    - **小列表（≤ `_PICK_SEARCH_MIN` 项）**：沿用 CTk 复选框（行为与 3-a 完全一致）；
    - **大列表（> `_PICK_SEARCH_MIN` 项，如"条目型"数千条）**：改用 `ttk.Treeview` 渲染
      （实测 2500 行 ≈42 ms，远快于逐个 CTk 复选框），并提供**搜索过滤**、
      「全选匹配 / 清空选择」；已选项始终保留显示（不受过滤影响）；
    - 结果写在 `self.result`（值列表）；点「确定」时 `confirmed=True`。
    """

    def __init__(self, master, title: str, options: list, selected: list,
                 multi: bool = False, allow_new: bool = True) -> None:
        super().__init__(master)
        self.result = []
        self.confirmed = False
        self._multi = bool(multi)
        self._allow_new = bool(allow_new)
        self._options = [dict(o) for o in options]
        self._checked = [o["value"] for o in options if o["value"] in (selected or [])]
        self._single_idx = -1
        for i, o in enumerate(self._options):
            if o["value"] in (selected or []):
                self._single_idx = i
                break
        self._filtered = len(self._options) > _PICK_SEARCH_MIN   # 大列表模式（Treeview）
        self._rendered_values = []

        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(self, text=title, font=("Microsoft YaHei", 14, "bold")
                     ).pack(padx=18, pady=(14, 2), anchor="w")
        ctk.CTkLabel(self, text=("可多选" if self._multi else "单选")
                     + ("　·　可直接新建项" if self._allow_new else ""),
                     text_color="gray", font=("Microsoft YaHei", 11)
                     ).pack(padx=18, pady=(0, 6), anchor="w")

        if self._filtered:      # 大列表：搜索过滤 + 计数 + Treeview
            srow = ctk.CTkFrame(self, fg_color="transparent")
            srow.pack(padx=18, pady=(0, 4), fill="x")
            self.search_entry = ctk.CTkEntry(srow, placeholder_text="输入关键词过滤（可选）…")
            self.search_entry.pack(side="left", fill="x", expand=True)
            self.search_entry.bind("<KeyRelease>", lambda _e=None: self._on_filter())
            info = ctk.CTkFrame(self, fg_color="transparent")
            info.pack(padx=18, pady=(0, 4), fill="x")
            self.count_lbl = ctk.CTkLabel(info, text="", text_color="gray",
                                          font=("Microsoft YaHei", 10))
            self.count_lbl.pack(side="left")
            self.sel_lbl = ctk.CTkLabel(info, text="已选 0 项", text_color="#2f6fb0",
                                        font=("Microsoft YaHei", 10))
            self.sel_lbl.pack(side="right")
            wrap = ctk.CTkFrame(self, fg_color="transparent")
            wrap.pack(padx=18, pady=(0, 6), fill="both", expand=True)
            self.tree = ttk.Treeview(wrap, show="tree", height=12,
                                     selectmode=("extended" if self._multi else "browse"))
            self.tree.column("#0", width=440, minwidth=220, stretch=True)
            self.tree.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
            sb.pack(side="right", fill="y")
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.bind("<<TreeviewSelect>>", lambda _e=None: self._on_tree_select())
            if self._multi:
                brow = ctk.CTkFrame(self, fg_color="transparent")
                brow.pack(padx=18, pady=(0, 4), fill="x")
                ctk.CTkButton(brow, text="全选匹配", width=84, height=26,
                              command=self._select_all_visible).pack(side="left")
                ctk.CTkButton(brow, text="清空选择", width=84, height=26, fg_color="#8a94a6",
                              command=self._clear_selection).pack(side="left", padx=(6, 0))
                ctk.CTkLabel(brow, text="（Ctrl / Shift 可多选）", text_color="gray",
                             font=("Microsoft YaHei", 10)).pack(side="left", padx=(8, 0))
        else:
            self._body = ctk.CTkScrollableFrame(self, width=340, height=230)
            self._body.pack(padx=18, pady=(0, 6), fill="both", expand=True)

        self._render_options()

        if self._allow_new:
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(padx=18, pady=(0, 4), fill="x")
            self.new_entry = ctk.CTkEntry(row, placeholder_text="新建项…")
            self.new_entry.pack(side="left", fill="x", expand=True)
            self.new_entry.bind("<Return>", lambda _e=None: self._add_new())
            ctk.CTkButton(row, text="＋ 加入", width=76, command=self._add_new
                          ).pack(side="left", padx=(6, 0))

        btn = ctk.CTkFrame(self, fg_color="transparent")
        btn.pack(padx=18, pady=(4, 14), anchor="e")
        ctk.CTkButton(btn, text="确定", width=92, fg_color=_C_OK,
                      command=self._ok).pack(side="left", padx=4)
        ctk.CTkButton(btn, text="取消", width=92, command=self.destroy).pack(side="left", padx=4)
        self._center()

    # ---- 搜索过滤（3-c）---- #
    def _on_filter(self) -> None:
        """输入关键词：收下当前勾选状态并**防抖**，稍后按新关键词重建可见列表"""
        self._collect_current()
        job = getattr(self, "_filter_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._filter_job = self.after(_PICK_FILTER_MS, self._apply_filter)

    def _apply_filter(self) -> None:
        self._filter_job = None
        self._render_options()

    def _filter_kw(self) -> str:
        if self._filtered and getattr(self, "search_entry", None) is not None:
            return self.search_entry.get().strip()
        return ""

    def _visible_options(self) -> list:
        """返回本次要渲染的 [(在 _options 中的下标, 选项)]（过滤 + 已选项保底 + 计数）"""
        kw = self._filter_kw()
        matched = [(i, o) for i, o in enumerate(self._options)
                   if (not kw or kw in (o.get("label") or o["value"]) or kw in o["value"])]
        shown = list(matched)
        keep = self._selected_values()
        shown_vals = {o["value"] for _i, o in shown}
        for i, o in enumerate(self._options):     # 已选项始终保留显示（不受过滤影响）
            if o["value"] in keep and o["value"] not in shown_vals:
                shown.append((i, o))
        if self._filtered and getattr(self, "count_lbl", None) is not None:
            self.count_lbl.configure(text=f"共 {len(self._options)} 项，匹配 {len(matched)} 项")
        return shown

    def _selected_values(self) -> set:
        if self._multi:
            return set(self._checked)
        if 0 <= self._single_idx < len(self._options):
            return {self._options[self._single_idx]["value"]}
        return set()

    def _render_options(self) -> None:
        vis = self._visible_options()
        if self._filtered:                       # 大列表：Treeview 渲染（重建成本极低，无需跳过）
            self._render_tree(vis)
            self._refresh_sel_count()
            return
        # 小列表：CTk 复选框（仅在打开对话框 / 「＋ 加入」时重建）
        for w in self._body.winfo_children():
            w.destroy()
        self._vars = {}
        if not vis:
            ctk.CTkLabel(self._body, text="（没有匹配的候选值）", text_color="#9aa4b1",
                         font=("Microsoft YaHei", 11)).pack(anchor="w", padx=4, pady=4)
            return
        for idx, o in vis:
            val = o.get("value", "")
            lbl = o.get("label") or val
            if self._multi:
                var = ctk.StringVar(value="1" if val in self._checked else "0")
                ctk.CTkCheckBox(self._body, text=lbl, variable=var, onvalue="1", offvalue="0",
                                font=("Microsoft YaHei", 12)
                                ).pack(anchor="w", padx=4, pady=2)
                self._vars[val] = var
            else:
                ctk.CTkRadioButton(self._body, text=lbl, value=idx,
                                   variable=self._radio_var(), font=("Microsoft YaHei", 12)
                                   ).pack(anchor="w", padx=4, pady=2)
        if not self._multi:
            self._radio_var().set(self._single_idx)   # 单选：同步回"当前选中项"

    # ---- 大列表：Treeview 渲染（3-c+）--------------------------------- #
    def _render_tree(self, vis: list) -> None:
        """用 ttk.Treeview 渲染候选（一行为一项，行 id 即取值令牌）"""
        kids = self.tree.get_children()
        if kids:
            self.tree.delete(*kids)
        self._rendered_values = [o["value"] for _i, o in vis]
        for _i, o in vis:
            v = o["value"]
            self.tree.insert("", "end", iid=v, text=(o.get("label") or v))
        keep = self._selected_values()
        for v in self._rendered_values:
            if v in keep:
                self.tree.selection_add(v)
        if not self._multi and self._single_idx >= 0:
            v = self._options[self._single_idx]["value"]
            if self.tree.exists(v):
                self.tree.selection_set(v)
                self.tree.see(v)

    def _on_tree_select(self) -> None:
        """Treeview 选中变化 → 收进内部状态并刷新"已选 N 项"（单选/多选通用）"""
        self._collect_current()
        self._refresh_sel_count()

    def _refresh_sel_count(self) -> None:
        lbl = getattr(self, "sel_lbl", None)
        if lbl is None or not lbl.winfo_exists():
            return
        n = len(self._checked) if self._multi else len(self._selected_values())
        lbl.configure(text=f"已选 {n} 项")

    def _select_all_visible(self) -> None:
        """多选：把当前可见（匹配）的全部行加入选择（不动被过滤掉但已选的项）"""
        if self._rendered_values:
            self.tree.selection_add(*self._rendered_values)
        self._collect_current()
        self._refresh_sel_count()

    def _clear_selection(self) -> None:
        """清空全部选择（含被过滤掉但已选的项）"""
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_remove(*kids)
        self._checked = []
        self._single_idx = -1
        self._refresh_sel_count()

    def _collect_current(self) -> None:
        """把界面上**当前可见**项的勾选/选中状态收进内部状态（不可见项保持原状态）"""
        if self._filtered:                        # 大列表（Treeview）
            if not hasattr(self, "tree") or not self.tree.winfo_exists():
                return
            sel = set(self.tree.selection())
            if self._multi:
                cur = list(self._checked)
                for v in list(self._rendered_values):
                    if v in sel and v not in cur:
                        cur.append(v)
                    elif v not in sel and v in cur:
                        cur.remove(v)
                self._checked = cur
            else:
                vals = list(sel)
                self._single_idx = next(
                    (i for i, o in enumerate(self._options)
                     if vals and o["value"] == vals[0]), -1)
            return
        if self._multi:
            vars_ = getattr(self, "_vars", None) or {}
            sel = list(self._checked)
            for o in self._options:
                var = vars_.get(o["value"])
                if var is None:
                    continue
                v = o["value"]
                if var.get() == "1" and v not in sel:
                    sel.append(v)
                elif var.get() != "1" and v in sel:
                    sel.remove(v)
            self._checked = sel
        else:
            self._single_idx = self._radio_var().get()

    def _radio_var(self):
        if not hasattr(self, "_rv"):
            self._rv = ctk.IntVar(value=self._single_idx)
        return self._rv

    def _add_new(self) -> None:
        txt = self.new_entry.get().strip()
        if not txt:
            return
        self._collect_current()   # 先收下当前勾选/选中状态（重建会清空控件）
        vals = [o["value"] for o in self._options]
        if txt not in vals:
            self._options.append({"value": txt, "label": txt})
            if self._multi:
                self._checked.append(txt)
            else:
                self._single_idx = len(self._options) - 1
        else:
            if self._multi:
                if txt not in self._checked:
                    self._checked.append(txt)
            else:
                self._single_idx = vals.index(txt)
        self.new_entry.delete(0, "end")
        # 新建项若被"搜索过滤"挡住会看不到 → 先清空过滤词（3-c）
        if self._filtered and getattr(self, "search_entry", None) is not None:
            if self.search_entry.get():
                self.search_entry.delete(0, "end")
        self._render_options()
        if self._filtered and self.tree.exists(txt):     # 大列表：滚动到新建项
            self.tree.see(txt)
        self._refresh_sel_count()

    def _ok(self) -> None:
        self._collect_current()   # 3-c：以内部状态为准（不可见项也在其中）
        if self._multi:
            self.result = list(self._checked)
        else:
            i = self._single_idx
            self.result = [self._options[i]["value"]] if 0 <= i < len(self._options) else []
        self.confirmed = True
        self.destroy()

    def _center(self) -> None:
        self.update_idletasks()
        try:
            x = self.master.winfo_x() + (self.master.winfo_width() - self.winfo_width()) // 2
            y = self.master.winfo_y() + (self.master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass
        self.lift()


class MainWindow(ctk.CTk):
    def __init__(self, db, startup_warning: str = ""):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.db = db
        self.startup_warning = startup_warning

        self.title("PromptSprite（提示精灵）")
        self.geometry("1480x780")  # 2026-08-29（M2）：四列导航，默认宽度 1360→1480
        # 2026-09-10（用户要求 3）：最小宽度改为随"目录隐藏"动态计算（见 apply_nav_visibility）
        # 2026-09-14（用户要求）：最小高度同样按屏幕可用高度收敛（`_win_min_height()`），
        #   保证"窗口缩到最小时整窗（含底边）可见"。
        self.minsize(_BASE_MIN_WIDTH, self._win_min_height())
        # 2026-09-17（需求 U-1）：设置**运行期窗口 / 任务栏图标**——原先未设置，
        #   窗口左上角与任务栏显示的是 Tk 默认图标（EXE 图标本身是正常的）。
        self._set_window_icon()

        # 交互状态
        self._lock_on = False
        # 2026-09-15（批次 6-2）：标签页**写操作按钮**引用（锁定态即时置灰 / 解锁后恢复用）
        self._tag_gov_btns = []
        self._cur_domain_id = None
        self._cur_cat_id = None
        # 2026-09-15（批次 8-A）：最近一次有分类的视图（推荐上下文兜底用，见 `_suggest_ctx_names`）
        self._last_cat_id = None
        self._view = None          # (kind, ref)：kind ∈ domain/cat/uncat/fav/search
        # 2026-09-14（审核修复 P3）：进入搜索前的浏览视图——清空搜索框时回到它
        self._view_before_search = None
        self._view_mode = "card"   # 卡片/列表
        # 2026-09-16（批次 11-7，用户要求 3）：条目区排序方式（"updated"/"created"/"name"）
        self._entry_sort = "updated"
        self._detail_mode = config.DETAIL_MODE_AUTO  # 2026-08-18：详情字段策略（自动/全部/精简）
        self._remember_size = True                   # 2026-08-18：是否记住窗口大小
        self._detail_entry_id = None
        self._detail_boxes = {}
        # 2026-09-14（审核补充 P6）：自定义字段"专用控件"取值回调与需置灰的按钮
        #   _extra_field_getters: {field_key: () -> (value_text, value_json)}
        self._extra_field_getters = {}
        self._extra_field_widgets = []
        self._extra_field_widgets_by_key = {}   # {field_key: 取值控件}（置灰/回读/自测用）
        # 2026-09-14（P6 补充）："图像"类型自定义字段的内嵌缩略预览
        self._typed_img_holders = {}            # {field_key: 预览容器 CTkFrame}
        self._typed_img_refs = {}               # {field_key: CTkImage}（保持引用，避免被回收）
        self._field_problem_list = []    # P6：类型格式校验未通过的字段说明（保存后提示）
        self._detail_dirty = False
        self._detail_hidden = set()   # 2026-08-18：当前根目录下详情区隐藏的字段（③-⑦）
        # 2026-09-13（1-B）：会话内"显示全部字段"临时覆盖（None 语义用 False 表示未覆盖）
        self._detail_show_all = False
        # 2026-09-13（1-C-2）：当前条目的标签名列表（有序去重）与 chip 上的"×"按钮引用
        self._tag_names = []
        self._tag_chip_btns = []
        # 2026-09-16（批次 14）：🏷 标签区块"本次是否已渲染"——标签区块被手动隐藏时不渲染，
        #   保存标签与"推荐标签"据此跳过，避免清空/串写标签。
        self._tag_block_rendered = False
        # 2026-09-13（1-C-3）：标签页面是否打开（标签页面与四级目录互斥）
        self._tag_page_on = False
        # 2026-09-13（1-C-3b）：标签页面的选择/逻辑/呈现方式/排序/搜索词
        self._tag_page_selected = []          # 已选标签名（有序）
        self._tag_page_logic = "and"          # "and"=且 / "or"=或
        self._tag_page_search = ""
        self._tag_page_sort = "count"         # 列表呈现排序："count" / "name"
        self._view_before_tag_page = None     # 打开标签页面前的条目视图（关闭时恢复）
        # 2026-09-14（阶段 4 4-b）：热点词页签的搜索过滤词与"新增词条"输入框引用
        self._hotword_search = ""
        self._hotword_entry = None
        # 2026-09-14（性能优化，用户确认）：条目区渲染上限 / 标签一次性预取 / 分批追加
        self._entry_render_limit = ENTRY_RENDER_LIMIT
        self._entries_all = []          # 当前条目区**完整**列表（供"显示更多"）
        self._entries_title = ""        # 当前条目区标题（重渲染用）
        self._entry_render_shown = []   # 已渲染的条目（追加时只渲染新增部分）
        self._entry_more_btns = []      # 「显示更多 / 全部显示」按钮引用
        self._entry_count_lbl = None    # 「共 N 条（已显示前 M 条）」标签引用
        self._entry_tags_cache = {}     # {entry_id: [标签名]} 一次性预取，消除逐条查询
        self._tag_page_limit = TAG_PAGE_LIMIT
        # 2026-09-15（用户要求 6+7）：标签页分页——当前页码（仅"标签云/列表"两档生效；
        #   页码在渲染时按总页数自动钳制，改每页条数/搜索/排序/切档都会复位到第 1 页）。
        self._tag_page_no = 1
        # 2026-09-15（用户要求 8）：标签页/热点词搜索框的防抖定时器句柄
        self._tag_search_after = None
        # 2026-09-15（批次 4）：界面外观（字体/字号/颜色）配置——先给空配置，随后由 `_load_settings()` 载入
        self._ui_appearance = ui_appearance.empty()
        # 2026-09-14（阶段 3）：录入时自动推荐标签
        self._auto_tag_suggest = False   # T2：输入提示词后防抖自动推荐（设置开关，默认关）
        self._suggest_timer = None       # 防抖定时器
        self._rec_last_name = ""         # 上次已按该名称推荐过（避免重复推荐）
        try:
            self._tag_page_view = self.db.get_meta(config.META_TAG_VIEW) or "cloud"
        except Exception:
            self._tag_page_view = "cloud"
        # 2026-09-13（1-C-4b）：是否在条目处显示标签（默认关；由 _load_settings 读取）
        self._show_tags_in_list = False
        # 2026-09-13（第 2 期 2-b/2-c）：新建条目态"做法 B"暂存（只记源路径，保存成功后才复制落库）
        self._staged_cover = None       # 暂存的封面源文件（绝对路径）
        self._staged_gallery = []       # 暂存的图集项 [{kind, _src, source_url}]
        self._gallery_btns = []         # 图集行内按钮引用（浏览态统一置灰用）
        # 2026-09-13（第 3 期 3-a-2）：列表框字段的已选值 / 显示标签 / 按钮引用
        self._list_field_values = {}    # field_key -> [已选值]
        self._list_field_snaps = {}     # field_key -> {值: 快照名}（3-b 层级型：失联占位用）
        self._list_field_labels = {}    # field_key -> 显示用 CTkLabel
        self._list_field_btns = []      # 列表框"选择…/清空"按钮引用（浏览态统一置灰用）
        self._select_timer = None   # 悬停选中防抖定时器
        self._l0_btns = {}          # 根目录列按钮引用（用于原地更新高亮）
        self._l1_btns = {}          # 一级分类列按钮引用
        self._l2_btns = {}          # 二级分类列按钮引用
        self._l0_styles = {}        # 根目录列按钮原始配色（恢复高亮用）
        self._l1_styles = {}
        self._l2_styles = {}
        self._p_btns = {}           # 项目类别列按钮引用（四级分类最高层级，2026-08-29 新增）
        self._p_styles = {}         # 项目类别列按钮原始配色
        self._cur_project_id = None  # 当前项目类别 id（None=未分配视图）
        self._nav_initialized = False  # 首次导航默认选择是否已确定
        # 2026-09-10（用户要求 2-（2））：目录隐藏/目录显示——从"项目类别"起连续隐藏的列数（0~4）
        self._nav_hidden = 0  # 0=全部显示（按钮显示"目录隐藏"）；>0=按钮显示"目录显示"
        self._nav_cols = []   # 四个分类列控件（项目类别/根目录/一级分类/二级分类），_build_body 中填充
        self._toast_label = None
        self._name_entry = None  # 2026-09-07：名称输入框移入详情区后初始化占位
        # 2026-09-06：主界面就地"新增条目"状态
        self._adding_new = False     # 详情区是否处于"新增条目"空白态
        self._add_target = None      # 新增目标分类 id（None = 未分类）；仅新增态有效
        self._add_group_open = False # 新增态 ③-⑦ 折叠组是否展开
        self._add_group_pairs = []   # 折叠组内的 (标签, 文本框) 对（折叠/展开显隐用）
        self._add_prompt_toggles = {}  # 2026-09-11（用户要求 1）：新增表单 ⑧/⑨ 展开/收起按钮引用
        self._add_entry_btn = None   # 条目区"✚ 新增条目"按钮引用
        # 2026-09-22（用户要求 1）：四个分类列「✚ 新增…」按钮引用（固定在列顶，见 _build_body）
        self._add_proj_btn = None
        self._add_domain_btn = None
        self._add_l1_btn = None
        self._add_l2_btn = None
        # 2026-09-22（用户要求 2）：条目区分隔条拖拽状态（**仅内存**，不写任何设置）
        self._entry_grip_x0 = None
        self._entry_grip_w0 = 0

        # 2026-09-16（批次 13）：原"②~⑦ 折叠组"状态变量已删除——
        #   ②~⑦ 现拆为独立可折叠块，由 _detail_expand_all 控制全部展开/收起。
        # 2026-09-16（批次 13）："显示全部字段"按钮切换"全部展开/收起所有可折叠字段"
        self._detail_expand_all = False
        self._detail_last_entry_id = None  # 上一次渲染详情的条目 id（切条目时复位会话覆盖）
        self._browse_mode = False         # 浏览/编辑切换：True=只读浏览（可选中复制）
        # 2026-09-09：条目列悬停"全部条目名"浮层状态
        self._entry_ov_names = []
        self._entry_ov_popup = None
        self._entry_ov_after = None
        self._entry_ov_y = None
        self._entry_ov_listbox = None   # 2026-09-10（用户要求 4）：浮层内的列表控件（滚动同步用）
        # 2026-09-11 09:27（用户要求 3）：浮层内"光标所在条目"深色高亮状态
        self._entry_ov_ids = []         # 与 _entry_ov_names 一一对应的条目 id（定位高亮行用）
        self._entry_ov_cur = None       # 光标当前所在条目 id
        self._entry_ov_hl = None        # 浮层内上一次高亮的行号（恢复默认配色用）
        self._search_after = None       # 2026-09-10（用户要求）：搜索输入防抖定时器 id
        # 2026-09-12（用户要求 1~3）：详情区字段浮动提示 + "打开/关闭浮动提示窗口"总开关
        self._float_tips_on = True      # 总开关（作用：条目名称一览 + 详情区字段浮动提示）
        self._tip_popup = None          # 详情区字段浮动提示窗口（Toplevel）
        self._tip_text = None           # 浮窗内只读文本控件（tk.Text，同步滚动/加深用）
        self._tip_src = None            # 触发提示的源文本框内部 Text（tk.Text）
        self._tip_trigger = None        # 触发提示的控件（文本框，判断光标是否仍在阅读区）
        self._tip_after = None          # 延时弹出/延时关闭定时器 id
        self._tip_hl = None             # 浮窗内上一次加深的行号
        # 2026-09-14（用户要求，本轮）：工具栏"窗口过窄时折成两行"——
        #   _bar_row2 = **第二行容器**（默认隐藏）：折行时**只把"搜索框"搬到这里靠右**，
        #     两组工具按钮**始终留在第一行**（详见 _set_bar_folded 的说明）；
        #   _bar_folded = 当前是否已折行；_bar_one_row_need = "一行时工具栏所需宽度"（实测缓存）；
        #   _bar_last_w = 上次处理的窗口宽度（避免 <Configure> 高频重复计算）。
        # ⚠ 2026-09-15（审核 L-2）：原文写作"_bar_row2 = 第二组按钮(新建/导入/导出/锁定/设置)所在的容器"，
        #   与最终实现（第二组按钮从不离开第一行）不符 → 已更正。
        self._bar_row2 = None
        self._bar_folded = False
        self._bar_one_row_need = 0
        self._bar_last_w = 0
        # 2026-09-15（审核 L-7B）：窗口缩放的"最近一次成功值"缓存（异常时兜底用）；
        # 2026-09-15（审核 L-8）：折行时"第二行首列留白"的未折行实测缓存。
        self._scale_cache = None
        self._bar_row2_left = 0

        self._load_settings()   # 2026-08-18：应用持久化设置（窗口大小/视图模式/详情策略）
        self._build_toolbar()
        self._build_body()
        # 2026-09-10（用户要求 4-一）：在条目区滚动滚轮时，浮层"条目名称一览"按比例同步滚动。
        # 注册在 _build_body 之后，保证晚于 CTkScrollableFrame 自身的 bind_all 处理（先滚动条目列、
        # 再按新的滚动位置同步浮层）。add="+" 只追加不覆盖既有绑定。
        self.bind_all("<MouseWheel>", self._entry_ov_wheel, add="+")
        self._build_statusbar()
        self.refresh_domains()

        self.bind("<Escape>", self._on_escape)
        # 2026-09-14（用户要求，本轮）：窗口尺寸变化时判断工具栏是否需要折成两行（见 _on_root_configure）
        self.bind("<Configure>", self._on_root_configure, add="+")
        self._center_window()  # 2026-08-18（第022条）：窗口居中（左右居中、纵向略偏上）

    def _center_window(self) -> None:
        """主窗口定位：左右居中、纵向固定上边距（2026-08-19 00:30，第025条按用户方案简化）。

        方案：屏幕宽度作为变量 sw，窗口中心 = sw 的一半（左右水平居中）；
        纵向上窗口上边距屏幕上边固定 60px（用户指定 50~100px 区间）。
        触发机制（2026-08-19 00:30，第025条重构）：以 <Map> 事件为准——窗口真正显示（映射）
        瞬间触发定位，天然适应 EXE 慢启动（onefile 解压可达数十秒），不依赖固定重试
        时长；每次触发仅带 5 次短重试（共 1 秒），避免并行重试链膨胀；定位成功即置
        _centered 标志，后续 <Map>（如托盘恢复窗口）不再重复移动，不干扰用户手动拖动。
        边框修正：winfo_width 为内容区宽度，用 winfo_rootx - winfo_x 取得装饰框左
        边框宽度，使窗口装饰框真正水平居中（EXE 真机验证：中心偏差 ≤1px）。
        """
        _centered = False  # 2026-08-19 00:30（第025条）：已成功定位过则不再重复移动窗口
        def _do_center(attempt: int = 0):
            nonlocal _centered
            if _centered:                    # 已定位成功 → 不再重复移动窗口
                return
            try:
                self.update_idletasks()                  # 强制完成布局，取得真实尺寸
                w, h = self.winfo_width(), self.winfo_height()
                if not self.winfo_viewable() or w <= 1 or h <= 1:  # 未就绪 → 短重试
                    if attempt < 5:
                        self.after(200, lambda: _do_center(attempt + 1))
                    return
                sw = self.winfo_screenwidth()            # 屏幕宽度变量
                # 边框修正（2026-08-19 00:30，第025条）：winfo_width 是内容区宽度，装饰框
                # 多出左右边框，用 winfo_rootx - winfo_x 取得左边框宽，使装饰框真正居中
                border = self.winfo_rootx() - self.winfo_x()
                frame_w = w + 2 * border if border > 0 else w
                x = max((sw - frame_w) // 2, 0)          # 窗口中心 = 屏幕宽度的一半（左右居中）
                y = 60                                   # 上边距固定 60px（50~100px 区间）
                if y + h > self.winfo_screenheight():    # 极小屏保护：避免窗口底部超出屏幕
                    y = max(self.winfo_screenheight() - h - 20, 0)
                self.geometry(f"+{x}+{y}")
                # 2026-09-14（用户要求）：映射后按**真实缩放**再兜底一次——
                #   ① 重设"最小高度"（防止极小屏上最小高度 > 屏幕可用高度）；
                #   ② 把窗口宽/高与位置夹回屏幕内（**保证底边可见**）。
                try:
                    self.minsize(self._nav_min_width(), self._win_min_height())
                except Exception as exc:              # 2026-09-15（审核 L-7）：不再静默
                    _geom_warn("重设最小宽高", exc)
                self._fit_window_to_screen()
                _centered = True
            except Exception as exc:                  # 2026-09-15（审核 L-7）：不再静默
                _geom_warn("窗口居中定位", exc)

        # 2026-08-19 00:30（第025条）：窗口映射（显示）瞬间触发定位，覆盖 EXE 慢启动场景
        self.bind("<Map>", lambda _e=None: self.after(150, _do_center), add="+")
        self.after(100, _do_center)  # 首次尝试（窗口若已显示则立即定位）

    # ------------------------------------------------------------------ #
    # 布局构建
    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        # 2026-09-10（用户要求 1-（2））：搜索框移到最右侧（"⚙ 设置"右侧、靠边）；
        # 该列最小宽度 = 搜索输入框(96px) + 右侧放大镜按钮(27px) + 间距(4px) = 127px。
        # 2026-09-14（用户要求"无标签条目"入口 方案 A）：新增"🏷 无标条目"按钮插在"⭐ 常用"
        # **左侧**（col4），"⭐ 常用"顺延到其右侧（col5）；其后各列索引整体 +1 → 搜索框列由 10 改为 11。
        # 2026-09-14（用户要求"省宽"）：搜索框最小宽度 171 → 143（10→8 汉字）→ 127（8→7 汉字）。
        # 2026-09-14（用户要求，本轮）：**搜索框此前独占全部剩余宽度**（col 只它一个 weight=1）
        #   → 默认窗口（1792 宽）下被拉到约 559px，过宽。现改为**两列按 1:2 分配**额外宽度：
        #   · col11 = 弹性**空白**列（weight=2，无控件）；col12 = 搜索框列（weight=1, minsize=260）。
        #   → 额外宽度的 1/3 给搜索框、2/3 留白（搜索框不再独占全部剩余宽度）。
        # 2026-09-15（用户要求，本轮）：搜索输入框源码宽 152 → **202**（+50）、🔍 源码宽 1 → **45**（约原 2 倍）
        #   ⇒ 列最小宽度 183 → **260**（= 202 + 54 + 4，与下方 _build_toolbar 的标定注释一致）。
        bar.grid_columnconfigure(11, weight=2)                  # 弹性空白（"设置"与搜索框之间留白）
        bar.grid_columnconfigure(12, weight=1, minsize=260)     # 搜索框列（2026-09-15 用户要求：260）

        # 2026-09-14（用户最终决定，本轮 8）：**工具栏第二行只放"搜索框（输入框+🔍）"，靠右**——
        #   两组工具按钮（标签/目录显示/无类条目/无标条目/常用 + 新建/导入/导出/锁定/设置）
        #   始终固定在第一行不再移动；窗口放不下一行时，把搜索框移到本行右端。
        #   本容器**铺满整行**（`columnspan=13` + `sticky="ew"`）；内部第 0 列留出
        #   "第一行最左侧按钮左边界"的空白、第 1 列伸展 → 搜索框从该处向右自适应伸展（不外溢）。
        self._bar_row2 = ctk.CTkFrame(bar, fg_color="transparent")
        self._bar_row2.grid(**_BAR_ROW2_GRID)
        self._bar_row2.grid_forget()

        ctk.CTkLabel(bar, text="🧩 PromptSprite2", font=("Microsoft YaHei", 18, "bold")
                     ).grid(row=0, column=0, padx=(14, 20), pady=8)
        # 2026-09-10（用户要求 附）：两个汉字的命令按钮（锁定/常用/新建/导入/导出/设置）
        # 宽度再各减 ≈2 个英文字符（≈14px）；已到"文字+内边距"下限的（如设置）只能减到该下限。
        # 2026-09-14（用户要求，本轮 2）：**按钮内边距统一为 5、圆角随之改为 5**、
        #   **按钮之间间距为 5（源码 padx=2 → 本机 120% 缩放实际每侧 2px、相邻合计 4px，
        #   是整数源码值下最接近 5 的结果）**。
        #   内边距机制（已查 customtkinter 5.2.2 源码 ctk_button.py:304-310）：
        #   每侧内边距 = max(corner_radius, border_width+1, border_spacing) × 控件缩放；
        #   故把 `corner_radius` 设为 **5**（border_width 默认 0 与 border_spacing 默认 2 均小于 5）
        #   → 每侧 5（本机 120% 缩放实际渲染 ≈6px）。圆角也随之变为 5（即用户要求的"圆角一起改"）。
        #   注：CTk 用"圆角值"充当"最小内边距"，两者由同一个参数决定，无法各自独立设置。
        self.lock_btn = ctk.CTkButton(bar, text="🔒 锁定", width=1, corner_radius=5,
                                      command=self._toggle_lock)
        # 2026-09-14（用户要求）：按钮顺序调整为
        #   🏷标签(1) → 🗂目录隐藏(2) → 📂无类条目(3) → 🏷无标条目(4) → ⭐常用(5) → ✚新建(6) →
        #   ⇩导入(7) → ⇧导出(8) → 🔒锁定(9) → ⚙设置(10) → 搜索框(11)
        #   即："标签"移到最左侧第一个命令按钮；"锁定"移到"导出"与"设置"之间。
        #   2026-09-14（"无标签条目"入口 方案 A + 用户要求 4）：在"📂 无类条目"右侧插入
        #   "🏷 无标条目"（col4），"⭐ 常用"顺延到 col5，故 新建/导入/导出/锁定/设置/搜索框 各 +1。
        #   2026-09-14（本轮折行设计）：第二组 5 个按钮**一行时仍在第一行**（col6~col10），
        #   折行时由 `_set_bar_folded` 用 grid(in_=...) 移入第二行容器 `_bar_row2`。
        self.lock_btn.grid(row=0, column=9, padx=2)
        # 2026-08-22（第007条）：记录锁定按钮默认配色——customtkinter 6.0.0 中
        # configure(fg_color=None) 会抛 ValueError，解锁时须恢复为记录的默认色
        self._lock_btn_default_fg = self.lock_btn.cget("fg_color")
        self._lock_btn_default_hover = self.lock_btn.cget("hover_color")
        # 2026-09-10（用户要求 1-（1）、2-（2））：原"🗂 项目列"按钮改名"目录隐藏/目录显示"，
        # 位置移到"🔒 锁定"右侧、"📂 无类条目"左侧；功能改为显示/隐藏各分类列（见 _on_dir_toggle）。
        self.btn_project_toggle = ctk.CTkButton(
            bar, text="🗂 目录隐藏", width=1, corner_radius=5, command=self._on_dir_toggle)
        self.btn_project_toggle.grid(row=0, column=2, padx=2)
        # 2026-09-10（用户要求 附）：按钮名称 "未分类条目" → "无类条目"，宽度按新文案贴合（96→84）
        # 2026-09-14（用户要求 1）：各命令按钮间距源码值 padx=4 → 3。
        #   ⚠ 实测纠正：CustomTkinter 会按**控件缩放**换算 grid 的 padx/pady（本机 120% 缩放，
        #   系数 ≈1.2），3 × 1.2 = 3.6 → **取整回 4**，故在本机上"间距"实际**没有变化**
        #   （grid_info 实测仍为 4；pady 8→10、(14,20)→(17,24) 同理）。此值在 100% 缩放环境才会
        #   真正变小。若要在本机也真正收窄，需设为 **padx=2**（2×1.2=2.4→2）——**待你允许后再改**。
        ctk.CTkButton(bar, text="📂 无类条目", width=1, corner_radius=5,
                      command=self._show_uncategorized
                      ).grid(row=0, column=3, padx=2)
        # 2026-09-14（用户要求"无标签条目"入口 方案 A）：与"📂 无类条目"并列——
        #   前者找"无分类"的条目，后者找"没有任何标签"的条目；点击后条目区列出全部无标签
        #   条目（标题栏显示总数），可逐条点选后在详情区打标签。
        #   2026-09-14（用户要求 3）：按钮文案由"🏷 无标签条目"改为"🏷 无标条目"（与"无类条目"对称）。
        #   2026-09-14（用户要求 4）：本按钮移到"⭐ 常用"**左侧**（col4），"⭐ 常用"移到其右侧（col5）。
        self.untagged_btn = ctk.CTkButton(bar, text="🏷 无标条目", width=1, corner_radius=5,
                                          command=self._show_untagged)
        self.untagged_btn.grid(row=0, column=4, padx=2)
        ctk.CTkButton(bar, text="⭐ 常用", width=1, corner_radius=5, command=self._show_favorites
                      ).grid(row=0, column=5, padx=2)

        # 导入/导出下拉菜单
        self.import_menu = tk.Menu(bar, tearoff=0)
        self.import_menu.add_command(label="数据迁移向导…", command=self._open_migrate_wizard)  # 2026-08-29（M5）
        # 2026-09-13（第 4 期 4-c）：T1 离线"网上资源结构化"向导
        self.import_menu.add_command(label="🧩 网上资源结构化（向导）…",
                                     command=self._open_import_wizard)
        self.import_menu.add_separator()
        self.import_menu.add_command(label="导入 JSON 备份…", command=self._import_json)
        self.import_menu.add_command(label="导入变更包…（新增/删除合并）",  # 2026-09-08（V1.7.0）：原"导入增量备份"
                                     command=self._import_change_pack)
        self.import_menu.add_command(label="导入 Excel…", command=self._import_excel)
        self.import_menu.add_command(label="导入 Markdown 手册…", command=self._import_md)
        self.import_menu.add_separator()  # 2026-09-08（V1.7.0）：数据比对（只读诊断）
        self.import_menu.add_command(label="数据比对…（与备份 *.db）",
                                     command=self._compare_with_backup)
        self.export_menu = tk.Menu(bar, tearoff=0)
        self.export_menu.add_command(label="导出全部 JSON…",
                                     command=lambda: self._export_json(current_only=False))
        self.export_menu.add_command(label="导出当前分类 JSON…",
                                     command=lambda: self._export_json(current_only=True))
        self.export_menu.add_separator()
        self.export_menu.add_command(label="导出全部 Excel…",
                                     command=lambda: self._export_excel(current_only=False))
        self.export_menu.add_command(label="导出当前分类 Excel…",
                                     command=lambda: self._export_excel(current_only=True))
        self.export_menu.add_separator()
        self.export_menu.add_command(label="导出全部 HTML…",
                                     command=lambda: self._export_html(current_only=False))
        self.export_menu.add_command(label="导出当前分类 HTML…",
                                     command=lambda: self._export_html(current_only=True))
        self.export_menu.add_separator()  # 2026-08-29（M4）变更包数据导出
        self.export_menu.add_command(label="变更包数据导出 Excel…",
                                     command=lambda: self._export_incremental_browse("excel"))
        self.export_menu.add_command(label="变更包数据导出 HTML…",
                                     command=lambda: self._export_incremental_browse("html"))
        self.export_menu.add_command(label="导出当日变更包文件到…",
                                     command=self._export_incremental_file_to)

        # 2026-09-10（用户要求 1-（3））：快速新建移到"⇩ 导入"左侧
        # 2026-09-10（用户要求 3 追加）：工具栏各命令按钮宽度统一缩减 ≈2 个英文字符（≈14 逻辑px），
        # 文字仍完整显示（CTkButton 会自动撑到"文字+内边距"的最小宽度，故不会出现文字裁切）。
        # 2026-09-10（用户要求 附）：名称 "快速新建" → "新建"，宽度再减 ≈14px（96→82）。
        # 2026-09-13（1-C-3）：工具栏"🏷 标签"按钮，打开/关闭"标签页面"。
        # 2026-09-14（用户要求）：**移到最左侧、成为第一个命令按钮**（原在"⭐ 常用"右侧）。
        self.tag_page_btn = ctk.CTkButton(bar, text="🏷 标签", width=1, corner_radius=5,
                                          command=self._toggle_tag_page)
        self.tag_page_btn.grid(row=0, column=1, padx=2)

        # ---- 2026-09-14（本轮折行设计）：第二组 5 个按钮（一行时仍在第一行 col6~col10） ----
        self.quick_add_btn = ctk.CTkButton(bar, text="✚ 新建", width=1, corner_radius=5,
                                           command=self._quick_add)
        self.quick_add_btn.grid(row=0, column=6, padx=2)

        self.import_btn = ctk.CTkButton(bar, text="⇩ 导入", width=1, corner_radius=5)
        self.import_btn.grid(row=0, column=7, padx=2)
        self.import_btn.bind("<Button-1>",
                             lambda e=None: self._popup_menu(self.import_menu, e))
        self.export_btn = ctk.CTkButton(bar, text="⇧ 导出", width=1, corner_radius=5)
        self.export_btn.grid(row=0, column=8, padx=2)
        self.export_btn.bind("<Button-1>",
                             lambda e=None: self._popup_menu(self.export_menu, e))

        # ⚙ 设置（与其它命令按钮一致：宽度＝刚好显示文字、内边距 5、间距 5）
        self.set_btn = ctk.CTkButton(bar, text="⚙ 设置", width=1, corner_radius=5,
                                     command=self._open_settings)  # 2026-08-18：设置入口
        self.set_btn.grid(row=0, column=10, padx=2)
        # 第二组按钮清单（仅供回归测试核对分组；一组/二组**始终同在第一行**，不再搬移）
        self._bar_group2 = [(self.quick_add_btn, 6), (self.import_btn, 7),
                            (self.export_btn, 8), (self.lock_btn, 9), (self.set_btn, 10)]
        # 2026-09-10（用户要求 1-（2））：搜索框移到"⚙ 设置"右侧（工具栏最右、靠边）。
        # 2026-09-10（用户要求 2）：搜索框右侧新增"🔍"小图标按钮，点击即可执行搜索。
        # 2026-09-14（用户要求"省宽"1）：搜索输入框最小宽度 10 汉字(140) → 8 汉字(112)。
        # 2026-09-14（用户要求 1，本轮）：再 **112 → 96（≈7 汉字）**，且右侧外边距 10 → 6。
        #   该列是可收缩列之一（weight=1，另有权重更大的空白列）→ 只影响"窗口被缩到很窄时"的最小宽度。
        #   实测：窗口最小可用宽度 1357（8 汉字）→ **1299**（本轮，含按钮间距收窄）。
        # 2026-09-14（用户明确要求，本轮 3）：搜索输入框宽度 **+4 个汉字（4×14 = 56px）：96 → 152**；
        #   对应列最小宽度 127 → **183**（= 152 + 27 + 4）。
        # 2026-09-15（用户要求，本轮）：输入框源码宽再 **+50：152 → 202**；
        #   🔍 由"按文字自适应"（源码 1）改为**固定 45**（本机 ≈54px，约原 27px 的 2 倍）
        #   ⇒ 对应列最小宽度 183 → **260**（= 202 + 54 + 4）。
        self.search_box = ctk.CTkFrame(bar, fg_color="transparent")
        self.search_box.grid(row=0, column=12, padx=(4, 6), sticky="ew")
        self.search_entry = ctk.CTkEntry(self.search_box, placeholder_text="搜索提示词（匹配全部字段）",
                                         width=202)
        self.search_entry.pack(side="left", fill="x", expand=True)
        # 2026-09-10（用户要求）：输入走防抖（停顿 _SEARCH_DEBOUNCE_MS 才查询）；回车立即查询。
        self.search_entry.bind("<KeyRelease>", self._on_search_typing)
        self.search_entry.bind("<Return>", self._on_search_key)
        # 2026-09-17（用户要求 1）：输入框右侧新增"✕"**一键清除**按钮——原先只能逐字退格删除。
        #   宽度取 20（≈2 个汉字的一半）：既一眼可点，又尽量少占"搜索框列"的宽度。
        self.search_clear_btn = ctk.CTkButton(
            self.search_box, text="✕", width=20, height=28, corner_radius=5,
            fg_color="#e8ecf1", hover_color="#d5dce5", text_color="#1f2937",
            font=("Microsoft YaHei", 12), command=self._on_search_clear)
        self.search_clear_btn.pack(side="left", padx=(2, 0))
        # 2026-09-15（用户要求）：🔍 宽度 1 → **45**（固定宽度；约为原来 27px 的 2 倍）
        self.search_btn = ctk.CTkButton(self.search_box, text="🔍", width=45, height=28,
                                        corner_radius=5,
                                        command=self._on_search_click)
        self.search_btn.pack(side="left", padx=(2, 0))

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self)
        self._body = body   # 2026-09-13（1-C-3）：标签页面需要调整第 0 列最小宽度
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(4, weight=0)   # 条目区：固定宽度（≈16汉字，2026-09-09 加宽）
        body.grid_columnconfigure(5, weight=1)   # 详情区：占据剩余空间

        # 2026-08-29 用户要求列宽：1汉字≈14px；项目类别/根目录 ≤8汉字(112px)，一级/二级 ≤12汉字(168px)
        # 2026-09-09：条目区由 168px(≈12汉字) 加宽至 224px(≈16汉字)，便于同屏多看几个条目名
        # 2026-09-10（用户要求 3）：列宽统一取自模块常量 _NAV_COL_WIDTHS（与窗口最小宽度计算同源）
        # 2026-09-22（用户要求 1）：四个分类列与条目区改为"**上方固定「新增」按钮** + 下方滚动列表"。
        #   原先「✚ 新增…」按钮建在可滚动区内，会随内容一起滚动（条目/分类一多就滚出视野）。
        #   现改为：外层容器（与可滚动区**同默认底色/圆角**，外观无变化）承载"固定按钮 + 滚动区"；
        #   外层容器即"列本体"（目录隐藏/显示按整列显隐），故 `_nav_cols` 改指向容器。
        self.project_col = ctk.CTkFrame(body)
        self.l0_col = ctk.CTkFrame(body)
        self.l1_col = ctk.CTkFrame(body)
        self.l2_col = ctk.CTkFrame(body)
        self.entry_col = ctk.CTkFrame(body)

        self.project_frame = ctk.CTkScrollableFrame(self.project_col, width=_NAV_COL_WIDTHS[0], label_text="项目类别")
        self.l0_frame = ctk.CTkScrollableFrame(self.l0_col, width=_NAV_COL_WIDTHS[1], label_text="根目录")
        self.l1_frame = ctk.CTkScrollableFrame(self.l1_col, width=_NAV_COL_WIDTHS[2], label_text="一级分类")
        self.l2_frame = ctk.CTkScrollableFrame(self.l2_col, width=_NAV_COL_WIDTHS[3], label_text="二级分类")
        # 2026-09-22（用户要求 2）：分隔条宽度**算在条目区总宽之内**（内容宽 = 224 − 6），
        #   这样条目区列总宽与改动前完全一致，不影响"窗口最小宽度/详情区最小宽"的既有标定。
        self.entry_frame = ctk.CTkScrollableFrame(self.entry_col,
                                                  width=_ENTRY_COL_W - _ENTRY_GRIP_W,
                                                  label_text="条目")

        # 2026-09-22（用户要求 2）：条目区右缘加一条**可拖拽的"分隔条"**——
        #   拖动即调整条目区宽度（右侧详情区自动让位）；宽度只存内存、不写任何设置，
        #   故每次启动都按默认值 _ENTRY_COL_W 显示（不记忆上次关闭时的宽度）。
        #   放在这里先 pack（side="right"），使其占满条目区整列高度。
        self.entry_grip = tk.Frame(self.entry_col, width=_ENTRY_GRIP_W, bg="#c9d1dd",
                                   cursor="sb_h_double_arrow", highlightthickness=0,
                                   bd=0)
        self.entry_grip.pack(side="right", fill="y")
        self.entry_grip.bind("<Button-1>", self._entry_grip_press)
        self.entry_grip.bind("<B1-Motion>", self._entry_grip_drag)

        # 2026-09-22（用户要求 1）：五个「新增」按钮**高度统一为 28**；
        #   「新增条目」仍保留绿底白字加粗的醒目样式（字号由 15 调整为 13 以适配 28 高度）。
        # 2026-09-22（用户要求 1）：五个「新增」按钮**只创建一次**并固定在各自区域顶部；
        #   各 `_refresh_*` / `_render_entries` 只更新其启用状态，不再在滚动区内重建按钮。
        self._add_proj_btn = ctk.CTkButton(
            self.project_col, text="✚ 新增项目类别", height=28, **_ADD_BTN,
            command=self._add_project)
        self._add_domain_btn = ctk.CTkButton(
            self.l0_col, text="✚ 新增根目录", height=28, **_ADD_BTN,
            command=self._add_domain)
        self._add_l1_btn = ctk.CTkButton(
            self.l1_col, text="✚ 新增一级分类", height=28, **_ADD_BTN,
            command=self._add_l1)
        self._add_l2_btn = ctk.CTkButton(
            self.l2_col, text="✚ 新增二级分类", height=28, **_ADD_BTN,
            command=self._add_l2)
        # 2026-09-07：条目区"新增条目"主按钮——绿色白字加粗，突出"要新增就点这里"
        self._add_entry_btn = ctk.CTkButton(
            self.entry_col, text="＋ 新增条目", height=28,
            fg_color=_C_OK, hover_color="#256e46", text_color="white",
            font=("Microsoft YaHei", 13, "bold"),
            state="normal" if self._add_available() else "disabled",
            command=self._start_new_entry)

        for _col, _btn, _pad in ((self.project_col, self._add_proj_btn, (3, 2)),
                                 (self.l0_col, self._add_domain_btn, (3, 2)),
                                 (self.l1_col, self._add_l1_btn, (2, 2)),
                                 (self.l2_col, self._add_l2_btn, (2, 2)),
                                 (self.entry_col, self._add_entry_btn, (4, 2))):
            _btn.pack(side="top", fill="x", padx=6, pady=_pad)

        self.project_frame.pack(side="top", fill="both", expand=True)
        self.l0_frame.pack(side="top", fill="both", expand=True)
        self.l1_frame.pack(side="top", fill="both", expand=True)
        self.l2_frame.pack(side="top", fill="both", expand=True)
        self.entry_frame.pack(side="top", fill="both", expand=True)

        self.project_col.grid(row=0, column=0, sticky="nsew")
        self.l0_col.grid(row=0, column=1, sticky="nsew")
        self.l1_col.grid(row=0, column=2, sticky="nsew")
        self.l2_col.grid(row=0, column=3, sticky="nsew")
        self.entry_col.grid(row=0, column=4, sticky="nsew")
        # 2026-09-10（用户要求 2-（2））：四个分类列控件按"从左到右"顺序登记，
        # 供"目录隐藏/目录显示"按连续前缀隐藏、连续后缀显示（见 apply_nav_visibility）。
        # 2026-09-22（用户要求 1）：登记的改为**外层容器**（容器显隐＝整列显隐，含固定按钮）。
        self._nav_cols = [self.project_col, self.l0_col, self.l1_col, self.l2_col]
        # 2026-09-22（用户要求 1 的必要配套）：**锁定各列列宽**——
        #   把「新增」按钮移出滚动区后，按钮"文字所需宽度"会直接顶宽 grid 列
        #   （实测：项目类别列 112→182、条目列 →293），从详情区抢走约 315px，
        #   导致"详情区最小宽"时 2 个按钮被压（GUI 回归实测到）。故容器设固定宽 +
        #   关闭尺寸传播，使各列宽度**与改动前完全一致**（列宽只由下面两个常量决定）。
        #   注：容器内子控件用 pack 排布 ⇒ 必须用 **pack_propagate(False)**（用 grid_propagate 无效）。
        for _col, _w in ((self.project_col, _NAV_COL_WIDTHS[0]),
                         (self.l0_col, _NAV_COL_WIDTHS[1]),
                         (self.l1_col, _NAV_COL_WIDTHS[2]),
                         (self.l2_col, _NAV_COL_WIDTHS[3]),
                         (self.entry_col, _ENTRY_COL_W)):
            _col.configure(width=_w)
            _col.pack_propagate(False)
        # 2026-09-09：悬停条目区浮出"全部条目名"（进入/离开各处理一次，避免重复绑定累积）
        self.entry_frame.bind("<Enter>", self._entry_ov_enter, add="+")
        self.entry_frame.bind("<Leave>", self._entry_ov_leave, add="+")

        # 2026-09-13（1-C-3）：标签页面容器——与四级目录**互斥**、占用左侧同一区域
        # （宽度＝四列合计，故条目区/详情区位置与宽度完全不变）；初始不 grid（隐藏），
        # 打开时以 column=0、columnspan=4 覆盖原四列位置，由 _open_tag_page 负责显示。
        self.tag_frame = ctk.CTkFrame(body, width=sum(_NAV_COL_WIDTHS), corner_radius=0,
                                      fg_color="#ffffff")

        # 详情区：右侧以"浅灰蓝底 + 白色内容卡片"与左侧导航区分（2026-09-07 美化）
        self.detail_root = ctk.CTkFrame(body, fg_color="#e9eef5")
        self.detail_root.grid(row=0, column=5, sticky="nsew")
        self.detail_root.grid_rowconfigure(1, weight=1)
        self.detail_root.grid_columnconfigure(0, weight=1)

        self.detail_head = ctk.CTkFrame(self.detail_root, fg_color="#e9eef5")
        self.detail_head.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))

        row1 = ctk.CTkFrame(self.detail_head, fg_color="transparent")
        row1.pack(fill="x", padx=6, pady=(4, 0))
        # 名称输入框已移入详情区（① 条目名称），row1 只保留命令按钮；
        # 2026-09-10（用户要求）："编辑/浏览"与"☆ 收藏"互换位置 →
        #   显示顺序（左→右）＝ ☆ 收藏 → 移动到 → 关联到 → 复制到 …… 编辑/浏览（最右）。
        # 2026-09-14（用户要求，本轮）：详情区上方**所有按钮**宽度＝"刚好显示文字"（width=1 自适应）、
        #   内边距 5（corner_radius=5）、按钮之间间距 5（源码 padx=2 → 本机实际每侧 2px、相邻合计 4px）。
        # 2026-09-10（用户第2条）："☆ 收藏"文案短、宽度余量大 → 92→74。
        # 2026-09-10（用户要求）：三个按钮文字标签**去掉末尾省略号**（…）。
        # 2026-09-14（用户要求，本轮）："☆ 收藏"按钮宽度**再增加约 1 个汉字字符**
        #   （约 +14px 实际，源码 width 由"自适应(width=1)"改为 67 → 实测渲染 ≈80px）。
        # 2026-09-14 20:29（用户确认"以现状为准"）：本行最终值为 **87**（67 + 20px ≈ 1 个汉字，
        #   本机字体 15pt ≈ 20px）；测试断言同步为 87。
        self.fav_btn = ctk.CTkButton(row1, text="☆ 收藏", width=87, corner_radius=5)
        # 2026-09-10（用户要求 附）："移动到/关联到/复制到"宽度缩减到刚好容纳文字标签。
        # 2026-09-14 20:35（用户要求，本轮）：这三个按钮改用**统一固定宽度** `_DETAIL6_BTN_W`
        #   （与第二行"复制全部/复制中文/复制英文"同宽、上下对齐）；
        #   同时把被外部改坏的文案「➜ 移-动-到」**恢复为「➜ 移动到」**（用户明确：对齐后即可去掉横线）。
        self.copyto_btn = ctk.CTkButton(row1, text="⧉ 复制到", width=_DETAIL6_BTN_W, corner_radius=5,
                                        command=lambda: None)
        self.link_btn = ctk.CTkButton(row1, text="↔ 关联到", width=_DETAIL6_BTN_W, corner_radius=5,
                                      command=lambda: None)
        self.move_btn = ctk.CTkButton(row1, text="➜ 移动到", width=_DETAIL6_BTN_W, corner_radius=5,
                                      command=lambda: None)
        # 2026-09-14 20:35（用户要求，本轮）：右侧内边距 2 → 3（+1px），使本行三个按钮的
        #   起始 x（111）与第二行三个按钮的起始 x（111 = "⑧/⑨ 提示词："标签宽 109 + padx 2）**逐列对齐**。
        self.fav_btn.pack(side="left", padx=(2, 3))   # 收藏换到最左端

        # 2026-09-09：浏览/编辑切换——浏览时详情文本只读（可选中复制），避免误改内容
        # 2026-09-10（用户第2条）：CTkSegmentedButton 的 width 参数不生效，整体宽度由各分段按钮
        # "文案+内边距"自适应，此处显式设置每个分段按钮的宽度。
        # 2026-09-10（用户要求 1）：分段按钮宽度改为"刚好容纳最宽标签（✏️ 编辑）"的最小值——
        # 实测 "✏️ 编辑" 文字 61px、"👁 浏览" 文字 41px（本轮实测值）。
        # 2026-09-14（用户要求 2 + 本轮"都减小"）：**整体宽度 112 → 100**（实测总宽 134 → 120），
        #   并把"浏览"段比例再压低（60 → 52）→ 实测实宽 编辑 73→约69、浏览 61→约51；
        #   下限：编辑段实宽必须 ≥ 其文字宽 61px（否则文字被裁），故整体不宜小于 100。
        #   ⚠ 2026-09-15（审核 L-4）：以上 3 条均为**历史**——该分段控件已于 2026-09-14 被
        #     "普通按钮"取代（见下条），其中的宽度数值**不再生效**，仅作沿革留档。
        # 2026-09-14（用户要求，本轮）：**改为普通按钮**（与「🗂 目录隐藏」同形式）——
        #   点一下进入"浏览"（按钮显示「👁 浏览」），再点一下回到"编辑"（按钮显示「✏️ 编辑」）；
        #   即按钮文字始终表示**当前状态**。**该条当时**宽度＝刚好显示文字（width=1）、内边距 5、间距 2
        #   （⚠ 2026-09-15：现为**固定宽度** `_DETAIL_RCOL_A_W`，见下条）。
        #   影响面评估：全项目对 edit_mode_toggle 的引用共 6 处（本处创建 + 3 处 set + 2 处 state
        #   配置），改用普通按钮后 `set()` → `configure(text=...)`，其余（state 禁用/启用）语义不变 → 风险小。
        # 2026-09-14 21:05（用户选方案 A）：宽度改为**固定** `_DETAIL_RCOL_A_W`（源码 60 → 72px），
        #   与第二行「重置」同列同宽；且切换"编辑/浏览"文案时宽度**不再变化**（原 width=1 会随文案变宽变窄）。
        self.edit_mode_toggle = ctk.CTkButton(
            row1, text="✏️ 编辑", width=_DETAIL_RCOL_A_W, corner_radius=5,
            command=self._on_edit_mode_click)
        # 2026-09-13（1-A-4，用户指定）：详情区右上"编辑/浏览"**右侧**新增"字段管理"小图标按钮。
        # pack 规则：同为 side="right" 时"先 pack 者更靠右"——故本按钮须在 edit_mode_toggle
        # 之前 pack，才能出现在其右侧；不影响其余按钮的位置与宽度。
        # 2026-09-14 21:05（用户选方案 A）：宽度改为**固定** `_DETAIL_RCOL_B_W`（源码 55 → 66px），
        #   与第二行「💾 保存」同列同宽（文字仍是"🔧"，仅内边距变大、图标居中）。
        self.field_mgr_btn = ctk.CTkButton(
            row1, text="🔧", width=_DETAIL_RCOL_B_W, height=28, corner_radius=5,
            font=("Microsoft YaHei", 14),
            command=self._open_field_manager)
        self.field_mgr_btn.pack(side="right", padx=2)
        _attach_tooltip(self.field_mgr_btn, "字段管理（重命名详情区区块）")
        # 2026-09-10（用户要求）：与"☆ 收藏"互换位置 → 编辑/浏览改靠最右端
        self.edit_mode_toggle.pack(side="right", padx=2)
        # 2026-09-10（用户要求 2）：移动到/关联到/复制到 紧挨"☆ 收藏"依次向右排列
        self.move_btn.pack(side="left", padx=2)
        self.link_btn.pack(side="left", padx=2)
        self.copyto_btn.pack(side="left", padx=2)

        row2 = ctk.CTkFrame(self.detail_head, fg_color="transparent")
        row2.pack(fill="x", padx=6, pady=(2, 6))
        # 2026-09-14 20:35（用户要求，本轮）：标签宽度固定为源码 92（实际 110px；其文字需求 109px，
        #   故不裁字），使本行三个按钮的起始 x 与第一行**完全一致**（112/212/312），实现逐列对齐。
        ctk.CTkLabel(row2, text="⑧/⑨ 提示词：", width=92,
                     font=("Microsoft YaHei", 13, "bold")).pack(side="left")
        # 2026-09-10（用户要求）："复制中文/复制英文/保存"各减 1 个英文字符（≈7px），
        # 为右侧"重置"腾出空间；保存宽度 78→71（上一条要求已 92→78）；
        # "复制全部"再减 1 个英文字符（86→79），进一步为"重置"腾空间。
        # 2026-09-14（用户要求，本轮）：本行按钮全部改为"刚好显示文字"（width=1）+ 内边距 5 + 间距 5。
        # 2026-09-14 20:35（用户要求，本轮）："复制全部/复制中文/复制英文"改用**统一固定宽度**
        #   `_DETAIL6_BTN_W`（与第一行"移动到/关联到/复制到"同宽、上下对齐）。
        self.copy_all_btn = ctk.CTkButton(row2, text="📋 复制全部", width=_DETAIL6_BTN_W,
                                          corner_radius=5, fg_color=_C_OK)
        self.copy_all_btn.pack(side="left", padx=2)
        self.copy_cn_btn = ctk.CTkButton(row2, text="复制中文", width=_DETAIL6_BTN_W,
                                         corner_radius=5)
        self.copy_cn_btn.pack(side="left", padx=2)
        self.copy_en_btn = ctk.CTkButton(row2, text="复制英文", width=_DETAIL6_BTN_W,
                                         corner_radius=5)
        self.copy_en_btn.pack(side="left", padx=2)
        # ⚠ 2026-09-15（审核 L-4）：以下为"保存 / 重置"宽度的**完整沿革**，
        #   最终取值见**最后一条**：保存 `_DETAIL_RCOL_B_W`（源码 55 → 66px）、重置 `_DETAIL_RCOL_A_W`（源码 60 → 72px）。
        # 2026-09-10（用户要求 3）：保存宽度 −2 个英文字符（≈14px）：92→78；
        # 重置宽度 +2 个英文字符（≈14px）：72→86。
        # 2026-09-10（用户要求）："重置"过宽 → 再减 1 个汉字字符（≈14px）：86→72；
        # "保存"过窄 → 加 1 个英文字符（≈7px）：71→78。
        # 2026-09-14（用户要求，本轮）：宽度改为"刚好显示文字"（width=1）+ 内边距 5 + 间距 5。
        #   2026-09-14（用户要求，本轮）："重置"宽度明确设为 **3 个汉字字符宽**
        #   （源码 36 → 实际 ≈43px ≈ 3 汉字；文字"重置"仅 2 汉字，故左右各有约 8px 余量）。
        # 2026-09-14 20:29（用户确认"以现状为准"）：一度改为 **56**（36 + 20px ≈ 1 个汉字）。
        # 2026-09-14 20:45（用户最终确认）：**仍取 36**（＝3 个汉字，回到最初要求）。
        # 2026-09-14 21:05（用户选方案 A，右侧对齐）：为与第一行「✏️ 编辑」（72px）**同列同宽**，
        #   重置宽度改为 `_DETAIL_RCOL_A_W`（源码 60 → 72px）——用户已知悉并同意。
        self.save_btn = ctk.CTkButton(row2, text="💾 保存", width=_DETAIL_RCOL_B_W,
                                      corner_radius=5, fg_color=_C_OK)
        self.save_btn.pack(side="right", padx=2)
        self.reset_btn = ctk.CTkButton(row2, text="重置", width=_DETAIL_RCOL_A_W, corner_radius=5)
        self.reset_btn.pack(side="right", padx=2)

        # 2026-09-10（用户第1条）：详情内容"白色圆角卡片"独立成 detail_card，
        # 状态行移入白卡内部顶部（位于"复制…/保存"行之下、"① 条目名称"之上），
        # 仍固定在白卡内、不随滚动消失；下方为可滚动正文。圆角与边框由白卡承担，
        # 滚动区自身不再画边框，避免出现"双边框"。
        self.detail_card = ctk.CTkFrame(self.detail_root, fg_color="#ffffff",
                                        corner_radius=12, border_width=1,
                                        border_color="#c9d3df")
        self.detail_card.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.detail_card.grid_rowconfigure(1, weight=1)
        self.detail_card.grid_columnconfigure(0, weight=1)

        # 2026-09-09：详情区固定状态行（不随滚动消失）——左侧"详情 · 精简模式 /
        # 已展开全部字段"状态标题，右侧"⏵ 显示全部字段 / ⏸ 精简显示"开关，只作用于 ②~⑦，
        # 与 ②~⑦ 标题条内联"展开/收起"联动。任何根目录/任何层级查看时都恒常出现。
        state_row = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        state_row.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        self.detail_state_lbl = ctk.CTkLabel(
            state_row, text="详情 · 精简模式", font=("Microsoft YaHei", 13, "bold"),
            text_color="#25639c", anchor="w")
        self.detail_state_lbl.pack(side="left", fill="x", expand=True, padx=(4, 6))
        self.show_all_btn = ctk.CTkButton(state_row, text="⏵ 显示全部字段",
                                          width=140, height=28, **_ADD_BTN,
                                          command=self._toggle_detail_show_all)
        self.show_all_btn.pack(side="right", padx=(0, 4))
        ctk.CTkLabel(state_row, text="（②~⑦ 默认折叠，展开/收起用本开关或标题条“展开”）",
                     font=("Microsoft YaHei", 9), text_color="#9aa4b1"
                     ).pack(side="right", padx=(0, 8))

        # 2026-09-07：详情内容区做成"白色圆角卡片"，与浅灰蓝底区隔、更聚焦
        # 2026-09-10：改为白卡内部的正文滚动区（白底、无边框、无圆角，随白卡一起呈现）
        self.detail_scroll = ctk.CTkScrollableFrame(self.detail_card, label_text="详情",
                                                    fg_color="#ffffff", corner_radius=0)
        self.detail_scroll.grid(row=1, column=0, sticky="nsew", padx=(2, 0), pady=(0, 2))

        # 2026-09-07（第3条改进）："🗑 删除"移到详情区最下方"底部常驻栏"，
        # 不再占用顶部操作行；无当前条目/锁定态/新增态时自动禁用（见 _apply_lock_state）。
        # 2026-09-10（用户要求 1~3）：三个历史/删除类按钮整体右侧停靠，顺序（左→右）
        # 新增历史 → 删除历史/回收站 → 删除当前条目（最右靠边）；宽度按"刚好容纳文字标签"缩减。
        self.detail_foot = ctk.CTkFrame(self.detail_root, fg_color="#e9eef5")
        self.detail_foot.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
        self.del_btn = ctk.CTkButton(self.detail_foot, text="🗑 删除当前条目",
                                     width=110, height=32, fg_color=_C_DANGER)
        # 2026-09-10（用户要求 二）："删除当前条目"左侧新增小图标快捷按钮（无文字标签），
        # 一键"全部隐藏/全部显示"各分类区域（与工具栏"目录隐藏/目录显示"按钮等价，见 _toggle_all_dirs）。
        self.dir_toggle_btn = ctk.CTkButton(
            self.detail_foot, text="🗂", width=32, height=32,
            fg_color=_DIR_OPEN_BG, hover_color=_DIR_OPEN_HOVER, font=("Microsoft YaHei", 15),
            command=self._toggle_all_dirs)
        self.dir_toggle_btn.pack(side="left", padx=(8, 0), pady=4)
        self._apply_dir_toggle_style()   # 2026-09-12（用户要求 3）：按目录显隐刷绿色/黄色
        # 2026-09-13（1-C-3）：🗂 与 💬 **之间**新增"打开/关闭标签页面"快捷按钮（无文字标签）。
        # 打开＝紫色、关闭＝橙色；与"打开/关闭四级目录"互斥（打开标签页面即关闭四级目录）。
        self.tag_toggle_btn = ctk.CTkButton(
            self.detail_foot, text="🏷", width=32, height=32,
            fg_color=_TAG_CLOSED_BG, hover_color=_TAG_CLOSED_HOVER,
            font=("Microsoft YaHei", 15), command=self._toggle_tag_page)
        self.tag_toggle_btn.pack(side="left", padx=(6, 0), pady=4)
        self._apply_tag_toggle_style()
        # 2026-09-12（用户要求 3）："🗂"右侧新增"打开/关闭浮动提示窗口"快捷按钮（无文字标签）。
        # 开启=蓝色、关闭=灰色；作用于"条目名称一览"浮层与"详情区字段内容"浮动提示两处。
        self.tip_toggle_btn = ctk.CTkButton(
            self.detail_foot, text="💬", width=32, height=32,
            fg_color="#2f6fb0", hover_color="#255a92", font=("Microsoft YaHei", 15),
            command=self._toggle_float_tips)
        self.tip_toggle_btn.pack(side="left", padx=(6, 0), pady=4)
        # 2026-09-12（用户要求 3/4）：两个快捷按钮的浮动提示文字
        _FieldTooltip(self.tip_toggle_btn, "打开/关闭浮动提示窗口")
        _FieldTooltip(self.dir_toggle_btn, "打开/关闭四级目录")
        _FieldTooltip(self.tag_toggle_btn, "打开/关闭标签页面")
        self.recycle_btn = ctk.CTkButton(
            self.detail_foot, text="♻ 删除历史 / 回收站", width=139, height=32,
            fg_color="#6b7280", hover_color="#575e68",
            command=self._open_recycle)
        self.recent_btn = ctk.CTkButton(
            self.detail_foot, text="🕒 新增历史", width=83, height=32,
            fg_color="#1f6f8f", hover_color="#185a73",
            command=self._open_recent_adds)
        # pack(side="right") 先打包者最靠右：删除当前条目 → 删除历史/回收站 → 新增历史
        self.del_btn.pack(side="right", padx=(4, 8), pady=4)
        self.recycle_btn.pack(side="right", padx=4, pady=4)
        self.recent_btn.pack(side="right", padx=4, pady=4)

    def _build_statusbar(self) -> None:
        self.status_label = ctk.CTkLabel(self, text="", anchor="w", height=24)
        self.status_label.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
        self._status_default()  # 2026-08-18（第017条）：状态栏默认显示统计（总提示词数/一级目录数/二级目录数/条目数）
        if self.startup_warning:
            self.status_label.configure(text=f"● 备份失败：{self.startup_warning}")

    # ------------------------------------------------------------------ #
    # 状态栏统计（2026-08-18 第017条：按鼠标悬停层级动态显示）
    # ------------------------------------------------------------------ #
    def _cat_entry_count(self, cat_id: int) -> int:
        """分类及其全部子分类下的条目总数（直挂 + 子树递归；2026-08-29 复审优化：COUNT 不加载行）"""
        total = self.db.count_entries(cat_id)
        for sub in self.db.list_categories(parent_id=cat_id):
            total += self._cat_entry_count(sub["id"])
        return total

    def _domain_entry_count(self, domain_id: int) -> int:
        """根目录下全部提示词总数（其关联的所有一级分类子树）"""
        total = 0
        for c in self.db.list_categories(domain_id=domain_id, parent_id=None):
            total += self._cat_entry_count(c["id"])
        return total

    def _status_default(self) -> None:
        """无选择状态：总提示词数 / 项目类别 / 根目录 / 一级 / 二级目录数"""
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        np_ = len(self.db.list_projects())
        nd = len(self.db.list_domains())
        l1 = len(self.db.list_categories(parent_id=None))
        l2 = 0
        for c in self.db.list_categories(parent_id=None):
            l2 += len(self.db.list_categories(parent_id=c["id"]))
        self.status_label.configure(
            text=f"总提示词数 {total}｜项目类别 {np_}｜根目录 {nd}｜一级目录 {l1}｜二级目录 {l2}")

    def _status_hover_project(self, project_id: Optional[int]) -> None:
        """悬停项目类别：项目名 + 根目录数 + 提示词总数（"未分配"显示无归属统计）"""
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        if project_id is None:
            domains = self.db.list_unassigned_domains()
            name = "未分配"
        else:
            p = self.db.get_project(project_id)
            if not p:
                return
            name = p["name"]
            domains = self.db.list_domains(project_id=project_id)
        n = sum(self._domain_entry_count(d["id"]) for d in domains)
        self.status_label.configure(
            text=f"总提示词数 {total}｜项目【{name}】根目录 {len(domains)} 个｜提示词 {n}")

    def _status_hover_domain(self, domain_id: int) -> None:
        """悬停根目录：总提示词数 + 当前根目录名称和该项下提示词总数 + 所属项目"""
        d = self.db.get_domain(domain_id)
        if not d:
            return
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        n = self._domain_entry_count(domain_id)
        pname = ""
        if d.get("project_id"):
            p = self.db.get_project(d["project_id"])
            pname = f"｜项目【{p['name']}】" if p else ""
        self.status_label.configure(
            text=f"总提示词数 {total}｜根目录【{d['name']}】提示词 {n}{pname}")

    def _status_hover_cat(self, cat_id: int) -> None:
        """悬停一级/二级分类：所属根目录 + 一级（+二级）统计"""
        cat = self.db.get_category(cat_id)
        if not cat:
            return
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        root = self.db.category_root(cat_id)          # 一级分类 id（category_root 返回 id）
        doms = self.db.linked_domains(root) if root else []
        dom_name = doms[0]["name"] if doms else ""
        dom_n = self._domain_entry_count(doms[0]["id"]) if doms else 0
        l1_cat = self.db.get_category(root) if root else None
        l1_name = l1_cat["name"] if l1_cat else ""
        l1_n = self._cat_entry_count(root) if root else 0
        text = f"总提示词数 {total}｜根目录【{dom_name}】提示词 {dom_n}｜一级【{l1_name}】提示词 {l1_n}"
        if cat["parent_id"] is not None:              # 二级分类
            l2_n = self._cat_entry_count(cat_id)
            text += f"｜二级【{cat['name']}】提示词 {l2_n}"
        self.status_label.configure(text=text)

    def _status_hover_entry(self, e: dict) -> None:
        """悬停条目：链路统计（根目录/一级/二级）+ 本条目的名称"""
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        cat_id = e.get("category_id")
        name = (e.get("name") or "").strip()
        if not cat_id:                                 # 未分类条目
            self.status_label.configure(
                text=f"总提示词数 {total}｜未分类条目【{name}】")
            return
        root = self.db.category_root(cat_id)          # 一级分类 id
        doms = self.db.linked_domains(root) if root else []
        dom_name = doms[0]["name"] if doms else ""
        dom_n = self._domain_entry_count(doms[0]["id"]) if doms else 0
        l1_cat = self.db.get_category(root) if root else None
        l1_name = l1_cat["name"] if l1_cat else ""
        l1_n = self._cat_entry_count(root) if root else 0
        text = f"总提示词数 {total}｜根目录【{dom_name}】提示词 {dom_n}｜一级【{l1_name}】提示词 {l1_n}"
        cat = self.db.get_category(cat_id)
        if cat and cat["parent_id"] is not None:       # 条目挂在二级分类下
            l2_n = self._cat_entry_count(cat_id)
            text += f"｜二级【{cat['name']}】提示词 {l2_n}"
        text += f"｜条目【{name}】"
        self.status_label.configure(text=text)

    @staticmethod
    def _clear_frame(frame) -> None:
        for child in frame.winfo_children():
            child.destroy()

    @staticmethod
    def _scroll_top(frame) -> None:
        """把 CTkScrollableFrame 的垂直滚动复位到顶部（2026-09-07 第4条改进）"""
        try:
            canvas = getattr(frame, "_parent_canvas", None)
            if canvas is not None and canvas.winfo_exists():
                canvas.yview_moveto(0)
        except Exception:
            pass

    @staticmethod
    def _nav_hint(frame, text: str) -> None:
        """在某导航/条目列内放一条居中提示（用于尚无可用内容的空/初始视图）"""
        ctk.CTkLabel(frame, text=text, text_color="#9aa4b1",
                     font=("Microsoft YaHei", 11), justify="center",
                     anchor="center", wraplength=150,
                     ).pack(fill="x", padx=10, pady=(14, 2))

    # ------------------------------------------------------------------ #
    # 悬停选中（主界面导航/条目采用"鼠标悬浮即选择"，与快捷新建一致）
    # ------------------------------------------------------------------ #
    def _schedule_select(self, ms: int, fn) -> None:
        self._cancel_select()
        self._select_timer = self.after(ms, fn)

    def _cancel_select(self) -> None:
        if self._select_timer is not None:
            try:
                self.after_cancel(self._select_timer)
            except Exception:
                pass
            self._select_timer = None

    # ------------------------------------------------------------------ #
    # 根目录 / 分类导航
    # ------------------------------------------------------------------ #
    def refresh_domains(self, silent: bool = False) -> None:
        """（重）渲染导航列并复位。

        silent=True（2026-08-18，P1-2 修复）：跳过"未保存修改"检查、保留详情区当前状态，
        供快捷新建保存后调用，避免弹出未保存确认框打断连续录入。

        2026-09-07（第1条改进）：打开软件后默认【不自动选中任何层级】——仅顶级
        "项目类别"列展示选项，其余列给出提示，由用户逐级选择后再逐列展开，
        不再出现"尚未选择就整列灌满全部分类"的旧行为。
        """
        if not silent and not self._confirm_unsaved():
            return
        # 未选择任何层级时（首启/结构刷新），各列刷新函数会自动：只给提示 + “新增”按钮灰显；
        # 用户逐级选择后按钮随可用条件自动点亮、对应列填充内容。
        self._nav_initialized = True
        self._refresh_projects()
        self._refresh_l0()
        self._refresh_l1()
        self._refresh_l2()
        self._render_entries([], "条目")
        if not silent:
            self._show_detail(None)

    # ---- 导航按钮引用（选中高亮原地更新，不销毁重建，杜绝悬停闪烁） ----
    def _clear_nav_btns(self, col: str) -> None:
        if col == "p":
            self._p_btns, self._p_styles = {}, {}
        elif col == "l0":
            self._l0_btns, self._l0_styles = {}, {}
        elif col == "l1":
            self._l1_btns, self._l1_styles = {}, {}
        else:
            self._l2_btns, self._l2_styles = {}, {}

    @staticmethod
    def _style_nav_btn(btn, selected: bool, orig) -> None:
        """选中态：深蓝底白字；未选中：恢复创建时的原始配色（不能传 None）"""
        if selected:
            try:
                btn.configure(fg_color=_SEL_BTN["fg_color"],
                              hover_color=_SEL_BTN["hover_color"],
                              text_color=_SEL_BTN["text_color"])
            except Exception:
                pass  # 2026-09-07：按钮可能已被重建销毁，忽略（避免残留引用崩溃）
        else:
            if orig is None:
                # 2026-08-18（P1-4 修复）：样式字典缺失该按钮时保持当前样式，避免解包 None 崩溃
                return
            fg, hover, text = orig
            try:
                btn.configure(fg_color=fg, hover_color=hover, text_color=text)
            except Exception:
                pass

    def _apply_nav_highlight(self) -> None:
        """原地更新四列选中高亮"""
        l1_sel = self._l1_highlight_id()
        l2_sel = self._l2_highlight_id()
        for pid, btn in self._p_btns.items():
            self._style_nav_btn(btn, pid == self._cur_project_id, self._p_styles.get(pid))
        for cid, btn in self._l0_btns.items():
            self._style_nav_btn(btn, cid == self._cur_domain_id, self._l0_styles.get(cid))
        for cid, btn in self._l1_btns.items():
            self._style_nav_btn(btn, cid == l1_sel, self._l1_styles.get(cid))
        for cid, btn in self._l2_btns.items():
            self._style_nav_btn(btn, cid == l2_sel, self._l2_styles.get(cid))

    @staticmethod
    def _attach_nav_tooltip(btn, name: str, budget: int) -> None:
        """长名称悬停提示：名称长度超过列宽预算时附加 tooltip（2026-08-29 UI 优化）。
        budget：该列可显示的大致汉字数（项目/根目录≈8，一级/二级/条目≈12）。
        2026-09-17（审核 R-3）：改用 `ui_common.maybe_tooltip`（与快速新建窗口同一实现）。
        """
        _ui_common.maybe_tooltip(btn, name, budget)

    # ------------------------------------------------------------------ #
    # 分类列"上移/下移"（2026-09-11 08:52 用户要求 2）
    # ------------------------------------------------------------------ #
    def _column_ids(self, kind: str) -> List[int]:
        """某分类列当前可见项的 id 顺序（必须与对应 _refresh_* 的渲染顺序一致）。

        kind：project=项目类别列 / domain=根目录列 / l1=一级分类列 / l2=二级分类列。
        """
        if kind == "project":
            return [p["id"] for p in self.db.list_projects()]
        if kind == "domain":
            rows = (self.db.list_unassigned_domains() if self._cur_project_id is None
                    else self.db.list_domains(project_id=self._cur_project_id))
            return [d["id"] for d in rows]
        if kind == "l1":
            if self._cur_domain_id is None:
                return []
            return [c["id"] for c in self.db.list_categories(
                domain_id=self._cur_domain_id, parent_id=None)]
        parent_id = self._l2_parent_id()
        if parent_id is None:
            return []
        return [c["id"] for c in self.db.list_categories(parent_id=parent_id)]

    def _add_move_menu_items(self, menu, kind: str, item_id: int, lock_state: str) -> None:
        """在右键菜单末尾追加"上移/下移"两项（已到列首/列尾则置灰）"""
        ids = self._column_ids(kind)
        idx = ids.index(item_id) if item_id in ids else -1
        can = (lock_state == "normal" and idx >= 0)
        menu.add_separator()
        menu.add_command(label="⬆ 上移", state=("normal" if (can and idx > 0) else "disabled"),
                         command=lambda: self._move_in_column(kind, item_id, -1))
        menu.add_command(label="⬇ 下移",
                         state=("normal" if (can and idx < len(ids) - 1) else "disabled"),
                         command=lambda: self._move_in_column(kind, item_id, +1))

    def _move_in_column(self, kind: str, item_id: int, delta: int) -> None:
        """把某分类列内的项上移/下移一格，随后重建该列（保持选中高亮）"""
        if self._lock_on:
            return
        table = "projects" if kind == "project" else ("domains" if kind == "domain"
                                                     else "categories")
        if not self.db.swap_order(table, self._column_ids(kind), item_id, delta):
            self.toast("已在最" + ("上" if delta < 0 else "下") + "端", color=_C_DANGER)
            return
        if kind == "project":
            self._refresh_projects()
        elif kind == "domain":
            self._refresh_l0()
        elif kind == "l1":
            self._refresh_l1()
        else:
            self._refresh_l2()
        self.toast("已上移" if delta < 0 else "已下移")

    # ------------------------------------------------------------------ #
    # 项目类别（四级分类最高层级，2026-08-29 M2 新增）
    # ------------------------------------------------------------------ #
    def _refresh_projects(self) -> None:
        """渲染项目类别列（含"新增"按钮、"未分配"虚拟项）"""
        self._clear_frame(self.project_frame)
        self._clear_nav_btns("p")
        # 2026-09-22（用户要求 1）：按钮已固定在列顶（见 _build_body）→ 此处只更新启用状态
        if self._add_proj_btn is not None and self._add_proj_btn.winfo_exists():
            self._add_proj_btn.configure(state="disabled" if self._lock_on else "normal")
        for p in self.db.list_projects():
            btn = ctk.CTkButton(self.project_frame, text=p["name"], anchor="w", height=32,
                                command=lambda pid=p["id"]: self._select_project(pid))
            btn.pack(fill="x", padx=6, pady=2)
            self._p_btns[p["id"]] = btn
            self._p_styles[p["id"]] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                        btn.cget("text_color"))
            btn.bind("<Button-3>",
                     lambda e=None, pid=p["id"], n=p["name"]: self._project_menu(e, pid, n))
            btn.bind("<Enter>",
                     lambda _e=None, pid=p["id"]: (self._schedule_select(
                         _HOVER_SELECT_MS, lambda: self._select_project(pid)),
                         self._status_hover_project(pid)))
            btn.bind("<Leave>", lambda _e=None: (self._cancel_select(),
                                            self._status_default()))
            self._attach_nav_tooltip(btn, p["name"], 6)  # 2026-08-29：长名称悬停提示
        # "未分配"虚拟项（存在无归属根目录时显示）
        if self.db.list_unassigned_domains():
            btn = ctk.CTkButton(self.project_frame, text="🗂 未分配", anchor="w", height=32,
                                command=lambda: self._select_project(None))
            btn.pack(fill="x", padx=6, pady=2)
            self._p_btns[None] = btn
            self._p_styles[None] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                    btn.cget("text_color"))
            btn.bind("<Button-3>", lambda e=None: self._project_menu(e, None, "未分配"))
            btn.bind("<Enter>", lambda _e=None: (self._schedule_select(
                _HOVER_SELECT_MS, lambda: self._select_project(None)),
                self._status_hover_project(None)))
            btn.bind("<Leave>", lambda _e=None: (self._cancel_select(),
                                            self._status_default()))
        self._apply_nav_highlight()
        if not self.db.list_projects() and not self.db.list_unassigned_domains():
            self._nav_hint(self.project_frame, "（暂无项目类别，请点上方新增）")
        self._scroll_top(self.project_frame)

    def _select_project(self, project_id: Optional[int]) -> None:
        """选择项目类别（None=未分配视图）：列出其根目录列，再逐级展开。

        2026-09-07（第1/2条改进）：不整列重建项目类别列——内容未变时仅原地更新高亮，
        悬浮切换更流畅；未选择根目录前，一级分类列只给提示、不再灌入全库分类。
        """
        if (self._view == ("project", project_id)
                and self._cur_project_id == project_id
                and self._cur_domain_id is None):
            return
        self._cur_project_id = project_id
        self._cur_domain_id = None
        self._cur_cat_id = None
        self._view = ("project", project_id)
        self._apply_nav_highlight()   # 2026-09-07：原地高亮，避免整列重建拖慢悬浮
        self._refresh_l0()
        self._refresh_l1()
        self._refresh_l2()            # 未选一级分类 → 显示"请先选一级分类"提示
        self._render_entries([], "条目")

    def _on_dir_toggle(self) -> None:
        """「目录隐藏 / 目录显示」按钮：打开对话框选择隐藏或显示各分类列（2026-09-10）。

        - 当前无隐藏列（_nav_hidden == 0，按钮显示"目录隐藏"）→ 打开"隐藏"对话框；
        - 当前有隐藏列（_nav_hidden > 0，按钮显示"目录显示"）→ 打开"显示"对话框。
        """
        mode = "hide" if self._nav_hidden == 0 else "show"
        ColumnVisibilityDialog(self, self._nav_hidden, mode, self.apply_nav_visibility)

    # ------------------------------------------------------------------ #
    # 条目区宽度可拖拽调整（2026-09-22，用户要求 2）
    #   拖动条目区右缘的"分隔条"即可调整条目区宽度（右侧详情区自动让位）；
    #   宽度**只存在内存、不写任何设置** ⇒ 无论上次调到多少，下次启动都按默认值
    #   `_ENTRY_COL_W`（224）显示，**不记忆上次关闭时的宽度**。
    # ------------------------------------------------------------------ #
    def _entry_grip_press(self, event) -> None:
        """记录拖拽起点：起始鼠标 x 与条目区**总宽**（列容器当前实际宽度）"""
        self._entry_grip_x0 = int(event.x_root)
        try:
            self._entry_grip_w0 = int(self.entry_col.winfo_width())
        except Exception:                                  # noqa: BLE001
            self._entry_grip_w0 = _ENTRY_COL_W

    def _entry_grip_drag(self, event) -> None:
        """拖动分隔条 → 按鼠标位移调整条目区**总宽**（钳制在上下限内）

        2026-09-22 修复（用户反馈"看起来能拖、实际拖不动"）：列容器已用
        `pack_propagate(False)` 锁宽 ⇒ **只改滚动区宽度不会改变列宽**；
        故必须**同时改列容器宽度**（列宽 = 容器宽度），滚动区宽度 = 总宽 − 分隔条宽。
        """
        if getattr(self, "_entry_grip_x0", None) is None:
            return
        _delta = int(event.x_root) - int(self._entry_grip_x0)
        _scale = _ui_common.widget_scaling(self.entry_col) or 1.0
        _total_src = (getattr(self, "_entry_grip_w0", _ENTRY_COL_W) + _delta) / _scale
        _total_src = max(_ENTRY_W_MIN, min(_total_src, _ENTRY_W_MAX))
        try:
            self.entry_col.configure(width=_total_src)                       # 列宽（关键）
            self.entry_frame.configure(width=max(_total_src - _ENTRY_GRIP_W, 1))
        except Exception:                                  # noqa: BLE001
            pass

    def _nav_min_width(self) -> int:
        """按"连续隐藏的分类列宽度"计算当前窗口最小宽度（2026-09-10，用户要求 3）。

        2026-09-14（用户要求"详情区最小宽"，并允许窗口缩得更窄）：
        改为**按隐藏列数查实测标定表** `_NAV_MIN_WIDTH_BY_HIDDEN`（1396/1263/1129/940/886），
        使详情区在各状态下都收窄到目标值（**现为 584**；原"用设定列宽做减法"会算偏大 → 详情区被撑宽）。
        下限 `_MIN_WIDTH_FLOOR`(886) 为"窗口宽度以工具栏需要为准"的实测下限，作为兜底。

        另（"所有状态下详情区 ≥ 目标值"）：**标签页面打开**时左侧被标签页面占用
        （`_body` 第 0 列 minsize = 560），需同时不低于 `_TAG_PAGE_MIN_WIDTH`（1277）。
        ⚠ 2026-09-15（审核 L-1）：以上括号内的旧数值（1368/1234/1101/912/773、773、1250）已按当前常量更正。
        """
        h = max(0, min(int(getattr(self, "_nav_hidden", 0)), len(_NAV_MIN_WIDTH_BY_HIDDEN) - 1))
        base = _NAV_MIN_WIDTH_BY_HIDDEN[h]
        if getattr(self, "_tag_page_on", False):
            base = max(base, _TAG_PAGE_MIN_WIDTH)
        return max(base, _MIN_WIDTH_FLOOR)

    def apply_nav_visibility(self, hidden_count: int) -> None:
        """按"从项目类别起连续隐藏的列数"显示/隐藏左侧四个分类列并更新按钮文案（2026-09-10）。

        hidden_count：0~4。0 = 四列全显示（按钮"目录隐藏"）；>0 = 按钮显示"目录显示"。
        """
        hidden_count = max(0, min(len(self._nav_cols), int(hidden_count)))
        # 2026-09-13（1-C-3）：标签页面与四级目录**互斥**——恢复目录显示（hidden_count==0）
        # 即视为关闭标签页面（避免两者同时占用左侧同一区域）。此处不递归调用本方法。
        if hidden_count == 0 and getattr(self, "_tag_page_on", False):
            self._tag_page_on = False
            try:
                self.tag_frame.grid_remove()
                self._body.grid_columnconfigure(0, minsize=0)   # 交还列宽给四级目录
            except Exception:
                pass
            self._apply_tag_toggle_style()
            # 2026-09-14（审核修复 P3）：经"目录显示"隐式关闭标签页面时，
            # 也要像底部"🏷"按钮那样恢复打开前的条目视图（此前两条关闭路径行为不一致）。
            self._restore_view_after_tag_page()
        self._nav_hidden = hidden_count
        for idx, frame in enumerate(self._nav_cols):
            if idx < hidden_count:
                frame.grid_remove()                                  # 隐藏：列宽自动收缩为 0
            else:
                frame.grid(row=0, column=idx, sticky="nsew")         # 显示：恢复原列位
        self.btn_project_toggle.configure(
            text="🗂 目录显示" if hidden_count > 0 else "🗂 目录隐藏")
        self._apply_dir_toggle_style()   # 2026-09-12（用户要求 3）：详情区"🗂"按钮绿/黄两态
        # 2026-09-10（用户要求 3）：按被隐藏的列宽同步降低窗口最小宽度，
        # 使用户可把窗口（连同右侧详情区）缩得更窄；恢复显示时自动还原。
        # 2026-09-14（用户要求）：最小高度取"屏幕可用高度"与 _BASE_MIN_HEIGHT 的较小者。
        self.minsize(self._nav_min_width(), self._win_min_height())
        # 2026-09-14（用户要求，本轮）：**四个分类区全关闭时，窗口自动收缩到"最小宽度"**
        #   （此时工具栏也处于最窄形态：搜索框下移到第二行）。
        #   注：打开"标签页面"内部也会调用本方法（hidden=4），但那不是"关闭四区"，
        #      故用 `_tag_page_on` 排除，避免打开标签页面时窗口被缩窄。
        if hidden_count >= len(self._nav_cols) and not getattr(self, "_tag_page_on", False):
            self._shrink_window_to_min_width()

    def _shrink_window_to_min_width(self) -> None:
        """把窗口**宽度**收缩到当前最小宽度（**高度保持不变**）。2026-09-14 用户要求。

        ⚠ 2026-09-14 20:52（用户报告"窗口高度被增大、下面看不到" → 定位到的根因修复）：
        原实现用 `self._apply_window_scaling(1)` 求缩放系数（本机返回 **1**，并非真实几何缩放 1.2），
        再把 `winfo_height()`（**实际像素**）当作**源码值**传回 `geometry()`——而 CTk 的
        `geometry()` 会再乘一次窗口缩放 ⇒ **高度被放大 1.2 倍**，且每触发一次就再放大一次
        （934 → 1119 → 1340…）→ 窗口底边跑出屏幕。
        现改为：**宽高都从 `self.geometry()` 读取源码值**（CTk 的 getter 已按缩放还原），
        只替换其中的宽度 → 高度严格不变；末尾再调用 `_fit_window_to_screen()` 兜底。
        """
        try:
            src = self.geometry().split("+")[0]            # 源码值，如 "1480x780"
            w_now, h_now = (int(v) for v in src.split("x")[:2])
            w_src = max(int(self._nav_min_width()), 1)
            if w_now <= w_src:                             # 已经不比最小宽更宽 → 不动
                return
            self.geometry(f"{w_src}x{h_now}")              # 高度用原源码值，不再换算
        except Exception as exc:                           # 2026-09-15（审核 L-7）：不再静默
            _geom_warn("收缩窗口宽度", exc)
        self._fit_window_to_screen()

    def _window_scale(self) -> float:
        """当前窗口的几何缩放系数（源码值 → 实际像素）。2026-09-14（高度问题）新增。

        CTk 的 `geometry()` 读写都按"窗口缩放"换算：getter 返回**源码值**，
        `winfo_width()/winfo_height()` 返回**实际像素**，两者相除即本机缩放系数。

        2026-09-15（审核 L-7B"缩放缓存加固"）：成功时把结果缓存到 `self._scale_cache`；
        取不到时**优先用缓存值**，无缓存才退回 1.0——避免用错误缩放算出"已在屏内"而跳过夹取。
        注：上一版注释里提到的 `_apply_window_scaling(1)` 已不再使用（缩放系数改由本方法实测）。
        """
        try:
            w_src = int(self.geometry().split("+")[0].split("x")[0])
            act = int(self.winfo_width())
            if w_src > 0 and act > 1:
                self._scale_cache = act / w_src            # 成功 → 缓存（供异常时兜底）
                return self._scale_cache
        except Exception as exc:                           # 2026-09-15（审核 L-7）：不再静默
            _geom_warn("取窗口缩放系数", exc)
        if self._scale_cache:                              # L-7B：失败但有缓存 → 用缓存
            return self._scale_cache
        return 1.0

    def _max_fit_height_src(self) -> int:
        """窗口高度（源码值）上限 = 屏幕高度 − 上下留白（任务栏/标题栏，共 80px）。2026-09-14。"""
        try:
            return max(int((int(self.winfo_screenheight()) - 80) / max(self._window_scale(), 0.01)), 200)
        except Exception as exc:                           # 2026-09-15（审核 L-7）：不再静默
            _geom_warn("算窗口高度上限", exc)
            return _BASE_MIN_HEIGHT

    def _win_min_height(self) -> int:
        """窗口最小高度（源码值）：不超过屏幕可用高度，确保"缩到最小也能整窗可见"。2026-09-14。

        ⚠ 命名注意：不能叫 `_min_height`——CTk 自己用 `self._min_height` 保存最小高度（int），
        会遮蔽同名方法（实测报 `'int' object is not callable`）。
        """
        return max(min(_BASE_MIN_HEIGHT, self._max_fit_height_src()), 200)

    def _fit_window_to_screen(self) -> None:
        """确保窗口**完全落在屏幕内**（重点：**底边不越界**）。2026-09-14 用户要求。

        场景：① 恢复的窗口尺寸高于屏幕可用高度；② 缩放导致高度被放大（见 `_shrink_window_to_min_width`）。
        做法：先把宽/高（源码值）收敛到屏幕可用范围（下边留 40px、上边留 40px），
        再把窗口位置夹回屏幕内；CTk 的 `geometry()` 承担缩放换算。
        """
        try:
            if not self.winfo_viewable():
                return
            self.update_idletasks()
            sw, sh = int(self.winfo_screenwidth()), int(self.winfo_screenheight())
            scale = max(self._window_scale(), 0.01)
            # 2026-09-15（审核 L-7B）：缩放取值不可信（无任何成功缓存）时**跳过夹取**，
            #   避免用错误缩放算出"已在屏内"而不做任何处理（原先会静默按 1.0 硬算）。
            if not self._scale_cache:
                _geom_warn("屏幕夹取", "缩放系数不可信（无可用缓存）→ 跳过本夹取")
                return
            w_src, h_src = (int(v) for v in self.geometry().split("+")[0].split("x")[:2])
            max_w_src = int(max(sw - 40, 200) / scale)
            max_h_src = int(max(sh - 80, 200) / scale)
            new_w, new_h = min(w_src, max_w_src), min(h_src, max_h_src)
            if (new_w, new_h) != (w_src, h_src):
                self.geometry(f"{new_w}x{new_h}")
            w_act, h_act = int(new_w * scale), int(new_h * scale)
            x = min(max(int(self.winfo_x()), 0), max(sw - w_act, 0))
            # 下边留白统一用常量（2026-09-15 审核 L-7：抽常量，**数值与原字面量 40 一致**）
            y = min(max(int(self.winfo_y()), 0), max(sh - h_act - _SCREEN_BOTTOM_MARGIN, 0))
            self.geometry(f"+{x}+{y}")
        except Exception as exc:                           # 2026-09-15（审核 L-7）：不再静默
            _geom_warn("屏幕夹取", exc)

    def _apply_dir_toggle_style(self) -> None:
        """按"四级目录是否显示"刷新详情区左下"🗂"按钮配色（2026-09-12，用户要求 3）。

        目录打开（四列显示，_nav_hidden == 0）＝绿色；目录关闭（四列隐藏）＝黄色。
        与工具栏"目录隐藏/目录显示"共用 apply_nav_visibility 这一唯一入口，故两处始终同步。
        """
        opened = self._nav_hidden == 0
        try:
            self.dir_toggle_btn.configure(
                fg_color=(_DIR_OPEN_BG if opened else _DIR_CLOSED_BG),
                hover_color=(_DIR_OPEN_HOVER if opened else _DIR_CLOSED_HOVER))
        except Exception:
            pass

    def _toggle_all_dirs(self) -> None:
        """详情区底部"🗂"小图标快捷按钮：一键全部隐藏 / 全部显示各分类列（2026-09-10，用户要求 二）。

        当前有隐藏列 → 全部显示；四列全显示 → 全部隐藏。与工具栏按钮共用 apply_nav_visibility。
        """
        if self._nav_hidden > 0:
            self.apply_nav_visibility(0)                       # 有隐藏（含部分隐藏）→ 全部显示
        else:
            self.apply_nav_visibility(len(self._nav_cols))     # 四列全显示 → 全部隐藏

    def _toggle_float_tips(self) -> None:
        """打开/关闭浮动提示窗口（2026-09-12，用户要求 3）。

        作用于"条目名称一览"浮层与"详情区字段内容"浮动提示两处；关闭时立即收起已显示的浮层。
        不影响按钮/导航长名称提示（用户 2026-09-12 确认：只关这两类）。
        """
        self._float_tips_on = not self._float_tips_on
        if not self._float_tips_on:
            self._hide_entry_overview()
            self._hide_field_tip()
        self._apply_tip_toggle_style()

    def _apply_tip_toggle_style(self) -> None:
        """按总开关状态刷新"💬"按钮配色（开=蓝、关=灰）（2026-09-12，用户要求 3）。"""
        on = self._float_tips_on
        try:
            self.tip_toggle_btn.configure(
                fg_color=("#2f6fb0" if on else "#9aa4b1"),
                hover_color=("#255a92" if on else "#7c8591"))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 标签页面（2026-09-13，1-C-3）：与"四级目录"互斥，占用左侧同一区域
    # ------------------------------------------------------------------ #
    def _toggle_tag_page(self) -> None:
        """工具栏"🏷 标签"按钮 与 详情区左下"🏷"快捷按钮 共用：打开/关闭标签页面。"""
        if self._tag_page_on:
            self._close_tag_page()
        else:
            self._open_tag_page()

    def _open_tag_page(self) -> None:
        """打开标签页面：先关闭四级目录（复用 apply_nav_visibility 唯一入口），再显示页面容器。"""
        self._tag_page_on = True
        self._view_before_tag_page = self._view     # 记住原条目视图，关闭时恢复
        self.apply_nav_visibility(len(self._nav_cols))   # 等价"全部隐藏四级目录"
        self.tag_frame.grid(row=0, column=0, columnspan=4, sticky="nsew")
        # 跨 4 列的控件在 Tk 中会被压缩：显式给第 0 列最小宽度＝四列合计，
        # 使标签页面与"四级目录"同宽（否则实测只剩约 314px）。
        try:
            self._body.grid_columnconfigure(0, minsize=sum(_NAV_COL_WIDTHS))
        except Exception:
            pass
        self._refresh_tag_page()
        self._apply_tag_toggle_style()

    def _close_tag_page(self) -> None:
        """关闭标签页面：隐藏容器并**恢复四级目录显示**（用户确认的默认行为）。"""
        self._tag_page_on = False
        self._cancel_tag_search_timer()   # 2026-09-15（用户要求 8）：关闭时取消未到点的搜索防抖
        try:
            self.tag_frame.grid_remove()
            self._body.grid_columnconfigure(0, minsize=0)   # 交还列宽给四级目录
        except Exception:
            pass
        self.apply_nav_visibility(0)   # 恢复四列显示（其中已含互斥收尾与配色刷新）
        self._apply_tag_toggle_style()
        # 2026-09-13（1-C-3b）：恢复打开标签页面前的条目视图
        self._restore_view_after_tag_page()

    def _restore_view_after_tag_page(self) -> None:
        """关闭标签页面后恢复"打开前"的条目视图（2026-09-14，审核修复 P3）。

        两条关闭路径（底部"🏷"按钮 / 经"目录显示"隐式关闭）共用本方法，避免行为不一致。
        """
        if self._view_before_tag_page is not None:
            self._view = self._view_before_tag_page
            self._view_before_tag_page = None
            self._restore_view()

    def _apply_tag_toggle_style(self) -> None:
        """按标签页面开关刷新"🏷"按钮配色（打开＝紫、关闭＝橙）（2026-09-13，1-C-3）。"""
        on = bool(self._tag_page_on)
        try:
            self.tag_toggle_btn.configure(
                fg_color=(_TAG_OPEN_BG if on else _TAG_CLOSED_BG),
                hover_color=(_TAG_OPEN_HOVER if on else _TAG_CLOSED_HOVER))
        except Exception:
            pass

    def _refresh_tag_page(self) -> None:
        """重建标签页面（2026-09-13，1-C-3b；2026-09-14 阶段 4 新增「热点词」、阶段 0.5 新增「词表」）。

        结构（自上而下）：标题 → 顶部（搜索 / 四档切换 / ＋新建）→ 已选栏（仅标签档）
        → 主区（标签云 / 列表+计数徽标 / 热点词管理 / 词表结构预览，可滚动）→ 底部管理
        （标签档：重命名 / 合并 / 删除 / 清理未使用；热点词档：文本导入 / 热点词更新 / 清空全部；
          词表档：导出 / 导入 / 恢复出厂）。
        **只有标签档会刷新条目区**；热点词档与词表档均不改动条目区（互不干扰）。
        """
        tf = getattr(self, "tag_frame", None)
        if tf is None or not tf.winfo_exists():
            return
        # 2026-09-15（用户要求 8）：重建页面前先取消"未到点"的搜索防抖，避免其打到已销毁的控件上
        self._cancel_tag_search_timer()
        # 2026-09-15（批次 6-2）：重建前先清空写操作按钮引用，避免残留已销毁控件的句柄
        self._tag_gov_btns = []
        for w in tf.winfo_children():
            w.destroy()

        mode = self._tag_page_view            # "cloud" / "list" / "hot" / "dict"
        is_hot = (mode == "hot")              # 2026-09-14（4-b）：热点词档
        is_dict = (mode == "dict")            # 2026-09-14（0.5）：词表档
        is_tag = not (is_hot or is_dict)      # 标签云 / 列表 两档（保持既有行为不变）

        # 清理失效选择（标签可能被删除/改名）
        try:
            valid = {t["name"] for t in self.db.list_tags()}
        except Exception:
            valid = set()
        self._tag_page_selected = [n for n in self._tag_page_selected if n in valid]

        _title = {"hot": "🔥 热点词（供自动打标使用）",
                  "dict": "📚 词表（通用骨架 + 领域维度包）"}.get(mode)
        ctk.CTkLabel(tf, text=(_title or "🏷 标签页面"), text_color=_TAG_OPEN_BG,
                     font=("Microsoft YaHei", 15, "bold"), anchor="w"
                     ).pack(fill="x", padx=14, pady=(12, 4))
        # 2026-09-18（用户要求）：**移除词表"规模预警"提示**（原 FR-96，阈值 500）。
        #   用户的新出厂词表为 809 个标签且还会继续扩充，该提示属"提醒而非限制"、无实际作用，
        #   按用户要求整体去除（`tagger.dict_size_hint` / `DICT_WARN_TAGS` 一并删除）。

        # ---- 顶部：搜索（词表档不显示）/ 四档切换 / ＋新建（仅标签档）----
        top = ctk.CTkFrame(tf, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(0, 4))
        if not is_dict:
            self._tag_search_entry = ctk.CTkEntry(
                top, placeholder_text=("搜索热点词…" if is_hot else "搜索标签…"), height=28)
            self._tag_search_entry.pack(side="left", fill="x", expand=True)
            _kw = self._hotword_search if is_hot else self._tag_page_search
            if _kw:
                self._tag_search_entry.insert(0, _kw)
            self._tag_search_entry.bind(
                "<KeyRelease>",
                self._on_hotword_search_typed if is_hot else self._on_tag_search_typed)
            # 2026-09-17（用户要求 2）：与主界面搜索框一致，右侧新增"✕"**一键清除**按钮
            #   （标签档 / 热点词档共用同一控件；点击只清关键词并重建主区，不重建本输入框）。
            self._tag_search_clear_btn = ctk.CTkButton(
                top, text="✕", width=20, height=28, corner_radius=5,
                fg_color="#e8ecf1", hover_color="#d5dce5", text_color="#1f2937",
                font=("Microsoft YaHei", 12), command=self._on_tag_search_clear)
            self._tag_search_clear_btn.pack(side="left", padx=(2, 0))
        seg_v = ctk.CTkSegmentedButton(top, values=["标签云", "列表", "热点词", "词表"], width=252,
                                       command=self._on_tag_view_change)
        seg_v.set({"cloud": "标签云", "list": "列表", "hot": "热点词", "dict": "词表"}
                  .get(mode, "标签云"))
        seg_v.pack(side="left", padx=(0 if is_dict else 6, 0))
        if is_tag:
            ctk.CTkButton(top, text="＋ 新建", width=64, fg_color=_TAG_OPEN_BG,
                          command=self._on_tag_new).pack(side="left", padx=(6, 0))
            # 2026-09-15（用户要求 3）：把"且/或"逻辑按钮从下方"已选栏"移到**「＋ 新建」的右侧**。
            #   注：本行右侧因此多占 ≈91px（源码 76），其左侧的自适应搜索框可用宽度相应减少。
            _seg_l = ctk.CTkSegmentedButton(top, values=["且", "或"], width=76,
                                            command=self._on_tag_logic_change)
            _seg_l.set("且" if self._tag_page_logic == "and" else "或")
            _seg_l.pack(side="left", padx=(6, 0))

        # ---- 已选栏（仅标签档：热点词/词表与"按标签过滤条目"无关）----
        if is_tag:
            sel = ctk.CTkFrame(tf, fg_color="transparent")
            sel.pack(fill="x", padx=12, pady=(2, 2))
            ctk.CTkLabel(sel, text="已选：", font=("Microsoft YaHei", 11),
                         text_color="#5b6b7c").pack(side="left")
            if not self._tag_page_selected:
                ctk.CTkLabel(sel, text="（未选择）", font=("Microsoft YaHei", 11),
                             text_color="#9aa4b1").pack(side="left")
            for n in self._tag_page_selected:
                col = _tag_color(n)
                chip = ctk.CTkFrame(sel, fg_color=col, corner_radius=11)
                chip.pack(side="left", padx=(0, 5), pady=2)
                ctk.CTkLabel(chip, text=n, text_color="#ffffff",
                             font=("Microsoft YaHei", 11)
                             ).pack(side="left", padx=(8, 2), pady=2)
                ctk.CTkButton(chip, text="×", width=18, height=18, fg_color=col,
                              hover_color="#8a94a6", text_color="#ffffff",
                              font=("Microsoft YaHei", 11),
                              command=lambda s=n: self._on_tag_toggle(s)
                              ).pack(side="left", padx=(0, 3), pady=2)
            # 2026-09-15（用户要求 3）：原在本行右侧的"且/或"按钮**已上移到顶部「＋ 新建」右侧**
            #   （见 `_refresh_tag_page` 的 top 区）→ 本行现在整行都可用于展示已选标签 chips。

        # ---- 固定头部（统计 / 排序）2026-09-15（用户要求 6+1）：**不随内容滚动** ----
        #   原先"共 N 个标签 + 显示条数下拉"建立在滚动区 `_tag_main` **内部** → 一滚动就看不见。
        #   2026-09-15（用户要求 2）：**分页与「每页」控件已下移到页面最底部的"无标签条目"行**
        #   （见本函数末尾的 `row2`）→ 本行只保留"统计"与"排序"。
        #   仅"标签云/列表"两档使用（热点词/词表档不显示，互不干扰）。
        self._tag_head = None
        self._tag_stat_lbl = None
        self._tag_page_lbl = None
        self._tag_limit_om = None
        self._tag_page_btns = {}
        self._tag_sort_seg = None       # 2026-09-15（用户要求 1）：列表档排序控件（固定头部，仅列表档显示）
        self._tag_sort_lbl = None
        self._tag_bottom_row = None     # 2026-09-15（用户要求 2）：底部行（无标签按钮 + 分页/每页）
        self._tag_page_group = None     # 2026-09-15（用户要求）：底部行内"分页+每页"整组（靠右）
        if is_tag:
            self._tag_head = ctk.CTkFrame(tf, fg_color="transparent")
            self._tag_head.pack(fill="x", padx=10, pady=(2, 0))
            self._tag_stat_lbl = ctk.CTkLabel(
                self._tag_head, text="", font=("Microsoft YaHei", 10), text_color="#5b6b7c")
            self._tag_stat_lbl.pack(side="left")
            # 2026-09-15（用户要求 1）：列表档的「排序：」也移到固定头部（原先在滚动区内，
            #   一滚动就看不见）。**仅"列表"档显示**——显隐与取值由 `_update_tag_head` 统一处理。
            #   位置：pack 顺序在所有"side=right"控件之后 → 显示在它们的**左侧**。
            self._tag_sort_seg = ctk.CTkSegmentedButton(
                self._tag_head, values=["计数", "名称"], width=120,
                command=self._on_tag_sort_change)
            self._tag_sort_seg.set("计数" if self._tag_page_sort == "count" else "名称")
            self._tag_sort_lbl = ctk.CTkLabel(self._tag_head, text="排序：",
                                              font=("Microsoft YaHei", 10), text_color="#5b6b7c")

        # ---- 主区（可滚动）----
        self._tag_main = ctk.CTkScrollableFrame(tf, fg_color="transparent")
        self._tag_main.pack(fill="both", expand=True, padx=8, pady=(2, 2))

        # ---- 底部管理 ----
        mgr = ctk.CTkFrame(tf, fg_color="transparent")
        mgr.pack(fill="x", padx=12, pady=(2, 10))
        if is_hot:
            # 2026-09-14（4-b）：热点词模式的底部按钮（与标签治理按钮互不干扰）
            # 2026-09-15（批次 6-2 补充，用户选定"一并置灰"）：3 个按钮均为**写操作**
            #   （文本导入 / 抓取写库 / 清空全部）⇒ 纳入 `_tag_gov_btns`，锁定态一并置灰。
            for _txt, _cmd in (("📋 文本导入…", self._on_hotword_import),
                               ("🔄 热点词更新…", self._on_hotword_fetch),
                               ("🗑 清空全部", self._on_hotword_clear)):
                _b = ctk.CTkButton(mgr, text=_txt, width=104, height=26, fg_color="#8a94a6",
                                   font=("Microsoft YaHei", 11), command=_cmd)
                _b.pack(side="left", padx=(0, 5))
                self._tag_gov_btns.append(_b)
            try:
                _hn = self.db.count_hotwords()
            except Exception:
                _hn = 0
            ctk.CTkLabel(mgr, text=f"共 {_hn} 个词条（自动打标 ⑧ 维度）",
                         font=("Microsoft YaHei", 10),
                         text_color="#9aa4b1").pack(side="right")
        elif is_dict:
            # 2026-09-14（0.5）：词表档的底部按钮（导出 / 导入 / 恢复出厂）
            # 2026-09-15（批次 6-2 补充，用户选定"一并置灰"）：其中"⬇ 导入词表…/↺ 恢复出厂词表"
            #   是**写操作** ⇒ 纳入 `_tag_gov_btns` 置灰；"⬆ 导出词表…"是只读 ⇒ **保持可用**。
            # 2026-09-17（用户需求）：**新增第 4 个入口「➕ 增量导入…」**（只增不删）——与
            #   「⬇ 导入（替换）…」**并存**；两个入口文案各自写明口径，避免误用（确认框再写明一次）。
            #   为在 ≈560 宽的面板里放下 4 个按钮：按钮宽度与右侧说明文字一并收窄
            #   （仅改宽度与说明文字的简写，**功能与口径一字未变**）。
            for _txt, _w, _cmd, _write in (
                    ("⬆ 导出词表…", 96, self._on_dict_export, False),
                    ("⬇ 导入（替换）…", 126, self._on_dict_import, True),
                    ("➕ 增量导入…", 108, self._on_dict_merge, True),
                    ("↺ 恢复出厂…", 104, self._on_dict_reset, True)):
                _b = ctk.CTkButton(mgr, text=_txt, width=_w, height=26, fg_color="#8a94a6",
                                   font=("Microsoft YaHei", 11), command=_cmd)
                _b.pack(side="left", padx=(0, 5))
                if _write:
                    self._tag_gov_btns.append(_b)
            ctk.CTkLabel(mgr, text="词表存于数据库", font=("Microsoft YaHei", 10),
                         text_color="#9aa4b1").pack(side="right")
        else:
            # 2026-09-15（批次 6-2，用户选定"禁用（可见但置灰）"）：标签页治理按钮
            #   重命名 / 合并… / 删除 / 清理未使用 / 🤖 批量打标 均为**写操作** ⇒ 锁定态置灰。
            #   引用收集到 `self._tag_gov_btns`，由 `_apply_tag_gov_lock()` 即时刷新
            #   （页面重建后立即同步，锁定开关切换时由 `_apply_lock_state()` 同步）。
            for _txt, _cmd in (("重命名", self._on_tag_rename), ("合并…", self._on_tag_merge),
                               ("删除", self._on_tag_delete), ("清理未使用", self._on_tag_purge)):
                _b = ctk.CTkButton(mgr, text=_txt, width=76, height=26, fg_color="#8a94a6",
                                   font=("Microsoft YaHei", 11), command=_cmd)
                _b.pack(side="left", padx=(0, 5))
                self._tag_gov_btns.append(_b)
            # 2026-09-14（阶段 2）：批量智能自动打标入口（另一入口在"⚙ 设置 → 标签与词表"）
            _bb = ctk.CTkButton(mgr, text="🤖 批量打标…", width=104, height=26, fg_color=_C_TAG,
                                font=("Microsoft YaHei", 11), command=self._open_batch_tag)
            _bb.pack(side="left", padx=(0, 5))
            self._tag_gov_btns.append(_bb)
            ctk.CTkLabel(mgr, text="选中→过滤条目", font=("Microsoft YaHei", 10),
                         text_color="#9aa4b1").pack(side="right")
            # 2026-09-14（用户要求"无标签条目"入口 方案 B）：标签页面内的入口，与工具栏
            #   "🏷 无标签"按钮同源（同一 `_show_untagged`），此处**带实时数量**，便于就地看到
            #   还有多少条没打标签。**单独一行**：上面 5 个治理按钮已占满 560px 面板宽，
            #   挤在同一行会被裁切。仅标签档显示（热点词/词表档不加，互不干扰）。
            row2 = ctk.CTkFrame(tf, fg_color="transparent")
            row2.pack(fill="x", padx=12, pady=(0, 10))
            self._tag_bottom_row = row2      # 2026-09-15（用户要求 2）：底部行引用（供自测/后续使用）
            # 2026-09-15（审核 M-2，用户同意）：count_untagged 失败时返回 −1 → 此处显示"未知"，
            #   不再把"库异常"显示成"（0）"而掩盖故障。
            try:
                _un_n = self.db.count_untagged()
            except Exception:
                _un_n = -1
            _un_txt = ("⚠ 无标签条目（未知）" if _un_n < 0
                       else f"⚠ 无标签条目（{_un_n}）")
            _un_btn = ctk.CTkButton(row2, text=_un_txt, width=136, height=26,
                                    fg_color="#C77700", hover_color="#A66200",
                                    font=("Microsoft YaHei", 11), command=self._show_untagged)
            _un_btn.pack(side="left")
            # 2026-09-15（用户选方案 A）：原在此处的灰色提示文字改为**悬停提示**——
            #   实测底部行放不下"按钮 + 提示文字 + 分页/每页控件"（需 ≈800px、可用 ≈575px），
            #   故提示文字不占位（信息不丢）。
            _attach_tooltip(_un_btn, "点击→条目区列出全部无标签条目，可逐条打标签")
            # ---- 分页 + 「每页」：2026-09-15（用户要求 2）：从固定头部下移到本行 ----
            #   2026-09-15（用户要求，本轮）：整组**在本行依次靠右**（"无标签条目"按钮保持靠左不动）。
            #   做法：整组放进一个子容器 `pg` 并让其 `side="right"` 贴右边界；组内仍按
            #   [⏮ ◀ 第X/Y页 ▶ ⏭ | 每页 ▾] **从左到右**排列（用 side="left"），避免顺序被反转。
            pg = ctk.CTkFrame(row2, fg_color="transparent")
            pg.pack(side="right")
            self._tag_page_group = pg
            for _key, _txt, _cmd, _pad in (("first", "⏮", self._on_tag_page_first, (0, 0)),
                                           ("prev", "◀", self._on_tag_page_prev, (4, 0))):
                _b = ctk.CTkButton(pg, text=_txt, width=28, height=24, fg_color="#8a94a6",
                                   font=("Microsoft YaHei", 11), command=_cmd)
                _b.pack(side="left", padx=_pad)
                self._tag_page_btns[_key] = _b
            self._tag_page_lbl = ctk.CTkLabel(pg, text="", font=("Microsoft YaHei", 10),
                                              text_color="#5b6b7c")
            self._tag_page_lbl.pack(side="left", padx=(6, 4))
            for _key, _txt, _cmd, _pad in (("next", "▶", self._on_tag_page_next, (4, 0)),
                                           ("last", "⏭", self._on_tag_page_last, (4, 0))):
                _b = ctk.CTkButton(pg, text=_txt, width=28, height=24, fg_color="#8a94a6",
                                   font=("Microsoft YaHei", 11), command=_cmd)
                _b.pack(side="left", padx=_pad)
                self._tag_page_btns[_key] = _b
            ctk.CTkLabel(pg, text="每页", font=("Microsoft YaHei", 10),
                         text_color="#5b6b7c").pack(side="left", padx=(8, 4))
            self._tag_limit_om = ctk.CTkOptionMenu(pg, width=76, values=list(_TAG_LIMIT_LABELS),
                                                   command=self._on_tag_limit_change,
                                                   font=("Microsoft YaHei", 11))
            self._tag_limit_om.pack(side="left")

            # 2026-09-22（用户要求 1）：把上面 5 个治理按钮（重命名 / 合并… / 删除 /
            #   清理未使用 / 🤖 批量打标…）整行**移到页面最底部**——即"⚠ 无标签条目 + 分页/每页"
            #   这一行的**下方**。理由：它们是"对选中标签的批量写操作"，放在"浏览控制"之后
            #   更符合"先浏览、后操作"的动线，且让无标签入口与分页紧贴标签云。
            #   做法：pack_forget + 重新 pack（同一父容器内重排到最后）——按钮顺序与宽度**未变**。
            mgr.pack_forget()
            mgr.pack(fill="x", padx=12, pady=(0, 10))

        # 2026-09-15（批次 6-2，用户在"同类漏网"议题上选定"一并置灰"）：三档的**写操作按钮**
        #   统一按锁定态置灰——标签档 5 个（治理按钮）/ 热点词档 3 个 /
        #   词表档 3 个（2026-09-17 用户需求：新增「➕ 增量导入…」后由 2 → 3）；
        #   "⬆ 导出词表…"为只读，**不**纳入置灰。此处统一调用，保证"页面重建后不漏置灰"。
        self._apply_tag_gov_lock()

        self._render_tag_main()
        if is_tag:
            # 只有标签档刷新条目区：热点词档/词表档**不改动条目区**，避免"管理词库/词表"时
            # 把用户当前的条目浏览结果清空（互不干扰，2026-09-14）
            self._show_entries_by_tags()

    # ---- 标签页面：主区渲染 ---- #
    def _tag_cloud_font(self, count: int) -> int:
        """标签云字号档位（按条目计数；面板宽约 560，故档位取 11~20）"""
        if count >= 100:
            return 20
        if count >= 40:
            return 16
        if count >= 15:
            return 13
        return 11

    def _render_tag_main(self) -> None:
        """渲染标签云或"列表 + 计数徽标"（按搜索词过滤）；2026-09-14（4-b）新增"热点词"分支。"""
        box = getattr(self, "_tag_main", None)
        if box is None or not box.winfo_exists():
            return
        for w in box.winfo_children():
            w.destroy()
        if self._tag_page_view == "hot":
            self._render_hotwords()          # 2026-09-14（阶段 4 4-b）：热点词页签
            return
        if self._tag_page_view == "dict":
            self._render_dict_panel()        # 2026-09-14（阶段 0.5）：词表页签
            return
        try:
            all_tags = self.db.list_tags_with_counts()
        except Exception:
            all_tags = []
        kw = self._tag_page_search
        tags = [t for t in all_tags if (not kw) or (kw in t["name"])]
        lim = int(getattr(self, "_tag_page_limit", TAG_PAGE_LIMIT) or 0)   # 0＝不限（单页全部）
        if not all_tags:
            ctk.CTkLabel(box, text="（暂无标签：可在条目详情区的 🏷 标签块中为条目打标签）",
                         text_color="#9aa4b1", font=("Microsoft YaHei", 11)).pack(
                anchor="w", padx=6, pady=8)
            self._update_tag_head(0, 0, 1, 1, lim)
            return
        if not tags:
            ctk.CTkLabel(box, text="（没有匹配的标签）", text_color="#9aa4b1",
                         font=("Microsoft YaHei", 11)).pack(anchor="w", padx=6, pady=8)
            self._update_tag_head(len(all_tags), 0, 1, 1, lim)
            return

        # 2026-09-14（性能优化，用户确认"默认前 100 + 可切全部"）：622 个标签全量渲染实测
        #   17~42 秒（每项 27~68ms，瓶颈在逐个创建 UI 控件；纯查询仅 3ms）→ 当时"只显示前 N 个（截断）"。
        # 2026-09-15（用户要求 6+7）：改为**分页**——`_tag_page_limit` 语义 = "**每页 N 条**"
        #   （0／"不限"＝单页显示全部）；页码越界自动钳制，改每页条数/搜索/排序/切档都复位到第 1 页。
        if self._tag_page_sort == "name":
            ordered = sorted(tags, key=lambda t: t["name"])
        else:
            ordered = sorted(tags, key=lambda t: (-int(t.get("entry_count") or 0), t["name"]))
        total = len(ordered)
        per = lim if lim > 0 else max(total, 1)
        pages = max(1, (total + per - 1) // per)
        page = min(max(int(getattr(self, "_tag_page_no", 1)), 1), pages)
        self._tag_page_no = page
        off = (page - 1) * per
        shown = ordered[off:off + per]
        # 统计文案 / 每页下拉 / 页码 / 翻页按钮状态 → 全部更新到**固定头部**（不在滚动区内）
        self._update_tag_head(len(all_tags), len(tags), pages, page, lim)

        if self._tag_page_view == "list":
            self._render_tag_list(shown)
        else:
            self._render_tag_cloud(shown)

    def _update_tag_head(self, total_all: int, total_match: int, pages: int,
                         page: int, lim: int) -> None:
        """更新标签页的"统计 / 每页条数 / 页码 / 翻页按钮 / 排序"显示状态。

        2026-09-15（用户要求 6+7+2）：这些控件**都不在滚动区内**——
        "统计/排序"在页面顶部固定行 `_tag_head`，"每页/页码/翻页"在页面底部行 `_tag_bottom_row`
        （"无标签条目"按钮右侧）→ 因此滚动标签云时始终可见、可随时调整单页条数。
        """
        try:
            if self._tag_stat_lbl is not None and self._tag_stat_lbl.winfo_exists():
                self._tag_stat_lbl.configure(
                    text=(f"共 {total_all} 个标签"
                          + (f"（匹配 {total_match} 个）" if self._tag_page_search else "")))
            if self._tag_limit_om is not None and self._tag_limit_om.winfo_exists():
                self._tag_limit_om.set("不限" if lim <= 0 else str(lim))
            if self._tag_page_lbl is not None and self._tag_page_lbl.winfo_exists():
                self._tag_page_lbl.configure(text=f"第 {page} / {pages} 页")
            for _k, _b in (self._tag_page_btns or {}).items():
                if not _b.winfo_exists():
                    continue
                _ok = ((_k in ("first", "prev") and page > 1)
                       or (_k in ("next", "last") and page < pages))
                _b.configure(state=("normal" if _ok else "disabled"))
            # 2026-09-15（用户要求 1）：「排序：」控件**仅"列表"档显示**（标签云按固定档位渲染，无需排序）
            if self._tag_sort_seg is not None and self._tag_sort_seg.winfo_exists():
                self._tag_sort_seg.set("计数" if self._tag_page_sort == "count" else "名称")
                if self._tag_page_view == "list":
                    if not self._tag_sort_seg.winfo_ismapped():
                        self._tag_sort_seg.pack(side="right")
                        self._tag_sort_lbl.pack(side="right", padx=(0, 4))
                else:
                    self._tag_sort_seg.pack_forget()
                    self._tag_sort_lbl.pack_forget()
        except Exception as exc:
            print(f"[标签页] 更新固定头部失败：{exc!r}")

    def _on_tag_limit_change(self, value: str) -> None:
        """标签页「每页」条数切换。

        2026-09-14：原为"显示前 N 个"（截断）。2026-09-15（用户要求 6+7）：语义改为"**每页 N 条**"
        （"不限"＝0＝单页显示全部）；改每页条数后**回到第 1 页**。
        """
        self._tag_page_limit = {"不限": 0, "100": 100, "200": 200, "500": 500}.get(value, 50)
        self._tag_page_no = 1
        self._render_tag_main()

    def _tag_goto_page(self, no: int) -> None:
        """跳转页码（2026-09-15 用户要求 7）。页码在 `_render_tag_main` 内按总页数自动钳制；
        **只重建主区**（不重建页面框架）→ 不丢搜索框焦点。
        """
        self._tag_page_no = max(1, int(no))
        self._render_tag_main()

    def _on_tag_page_first(self) -> None:
        self._tag_goto_page(1)

    def _on_tag_page_prev(self) -> None:
        self._tag_goto_page(int(getattr(self, "_tag_page_no", 1)) - 1)

    def _on_tag_page_next(self) -> None:
        self._tag_goto_page(int(getattr(self, "_tag_page_no", 1)) + 1)

    def _on_tag_page_last(self) -> None:
        # 传一个足够大的页码，由 `_render_tag_main` 钳制到实际末页
        self._tag_goto_page(10 ** 6)

    def _render_tag_cloud(self, tags) -> None:
        """标签云：按计数分档字号、哈希柔和色、点击选中（选中＝实心）。

        2026-09-14 修复（用户反馈"标签云只显示 3 个标签"）：原先所有按钮共用一个 Frame
        并 `pack(side="left")` 平铺，而 tkinter 的 pack **不会自动换行** → 标签一多就横向
        溢出，超出面板宽度的部分被裁剪（该面板只支持垂直滚动）；而标签按计数降序，所以
        恰好只看到前 3 个（视觉/人像/产品）。现改为**按实测文字宽度自动换行**。
        """
        box = self._tag_main
        wrap = ctk.CTkFrame(box, fg_color="transparent")
        wrap.pack(fill="x", padx=6, pady=6)

        # 可用宽度：优先取主区实际宽度；首次渲染（控件尚未映射，宽度为 1）时按标签页面宽度估算
        avail = 0
        for _w in (box, getattr(self, "tag_frame", None)):
            try:
                if _w is not None:
                    avail = max(avail, int(_w.winfo_width()))
            except Exception:
                pass
        if avail < 200:
            avail = sum(_NAV_COL_WIDTHS)
        avail = max(200, avail - _CLOUD_RESERVED)   # 扣滚动条 / 内边距 / 余量（2026-09-14 实测校准）

        row, used = None, 0
        for t in tags:
            name, cnt = t["name"], int(t.get("entry_count") or 0)
            col = _tag_color(name)
            picked = name in self._tag_page_selected
            fs = self._tag_cloud_font(cnt)
            bold = cnt >= 40
            label = f"{name} ({cnt})"
            wpx = _text_px(label, fs, bold) + _CLOUD_BTN_PAD
            if wpx > avail - 12:                       # 单个标签超宽 → 截断显示，避免被裁剪
                keep = max(4, int((avail - 12) / max(1, _text_px("x", fs, bold))) - 6)
                label = name[:keep] + "… (%d)" % cnt
                wpx = _text_px(label, fs, bold) + _CLOUD_BTN_PAD
            if row is None or (used and used + wpx > avail):
                row = ctk.CTkFrame(wrap, fg_color="transparent")
                row.pack(fill="x")
                used = 0
            ctk.CTkButton(
                row, text=label, width=1,      # 2026-09-14：width=1 → 按文字自适应（默认 140 会强制撑宽）
                height=max(28, fs + 14),
                font=("Microsoft YaHei", fs, "bold" if bold else "normal"),
                fg_color=(col if picked else "transparent"),
                text_color=("#ffffff" if picked else col),
                border_width=1, border_color=col, corner_radius=12,
                command=lambda s=name: self._on_tag_toggle(s)
            ).pack(side="left", padx=4, pady=4)
            used += wpx

    def _render_tag_list(self, tags) -> None:
        """列表 + 计数徽标：每行 复选框 + 色点 + 标签名 + 条目数徽标；可按计数/名称排序"""
        box = self._tag_main
        # 2026-09-15（用户要求 1）：原先建在**滚动区内**的「排序：计数/名称」已上移到**固定头部**
        #   （见 `_refresh_tag_page` 的 `_tag_head`，由 `_update_tag_head` 控制显隐与取值）
        #   → 本函数不再创建排序控件，列表首行即数据行。
        if self._tag_page_sort == "name":
            ordered = sorted(tags, key=lambda t: t["name"])
        else:
            ordered = sorted(tags, key=lambda t: (-int(t.get("entry_count") or 0), t["name"]))
        for t in ordered:
            name, cnt = t["name"], int(t.get("entry_count") or 0)
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=1)
            var = ctk.BooleanVar(value=(name in self._tag_page_selected))
            ctk.CTkCheckBox(row, text="", width=24, variable=var,
                            command=lambda s=name: self._on_tag_toggle(s)
                            ).pack(side="left")
            dot = ctk.CTkFrame(row, width=11, height=11, corner_radius=6,
                               fg_color=_tag_color(name))
            dot.pack(side="left", padx=(2, 6))
            ctk.CTkButton(row, text=name, height=24, anchor="w", fg_color="transparent",
                          text_color="#1b2733", hover_color="#eef2f7",
                          font=("Microsoft YaHei", 12),
                          command=lambda s=name: self._on_tag_toggle(s)
                          ).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(row, text=str(cnt), width=38, height=20, corner_radius=10,
                         fg_color="#8fa3b8", text_color="#ffffff",
                         font=("Microsoft YaHei", 10)).pack(side="right", padx=(4, 2))

    # ---- 标签页面：交互 ---- #
    def _cancel_tag_search_timer(self) -> None:
        """取消尚未到点的"标签页搜索防抖"定时器（2026-09-15 用户要求 8）。"""
        if self._tag_search_after is not None:
            try:
                self.after_cancel(self._tag_search_after)
            except Exception:
                pass
            self._tag_search_after = None

    def _on_tag_search_typed(self, _event=None) -> None:
        """标签页搜索框：**延迟刷新**（2026-09-15 用户要求 8：防止逐字不停刷新）。

        原先每输入一个字符就 `_render_tag_main()`（全量重算 + 重建标签云/列表，622 个标签时很慢）。
        现改为：只记下关键词并排一个 **400ms 防抖**定时器，到点才 `_apply_tag_search()` 重建主区；
        注意**不重建搜索框本身**（否则会丢输入焦点与光标位置）。
        """
        ent = getattr(self, "_tag_search_entry", None)
        if ent is None or not ent.winfo_exists():
            return
        self._tag_page_search = ent.get().strip()
        self._cancel_tag_search_timer()
        try:
            self._tag_search_after = self.after(_TAG_SEARCH_DEBOUNCE_MS, self._apply_tag_search)
        except Exception:
            self._apply_tag_search()

    def _apply_tag_search(self) -> None:
        """防抖到点后真正应用"标签页搜索"（页码复位到第 1 页，只重建主区）。"""
        self._tag_search_after = None
        self._tag_page_no = 1
        self._render_tag_main()

    def _on_tag_search_clear(self) -> None:
        """点击标签页面搜索框右侧"✕"：**一次清空**关键词（2026-09-17 用户要求 2）。

        标签档 / 热点词档共用本按钮：
          · 只清关键词 + 立即重建**主区**（取消待执行的防抖、页码复位到第 1 页）；
          · **不重建顶部输入框本身** ⇒ 与"逐字删空"一样，输入框不失焦、光标即回；
          · 词表档不显示搜索框（也就没有本按钮）。
        """
        ent = getattr(self, "_tag_search_entry", None)
        if ent is not None and ent.winfo_exists():
            try:
                ent.delete(0, "end")
            except Exception:
                pass
        self._cancel_tag_search_timer()
        if self._tag_page_view == "hot":
            self._hotword_search = ""
        else:
            self._tag_page_search = ""
        self._tag_page_no = 1
        self._render_tag_main()
        try:
            if ent is not None and ent.winfo_exists():
                ent.focus_set()
        except Exception:
            pass

    def _on_tag_view_change(self, value: str) -> None:
        # 2026-09-14（阶段 4 4-b 新增「热点词」；阶段 0.5 新增「词表」）
        old = self._tag_page_view
        new = {"列表": "list", "热点词": "hot", "词表": "dict"}.get(value, "cloud")
        self._tag_page_view = new
        self._tag_page_no = 1        # 2026-09-15（用户要求 7）：切档回到第 1 页
        try:
            self.db.set_meta(config.META_TAG_VIEW, new)   # 记住上次选择
        except Exception:
            pass
        if new in ("hot", "dict") or old in ("hot", "dict"):
            # 进出"热点词/词表"档：顶部搜索框占位、已选栏、底部按钮组都不同 → 需重建整页
            self._refresh_tag_page()
        else:
            # 标签云 ⇄ 列表：与既有行为**完全一致**（只重渲染主区，不动条目区、不失焦搜索框）
            self._render_tag_main()

    def _on_tag_sort_change(self, value: str) -> None:
        self._tag_page_sort = "name" if value == "名称" else "count"
        self._tag_page_no = 1        # 2026-09-15（用户要求 7）：改排序回到第 1 页
        self._render_tag_main()

    def _on_tag_logic_change(self, value: str) -> None:
        self._tag_page_logic = "or" if value == "或" else "and"
        self._refresh_tag_page()   # 同步刷新"已选/主区"，并重查条目区

    def _on_tag_toggle(self, name: str) -> None:
        """点选/取消一个标签（更新选择 → 重建页面 → 刷新条目区）"""
        if name in self._tag_page_selected:
            self._tag_page_selected.remove(name)
        else:
            self._tag_page_selected.append(name)
        self._refresh_tag_page()

    def _show_entries_by_tags(self) -> None:
        """按标签页面的当前选择刷新条目区（跨分类）；未选择时清空并提示。"""
        if not self._tag_page_selected:
            self._view = None
            self._render_entries([], "🏷 标签（未选择）")
            return
        tags = [self.db.get_tag_by_name(n) for n in self._tag_page_selected]
        ids = [t["id"] for t in tags if t]
        names = [t["name"] for t in tags if t]
        if not ids:
            self._view = None
            self._render_entries([], "🏷 标签（未选择）")
            return
        mode = self._tag_page_logic
        join = " ∧ " if mode == "and" else " ∨ "
        title = "🏷 " + join.join(names)
        self._view = ("tag", (ids, mode, title))
        self._render_entries(self.db.list_entries_by_tags(ids, mode), title)

    def _refresh_detail_if_safe(self) -> None:
        """标签管理操作后，若详情区无未保存内容则重建，使标签块同步（防丢改动）。"""
        if self._adding_new or self._detail_dirty or self._detail_entry_id is None:
            return
        e = self.db.get_entry(self._detail_entry_id)
        if e is not None:
            self._show_detail(e)

    def _on_tag_new(self) -> None:
        # 2026-09-15（批次 6-3，用户选定"仅入口拦截 + toast 提示"）：锁定态拦截（本入口原无任何拦截）
        if not self._assert_unlocked("新建标签"):
            return
        name = simpledialog.askstring("新建标签", "请输入标签名称：", parent=self)
        if not name or not name.strip():
            return
        try:
            self.db.add_tag(name.strip())
        except ValueError as exc:
            messagebox.showwarning("提示", str(exc), parent=self)
            return
        self.toast(f"✅ 已新建标签：{name.strip()}")
        self._refresh_tag_page()

    def _on_tag_rename(self) -> None:
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("重命名标签"):
            return
        if len(self._tag_page_selected) != 1:
            messagebox.showinfo("提示", "请先在标签页面中选中**恰好一个**标签。", parent=self)
            return
        old = self._tag_page_selected[0]
        new = simpledialog.askstring("重命名标签", f"把「{old}」重命名为：", parent=self)
        if not new or not new.strip():
            return
        t = self.db.get_tag_by_name(old)
        if t is None:
            return
        try:
            self.db.rename_tag(t["id"], new.strip())
        except ValueError as exc:
            messagebox.showwarning("提示", str(exc), parent=self)
            return
        self._tag_page_selected = [new.strip()]
        self.toast(f"✅ 已重命名为「{new.strip()}」")
        self._refresh_tag_page()
        self._refresh_detail_if_safe()

    def _on_tag_merge(self) -> None:
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("合并标签"):
            return
        if len(self._tag_page_selected) != 1:
            messagebox.showinfo("提示", "请先选中**恰好一个**标签作为「来源」，再输入要并入的目标标签名。",
                                parent=self)
            return
        src_name = self._tag_page_selected[0]
        dst_name = simpledialog.askstring(
            "合并标签", f"把「{src_name}」的全部条目并入哪个标签？\n（输入已存在的标签名）",
            parent=self)
        if not dst_name or not dst_name.strip():
            return
        dst_name = dst_name.strip()
        src = self.db.get_tag_by_name(src_name)
        dst = self.db.get_tag_by_name(dst_name)
        if src is None or dst is None:
            messagebox.showwarning("提示", f"目标标签「{dst_name}」不存在。", parent=self)
            return
        if not messagebox.askyesno(
                "合并标签",
                f"将把「{src_name}」的全部条目并入「{dst_name}」，并删除「{src_name}」。\n\n确定吗？",
                parent=self):
            return
        self.db.merge_tags(src["id"], dst["id"])
        self._tag_page_selected = [dst_name]
        self.toast(f"✅ 已合并「{src_name}」→「{dst_name}」")
        self._refresh_tag_page()
        self._refresh_detail_if_safe()

    def _on_tag_delete(self) -> None:
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("删除标签"):
            return
        if not self._tag_page_selected:
            messagebox.showinfo("提示", "请先选中要删除的标签（可多选）。", parent=self)
            return
        names = list(self._tag_page_selected)
        if not messagebox.askyesno(
                "删除标签",
                f"将删除 {len(names)} 个标签：{('、'.join(names))}\n\n"
                "删除标签**不会删除任何条目**，仅解除标签关联。确定吗？", parent=self):
            return
        for n in names:
            t = self.db.get_tag_by_name(n)
            if t:
                self.db.delete_tag(t["id"])
        self._tag_page_selected = []
        self.toast(f"✅ 已删除 {len(names)} 个标签")
        self._refresh_tag_page()
        self._refresh_detail_if_safe()

    def _on_tag_purge(self) -> None:
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("清理未使用标签"):
            return
        if not messagebox.askyesno(
                "清理未使用标签",
                "将清除**没有任何条目关联**的标签（不影响条目与其它标签）。确定吗？", parent=self):
            return
        n = self.db.purge_unused_tags()
        self.toast(f"✅ 已清理 {n} 个未使用标签")
        self._refresh_tag_page()

    # ------------------------------------------------------------------ #
    # 热点词页签（2026-09-14 11:15，阶段 4 之 4-b / 4-c / 4-d / 4-e）
    #   热点词表 = "自动打标"⑧ 维度的候选词库（与 tags 表完全隔离，不产生任何标签）。
    #   入口双通道：① 标签页面「热点词」页签；② 设置对话框「热点词管理…」按钮（复用同一界面）。
    #   维护三通道：① 逐条手动添加；② 文本导入 / 粘贴；③ 「热点词更新」从来源网址抓取。
    # ------------------------------------------------------------------ #
    def _on_hotword_search_typed(self, _event=None) -> None:
        """热点词页签的搜索框：仅过滤显示，不改动词表。

        2026-09-15（用户要求 8）：与标签页搜索合并走同一套 **400ms 防抖**
        （热点词档同样是"每键一次全量重建"，且两档共用同一个搜索框控件与同一个定时器句柄）。
        """
        ent = getattr(self, "_tag_search_entry", None)
        if ent is None or not ent.winfo_exists():
            return
        self._hotword_search = ent.get().strip()
        self._cancel_tag_search_timer()
        try:
            self._tag_search_after = self.after(_TAG_SEARCH_DEBOUNCE_MS, self._apply_tag_search)
        except Exception:
            self._apply_tag_search()

    def _render_hotwords(self) -> None:
        """渲染热点词管理面板：新增输入行 + 词条列表（每行可逐条删除）。"""
        box = getattr(self, "_tag_main", None)
        if box is None or not box.winfo_exists():
            return

        # ---- 新增行 ----
        addrow = ctk.CTkFrame(box, fg_color="transparent")
        addrow.pack(fill="x", padx=6, pady=(6, 2))
        self._hotword_entry = ctk.CTkEntry(
            addrow, placeholder_text="输入热点词，回车添加（一次可粘贴多个）", height=30)
        self._hotword_entry.pack(side="left", fill="x", expand=True)
        self._hotword_entry.bind("<Return>", self._on_hotword_add)
        ctk.CTkButton(addrow, text="＋ 添加", width=76, height=30, fg_color=_TAG_OPEN_BG,
                      command=self._on_hotword_add).pack(side="left", padx=(6, 0))

        # ---- 词条列表 ----
        try:
            words = self.db.list_hotwords()
        except Exception:
            words = []
        kw = self._hotword_search
        shown = [w for w in words if (not kw) or (kw in w)]

        if not words:
            ctk.CTkLabel(
                box,
                text=("（热点词表为空）\n"
                      "· 逐条添加：在上方输入后回车\n"
                      "· 批量添加：点底部「📋 文本导入…」粘贴或导入文件\n"
                      "· 从网站获取：点底部「🔄 热点词更新…」，填写来源网址后抓取\n"
                      "说明：热点词会在「自动打标」时作为 ⑧ 维度标签使用，每条例目最多取 1 个。"),
                text_color="#9aa4b1", justify="left",
                font=("Microsoft YaHei", 11)).pack(anchor="w", padx=10, pady=10)
            return
        if not shown:
            ctk.CTkLabel(box, text="（没有匹配的热点词）", text_color="#9aa4b1",
                         font=("Microsoft YaHei", 11)).pack(anchor="w", padx=10, pady=8)
            return

        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(head,
                     text=(f"共 {len(words)} 个词条（匹配 {len(shown)} 个）" if kw
                           else f"共 {len(words)} 个词条"),
                     font=("Microsoft YaHei", 10), text_color="#5b6b7c").pack(side="left")

        for w in shown:
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=1)
            ctk.CTkFrame(row, width=11, height=11, corner_radius=6,
                         fg_color=_tag_color(w)).pack(side="left", padx=(2, 6))
            ctk.CTkLabel(row, text=w, anchor="w", font=("Microsoft YaHei", 12),
                         text_color="#1b2733").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="×", width=24, height=22, fg_color="#c9d3df",
                          hover_color=_C_DANGER, text_color="#1b2733",
                          font=("Microsoft YaHei", 11),
                          command=lambda s=w: self._on_hotword_delete(s)).pack(side="right")

    def _on_hotword_add(self, _event=None) -> None:
        """把输入框内容加入热点词表（支持一次粘贴多个，按常见分隔符自动拆分）。"""
        # 2026-09-15（批次 6-3，用户选定"仅入口拦截 + toast 提示"）：锁定态拦截（本入口原无任何拦截）
        if not self._assert_unlocked("添加热点词"):
            return
        ent = getattr(self, "_hotword_entry", None)
        if ent is None or not ent.winfo_exists():
            return
        words = self.db.parse_hotwords_text(ent.get())
        if not words:
            self.toast("⚠ 请输入有效热点词（单个词条不超过 24 字符）")
            return
        res = self.db.add_hotwords(words)
        try:
            ent.delete(0, "end")
        except Exception:
            pass
        msg = f"✅ 已添加 {res['added_count']} 个热点词"
        if res["skipped"]:
            msg += f"（{res['skipped']} 个已存在）"
        if res["invalid"]:
            msg += f"（忽略 {res['invalid']} 个非法项）"
        self.toast(msg + f"，现有 {res['total']} 个")
        self._render_tag_main()
        self._focus_hotword_entry()

    def _focus_hotword_entry(self) -> None:
        """把输入焦点交回热点词输入框（2026-09-17 用户要求 3）。

        添加成功后 `_render_tag_main()` 会**重建整块主区**（输入行随之新建）⇒ 焦点丢失，
        原先必须再用鼠标点一下才能继续输入。这里延后 10ms（等新控件完成创建/布局）把焦点放回，
        便于"连续添加多个热点词"（回车或点「＋ 添加」均可直接接着输入）。
        """
        def _do():
            ent = getattr(self, "_hotword_entry", None)
            if ent is None or not ent.winfo_exists():
                return
            try:
                ent.focus_set()
            except Exception:
                pass
        try:
            self.after(10, _do)
        except Exception:
            pass

    def _on_hotword_delete(self, name: str) -> None:
        """删除单个热点词（即时生效，无需确认：可随时再加回）。"""
        # 2026-09-15（批次 6-3，用户选定"仅入口拦截 + toast 提示"）：锁定态拦截（本入口原无任何拦截）
        if not self._assert_unlocked("删除热点词"):
            return
        if self.db.remove_hotwords([name]):
            self.toast(f"✅ 已删除热点词：{name}")
        self._render_tag_main()

    def _on_hotword_clear(self) -> None:
        """清空全部热点词（二次确认；不影响已有标签与条目）。"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("清空热点词"):
            return
        n = self.db.count_hotwords()
        if n == 0:
            self.toast("热点词表已为空")
            return
        if not messagebox.askyesno(
                "清空热点词",
                f"将清空全部 {n} 个热点词（不影响已有标签与条目）。\n\n确定吗？", parent=self):
            return
        self.db.set_hotwords([])
        self.toast(f"✅ 已清空 {n} 个热点词")
        self._render_tag_main()

    def _on_hotword_import(self) -> None:
        """「文本导入…」（4-c）：弹窗内粘贴 / 导入文件 → 解析预览 → **并集追加**或整体替换。"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("导入热点词文本"):
            return
        win = ctk.CTkToplevel(self)
        win.title("📋 热点词文本导入")
        win.transient(self)
        win.grab_set()
        win.geometry("640x470")

        ctk.CTkLabel(win, text="粘贴或编辑热点词：每行一个；也支持逗号、顿号、分号、竖线分隔"
                               "（单个词条不超过 24 字符，超出会被忽略）",
                     justify="left", font=("Microsoft YaHei", 12)
                     ).pack(anchor="w", padx=14, pady=(12, 4))
        box = ctk.CTkTextbox(win, height=190, font=("Microsoft YaHei", 12))
        box.pack(fill="both", expand=True, padx=14, pady=4)
        status = ctk.CTkLabel(win, text="", justify="left", font=("Microsoft YaHei", 11),
                              text_color="#5b6b7c")
        status.pack(anchor="w", padx=14, pady=(0, 4))

        def _parsed():
            return self.db.parse_hotwords_text(box.get("1.0", "end"))

        def _preview() -> None:
            words = _parsed()
            have = set(self.db.list_hotwords())
            new = [w for w in words if w not in have]
            if not words:
                status.configure(text="未解析到词条。", text_color="#9aa4b1")
                return
            status.configure(
                text=f"解析到 {len(words)} 个词条；其中新增 {len(new)} 个、已存在 {len(words) - len(new)} 个。",
                text_color=(_C_OK if new else "#5b6b7c"))

        def _set_text(text: str) -> None:
            box.delete("1.0", "end")
            box.insert("1.0", text)
            _preview()

        def _read_file() -> None:
            path = filedialog.askopenfilename(
                title="选择词表文件", parent=win,
                filetypes=[("文本/表格文件", "*.txt *.csv *.md *.tsv"),
                           ("JSON 文件", "*.json"), ("全部文件", "*.*")])
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                    content = f.read()
            except Exception as exc:
                messagebox.showwarning("读取失败", str(exc), parent=win)
                return
            if path.lower().endswith(".json"):     # 兼容 ["词1","词2"] 与 {"hotwords": [...]}
                try:
                    data = json.loads(content)
                except Exception:
                    data = None
                if isinstance(data, list):
                    content = "\n".join(str(x) for x in data)
                elif isinstance(data, dict):
                    for k in ("hotwords", "words", "tags", "list"):
                        if isinstance(data.get(k), list):
                            content = "\n".join(str(x) for x in data[k])
                            break
            _set_text(content)

        def _paste() -> None:
            try:
                txt = pyperclip.paste() or ""
            except Exception:
                txt = ""
            if not txt.strip():
                status.configure(text="剪贴板为空或无法读取。", text_color=_C_DANGER)
                return
            cur = box.get("1.0", "end").strip()
            _set_text((cur + "\n" + txt) if cur else txt)

        def _apply() -> None:
            words = _parsed()
            if not words:
                status.configure(text="没有可导入的词条。", text_color=_C_DANGER)
                return
            res = self.db.add_hotwords(words)
            self.toast(f"✅ 已导入 {res['added_count']} 个热点词（跳过 {res['skipped']} 个），"
                       f"现有 {res['total']} 个")
            win.destroy()
            self._render_tag_main()

        def _replace() -> None:
            words = _parsed()
            if not words:
                status.configure(text="没有可替换的词条。", text_color=_C_DANGER)
                return
            if not messagebox.askyesno(
                    "替换词表",
                    f"将用这 {len(words)} 个词条「替换」当前全部热点词（不可撤销）。\n\n确定吗？",
                    parent=win):
                return
            n = self.db.set_hotwords(words)
            self.toast(f"✅ 热点词表已替换为 {n} 个词条")
            win.destroy()
            self._render_tag_main()

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(2, 12))
        ctk.CTkButton(btns, text="从文件导入…", width=106, fg_color="#8a94a6",
                      command=_read_file).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="从剪贴板粘贴", width=110, fg_color="#8a94a6",
                      command=_paste).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="清空输入框", width=96, fg_color="#8a94a6",
                      command=lambda: _set_text("")).pack(side="left")
        ctk.CTkButton(btns, text="替换词表", width=92, fg_color=_C_WARN,
                      command=_replace).pack(side="right")
        ctk.CTkButton(btns, text="添加到词表", width=104, fg_color=_C_OK,
                      command=_apply).pack(side="right", padx=(0, 6))
        ctk.CTkButton(btns, text="取消", width=76,
                      command=win.destroy).pack(side="right", padx=(0, 6))

        box.bind("<KeyRelease>", lambda _e: _preview())
        _preview()
        win.update_idletasks()
        win.geometry(f"+{max(self.winfo_x() + 40, 0)}+{max(self.winfo_y() + 60, 0)}")

    def _on_hotword_fetch(self) -> None:
        """「热点词更新…」（4-d）：填写来源网址 → 抓取 → 预览候选 → **并集追加**。

        - 来源网址为空时不做任何请求；来源列表随抓取一并保存到 meta（下次自动带出）。
        - 复用既有 `app/parser/fetcher.py`（标准库 urllib，**零新增依赖**）；**懒加载**导入。
        - 抓取失败 / 疑似被拦截：如实提示并继续下一个来源（不整体中止）。
        """
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("更新热点词"):
            return
        _MAX = 500        # 单次并入的词条上限（防误抓整站）
        src_urls = self.db.list_hotword_sources()

        win = ctk.CTkToplevel(self)
        win.title("🔄 热点词更新")
        win.transient(self)
        win.grab_set()
        win.geometry("680x560")

        ctk.CTkLabel(win, text="来源网址（每行一个 http/https 网址；默认留空，由你填写）\n"
                               "· 建议填写「每行一个词条」的文本 / JSON / Markdown 页面\n"
                               "· 抓取为只读一次请求；失败或被拦截会如实提示，不影响下一个来源",
                     justify="left", font=("Microsoft YaHei", 12)
                     ).pack(anchor="w", padx=14, pady=(12, 4))
        src_box = ctk.CTkTextbox(win, height=76, font=("Microsoft YaHei", 12))
        src_box.pack(fill="x", padx=14, pady=(0, 4))
        if src_urls:
            src_box.insert("1.0", "\n".join(src_urls))

        status = ctk.CTkLabel(win, text="", justify="left", font=("Microsoft YaHei", 11),
                              text_color="#5b6b7c")
        status.pack(anchor="w", padx=14, pady=(0, 2))
        result_box = ctk.CTkTextbox(win, height=220, font=("Microsoft YaHei", 11))
        result_box.pack(fill="both", expand=True, padx=14, pady=(0, 4))

        state = {"new": []}

        def _extract(text: str):
            """从抓取到的文本中提取候选词条（去脚本/样式/标签；按行与常见分隔符拆分）。"""
            t = re.sub(r"<script[\s\S]*?</script>", " ", text or "", flags=re.I)
            t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
            t = re.sub(r"<[^>]+>", "\n", t)        # 标签 → 换行，利于"每行一个词条"
            t = t.replace("&nbsp;", " ").replace("&amp;", "&")
            out = []
            for w in self.db.parse_hotwords_text(t):
                if re.fullmatch(r"[\W_]+", w):     # 纯符号
                    continue
                out.append(w)
            return out

        def _read_urls():
            items = [x.strip() for x in src_box.get("1.0", "end").splitlines() if x.strip()]
            return [u for u in items if u.lower().startswith(("http://", "https://"))]

        def _do_fetch() -> None:
            urls = _read_urls()
            if not urls:
                status.configure(text="请先填写至少一个 http/https 来源网址。", text_color=_C_DANGER)
                return
            self.db.set_hotword_sources(urls)      # 顺手记住来源，下次自动带出
            fetch_btn.configure(state="disabled", text="抓取中…")
            found, lines = [], []
            try:
                from ..parser import fetcher       # 懒加载：仅在真正抓取时引入
                for i, u in enumerate(urls, 1):
                    status.configure(text=f"（{i}/{len(urls)}）正在抓取：{u}", text_color="#5b6b7c")
                    win.update_idletasks()
                    res = fetcher.fetch_url(u)
                    if not res.get("ok"):
                        lines.append(f"✗ {u}\n    失败：{res.get('error') or '未知错误'}")
                        continue
                    got = _extract(res.get("text") or "")
                    found.extend(got)
                    lines.append(f"✓ {u}\n    状态 {res.get('status')} · {res.get('bytes')} 字节"
                                 f" · 解析出 {len(got)} 个候选"
                                 + ("　⚠ 疑似被网站拦截，建议换来源" if res.get("blocked_hint") else ""))
            except Exception as exc:               # 不整体中止：如实报告
                lines.append(f"✗ 抓取异常：{exc}")
            finally:
                try:
                    fetch_btn.configure(state="normal", text="开始抓取")
                except Exception:
                    pass

            uniq, seen = [], set()
            for w in found:
                if w not in seen:
                    seen.add(w)
                    uniq.append(w)
            have = set(self.db.list_hotwords())
            new = [w for w in uniq if w not in have][:_MAX]
            state["new"] = new
            tail = (f"\n\n候选词条：去重后 {len(uniq)} 个，其中可新增 {len(new)} 个"
                    + (f"（超出 {_MAX} 个上限的部分已省略）" if len(uniq) > _MAX else "")
                    + "：\n" + "\n".join(new)) if new else "\n\n没有可新增的词条。"
            result_box.delete("1.0", "end")
            result_box.insert("1.0", "\n".join(lines) + tail)
            status.configure(text=f"抓取完成：候选 {len(uniq)} 个，可新增 {len(new)} 个。",
                             text_color=(_C_OK if new else "#5b6b7c"))

        def _apply() -> None:
            new = state.get("new") or []
            if not new:
                status.configure(text="没有可并入的词条（请先抓取）。", text_color=_C_DANGER)
                return
            res = self.db.add_hotwords(new)
            self.toast(f"✅ 已并入 {res['added_count']} 个热点词（跳过 {res['skipped']} 个），"
                       f"现有 {res['total']} 个")
            state["new"] = []
            win.destroy()
            self._render_tag_main()

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(2, 12))
        fetch_btn = ctk.CTkButton(btns, text="开始抓取", width=104, fg_color=_TAG_OPEN_BG,
                                  command=_do_fetch)
        fetch_btn.pack(side="left")
        ctk.CTkButton(btns, text="保存来源", width=92, fg_color="#8a94a6",
                      command=lambda: self.toast(
                          f"✅ 已保存 {self.db.set_hotword_sources(_read_urls())} 个来源网址")
                      ).pack(side="left", padx=(6, 0))
        ctk.CTkButton(btns, text="并入词表", width=96, fg_color=_C_OK,
                      command=_apply).pack(side="right")
        ctk.CTkButton(btns, text="关闭", width=76,
                      command=win.destroy).pack(side="right", padx=(0, 6))

        status.configure(text=("已带出 %d 个已保存的来源网址。" % len(src_urls)) if src_urls
                                else "尚未配置来源网址。")
        win.update_idletasks()
        win.geometry(f"+{max(self.winfo_x() + 40, 0)}+{max(self.winfo_y() + 40, 0)}")

    def _open_hotword_manager(self) -> None:
        """「热点词管理…」入口（4-e，供设置对话框调用）。

        复用标签页面的【热点词】页签——**两处入口共享同一界面与同一份数据**（零重复实现）；
        若标签页面尚未打开则先打开，再切到"热点词"档。
        """
        self._tag_page_view = "hot"
        try:
            self.db.set_meta(config.META_TAG_VIEW, "hot")
        except Exception:
            pass
        if not self._tag_page_on:
            self._open_tag_page()
        else:
            self._refresh_tag_page()

    # ------------------------------------------------------------------ #
    # 词表页签（2026-09-14 12:00，阶段 0.5）
    #   词表 = 通用骨架 + 领域维度包 + 领域判定表；出厂种子见 app/tagger_dict.py（阶段 0 产出）。
    #   权威数据存于数据库 meta（键 tag_dict），**可编辑、可扩展**：
    #   要"新增一个大类"（如「学术」）= 在导出的 JSON 里加一个 domains 块后再导入。
    #   入口双通道：① 标签页面「词表」页签；② 设置对话框「📚 词表管理…」按钮（复用同一界面）。
    #   词表本身不参与本步任何匹配计算（匹配引擎属阶段 1），此处只做查看与维护。
    # ------------------------------------------------------------------ #
    def _render_dict_panel(self) -> None:
        """渲染词表结构预览（只读）：概要 + 通用骨架 + 各领域包的维度与标签清单。"""
        box = getattr(self, "_tag_main", None)
        if box is None or not box.winfo_exists():
            return
        try:
            data = tagger.load_dict(self.db)
            s = tagger.dict_summary(data)
        except Exception as exc:
            ctk.CTkLabel(box, text=f"（词表读取失败：{exc}）", text_color=_C_DANGER,
                         font=("Microsoft YaHei", 11)).pack(anchor="w", padx=10, pady=10)
            return

        def _row(text, color="#1b2733", size=12, bold=False):
            # 2026-09-18：出厂词表升到 19 个领域包后，单行标签清单会超出面板宽度被裁掉
            #   ⇒ 加 `wraplength` 自动换行（面板本身可滚动，换行只增加高度、不裁切）。
            ctk.CTkLabel(box, text=text, text_color=color, justify="left", anchor="w",
                         wraplength=max(sum(_NAV_COL_WIDTHS) - 40, 320),
                         font=("Microsoft YaHei", size, "bold" if bold else "normal")
                         ).pack(fill="x", padx=10, pady=(6, 0))

        _row(f"版本 v{s.get('version')} ｜ 更新于 {s.get('updated_at')} ｜ "
             f"标签总数 {s.get('标签总数')} ｜ 领域包 {len(s.get('领域包') or {})} 个",
             color="#5b6b7c", size=11)
        _row("【通用骨架】（所有领域共用）", color=_TAG_OPEN_BG, bold=True)
        for dim, labels in (data.get("universal") or {}).items():
            _row(f"　{dim}（{len(labels)}）：{' · '.join(labels.keys())}")

        for dom, info in (s.get("领域包") or {}).items():
            _row(f"【领域包：{dom}】{info['维度数']} 个维度 / {info['标签数']} 个标签",
                 color=_TAG_OPEN_BG, bold=True)
            for dim, labels in ((data.get("domains") or {}).get(dom) or {}).items():
                _row(f"　{dim}（{len(labels)}）：{' · '.join(labels.keys())}")

        _row("说明：以数据库中的词表为准（可编辑）。要新增一个大类（例如「学术」）："
             "点「⬆ 导出词表…」导出 JSON → 在 domains 里加一个「学术」块"
             "（并在 universal.领域 与 domain_map 各加一项）→ 点「➕ 增量导入…」并入即可"
             "（只增不删，现有标签全部保留）；要「整份照文件来」则用「⬇ 导入（替换）…」。",
             color="#9aa4b1", size=11)

    def _on_dict_export(self) -> None:
        """导出词表为 JSON 文件（供外部编辑 / 备份 / 换机搬运）。"""
        path = filedialog.asksaveasfilename(
            title="导出词表", parent=self, defaultextension=".json",
            initialfile="tag_dict.json",
            filetypes=[("JSON 文件", "*.json"), ("全部文件", "*.*")])
        if not path:
            return
        res = tagger.export_dict(self.db, path)
        if res.get("ok"):
            self.toast(f"✅ 词表已导出（{res.get('tags')} 个标签）：{os.path.basename(path)}")
        else:
            messagebox.showwarning("导出失败", str(res.get("error")), parent=self)

    def _on_dict_import(self) -> None:
        """从 JSON 文件导入词表（整体替换；导入前自动备份当前词表）。"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("导入词表"):
            return
        path = filedialog.askopenfilename(
            title="导入词表", parent=self,
            filetypes=[("JSON 文件", "*.json"), ("全部文件", "*.*")])
        if not path:
            return
        read = tagger.read_dict_file(path)          # 先只读校验，避免"确认了才发现格式错"
        if not read.get("ok"):
            detail = "\n".join(read.get("errors") or []) or str(read.get("error"))
            messagebox.showwarning("词表文件不合法",
                                   f"{detail}\n\n未做任何修改。", parent=self)
            return
        new_tags = tagger.count_tags(read["data"])
        doms = "、".join((read["data"].get("domains") or {}).keys())
        if not messagebox.askyesno(
                "导入词表",
                f"将用该文件「替换」当前词表。\n\n· 文件标签总数：{new_tags}\n"
                f"· 领域包：{doms}\n\n导入前会自动备份当前词表。确定吗？", parent=self):
            return
        res = tagger.import_dict(self.db, path)
        if not res.get("ok"):
            detail = "\n".join(res.get("errors") or []) or str(res.get("error"))
            messagebox.showwarning("导入失败", detail, parent=self)
            return
        self.toast(f"✅ 词表已导入（{res['before_tags']} → {res['tags']} 个标签），原词表已备份")
        self._render_tag_main()

    def _on_dict_merge(self) -> None:
        """**增量导入**词表（只增不删；2026-09-17 用户需求）。

        与「⬇ 导入（替换）…」的分工（`tagger.merge_dict` 的口径）：
          · 同名维度 / 同名标签 → 匹配词**并集去重**（原有在前、新词追加在后）；
          · 新标签 / 新维度 / **新领域包** / 新判定关键词 → 自动新增；
          · **现有内容一个不删**（本操作绝不会让词表变小），因此不必再"导出→手工并整份→导入"。
        安全口径与"导入"完全一致：**先只读校验**（不合法直接拒绝、不写库）→ 预览将新增多少 →
        二次确认 → 写库前自动备份当前词表。
        """
        if not self._assert_unlocked("增量导入词表"):
            return
        path = filedialog.askopenfilename(
            title="增量导入词表（只增不删）", parent=self,
            filetypes=[("JSON 文件", "*.json"), ("全部文件", "*.*")])
        if not path:
            return
        read = tagger.read_dict_file(path)      # 先只读校验，避免"确认了才发现格式错"
        if not read.get("ok"):
            detail = "\n".join(read.get("errors") or []) or str(read.get("error"))
            messagebox.showwarning("词表文件不合法",
                                   f"{detail}\n\n未做任何修改。", parent=self)
            return
        try:
            pre = tagger.merge_preview(tagger.load_dict(self.db), read["data"])
        except Exception as exc:
            messagebox.showwarning("无法合并", str(exc), parent=self)
            return
        add_n = pre["added_labels"] + pre["added_words"] + pre["added_map_words"]
        if add_n <= 0:
            # 该文件没有任何"当前词表里没有"的内容 → 明确告知并停止（不写库、不产生备份）
            messagebox.showinfo(
                "无可新增内容",
                f"该文件的内容，当前词表里都已经有了（现有 {pre['tags_before']} 个标签）。\n\n"
                "未做任何修改。", parent=self)
            return
        if not messagebox.askyesno(
                "增量导入词表（只增不删）",
                f"将把该文件「并入」当前词表 —— 只增不删：\n\n"
                f"· 现有标签：{pre['tags_before']} 个（全部保留）\n"
                f"· 将新增标签：{pre['added_labels']} 个\n"
                f"· 将新增维度：{pre['added_dims']} 个 ｜ 新增领域包：{pre['added_domains']} 个\n"
                f"· 将新增匹配词：{pre['added_words']} 个"
                f"（另有领域判定关键词 {pre['added_map_words']} 个）\n"
                f"· 合并后标签总数：{pre['tags_after']} 个\n\n"
                f"写库前会自动备份当前词表。确定吗？", parent=self):
            return
        res = tagger.merge_dict(self.db, path)
        if not res.get("ok"):
            detail = "\n".join(res.get("errors") or []) or str(res.get("error"))
            messagebox.showwarning("增量导入失败", detail, parent=self)
            return
        self.toast(f"✅ 已增量并入（{res['tags_before']} → {res['tags_after']} 个标签；"
                   f"新增标签 {res['added_labels']} 个、匹配词 {res['added_words']} 个），"
                   f"原词表已备份")
        self._render_tag_main()

    def _on_dict_reset(self) -> None:
        """恢复出厂词表（出厂种子见 app/tagger_dict.py；恢复前自动备份当前词表）。"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("恢复出厂词表"):
            return
        try:
            cur = tagger.count_tags(tagger.load_dict(self.db))
        except Exception:
            cur = 0
        if not messagebox.askyesno(
                "恢复出厂词表",
                f"将把词表恢复为「出厂默认」（当前 {cur} 个标签将被替换）。\n\n"
                "恢复前会自动备份当前词表。确定吗？", parent=self):
            return
        res = tagger.reset_dict(self.db)
        self.toast(f"✅ 已恢复出厂词表（{res['tags']} 个标签）")
        self._render_tag_main()

    def _open_dict_manager(self) -> None:
        """「📚 词表管理…」入口（阶段 0.5，供设置对话框调用）。

        与「🏷 热点词管理…」同一做法：复用标签页面的【词表】页签（两处入口同一界面、同一份数据）。
        """
        self._tag_page_view = "dict"
        try:
            self.db.set_meta(config.META_TAG_VIEW, "dict")
        except Exception:
            pass
        if not self._tag_page_on:
            self._open_tag_page()
        else:
            self._refresh_tag_page()

    def _open_auto_words_manager(self) -> None:
        """「🧠 自动取词词库…」入口（2026-09-16 批次 12-3，供设置对话框调用）。

        打开独立的「智能自动取词词库」管理对话框（查看 / 审核 / 提升 / 清理）。
        本对话框位于主窗口之上，故先释放设置窗口的模态抓取。
        """
        from .auto_words_dialog import AutoWordsDialog
        try:
            _dlg = AutoWordsDialog(self, self.db)
        except Exception as exc:
            self.toast("⚠ 打开自动取词词库失败：%s" % exc)
            return
        return _dlg

    def _open_tag_cleanup(self) -> None:
        """「🧹 清理垃圾标签…」入口（2026-09-17 新增，供设置对话框调用）。

        打开「清理垃圾标签」对话框：先**只读扫描**并列出清单（A 类将删除 / B 类将重命名），
        由用户确认后执行；执行前自动整库备份（见 app/tag_cleanup.py）。
        与仓库根目录 CLI 工具 `clean_junk_tags.py` 共用同一数据层，判定口径唯一。
        """
        from .tag_cleanup_dialog import TagCleanupDialog
        try:
            _dlg = TagCleanupDialog(self, self.db)
        except Exception as exc:
            self.toast("⚠ 打开清理垃圾标签失败：%s" % exc)
            return
        return _dlg

    def _set_window_icon(self) -> None:
        """设置运行期窗口 / 任务栏图标（2026-09-17，需求 U-1）。

        路径与托盘图标一致（`config.resource_dir()/Icons/PSicon.png`）：打包态从 PyInstaller
        解压目录读、开发态从项目 `Icons/` 读。用 `iconphoto`（PNG，跨平台安全）并**保持引用**
        （避免被 GC 回收导致图标消失）。**任何失败都静默忽略**——纯外观，不影响功能。
        """
        try:
            _p = os.path.join(config.resource_dir(), "Icons", "PSicon.png")
            if not os.path.isfile(_p):
                return
            from PIL import Image, ImageTk
            _img = Image.open(_p)
            _img.thumbnail((64, 64))
            self._icon_photo = ImageTk.PhotoImage(_img)   # 保持引用，勿删
            self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def apply_hotkey_combo(self, combo: str) -> bool:
        """按新的热键组合**立即重新注册**全局热键（2026-09-17，需求 S-2）。返回是否成功。

        供「设置 → 数据与备份 → 全局热键」在保存时调用：先释放旧热键，再按新组合注册。
        注册失败（被占用 / 无权限 / 杀软拦截）返回 False，由调用方提示；**不影响其它功能**。
        """
        from .. import hotkey
        try:
            hotkey.unregister_all()
        except Exception:
            pass
        return hotkey.register_global_hotkey(
            lambda: self.after(0, self.show_and_focus_search), combo)

    def _open_batch_tag(self) -> None:
        """打开「🤖 批量智能自动打标」对话框（2026-09-14，阶段 2）。

        入口双通道：① 标签页面（标签档）底部按钮；② 设置 → 标签与词表 页。
        2026-09-14（目标库增强）：把当前库文件路径一并传入，对话框可切换"目标库＝其他库文件"。
        """
        # 2026-09-15（批次 6-3b，用户选定"仅入口拦截"）：统一守卫——
        #   锁定态不打开批量打标对话框（对话框内可批量写标签，且可指向"其他库文件"）。
        if not self._assert_unlocked("打开批量打标"):
            return
        BatchTagDialog(self, self.db,
                       db_path=os.path.join(config.data_dir(), config.DB_FILE_NAME))

    # ------------------------------------------------------------------ #
    # 工具栏"折成两行"（2026-09-14 用户设计）
    # ------------------------------------------------------------------ #
    def _toolbar_one_row_need(self) -> int:
        """返回"一行时工具栏所需宽度"（实际像素，含工具栏左右外边距）。

        注（2026-09-15 审核 L-3 更正）：折行时**只搬移搜索框**、第一行内容不变，
        故 `bar.winfo_reqwidth()` 折行前后其实一致（原注释"第二组已不在第一行"是旧设计残留）。
        仍采用"**未折行时实测并缓存**"的取法：缓存值可自动适应不同 DPI / 字体（不用写死常量），
        并避免每次 <Configure> 都触发一次布局测量。
        """
        b = getattr(self, "untagged_btn", None)
        bar = b.master if b is not None else None
        if bar is None or not bar.winfo_exists():
            return 0
        if not self._bar_folded:
            # 工具栏左右外边距：源码 padx=8 → 本机缩放后约 10，两侧合计 20
            self._bar_one_row_need = bar.winfo_reqwidth() + 20
            # 2026-09-15（审核 L-8）：同时缓存"第一行最左按钮的左边界"（折行时作第二行首列宽）。
            #   ⚠ `winfo_x()` 在控件尚未布局时会**合法返回 0**（不抛异常），故必须在**未折行**时实测缓存，
            #   折行时若实时值 ≤0 就用本缓存值兜底（否则搜索框会"贴左"而不是"靠右"）。
            try:
                _x = int(self.tag_page_btn.winfo_x())
                if _x > 0:
                    self._bar_row2_left = _x
            except Exception as exc:
                _geom_warn("缓存工具栏左边界", exc)
        return self._bar_one_row_need

    def _on_root_configure(self, _event=None) -> None:
        """窗口尺寸变化 → 按"是否放得下一行工具栏"自动折成两行 / 恢复一行（幂等、含滞后）。"""
        if _event is not None and getattr(_event, "widget", None) is not self:
            return
        try:
            w = self.winfo_width()
        except Exception:
            return
        if w == self._bar_last_w:      # 宽度未变（如仅高度变化）→ 不重复计算
            return
        self._bar_last_w = w
        need = self._toolbar_one_row_need()
        if not need or w <= 1:         # 尚未完成布局
            return
        if not self._bar_folded and w < need:
            self._set_bar_folded(True)
        elif self._bar_folded and w >= need + _BAR_UNFOLD_MARGIN:
            self._set_bar_folded(False)

    def _set_bar_folded(self, folded: bool) -> None:
        """把**搜索框（输入框+🔍）**在"第一行最右"与"第二行靠右"之间搬移（幂等）。

        2026-09-14 用户最终决定：两组工具按钮固定第一行不变，只让搜索框下移到第二行靠右；
        搜索框搬走后，第一行的搜索列 `minsize` 同时置 0，避免留出一条空白。
        """
        row2 = getattr(self, "_bar_row2", None)
        box = getattr(self, "search_box", None)
        if row2 is None or box is None or not row2.winfo_exists():
            return
        folded = bool(folded)
        if folded == self._bar_folded:
            return
        self._bar_folded = folded
        bar = row2.master
        try:
            box.grid_forget()
            if folded:
                # 第二行：左侧先留出"第一行最左侧图标（第一个按钮）的左边界"这么宽，
                # 搜索框从该处向右**自适应伸展**（sticky="ew"）——
                # 既满足"靠右"，又保证"搜索框左边不越过第一行最左侧图标的左边"。
                try:
                    _left = max(int(self.tag_page_btn.winfo_x()), 0)   # 相对工具栏的 x
                except Exception as exc:
                    _left = 0
                    _geom_warn("取工具栏左边界", exc)
                if _left <= 0:
                    # 2026-09-15（审核 L-8）：未完成布局时 winfo_x() 会**合法返回 0**（不抛异常），
                    #   原 except 兜底形同虚设 → 这里用"未折行时实测缓存"，再退到常量下限。
                    _left = max(int(getattr(self, "_bar_row2_left", 0)), _BAR_ROW2_MIN_LEFT)
                row2.grid_columnconfigure(0, weight=0, minsize=_left)
                row2.grid_columnconfigure(1, weight=1)
                box.grid(in_=row2, row=0, column=1, sticky="ew")
                # 2026-09-15（审核 L-8）：补 `weight=0`——原先只清 minsize、保留 weight=1，
                #   与恢复分支（下方 3035 行 weight=1, minsize=260）不对称，第一行仍留约 1/3 弹性空白。
                bar.grid_columnconfigure(12, weight=0, minsize=0)
                # ⚠ 必须带**完整参数**恢复本容器的 grid 配置：若写成 `row2.grid()`（无参），
                #   Tk 会把配置重置为默认（columnspan=1、sticky=""、padx=0）→ 本容器只占第 0 列
                #   并把它撑宽，导致**第一行所有按钮整体右移**（2026-09-14 实测踩坑，已修）。
                row2.grid(**_BAR_ROW2_GRID)
            else:
                # 第一行最右（原列位 col12）
                box.grid(in_=bar, row=0, column=12, padx=(4, 6), sticky="ew")
                bar.grid_columnconfigure(12, weight=1, minsize=260)   # 2026-09-15：183 → 260（与创建处一致）
                row2.grid_forget()
        except Exception:
            pass

    def _popup_menu(self, menu, event) -> None:
        """弹出菜单（容错包装：Tk 边界情况下回调可能被零参数调用 → event 为 None 时忽略）

        2026-09-13（专项加固）：与 tooltip 同源问题的统一处理。
        """
        if event is None:
            return
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _project_menu(self, event, project_id: Optional[int], name: str) -> None:
        if event is None:      # Tk 边界情况下可能零参数调用回调 → 直接忽略
            return
        lock_state = "disabled" if self._lock_on else "normal"
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="新增项目类别", state=lock_state, command=self._add_project)
        if project_id is not None:
            m.add_command(label="重命名", state=lock_state,
                          command=lambda: self._rename_project(project_id))
            m.add_command(label="删除", state=lock_state,
                          command=lambda: self._delete_project(project_id))
            # 2026-09-22（用户要求 3-2）：批量删除本分支全部数据（项目类别 = 最高风险层）
            m.add_command(label="🧹 批量删除本分支全部数据…", state=lock_state,
                          command=lambda: self._batch_delete_branch("project", project_id))
            self._add_move_menu_items(m, "project", project_id, lock_state)  # 2026-09-11：上移/下移
        m.tk_popup(event.x_root, event.y_root)

    def _add_project(self) -> None:
        if self._lock_on:
            return
        name = simpledialog.askstring("新增项目类别", "请输入项目类别名称：", parent=self)
        if name and name.strip():
            pid = self.db.add_project(name.strip())
            self._refresh_projects()  # 2026-09-07：先重建列使新按钮出现（_select_project 现仅原地高亮）
            self._select_project(pid)
            self.toast("✅ 已新增项目类别")

    def _rename_project(self, project_id: int) -> None:
        if self._lock_on:
            return
        p = self.db.get_project(project_id)
        if not p:
            return
        name = simpledialog.askstring("重命名项目类别", "请输入新名称：",
                                      initialvalue=p["name"], parent=self)
        if name and name.strip() and name.strip() != p["name"]:
            self.db.rename_project(project_id, name.strip())
            self._refresh_projects()
            self.toast("✅ 已重命名")

    def _delete_project(self, project_id: int) -> None:
        if self._lock_on:
            return
        p = self.db.get_project(project_id)
        if not p:
            return
        n = self.db.count_project_domains(project_id)
        if not messagebox.askyesno("删除确认",
                                   f"⚠️ 确定要删除项目类别【{p['name']}】吗？"):
            return
        fallback = self.db.ensure_project(config.PROJECT_FALLBACK)
        if n:
            if not messagebox.askyesno(
                    "根目录迁移",
                    f"该项目下 {n} 个根目录将移动到【{config.PROJECT_FALLBACK}】，继续？"):
                return
        self.db.delete_project(project_id, fallback_project_id=fallback)
        if self._cur_project_id == project_id:
            self._cur_project_id = None
        self._refresh_projects()
        self._refresh_l0()
        self.toast("已删除项目类别")

    def _choose_project(self, title: str, message: str,
                        include_unassigned: bool = False):
        """弹窗选择一个项目类别；选中返回 (True, pid)，取消返回 None。
        include_unassigned=True 时提供"🗂 未分配"选项（pid 为 None 表示清除归属）。
        2026-08-29：抽取到公共模块 project_chooser 复用（冗余优化）。
        """
        if not self.db.list_projects():
            self.toast("暂无项目类别", color=_C_DANGER)
            return None
        from .project_chooser import choose_project
        return choose_project(self, self.db, title, message, include_unassigned)

    def _move_domain_to_project(self, domain_id: int) -> None:
        """根目录 → 其他项目类别（快捷移动；"复制到"走 M3 的复制/移动对话框）"""
        if self._lock_on:
            return
        d = self.db.get_domain(domain_id)
        if not d:
            return
        r = self._choose_project("移动到项目类别", f"【{d['name']}】移动到：",
                                 include_unassigned=True)
        if r is None:
            return
        self.db.move_domain_to_project(domain_id, r[1])
        self._refresh_projects()
        self._refresh_l0()
        self.toast("✅ 已移动")

    def _refresh_l0(self) -> None:
        """渲染根目录列（当前项目类别下；"未分配"视图显示无归属根目录）。

        2026-09-07（第1条改进）：尚未选择任何项目类别时只给提示、不预填内容。
        """
        self._clear_frame(self.l0_frame)
        self._clear_nav_btns("l0")
        # 2026-09-22（用户要求 1）：按钮已固定在列顶（见 _build_body）→ 此处只更新启用状态
        if self._add_domain_btn is not None and self._add_domain_btn.winfo_exists():
            self._add_domain_btn.configure(
                state=("disabled" if (self._lock_on
                                      or (self._view is None and self._cur_project_id is None))
                       else "normal"))   # 2026-08-21（第005条）：锁定时禁用；未选任何项目时灰显
        if self._view is None and self._cur_project_id is None:
            domains = []                      # 初始态：未选项目类别 → 列留提示
            empty_hint = "先选择上方「项目类别」，\n此处将列出其根目录"
        else:
            domains = (self.db.list_unassigned_domains() if self._cur_project_id is None
                       else self.db.list_domains(project_id=self._cur_project_id))
            empty_hint = "（该视图暂无根目录）"
        for d in domains:
            btn = ctk.CTkButton(self.l0_frame, text=d["name"], anchor="w", height=32,
                                command=lambda did=d["id"]: self._select_domain(did))
            btn.pack(fill="x", padx=6, pady=2)
            self._l0_btns[d["id"]] = btn
            self._l0_styles[d["id"]] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                         btn.cget("text_color"))
            btn.bind("<Button-3>",
                     lambda e=None, did=d["id"], n=d["name"]: self._domain_menu(e, did, n))
            btn.bind("<Enter>",
                     lambda _e=None, did=d["id"]: (self._schedule_select(
                         _HOVER_SELECT_MS, lambda: self._select_domain(did)),
                         self._status_hover_domain(did)))  # 2026-08-18：状态栏显示根目录统计
            btn.bind("<Leave>", lambda _e=None: (self._cancel_select(),
                                            self._status_default()))  # 2026-08-18：离开恢复默认统计
            self._attach_nav_tooltip(btn, d["name"], 6)  # 2026-08-29：长名称悬停提示
        if not domains:
            self._nav_hint(self.l0_frame, empty_hint)
        self._apply_nav_highlight()
        self._scroll_top(self.l0_frame)

    def _select_domain(self, domain_id: int) -> None:
        # 已选中同一根目录且未深入子级时直接返回
        if self._cur_domain_id == domain_id and self._cur_cat_id is None:
            return
        domain_changed = self._cur_domain_id != domain_id
        self._cur_domain_id = domain_id
        self._cur_cat_id = None
        self._view = ("domain", domain_id)
        self._apply_nav_highlight()  # 2026-09-07：根目录列原地高亮（内容未变，不再整列重建）
        if domain_changed:
            self._refresh_l1()   # 一级内容随领域变化
        self._refresh_l2()       # 未选一级分类 → 显示"请先选一级分类"提示
        self._render_entries([], "条目")

    # ---- 选中链路辅助（用于三列高亮） ----
    def _l1_highlight_id(self):
        """当前浏览链路的一级分类 id（用于一级列高亮）"""
        if not self._cur_cat_id:
            return None
        return self.db.category_root(self._cur_cat_id)

    def _l2_highlight_id(self):
        """当前选中的二级分类 id（一级分类自身不在此列高亮）"""
        if not self._cur_cat_id:
            return None
        cat = self.db.get_category(self._cur_cat_id)
        return self._cur_cat_id if (cat and cat["parent_id"] is not None) else None

    def _l2_parent_id(self):
        """二级列当前应展示的父分类 id：一级分类展示其子级；二级叶子展示其兄弟"""
        if not self._cur_cat_id:
            return None
        cat = self.db.get_category(self._cur_cat_id)
        if cat is None:
            return None
        return self._cur_cat_id if cat["parent_id"] is None else cat["parent_id"]

    def _add_l1(self) -> None:
        """一级分类新增按钮：在当前领域下新建一级分类并建立领域关联"""
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止新增
            return
        if self._cur_domain_id is None:
            self.toast("请先选择根目录", color=_C_DANGER)
            return
        name = simpledialog.askstring("新增一级分类", "请输入一级分类名称：", parent=self)
        if name and name.strip():
            self.db.add_category(name.strip(), domain_id=self._cur_domain_id)
            self._refresh_l1()

    def _refresh_l1(self) -> None:
        """渲染一级分类列。

        2026-09-07（第1条改进）：未选择根目录时只显示提示，
        不再把全库一级分类无差别灌入（旧行为会一次性铺满整列）。
        """
        self._clear_frame(self.l1_frame)
        self._clear_nav_btns("l1")
        # 2026-09-22（用户要求 1）：按钮已固定在列顶（见 _build_body）→ 此处只更新启用状态
        #   （2026-08-21 第005条：锁定时禁用新增；2026-09-07：未选根目录时灰显但保留可见）
        if self._add_l1_btn is not None and self._add_l1_btn.winfo_exists():
            self._add_l1_btn.configure(
                state="disabled" if (self._lock_on or self._cur_domain_id is None) else "normal")
        if self._cur_domain_id is None:
            self._nav_hint(self.l1_frame, "先在上方选择「根目录」，\n此处将列出一级分类")
            self._apply_nav_highlight()
            self._scroll_top(self.l1_frame)
            return
        cats = self.db.list_categories(domain_id=self._cur_domain_id, parent_id=None)
        for c in cats:
            btn = ctk.CTkButton(self.l1_frame, text=c["name"], anchor="w", height=30,
                                command=lambda cid=c["id"]: self._select_category(cid))
            btn.pack(fill="x", padx=6, pady=2)
            self._l1_btns[c["id"]] = btn
            self._l1_styles[c["id"]] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                         btn.cget("text_color"))
            btn.bind("<Button-3>",
                     lambda e=None, cid=c["id"], n=c["name"]: self._category_menu(e, cid, n))
            btn.bind("<Enter>",
                     lambda _e=None, cid=c["id"]: (self._schedule_select(
                         _HOVER_SELECT_MS, lambda: self._select_category(cid)),
                         self._status_hover_cat(cid)))  # 2026-08-18：状态栏显示一级分类统计
            btn.bind("<Leave>", lambda _e=None: (self._cancel_select(),
                                            self._status_default()))  # 2026-08-18：离开恢复默认统计
            self._attach_nav_tooltip(btn, c["name"], 10)  # 2026-08-29：长名称悬停提示
        if not cats:
            self._nav_hint(self.l1_frame, "（该根目录暂无一级分类）")
        self._apply_nav_highlight()
        self._scroll_top(self.l1_frame)

    def _add_l2(self) -> None:
        """二级分类新增按钮：在当前分类下新建子分类"""
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止新增
            return
        if self._cur_cat_id is None:
            self.toast("请先在左侧选中分类", color=_C_DANGER)
            return
        name = simpledialog.askstring("新增二级分类", "请输入二级分类名称：", parent=self)
        if name and name.strip():
            self.db.add_category(name.strip(), parent_id=self._cur_cat_id)
            self._refresh_l2()

    def _refresh_l2(self) -> None:
        self._clear_frame(self.l2_frame)
        self._clear_nav_btns("l2")
        parent_id = self._l2_parent_id()
        # 2026-09-22（用户要求 1）：按钮已固定在列顶（见 _build_body）→ 此处只更新启用状态
        #   （2026-08-21 第005条：锁定时禁用新增；2026-09-07：未选分类时灰显但保留可见）
        if self._add_l2_btn is not None and self._add_l2_btn.winfo_exists():
            self._add_l2_btn.configure(
                state="disabled" if (self._lock_on or self._cur_cat_id is None) else "normal")
        if parent_id is not None:
            subs = self.db.list_categories(parent_id=parent_id)
            for c in subs:
                btn = ctk.CTkButton(self.l2_frame, text=c["name"], anchor="w", height=30,
                                    command=lambda cid=c["id"]: self._select_category(cid))
                btn.pack(fill="x", padx=6, pady=2)
                self._l2_btns[c["id"]] = btn
                self._l2_styles[c["id"]] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                             btn.cget("text_color"))
                btn.bind("<Button-3>",
                         lambda e=None, cid=c["id"], n=c["name"]: self._category_menu(e, cid, n))
                btn.bind("<Enter>",
                         lambda _e=None, cid=c["id"]: (self._schedule_select(
                             _HOVER_SELECT_MS, lambda: self._select_category(cid)),
                             self._status_hover_cat(cid)))  # 2026-08-18：状态栏显示二级分类统计
                btn.bind("<Leave>", lambda _e=None: (self._cancel_select(),
                                                self._status_default()))  # 2026-08-18：离开恢复默认统计
                self._attach_nav_tooltip(btn, c["name"], 10)  # 2026-08-29：长名称悬停提示
            if not subs:
                self._nav_hint(self.l2_frame, "（该分类暂无二级分类）")
        else:
            self._nav_hint(self.l2_frame, "先选择一级分类，\n此处将列出二级分类")
        self._apply_nav_highlight()
        self._scroll_top(self.l2_frame)

    def _select_category(self, cat_id: int) -> None:
        """点击/悬停分类：高亮原地更新；一级展开时重建二级列。

        2026-09-07（阶段2）：任一分级分类都显示其"本级条目"（主挂靠∪关联），
        即使它仍有子分类——子分类照常显示在其二级列。
        """
        if self._cur_cat_id == cat_id:
            return
        if not self._confirm_unsaved():
            return
        self._cur_cat_id = cat_id
        self._last_cat_id = cat_id   # 2026-09-15（批次 8-A）：记住"最近一次有分类的视图"
        if self.db.category_has_children(cat_id):
            self._refresh_l2()          # 有子级：重建二级列展示子分类
            self._view = ("cat", cat_id)
            self._render_entries(
                self.db.list_entries(cat_id, order_by=self._entry_sort), "条目")
            self._apply_nav_highlight()
        else:
            cat = self.db.get_category(cat_id)
            if cat is not None and cat["parent_id"] is None:
                # 2026-08-18 13:37：修复残留——无子分类的一级分类（如"按年代"维度）也须重建二级列，
                # 否则从其他一级分类移入时，上一分类的二级按钮不会消失、造成误认。
                self._refresh_l2()
            self._view = ("cat", cat_id)
            self._render_entries(
                self.db.list_entries(cat_id, order_by=self._entry_sort), "条目")
            self._apply_nav_highlight()  # 原地更新高亮，不重建按钮，杜绝闪烁

    # ------------------------------------------------------------------ #
    # 未分类 / 收藏 / 搜索
    # ------------------------------------------------------------------ #
    def _show_uncategorized(self) -> None:
        if not self._confirm_unsaved():
            return
        self._view = ("uncat", None)
        self._render_entries(self.db.list_uncategorized(), "📂 未分类")

    def _show_favorites(self) -> None:
        if not self._confirm_unsaved():
            return
        self._view = ("fav", None)
        self._render_entries(self.db.list_favorites(), "⭐ 常用")

    # 2026-09-14（用户要求"无标签条目"入口 方案 A）：工具栏"🏷 无标条目"按钮的响应。
    #   列出所有**没有任何标签**的条目（按修改时间倒序），标题带总数；用户可逐条点选，
    #   在详情区打标签（详情区标签编辑区已具备）。视图 kind="untagged"、ref=关键词（""＝不过滤），
    #   与搜索框 `#无标签` 语法（方案 C）共用同一渲染分支。
    #   2026-09-14（用户要求 2）：本视图的 _entries_all 即"全部无标签条目"，故
    #   打开「🤖 批量打标」后选「范围＝当前列表」即可对这批条目批量打标。
    def _show_untagged(self) -> None:
        if not self._confirm_unsaved():
            return
        self._view = ("untagged", "")
        self._restore_view()

    def _cancel_search_timer(self) -> None:
        """取消尚未到点的"搜索防抖"定时器（2026-09-10，用户要求）"""
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
            self._search_after = None

    @staticmethod
    def _parse_search_query(text: str):
        """解析搜索框文本 → (标签名列表, 关键词)。

        2026-09-13（1-C-4b）：`#` 开头的词视为"标签过滤"，其余词拼成关键词。
        例："#写实 电影" → (["写实"], "电影")；"#写实 #电影感" → (["写实","电影感"], "")。
        """
        tags, words = [], []
        for tok in (text or "").split():
            if tok.startswith("#") and len(tok) > 1:
                n = tok[1:].strip()
                if n and n not in tags:
                    tags.append(n)
            else:
                words.append(tok)
        return tags, " ".join(words)

    def _on_search_typing(self, _event=None) -> None:
        """搜索框输入中：重置防抖定时器，**停止输入 _SEARCH_DEBOUNCE_MS 后**才真正查询。

        2026-09-10（用户要求）：此前每敲一个字符就触发一次"全字段检索 + 列表重建"，
        输入长句时持续刷新、卡顿；改为停顿后才查询。回车或点击右侧"🔍"仍可立即查询。
        """
        if _event is not None and getattr(_event, "keysym", "") == "Return":
            return  # 回车已由 <Return> 绑定立即查询，无需再排队一次
        self._cancel_search_timer()
        self._search_after = self.after(_SEARCH_DEBOUNCE_MS, self._on_search_key)

    def _on_search_key(self, _event=None) -> None:
        """真正执行搜索（防抖到点 / 回车 / 点击"🔍"三处共用）。

        2026-09-13（1-C-4b）：支持 `#标签` 语法——`#` 开头的词作为标签过滤，其余为关键词。
        例：`#写实 电影` ＝（带"写实"标签）∧（全字段含"电影"）。
        """
        self._cancel_search_timer()
        text = self.search_entry.get().strip()
        tags, kw = self._parse_search_query(text)
        # 2026-09-14（审核修复 P3）：第一次进入搜索时记住"搜索前的浏览视图"，
        # 清空搜索框时就能回到原来的分类/收藏/标签等视图（此前会停在旧搜索结果上）。
        cur = self._view
        _in_search = (cur is None
                      or (isinstance(cur, tuple) and cur and cur[0] in ("search", "search_tag")))
        if (tags or kw) and not _in_search:
            self._view_before_search = cur
        # 2026-09-14（用户要求"无标签条目"入口 方案 C）：`#无标签` / `#none` 保留词。
        #   命中则切到"无标签条目"视图（可带关键词过滤）；若同时写了其他真实 `#标签`，
        #   语义自相矛盾（无标签的条目不可能带这些标签）→ 明确提示，不静默忽略用户输入。
        if any(t in _UNTAGGED_ALIASES for t in tags):
            rest = [t for t in tags if t not in _UNTAGGED_ALIASES]
            if rest:
                self._view = None
                self._render_entries(
                    [], "🏷 “#无标签”不能与 #" + "、#".join(rest) + " 同时使用")
                return
            self._view = ("untagged", kw)
            self._restore_view()
            return
        if tags:
            ids, names = [], []
            for n in tags:
                t = self.db.get_tag_by_name(n)
                if t:
                    ids.append(t["id"])
                    names.append(t["name"])
            if not ids:
                self._view = None
                self._render_entries([], "🏷 未找到标签：" + "、".join(tags))
                return
            title = "🏷 " + " ∧ ".join(names) + (f"　+　“{kw}”" if kw else "")
            self._view = ("search_tag", (ids, kw, title))
            self._restore_view()
            return
        if not kw:
            # 清空搜索框（既无标签也无关键词）→ 回到搜索前的浏览视图
            if self._view_before_search is not None or _in_search:
                self._view = self._view_before_search
                self._view_before_search = None
            self._restore_view()
            return
        self._view = ("search", kw)
        self._render_entries(self.db.search(kw), f"搜索结果（{kw}）")

    def _on_search_click(self) -> None:
        """点击搜索框右侧"🔍"图标按钮：立即按输入内容执行搜索并保持输入焦点（2026-09-10，用户要求 2）"""
        self._on_search_key()   # 内部已取消待执行的防抖定时器
        try:
            self.search_entry.focus_set()
        except Exception:
            pass

    def _on_search_clear(self) -> None:
        """点击搜索框右侧"✕"：**一次清空**全部输入（2026-09-17 用户要求 1）。

        原先清空只能逐字退格删除，长关键词很费事。此处与"逐字删空"走同一条通路
        （`_on_search_key()`：清空后自动回到**搜索前的浏览视图**），并保持输入焦点，
        便于立刻输入新的关键词。空框时点击无副作用。
        """
        ent = getattr(self, "search_entry", None)
        if ent is not None and ent.winfo_exists():
            try:
                ent.delete(0, "end")
            except Exception:
                pass
        self._on_search_key()      # 内部已取消待执行的防抖定时器
        try:
            if ent is not None and ent.winfo_exists():
                ent.focus_set()
        except Exception:
            pass

    def _restore_view(self) -> None:
        """重新渲染当前浏览视图（搜索清空/切换视图/保存后刷新）"""
        if self._view is None:
            return
        kind, ref = self._view
        if kind == "project":
            self._cur_project_id = ref
            self._refresh_projects()
            self._refresh_l0()
            self._refresh_l1()
            self._render_entries([], "条目")
        elif kind == "domain":
            self._refresh_l1()
            self._render_entries([], "条目")
        elif kind == "cat":
            self._render_entries(
                self.db.list_entries(ref, order_by=self._entry_sort), "条目")
        elif kind == "uncat":
            self._render_entries(self.db.list_uncategorized(), "📂 未分类")
        elif kind == "fav":
            self._render_entries(self.db.list_favorites(), "⭐ 常用")
        elif kind == "untagged":
            # 2026-09-14（"无标签条目"入口 方案 A + 方案 C 共用）：ref = 关键词（""＝不过滤）。
            #   标题里的总数每次重建都重算，故"给某条打了标签"后该条会立即移出并更新计数。
            # 2026-09-15（审核 L-9）：**本通路补容错**——原先与"计数通路"（有 try + −1 哨兵）不一致，
            #   库被锁/损坏/缺表时异常会冒泡到 Tk 回调（无控制台 EXE 下表现为"点了没反应"）。
            try:
                items = self.db.list_untagged()
                if ref:
                    keep = {e["id"] for e in self.db.search(ref)}
                    items = [e for e in items if e["id"] in keep]
            except Exception as exc:
                _geom_warn("无标签条目列表", exc)
                self._render_entries([], "🏷 无标签条目（读取失败，请检查数据文件）")
                return
            title = ("🏷 无标签条目"
                     + (f"　+　“{ref}”" if ref else "")
                     + f"（共 {len(items)} 条）")
            self._render_entries(items, title)
        elif kind == "search":
            self._render_entries(self.db.search(ref), f"搜索结果（{ref}）")
        elif kind == "tag":
            # 2026-09-13（1-C-3b）：标签视图（跨分类），ref=(tag_ids, mode, title)
            ids, mode, title = ref
            self._render_entries(self.db.list_entries_by_tags(ids, mode), title)
        elif kind == "search_tag":
            # 2026-09-13（1-C-4b）："#标签 + 关键词"组合搜索，ref=(tag_ids, 关键词, 标题)
            ids, kw, title = ref
            items = self.db.list_entries_by_tags(ids, "and") if ids else []
            if kw:
                keep = {e["id"] for e in self.db.search(kw)}
                items = [e for e in items if e["id"] in keep]
            self._render_entries(items, title)

    # ------------------------------------------------------------------ #
    # 条目区（卡片/列表视图切换）
    # ------------------------------------------------------------------ #
    _NO_ADD = -1  # 视图不可新增条目的哨兵值（分类 id 恒为正）

    def _add_context_target(self):
        """当前浏览视图对应的"新增目标"：叶子分类视图→分类 id；未分类视图→None；
        其余视图不可新增，返回 _NO_ADD。供新增态自动退出时判断目标是否已改变。
        """
        if not self._view:
            return self._NO_ADD
        kind = self._view[0]
        if kind == "cat":
            return self._cur_cat_id if self._cur_cat_id is not None else self._NO_ADD
        if kind == "uncat":
            return None
        return self._NO_ADD

    def _add_available(self) -> bool:
        """就地新增是否可用：当前"条目"列表里能新增时即可
        ——未分类视图，或选中任一分级分类（含仍带子分类的一级，本级条目可直接新增）；
        锁定态、项目/根目录/搜索/常用视图禁用。

        2026-09-07（阶段2）：放宽为"选中任意分类均可新增"（各分类都可持有本级条目）。
        """
        if self._lock_on or not self._view:
            return False
        kind = self._view[0]
        if kind == "uncat":
            return True
        if kind != "cat":
            return False
        return self._cur_cat_id is not None

    def _set_view_mode(self, value: str) -> None:
        self._view_mode = "card" if value == "卡片" else "list"
        self._restore_view()

    def _render_entries(self, entries, title: str) -> None:
        # 2026-09-14（性能优化，用户确认"默认前 200 条 + 显示更多"）：
        #   实测每条例目渲染约 54ms（100 条≈5.4 秒、2329 条≈137 秒），瓶颈在逐个创建 UI 控件。
        #   故先记下**完整**列表，再按上限截断渲染；其余由「显示更多 / 全部显示」按需追加。
        self._entries_all = list(entries or [])
        self._entries_title = title
        _lim = int(getattr(self, "_entry_render_limit", ENTRY_RENDER_LIMIT) or 0)
        shown = (self._entries_all[:_lim]
                 if (_lim and len(self._entries_all) > _lim) else list(self._entries_all))
        entries = shown

        self.entry_frame.configure(label_text=title)
        self._hide_entry_overview()  # 2026-09-09：列表重建前收起"全部条目名"浮层
        self._entry_ov_names = []
        self._entry_ov_ids = []      # 2026-09-11 09:27（用户要求 3）：浮层高亮定位用
        self._entry_ov_cur = None    # 2026-09-11 09:27：列表重建后清空"光标所在条目"
        self._clear_frame(self.entry_frame)

        # 2026-09-06：浏览视图离开新增目标时，退出"新增条目"态并清空残留空表单
        # （未保存输入已由各切换入口的 _confirm_unsaved 处理，此处只管无脏内容场景）
        if self._adding_new and not self._detail_dirty:
            if self._add_context_target() != self._add_target:
                self._adding_new = False
                self._add_target = None
                self._show_detail(None)

        # 2026-09-22（用户要求 1）：条目区"新增条目"主按钮已固定在区域顶部（见 _build_body）
        #   ——原先在此处重建按钮，会随条目列表一起滚动；现只更新其启用状态。
        if self._add_entry_btn is not None and self._add_entry_btn.winfo_exists():
            self._add_entry_btn.configure(
                state="normal" if self._add_available() else "disabled")

        # 2026-09-14：被截断时显示"已显示前 N 条 · 剩余 M 条" + 「显示更多 / 全部显示」
        # （整行 fill=x，适配条目区仅 224px 的窄宽度；不再截断后按钮置灰）
        self._entry_more_btns = []
        self._entry_count_lbl = None
        if len(shown) < len(self._entries_all):
            self._entry_count_lbl = ctk.CTkLabel(
                self.entry_frame, text=self._entry_count_text(),
                text_color="#8a94a6", font=("Microsoft YaHei", 10))
            self._entry_count_lbl.pack(fill="x", padx=8, pady=(2, 0))
            _b1 = ctk.CTkButton(self.entry_frame,
                                text=f"▼ 显示更多（+{ENTRY_RENDER_STEP}）", height=26,
                                font=("Microsoft YaHei", 11), fg_color="#25639c",
                                command=self._show_more_entries)
            _b1.pack(fill="x", padx=6, pady=(2, 0))
            _b2 = ctk.CTkButton(self.entry_frame,
                                text=f"全部显示（共 {len(self._entries_all)} 条）", height=24,
                                font=("Microsoft YaHei", 11), fg_color="#6b7280",
                                command=lambda: self._show_more_entries(all_=True))
            _b2.pack(fill="x", padx=6, pady=(2, 0))
            self._entry_more_btns = [_b1, _b2]

        toggle = ctk.CTkFrame(self.entry_frame, fg_color="transparent")
        toggle.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(toggle, text=f"共 {len(self._entries_all)} 条",
                     text_color="gray").pack(side="left")
        switch = ctk.CTkSegmentedButton(toggle, values=["卡片", "列表"], width=150,
                                        command=self._set_view_mode)
        switch.set("卡片" if self._view_mode == "card" else "列表")
        switch.pack(side="right")

        if not entries:
            self._entry_render_shown = []
            self._entry_tags_cache = {}
            ctk.CTkLabel(self.entry_frame, text="（暂无条目）",
                         text_color="gray").pack(pady=30)
            self._scroll_top(self.entry_frame)  # 2026-09-07（第4条改进）
            return
        # 2026-09-14：一次性预取标签（消除"逐条查询"的 N+1），再渲染
        self._entry_tags_cache = self._prefetch_entry_tags(entries)
        self._entry_render_shown = []
        self._append_entry_items(entries)
        self._scroll_top(self.entry_frame)  # 2026-09-07（第4条改进）：切换分类后条目列回到顶部

    # ---- 条目区：渲染上限相关辅助（2026-09-14 性能优化） ---- #
    def _entry_count_text(self) -> str:
        """「已显示前 N 条 · 剩余 M 条（共 X 条）」提示文本。"""
        total = len(getattr(self, "_entries_all", []) or [])
        cur = len(getattr(self, "_entry_render_shown", []) or [])
        return f"已显示前 {cur} 条 · 剩余 {max(0, total - cur)} 条（共 {total} 条）"

    def _prefetch_entry_tags(self, entries) -> dict:
        """一次性预取条目标签（消除 N+1 逐条查询；仅在开启"条目处显示标签"时执行）。"""
        if not getattr(self, "_show_tags_in_list", False):
            return {}
        try:
            return self.db.list_tags_for_entries([e["id"] for e in entries])
        except Exception:
            return {}

    def _append_entry_items(self, items) -> None:
        """把条目**追加**渲染到条目区末尾（供"显示更多"复用，不重建已渲染部分）。"""
        items = list(items or [])
        if not items:
            return
        for e in items:
            if self._view_mode == "card":
                self._add_card(e)
            else:
                self._add_row(e)
        self._entry_render_shown = (list(getattr(self, "_entry_render_shown", []) or [])
                                   + items)
        self._entry_ov_names = [x["name"] for x in self._entry_render_shown]
        self._entry_ov_ids = [x["id"] for x in self._entry_render_shown]

    def _show_more_entries(self, all_: bool = False) -> None:
        """追加渲染更多条目（只渲染新增部分；不重建已有控件，避免重复开销）。"""
        total = len(getattr(self, "_entries_all", []) or [])
        cur = len(getattr(self, "_entry_render_shown", []) or [])
        if cur >= total:
            return
        if all_:
            # 2026-09-14 13:40（用户确认）：超大集合先二次确认，避免误点后界面长时间无响应
            if total > ENTRY_ALL_CONFIRM_THRESHOLD:
                _secs = int(total * 0.054) + 1      # 按实测 54ms/条 估算
                if not messagebox.askyesno(
                        "全部显示",
                        f"将一次渲染全部 {total} 条条目，约需 {_secs} 秒"
                        "（渲染期间界面无响应、不可中断）。\n\n"
                        "建议改用「▼ 显示更多」分步加载。确定要全部显示吗？", parent=self):
                    return
            self._entry_render_limit = 0
            end = total
        else:
            self._entry_render_limit = (int(getattr(self, "_entry_render_limit",
                                                   ENTRY_RENDER_STEP) or 0)
                                        + ENTRY_RENDER_STEP)
            end = min(self._entry_render_limit, total)
        # 先给出"已点击"的反馈（渲染期间界面会短暂无响应，与既有行为一致）
        for b in (getattr(self, "_entry_more_btns", None) or []):
            try:
                b.configure(state="disabled")
            except Exception:
                pass
        try:
            self.update_idletasks()
        except Exception:
            pass

        self._append_entry_items(self._entries_all[cur:end])
        # 追加部分的标签：只补查新增条目
        if getattr(self, "_show_tags_in_list", False):
            try:
                self._entry_tags_cache.update(
                    self.db.list_tags_for_entries([e["id"] for e in self._entries_all[cur:end]]))
            except Exception:
                pass
        try:
            if self._entry_count_lbl is not None and self._entry_count_lbl.winfo_exists():
                self._entry_count_lbl.configure(text=self._entry_count_text())
            done = len(self._entry_render_shown) >= total
            if not done and self._entry_more_btns:
                self._entry_more_btns[0].configure(
                    text=f"▼ 显示更多（+{ENTRY_RENDER_STEP}）")
            for b in (self._entry_more_btns or []):
                if b.winfo_exists():
                    b.configure(state=("disabled" if done else "normal"))
        except Exception:
            pass

    def _entry_tags_display(self, entry_id: int):
        """条目区标签显示 → (短文本, 完整文本)。

        2026-09-13（1-C-4b）：设置关闭或无标签时返回 ("", "")；短文本超长按字符截断
        （条目区不加宽，故用截断 + 悬浮查看的处理方式）。
        2026-09-14：优先用**一次性预取的缓存** `_entry_tags_cache`（消除逐条查询的 N+1）。
        """
        if not self._show_tags_in_list:
            return "", ""
        cache = getattr(self, "_entry_tags_cache", None)
        if cache is not None and entry_id in cache:
            names = cache.get(entry_id) or []
        else:
            try:
                names = self.db.list_entry_tag_names(entry_id)
            except Exception:
                names = []
        if not names:
            return "", ""
        full = "、".join(names)
        short = full if len(full) <= 16 else full[:15] + "…"
        return short, full

    def _add_card(self, e: dict) -> None:
        card = ctk.CTkFrame(self.entry_frame, corner_radius=8)
        card.pack(fill="x", padx=6, pady=3)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(6, 0))
        star = "★ " if e["is_favorite"] else ""
        ctk.CTkLabel(top, text=f"{star}{e['name']}",
                     font=ui_appearance.font("entry_rows", _ENTRY_NAME_FONT_CARD,
                                             self._ui_appearance),
                     anchor="w").pack(side="left")
        # 2026-09-13（1-C-4b）：可选显示标签（默认关）
        _short, _full = self._entry_tags_display(e["id"])
        if _short:
            _tl = ctk.CTkLabel(card, text="🏷 " + _short, text_color=_C_TAG,
                               font=("Microsoft YaHei", 10), anchor="w")
            _tl.pack(fill="x", padx=8, pady=(1, 0))
            _FieldTooltip(_tl, "标签：" + _full)
        summary = (e["intro"] or "").strip() or "（无介绍）"
        ctk.CTkLabel(card, text=summary, wraplength=330, justify="left",
                     text_color="gray", anchor="w").pack(fill="x", padx=8, pady=(2, 6))
        self._bind_card_events(card, e)

    def _add_row(self, e: dict) -> None:
        row = ctk.CTkFrame(self.entry_frame, corner_radius=6)
        row.pack(fill="x", padx=6, pady=1)
        star = "★ " if e["is_favorite"] else ""
        # 2026-09-13（1-C-4b）：开启"在条目处显示标签"时，名称下再加一行小字标签
        _short, _full = self._entry_tags_display(e["id"])
        if _short:
            _left = ctk.CTkFrame(row, fg_color="transparent")
            _left.pack(side="left", padx=8, pady=3)
            ctk.CTkLabel(_left, text=f"{star}{e['name']}",
                         font=ui_appearance.font("entry_rows", _ENTRY_NAME_FONT_LIST,
                                                 self._ui_appearance),
                         anchor="w").pack(anchor="w")
            _tl = ctk.CTkLabel(_left, text="🏷 " + _short, text_color=_C_TAG,
                               font=("Microsoft YaHei", 10), anchor="w")
            _tl.pack(anchor="w")
            _FieldTooltip(_tl, "标签：" + _full)
        else:
            ctk.CTkLabel(row, text=f"{star}{e['name']}",
                         font=ui_appearance.font("entry_rows", _ENTRY_NAME_FONT_LIST,
                                                 self._ui_appearance),
                         anchor="w").pack(side="left", padx=8, pady=4)
        summary = ((e["intro"] or "").replace("\n", " ")[:36]) or "（无介绍）"
        ctk.CTkLabel(row, text=summary, text_color="gray", anchor="e",
                     wraplength=240).pack(side="right", padx=8)
        self._bind_card_events(row, e)

    def _bind_card_events(self, widget, e: dict) -> None:
        entry_id = e["id"]

        def _click(_ev, eid=entry_id):
            # 2026-09-22：点击前先取消"悬停延迟选中"定时器。
            #   原逻辑点击时不取消失效，会出现"悬停 200ms 定时器"与"点击"先后各触发
            #   一次 _select_entry，导致详情区被重建两次（用户反馈的多次刷新问题）。
            self._cancel_select()
            self._select_entry(eid)

        def _menu(_ev, eid=entry_id):
            self._entry_menu(_ev, eid)

        def _hover(_ev, ent=e):
            self._schedule_select(_HOVER_SELECT_MS, lambda: self._select_entry(ent["id"]))
            self._status_hover_entry(ent)  # 2026-08-18：状态栏显示条目链路统计
            # 2026-09-11 09:27（用户要求 3）：记录光标所在条目，供浮层内深色高亮
            self._entry_ov_set_current(ent["id"])

        def _leave(_ev):
            self._cancel_select()
            self._status_default()  # 2026-08-18：离开恢复默认统计

        # 2026-09-13（1-C-4b）：改为"递归绑定全部后代"——"在条目处显示标签"会引入嵌套子控件
        # （列表模式把名称/标签放在子框架内），若只绑一层，点名称将无法选中条目。
        for w in [widget] + list(self._walk_widgets(widget)):
            w.bind("<Button-1>", _click)
            w.bind("<Button-3>", _menu)
            w.bind("<Enter>", _hover)
            w.bind("<Leave>", _leave)
            # 2026-09-09：悬停条目卡片同样触发"全部条目名"浮层（add 保留原有悬停选中）
            w.bind("<Enter>", self._entry_ov_enter, add="+")
            w.bind("<Leave>", self._entry_ov_leave, add="+")

    # ------------------------------------------------------------------ #
    # 条目列悬停"全部条目名"浮层（2026-09-09：列宽加宽后仍看不全时的兜底）
    # ------------------------------------------------------------------ #
    def _cancel_entry_overview(self) -> None:
        if self._entry_ov_after is not None:
            try:
                self.after_cancel(self._entry_ov_after)
            except Exception:
                pass
            self._entry_ov_after = None

    def _entry_ov_enter(self, _event=None) -> None:
        # 2026-09-12（用户要求 3）：总开关关闭时不弹"条目名称一览"
        if not self._float_tips_on or not self._entry_ov_names:
            return
        # 2026-09-12（用户要求 2）：详情区字段浮窗打开时不叠加"条目名称一览"
        if self._tip_popup is not None:
            return
        if _event is not None and getattr(_event, "y_root", None):
            self._entry_ov_y = _event.y_root
        self._cancel_entry_overview()
        # 稍长的延迟：快速扫读条目时不至于频繁弹层
        self._entry_ov_after = self.after(500, self._show_entry_overview)

    def _entry_ov_set_current(self, entry_id) -> None:
        """记录"光标所在条目"并刷新浮层高亮（2026-09-11 09:27 用户要求 3）"""
        self._entry_ov_cur = entry_id
        self._highlight_entry_overview()

    def _highlight_entry_overview(self) -> None:
        """把光标所在条目在浮层列表中深色高亮（2026-09-11 09:27 用户要求 3）。

        浮层尚未打开时只记录状态；浮层打开后由 _show_entry_overview 调用本方法立即上色，
        鼠标移到别的条目时再动态切换高亮行。
        """
        lb = self._entry_ov_listbox
        if lb is None or self._entry_ov_cur is None:
            return
        try:
            if not lb.winfo_exists():
                return
            idx = self._entry_ov_ids.index(self._entry_ov_cur)
            # 2026-09-15（批次 4）：复原色用与浮层相同的配色（用户若改过背景/文字色，此处同步）
            _bg, _fg = getattr(self, "_entry_ov_colors", (_ENTRY_OV_BG, _ENTRY_OV_FG))
            if self._entry_ov_hl is not None and self._entry_ov_hl != idx:
                lb.itemconfig(self._entry_ov_hl, background=_bg,
                              foreground=_fg)   # 复原上一行默认配色
            lb.itemconfig(idx, background=_ENTRY_OV_HL_BG, foreground=_ENTRY_OV_HL_FG)
            self._entry_ov_hl = idx
            lb.see(idx)   # 条目多于浮层可视行数时，把高亮行滚入可视区
        except Exception:
            pass   # 控件已销毁/条目已不在列表时忽略，绝不影响浮层原有行为

    def _entry_ov_leave(self, _event=None) -> None:
        # 2026-09-10（用户要求 4-二）：不再直接关闭，改为延时判断光标是否已移到浮层内
        self._cancel_entry_overview()
        self._entry_ov_after = self.after(200, self._entry_ov_maybe_hide)

    def _entry_ov_maybe_hide(self) -> None:
        """延时判断是否关闭浮层（2026-09-10，用户要求 4-二）。

        光标已离开条目列时，若仍停在浮层内（例如正把鼠标移向浮层的滚动条），则**不关闭**，
        改为继续轮询；只有条目列与浮层都不在光标下才真正关闭，使浮层内容可被滚动。
        """
        self._cancel_entry_overview()
        if self._entry_ov_popup is None:
            return
        if self._pointer_in_overview_area():
            self._entry_ov_after = self.after(150, self._entry_ov_maybe_hide)
            return
        self._hide_entry_overview()

    def _pointer_in_overview_area(self) -> bool:
        """光标当前是否位于条目列或浮层窗口内（2026-09-10，用户要求 4-二）"""
        try:
            px, py = self.winfo_pointerxy()
        except Exception:
            return False
        for w in (self._entry_ov_popup, self.entry_frame):
            if w is None:
                continue
            try:
                if not w.winfo_exists() or not w.winfo_ismapped():
                    continue
                x, y = w.winfo_rootx(), w.winfo_rooty()
                if x <= px <= x + w.winfo_width() and y <= py <= y + w.winfo_height():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _widget_inside(widget, ancestor) -> bool:
        """判断控件是否在指定祖先控件之内（2026-09-10，用户要求 4-一）"""
        w = widget
        while w is not None:
            if w is ancestor:
                return True
            try:
                w = w.master
            except Exception:
                return False
        return False

    def _entry_ov_wheel(self, event=None) -> None:
        """条目区滚动时，浮层"条目名称一览"按相同滚动比例同步滚动（2026-09-10，用户要求 4-一）"""
        if self._entry_ov_popup is None or self._entry_ov_listbox is None:
            return
        if event is None or not self._widget_inside(getattr(event, "widget", None),
                                                   self.entry_frame):
            return
        try:
            lb = self._entry_ov_listbox
            if not lb.winfo_exists():
                return
            canvas = getattr(self.entry_frame, "_parent_canvas", None)
            if canvas is None or not canvas.winfo_exists():
                return
            first, _last = canvas.yview()   # 条目列与浮层列表条目一一对应，按比例同步
            lb.yview_moveto(first)
        except Exception:
            pass

    def _hide_entry_overview(self) -> None:
        self._cancel_entry_overview()
        if self._entry_ov_popup is not None:
            try:
                self._entry_ov_popup.destroy()
            except Exception:
                pass
            self._entry_ov_popup = None
        self._entry_ov_listbox = None
        self._entry_ov_hl = None   # 2026-09-11 09:27（用户要求 3）：浮层已销毁，清空高亮行记录

    # ---------------- 外观设置叠加（2026-09-15 批次 4） ---------------- #
    def _default_ctk_font(self) -> tuple:
        """CTk 当前默认字体 (family, size, "normal")。

        用途：把外观设置叠加到**未显式指定字体**的控件（如 ⑧/⑨ 提示词文本框）上时，
        需要知道"原值"是什么，才能真正做到"未设置＝与改动前完全一致"。
        """
        try:
            if getattr(self, "_ctk_default_font", None) is None:
                _f = ctk.CTkFont()
                self._ctk_default_font = (str(_f.cget("family")), int(_f.cget("size")), "normal")
            return self._ctk_default_font
        except Exception:
            return ("Microsoft YaHei", 14, "normal")

    def _look_kwargs(self, group: str, base_font=None) -> dict:
        """返回"外观设置"叠加后的控件参数（供创建控件时 `**` 展开）。

        **未设置任何字体/颜色时返回空 dict** ⇒ 调用处的控件与改动前**完全一致**（零差异）。
        """
        data = self._ui_appearance or {}
        g = (data.get(group) or {})
        kw = {}
        if g.get("family") or g.get("size"):
            kw["font"] = ui_appearance.font(group, base_font or self._default_ctk_font(), data)
        _fg = ui_appearance.color(group, data, "fg", None)
        if _fg:
            kw["text_color"] = _fg
        return kw

    def _show_entry_overview(self) -> None:
        """在条目列左侧浮出当前分类下全部条目名称（可滚动，超长自动横向滚动）。"""
        if not self._entry_ov_names or self._entry_ov_popup is not None:
            return
        names = self._entry_ov_names
        popup = tk.Toplevel(self.entry_frame)
        popup.wm_overrideredirect(True)
        # 2026-09-15（批次 4）：本浮层的字体（组 entry_ov）与配色（bg/fg）可由设置叠加——
        #   未设置时与原值**完全一致**；标题栏仍是主题蓝，不随背景色变化。
        _ov_bg = ui_appearance.color("entry_ov", self._ui_appearance, "bg", _ENTRY_OV_BG)
        _ov_fg = ui_appearance.color("entry_ov", self._ui_appearance, "fg", _ENTRY_OV_FG)
        self._entry_ov_colors = (_ov_bg, _ov_fg)   # 供"鼠标移动高亮复原"使用同一套配色
        popup.configure(bg=_ov_bg)
        # 2026-09-12（用户要求 1）：浮层内文字字号＝条目区"条目名称"字号（随当前视图模式）
        name_font = (_ENTRY_NAME_FONT_CARD if self._view_mode == "card"
                     else _ENTRY_NAME_FONT_LIST)
        name_font = ui_appearance.font("entry_ov", name_font, self._ui_appearance)
        head = tk.Label(popup, text=f"📋 条目名称一览（共 {len(names)} 条）",
                        bg="#25639c", fg="white", padx=8, pady=4,
                        font=name_font)
        head.pack(fill="x")
        body = tk.Frame(popup, bg=_ov_bg)
        body.pack(fill="both", expand=True)
        sb = tk.Scrollbar(body)
        sb.pack(side="right", fill="y")
        max_len = max((len(n) for n in names), default=4)
        width = max(min(max_len + 4, 60), 24)
        # 2026-09-12（用户要求）：最小高度固定为可显示 25 行文本（恒为 25 行高，条目更多时滚动）
        # 2026-09-16（批次 11-1，用户反馈 4）：再受"屏幕工作区可容纳行数"封顶——
        #   小屏（如 768 高）上原恒 25 行会过高，导致浮层下边被任务栏/屏幕下边遮挡。
        _wl, _wt, _wr, _wb = work_area(popup)
        try:
            _line_px = tkfont.Font(root=popup, font=name_font).metrics("linespace") or 22
        except Exception:
            _line_px = 22
        _fit_rows = max(6, (_wb - _wt - 80) // max(int(_line_px), 1))   # 80 ≈ 标题栏 + 上下边距
        rows = min(max(len(names), _ENTRY_OV_MIN_ROWS), _ENTRY_OV_MAX_ROWS, _fit_rows)
        lb = tk.Listbox(body, font=name_font, activestyle="none",
                        width=width, height=rows,
                        bg=_ov_bg, fg=_ov_fg,   # 2026-09-11 09:27（用户要求 3）：显式配色
                        yscrollcommand=sb.set, borderwidth=0, highlightthickness=0)
        for i, n in enumerate(names, 1):
            lb.insert("end", f"{i}. {n}")
        lb.pack(side="left", fill="both", expand=True)
        sb.configure(command=lb.yview)
        # 2026-09-10（用户要求 4-二）：浮层内各控件绑定"进入取消关闭 / 离开延时关闭"，
        # 使鼠标可移入浮层（含滚动条）滚动内容而不消失。
        for wdg in (popup, head, body, lb, sb):
            wdg.bind("<Enter>", lambda _e=None: self._cancel_entry_overview(), add="+")
            wdg.bind("<Leave>", self._entry_ov_leave, add="+")
        popup.update_idletasks()
        w, h = popup.winfo_reqwidth(), popup.winfo_reqheight()
        # 2026-09-11 08:52（用户要求 1）：在现有宽度基础上加宽约 150px（≈10-11 个汉字）。
        # 通过显式指定宽度实现：宽度增加后，下方 x 定位仍以"条目列左侧 - w - 4"计算，
        # 故浮层右边与条目列的相对位置不变，增加的部分全部向左扩展。
        w += _ENTRY_OV_EXTRA_W
        # 2026-09-16（批次 11-1，用户反馈 4）：夹紧改用"屏幕工作区"（已扣任务栏），
        #   原先用整屏高度 ⇒ 浮窗底边落进任务栏被遮挡。
        _wl, _wt, _wr, _wb = work_area(popup)
        # 定位：条目列【左侧】（右侧是详情区，浮层不应盖住它）；超出屏幕左侧则改放右侧
        x = self.entry_frame.winfo_rootx() - w - 4
        if x < _wl + 8:
            x = self.entry_frame.winfo_rootx() + self.entry_frame.winfo_width() + 4
        if x + w > _wr:
            x = max(_wr - w - 8, _wl + 8)
        y = self._entry_ov_y or self.entry_frame.winfo_rooty()
        if y + h > _wb - 8:
            y = max(_wb - h - 8, _wt + 8)
        if y < _wt + 8:
            y = _wt + 8
        popup.wm_geometry(f"{w}x{h}+{x}+{y}")  # 2026-09-11 08:52（用户要求 1）：显式宽度以落实加宽
        self._entry_ov_popup = popup
        self._entry_ov_listbox = lb   # 2026-09-10（用户要求 4-一）：滚动同步对象
        self._entry_ov_hl = None      # 2026-09-11 09:27（用户要求 3）：新浮层无旧高亮行
        self._highlight_entry_overview()   # 2026-09-11 09:27（用户要求 3）：立即高亮光标所在条目

    # ------------------------------------------------------------------ #
    # 详情区字段浮动提示（2026-09-12，用户要求 1~2）
    #   1) 位置固定：整体在详情区【左侧】、右边紧贴详情区左边界并向左展开；顶部与被悬停字段对齐，
    #      不再出现"左/上/下"三种位置；宽度取左侧可用空间，隐藏目录后自动压窄、绝不遮盖正文。
    #   2) 交互：延时弹出；光标移入浮窗不消失（只有离开文本区且未移向浮窗才关闭）；
    #      浮窗内可滚动（滚轮 / 滚动条拖动）；源文本框滚动时浮窗同步滚动；光标所在行在浮窗内加深。
    # ------------------------------------------------------------------ #
    def _attach_field_tip(self, widget, text: str, src_text, title: str) -> None:
        """为详情区字段文本框挂接浮动提示（2026-09-12，用户要求 2）。

        解决原 _FieldTooltip 的闪烁：原实现"悬停即建窗、离开即销毁"，提示窗紧贴控件边缘，
        光标一旦落到提示窗上就触发 Leave→销毁→再 Enter→重建的死循环。此处改为"延时弹出 +
        延时判断光标位置"，并额外绑定源文本框的 Motion 以支持同步滚动/逐行加深。

        2026-09-13（专项排查）：Tk 在小概率情况下会以**零参数**调用事件回调
        （tkinter 的 CallWrapper 在无事件替换参数时直接 `func()`），此时带必填 `_e` 的
        lambda 会抛 TypeError 并打印到 stderr。故此处所有回调参数均给默认值 `None`，
        使提示绑定在无事件场景下也安全（相关处理函数本身不依赖 event）。
        """
        widget.bind("<Enter>",
                    lambda _e=None, w=widget, t=text, s=src_text, ti=title:
                    self._field_tip_enter(w, t, s, ti), add="+")
        widget.bind("<Leave>", lambda _e=None, w=widget: self._field_tip_leave(w), add="+")
        # 光标在源文本框内移动 → 浮窗内同一行加深（同一源文本框只绑定一次 Motion）
        if src_text is not None and not getattr(src_text, "_ps_tip_motion_ok", False):
            try:
                src_text._ps_tip_motion_ok = True
                src_text.bind("<Motion>",
                              lambda e=None, s=src_text: self._field_tip_track(s, e), add="+")
            except Exception:
                pass

    def _cancel_field_tip(self) -> None:
        """取消挂起的"延时弹出/延时关闭"定时器（2026-09-12）。"""
        if self._tip_after is not None:
            try:
                self.after_cancel(self._tip_after)
            except Exception:
                pass
            self._tip_after = None

    def _field_tip_enter(self, widget, text: str, src_text, title: str) -> None:
        """悬停字段：延时弹出完整内容（2026-09-12，用户要求 2）。"""
        if not self._float_tips_on or self._tip_popup is not None:
            return
        self._cancel_field_tip()
        self._tip_after = self.after(
            _TIP_SHOW_MS, lambda: self._show_field_tip(widget, text, src_text, title))

    def _field_tip_leave(self, widget=None) -> None:
        """离开字段：延时判断光标是否移入浮窗（2026-09-12，用户要求 2-(3)）。

        widget 非空时先判断光标是否仍在触发控件矩形内——Tk 在"父/子控件"之间切换会补发
        成对 Enter/Leave，若不忽略会把刚排好的"延时弹出"误取消，导致浮窗不出现。
        """
        if widget is not None and self._pointer_in_widget(widget):
            return
        self._cancel_field_tip()
        self._tip_after = self.after(_TIP_HIDE_MS, self._field_tip_maybe_hide)

    def _field_tip_maybe_hide(self) -> None:
        """延时判断是否关闭浮窗；光标仍在浮窗/阅读区内则保持并轮询（用户要求 2-(3)）。"""
        self._cancel_field_tip()
        if self._tip_popup is None:
            return
        if self._pointer_in_tip_area():
            self._tip_after = self.after(_TIP_POLL_MS, self._field_tip_maybe_hide)
            return
        self._hide_field_tip()

    @staticmethod
    def _pointer_in_widget(widget) -> bool:
        """光标当前是否位于指定控件的屏幕矩形内（2026-09-12）。"""
        try:
            if widget is None or not widget.winfo_exists() or not widget.winfo_ismapped():
                return False
            px, py = widget.winfo_pointerxy()
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            return (x <= px <= x + widget.winfo_width()
                    and y <= py <= y + widget.winfo_height())
        except Exception:
            return False

    def _pointer_in_tip_area(self) -> bool:
        """光标是否仍在"浮窗 + 触发控件 + 源文本框"范围内（2026-09-12，用户要求 2-(3)）。"""
        for w in (self._tip_popup, self._tip_trigger, self._tip_src):
            if w is not None and self._pointer_in_widget(w):
                return True
        return False

    def _hide_field_tip(self) -> None:
        """立即关闭详情区字段浮动提示并清理状态（2026-09-12）。"""
        self._cancel_field_tip()
        if self._tip_popup is not None:
            try:
                self._tip_popup.destroy()
            except Exception:
                pass
        self._tip_popup = None
        self._tip_text = None
        self._tip_src = None
        self._tip_trigger = None
        self._tip_hl = None

    def _show_field_tip(self, widget, text: str, src_text, title: str) -> None:
        """构建并显示详情区字段浮动提示（2026-09-12，用户要求 1~2）。

        位置：右边贴详情区左边界、向左展开（顶部与被悬停字段顶部对齐）；宽度＝详情区左侧
        可用空间（上限 _TIP_MAX_W），故隐藏四级目录后自动压窄，始终不遮盖详情区正文；
        内容超出 _TIP_MAX_LINES 行时用右侧滚动条。
        """
        self._cancel_field_tip()
        if not self._float_tips_on or self._tip_popup is not None:
            return
        try:
            if not widget.winfo_exists():
                return
            popup = tk.Toplevel(widget)
        except Exception:
            return
        popup.wm_overrideredirect(True)
        # 2026-09-15（批次 4）：本浮窗的字体（组 field_tip）与文字色/背景色可由设置叠加——
        #   未设置时与原值**完全一致**（背景默认白、文字默认 #111111）；标题栏仍是主题蓝。
        _tip_bg = ui_appearance.color("field_tip", self._ui_appearance, "bg", "#ffffff")
        _tip_fg = ui_appearance.color("field_tip", self._ui_appearance, "fg", "#111111")
        popup.configure(bg=_tip_bg)
        head = tk.Label(popup, text=f"📄 {title} · 完整内容",
                        bg="#25639c", fg="white", padx=8, pady=4, anchor="w",
                        font=("Microsoft YaHei", 10, "bold"))
        head.pack(fill="x")
        bd = tk.Frame(popup, bg=_tip_bg)
        bd.pack(fill="both", expand=True)
        sb = tk.Scrollbar(bd)
        sb.pack(side="right", fill="y")
        # 2026-09-12（用户要求 1）：字号默认固定 14pt（可由设置叠加）；高度固定最小值 18 行，内容多时按行增加
        tip_font = ui_appearance.font("field_tip", _TIP_FONT, self._ui_appearance)
        try:
            import tkinter.font as tkfont
            line_px = tkfont.Font(root=popup, font=tip_font).metrics("linespace") or 20
        except Exception:
            line_px = 22
        # 2026-09-16（批次 11-1，用户反馈 4）：改用"屏幕工作区"（已扣任务栏）计算可容纳行数，
        #   原先按整屏算 ⇒ 浮窗过高时底边会落进任务栏被遮挡。
        _wl, _wt, _wr, _wb = work_area(popup)
        fit_lines = max(6, (_wb - _wt - 100) // max(line_px, 1))   # 小屏保护：不超过工作区可容纳行数
        lines = (text or "").count("\n") + 1
        show_lines = min(max(lines, _TIP_MIN_LINES), _TIP_MAX_LINES, fit_lines)
        tip = tk.Text(bd, font=tip_font, wrap="word", height=show_lines,
                      bg=_tip_bg, fg=_tip_fg, relief="flat", borderwidth=0,
                      highlightthickness=0, padx=8, pady=6,
                      yscrollcommand=sb.set, cursor="arrow")
        tip.insert("1.0", text or "")
        tip.tag_configure("curline", background=_TIP_HL_BG, foreground=_TIP_HL_FG)
        tip.configure(state="disabled")
        tip.pack(side="left", fill="both", expand=True)
        sb.configure(command=tip.yview)
        # 浮窗内：进入→取消关闭；离开→延时判断；滚轮→直接滚动浮窗内容（用户要求 2-(4)/(3)）
        for wdg in (popup, head, bd, tip, sb):
            wdg.bind("<Enter>", lambda _e=None: self._cancel_field_tip(), add="+")
            wdg.bind("<Leave>", lambda _e=None: self._field_tip_leave(None), add="+")
        popup.bind("<MouseWheel>", lambda e=None, t=tip: self._tip_wheel(e, t))
        tip.bind("<MouseWheel>", lambda e=None, t=tip: self._tip_wheel(e, t))
        # 位置：右边紧贴详情区左边界，向左展开；宽度＝左侧可用空间（隐藏目录后自动压窄）
        popup.update_idletasks()
        h = popup.winfo_reqheight()
        try:
            right = self.detail_root.winfo_rootx() - _TIP_GAP
        except Exception:
            right = widget.winfo_rootx() - _TIP_GAP
        # 2026-09-16（批次 11-1，用户反馈 4）：夹紧改用"屏幕工作区"（已扣任务栏）——
        #   原先用整屏高度，浮窗底边最多停到"整屏高 - 8"，正好落进任务栏被遮挡约 40px。
        _avail = max(right - _wl - _TIP_MARGIN, 120)
        w = max(min(_avail, _TIP_MAX_W), 120)
        x = max(right - w, _wl + _TIP_MARGIN)
        y = widget.winfo_rooty()          # 顶部与被悬停字段顶部对齐（便于逐行对照）
        if y + h > _wb - _TIP_MARGIN:
            y = max(_wb - h - _TIP_MARGIN, _wt + _TIP_MARGIN)
        if y < _wt + _TIP_MARGIN:
            y = _wt + _TIP_MARGIN
        popup.wm_geometry(f"{w}x{h}+{x}+{y}")
        self._tip_popup = popup
        self._tip_text = tip
        self._tip_src = src_text
        self._tip_trigger = widget
        self._tip_hl = None
        self._sync_field_tip(src_text)              # 初始位置与源文本框一致（用户要求 2-(5)）
        self._field_tip_initial_highlight(src_text)  # 初始加深光标所在行（用户要求 2-(5)）

    @staticmethod
    def _tip_wheel(event, tip) -> str:
        """浮窗内滚轮滚动浮窗内容；返回 "break" 阻止联动详情区滚动（用户要求 2-(4)）。"""
        try:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta and tip.winfo_exists():
                steps = -int(delta / 120)
                tip.yview_scroll(steps if steps else (-1 if delta > 0 else 1), "units")
        except Exception:
            pass
        return "break"

    @staticmethod
    def _count_display_lines(tip, index1, index2) -> int:
        """统计 tk.Text 两字符位置之间的"显示行数"（自动换行后的实际行数）（2026-09-12）。"""
        try:
            n = tip.count(index1, index2, "displaylines")
            if isinstance(n, (tuple, list)):
                n = n[0] if n else 0
            return int(n or 0)
        except Exception:
            return 0

    def _sync_field_tip(self, src=None) -> None:
        """源文本框滚动 → 浮窗按同一位置同步滚动（2026-09-12，用户要求 2-(5)）。

        浮窗内是与源文本框逐字符相同的文本，故取"源文本框可视区首字符"在浮窗内的显示行，
        换算为滚动比例后 moveto——与两窗宽度是否相同无关，隐藏目录变宽后依然准确。
        """
        src = src if src is not None else self._tip_src
        tip = self._tip_text
        if src is None or tip is None:
            return
        try:
            if not src.winfo_exists() or not tip.winfo_exists():
                return
            top = src.index("@0,0")          # 源文本框可视区左上角字符
            cur = self._count_display_lines(tip, "1.0", top)
            total = self._count_display_lines(tip, "1.0", "end")
            if total > 0:
                tip.yview_moveto(max(0.0, min(1.0, cur / total)))
        except Exception:
            pass

    def _field_tip_track(self, src_text, event) -> None:
        """光标在源文本框内移动 → 浮窗内同一行加深（2026-09-12，用户要求 2-(5)）。

        2026-09-13：`event` 可能为 None（Tk 零参数调用的边界情况）→ 直接忽略。
        """
        if event is None or self._tip_popup is None or src_text is not self._tip_src:
            return
        try:
            line = int(src_text.index(f"@{event.x},{event.y}").split(".")[0])
        except Exception:
            return
        self._highlight_field_tip_line(line)

    def _field_tip_initial_highlight(self, src_text) -> None:
        """浮窗弹出时先按当前光标位置加深一行（2026-09-12，用户要求 2-(5)）。"""
        if src_text is None or not self._pointer_in_widget(src_text):
            return
        try:
            px, py = src_text.winfo_pointerxy()
            line = int(src_text.index(
                f"@{px - src_text.winfo_rootx()},{py - src_text.winfo_rooty()}"
            ).split(".")[0])
            self._highlight_field_tip_line(line)
        except Exception:
            pass

    def _highlight_field_tip_line(self, line: int) -> None:
        """把浮窗内指定行加深（2026-09-12，用户要求 2-(5)）。"""
        tip = self._tip_text
        if tip is None:
            return
        try:
            if not tip.winfo_exists():
                return
            tip.configure(state="normal")   # 只读态下 tag 操作保险起见先临时放行
            if self._tip_hl is not None and self._tip_hl != line:
                tip.tag_remove("curline", "1.0", "end")
            tip.tag_add("curline", f"{line}.0", f"{line}.end")
            tip.configure(state="disabled")
            self._tip_hl = line
        except Exception:
            pass

    def _select_entry(self, entry_id: int) -> None:
        # 2026-09-22：去重——若已是当前展示条目且无未保存修改，则直接返回，
        #   避免"悬停延迟 + 点击"或重复事件导致详情区被反复重建（用户反馈的多次刷新问题）。
        #   注意：必须保留 `not self._detail_dirty` 前提——若存在未保存修改，
        #   仍需走原有 `_confirm_unsaved()` 询问流程，行为不变。
        if (entry_id == self._detail_entry_id
                and not self._detail_dirty
                and not self._adding_new):
            return
        if not self._confirm_unsaved():
            return
        e = self.db.get_entry(entry_id)
        if e:
            self._show_detail(e)

    # ------------------------------------------------------------------ #
    # 详情区（9 字段可编辑 + 图片预览 + 复制/收藏/删除）
    # ------------------------------------------------------------------ #
    def _field_labels_map(self) -> dict:
        """内置字段"显示名覆盖表"：{field_key: display_name}（来自 field_defs）。

        2026-09-13（1-A-3 元数据驱动·只读等价改造）：用于让"前 10 个区块可改名"生效——
        渲染时优先取此表的名字，取不到则回退 _FIELDS 的内置默认名。
        读库异常时返回空 dict，保证界面在任何情况下都能正常渲染（行为与改造前一致）。
        """
        try:
            return {d["field_key"]: d["display_name"] for d in self.db.list_field_defs()}
        except Exception:
            return {}

    # 2026-09-16（批次 13）：详情区区块按 sort_order 排序后的列表。
    #   返回 [(field_key, display_name, field_type), ...]，排除 ① name（固定在最前）。
    #   读库异常时返回内置默认顺序（_FIELDS + 虚拟区块），保证界面可渲染。
    def _ordered_detail_blocks(self) -> list:
        try:
            defs = self.db.list_field_defs()
        except Exception:
            defs = []
        if not defs:
            # 兜底：按内置默认顺序
            fallback = [("_location", "🧭 位置", "virtual"),
                        ("_time", "🕒 时间", "virtual"),
                        ("_tags", "🏷 标签", "virtual")]
            fallback += [(k, l, t) for l, k, t in
                         [("② 介绍", "intro", "textarea"), ("③ 溯源", "origin", "textarea"),
                          ("④ 核心特征", "features", "textarea"), ("⑤ 应用场景", "scenes", "textarea"),
                          ("⑥ 代表作", "works", "textarea"), ("⑦ 代表高清配图", "image_desc", "textarea"),
                          ("⑧ 中文版提示词", "prompt_cn", "textarea"),
                          ("⑨ 英文版提示词", "prompt_en", "textarea"),
                          ("⑩ 图像获取方案", "image_plan", "link")]]
            return fallback
        return [(d["field_key"], d["display_name"], d.get("field_type") or "text")
                for d in defs if d["field_key"] != "name"]

    # ---- 自定义字段渲染与保存（2026-09-13，1-A-5 第 2 步）-------------- #
    def _custom_field_defs(self) -> list:
        """自定义字段定义列表（is_builtin=0），按 field_defs 顺序；读库异常返回空列表。"""
        try:
            return [d for d in self.db.list_field_defs() if not d.get("is_builtin")]
        except Exception:
            return []

    def _add_custom_field_blocks(self, values: Optional[dict] = None) -> None:
        """在详情滚动区**末尾**渲染全部自定义字段块（置于 10 个内置区块之后）。

        2026-09-16（批次 13）：详情区已改为"按 sort_order 统一渲染"，本方法仅用于
        「新增条目」态（`_build_new_entry_editor`）等仍需批量渲染自定义字段的场景。
        详情态由 `_show_detail` 循环调用 `_render_custom_field` 逐个渲染。
        """
        values = values or {}
        for d in self._custom_field_defs():
            self._render_custom_field(d["field_key"], d["display_name"],
                                      d.get("field_type") or "text",
                                      values.get(d["field_key"], "") or "")

    # 2026-09-16（批次 13）：渲染**单个**自定义字段（从 _add_custom_field_blocks 抽取出）。
    #   渲染方式（2026-09-14 审核补充 P6：按 **字段类型** 分发）：
    #     - text/textarea/link：CTkTextbox；
    #     - list：专用选择器；
    #     - number/date/bool/tag/file/audio/image：专用控件。
    def _render_custom_field(self, key: str, label: str, ftype: str, val: str) -> None:
        block, label_c, box_bg, box_border = self._begin_field_block(key)
        ctk.CTkLabel(block, text=label, text_color=label_c,
                     font=("Microsoft YaHei", 12, "bold"), anchor="w"
                     ).pack(fill="x", padx=12, pady=(8, 2))
        if ftype == "list":
            # 2026-09-13（第 3 期 3-a-2）：列表框改用**专用控件**（候选值选择器）
            self._build_list_field_block(block, key, val)
            return
        if ftype in ("text", "textarea", "link"):
            rows = 1 if ftype == "text" else (6 if ftype == "textarea" else 3)
            if ftype == "link":
                row = ctk.CTkFrame(block, fg_color="transparent")
                row.pack(fill="x", padx=6, pady=(0, 6))
                box = ctk.CTkTextbox(row, height=_rows_to_px(rows), fg_color=box_bg,
                                     border_width=1, border_color=box_border, corner_radius=6)
                box.pack(side="left", fill="x", expand=True)
                if val.strip().startswith(("http://", "https://")):
                    ctk.CTkButton(row, text="打开", width=52, height=28,
                                  command=lambda b=box: self._open_image_plan(b)
                                  ).pack(side="right", padx=(6, 0))
            else:
                box = ctk.CTkTextbox(block, height=_rows_to_px(rows), fg_color=box_bg,
                                     border_width=1, border_color=box_border,
                                     corner_radius=6)
                box.pack(fill="x", padx=6, pady=(0, 6))
            box.insert("1.0", val)
            box.bind("<KeyRelease>", self._mark_dirty)
            self._detail_boxes[key] = box
            self._attach_field_tip(box, val or "（无内容）", _ui_common.textbox_internal(box), label)
            return
        # 其余类型：专用控件（P6）
        self._build_typed_field_block(block, key, ftype, val)

    # ---- 专用类型字段（P6：number/date/bool/tag/file/audio/image）-------- #
    _EXTRA_TYPE_HINTS = {
        "number": "仅数字（可含小数与负号）",
        "date": "格式 YYYY-MM-DD",
        "bool": "勾选＝是；不勾＝否",
        "tag": "多个标签用逗号分隔",
        "file": "任意文件路径（可『选择文件…』）",
        "audio": "音频文件路径（WAV 可直接试听）",
        "image": "图片文件路径（可『选择图片…』；下方内嵌缩略预览）",
    }

    def _build_typed_field_block(self, block, key: str, ftype: str, val: str) -> None:
        """渲染 P6 的专用类型控件，并注册 `_extra_field_getters[key]` 供保存时回读。

        - number / date：单行输入 + 灰字格式提示（保存时校验）；
        - bool：勾选框（存 "1" / "0"）；
        - tag：单行输入（逗号分隔，存文本，可被搜索/导出/同步自动覆盖）；
        - file / image / audio：单行输入 + 「选择…」/「打开」（audio 的 WAV 可试听）；
        - image 的缩略预览留待后续（当前仅路径 + 打开）。
        """
        hint = self._EXTRA_TYPE_HINTS.get(ftype, "")
        if ftype == "bool":
            var = ctk.StringVar(value="1" if str(val).strip() in ("1", "是", "true", "True")
                                else "0")
            chk = ctk.CTkCheckBox(block, text="是", variable=var, onvalue="1", offvalue="0",
                                  command=self._mark_dirty, font=("Microsoft YaHei", 11))
            chk.pack(anchor="w", padx=12, pady=(0, 8))
            self._extra_field_widgets.append(chk)   # 浏览态一并置灰
            self._extra_field_widgets_by_key[key] = chk
            self._extra_field_getters[key] = lambda v=var: (v.get(), "")
            if hint:
                ctk.CTkLabel(block, text=hint, text_color="gray",
                             font=("Microsoft YaHei", 10)).pack(anchor="w", padx=12,
                                                               pady=(0, 6))
            return

        row = ctk.CTkFrame(block, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(0, 6))
        ent = ctk.CTkEntry(row, placeholder_text=hint, font=("Microsoft YaHei", 11))
        ent.pack(side="left", fill="x", expand=True)
        ent.insert(0, val)
        ent.bind("<KeyRelease>", self._mark_dirty)
        self._extra_field_widgets.append(ent)       # 浏览态一并置灰（防误改）
        self._extra_field_widgets_by_key[key] = ent
        self._extra_field_getters[key] = lambda e=ent: (e.get(), "")

        if ftype in ("file", "image", "audio"):
            kind = {"file": "选择文件…", "image": "选择图片…", "audio": "选择音频…"}[ftype]
            b_pick = ctk.CTkButton(
                row, text=kind, width=92, height=28, fg_color="#2f6fb0",
                command=lambda e=ent, k=ftype, kk=key: self._pick_typed_file(e, k, kk))
            b_pick.pack(side="left", padx=(6, 0))
            b_open = ctk.CTkButton(row, text="打开", width=52, height=28,
                                   command=lambda e=ent: self._open_typed_path(e.get()))
            b_open.pack(side="left", padx=(4, 0))
            self._extra_field_widgets.extend([b_pick, b_open])
            if ftype == "audio":
                b_play = ctk.CTkButton(row, text="试听", width=52, height=28,
                                       fg_color="#8a94a6",
                                       command=lambda e=ent: self._play_audio(e.get()))
                b_play.pack(side="left", padx=(4, 0))
                self._extra_field_widgets.append(b_play)
        if hint:
            ctk.CTkLabel(row, text=hint, text_color="gray",
                         font=("Microsoft YaHei", 10)).pack(side="left", padx=(8, 0))
        # 2026-09-14（P6 补充）："图像"类型 → 输入行下方**内嵌缩略预览**
        if ftype == "image":
            holder = ctk.CTkFrame(block, fg_color="transparent")
            holder.pack(fill="x", padx=12, pady=(2, 8))
            self._typed_img_holders[key] = holder
            self._refresh_typed_image_preview(key)
            # 手输/粘贴路径后在"失焦"或"回车"时刷新预览（避免边打字边读盘）
            ent.bind("<FocusOut>",
                     lambda _e=None, k=key: self._refresh_typed_image_preview(k))
            ent.bind("<Return>",
                     lambda _e=None, k=key: self._refresh_typed_image_preview(k))

    def _refresh_typed_image_preview(self, key: str) -> None:
        """重绘"图像"类型自定义字段的内嵌预览（2026-09-14，P6 补充）。

        - 本地路径：读缩略图（最大 220×150，复用 `_load_thumb`）→ 缩略图 + 文件名；
        - 外链 http(s)：**不下载**（省流量、保持离线安全）→ 提示"点『打开』用浏览器查看"；
        - 空值 / 文件不存在 / 读图失败：给出灰字占位，不报错。
        """
        holder = self._typed_img_holders.get(key)
        ent = self._extra_field_widgets_by_key.get(key)
        if holder is None or ent is None:
            return
        try:
            if not holder.winfo_exists():
                return
        except Exception:
            return
        for w in holder.winfo_children():
            w.destroy()
        self._typed_img_refs.pop(key, None)
        try:
            path = str(ent.get() or "").strip().strip('"')
        except Exception:
            path = ""
        if not path:
            text, color, thumb, name = "（未设置图片）", "#9aa4b1", None, ""
        elif path.lower().startswith(("http://", "https://")):
            text, color, thumb, name = ("🔗 外链图片（为省流量不下载预览，点『打开』用浏览器查看）",
                                        "#2f6fb0", None, "")
        else:
            thumb = self._load_thumb(path, (220, 150))
            text = "" if thumb is not None else "（图片不存在或无法预览）"
            color, name = ("#5b6b7c", os.path.basename(path)[:60]) if thumb else ("#9aa4b1", "")
        if thumb is not None:
            self._typed_img_refs[key] = thumb      # 保持引用，避免被 Tk 回收后显示空白
            ctk.CTkLabel(holder, image=thumb, text="").pack(anchor="w")
            if name:
                ctk.CTkLabel(holder, text=name, text_color="#5b6b7c",
                             font=("Microsoft YaHei", 10)).pack(anchor="w", pady=(2, 0))
        else:
            ctk.CTkLabel(holder, text=text, text_color=color,
                         font=("Microsoft YaHei", 10)).pack(anchor="w")

    def _pick_typed_file(self, ent, ftype: str, key: str = "") -> None:
        """「选择文件 / 图片 / 音频…」→ 写入所选绝对路径（图像类型顺带刷新内嵌预览）"""
        kinds = {
            "file": [("全部文件", "*.*")],
            "image": [("图片", "*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg"),
                      ("全部文件", "*.*")],
            "audio": [("音频", "*.wav *.mp3 *.m4a *.ogg *.flac *.wma"),
                      ("全部文件", "*.*")],
        }
        path = filedialog.askopenfilename(title="选择文件", parent=self,
                                          filetypes=kinds.get(ftype, [("全部文件", "*.*")]))
        if not path:
            return
        ent.delete(0, "end")
        ent.insert(0, path)
        self._mark_dirty()
        if key:
            self._refresh_typed_image_preview(key)

    def _open_typed_path(self, path: str) -> None:
        """用系统默认程序打开字段里的文件路径（不存在则提示）"""
        p = (path or "").strip().strip('"')
        if not p:
            messagebox.showinfo("提示", "该字段当前为空。", parent=self)
            return
        if not os.path.isfile(p):
            messagebox.showwarning("文件不存在", f"找不到文件：\n{p}", parent=self)
            return
        try:
            os.startfile(p)          # noqa: S606 - Windows 专用，用户主动点击
        except Exception as exc:     # noqa: BLE001
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _play_audio(self, path: str) -> None:
        """音频试听：WAV 用标准库 `winsound` 异步播放；其它格式交系统默认程序"""
        p = (path or "").strip().strip('"')
        if not p or not os.path.isfile(p):
            messagebox.showwarning("无法试听", f"找不到音频文件：\n{p}", parent=self)
            return
        if p.lower().endswith(".wav"):
            try:
                import winsound
                winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC)
                self.toast("正在试听（WAV）…")
                return
            except Exception as exc:                 # noqa: BLE001
                messagebox.showwarning("试听失败", f"winsound 播放失败：{exc}\n将改用系统程序打开。",
                                       parent=self)
        self._open_typed_path(p)

    @staticmethod
    def _validate_typed_value(label: str, ftype: str, text: str) -> str:
        """类型格式校验（P6）：返回错误说明（'' 表示通过）"""
        t = (text or "").strip()
        if not t:
            return ""
        if ftype == "number":
            try:
                float(t)
            except ValueError:
                return f"『{label}』应为数字，当前为「{t}」"
        elif ftype == "date":
            try:
                datetime.strptime(t, "%Y-%m-%d")
            except ValueError:
                return f"『{label}』日期格式应为 YYYY-MM-DD，当前为「{t}」"
        return ""

    # ---- 列表框字段（list）——2026-09-13，第 3 期 3-a-2 ---------------- #
    def _build_list_field_block(self, block, key: str, val: str) -> None:
        """列表框字段的详情/新增表单块：已选值 + 「选择…」「清空」。

        取值由数据源配置决定候选（自定义序列 / 目录层级）；单选/多选、允许新建项均来自配置。
        """
        cfg = self.db.list_field_config(key)
        vals, snaps = self._split_list_value(val, cfg)
        self._list_field_values[key] = vals
        self._list_field_snaps[key] = snaps
        lbl = ctk.CTkLabel(block, text=self._list_display_text(key), anchor="w",
                           justify="left", wraplength=520, text_color="#2f6fb0",
                           font=("Microsoft YaHei", 11))
        lbl.pack(fill="x", padx=12, pady=(0, 2))
        self._list_field_labels[key] = lbl
        row = ctk.CTkFrame(block, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(0, 8))
        b_pick = ctk.CTkButton(row, text="选择…", width=76, height=28, fg_color="#2f6fb0",
                               command=lambda k=key: self._pick_list_values(k))
        b_pick.pack(side="left")
        b_clr = ctk.CTkButton(row, text="清空", width=60, height=28, fg_color="#8a94a6",
                              command=lambda k=key: self._clear_list_values(k))
        b_clr.pack(side="left", padx=(6, 0))
        src_hint = {"sequence": "", "tree_level": "·目录层级",
                    "entries": "·条目"}.get(cfg["source_type"], "·数据源待实现")
        ctk.CTkLabel(row, text=("多选" if cfg["multi"] else "单选") + src_hint,
                     text_color="gray", font=("Microsoft YaHei", 10)
                     ).pack(side="left", padx=(10, 0))
        self._list_field_btns.extend([b_pick, b_clr])
        self._attach_field_tip(lbl, self._list_display_text(key), None, key)

    def _split_list_value(self, val: str, cfg: dict) -> tuple:
        """已存值 → (已选值列表, {值: 快照名})。

        兼容三种形态：JSON 字符串数组（序列型）、JSON 对象数组（3-b 层级型，
        每项 {"value": 令牌, "label": 快照名}）、"、"分隔文本（旧数据）。
        """
        s = (val or "").strip()
        if not s:
            return [], {}
        vals, snaps = [], {}
        if s.startswith("[") and s.endswith("]"):
            try:
                arr = json.loads(s)
            except (TypeError, ValueError):
                arr = None
            if isinstance(arr, list):
                for x in arr:
                    if isinstance(x, dict):
                        v = str(x.get("value") or "").strip()
                        if v:
                            vals.append(v)
                            snaps[v] = str(x.get("label") or "").strip()
                    else:
                        v = str(x).strip()
                        if v:
                            vals.append(v)
        if not vals:
            vals = [x.strip() for x in s.replace("，", "、").replace(",", "、").split("、")
                    if x.strip()]
        uniq = []
        for v in vals:
            if v not in uniq:
                uniq.append(v)
        if not cfg["multi"]:
            uniq = uniq[:1]
        return uniq, snaps

    @staticmethod
    def _is_ref_source(cfg: dict) -> bool:
        """是否为"引用型"数据源（目录层级型 / 条目型）：值＝稳定令牌 + 快照名（3-b/3-c）"""
        return cfg.get("source_type") in ("tree_level", "entries")

    def _list_display_text(self, key: str) -> str:
        """列表框字段的显示文本。

        引用型（目录层级型 / 条目型）：值＝稳定令牌，显示时按当前目录/条目实时解析
        （**改名自动跟随**）；令牌已不存在（被删）→ 显示"该项已经被删除（原：<快照名>）"，
        不丢值、不报错。
        """
        vals = self._list_field_values.get(key) or []
        if not vals:
            return "（未选择）"
        cfg = self.db.list_field_config(key)
        if not self._is_ref_source(cfg):
            return "、".join(vals)
        live = {o["value"]: o["label"] for o in self.db.resolve_list_options(key)}
        snaps = self._list_field_snaps.get(key) or {}
        parts = []
        for v in vals:
            if v in live:
                parts.append(live[v])
            else:
                snap = snaps.get(v) or ""
                parts.append(f"该项已经被删除（原：{snap}）" if snap
                             else f"该项已经被删除（{v}）")
        return "、".join(parts)

    def _list_json_payload(self, key: str) -> str:
        """引用型字段的存库载荷：[{"value": 令牌, "label": 显示名(快照)}]。

        显示名优先取当前名称（改名后保存即刷新快照），取不到则沿用旧快照。
        """
        vals = self._list_field_values.get(key) or []
        if not vals:
            return ""
        cfg = self.db.list_field_config(key)
        if not self._is_ref_source(cfg):
            return json.dumps(vals, ensure_ascii=False)
        live = {o["value"]: o["label"] for o in self.db.resolve_list_options(key)}
        snaps = self._list_field_snaps.get(key) or {}
        return json.dumps([{"value": v, "label": live.get(v) or snaps.get(v) or ""}
                           for v in vals], ensure_ascii=False)

    def _refresh_list_field(self, key: str) -> None:
        lbl = self._list_field_labels.get(key)
        txt = self._list_display_text(key)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=txt)

    def _pick_list_values(self, key: str) -> None:
        """打开候选值选择对话框（单选/多选；序列型还可新建项）"""
        cfg = self.db.list_field_config(key)
        opts = self.db.resolve_list_options(key)
        cur = self._list_field_values.get(key) or []
        if self._is_ref_source(cfg):
            # 3-b/3-c：已选但对象已不存在的令牌 → 仍作为"失联项"列出（可取消勾选＝移除，或重新指定）
            have = {o["value"] for o in opts}
            snaps = self._list_field_snaps.get(key) or {}
            for v in cur:
                if v not in have:
                    snap = snaps.get(v) or ""
                    opts.append({"value": v,
                                 "label": f"该项已经被删除（原：{snap}）" if snap
                                          else f"该项已经被删除（{v}）"})
        if not opts:
            messagebox.showinfo(
                "提示",
                "该字段当前没有可选项。\n\n请到「🔧 字段管理 → 数据源…」为该列表框配置候选值"
                "（现支持「自定义序列」「目录层级」「条目」）。", parent=self)
            return
        label = next((d["display_name"] for d in self._custom_field_defs()
                      if d["field_key"] == key), key)
        dlg = _ListPickDialog(self, f"选择 · {label}", opts, cur,
                              multi=cfg["multi"], allow_new=cfg["allow_new"])
        self.wait_window(dlg)
        if not getattr(dlg, "confirmed", False):
            return
        vals = list(dlg.result)
        if not cfg["multi"] and len(vals) > 1:
            vals = vals[:1]
        if vals == cur:
            return
        self._list_field_values[key] = vals
        self._mark_dirty()
        self._refresh_list_field(key)

    def _clear_list_values(self, key: str) -> None:
        if not (self._list_field_values.get(key) or []):
            return
        self._list_field_values[key] = []
        self._mark_dirty()
        self._refresh_list_field(key)

    def _save_custom_field_values(self, entry_id: Optional[int]) -> None:
        """把"本次界面已渲染"的自定义字段值写入 entry_field_values。

        只写已渲染的字段（避免误清空未渲染/已归档字段的值）；
        内置 10 字段不在此列（仍由 entries 既有列承载）。
        """
        # 2026-09-15（批次 6-3b）：统一守卫（本方法由 _save_detail/_save_new_entry 内部调用，
        #   外层已拦；此处为"防御性冗余"，确保任何路径都不会在锁定态写字段值）
        if not self._assert_unlocked("保存自定义字段"):
            return
        if entry_id is None:
            return
        self._field_problem_list = []
        for d in self._custom_field_defs():
            key = d["field_key"]
            # 2026-09-14（审核补充 P6）：专用控件类型（number/date/bool/tag/file/audio/image）
            # 从 getter 回读，并按类型做格式校验（不通过则保留原值并记入提示）。
            if key in self._extra_field_getters:
                try:
                    text, payload = self._extra_field_getters[key]()
                except Exception:                    # noqa: BLE001
                    continue
                err = self._validate_typed_value(d["display_name"], d.get("field_type") or "",
                                                 text)
                if err:
                    self._field_problem_list.append(err)
                    continue
                self.db.set_entry_field_value(entry_id, key, text, payload or "")
            elif key in self._list_field_values:
                # 2026-09-13（3-a-2 / 3-b）：列表框字段——多选同时写入"、"文本（便于搜索/导出）
                # 与 value_json（精确还原）；单选只写文本。
                # 3-b 层级型：值＝稳定令牌，文本存"显示名"（可读可搜），value_json 存
                # [{"value": 令牌, "label": 快照名}]（目录改名不失效、目录删除可识别失联）。
                vals = self._list_field_values.get(key) or []
                cfg = self.db.list_field_config(key)
                text = self._list_display_text(key)
                text = "" if text == "（未选择）" else text
                if self._is_ref_source(cfg):
                    payload = self._list_json_payload(key)
                else:
                    payload = json.dumps(vals, ensure_ascii=False) if vals else ""
                if cfg["multi"]:
                    self.db.set_entry_field_value(entry_id, key, text, payload)
                else:
                    self.db.set_entry_field_value(entry_id, key, text,
                                                  payload if self._is_ref_source(cfg)
                                                  else "")
            elif key in self._detail_boxes:
                self.db.set_entry_field_value(entry_id, key, self._box_text(key))

    def _warn_field_problems(self) -> None:
        """自定义字段"类型格式校验未通过"的提示（P6）。

        其它字段已正常保存；问题字段**保留原值**，避免写入脏数据。
        """
        problems = list(getattr(self, "_field_problem_list", None) or [])
        self._field_problem_list = []
        if not problems:
            return
        messagebox.showwarning(
            "自定义字段格式提示",
            "以下字段的格式不符合其类型要求，本次未保存（保留原值）：\n· "
            + "\n· ".join(problems), parent=self)

    # ---- 🏷 标签区块（2026-09-13，1-C-2）-------------------------------- #
    def _build_tag_block(self, tags) -> None:
        """详情区/新增表单的"🏷 标签"区块（① 条目名称之后、②~⑦ 折叠组之前）。

        - 已选标签以彩色 chip 展示，点"×"移除；
        - 输入框回车或点「＋ 添加」加入；输入过程中在下方列出**匹配的现有标签**供点选（自动补全）；
        - 取值保存在 `self._tag_names`（有序、去重），由 `_save_detail`/`_save_new_entry` 写库。
        """
        names = []
        for t in (tags or []):
            n = (t.get("name") if isinstance(t, dict) else str(t)) or ""
            n = n.strip()
            if n and n not in names:
                names.append(n)
        self._tag_names = names
        self._tag_chip_btns = []
        # 2026-09-16（批次 14）：标记"本次详情/新增表单确实渲染过标签区块"，
        #   供 _save_entry_tags() 判断是否可以安全写库（被隐藏时不渲染 ⇒ 不写库）。
        self._tag_block_rendered = True

        block = ctk.CTkFrame(self.detail_scroll, fg_color="#f7f1fe",
                             corner_radius=10, border_width=1, border_color="#e0d0f2")
        block.pack(fill="x", padx=10, pady=(10, 2))
        ctk.CTkLabel(block, text="🏷 标签", text_color=_C_TAG,
                     font=("Microsoft YaHei", 12, "bold"), anchor="w"
                     ).pack(fill="x", padx=12, pady=(8, 2))
        # 2026-09-16（批次 16，用户要求 1）：按「新建对话框」的布局把 4 个控件合并到**同一行**——
        #   「✨ 推荐标签」+ 标签输入框 + 「＋ 添加」+ 「📋 选择…」（原来"推荐+说明"一行、
        #   "输入框+添加+选择"另起一行 ⇒ 现省去空出的那一行，本区块高度随之减少）；
        #   原"推荐标签"右侧的文字说明改为**按钮浮动提示**（悬停「✨ 推荐标签」即显示）。
        rec = ctk.CTkFrame(block, fg_color="transparent")
        rec.pack(fill="x", padx=10, pady=(0, 3))
        self._rec_btn = ctk.CTkButton(rec, text="✨ 推荐标签", width=104, height=24,
                                      fg_color=_C_TAG, font=("Microsoft YaHei", 11),
                                      command=self._suggest_tags)
        self._rec_btn.pack(side="left")
        self._attach_tag_hint_tip()   # 说明文字 → 浮动提示（原为按钮右侧的一行长文字）
        self._tag_entry = ctk.CTkEntry(rec, placeholder_text="输入标签，回车新建（自动补全）")
        self._tag_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._tag_entry.bind("<Return>", lambda _e=None: self._add_tag_from_entry())
        self._tag_entry.bind("<KeyRelease>", lambda _e=None: self._refresh_tag_suggest())
        self._tag_add_btn = ctk.CTkButton(rec, text="＋ 添加", width=76, height=24,
                                          command=self._add_tag_from_entry)
        self._tag_add_btn.pack(side="left", padx=(6, 0))
        # 2026-09-15 18:30（批次 9，用户确认 C18 路线①）：新增「📋 选择…」——从
        #   "库中已用标签 ∪ 词表 ∪ 热点词"候选池中**多选/新建**后并入已选（复用 _ListPickDialog）。
        self._tag_pick_btn = ctk.CTkButton(rec, text="📋 选择…", width=84, height=24,
                                           fg_color="#5B7CC7",
                                           command=self._pick_tags_dialog)
        self._tag_pick_btn.pack(side="left", padx=(6, 0))
        # 已选 chip 行（2026-09-16 批次 16：下边距 4 → 2，配合整块高度压缩）
        self._tag_chips_row = ctk.CTkFrame(block, fg_color="transparent")
        self._tag_chips_row.pack(fill="x", padx=10, pady=(0, 2))
        # 自动补全建议行（2026-09-13 用户要求：**按内容自动大小**——无匹配时整行不占位，
        # 避免输入框下方留出一大片无作用空白；有匹配时才 pack 出来）
        self._tag_sugg_row = ctk.CTkFrame(block, fg_color="transparent", height=1)

        self._refresh_tag_chips()

    def _attach_tag_hint_tip(self) -> None:
        """给「✨ 推荐标签」按钮挂浮动提示（2026-09-16 批次 16，用户要求 1）。

        原为按钮右侧一行长文字（"按名称/提示词自动推荐，结果直接加入下方标签（点 × 可去掉）"），
        为给"输入框 + 添加 + 选择"腾出同一行而改为悬停提示；**文案不变**，信息不丢失。
        与 🔧 字段管理按钮同一做法：用 `_attach_tooltip` 覆盖按钮全部可命中区域。
        """
        btn = getattr(self, "_rec_btn", None)
        if btn is None:
            return
        try:
            _attach_tooltip(btn, "按名称 / 提示词自动推荐标签；"
                                 "推荐结果直接加入下方标签（点 × 可去掉）")
        except Exception:                                    # noqa: BLE001
            pass       # 提示失败不影响主流程（推荐按钮本身照常可用）

    def _refresh_tag_chips(self) -> None:
        """重绘已选标签 chip 行（浏览态下"×"按钮置灰不可点）"""
        row = getattr(self, "_tag_chips_row", None)
        if row is None or not row.winfo_exists():
            return
        for w in row.winfo_children():
            w.destroy()
        self._tag_chip_btns = []
        if not self._tag_names:
            ctk.CTkLabel(row, text="（暂无标签）", text_color="#9aa4b1",
                         font=("Microsoft YaHei", 11)).pack(side="left")
            return
        browsing = bool(self._browse_mode and not self._adding_new
                        and self._detail_entry_id is not None)
        for n in self._tag_names:
            color = _tag_color(n)
            chip = ctk.CTkFrame(row, fg_color=color, corner_radius=11)
            chip.pack(side="left", padx=(0, 6), pady=2)
            ctk.CTkLabel(chip, text=n, text_color="#ffffff",
                         font=("Microsoft YaHei", 11)
                         ).pack(side="left", padx=(9, 2), pady=2)
            btn = ctk.CTkButton(chip, text="×", width=20, height=20, fg_color=color,
                                hover_color="#8a94a6", text_color="#ffffff",
                                font=("Microsoft YaHei", 11),
                                state=("disabled" if browsing else "normal"),
                                command=lambda s=n: self._remove_tag(s))
            btn.pack(side="left", padx=(0, 3), pady=2)
            self._tag_chip_btns.append(btn)

    def _refresh_tag_suggest(self) -> None:
        """输入框下方列出与当前输入匹配的现有标签（最多 8 个，点选即加入）

        2026-09-13（用户要求）：**按内容自动大小**——无匹配时整行隐藏（不占高度），
        有匹配时才显示，避免"标签"区块下方出现无作用空白。
        """
        row = getattr(self, "_tag_sugg_row", None)
        ent = getattr(self, "_tag_entry", None)
        if row is None or not row.winfo_exists() or ent is None:
            return
        for w in row.winfo_children():
            w.destroy()
        text = ent.get().strip()
        matches = []
        if text:
            try:
                # 2026-09-15 18:30（批次 9，用户确认 C19）：自动补全数据源由"仅 tags 表"
                #   扩展为"库中已用标签 ∪ 词表 ∪ 热点词"（与「📋 选择…」候选池同一来源）
                all_names = tagger.tag_name_pool(self.db)
            except Exception:
                all_names = []
            matches = [n for n in all_names if text in n and n not in self._tag_names][:8]
        if not matches:
            row.pack_forget()          # 无匹配 → 整行不占位（自动大小）
            return
        if not row.winfo_manager():
            row.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(row, text="匹配：", text_color="#9aa4b1",
                     font=("Microsoft YaHei", 10)).pack(side="left")
        for n in matches:
            ctk.CTkButton(row, text=n, width=max(56, 14 * len(n)), height=22,
                          fg_color=_tag_color(n), font=("Microsoft YaHei", 10),
                          command=lambda s=n: self._add_tag_name(s)
                          ).pack(side="left", padx=(0, 4))

    def _add_tag_name(self, name: str) -> None:
        """把标签名加入当前已选集合（去重），并标记为有未保存修改"""
        n = (name or "").strip()
        if not n:
            return
        if n not in self._tag_names:
            self._tag_names.append(n)
            self._mark_dirty()
        ent = getattr(self, "_tag_entry", None)
        if ent is not None and ent.winfo_exists():
            ent.delete(0, "end")
        self._refresh_tag_chips()
        self._refresh_tag_suggest()

    def _add_tag_from_entry(self) -> None:
        """输入框回车 / 点「＋ 添加」"""
        ent = getattr(self, "_tag_entry", None)
        if ent is None or not ent.winfo_exists():
            return
        self._add_tag_name(ent.get())

    def _pick_tags_dialog(self) -> None:
        """「📋 选择…」：从候选池（已用标签 ∪ 词表 ∪ 热点词）多选/新建标签并并入已选。

        2026-09-15 18:30（批次 9，用户确认 C18 路线①）：复用全项目唯一的
        "可搜索 + 多选 + 预选 + 新建" 对话框 `_ListPickDialog`；
        结果逐个交给既有 `_add_tag_name()`（自带去重 + 置脏 + 重绘），**不改保存流程**。
        """
        if self._browse_mode and not self._adding_new:
            self.toast("ℹ 浏览态为只读，请点「✎ 编辑」后再选择标签")
            return
        if not self._assert_unlocked("选择标签"):
            return
        try:
            pool = tagger.tag_name_pool(self.db)
        except Exception:
            pool = []
        opts = [{"value": n} for n in pool if n not in (self._tag_names or [])]
        if not opts:
            self.toast("⚠ 没有可选标签（可先点「✨ 推荐标签」或直接在输入框新建）")
            return
        dlg = _ListPickDialog(self, "选择 · 标签", opts, [], multi=True, allow_new=True)
        self.wait_window(dlg)
        if not getattr(dlg, "confirmed", False):
            return
        for n in list(dlg.result):
            self._add_tag_name(n)

    def _remove_tag(self, name: str) -> None:
        """移除已选标签"""
        if name in self._tag_names:
            self._tag_names.remove(name)
            self._mark_dirty()
            self._refresh_tag_chips()
            self._refresh_tag_suggest()

    def _save_entry_tags(self, entry_id: Optional[int]) -> None:
        """把当前已选标签写入 entry_tags（整体替换；虚拟新增态 entry_id=None 时跳过）"""
        # 2026-09-15（批次 6-3b）：统一守卫（本方法由 _save_detail/_save_new_entry 内部调用，
        #   外层已拦；此处为"防御性冗余"）
        if not self._assert_unlocked("保存标签"):
            return
        if entry_id is None:
            return
        # 2026-09-16（批次 14，安全修复）：🏷 标签区块本次未渲染（被手动隐藏）时**不写库**，
        #   保留条目的原有标签；否则会用上一次渲染残留的 _tag_names 覆盖（清空或串写）。
        if not getattr(self, "_tag_block_rendered", False):
            return
        self.db.set_entry_tags(entry_id, self._tag_names)

    # ------------------------------------------------------------------ #
    # 录入时自动推荐标签（2026-09-14，阶段 3）
    #   行为与用户要求一致：**推荐结果直接并入已选标签（即"默认全选"）**，用户点 chip 上的
    #   "×"去掉不要的即可；已存在的标签不会重复添加（只追加、不覆盖）。
    #   触发方式：①（默认启用）「✨ 推荐标签」按钮 + ①名称框回车/失焦；
    #            ②（设置开关，默认关）⑧中文/⑨英文输入停止 800ms 后防抖重算。
    # ------------------------------------------------------------------ #
    def _suggest_ctx_names(self) -> list:
        """推荐用的分类上下文名（新建态取目标分类；浏览/编辑态取该条目的分类链）。

        2026-09-15（批次 8-A，用户确认）：新增态 `_add_target` 为空时**不再直接返回 []**，
        改为回退"当前视图分类 → 最近一次有分类的视图"⇒ 解决"在未分类/搜索等视图下新增时
        推荐必空"的问题（实测无上下文时 300 条里仅 4 条能出标签）。编辑/浏览态逻辑不变。
        """
        try:
            index = tagger.build_context_index(self.db)
            cid = self._add_target if self._adding_new else None
            # 2026-09-15（批次 8-A）：新增态上下文兜底
            if self._adding_new and cid is None:
                cid = self._cur_cat_id or self._last_cat_id
            if cid is None and self._detail_entry_id is not None:
                e = self.db.get_entry(self._detail_entry_id)
                cid = (e or {}).get("category_id")
            return tagger.entry_context_names(index, cid) if cid else []
        except Exception:
            return []

    def _suggest_tags(self, from_auto: bool = False) -> None:
        """✨ 自动推荐标签：按当前表单内容推荐 1~3 个标签并**并入**已选标签。

        from_auto（2026-09-16 批次 12-3）：是否来自 T2 防抖自动推荐——用于「智能自动取词词库」
        的采集范围判断（默认只采集"用户主动点按钮"路径，设置里可放开 T2）。
        """
        if (self._browse_mode and not self._adding_new
                and self._detail_entry_id is not None):
            self.toast("ℹ 浏览态为只读，请点「✎ 编辑」后再推荐标签")
            return
        # 2026-09-16（批次 14）：🏷 标签区块被隐藏时不推荐——结果无处显示、也不会被保存，
        #   给出明确提示，避免"提示已推荐但看不到任何变化"的困惑。
        if not getattr(self, "_tag_block_rendered", False):
            self.toast("ℹ 🏷 标签区块已隐藏；请在「字段管理」中改回「显示」后再推荐标签")
            return
        # 2026-09-22（用户决策 4：先保存后推荐 + 统一取值来源）：
        #   原实现从 UI 控件 `_box_text()` 取这 6 个字段；而"字段管理"里被设为「隐藏」
        #   的字段（如隐藏的提示词）不渲染 ⇒ 无控件 ⇒ 取到空串 ⇒ 推荐只能从标题提取。
        #   现改为：已存条目**先确保保存，再统一从数据库读取**这 6 个字段，
        #   与"字段是否隐藏"彻底解耦（隐藏字段同样参与推荐，行为确定可预期）。
        #   新增态没有数据库行，仍从 UI 取（隐藏字段本就无从填写，无内容可读）。
        _suggest_keys = ("name", "intro", "features", "image_desc", "prompt_cn", "prompt_en")
        if not self._adding_new and self._detail_entry_id is not None:
            if self._detail_dirty and not self._lock_on:
                if not messagebox.askyesno(
                        "先保存，再推荐标签",
                        "推荐标签将基于**已保存**的内容（含被隐藏的字段，如提示词）。\n\n"
                        "当前条目有未保存的修改，是否先保存再推荐？",
                        parent=self):
                    self.toast("ℹ 已取消推荐：请先保存条目修改后再试")
                    return
                self._save_detail()
                if self._detail_dirty:
                    return   # 保存未生效（如重复内容提示中选了"否"）
            _cur = self.db.get_entry(self._detail_entry_id) or {}
            texts = {k: (_cur.get(k) or "") for k in _suggest_keys}
        else:
            texts = {k: self._box_text(k) for k in _suggest_keys}
        try:
            nm = self._name_entry.get().strip()
            if nm:
                texts["name"] = nm
        except Exception:
            pass
        if not any((texts.get(k) or "").strip() for k in texts):
            self.toast("⚠ 请先填写名称或提示词，再点「✨ 推荐标签」")
            return
        # 2026-09-17（审核 R-2）：把"读词表 → 调引擎 → 取词采集 → 来源标注"统一交给
        #   `tagger.run_ui_suggest`（与「快速新建」窗口**共用同一实现**，改一次两处生效）。
        #   此处只保留本窗口特有的东西：守卫、toast 提示、chip 刷新与"置脏"。
        #   口径不变：UI 单条推荐仍启用"全词典兜底 + 字段取词"，并读取用户配置的
        #   推荐策略顺序与热点词清单（详见 tagger.run_ui_suggest 的说明）。
        try:
            _r = tagger.run_ui_suggest(self.db, texts, self._suggest_ctx_names(),
                                       from_auto=from_auto,
                                       collect=self._collect_auto_words)
        except Exception as exc:
            self.toast(f"⚠ 推荐失败：{exc}")
            return
        names = _r["names"]
        _auto_note, _note = _r["auto_note"], _r["source_note"]
        if not names:
            self.toast("⚠ 未推荐出标签（可补充名称/提示词后再试）%s" % _auto_note)
            return
        added = []
        for n in names:
            if n not in self._tag_names:
                self._tag_names.append(n)
                added.append(n)
        if added:
            self._mark_dirty()
        self._refresh_tag_chips()
        self.toast("✨ 已推荐 %d 个标签%s%s%s" % (
            len(names), _note,
            ("（新增 %d 个）" % len(added)) if added else "（均已存在）", _auto_note))

    def _collect_auto_words(self, texts, dict_data, from_auto: bool = False) -> str:
        """把"未命中词表/热点词"的取词候选记入「智能自动取词词库」（2026-09-16 批次 12-3）。

        返回**追加到提示语末尾**的一句话（无采集/无变化时返回空串），便于与推荐结果一起提示。
        采集范围：设置里 `enabled` 为真；`from_auto=True`（T2 自动推荐）时还需 `include_t2` 为真。
        设计：**任何异常都不得影响推荐主流程** ⇒ 整体 try/except 兜底。
        """
        try:
            from .. import auto_words
            cfg = auto_words.load_cfg(self.db)
            if not cfg.get("enabled") or (from_auto and not cfg.get("include_t2")):
                return ""
            _hot = self.db.list_hotwords()
            _cands = tagger_engine.field_candidates(
                texts, ("name", "prompt_cn", "prompt_en"), 24,
                tagger_engine.build_vocab(dict_data), _hot)
            if not _cands:
                return ""
            _res = auto_words.record(
                self.db, [(c["word"], c["field"]) for c in _cands],
                entry_id=self._detail_entry_id)
            if _res.get("hot"):
                return "；🧠 自动取词：%d 个词已达阈值，已加入热点词表（%s）" % (
                    len(_res["hot"]), "、".join(_res["hot"][:3]))
            if _res.get("ready_tag"):
                return "；🧠 自动取词：%d 个词已达词表阈值，待你在设置里审核" % len(_res["ready_tag"])
            return ""
        except Exception:
            return ""

    def _on_name_committed(self, _event=None) -> None:
        """①名称框回车/失焦 → 自动推荐一次（T1；同名不重复推荐，避免反复打扰）。"""
        try:
            name = self._name_entry.get().strip()
        except Exception:
            return
        if not name or name == getattr(self, "_rec_last_name", ""):
            return
        self._rec_last_name = name
        self._suggest_tags()

    def _on_prompt_typed(self, _event=None) -> None:
        """⑧⑨ 输入后防抖自动推荐（T2，设置开关默认关）。"""
        if not getattr(self, "_auto_tag_suggest", False):
            return
        if self._browse_mode and not self._adding_new:
            return                        # 浏览态只读，不推荐
        try:
            if self._suggest_timer is not None:
                self.after_cancel(self._suggest_timer)
        except Exception:
            pass
        self._suggest_timer = self.after(800, self._suggest_tags_debounced)

    def _suggest_tags_debounced(self) -> None:
        self._suggest_timer = None
        # 2026-09-16（批次 12-3）：标记为"来自 T2 自动推荐"，供自动取词词库的采集范围判断
        self._suggest_tags(from_auto=True)

    def _bind_auto_suggest_triggers(self) -> None:
        """把 T2 防抖触发挂到 ⑧中文 / ⑨英文 文本框（详情区每次重建后调用一次）。"""
        for key in ("prompt_cn", "prompt_en"):
            box = (getattr(self, "_detail_boxes", None) or {}).get(key)
            if box is None:
                continue
            try:
                box.bind("<KeyRelease>", self._on_prompt_typed, add="+")
            except Exception:
                pass

    # ---- 详情显示模式（2026-09-13，1-B 修复：此前设置值从未参与渲染）---- #
    def _entry_domain_names(self, e) -> list:
        """条目所属一级分类关联的"根目录名"列表（未分类/异常返回空列表）

        注意：`db.category_root()` 返回的是**顶级（一级）分类的 id（int）**，不是 dict。
        """
        try:
            cid = (e or {}).get("category_id")
            if not cid:
                return []
            root_id = self.db.category_root(cid)
            if not root_id:
                return []
            return [d["name"] for d in self.db.linked_domains(root_id)]
        except Exception:
            return []

    def _detail_mode_full(self, e) -> bool:
        """按"设置里的详情字段显示策略"判断当前条目是否应"全部显示"。

        - full    ：始终全部显示（②~⑦ 全含）；
        - compact ：始终精简（只留 ② 介绍，隐藏 ③~⑦）；
        - auto    ：条目所属根目录命中 config.DETAIL_FULL_FIELDS_DOMAINS（视频/图像）则全部显示，
                    否则精简。未分类条目视为精简。
        """
        mode = self._detail_mode
        if mode == config.DETAIL_MODE_FULL:
            return True
        if mode == config.DETAIL_MODE_COMPACT:
            return False
        return any(n in config.DETAIL_FULL_FIELDS_DOMAINS
                   for n in self._entry_domain_names(e))

    def _detail_hidden_keys(self, e) -> set:
        """当前应隐藏的字段键集合（**会话覆盖优先于显示模式**）。

        说明：这正是 `self._detail_hidden` 的取值来源——修复前它从未被赋值，
        导致"自动/全部显示/精简"三种设置对界面没有任何效果。
        """
        if self._detail_show_all or self._detail_mode_full(e):
            return set()
        return set(config.DETAIL_HIDDEN_KEYS)

    def _manual_hidden_keys(self) -> set:
        """详情区"手动隐藏"的字段键集合（字段管理里的「隐藏」开关，存 meta）。

        2026-09-16（批次 14）：**硬隐藏**——不随"详情字段显示策略"或会话覆盖而解除；
        读库异常时返回空集合（宁可不隐藏，也不误隐藏）。① 名称不会出现在此集合中
        （数据库读取时已强制剔除）。
        """
        try:
            return set(self.db.get_hidden_field_keys())
        except Exception:                                   # noqa: BLE001
            return set()

    def _show_detail(self, e: Optional[dict]) -> None:
        """展示/刷新详情：固定头部更新 + 滚动内容区重建

        2026-08-18：按当前查看的根目录决定字段显示模式——根目录为
        config.DETAIL_FULL_FIELDS_DOMAINS（视觉风格分类/视频/图像）时 9 字段全部显示；
        其余根目录隐藏 ③-⑦（config.DETAIL_HIDDEN_KEYS），突出提示词内容。
        """
        # 2026-09-06：展示已存条目/清空详情时一律退出"新增条目"态
        self._adding_new = False
        self._add_target = None
        self._add_group_open = False
        self._add_group_pairs = []
        self._hide_field_tip()   # 2026-09-12（用户要求 2）：详情重建前先收起字段浮动提示（避免引用已销毁控件）
        self._clear_frame(self.detail_scroll)
        self._detail_boxes = {}
        self._gallery_btns = []   # 2026-09-13（2-b）：重建前清空图集按钮引用（避免残留旧控件）
        # 2026-09-13（3-a-2）：列表框字段状态同样清空后重建
        self._list_field_values = {}
        self._list_field_snaps = {}
        self._list_field_labels = {}
        self._list_field_btns = []
        self._extra_field_getters = {}   # P6：专用类型字段的取值回调
        self._extra_field_widgets = []   # P6：专用类型字段的控件（浏览态置灰用）
        self._extra_field_widgets_by_key = {}
        self._typed_img_holders = {}     # 图像字段预览容器（重建时清空）
        self._typed_img_refs = {}
        self._detail_dirty = False
        # 2026-09-16（批次 14，安全修复）：🏷 标签区块可能因"手动隐藏"而**不被渲染**。
        #   此处先清空已选标签并标记"未渲染"，由 _build_tag_block() 渲染时置 True；
        #   _save_entry_tags() 仅在"本次确实渲染过"时才写库——否则隐藏标签后
        #   会读到上一次渲染残留的 _tag_names，造成**标签串写或清空**。
        self._tag_names = []
        self._tag_block_rendered = False
        if e is None:
            self._detail_entry_id = None
            self._detail_hidden = set()  # 2026-08-18：无条目时无隐藏字段
            self.detail_scroll.configure(label_text="")  # 2026-09-09：固定头部状态行取代内置标题
            self.fav_btn.configure(command=lambda: None)
            self.del_btn.configure(command=lambda: None)
            self.link_btn.configure(state="disabled", command=lambda: None)
            self.copyto_btn.configure(state="disabled", command=lambda: None)
            self.move_btn.configure(state="disabled", command=lambda: None)
            self.copy_all_btn.configure(command=lambda: None)
            self.copy_cn_btn.configure(command=lambda: None)
            self.copy_en_btn.configure(command=lambda: None)
            self.save_btn.configure(command=lambda: None)
            self.reset_btn.configure(command=lambda: None)
            # 2026-09-09：无条目时编辑/浏览切换无意义 → 禁用并回到编辑态
            # 2026-09-14：改普通按钮后，`set()` → `configure(text=...)`
            try:
                self.edit_mode_toggle.configure(text="✏️ 编辑")
                self.edit_mode_toggle.configure(state="disabled")
            except Exception:
                pass
            self._apply_lock_state()
            self._refresh_detail_header()
            ctk.CTkLabel(self.detail_scroll, text="请选择条目查看详情",
                         text_color="gray").pack(pady=40)
            return
        self._detail_entry_id = e["id"]
        # 2026-09-09：固定头部状态行恒常显示"显示全部字段/精简显示"开关（任何根目录/层级），
        # 滚动区内不再按根目录条件显示精简顶栏，也不显示孤立"详情"标题
        self.detail_scroll.configure(label_text="")

        # 命令按钮：收藏 / 删除 / 关联到 / 复制到 / 复制提示词 / 保存 / 重置
        star = "★ 已收藏" if e["is_favorite"] else "☆ 收藏"
        self.fav_btn.configure(text=star,
                               command=lambda: self._toggle_favorite(e["id"]))
        self.del_btn.configure(command=lambda: self._delete_entry(e["id"]))
        # 2026-09-07（阶段2）：详情区"关联到/复制到"作用于当前展示的条目
        self.link_btn.configure(state="normal",
                                command=lambda eid=e["id"]: self._link_entry(eid))
        self.copyto_btn.configure(state="normal",
                                  command=lambda eid=e["id"]: self._copy_entry_to_targets(eid))
        self.move_btn.configure(state="normal",
                                command=lambda eid=e["id"]: self._move_entry(eid))
        self.copy_all_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_ALL))
        self.copy_cn_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_CN))
        self.copy_en_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_EN))
        self.save_btn.configure(command=self._save_detail)
        self.reset_btn.configure(command=self._reset_detail)
        # 2026-09-07：① 条目名称（风格名称）作为详情区第一个字段（醒目大输入框）
        self._build_name_field(initial=e["name"])
        # 2026-09-13（1-C-2）：🏷 标签区块所需数据（在遍历前准备，避免循环中重复查库）
        _entry_tags = self.db.list_entry_tags(e["id"])
        # 2026-09-13（1-B）：切到别的条目时复位"会话覆盖"
        if self._detail_last_entry_id != e["id"]:
            self._detail_show_all = False
            self._detail_last_entry_id = e["id"]
        # 真正驱动 _detail_hidden（修复前该变量从未被赋值 → 设置形同无效）
        # 2026-09-16（批次 14）：并入手动隐藏（字段管理里的「隐藏」开关）——硬隐藏：
        #   无论"详情字段显示策略"是精简/自动/全部，被隐藏项都不渲染；
        #   不渲染 ⇒ 该项不占高度、下方内容自然整体上移（无需额外布局处理）。
        self._detail_hidden = self._detail_hidden_keys(e) | self._manual_hidden_keys()

        # 2026-09-16（批次 13）：详情区区块按 field_defs.sort_order 统一排序渲染。
        #   ① 名称固定在最前；其余（🏷标签 / 🧭位置 / 🕒时间 / ②~⑩ / 自定义字段）
        #   全部按 sort_order 顺序渲染，用户可在「字段管理」中自由调整顺序（含跨组）。
        blocks = self._ordered_detail_blocks()
        # 自定义字段取值（列表型取 value_json，其余取 value_text）
        try:
            _list_keys = {d["field_key"] for d in self._custom_field_defs()
                          if (d.get("field_type") or "") == "list"}
            _custom_vals = {}
            for r in self.db.list_entry_field_values(e["id"]):
                k = r["field_key"]
                _custom_vals[k] = ((r.get("value_json") or "") if k in _list_keys
                                   else (r.get("value_text") or ""))
        except Exception:
            _custom_vals = {}

        for key, label, ftype in blocks:
            if key in self._detail_hidden:
                continue   # 显示模式隐藏的字段跳过
            if key == "_tags":
                self._build_tag_block(_entry_tags)
            elif key == "_location":
                self._build_detail_location_hint(e["id"])
            elif key == "_time":
                self._build_detail_time_line(e)
            elif key in _INFO_GROUP_KEYS:   # ②~⑦：独立可折叠，默认折叠
                self._build_collapsible_field(e, label, key, default_collapsed=True)
            elif key in _COLLAPSIBLE_KEYS:  # ⑧⑨：可折叠，默认显示 6 行
                self._build_collapsible_field(e, label, key, default_collapsed=False)
            elif key == "image_plan":        # ⑩：链接型，默认显示 + 打开按钮
                self._build_collapsible_field(e, label, key, default_collapsed=False)
            else:                            # 自定义字段
                self._render_custom_field(key, label, ftype, _custom_vals.get(key, ""))

        self._apply_lock_state()   # 2026-09-07：锁定态统一禁用编辑控件（含位置解除按钮等）

        # 2026-09-10（用户要求）：详情区文本框内的滚轮统一转给详情区滚动
        self._install_detail_wheel(self.detail_scroll)

        # 2026-09-09：为详情区全部文本框启用撤销/重做，并按当前模式设置只读
        _enable_text_undo(self.detail_scroll)
        self._apply_browse()
        self._refresh_tag_chips()   # 1-C-2：按最新浏览/编辑态刷新 chip 上"×"的可用性
        try:
            self.edit_mode_toggle.configure(state="normal")  # 非新增态恢复切换可用
        except Exception:
            pass
        self._sync_edit_mode_btn()   # 2026-09-14：普通按钮文字同步为当前状态
        self._refresh_detail_header()
        # 2026-09-14（阶段 3）：挂接"自动推荐标签"的输入触发点（①名称框已在建框时绑定）
        self._bind_auto_suggest_triggers()
        self._scroll_top(self.detail_scroll)  # 2026-09-07（第4条改进）：打开新条目详情回到顶部

    # ------------------------------------------------------------------ #
    # 详情区文本框滚轮（2026-09-10，用户要求）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _walk_widgets(root_widget):
        """深度遍历控件树（不含自身，含全部子级）"""
        for child in root_widget.winfo_children():
            yield child
            for grand in MainWindow._walk_widgets(child):
                yield grand

    def _install_detail_wheel(self, root_widget) -> None:
        """把详情区文本框上的滚轮按"是否已点击激活"分流（2026-09-10 引入，2026-09-11 用户要求 4 修订）。

        背景根因：tkinter 的 Text 有**类级** `<MouseWheel>` 绑定，会把滚轮"吞"去滚动
        文本框自身；于是鼠标停在文本框上时详情区（页面）几乎不滚动，用户反馈
        "指针在文本框上滚动失效、只有移到文本框以外才有效"。
        2026-09-11（用户要求 4）修订：改为"先点击进入的文本框才滚动自身"——未点击激活
        （无键盘焦点）时仍整体转给详情区；已点击激活（有键盘焦点）时优先滚动文本框自身，
        到上/下端或无溢出时继续转给详情区。判定与分流见 _detail_wheel_route。
        需要阅读超长提示词时：点 ⑧/⑨ 的"展开"放大文本框，或用键盘（PageUp/PageDown、
        方向键、Ctrl+Home/End）浏览。
        """
        canvas = getattr(self.detail_scroll, "_parent_canvas", None)
        if canvas is None:
            return
        for w in self._walk_widgets(root_widget):
            # 2026-09-17（R-6）：`_textbox` 私有属性统一经 ui_common 访问（仅多行文本框）
            inner = _ui_common.textbox_internal(w)
            if inner is None or getattr(inner, "_ps_wheel_ok", False):
                continue
            inner._ps_wheel_ok = True
            inner.bind("<MouseWheel>",
                       lambda e=None, t=inner, c=canvas: (self._detail_wheel_route(e, t, c)
                                                          if e is not None else None),
                       add="+")

    def _detail_wheel_route(self, event, text_widget, canvas):
        """详情区文本框滚轮分流（2026-09-11，用户要求 4；2026-09-12 增加浮窗同步）。

        激活判定：该文本框是否拥有键盘焦点——点击文本区即会获得焦点，仅悬停/滑过不会，
        因此"必须先在区域内点击，滚轮才作用于该文本框"。
        - 未激活：与既有行为一致，滚轮整体转给详情区滚动。
        - 已激活：先滚动文本框自身（约 20px/格，与详情区步进一致）；若已到上/下端
          （或内容无溢出、无处可滚），再把该次滚轮转给详情区。
        - 2026-09-12（用户要求 2-(5)）：详情区字段浮窗打开且滚轮落在其源文本框上时，
          始终滚动该文本框并同步浮窗（不再转给详情区页面，避免源文本框被滚出视野）。
        始终返回 "break"：阻止 tk.Text 类级绑定与 CTkScrollableFrame 的 bind_all
        处理，避免"文本框与详情区同时滚动"。
        """
        delta = int(getattr(event, "delta", 0) or 0)
        if delta and self._tip_popup is not None and text_widget is self._tip_src:
            try:
                text_widget.yview_scroll(-int(delta / 6), "pixels")
            except Exception:
                pass
            self._sync_field_tip(text_widget)   # 浮窗内容随源文本框同步滚动
            return "break"
        try:
            if text_widget.focus_get() is text_widget:
                if delta:
                    before = text_widget.yview()
                    text_widget.yview_scroll(-int(delta / 6), "pixels")
                    if text_widget.yview() != before:
                        return "break"
        except Exception:
            pass
        return self._detail_wheel_to_page(event, canvas)

    @staticmethod
    def _detail_wheel_to_page(event, canvas):
        """把滚轮事件转给详情区画布滚动；返回 "break" 以阻止文本框自身滚动"""
        try:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta and canvas.winfo_exists():
                canvas.yview("scroll", -int(delta / 6), "units")
        except Exception:
            pass
        return "break"

    # 2026-09-16（批次 13）：原「②~⑦ 折叠组」方法已删除——
    #   ②~⑦ 现拆为独立可折叠块，由 _build_collapsible_field(default_collapsed=True) 渲染。
    def _build_collapsible_field(self, e, label: str, key: str,
                                 default_collapsed: bool = False) -> None:
        """可折叠字段：标签行 + 展开/收起按钮 + 文本框（height 单位为像素）。

        2026-08-18：有内容默认 _COLLAPSED_H=120px（6 行文字完整可见）；点击"展开"时
        高度自适应为内容实际显示行数（_content_fit_height，有多少行显示多少行）；
        无内容时只显示 _EMPTY_H=24px（1 行空行），且不显示"展开"按钮。

        2026-09-16（批次 13）：
          - 新增 `default_collapsed` 参数：True 时**默认折叠**（只显示标题行，内容区不 pack），
            用于 ②~⑦ 短文本字段——取代原"②~⑦ 整组折叠"，每个字段独立可折叠；
          - ⑩ image_plan 为链接型：文本框右侧加「打开」按钮；
          - ⑦ image_desc：文本框下方追加图片预览区（封面 + 图集），随字段一同显隐；
          - 文本框 + 图片区统一放在 content_frame 内，折叠时整区 pack_forget。
        """
        has_content = bool((e[key] or "").strip())
        # 2026-09-16：全部展开模式下，②~⑦ 也默认展开（显示 6 行）
        _collapsed = default_collapsed and not getattr(self, "_detail_expand_all", False)
        if _collapsed:
            base_h = 0          # 默认折叠：不显示内容区
        else:
            base_h = _COLLAPSED_H if has_content else _EMPTY_H

        # 2026-09-07（第5条改进）：可折叠字段同样用"淡彩卡片块"——标签主题色 + 内容状态小标签
        block, label_c, box_bg, box_border = self._begin_field_block(key)
        head = ctk.CTkFrame(block, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 0))
        lbl = ctk.CTkLabel(head, text=label, text_color=label_c,
                           font=("Microsoft YaHei", 12, "bold"), anchor="w")
        lbl.pack(side="left")
        status = ctk.CTkLabel(
            head,
            text=("● 有内容" if has_content else "（无内容）"),
            font=("Microsoft YaHei", 10),
            text_color=(_C_OK if has_content else "#9aa4b1"))
        status.pack(side="left", padx=(6, 0))
        toggle = None
        # 2026-09-16：default_collapsed 时即使无内容也显示"展开"按钮（保持交互一致）
        if has_content or default_collapsed:
            toggle = ctk.CTkButton(head, text="展开", width=52, height=22, **_ADD_BTN)
            toggle.pack(side="right")

        # 内容区：文本框（+ image_plan 的「打开」按钮）+ image_desc 的图片预览区
        content = ctk.CTkFrame(block, fg_color="transparent")
        # 文本框行（⑩ image_plan 含「打开」按钮）
        if key == "image_plan":
            box_row = ctk.CTkFrame(content, fg_color="transparent")
            box = ctk.CTkTextbox(box_row, height=max(base_h, _EMPTY_H), fg_color=box_bg,
                                 border_width=1, border_color=box_border, corner_radius=6)
            box.pack(side="left", fill="x", expand=True)
            url = (e[key] or "").strip()
            if url.startswith(("http://", "https://")):
                open_btn = ctk.CTkButton(box_row, text="打开", width=52, height=28,
                                         command=lambda b=box: self._open_image_plan(b))
                open_btn.pack(side="right", padx=(6, 0))
                self._extra_field_widgets.append(open_btn)   # 浏览态一并置灰
            box_row.pack(fill="x", padx=6, pady=(0, 6))
        else:
            box = ctk.CTkTextbox(content, height=max(base_h, 1), fg_color=box_bg,
                                 border_width=1, border_color=box_border, corner_radius=6,
                                 **self._look_kwargs(key))
            box.pack(fill="x", padx=6, pady=(0, 6))
        box.insert("1.0", e[key] or "")
        box.bind("<KeyRelease>", self._mark_dirty)
        self._detail_boxes[key] = box
        full = e[key] or "（无内容）"
        # 2026-09-12（用户要求 2）：改用新的浮动提示（左移不遮正文、可滚动、同步滚动、光标行加深）
        self._attach_field_tip(box, full, _ui_common.textbox_internal(box), label)

        # ⑦ image_desc：文本框下方追加图片预览区（封面 + 图集）
        if key == "image_desc":
            self._build_image_area(parent=content, pack_now=True)

        # 默认折叠：不 pack 内容区
        if not _collapsed:
            content.pack(fill="x", padx=0, pady=(0, 2))

        if toggle is not None:
            def _toggle(_c=_collapsed):
                expanded = toggle.cget("text") == "展开"
                if expanded:
                    content.pack(fill="x", padx=0, pady=(0, 2))
                    box.configure(height=self._content_fit_height(box))
                    toggle.configure(text="收起")
                    status.configure(text="⏶ 已展开", text_color="#25639c")
                else:
                    if _c:
                        content.pack_forget()
                    else:
                        box.configure(height=base_h)
                    toggle.configure(text="展开")
                    status.configure(text=("● 有内容" if has_content else "（无内容）"),
                                     text_color=(_C_OK if has_content else "#9aa4b1"))

            toggle.configure(command=_toggle)
        return block

    @staticmethod
    def _content_fit_height(box) -> int:
        """文本框恰好显示全部内容的像素高度（含自动换行；最少 1 行）。

        2026-08-18 新增；2026-09-17（审核 R-1）：实现统一到 `ui_common.content_fit_height`
        （与「快速新建」窗口共用同一份），此处仅保留方法名以兼容既有调用点。
        """
        return _ui_common.content_fit_height(box)

    def _build_image_area(self, parent=None, pack_now: bool = True) -> ctk.CTkFrame:
        """⑦ 代表高清配图 下的"**封面 + 图集**"区（返回容器，随折叠组显隐）。

        2026-09-13（第 2 期 2-b）：在原有"封面预览 + 选择/移除"之下，新增**图集**（附加图）：
          - 本地文件与 URL 外链双源；每张可 移除 / 上移 / 下移；外链可 打开 / 下载到本地；
          - 封面仍用 `entries.image_path`（既有行为不变）。
        新建条目态（无 id）采用**做法 B**：只把源路径记在内存，保存成功后复制落库（不产生临时文件）。
        """
        parent = parent or self.detail_scroll
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        if pack_now:
            wrap.pack(fill="x", padx=8, pady=(2, 6))

        # ---- 封面行 ----
        cover = ctk.CTkFrame(wrap, fg_color="transparent")
        cover.pack(fill="x")
        # 无图时占位符为紧凑尺寸（高度与右侧两按钮一致），有图时动态放大
        self._img_view = ctk.CTkLabel(cover, text="（无关联图片）", text_color="gray",
                                      width=180, height=64, corner_radius=8,
                                      fg_color="#eceff4")
        self._img_view.pack(side="left", padx=(0, 8))
        btn_col = ctk.CTkFrame(cover, fg_color="transparent")
        btn_col.pack(side="left", fill="y")
        self._pick_img_btn = ctk.CTkButton(btn_col, text="选择图片…", width=100,
                                           command=self._pick_image)
        self._pick_img_btn.pack(pady=2)
        self._remove_img_btn = ctk.CTkButton(btn_col, text="移除图片", width=100,
                                             fg_color="#8a94a6", command=self._remove_image)
        self._remove_img_btn.pack(pady=2)

        # ---- 图集 ----
        g_head = ctk.CTkFrame(wrap, fg_color="transparent")
        g_head.pack(fill="x", pady=(8, 0))
        self._gallery_title = ctk.CTkLabel(g_head, text="图集（0 张）",
                                           font=("Microsoft YaHei", 11, "bold"),
                                           text_color="#3b5a78")
        self._gallery_title.pack(side="left")
        self._gallery_add_btn = ctk.CTkButton(g_head, text="＋ 添加本地图…", width=118,
                                              height=26, fg_color="#8a94a6",
                                              command=self._add_gallery_local)
        self._gallery_add_btn.pack(side="right", padx=(4, 0))
        self._gallery_url_btn = ctk.CTkButton(g_head, text="＋ 添加链接…", width=106,
                                              height=26, fg_color="#2f6fb0",
                                              command=self._add_gallery_url)
        self._gallery_url_btn.pack(side="right")
        self._gallery_box = ctk.CTkFrame(wrap, fg_color="transparent")
        self._gallery_box.pack(fill="x", pady=(2, 0))

        # 2026-09-15（批次 6-1，用户要求"锁定态图片/图集写操作**完全禁止**"）：
        #   ⚠ 根因（实测确认）：这几个按钮原先**总是以 normal 创建**，而"锁定/浏览"的状态刷新
        #   （`_apply_browse()` 在 `_apply_lock_state()` 内被调用）**可能在渲染详情区之前**就已执行过
        #   ⇒ 锁定态下渲染出来的图片/图集按钮仍是**可点**的（此前多次实测均为 normal）。
        #   现按当前"锁定 或 浏览态"**直接以置灰状态创建**，与详情区其余控件保持一致。
        _img_st = ("disabled" if (self._lock_on or (self._browse_mode and not self._adding_new
                                                   and self._detail_entry_id is not None))
                   else "normal")

        def _apply_img_st() -> None:
            for _btn in (self._pick_img_btn, self._remove_img_btn,
                         self._gallery_add_btn, self._gallery_url_btn,
                         *getattr(self, "_gallery_btns", [])):
                try:
                    _btn.configure(state=_img_st)
                except Exception:
                    pass

        self._render_image_preview()
        self._render_gallery()
        _apply_img_st()      # 图集项按钮在 `_render_gallery()` 内创建 → 在其后统一套用同一状态
        return wrap

    # ---- 图集：数据与渲染（2026-09-13，第 2 期 2-b）------------------- #
    def _gallery_items(self) -> list:
        """当前应展示的图集项：已存条目取库；新建态取内存暂存（做法 B）。"""
        if self._detail_entry_id is not None:
            return self.db.list_entry_images(self._detail_entry_id)
        return list(self._staged_gallery)

    @staticmethod
    def _load_thumb(rel_or_abs: str, max_size=(96, 64)):
        """读取图片缩略图（相对 data/ 或绝对路径）；失败返回 None（离线安全，不联网）。

        2026-09-14：新增 `max_size` 参数（默认沿用图集的 96×64）——"图像"类型自定义字段的
        内嵌预览用更大尺寸（220×150），两者共用本函数。
        """
        if not rel_or_abs:
            return None
        try:
            full = (rel_or_abs if os.path.isabs(rel_or_abs)
                    else os.path.join(config.data_dir(), rel_or_abs))
            if not os.path.isfile(full):
                return None
            from PIL import Image
            pil = Image.open(full)
            pil.thumbnail(max_size)
            return ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
        except Exception:
            return None

    def _render_gallery(self) -> None:
        """重绘图集列表（标题计数 + 每行缩略图/外链标记 + 操作按钮）。"""
        box = getattr(self, "_gallery_box", None)
        if box is None or not box.winfo_exists():
            return
        for w in box.winfo_children():
            w.destroy()
        self._gallery_btns = []
        items = self._gallery_items()
        title = getattr(self, "_gallery_title", None)
        if title is not None and title.winfo_exists():
            title.configure(text=f"图集（{len(items)} 张）")
        if not items:
            ctk.CTkLabel(box, text="（暂无附加图）", text_color="#9aa4b1",
                         font=("Microsoft YaHei", 10)).pack(anchor="w", padx=2)
            return
        browsing = bool(self._browse_mode and not self._adding_new
                        and self._detail_entry_id is not None)
        for idx, it in enumerate(items):
            row = ctk.CTkFrame(box, fg_color="#f7f8fa", corner_radius=6)
            row.pack(fill="x", pady=2)
            if it.get("kind") == "url":
                ctk.CTkLabel(row, text="🔗 外链", text_color="#2f6fb0",
                             font=("Microsoft YaHei", 10, "bold")).pack(side="left", padx=6, pady=4)
                desc = str(it.get("source_url") or "")
            else:
                src = it.get("path") or it.get("_src") or ""
                thumb = self._load_thumb(src)
                if thumb is not None:
                    ctk.CTkLabel(row, image=thumb, text="").pack(side="left", padx=6, pady=4)
                else:
                    ctk.CTkLabel(row, text="（图片不可读）", text_color="#9aa4b1",
                                 font=("Microsoft YaHei", 10)).pack(side="left", padx=6, pady=4)
                desc = os.path.basename(str(src))
            ctk.CTkLabel(row, text=desc[:60], text_color="#5b6b7c", anchor="w",
                         font=("Microsoft YaHei", 10)).pack(side="left", padx=(0, 6),
                                                             fill="x", expand=True)
            btns = []
            btns.append(ctk.CTkButton(row, text="移除", width=52, height=24, fg_color=_C_DANGER,
                                      command=lambda i=idx: self._remove_gallery_item(i)))
            btns.append(ctk.CTkButton(row, text="↓", width=28, height=24, fg_color="#8a94a6",
                                      command=lambda i=idx: self._move_gallery_item(i, 1)))
            btns.append(ctk.CTkButton(row, text="↑", width=28, height=24, fg_color="#8a94a6",
                                      command=lambda i=idx: self._move_gallery_item(i, -1)))
            if it.get("kind") == "url":
                btns.append(ctk.CTkButton(row, text="打开", width=48, height=24,
                                          fg_color="#2f6fb0",
                                          command=lambda u=it.get("source_url"): self._open_url(u)))
                if self._detail_entry_id is not None:
                    btns.append(ctk.CTkButton(
                        row, text="下载到本地", width=96, height=24, fg_color=_C_OK,
                        command=lambda i=idx: self._download_gallery_item(i)))
            for b in btns:
                b.configure(state=("disabled" if browsing else "normal"))
                b.pack(side="right", padx=(3, 0))
                self._gallery_btns.append(b)

    def _open_url(self, url: str) -> None:
        """用系统默认浏览器打开链接（离线安全：仅用户主动点击时调用）"""
        u = (url or "").strip()
        if not u:
            return
        try:
            import webbrowser
            webbrowser.open(u)
        except Exception as exc:
            self.toast(f"打开链接失败：{exc}", color=_C_DANGER)

    def _add_gallery_local(self) -> None:
        """图集：添加本地图片（已存条目立即复制落库；新建态仅暂存源路径＝做法 B）"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("添加图集图片"):
            return
        path = filedialog.askopenfilename(
            title="选择图片（加入图集）", parent=self,
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                       ("所有文件", "*.*")])
        if not path:
            return
        if self._detail_entry_id is None:
            self._staged_gallery.append({"id": None, "kind": "local", "path": "",
                                         "_src": path, "source_url": ""})
            self._mark_dirty()
        else:
            try:
                ext = os.path.splitext(path)[1].lower() or ".png"
                rel = self.db.gallery_file_rel(self._detail_entry_id, ext)
                dest = os.path.join(config.data_dir(), rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(path, dest)
                self.db.add_entry_image(self._detail_entry_id, "local", path=rel)
                self.toast("✅ 已加入图集")
            except Exception as exc:
                self.toast(f"加入图集失败：{exc}", color=_C_DANGER)
                return
        self._render_gallery()

    def _add_gallery_url(self) -> None:
        """图集：添加图片外链（http/https）"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("添加图集外链"):
            return
        url = simpledialog.askstring("添加图片链接", "请输入图片网址（http/https）：", parent=self)
        if not url or not url.strip():
            return
        u = url.strip()
        if not u.startswith(("http://", "https://")):
            messagebox.showwarning("提示", "网址需以 http:// 或 https:// 开头。", parent=self)
            return
        if self._detail_entry_id is None:
            self._staged_gallery.append({"id": None, "kind": "url", "path": "",
                                         "_src": "", "source_url": u})
            self._mark_dirty()
        else:
            self.db.add_entry_image(self._detail_entry_id, "url", source_url=u)
        self._render_gallery()

    def _remove_gallery_item(self, idx: int) -> None:
        """图集：移除第 idx 张"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("移除图集图片"):
            return
        items = self._gallery_items()
        if idx < 0 or idx >= len(items):
            return
        if self._detail_entry_id is None:
            self._staged_gallery.pop(idx)
            self._mark_dirty()
        else:
            self.db.remove_entry_image(items[idx]["id"], purge_file=True)
        self._render_gallery()

    def _move_gallery_item(self, idx: int, delta: int) -> None:
        """图集：第 idx 张上移/下移"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("调整图集顺序"):
            return
        items = self._gallery_items()
        if idx < 0 or idx >= len(items):
            return
        if self._detail_entry_id is None:
            j = idx + delta
            if 0 <= j < len(self._staged_gallery):
                self._staged_gallery[idx], self._staged_gallery[j] = \
                    self._staged_gallery[j], self._staged_gallery[idx]
                self._mark_dirty()
        else:
            if not self.db.swap_entry_image_order(self._detail_entry_id,
                                                  items[idx]["id"], delta):
                return
        self._render_gallery()

    def _download_gallery_item(self, idx: int) -> None:
        """图集：把外链图"下载到本地"（联网；失败保持外链并保留 source_url）"""
        # 2026-09-15（批次 6-3b）：统一守卫（本入口原先仅靠按钮置灰，方法内无判断）
        if not self._assert_unlocked("下载图集图片"):
            return
        items = self._gallery_items()
        if idx < 0 or idx >= len(items) or self._detail_entry_id is None:
            return
        it = items[idx]
        if it.get("kind") != "url":
            return
        url = (it.get("source_url") or "").strip()
        if not url:
            return
        try:
            import urllib.parse
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                ext = ".jpg"
            rel = self.db.gallery_file_rel(self._detail_entry_id, ext)
            dest = os.path.join(config.data_dir(), rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            self.db.set_entry_image_local(it["id"], rel, source_url=url)
            self.toast("✅ 已下载到本地（原链接已保留）")
        except Exception as exc:
            self.toast(f"下载失败（网络或链接不可用）：{exc}", color=_C_DANGER)
        self._render_gallery()

    def _materialize_staged_images(self, entry_id: int) -> None:
        """新建条目保存成功后，把"做法 B"暂存的封面与图集源文件复制落库（2-b/2-c）。"""
        # 2026-09-15（批次 6-3b）：统一守卫（本方法由 _save_new_entry 内部调用，外层已拦；
        #   此处为"防御性冗余"）
        if not self._assert_unlocked("保存图片"):
            return
        try:
            if self._staged_cover and os.path.isfile(self._staged_cover):
                src = self._staged_cover
                ext = os.path.splitext(src)[1].lower() or ".png"
                img_dir = os.path.join(config.data_dir(), config.IMAGES_DIR_NAME)
                os.makedirs(img_dir, exist_ok=True)
                name = f"entry_{entry_id}{ext}"
                shutil.copyfile(src, os.path.join(img_dir, name))
                self.db.set_entry_image(entry_id,
                                        os.path.join(config.IMAGES_DIR_NAME, name))
            for it in list(self._staged_gallery):
                if it.get("kind") == "url":
                    self.db.add_entry_image(entry_id, "url",
                                            source_url=it.get("source_url", ""))
                    continue
                src = it.get("_src") or ""
                if not src or not os.path.isfile(src):
                    continue
                ext = os.path.splitext(src)[1].lower() or ".png"
                rel = self.db.gallery_file_rel(entry_id, ext)
                dest = os.path.join(config.data_dir(), rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(src, dest)
                self.db.add_entry_image(entry_id, "local", path=rel)
        except Exception as exc:
            self.toast(f"图片保存失败：{exc}", color=_C_DANGER)
        finally:
            self._staged_cover = None
            self._staged_gallery = []

    def _mark_dirty(self, _event=None) -> None:
        self._detail_dirty = True

    def _open_image_plan(self, box) -> None:
        """打开"⑩图像获取方案"文本框中的链接（2026-08-18 第015条扩展）。

        实时读取文本框当前内容，提取第一个 http(s) 链接并用默认浏览器打开；
        未找到链接时给出轻提示。这样使用者输入链接后（无论是否保存）点击
        "打开"按钮即可直达对应图片/网址。
        """
        text = box.get("1.0", "end").strip() if box else ""
        m = re.search(r"https?://[^\s\"'<>]+", text)
        if m:
            webbrowser.open(m.group(0))
        else:
            self.toast("未找到链接（请输入 http:// 或 https:// 开头网址）", color=_C_DANGER)

    def _box_text(self, key) -> str:
        if key in self._list_field_values:   # 2026-09-13（3-a-2/3-b/3-c）：列表框字段
            if self._is_ref_source(self.db.list_field_config(key)):
                txt = self._list_display_text(key)   # 引用型：导出/展示用可读显示名
                return "" if txt == "（未选择）" else txt
            return "、".join(self._list_field_values.get(key) or [])
        box = self._detail_boxes.get(key)
        return box.get("1.0", "end").strip() if box else ""

    def _toggle_detail_show_all(self) -> None:
        """固定头部"显示全部字段/精简显示"开关（2026-09-13，1-B 修复）。

        - 新增条目态：展开/收起 ②~⑦ 补充组（行为不变）；
        - 详情态（按当前状态分三种）：
          1) 处于"会话覆盖"中 → 取消覆盖，回到显示模式默认（重建详情区）；
          2) 当前有隐藏项（精简）→ 置会话覆盖，一键显示全部字段并展开；
          3) 无隐藏项（全部显示）→ 沿用既有"展开/收起"折叠组行为。
        """
        if self._adding_new:
            if self._add_group_pairs:
                self._toggle_add_group()
            self._refresh_detail_header()
            return
        if self._detail_entry_id is None:
            self._refresh_detail_header()
            return
        e = self.db.get_entry(self._detail_entry_id)
        if e is None:
            self._refresh_detail_header()
            return
        if self._detail_show_all:                    # 1) 取消覆盖 → 回模式默认
            self._detail_show_all = False
            self._detail_expand_all = False
            self._show_detail(e)
            return
        if self._detail_hidden_keys(e):              # 2) 有隐藏项 → 一键显示全部
            self._detail_show_all = True
            self._detail_expand_all = True
            self._show_detail(e)
            return
        # 3) 无隐藏项 → 切换"全部展开/收起所有可折叠字段"（2026-09-16 批次 13）
        self._detail_expand_all = not self._detail_expand_all
        self._show_detail(e)

    def _manual_hidden_note(self) -> str:
        """固定头部用的"手动隐藏项"提示片段（无手动隐藏项时返回空串）。

        2026-09-16（批次 14）：手动隐藏是**硬隐藏**——"⏵ 显示全部字段"按钮不会恢复它，
        因此当存在手动隐藏项时在状态行明确提示，避免用户误以为"字段丢了"。
        """
        try:
            n = len(self.db.get_hidden_field_keys())
        except Exception:                                   # noqa: BLE001
            return ""
        if not n:
            return ""
        return f"（另有 {n} 项已隐藏，可在「字段管理」中恢复）"

    def _refresh_detail_header(self) -> None:
        """同步固定头部状态行：左侧状态标题、右侧开关文案与可用性（与折叠组状态联动）。

        2026-09-13（1-B）：并入"显示模式/会话覆盖"状态——
        精简且有隐藏项时按钮为"⏵ 显示全部字段"；覆盖中则显示"（临时）"并提供"回到精简显示"。
        2026-09-16（批次 14）：手动隐藏项（字段管理里的「隐藏」开关）为**硬隐藏**，
        按钮文案/行为**不**受其影响（按钮只作用于"显示模式隐藏项"），仅在状态行追加提示。
        """
        lbl = getattr(self, "detail_state_lbl", None)
        btn = getattr(self, "show_all_btn", None)
        if lbl is None or btn is None:
            return
        if self._adding_new:
            opened = bool(self._add_group_open)
            enabled = bool(self._add_group_pairs)
            lbl.configure(text="新增条目 · 已展开全部字段" if opened else "新增条目 · 精简模式")
            btn.configure(text="⏸ 精简显示" if opened else "⏵ 显示全部字段",
                          state="normal" if enabled else "disabled")
            return
        # 2026-09-16（批次 13）：②~⑦ 已拆为独立可折叠块，按钮可用性只要有当前条目即可
        enabled = bool(self._detail_entry_id is not None)
        state = "normal" if enabled else "disabled"
        e = self.db.get_entry(self._detail_entry_id) if self._detail_entry_id else None
        note = self._manual_hidden_note()   # 2026-09-16（批次 14）
        if self._detail_show_all:
            lbl.configure(text="详情 · 已显示全部字段（临时）" + note)
            btn.configure(text="⏸ 回到精简显示", state=state)
            return
        if e is not None and self._detail_hidden_keys(e):
            lbl.configure(text="详情 · 精简模式" + note)
            btn.configure(text="⏵ 显示全部字段", state=state)
            return
        # 2026-09-16：用 _detail_expand_all 替代 _detail_group_open
        opened = bool(self._detail_expand_all)
        lbl.configure(text=("详情 · 已展开全部字段" if opened else "详情 · 精简模式") + note)
        btn.configure(text="⏸ 精简显示" if opened else "⏵ 显示全部字段", state=state)

    def _save_detail(self) -> None:
        # 2026-09-15（批次 6-3b）：统一守卫（原先仅靠 save_btn 置灰；方法内无 _lock_on 判断）
        if not self._assert_unlocked("保存条目修改"):
            return
        if self._detail_entry_id is None:
            return
        cur = self.db.get_entry(self._detail_entry_id)
        if cur is None:
            return

        def _field(key: str) -> str:
            # 2026-09-09：②~⑦ 折叠组内字段始终构建；仅当某字段确实未构建时
            # （理论兜底）才保留数据库原值，避免误清空。
            # 2026-09-16（批次 14）：本兜底同时是"字段被手动隐藏/被显示模式隐藏"时的
            # 数据保护——未渲染 ⇒ 不在 _detail_boxes ⇒ 保存时保留库中原值，绝不清空。
            return self._box_text(key) if key in self._detail_boxes else cur[key]

        # 2026-09-16（批次 14，安全修复）：此前 `intro`/`prompt_cn`/`prompt_en`/`image_plan`
        #   直接调用 `_box_text()`（未渲染时返回 ""），一旦这些区块被隐藏，
        #   保存就会把它们**清空**。现统一改为 `_field()`：
        #   已渲染时行为与原来完全一致；未渲染时保留数据库原值。
        e = Entry(id=self._detail_entry_id, category_id=cur["category_id"],
                  name=self._name_entry.get().strip() or cur["name"],
                  intro=_field("intro"),
                  origin=_field("origin"), features=_field("features"),
                  scenes=_field("scenes"), works=_field("works"),
                  image_desc=_field("image_desc"),
                  prompt_cn=_field("prompt_cn"), prompt_en=_field("prompt_en"),
                  image_plan=_field("image_plan"),
                  image_path=cur["image_path"], is_favorite=cur["is_favorite"])
        # 2026-09-07：保存前同内容一致性轻提示（排除自身）
        dup = self.db.find_content_duplicates(self.db.content_key(e),
                                              exclude_entry_id=self._detail_entry_id)
        if dup:
            sample = "、".join(d["name"] for d in dup)
            if not messagebox.askyesno(
                    "内容重复提示",
                    f"检测到 {len(dup)} 个相同内容的其它条目（示例：{sample}）。\n\n仍要保存吗？",
                    parent=self):
                return
        self.db.update_entry(e)
        # 2026-09-13（1-A-5 第 2 步）：同步保存自定义字段取值（内置字段仍走 entries 既有列）
        self._save_custom_field_values(self._detail_entry_id)
        self._warn_field_problems()   # P6：类型格式校验未通过的字段给出提示
        # 2026-09-13（1-C-2）：同步保存标签
        self._save_entry_tags(self._detail_entry_id)
        self._detail_dirty = False
        self.toast("✅ 已保存")
        self._restore_view()
        # 2026-08-18（第015条）：保存后重建详情区，使"⑩图像获取方案"的"打开"按钮与最新链接对应
        # （使用者输入链接点击保存后，按钮立即指向该链接，点击即可打开）
        e2 = self.db.get_entry(self._detail_entry_id)
        if e2 is not None:
            self._show_detail(e2)

    def _reset_detail(self) -> None:
        e = self.db.get_entry(self._detail_entry_id) if self._detail_entry_id else None
        self._detail_dirty = False
        self._show_detail(e)

    def _confirm_unsaved(self) -> bool:
        """切换前检查未保存修改；返回是否继续切换

        2026-09-06：支持"新增条目"态——有未保存的新增内容时同样提示，
        【是】保存本次新增后继续切换、【否】放弃、【取消】返回。
        2026-09-15（批次 6-3，用户选定"锁定态不弹保存询问"）：锁定＝只读 ⇒
        未保存的修改**不落库**、也不再弹窗打扰；直接按"不保存（放弃）"继续切换。
        """
        if self._lock_on:
            self._detail_dirty = False
            return True
        if not self._detail_dirty:
            return True
        if self._adding_new:
            r = messagebox.askyesnocancel(
                "未保存的新增内容",
                "当前新增的条目尚未保存。\n\n【是】保存新增　【否】放弃新增　【取消】返回")
            if r is None:
                return False
            if r:
                return self._save_new_entry(exit_mode=True)
            self._detail_dirty = False
            return True
        if self._detail_entry_id is None:
            return True
        r = messagebox.askyesnocancel(
            "未保存的修改",
            "当前条目有未保存的修改。\n\n【是】保存修改　【否】放弃修改　【取消】返回")
        if r is None:
            return False
        if r:
            self._save_detail()
        else:
            self._detail_dirty = False
        return True

    # ------------------------------------------------------------------ #
    # 主界面就地"新增条目"（2026-09-06）
    # ------------------------------------------------------------------ #
    def _cat_label(self, cat_id: int) -> str:
        """分类全路径显示文本（如"一级 › 二级"）"""
        parts = []
        cid = cat_id
        seen = set()
        while cid is not None and cid not in seen:
            c = self.db.get_category(cid)
            if not c:
                break
            parts.append(c["name"])
            seen.add(cid)
            cid = c["parent_id"]
        return " › ".join(reversed(parts))

    def _full_location_path(self, cat_id: int) -> str:
        """位置"面包屑"完整路径：项目类别 › 根目录 › 一级分类 › …（2026-09-07 第2条改进）。

        任一分级分类都可能挂在某根目录（L0）下；根目录再归属某项目类别（最高层级），
        据此拼出从最高层到自身的完整链；未关联到任何根目录时退化为分类链本身。
        """
        root = self.db.category_root(cat_id)
        segs = []
        if root:
            doms = self.db.linked_domains(root)
            if doms:
                d = doms[0]
                if d.get("project_id"):
                    p = self.db.get_project(d["project_id"])
                    if p:
                        segs.append(p["name"])
                segs.append(d["name"])
        parts = []
        cid = cat_id
        seen = set()
        while cid is not None and cid not in seen:
            c = self.db.get_category(cid)
            if not c:
                break
            parts.append(c["name"])
            seen.add(cid)
            cid = c["parent_id"]
        segs.extend(reversed(parts))
        return " › ".join(segs) if segs else "未分类"

    def _begin_field_block(self, key: str, pady_top: int = 10) -> tuple:
        """为 ②~⑩ 某一字段新建"淡彩圆角卡片块"（2026-09-07 第5条改进）。

        返回 (block, 标签文字色, 输入框底色, 输入框描边色)，调用方在其内部放标签与输入框，
        各字段因此拥有各自的浅彩底色与主题色标签，与 ① 名称深蓝卡形成统一层次。
        """
        label_c, block_bg, box_bg, box_border = _field_style(key)
        block = ctk.CTkFrame(self.detail_scroll, fg_color=block_bg, corner_radius=10,
                             border_width=1, border_color=box_border)
        block.pack(fill="x", padx=10, pady=(pady_top, 2))
        return block, label_c, box_bg, box_border

    def _build_name_field(self, initial: str = "") -> None:
        """详情/新增表单的"① 条目名称（风格名称）"输入字段（2026-09-07）。

        以淡蓝卡片 + 细边框突出"主字段"，与下方 ②~⑩ 淡彩字段块形成统一层次。
        """
        card = ctk.CTkFrame(self.detail_scroll, fg_color="#e2edfa",
                            corner_radius=10, border_width=1, border_color="#a9c6e4")
        card.pack(fill="x", padx=10, pady=(10, 2))
        ctk.CTkLabel(card, text="① 条目名称（风格名称）",
                     font=("Microsoft YaHei", 13, "bold"), anchor="w",
                     text_color="#1d4e89").pack(fill="x", padx=12, pady=(8, 2))
        # 2026-09-15（批次 4）：①名称框的字体/字号可由设置叠加（组 name_field）。
        #   · 未设置时 `font()` 原样返回基础字体、高度保持 44 ⇒ 与改动前**完全一致**；
        #   · 设置了字号时按字号**等比放大输入框高度**（44 × size/15），避免大字号被上下裁切。
        _nf_base = ("Microsoft YaHei", 15, "bold")
        _nf_font = ui_appearance.font("name_field", _nf_base, self._ui_appearance)
        _nf_size = ui_appearance.size("name_field", self._ui_appearance)
        _nf_h = max(44, int(round(44 * int(_nf_size) / 15.0))) if _nf_size else 44
        self._name_entry = ctk.CTkEntry(card, height=_nf_h, font=_nf_font)
        self._name_entry.pack(fill="x", padx=12, pady=(0, 8))
        self._name_entry.insert(0, initial)
        self._name_entry.bind("<KeyRelease>", self._mark_dirty)
        # 2026-09-14（阶段 3）：①名称框**回车 / 失焦**时自动推荐一次标签（T1，无需开关）
        self._name_entry.bind("<Return>", self._on_name_committed)
        self._name_entry.bind("<FocusOut>", self._on_name_committed)

    def _build_detail_location_hint(self, entry_id: int) -> None:
        """详情区顶部"所在位置"提示行（2026-09-07 阶段3）。

        列出条目全部位置（面包屑式完整链）：主位置标"主"，额外关联位置各带"×"解除按钮。
        2026-09-07（第2条改进）：面包屑带出「项目类别 › 根目录 › …」两级，
        并将整个面包屑栏背景改为更淡的浅蓝灰底、轻色小标签。
        """
        locs = self.db.list_entry_locations(entry_id)
        if not locs:
            return
        main_id = (self.db.get_entry(entry_id) or {}).get("category_id")
        head = ctk.CTkFrame(self.detail_scroll, fg_color="#eaf1f9",
                            corner_radius=10, border_width=1,
                            border_color="#d6e2ef")
        head.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(head, text="🧭 位置", font=("Microsoft YaHei", 12, "bold"),
                     text_color="#5a6f88").pack(side="left", padx=(12, 4), pady=6)
        for cid in locs:
            bread = self._full_location_path(cid)
            is_main = cid == main_id
            # 主位置=浅绿、关联=浅灰蓝（浅色背景+深色字），整体观感更轻
            if is_main:
                chip = ctk.CTkFrame(head, fg_color="#e3f2ea", corner_radius=8)
                tag = "主"
                txt_color = "#1f7a50"
            else:
                chip = ctk.CTkFrame(head, fg_color="#e8eef6", corner_radius=8)
                tag = "关联"
                txt_color = "#25639c"
            chip.pack(side="left", padx=(0, 6), pady=6)
            ctk.CTkLabel(chip, text=f"{tag} · {bread}",
                         font=("Microsoft YaHei", 11),
                         text_color=txt_color).pack(side="left", padx=6, pady=2)
            if not is_main:  # 关联位置提供"解除"按钮（主位置用右键菜单移动/解除）
                ctk.CTkButton(
                    chip, text="×", width=22, height=20, fg_color="#9aa9ba",
                    command=lambda c=cid: self._remove_detail_location(entry_id, c)
                ).pack(side="right", padx=(0, 3), pady=2)
        # 提示：多位置条目编辑一处全同步
        if len(locs) > 1:
            ctk.CTkLabel(head, text="（同一条目编辑后各位置同步）",
                         text_color="#8aa0b5", font=("Microsoft YaHei", 10)
                         ).pack(side="left", padx=(2, 0))

    def _build_detail_time_line(self, e: dict) -> None:
        """详情区「🕒 新增于 … · 修改于 …」只读时间行（2026-09-16 批次 11-7，用户要求 3）。

        数据来源：`entries.created_at / updated_at`（插入时即写入，格式 `YYYY-MM-DD HH:MM:SS`）。
        **纯展示**：不写库、不改任何字段；时间缺失时整行不渲染（老库/异常数据不显示空壳）。
        位置：紧跟在「🧭 位置」面包屑之后，所有条目（含"未分类"）都会显示。
        """
        _ca = str((e or {}).get("created_at") or "").strip()
        _ua = str((e or {}).get("updated_at") or "").strip()
        if not (_ca or _ua):
            return
        _txt = ""
        if _ca:
            _txt = "🕒 新增于 %s" % _ca
        if _ua:
            _txt += ("　·　修改于 %s" % _ua) if _txt else ("🕒 修改于 %s" % _ua)
        ctk.CTkLabel(self.detail_scroll, text=_txt, text_color="#8aa0b5", anchor="w",
                     font=("Microsoft YaHei", 10)).pack(fill="x", padx=14, pady=(2, 0))

    def _remove_detail_location(self, entry_id: int, cat_id: int) -> None:
        """详情位置行"×"：解除该条目在某关联分类的位置"""
        if self._lock_on or self._browse_mode:  # 2026-09-09：浏览态禁止改动
            return
        self.db.unlink_entry(entry_id, cat_id)
        self._restore_view()
        e = self.db.get_entry(entry_id)
        if e is not None:
            self._show_detail(e)
        self.toast("✅ 已解除该位置关联")

    def _start_new_entry(self) -> None:
        """条目区"✚ 新增条目"：详情区切入空白新增态（连续录入用）。

        目标分类取自当前视图：叶子分类视图 → 该分类；未分类视图 → 未分类。
        其他视图按钮已置灰，不会进入本方法。
        """
        if self._lock_on:
            return
        if not self._confirm_unsaved():  # 丢弃/保存当前编辑或新增内容后继续
            return
        kind = self._view[0] if self._view else None
        if kind == "cat":
            target = self._cur_cat_id
        elif kind == "uncat":
            target = None
        else:
            # 连续录入时视图可能已切走（如搜索中）：退出新增态并清空详情，避免残留空表单
            self.toast("请先在分类树/未分类中定位再新增条目", color=_C_DANGER)
            self._show_detail(None)
            return
        self._adding_new = True
        self._add_target = target
        self._detail_entry_id = None
        self._detail_dirty = False
        self._build_new_entry_editor(target)

    def _build_new_entry_editor(self, target) -> None:
        """新增态表单：空白 9 字段编辑器，②-⑦ 详情信息默认折叠为一组。

        2026-09-06：与详情编辑共用同一批字段控件；不采用根目录显隐策略。
        2026-09-09：② 介绍 一并并入折叠组（此前仅 ③-⑦ 折叠、② 常显占行），
        折叠态只占 1 行标题，点"展开"才显示 ②~⑦ 全部字段（⑧⑨⑩ 常显）。
        """
        self._clear_frame(self.detail_scroll)
        self._detail_boxes = {}
        self._gallery_btns = []   # 2026-09-13（2-b）：重建前清空图集按钮引用（避免残留旧控件）
        # 2026-09-13（3-a-2）：列表框字段状态同样清空后重建
        self._list_field_values = {}
        self._list_field_snaps = {}
        self._list_field_labels = {}
        self._list_field_btns = []
        self._extra_field_getters = {}   # P6：专用类型字段的取值回调
        self._extra_field_widgets = []   # P6：专用类型字段的控件（浏览态置灰用）
        self._extra_field_widgets_by_key = {}
        self._typed_img_holders = {}     # 图像字段预览容器（重建时清空）
        self._typed_img_refs = {}
        self._detail_dirty = False
        self._add_group_open = False
        self._add_group_pairs = []
        # 2026-09-13（2-c）：进入新增态时清空图片暂存（做法 B）
        self._staged_cover = None
        self._staged_gallery = []
        self._add_prompt_toggles = {}  # 2026-09-11（用户要求 1）：重建表单时清空 ⑧/⑨ 展开按钮引用
        self._hide_field_tip()         # 2026-09-12（用户要求 2）：新增表单重建前先收起字段浮动提示
        self._browse_mode = False  # 新增必须录入 → 强制编辑模式
        try:
            self.edit_mode_toggle.configure(text="✏️ 编辑")   # 2026-09-14：普通按钮改为 configure(text)
            self.edit_mode_toggle.configure(state="disabled")
        except Exception:
            pass

        loc = self._cat_label(target) if target is not None else "未分类"
        self.detail_scroll.configure(label_text="")  # 2026-09-09：固定头部状态行承担顶部状态
        cap = ctk.CTkLabel(self.detail_scroll, text=f"＋ 将新增到：「{loc}」",
                           font=("Microsoft YaHei", 12, "bold"),
                           text_color=_C_OK, anchor="w")
        cap.pack(fill="x", padx=8, pady=(8, 0))

        # 顶部命令按钮：收藏/删除/复制对新条目无意义 → 禁用（名称框随表单置入详情区）
        self.fav_btn.configure(state="disabled", command=lambda: None)
        self.del_btn.configure(state="disabled", command=lambda: None)
        for b in (self.move_btn, self.link_btn, self.copyto_btn,
                  self.copy_all_btn, self.copy_cn_btn, self.copy_en_btn):
            b.configure(state="disabled", command=lambda: None)
        self.save_btn.configure(state="normal", command=self._save_new_entry)
        self.reset_btn.configure(state="normal", command=self._reset_new_entry)

        # 2026-09-07：名称输入框作为表单第一个字段（醒目大输入框）
        self._build_name_field(initial="")
        # 2026-09-13（1-C-2）：新增态同样提供 🏷 标签区块（起始为空）
        self._build_tag_block([])
        # ②-⑦ 补充信息（默认折叠为 1 行，点"展开"逐条填写）
        self._build_add_extra_group()
        # ⑧ 中文版提示词 / ⑨ 英文版提示词（固定约 6 行高，方便直接录入）
        # 2026-09-13（1-A-3 元数据驱动·只读等价）：显示名取自 field_defs（支持改名），回退内置默认名。
        labels = self._field_labels_map()
        anchor = self._add_field_block(labels.get("prompt_cn", "⑧ 中文版提示词"),
                                       "prompt_cn", prompt=True)
        self._add_field_block(labels.get("prompt_en", "⑨ 英文版提示词"),
                              "prompt_en", prompt=True)
        # ⑩ 图像获取方案（右侧"打开"按钮，与详情一致）
        self._add_field_block(labels.get("image_plan", "⑩ 图像获取方案"), "image_plan")
        self._add_group_anchor = anchor  # 折叠组展开时的锚点：⑧ 字段块
        # 2026-09-13（1-A-5 第 2 步）：自定义字段（新增态同样按 field_defs 渲染，起始为空）
        self._add_custom_field_blocks()
        # 2026-09-10（用户要求）：新增表单文本框内的滚轮同样转给详情区滚动
        self._install_detail_wheel(self.detail_scroll)
        # 2026-09-09：新增表单文本框同样启用撤销/重做
        _enable_text_undo(self.detail_scroll)
        self._apply_browse()
        self._refresh_detail_header()
        # 2026-09-15 17:15（批次 8-D，用户确认）：新增态同样挂接 T2"输入停止后自动推荐"触发点。
        #   此前唯一调用点在详情态（_build_detail 末尾）⇒ 即使设置里打开"自动推荐标签"，
        #   主窗口新增态也不会触发任何自动推荐；此处补齐（逻辑与详情态完全一致）。
        self._bind_auto_suggest_triggers()
        self._scroll_top(self.detail_scroll)  # 2026-09-07（第4条改进）：打开新增表单回到顶部

        if not self._lock_on:
            self._name_entry.focus_set()

    def _add_field_block(self, label: str, key: str, prompt: bool = False) -> None:
        """在新增表单中渲染单个标签 + 文本框；②-⑦ 折叠组之外的字段使用。

        非折叠框沿用详情编辑的像素高度（_FIELDS）；⑧/⑨ 提示词默认 _COLLAPSED_H=120px（≈6 行），
        2026-09-11（用户要求 1）起增加"展开/收起"按钮：点"展开"按内容自适应高度、
        点"收起"还原 120px，便于编辑/粘贴较长篇幅文档；⑩ 追加"打开"按钮读取网址。
        （2026-09-07 第5条改进：与详情编辑一致的淡彩卡片块样式）
        """
        heights = {k: h for _l, k, h in _FIELDS}
        block, label_c, box_bg, box_border = self._begin_field_block(key)
        if prompt:
            # 2026-09-11（用户要求 1）：⑧/⑨ 标签行右侧增加"展开/收起"按钮；
            # 默认 120px，展开时按实际内容显示行数自适应（与浏览态 _build_collapsible_field 行为一致）。
            head = ctk.CTkFrame(block, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=(8, 0))
            ctk.CTkLabel(head, text=label, text_color=label_c,
                         font=("Microsoft YaHei", 12, "bold"), anchor="w"
                         ).pack(side="left")
            toggle = ctk.CTkButton(head, text="展开", width=52, height=22, **_ADD_BTN)
            toggle.pack(side="right")
            box = ctk.CTkTextbox(block, height=_COLLAPSED_H, fg_color=box_bg,
                                 border_width=1, border_color=box_border, corner_radius=6,
                                 **self._look_kwargs(key))   # 2026-09-15（批次 4）：⑧/⑨ 可叠加
            box.pack(fill="x", padx=6, pady=(0, 6))

            def _toggle_prompt(b=box, t=toggle):
                expanded = t.cget("text") == "展开"
                # 无内容时展开仍保持 120px（避免输入框缩成 1 行无法录入）
                # 2026-09-17（R-6）：内容读取统一经 ui_common.text_of（私有属性不落在外层）
                if expanded and _ui_common.text_of(b).strip():
                    b.configure(height=self._content_fit_height(b))
                else:
                    b.configure(height=_COLLAPSED_H)
                t.configure(text="收起" if expanded else "展开")

            toggle.configure(command=_toggle_prompt)
            self._add_prompt_toggles[key] = toggle
        else:
            ctk.CTkLabel(block, text=label, text_color=label_c,
                         font=("Microsoft YaHei", 12, "bold"), anchor="w"
                         ).pack(fill="x", padx=12, pady=(8, 2))
            if key == "image_plan":
                row = ctk.CTkFrame(block, fg_color="transparent")
                row.pack(fill="x", padx=6, pady=(0, 6))
                box = ctk.CTkTextbox(row, height=_rows_to_px(heights.get(key, 3)), fg_color=box_bg,
                                     border_width=1, border_color=box_border, corner_radius=6)
                box.pack(side="left", fill="x", expand=True)
                open_btn = ctk.CTkButton(row, text="打开", width=52, height=28,
                                         command=lambda b=box: self._open_image_plan(b))
                open_btn.pack(side="right", padx=(6, 0))
            else:
                box = ctk.CTkTextbox(block, height=_rows_to_px(heights.get(key, 3)),
                                     fg_color=box_bg, border_width=1,
                                     border_color=box_border, corner_radius=6)
                box.pack(fill="x", padx=6, pady=(0, 6))
        box.bind("<KeyRelease>", self._mark_dirty)
        self._detail_boxes[key] = box
        return block

    def _build_add_extra_group(self) -> None:
        """②~⑦ 补充信息折叠组（新增态，2026-09-09 二次修订）。

        标题条与各字段卡片均为详情滚动区的兄弟控件：折叠时只创建不 pack（仅 1 行标题）；
        点"展开"用 pack(before=锚点) 把 ②~⑦ 卡片按顺序插回标题条与 ⑧⑨⑩ 之间，
        与页面其它内容一样正常参与滚动布局，避免"容器嵌套导致展开显示不全"。
        """
        bar = ctk.CTkFrame(self.detail_scroll, fg_color="#eef2f7",
                           corner_radius=10, border_width=1, border_color="#cfd9e5")
        bar.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(bar, text="📋 ②~⑦ 补充信息",
                     font=("Microsoft YaHei", 12, "bold"),
                     text_color="#3b5a78", anchor="w").pack(side="left", padx=(12, 4), pady=5)
        ctk.CTkLabel(bar, text="（点“展开”填写）",
                     font=("Microsoft YaHei", 10), text_color="#9aa4b1"
                     ).pack(side="left", padx=(2, 6))
        self._add_group_toggle = ctk.CTkButton(bar, text="展开", width=56,
                                               height=24, **_ADD_BTN,
                                               command=self._toggle_add_group)
        self._add_group_toggle.pack(side="right", padx=(6, 8), pady=3)
        self._add_group_anchor = None  # 锚点：⑧ 中文版提示词字段块（随后创建时赋值）

        heights = {k: h for _l, k, h in _FIELDS}
        # 2026-09-13（1-A-3 元数据驱动·只读等价）：②~⑦ 显示名取自 field_defs（支持改名），
        # 回退内置默认名；高度仍沿用内置布局。
        labels = self._field_labels_map()
        for label, key in (("② 介绍", "intro"), ("③ 溯源", "origin"),
                           ("④ 核心特征", "features"), ("⑤ 应用场景", "scenes"),
                           ("⑥ 代表作", "works"), ("⑦ 代表高清配图", "image_desc")):
            label = labels.get(key, label)
            # 2026-09-07（第5条改进）：组内每字段同样用淡彩卡片块
            label_c, block_bg, box_bg, box_border = _field_style(key)
            blk = ctk.CTkFrame(self.detail_scroll, fg_color=block_bg,
                               corner_radius=10, border_width=1, border_color=box_border)
            ctk.CTkLabel(blk, text=label, text_color=label_c,
                         font=("Microsoft YaHei", 12, "bold"), anchor="w"
                         ).pack(fill="x", padx=12, pady=(8, 2))
            box = ctk.CTkTextbox(blk, height=_rows_to_px(heights.get(key, 3)),
                                 fg_color=box_bg, border_width=1,
                                 border_color=box_border, corner_radius=6)
            box.pack(fill="x", padx=6, pady=(0, 6))
            box.bind("<KeyRelease>", self._mark_dirty)
            self._detail_boxes[key] = box
            self._add_group_pairs.append((blk, box))
        # 2026-09-13（2-b/2-c）：⑦ 之后追加"封面 + 图集"区（新增态用做法 B 暂存；
        # 一并纳入折叠组，展开 ②~⑦ 时即出现"选择图片/移除图片"与图集操作）。
        self._add_group_pairs.append(
            (self._build_image_area(parent=self.detail_scroll, pack_now=False), None))

    def _toggle_add_group(self) -> None:
        """展开/收起 ②~⑦ 补充信息组（内容保留，仅切换整块显隐）"""
        anchor = getattr(self, "_add_group_anchor", None)
        if not self._add_group_open:
            if anchor is not None and anchor.winfo_exists():
                for blk, _box in self._add_group_pairs:
                    blk.pack(fill="x", pady=(4, 0), before=anchor)
            else:
                for blk, _box in self._add_group_pairs:
                    blk.pack(fill="x", pady=(4, 0))
            self._add_group_toggle.configure(text="收起")
            self._add_group_open = True
        else:
            for blk, _box in reversed(self._add_group_pairs):
                blk.pack_forget()
            self._add_group_toggle.configure(text="展开")
            self._add_group_open = False
        self._refresh_detail_header()

    def _reset_new_entry(self) -> None:
        """新增态"重置"：清空表单并回到折叠的默认形态"""
        if not self._adding_new:
            return
        self._name_entry.delete(0, "end")
        for key, box in self._detail_boxes.items():
            box.delete("1.0", "end")
        # 2026-09-11（用户要求 1）：⑧/⑨ 若已"展开"，重置时一并还原为默认 120px（折叠态）
        for key, toggle in self._add_prompt_toggles.items():
            box = self._detail_boxes.get(key)
            if box is not None:
                box.configure(height=_COLLAPSED_H)
            toggle.configure(text="展开")
        if self._add_group_open:
            self._toggle_add_group()
        # 2026-09-13（1-C-2）：标签一并清空
        self._tag_names = []
        self._refresh_tag_chips()
        ent = getattr(self, "_tag_entry", None)
        if ent is not None and ent.winfo_exists():
            ent.delete(0, "end")
        self._refresh_tag_suggest()
        # 2026-09-13（2-c）：图片暂存（封面/图集）一并清空
        self._staged_cover = None
        self._staged_gallery = []
        self._render_image_preview()
        self._render_gallery()
        self._detail_dirty = False
        self._name_entry.focus_set()

    def _save_new_entry(self, exit_mode: bool = False) -> bool:
        """保存新增条目；exit_mode=True 供"确认后切换"调用（保存后不连续新增）。

        默认（保存按钮）：保存后停留空白新增态并聚焦名称框，支持连续录入。
        名称缺失时给出提示并返回 False（阻止切换继续）。
        """
        # 2026-09-15（批次 6-3b）：统一守卫（原先仅靠 save_btn 置灰 + 入口判断；
        #   返回 False ＝ 阻止继续切换，语义与"名称缺失"一致）
        if not self._assert_unlocked("保存新增条目"):
            return False
        if not self._adding_new:
            return False
        name = self._name_entry.get().strip()
        if not name:
            messagebox.showwarning("提示", "请填写风格名称后再保存新增", parent=self)
            self._name_entry.focus_set()
            return False
        e = Entry(category_id=self._add_target, name=name,
                  intro=self._box_text("intro"), origin=self._box_text("origin"),
                  features=self._box_text("features"), scenes=self._box_text("scenes"),
                  works=self._box_text("works"), image_desc=self._box_text("image_desc"),
                  prompt_cn=self._box_text("prompt_cn"),
                  prompt_en=self._box_text("prompt_en"),
                  image_plan=self._box_text("image_plan"))
        # 2026-09-07：新增前同内容一致性轻提示
        dup = self.db.find_content_duplicates(self.db.content_key(e))
        if dup:
            sample = "、".join(d["name"] for d in dup)
            if not messagebox.askyesno(
                    "内容重复提示",
                    f"检测到 {len(dup)} 个相同内容的条目（示例：{sample}）。\n\n仍要保存新增吗？",
                    parent=self):
                self._name_entry.focus_set()
                return False
        _new_id = self.db.add_entry(e)
        # 2026-09-13（1-A-5 第 2 步）：新增保存后写入自定义字段取值（拿到新 id 才能写）
        self._save_custom_field_values(_new_id)
        self._warn_field_problems()   # P6：类型格式校验未通过的字段给出提示
        # 2026-09-13（1-C-2）：新增保存后写入标签（拿到新 id 才能写）
        self._save_entry_tags(_new_id)
        # 2026-09-13（2-c）：把"做法 B"暂存的封面/图集落库（复制文件）
        self._materialize_staged_images(_new_id)
        self._detail_dirty = False
        self._adding_new = False
        self._add_target = None
        self.toast(f"✅ 已新增：{name}")
        self._restore_view()  # 刷新条目列表，使新条目立即出现
        if exit_mode:
            self._show_detail(None)  # 交还给切换流程：清空详情，等待显示目标
            return True
        self._start_new_entry()  # 连续录入：清空并重新进入空白新增态
        return True

    # ------------------------------------------------------------------ #
    # 图片关联 / 预览 / 移除
    # ------------------------------------------------------------------ #
    def _render_image_preview(self) -> None:
        """渲染封面预览：已存条目取 `entries.image_path`；新建态取"做法 B"暂存的源文件。"""
        if not hasattr(self, "_img_view"):
            return
        if self._detail_entry_id is None:
            # 新建态：只显示暂存封面（源文件绝对路径）
            src = self._staged_cover or ""
            if src and os.path.isfile(src):
                try:
                    from PIL import Image
                    pil = Image.open(src)
                    pil.thumbnail((320, 200))
                    img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                    self._img_view.configure(image=img, text="",
                                             width=pil.size[0], height=pil.size[1])
                except Exception:
                    self._img_view.configure(image=None, text="（图片加载失败）",
                                             width=180, height=64)
            else:
                self._img_view.configure(image=None, text="（未选择封面）",
                                         width=180, height=64)
            return
        e = self.db.get_entry(self._detail_entry_id)
        p = (e or {}).get("image_path") or ""
        full = os.path.join(config.data_dir(), p) if p else ""
        if p and os.path.isfile(full):
            try:
                from PIL import Image
                pil = Image.open(full)
                pil.thumbnail((320, 200))
                img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                self._img_view.configure(image=img, text="",
                                         width=pil.size[0], height=pil.size[1])
            except Exception:
                self._img_view.configure(image=None, text="（图片加载失败）",
                                         width=180, height=64)
        else:
            # 无图：紧凑占位（高度与右侧"选择/移除图片"两按钮一致）
            self._img_view.configure(image=None, text="（无关联图片）",
                                     width=180, height=64)

    def _pick_image(self) -> None:
        """选择封面图片。

        2026-09-13（2-b/2-c）：**新建条目态也可选图**（做法 B：只记源路径，保存成功后才复制落库）；
        已存条目沿用既有"立即复制并写 image_path"逻辑。
        """
        # 2026-09-15（批次 6-3b）：统一守卫（原先只判浏览态、无 _lock_on 判断；仅靠按钮置灰）
        if not self._assert_unlocked("选择图片"):
            return
        if self._browse_mode:  # 浏览态禁止改动
            return
        path = filedialog.askopenfilename(
            title="选择图片", parent=self,
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                       ("所有文件", "*.*")])
        if not path:
            return
        if self._detail_entry_id is None:
            self._staged_cover = path
            self._mark_dirty()
            self._render_image_preview()
            return
        try:
            ext = os.path.splitext(path)[1].lower() or ".png"
            img_dir = os.path.join(config.data_dir(), config.IMAGES_DIR_NAME)
            os.makedirs(img_dir, exist_ok=True)
            dest = os.path.join(img_dir, f"entry_{self._detail_entry_id}{ext}")
            shutil.copyfile(path, dest)
            rel = os.path.join(config.IMAGES_DIR_NAME, f"entry_{self._detail_entry_id}{ext}")
            # 2026-08-18（P2-1 修复）：替换图片时删除旧图片文件，避免换扩展名后旧文件残留
            old = (self.db.get_entry(self._detail_entry_id) or {}).get("image_path") or ""
            if old and old != rel:
                try:
                    full = os.path.join(config.data_dir(), old)
                    if os.path.isfile(full):
                        os.remove(full)
                except OSError:
                    pass
            self.db.set_entry_image(self._detail_entry_id, rel)
            self._render_image_preview()
            self.toast("✅ 图片已关联")
        except Exception as exc:
            self.toast(f"图片关联失败：{exc}", color=_C_DANGER)

    def _remove_image(self) -> None:
        """移除封面图片（新建态只清除暂存；已存条目删除文件并清空 image_path）"""
        # 2026-09-15（批次 6-3b）：统一守卫（原先只判浏览态、无 _lock_on 判断；仅靠按钮置灰）
        if not self._assert_unlocked("移除图片"):
            return
        if self._browse_mode:  # 浏览态禁止改动
            return
        if self._detail_entry_id is None:
            self._staged_cover = None
            self._mark_dirty()
            self._render_image_preview()
            return
        e = self.db.get_entry(self._detail_entry_id)
        if e and e.get("image_path"):
            try:
                full = os.path.join(config.data_dir(), e["image_path"])
                if os.path.isfile(full):
                    os.remove(full)
            except OSError:
                pass
            self.db.set_entry_image(self._detail_entry_id, "")
            self._render_image_preview()
            self.toast("图片已移除")

    # ------------------------------------------------------------------ #
    # 条目操作：复制 / 收藏 / 移动 / 删除
    # ------------------------------------------------------------------ #
    def _copy_entry(self, entry_id: int, mode: int) -> None:
        e = self.db.get_entry(entry_id)
        if not e:
            return
        if mode == _COPY_ALL:
            text = f"{e['prompt_cn']}\n\n{e['prompt_en']}".strip()
        elif mode == _COPY_CN:
            text = e["prompt_cn"].strip()
        else:
            text = e["prompt_en"].strip()
        if not text:
            self.toast("内容为空，未复制")
            return
        try:
            pyperclip.copy(text)
            self.toast("✅ 已复制")
        except Exception:
            self.toast("复制失败，请检查剪贴板", color=_C_DANGER)

    def _toggle_favorite(self, entry_id: int) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止修改收藏状态
            return
        self.db.toggle_favorite(entry_id)
        self._restore_view()              # 刷新列表中的星标
        if self._detail_entry_id == entry_id:
            self._show_detail(self.db.get_entry(entry_id))

    def _entry_menu(self, event, entry_id: int) -> None:
        lock_state = "disabled" if self._lock_on else "normal"  # 2026-08-21（第005条）：锁定时仅"复制提示词"可用
        m = tk.Menu(self, tearoff=0)
        # 2026-09-07（阶段2）：条目"关联到/复制到/移动到"入口
        m.add_command(label="关联到…（多选）", state=lock_state,
                      command=lambda: self._link_entry(entry_id))
        m.add_command(label="复制到…（多选）", state=lock_state,
                      command=lambda: self._copy_entry_to_targets(entry_id))
        m.add_command(label="移动到…", state=lock_state,
                      command=lambda: self._move_entry(entry_id))
        ctx = self._current_cat_context()
        if ctx is not None and ctx in self.db.list_entry_locations(entry_id):
            m.add_command(label="解除在本分类的关联", state=lock_state,
                          command=lambda eid=entry_id, cid=ctx:
                          self._unlink_entry(eid, cid))
        m.add_separator()
        m.add_command(label="收藏 / 取消收藏", state=lock_state,
                      command=lambda: self._toggle_favorite(entry_id))
        m.add_command(label="复制提示词（全部）",
                      command=lambda: self._copy_entry(entry_id, _COPY_ALL))
        m.add_separator()
        m.add_command(label="删除", state=lock_state,
                      command=lambda: self._delete_entry(entry_id))
        m.tk_popup(event.x_root, event.y_root)

    # 2026-09-07 阶段2：条目 关联到 / 复制到 / 移动到 / 解除本分类关联
    def _current_cat_context(self):
        """当前条目列表所在的分类 id；未分类/搜索/常用等视图返回 None"""
        if self._view and self._view[0] == "cat":
            return self._cur_cat_id
        return None

    def _pick_entry_targets(self, mode: str, entry_id: int):
        """弹出目标分类选择器（link/copy 多选）；返回选中 id 列表，取消返回 None。

        已属该条目的位置自动带"（已在）"标记并在确定时剔除。
        """
        exclude = self.db.list_entry_locations(entry_id)
        dlg = MoveSelector(self, self.db, mode=mode, exclude=exclude)
        self.wait_window(dlg)
        if dlg.result != "ok":
            return None
        return dlg.selected_cat_ids

    def _link_entry(self, entry_id: int) -> None:
        if self._lock_on:
            return
        targets = self._pick_entry_targets("link", entry_id)
        if not targets:
            return
        before = set(self.db.list_entry_locations(entry_id))
        fresh = [c for c in targets if c not in before]
        if not fresh:
            self.toast("所选分类均为该条目已有位置，无需重复关联", color=_C_DANGER)
            return
        self.db.set_entry_locations(entry_id, add=fresh)
        self._restore_view()
        self.toast(f"✅ 已关联到 {len(fresh)} 个分类")

    def _copy_entry_to_targets(self, entry_id: int) -> None:
        if self._lock_on:
            return
        targets = self._pick_entry_targets("copy", entry_id)
        if not targets:
            return
        for cid in targets:
            self.db.copy_entry_to(entry_id, cid)
        self._restore_view()
        self.toast(f"✅ 已复制到 {len(targets)} 个分类（独立副本）")

    def _unlink_entry(self, entry_id: int, cat_id: int) -> None:
        if self._lock_on:
            return
        self.db.unlink_entry(entry_id, cat_id)
        self._restore_view()
        self.toast("✅ 已解除在本分类的关联")

    def _move_entry(self, entry_id: int) -> None:
        """移动到…：目标单选；可"移入未分类"。

        - 在有当前位置的分类列表里操作：默认"仅从当前位置移出并挂到目标，
          其它关联保留"；勾选"整体转移"则清空其它全部关联。
        - 在未分类/搜索/常用里操作（无当前位置）：按整体转移处理（旧语义）。
        """
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止条目移动
            return
        ctx = self._current_cat_context()
        locs = self.db.list_entry_locations(entry_id)
        has_ctx = ctx is not None and ctx in locs
        dlg = MoveSelector(self, self.db, mode="move")
        self.wait_window(dlg)
        if dlg.result == "cancel":
            return
        target = dlg.selected_cat_ids[0] if (dlg.result == "ok"
                                             and dlg.selected_cat_ids) else None
        overall = bool(getattr(dlg, "overall", False))
        if has_ctx and not overall:
            remove = [ctx]               # 仅当前位置移出
            add = [] if target is None else [target]
        else:
            remove = list(locs)          # 整体转移（清空其它位置）
            add = [] if target is None else [target]
        if not remove and not add:
            return
        self.db.set_entry_locations(entry_id, remove=remove, add=add)
        self._restore_view()
        self.toast("✅ 已移动")

    @staticmethod
    def _ref_tip(report: dict, subject: str = "它") -> str:
        """把"被引用统计"拼成删除确认弹窗里的一行提示（2026-09-13，3-c）。

        无引用返回空串；本方法只读统计、**不阻断删除**（异常一律视为无引用）。
        """
        try:
            n = int(report.get("total") or 0)
            fields = report.get("fields") or []
        except Exception:
            return ""
        if not n:
            return ""
        names = "、".join(f"{f['display_name']}（{f['count']} 处）" for f in fields[:4])
        more = "" if len(fields) <= 4 else f" 等 {len(fields)} 个字段"
        return (f"\n\n⚠ 提示：{subject}已被自定义字段引用 {n} 处（{names}{more}）；"
                "删除后这些引用会显示为「该项已经被删除（原：…）」，"
                "可在对应条目中移除或重新指定。")

    def _delete_entry(self, entry_id: int) -> None:
        if self._lock_on:
            return
        e = self.db.get_entry(entry_id)
        if not e:
            return
        # 2026-09-07（阶段3）：多位置条目的删除会从全部位置消失，明确提示影响范围
        locs = self.db.list_entry_locations(entry_id)
        if len(locs) > 1:
            names = "、".join(self._cat_label(c) for c in locs[:6])
            more = "" if len(locs) <= 6 else f" 等 {len(locs)} 处"
            scope = (f"\n\n该条目当前同时存在于 {len(locs)} 个位置（{names}{more}），"
                     "删除后将在全部位置消失。")
        else:
            scope = ""
        # 2026-09-13（3-c）：条目被"条目型"字段引用时提示（不阻断删除）
        tip = self._ref_tip(self.db.refs_using_entry(entry_id), f"条目【{e['name']}】")
        # 2026-09-07（第2条改进）：删除＝移入回收站，可随时从"删除历史/回收站"恢复
        if not messagebox.askyesno("删除确认",
                                   f"⚠️ 确定要删除【{e['name']}】吗？{scope}{tip}"):
            return
        if not messagebox.askyesno("再次确认",
                                   "确定移入回收站？\n（可从详情区下方“♻ 删除历史/回收站”恢复）"):
            return
        self.db.trash_entry(entry_id, reason="手动删除")
        self._restore_view()
        if self._detail_entry_id == entry_id:
            self._show_detail(None)
        self.toast("已删除（可到回收站恢复）")

    # ------------------------------------------------------------------ #
    # 锁定开关
    # ------------------------------------------------------------------ #
    def _assert_unlocked(self, action: str = "") -> bool:
        """统一锁定守卫（2026-09-15 批次 6-3，用户选定"全覆盖 + 拦截并 toast 提示"）。

        用法：所有**写操作入口**首行调用 `if not self._assert_unlocked("动作名"): return`。
          · 未锁定 → 返回 True（放行，零副作用）；
          · 已锁定 → **拦截**：弹 toast 提示并返回 False（调用方立即 return，不写任何数据）。
        action 为动作名（如"新增标签"），用于提示语；缺省用通用提示。
        """
        if not self._lock_on:
            return True
        self.toast(f"⚠ 已锁定，无法{action}" if action else "⚠ 已锁定，此操作已阻止",
                   color=_C_DANGER)
        return False

    def _toggle_lock(self) -> None:
        self._lock_on = not self._lock_on
        # 2026-08-22（第007条）：解锁时恢复记录的默认配色（customtkinter 6.0.0
        # 不接受 fg_color=None，会抛 ValueError 中断本方法导致按钮显示卡死）
        if self._lock_on:
            self.lock_btn.configure(text="🔓 已锁定", fg_color=_C_DANGER)
        else:
            self.lock_btn.configure(
                text="🔒 锁定",
                fg_color=self._lock_btn_default_fg,
                hover_color=self._lock_btn_default_hover)
        self._apply_lock_state(rebuild_nav=True)  # 2026-09-07：仅锁定切换时重建导航列
        self.toast("已开启全局锁定，删除功能已禁用" if self._lock_on else "已解除锁定")

    def _set_child_states(self, widget, state: str) -> None:
        """递归设置某容器下所有控件的 state（锁定状态切换用，2026-08-21 第005条新增）"""
        for child in widget.winfo_children():
            try:
                child.configure(state=state)
            except Exception:
                pass
            self._set_child_states(child, state)

    def _apply_tag_gov_lock(self) -> None:
        """按锁定态刷新标签页的**写操作按钮**（2026-09-15 批次 6-2，含用户选定"一并置灰"）。

        纳入范围（全部为写操作）：
          · 标签档 5 个：重命名 / 合并… / 删除 / 清理未使用 / 🤖 批量打标…
          · 热点词档 3 个：📋 文本导入… / 🔄 热点词更新… / 🗑 清空全部
          · 词表档 3 个：⬇ 导入（替换）… / ➕ 增量导入… / ↺ 恢复出厂…
            （2026-09-17 用户需求新增「➕ 增量导入…」，由 2 个 → 3 个）
        **不**纳入："⬆ 导出词表…"（只读）。
        用户选定"禁用（可见但置灰）"⇒ 锁定态 `state="disabled"`，解锁态 `"normal"`。
        幂等：页面重建后与锁定开关切换时均可重复调用；控件可能已销毁（页面重建），故全部容错。
        """
        _st = "disabled" if self._lock_on else "normal"
        for _b in list(getattr(self, "_tag_gov_btns", [])):
            try:
                if _b.winfo_exists():
                    _b.configure(state=_st)
            except Exception:
                pass

    def _apply_lock_state(self, rebuild_nav: bool = False) -> None:
        """锁定：只能查询和复制，禁止移动/删除/新增/重命名/导入/编辑保存等（2026-08-21 第005条扩展）。

        rebuild_nav（2026-09-07 第2条改进）：仅在锁定开关真正切换时才重建 4 个导航列；
        平时展示/切换条目也会调用本方法刷新详情控件状态，若每次都整列重建，
        会造成"悬浮逐级选择反应迟钝"与导航滚动位置被顶回顶部。
        """
        locked = self._lock_on
        state = "disabled" if locked else "normal"
        for attr in ("del_btn", "fav_btn", "move_btn", "link_btn", "copyto_btn",
                     "save_btn", "reset_btn",
                     "import_btn", "quick_add_btn"):
            w = getattr(self, attr, None)
            if w is not None and w.winfo_exists():
                w.configure(state=state)
        # 详情名称输入框 + 详情滚动区编辑控件（复制按钮不受影响）
        # 名称输入框已随表单放入详情区，lock 状态由 detail_scroll 子控件统一处理
        # （此处仅防御性兜底，widget 可能因重建已销毁）
        ne = getattr(self, "_name_entry", None)
        if ne is not None:
            try:
                if ne.winfo_exists():
                    ne.configure(state=state)
            except Exception:
                pass
        if hasattr(self, "detail_scroll") and self.detail_scroll.winfo_exists():
            self._set_child_states(self.detail_scroll, state)
        # 导入菜单项（导入=新增数据，锁定时禁用）
        if hasattr(self, "import_menu"):
            try:
                last = self.import_menu.index("end")
            except Exception:
                last = None
            if last is not None:
                for i in range(last + 1):
                    try:
                        self.import_menu.entryconfigure(i, state=state)
                    except tk.TclError:
                        pass  # 分隔符等不支持 state 的项跳过（2026-08-29 M5：菜单新增分隔符后修复）
        # 2026-09-07：锁定开关真正切换时才重建 4 个导航列（避免每次显示条目的重建开销）
        if rebuild_nav:
            self._refresh_projects()
            self._refresh_l0()
            self._refresh_l1()
            self._refresh_l2()
        # 2026-09-06：同步条目区"新增条目"按钮（随锁定/视图启用置灰）
        if self._add_entry_btn is not None and self._add_entry_btn.winfo_exists():
            self._add_entry_btn.configure(
                state="normal" if self._add_available() else "disabled")
        # 2026-09-06：新增条目态下 收藏/删除/移动/关联/复制 保持禁用（解锁后不误恢复）
        if self._adding_new and not locked:
            for w in (self.del_btn, self.fav_btn, self.move_btn, self.link_btn,
                      self.copyto_btn, self.copy_all_btn,
                      self.copy_cn_btn, self.copy_en_btn):
                try:
                    w.configure(state="disabled")
                except Exception:
                    pass
        # 2026-09-07（阶段2/第3条）：无当前条目时 删除/关联到/复制到/移动到 保持禁用
        # （删除按钮在详情区底部常驻栏，无条目时应为置灰而非"点了没反应"）
        # 2026-09-09（P2-9）：无条目时 收藏/复制全部/中文/英文/保存/重置 一并置灰，
        # 避免"看似可用实则空操作"。
        if not locked and not self._adding_new and self._detail_entry_id is None:
            for w in (self.del_btn, self.move_btn, self.link_btn, self.copyto_btn,
                      self.fav_btn, self.copy_all_btn, self.copy_cn_btn,
                      self.copy_en_btn, self.save_btn, self.reset_btn):
                try:
                    w.configure(state="disabled")
                except Exception:
                    pass
        # 2026-09-09（修正）：复制全部/中文/英文——只要有当前条目（编辑、浏览、锁定态都允许
        # 复制）就保持可用；此前只有"禁用侧"逻辑没有恢复侧，走过新增/无条目流程后按钮会一直
        # 灰显。这里每次状态刷新都统一按"有当前条目且非新增"来设置。
        copy_ok = self._detail_entry_id is not None and not self._adding_new
        for w in (self.copy_all_btn, self.copy_cn_btn, self.copy_en_btn):
            try:
                w.configure(state="normal" if copy_ok else "disabled")
            except Exception:
                pass
        # 2026-09-15（批次 6-2）：标签页治理按钮随锁定态置灰/恢复（可见但不可点）
        self._apply_tag_gov_lock()
        # 2026-09-09：锁定/无条目状态处理完后再应用"浏览只读"，避免互相覆盖
        self._apply_browse()

    # ------------------------------------------------------------------ #
    # 浏览 / 编辑 切换（2026-09-09：避免浏览与编辑混用时的误操作）
    # ------------------------------------------------------------------ #
    def _on_edit_mode_click(self) -> None:
        """「✏️ 编辑 / 👁 浏览」普通按钮的点击回调（2026-09-14 用户要求：改为普通切换按钮）。

        点一下切到"浏览"（按钮显示「👁 浏览」），再点一下切回"编辑"（按钮显示「✏️ 编辑」）；
        按钮文字始终表示**当前状态**。
        """
        self._on_edit_mode_change("✏️ 编辑" if self._browse_mode else "👁 浏览")

    def _sync_edit_mode_btn(self) -> None:
        """把「编辑/浏览」按钮文字同步为**当前状态**（2026-09-14 改普通按钮后新增，幂等）。"""
        w = getattr(self, "edit_mode_toggle", None)
        if w is None:
            return
        try:
            w.configure(text=("👁 浏览" if self._browse_mode else "✏️ 编辑"))
        except Exception:
            pass

    def _on_edit_mode_change(self, value: str) -> None:
        """编辑/浏览切换：浏览=文本只读（可选中复制）+ 禁用编辑类按钮。"""
        self._browse_mode = (value == "👁 浏览")
        self._sync_edit_mode_btn()      # 2026-09-14：按钮文字跟随当前状态
        if self._browse_mode:
            self._apply_browse()
            self.toast("已切换为浏览模式（详情内容只读，可选中复制）", color="#25639c")
        else:
            self._apply_lock_state()  # 恢复编辑可用态（按钮/文本框状态由锁定等逻辑统一管理）
            self._apply_browse()
            self.toast("已切换为编辑模式")

    def _apply_browse(self) -> None:
        """按当前"浏览/编辑"模式刷新详情编辑控件状态（可重复调用，幂等）。

        - 浏览模式（已选条目且非新增）：名称与全部文本框只读但可选中复制
          （_set_boxes_readonly 只做键盘屏蔽，不影响鼠标选区与 Ctrl+C）；
          保存/重置/删除/移动/关联到/复制到 按钮禁用，收藏与复制提示词不受影响。
        - 编辑/锁定/无条目：只读标志复位；按钮启用状态交由 _apply_lock_state 管理。
        """
        browsing = (self._browse_mode and not self._adding_new
                    and self._detail_entry_id is not None)
        ds = getattr(self, "detail_scroll", None)
        if ds is not None and ds.winfo_exists():
            _set_boxes_readonly(ds, browsing)
        if browsing:
            for w in (self.save_btn, self.reset_btn, self.del_btn,
                      self.move_btn, self.link_btn, self.copyto_btn):
                try:
                    w.configure(state="disabled")
                except Exception:
                    pass
        # 2026-09-13（1-C-2）：标签控件随"浏览/编辑"态启用或禁用（浏览态只读，防误改）
        # 2026-09-13（2-b）：封面按钮（选择/移除图片）与图集按钮同样**真正置灰**。
        # 2026-09-15（批次 6-1，用户要求"锁定态下图片/图集写操作**完全禁止**"）：并入**锁定态**判断——
        #   原先只看"浏览态"，而本方法在 `_apply_lock_state()` 末尾会被调用，于是**锁定 + 编辑模式**下
        #   会把刚被置灰的这些按钮**又设回 normal**（审计称之为"覆盖陷阱"）⇒ 图片/图集/标签输入仍可点。
        #   现改为：浏览态 **或 锁定态** 一律置灰（覆盖：图片/图集/标签输入与 chip ×、图集项、列表框按钮）。
        _st = "disabled" if (browsing or self._lock_on) else "normal"
        for _attr in ("_tag_entry", "_tag_add_btn", "_rec_btn", "_tag_pick_btn",
                      "_pick_img_btn", "_remove_img_btn",
                      "_gallery_add_btn", "_gallery_url_btn"):
            _w = getattr(self, _attr, None)
            if _w is not None and _w.winfo_exists():
                try:
                    _w.configure(state=_st)
                except Exception:
                    pass
        for _b in getattr(self, "_tag_chip_btns", []):
            try:
                if _b.winfo_exists():
                    _b.configure(state=_st)
            except Exception:
                pass
        for _b in getattr(self, "_gallery_btns", []):
            try:
                if _b.winfo_exists():
                    _b.configure(state=_st)
            except Exception:
                pass
        # 2026-09-13（3-a-2）：列表框字段的"选择…/清空"同样随浏览态置灰
        for _b in getattr(self, "_list_field_btns", []):
            try:
                if _b.winfo_exists():
                    _b.configure(state=_st)
            except Exception:
                pass
        # 2026-09-14（P6）：专用类型字段的「选择…/打开/试听」按钮同样随浏览态置灰
        for _b in getattr(self, "_extra_field_widgets", []):
            try:
                if _b.winfo_exists():
                    _b.configure(state=_st)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 根目录 / 分类 右键菜单
    # ------------------------------------------------------------------ #
    def _domain_menu(self, event, domain_id: int, name: str) -> None:
        if event is None:      # Tk 边界情况下可能零参数调用回调 → 直接忽略
            return
        lock_state = "disabled" if self._lock_on else "normal"  # 2026-08-21（第006条）：锁定时"复制到"同样禁用
        m = tk.Menu(self, tearoff=0)
        # 2026-08-21（第004条）：根目录"复制到/移动到"
        m.add_command(label="复制到…", state=lock_state,
                      command=lambda: self._copy_move_domain(domain_id, "copy"))
        m.add_command(label="移动到…", state=lock_state,
                      command=lambda: self._copy_move_domain(domain_id, "move"))
        m.add_command(label="移动到其他项目类别…", state=lock_state,  # 2026-08-29（M2）
                      command=lambda: self._move_domain_to_project(domain_id))
        m.add_separator()
        m.add_command(label="重命名", state=lock_state,
                      command=lambda: self._rename_domain(domain_id))
        m.add_command(label="删除", state=lock_state,
                      command=lambda: self._delete_domain(domain_id))
        # 2026-09-22（用户要求 3-2）：批量删除本根目录下的全部数据
        m.add_command(label="🧹 批量删除本分支全部数据…", state=lock_state,
                      command=lambda: self._batch_delete_branch("domain", domain_id))
        self._add_move_menu_items(m, "domain", domain_id, lock_state)  # 2026-09-11：上移/下移
        m.tk_popup(event.x_root, event.y_root)

    def _category_menu(self, event, cat_id: int, name: str) -> None:
        if event is None:      # Tk 边界情况下可能零参数调用回调 → 直接忽略
            return
        cat = self.db.get_category(cat_id)
        src_type = "l1" if (cat and cat["parent_id"] is None) else "l2"
        lock_state = "disabled" if self._lock_on else "normal"  # 2026-08-21（第006条）：锁定时"复制到"同样禁用
        m = tk.Menu(self, tearoff=0)
        # 2026-08-21（第004条）：一级/二级分类"复制到/移动到"
        m.add_command(label="复制到…", state=lock_state,
                      command=lambda: self._copy_move_category(src_type, cat_id, "copy"))
        m.add_command(label="移动到…", state=lock_state,
                      command=lambda: self._copy_move_category(src_type, cat_id, "move"))
        m.add_separator()
        m.add_command(label="新增子分类", state=lock_state,
                      command=lambda: self._add_subcategory(cat_id))
        m.add_command(label="重命名", state=lock_state,
                      command=lambda: self._rename_category(cat_id))
        m.add_command(label="删除", state=lock_state,
                      command=lambda: self._delete_category(cat_id))
        # 2026-09-22（用户要求 3-2）：批量删除本分类（含全部子分类）下的全部数据
        m.add_command(label="🧹 批量删除本分支全部数据…", state=lock_state,
                      command=lambda: self._batch_delete_branch("cat", cat_id))
        # 2026-09-11（用户要求 2）：一级/二级分类列内上移/下移
        self._add_move_menu_items(m, src_type, cat_id, lock_state)
        m.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------ #
    # 复制到 / 移动到（2026-08-21 第004条新增）
    # ------------------------------------------------------------------ #
    def _copy_move_domain(self, domain_id: int, action: str) -> None:
        """根目录"复制到/移动到"：目标为另一根目录下（作其一级分类）"""
        if self._lock_on:  # 2026-08-21（第006条修正）：锁定时仅可查询和复制详情内容，"复制到"同样禁止
            return
        d = self.db.get_domain(domain_id)
        if not d:
            return
        dlg = CopyMoveDialog(self, self.db, "domain", domain_id, d["name"], action)
        self.wait_window(dlg)
        if dlg.result != "ok":
            return
        try:
            if dlg.target_kind == "domain_to_project":  # 2026-08-29（M3）：根目录 → 项目类别
                if action == "copy":
                    new_name = self.db.unique_domain_name(d["name"])
                    self.db.copy_domain_to_project(domain_id, dlg.target_id, new_name)
                else:
                    self.db.move_domain_to_project(domain_id, dlg.target_id)
            elif action == "copy":
                self.db.copy_domain_to_domain(domain_id, dlg.target_id)
            else:
                self.db.move_domain_to_domain(domain_id, dlg.target_id)
        except ValueError as e:
            messagebox.showwarning("无法操作", str(e), parent=self)
            return
        self._after_copy_move()
        self.toast("✅ 已复制" if action == "copy" else "✅ 已移动")

    def _copy_move_category(self, src_type: str, cat_id: int, action: str) -> None:
        """一级/二级分类"复制到/移动到"（目标：新建根目录项/某根目录下/某一级分类下）"""
        if self._lock_on:  # 2026-08-21（第006条修正）：锁定时仅可查询和复制详情内容，"复制到"同样禁止
            return
        cat = self.db.get_category(cat_id)
        if not cat:
            return
        dlg = CopyMoveDialog(self, self.db, src_type, cat_id, cat["name"], action,
                             from_domain_id=self._cur_domain_id)
        self.wait_window(dlg)
        if dlg.result != "ok":
            return
        try:
            if dlg.target_kind == "l1_to_domain":
                if action == "copy":
                    self.db.copy_l1_to_domain(cat_id, dlg.target_id, dlg.new_name)
                else:
                    self.db.move_l1_to_domain(cat_id, self._cur_domain_id,
                                              dlg.target_id, dlg.new_name)
            elif dlg.target_kind == "l1_to_l2":
                if action == "copy":
                    self.db.copy_l1_to_l2(cat_id, dlg.target_id)
                else:
                    self.db.move_l1_to_l2(cat_id, dlg.target_id)
            elif dlg.target_kind == "l2_to_domain":
                if action == "copy":
                    self.db.copy_l2_to_domain(cat_id, dlg.target_id, dlg.new_name)
                else:
                    self.db.move_l2_to_domain(cat_id, dlg.target_id, dlg.new_name)
            elif dlg.target_kind == "l2_to_l2":
                if action == "copy":
                    self.db.copy_l2_to_l2(cat_id, dlg.target_id)
                else:
                    self.db.move_l2_to_l2(cat_id, dlg.target_id)
        except ValueError as e:
            messagebox.showwarning("无法操作", str(e), parent=self)
            return
        self._after_copy_move()
        self.toast("✅ 已复制" if action == "copy" else "✅ 已移动")

    def _after_copy_move(self) -> None:
        """复制/移动完成后刷新导航与详情（结构性变化，静默重建并复位，避免未保存确认中断）"""
        self.refresh_domains(silent=True)
        self._show_detail(None)
        self._cur_cat_id = None
        if self._cur_domain_id:
            self._view = ("domain", self._cur_domain_id)

    def _add_domain(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止新增
            return
        name = simpledialog.askstring("新增根目录", "请输入根目录名称：", parent=self)
        if not (name and name.strip()):
            return
        # 决策 5：新增根目录弹窗选择所属项目类别；未选择则默认归入"未明确分类"
        r = self._choose_project("选择项目类别", f"【{name.strip()}】归属项目类别：")
        if r is None:
            project_id = self.db.ensure_project(config.PROJECT_FALLBACK)
        else:
            project_id = r[1]
        self.db.add_domain(name.strip(), project_id=project_id)
        self.refresh_domains()

    def _rename_domain(self, domain_id: int) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止重命名
            return
        cur = self.db.get_domain(domain_id)
        name = simpledialog.askstring("重命名根目录", "请输入新名称：",
                                      initialvalue=cur["name"], parent=self)
        if name and name.strip():
            self.db.rename_domain(domain_id, name.strip())
            self.refresh_domains()

    def _delete_domain(self, domain_id: int) -> None:
        if self._lock_on:
            return
        d = self.db.get_domain(domain_id)
        stat = self.db.count_domain_items(domain_id)
        # 2026-09-13（3-c）：根目录被"目录层级型"字段引用时提示（不阻断删除）
        tip = self._ref_tip(self.db.refs_using_domain(domain_id), f"根目录【{d['name']}】")
        if not messagebox.askyesno(
                "删除确认",
                f"⚠️ 确定要删除根目录【{d['name']}】吗？\n其下关联 {stat['categories']} 个分类、"
                f"{stat['entries']} 个条目。\n\n删除仅解除关联，分类与条目数据将保留"
                f"（其他关联该分类的根目录仍可正常访问）。{tip}"):
            return
        if not messagebox.askyesno("再次确认", "🚨 删除后不可恢复！请再次点击确定。"):
            return
        self.db.delete_domain(domain_id)
        self.refresh_domains()

    def _add_subcategory(self, parent_id: int) -> None:
        """右键"新增子分类"：在当前分类下新建子分类（分类为全局共享树，无需领域）"""
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止新增
            return
        name = simpledialog.askstring("新增子分类", "请输入分类名称：", parent=self)
        if not (name and name.strip()):
            return
        self.db.add_category(name.strip(), parent_id=parent_id)
        self._refresh_l2()

    def _rename_category(self, cat_id: int) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止重命名
            return
        cur = self.db.get_category(cat_id)
        name = simpledialog.askstring("重命名分类", "请输入新名称：",
                                      initialvalue=cur["name"], parent=self)
        if name and name.strip():
            self.db.rename_category(cat_id, name.strip())
            if cur["parent_id"] is None:
                self._refresh_l1()
            else:
                self._refresh_l2()

    def _delete_category(self, cat_id: int) -> None:
        """分类删除保护（2026-09-07 阶段3 接入 UI）：
        - 有子分类：不允许直接删除，可进入"级联删除"（需输入确认短语）；
        - 无子分类：安全删除——仅删除该分类，其直挂条目解除本位置
          （无其它位置的条目转「未分类」，条目数据保留）。
        """
        if self._lock_on:
            return
        cat = self.db.get_category(cat_id)
        if not cat:
            return
        # 2026-09-13（3-c）：分类（含子分类）被"目录层级型"字段引用时提示（不阻断删除）
        tip = self._ref_tip(self.db.refs_using_category(cat_id), f"分类【{cat['name']}】及子分类")
        if self.db.category_has_children(cat_id):
            stat = self.db.count_descendants(cat_id)
            if not messagebox.askyesno(
                    "删除分类",
                    f"【{cat['name']}】下仍有 {stat['categories']} 个子分类，不能直接删除。\n\n"
                    "请先处理下级分类；若需连同其全部下级与相关条目一并删除，"
                    f"请点【是】进入级联删除（需输入确认短语）。{tip}"):
                return
            self._cascade_delete_category(cat_id, cat)
            return
        direct = len(self.db.list_entries(cat_id))
        if not messagebox.askyesno(
                "删除确认",
                f"确定删除分类【{cat['name']}】？\n"
                f"其 {direct} 条本级条目仅解除在本分类的关联"
                f"（无其它位置的条目将转「未分类」，条目数据保留）。{tip}"):
            return
        self.db.delete_category_safe(cat_id)
        self.toast("✅ 分类已删除（直挂条目已解除/转未分类）")
        self._after_delete_category(cat)

    def _cascade_delete_category(self, cat_id: int, cat: dict) -> None:
        """级联删除整棵：分类结构永久删除；其中直挂条目先移入回收站（可恢复），
        输入确认短语 → 再次确认 → 执行。"""
        # 2026-09-15（批次 6-3b）：统一守卫（唯一调用点 _delete_category 已拦；此处防御性冗余）
        if not self._assert_unlocked("级联删除分类"):
            return
        stat = self.db.count_descendants(cat_id)
        phrase = self.db._CASCADE_PHRASE
        got = simpledialog.askstring(
            "级联删除确认",
            f"将删除：\n【{cat['name']}】及其全部下级分类（共 {stat['categories'] + 1} 个分类）。\n"
            f"其中直挂条目 {stat['entries']} 条将移入回收站（可在“删除历史/回收站”中恢复，"
            "图片文件保留至回收站彻底清除）。\n\n"
            f"请输入确认短语「{phrase}」以继续：",
            parent=self)
        if not got:
            return
        if got.strip() != phrase:
            messagebox.showwarning("已取消", "确认短语不正确，删除已取消。")
            return
        if not messagebox.askyesno(
                "最后确认",
                "🚨 再次确认：分类及其全部下级分类结构将【永久删除】；\n"
                "相关条目移入回收站（彻底清除前可恢复）。确定删除？"):
            return
        self.db.delete_category_cascade(cat_id, phrase)
        self.toast("✅ 已级联删除（条目已入回收站）")
        self._after_delete_category(cat, reset_view=True)

    # ------------------------------------------------------------------ #
    # 批量删除本分支全部数据（2026-09-22，用户要求 3-2）
    #   入口：左侧导航 项目类别 / 根目录 / 一级·二级分类 的右键菜单
    #   严格流程（顺序不可跳步）：
    #     ① 影响范围统计弹窗 → askyesno
    #     ② 手工输入确认短语（db._CASCADE_PHRASE）
    #     ③ 选择删除方式：软删除（入回收站，可恢复）/ 硬删除（彻底清除）
    #     ④ 仅"项目类别"层：手输项目类别名称 + 专用口令（更高级别确认）
    #     ⑤ 双重备份：全量库快照 + 待删分支 JSON（任一失败 → 立即中止，不写数据）
    #     ⑥ 执行删除 → 刷新左侧导航 / 条目区 / 详情区
    # ------------------------------------------------------------------ #
    def _batch_delete_branch(self, kind: str, scope_id: int) -> None:
        """按分支批量删除全部数据（kind：'project'/'domain'/'cat'）"""
        if not self._assert_unlocked("批量删除"):
            return
        st = self.db.count_scope_items(kind, scope_id)
        if not st.get("exists"):
            self.toast("⚠ 目标不存在，可能已被删除", color=_C_DANGER)
            return
        level = {"project": "项目类别", "domain": "根目录", "cat": "分类"}.get(kind, "分支")
        # ① 影响范围（先讲清楚"删什么 / 保留什么"，再让用户决定）
        msg = [f"⚠️ 将对{level}【{st['title']}】执行**批量删除**：", ""]
        msg.append(f"· 删除分类记录：{st['categories']} 个（含全部子分类）")
        if st["l1_shared"]:
            msg.append(f"· 解除共享分类关联：{st['l1_shared']} 个"
                       "（分类本身保留，其他根目录仍可正常访问）")
        if st["domains"]:
            msg.append(f"· 删除根目录记录：{st['domains']} 个")
        if st["projects"]:
            msg.append(f"· 删除项目类别记录：{st['projects']} 个")
        msg.append(f"· 处置条目本体：{st['entries_purge']} 条（其全部位置都在本分支内）")
        if st["entries_unlink"]:
            msg.append(f"· 仅解除位置关联：{st['entries_unlink']} 条"
                       "（其他分支仍有位置，条目本体保留）")
        msg += ["", "确定进入下一步？"]
        if not messagebox.askyesno("批量删除 · 影响范围", "\n".join(msg), parent=self):
            return
        # ② 手工输入确认短语
        phrase = self.db._CASCADE_PHRASE
        got = simpledialog.askstring(
            "批量删除 · 确认短语",
            f"请输入确认短语「{phrase}」以继续：", parent=self)
        if got is None:
            return
        if got.strip() != phrase:
            messagebox.showwarning("已取消", "确认短语不正确，批量删除已取消。", parent=self)
            return
        # ③ 删除方式（软删除 / 硬删除）
        r = messagebox.askyesnocancel(
            "批量删除 · 选择删除方式",
            "请选择本次删除方式：\n\n"
            "【是】软删除：条目移入回收站，可从「删除历史 / 回收站」恢复（推荐）\n"
            "【否】硬删除：条目彻底删除，无法从回收站恢复（只能靠备份 / 导出文档恢复）\n"
            "【取消】放弃本次删除",
            parent=self)
        if r is None:
            self.toast("ℹ 已取消批量删除")
            return
        mode = "soft" if r else "hard"
        # ④ 项目类别层：更高级别确认（名称 + 专用口令，用户决策 3）
        passphrase = None
        if kind == "project":
            nm = simpledialog.askstring(
                "批量删除 · 高等级确认",
                f"这是**最高风险**操作（相当于删除整库的一个子集）。\n"
                f"请输入该项目类别的完整名称「{st['title']}」以确认：", parent=self)
            if (nm or "").strip() != st["title"]:
                messagebox.showwarning("已取消", "项目类别名称不匹配，批量删除已取消。", parent=self)
                return
            passphrase = simpledialog.askstring(
                "批量删除 · 专用口令", "请输入专用口令：", parent=self)
            if (passphrase or "").strip() != self.db._BATCH_DELETE_PASSPHRASE:
                messagebox.showwarning("已取消", "口令不正确，批量删除已取消。", parent=self)
                return
        # ⑤ 双重备份（失败即中止，绝不带病删除）
        if not self._backup_before_batch_delete(kind, scope_id, st["title"]):
            return
        # ⑥ 执行
        try:
            if kind == "project":
                self.db.delete_project_cascade(scope_id, phrase, passphrase, mode=mode)
            elif kind == "domain":
                self.db.delete_domain_cascade(scope_id, phrase, mode=mode)
            else:
                self.db.delete_scope_cascade("cat", scope_id, phrase, mode=mode)
        except Exception as exc:
            messagebox.showerror("批量删除失败", f"删除过程中出错：\n{exc}", parent=self)
            return
        self._after_batch_delete(kind, scope_id)
        self.toast("✅ 批量删除完成（删除前已双重备份：全量库 + 分支）")

    def _backup_before_batch_delete(self, kind: str, scope_id: int, title: str) -> bool:
        """批量删除前的**双重备份**（2026-09-22 用户要求 3-2）。

        第 1 份：全量库快照（backup.predel_snapshot，独立前缀 prompts_predel_*.db）
        第 2 份：待删分支全部数据（json_io.export_json，按当前层级范围导出为 JSON）
        两者同放现有备份目录 data/backup/；任一失败 → 返回 False（调用方立即中止删除）。
        """
        snap = backup_mod.predel_snapshot()
        if not snap.get("ok"):
            messagebox.showerror(
                "备份失败，已中止删除",
                f"删除前【全量库备份】失败：\n{snap.get('error')}\n\n"
                "为避免数据无法恢复，本次删除已中止。", parent=self)
            return False
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(title)).strip(" ._") or "分支"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(os.path.dirname(snap["path"]),
                            f"prompts_branch_{safe}_{stamp}.json")
        try:
            n = json_io.export_json(
                self.db, dest, **self._export_scope_kwargs({"kind": kind, "id": scope_id}))
        except Exception as exc:
            messagebox.showerror(
                "备份失败，已中止删除",
                f"删除前【分支数据备份】失败：\n{exc}\n\n"
                "为避免数据无法恢复，本次删除已中止。", parent=self)
            return False
        self.toast(f"✅ 删除前双重备份完成（全量库 + 分支 {n} 条）")
        return True

    def _after_batch_delete(self, kind: str, scope_id: int) -> None:
        """批量删除后：清理已失效的选中态 → 重建左侧导航 → 清空条目区 / 详情区"""
        if kind == "project" and self._cur_project_id == scope_id:
            self._cur_project_id = None
            self._cur_domain_id = None
            self._cur_cat_id = None
            self._view = None
        elif kind == "domain" and self._cur_domain_id == scope_id:
            self._cur_domain_id = None
            self._cur_cat_id = None
            self._view = ("project", self._cur_project_id)
        elif kind == "cat":
            self._cur_cat_id = None
            if self._cur_domain_id:
                self._view = ("domain", self._cur_domain_id)
        # 当前浏览的分类可能被"连带删除" → 统一收敛为"未选中分类"
        if self._cur_cat_id is not None and self.db.get_category(self._cur_cat_id) is None:
            self._cur_cat_id = None
        self._refresh_projects()
        self._refresh_l0()
        self._refresh_l1()
        self._refresh_l2()
        self._render_entries([], "条目")
        # 详情区若正在展示已被删除的条目 → 清空，避免展示"已不存在"的数据
        if self._detail_entry_id is not None and self.db.get_entry(self._detail_entry_id) is None:
            self._show_detail(None)

    def _after_delete_category(self, cat: dict, reset_view: bool = False) -> None:
        """删除分类后刷新导航；若正在浏览被删分类（或级联删除了其子树）则回到根目录视图"""
        if cat["parent_id"] is None:
            self._refresh_l1()
        else:
            self._refresh_l2()
        if reset_view or self._cur_cat_id == cat["id"]:
            self._cur_cat_id = None
            if self._cur_domain_id:
                self._view = ("domain", self._cur_domain_id)
                self._clear_frame(self.l2_frame)
            self._render_entries([], "条目")

    # ------------------------------------------------------------------ #
    # 数据导入导出（阶段六）
    # ------------------------------------------------------------------ #
    def _current_export_scope(self) -> dict:
        """当前左侧导航选中项的**导出/删除范围**（子树根）。

        2026-09-22（用户要求 3-1 前置改造）：原 `_current_subtree_cat_id()` 只返回
        `self._cur_cat_id`，导致在"项目类别""根目录"层级无法按分支导出
        （选中它们时 `_cur_cat_id` 为 None，界面提示"请先在左侧选中分类"）。
        现按左侧**实际选中层级**返回，供导出与批量删除共用：

          - `{"kind": "cat",     "id": 分类 id}`   ← 选中一级 / 二级分类
          - `{"kind": "domain",  "id": 根目录 id}` ← 选中根目录
          - `{"kind": "project", "id": 项目类别 id}` ← 选中项目类别
          - `{"kind": None,      "id": None}`       ← 其它视图（未分类/收藏/搜索/标签/无标签）

        说明：以 `self._view` 为准（它正是左侧高亮的那个层级）；无视图时按
        "分类 > 根目录" 回退，与改造前的行为保持一致。
        """
        view = self._view if isinstance(self._view, tuple) and self._view else None
        if view:
            kind = view[0]
            ref = view[1] if len(view) > 1 else None
            if kind in ("cat", "domain", "project") and ref:
                return {"kind": kind, "id": ref}
            return {"kind": None, "id": None}
        if self._cur_cat_id:
            return {"kind": "cat", "id": self._cur_cat_id}
        if self._cur_domain_id:
            return {"kind": "domain", "id": self._cur_domain_id}
        return {"kind": None, "id": None}

    def _export_scope_kwargs(self, scope: dict) -> dict:
        """把 `_current_export_scope()` 的结果转成导出函数的参数关键字"""
        if scope.get("kind") == "cat":
            return {"category_id": scope["id"]}
        if scope.get("kind") == "domain":
            return {"domain_id": scope["id"]}
        if scope.get("kind") == "project":
            return {"project_id": scope["id"]}
        return {}

    def _scope_default_filename(self, scope: dict, ext: str, plain_name: str) -> str:
        """按范围生成导出默认文件名（用户决策 1，方案 A）。

        2026-09-22：**项目类别 / 根目录**导出时用
        `prompts_backup_<项目名/根目录名>_<YYYYMMDD_HHMMSS>.<ext>`（便于按分支+时间区分备份）；
        其它情况（全库导出、分类导出）沿用原来的固定名 `plain_name`——不改动既有习惯。
        名称中的文件名非法字符统一替换为 `_`。
        """
        if scope.get("kind") not in ("project", "domain"):
            return plain_name
        title = json_io.scope_title(self.db, **self._export_scope_kwargs(scope)) or "分支"
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(title)).strip(" ._") or "分支"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"prompts_backup_{safe}_{stamp}.{ext}"

    def _open_import_wizard(self) -> None:
        """打开 T1 离线"网上资源结构化"向导（2026-09-13，第 4 期 4-c）"""
        if self._lock_on:      # 锁定时禁止导入（导入＝新增数据）
            return
        if not self._confirm_unsaved():
            return
        dlg = ImportWizardDialog(self, self.db)
        self.wait_window(dlg)
        if getattr(dlg, "imported", False):
            self.refresh_domains()
            self.toast("✅ 结构化资源已导入")

    def _import_json(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止导入（导入=新增数据）
            return
        path = filedialog.askopenfilename(title="选择 JSON 备份", parent=self,
                                          filetypes=[("JSON", "*.json")])
        if not path:
            return
        if not self._confirm_unsaved():
            return
        # 2026-09-08（V1.7.0）：含删除清单的"变更包"一律走导入向导，防止盲目导入删除
        try:
            summary = json_io.read_pack_summary(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        if summary.get("del_total") or summary.get("type") == "change":
            self._run_change_import(path, summary)
            return
        try:
            total = json_io.count_json_entries(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        # 2026-09-13：随包字段定义与本地不一致时，先让用户逐项选择（新增/覆盖/跳过）
        proceed, resolver = confirm_field_defs_file(self, self.db, path)
        if not proceed:
            self.toast("已取消导入（字段定义差异未确认）")
            return
        dlg = ProgressDialog(self, total=total, message="正在导入 JSON…")
        try:
            result = json_io.import_json(self.db, path, progress_cb=dlg.update_progress,
                                         field_defs_resolver=resolver)
            self.refresh_domains()
            self.toast(self._import_done_msg(result))
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        finally:
            dlg.finish()

    def _import_excel(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止导入（导入=新增数据）
            return
        path = filedialog.askopenfilename(title="选择 Excel 文件", parent=self,
                                          filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        if not self._confirm_unsaved():
            return
        # 2026-08-29（P2 修复）：大文件限制（≤20MB），避免内存风险
        try:
            if os.path.getsize(path) > 20 * 1024 * 1024:
                messagebox.showwarning(
                    "文件过大",
                    "所选 Excel 文件超过 20MB，请拆分后导入，或改用 JSON 导入。",
                    parent=self)
                return
        except OSError as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        try:
            total = excel_io.count_excel_rows(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        dlg = ProgressDialog(self, total=total, message="正在导入 Excel…")
        try:
            result = excel_io.import_excel(self.db, path, progress_cb=dlg.update_progress)
            self.refresh_domains()
            self.toast(self._import_done_msg(result))
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        finally:
            dlg.finish()

    @staticmethod
    def _import_done_msg(result: dict) -> str:
        """导入完成提示：新增条数 +（跳过重复条数）+（删除同步统计）。

        2026-08-18（P1-1）：新增"跳过重复"统计（详情内容去重）。
        2026-08-29（增量备份增强）：新增删除同步统计。
        """
        msg = f"✅ 导入完成：新增 {result.get('entries', 0)} 条"
        # 2026-09-17（FR-93，v6）：按稳定 ID 命中、**更新同一条目**的条数（不再重复新增）
        if result.get("updated"):
            msg += f"，更新同一条目 {result['updated']} 条"
        if result.get("skipped"):
            msg += f"，跳过重复 {result['skipped']} 条"
        rec = result.get("recovered", 0)
        if result.get("mode") == "reverse":
            msg = (f"✅ 逆向恢复完成：恢复已删除 {rec} 条"
                   f"（另新增/修改 {result.get('entries', 0)} 条）")
            if result.get("updated"):
                msg += f"，更新同一条目 {result['updated']} 条"
            if result.get("skipped"):
                msg += f"，跳过重复 {result['skipped']} 条"
        elif rec:
            msg += f"，其中逆向恢复 {rec} 条"
        d = result.get("deleted")
        if d and (d.get("entries") or d.get("categories") or d.get("domains")):
            msg += (f"，同步删除 条目{d.get('entries', 0)}/分类{d.get('categories', 0)}/"
                    f"根目录{d.get('domains', 0)}（条目已移入回收站可恢复）")
        return msg

    def _import_md(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止导入（导入=新增数据）
            return
        path = filedialog.askopenfilename(
            title="选择 Markdown 手册", parent=self,
            filetypes=[("Markdown", "*.md *.markdown"), ("所有文件", "*.*")])
        if not path:
            return
        if not self._confirm_unsaved():
            return
        domain = simpledialog.askstring("导入 MD", "请输入目标根目录名称（不存在将自动创建）：",
                                        initialvalue="视觉风格分类", parent=self)  # 2026-08-18（P1-1）：默认目标由"视频"改为"视觉风格分类"
        if not domain or not domain.strip():
            return
        dlg = None  # 2026-08-29（B1 修复）：提前初始化，避免 parse 异常时 finally 触发 NameError
        try:
            manual = md_parser.parse_file(path)
            total = manual.count_entries()
            if total == 0:
                messagebox.showinfo("导入结果",
                                    "未解析到可导入的条目（请确认文件符合手册格式）。",
                                    parent=self)
                return
            dlg = ProgressDialog(self, total=total, message="正在导入 Markdown…")
            result = md_parser.import_manual(self.db, manual, domain.strip(),
                                             progress_cb=dlg.update_progress)
            self.refresh_domains()
            self.toast(f"✅ 导入完成：{result['entries']} 条")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        finally:
            if dlg is not None:  # 2026-08-29（B1 修复）：dlg 可能未创建
                dlg.finish()

    # ------------------------------------------------------------------ #
    # 变更包：导入向导 / 导出浏览（2026-08-29 M4；2026-09-08 V1.7.0 更名+安全导入）
    # ------------------------------------------------------------------ #
    def _open_migrate_wizard(self) -> None:
        """打开老版本数据迁移向导（未归属根目录 → 项目类别）"""
        # 2026-09-15（批次 6-3b，用户选定"仅入口拦截"）：统一守卫（本入口原无任何拦截）
        if not self._assert_unlocked("打开数据迁移向导"):
            return
        from .migrate_dialog import MigrateDialog
        dlg = MigrateDialog(self, self.db)
        self.wait_window(dlg)
        if dlg.result == "ok":
            self.refresh_domains()
            self.toast("✅ 迁移完成")
        else:
            self.db.set_meta(config.META_MIGRATE_WIZARD_DISMISSED, "1")
            self.refresh_domains(silent=True)

    def _import_change_pack(self) -> None:
        """导入变更包（2026-09-08 V1.7.0）：先预览摘要与执行范围，再执行。"""
        if self._lock_on:
            return
        path = filedialog.askopenfilename(title="选择变更包 JSON 文件", parent=self,
                                          filetypes=[("变更包/JSON", "*.json")])
        if not path:
            return
        if not self._confirm_unsaved():
            return
        try:
            summary = json_io.read_pack_summary(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        self._run_change_import(path, summary)

    def _run_change_import(self, path: str, summary: dict) -> None:
        """变更包导入向导主流程：预览→选择范围→确认→自动快照→导入。"""
        # 2026-09-15（批次 6-3b）：统一守卫（唯一调用点 _import_change_pack 已拦；防御性冗余）
        if not self._assert_unlocked("导入变更包"):
            return
        last_sync = self.db.get_meta(config.META_INCR_LAST_SYNC) or ""
        day = summary.get("day") or ""
        behind = bool(last_sync and day and last_sync[:10] < day)
        dlg = ChangeImportDialog(self, summary, behind)
        self.wait_window(dlg)
        if dlg.result is None:
            return  # 用户取消
        apply_additions, del_mode = dlg.result

        # 2026-09-09（审核 P1-5）：正常应用删除且含分类删除时，先披露目标端子树实际影响
        if del_mode == "apply" and summary.get("del_categories"):
            if not self._disclose_sync_delete_impact(path):
                self.toast("已取消导入", color=_C_DANGER)
                return

        # 2026-09-14（审核修复 P1-C）：变更包现已随包携带 field_defs，
        # 与"导入 JSON 备份"一致地先做**字段定义差异逐项确认**（无差异则不打扰）。
        proceed, resolver = confirm_field_defs_file(self, self.db, path)
        if not proceed:
            self.toast("已取消导入（字段定义差异未确认）", color=_C_DANGER)
            return

        # 导入前自动快照（可回滚；独立前缀 prompts_preimport_*，不参与自动清理）
        snap = backup_mod.preimport_snapshot(self.db.db_path)
        if not snap.get("ok"):
            messagebox.showwarning(
                "导入中止", f"无法生成导入前快照，已取消本次导入：{snap.get('error')}",
                parent=self)
            return

        del_units = summary.get("del_total", 0) if del_mode == "apply" else 0
        rec_units = summary.get("deleted_snapshots", 0) if del_mode == "reverse" else 0
        total = max(int(summary.get("add_entries", 0))
                    + max(int(del_units), int(rec_units)), 1)
        tip = ("（删除将先移入回收站）" if del_mode == "apply"
               else "（删除数据将反向重新导入）" if del_mode == "reverse" else "")
        pd = ProgressDialog(self, total=total,
                            message="正在导入变更包…" + tip)
        try:
            result = json_io.import_json(
                self.db, path,
                progress_cb=pd.update_progress,
                deletion_mode=del_mode,
                apply_additions=apply_additions,
                field_defs_resolver=resolver)
            self.refresh_domains()
            self.toast(self._import_done_msg(result))
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        finally:
            pd.finish()

    def _disclose_sync_delete_impact(self, path: str) -> bool:
        """同步删除分类前，披露目标端子树实际影响（2026-09-09 审核 P1-5）。

        对变更包 deleted_categories 中“本库确实存在”的分类，统计现存子树规模
        （子分类/相关条目），向用户披露（可能含目标机独有内容）后再决定是否继续。
        无法解析文件或不存在目标分类时直接放行。
        """
        try:
            import json as _json
            data = _json.load(open(path, encoding="utf-8"))
        except Exception:
            return True
        seen = set()
        sub_cats = 0
        sub_entries = 0
        for dc in data.get("deleted_categories", []):
            chain = dc.get("chain") or []
            if not chain:
                continue
            cid = self.db.find_category_by_chain(chain)
            if cid is None or cid in seen:
                continue
            st = self.db.count_descendants(cid)
            # count_descendants：categories=子分类数，entries=子树内直挂条目数；
            # 父子链可能重叠统计，故文案注明“最大影响面（约）”
            sub_cats += st.get("categories", 0)
            sub_entries += st.get("entries", 0)
            seen.add(cid)
        if not (sub_cats or sub_entries):
            return True
        msg = ("同步删除分类将同时影响本库现存数据（最大影响面约）：\n"
               f"· 现存子分类 {sub_cats} 个\n"
               f"· 现存相关条目 {sub_entries} 条（将转入“未分类”保留）\n\n"
               "其中可能包含目标机独有、源机没有的子分类/内容。仍继续删除？")
        return messagebox.askyesno("删除范围披露", msg, parent=self)

    def _export_incremental_browse(self, fmt: str) -> None:
        """把选中的变更包 JSON 导出为 Excel/HTML 浏览文件（导入临时库后导出，不改主库）"""
        if self._lock_on:
            return
        src = filedialog.askopenfilename(
            title="选择变更包 JSON 文件", parent=self,
            filetypes=[("变更包/JSON", "*.json"), ("JSON", "*.json")])
        if not src:
            return
        if fmt == "excel":
            out = filedialog.asksaveasfilename(
                title="另存为 Excel", parent=self, defaultextension=".xlsx",
                initialfile="change_pack_export.xlsx",
                filetypes=[("Excel 工作簿", "*.xlsx")])
        else:
            out = filedialog.asksaveasfilename(
                title="另存为 HTML", parent=self, defaultextension=".html",
                initialfile="change_pack_export.html",
                filetypes=[("HTML 页面", "*.html")])
        if not out:
            return
        try:
            n = export_incremental_to(self.db, src, out, fmt)
        except Exception as exc:
            messagebox.showerror("导出失败", f"无法导出：{exc}", parent=self)
            return
        self.toast(f"✅ 已导出 {n} 条供浏览")

    def _find_today_change_pack(self) -> Optional[str]:
        """定位当日变更包文件（新前缀优先，兼容旧"增量"前缀遗留）。无则返回 None"""
        code = get_computer_code(self.db)
        day = datetime.now().strftime("%Y-%m-%d")
        d = incr_dir(self.db)
        if not os.path.isdir(d):
            return None
        for prefix in (config.INCR_FILE_PREFIX, config.INCR_LEGACY_PREFIX):
            hits = [os.path.join(d, f) for f in os.listdir(d)
                    if f.startswith(f"{prefix}_{code}_{day}_") and f.endswith(".json")]
            if hits:
                return max(hits, key=os.path.getmtime)
        return None

    def _export_incremental_file_to(self) -> None:
        """定位/复制当日变更包文件到指定位置（导出前披露增删计数）。"""
        src = self._find_today_change_pack()
        if not src:
            messagebox.showinfo("提示", "今日暂无变更包文件"
                                       "（无当日变更数据时不会生成）。", parent=self)
            return
        # 导出前披露：从文件名计数 + 包内 summary 双向校验
        try:
            summary = json_io.read_pack_summary(src)
        except Exception:
            summary = {}
        add_n = summary.get("add_entries", 0)
        del_n = summary.get("del_entries", 0)
        desc = f"文件：{os.path.basename(src)}\n新增/修改条目：{add_n}  删除条目：{del_n}"
        if summary.get("del_total"):
            desc += (f"\n（另含删除 分类 {summary.get('del_categories', 0)} / "
                     f"根目录 {summary.get('del_domains', 0)}）")
        if add_n == 0 and del_n > 0:
            messagebox.showwarning("⚠ 负增量（纯删除包）",
                                   desc + "\n\n该变更包为当日仅删除数据的“负增量”包，"
                                          "请谨慎分发/导入！", parent=self)
        else:
            messagebox.showinfo("导出变更包", desc + "\n\n确定导出该文件吗？")
        out = filedialog.asksaveasfilename(
            title="导出当日变更包文件到", parent=self,
            initialfile=os.path.basename(src), defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not out:
            return
        try:
            shutil.copy2(src, out)
            self.toast("✅ 已导出当日变更包文件")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _compare_with_backup(self) -> None:
        """数据比对（只读诊断，V1.7.0）：与备份 *.db 对比当前库差异。"""
        if self._lock_on:
            return
        from .compare_dialog import CompareDialog
        CompareDialog(self, self.db)

    def _confirm_export_scope(self, scope: dict, fmt_label: str) -> bool:
        """导出前弹"范围确认"对话框（2026-09-22，用户要求 1）。

        对话框展示**整个库的结构树**，把用户已选分支及其全部下属**加重显示**，
        并给出本次将导出的条目数；未选中任何级别/选项时给出提醒且不允许确认。
        返回 True 表示用户已确认可以导出。
        """
        dlg = ExportScopeDialog(self, self.db, scope, fmt_label)
        self.wait_window(dlg)
        return bool(getattr(dlg, "result", False))

    def _export_json(self, current_only: bool = False) -> None:
        # 2026-09-22（用户要求 3-1）：current_only 时按左侧**实际选中层级**导出
        #   （项目类别 / 根目录 / 分类 三种分支均可），不再局限于分类。
        scope = self._current_export_scope() if current_only else {"kind": None, "id": None}
        # 2026-09-22（用户要求 1）：导出当前分类前先弹范围确认对话框
        if current_only and not self._confirm_export_scope(scope, "JSON"):
            return
        path = filedialog.asksaveasfilename(
            title="导出 JSON", parent=self,
            defaultextension=".json",
            initialfile=self._scope_default_filename(scope, "json", "prompts_backup.json"),
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            n = json_io.export_json(self.db, path, **self._export_scope_kwargs(scope))
            self.toast(f"✅ 已导出 {n} 条")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _export_excel(self, current_only: bool = False) -> None:
        # 2026-09-22（用户要求 3-1）：同 _export_json，支持项目类别 / 根目录分支导出
        scope = self._current_export_scope() if current_only else {"kind": None, "id": None}
        # 2026-09-22（用户要求 1）：导出当前分类前先弹范围确认对话框
        if current_only and not self._confirm_export_scope(scope, "Excel"):
            return
        path = filedialog.asksaveasfilename(
            title="导出 Excel", parent=self,
            defaultextension=".xlsx",
            initialfile=self._scope_default_filename(scope, "xlsx", "prompts.xlsx"),
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            n = excel_io.export_excel(self.db, path, **self._export_scope_kwargs(scope))
            self.toast(f"✅ 已导出 {n} 条")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _export_html(self, current_only: bool = False) -> None:
        # 2026-09-22（用户要求 3-1）：同 _export_json，支持项目类别 / 根目录分支导出
        scope = self._current_export_scope() if current_only else {"kind": None, "id": None}
        # 2026-09-22（用户要求 1）：导出当前分类前先弹范围确认对话框
        if current_only and not self._confirm_export_scope(scope, "HTML"):
            return
        path = filedialog.asksaveasfilename(
            title="导出 HTML", parent=self,
            defaultextension=".html",
            initialfile=self._scope_default_filename(scope, "html", "index.html"),
            filetypes=[("HTML", "*.html")])
        if not path:
            return
        try:
            n = html_export.export_html(self.db, path, **self._export_scope_kwargs(scope))
            self.toast(f"✅ 已导出 {n} 条")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    # ------------------------------------------------------------------ #
    # 其他：快捷新建、轻提示、热键
    # ------------------------------------------------------------------ #
    def _quick_add(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止快速新建（新增条目）
            return
        # 2026-09-15（批次 8-A，用户确认）：把主窗口"当前视图分类（无则最近一次分类）"作为
        #   快速新建窗口的**默认目标分类**，解决"打开窗口直接打字、未悬停分类 ⇒ 推荐不出标签"。
        QuickAddWindow(self, self.db,
                       default_cat_id=(self._cur_cat_id or self._last_cat_id))

    def toast(self, msg: str, color: str = _C_OK) -> None:
        if self._toast_label is not None:
            self._hide_toast(self._toast_label)
        lbl = ctk.CTkLabel(self, text=msg, fg_color=color, text_color="white",
                           corner_radius=8, font=("Microsoft YaHei", 13))
        lbl.place(relx=0.5, rely=0.93, anchor="center")
        self._toast_label = lbl
        self.after(1600, lambda: self._hide_toast(lbl))

    @staticmethod
    def _hide_toast(lbl) -> None:
        try:
            lbl.destroy()
        except Exception:
            pass

    def _on_escape(self, _event=None):
        self.withdraw()
        return "break"

    def report_callback_exception(self, exc, val, tb) -> None:
        """集中打印 Tk 回调里的未捕获异常（2026-09-15 审核 L-7 新增）。

        背景：项目原先未覆盖本钩子 → Tk 回调异常只写 stderr；打包为**无控制台 EXE** 时，
        用户看到的只是"点了按钮没反应"，排查无从下手。此处只打印（不弹窗、不改变行为）。
        """
        try:
            import traceback
            print("[回调异常] " + "".join(traceback.format_exception(exc, val, tb))[-1500:])
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 设置（2026-08-18："⚙ 设置"入口；持久化到数据库 meta 表）
    # ------------------------------------------------------------------ #
    def destroy(self) -> None:
        """销毁前确认未保存修改 + 保存设置 + 生成当日变更包（失败不阻塞退出）。

        2026-09-09（审核 P1-4 修复）：标题栏 X / 托盘"退出"（均最终走到 destroy）
        也会先弹出与切换时一致的"未保存修改"确认，避免静默丢弃正在编辑的内容。
        """
        if not getattr(self, "_closing_ok", False):
            try:
                if not self._confirm_unsaved():
                    return  # 用户取消关闭
            except Exception:
                pass  # 确认过程异常时不阻断关闭（避免退不出去）
            self._closing_ok = True
        # 2026-09-10（用户要求）：退出前取消未到点的搜索防抖定时器，避免回调打到已销毁窗口
        self._cancel_search_timer()
        try:
            self._save_settings()
        except Exception:
            pass
        try:  # 2026-08-29（M4）：每次关闭软件时执行每日变更包备份
            r = write_incremental(self.db)
            if not r["ok"] and r.get("error"):
                print(f"[变更包] 失败（不阻塞退出）：{r['error']}")
        except Exception as exc:
            print(f"[变更包] 失败（不阻塞退出）：{exc}")
        super().destroy()

    def _current_size(self) -> str:
        """当前窗口大小 "WxH"（去掉位置偏移）"""
        try:
            return self.geometry().split("+")[0]
        except Exception:
            return "1360x780"

    def _load_settings(self) -> None:
        """启动时应用持久化设置：记住窗口大小 / 视图模式 / 详情字段策略。"""
        self._remember_size = self.db.get_meta(config.META_REMEMBER_SIZE) != "0"
        vm = self.db.get_meta(config.META_VIEW_MODE)
        if vm in ("card", "list"):
            self._view_mode = vm
        dm = self.db.get_meta(config.META_DETAIL_MODE)
        if dm in (config.DETAIL_MODE_AUTO, config.DETAIL_MODE_FULL, config.DETAIL_MODE_COMPACT):
            self._detail_mode = dm
        # 2026-09-13（1-C-4b）：是否在条目处显示标签（默认关）
        self._show_tags_in_list = self.db.get_meta(config.META_SHOW_TAGS_IN_LIST) == "1"
        # 2026-09-14（阶段 3）：录入时"输入停止后自动推荐标签"（T2，默认关）
        self._auto_tag_suggest = self.db.get_meta(config.META_AUTO_TAG_SUGGEST) == "1"
        # 2026-09-15（批次 4）：界面外观（字体/字号/颜色）——载入配置；各渲染点用
        #   `ui_appearance.font(...)` / `ui_appearance.color(...)` **叠加**（未设置时返回原值）
        self._ui_appearance = ui_appearance.load(self.db)
        # 2026-09-16（批次 11-7，用户要求 3）：条目区排序方式（分类视图生效）
        _es = self.db.get_meta(config.META_ENTRY_SORT)
        if _es in ("updated", "created", "name"):
            self._entry_sort = _es
        if self._remember_size:
            size = self.db.get_meta(config.META_WINDOW_SIZE)
            if size and "x" in size:
                try:
                    self.geometry(size)
                except Exception:
                    pass

    def _save_settings(self) -> None:
        """保存当前设置（关闭/退出时调用）。"""
        if self._remember_size:
            self.db.set_meta(config.META_WINDOW_SIZE, self._current_size())
        self.db.set_meta(config.META_VIEW_MODE, self._view_mode)
        self.db.set_meta(config.META_DETAIL_MODE, self._detail_mode)

    def _open_settings(self) -> None:
        """打开设置对话框。"""
        SettingsDialog(self, self.db)

    def _open_field_manager(self) -> None:
        """打开"字段管理"对话框（2026-09-13，1-A-4：内置 10 个区块改名）。

        保存后刷新策略（避免影响其它功能）：
          - 当前处于"＋新增条目"态、或详情区有未保存修改时**不重建**界面
            （重建会清空用户已录入内容），仅提示"切换条目后生效"；
          - 否则重建当前详情区，使新名称立即生效。
        """
        # 2026-09-15（批次 6-3，用户选定"仅入口拦截 + toast 提示"）：锁定态拦截
        #   （本入口原无任何拦截：🔧 按钮不在置灰白名单、也不在详情滚动区内 ⇒ 锁定态仍可点）
        if not self._assert_unlocked("打开字段管理"):
            return
        dlg = FieldManagerDialog(self, self.db)
        self.wait_window(dlg)
        if not getattr(dlg, "changed", False):
            return
        if self._adding_new or self._detail_dirty:
            self.toast("字段设置已保存；当前有未保存内容，切换条目后生效")
            return
        if self._detail_entry_id is not None:
            e = self.db.get_entry(self._detail_entry_id)
            if e is not None:
                self._show_detail(e)
        self.toast("✅ 字段设置已保存")

    def apply_settings(self) -> None:
        """设置对话框确定后应用：视图模式 / 详情策略 / 窗口大小记忆。"""
        self._remember_size = self.db.get_meta(config.META_REMEMBER_SIZE) != "0"
        vm = self.db.get_meta(config.META_VIEW_MODE) or "card"
        self._view_mode = vm if vm in ("card", "list") else "card"
        dm = self.db.get_meta(config.META_DETAIL_MODE) or config.DETAIL_MODE_AUTO
        self._detail_mode = dm if dm in (config.DETAIL_MODE_AUTO, config.DETAIL_MODE_FULL,
                                         config.DETAIL_MODE_COMPACT) else config.DETAIL_MODE_AUTO
        # 2026-09-13（1-C-4b）：是否在条目处显示标签（默认关）
        self._show_tags_in_list = self.db.get_meta(config.META_SHOW_TAGS_IN_LIST) == "1"
        # 2026-09-14（阶段 3）：录入时"输入停止后自动推荐标签"（T2，默认关）
        self._auto_tag_suggest = self.db.get_meta(config.META_AUTO_TAG_SUGGEST) == "1"
        # 2026-09-15（批次 4）：界面外观（字体/字号/颜色）——重新载入；下方 `_restore_view()`
        #   会重建条目区、`_show_detail()` 会重建详情区 ⇒ 新的外观设置**立即生效**
        self._ui_appearance = ui_appearance.load(self.db)
        # 2026-09-16（批次 11-7，用户要求 3）：条目区排序方式（保存后立即生效）
        _es2 = self.db.get_meta(config.META_ENTRY_SORT)
        self._entry_sort = _es2 if _es2 in ("updated", "created", "name") else "updated"
        self._restore_view()  # 视图模式重建条目区
        # 2026-09-13（1-B）：切换显示策略后复位"会话覆盖"与折叠归属，使新策略立即生效
        self._detail_show_all = False
        self._detail_last_entry_id = None  # 2026-09-16：原 _detail_group_for
        if self._detail_entry_id is not None:
            self._show_detail(self.db.get_entry(self._detail_entry_id))  # 详情策略重建
        self.toast("✅ 设置已保存")

    # ------------------------------------------------------------------ #
    # 删除历史/回收站 与 新增历史（2026-09-07 第2/3条改进）
    # ------------------------------------------------------------------ #
    def _open_recycle(self) -> None:
        """打开"删除历史 / 回收站"：可恢复被删条目，或彻底删除/清空"""
        from .history_dialog import RecycleBinDialog
        dlg = RecycleBinDialog(self, self.db, locked=self._lock_on)
        self.wait_window(dlg)

    def _open_recent_adds(self) -> None:
        """打开"新增历史"：查看最近新增的条目"""
        from .history_dialog import RecentAdditionsDialog
        RecentAdditionsDialog(self, self.db)

    def show_and_focus_search(self) -> None:
        """全局热键/托盘回调：恢复窗口并聚焦搜索框"""
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.attributes("-topmost", False)
        self.after(60, self.search_entry.focus_set)
