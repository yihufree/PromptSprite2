# -*- coding: utf-8 -*-
"""auto_words_dialog.py - 「智能自动取词词库」管理对话框（2026-09-16 批次 12-3）

对应《用户要求 1》：软件使用中自动沉淀"未命中词表/热点词"的新词并统计频次；
- 达「热点词阈值」→ 词库已**自动**把它加入热点词表（状态"已升热点词"）；
- 达「词表阈值」→ 在本窗口**提示并交由你审核**：选择领域与维度后确认，才写入词表标签。

本窗口提供：词库列表（词 / 不同条目数 / 命中次数 / 来源字段 / 末现时间 / 状态）、
按状态与词搜索、批量操作（加入热点词 / 忽略 / 删除 / 一键处理全部达标项），
以及采集范围的补充设置（是否计入 T2 自动推荐、词库条数上限）。

设计：纯 UI，不直接改词表实现——加词表走 `auto_words.promote_tag()`（内部调 tagger.save_dict）。
"""
import customtkinter as ctk

from .. import auto_words, tagger
from .ui_common import C_OK as _C_OK, C_TAG as _C_TAG, C_DANGER as _C_DANGER, C_WARN as _C_WARN  # 2026-09-17（U-2）：主色常量

_STATE_TXT = {"new": "候选中", "hot": "已升热点词", "tag": "已入词表", "ignored": "已忽略"}
_STATE_CLR = {"new": "#25639c", "hot": _C_OK, "tag": _C_TAG, "ignored": "#9aa4b1"}


class AutoWordsDialog(ctk.CTkToplevel):
    """自动取词词库：查看 / 审核 / 提升 / 清理。"""

    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.master = master
        self.title("🧠 智能自动取词词库")
        self.transient(master)
        self.grab_set()
        try:
            _sh = self.winfo_screenheight()
        except Exception:
            _sh = 900
        self.geometry("900x%d" % max(560, min(720, int(_sh * 0.82))))
        try:
            self.minsize(820, 560)
        except Exception:
            pass
        self._rows = []
        self._checked = {}
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
        ctk.CTkLabel(top, text="说明：达「热点词阈值」的词已自动加入热点词表；"
                               "达「词表阈值」的词请在右侧选择领域 / 维度后确认加入词表。",
                     font=("Microsoft YaHei", 11), text_color="#5b6b7c", anchor="w",
                     justify="left").pack(side="left")

        # 采集设置（补充项；阈值本身在「设置 → 标签与词表」页，两处读写同一 meta）
        cfg = auto_words.load_cfg(self.db)
        srow = ctk.CTkFrame(self, fg_color="transparent")
        srow.pack(fill="x", padx=pad, pady=(4, 2))
        self.var_inc_t2 = ctk.BooleanVar(value=bool(cfg.get("include_t2")))
        ctk.CTkCheckBox(srow, text="采集范围含 T2 自动推荐", variable=self.var_inc_t2,
                        font=("Microsoft YaHei", 11),
                        command=self._save_cfg).pack(side="left")
        ctk.CTkLabel(srow, text="　词库上限", font=("Microsoft YaHei", 11)).pack(side="left")
        self.ent_cap = ctk.CTkEntry(srow, width=80, font=("Microsoft YaHei", 11))
        self.ent_cap.insert(0, str(cfg.get("cap")))
        self.ent_cap.pack(side="left", padx=(4, 12))
        ctk.CTkLabel(srow, text="热点阈值", font=("Microsoft YaHei", 11)).pack(side="left")
        self.ent_hot = ctk.CTkEntry(srow, width=60, font=("Microsoft YaHei", 11))
        self.ent_hot.insert(0, str(cfg.get("hot_th")))
        self.ent_hot.pack(side="left", padx=(4, 12))
        ctk.CTkLabel(srow, text="词表阈值", font=("Microsoft YaHei", 11)).pack(side="left")
        self.ent_tag = ctk.CTkEntry(srow, width=60, font=("Microsoft YaHei", 11))
        self.ent_tag.insert(0, str(cfg.get("tag_th")))
        self.ent_tag.pack(side="left", padx=(4, 12))
        ctk.CTkButton(srow, text="保存设置", width=90, font=("Microsoft YaHei", 11),
                      fg_color="#8a94a6", command=self._save_cfg).pack(side="left")

        # 过滤 + 批量操作
        brow = ctk.CTkFrame(self, fg_color="transparent")
        brow.pack(fill="x", padx=pad, pady=(4, 2))
        ctk.CTkLabel(brow, text="显示", font=("Microsoft YaHei", 11)).pack(side="left")
        self.om_state = ctk.CTkOptionMenu(
            brow, width=140, font=("Microsoft YaHei", 11),
            values=["全部", "仅候选中", "仅已达词表阈值", "已升热点词", "已入词表", "已忽略"],
            command=lambda _v: self._render())
        self.om_state.set("全部")
        self.om_state.pack(side="left", padx=(4, 10))
        self.ent_search = ctk.CTkEntry(brow, width=140, placeholder_text="搜索词…",
                                       font=("Microsoft YaHei", 11))
        self.ent_search.pack(side="left", padx=(0, 10))
        self.ent_search.bind("<KeyRelease>", lambda _e=None: self._render())
        ctk.CTkButton(brow, text="☑ 全选可见", width=100, font=("Microsoft YaHei", 11),
                      fg_color="#8a94a6", command=lambda: self._check_all(True)
                      ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(brow, text="☐ 全不选", width=90, font=("Microsoft YaHei", 11),
                      fg_color="#8a94a6", command=lambda: self._check_all(False)
                      ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(brow, text="🗑 清空词库", width=110, font=("Microsoft YaHei", 11),
                      fg_color=_C_DANGER, command=self._clear_all).pack(side="right")

        arow = ctk.CTkFrame(self, fg_color="transparent")
        arow.pack(fill="x", padx=pad, pady=(2, 6))
        for _t, _fg, _fn in (
                ("🔥 加入热点词表", _C_WARN, self._do_hot),
                ("📚 加入词表标签…", _C_TAG, self._do_tag),
                ("🚫 忽略", "#8a94a6", self._do_ignore),
                ("🗑 从词库删除", _C_DANGER, self._do_remove),
                ("⚡ 一键处理全部达标项", _C_OK, self._do_all_ready)):
            ctk.CTkButton(arow, text=_t, width=150, font=("Microsoft YaHei", 11),
                          fg_color=_fg, command=_fn).pack(side="left", padx=(0, 6))
        # 2026-09-17（F-2 修复）：勾选数改用**独立标签**显示。
        #   原实现把"已勾选 N 条"拼进 status 文本、刷新时再用 split 裁掉，
        #   既依赖全角分隔符字面量，又会被 _tip() 的提示语覆盖。现各管各的。
        self.sel_label = ctk.CTkLabel(arow, text="", font=("Microsoft YaHei", 11),
                                      text_color="#25639c")
        self.sel_label.pack(side="right", padx=(6, 0))

        # 列表（滚动）
        head = ctk.CTkFrame(self, fg_color="#eaf1f9")
        head.pack(fill="x", padx=pad, pady=(2, 0))
        for _txt, _w in (("选", 40), ("词", 190), ("不同条目", 80), ("命中", 60),
                         ("来源字段", 110), ("末现时间", 150), ("状态", 110)):
            ctk.CTkLabel(head, text=_txt, width=_w, font=("Microsoft YaHei", 11, "bold"),
                         text_color="#5a6f88", anchor="w").pack(side="left", padx=4, pady=4)
        self.box = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.box.pack(fill="both", expand=True, padx=pad, pady=(0, 4))
        self._render()

    # ------------------------------------------------------------------ #
    # 数据 → 界面
    # ------------------------------------------------------------------ #
    def _visible(self, cfg=None) -> list:
        """按当前筛选/搜索返回可见词条。
        2026-09-17（B-5 修复）：`cfg` 由调用方传入（一次解析、复用），避免重复 JSON 解析。
        """
        rows = auto_words.list_words(self.db, "freq")
        st = self.om_state.get()
        kw = (self.ent_search.get() or "").strip().lower()
        _cfg = cfg if isinstance(cfg, dict) else auto_words.load_cfg(self.db)
        _tth = int(_cfg.get("tag_th") or 3)
        out = []
        for r in rows:
            if kw and kw not in r["word"].lower():
                continue
            if st == "仅候选中" and r["state"] != "new":
                continue
            if st == "仅已达词表阈值" and not (r["e"] >= _tth and r["state"] in ("new", "hot")):
                continue
            if st == "已升热点词" and r["state"] != "hot":
                continue
            if st == "已入词表" and r["state"] != "tag":
                continue
            if st == "已忽略" and r["state"] != "ignored":
                continue
            out.append(r)
        return out

    def _render(self) -> None:
        for w in self.box.winfo_children():
            w.destroy()
        # 2026-09-17（B-5）：设置只解析一次，供 _visible 与本方法共用（原先各解析一次）
        cfg = auto_words.load_cfg(self.db)
        self._rows = self._visible(cfg)
        self._checked = {}
        _tth = int(cfg.get("tag_th") or 3)
        if not self._rows:
            ctk.CTkLabel(self.box, text="（词库为空，或当前筛选无结果）", text_color="#9aa4b1",
                         font=("Microsoft YaHei", 11)).pack(anchor="w", padx=8, pady=10)
        for r in self._rows:
            row = ctk.CTkFrame(self.box, fg_color="transparent")
            row.pack(fill="x", pady=1)
            var = ctk.BooleanVar(value=False)
            self._checked[r["word"]] = var
            _ready = r["e"] >= _tth and r["state"] in ("new", "hot")
            ctk.CTkCheckBox(row, text="", variable=var, width=40, checkbox_width=18,
                            checkbox_height=18).pack(side="left", padx=(4, 4))
            ctk.CTkLabel(row, text=r["word"], width=190, font=("Microsoft YaHei", 11),
                         anchor="w", text_color=(_C_DANGER if _ready else None)
                         ).pack(side="left", padx=4)
            ctk.CTkLabel(row, text=str(r["e"]), width=80, font=("Microsoft YaHei", 11),
                         anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=str(r["n"]), width=60, font=("Microsoft YaHei", 11),
                         anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=r["src"] or "-", width=110, font=("Microsoft YaHei", 11),
                         anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=r["last"] or "-", width=150, font=("Microsoft YaHei", 11),
                         anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=_STATE_TXT.get(r["state"], r["state"]) + ("　⚠待审核" if _ready else ""),
                         width=150, font=("Microsoft YaHei", 11), anchor="w",
                         text_color=_STATE_CLR.get(r["state"], "#5b6b7c")).pack(side="left", padx=4)
        _st = auto_words.stats(self.db)
        self.status.configure(
            text="词库共 %d 条　|　已达热点阈值待自动提升 %d 条　|　已达词表阈值待审核 %d 条　|　"
                 "已升热点词 %d 条　|　已入词表 %d 条"
                 % (_st["total"], _st["ready_hot"], _st["ready_tag"],
                    _st["done_hot"], _st["done_tag"]),
            text_color="#5b6b7c")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        """把"当前勾选数"写到**独立标签**（2026-09-17 F-2 修复）。

        原实现把"已勾选 N 条"拼进 status 文本、刷新时用 `split("　|　已勾选")` 裁掉：
        既依赖全角分隔符字面量（脆弱），又会被 `_tip()` 写入的提示语覆盖/丢失。
        现改为各管各的：本方法只动 `sel_label`，状态行只由 `_render()` / `_tip()` 负责。
        """
        _n = sum(1 for v in self._checked.values() if v.get())
        try:
            self.sel_label.configure(text=("已勾选 %d 条" % _n) if _n else "")
        except Exception:
            pass

    def _check_all(self, val: bool) -> None:
        for v in self._checked.values():
            v.set(bool(val))
        self._refresh_buttons()

    def _selected(self) -> list:
        return [w for w, v in self._checked.items() if v.get()]

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #
    def _save_cfg(self) -> None:
        """保存采集设置（2026-09-17 F-1 修复）。

        原实现对"词库上限 / 热点阈值 / 词表阈值"的非法输入**静默丢弃**（不写入也不提示），
        界面又保留着刚才输入的脏值 ⇒ 用户会误以为已生效。现改为：
        ① 合法值写入；② 非法值**保留原值并提示是哪几项**；③ 保存后把三个输入框
        **回填为实际生效值**，保证"看到的＝真实设置"。
        """
        cfg = auto_words.load_cfg(self.db)
        cfg["include_t2"] = bool(self.var_inc_t2.get())
        _bad = []
        for _k, _ent, _lbl in (("cap", self.ent_cap, "词库上限"),
                               ("hot_th", self.ent_hot, "热点阈值"),
                               ("tag_th", self.ent_tag, "词表阈值")):
            _v = (self.ent_edit_text(_ent) or "").strip()
            if _v.isdigit() and int(_v) >= 1:
                cfg[_k] = int(_v)
            else:
                cfg[_k] = int(cfg.get(_k) or auto_words.DEFAULT_CFG.get(_k, 1))
                _bad.append(_lbl)
        _saved = auto_words.save_cfg(self.db, cfg)
        for _ent, _k in ((self.ent_cap, "cap"), (self.ent_hot, "hot_th"),
                         (self.ent_tag, "tag_th")):
            self._fill_entry(_ent, _saved.get(_k))
        self._render()
        if _bad:
            self._tip("⚠ %s 需为 ≥1 的整数，已保留原值" % "、".join(_bad), warn=True)
        else:
            self._tip("✅ 设置已保存")

    @staticmethod
    def _fill_entry(ent, value) -> None:
        """把输入框内容覆盖为"实际生效值"（2026-09-17 F-1）。"""
        try:
            ent.delete(0, "end")
            ent.insert(0, str(value if value is not None else ""))
        except Exception:
            pass

    @staticmethod
    def ent_edit_text(ent) -> str:
        """CTkEntry 取文本（统一入口，便于日后替换控件）。"""
        try:
            return ent.get()
        except Exception:
            return ""

    def _do_hot(self) -> None:
        _ws = self._selected()
        if not _ws:
            self._tip("请先勾选要处理的词")
            return
        _n = auto_words.promote_hot(self.db, _ws)
        self._render()
        self._tip("✅ 已把 %d 个词加入热点词表" % _n)

    def _do_ignore(self) -> None:
        _ws = self._selected()
        if not _ws:
            self._tip("请先勾选要处理的词")
            return
        _n = auto_words.ignore(self.db, _ws)
        self._render()
        self._tip("✅ 已忽略 %d 个词（不再提示提升）" % _n)

    def _do_remove(self) -> None:
        _ws = self._selected()
        if not _ws:
            self._tip("请先勾选要处理的词")
            return
        _n = auto_words.remove(self.db, _ws)
        self._render()
        self._tip("✅ 已从词库删除 %d 个词" % _n)

    def _do_tag(self) -> None:
        """加入词表标签：需用户选择「领域 → 维度 → 标签名」（可批量，共用同一归属）。"""
        _ws = self._selected()
        if not _ws:
            self._tip("请先勾选要加入词表的词")
            return
        try:
            data = tagger.load_dict(self.db)
        except Exception as exc:
            self._tip("⚠ 词表读取失败：%s" % exc)
            return
        _doms = ["通用骨架"] + list((data.get("domains") or {}).keys())
        _dlg = _TagTargetDialog(self, self.db, _ws, _doms, data)
        self.wait_window(_dlg)
        if getattr(_dlg, "result", None):
            self._render()
            self._tip(_dlg.result)

    def _do_all_ready(self) -> None:
        """一键处理全部达标项：热点阈值达标的加入热点词表；词表阈值达标的**只提示**（需人工选归属）。"""
        cfg = auto_words.load_cfg(self.db)
        _tth = int(cfg.get("tag_th") or 3)
        _ready_hot = [r["word"] for r in auto_words.list_words(self.db)
                      if r["state"] == "new" and r["e"] >= int(cfg.get("hot_th") or 3)]
        _ready_tag = [r["word"] for r in auto_words.list_words(self.db)
                      if r["state"] in ("new", "hot") and r["e"] >= _tth]
        _n = auto_words.promote_hot(self.db, _ready_hot) if _ready_hot else 0
        self._render()
        self._tip("✅ 已把 %d 个达标词加入热点词表；另有 %d 个词已达词表阈值，"
                  "请勾选后点「📚 加入词表标签…」逐个确认归属" % (_n, len(_ready_tag)))

    def _clear_all(self) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno("清空自动取词词库",
                                   "确定清空整个自动取词词库吗？\n"
                                   "（已加入热点词 / 词表的词**不受影响**；清空后新词会重新累计）",
                                   parent=self):
            return
        auto_words.clear(self.db)
        self._render()
        self._tip("✅ 已清空自动取词词库")

    def _tip(self, text: str, warn: bool = False) -> None:
        """状态行提示（2026-09-17 F-1：新增 `warn`——警示用橙、成功用绿）。"""
        try:
            self.status.configure(text=text,
                                  text_color=("#C77700" if warn else _C_OK))
        except Exception:
            pass


class _TagTargetDialog(ctk.CTkToplevel):
    """选择「领域 → 维度 → 标签名」把勾选的词写入词表（2026-09-16 批次 12-3）。"""

    def __init__(self, master, db, words, domains, dict_data):
        super().__init__(master)
        self.db = db
        self.words = list(words)
        self.dict_data = dict_data
        self.result = None
        self.title("📚 加入词表标签")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        pad = 14
        ctk.CTkLabel(self, text="将 %d 个词写入词表：%s" % (
            len(self.words), "、".join(self.words[:6]) + ("…" if len(self.words) > 6 else "")),
            font=("Microsoft YaHei", 11), anchor="w", justify="left", wraplength=460
        ).pack(anchor="w", padx=pad, pady=(12, 6))

        r1 = ctk.CTkFrame(self, fg_color="transparent")
        r1.pack(fill="x", padx=pad, pady=4)
        ctk.CTkLabel(r1, text="领域", width=60, font=("Microsoft YaHei", 12)).pack(side="left")
        self.om_dom = ctk.CTkOptionMenu(r1, width=180, values=domains,
                                        command=lambda _v: self._fill_dims(),
                                        font=("Microsoft YaHei", 12))
        self.om_dom.set(domains[0] if domains else "通用骨架")
        self.om_dom.pack(side="left")

        r2 = ctk.CTkFrame(self, fg_color="transparent")
        r2.pack(fill="x", padx=pad, pady=4)
        ctk.CTkLabel(r2, text="维度", width=60, font=("Microsoft YaHei", 12)).pack(side="left")
        self.ent_dim = ctk.CTkEntry(r2, width=180, font=("Microsoft YaHei", 12))
        self.ent_dim.pack(side="left")
        ctk.CTkLabel(r2, text="（可直接输入新维度名）", font=("Microsoft YaHei", 10),
                     text_color="#9aa4b1").pack(side="left", padx=(6, 0))

        r3 = ctk.CTkFrame(self, fg_color="transparent")
        r3.pack(fill="x", padx=pad, pady=4)
        ctk.CTkLabel(r3, text="标签名", width=60, font=("Microsoft YaHei", 12)).pack(side="left")
        self.ent_tag = ctk.CTkEntry(r3, width=180, font=("Microsoft YaHei", 12))
        self.ent_tag.pack(side="left")
        ctk.CTkLabel(r3, text="（默认＝每个词各自成为标签；填了则全部并入该标签的匹配词）",
                     font=("Microsoft YaHei", 10), text_color="#9aa4b1").pack(side="left", padx=(6, 0))

        self.lbl = ctk.CTkLabel(self, text="", font=("Microsoft YaHei", 10),
                                text_color="#9aa4b1", anchor="w")
        self.lbl.pack(fill="x", padx=pad, pady=(2, 6))
        self._fill_dims()

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=pad, pady=(0, 12))
        ctk.CTkButton(bar, text="确定写入", width=110, fg_color=_C_OK,
                      command=self._ok).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bar, text="取消", width=90, command=self.destroy).pack(side="right")

    def _fill_dims(self) -> None:
        """把选定领域的**现有维度名**提示出来供参考（维度仍可直接输入新名）。"""
        dom = self.om_dom.get()
        layer = (self.dict_data.get("universal") or {}) if dom == "通用骨架" \
            else ((self.dict_data.get("domains") or {}).get(dom) or {})
        dims = [d for d in layer.keys()]
        if dims and not (self.ent_dim.get() or "").strip():
            self.ent_dim.insert(0, dims[0])
        self.lbl.configure(text="该领域现有维度：%s" % ("、".join(dims) if dims else "（无）"))

    def _ok(self) -> None:
        dom = self.om_dom.get()
        dim = (self.ent_dim.get() or "").strip()
        tag = (self.ent_tag.get() or "").strip()
        if not dim:
            self.lbl.configure(text="⚠ 请填写维度名", text_color=_C_DANGER)
            return
        _done, _errs = 0, []
        for _w in self.words:
            ok, _msg = auto_words.promote_tag(self.db, _w, dom, dim, tag or None)
            if ok:
                _done += 1
            else:
                _errs.append("%s：%s" % (_w, _msg))
        if _errs:
            self.lbl.configure(text="⚠ " + "；".join(_errs[:3]), text_color=_C_DANGER)
            return
        self.result = "✅ 已把 %d 个词写入词表（%s / %s）" % (_done, dom, dim)
        self.destroy()
