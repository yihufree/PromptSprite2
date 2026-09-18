# -*- coding: utf-8 -*-
"""
settings_dialog.py - 用户设置对话框
创建日期：2026-08-18（"设置"入口新增）

设置项（持久化到数据库 meta 表）：
  1. 记住窗口大小（开关）：关闭/退出时保存窗口大小，下次启动恢复
  2. 默认视图模式：卡片 / 列表
  3. 详情字段显示策略：自动（按根目录）/ 全部显示 / 精简（隐藏 ③-⑦）

用法：SettingsDialog(master, db) —— 确定后写回 meta 并调用 master.apply_settings()。
"""
import os
import sys

import customtkinter as ctk

from .. import auto_words  # 2026-09-16（批次 12-3）：智能自动取词词库（数据层）
from .. import config
from .. import hotkey  # 2026-09-17（需求 S-2）：全局热键可配置（读取/规范化）
from .. import tagger  # 2026-09-14（阶段 0.5）：词表管理入口的状态摘要
from .. import tagger_engine  # 2026-09-16（批次 11-6）：推荐策略来源标识与标签文案
from .. import ui_appearance  # 2026-09-15（批次 4）：界面外观（字体/字号/颜色）数据层
from . import ui_common as _ui_common  # 2026-09-17（R-6）：CTk 私有 API 统一入口
from .ui_common import C_DANGER as _C_DANGER, C_TAG as _C_TAG, C_OK as _C_OK  # 2026-09-17（U-2）：主色常量


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.master = master

        self.title("⚙ 设置")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        # 读取当前设置
        self._remember = db.get_meta(config.META_REMEMBER_SIZE) != "0"
        self._view_mode = db.get_meta(config.META_VIEW_MODE) or "card"
        # 2026-09-16（批次 11-7，用户要求 3）：条目区排序方式（"updated"/"created"/"name"）
        _es = db.get_meta(config.META_ENTRY_SORT)
        self._entry_sort = _es if _es in ("updated", "created", "name") else "updated"
        # 2026-09-16（批次 12-2，用户要求 2）：取词是否用于"批量打标 / 离线打标"
        #   （默认关；与「批量智能自动打标」对话框共用同一 meta 键）
        self._fallback_batch = db.get_meta(config.META_FALLBACK_BATCH) == "1"
        self._detail_mode = db.get_meta(config.META_DETAIL_MODE) or config.DETAIL_MODE_AUTO
        self._computer_code = db.get_meta(config.META_COMPUTER_CODE) or ""
        # 2026-09-17（需求 S-2）：当前生效的全局热键（读 meta → 规范化 → 非法回退默认）
        self._hotkey = hotkey.current_hotkey(db)
        try:
            self._incr_keep_days = int(db.get_meta(config.META_INCR_KEEP_DAYS)
                                       or config.INCR_KEEP_DAYS)
        except (TypeError, ValueError):
            self._incr_keep_days = config.INCR_KEEP_DAYS
        try:
            self._backup_keep = int(db.get_meta(config.META_BACKUP_KEEP)
                                    or config.BACKUP_KEEP_COUNT)
        except (TypeError, ValueError):
            self._backup_keep = config.BACKUP_KEEP_COUNT
        self._snapshot = db.get_meta(config.META_CHANGE_PACK_SNAPSHOT) == "1"
        # 2026-09-13（1-C-4b）：是否在条目处显示标签（默认关）
        self._show_tags = db.get_meta(config.META_SHOW_TAGS_IN_LIST) == "1"
        # 2026-09-14（阶段 3）：录入时"输入停止后自动推荐标签"（T2，默认关）
        self._auto_tag = db.get_meta(config.META_AUTO_TAG_SUGGEST) == "1"
        # 2026-09-15（批次 4）：界面外观（字体/字号/颜色）——6 组，见 app/ui_appearance.py
        self._look = ui_appearance.load(db)
        self._look_widgets = {}
        self._look_color_map = {}
        self._look_default = "默认（不改）"
        # 2026-09-16（批次 11-6，用户确认问题 2）：标签推荐策略与顺序（4 个来源的开关 + 先后顺序）
        _pol = tagger.load_policy(db)
        self._policy_order = list(_pol["order"])
        self._policy_enabled = dict(_pol["enabled"])
        self._policy_rows = {}

        self._build()
        self._center()

    def _build(self) -> None:
        """分页：【界面】/【外观】/【数据与备份】/【字段】/【标签与词表】/【关于】。
        （2026-09-15 批次 4 新增【外观】页：字体/字号/颜色，6 个元素。）
        （2026-09-16 批次 11-2 新增【字段】页：把「字段管理」从「标签与词表」页移出——
          它是**数据结构（字段定义）管理**，与"标签 / 词表"无逻辑关系。）
        **只重排布局**——所有 meta 键、控件属性名与 `_apply()` 的读写逻辑**完全不变**。
        """
        pad = 16
        # 2026-09-16（批次 12-2 复核）：页高**回落**到 500——580 时实测整窗高 ≈ 770，
        #   在 1366×768（说明书写明的最低分辨率）上会被任务栏挡住底部按钮；
        #   故本轮把「推荐策略与顺序」改为**两列紧凑布局**、并把新增开关并入同一行，
        #   以"内容更少占用"换取更矮的窗口（内容仍全部可见，见 gui_self_test 的页高断言）。
        # 2026-09-16（批次 12-3）：再新增「智能自动取词」一行（启用 + 两个阈值 + 管理入口）
        #   ⇒ 页高 500 → 540；同时把设置窗口定位改为按**屏幕工作区**（见 _center），
        #     使 1366×768 上整窗仍在工作区内（gui_self_test 有「适配 768 屏」断言：
        #     页高 560 时整窗 req=737 会越出工作区，故取 540 → req≈717）。
        tab = ctk.CTkTabview(self, width=580, height=540)
        tab.pack(padx=12, pady=(12, 2), fill="both", expand=True)
        pg_ui = tab.add("界面")
        pg_look = tab.add("外观")            # 2026-09-15（批次 4）
        pg_data = tab.add("数据与备份")
        pg_field = tab.add("字段")           # 2026-09-16（批次 11-2）
        pg_tag = tab.add("标签与词表")
        pg_about = tab.add("关于")
        for _pg in (pg_ui, pg_look, pg_data, pg_field, pg_tag, pg_about):
            _pg.grid_columnconfigure(1, weight=1)

        def _lab(pg, text, row):
            ctk.CTkLabel(pg, text=text, font=("Microsoft YaHei", 13)
                         ).grid(row=row, column=0, padx=pad, pady=8, sticky="w")

        def _hint(pg, text, row, color="gray"):
            ctk.CTkLabel(pg, text=text, text_color=color, justify="left",
                         font=("Microsoft YaHei", 11)
                         ).grid(row=row, column=0, columnspan=2, padx=pad, pady=(0, 4), sticky="w")

        # ---------------- 第 1 页：界面 ----------------
        _lab(pg_ui, "记住窗口大小", 0)
        self.sw_size = ctk.CTkSwitch(pg_ui, text="关闭时保存，下次启动恢复",
                                     command=self._on_remember_toggle)
        self.sw_size.select() if self._remember else self.sw_size.deselect()
        self.sw_size.grid(row=0, column=1, padx=pad, pady=8, sticky="w")

        _lab(pg_ui, "默认视图模式", 1)
        self.seg_view = ctk.CTkSegmentedButton(pg_ui, values=["卡片", "列表"], width=180)
        self.seg_view.set("卡片" if self._view_mode == "card" else "列表")
        self.seg_view.grid(row=1, column=1, padx=pad, pady=8, sticky="w")

        _lab(pg_ui, "详情字段显示", 2)
        self.seg_detail = ctk.CTkSegmentedButton(
            pg_ui, values=["自动", "全部显示", "精简"], width=260)
        _map = {config.DETAIL_MODE_AUTO: "自动", config.DETAIL_MODE_FULL: "全部显示",
                config.DETAIL_MODE_COMPACT: "精简"}
        self.seg_detail.set(_map.get(self._detail_mode, "自动"))
        self.seg_detail.grid(row=2, column=1, padx=pad, pady=8, sticky="w")
        _hint(pg_ui, "自动：视频 / 图像 根目录显示全部，其余只显示 ②介绍；\n"
                     "全部显示：②~⑦ 全部显示并默认展开；精简：只显示 ②介绍", 3)

        _lab(pg_ui, "在条目处显示标签", 4)
        self.sw_show_tags = ctk.CTkSwitch(pg_ui, text="在条目名称下显示标签小字（默认关）")
        self.sw_show_tags.select() if self._show_tags else self.sw_show_tags.deselect()
        self.sw_show_tags.grid(row=4, column=1, padx=pad, pady=8, sticky="w")
        _hint(pg_ui, "开启后条目区不自动加宽；标签过长会截断，鼠标悬浮可查看完整内容。", 5)

        # 2026-09-14（阶段 3）：录入时自动推荐标签（T2 开关）
        _lab(pg_ui, "自动推荐标签", 6)
        self.sw_auto_tag = ctk.CTkSwitch(pg_ui, text="输入提示词后自动推荐标签（默认关）")
        self.sw_auto_tag.select() if self._auto_tag else self.sw_auto_tag.deselect()
        self.sw_auto_tag.grid(row=6, column=1, padx=pad, pady=8, sticky="w")
        _hint(pg_ui, "开启后：⑧中文 / ⑨英文 输入停止约 0.8 秒后自动重算推荐（结果直接加入标签，默认全选）。\n"
                     "「✨ 推荐标签」按钮、①名称框回车/失焦触发的推荐始终可用，无需开启此项。", 7)

        # 2026-09-16（批次 11-7，用户要求 3）：条目区排序方式（分类视图生效）
        _lab(pg_ui, "条目排序", 8)
        self.seg_sort = ctk.CTkSegmentedButton(
            pg_ui, values=["修改时间", "添加时间", "名称"], width=260)
        _smap = {"修改时间": "updated", "添加时间": "created", "名称": "name"}
        self.seg_sort.set({v: k for k, v in _smap.items()}.get(self._entry_sort, "修改时间"))
        self.seg_sort.grid(row=8, column=1, padx=pad, pady=8, sticky="w")
        _hint(pg_ui, "仅影响左侧按分类列出的条目顺序（搜索 / 常用 / 无类等其他视图不变）。\n"
                     "「添加时间」即条目的创建时间——由软件在新增时自动记录，详情区可见。", 9)

        # ---------------- 第 2 页：外观（2026-09-15，批次 4） ----------------
        #   6 个可设置元素（见 app/ui_appearance.py 的 GROUPS）。**每一项都有「默认（不改）」**，
        #   不设置时界面与现在**完全一致**（数据层用 None 表示"沿用代码里的原值"）。
        #   颜色用**预设色下拉**（避免手输非法值；数据层仍支持任意 #RRGGBB，日后可加输入框）。
        _COLOR_CHOICES = [("默认（不改）", None),
                          ("黑 #111111", "#111111"), ("深灰 #5b6b7c", "#5b6b7c"),
                          ("浅灰 #9aa4b1", "#9aa4b1"), ("蓝 #1d4e89", "#1d4e89"),
                          ("紫 #7a4fbf", "#7a4fbf"), ("绿 #2e8b57", "#2e8b57"),
                          ("橙 #c77700", "#c77700"), ("红 #d9534f", "#d9534f"),
                          ("白 #ffffff", "#ffffff")]
        self._look_color_map = {lbl: val for lbl, val in _COLOR_CHOICES}
        _color_labels = [lbl for lbl, _val in _COLOR_CHOICES]
        _fams = [self._look_default]
        try:
            import tkinter.font as _tkfont
            _fams += sorted({str(f) for f in _tkfont.families() if str(f).strip()})
        except Exception:
            pass
        ctk.CTkLabel(pg_look, text="仅对下列元素生效；每项默认「不改」，不设置时界面与现在完全一致：",
                     text_color="gray", justify="left", font=("Microsoft YaHei", 11)
                     ).grid(row=0, column=0, columnspan=5, padx=pad, pady=(6, 8), sticky="w")
        _lrow = 1
        for _g, _spec in ui_appearance.GROUPS.items():
            _cur = (self._look or {}).get(_g) or {}
            ctk.CTkLabel(pg_look, text=_spec["label"], font=("Microsoft YaHei", 11)
                         ).grid(row=_lrow, column=0, padx=pad, pady=3, sticky="w")
            _w = {}
            _om = ctk.CTkOptionMenu(pg_look, width=130, values=_fams,
                                    font=("Microsoft YaHei", 11))
            _om.set(_cur.get("family") or self._look_default)
            _om.grid(row=_lrow, column=1, padx=(0, 6), pady=3, sticky="w")
            _w["family"] = _om
            _sizes = [self._look_default] + [str(n) for n in
                                             range(ui_appearance.MIN_SIZE, int(_spec["size_max"]) + 1)]
            _om2 = ctk.CTkOptionMenu(pg_look, width=72, values=_sizes,
                                     font=("Microsoft YaHei", 11))
            _om2.set(str(_cur["size"]) if _cur.get("size") else self._look_default)
            _om2.grid(row=_lrow, column=2, padx=(0, 6), pady=3, sticky="w")
            _w["size"] = _om2
            for _ci, _ck in enumerate(("fg", "bg"), start=3):
                if _ck not in _spec["keys"]:
                    continue
                _om3 = ctk.CTkOptionMenu(pg_look, width=100, values=_color_labels,
                                         font=("Microsoft YaHei", 11))
                _cur_v = _cur.get(_ck)
                _om3.set(next((lbl for lbl, val in _COLOR_CHOICES if val == _cur_v),
                              self._look_default))
                _om3.grid(row=_lrow, column=_ci, padx=(0, 6), pady=3, sticky="w")
                _w[_ck] = _om3
            self._look_widgets[_g] = _w
            _lrow += 1
        ctk.CTkLabel(pg_look,
                     text="（背景色仅「条目名称一览浮层」「各字段浮动提示窗」可设；提示词框底色沿用字段配色）",
                     text_color="gray", justify="left", font=("Microsoft YaHei", 10)
                     ).grid(row=_lrow, column=0, columnspan=5, padx=pad, pady=(8, 2), sticky="w")

        def _reset_look() -> None:
            for _w in self._look_widgets.values():
                _w["family"].set(self._look_default)
                _w["size"].set(self._look_default)
                for _ck in ("fg", "bg"):
                    if _ck in _w:
                        _w[_ck].set(self._look_default)

        ctk.CTkButton(pg_look, text="恢复全部默认（不改）", width=170, fg_color="#8a94a6",
                      font=("Microsoft YaHei", 11), command=_reset_look
                      ).grid(row=_lrow + 1, column=0, columnspan=2, padx=pad, pady=(6, 4), sticky="w")
        _hint(pg_look, "字号上限按元素分别限制（条目区名称 ≤20、①名称框 ≤22），避免把界面撑坏；\n"
                       "例：把「条目区名称」字号设为 16，或把「各字段浮动提示窗」字号设为 18 并改文字色。",
              _lrow + 2)

        # ---------------- 第 3 页：数据与备份 ----------------
        _lab(pg_data, "电脑代号", 0)
        self.entry_code = ctk.CTkEntry(pg_data, placeholder_text="默认取主机名")
        self.entry_code.grid(row=0, column=1, padx=pad, pady=8, sticky="ew")
        if self._computer_code:
            self.entry_code.insert(0, self._computer_code)
        _hint(pg_data, "用于变更包文件名区分不同电脑，可随时修改", 1)

        _lab(pg_data, "变更包保留天数", 2)
        self.entry_keep = ctk.CTkEntry(pg_data, width=120)
        self.entry_keep.grid(row=2, column=1, padx=pad, pady=8, sticky="w")
        self.entry_keep.insert(0, str(self._incr_keep_days))

        _lab(pg_data, "自动备份保留份数", 3)
        self.entry_bkeep = ctk.CTkEntry(pg_data, width=120)
        self.entry_bkeep.grid(row=3, column=1, padx=pad, pady=8, sticky="w")
        self.entry_bkeep.insert(0, str(self._backup_keep))
        _hint(pg_data, "同日只保留最后一份；手动快照（snapshot_* 等）永不自动清理；\n"
                       "预置/批量打标前的快照（prompts_pretag_*）单独保留最近 10 份。", 4)

        _lab(pg_data, "变更包携带被删快照", 5)
        self.sw_snapshot = ctk.CTkSwitch(pg_data, text="导出时包含被删条目完整内容（默认关）")
        self.sw_snapshot.select() if self._snapshot else self.sw_snapshot.deselect()
        self.sw_snapshot.grid(row=5, column=1, padx=pad, pady=8, sticky="w")
        _hint(pg_data, "⚠ 开启后变更包内会包含“已删除”的内容（体积增大、含敏感数据），\n"
                       "  但可在导入时使用“⑤ 逆向恢复”找回被删数据。", 6, color=_C_DANGER)

        # 2026-09-17（需求 S-2）：全局热键**可配置**——保存后立即重新注册（默认 Ctrl+Shift+P）。
        _lab(pg_data, "全局热键", 7)
        _hk_row = ctk.CTkFrame(pg_data, fg_color="transparent")
        _hk_row.grid(row=7, column=1, padx=pad, pady=8, sticky="w")
        self.entry_hotkey = ctk.CTkEntry(_hk_row, width=200,
                                         placeholder_text=config.GLOBAL_HOTKEY)
        self.entry_hotkey.insert(0, self._hotkey)
        self.entry_hotkey.pack(side="left")

        def _hk_reset() -> None:
            self.entry_hotkey.delete(0, "end")
            self.entry_hotkey.insert(0, config.GLOBAL_HOTKEY)

        ctk.CTkButton(_hk_row, text="恢复默认", width=90, fg_color="#8a94a6",
                      font=("Microsoft YaHei", 11), command=_hk_reset
                      ).pack(side="left", padx=(6, 0))
        _hint(pg_data, "格式如 ctrl+shift+p / alt+f1（必须含修饰键）；保存后**立即生效**。\n"
                       "注册失败多因被其它软件占用或缺管理员权限——不影响其它功能（可点任务栏图标）。", 8)

        # ---------------- 第 4 页：字段（2026-09-16 批次 11-2 新增） ----------------
        # 2026-09-16（批次 11-2，用户反馈 5）：原「字段管理」入口放在「标签与词表」页下，
        #   但它是**字段定义（数据结构）管理**，与"标签 / 词表"无逻辑关系 ⇒ 独立成【字段】页。
        #   控件名 `_mgr_btn_field` 与 `_apply()` / 锁定置灰逻辑**保持不变**（零功能改动）。
        #   注意：本块必须排在下方"锁定置灰循环"之前（该循环会引用 `_mgr_btn_field`）。
        _lab(pg_field, "字段管理", 0)
        self._mgr_btn_field = ctk.CTkButton(pg_field, text="🔧 字段管理…", width=150,
                                           fg_color=_C_TAG,
                                           command=self._open_field_manager)
        self._mgr_btn_field.grid(row=0, column=1, padx=pad, pady=8, sticky="w")
        _hint(pg_field, "内置 10 个字段可改名（不可删）；自定义字段可增 / 删 / 改名 / 排序 / 改类型。\n"
                        "另一入口在详情区右上角的 🔧 按钮。", 1)

        # ---------------- 第 5 页：标签与词表（三个管理入口） ----------------
        _lab(pg_tag, "标签词表", 0)
        self._mgr_btn_dict = ctk.CTkButton(pg_tag, text="📚 词表管理…", width=150,
                                           fg_color=_C_TAG,
                                           command=self._open_dict_manager)
        self._mgr_btn_dict.grid(row=0, column=1, padx=pad, pady=8, sticky="w")
        _dict_line = "读取失败"
        try:
            _dd = tagger.load_dict(self.db)
            _s = tagger.dict_summary(_dd)
            # 2026-09-18：出厂词表有 19 个领域包 ⇒ 领域名**只列前 6 个 + 等 N 个**，
            #   否则这一行会被页面宽度裁掉（该处标签不自动换行）。
            _dom_list = list((_s.get("领域包") or {}).keys())
            _dom_txt = "、".join(_dom_list[:6]) + ("…等 %d 个" % len(_dom_list)
                                                  if len(_dom_list) > 6 else "")
            _dict_line = (f"当前 {_s.get('标签总数', 0)} 个标签 · "
                          f"{len(_dom_list)} 个领域包（{_dom_txt}）")
        except Exception:
            pass
        # 2026-09-18（用户要求）：**移除"规模预警"追加行**（原 FR-96，>500 标签时提示）。
        _hint(pg_tag, f"{_dict_line}。可导出 JSON 编辑后再导入"
                      "（新增「学术」等大类即加一个领域包）", 1)

        _lab(pg_tag, "热点词表", 2)
        self._mgr_btn_hot = ctk.CTkButton(pg_tag, text="🏷 热点词管理…", width=150,
                                         fg_color=_C_TAG,
                                         command=self._open_hotword_manager)
        self._mgr_btn_hot.grid(row=2, column=1, padx=pad, pady=8, sticky="w")
        try:
            _hot_n = self.db.count_hotwords()
        except Exception:
            _hot_n = 0
        _hint(pg_tag, f"当前 {_hot_n} 个词条。供「自动打标」使用（初始为空；"
                      "可手动添加 / 文本导入 / 从来源网址抓取）", 3)

        # 2026-09-17（用户要求 3）：新增「🧹 清理垃圾标签…」入口——
        #   清理**历史遗留**的垃圾标签（打标引擎旧版无质量闸门时写入）：
        #     ① 纯数字 `12`/`234`；② 十六进制色值 `000000`/`1A2332`；③ 内部含标点 `古风/汉服`。
        #   与仓库根目录 CLI 工具 `clean_junk_tags.py` **共用数据层** `app/tag_cleanup.py`（口径唯一）。
        #   ⚠ 布局：本页内容须不超过页高（gui_self_test 有断言），故把它与「批量智能打标」**并入同一行**，
        #     并把两段说明合并为一行提示，净省约一行高度。
        _lab(pg_tag, "批量打标与维护", 4)
        _mgr_row = ctk.CTkFrame(pg_tag, fg_color="transparent")
        _mgr_row.grid(row=4, column=1, padx=pad, pady=8, sticky="w")
        self._mgr_btn_batch = ctk.CTkButton(_mgr_row, text="🤖 批量智能打标…", width=150,
                                           fg_color=_C_TAG,
                                           command=self._open_batch_tag)
        self._mgr_btn_batch.pack(side="left")
        self._mgr_btn_cleanup = ctk.CTkButton(_mgr_row, text="🧹 清理垃圾标签…", width=150,
                                             fg_color=_C_DANGER,
                                             command=self._open_tag_cleanup)
        self._mgr_btn_cleanup.pack(side="left", padx=(6, 0))
        _hint(pg_tag, "批量打标：先备份 + 先预演 + 二次确认 + 可精确撤销；"
                      "清理垃圾标签：先预览、执行前自动整库备份。", 5)
        # 2026-09-16（批次 12-2，用户要求 2）：取词是否用于"批量打标 / 离线打标"
        #   —— 与「批量智能自动打标」对话框的「⑦ 取词用于批量打标」是**同一个开关**（同 meta 键）。
        #   说明文字并入开关文本，**不再单独占一行**（省高度，见页高回落说明）。
        _lab(pg_tag, "取词用于批量打标", 6)
        self.sw_fb_batch = ctk.CTkSwitch(
            pg_tag, text="批量 / 离线打标也采用「字段取词」（默认关；开启前请先预演）")
        self.sw_fb_batch.select() if self._fallback_batch else self.sw_fb_batch.deselect()
        self.sw_fb_batch.grid(row=6, column=1, padx=pad, pady=8, sticky="w")

        # 2026-09-15（批次 6-2，用户选"锁定态允许打开设置、管理入口置灰"）：
        #   本页 4 个管理入口都会**改数据** ⇒ 锁定态一律置灰；**设置对话框本身仍可打开**
        #   （窗口/视图/外观等纯偏好不受影响）。锁定状态由主窗口持有，故从 master 读取。
        # 2026-09-17：新增「🧹 清理垃圾标签…」——同样会改数据，故一并纳入置灰集合（本页 4 个）。
        _locked = bool(getattr(self.master, "_lock_on", False))
        for _b in (self._mgr_btn_dict, self._mgr_btn_hot, self._mgr_btn_cleanup,
                   self._mgr_btn_field, self._mgr_btn_batch):
            try:
                _b.configure(state=("disabled" if _locked else "normal"))
            except Exception:
                pass
        if _locked:
            _hint(pg_tag, "⚠ 已锁定：以上 4 个管理入口暂不可用（解锁后即可使用）", 8, color="#C77700")
            _hint(pg_field, "⚠ 已锁定：字段管理暂不可用（解锁后即可使用）", 2, color="#C77700")

        # ---------------- 标签推荐策略与顺序（2026-09-16 批次 11-6，用户确认问题 2） ----------------
        #   作用：控制"无显式标签时"自动推荐（详情区「✨ 推荐标签」/ 快速新建）**用哪些来源、
        #   按什么先后顺序**取值。每项 = 勾选框（是否作为推荐来源）+ ↑/↓（调整先后顺序）。
        #   注意：「显式标签」（来源文本自带"标签：…"）**恒为最高优先且不可关闭**，故不列入下表；
        #   2026-09-18（用户确认）：配额改为"**各来源尽力推荐 → 按顺序拼队列 → 取前 N（默认 3）**"，
        #   来源之间**不再互相扣减名额**（旧 `tagger_engine.fallback_cap` 语义已废弃）；
        #   即：把某来源排在前面，它的标签就更可能进最终结果。
        _lab(pg_tag, "推荐策略与顺序", 9)
        self._pol_frame = ctk.CTkFrame(pg_tag, fg_color="transparent")
        self._pol_frame.grid(row=10, column=0, columnspan=2, padx=pad, pady=(0, 2), sticky="w")
        ctk.CTkLabel(self._pol_frame,
                     text="① 显式标签（来源文本自带，恒最高优先、不可关闭）",
                     font=("Microsoft YaHei", 11), text_color="#4a5568", justify="left"
                     ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._policy_rows = {}
        self._pol_rebuild()
        # 「恢复默认顺序」与使用说明**同一行**（省一行高度，见页高回落说明）
        _pr = ctk.CTkFrame(self._pol_frame, fg_color="transparent")
        _pr.grid(row=(len(self._policy_order) + 1) // 2 + 1, column=0, columnspan=2,
                 sticky="w", pady=(6, 2))
        ctk.CTkButton(_pr, text="↺ 恢复默认顺序", width=130, fg_color="#8a94a6",
                      font=("Microsoft YaHei", 11), command=self._pol_reset
                      ).pack(side="left")
        ctk.CTkLabel(_pr, text="①显式标签恒最高优先·不可关；勾选＝参与推荐；↑/↓ 调整先后；各来源尽力推荐，按序取前 N",
                     font=("Microsoft YaHei", 10), text_color="gray"
                     ).pack(side="left", padx=(8, 0))

        # ---------------- 智能自动取词（2026-09-16 批次 12-3，用户要求 1） ----------------
        #   作用：把"未命中词表/热点词"的取词沉淀为"新词库"并累计频次；达热点阈值**自动**加入
        #   热点词表；达词表阈值**只提示**，由你在「🧠 自动取词词库…」里审核后加入词表标签。
        _lab(pg_tag, "智能自动取词", 11)
        _af = ctk.CTkFrame(pg_tag, fg_color="transparent")
        _af.grid(row=11, column=1, padx=pad, pady=8, sticky="w")
        _cfg3 = auto_words.load_cfg(self.db)
        self.sw_aw = ctk.CTkSwitch(_af, text="启用采集")
        self.sw_aw.select() if _cfg3.get("enabled") else self.sw_aw.deselect()
        self.sw_aw.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(_af, text="热点阈值", font=("Microsoft YaHei", 11)).pack(side="left")
        self.ent_aw_hot = ctk.CTkEntry(_af, width=52, font=("Microsoft YaHei", 11))
        self.ent_aw_hot.insert(0, str(_cfg3.get("hot_th")))
        self.ent_aw_hot.pack(side="left", padx=(4, 10))
        ctk.CTkLabel(_af, text="词表阈值", font=("Microsoft YaHei", 11)).pack(side="left")
        self.ent_aw_tag = ctk.CTkEntry(_af, width=52, font=("Microsoft YaHei", 11))
        self.ent_aw_tag.insert(0, str(_cfg3.get("tag_th")))
        self.ent_aw_tag.pack(side="left", padx=(4, 10))
        ctk.CTkButton(_af, text="🧠 自动取词词库…", width=150, fg_color=_C_TAG,
                      command=self._open_auto_words).pack(side="left")
        # 2026-09-17（需求 FR-97）：达「词表阈值」的待审核词做**角标提示**——
        #   只在此处显示计数，不弹窗、不打断；为 0 时不显示（界面与原来完全一致）。
        try:
            _aw_ready = int(auto_words.stats(self.db).get("ready_tag") or 0)
        except Exception:
            _aw_ready = 0
        if _aw_ready:
            ctk.CTkLabel(_af, text="⚠ 待审核 %d" % _aw_ready,
                         font=("Microsoft YaHei", 11), text_color="#C77700"
                         ).pack(side="left", padx=(8, 0))

        # ---------------- 第 6 页：关于 ----------------
        ctk.CTkLabel(pg_about, text=f"{config.APP_NAME} · 提示精灵　V{config.APP_VERSION}",
                     font=("Microsoft YaHei", 15, "bold")
                     ).grid(row=0, column=0, columnspan=2, padx=pad, pady=(14, 2), sticky="w")
        _st = {}
        try:
            _st = self.db.stats()
        except Exception:
            _st = {}
        ctk.CTkLabel(pg_about, text=(f"当前库数据：{_st.get('projects', 0)} 项目类别 · "
                                     f"{_st.get('domains', 0)} 根目录 · "
                                     f"{_st.get('categories', 0)} 分类 · "
                                     f"{_st.get('entries', 0)} 条提示词"),
                     font=("Microsoft YaHei", 12)
                     ).grid(row=1, column=0, columnspan=2, padx=pad, pady=(2, 6), sticky="w")
        # 2026-09-14（用户要求 3）：原「联网说明」是**一整行 66 汉字**（实测需求宽 858px），
        #   远超本页可视文字宽（约 645px）→ 右侧约 16 个汉字**看不见**。现按 40 汉字左右**断为 2 行**
        #   （**该提示自身**仍为 3 行、高度不变），并保持"　"缩进的续行风格。
        #   ⚠ 2026-09-15（审核 L-5）：本页随后在 row=3 另加了"无标签条目"提示 → 整页内容比之前更多；
        #     原注释"总行数仍为 3 行，页面高度不变"只对上面这一条提示成立，已写明口径避免误读。
        _hint(pg_about, "本地优先的 AI 提示词管理工具：数据 100% 存在本机。\n"
                        "联网说明：仅在你主动使用向导的「从网址获取」或「热点词更新」时才会访问网络；\n"
                        "　（只读抓取你填写的网址，不后台访问、不写盘、不留登录态）其余功能全程离线。", 2)
        # 2026-09-14（用户要求 1）：把"在查询栏输入查无标签条目"的方法写进「关于」。
        # 每行控制在约 45 汉字内，避免超出本页可视宽度（实测本页可视宽约 677px、可用文字约 645px）。
        _hint(pg_about, "🔎 查「无标签」条目（三种方式效果相同）：\n"
                        "　① 搜索框输入 #无标签（英文别名 #none）；② 工具栏「🏷 无标条目」；\n"
                        "　③ 标签页面底部「⚠ 无标签条目（N）」。\n"
                        "　打标签两法：逐条点选该条目 → 在详情区「🏷 标签」区打标签并保存；\n"
                        "　或打开「🤖 批量打标…」→「范围」选「当前列表」→ 预演 → 执行。",
              3)
        # 2026-09-16（批次 15，用户要求 4）：关于页补入"详情区排布"新能力说明（对应说明书 23.1 / 23.2）。
        #   每行控制在约 45 汉字内（本页可视文字宽约 645px），避免被裁。
        _hint(pg_about, "🧩 详情区排布（2026-09-16 新增）：⚙ 设置 → 字段 → 「🔧 字段管理…」\n"
                        "　· 排序：用 ↑↓ 调整除 ① 名称外所有区块（②~⑩ / 🏷 标签 / 🧭 位置 / 🕒 时间 / 自定义字段）；\n"
                        "　· 隐藏：每行「隐藏 / 显示」，或一键「全部隐藏 / 全部显示」（① 名称固定显示）；\n"
                        "　· 隐藏只影响详情区展示，已填内容不会丢；头部状态行会提示已隐藏的项数。", 4)
        # 2026-09-17 12:49（V2.1.0 文档修订）：关于页补入"数据与安全 + 已知限制摘要"说明，
        #   依据《项目审核和修改建议报告 V10》S-1（明文库披露）、S-2（热键不可改）与说明书第 22 章。
        #   每行控制在约 45 汉字内（本页可视文字宽约 645px），避免被裁。
        _hint(pg_about, "🔐 数据与安全（请务必了解）：\n"
                        "　· 数据位置：与程序同目录的 data 文件夹（prompts.db）；整个 data 复制走即可换机；\n"
                        "　· 备份：每次启动自动全量备份（data\\backup\\，按天去重、默认留 30 份）；\n"
                        "　· 导入前 / 批量打标前自动快照可回滚；删除先进回收站，可恢复；\n"
                        "　· 数据库为明文 SQLite（不含加密），有隐私需求建议配合系统级磁盘加密；\n"
                        "　· 库结构 v5 / 导出格式 v6：回退旧版软件需同时恢复数据库备份（本次修订）。", 5)
        ctk.CTkButton(pg_about, text="ℹ 查看完整说明（功能亮点 / 数据与安全 / 各版本修订 / 运行信息）…",
                      width=430, fg_color="#8a94a6", command=self._show_about
                      ).grid(row=6, column=0, columnspan=2, padx=pad, pady=(10, 6), sticky="w")

        # ---------------- 底部固定按钮 ----------------
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(2, 12))
        ctk.CTkButton(btn_row, text="确定", width=110, fg_color=_C_OK,
                      command=self._apply).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_row, text="取消", width=110,
                      command=self.destroy).pack(side="right")

    def _open_field_manager(self) -> None:
        """打开主窗口的"字段管理"对话框（2026-09-13 1-A-4 新增入口）。

        通过主窗口方法调用，避免本模块反向依赖字段管理对话框（保持功能隔离）。
        """
        fn = getattr(self.master, "_open_field_manager", None)
        if callable(fn):
            fn()

    def _open_auto_words(self) -> None:
        """打开「🧠 自动取词词库」管理对话框（2026-09-16 批次 12-3）。

        与「字段管理 / 热点词管理」同一做法：经主窗口方法调用（避免反向依赖）；
        本对话框需在主窗口之上交互，故先释放本窗口的模态抓取。
        """
        fn = getattr(self.master, "_open_auto_words_manager", None)
        if not callable(fn):
            return
        try:
            self.grab_release()
        except Exception:
            pass
        fn()
        try:
            self.lift()
        except Exception:
            pass

    def _open_tag_cleanup(self) -> None:
        """打开「🧹 清理垃圾标签」对话框（2026-09-17 新增，用户要求 3）。

        与「字段管理 / 热点词管理 / 自动取词词库」同一做法：经主窗口方法调用
        （避免本模块反向依赖具体对话框）。本对话框需在主窗口之上交互，
        故先释放本窗口的模态抓取。
        """
        fn = getattr(self.master, "_open_tag_cleanup", None)
        if not callable(fn):
            return
        try:
            self.grab_release()
        except Exception:
            pass
        fn()
        try:
            self.lift()
        except Exception:
            pass

    # ---- 标签推荐策略与顺序（2026-09-16 批次 11-6，用户确认问题 2） ---- #
    # 2026-09-16（批次 12-2）：改为**两列紧凑布局**（每行 2 个来源）＋短标签，
    #   以压低页高（详见 _build 里页高回落的说明）；顺序仍为「从左到右、再下一行」。
    _POLICY_SHORT = {tagger_engine.SOURCE_HOTWORD: "热点词",
                     tagger_engine.SOURCE_DOMAIN_DICT: "领域+词表匹配",
                     tagger_engine.SOURCE_FIELD: "字段取词",
                     tagger_engine.SOURCE_EXT: "扩展接口（预留）"}

    def _pol_rebuild(self) -> None:
        """重建策略的 4 行控件（勾选框 + 来源名 + ↑/↓）；顺序取自 `self._policy_order`。"""
        try:
            for _w in (self._policy_rows or {}).values():
                _w["row"].destroy()
        except Exception:
            pass
        self._policy_rows = {}
        for _i, _src in enumerate(self._policy_order):
            _r, _c = divmod(_i, 2)
            _row = ctk.CTkFrame(self._pol_frame, fg_color="transparent")
            _row.grid(row=_r + 1, column=_c, sticky="w", padx=(0, 12), pady=1)
            _chk = ctk.CTkCheckBox(
                _row, text="%d. %s" % (_i + 1, self._POLICY_SHORT.get(_src, _src)),
                font=("Microsoft YaHei", 11), checkbox_width=18, checkbox_height=18,
                width=180, command=self._pol_snapshot)
            if self._policy_enabled.get(_src):
                _chk.select()
            else:
                _chk.deselect()
            _chk.grid(row=0, column=0, sticky="w")
            for _ck, _ct, _delta in (("up", "↑", -1), ("dn", "↓", 1)):
                _b = ctk.CTkButton(_row, text=_ct, width=30, height=24,
                                   font=("Microsoft YaHei", 11),
                                   command=lambda s=_src, d=_delta: self._pol_move(s, d))
                _b.grid(row=0, column=(1 if _ck == "up" else 2), padx=(3, 2))
                if _ck == "up":
                    _b.configure(state=("normal" if _i > 0 else "disabled"))
                else:
                    _b.configure(state=("normal" if _i < len(self._policy_order) - 1
                                        else "disabled"))
            self._policy_rows[_src] = {"row": _row, "chk": _chk}

    def _pol_snapshot(self) -> None:
        """把当前各勾选框状态记回 `self._policy_enabled`（切换/保存前调用）。"""
        for _src, _w in (self._policy_rows or {}).items():
            try:
                self._policy_enabled[_src] = bool(_w["chk"].get())
            except Exception:
                pass

    def _pol_move(self, src: str, delta: int) -> None:
        """把来源上移 / 下移一位（先记录勾选状态，避免重建后丢失）。"""
        self._pol_snapshot()
        try:
            _i = self._policy_order.index(src)
        except ValueError:
            return
        _j = _i + int(delta)
        if _j < 0 or _j >= len(self._policy_order):
            return
        self._policy_order[_i], self._policy_order[_j] = (self._policy_order[_j],
                                                          self._policy_order[_i])
        self._pol_rebuild()

    def _pol_reset(self) -> None:
        """恢复默认顺序与默认开关（顺序：热点词 → 领域+词典 → 取词 → 扩展）。"""
        self._policy_order = list(tagger_engine.DEFAULT_POLICY["order"])
        self._policy_enabled = dict(tagger_engine.DEFAULT_POLICY["enabled"])
        self._pol_rebuild()

    def _open_hotword_manager(self) -> None:
        """打开主窗口的"热点词管理"（2026-09-14 阶段 4 之 4-e 新增入口）。

        与"字段管理"入口同一做法（经主窗口方法调用，避免本模块反向依赖标签页面）；
        实际复用标签页面的【热点词】页签——两处入口共享同一界面与同一份数据。

        注：标签页面位于主窗口内，故先释放本窗口的模态抓取（否则主窗口不可交互）；
        本窗口保持打开，返回后可继续修改设置并按"确定/取消"。
        """
        fn = getattr(self.master, "_open_hotword_manager", None)
        if not callable(fn):
            return
        try:
            self.grab_release()
        except Exception:
            pass
        fn()
        try:
            self.lift()
        except Exception:
            pass

    def _open_dict_manager(self) -> None:
        """打开主窗口的"词表管理"（2026-09-14 12:00 阶段 0.5 新增入口）。

        与「🏷 热点词管理…」同一做法：经主窗口方法调用，实际复用标签页面的【词表】页签；
        因该页面位于主窗口内，先释放本窗口的模态抓取（否则主窗口不可交互）。
        """
        fn = getattr(self.master, "_open_dict_manager", None)
        if not callable(fn):
            return
        try:
            self.grab_release()
        except Exception:
            pass
        fn()
        try:
            self.lift()
        except Exception:
            pass

    def _open_batch_tag(self) -> None:
        """打开主窗口的「🤖 批量智能自动打标」对话框（2026-09-14 阶段 2 新增入口）。

        与「🏷 热点词管理…」同一做法：经主窗口方法调用（避免本模块反向依赖该对话框）。
        """
        fn = getattr(self.master, "_open_batch_tag", None)
        if not callable(fn):
            return
        fn()

    def _show_about(self) -> None:
        """关于窗口：软件名、版本、本机运行信息、功能亮点与各版本修订（2026-09-12 完善至 V1.9.0）。

        2026-09-12：修订说明随版本累积变长，内容区改为"可滚动 + 高度不超过屏幕 68%"，
        避免在 1366×768 等小屏上"关闭"按钮被挤出屏幕。
        """
        win = ctk.CTkToplevel(self)
        win.title("关于 PromptSprite")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        # 本机运行信息（2026-09-07 新增：运行文件 / 程序路径 / 提示词长度）
        if config.is_frozen():
            exe_name = os.path.basename(sys.executable)      # 打包态：EXE 全名
            exe_dir = os.path.dirname(sys.executable)        # 打包态：EXE 所在目录
            run_desc = f"运行文件：{exe_name}"
            path_desc = f"程序路径：{exe_dir}"
        else:
            script = os.path.basename(sys.argv[0]) if sys.argv else ""
            run_desc = f"运行文件：开发模式（{script}），未打包 EXE"
            path_desc = f"程序路径：{config.PROJECT_ROOT}"
        # P2-10：内置/当前数据动态取数（不硬编码，避免与真实库脱节）
        _st = {}
        try:
            _st = self.db.stats()
        except Exception:
            _st = {}
        data_line = (f"· 当前库数据：{_st.get('projects', 0)} 项目类别 · "
                     f"{_st.get('domains', 0)} 根目录 · "
                     f"{_st.get('entries', 0)} 条提示词（动态统计）\n")
        info = (
            f"{config.APP_NAME} · 提示精灵\n"
            f"版本 V{config.APP_VERSION}（2026-09-17 条目稳定 ID 版；\n"  # 2026-09-17 16:55：版本号升为 2.2.0，首行描述同步
            "  基础版 2026-09-14：智能标签 / 条目图集 / 结构化向导 / 联网抓取；\n"
            "  2026-09-15 / 09-16 增强：详情区区块排序与隐藏、智能自动取词；\n"
            "  2026-09-17 增强：条目稳定 ID（库结构升 schema v5、导出格式升 JSON v6）——\n"
            "  「修改」真正同步为更新同一条目、多位置往返保真、变更包按 uuid 精确同步；\n"
            "  同日文档修订：审核报告 V10 / 需求规格 V9 / 说明书 V12 / 打包指南 V11）\n"
            "──────────────────────────\n"
            "本地优先的 AI 提示词管理工具：数据 100% 存在本机。\n"
            "· 联网说明：仅在你主动使用向导的「从网址获取」或「热点词更新」时才会访问网络"
            "（只读抓取你填写的网址，不后台访问、不写盘、不留登录态）；其余功能全程离线。\n"
            # 2026-09-17 12:49（V2.1.0 文档修订）：新增"数据与安全"整段（依据审核报告 V10 的 S-1/S-2 与说明书第 16/22 章）
            "· 数据与安全：\n"
            "  - 数据位置：与程序同目录的 data 文件夹（主库 prompts.db）；整个 data 复制走即可换机；\n"
            "  - 备份机制：每次启动自动全量备份（data\\backup\\，按天去重、默认保留 30 份，可在设置调整）；\n"
            "    导入前 / 批量打标前自动生成快照（preimport / pretag），快照失败即中止操作；\n"
            "    删除条目先进回收站（完整快照，可恢复）；批量打标可精确撤销；\n"
            "  - 明文存储：数据库为明文 SQLite，不含加密；如有隐私需求建议配合系统级磁盘加密（BitLocker 等）；\n"
            "  - 库结构 v5 / 导出格式 v6：回退旧版软件需同时恢复数据库备份（旧版不认识新结构）。\n"
            "· 五级分类书架（项目类别 / 根目录 / 一级分类 / 二级分类 / 条目）\n"
            "· 标签系统（详情区打标签；标签页面「标签云 / 列表＋计数」双呈现，且/或组合过滤）\n"
            "· 智能标签：内置库已预置标签（开箱即用）；「批量智能自动打标」可对无标签/全部条目批量打标\n"
            "  （强制先备份 + 先预演 + 二次确认，可精确撤销）；标签词表与热点词表均可在软件内维护\n"
            "· 条目图集（封面 + 多图；本地图与外链图，可下载外链图到本地）\n"
            # 2026-09-16（批次 15，用户要求 4）：关于页补入 09-15/09-16 两项新能力
            "· 详情区区块可自由排序、可逐个隐藏（🔧 字段管理；① 名称固定显示，隐藏不丢内容）\n"
            "· 智能自动取词词库（自动沉淀词表外新词，达阈值升热点词 / 提示并入词表）\n"
            "· 网上资源结构化向导（粘贴 / 文件 / 网址抓取 → 四层映射 → 质量自检 → 先存后导）\n"
            f"{data_line}"
            "· 条目多位置：关联到（一处编辑各处同步）/ 复制到（独立副本）/ 移动到（可整体转移）\n"
            "· 条目就地新增醒目入口；任一分级分类可持有并显示本级条目\n"
            "· 详情区多位置提示；删除分类三级保护（级联删除需输入确认短语）\n"
            "· 每日变更包（按电脑代号区分，可换机合并导入）；纯删除包有醒目标识、可逆向恢复\n"
            "· 导入向导：新增 / 删除分两维选择（忽略删除 / 应用删除 / 逆向恢复），导入前自动快照\n"
            "· 一键复制 / 新建 / 全局热键 Ctrl+Shift+P 唤起；自动全量备份（按天去重、默认保留 30 份）\n"
            "· JSON / Excel / Markdown / HTML 多格式导入导出；可与备份库数据比对\n"  # 2026-09-17 17:43：比对已支持按稳定 ID 识别"修改"
            "  （比对「内容已修改」时优先按稳定 ID 配对：改名又改内容也能准确识别）\n"
            "· 老版本数据自动 / 引导迁移（未明确分类兜底）\n"
            # 2026-09-17 12:49（V2.1.0 文档修订）：新增"已知限制摘要"与"配套文档"两段
            #   （依据《软件安装使用说明书 V12》第 22 章"已知限制"与第 25 章"开源仓库与许可"）
            "· ⚠ 已知限制摘要（详见《软件安装使用说明书 V12》第 22 章）：\n"
            "  - 搜索停顿约 1.5 秒才查是刻意设计（点 🔍 或回车可立即搜）；\n"
            "  - 区块顺序 / 隐藏 / 外观属「本机偏好」，不随数据包跨机同步；\n"
            "  - 「手动隐藏」是硬隐藏，头部「显示全部字段」不会恢复它（须回字段管理改回「显示」）；\n"
            "  - ① 名称在详情区标题固定，不参与排序、不可隐藏；\n"
            "  - 多位置条目：JSON 已保真（1 条 + N 位置）；Excel 按行展开但导入按 uuid 合并、不再产生副本；\n"
            "  - 换机同步中的「修改」已按稳定 ID **更新同一条目**（旧包仍按内容键合并）；\n"
            "  - 库结构 v5：回退旧版软件需**同时恢复数据库备份**；\n"
            "  - 数据库为明文 SQLite，不含加密。\n"
            "· 配套文档：README.md ／ 需求规格 V9 ／ 审核报告 V10 ／ 说明书 V12 ／ 打包指南 V11\n"
            "  ／ 条目稳定 ID 改造施工方案 V1（均随仓库 docs/ 目录提供；以最新文档为准）\n"
            # 2026-09-18 午后（用户要求）：取词策略优化（用户确认"方案 A"）
            "· 取词能力优化（2026-09-18 午后）：\n"
            "  - **取词不再只能产出词表已有的标签**：命中词表/热点词的映射为**规范标签名**；\n"
            "    词表外的**高质量新词**（中文 2~6 字、英文两词短语）可直接成为标签，\n"
            "    来源标注「取词（新词）」（提示会显示「来源：标题/提示词，含新词」）\n"
            "  - 新增**提示词样板词/参数词黑名单**（masterpiece / 8K / HDR / aspect ratio /\n"
            "    BRAND NAME / headline text / 生成图片 …）：模板词与占位符不再成为标签，\n"
            "    也不再污染「智能自动取词词库」的新词沉淀\n"
            "  - **召回增强**：两词短语合并命中（`high`+`contrast` → 高对比）、\n"
            "    保守词形兜底（portraits → 人像）、连字符/下划线归一（`film-grain`/`film_grain` → 胶片）\n"
            "  - **配额改为「各来源尽力推荐 → 按策略顺序取前 N」**：不再由来源之间互相扣减名额\n"
            "    （旧 `fallback_cap` 语义废弃）；把「字段取词」排在前面，它的标签就会排在最终结果前面\n"
            "  - **有来源自带标签（「标签：…」）时也不再跳过取词**：取词照常产出，只是排在显式标签之后\n"
            "  - **兜底取词（保证不空白）**：取词正常按严格闸门「有就推荐、没有就算了」（0~3 个）；\n"
            "    但**当「热点词」与「领域+词表」两个来源都为 0 时**，自动用兜底闸门（中文 2~12 字、\n"
            "    英文 1~3 词）重算一次并追加到队尾 ⇒ **保证字段取词至少推荐 1 个**（标注「取词（兜底）」）\n"
            "  - 实测（主库 2556 条、UI 单条推荐口径）：取词标签 200 → **776** 次；完全无标签 102 → **56** 条；\n"
            "    最终结果里**能看到取词标签的条目 600 条（23.5%）**；「另两来源为 0 且取词为 0」= **0 条**\n"
            # 2026-09-18（用户要求）：词表能力增强 + 出厂词表升级为 699 标签版 + 移除规模预警
            "· 词表与查询体验增强（2026-09-17 深夜 ~ 09-18）：\n"
            "  - 词表页签新增「➕ 增量导入…」：只把新词**并入**当前词表（只增不删、重复导入幂等），\n"
            "    与原有「⬇ 导入（替换）…」并存；确认框会写明「现有多少全部保留、将新增多少」\n"
            "  - 主界面与标签页面的搜索框新增「✕ 一键清除」；「热点词」添加后光标自动回输入框（可连续添加）\n"
            "  - **出厂词表已升级**：由 135 个标签（视觉 / 文学 / 编程 3 个领域包）升级为\n"
            "    **809 个标签 / 21 个领域包**——新安装用户与「↺ 恢复出厂…」都用这一版；\n"
            "    已装老库不会自动跟随，如需切换请点一次「↺ 恢复出厂…」（改前自动备份当前词表）\n"
            "    （升级轨迹：135 / 3 → 699 / 19（09-18 凌晨）→ **809 / 21**（09-18 午后，\n"
            "      新增「作者专用」「用户词表」两个领域包））\n"
            "  - 移除词表「规模预警」提示（原为 >500 标签时提醒；新词表本身已更大，该提示无实际意义）\n"
            "  - 内置库「预置标签」按当时的新词表**重打一遍**（工具 `tag_builtin.py --replace`）：\n"
            "    593 个标签 / 6574 条关联（此前 592 / 6536；按当时 699 版词表重打）\n"
            "  - 批量打标窗口适配维度大增：「⑤ 标签重点范围」改为**固定高度 + 多列 + 可滚动**，\n"
            "    不再把下方的预演区与「预演 / 确认执行 / 取消」按钮挤出视野\n"
            "──────────────────────────\n"
            # 2026-09-16（批次 15，用户要求 4）：关于页补入 09-15/09-16 修订说明（版本号未变）
            "V2.1.0 后续增强（2026-09-15 / 09-16，版本号未变）：\n"
            "   - 详情区区块自由排序：字段管理用 ↑↓ 调整除 ① 名称外所有区块（②~⑩ / 🏷 标签 /\n"
            "     🧭 位置 / 🕒 时间 / 自定义字段）的顺序，可跨组移动；编辑态与浏览态一致\n"
            "   - 区块可逐个隐藏：字段管理每行「隐藏 / 显示」，另有「全部隐藏 / 全部显示」一键批量；\n"
            "     ① 名称固定显示、不可隐藏；手动隐藏是硬隐藏（「显示全部字段」也不恢复它），\n"
            "     只影响详情区展示——已填内容不会丢，隐藏项不占位置、下方内容自动上移\n"
            "   - 智能自动取词词库：词表外新词自动沉淀并按「不同条目数」计数（同一条目只算 1 次），\n"
            "     达热点阈值（默认 3）自动升为热点词、达词表阈值（默认 5）只提示审核；\n"
            "     入口：设置 → 标签与词表 → 「🧠 自动取词词库…」（批量 / 离线打标不采集）\n"
            "   - 「取词用于批量打标」开关：批量打标对话框与设置页两个入口指向同一设置（默认关）\n"
            "   - 推荐策略：条目无显式标签时优先从「①名称 + ⑧中文 + ⑨英文」直接切词取标签\n"
            "     （按逗号 / 顿号 / 分号切，凑够 3 个即止；不足再由领域判定 + 词表匹配补足）\n"
            "   - 详情区 ②~⑦ 改为每个字段各自独立折叠（各有「展开 / 收起」）；⑦ 的图片区随该字段显隐\n"
            "   - 设置对话框增至 6 页：界面 / 外观 / 数据与备份 / 字段 / 标签与词表 / 关于\n"
            "   - 兼容性：库结构仍 schema v4、导入导出仍 JSON v5、未新增第三方依赖\n"
            "V2.1.0 智能标签升级（2026-09-14）：\n"
            "   - 智能标签体系：出厂词表 =「通用骨架（领域 / 用途）」+「领域维度包」；打标三源优先级：\n"
            "     来源自带标签 → 分类名映射 → 词表匹配（V2.1.0 时为 135 个标签 / 视觉·文学·编程 3 个领域包；\n"
            "     2026-09-18 起出厂词表已升级为 809 个标签 / 21 个领域包，见上方「词表与查询体验增强」）\n"
            "   - 内置库已预置标签：2555 条条目 → 622 个标签 / 6579 条关联，开箱即用（无需逐条手工打标）\n"
            "   - 批量智能自动打标：可对「仅无标签的条目」或「全部条目」批量打标；\n"
            "     范围 / 数量上限 / 每条例目标签数 / 标签重点范围（维度）/ 追加或覆盖 均可选；\n"
            "     强制「执行前自动备份」+「必须先预演」+「二次确认」，执行后可「精确撤销」\n"
            "   - 词表管理：标签页面「词表」页签 / 设置内入口；导出 JSON 后可自行增删（新增一个大类\n"
            "     只需加一个领域包）再导入；支持「恢复出厂词表」\n"
            "   - 热点词表：标签页面「热点词」页签 / 设置内入口；逐条添加 / 文本导入 / 来源网址抓取\n"
            "   - 设置对话框按 4 页重排（界面 / 数据与备份 / 标签与词表 / 关于）\n"
            "   - 性能：标签云改为自动换行（修复「标签多时只显示前几个」）；标签页与条目区加上限\n"
            "     （默认前 50，可「显示更多」/「全部显示」），首屏等待从分钟级降到秒级\n"
            "   - 推荐策略优化（2026-09-15）：条目无显式标签时，优先从「①名称 + ⑧中文 + ⑨英文」\n"   # 2026-09-15 19:30（批次10）：补充推荐策略优化说明
            "     提示词直接切词取标签（提示词按逗号/顿号/分号切，保留 film grain 类短语），\n"
            "     凑够 3 个即止，不足再由领域判定 + 词表匹配补足；有显式标签时不变\n"
            "   - 无标签条目入口（2026-09-14）：工具栏「🏷 无标条目」/ 标签页面底部「⚠ 无标签条目（N）」/\n"
            "     搜索框输入「#无标签」（英文别名 #none，可与关键词组合如「#无标签 电影」）；\n"
            "     该列表可逐条点选打标签，也可「🤖 批量打标 → 范围＝当前列表」整批打标\n"
            "V2.0.0 重大升级（2026-09-14）：\n"
            "   - 标签系统：详情区「🏷 标签」区块（输入即补全既有标签 / 回车新建 / ×移除）；\n"
            "     工具栏与详情区底部均可打开「标签页面」——标签云 ⇄ 列表＋计数双呈现、\n"
            "     且/或组合过滤、重命名/合并/删除/清理未使用；与四级目录互斥\n"
            "   - 条目图集：详情区升级为「封面 + 图集」，本地图与外链图双源，外链图可「下载到本地」\n"
            "   - 字段能力：自定义字段可增/删/改名/排序/改类型；11 种类型各有专用控件\n"
            "     （单/多行文本、链接、列表框、数字、日期、是/否、标签、附件、短音频可试听、\n"
            "     图像——含内嵌缩略预览）\n"
            "   - 列表框字段三类数据源：自定义序列 / 目录层级（可选层级与子树）/ 条目（全部或指定分类）\n"
            "   - 网上资源结构化向导：粘贴文本 / 本地文件（CSV·TSV·TXT·HTML·MD）/ 网址获取\n"
            "     （网页 / GitHub 文件 / GitHub 目录批量）；四层映射 → 字段映射 → 质量自检 →\n"
            "     先保存数据文件、再导入（可一键回滚）\n"
            "   - 联网抓取：多通道降级（raw → contents API → git blob）、多表页面可选表、\n"
            "     相对链接自动补全、GitHub 目录批量（默认勾选常见文档；单文件≤2MB、一次≤20 个、\n"
            "     合计≤20MB；个别失败逐个跳过不影响整体）\n"
            "   - 数据安全修复：回收站批量快照补全标签/自定义字段/图集；清空回收站释放图集文件；\n"
            "     变更包随包携带字段定义；打标签、改自定义字段会正确进入当日变更包\n"
            "   - 库结构升到 schema v4、导入导出格式升到 JSON v5（旧备份与旧导出文件仍可直接导入）\n"
            "V1.9.0 界面修订（2026-09-12）：\n"
            "   - 详情区字段浮动提示窗口重构：修复“光标与提示窗重叠时闪烁”；提示窗固定在\n"
            "     详情区左侧、不遮盖正文；隐藏四级目录后自动压窄\n"
            "   - 提示窗增强：内容可滚动（滚动条 / 滚轮）、随文本框同步滚动、光标所在行加深、\n"
            "     字号 14pt、高度最少 18 行 / 最多 30 行\n"
            "   - 新增“💬 打开/关闭浮动提示窗口”总开关：一键关闭“条目名称一览”与\n"
            "     “详情区字段提示”两类浮动提示（按钮 / 导航长名称提示不受影响）\n"
            "   - “🗂”按钮新增浮动提示“打开/关闭四级目录”，并支持两态配色：\n"
            "     目录打开＝绿色、目录关闭＝黄色\n"
            "   - “条目名称一览”浮层文字字号与条目名称一致，最小高度 25 行\n"
            "V1.8.0 界面修订（2026-09-10）：\n"
            "   - 工具栏重排：锁定 · 目录隐藏/显示 · 无类条目 · 常用 · 新建 · 导入 · 导出 · 设置 · 搜索(🔍)\n"
            "   - 🗂 目录隐藏/目录显示：可任选隐藏各分类列（含“全部隐藏/全部显示”），\n"
            "     底部小图标 🗂 一键全隐/全显；窗口最小宽度随之动态调整\n"
            "   - ✏️ 编辑 / 👁 浏览 切换：浏览态详情只读、可选中复制，防误改\n"
            "   - 搜索防抖：输入停顿约 1.5 秒后才查询（点 🔍 或回车可立即搜索）\n"
            "   - 条目列悬停“条目名称一览”浮层：可移入浮层滚动，滚轮与条目区同步\n"
            "   - 详情区 ②~⑦ 整组默认折叠为 1 行；命令条与底部栏按钮重排、宽度贴合文字\n"
            "· 打包版内置精简模板（示例提示词，以实际打包库为准）\n"
            "──────────────────────────\n"
            f"{run_desc}\n"
            f"{path_desc}\n"
            "提示词长度：中文提示词不设固定字符上限——数据库为 SQLite TEXT 类型，\n"
            "             单字段可存上亿字符，超出文本框高度时自动展开/滚动查看\n"
            "──────────────────────────\n"
            "技术栈：Python 3.12 · CustomTkinter · SQLite\n"
            "数据文件：data/prompts.db（随软件目录整体迁移即可换机使用）\n"
            "开源仓库：https://github.com/yihufree/PromptSprite\n"
            "（MIT License，欢迎 Star / Issue）\n"
            "© 2026 PromptSprite 开发组 · 仅供学习与个人使用"
        )
        # 2026-09-12（V1.9.0）：内容区改为"只读文本框 + 原生滚动"，高度取"内容所需"与
        # "屏幕 68%"的较小值，确保小屏（如 1366×768）上"关闭"按钮也不会被挤出屏幕。
        try:
            import tkinter.font as tkfont
            _f = tkfont.Font(root=win, font=("Microsoft YaHei", 13))
            _line_h = _f.metrics("linespace") or 22
            _need_h = (info.count("\n") + 1) * _line_h + 20
            _max_w = max((_f.measure(_ln) for _ln in info.split("\n")), default=420)
        except Exception:
            _need_h, _max_w = 560, 620
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        box = ctk.CTkTextbox(win, width=200, height=200, wrap="word",
                             fg_color="transparent", border_width=0,
                             font=("Microsoft YaHei", 13))
        # CTk 会按系统 DPI 缩放控件尺寸，故按控件实际缩放把"像素目标"换算成控件单位，
        # 否则高 DPI 下高度会被放大、仍可能超出屏幕。
        # 2026-09-17（R-6）：改经 ui_common.widget_scaling 访问（CTk 私有 API 单一入口）。
        scale = _ui_common.widget_scaling(box)
        want_w = min(_max_w + 70, int(sw * 0.92), 780)
        want_h = max(min(_need_h, int(sh * 0.68)), 200)
        box.configure(width=want_w / scale, height=want_h / scale)
        box.pack(padx=10, pady=(12, 2))
        box.insert("1.0", info)
        box.configure(state="disabled")
        ctk.CTkButton(win, text="关闭", width=88,
                      command=win.destroy).pack(pady=(6, 14))
        win.update_idletasks()
        ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        # 2026-09-12（用户要求）：初始位置改为"窗口上边距屏幕顶端 100 像素"（原为按父窗口垂直居中）；
        # 横向仍在父窗口（设置窗口）内居中；极端小屏时兜底不越出屏幕下边。
        x = self.winfo_x() + (self.winfo_width() - ww) // 2
        y = 100
        if y + wh > sh - 8:
            y = max(sh - wh - 8, 0)
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.lift()

    def _on_remember_toggle(self) -> None:
        # 打开"记住窗口大小"时，立即记录当前窗口大小
        if self.sw_size.get() == 1:
            self.db.set_meta(config.META_WINDOW_SIZE, self.master._current_size())

    def _apply(self) -> None:
        self.db.set_meta(config.META_REMEMBER_SIZE, "1" if self.sw_size.get() == 1 else "0")
        view = "card" if self.seg_view.get() == "卡片" else "list"
        self.db.set_meta(config.META_VIEW_MODE, view)
        # 2026-09-16（批次 11-7，用户要求 3）：条目排序方式
        self.db.set_meta(config.META_ENTRY_SORT,
                         {"修改时间": "updated", "添加时间": "created",
                          "名称": "name"}.get(self.seg_sort.get(), "updated"))
        _rmap = {"自动": config.DETAIL_MODE_AUTO, "全部显示": config.DETAIL_MODE_FULL,
                 "精简": config.DETAIL_MODE_COMPACT}
        self.db.set_meta(config.META_DETAIL_MODE, _rmap.get(self.seg_detail.get(),
                                                           config.DETAIL_MODE_AUTO))
        # 电脑代号（增量备份区分多机，2026-08-29 M4）
        code = self.entry_code.get().strip()
        if code:
            self.db.set_meta(config.META_COMPUTER_CODE, code)
        # 变更包保留天数（2026-08-29 M4；2026-09-08 V1.7.0 更名）
        keep = self.entry_keep.get().strip()
        if keep.isdigit() and int(keep) >= 1:
            self.db.set_meta(config.META_INCR_KEEP_DAYS, str(int(keep)))
        # 自动全量备份保留份数（2026-09-08 V1.7.0）
        bkeep = self.entry_bkeep.get().strip()
        if bkeep.isdigit() and int(bkeep) >= 1:
            self.db.set_meta(config.META_BACKUP_KEEP, str(int(bkeep)))
        # 变更包携带被删快照（2026-09-08 V1.7.0；默认关）
        self.db.set_meta(config.META_CHANGE_PACK_SNAPSHOT,
                         "1" if self.sw_snapshot.get() else "0")
        # 在条目处显示标签（2026-09-13 1-C-4b；默认关）
        self.db.set_meta(config.META_SHOW_TAGS_IN_LIST,
                         "1" if self.sw_show_tags.get() else "0")
        # 录入时"输入停止后自动推荐标签"（2026-09-14 阶段 3；默认关）
        self.db.set_meta(config.META_AUTO_TAG_SUGGEST,
                         "1" if self.sw_auto_tag.get() else "0")
        # 2026-09-16（批次 12-2，用户要求 2）：取词是否用于"批量打标 / 离线打标"
        #   （与「批量智能自动打标」对话框共用同一 meta 键）
        self.db.set_meta(config.META_FALLBACK_BATCH,
                         "1" if self.sw_fb_batch.get() else "0")
        # 2026-09-16（批次 12-3，用户要求 1）：智能自动取词——开关与两个阈值
        #   （先读回 meta 里的既有值再覆盖，避免把"含 T2 采集 / 上限"等本页不展示的项清掉）
        _aw = auto_words.load_cfg(self.db)
        _aw["enabled"] = bool(self.sw_aw.get())
        for _k, _ent in (("hot_th", self.ent_aw_hot), ("tag_th", self.ent_aw_tag)):
            _v = (self.ent_aw_hot.get() if _k == "hot_th" else self.ent_aw_tag.get()) or ""
            _v = str(_v).strip()
            if _v.isdigit() and int(_v) >= 1:
                _aw[_k] = int(_v)
        try:
            auto_words.save_cfg(self.db, _aw)
        except Exception:
            pass
        # 界面外观（2026-09-15，批次 4）：6 组字体/字号/颜色 → 统一交给数据层校验后写 meta
        #   （"默认（不改）"→ 不写入该项；非法/越界值由 ui_appearance.normalize 丢弃）
        _look = {}
        for _g, _w in (self._look_widgets or {}).items():
            _item = {}
            _fam = _w["family"].get()
            if _fam and _fam != self._look_default:
                _item["family"] = _fam
            _sz = _w["size"].get()
            if _sz and str(_sz).isdigit():
                _item["size"] = int(_sz)
            for _ck in ("fg", "bg"):
                if _ck in _w:
                    _v = (self._look_color_map or {}).get(_w[_ck].get())
                    if _v:
                        _item[_ck] = _v
            _look[_g] = _item
        ui_appearance.save(self.db, _look)
        # 2026-09-16（批次 11-6，用户确认问题 2）：标签推荐策略与顺序（顺序 + 来源开关）
        #   先快照当前勾选状态（↑/↓ 重建后仍以界面为准），再交给数据层归一化写 meta。
        self._pol_snapshot()
        try:
            tagger.save_policy(self.db, {"order": list(self._policy_order),
                                         "enabled": dict(self._policy_enabled)})
        except Exception:
            pass
        # 2026-09-17（需求 S-2）：全局热键——规范化后写 meta，并**立即重新注册**。
        #   空输入视作"恢复默认"；非法输入保留原值并提示（不写脏值）。
        _hk_txt = (self.entry_hotkey.get() or "").strip()
        _hk_new = config.GLOBAL_HOTKEY if not _hk_txt else hotkey.normalize_hotkey(_hk_txt)
        _hk_msg = ""
        if not _hk_new:
            _hk_msg = "⚠ 热键格式不合法（需含修饰键，如 ctrl+shift+p），已保留原值"
        else:
            self.db.set_meta(config.META_HOTKEY, _hk_new)
            if _hk_new != self._hotkey:
                _hk_ok = False
                _fn = getattr(self.master, "apply_hotkey_combo", None)
                if callable(_fn):
                    try:
                        _hk_ok = bool(_fn(_hk_new))
                    except Exception:
                        _hk_ok = False
                self._hotkey = _hk_new
                _hk_msg = (("✅ 全局热键已改为 %s" % _hk_new) if _hk_ok else
                           ("⚠ 热键 %s 注册失败（可能被其它软件占用或缺少管理员权限）；"
                            "设置已保存，下次启动会再试一次" % _hk_new))
        if hasattr(self.master, "apply_settings"):
            self.master.apply_settings()
        if _hk_msg:
            try:
                self.master.toast(_hk_msg)
            except Exception:
                pass
        self.destroy()

    def _center(self) -> None:
        """设置窗口初始位置（2026-09-12 用户要求；2026-09-16 批次 12-3 改为按**屏幕工作区**）。

        横向仍在父窗口（主窗口）内居中；**窗口上边固定距屏幕顶端 100 像素**；
        2026-09-16 修正：原先兜底用**整屏高度**（未扣任务栏），在 1366×768 上会把窗口底部的
        「确定 / 取消」按到任务栏之下；现改用 `main_window.work_area()` 的工作区底边夹取。
        """
        self.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() - self.winfo_width()) // 2
        y = 100
        try:
            from .main_window import work_area      # 函数内延迟导入：避免模块级循环依赖
            _wl, _wt, _wr, _wb = work_area(self)
        except Exception:
            _wl, _wt, _wr, _wb = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
        if x + self.winfo_width() > _wr - 8:
            x = max(_wr - self.winfo_width() - 8, _wl + 8)
        if y + self.winfo_height() > _wb - 8:
            y = max(_wb - self.winfo_height() - 8, _wt + 8)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()
