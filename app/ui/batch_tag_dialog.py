# -*- coding: utf-8 -*-
"""
batch_tag_dialog.py - 「批量智能自动打标」对话框（阶段 2，2026-09-14）

功能（对应《可行性研究报告 v2》§10）：
  1. **打标对象**：仅无标签的条目（默认，保护人工标签） / 全部条目；
  2. **范围**：全部条目 / 指定根目录（含子树）/ 当前列表；
  3. **数量上限**：不限 / 前 100 / 前 500 / 前 1000；
  4. **每条例目标签数**：1 / 2 / 3（默认 3）；
  5. **标签重点范围**：勾选参与打分的维度（"领域/显式标签"始终参与）；默认全选；
  6. **写入策略**：追加（并入既有标签，默认） / 覆盖（替换）；
  7. **强制预演**（只读，先看清楚再执行）+ **二次确认**；
  8. **强制备份**：执行前调用 `backup.pretag_snapshot()`，**失败即中止**；
  9. **精确撤销**：执行后写入"打标批次"明细，可只回退本次打标（不影响其后修改）；
 10. **历史批次**：查看最近批次并撤销。

**目标库（2026-09-14 增强）**：⓪ 可选"当前库（软件自带）"或"选择其他库文件…"（如私人库）。
- 选其他库时会**校验必需表**，非法/损坏文件直接拒绝；校验通过后建**独立连接**，关闭窗口时释放；
- 预演与执行**都作用于目标库**；「范围＝当前列表」对其他库无意义（选该库时自动退回"全部条目"）；
- **打标前强制备份的是目标库**；批次记录里写入目标库路径，故**撤销总会回到正确的库**；
- 若预演之后又切换了目标库，执行前会作废并要求**重新预演**，避免"预演 A 库、执行 B 库"；
- 词表取"目标库自己的词表"，该库没有则用当前软件使用的词表（**只读目标库，不写词表**）。

与「预置标签」的区别（重要）：
  - 预置标签（`tag_builtin.py`）是**打包前的数据初始化**，`touch_updated=False`（不进变更包）；
  - 本对话框是**用户操作**，`touch_updated=True`（应进当日变更包，与 P1-D 修复一致），
    故二次确认中会如实提示"会把 N 条计入今日变更包"。
"""
import os

import customtkinter as ctk
from tkinter import filedialog, messagebox

from .. import backup, config, tagger, tagger_batch, tagger_engine
from ..database import Database

_LIMIT_MAP = {"不限": 0, "前 100": 100, "前 500": 500, "前 1000": 1000}
_MAX_TAGS = ["1", "2", "3"]


class BatchHistoryDialog(ctk.CTkToplevel):
    """历史打标批次：查看最近批次并**精确撤销**。

    2026-09-14（阶段 2 增强）：本窗口**不再直接持有数据库**，撤销一律通过 `undo_fn(batch_path)`
    回调交给调用方——由调用方按"批次记录里的目标库路径"决定在**哪个库**上撤销
    （批量打标现已支持"目标库＝其他库文件"）。
    """

    def __init__(self, master, undo_fn, current_path: str = ""):
        super().__init__(master)
        self._undo_fn = undo_fn
        self.current_path = current_path
        self.title("🕒 打标批次历史")
        self.transient(master)
        self.grab_set()
        self.geometry("680x420")
        self._build()

    def _build(self) -> None:
        ctk.CTkLabel(self, text="最近的打标批次（可精确撤销：只回退那次打标，不影响其后修改）",
                     font=("Microsoft YaHei", 12)).pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(self, text=f"明细目录：{tagger_batch.batch_dir()}（只保留最近 "
                                f"{tagger_batch.BATCH_KEEP} 个）",
                     font=("Microsoft YaHei", 10), text_color="#9aa4b1"
                     ).pack(anchor="w", padx=14, pady=(0, 6))

        # 2026-09-14：底部按钮栏**先 pack 到底部**（与主对话框同一修复：避免被可扩展列表挤掉）
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=14, pady=(2, 12))
        self.status = ctk.CTkLabel(bar, text="", font=("Microsoft YaHei", 11),
                                   text_color="#5b6b7c")
        self.status.pack(side="left")
        ctk.CTkButton(bar, text="关闭", width=88, command=self.destroy).pack(side="right")

        self.box = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.box.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self._render()

    def _render(self) -> None:
        for w in self.box.winfo_children():
            w.destroy()
        rows = tagger_batch.list_batches()
        if not rows:
            ctk.CTkLabel(self.box, text="（暂无打标批次）", text_color="#9aa4b1",
                         font=("Microsoft YaHei", 11)).pack(anchor="w", padx=8, pady=8)
            return
        for b in rows:
            row = ctk.CTkFrame(self.box, fg_color="transparent")
            row.pack(fill="x", pady=2)
            state = "已撤销（%s）" % b["undone_at"] if b["undone"] else "可撤销"
            dpath = tagger_batch.batch_db_path(b["path"])
            dname = os.path.basename(dpath) if dpath else "（未记录）"
            same = (dpath and os.path.abspath(dpath) == os.path.abspath(self.current_path))
            ctk.CTkLabel(row, text=f"{b['created_at']}　{b['total']} 条 / {b['tags']} 个标签　"
                                   f"库：{dname}{'（当前库）' if same else ''}　{state}",
                         font=("Microsoft YaHei", 11), anchor="w"
                         ).pack(side="left", fill="x", expand=True)
            btn = ctk.CTkButton(row, text="撤销", width=64, height=24,
                                font=("Microsoft YaHei", 11),
                                fg_color=("#8a94a6" if b["undone"] else "#D9534F"),
                                state=("disabled" if b["undone"] else "normal"),
                                command=lambda p=b["path"], d=dpath: self._undo(p, d))
            btn.pack(side="right")

    def _undo(self, path: str, db_path: str) -> None:
        hint = f"\n\n目标库：{db_path}" if db_path else ""
        if not messagebox.askyesno(
                "撤销该批次",
                "将把该批次涉及的条目**还原为打标前的标签**（只回退本次打标，"
                f"不影响你此后的其他修改）。{hint}\n\n确定吗？", parent=self):
            return
        res = self._undo_fn(path) if callable(self._undo_fn) else {
            "ok": False, "error": "撤销入口不可用", "restored": 0, "skipped": 0}
        if res.get("ok"):
            self.status.configure(text=f"✅ 已撤销：还原 {res['restored']} 条"
                                       f"（跳过 {res['skipped']} 条）", text_color="#2E8B57")
            self._render()
        else:
            self.status.configure(text=f"⚠ {res.get('error')}", text_color="#D9534F")


class BatchTagDialog(ctk.CTkToplevel):
    """「批量智能自动打标」主对话框。"""

    def __init__(self, master, db, db_path: str = ""):
        super().__init__(master)
        self.db = db                                   # 当前库（软件自带 data\prompts.db）
        self.master = master
        self._cur_db_path = db_path or os.path.join(config.data_dir(), config.DB_FILE_NAME)
        # 2026-09-14（目标库增强）：可把打标作用于**其他库文件**（如私人库）
        self._target_db = db                           # 实际打标/预演使用的库连接
        self._target_path = self._cur_db_path
        self._own_db = None                            # "其他库"时自建的独立连接（关闭窗口时关掉）
        self.title("🤖 批量智能自动打标")
        self.transient(master)
        self.grab_set()
        # 2026-09-14 修复（用户反馈"预演后找不到确认按钮"）：窗口高度按**屏幕可用高度**自适应；
        # 2026-09-14 复审修正：最小宽度 720 → **800**——工具行现有 5 个按钮（预演/历史批次/撤销/
        #   取消/确认执行）实测共需 729px + 左右内边距 28px = 757px，720 宽会把按钮挤出可视区，
        #   故最小宽度必须 ≥ 757（取 800 留余量）；最小高度 520 → 560（表单区约 400px + 工具行 + 预演区）。
        try:
            _sh = self.winfo_screenheight()
        except Exception:
            _sh = 900
        self.geometry("820x%d" % max(560, min(700, int(_sh * 0.85))))
        try:
            self.minsize(800, 560)
        except Exception:
            pass
        try:
            self.dict_data = tagger.load_dict(db)
        except Exception:
            self.dict_data = {}
        self._dim_vars = {}
        self._plan = None
        self._last_batch_path = None
        self._build()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        pad = 14

        # ------------------------------------------------------------------ #
        # 状态行：**优先 pack 到窗口最底部**（2026-09-14）
        #   tkinter 的 pack 按调用顺序分配空间，空间不足时**最后 pack 的控件会被挤掉**。
        #   原先「✅ 确认执行 / 取消」排在可扩展的预演文本框之后 → 窗口高度不够时按钮
        #   被挤出可视区（用户实测"预演后找不到确认按钮"）。
        #   现按用户要求把两个按钮**移到工具行右上方**（与「🔍 预演」同行），底部只留状态提示行，
        #   且状态行**优先占底**：即使空间不足也只压缩可扩展的预演文本框，关键控件永远可见。
        # ------------------------------------------------------------------ #
        self._status_bar = ctk.CTkFrame(self, fg_color="transparent")
        self._status_bar.pack(side="bottom", fill="x", padx=pad, pady=(0, 8))
        self.status = ctk.CTkLabel(self._status_bar, text="", font=("Microsoft YaHei", 11),
                                   text_color="#5b6b7c", anchor="w", justify="left")
        self.status.pack(side="left", fill="x", expand=True)

        frm = ctk.CTkFrame(self, fg_color="transparent")
        frm.pack(fill="x", padx=pad, pady=(10, 2))
        frm.grid_columnconfigure(1, weight=1)
        r = [0]

        def _row(label):
            ctk.CTkLabel(frm, text=label, font=("Microsoft YaHei", 12)
                         ).grid(row=r[0], column=0, padx=(0, 8), pady=6, sticky="w")
            r[0] += 1

        # ⓪ 目标库（2026-09-14 新增：可给"其他库文件"打标，如私人库）
        _row("⓪ 目标库")
        self.om_target = ctk.CTkOptionMenu(frm, width=200,
                                           values=["当前库（软件自带）", "选择其他库文件…"],
                                           command=self._on_target_change,
                                           font=("Microsoft YaHei", 12))
        self.om_target.set("当前库（软件自带）")
        self.om_target.grid(row=r[0] - 1, column=1, pady=6, sticky="w")
        self.lbl_target = ctk.CTkLabel(frm, text="", font=("Microsoft YaHei", 10),
                                       text_color="#9aa4b1", anchor="w", justify="left")
        self.lbl_target.grid(row=r[0], column=0, columnspan=2, pady=(0, 2), sticky="w")
        r[0] += 1
        self._refresh_target_label()

        # ① 打标对象
        _row("① 打标对象")
        self.seg_scope = ctk.CTkSegmentedButton(
            frm, values=["仅无标签的条目", "全部条目"], width=300,
            command=lambda _v: self._invalidate())
        self.seg_scope.set("仅无标签的条目")
        self.seg_scope.grid(row=r[0] - 1, column=1, pady=6, sticky="w")

        # ② 范围
        _row("② 范围")
        self.om_range = ctk.CTkOptionMenu(frm, width=200,
                                          values=["全部条目", "指定根目录", "当前列表"],
                                          command=self._on_range_change,
                                          font=("Microsoft YaHei", 12))
        self.om_range.set("全部条目")
        self.om_range.grid(row=r[0] - 1, column=1, pady=6, sticky="w")
        self.om_domain = ctk.CTkOptionMenu(frm, width=200, values=["（全部）"],
                                           font=("Microsoft YaHei", 12))
        self._fill_domains()
        self.om_domain.grid(row=r[0] - 1, column=1, padx=(210, 0), pady=6, sticky="w")
        self.om_domain.grid_remove()          # 仅"指定根目录"时显示

        # ③ 数量上限
        _row("③ 数量上限")
        self.om_limit = ctk.CTkOptionMenu(frm, width=140, values=list(_LIMIT_MAP.keys()),
                                          font=("Microsoft YaHei", 12))
        self.om_limit.set("不限")
        self.om_limit.grid(row=r[0] - 1, column=1, pady=6, sticky="w")

        # ④ 每条例目标签数
        _row("④ 每条例目标签数")
        self.om_tags = ctk.CTkOptionMenu(frm, width=140, values=_MAX_TAGS,
                                         font=("Microsoft YaHei", 12))
        self.om_tags.set("3")
        self.om_tags.grid(row=r[0] - 1, column=1, pady=6, sticky="w")

        # ⑤ 标签重点范围（维度多选）
        _row("⑤ 标签重点范围")
        dimb = ctk.CTkFrame(frm, fg_color="transparent")
        dimb.grid(row=r[0] - 1, column=1, pady=6, sticky="w")
        for i, dim in enumerate(tagger.all_dimension_names(self.dict_data)):
            var = ctk.BooleanVar(value=True)
            self._dim_vars[dim] = var
            ctk.CTkCheckBox(dimb, text=dim, variable=var, width=86, font=("Microsoft YaHei", 11)
                            ).grid(row=i // 5, column=i % 5, padx=(0, 6), pady=2, sticky="w")
        ctk.CTkLabel(frm, text="（取消勾选即不参与打分；「领域」与来源自带的「显式标签」始终保留）",
                     font=("Microsoft YaHei", 10), text_color="#9aa4b1"
                     ).grid(row=r[0], column=1, pady=(0, 4), sticky="w")
        r[0] += 1

        # ⑥ 写入策略
        _row("⑥ 写入策略")
        self.seg_write = ctk.CTkSegmentedButton(
            frm, values=["追加（并入既有标签）", "覆盖（替换既有标签）"], width=300)
        self.seg_write.set("追加（并入既有标签）")
        self.seg_write.grid(row=r[0] - 1, column=1, pady=6, sticky="w")

        # 保护说明
        ctk.CTkLabel(frm, text="🛡 执行前**强制自动备份数据库**（备份失败则中止，不写入）；"
                               "执行记录明细，可精确撤销",
                     font=("Microsoft YaHei", 10), text_color="#D9534F"
                     ).grid(row=r[0], column=0, columnspan=2, pady=(2, 4), sticky="w")
        r[0] += 1

        # 工具行（2026-09-14 用户要求：把「✅ 确认执行 / 取消」放在**预演文本框右上方**，
        #   与「🔍 预演（不写库）」同一行 → 预演与确认在同一视觉区域，操作连贯；
        #   同时因本行位于可扩展的预演区**之前** pack，空间不足时也不会被挤掉）
        self._tool_bar = ctk.CTkFrame(self, fg_color="transparent")
        self._tool_bar.pack(fill="x", padx=pad, pady=(4, 2))
        ctk.CTkButton(self._tool_bar, text="🔍 预演（不写库）", width=140, fg_color="#25639c",
                      command=self._on_preview).pack(side="left")
        ctk.CTkButton(self._tool_bar, text="🕒 历史批次…", width=110, fg_color="#8a94a6",
                      command=lambda: BatchHistoryDialog(self, self._undo_batch_by_path,
                                                         self._target_path)
                      ).pack(side="left", padx=(6, 0))
        self.btn_undo = ctk.CTkButton(self._tool_bar, text="↩ 撤销本次", width=100,
                                      fg_color="#D9534F", state="disabled",
                                      command=self._on_undo)
        self.btn_undo.pack(side="left", padx=(6, 0))
        # 右侧（预演文本框右上角位置）：确认执行 / 取消
        ctk.CTkButton(self._tool_bar, text="✅ 确认执行", width=120, fg_color="#2E8B57",
                      command=self._on_execute).pack(side="right")
        ctk.CTkButton(self._tool_bar, text="取消", width=90,
                      command=self.destroy).pack(side="right", padx=(0, 6))

        # 预演区（**最后 pack**：空间不足时优先被压缩，其自带滚动条 → 不损失功能）
        self.txt = ctk.CTkTextbox(self, height=200, font=("Microsoft YaHei", 11), wrap="none")
        self.txt.pack(fill="both", expand=True, padx=pad, pady=(2, 2))
        self.txt.insert("1.0", "点「🔍 预演（不写库）」查看将要写入的标签（不会修改任何数据）。")
        self.txt.configure(state="disabled")

    # 注：状态行已优先 pack 到窗口底部、「✅ 确认执行 / 取消」已并入上方工具行
    #     （2026-09-14 修复"预演后找不到确认按钮" + 用户要求的按钮位置调整）。

    # ------------------------------------------------------------------ #
    # 目标库（2026-09-14 新增）：当前库 / 其他库文件
    # ------------------------------------------------------------------ #
    def _is_current_db(self) -> bool:
        return os.path.abspath(self._target_path) == os.path.abspath(self._cur_db_path)

    def _refresh_target_label(self) -> None:
        lbl = getattr(self, "lbl_target", None)
        if lbl is None or not lbl.winfo_exists():
            return
        if self._is_current_db():
            lbl.configure(text="目标：当前库（软件自带）　%s" % self._cur_db_path,
                          text_color="#5b6b7c")
        else:
            lbl.configure(text="⚠ 目标：其他库文件　%s" % self._target_path,
                          text_color="#D9534F")

    def _on_target_change(self, value: str) -> None:
        """切换目标库；选"其他库文件…"时弹文件选择并**校验是否为合法库**。"""
        if value != "选择其他库文件…":
            self._use_target(self.db, self._cur_db_path, own=False)
            return
        path = filedialog.askopenfilename(
            title="选择要打标的库文件（*.db）", parent=self,
            filetypes=[("PromptSprite 数据库", "*.db"), ("所有文件", "*.*")],
            initialdir=os.path.dirname(self._cur_db_path) or None)
        if not path:
            self.om_target.set("当前库（软件自带）")
            return
        ok, msg, tdb = self._open_target_db(path)
        if not ok:
            messagebox.showwarning("无法使用该库", msg, parent=self)
            self.om_target.set("当前库（软件自带）")
            return
        self._use_target(tdb, path, own=(tdb is not self.db))
        if msg:
            self.toast_info(msg)

    def toast_info(self, msg: str) -> None:
        try:
            self.master.toast(msg)
        except Exception:
            pass

    def _open_target_db(self, path: str):
        """打开并**校验**目标库（必需表是否齐全）；返回 (ok, 说明, Database|None)。

        - 若选中的就是当前库 → 复用现有连接；
        - 校验通过返回新连接（由调用方负责关闭）。
        """
        if not os.path.isfile(path):
            return False, "文件不存在。", None
        if os.path.abspath(path) == os.path.abspath(self._cur_db_path):
            return True, "已切换回当前库。", self.db
        tdb = None
        try:
            tdb = Database(path)
            n = tdb.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            tdb.conn.execute("SELECT COUNT(*) FROM tags").fetchone()
            tdb.conn.execute("SELECT COUNT(*) FROM categories").fetchone()
            tdb.get_meta(config.META_TAG_DICT)      # meta 表可用性
        except Exception as exc:
            if tdb is not None:
                try:
                    tdb.close()
                except Exception:
                    pass
            return False, ("这不是一个可用的 PromptSprite 数据库（缺少必需的表或已损坏）。\n\n"
                           "技术信息：%s" % exc), None
        return True, "目标库已切换：%s（共 %d 条条目）" % (os.path.basename(path), n), tdb

    def _use_target(self, tdb, path: str, own: bool) -> None:
        """把打标目标切到 tdb，并关闭此前自建的连接、刷新界面相关项。

        注意：若传入的连接**就是**当前自建连接（例如重复选用同一个库），则不要关闭它。
        """
        if (self._own_db is not None and self._own_db is not self.db
                and self._own_db is not tdb):
            try:
                self._own_db.close()
            except Exception:
                pass
        self._target_db = tdb
        self._target_path = path
        self._own_db = tdb if own else None
        # 根目录下拉需按目标库重建；"当前列表"对其他库无意义 → 退回"全部条目"
        self._fill_domains()
        if not self._is_current_db() and self.om_range.get() == "当前列表":
            self.om_range.set("全部条目")
        self._invalidate()
        self._refresh_target_label()

    def _load_target_dict(self) -> dict:
        """目标库的打标词表：**该库已有词表就用它**，否则用当前软件使用的词表（只读，不写库）。"""
        raw = ""
        try:
            raw = self._target_db.get_meta(config.META_TAG_DICT) or ""
        except Exception:
            raw = ""
        if raw.strip():
            try:
                return tagger.load_dict(self._target_db)
            except Exception:
                pass
        return self.dict_data

    def _with_batch_db(self, batch_path: str, fn):
        """在"该批次记录的目标库"上执行 fn(db)（需临时开库则用后关闭）。

        用于「撤销」：批次记录里存了当时的 `db_path`，故即使之后切换了目标库也不会撤销错库。
        """
        bpath = tagger_batch.batch_db_path(batch_path) or self._target_path
        cur = os.path.abspath(self._cur_db_path)
        if not bpath or os.path.abspath(bpath) == cur:
            return fn(self.db)
        if (self._own_db is not None and self._own_db is not self.db
                and os.path.abspath(self._target_path) == os.path.abspath(bpath)):
            return fn(self._target_db)
        tdb = None
        try:
            tdb = Database(bpath)
            return fn(tdb)
        except Exception as exc:
            return {"ok": False, "error": "无法打开该批次的目标库：%s" % exc,
                    "restored": 0, "skipped": 0}
        finally:
            if tdb is not None:
                try:
                    tdb.close()
                except Exception:
                    pass

    def _undo_batch_by_path(self, batch_path: str) -> dict:
        """按批次记录的目标库执行**精确撤销**（供历史批次窗口回调）。"""
        res = self._with_batch_db(batch_path, lambda d: tagger_batch.undo_batch(d, batch_path))
        # 仅当撤销的正是当前库时，才刷新主窗口标签页面
        try:
            if res.get("ok") and self._is_current_db_batch(batch_path):
                if getattr(self.master, "_tag_page_on", False):
                    self.master._refresh_tag_page()
        except Exception:
            pass
        return res

    def _is_current_db_batch(self, batch_path: str) -> bool:
        bpath = tagger_batch.batch_db_path(batch_path) or self._target_path
        return bool(bpath) and os.path.abspath(bpath) == os.path.abspath(self._cur_db_path)

    def destroy(self) -> None:
        """关闭窗口时释放"其他库"的独立连接（当前库连接由主窗口持有，不动）。"""
        try:
            if self._own_db is not None and self._own_db is not self.db:
                self._own_db.close()
        except Exception:
            pass
        self._own_db = None
        super().destroy()

    def _fill_domains(self) -> None:
        names = ["（全部）"]
        self._domain_ids = {"（全部）": None}
        try:
            for d in self._target_db.list_domains():     # 2026-09-14：按**目标库**列出根目录
                names.append(d["name"])
                self._domain_ids[d["name"]] = d["id"]
        except Exception:
            pass
        self.om_domain.configure(values=names)
        self.om_domain.set(names[0])

    def _on_range_change(self, value: str) -> None:
        if value == "指定根目录":
            self.om_domain.grid()
        else:
            self.om_domain.grid_remove()
        self._invalidate()

    def _invalidate(self) -> None:
        """选项变化 → 之前的预演结果作废。"""
        self._plan = None
        self.status.configure(text="选项已变化，请重新预演。", text_color="#E08A00")

    def _set_text(self, text: str) -> None:
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", text)
        self.txt.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 取范围
    # ------------------------------------------------------------------ #
    def _entries_of_domain(self, domain_id) -> list:
        """取某根目录（含其关联分类的整棵子树，含"仅关联"的条目）下的全部条目。"""
        c = self._target_db.conn
        roots = [r[0] for r in c.execute(
            "SELECT category_id FROM domain_category WHERE domain_id = ?", (domain_id,))]
        subs, stack = set(), list(roots)
        while stack:
            cur = stack.pop()
            if cur in subs:
                continue
            subs.add(cur)
            for r in c.execute("SELECT id FROM categories WHERE parent_id = ?", (cur,)):
                stack.append(r[0])
        if not subs:
            return []
        ph = ",".join("?" * len(subs))
        args = tuple(subs)
        return [dict(r) for r in c.execute(
            "SELECT * FROM entries WHERE category_id IN (" + ph + ")"
            " OR id IN (SELECT entry_id FROM entry_links WHERE category_id IN (" + ph + "))"
            " ORDER BY updated_at DESC, id", args + args)]

    def _range_entries(self) -> list:
        mode = self.om_range.get()
        if mode == "指定根目录":
            did = self._domain_ids.get(self.om_domain.get())
            if did is None:
                return self._target_db.list_all_entries()
            return self._entries_of_domain(did)
        if mode == "当前列表":
            if not self._is_current_db():
                return []          # "当前列表"指的是软件里正在看的列表，对其他库无意义
            return list(getattr(self.master, "_entries_all", None) or [])
        return self._target_db.list_all_entries()

    # ------------------------------------------------------------------ #
    # 预演 / 执行
    # ------------------------------------------------------------------ #
    def _opts(self) -> dict:
        return {
            "打标对象": self.seg_scope.get(),
            "范围": self.om_range.get() + ("/" + self.om_domain.get()
                                          if self.om_range.get() == "指定根目录" else ""),
            "数量上限": self.om_limit.get(),
            "每条例目标签数": int(self.om_tags.get()),
            "取消的维度": [d for d, v in self._dim_vars.items() if not v.get()],
            "写入策略": self.seg_write.get(),
        }

    def _build_plan(self) -> dict:
        """计算打标方案（**不写库**）：范围 → 仅无标签 → 数量上限 → 引擎 → 汇总。"""
        entries = self._range_entries()
        in_range = len(entries)
        only_untagged = (self.seg_scope.get() == "仅无标签的条目")
        before_map = {}
        if entries:
            try:
                before_map = self._target_db.list_tags_for_entries([e["id"] for e in entries])
            except Exception:
                before_map = {}
        skipped_existing = 0
        if only_untagged:
            kept = [e for e in entries if not before_map.get(e["id"])]
            skipped_existing = len(entries) - len(kept)
            entries = kept
        limit = _LIMIT_MAP.get(self.om_limit.get(), 0)
        truncated = 0
        if limit and len(entries) > limit:
            truncated = len(entries) - limit
            entries = entries[:limit]

        index = tagger.build_context_index(self._target_db)
        exclude = [d for d, v in self._dim_vars.items() if not v.get()]
        max_tags = int(self.om_tags.get())
        dict_data = self._load_target_dict()          # 目标库词表（无则用当前软件词表）
        assignments, items, samples = {}, [], []
        freq, dims_used, domains = {}, {}, {}
        zero = 0
        for e in entries:
            texts = {k: e.get(k) for k in ("name", "intro", "features",
                                           "image_desc", "prompt_cn", "prompt_en")}
            res = tagger_engine.suggest(
                texts, dict_data,
                tagger.entry_context_names(index, e.get("category_id")),
                max_tags=max_tags, exclude_dims=exclude)
            names = tagger_engine.tag_names(res)
            before = list(before_map.get(e["id"]) or [])
            after = before + [n for n in names if n not in before] \
                if self.seg_write.get().startswith("追加") else names
            assignments[e["id"]] = after
            items.append({"entry_id": e["id"], "name": e.get("name") or "",
                          "before": before, "after": after})
            domains[res["domain"] or "（兜底）"] = domains.get(res["domain"] or "（兜底）", 0) + 1
            if not names:
                zero += 1
            for t in res["tags"]:
                freq[t["tag"]] = freq.get(t["tag"], 0) + 1
                dims_used[t["dim"]] = dims_used.get(t["dim"], 0) + 1
            if len(samples) < 80:
                samples.append((e.get("name") or "", res["domain"] or "兜底", names))
        links = sum(len(v) for v in assignments.values())
        prev_links = sum(len(v) for v in before_map.values() if v)
        return {
            "entries": [e["id"] for e in entries],
            "target_path": self._target_path,      # 本方案针对的目标库（执行前会复核）
            "assignments": assignments, "items": items, "samples": samples,
            "in_range": in_range, "skipped_existing": skipped_existing,
            "truncated": truncated, "zero": zero,
            "freq": freq, "dims": dims_used, "domains": domains,
            "prev_links": prev_links,
            "stats": {"处理": len(entries), "写入标签关联": links,
                      "其中原有": prev_links if not self.seg_write.get().startswith("覆盖") else 0,
                      "零标签条目": zero},
        }

    def _render_plan(self, plan) -> None:
        L = []
        L.append("【预演结果】（只读，未修改任何数据）")
        L.append("  目标库：" + ("当前库（软件自带）" if self._is_current_db()
                                 else "⚠ 其他库文件 → %s" % self._target_path))
        L.append("")
        L.append("  范围内条目：%d 条" % plan["in_range"])
        if plan["skipped_existing"]:
            L.append("  已有标签而跳过：%d 条（「仅无标签的条目」）" % plan["skipped_existing"])
        if plan["truncated"]:
            L.append("  受数量上限截断：%d 条（未处理，可调大上限后重跑）" % plan["truncated"])
        L.append("  本次将处理：%d 条" % plan["stats"]["处理"])
        L.append("  将写入标签关联：%d 条（其中原本已存在的 %d 条会被跳过）"
                 % (plan["stats"]["写入标签关联"], plan["stats"]["其中原有"]))
        L.append("  打完仍无标签：%d 条" % plan["zero"])
        L.append("")
        L.append("  领域判定分布：" + "；".join("%s %d" % (k, v)
                                                for k, v in sorted(plan["domains"].items(),
                                                                   key=lambda x: -x[1])))
        L.append("  维度命中分布：" + "；".join("%s %d" % (k, v)
                                                for k, v in sorted(plan["dims"].items(),
                                                                   key=lambda x: -x[1])))
        L.append("  标签频次 TOP15：" + "；".join(
            "%s %d" % (k, v) for k, v in sorted(plan["freq"].items(),
                                                 key=lambda x: -x[1])[:15]))
        L.append("")
        L.append("  样例（前 %d 条）：" % len(plan["samples"]))
        for i, (name, dom, tags) in enumerate(plan["samples"], 1):
            L.append("   %3d. %-34s [%s] %s"
                     % (i, name[:34], dom, " / ".join(tags) or "（无标签）"))
        self._set_text("\n".join(L))

    def _on_preview(self) -> None:
        self.status.configure(text="正在预演…（不写库）", text_color="#5b6b7c")
        self.update_idletasks()
        try:
            plan = self._build_plan()
        except Exception as exc:
            self.status.configure(text=f"⚠ 预演失败：{exc}", text_color="#D9534F")
            return
        self._plan = plan
        self._render_plan(plan)
        self.status.configure(
            text="✅ 预演完成：将处理 %d 条、写入 %d 个标签关联（尚未写入任何数据）"
                 % (plan["stats"]["处理"], plan["stats"]["写入标签关联"]),
            text_color="#2E8B57")

    def _on_execute(self) -> None:
        if self._plan is None:
            self._on_preview()
            if self._plan is None:
                return
        plan = self._plan
        # 目标库复核：预演之后若换过目标库，则作废重来（避免"预演 A 库、执行 B 库"）
        if plan.get("target_path") and \
                os.path.abspath(plan["target_path"]) != os.path.abspath(self._target_path):
            self._plan = None
            messagebox.showwarning("请重新预演", "目标库已改变，请重新预演后再执行。", parent=self)
            return
        n = plan["stats"]["处理"]
        if n == 0:
            messagebox.showinfo("无可用条目", "本次没有需要处理的条目。", parent=self)
            return
        _tgt = ("当前库（软件自带）" if self._is_current_db()
                else "⚠ 其他库文件：%s" % self._target_path)
        # —— 二次确认（如实提示"会影响当日变更包"与目标库）——
        if not messagebox.askyesno(
                "确认执行批量打标",
                f"目标库：{_tgt}\n\n"
                f"即将为 {n} 条条目写入共 {plan['stats']['写入标签关联']} 个标签关联。\n\n"
                f"· 执行前会「自动备份数据库」（备份失败则中止，不写入任何数据）；\n"
                f"· 这 {n} 条会被计入「今日变更包」（换机同步时会随包带上）；\n"
                f"· 执行后可在「🕒 历史批次…」中「精确撤销」本次打标。\n\n"
                "确定继续吗？", parent=self):
            return
        # —— 强制备份（备份**目标库**）：失败即中止 ——
        snap = backup.pretag_snapshot(self._target_path)
        if not snap.get("ok"):
            messagebox.showwarning("已中止",
                                   f"打标前备份失败：{snap.get('error')}\n\n未写入任何数据。",
                                   parent=self)
            self.status.configure(text="⚠ 备份失败，已中止（未写入）", text_color="#D9534F")
            return
        # —— 写入（单事务；写入期间窗口短暂无响应，与既有行为一致）——
        mode = "append" if self.seg_write.get().startswith("追加") else "replace"
        self.status.configure(text="正在写入…（请稍候，勿关闭窗口）", text_color="#E08A00")
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            res = self._target_db.set_entry_tags_bulk(plan["assignments"], mode=mode,
                                                      touch_updated=True)
        except Exception as exc:
            messagebox.showwarning("执行失败", str(exc), parent=self)
            self.status.configure(text=f"⚠ 执行失败：{exc}", text_color="#D9534F")
            return
        # —— 记录批次（供精确撤销；含目标库路径）——
        batch = tagger_batch.new_batch(self._opts(), db_path=self._target_path)
        batch["items"] = plan["items"]
        batch["stats"] = dict(plan["stats"])
        batch["stats"]["links_added"] = res.get("links", 0)
        batch["stats"]["tags_created"] = res.get("tags_created", 0)
        saved = tagger_batch.save_batch(batch)
        self._last_batch_path = saved.get("path")
        self.btn_undo.configure(state=("normal" if self._last_batch_path else "disabled"))
        # —— 结果 ——
        msg = (f"✅ 完成：处理 {res.get('entries', 0)} 条，新增标签关联 {res.get('links', 0)} 条，"
               f"新建标签 {res.get('tags_created', 0)} 个。\n"
               f"备份：{os.path.basename(snap.get('path') or '')}\n"
               f"批次明细：{os.path.basename(self._last_batch_path or '（未记录）')}")
        self.status.configure(text=msg.split("\n")[0], text_color="#2E8B57")
        self._set_text(msg + "\n\n（如需回退，点「↩ 撤销本次打标」）")
        self._plan = None
        # 同步主窗口的标签页面计数与条目区标签显示（**仅当打标的就是当前库**）
        if self._is_current_db():
            try:
                if getattr(self.master, "_tag_page_on", False):
                    self.master._refresh_tag_page()
                self.master.toast("✅ 批量打标完成：处理 %d 条" % res.get("entries", 0))
            except Exception:
                pass
        else:
            self.toast_info("✅ 已在「%s」完成打标：处理 %d 条"
                            % (os.path.basename(self._target_path), res.get("entries", 0)))

    def _on_undo(self) -> None:
        if not self._last_batch_path:
            return
        _bpath = tagger_batch.batch_db_path(self._last_batch_path) or self._target_path
        if not messagebox.askyesno(
                "撤销本次打标",
                "将把本次打标涉及的条目**还原为打标前的标签**"
                "（只回退本次打标，不影响打标之后你做的其他修改）。\n\n"
                f"目标库：{_bpath}\n\n确定吗？", parent=self):
            return
        res = self._undo_batch_by_path(self._last_batch_path)
        if res.get("ok"):
            self.status.configure(text=f"✅ 已撤销：还原 {res['restored']} 条"
                                       f"（跳过 {res['skipped']} 条）", text_color="#2E8B57")
            self._set_text("已撤销本次打标：还原 %d 条，跳过 %d 条。"
                           % (res["restored"], res["skipped"]))
            self.btn_undo.configure(state="disabled")
        else:
            messagebox.showwarning("撤销失败", str(res.get("error")), parent=self)
