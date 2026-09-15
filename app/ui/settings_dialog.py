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

from .. import config
from .. import tagger  # 2026-09-14（阶段 0.5）：词表管理入口的状态摘要
from .. import ui_appearance  # 2026-09-15（批次 4）：界面外观（字体/字号/颜色）数据层


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
        self._detail_mode = db.get_meta(config.META_DETAIL_MODE) or config.DETAIL_MODE_AUTO
        self._computer_code = db.get_meta(config.META_COMPUTER_CODE) or ""
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

        self._build()
        self._center()

    def _build(self) -> None:
        """分页：【界面】/【外观】/【数据与备份】/【标签与词表】/【关于】。
        （2026-09-15 批次 4 新增【外观】页：字体/字号/颜色，6 个元素。）
        **只重排布局**——所有 meta 键、控件属性名与 `_apply()` 的读写逻辑**完全不变**。
        """
        pad = 16
        tab = ctk.CTkTabview(self, width=580, height=410)
        tab.pack(padx=12, pady=(12, 2), fill="both", expand=True)
        pg_ui = tab.add("界面")
        pg_look = tab.add("外观")            # 2026-09-15（批次 4）
        pg_data = tab.add("数据与备份")
        pg_tag = tab.add("标签与词表")
        pg_about = tab.add("关于")
        for _pg in (pg_ui, pg_look, pg_data, pg_tag, pg_about):
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
                       "  但可在导入时使用“⑤ 逆向恢复”找回被删数据。", 6, color="#D9534F")

        # ---------------- 第 4 页：标签与词表（三个管理入口） ----------------
        _lab(pg_tag, "标签词表", 0)
        self._mgr_btn_dict = ctk.CTkButton(pg_tag, text="📚 词表管理…", width=150,
                                           fg_color="#7A4FBF",
                                           command=self._open_dict_manager)
        self._mgr_btn_dict.grid(row=0, column=1, padx=pad, pady=8, sticky="w")
        _dict_line = "读取失败"
        try:
            _s = tagger.dict_summary(tagger.load_dict(self.db))
            _dict_line = (f"当前 {_s.get('标签总数', 0)} 个标签 · "
                          f"{len(_s.get('领域包') or {})} 个领域包"
                          f"（{'、'.join((_s.get('领域包') or {}).keys())}）")
        except Exception:
            pass
        _hint(pg_tag, f"{_dict_line}。可导出 JSON 编辑后再导入"
                      "（新增「学术」等大类即加一个领域包）", 1)

        _lab(pg_tag, "热点词表", 2)
        self._mgr_btn_hot = ctk.CTkButton(pg_tag, text="🏷 热点词管理…", width=150,
                                         fg_color="#7A4FBF",
                                         command=self._open_hotword_manager)
        self._mgr_btn_hot.grid(row=2, column=1, padx=pad, pady=8, sticky="w")
        try:
            _hot_n = self.db.count_hotwords()
        except Exception:
            _hot_n = 0
        _hint(pg_tag, f"当前 {_hot_n} 个词条。供「自动打标」使用（初始为空；"
                      "可手动添加 / 文本导入 / 从来源网址抓取）", 3)

        _lab(pg_tag, "字段管理", 4)
        self._mgr_btn_field = ctk.CTkButton(pg_tag, text="🔧 字段管理…", width=150,
                                           fg_color="#7A4FBF",
                                           command=self._open_field_manager)
        self._mgr_btn_field.grid(row=4, column=1, padx=pad, pady=8, sticky="w")
        _hint(pg_tag, "内置 10 个字段可改名（不可删）；自定义字段可增 / 删 / 改名 / 排序 / 改类型。\n"
                      "另一入口在详情区右上角的 🔧 按钮。", 5)

        _lab(pg_tag, "批量打标", 6)
        self._mgr_btn_batch = ctk.CTkButton(pg_tag, text="🤖 批量智能打标…", width=150,
                                           fg_color="#7A4FBF",
                                           command=self._open_batch_tag)
        self._mgr_btn_batch.grid(row=6, column=1, padx=pad, pady=8, sticky="w")
        _hint(pg_tag, "为「无标签的条目」或「全部条目」批量自动打标：强制先备份、先预演、二次确认，\n"
                      "执行后可精确撤销。另一入口在标签页面底部。", 7)
        # 2026-09-15（批次 6-2，用户选"锁定态允许打开设置、管理入口置灰"）：
        #   本页 4 个管理入口都会**改数据** ⇒ 锁定态一律置灰；**设置对话框本身仍可打开**
        #   （窗口/视图/外观等纯偏好不受影响）。锁定状态由主窗口持有，故从 master 读取。
        _locked = bool(getattr(self.master, "_lock_on", False))
        for _b in (self._mgr_btn_dict, self._mgr_btn_hot,
                   self._mgr_btn_field, self._mgr_btn_batch):
            try:
                _b.configure(state=("disabled" if _locked else "normal"))
            except Exception:
                pass
        if _locked:
            _hint(pg_tag, "⚠ 已锁定：以上 4 个管理入口暂不可用（解锁后即可使用）", 8, color="#C77700")

        # ---------------- 第 5 页：关于 ----------------
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
        ctk.CTkButton(pg_about, text="ℹ 查看完整说明（功能亮点 / 各版本修订 / 运行信息）…",
                      width=360, fg_color="#8a94a6", command=self._show_about
                      ).grid(row=4, column=0, columnspan=2, padx=pad, pady=(10, 6), sticky="w")

        # ---------------- 底部固定按钮 ----------------
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(2, 12))
        ctk.CTkButton(btn_row, text="确定", width=110, fg_color="#2E8B57",
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
            f"版本 V{config.APP_VERSION}（2026-09-14 智能标签/图集/向导/联网抓取版）\n"
            "──────────────────────────\n"
            "本地优先的 AI 提示词管理工具：数据 100% 存在本机。\n"
            "· 联网说明：仅在你主动使用向导的「从网址获取」或「热点词更新」时才会访问网络"
            "（只读抓取你填写的网址，不后台访问、不写盘、不留登录态）；其余功能全程离线。\n"
            "· 五级分类书架（项目类别 / 根目录 / 一级分类 / 二级分类 / 条目）\n"
            "· 标签系统（详情区打标签；标签页面「标签云 / 列表＋计数」双呈现，且/或组合过滤）\n"
            "· 智能标签：内置库已预置标签（开箱即用）；「批量智能自动打标」可对无标签/全部条目批量打标\n"
            "  （强制先备份 + 先预演 + 二次确认，可精确撤销）；标签词表与热点词表均可在软件内维护\n"
            "· 条目图集（封面 + 多图；本地图与外链图，可下载外链图到本地）\n"
            "· 网上资源结构化向导（粘贴 / 文件 / 网址抓取 → 四层映射 → 质量自检 → 先存后导）\n"
            f"{data_line}"
            "· 条目多位置：关联到（一处编辑各处同步）/ 复制到（独立副本）/ 移动到（可整体转移）\n"
            "· 条目就地新增醒目入口；任一分级分类可持有并显示本级条目\n"
            "· 详情区多位置提示；删除分类三级保护（级联删除需输入确认短语）\n"
            "· 每日变更包（按电脑代号区分，可换机合并导入）；纯删除包有醒目标识、可逆向恢复\n"
            "· 导入向导：新增 / 删除分两维选择（忽略删除 / 应用删除 / 逆向恢复），导入前自动快照\n"
            "· 一键复制 / 新建 / 全局热键 Ctrl+Shift+P 唤起；自动全量备份（按天去重、默认保留 30 份）\n"
            "· JSON / Excel / Markdown / HTML 多格式导入导出；可与备份库数据比对\n"
            "· 老版本数据自动 / 引导迁移（未明确分类兜底）\n"
            "──────────────────────────\n"
            "V2.1.0 智能标签升级（2026-09-14）：\n"
            "   - 智能标签体系：出厂词表 =「通用骨架（领域 / 用途）」+「领域维度包（视觉 / 文学 / 编程）」\n"
            "     （共 135 个标签，结构可扩展）；打标三源优先级：来源自带标签 → 分类名映射 → 词表匹配\n"
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
        try:
            scale = float(box._get_widget_scaling()) or 1.0
        except Exception:
            scale = 1.0
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
        if hasattr(self.master, "apply_settings"):
            self.master.apply_settings()
        self.destroy()

    def _center(self) -> None:
        """设置窗口初始位置（2026-09-12 用户要求）。

        横向仍在父窗口（主窗口）内居中；**窗口上边固定距屏幕顶端 100 像素**（原为按父窗口
        垂直居中）；并兜底不越出屏幕下边。
        """
        self.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() - self.winfo_width()) // 2
        y = 100
        sh = self.winfo_screenheight()
        if y + self.winfo_height() > sh - 8:
            y = max(sh - self.winfo_height() - 8, 0)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()
