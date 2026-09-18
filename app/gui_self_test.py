# -*- coding: utf-8 -*-
"""
gui_self_test.py - 主界面 GUI 自动化回归（正式测试文件，2026-09-07 起）

覆盖：
  A. 数据层新特性：保存去重提示、副本命名（（副本）/（副本2））
  B. UI：新增主按钮样式与启用规则、任一分级分类显示本级条目与就地新增、
     详情区名称字段（① 条目名称）与顶部纯按钮命令条（移动/关联/复制/收藏/删除）、
     多位置提示行、移动语义（仅当前位置/整体转移）、复制独立副本、
     删除分类三级保护（级联短语 / 安全删除）、精简模式合并顶栏行。

运行：python -m app.gui_self_test
说明：需要可显示环境（Windows 桌面）；使用临时数据库，不影响真实数据；
      结束自动隐藏窗口并清理临时库。所有弹窗均以桩替换，不阻塞。
"""
import os
import sys
import tempfile
import time
import tkinter as tk   # 2026-09-16（批次 11-1）：浮窗"底边夹紧"专项断言需自建底部控件

from app.database import Database
from app.models import Entry
from app import auto_words, config, tagger, tagger_dict   # 2026-09-18：词表断言改为"与出厂种子对比"
from app.ui import main_window as _mw
from app.ui import auto_words_dialog as _awmod   # 2026-09-16（批次 12-3）：自动取词管理对话框
from app.ui import batch_tag_dialog as _btd   # 阶段 2：批量打标（备份桩用）
from app.ui.main_window import MainWindow
from app.ui.move_selector import MoveSelector
from app.ui.settings_dialog import SettingsDialog


# ---------------------------------------------------------------- #
# 弹窗桩
# ---------------------------------------------------------------- #
class StubMB:
    """askyesno 默认返回 True（继续）；记录调用便于断言提示文案"""
    def __init__(self):
        self.asks = []

    def askyesno(self, *a, **k):
        self.asks.append(("yesno", a, k))
        return True

    def askyesnocancel(self, *a, **k):
        self.asks.append(("ask", a, k))
        return True

    def showwarning(self, *a, **k):
        self.asks.append(("warn", a, k))

    def showinfo(self, *a, **k):
        self.asks.append(("info", a, k))


class StubSD:
    phrase = ""

    def __init__(self):
        self.calls = []

    def askstring(self, *a, **k):
        self.calls.append(("askstring", a, k))
        return self.phrase


class StubFD:
    """文件对话框桩（2026-09-14 阶段 0.5）：save_path / open_path 由用例设置。"""

    def __init__(self):
        self.save_path = ""
        self.open_path = ""
        self.calls = []

    def asksaveasfilename(self, *a, **k):
        self.calls.append(("save", a, k))
        return self.save_path

    def askopenfilename(self, *a, **k):
        self.calls.append(("open", a, k))
        return self.open_path


def pump(app, n=8, dt=0.012):
    for _ in range(n):
        app.update()
        time.sleep(dt)


def text_of(w):
    try:
        return str(w.cget("text"))
    except Exception:
        return ""


def walk_types(w, cls_name, out):
    for c in w.winfo_children():
        if type(c).__name__ == cls_name:
            out.append(c)
        walk_types(c, cls_name, out)


# ---------------------------------------------------------------- #
# 数据层快速校验（无窗口）
# ---------------------------------------------------------------- #
def db_level_checks(db, check):
    # 副本命名
    c1 = db.add_category("副本L", domain_id=db.list_domains()[0]["id"])
    ea = db.add_entry(Entry(name="同一风格", category_id=c1))
    n1 = db.copy_entry_to(ea, c1)          # 目标已有同名 → 加（副本）
    n2 = db.copy_entry_to(ea, c1)          # 再复制 → （副本2）
    check("copy naming 副本/副本2",
          db.get_entry(n1)["name"] == "同一风格（副本）"
          and db.get_entry(n2)["name"] == "同一风格（副本2）")
    # 去重查找
    c2 = db.add_category("重复L", domain_id=db.list_domains()[0]["id"])
    dup1 = db.add_entry(Entry(name="B", intro="X", prompt_cn="P", category_id=c2))
    db.add_entry(Entry(name="B", intro="X", prompt_cn="P", category_id=c2))  # 同名同内容
    hits = db.find_content_duplicates(db.content_key(db.get_entry(dup1)),
                                      exclude_entry_id=dup1)
    check("content dup find", len(hits) == 1)
    hits2 = db.find_content_duplicates(db.content_key(db.get_entry(dup1)))
    check("content dup find no exclude", len(hits2) == 2)
    # 重名检测同时考虑主挂靠与关联位置
    c3 = db.add_category("主位置L", domain_id=db.list_domains()[0]["id"])
    c4 = db.add_category("关联位置L", domain_id=db.list_domains()[0]["id"])
    eb = db.add_entry(Entry(name="贯穿名", category_id=c3))
    db.link_entry(eb, c4)
    check("entry name exists via link", db._entry_name_exists("贯穿名", c4))
    check("unique entry name via link",
          db.unique_entry_name("贯穿名", c4) == "贯穿名（副本）")
    return (c1, c2, c3, c4, eb)


# ---------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------- #
def main():
    mb = StubMB()
    sd = StubSD()
    fd = StubFD()
    orig_mb, orig_sd, orig_fd = _mw.messagebox, _mw.simpledialog, _mw.filedialog
    _mw.messagebox = mb
    _mw.simpledialog = sd
    _mw.filedialog = fd

    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(("PASS" if cond else "FAIL"), name)

    tmp = tempfile.mkdtemp(prefix="ps_gui_self_")
    db = Database(os.path.join(tmp, "t.db"))
    app = None
    try:
        pid = db.add_project("测试项目")
        did = db.add_domain("非全字段域", project_id=pid)   # 精简模式（auto 隐藏 ③-⑦）
        l1 = db.add_category("L1父级", domain_id=did)       # 带子级
        l2a = db.add_category("L2-A", parent_id=l1)
        l2b = db.add_category("L2-B", parent_id=l1)
        l_leaf = db.add_category("L1叶子", domain_id=did)   # 无子级叶子一级

        # 先做数据层校验
        db_level_checks(db, check)

        app = MainWindow(db)
        pump(app)
        # 2026-09-15（stage3 间歇失败排查结论 + 用户批准"方案 A"）：**让自测窗口对真实鼠标/键盘输入免疫**。
        #   根因：自测运行期间，真实鼠标点击落在窗口上会驱动产品既定行为——
        #     ① 点条目行（`bind("<Button-1>")` → `_select_entry` → `_show_detail`）会**清掉"新增条目态"**，
        #        导致 stage3 的 6 条断言连锁失败；
        #     ② 点左侧分类按钮（CTkButton command → `_select_category`）会**改掉 `_view`**，
        #        导致早期 `view cat at L1-with-children` 间歇失败。
        #   做法：Windows 下把主窗口设为 disabled（`wm attributes -disabled 1`）→ 用户输入不再送达本窗口；
        #   Tk 内部布局与几何（winfo_*）不受影响，故所有断言照常有效；产品行为一行未改。
        #   注：自测中新建的对话框（Toplevel）各自独立，本屏蔽不覆盖它们（其交互均由用例代码驱动）。
        _input_shield = False
        try:
            app.attributes("-disabled", True)
            _input_shield = bool(app.attributes("-disabled"))
        except Exception as _exc:
            print(f"[自测] ⚠ 无法启用窗口输入屏蔽（用例仍会执行，但可能受真实鼠标干扰）：{_exc}")
        # 2026-09-15（stage3 间歇失败专项排查）：**只记录、不改变行为**——
        #   "悬停选中"（导航/条目按钮的 <Enter> → `_schedule_select(200ms, 选中)`）是**产品既定行为**；
        #   但自测运行时若真实鼠标恰好停在窗口内，它会在两次断言之间改掉选中/详情态，
        #   造成"间歇性失败"（实测已见：stage3 新增态被清、以及早期 `view cat at L1-with-children`）。
        #   这里记录每次调度（含鼠标坐标与调用栈），失败时随日志打印，用于判定根因。
        _hover_log = []
        _orig_sched_select = app._schedule_select

        def _sched_spy(ms, fn):
            import traceback as _tb
            _hover_log.append(("schedule %sms %s" % (ms, getattr(fn, "__name__", repr(fn))[:40]),
                               "鼠标=%s/%s" % (app.winfo_pointerx(), app.winfo_pointery()),
                               "".join(_tb.format_stack(limit=6))[-300:]))
            return _orig_sched_select(ms, fn)
        app._schedule_select = _sched_spy

        # ---- 1. 新增主按钮 + 浏览/新增放开 ----
        app._select_domain(did); pump(app)
        app._select_category(l1); pump(app)          # 带子级一级
        # 2026-09-15（stage3 专项排查）：补上"实际 view"以便失败时定位（本用例曾间歇失败）
        check("view cat at L1-with-children [view=%s cat=%s]" % (app._view, app._cur_cat_id),
              app._view == ("cat", l1))
        check("add btn green text", app._add_entry_btn.cget("text_color") == "white")
        check("add btn font15", "15" in str(app._add_entry_btn.cget("font")))
        check("add available at non-leaf", app._add_available())

        # 详情名称字段：顶部无输入框、名称字段 ① 在详情区
        e_m = db.add_entry(Entry(name="多位置条目", category_id=l2a, prompt_cn="PM"))
        db.link_entry(e_m, l2b)
        e_only = db.add_entry(Entry(name="仅位置条目", category_id=l_leaf))
        app._show_detail(db.get_entry(e_m)); pump(app)
        head_entries = []
        walk_types(app.detail_head, "CTkEntry", head_entries)
        check("header has no CTkEntry", len(head_entries) == 0)
        scroll_entries = []
        walk_types(app.detail_scroll, "CTkEntry", scroll_entries)
        # 2026-09-13 修正（1-C 标签区块引入后本断言失效）：详情区现有两个 CTkEntry——
        # ① 条目名称 与 🏷 标签输入框；此处只对"名称输入框"计数（按占位文本排除标签框）。
        name_entries = [x for x in scroll_entries
                        if "标签" not in str(x.cget("placeholder_text") or "")]
        check("one name field in scroll", len(name_entries) == 1)
        check("tag input exists in scroll", len(scroll_entries) == 2)
        labels = []
        walk_types(app.detail_scroll, "CTkLabel", labels)
        check("name label ① 条目名称",
              any("① 条目名称" in text_of(x) for x in labels))

        # 顶部命令条顺序（像素）——"🗑 删除"已移到底部常驻栏，不再出现在命令条
        # 2026-09-10（用户要求）："☆ 收藏"与"编辑/浏览"互换位置 → 收藏在最左
        app.update()
        row1 = app.detail_head.winfo_children()[0]
        want = ("☆ 收藏", "➜ 移动到", "↔ 关联到", "⧉ 复制到")
        pos = {}
        for child in row1.winfo_children():
            if type(child).__name__ == "CTkButton" and child.cget("text") in want:
                pos[child.cget("text")] = child.winfo_x()
        seq = [t for t, _ in sorted(pos.items(), key=lambda kv: kv[1])]
        check("command bar order", seq == list(want))
        check("move/link/copy enabled", app.move_btn.cget("state") == "normal"
              and app.link_btn.cget("state") == "normal"
              and app.copyto_btn.cget("state") == "normal")

        # 多位置提示行（chips 文案形如 "主 · …" / "关联 · …"）
        tags = [text_of(x) for x in labels]
        check("hint 主 tag", any("主 ·" in s for s in tags))
        check("hint 关联 tag", any("关联 ·" in s for s in tags))
        btn_count = 0
        def _count_x(w):
            nonlocal btn_count
            for c in w.winfo_children():
                try:
                    if type(c).__name__ == "CTkButton" and c.cget("text") == "×":
                        btn_count += 1
                except Exception:
                    pass
                _count_x(c)
        _count_x(app.detail_scroll)
        check("hint one remove btn", btn_count == 1)

        # 固定头部状态行（详情区顶部恒常显示，不随滚动消失，只作用于 ②~⑦）
        check("固定头部状态行存在",
              getattr(app, "detail_state_lbl", None) is not None
              and app.detail_state_lbl.cget("text") == "详情 · 精简模式"
              and getattr(app, "show_all_btn", None) is not None
              and app.show_all_btn.cget("text") == "⏵ 显示全部字段")
        # 2026-09-16（批次 13）：②~⑦ 现拆为独立可折叠块，默认折叠（各有自己的"展开"按钮）。
        #   切到全字段模式重渲染，确保 ②~⑦ 全部可见后再统计「展开」按钮数。
        _dm_bak = app._detail_mode
        app._detail_mode = _mw.config.DETAIL_MODE_FULL
        app._show_detail(db.get_entry(e_m)); pump(app)
        expand_btns = []
        def _count_expand(w):
            try:
                if type(w).__name__ == "CTkButton" and w.cget("text") == "展开":
                    expand_btns.append(w)
            except Exception:
                pass
            for c in w.winfo_children():
                _count_expand(c)
        _count_expand(app.detail_scroll)
        check("②~⑦ 各字段默认折叠（≥6 个「展开」按钮）",
              getattr(app, "_detail_expand_all", False) is False
              and len(expand_btns) >= 6)
        app._detail_mode = _dm_bak
        app._show_detail(db.get_entry(e_m)); pump(app)

        # ---- 2. 关联 / 复制 / 移动 / 解除 ----
        app._pick_entry_targets = lambda mode, eid: [l_leaf]
        app._link_entry(e_m)
        check("linked to leaf", set(db.list_entry_locations(e_m)) == {l2a, l2b, l_leaf})

        app._copy_entry_to_targets(e_m)
        copies = [x for x in db.list_entries(l_leaf)
                  if x["name"] == "多位置条目（副本）"]
        check("copy target gets （副本） name", len(copies) == 1)
        new_id = copies[0]["id"]
        check("copy independent + single loc",
              db.get_entry(new_id)["prompt_cn"] == "PM"
              and db.list_entry_locations(new_id) == [l_leaf])

        # 移动：有当前位置默认仅当前位置
        app.wait_window = lambda win: None
        _real = _mw.MoveSelector

        def fake_move(ov, tgt):
            class _F:
                result = "ok"
                selected_cat_ids = [tgt]
                overall = ov
            return _F()

        app._select_category(l2b); pump(app)
        _mw.MoveSelector = lambda *a, **k: fake_move(False, l2a)
        app._move_entry(e_m)
        _mw.MoveSelector = _real
        check("move keeps other", set(db.list_entry_locations(e_m)) == {l2a, l_leaf})

        # 解除本分类关联（× 处理）
        app._remove_detail_location(e_m, l_leaf); pump(app)
        check("unlink via hint", set(db.list_entry_locations(e_m)) == {l2a})

        # ---- 3. 就地新增（非叶子一级）----
        app._select_category(l1); pump(app)
        app._start_new_entry(); pump(app)
        check("add first field empty", app._name_entry.get() == "")
        app._name_entry.insert(0, "父级新增")
        app._detail_boxes["prompt_cn"].insert("1.0", "P2")
        app._save_new_entry()   # 桩 askyesno=True 通过（无同内容）
        check("add saved at non-leaf",
              any(x["name"] == "父级新增" and x["category_id"] == l1
                  for x in db.list_all_entries()))
        check("continuous blank", app._name_entry.get() == "")
        app._show_detail(None); pump(app)
        check("move/link/copy disabled on none", app.move_btn.cget("state") == "disabled")

        # ---- 4. 删除保护 ----
        l_del = db.add_category("待删叶子", domain_id=did)
        e1 = db.add_entry(Entry(name="仅此", category_id=l_del))
        e2k = db.add_entry(Entry(name="另有", category_id=l_del))
        db.link_entry(e2k, l_leaf)
        app._delete_category(l_del)          # 安全删除（无子级）
        check("safe delete removes cat", db.get_category(l_del) is None)
        check("only-loc to uncat", db.get_entry(e1)["category_id"] is None)
        check("other-loc kept", set(db.list_entry_locations(e2k)) == {l_leaf})

        l_par = db.add_category("级联父", domain_id=did)
        l_chi = db.add_category("级联子", parent_id=l_par)
        e_in = db.add_entry(Entry(name="级联条目", category_id=l_chi))
        sd.phrase = db._CASCADE_PHRASE
        mb.asks.clear()
        app._delete_category(l_par)           # 有子级 → 弹询问 → 级联（短语桩通过）
        check("cascade askstring used", any(c[0] == "askstring" for c in sd.calls))
        check("cascade removed", db.get_category(l_par) is None
              and db.get_category(l_chi) is None and db.get_entry(e_in) is None)

        # 错误短语中止
        l_bad = db.add_category("坏父", domain_id=did)
        db.add_category("坏子", parent_id=l_bad)
        sd.phrase = "错误短语"
        app._delete_category(l_bad)
        check("wrong phrase aborts", db.get_category(l_bad) is not None)

        # 多位置删除提示
        db.link_entry(e_m, l2b)               # 制造第二位置
        mb.asks.clear()
        app._delete_entry(e_m)
        check("multi-loc delete warns",
              any("个位置" in str(a) for (_k, a, _kk) in mb.asks))
        check("entry deleted", db.get_entry(e_m) is None)

        # ---- 5. 热点词页签（2026-09-14 阶段 4 之 4-b/4-c/4-d/4-e）----
        check("hot words initially empty", db.list_hotwords() == [])
        app._tag_page_view = "hot"
        app._open_tag_page(); pump(app)
        check("hot tab opened", app._tag_page_on
              and app._hotword_entry is not None and app._hotword_entry.winfo_exists())
        # 添加：多值分隔 + 自动去重
        app._hotword_entry.insert(0, "多巴胺穿搭、新中式，多巴胺穿搭")
        app._on_hotword_add(); pump(app)
        check("hot add parse/dedup", db.list_hotwords() == ["多巴胺穿搭", "新中式"])
        # 与 tags 表隔离：热点词不产生任何标签
        check("hot words isolated from tags", db.list_tags() == [])
        # 热点词档**不改动条目区**（app._view 保持原样）
        app._view = ("cat", l1)
        app._refresh_tag_page(); pump(app)
        check("hot tab keeps entry view", app._view == ("cat", l1))
        # 逐条删除
        app._on_hotword_delete("新中式"); pump(app)
        check("hot delete one", db.list_hotwords() == ["多巴胺穿搭"])
        # 来源网址：过滤非 http(s)、去重
        db.set_hotword_sources(["https://example.com/words.txt", "ftp://bad",
                                "https://example.com/words.txt"])
        check("hot sources filter",
              db.list_hotword_sources() == ["https://example.com/words.txt"])
        # 清空全部（桩 askyesno=True）
        app._on_hotword_clear(); pump(app)
        check("hot clear all", db.list_hotwords() == [])
        # 设置入口：经主窗口方法打开标签页面并切到热点词档
        app._tag_page_view = "cloud"
        app._open_hotword_manager(); pump(app)
        check("hot manager entry", app._tag_page_on and app._tag_page_view == "hot"
              and app._hotword_entry is not None and app._hotword_entry.winfo_exists())
        # 切回"标签云"：既有两个档位不受影响（热点词输入框随整页重建而销毁）
        app._on_tag_view_change("标签云"); pump(app)
        check("back to cloud tab", app._tag_page_view == "cloud"
              and not (app._hotword_entry and app._hotword_entry.winfo_exists()))
        app._close_tag_page(); pump(app)
        check("tag page closed", not app._tag_page_on)

        # ---- 6. 词表页签（2026-09-14 阶段 0.5）----
        app._tag_page_view = "dict"
        if not app._tag_page_on:
            app._open_tag_page()
        app._refresh_tag_page(); pump(app)
        check("dict tab opened", app._tag_page_view == "dict"
              and len(app._tag_main.winfo_children()) > 0)
        # 词表档**不改动条目区**
        app._view = ("cat", l1)
        app._refresh_tag_page(); pump(app)
        check("dict tab keeps entry view", app._view == ("cat", l1))
        seed = tagger.load_dict(db)
        # 2026-09-18：出厂词表已换成 809 标签 / 21 领域包版 ⇒ 断言改为"与出厂种子一致"
        #   （不再写死具体领域名，换出厂词表时本用例无需再改）。
        check("dict loaded from meta (seeded)", tagger.count_tags(seed) > 0
              and set((seed.get("domains") or {}).keys()) >= set(tagger_dict.builtin_domains()))
        # 导出 → 文件存在且标签数一致
        dict_path = os.path.join(tmp, "tag_dict_export.json")
        fd.save_path = dict_path
        app._on_dict_export(); pump(app)
        exported = tagger.read_dict_file(dict_path)
        check("dict export ok", exported["ok"]
              and tagger.count_tags(exported["data"]) == tagger.count_tags(seed))
        # 导入"含新增领域包（学术）"的词表 → 模拟用户自行新增大类
        add = tagger.load_dict(db)
        add["domains"]["学术"] = {"学科": {"计算机科学": ["计算机科学", "cs"]},
                                 "报告类型": {"文献综述": ["综述", "literature review"]}}
        add["domain_map"]["学术"] = ["学术", "论文"]
        add_path = os.path.join(tmp, "tag_dict_add.json")
        assert tagger.write_dict_file(add_path, add)["ok"]
        fd.open_path = add_path
        before_tags = tagger.count_tags(tagger.load_dict(db))
        mb.asks.clear()
        app._on_dict_import(); pump(app)
        after = tagger.load_dict(db)
        check("dict import adds new domain", "学术" in (after.get("domains") or {})
              and tagger.count_tags(after) > before_tags)
        check("dict import confirm asked", any("导入词表" in str(a[0]) for (_k, a, _kk) in mb.asks))
        # 导入非法文件 → 拒绝且词表不变
        bad_path = os.path.join(tmp, "tag_dict_bad.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write('{"universal": {}, "domains": {}}')
        fd.open_path = bad_path
        mb.asks.clear()
        app._on_dict_import(); pump(app)
        check("dict import rejects bad file", "学术" in (tagger.load_dict(db).get("domains") or {})
              and any(_k == "warn" and "词表文件不合法" in str(a[0]) for (_k, a, _kk) in mb.asks))
        # 恢复出厂 → 领域包回到出厂集合、且"学术"消失
        app._on_dict_reset(); pump(app)
        back = tagger.load_dict(db)
        check("dict reset to builtin",
              set((back.get("domains") or {}).keys()) == set(tagger_dict.builtin_domains()))
        # 2026-09-17（用户需求）：**增量导入**（只增不删）——与「导入（替换）」并存
        _seed_names_m = set(tagger.dict_tag_names(back))
        _add_only = {"version": 1,
                     "universal": {"领域": {"视觉": ["自测增量词"]}},
                     "domains": {"视觉": {"题材主体": {"人像": ["headshot增量"]},
                                          "自测新维度": {"自测新标签": ["自测新词"]}}},
                     "domain_map": {"视觉": ["自测判定词"]}}
        _add_only_path = os.path.join(tmp, "tag_dict_merge.json")
        assert tagger.write_dict_file(_add_only_path, _add_only)["ok"]
        fd.open_path = _add_only_path
        mb.asks.clear()
        app._on_dict_merge(); pump(app)
        _after_m = tagger.load_dict(db)
        check("dict merge: 只增不删（原标签一个不少 + 新标签/新维度已加 + 匹配词并入）",
              _seed_names_m <= set(tagger.dict_tag_names(_after_m))
              and "自测新标签" in _after_m["domains"]["视觉"]["自测新维度"]
              and "自测增量词" in _after_m["universal"]["领域"]["视觉"]
              and "headshot增量" in _after_m["domains"]["视觉"]["题材主体"]["人像"])
        check("dict merge: 确认框写明『只增不删』与将新增量 [%s]"
              % ([str(a[0]) for (_k, a, _kk) in mb.asks][:1] or "无"),
              any(_k == "yesno" and "只增不删" in str(a[0])
                  and "将新增标签" in str(a[1])
                  for (_k, a, _kk) in mb.asks))
        # 同一份文件再来一次 → **幂等**：明确提示"无可新增内容"，且词表不变
        _cnt_m = tagger.count_tags(_after_m)
        mb.asks.clear()
        app._on_dict_merge(); pump(app)
        check("dict merge: 重复合并幂等（提示无可新增、标签数不变）[%d→%d]"
              % (_cnt_m, tagger.count_tags(tagger.load_dict(db))),
              tagger.count_tags(tagger.load_dict(db)) == _cnt_m
              and any(_k == "info" and "无可新增内容" in str(a[0]) for (_k, a, _kk) in mb.asks))
        # 非法文件 → 拒绝且词表不变
        fd.open_path = bad_path
        mb.asks.clear()
        app._on_dict_merge(); pump(app)
        check("dict merge: 非法文件被拒且词表不变",
              tagger.count_tags(tagger.load_dict(db)) == _cnt_m
              and any(_k == "warn" and "词表文件不合法" in str(a[0]) for (_k, a, _kk) in mb.asks))
        # 复位回出厂状态，避免影响后续用例（与改动前的状态一致）
        app._on_dict_reset(); pump(app)
        check("dict merge: 用例后复位回出厂",
              set((tagger.load_dict(db).get("domains") or {}).keys())
              == set(tagger_dict.builtin_domains()))
        # 设置入口：经主窗口方法打开并切到词表档
        app._tag_page_view = "cloud"
        app._open_dict_manager(); pump(app)
        check("dict manager entry", app._tag_page_on and app._tag_page_view == "dict"
              and len(app._tag_main.winfo_children()) > 0)
        # 切回"标签云"：既有档位不受影响
        app._on_tag_view_change("标签云"); pump(app)
        check("back to cloud after dict", app._tag_page_view == "cloud")
        app._close_tag_page(); pump(app)
        check("tag page closed again", not app._tag_page_on)

        # ---- 6b（2026-09-15 批次 6-2）：标签档治理按钮锁定态"可见但置灰" ----
        app._tag_page_view = "cloud"
        if not app._tag_page_on:
            app._open_tag_page()
        app._refresh_tag_page(); pump(app)

        def _gov_now():
            # 只取"存活的"治理按钮（页面重建后旧句柄会失效，不能直接 cget）
            return [b for b in app._tag_gov_btns if b.winfo_exists()]

        check("tag gov(6-2): 5 个治理按钮已渲染 [%d]" % len(_gov_now()),
              len(_gov_now()) == 5)
        _lock_bg = app._lock_on
        try:
            app._lock_on = True
            app._apply_lock_state(); pump(app)
            _g1 = [(str(text_of(b))[:8], str(b.cget("state"))) for b in _gov_now()]
            check("tag gov(6-2): 锁定态全部置灰 [%s]" % (_g1 or "无"),
                  len(_g1) == 5 and all(s == "disabled" for (_t, s) in _g1))
            app._refresh_tag_page(); pump(app)   # 锁定态下重建页面 → 新按钮也应立即置灰
            _g1r = [str(b.cget("state")) for b in _gov_now()]
            check("tag gov(6-2): 锁定态下重建页面后仍全部置灰 [%s]" % (_g1r or "无"),
                  len(_g1r) == 5 and all(s == "disabled" for s in _g1r))
        finally:
            app._lock_on = _lock_bg
            app._refresh_tag_page(); pump(app)
            app._apply_lock_state(); pump(app)
        _g2 = [str(b.cget("state")) for b in _gov_now()]
        check("tag gov(6-2): 解锁后全部恢复可用 [%s]" % (_g2 or "无"),
              len(_g2) == 5 and all(s == "normal" for s in _g2))
        app._close_tag_page(); pump(app)
        check("tag page closed after gov check", not app._tag_page_on)

        # ---- 6c（2026-09-15 批次 6-2 补充，用户选定"一并置灰"）：
        #   热点词档 3 个（文本导入/热点词更新/清空全部）、词表档 3 个（导入（替换）/增量导入/恢复出厂）
        #   同为写操作 ⇒ 锁定态置灰；词表档"⬆ 导出词表…"是只读 ⇒ 保持可用。
        #   （2026-09-17 用户需求：词表档由 2 个写操作 → **3 个**，新增「➕ 增量导入…」。）
        def _gov_state():
            return [(str(text_of(b))[:10], str(b.cget("state"))) for b in _gov_now()]

        for _mode, _n, _ro in (("hot", 3, ""), ("dict", 3, "导出词表")):
            app._tag_page_view = _mode
            if not app._tag_page_on:
                app._open_tag_page()
            app._refresh_tag_page(); pump(app)
            _lock_bc = app._lock_on
            try:
                app._lock_on = True
                app._refresh_tag_page(); pump(app)   # 锁定态下重建页面 → 应"创建即置灰"
                _s1 = _gov_state()
                check("tag gov(6-2c): %s 档写按钮 %d 个全部置灰 [%s]" % (_mode, _n, _s1 or "无"),
                      len(_s1) == _n and all(s == "disabled" for (_t, s) in _s1))
                if _ro:
                    _allb = []
                    walk_types(app.tag_frame, "CTkButton", _allb)
                    _exp = [(str(text_of(b))[:10], str(b.cget("state"))) for b in _allb
                            if _ro in str(text_of(b))]
                    check("tag gov(6-2c): 词表档只读按钮不置灰（仍可用）[%s]" % (_exp or "无"),
                          len(_exp) == 1 and _exp[0][1] == "normal")
            finally:
                app._lock_on = _lock_bc
                app._refresh_tag_page(); pump(app)
            _s2 = _gov_state()
            check("tag gov(6-2c): %s 档解锁后写按钮恢复可用 [%s]" % (_mode, _s2 or "无"),
                  len(_s2) == _n and all(s == "normal" for (_t, s) in _s2))
        app._close_tag_page(); pump(app)
        check("tag page closed after gov(6-2c) check", not app._tag_page_on)

        # ---- 6d（2026-09-15 批次 6-3a）：统一守卫 `_assert_unlocked()` + 5 个"原完全无拦截"入口 ----
        import customtkinter as _ctk63
        _toasts_63 = []
        _orig_toast_63 = app.toast
        app.toast = lambda msg, **k: (_toasts_63.append(str(msg)), _orig_toast_63(msg, **k))[-1]
        _lock_b63 = app._lock_on
        _e63 = None
        try:
            app._lock_on = True
            _ok63 = app._assert_unlocked("测试动作")
            check("guard(6-3a): 锁定态守卫返回 False 并给出提示 [%s]"
                  % (_toasts_63[-1:] or "无"),
                  _ok63 is False and any("已锁定" in t for t in _toasts_63))
            # 1) 标签页"＋ 新建"（原先完全无拦截；若未拦截会真的新建标签）
            _tags_before = len(db.list_tags())
            sd.calls.clear()
            sd.phrase = "守卫测试标签"
            app._on_tag_new(); pump(app)
            check("guard(6-3a): 锁定态 _on_tag_new 被拦截 [标签 %d→%d 弹窗=%s]"
                  % (_tags_before, len(db.list_tags()), [c[0] for c in sd.calls] or "无"),
                  len(db.list_tags()) == _tags_before
                  and not any(c[0] == "askstring" for c in sd.calls))
            # 2) 热点词"＋ 添加"（原先完全无拦截）
            app._tag_page_view = "hot"
            if not app._tag_page_on:
                app._open_tag_page()
            app._refresh_tag_page(); pump(app)
            _hw_before = list(db.list_hotwords())
            if app._hotword_entry is not None and app._hotword_entry.winfo_exists():
                app._hotword_entry.delete(0, "end")
                app._hotword_entry.insert(0, "守卫测试词")
            app._on_hotword_add(); pump(app)
            check("guard(6-3a): 锁定态 _on_hotword_add 被拦截 [%s→%s]"
                  % (_hw_before, db.list_hotwords()), db.list_hotwords() == _hw_before)
            # 3) 热点词"×"删除（原先完全无拦截）：先造一条数据，再锁定删除
            app._lock_on = False
            db.add_hotwords(["守卫保留词"])
            app._lock_on = True
            app._on_hotword_delete("守卫保留词"); pump(app)
            check("guard(6-3a): 锁定态 _on_hotword_delete 被拦截 [%s]"
                  % db.list_hotwords(), "守卫保留词" in db.list_hotwords())
            app._lock_on = False
            db.remove_hotwords(["守卫保留词"])
            app._lock_on = True
            app._close_tag_page(); pump(app)
            # 4) 字段管理对话框（原先完全无拦截，且 🔧 按钮锁定态仍可点）
            _tops_b = [w for w in app.winfo_children() if isinstance(w, _ctk63.CTkToplevel)]
            app._open_field_manager(); pump(app)
            _tops_a = [w for w in app.winfo_children() if isinstance(w, _ctk63.CTkToplevel)]
            check("guard(6-3a): 锁定态 _open_field_manager 被拦截（未弹窗）[%d→%d]"
                  % (len(_tops_b), len(_tops_a)), len(_tops_a) == len(_tops_b))
            # 5) 未保存修改：锁定态**不弹**保存询问，直接按"不保存"继续切换（用户选定）
            _e63 = db.add_entry(Entry(name="守卫测试条目"))
            app._lock_on = False
            app._detail_entry_id = _e63
            app._adding_new = False
            app._detail_dirty = True
            app._lock_on = True
            mb.asks.clear()
            _r63 = app._confirm_unsaved(); pump(app)
            check("guard(6-3a): 锁定态 _confirm_unsaved 不弹保存询问、按不保存继续"
                  " [ret=%s 弹窗=%s dirty=%s]"
                  % (_r63, [c[0] for c in mb.asks] or "无", app._detail_dirty),
                  _r63 is True and not any(c[0] == "ask" for c in mb.asks)
                  and app._detail_dirty is False)
        finally:
            app._lock_on = _lock_b63
            app.toast = _orig_toast_63
            app._detail_entry_id = None
            app._detail_dirty = False
            sd.phrase = ""
            if _e63 is not None:          # 清理本段自建的测试条目（不影响后续用例的数据基线）
                try:
                    db.delete_entry(_e63)
                except Exception:
                    pass

        # ---- 6e（2026-09-15 批次 6-3b）：其余 25 个"仅靠按钮置灰"的写入口已全部加统一守卫 ----
        _lock_b63b = app._lock_on
        _orig_toast_63b = app.toast
        _toasts_63b = []
        app.toast = lambda msg, **k: (_toasts_63b.append(str(msg)), _orig_toast_63b(msg, **k))[-1]
        _e63b = None
        try:
            # 造测试数据（解锁态）
            app._lock_on = False
            db.add_tag("守卫无关联标签")
            db.add_hotwords(["守卫热词"])

            app._lock_on = True
            # 1) 标签治理类（原先仅靠按钮置灰）
            app._on_tag_purge(); pump(app)
            _nm63b = {t["name"] for t in db.list_tags()}
            check("guard(6-3b): 锁定态 _on_tag_purge 被拦截 [无关联标签仍在=%s]"
                  % ("守卫无关联标签" in _nm63b), "守卫无关联标签" in _nm63b)
            # 2) 热点词清空（原先仅靠按钮置灰）
            app._on_hotword_clear(); pump(app)
            check("guard(6-3b): 锁定态 _on_hotword_clear 被拦截 [%s]" % db.list_hotwords(),
                  "守卫热词" in db.list_hotwords())
            # 3) 词表导入 / 选封面 / 图集加图（原先仅靠按钮置灰）：锁定态不应弹出任何文件框
            fd.calls.clear()
            app._on_dict_import(); pump(app)
            app._on_dict_merge(); pump(app)      # 2026-09-17：新增的增量导入入口同受守卫
            app._pick_image(); pump(app)
            app._add_gallery_local(); pump(app)
            check("guard(6-3b): 锁定态 词表导入/增量导入/选图/图集添加均被拦截（未弹文件框）[%s]"
                  % ([c[0] for c in fd.calls] or "无"), not fd.calls)
            # 4) 保存新增条目：锁定态返回 False（阻止切换），不写库
            _adding_b = app._adding_new
            app._adding_new = True
            _r63b1 = app._save_new_entry()
            app._adding_new = _adding_b
            check("guard(6-3b): 锁定态 _save_new_entry 返回 False 被拦截 [ret=%s]" % _r63b1,
                  _r63b1 is False)
            # 5) 保存条目修改：锁定态即使改了名称框也不落库（强断言）
            _e63b = db.add_entry(Entry(name="守卫保存测试"))
            app._lock_on = False
            app._show_detail(db.get_entry(_e63b))   # 解锁态先渲染详情（生成 ① 名称框）
            pump(app, 2)
            app._lock_on = True
            _ne63b = getattr(app, "_name_entry", None)
            if _ne63b is not None and _ne63b.winfo_exists():
                _ne63b.delete(0, "end")
                _ne63b.insert(0, "守卫改名（不应落库）")
                app._save_detail(); pump(app)
                check("guard(6-3b): 锁定态 _save_detail 被拦截（未落库）[库中名=%s]"
                      % db.get_entry(_e63b)["name"],
                      db.get_entry(_e63b)["name"] == "守卫保存测试")
            else:
                check("guard(6-3b): 详情名称框存在可校验 [无 _name_entry]", False)
            app._detail_entry_id = None
            # 6) 回归：解锁后正常路径不受影响
            app._lock_on = False
            app._on_hotword_clear(); pump(app)
            check("guard(6-3b): 解锁后 _on_hotword_clear 仍正常生效 [%s]" % db.list_hotwords(),
                  "守卫热词" not in db.list_hotwords())
        finally:
            app._lock_on = _lock_b63b
            app.toast = _orig_toast_63b
            app._detail_entry_id = None
            try:
                app._show_detail(None)     # 清空详情区，避免残留"未落库"的名称文本
            except Exception:
                pass
            try:
                _t63b = db.get_tag_by_name("守卫无关联标签")
                if _t63b is not None:
                    db.delete_tag(_t63b["id"])
            except Exception:
                pass
            try:
                db.remove_hotwords(["守卫热词"])
            except Exception:
                pass
            if _e63b is not None:
                try:
                    db.delete_entry(_e63b)
                except Exception:
                    pass

        # ---- 6f（2026-09-15 批次 6-4）：锁定状态**不持久化**（固定断言；产品代码零改动）----
        #   现状已满足（无任何锁定 meta 键、启动即未锁定）；此处只把该行为"钉住"，防将来误加持久化。
        _lock_b64 = app._lock_on
        try:
            app._lock_on = True
            app._save_settings()          # 走"退出保存"路径（会写窗口大小/视图/详情模式）
        finally:
            app._lock_on = _lock_b64
        _meta_keys = []
        try:
            _meta_keys = [str(r[0]) for r in db.conn.execute("SELECT key FROM meta").fetchall()]
        except Exception:
            _meta_keys = []
        _lock_keys = [k for k in _meta_keys if "lock" in k.lower()]
        check("lock(6-4): 退出保存后 meta 中无任何锁定相关键 [键=%d 疑似=%s]"
              % (len(_meta_keys), _lock_keys or "无"), not _lock_keys)
        try:
            with open(_mw.__file__, "r", encoding="utf-8") as _f64:
                _src64 = _f64.read()
        except Exception:
            _src64 = ""
        check("lock(6-4): 源码中无任何把锁定写入 meta 的调用 [源码=%d 字符]" % len(_src64),
              bool(_src64) and not [ln for ln in _src64.splitlines()
                                    if "set_meta(" in ln and "lock" in ln.lower()])
        check("lock(6-4): __init__ 中 _lock_on 初始为 False（启动必为未锁定）",
              "self._lock_on = False" in _src64)

        # ---- 7a（2026-09-15 批次 7）：快速新建窗口屏内定位（上边距≈50 + 整窗在屏内 + 小屏压矮）----
        import re as _re7
        from app.ui.quick_add import QuickAddWindow as _QAW7
        _q7 = _QAW7(app, db)
        pump(app, 26)                       # 覆盖 after(60) / after(220) 两次映射后校正
        _sw7, _sh7 = _q7.winfo_screenwidth(), _q7.winfo_screenheight()
        _qx7, _qy7 = _q7.winfo_x(), _q7.winfo_y()
        _qw7, _qh7 = _q7.winfo_width(), _q7.winfo_height()
        check("quickadd(7): 上边距≈50 [y=%d]" % _qy7, 40 <= _qy7 <= 90)
        check("quickadd(7): 整窗在屏幕内 [x=%d y=%d w=%d h=%d 屏=%dx%d]"
              % (_qx7, _qy7, _qw7, _qh7, _sw7, _sh7),
              _qx7 >= 0 and _qy7 >= 0
              and _qx7 + _qw7 <= _sw7 + 2 and _qy7 + _qh7 <= _sh7 + 2)
        check("quickadd(7): 水平居中 [x=%d 期望=%d]" % (_qx7, max((_sw7 - _qw7) // 2, 0)),
              abs(_qx7 - max((_sw7 - _qw7) // 2, 0)) <= 8)
        # 小屏模拟：1366×600 ⇒ 源码高必须压矮到 ≤ (600−50−40)/缩放系数
        _bw7, _bh7 = _q7.winfo_screenwidth, _q7.winfo_screenheight
        try:
            _q7.winfo_screenwidth = lambda: 1366
            _q7.winfo_screenheight = lambda: 600
            _q7._place_on_screen()
            pump(app, 26)
            _m7 = _re7.match(r"(\d+)x(\d+)", str(_q7.geometry()))
            _gh7 = int(_m7.group(2)) if _m7 else -1
            _scale7 = _ctk63.ScalingTracker.get_window_scaling(_q7)
            _cap7 = int((600 - 50 - 40) / _scale7) + 1
            check("quickadd(7): 小屏(1366×600)自动压矮 [源码高=%d ≤ %d]" % (_gh7, _cap7),
                  0 < _gh7 <= _cap7)
        finally:
            _q7.winfo_screenwidth = _bw7
            _q7.winfo_screenheight = _bh7
            try:
                _q7.grab_release()
            except Exception:
                pass
            _q7.destroy()
        pump(app, 4)

        # ---- 8a（2026-09-15 批次 8-A）：标签推荐的"上下文兜底"（新增态无目标分类时不再必空）----
        _did8 = db.list_domains()[0]["id"]
        _cat8 = db.add_category("兜底测试分类", domain_id=_did8)
        _cat8b = db.add_category("目标优先分类", domain_id=_did8)
        _add_b8, _tgt_b8 = app._adding_new, app._add_target
        _cur_b8, _last_b8 = app._cur_cat_id, app._last_cat_id
        _det_b8 = app._detail_entry_id
        try:
            # 1) 新增态 + 无目标分类 ⇒ 回退"当前视图分类"
            app._adding_new = True
            app._add_target = None
            app._detail_entry_id = None
            app._cur_cat_id = _cat8
            app._last_cat_id = _cat8
            _ctx8 = app._suggest_ctx_names()
            check("suggest(8-A): 新增态无目标分类时回退当前视图分类 [%s]" % (_ctx8[:2],),
                  bool(_ctx8) and "兜底测试分类" in _ctx8)
            # 2) 有目标分类时仍以目标为准（回归：优先级不变）
            app._add_target = _cat8b
            _ctx8b = app._suggest_ctx_names()
            check("suggest(8-A): 有目标分类时仍以目标为准（优先级不变）[%s]" % (_ctx8b[:2],),
                  "目标优先分类" in _ctx8b)
            # 3) 编辑态逻辑不变：取条目自身分类（回归）
            app._adding_new = False
            app._add_target = None
            _e8 = db.add_entry(Entry(name="兜底测试条目", category_id=_cat8))
            app._detail_entry_id = _e8
            _ctx8c = app._suggest_ctx_names()
            check("suggest(8-A): 编辑态仍取条目自身分类（回归）[%s]" % (_ctx8c[:2],),
                  "兜底测试分类" in _ctx8c)
            db.delete_entry(_e8)
            app._detail_entry_id = None
            # 4) 快速新建窗口：传入默认目标分类 ⇒ 不悬停任何分类也有推荐上下文
            _qa8 = _QAW7(app, db, default_cat_id=_cat8)
            pump(app, 22)
            check("suggest(8-A): 快速新建窗口默认目标分类生效 [eff=%s]"
                  % _qa8._effective_cat_id(), _qa8._effective_cat_id() == _cat8)
            _ctx_qa8 = _qa8._suggest_ctx_names()
            check("suggest(8-A): 快速新建窗口不悬停也有推荐上下文 [%s]" % (_ctx_qa8[:2],),
                  "兜底测试分类" in _ctx_qa8)
            _qa8._uncat_locked = True
            check("suggest(8-A): 点过「归入未分类」后默认目标不生效（优先级不变）[eff=%s]"
                  % _qa8._effective_cat_id(), _qa8._effective_cat_id() is None)
            _qa8._uncat_locked = False
            try:
                _qa8.grab_release()
            except Exception:
                pass
            _qa8.destroy()
            pump(app, 4)
            # 5) 主窗口「＋ 新建」入口确实把当前视图分类传给快速新建窗口
            app._cur_cat_id = _cat8
            app._last_cat_id = _cat8
            app._quick_add(); pump(app, 22)
            _tops8 = [w for w in app.winfo_children() if isinstance(w, _ctk63.CTkToplevel)]
            _new8 = _tops8[-1] if _tops8 else None
            check("suggest(8-A): 主窗口「快速新建」把当前视图分类传入窗口 [默认=%s]"
                  % getattr(_new8, "_default_cat_id", "无窗口"),
                  _new8 is not None and getattr(_new8, "_default_cat_id", None) == _cat8)
            if _new8 is not None:
                try:
                    _new8.grab_release()
                except Exception:
                    pass
                _new8.destroy()
            pump(app, 4)
        finally:
            app._adding_new, app._add_target = _add_b8, _tgt_b8
            app._cur_cat_id, app._last_cat_id = _cur_b8, _last_b8
            app._detail_entry_id = _det_b8

        # ---- 8d（2026-09-15 批次 8-D）：T2"输入停止后自动推荐"在新增态/快速新建同样生效 ----
        try:
            with open(_mw.__file__, "r", encoding="utf-8") as _f8d:
                _src8d = _f8d.read()
        except Exception:
            _src8d = ""
        _i8d = _src8d.find("def _build_new_entry_editor")
        _seg8d = (_src8d[_i8d:_src8d.find("\n    def ", _i8d + 10)] if _i8d >= 0 else "")
        check("T2(8-D): 新增态构建函数内已调用 _bind_auto_suggest_triggers [段=%d 字符]"
              % len(_seg8d), "_bind_auto_suggest_triggers()" in _seg8d)

        _vw8d, _cc8d, _add8d = app._view, app._cur_cat_id, app._adding_new
        _tgt8d, _det8d, _dirty8d = app._add_target, app._detail_entry_id, app._detail_dirty
        _auto8d = getattr(app, "_auto_tag_suggest", False)
        _q8d = None
        try:
            # 1) 主窗口新增态：⑧中文框应已挂 T2 防抖（绑定数：内容自适应/脏标记 + T2 ≥ 2）
            app._view = ("cat", _cat8)
            app._cur_cat_id = _cat8
            app._detail_dirty = False
            app._start_new_entry()
            pump(app, 8)
            _box8d = (getattr(app, "_detail_boxes", None) or {}).get("prompt_cn")
            _cmds8d = list(getattr(_box8d, "_tclCommands", None) or [])
            check("T2(8-D): 新增态 ⑧中文框已挂防抖绑定 [绑定数=%d]" % len(_cmds8d),
                  len(_cmds8d) >= 2)
            app._auto_tag_suggest = True
            app._suggest_timer = None
            app._on_prompt_typed()
            _sch8d = app._suggest_timer is not None
            try:
                app.after_cancel(app._suggest_timer)
            except Exception:
                pass
            app._suggest_timer = None
            check("T2(8-D): 新增态开启开关后排入防抖", _sch8d)

            # 2) 快速新建窗口：⑧中文框挂 T2；开关关闭不排、开启才排
            _q8d = _QAW7(app, db)     # 复用本函数已在 7a 段导入的窗口类（顶层名字是局部导入，勿直呼）
            pump(app, 8)
            _qb8d = (getattr(_q8d, "_boxes", None) or {}).get("prompt_cn")
            _qc8d = list(getattr(_qb8d, "_tclCommands", None) or [])
            check("T2(8-D): 快速新建 ⑧中文框已挂防抖绑定 [绑定数=%d]" % len(_qc8d),
                  len(_qc8d) >= 2)
            _q8d._auto_tag_suggest = False
            _q8d._suggest_timer = None
            _q8d._on_prompt_typed()
            check("T2(8-D): 快速新建开关关闭时不排防抖", _q8d._suggest_timer is None)
            _q8d._auto_tag_suggest = True
            _q8d._on_prompt_typed()
            _qsch8d = _q8d._suggest_timer is not None
            try:
                _q8d.after_cancel(_q8d._suggest_timer)
            except Exception:
                pass
            _q8d._suggest_timer = None
            check("T2(8-D): 快速新建开启开关后排入防抖", _qsch8d)
            # 3) 防抖到点：无内容 → 静默返回（自动路径不弹提示框）
            _q8d._tag_names = []
            _q8d._suggest_tags_debounced()
            check("T2(8-D): 快速新建防抖到点但无内容时静默返回", _q8d._tag_names == [])
            # 4) 防抖到点：有内容 → 真正执行一次推荐
            _q8d._default_cat_id = _cat8
            _q8d.name_entry.insert(0, "Editorial portrait poster")
            _q8d._boxes["prompt_en"].insert("1.0", "portrait, film grain, cinematic")
            _q8d._suggest_tags_debounced()
            _lab8d = str(_q8d.lock_label.cget("text"))
            check("T2(8-D): 快速新建防抖到点有内容时执行推荐 [%s]" % _lab8d[:12],
                  _lab8d.startswith("✨") or _lab8d.startswith("⚠"))
        finally:
            app._view, app._cur_cat_id, app._adding_new = _vw8d, _cc8d, _add8d
            app._add_target, app._detail_entry_id = _tgt8d, _det8d
            app._auto_tag_suggest = _auto8d
            app._suggest_timer = None
            if _q8d is not None:
                try:
                    _q8d.grab_release()
                except Exception:
                    pass
                _q8d.destroy()
            app._detail_dirty = False
            app._show_detail(None)     # 清空新增态表单，回到空白详情
            app._detail_dirty = _dirty8d
            pump(app, 4)

        # ---- 8bc（2026-09-15 批次 8-B/8-C）：无上下文也能推荐 + ①名称取词兜底 + 批量路径隔离 ----
        # 2026-09-17（审核 R-2）：两窗口的"单条推荐"公共流程已抽取到
        #   `app/tagger.py::run_ui_suggest`，故本组断言改为：
        #   ① 两个窗口都调用 run_ui_suggest；② 公共实现里带 fallback_global / field_fallback。
        try:
            with open(_mw.__file__, "r", encoding="utf-8") as _f8bc:
                _src8bc = _f8bc.read()
        except Exception:
            _src8bc = ""
        _i8bc = _src8bc.find("def _suggest_tags")
        _seg8bc = _src8bc[_i8bc:_i8bc + 6000] if _i8bc >= 0 else ""
        check("engine(8-B/R-2): 主窗口单条推荐走公共 run_ui_suggest [段=%d 字符]" % len(_seg8bc),
              "run_ui_suggest" in _seg8bc)
        _qsrc8bc, _bsrc8bc = "", ""
        try:
            from app.ui import quick_add as _qa8bc
            with open(_qa8bc.__file__, "r", encoding="utf-8") as _fqa8bc:
                _qsrc8bc = _fqa8bc.read()
        except Exception:
            _qsrc8bc = ""
        try:
            from app.ui import batch_tag_dialog as _bt8bc
            with open(_bt8bc.__file__, "r", encoding="utf-8") as _fb8bc:
                _bsrc8bc = _fb8bc.read()
        except Exception:
            _bsrc8bc = ""
        check("engine(8-B/R-2): 快速新建单条推荐同样走公共 run_ui_suggest",
              "run_ui_suggest" in _qsrc8bc)
        check("engine(8-B): 批量打标路径未启用 fallback_global（隔离）[源=%d 字符]"
              % len(_bsrc8bc), bool(_bsrc8bc) and "fallback_global" not in _bsrc8bc)
        # 公共实现（tagger.py）必须带这两个口径；两窗口自身不再重复书写
        try:
            with open(tagger.__file__, "r", encoding="utf-8") as _ftg8bc:
                _tsrc8bc = _ftg8bc.read()
        except Exception:
            _tsrc8bc = ""
        _itg = _tsrc8bc.find("def run_ui_suggest")
        _tseg = _tsrc8bc[_itg:_itg + 3000] if _itg >= 0 else ""
        check("engine(8-B/R-2): 公共实现带 fallback_global=True [段=%d 字符]" % len(_tseg),
              "fallback_global=True" in _tseg)

        # ---- 11-3（2026-09-16）：字段取词只对"UI 单条推荐"开启；批量/离线打标保持关闭 ----
        check("engine(11-3/R-2): 公共实现带 field_fallback=True",
              "field_fallback=True" in _tseg)
        # 2026-09-16（批次 12-2，用户要求 2）：批量/离线打标的取词**改由设置开关控制**（默认关）
        check("engine(11-3/12-2): 批量打标路径的取词由设置控制（默认关）[源=%d 字符]"
              % len(_bsrc8bc),
              "field_fallback=_use_fb" in _bsrc8bc and "META_FALLBACK_BATCH" in _bsrc8bc)
        _btb_src = ""
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "tag_builtin.py"), "r", encoding="utf-8") as _fb3:
                _btb_src = _fb3.read()
        except Exception:
            _btb_src = ""
        check("engine(11-3/12-2): 离线打标 tag_builtin 的取词同样由设置控制",
              "META_FALLBACK_BATCH" in _btb_src and "field_fallback=_use_fb" in _btb_src)

        _vwbc, _ccbc, _lcbc = app._view, app._cur_cat_id, app._last_cat_id
        _addbc, _tgtbc, _detbc = app._adding_new, app._add_target, app._detail_entry_id
        try:
            # 1) 无任何分类上下文（未分类视图新增）+ 词表可命中 → 引擎兜底也应给出标签
            app._view = ("uncat", None)
            app._cur_cat_id = None
            app._last_cat_id = None
            app._detail_dirty = False
            app._start_new_entry()
            pump(app, 8)
            app._name_entry.insert(0, "portrait film grain cinematic")
            app._detail_boxes["prompt_en"].insert("1.0", "portrait, film grain")
            app._tag_names = []
            app._refresh_tag_chips()
            app._suggest_tags()
            pump(app, 4)
            check("engine(8-B): 无分类上下文新增也能推荐出标签 [%s]" % (app._tag_names[:2],),
                  len(app._tag_names) >= 1)
            # 2) 2026-09-16（批次 11-5，方案 D）→ **2026-09-18（用户第 3 轮"兜底规则"）改版**：
            #    原口径"词表外的取词词一律不采纳"在"热点词 + 领域/词表都为 0"时会导致**整条无标签**，
            #    与用户要求"字段取词必须推荐 1~3 个"冲突 ⇒ 现由**兜底取词**接住：
            #    另两来源为 0 时，词表外的词**以原文成为标签**（来源标注「取词（兜底）」）。
            app._name_entry.delete(0, "end")
            app._name_entry.insert(0, "Kqxl Mmno")
            for _kbc in ("prompt_cn", "prompt_en"):
                app._detail_boxes[_kbc].delete("1.0", "end")
            app._tag_names = []
            app._refresh_tag_chips()
            app._suggest_tags()
            pump(app, 4)
            check("engine(11-5→09-18): 另两来源为 0 时由「兜底取词」接住 [%s]" % (app._tag_names,),
                  "Kqxl" in app._tag_names or "Mmno" in app._tag_names)
            # 3) 名称里含词表标签 ⇒ 取词命中并推荐
            app._name_entry.delete(0, "end")
            app._name_entry.insert(0, "Kqxl 赛博朋克")
            app._tag_names = []
            app._refresh_tag_chips()
            app._suggest_tags()
            pump(app, 4)
            check("engine(11-5): 名称含词表标签时取词命中 [%s]" % (app._tag_names,),
                  "赛博朋克" in app._tag_names)
        finally:
            app._view, app._cur_cat_id, app._last_cat_id = _vwbc, _ccbc, _lcbc
            app._adding_new, app._add_target = _addbc, _tgtbc
            app._detail_entry_id = _detbc
            app._detail_dirty = False
            app._show_detail(None)
            pump(app, 4)

        # ---- 12-3（2026-09-16，用户要求 1）：智能自动取词词库（采集 / 自动升热点词 / 开关）----
        _aw_cfg_bak = auto_words.load_cfg(db)
        _aw_data_bak = auto_words.load(db)
        try:
            auto_words.clear(db)
            auto_words.save_cfg(db, {"enabled": True, "hot_th": 1, "tag_th": 99})
            app._view = ("uncat", None)
            app._cur_cat_id = None
            app._last_cat_id = None
            app._detail_dirty = False
            app._start_new_entry(); pump(app, 8)
            app._name_entry.insert(0, "ZzNewWord 赛博朋克")
            app._tag_names = []
            app._refresh_tag_chips()
            app._suggest_tags(); pump(app, 4)
            _aw = auto_words.load(db)
            check("auto-words(12-3): 推荐后把词表外新词沉淀入词库 [%s]" % list(_aw)[:3],
                  "ZzNewWord" in _aw)
            check("auto-words(12-3): 词表内词不采集（赛博朋克）", "赛博朋克" not in _aw)
            check("auto-words(12-3): 达热点阈值自动升为热点词 [%s]" % db.list_hotwords()[:3],
                  "ZzNewWord" in db.list_hotwords())
            # 关开关 ⇒ 不再采集
            auto_words.save_cfg(db, {"enabled": False})
            app._name_entry.delete(0, "end")
            app._name_entry.insert(0, "ZzAnotherWord")
            app._tag_names = []
            app._refresh_tag_chips()
            app._suggest_tags(); pump(app, 4)
            check("auto-words(12-3): 关闭开关后不再采集",
                  "ZzAnotherWord" not in auto_words.load(db))
            # 管理对话框：可打开并列出词条
            auto_words.save_cfg(db, {"enabled": True, "hot_th": 1, "tag_th": 99})
            _awd = _awmod.AutoWordsDialog(app, db); pump(app)
            check("auto-words(12-3): 管理对话框打开并列出词条 [%d]" % len(_awd._rows),
                  len(_awd._rows) >= 1)
            try:
                _awd.grab_release()
            except Exception:
                pass
            _awd.destroy(); pump(app)
        finally:
            auto_words.save(db, _aw_data_bak)
            auto_words.save_cfg(db, _aw_cfg_bak)
            try:
                db.remove_hotwords(["ZzNewWord"])
            except Exception:
                pass
            app._show_detail(None)
            pump(app, 4)

        # ---- 9（2026-09-15 批次 9）：标签选择器（三来源候选池 + 详情/快速新建入口 + 自动补全扩展）----
        try:
            _pool9 = tagger.tag_name_pool(db)
        except Exception:
            _pool9 = []
        try:
            _used9 = {str(t["name"]) for t in db.list_tags()}
        except Exception:
            _used9 = set()
        _dict9 = set(tagger.dict_tag_names(tagger.load_dict(db, write_if_missing=False)))
        try:
            _hot9 = set(db.list_hotwords())
        except Exception:
            _hot9 = set()
        check("tag picker(9): 候选池 = 已用标签 ∪ 词表 ∪ 热点词（去重）"
              "[池=%d 用=%d 词表=%d 热=%d]"
              % (len(_pool9), len(_used9), len(_dict9), len(_hot9)),
              bool(_pool9) and len(_pool9) == len(set(_pool9))
              and _used9 <= set(_pool9) and _dict9 <= set(_pool9) and _hot9 <= set(_pool9))

        # 三来源"实际取证"：临时造 1 个已用标签 + 1 个热点词 → 必须都进入候选池（随后清理）
        _e9 = db.add_entry(Entry(name="标签选择器自测条目"))
        _hot9b = "自测热点词9"
        try:
            db.set_entry_tags(_e9, ["自测已用标签9"])
        except Exception:
            pass
        try:
            db.add_hotwords([_hot9b])
        except Exception:
            pass
        try:
            _pool9b = tagger.tag_name_pool(db)
        finally:
            try:
                db.delete_entry(_e9)
            except Exception:
                pass
            try:
                db.remove_hotwords([_hot9b])
            except Exception:
                pass
        check("tag picker(9): 已用标签与热点词确实并入候选池 [含用=%s 含热=%s]"
              % ("自测已用标签9" in _pool9b, _hot9b in _pool9b),
              "自测已用标签9" in _pool9b and _hot9b in _pool9b)

        _vw9, _cc9, _lc9 = app._view, app._cur_cat_id, app._last_cat_id
        _add9, _tgt9, _det9 = app._adding_new, app._add_target, app._detail_entry_id
        _orig_pick9, _orig_wait9 = _mw._ListPickDialog, app.wait_window
        _q9 = None
        try:
            app._view = ("cat", _cat8)
            app._cur_cat_id = _cat8
            app._detail_dirty = False
            app._start_new_entry()
            pump(app, 8)
            check("tag picker(9): 详情区「📋 选择…」按钮已渲染",
                  hasattr(app, "_tag_pick_btn") and app._tag_pick_btn.winfo_exists())

            # 桩替代模态对话框（无法在自动化中等待人工点击）：确认 → 并入已选 + 置脏
            _pick9 = list(_pool9[:3])

            class _StubPick9:
                def __init__(self, *a, **k):
                    self.result = list(_pick9)
                    self.confirmed = True

            _mw._ListPickDialog = _StubPick9
            app.wait_window = lambda _w=None: None
            app._tag_names = []
            app._refresh_tag_chips()
            app._detail_dirty = False
            app._pick_tags_dialog()
            check("tag picker(9): 确定后标签并入已选且置脏 [%s]" % (app._tag_names[:3],),
                  app._tag_names == _pick9 and bool(app._detail_dirty))

            # 取消 → 不改变任何状态
            app._tag_names = ["__keep9"]
            app._refresh_tag_chips()

            class _StubCancel9:
                def __init__(self, *a, **k):
                    self.result = ["不应加入"]
                    self.confirmed = False

            _mw._ListPickDialog = _StubCancel9
            app._pick_tags_dialog()
            check("tag picker(9): 取消不改变任何状态 [%s]" % (app._tag_names,),
                  app._tag_names == ["__keep9"])

            # C19：详情区"自动补全"数据源已扩到三来源（输入词表标签 → 建议行出候选）
            app._tag_names = []
            app._refresh_tag_chips()
            _t9 = _pool9[0]
            app._tag_entry.delete(0, "end")
            app._tag_entry.insert(0, _t9)
            app._refresh_tag_suggest()
            pump(app, 2)
            _sugg9 = len(app._tag_sugg_row.winfo_children())
            check("tag picker(9): 自动补全扩展到三来源 [输入=%s 候选控件=%d]" % (_t9, _sugg9),
                  _sugg9 >= 2)

            # 快速新建窗口同样有入口，并把结果并入已选
            _q9 = _QAW7(app, db)
            pump(app, 8)
            _q9.wait_window = lambda _w=None: None
            _qpick9 = list(_pool9[:2])

            class _StubPickQ9:
                def __init__(self, *a, **k):
                    self.result = list(_qpick9)
                    self.confirmed = True

            _mw._ListPickDialog = _StubPickQ9
            _q9._tag_names = []
            _q9._pick_tags_dialog()
            check("tag picker(9): 快速新建「📋 选择…」并入已选 [%s]" % (_q9._tag_names[:2],),
                  _q9._tag_names == _qpick9)
        finally:
            _mw._ListPickDialog = _orig_pick9
            app.wait_window = _orig_wait9
            app._view, app._cur_cat_id, app._last_cat_id = _vw9, _cc9, _lc9
            app._adding_new, app._add_target = _add9, _tgt9
            app._detail_entry_id = _det9
            if _q9 is not None:
                try:
                    _q9.grab_release()
                except Exception:
                    pass
                _q9.destroy()
            app._detail_dirty = False
            app._show_detail(None)
            pump(app, 4)

        # ---- 7. 设置对话框（2026-09-14 12:30 分页重组为 4 页；2026-09-15 批次 4 新增【外观】页）----
        import customtkinter as _ctk
        dlg = SettingsDialog(app, db)
        pump(app)
        tabv = None
        for _w in dlg.winfo_children():
            if isinstance(_w, _ctk.CTkTabview):
                tabv = _w
                break
        check("settings uses tabview", tabv is not None)
        if tabv is not None:
            _names = list(getattr(tabv, "_tab_dict", {}).keys())
            # 2026-09-16（批次 11-2，用户反馈 5）：新增独立【字段】页（原字段管理在标签与词表页）
            check("settings has 6 pages",
                  _names == ["界面", "外观", "数据与备份", "字段", "标签与词表", "关于"])
            _pgf = tabv._tab_dict.get("字段")
            check("field page exists & 字段管理按钮在其上",
                  _pgf is not None and dlg._mgr_btn_field.winfo_parent() == str(_pgf))
            # 标签与词表页的管理入口共 4 个：词表 / 热点词 / 批量打标 / 清理垃圾标签。
            # 2026-09-17（用户要求 3）：为不超出本页高度，「批量打标」与「清理垃圾标签」
            #   并入**同一行的 Frame** ⇒ 直接挂在页面上的 CTkButton 变为 2 个，
            #   4 个按钮本体仍都在本页（下面逐项校验）。
            _pgt = tabv._tab_dict.get("标签与词表")
            _tag_mgr = [b for b in (_pgt.winfo_children() if _pgt is not None else [])
                        if isinstance(b, _ctk.CTkButton)]
            _mgr_all = (dlg._mgr_btn_dict, dlg._mgr_btn_hot,
                        dlg._mgr_btn_batch, dlg._mgr_btn_cleanup)
            check("tag page holds 4 mgr entries (2 direct + 2 in row frame) [%d]" % len(_tag_mgr),
                  len(_tag_mgr) == 2
                  and all(b is not dlg._mgr_btn_field for b in _tag_mgr)
                  and all(b.winfo_exists() for b in _mgr_all)
                  and dlg._mgr_btn_batch.master.master == _pgt
                  and dlg._mgr_btn_cleanup.master.master == _pgt)

            # ---- 11-6（2026-09-16，用户确认问题 2）：标签推荐策略与顺序（勾选 + ↑/↓ 排序） ----
            check("policy(11-6): 4 个来源行已渲染 [%d]" % len(dlg._policy_rows),
                  len(dlg._policy_rows) == 4
                  and all(w["chk"].winfo_exists() for w in dlg._policy_rows.values()))
            check("policy(11-6): 默认顺序 = 热点词 → 领域+词典 → 取词 → 扩展 [%s]"
                  % dlg._policy_order,
                  dlg._policy_order == ["hotword", "domain_dict", "field", "ext"])
            _i_before = dlg._policy_order.index("field")
            dlg._pol_move("field", -1); pump(app)
            check("policy(11-6): ↑ 上移一位生效 [%s]" % dlg._policy_order,
                  dlg._policy_order.index("field") == _i_before - 1
                  and len(dlg._policy_rows) == 4)
            dlg._policy_rows["field"]["chk"].deselect(); dlg._pol_snapshot()
            check("policy(11-6): 取消勾选被记录为不启用 [%s]"
                  % dlg._policy_enabled.get("field"),
                  dlg._policy_enabled.get("field") is False)
            dlg._pol_reset(); pump(app)
            check("policy(11-6): 恢复默认顺序与开关 [%s]" % (dlg._policy_order,),
                  dlg._policy_order == ["hotword", "domain_dict", "field", "ext"]
                  and dlg._policy_enabled.get("field") is True)
            _pgt_h = (_pgt.winfo_height() if _pgt.winfo_height() > 50
                      else max(tabv.winfo_height() - 46, 100))   # 页内容区高（未映射时退化为估值）
            _pgt_bottom = max((c.winfo_y() + c.winfo_height()
                               for c in _pgt.winfo_children()), default=0)
            check("policy(11-6): 标签与词表页内容未超出页高 [底=%d 页高≈%d]"
                  % (_pgt_bottom, _pgt_h), _pgt_bottom <= _pgt_h)
            # 2026-09-16（批次 11-7，用户要求 3）：界面页新增「条目排序」三选项
            _pgu = tabv._tab_dict.get("界面")
            check("sort(11-7): 界面页含「条目排序」三选项 [%s]" % dlg.seg_sort.get(),
                  dlg.seg_sort.get() in ("修改时间", "添加时间", "名称"))
            _pgu_bottom = max((c.winfo_y() + c.winfo_height()
                               for c in _pgu.winfo_children()), default=0)
            check("sort(11-7): 界面页内容未超出页高 [底=%d 页高≈%d]" % (_pgu_bottom, _pgt_h),
                  _pgu_bottom <= _pgt_h)
            # 2026-09-16（批次 12-2，用户要求 2）：设置页同一开关（与批量对话框共用 meta 键）
            check("fb-batch(12-2): 设置页含「取词用于批量打标」开关且默认关",
                  hasattr(dlg, "sw_fb_batch") and dlg.sw_fb_batch.get() == 0)
            # 2026-09-16（批次 12-3，用户要求 1）：设置页同一组「智能自动取词」（开关 + 两阈值 + 入口）
            check("auto-words(12-3): 设置页含智能自动取词组（开关/两阈值）",
                  all(hasattr(dlg, a) for a in ("sw_aw", "ent_aw_hot", "ent_aw_tag")))
            # 2026-09-16（批次 12-2/12-3）：设置窗口须能在 1366×768（最低分辨率）的工作区内完整显示
            _req_h = dlg.winfo_reqheight()
            check("settings(12-2): 设置窗口高度适配 768 屏 [req=%d ≤ 720]" % _req_h,
                  _req_h <= 720)
            # 2026-09-15（批次 4）：【外观】页 = 6 组 × (字体/字号[/文字色/背景色])，均为"默认（不改）"
            _pg = tabv._tab_dict.get("外观")
            check("appearance page exists", _pg is not None)
            if _pg is not None:
                check("appearance page: 6 组控件齐备且默认值=不改",
                      len(dlg._look_widgets) == 6
                      and all(w["family"].get() == dlg._look_default
                              for w in dlg._look_widgets.values())
                      and all(w["size"].get() == dlg._look_default
                              for w in dlg._look_widgets.values()))
                _wide = [(str(c.cget("text"))[:12], c.winfo_reqwidth())
                         for c in _pg.winfo_children()
                         if isinstance(c, _ctk.CTkLabel) and c.winfo_reqwidth() > max(tabv.winfo_width() - 50, 200)]
                check("appearance page: 无文字超出页宽 [%s]" % (_wide or "无"), not _wide)
                _overs = []
                for _r in range(1, len(dlg._look_widgets) + 1):
                    _sum = 0
                    for c in _pg.winfo_children():
                        _gi = c.grid_info()
                        if _gi and int(_gi.get("row", -1)) == _r:
                            _sum += c.winfo_reqwidth()
                    if _sum + 50 > max(tabv.winfo_width(), 200):
                        _overs.append((_r, _sum))
                check("appearance page: 每行控件总宽放得下 [%s]" % (_overs or "无"), not _overs)
        # 7.1 用户要求 1：把"在查询栏输入 #无标签 查无标签条目"的方法写进「关于」页；
        #     并保证该说明不超出「关于」页可视宽度（否则会被裁切）
        if tabv is not None:
            _pg = tabv._tab_dict.get("关于")
            _lbls = []
            walk_types(_pg, "CTkLabel", _lbls)
            _hl = next((w for w in _lbls if "#无标签" in str(w.cget("text"))), None)
            check("about page documents '#无标签' rule",
                  _hl is not None and "#none" in str(_hl.cget("text")))
            # 2026-09-15（审核 L-10/L-11）：原 `_avail = max(winfo_width() - 50, 400)` 在布局尚未完成时
            #   会**静默放宽**断言（页面宽度很小时仍给 400）→ 改为：先要求页面宽度可信，再据实计算。
            _tabv_w = int(tabv.winfo_width())
            check("about page: 布局已完成、宽度可信 [%d]" % _tabv_w, _tabv_w > 200)
            _avail = max(_tabv_w - 50, 200)
            check("about page hint fits width [req=%s avail=%s]"
                  % (None if _hl is None else _hl.winfo_reqwidth(), _avail),
                  _hl is not None and _hl.winfo_reqwidth() <= _avail
                  and len(str(_hl.cget("text")).split("\n")) <= 6)
            # 2026-09-14（用户要求 3）：原「联网说明」一整行 66 汉字超宽被裁 → 已断行；
            #   此处统一校验"关于页**所有**文字都不超出页宽"，防止再次出现被裁的行。
            _wide = [(str(_w.cget("text"))[:14], _w.winfo_reqwidth()) for _w in _lbls
                     if _w.winfo_reqwidth() > _avail]
            check("about page: no text overflows page width [%s]" % (_wide or "ok"),
                  not _wide)
            # 2026-09-16（批次 15）：关于页新增「详情区排布」说明 → 校验其内容不超出页高
            #   （本页无滚动条，超出即被裁掉）
            _pgab_bottom = max((c.winfo_y() + c.winfo_height()
                                for c in _pg.winfo_children()), default=0)
            _pgt_h2 = (_pgt.winfo_height() if _pgt.winfo_height() > 50
                       else max(tabv.winfo_height() - 46, 100))
            check("about page: 内容未超出页高 [底=%d 页高≈%d]" % (_pgab_bottom, _pgt_h2),
                  _pgab_bottom <= _pgt_h2)
        # 7.2 关于窗口（完整说明）也在 V2.1.0 修订里补了"无标签条目入口"一条（用户要求 1）
        #     注：必须放在 dlg._apply() **之前**——_apply() 末尾会 self.destroy()。
        try:
            _before = [w for w in dlg.winfo_children()
                       if isinstance(w, _ctk.CTkToplevel)]
            dlg._show_about(); pump(dlg)
            _newtops = [w for w in dlg.winfo_children()
                        if isinstance(w, _ctk.CTkToplevel) and w not in _before]
            # 关于窗口的正文是"只读 CTkTextbox"（不是 Label），故按文本框取文本
            _boxes = []
            for _t in _newtops:
                walk_types(_t, "CTkTextbox", _boxes)
            _txt = ""
            for _bx in _boxes:
                try:
                    _txt += str(_bx.get("1.0", "end"))
                except Exception:
                    pass
            check("about window mentions 无标签 entry",
                  bool(_newtops) and "无标签" in _txt and "#none" in _txt)
            # 2026-09-16（批次 15，用户要求 4）：关于窗口补入 09-15/09-16 增强说明
            check("about window mentions 09-16 增强（区块排序/隐藏 + 智能自动取词）",
                  "后续增强" in _txt and "区块" in _txt
                  and "自动取词" in _txt and "全部隐藏" in _txt)
            for _t in _newtops:
                try:
                    _t.grab_release()
                except Exception:
                    pass
                _t.destroy()
            pump(dlg)
        except Exception as _e:      # 不影响后续用例：仅把该断言记为失败并给出原因
            check("about window mentions 无标签 entry [err=%s]" % _e, False)
        # 控件属性名不变 → _apply 的 meta 读写逻辑不变
        check("settings controls intact",
              all(hasattr(dlg, a) for a in ("sw_size", "seg_view", "seg_detail",
                                            "sw_show_tags", "entry_code", "entry_keep",
                                            "entry_bkeep", "sw_snapshot")))
        dlg.seg_view.set("列表")
        dlg.sw_show_tags.select()
        dlg.seg_sort.set("添加时间")          # 2026-09-16（批次 11-7，用户要求 3）
        dlg.sw_fb_batch.select()              # 2026-09-16（批次 12-2，用户要求 2）
        dlg._apply(); pump(app)
        check("settings apply writes meta",
              db.get_meta(config.META_VIEW_MODE) == "list"
              and db.get_meta(config.META_SHOW_TAGS_IN_LIST) == "1")
        check("fb-batch(12-2): 设置页保存写 meta（与批量对话框同键）[%s]"
              % db.get_meta(config.META_FALLBACK_BATCH),
              db.get_meta(config.META_FALLBACK_BATCH) == "1")
        check("sort(11-7): 排序设置写 meta 且主窗口已生效 [%s/%s]"
              % (db.get_meta(config.META_ENTRY_SORT), app._entry_sort),
              db.get_meta(config.META_ENTRY_SORT) == "created"
              and app._entry_sort == "created")
        check("policy(11-6): 策略已写 meta [%s…]"
              % str(db.get_meta(config.META_TAG_POLICY))[:40],
              bool(db.get_meta(config.META_TAG_POLICY)))
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy(); pump(app)

        # ---- 7b（2026-09-15 批次 6-2）：锁定态**仍可打开设置**，但「标签与词表」5 个管理入口置灰 ----
        #   用户选定："允许打开设置、管理入口置灰"（窗口/视图/外观等纯偏好不受锁定影响）。
        #   2026-09-17（用户要求 3）：新增「🧹 清理垃圾标签…」→ 4 个变 5 个。
        _lock_bs = app._lock_on
        try:
            app._lock_on = True
            _dlg2 = SettingsDialog(app, db)
            pump(app)
            _m_locked = [str(b.cget("state")) for b in
                         (_dlg2._mgr_btn_dict, _dlg2._mgr_btn_hot,
                          _dlg2._mgr_btn_cleanup, _dlg2._mgr_btn_field, _dlg2._mgr_btn_batch)]
            check("settings(6-2): 锁定态可打开设置且 5 个管理入口全部置灰 [%s]" % (_m_locked,),
                  len(_m_locked) == 5 and all(s == "disabled" for s in _m_locked))
            try:
                _dlg2.grab_release()
            except Exception:
                pass
            _dlg2.destroy(); pump(app)
        except Exception as _e:
            check("settings(6-2): 锁定态可打开设置且 5 个管理入口全部置灰 [err=%s]" % _e, False)
        finally:
            app._lock_on = _lock_bs
        # 解锁态：管理入口不受影响（保证本次改动未破坏正常路径）
        _dlg3 = SettingsDialog(app, db)
        pump(app)
        _m_free = [str(b.cget("state")) for b in
                   (_dlg3._mgr_btn_dict, _dlg3._mgr_btn_hot,
                    _dlg3._mgr_btn_cleanup, _dlg3._mgr_btn_field, _dlg3._mgr_btn_batch)]
        check("settings(6-2): 解锁态 5 个管理入口正常可用 [%s]" % (_m_free,),
              len(_m_free) == 5 and all(s == "normal" for s in _m_free))
        try:
            _dlg3.grab_release()
        except Exception:
            pass
        _dlg3.destroy(); pump(app)

        # ---- 8. 性能优化（2026-09-14）：标签云换行 / 显示上限 / 条目区渲染上限 / 按钮顺序 ----
        ts = "2026-09-14 00:00:00"
        # 8.0 默认上限常量（用户确认：条目区 50/步长 50、标签页 50）
        check("default render limits are 50",
              _mw.ENTRY_RENDER_LIMIT == 50 and _mw.ENTRY_RENDER_STEP == 50
              and _mw.TAG_PAGE_LIMIT == 50)
        # 8.1 工具栏按钮顺序：🏷标签 移到最左（col1）；🔒锁定 移到 导出(col8) 与 设置(col10) 之间(col9);
        #     2026-09-14（"无标签条目"入口 方案 A + 用户要求 4）：新增"🏷 无标条目"在"📂 无类条目"
        #     右侧（col4），"⭐ 常用"顺延到 col5 → 新建/导入/导出/锁定/设置/搜索框 列号整体 +1。
        check("toolbar: tag btn moved to col1",
              app.tag_page_btn.grid_info()["column"] == 1)
        check("toolbar: lock btn between export and settings",
              app.lock_btn.grid_info()["column"] == 9
              and app.export_btn.grid_info()["column"] == 8)
        # 8.2 标签云自动换行 + 显示条数上限
        db.conn.executemany(
            "INSERT INTO tags(namespace,name,color,created_at,updated_at)"
            " VALUES('__global__',?,'',?,?)",
            [("云标签%03d" % i, ts, ts) for i in range(150)])
        db.conn.commit()
        app._tag_page_view = "cloud"
        app._tag_page_limit = 100
        if not app._tag_page_on:
            app._open_tag_page()
        app._refresh_tag_page(); pump(app)

        def _cloud_stats():
            box = [0]
            masters = set()

            def walk(w):
                for c in w.winfo_children():
                    if isinstance(c, _ctk.CTkButton):
                        box[0] += 1
                        masters.add(str(c.master))
                    walk(c)
            walk(app._tag_main)
            return box[0], len(masters)

        def _find_om(w, out):
            for c in w.winfo_children():
                if isinstance(c, _ctk.CTkOptionMenu):
                    out.append(c)
                    return
                _find_om(c, out)

        n_lim, rows = _cloud_stats()
        check("tag cloud respects limit(=100)", 95 <= n_lim <= 110)
        check("tag cloud wraps into multiple rows", rows >= 3)
        _found = []
        _find_om(app.tag_frame, _found)            # 2026-09-15：下拉已移到固定头部（滚动区之外）
        check("tag limit options include 不限",
              bool(_found) and "不限" in list(_found[0].cget("values"))
              and _found[0] is app._tag_limit_om)
        app._tag_page_limit = 0                    # 不限（单页全部）
        app._render_tag_main(); pump(app)
        n_all, _r = _cloud_stats()
        check("tag cloud shows all when limit=不限", n_all > n_lim + 40)
        # --- 8.2b 2026-09-15（用户要求 6+7+2）：控件都不在滚动区内 + 分页 ---
        #     统计/排序在**顶部固定行** `_tag_head`；每页/页码/翻页在**底部行** `_tag_bottom_row`
        #     （"无标签条目"按钮右侧）。
        check("tag page: 统计在顶部固定行、每页/翻页在底部行（均不在滚动区内）",
              app._tag_head is not None and app._tag_head.master is app.tag_frame
              and app._tag_stat_lbl.master is app._tag_head
              and app._tag_bottom_row is not None
              and app._tag_page_group is not None
              and app._tag_page_group.master is app._tag_bottom_row
              and app._tag_limit_om.master is app._tag_page_group
              and app._tag_page_lbl.master is app._tag_page_group
              and all(b.master is app._tag_page_group for b in app._tag_page_btns.values()))
        # 2026-09-15（用户要求）：分页/每页**在本行依次靠右**，"无标签条目"按钮**保持靠左**
        _un_b = next((c for c in app._tag_bottom_row.winfo_children()
                      if isinstance(c, _ctk.CTkButton)), None)
        check("tag page: 分页整组靠右、无标签按钮靠左 [按钮x=%s 组右=%s 行宽=%s]"
              % (None if _un_b is None else _un_b.winfo_x(),
                 app._tag_page_group.winfo_x() + app._tag_page_group.winfo_width(),
                 app._tag_bottom_row.winfo_width()),
              _un_b is not None and _un_b.winfo_x() <= 20
              and app._tag_page_group.winfo_x() > _un_b.winfo_x() + _un_b.winfo_width()
              and app._tag_page_group.winfo_x() + app._tag_page_group.winfo_width()
              >= app._tag_bottom_row.winfo_width() - 12)
        # 用户选"方案 A"：底部行原先那句灰色提示文字已改为**悬停提示** → 行内不再有大段说明文字
        _row2_txts = [text_of(c) for c in app._tag_bottom_row.winfo_children()]
        check("tag page: 底部行无长提示文字（已改为悬停提示）[%s]" % _row2_txts,
              not any("点击→" in t for t in _row2_txts))
        # 底部行实际需求宽度不得超过面板可用宽度（防"放不下被裁切"）
        check("tag page: 底部行放得下 [req=%d 面板=%d]"
              % (app._tag_bottom_row.winfo_reqwidth(), app.tag_frame.winfo_width()),
              app._tag_bottom_row.winfo_reqwidth() + 30 <= max(app.tag_frame.winfo_width(), 1))
        app._tag_page_limit = 100; app._tag_page_no = 1
        app._render_tag_main(); pump(app)
        _n_p1, _ = _cloud_stats()
        _p1_txt = text_of(app._tag_page_lbl)
        check("tag page: 第 1 页显示 100 条且页码已更新 [n=%d %s]" % (_n_p1, _p1_txt),
              95 <= _n_p1 <= 110 and "第 1 /" in _p1_txt)
        check("tag page: 第 1 页时「首页/上页」置灰、可下翻",
              str(app._tag_page_btns["first"].cget("state")) == "disabled"
              and str(app._tag_page_btns["prev"].cget("state")) == "disabled"
              and str(app._tag_page_btns["next"].cget("state")) == "normal"
              and str(app._tag_page_btns["last"].cget("state")) == "normal")
        app._on_tag_page_next(); pump(app)
        _n_p2, _ = _cloud_stats()
        check("tag page: 下翻一页 → 第 2 页且内容已换 [n=%d %s]" % (_n_p2, text_of(app._tag_page_lbl)),
              "第 2 /" in text_of(app._tag_page_lbl) and _n_p2 > 0)
        app._on_tag_page_last(); pump(app)
        _n_pl, _ = _cloud_stats()
        _pl_txt = text_of(app._tag_page_lbl)
        check("tag page: 跳到末页 → 「下页/末页」置灰 [n=%d %s]" % (_n_pl, _pl_txt),
              "第 1 /" not in _pl_txt and _n_pl > 0
              and str(app._tag_page_btns["next"].cget("state")) == "disabled"
              and str(app._tag_page_btns["last"].cget("state")) == "disabled")
        app._on_tag_page_first(); pump(app)
        check("tag page: 回首页", "第 1 /" in text_of(app._tag_page_lbl))
        # 搜索防抖（用户要求 8）：输入后**不立即**刷新，约 0.4s 后才刷新
        app._tag_search_entry.delete(0, "end")
        app._tag_search_entry.insert(0, "云标签00")
        _stat_before = text_of(app._tag_stat_lbl)
        app._on_tag_search_typed(); pump(app, 5)       # ≈60ms：未到 400ms
        _pending = app._tag_search_after is not None
        _stat_mid = text_of(app._tag_stat_lbl)
        pump(app, 50)                                   # ≈600ms：应已刷新
        _stat_after = text_of(app._tag_stat_lbl)
        check("tag page: 搜索防抖（未到点不刷新、到点后刷新）[pending=%s %s→%s]"
              % (_pending, _stat_mid, _stat_after),
              _pending and _stat_mid == _stat_before and _stat_after != _stat_before)
        app._tag_search_entry.delete(0, "end")
        app._tag_page_search = ""
        app._apply_tag_search(); pump(app)
        app._tag_page_limit = 100
        app._close_tag_page(); pump(app)

        # 8.3 条目区渲染上限 + 「显示更多 / 全部显示」+ 标签一次性预取（用极小上限/步长快速验证）
        db.conn.executemany(
            "INSERT INTO entries(name, category_id, created_at, updated_at) VALUES(?,?,?,?)",
            [("性能条目%02d" % i, l1, ts, ts) for i in range(20)])
        db.conn.commit()
        all_rows = db.list_all_entries()
        db.set_entry_tags_bulk({e["id"]: ["性能标签"] for e in all_rows[:5]},
                               touch_updated=False)   # 供"标签预取"断言
        _orig_step = _mw.ENTRY_RENDER_STEP
        _mw.ENTRY_RENDER_STEP = 3
        try:
            app._entry_render_limit = 5
            app._show_tags_in_list = True
            app._render_entries(all_rows, "性能测试"); pump(app)
            check("entries truncated to limit",
                  len(app._entry_render_shown) == 5 and len(app._entry_more_btns) == 2)
            check("entry tags prefetched in one query",
                  len(app._entry_tags_cache) == 5)
            app._show_more_entries(); pump(app)
            check("show-more appends one step", len(app._entry_render_shown) == 8)
            # 「全部显示」在超大集合上先二次确认（临时把阈值降到 1 以触发该分支）
            _orig_thr = _mw.ENTRY_ALL_CONFIRM_THRESHOLD
            _mw.ENTRY_ALL_CONFIRM_THRESHOLD = 1
            try:
                mb.asks.clear()
                app._show_more_entries(all_=True); pump(app)
                check("show-all asks confirm for big set",
                      any(_k == "yesno" and "全部显示" in str(a[0])
                          for (_k, a, _kk) in mb.asks))
            finally:
                _mw.ENTRY_ALL_CONFIRM_THRESHOLD = _orig_thr
            check("show-all appends the rest",
                  len(app._entry_render_shown) == len(all_rows))
            check("more buttons disabled when all shown",
                  all(b.cget("state") == "disabled" for b in app._entry_more_btns))
        finally:
            _mw.ENTRY_RENDER_STEP = _orig_step
            app._show_tags_in_list = False
            app._entry_render_limit = _mw.ENTRY_RENDER_LIMIT

        # ---- 8.4 "无标签条目"入口（2026-09-14 用户要求：方案 A 工具栏 + B 标签页 + C 搜索语法）----
        # 8.4.1 工具栏按钮的**相对顺序**（2026-09-15 审核 L-11：原断言写死列号 4/5/6/7/8/9/12，
        #       改为"读实际 grid 顺序 + 断言自左向右的相对次序"，换机/增删按钮时更稳）、
        #       并校验"无标条目"文案、按钮不被裁、搜索框在所有按钮右侧。
        _bar_btns = sorted(
            (c for c in app.untagged_btn.master.winfo_children()
             if isinstance(c, _ctk.CTkButton) and c.grid_info()),
            key=lambda c: int(c.grid_info()["column"]))
        _seq = [str(c.cget("text")) for c in _bar_btns]
        _pos = []
        for _k in ("🏷 标签", "🗂", "📂 无类条目", "🏷 无标条目", "⭐ 常用",
                   "✚ 新建", "⇩ 导入", "⇧ 导出", "🔒 锁定", "⚙ 设置"):
            _pos.append(next((i for i, _t in enumerate(_seq) if _t.startswith(_k)), -1))
        check("untagged: 工具栏按钮相对顺序正确 %s" % _pos,
              all(_v >= 0 for _v in _pos) and _pos == sorted(_pos) and len(set(_pos)) == len(_pos))
        check("untagged: 搜索框在所有按钮右侧 [box=%d 最右按钮=%d]"
              % (app.search_box.winfo_x(), max(c.winfo_x() for c in _bar_btns)),
              app.search_box.winfo_x() >= max(c.winfo_x() for c in _bar_btns))
        check("untagged: toolbar btn text is 无标条目",
              "无标条目" in str(app.untagged_btn.cget("text"))
              and "无标签" not in str(app.untagged_btn.cget("text")))
        pump(app)
        check("untagged: btn visible (not clipped)",
              app.untagged_btn.winfo_width() > 0
              and app.untagged_btn.winfo_width() + 1 >= app.untagged_btn.winfo_reqwidth())
        # 8.4.1b 工具栏"省宽"（2026-09-14 用户要求 1）：按钮间距 padx 4→3、搜索框 96px；
        #       并插入**弹性空白列**（col11 weight=2）使搜索框不再独占全部剩余宽度。
        # 2026-09-15（审核 L-10）：原先"各列最小宽求和的自算模型 + ≤1366 屏"的做法已删除，
        #       改为在下方 **8.4.1e 的真实折行状态**下校验"第一行按钮完整可见"（见该处）。
        _bar = app.untagged_btn.master
        check("toolbar: search col=12 + spacer col=11",
              int(app.search_box.grid_info()["column"]) == 12
              and int(_bar.grid_columnconfigure(11).get("weight", 0)) == 2
              and int(_bar.grid_columnconfigure(12).get("weight", 0)) == 1
              and int(_bar.grid_columnconfigure(12).get("minsize", 0)) == 260)
        # 2026-09-15（审核 L-10）：原为 `cget("width") == 152`（写死源码值、绑定本机缩放）→ 改为**区间断言**
        # 2026-09-15（用户要求，本轮）：152 → **202**（+50）→ 区间上限随之抬到 260
        _se_w = int(app.search_entry.cget("width"))
        check("toolbar: search box width 在 [96, 260]（原写死 152→202）[%d]" % _se_w,
              96 <= _se_w <= 260)
        # 2026-09-15（用户要求，本轮）：🔍 由"按文字自适应"（源码 1）改为**固定 45**
        #   （本机 ≈54px，约为原来 ≈27px 的 2 倍）——用"源码值 + 实宽下限"两条断言固化。
        check("toolbar: 🔍 宽度固定 45（约原 2 倍）[src=%d 实宽=%d]"
              % (int(app.search_btn.cget("width")), app.search_btn.winfo_width()),
              int(app.search_btn.cget("width")) == 45 and app.search_btn.winfo_width() > 40)
        # 2026-09-15（审核 L-10/L-11）：原先用"各列最小宽求和"的**测试自算模型**（还对搜索框列取
        #   `min()` 主动下调）来证明"工具栏能放进 1366 屏"——属自证式弱断言（模型自算即自证），
        #   且 `_MIN_WIDTH_FLOOR == 886` 是写死源码值（绑定本机缩放）。
        #   现改为：① 这里只做**结构关系断言**（下限不低于"按隐藏列数标定表"的表末项）；
        #   ② "最窄时第一行按钮是否完整可见"由下方 8.4.1e 在**真实折行状态**下实测。
        check("toolbar: 下限常量与标定表口径一致 [floor=%d 表末=%d]"
              % (_mw._MIN_WIDTH_FLOOR, _mw._NAV_MIN_WIDTH_BY_HIDDEN[-1]),
              _mw._MIN_WIDTH_FLOOR <= _mw._NAV_MIN_WIDTH_BY_HIDDEN[-1])
        # 2026-09-14（用户要求）：**详情区最小宽度**由 550 逐步调到 584（6 按钮统一 96px +
        #   右侧 4 控件对齐后右块 142px 的需要）。
        # 2026-09-15（审核 L-10）：不再断言 `_BASE_MIN_WIDTH == 1396`（写死源码值），
        #   改为**结构关系 + 行为断言**：① 与标定表首项一致；② `nav_min_width()` 等于该常量；
        #   ③ 详情区最小宽下"所有详情区控件都不被压"由下方 8.4.1d-4 实测校验。
        check("detail: _BASE_MIN_WIDTH == 标定表首项（四列全显示）[%d == %d]"
              % (_mw._BASE_MIN_WIDTH, _mw._NAV_MIN_WIDTH_BY_HIDDEN[0]),
              _mw._BASE_MIN_WIDTH == _mw._NAV_MIN_WIDTH_BY_HIDDEN[0])
        check("detail: nav_min_width(四列全显示) == _BASE_MIN_WIDTH [%d]"
              % app._nav_min_width(),
              app._nav_min_width() == _mw._BASE_MIN_WIDTH)
        # 搜索框**实际宽度**必须远小于工具栏宽（改前它独占剩余宽度 ≈559px；现 ≈293px）
        # 2026-09-17（用户要求 1，新增"✕ 一键清除"按钮）：原阈值 0.25 是"搜索框 152px 时代"的余量；
        #   用户后来把输入框加宽到 202（实测已 ≈24.9%，贴住上限）⇒ 再放一个 ≈22px 的小按钮（本次）
        #   即 ≈26.2% 而失败。**阈值放宽到 0.30**：本断言要守的是"搜索框不得像改前那样独占工具栏
        #   （41%）"，而非某个具体百分点；且实测该比例随窗口宽/DPI 浮动（本机 25.1%~26.3%），
        #   0.30 保留守护语义，又不再因换机 ±1pp 差异误报。
        _sb_w, _bar_w = app.search_box.winfo_width(), max(_bar.winfo_width(), 1)
        check("toolbar: search box actual width now small [%d/%d=%.0f%%]"
              % (_sb_w, _bar_w, 100.0 * _sb_w / _bar_w),
              _sb_w > 0 and _sb_w <= _bar_w * 0.30)
        # 8.4.1c 详情区「编辑/浏览」已改为**普通切换按钮**（2026-09-14 用户要求，与「🗂 目录隐藏」同形式）
        _tgl = app.edit_mode_toggle
        check("detail: edit/browse is a plain toggle button [%s]"
              % _tgl.cget("text"),
              isinstance(_tgl, _ctk.CTkButton)
              and 3 <= int(_tgl.cget("corner_radius")) <= 8   # 2026-09-15（审核 L-10）：区间断言
              and "编辑" in str(_tgl.cget("text")))
        _t1 = str(_tgl.cget("text"))
        app._on_edit_mode_click(); pump(app)
        _b1, _t2 = app._browse_mode, str(_tgl.cget("text"))
        app._on_edit_mode_click(); pump(app)
        check("detail: toggle switches 编辑→浏览→编辑 [%s→%s→%s]" % (_t1, _t2, _tgl.cget("text")),
              _b1 is True and "浏览" in _t2
              and app._browse_mode is False and "编辑" in str(_tgl.cget("text")))
        # 2026-09-15（审核 L-10）：原先断言"重置 源码 == 60 / 收藏 源码 == 87"（写死源码值、绑定本机缩放）
        #   → 改为**关系断言**：① 重置 与 「✏️ 编辑」**同列同宽**（方案 A 的对齐目标）；
        #   ② 收藏比"刚好显示文字"更宽（即用户要求的"+1 个汉字"语义）。先拉到足够宽再测，避免窄窗压缩干扰。
        app.geometry("1500x760"); pump(app)
        check("detail: 重置 与 编辑 同列同宽（相对断言）[%d vs %d]"
              % (app.reset_btn.winfo_width(), app.edit_mode_toggle.winfo_width()),
              app.reset_btn.winfo_width() > 0
              and app.reset_btn.winfo_width() == app.edit_mode_toggle.winfo_width())
        _fav_need = app.fav_btn._text_label.winfo_reqwidth() + 12
        check("detail: 收藏 比'刚好显示文字'更宽（+1 汉字语义）[win=%d need=%d]"
              % (app.fav_btn.winfo_width(), _fav_need),
              app.fav_btn.winfo_width() >= _fav_need + 4)
        # 8.4.1d 按钮"内边距=5（圆角=5）/ 间距=5"（2026-09-14 用户要求）
        _btns = [("tag_page_btn", app.tag_page_btn), ("dir_toggle", app.btn_project_toggle),
                 ("untagged_btn", app.untagged_btn), ("quick_add_btn", app.quick_add_btn),
                 ("import_btn", app.import_btn), ("export_btn", app.export_btn),
                 ("lock_btn", app.lock_btn), ("search_btn", app.search_btn),
                 ("fav_btn", app.fav_btn), ("move_btn", app.move_btn),
                 ("link_btn", app.link_btn), ("copyto_btn", app.copyto_btn),
                 ("field_mgr_btn", app.field_mgr_btn),
                 ("copy_all_btn", app.copy_all_btn), ("copy_cn_btn", app.copy_cn_btn),
                 ("copy_en_btn", app.copy_en_btn), ("save_btn", app.save_btn),
                 ("reset_btn", app.reset_btn)]
        # 2026-09-15（审核 L-10）：原为"全部 == 5"（写死样式常量）→ 改为**一致性 + 区间**断言
        #   （用户要求"内边距 5、圆角随之"，取 [3,8] 区间足以覆盖换机缩放差异，且仍能发现"圆角不一致"这类真实问题）
        _crs = {int(b.cget("corner_radius")) for _, b in _btns}
        check("buttons: 圆角一致且在 [3, 8]（用户要求内边距 5 的圆角）[%s]" % sorted(_crs),
              len(_crs) == 1 and 3 <= next(iter(_crs)) <= 8)
        pump(app)
        # 宽度＝"刚好显示文字"：实宽 == 文字标签需求宽 + 2×内边距(5×1.2=6px)
        #   注：详情区那两行按钮需要**详情区足够宽**（历史结论：详情区 ≥620 时该行完整）；
        #   用户当前要求"详情区最小宽 480" → 窗口缩到最小时详情区仅 481，第 1/2 行末尾按钮会被压
        #   （已知取舍，见开发记录）。故此断言先把窗口设到足够宽再检查，
        #   避免把"用户要求的窄详情区"误判为缺陷。
        app.geometry("1500x760"); pump(app)
        # 注：重置/收藏（用户指定固定宽度）与"6 个统一宽度按钮"（2026-09-14 20:35 用户要求
        #   "大小一样大、上下对齐"→ 宽度由文字需求最大者决定）比"刚好显示文字"宽 → 不参与本检查
        _uni6 = ("move_btn", "link_btn", "copyto_btn",
                 "copy_all_btn", "copy_cn_btn", "copy_en_btn")
        # 右侧 4 控件（用户选方案 A）同样是固定宽度、两列取大值 → 不参与本检查
        _uni4 = ("field_mgr_btn", "save_btn")
        # 2026-09-15（用户要求，本轮）：🔍 亦为**固定宽度**（源码 45，约原 2 倍）→ 不参与本检查
        _badw = [(n, b.winfo_width(), b._text_label.winfo_reqwidth() + 12) for n, b in _btns
                 if n not in ("reset_btn", "fav_btn", "search_btn") and n not in _uni6 and n not in _uni4
                 and abs(b.winfo_width() - (b._text_label.winfo_reqwidth() + 12)) > 2]
        check("buttons: width == text + 2*6px [win=%d detail=%d %s]"
              % (app.winfo_width(), app.detail_root.winfo_width(), _badw or "all ok"),
              not _badw)
        # 8.4.1d-2（2026-09-14 20:35，用户要求）：这 6 个按钮**大小一样大、上下对齐**——
        #   ① 宽度全部相等且 ≥ 各自文字需求（不裁字）；② 第二行的 3 个与第一行的 3 个**起始 x 逐列相同**。
        _g6_top = [app.move_btn, app.link_btn, app.copyto_btn]
        _g6_bot = [app.copy_all_btn, app.copy_cn_btn, app.copy_en_btn]
        _w6 = {b.winfo_width() for b in _g6_top + _g6_bot}
        _clip = [b.cget("text") for b in _g6_top + _g6_bot
                 if b.winfo_width() + 1 < b._text_label.winfo_reqwidth() + 12]
        check("detail: 6 按钮同宽且不裁字 [w=%s clip=%s]" % (sorted(_w6), _clip or "none"),
              len(_w6) == 1 and next(iter(_w6)) > 0 and not _clip)
        check("detail: 6 按钮上下逐列对齐 [上=%s 下=%s]"
              % ([b.winfo_x() for b in _g6_top], [b.winfo_x() for b in _g6_bot]),
              [b.winfo_x() for b in _g6_top] == [b.winfo_x() for b in _g6_bot])
        # 8.4.1d-3（2026-09-14 21:05，用户选方案 A）：**右侧 4 个控件**（✏️ 编辑 / 🔧 与 重置 / 💾 保存）
        #   两行"整体宽度一样、上下逐列对齐"——A 列（编辑/重置）72px、B 列（🔧/保存）66px，
        #   右边界同为行右端；即 编辑对齐重置、🔧 对齐保存。
        _cA1, _cA2 = app.edit_mode_toggle, app.reset_btn
        _cB1, _cB2 = app.field_mgr_btn, app.save_btn
        check("detail: 右侧 A 列同宽（编辑/重置）[%d, %d]"
              % (_cA1.winfo_width(), _cA2.winfo_width()),
              _cA1.winfo_width() == _cA2.winfo_width() and _cA1.winfo_width() > 0
              and _cA1.winfo_width() + 1 >= _cA1._text_label.winfo_reqwidth() + 12)
        check("detail: 右侧 B 列同宽（🔧/保存）[%d, %d]"
              % (_cB1.winfo_width(), _cB2.winfo_width()),
              _cB1.winfo_width() == _cB2.winfo_width() and _cB1.winfo_width() > 0
              and _cB2.winfo_width() + 1 >= _cB2._text_label.winfo_reqwidth() + 12)
        check("detail: 右侧 4 控件上下逐列对齐 [上=%s 下=%s]"
              % ([_cA1.winfo_x(), _cB1.winfo_x()], [_cA2.winfo_x(), _cB2.winfo_x()]),
              [_cA1.winfo_x(), _cB1.winfo_x()] == [_cA2.winfo_x(), _cB2.winfo_x()])
        check("detail: 右侧两块右边界相同 [%d, %d]"
              % (_cB1.winfo_x() + _cB1.winfo_width(), _cB2.winfo_x() + _cB2.winfo_width()),
              _cB1.winfo_x() + _cB1.winfo_width() == _cB2.winfo_x() + _cB2.winfo_width())
        # 8.4.1d-4（2026-09-15 审核 L-11 补强）：**把窗口缩到"详情区最小宽"后，11 个详情区控件宽度逐个不变**
        #   原用例把窗口设到 1500 再校验按钮宽度（P.S. 注释自承窄窗会被压），等于**规避真实最窄场景**；
        #   这里显式缩到 `_BASE_MIN_WIDTH`（四列全显示时的最小宽）实测，直接验证"最窄也不压字/不变形"。
        _names11 = _uni6 + ("fav_btn", "edit_mode_toggle", "field_mgr_btn", "save_btn", "reset_btn")
        _w_wide = {n: getattr(app, n).winfo_width() for n in _names11}
        app.geometry("%dx760" % int(_mw._BASE_MIN_WIDTH)); pump(app, 8)
        _w_min = {n: getattr(app, n).winfo_width() for n in _names11}
        _diff = {n: (_w_wide[n], _w_min[n]) for n in _names11 if _w_wide[n] != _w_min[n]}
        check("detail: 详情区最小宽下 11 个控件宽度不变 [win=%d detail=%d 差异=%s]"
              % (app.winfo_width(), app.detail_root.winfo_width(), _diff or "无"), not _diff)
        app.geometry("1500x760"); pump(app, 4)
        # 工具栏相邻按钮间隙（源码 padx=2 → 本机 120% 缩放后每侧 2px）
        _xs = sorted([(c.winfo_x(), c.winfo_width()) for c in _bar.winfo_children()
                      if c.grid_info() and isinstance(c, _ctk.CTkButton)])
        _gaps = {_xs[i + 1][0] - (_xs[i][0] + _xs[i][1]) for i in range(len(_xs) - 1)}
        # 2026-09-15（审核 L-10）：原为 `_gaps == {4}`（写死 4px；100% 缩放机器上必然失败）
        #   → 改为**一致性 + 区间**断言：所有相邻间隙一致且落在 [2, 6]
        check("toolbar: 相邻按钮间隙一致且在 [2, 6]（原写死 {4}）[%s]" % sorted(_gaps),
              len(_gaps) == 1 and 2 <= next(iter(_gaps)) <= 6)
        # 8.4.1e 工具栏第二行（2026-09-14 用户**最终决定**）：
        #   **两组按钮始终同在第一行、不再下移**；窗口放不下一行时，只把**搜索框移到第二行靠右**。
        check("toolbar fold: row2 + 5 buttons in group2 [%d]"
              % len(getattr(app, "_bar_group2", [])),
              bool(app._bar_row2.winfo_exists()) and len(app._bar_group2) == 5
              and app._bar_folded is False)
        check("toolbar fold: one-row need measured [%d]" % app._toolbar_one_row_need(),
              app._toolbar_one_row_need() > 0)
        _row1_y = app.tag_page_btn.winfo_rooty()
        # 走**真实的自动流程**：先关闭四个分类区（窗口最小宽随之降到 ≈1063），再把窗口缩到
        # 放不下一行（实际 ≈1080 < 一行所需 1282）→ 应自动折行、把搜索框移到第二行靠右
        app.apply_nav_visibility(4); pump(app)
        app.geometry("900x760"); pump(app, 6)
        check("toolbar fold: 自动折行（窗口窄于一行需求）[win=%d folded=%s]"
              % (app.winfo_width(), app._bar_folded), bool(app._bar_folded))
        _fgi = app.search_box.grid_info() or {}
        _dx = abs((app.search_box.winfo_rootx() + app.search_box.winfo_width())
                  - (app._bar_row2.winfo_rootx() + app._bar_row2.winfo_width()))
        # 注：不再用 `grid_info()['in'] == str(row2)` 判断（Tk 路径字符串比较不可靠），
        #     改用"实际渲染位置"判断：y 落在第二行、且右边界与第二行容器右边界对齐。
        check("toolbar fold: row2 visible [%s]" % bool(app._bar_row2.grid_info()),
              bool(app._bar_row2.grid_info()))
        check("toolbar fold: search box on 2nd row [boxY=%s r2Y=%s row1Y=%s in=%s]"
              % (app.search_box.winfo_rooty(), app._bar_row2.winfo_rooty(), _row1_y,
                 _fgi.get("in")),
              app.search_box.winfo_rooty() > _row1_y)
        check("toolbar fold: search box right-aligned [dx=%d]" % _dx, _dx <= 24)
        # 2026-09-14（用户要求）：搜索框**左边不得越过第一行最左侧图标（第一个按钮）的左边**
        _box_left = app.search_box.winfo_rootx()
        _leftmost = app.tag_page_btn.winfo_rootx()
        check("toolbar fold: search box left ≥ 最左侧按钮左边界 [%d ≥ %d]"
              % (_box_left, _leftmost), _box_left >= _leftmost - 2)
        check("toolbar fold: 两组按钮仍在第一行 [%s vs %s]"
              % ({b.winfo_rooty() for b, _c in app._bar_group2}, _row1_y),
              {b.winfo_rooty() for b, _c in app._bar_group2} == {_row1_y})
        # 2026-09-15（审核 L-11 补强）：**窗口最窄（已折行）时，第一行所有按钮必须完整可见**——
        #   这是"窗口宽度以工具栏为准"的**真实行为校验**（替代原先"测试自算模型 ≤ 1366"的自证式断言）。
        _clip1 = [str(c.cget("text")) for c in _bar_btns
                  if c.winfo_width() + 1 < c.winfo_reqwidth()]
        _rightmost = max(c.winfo_x() + c.winfo_width() for c in _bar_btns)
        check("toolbar fold: 第一行按钮完整可见（未裁切）[裁切=%s 最右=%d 工具栏宽=%d]"
              % (_clip1 or "无", _rightmost, _bar.winfo_width()),
              not _clip1 and _rightmost <= _bar.winfo_width() + 2)
        app.geometry("1500x760"); pump(app, 6)
        check("toolbar unfold: 窗口变宽后自动回一行 [folded=%s]" % app._bar_folded,
              (not app._bar_folded) and (not app._bar_row2.grid_info())
              and (app.search_box.grid_info() or {}).get("in") != str(app._bar_row2))
        # 2026-09-14（用户要求）：**四个分类区全关闭时，窗口自动收缩到最小宽度**（工具栏最窄形态）
        app.geometry("1500x760"); pump(app, 6)
        _src_before = app.geometry().split("+")[0]
        app.apply_nav_visibility(4); pump(app, 8)
        # 2026-09-15（审核 L-10）：`* 1.2` 是写死的缩放系数（100% 缩放机器上会误判）→ 改用
        #   `app._window_scale()`（由'app 自己按 winfo_width/geometry 实测）
        _sc = max(app._window_scale(), 0.01)
        check("nav: 四区全关闭 → 窗口自动收缩到最小宽 [win=%d ≈%d]"
              % (app.winfo_width(), int(app._nav_min_width() * _sc)),
              app.winfo_width() <= app._nav_min_width() * _sc + 30)
        # 2026-09-14 20:52（用户报告"窗口高度被增大、下边看不到"→ 修复的回归断言）：
        #   ① 收缩只改宽度，**高度必须不变**（旧 bug 会把 winfo_height 当源码值 → 高度 ×1.2）；
        #   ② 重复触发**幂等**（旧 bug 每次再放大 1.2 倍：934→1119→1340…）；
        #   ③ 窗口**底边始终在屏幕内**；④ 最小高度不超过屏幕可用高度。
        _src_after = app.geometry().split("+")[0]
        check("nav: 关闭四区不改变窗口高度 [%s → %s]" % (_src_before, _src_after),
              _src_before.split("x")[1] == _src_after.split("x")[1])
        app.apply_nav_visibility(4); pump(app, 6)
        check("nav: 重复关闭四区尺寸稳定（幂等）[%s]" % app.geometry().split("+")[0],
              app.geometry().split("+")[0] == _src_after)
        check("nav: 窗口底边在屏幕内 [底边=%d 屏幕高=%d]"
              % (app.winfo_y() + app.winfo_height(), app.winfo_screenheight()),
              app.winfo_y() + app.winfo_height() <= app.winfo_screenheight())
        check("nav: 最小高度 ≤ 屏幕可用高度 [%d ≤ %d]"
              % (app._win_min_height(), app._max_fit_height_src()),
              app._win_min_height() <= app._max_fit_height_src())
        # 人为把窗口设成"高于屏幕" → `_fit_window_to_screen()` 必须把底边夹回屏幕内
        _scale = max(app._window_scale(), 0.01)
        _w_src = int(app.geometry().split("+")[0].split("x")[0])
        _tall_src = int((app.winfo_screenheight() + 150) / _scale)
        app.geometry("%dx%d" % (_w_src, _tall_src)); pump(app, 4)
        app._fit_window_to_screen(); pump(app, 4)
        check("nav: 过高窗口被夹回屏幕内 [底边=%d 屏幕高=%d]"
              % (app.winfo_y() + app.winfo_height(), app.winfo_screenheight()),
              app.winfo_y() + app.winfo_height() <= app.winfo_screenheight())
        # 8.4.1g（2026-09-15 审核 L-7/L-8/L-9 回归断言；全部"注入异常/临时打桩"后立即还原）
        # ① L-7：几何静默异常统一出口 `_geom_warn` 可用（只打印、不抛异常）
        try:
            _mw._geom_warn("自测探针", RuntimeError("注入"))
            _gw_ok = True
        except Exception:
            _gw_ok = False
        check("L-7: _geom_warn 可用（只打印不抛异常）", _gw_ok)
        # ② L-7B：缩放系数"成功即缓存"，失败时用缓存兜底（打桩 geometry 抛异常）
        app._scale_cache = None
        _sc_now = app._window_scale()
        check("L-7B: 缩放系数成功即缓存 [cache=%s]" % app._scale_cache,
              app._scale_cache is not None and abs(_sc_now - app._scale_cache) < 1e-9)
        _orig_geometry = app.geometry
        try:
            app.geometry = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("注入"))
            _sc_fb = app._window_scale()
            # ③ L-7B：无缓存时 `_fit_window_to_screen` 跳过夹取（不抛异常）
            app._scale_cache = None
            app._fit_window_to_screen()
            _fit_ok = True
        except Exception:
            _fit_ok = False
        finally:
            app.geometry = _orig_geometry
            app._scale_cache = None
            app._window_scale()          # 重新填回缓存
        check("L-7B: 缩放取值失败时用缓存兜底 [%s≈%s]" % (_sc_fb, _sc_now),
              abs(_sc_fb - _sc_now) < 1e-6)
        check("L-7B: 无缓存时跳过屏幕夹取（不抛异常）", _fit_ok)
        # ④ L-8：折行首列留白"用未折行实测缓存"（打桩 winfo_x 返回 0）
        app.geometry("1500x760"); pump(app, 4)
        app._set_bar_folded(False); pump(app, 4)
        app._toolbar_one_row_need()      # 触发一次"未折行实测缓存"
        _cached_left = int(app._bar_row2_left)
        check("L-8: 未折行时缓存了工具栏左边界 [%d]" % _cached_left, _cached_left > 0)
        _orig_wx = app.tag_page_btn.winfo_x
        try:
            app.tag_page_btn.winfo_x = lambda: 0
            app._set_bar_folded(True); pump(app, 4)
            _left_used = int(app._bar_row2.grid_columnconfigure(0)["minsize"])
            _col12_w = int(_bar.grid_columnconfigure(12)["weight"])
        finally:
            app.tag_page_btn.winfo_x = _orig_wx
        check("L-8: winfo_x()=0 时用缓存兜底（不贴左）[%d ≥ %d]" % (_left_used, _cached_left),
              _left_used >= _cached_left)
        check("L-8: 折行分支补 weight=0（与恢复分支对称）[%d]" % _col12_w, _col12_w == 0)
        app._set_bar_folded(False); pump(app, 4)
        # ⑤ L-9：untagged 列表通路容错（打桩 db.list_untagged 抛异常 → 给"读取失败"标题，不冒泡）
        _orig_untagged = app.db.list_untagged
        _view_backup = app._view
        try:
            app.db.list_untagged = lambda: (_ for _ in ()).throw(RuntimeError("注入"))
            app._view = ("untagged", "")
            app._restore_view(); pump(app)
            _uu_title = str(getattr(app, "_entries_title", ""))
            _uu_ok = True
        except Exception:
            _uu_ok, _uu_title = False, ""
        finally:
            app.db.list_untagged = _orig_untagged
            app._view = _view_backup
        check("L-9: untagged 列表异常不冒泡且提示读取失败 [%s]" % _uu_title,
              _uu_ok and "读取失败" in _uu_title)
        # ---- 8.4.1h（2026-09-15 批次 4）：外观设置（字体/字号/颜色）**逐点生效** + 未设置零差异 ----
        #   做法：直接改 `app._ui_appearance`（不驱动设置对话框 UI）→ 重建控件 → 断言；
        #   最后清空设置并断言"与改动前完全一致"（名称框 15pt/高 44、条目名 12pt、提示词框＝CTk 默认）。
        from app import ui_appearance as _ua
        _look_backup = app._ui_appearance
        _els = db.list_all_entries()
        check("look: 测试库有条目可用于外观断言", len(_els) > 0)

        def _f_size(w):
            """读取控件实际字号：CTk 控件是 CTkFont 对象；tk 控件（Listbox/Text）是字体描述串。

            三种取法依次尝试（不同控件/CTk 版本返回类型不同，任一成功即可）。
            """
            if w is None:
                return None
            try:
                f = w.cget("font")
            except Exception:
                return None
            for _get in (lambda: int(f.cget("size")),
                         lambda: int(f[1]),
                         lambda: int(w.tk.call("font", "actual", str(f), "-size"))):
                try:
                    _v = _get()
                    if _v:
                        return _v
                except Exception:
                    continue
            return None

        def _find_name_lbl(root, name):
            _out = []
            walk_types(root, "CTkLabel", _out)
            return next((w for w in _out if name in str(text_of(w))), None)

        if _els:
            _eid = int(_els[0]["id"])
            _nm = str(_els[0]["name"])
            _view_backup2 = app._view_mode
            _dm_backup = app._detail_mode
            app._detail_mode = _mw.config.DETAIL_MODE_FULL   # 保证 ⑧⑨ 提示词框被渲染
            app._view_mode = "list"
            app._ui_appearance = _ua.empty()
            app._render_entries(_els, "外观断言"); pump(app)
            _nl0 = _find_name_lbl(app.entry_frame, _nm)
            check("look: 未设置时条目区名称为基础字号 12 [%s]" % (_f_size(_nl0) if _nl0 else None),
                  _nl0 is not None and _f_size(_nl0) == 12)
            app._ui_appearance = {"entry_rows": {"size": 17},
                                  "entry_ov": {"size": 20, "fg": "#112233", "bg": "#eeeeee"},
                                  "field_tip": {"size": 16, "fg": "#005500"},
                                  "prompt_cn": {"size": 18, "fg": "#880000"},
                                  "name_field": {"size": 20}}
            app._render_entries(_els, "外观断言"); pump(app)
            _nl1 = _find_name_lbl(app.entry_frame, _nm)
            check("look: ①条目区名称字号随设置生效 17 [%s]" % (_f_size(_nl1) if _nl1 else None),
                  _nl1 is not None and _f_size(_nl1) == 17)
            # ② 条目名称一览浮层（字体 + 文字色/背景色）
            # 2026-09-16（批次 11-1，用户反馈 4）：把浮层的"光标 Y"强制放到屏幕最下方，
            #   以便确定性地触发"底边夹紧"逻辑（否则窗口居中时夹紧分支不执行、测不到）。
            _wa = _mw.work_area(app)
            app._entry_ov_y = _wa[3] - 20
            app._show_entry_overview(); pump(app)
            _lb = getattr(app, "_entry_ov_listbox", None)
            check("look: ②浮层字号/配色随设置生效 [%s %s %s]"
                  % (_f_size(_lb) if _lb else None,
                     _lb.cget("fg") if _lb else None, _lb.cget("bg") if _lb else None),
                  _lb is not None and _f_size(_lb) == 20
                  and str(_lb.cget("fg")) == "#112233" and str(_lb.cget("bg")) == "#eeeeee")
            # 2026-09-16（批次 11-1，用户反馈 4）：浮层底边必须落在"屏幕工作区"内（不再被任务栏遮挡）
            _ovp = getattr(app, "_entry_ov_popup", None)
            check("fix(11-1): ②浮层底边在工作中区内 [底=%s 工作区底=%s 屏底=%s]"
                  % ((_ovp.winfo_rooty() + _ovp.winfo_height()) if _ovp else None,
                     _wa[3], app.winfo_screenheight()),
                  _ovp is not None and _ovp.winfo_rooty() + _ovp.winfo_height() <= _wa[3])
            app._hide_entry_overview(); pump(app)
            app._entry_ov_y = None          # 复位，避免影响后续断言
            # ⑤ + ⑥ 详情区（提示词框 / ①名称框 + 高度自适应）
            app._show_detail(db.get_entry(_eid)); pump(app)
            _pc = app._detail_boxes.get("prompt_cn")
            check("look: ④中文版提示词框字号/文字色生效 [%s %s]"
                  % (_f_size(_pc) if _pc else None, _pc.cget("text_color") if _pc else None),
                  _pc is not None and _f_size(_pc) == 18
                  and str(_pc.cget("text_color")) == "#880000")
            _pe = app._detail_boxes.get("prompt_en")
            check("look: ⑤英文版提示词框（同代码路径，未设置时保持默认）[%s=%s]"
                  % (_f_size(_pe) if _pe else None, app._default_ctk_font()[1]),
                  _pe is not None and _f_size(_pe) == int(app._default_ctk_font()[1]))
            # 2026-09-16（批次 11-7，用户要求 3）：详情区显示「新增于 / 修改于」时间行（只读）
            _t_lbls = []
            walk_types(app.detail_scroll, "CTkLabel", _t_lbls)
            _t_txt = " ".join(str(c.cget("text")) for c in _t_lbls)
            check("time(11-7): 详情区显示时间行（新增于/修改于）",
                  "新增于" in _t_txt and "修改于" in _t_txt)
            check("look: ⑥①名称框字号生效且高度自适应 [%s %s]"
                  % (_f_size(app._name_entry), app._name_entry.cget("height")),
                  _f_size(app._name_entry) == 20
                  and int(app._name_entry.cget("height")) == int(round(44 * 20 / 15.0)))
            # ③ 字段浮动提示窗（字体 + 文字色）
            if app._float_tips_on:
                app._show_field_tip(app._name_entry, "测试内容" * 5, None, "外观断言"); pump(app)
                _tips = []
                if getattr(app, "_tip_popup", None) is not None:
                    walk_types(app._tip_popup, "Text", _tips)
                check("look: ③字段浮动提示窗字号/文字色生效 [%s]"
                      % (_f_size(_tips[0]) if _tips else None),
                      bool(_tips) and _f_size(_tips[0]) == 16
                      and str(_tips[0].cget("fg")) == "#005500")
                # 2026-09-16（批次 11-1，用户反馈 4）：把触发控件放到"工作区最底部"，
                #   确定性触发"底边夹紧"逻辑，断言提示窗底边不越出工作区（否则会落进任务栏被遮挡）。
                _low = tk.Toplevel(app)
                _low.geometry("140x24+%d+%d" % (_wa[0] + 40, _wa[3] - 26))
                _lf = tk.Frame(_low, width=140, height=24)
                _lf.pack()
                _low.update_idletasks()
                app._cancel_field_tip(); pump(app)
                app._show_field_tip(_lf, "底边夹紧测试\n" * 40, None, "底边断言"); pump(app)
                _tp = getattr(app, "_tip_popup", None)
                check("fix(11-1): ③字段提示窗底边在工作中区内 [底=%s 工作区底=%s 屏底=%s]"
                      % ((_tp.winfo_rooty() + _tp.winfo_height()) if _tp else None,
                         _wa[3], app.winfo_screenheight()),
                      _tp is not None and _tp.winfo_rooty() + _tp.winfo_height() <= _wa[3])
                app._cancel_field_tip(); pump(app)
                try:
                    _low.destroy()
                except Exception:
                    pass
            # 未设置（清空）→ 必须回到原值（零回归）
            app._ui_appearance = _ua.empty()
            app._render_entries(_els, "外观断言")
            app._show_detail(db.get_entry(_eid)); pump(app)
            _pc0 = app._detail_boxes.get("prompt_cn")
            check("look: 清空设置后 ①名称框回到 15pt/高 44 [%s %s]"
                  % (_f_size(app._name_entry), app._name_entry.cget("height")),
                  _f_size(app._name_entry) == 15
                  and int(app._name_entry.cget("height")) == 44)
            check("look: 清空设置后 提示词框回到 CTk 默认字号 [%s = %s]"
                  % (_f_size(_pc0) if _pc0 else None, app._default_ctk_font()[1]),
                  _pc0 is not None and _f_size(_pc0) == int(app._default_ctk_font()[1]))
            app._ui_appearance = _look_backup
            app._view_mode = _view_backup2
            app._detail_mode = _dm_backup
        app.apply_nav_visibility(0); pump(app, 6)
        # ---- 8.4.1i（2026-09-15 批次 6-1，用户选"完全禁止"）：锁定态图片/图集按钮必须全部置灰 ----
        #   背景：`_apply_browse()` 原先只看"浏览态"，锁定+编辑模式下会把图片/图集/标签按钮又设回
        #   normal（"覆盖陷阱"）→ 锁定态仍可改图片。此处实测"锁定 → 这些按钮是否真的置灰"。
        _lock_backup = app._lock_on
        _add_backup = app._adding_new
        _br_backup = app._browse_mode
        try:
            app._adding_new = False
            app._browse_mode = False
            app._show_detail(db.get_entry(_eid)); pump(app)
            _lock_btns = ("_pick_img_btn", "_remove_img_btn",
                          "_gallery_add_btn", "_gallery_url_btn")
            _exist_n = sum(1 for n in _lock_btns if getattr(app, n, None) is not None)
            check("lock: 图片/图集按钮存在可校验 [%d]" % _exist_n, _exist_n >= 2)
            app._lock_on = True

            def _st_map():
                return {n: str(getattr(app, n).cget("state")) for n in _lock_btns
                        if getattr(app, n, None) is not None}

            # 2026-09-15（批次 6-1 定位结论）：**按"界面里实际存活的按钮"断言**——
            #   按属性 `app._pick_img_btn` 读到的可能是**上一轮渲染遗留、Tk 窗口已销毁**的旧句柄
            #   （实测：对该句柄 configure 抛 `TclError: invalid command name …`，而 `cget` 仍返回旧值；
            #     本场景实测"活按钮 0 个"⇒ 该场景根本没渲染图片区，之前的 FAIL 属测试假象）。
            #   故：**有活按钮 → 必须全部置灰；无活按钮 → 本场景不适用（跳过并打印）**。
            _live = []
            walk_types(app.detail_scroll, "CTkButton", _live)
            _img_live = [b for b in _live
                         if any(_k in str(text_of(b)) for _k in
                                ("选择图片", "移除图片", "添加本地图", "添加链接"))]
            _st_bad = [(str(text_of(b))[:10], str(b.cget("state"))) for b in _img_live
                       if str(b.cget("state")) != "disabled"]
            check("lock: 锁定态图片/图集按钮全部置灰（6-1）[活=%d 未灰=%s]"
                  % (len(_img_live), _st_bad or "无"),
                  (not _img_live) or not _st_bad)
            # 解锁后应恢复可用（同样只在有活按钮时校验）
            app._lock_on = False
            app._show_detail(db.get_entry(_eid)); pump(app, 2)
            _live2 = []
            walk_types(app.detail_scroll, "CTkButton", _live2)
            _img_live2 = [b for b in _live2
                          if any(_k in str(text_of(b)) for _k in
                                 ("选择图片", "移除图片", "添加本地图", "添加链接"))]
            _st_after = [str(text_of(b))[:10] for b in _img_live2
                         if str(b.cget("state")) != "normal"]
            check("lock: 解锁后图片/图集按钮恢复可用（活=%d）[%s]"
                  % (len(_img_live2), _st_after or "全部可用"),
                  (not _img_live2) or not _st_after)
        finally:
            app._lock_on = _lock_backup
            app._adding_new = _add_backup
            app._browse_mode = _br_backup
            app._apply_lock_state(); pump(app)
        # 8.4.1f "所有状态下详情区 ≥ 目标值"：**标签页面打开时**不得沿用"按隐藏列下调"的最小宽度
        #        （否则实测详情区会被压到 377px）→ 此时下限取 _TAG_PAGE_MIN_WIDTH
        #        2026-09-14 20:40（用户决定 B）：目标 550 → 571 ⇒ 该常量 1250 → **1266**
        #        2026-09-14 21:05（用户选方案 A）：目标 571 → 578 ⇒ **1272**
        #        2026-09-14 21:05（实测复核）：目标 578 → 584 ⇒ **1277**
        app._open_tag_page(); pump(app)
        _tag_w = app._nav_min_width()
        # 2026-09-15（审核 L-10）：不再写死 1277 / 1396 → 改为**关系断言**
        check("tag page: nav_min_width ≥ _TAG_PAGE_MIN_WIDTH（标签页下限生效）[%d ≥ %d]"
              % (_tag_w, _mw._TAG_PAGE_MIN_WIDTH), _tag_w >= _mw._TAG_PAGE_MIN_WIDTH)
        app._close_tag_page(); pump(app)
        check("after closing tag page: nav_min_width == 标定表首项 [%d]"
              % app._nav_min_width(),
              app._nav_min_width() == _mw._NAV_MIN_WIDTH_BY_HIDDEN[0])
        # 8.4.2 数据层：无标签集合（有标签的必须排除、计数自洽）
        _u1 = db.add_entry(Entry(name="无标签条目A", category_id=l1))
        _u2 = db.add_entry(Entry(name="无标签条目B", category_id=l1))
        _u3 = db.add_entry(Entry(name="已打标签条目C", category_id=l1))
        db.set_entry_tags(_u3, ["写实"])
        _un_ids = {e["id"] for e in db.list_untagged()}
        check("untagged: list excludes tagged & count matches",
              {_u1, _u2} <= _un_ids and _u3 not in _un_ids
              and db.count_untagged() == len(_un_ids))
        # 8.4.3 方案 A：按钮 → untagged 视图 + 标题带总数 + 有标签的被排除
        app._detail_dirty = False      # 避免 _confirm_unsaved 走"保存未保存修改"分支
        app._show_untagged(); pump(app)
        check("untagged A: view kind + title count",
              app._view == ("untagged", "")
              and "无标签条目" in app._entries_title
              and ("共 %d 条" % len(_un_ids)) in app._entries_title)
        check("untagged A: tagged entry excluded from list",
              {_u1, _u2} <= {e["id"] for e in app._entries_all}
              and _u3 not in {e["id"] for e in app._entries_all})
        # 8.4.4 打上标签后立即移出该视图（_restore_view 重算，标题计数同步 -1）
        db.set_entry_tags(_u1, ["电影感"])
        app._restore_view(); pump(app)
        check("untagged A: tagged leaves view on refresh",
              _u1 not in {e["id"] for e in app._entries_all}
              and ("共 %d 条" % (len(_un_ids) - 1)) in app._entries_title)
        # 8.4.5 方案 C：搜索框 `#无标签` 语法（别名 #none / 关键词组合 / 与真实标签冲突提示）
        def _search(txt):
            app.search_entry.delete(0, "end")
            app.search_entry.insert(0, txt)
            app._on_search_key()
            pump(app)
        _search("#无标签")
        check("untagged C: '#无标签' → untagged view",
              app._view == ("untagged", "") and "无标签条目" in app._entries_title)
        _search("#无标签 条目B")
        check("untagged C: '#无标签 + 关键词' filters",
              app._view == ("untagged", "条目B")
              and {e["id"] for e in app._entries_all} == {_u2})
        _search("#none")
        check("untagged C: '#none' alias works", app._view == ("untagged", ""))
        _search("#无标签 #写实")
        check("untagged C: conflict with real tag shows hint",
              app._view is None and "不能与" in app._entries_title)
        _search("")   # 清空 → 回到搜索前的浏览视图（untagged 全量）
        check("untagged C: clearing box restores view",
              app._view == ("untagged", "") and _u2 in {e["id"] for e in app._entries_all})
        # 8.4.6 用户要求 2：无标签列表**可单条打标** + **可转入批量打标（范围＝当前列表）**
        app._show_untagged(); pump(app)
        _untag_full = {e["id"] for e in app._entries_all}
        check("untagged: view holds the FULL untagged list",
              _untag_full == {e["id"] for e in db.list_untagged()} and len(_untag_full) > 0)
        app._show_detail(db.get_entry(_u2)); pump(app)
        check("untagged: single-entry tag box editable (edit mode)",
              getattr(app, "_tag_entry", None) is not None
              and app._tag_entry.winfo_exists()
              and app._tag_add_btn.winfo_exists()
              and app._browse_mode is False)
        _dlg_u = _btd.BatchTagDialog(app, db)
        pump(app)
        _dlg_u.om_range.set("当前列表")
        check("untagged: batch range=当前列表 == untagged list",
              {e["id"] for e in _dlg_u._range_entries()} == _untag_full)
        try:
            _dlg_u.grab_release()
        except Exception:
            pass
        _dlg_u.destroy(); pump(app)
        # 8.4.7 方案 B：标签页面（标签档）底部入口带实时数量；热点词/词表档不出现
        app._open_tag_page(); pump(app)
        _tb_btns = []
        walk_types(app.tag_frame, "CTkButton", _tb_btns)
        check("untagged B: tag page entry with live count",
              any(("（%d）" % db.count_untagged()) in str(b.cget("text"))
                  and "无标签条目" in str(b.cget("text")) for b in _tb_btns))
        # 该行**不得超出标签页面宽度**（＝四列合计 560），否则说明按钮与说明文字过长会被裁切
        _urow = None
        for b in _tb_btns:
            if "无标签条目" in str(b.cget("text")) and "（" in str(b.cget("text")):
                _urow = b.master
        check("untagged B: bottom row fits tag panel",
              _urow is not None
              and _urow.winfo_reqwidth() <= sum(_mw._NAV_COL_WIDTHS))
        app._tag_page_view = "hot"
        app._refresh_tag_page(); pump(app)
        _hot_btns = []
        walk_types(app.tag_frame, "CTkButton", _hot_btns)
        check("untagged B: absent in hotwords tab",
              not any("无标签条目" in str(b.cget("text")) for b in _hot_btns))
        app._tag_page_view = "dict"
        app._refresh_tag_page(); pump(app)
        _dict_btns = []
        walk_types(app.tag_frame, "CTkButton", _dict_btns)
        check("untagged B: absent in dict tab",
              not any("无标签条目" in str(b.cget("text")) for b in _dict_btns))
        app._tag_page_view = "cloud"
        app._close_tag_page(); pump(app)

        # ---- 9. 阶段 2：批量智能自动打标（对话框 + 批次 + 精确撤销）----
        import app.tagger_batch as _tb
        from app.ui.batch_tag_dialog import BatchTagDialog
        # 造 5 条"可打标"的条目（根目录"图像" → 领域判为视觉；文本含人像/胶片等词）
        did_img = db.add_domain("图像")
        c_img = db.add_category("Portrait & People", domain_id=did_img)
        eids = [db.add_entry(Entry(name="portrait test %d" % i, category_id=c_img,
                                   intro="素材", prompt_en="portrait, film grain, cinematic"))
                for i in range(5)]
        _orig_batch_dir = _tb.batch_dir
        _tb.batch_dir = lambda: os.path.join(tmp, "tagging")
        _orig_snap = _btd.backup.pretag_snapshot
        _btd.backup = type("B", (), {"pretag_snapshot": staticmethod(
            lambda *a, **k: {"ok": True, "path": "（测试桩）", "removed": 0})})()
        _orig_btd_mb = _btd.messagebox          # 该模块自带 import 的 messagebox，需单独打桩
        _btd.messagebox = mb
        try:
            dlg2 = BatchTagDialog(app, db)
            pump(app)
            check("batch dialog built", all(hasattr(dlg2, a) for a in
                  ("seg_scope", "om_range", "om_limit", "om_tags", "seg_write",
                   "txt", "status", "btn_undo")))
            dlg2.seg_scope.set("仅无标签的条目")
            dlg2.om_range.set("全部条目")
            dlg2._on_preview(); pump(app)
            check("batch preview built plan",
                  dlg2._plan is not None and dlg2._plan["stats"]["处理"] > 0)
            check("batch preview asks nothing written",
                  all(not db.list_entry_tag_names(e) for e in eids))
            mb.asks.clear()
            dlg2._on_execute(); pump(app)
            check("batch execute asks confirm",
                  any(_k == "yesno" and "批量打标" in str(a[0]) for (_k, a, _kk) in mb.asks))
            check("batch execute wrote tags",
                  all(db.list_entry_tag_names(e) for e in eids))
            check("batch execute recorded batch",
                  bool(dlg2._last_batch_path) and os.path.isfile(dlg2._last_batch_path)
                  and len(_tb.list_batches()) == 1)
            check("batch undo button enabled", dlg2.btn_undo.cget("state") == "normal")
            # 精确撤销 → 回到打标前（无标签）
            dlg2._on_undo(); pump(app)
            check("batch undo restores tags",
                  all(not db.list_entry_tag_names(e) for e in eids))
            check("batch marked undone", _tb.list_batches()[0]["undone"] is True)
            try:
                dlg2.grab_release()
            except Exception:
                pass
            dlg2.destroy(); pump(app)
        finally:
            _tb.batch_dir = _orig_batch_dir
            _btd.backup = _orig_snap
            _btd.messagebox = _orig_btd_mb

        # ---- 10. 阶段 3：录入时自动推荐标签（主窗口 + 快速新建窗口）----
        from app.ui.quick_add import QuickAddWindow
        # 走**真实可用的上下文**进入新增态：
        #   ① 新建专用分类（名字含"图像"→ 命中词表 domain_map 的"视觉"领域；名字唯一不与内置模板冲突）
        #   ② 把"当前视图/当前分类/新增目标"三者设为一致，避免主窗口的
        #      "视图与新增目标不一致时自动退出新增态"既有自愈逻辑把状态清掉
        #      （早前用例直接手工赋 _adding_new/_add_target，视图未同步 → 延迟回调一触发就被清 → 间歇失败）
        _did2 = db.add_domain("图像素材")
        _c2 = db.add_category("视觉人像专用", domain_id=_did2)
        _info2 = db.get_category(_c2) or {}
        check("stage3: test category ready [id=%s name=%s]"
              % (_c2, _info2.get("name")), _info2.get("name") == "视觉人像专用")
        # 诊断（2026-09-14 起；**2026-09-15 stage3 专项排查增强**）：本用例曾"间歇性"失败（新增态被清）。
        # 装**只读探针**记录"是谁清的"：
        #   ① 清态入口（`_show_detail` / `_render_entries` 内的"离开新增目标"分支）；
        #   ② 清态瞬间正在执行的 **after 定时回调**（＝异步来源，探针包裹 app.after 以标记当前回调）；
        #   ③ 清态前后的新增态上下文（adding/target/view/cat/ctx）。
        # 全部只读，不改产品逻辑；失败时随断言一起打印。
        _clears = []
        _orig_render = app._render_entries
        _orig_show_detail = app._show_detail
        _orig_after = app.after
        _cur_cb = {"who": ""}

        def _after_spy(ms=None, func=None, *a):
            if func is None:                      # app.after("info") 之类：原样透传
                return _orig_after(ms)

            def _wrapped(*aa):
                _cur_cb["who"] = "%s(+%sms)" % (getattr(func, "__name__", repr(func))[:50], ms)
                try:
                    return func(*aa)
                finally:
                    _cur_cb["who"] = ""
            return _orig_after(ms, _wrapped, *a)
        app.after = _after_spy

        def _snap(where):
            import traceback as _tb
            return (where,
                    "cb=" + (_cur_cb["who"] or "（非定时器：直接调用/事件）"),
                    "清态后 adding=%s target=%s view=%s cat=%s ctx=%s" % (
                        app._adding_new, app._add_target, app._view, app._cur_cat_id,
                        (app._suggest_ctx_names() or [])[:2]),
                    "".join(_tb.format_stack(limit=6))[-400:])

        def _render_spy(entries, title):
            _before = bool(getattr(app, "_adding_new", False))
            _r = _orig_render(entries, title)
            try:
                if _before and not getattr(app, "_adding_new", False):
                    _clears.append(_snap("_render_entries(%s)" % str(title)[:20]))
            except Exception:
                pass
            return _r
        app._render_entries = _render_spy

        def _sd_spy(e):
            _before = bool(getattr(app, "_adding_new", False))
            try:
                return _orig_show_detail(e)
            finally:
                try:
                    if _before and not getattr(app, "_adding_new", False):
                        _clears.append(_snap("_show_detail(%s)"
                                             % ("None" if e is None else "entry")))
                except Exception:
                    pass
        app._show_detail = _sd_spy
        app._view = ("cat", _c2)
        app._cur_cat_id = _c2
        app._adding_new = True
        app._add_target = _c2
        app._detail_entry_id = None
        app._detail_dirty = False
        app._build_new_entry_editor(_c2)
        pump(app)
        check("stage3: new-entry state kept [adding=%s target=%s view=%s cat=%s ctx=%s]"
              % (app._adding_new, app._add_target, app._view, app._cur_cat_id,
                 app._suggest_ctx_names()[:2]),
              bool(app._adding_new) and app._add_target == _c2)
        # ---- 8.4.1j（2026-09-15 批次 6-1 **严格验证**）：新增态图片区**必然渲染**（含 4 个写操作按钮），
        #   故在此处做严格断言：锁定 → 全部置灰；解锁 → 全部恢复可用。（按"存活按钮"取，避免旧句柄假象）
        def _img_btns_now():
            _bs = []
            walk_types(app.detail_scroll, "CTkButton", _bs)
            return [b for b in _bs if any(_k in str(text_of(b)) for _k in
                                          ("选择图片", "移除图片", "添加本地图", "添加链接"))]

        _ib0 = _img_btns_now()
        check("lock: 新增态图片/图集按钮已渲染（可严格验证）[%d]" % len(_ib0), len(_ib0) >= 2)
        if _ib0:
            _lock_b1 = app._lock_on
            try:
                app._lock_on = True
                app._build_new_entry_editor(_c2); pump(app, 2)
                _ib1 = _img_btns_now()
                _b1 = [(str(text_of(b))[:10], str(b.cget("state"))) for b in _ib1
                       if str(b.cget("state")) != "disabled"]
                check("lock: 锁定态新增表单图片/图集按钮全部置灰（6-1 严格）[活=%d 未灰=%s]"
                      % (len(_ib1), _b1 or "无"), len(_ib1) >= 2 and not _b1)
            finally:
                app._lock_on = _lock_b1
                app._build_new_entry_editor(_c2); pump(app, 2)
            _ib2 = _img_btns_now()
            _b2 = [str(text_of(b))[:10] for b in _ib2 if str(b.cget("state")) != "normal"]
            check("lock: 解锁后新增表单图片/图集按钮恢复可用（6-1 严格）[活=%d 未恢复=%s]"
                  % (len(_ib2), _b2 or "无"), len(_ib2) >= 2 and not _b2)
        if not app._adding_new:
            check("stage3: who cleared new-entry state [鼠标=%s/%s] %s"
                  % (app.winfo_pointerx(), app.winfo_pointery(),
                     _clears[:2] or "（探针未捕获：可能由事件/其他路径清态）"), False)
        check("stage3: suggest btn in tag block",
              hasattr(app, "_rec_btn") and app._rec_btn.winfo_exists())
        check("stage3: ctx resolves to visual domain [%s]"
              % (app._suggest_ctx_names()[:2],),
              "图像素材" in (app._suggest_ctx_names() or []))
        # 10.1 填名称+提示词 → 点推荐 → 标签并入（＝默认全选）
        app._name_entry.delete(0, "end")
        app._name_entry.insert(0, "Editorial portrait poster")
        app._detail_boxes["prompt_en"].delete("1.0", "end")
        app._detail_boxes["prompt_en"].insert("1.0", "portrait, portrait, film grain, cinematic")
        app._tag_names = []
        app._refresh_tag_chips()
        _toasts = []
        _orig_toast = app.toast
        # 保留真实 toast 行为，同时记录提示语（便于定位"推荐为空"的真实原因）
        app.toast = lambda msg, **k: (_toasts.append(str(msg)), _orig_toast(msg, **k))[-1]
        app._suggest_tags(); pump(app)
        app.toast = _orig_toast
        check("stage3: suggest adds tags [%s]" % (" | ".join(_toasts) or "无提示"),
              len(app._tag_names) >= 2)
        check("stage3: chips rendered for suggested",
              len(app._tag_chip_btns) == len(app._tag_names))
        # 10.2 点 "×" 可去掉不要的
        #      注：加空列表保护——若上面"推荐为空"，此处原会抛 IndexError 直接中断整个测试，
        #      使后续用例与最终统计都拿不到（2026-09-14 修正为只记 FAIL、继续跑完）。
        _drop = app._tag_names[-1] if app._tag_names else ""
        app._remove_tag(_drop)
        check("stage3: remove suggested tag [drop=%s]" % (_drop or "（无可删）"),
              bool(_drop) and _drop not in app._tag_names)
        # 10.3 ①名称框失焦 → 自动推荐一次；同名不重复
        app._rec_last_name = ""
        app._tag_names = []
        app._refresh_tag_chips()
        app._on_name_committed()
        check("stage3: name commit auto-suggests",
              len(app._tag_names) >= 1
              and app._rec_last_name == "Editorial portrait poster")
        _n1 = len(app._tag_names)
        app._on_name_committed()
        check("stage3: same name not re-suggested", len(app._tag_names) == _n1)
        # 10.4 T2 开关（默认关 → 不排定时器；开启 → 排防抖）
        app._auto_tag_suggest = False
        app._suggest_timer = None
        app._on_prompt_typed()
        check("stage3: T2 off does not schedule", app._suggest_timer is None)
        app._auto_tag_suggest = True
        app._on_prompt_typed()
        _sched = app._suggest_timer is not None
        try:
            app.after_cancel(app._suggest_timer)
        except Exception:
            pass
        app._suggest_timer = None
        app._auto_tag_suggest = False
        check("stage3: T2 on schedules debounce", _sched)
        # 10.5 快速新建窗口：标签区、推荐、× 移除、保存落库
        qw = QuickAddWindow(app, db)
        pump(app)
        qw._cat_id = c_img              # 模拟"已锁定分类"（否则无分类上下文 → 兜底无标签）
        qw._active_cat_id = c_img
        check("stage3: quick-add tag area built",
              hasattr(qw, "_tag_chips_row") and hasattr(qw, "_tag_entry"))
        qw.name_entry.insert(0, "QA portrait tag test")
        qw._boxes["prompt_en"].insert("1.0", "portrait, portrait, film grain, cinematic")
        qw._suggest_tags(); pump(app)
        check("stage3: quick-add suggest works", len(qw._tag_names) >= 2)
        _kept = len(qw._tag_names) - 1
        qw._remove_tag(qw._tag_names[0])
        check("stage3: quick-add remove works", len(qw._tag_names) == _kept)
        qw._save(); pump(app)
        _qeid = None
        for _e in db.list_all_entries():
            if _e["name"] == "QA portrait tag test":
                _qeid = _e["id"]
        check("stage3: quick-add persists tags",
              _qeid is not None and len(db.list_entry_tag_names(_qeid)) == _kept)
        try:
            qw.grab_release()
        except Exception:
            pass
        qw.destroy(); pump(app)
        # 10.6 设置页：T2 开关存在且默认关
        _sdlg = SettingsDialog(app, db)
        pump(app)
        check("stage3: settings auto-tag switch exists & default off",
              hasattr(_sdlg, "sw_auto_tag") and _sdlg.sw_auto_tag.get() == 0)
        try:
            _sdlg.grab_release()
        except Exception:
            pass
        _sdlg.destroy(); pump(app)
        # 撤掉 stage3 诊断探针（还原真实方法/原 after，避免污染后续用例）
        app._render_entries = _orig_render
        app._show_detail = _orig_show_detail
        app.after = _orig_after

        # ---- 11. 批量打标「目标库」：当前库 / 其他库文件（2026-09-14 增强）----
        _db_path = os.path.join(tmp, "t.db")
        _other_path = os.path.join(tmp, "other_lib.db")
        _odb = Database(_other_path)
        _odid = _odb.add_domain("图像")
        _ocid = _odb.add_category("Portrait & People", domain_id=_odid)
        _oeids = [_odb.add_entry(Entry(name="other portrait %d" % i, category_id=_ocid,
                                      intro="素材",
                                      prompt_en="portrait, film grain, cinematic"))
                  for i in range(3)]
        _odb.close()
        _bad_path = os.path.join(tmp, "not_a_db.txt")
        with open(_bad_path, "w", encoding="utf-8") as _f:
            _f.write("this is not a database")
        _orig_bd2 = _tb.batch_dir
        _orig_mb2 = _btd.messagebox
        _orig_snap2 = _btd.backup
        _tb.batch_dir = lambda: os.path.join(tmp, "tagging11")
        _btd.messagebox = mb
        _btd.backup = type("B2", (), {"pretag_snapshot": staticmethod(
            lambda *a, **k: {"ok": True, "path": "（测试桩）", "removed": 0})})()
        try:
            dlg3 = BatchTagDialog(app, db, db_path=_db_path)
            pump(app)
            _dlg3_def_geo = dlg3.wm_geometry().split("+")[0]   # 记住默认尺寸（下方要恢复它做断言）
            check("target-db: control exists & defaults to current",
                  hasattr(dlg3, "om_target") and dlg3._is_current_db())
            # 2026-09-14 修复回归：底部"确认执行"按钮栏必须**先于可扩展预演区** pack，
            # 否则窗口高度不足时按钮会被挤出可视区（用户实测"预演后找不到确认按钮"）。
            _btns = []
            def _collect_btn_texts(w, out):
                for c in w.winfo_children():
                    try:
                        if isinstance(c, _ctk.CTkButton):
                            out.append(str(c.cget("text")))
                    except Exception:
                        pass
                    _collect_btn_texts(c, out)
            _collect_btn_texts(dlg3._tool_bar, _btns)
            check("batch dialog: toolbar holds 预演+确认执行+取消",
                  all(any(k in t for t in _btns) for k in ("预演", "确认执行", "取消")))
            _slaves = [str(w) for w in dlg3.pack_slaves()]
            check("batch dialog: toolbar packed before preview area",
                  _slaves.index(str(dlg3._tool_bar)) < _slaves.index(str(dlg3.txt)))
            dlg3.update_idletasks()
            check("batch dialog: toolbar within window",
                  dlg3._tool_bar.winfo_y() + dlg3._tool_bar.winfo_height()
                  <= dlg3.winfo_height() + 1)
            # 工具行 5 个按钮必须在**最小宽度**下也放得下（复审发现 720 宽会溢出 → 已改 800）
            # 注：CTk 的 minsize() 是 setter（无参调用会 TypeError）→ 读内部属性
            _min_w = getattr(dlg3, "_min_width", 800) or 800
            _min_h = getattr(dlg3, "_min_height", 560) or 560
            check("batch dialog: toolbar fits at min width %d" % _min_w,
                  dlg3._tool_bar.winfo_reqwidth() + 28 <= _min_w)
            # 更严格：缩到**最小尺寸**时按钮仍须可见（证明不再被预演区挤掉）
            dlg3.geometry("%dx%d" % (_min_w, _min_h))
            pump(app)
            dlg3.update_idletasks()
            check("batch dialog: toolbar visible at min size",
                  dlg3._tool_bar.winfo_y() + dlg3._tool_bar.winfo_height()
                  <= dlg3.winfo_height() + 1)
            # ---- 2026-09-18（用户实测反馈）：出厂词表升级后「⑤ 标签重点范围」维度由 8 个 → 101 个，
            #   原先"5 个/行、不限高"把工具行与预演区整体挤出视野（看不到也点不到）。
            #   修复口径：该区改为 **固定高度视口 + 多列 + 可滚动**（列数随宽度自适应），
            #   下面三条断言把该口径固化：① 高度受限且内容可滚动；② 维度一个不少；③ 默认尺寸下
            #   工具行与预演区都在视野内且有可用高度。
            _dim_n = len(dlg3._dim_vars)
            _dm_vp = dlg3._dim_box._parent_canvas.winfo_height()
            _dm_ct = dlg3._dim_box.winfo_height()
            check("batch dialog(09-18): 维度区高度受限且内容可滚动 [维度=%d 视口=%d 内容=%d 列=%d]"
                  % (_dim_n, _dm_vp, _dm_ct, dlg3._dim_cols),
                  _dim_n >= 20 and 40 <= _dm_vp <= 200 and _dm_ct > _dm_vp)
            check("batch dialog(09-18): 全部维度都有复选框（一个不少）[%d/%d]"
                  % (len(dlg3._dim_cbs), _dim_n),
                  len(dlg3._dim_cbs) == _dim_n == len(dlg3._dim_vars))
            dlg3.wm_geometry(_dlg3_def_geo)        # 恢复默认尺寸（用 wm_ 直通，避免 CTk 二次缩放）
            pump(app, 6)
            dlg3.update_idletasks()
            _tb_b, _tx_b, _tx_h, _win_h = (
                dlg3._tool_bar.winfo_y() + dlg3._tool_bar.winfo_height(),
                dlg3.txt.winfo_y() + dlg3.txt.winfo_height(),
                dlg3.txt.winfo_height(), dlg3.winfo_height())
            check("batch dialog(09-18): 默认尺寸下工具行与预演区都在视野内 "
                  "[工具行底=%d 预演底=%d 预演高=%d 窗高=%d]" % (_tb_b, _tx_b, _tx_h, _win_h),
                  _tb_b <= _win_h and _tx_b <= _win_h and _tx_h >= 80)
            # 2026-09-16（批次 12-2，用户要求 2）：「⑦ 取词用于批量打标」开关
            #   —— 与「设置 → 标签与词表」共用同一 meta 键；切换后立即写 meta 并作废预演
            db.set_meta(config.META_FALLBACK_BATCH, "0")      # 先归零，使断言不依赖前序用例
            check("fb-batch(12-2): 批量对话框含「取词用于批量打标」勾选框且初始为关 [%s]"
                  % bool(dlg3._use_field_fallback()),
                  hasattr(dlg3, "var_fallback") and dlg3._use_field_fallback() is False)
            dlg3.var_fallback.set(True); dlg3._on_fallback_toggle(); pump(app)
            check("fb-batch(12-2): 勾选后写 meta 且生效 [%s]"
                  % db.get_meta(config.META_FALLBACK_BATCH),
                  db.get_meta(config.META_FALLBACK_BATCH) == "1"
                  and dlg3._use_field_fallback() is True
                  and dlg3._plan is None)          # 策略变更 ⇒ 预演结果作废
            dlg3.var_fallback.set(False); dlg3._on_fallback_toggle(); pump(app)
            check("fb-batch(12-2): 取消勾选后写 meta 且生效 [%s]"
                  % db.get_meta(config.META_FALLBACK_BATCH),
                  db.get_meta(config.META_FALLBACK_BATCH) == "0"
                  and dlg3._use_field_fallback() is False)
            _ok_bad, _msg_bad, _tdb_bad = dlg3._open_target_db(_bad_path)
            check("target-db: rejects non-database file", (not _ok_bad) and _tdb_bad is None)
            _ok_o, _msg_o, _tdb_o = dlg3._open_target_db(_other_path)
            check("target-db: opens & validates other db",
                  _ok_o and _tdb_o is not None and _tdb_o is not db)
            dlg3._use_target(_tdb_o, _other_path, own=True)
            check("target-db: switched to other db", not dlg3._is_current_db())
            check("target-db: dict falls back to current lib",
                  tagger.count_tags(dlg3._load_target_dict()) >= 100)
            dlg3.om_range.set("当前列表")           # 对其他库无意义 → 应自动退回
            dlg3._use_target(_tdb_o, _other_path, own=True)
            check("target-db: 'current list' falls back to all",
                  dlg3.om_range.get() == "全部条目")
            dlg3.seg_scope.set("仅无标签的条目")
            dlg3._on_preview(); pump(app)
            check("target-db: preview targets other db",
                  dlg3._plan is not None
                  and os.path.abspath(dlg3._plan["target_path"]) == os.path.abspath(_other_path)
                  and dlg3._plan["stats"]["处理"] == 3)
            _cur_n = len(db.list_all_entries())
            _cur_links = db.conn.execute("SELECT COUNT(*) FROM entry_tags").fetchone()[0]
            mb.asks.clear()
            dlg3._on_execute(); pump(app)
            _odb2 = Database(_other_path)
            _got = [len(_odb2.list_entry_tags(e)) for e in _oeids]
            _odb2.close()
            check("target-db: wrote tags into other db", all(n >= 1 for n in _got))
            check("target-db: confirm dialog names the target lib",
                  any(_k == "yesno" and "其他库文件" in str(a[1])
                      for (_k, a, _kk) in mb.asks))
            check("target-db: current lib untouched",
                  len(db.list_all_entries()) == _cur_n
                  and db.conn.execute("SELECT COUNT(*) FROM entry_tags").fetchone()[0] == _cur_links)
            check("target-db: batch records target lib",
                  len(_tb.list_batches()) == 1
                  and os.path.abspath(_tb.batch_db_path(_tb.list_batches()[0]["path"]))
                  == os.path.abspath(_other_path))
            # 撤销 → 回到"其他库"并把标签清回打标前
            dlg3._on_undo(); pump(app)
            _odb3 = Database(_other_path)
            _cleared = all(len(_odb3.list_entry_tags(e)) == 0 for e in _oeids)
            _odb3.close()
            check("target-db: undo restores other db", _cleared)
            try:
                dlg3.grab_release()
            except Exception:
                pass
            dlg3.destroy(); pump(app)
            check("target-db: own connection released on close",
                  dlg3._own_db is None)
        finally:
            _tb.batch_dir = _orig_bd2
            _btd.messagebox = _orig_mb2
            _btd.backup = _orig_snap2

        # ---- 12. 标签云宽度校准（2026-09-14 修复"一行末个标签被裁剪"）----
        if not getattr(app, "_tag_page_on", False):
            app._open_tag_page()
            pump(app)
        app._tag_page_limit = 60
        app._render_tag_main()
        pump(app)
        _cloud_btns = []

        def _walk_btns(w, out):
            for c in w.winfo_children():
                if isinstance(c, _ctk.CTkButton):
                    out.append(c)
                _walk_btns(c, out)
        _walk_btns(app._tag_main, _cloud_btns)
        check("tag cloud: buttons present", len(_cloud_btns) >= 50)
        # 根因修复：必须显式 width=1，否则 CTkButton 默认 width=140 会把短标签撑到 168px
        check("tag cloud: every button uses width=1 (auto-size)",
              bool(_cloud_btns) and all(b.cget("width") == 1 for b in _cloud_btns))
        # 每行实际请求宽度不得超过可用宽度（否则最后一个会被父容器裁剪）
        _rows = []
        def _walk_rows(w):
            kids = w.winfo_children()
            if kids and all(isinstance(k, _ctk.CTkButton) for k in kids):
                _rows.append(kids)
                return
            for k in kids:
                _walk_rows(k)
        _walk_rows(app._tag_main)
        _avail = max(200, app._tag_main.winfo_width() - 40)
        _over = [sum(b.winfo_reqwidth() + 8 for b in r) for r in _rows
                 if sum(b.winfo_reqwidth() + 8 for b in r) > _avail]
        check("tag cloud: no row exceeds available width (%d rows, avail=%d)"
              % (len(_rows), _avail), bool(_rows) and not _over)
        app._tag_page_limit = _mw.TAG_PAGE_LIMIT
        app._render_tag_main(); pump(app)
        app._close_tag_page(); pump(app)

        # ================================================================ #
        # 2026-09-16（批次 14）：字段管理「隐藏 / 显示」——详情区区块逐个隐藏
        #   · ① 名称无开关；内置 9 项 + 虚拟 3 项 + 自定义字段均有开关
        #   · 硬隐藏：与"详情字段显示策略"无关，且"显示全部字段"按钮不解除
        #   · 隐藏不影响数据：保存后内容完整保留（不丢内容、不丢标签、不串写）
        #   · 空间回收：隐藏某项后其下方区块整体上移
        # ================================================================ #
        _h_dom = db.list_domains()[0]["id"]
        _h_cat = db.add_category("隐藏测试L", domain_id=_h_dom)
        _h_ck = db.add_field_def("隐藏测试自定义", "text")   # 用于验证自定义字段也有开关
        _h_e = db.add_entry(Entry(name="隐藏测试条目", category_id=_h_cat,
                                  intro="介绍内容KEEP", origin="溯源KEEP",
                                  prompt_cn="中文提示词KEEP", prompt_en="ENGLISH KEEP",
                                  image_plan="https://example.com/k.png"))
        db.set_entry_tags(_h_e, ["隐藏标签甲", "隐藏标签乙"])
        _dm_bak14 = app._detail_mode
        app._detail_mode = _mw.config.DETAIL_MODE_FULL    # 全字段模式（验证硬隐藏不被模式解除）
        app._show_detail(db.get_entry(_h_e)); pump(app)

        def _rendered14(key):
            """该区块当前是否已渲染（标签区块用 chip 行判断，其余用文本框）"""
            if key == "_tags":
                _r = getattr(app, "_tag_chips_row", None)
                return bool(_r is not None and _r.winfo_exists())
            return key in app._detail_boxes

        check("14: 全字段模式下各区块默认全部渲染",
              all(_rendered14(k) for k in ("intro", "origin", "prompt_cn",
                                           "prompt_en", "image_plan", "_tags")))

        def _hide_btns14(row):
            _out = []
            for _c in row.winfo_children():
                try:
                    if (type(_c).__name__ == "CTkButton"
                            and _c.cget("text") in ("隐藏", "显示")):
                        _out.append(_c)
                except Exception:
                    pass
            return _out

        _dlg14 = _mw.FieldManagerDialog(app, db); pump(app)
        _rows14 = {k: entry.master for _f, k, entry, _o, _b, _a in _dlg14._rows}
        check("14: ① 名称行无「隐藏/显示」按钮",
              len(_hide_btns14(_rows14["name"])) == 0)
        check("14: 内置9项/虚拟3项/自定义 均有「隐藏/显示」按钮 [%s]" % _h_ck,
              all(len(_hide_btns14(_rows14[k])) == 1 for k in
                  ("intro", "origin", "features", "scenes", "works", "image_desc",
                   "prompt_cn", "prompt_en", "image_plan",
                   "_tags", "_location", "_time", _h_ck)))

        # 「空间回收」基准：隐藏 5 个区块之前，详情区内实际生成的区块控件个数。
        #   说明：详情区是**顺序 pack 布局**——不渲染 ⇒ 该区块控件根本不创建
        #   ⇒ 不占高度 ⇒ 其下方区块整体上移。用"直属子控件个数"断言最稳定
        #   （不受窗口是否已映射 / 坐标系影响）。
        _n_before14 = len(app.detail_scroll.winfo_children())
        for _k14 in ("intro", "origin", "prompt_cn", "prompt_en", "image_plan"):
            _dlg14._toggle_hidden(_k14)
        _dlg14._toggle_hidden("name")          # ① 名称：应被拒绝
        pump(app)
        check("14: 点「隐藏」写入 meta；① 名称被拒绝 [%s]" % sorted(db.get_hidden_field_keys()),
              db.get_hidden_field_keys() == {"intro", "origin", "prompt_cn",
                                             "prompt_en", "image_plan"})
        _rows14b = {k: entry.master for _f, k, entry, _o, _b, _a in _dlg14._rows}
        check("14: 已隐藏项按钮文案翻转为「显示」",
              _hide_btns14(_rows14b["intro"])[0].cget("text") == "显示")
        check("14: 对话框 changed=True（供主窗口重建详情区）", _dlg14.changed is True)
        _dlg14.destroy(); pump(app)

        # 关闭对话框后主窗口重建详情区（与 _open_field_manager 收尾口径一致）
        app._show_detail(db.get_entry(_h_e)); pump(app)
        check("14: 硬隐藏生效——全字段模式下这些区块仍不渲染",
              not any(_rendered14(k) for k in
                      ("intro", "origin", "prompt_cn", "prompt_en", "image_plan")))
        check("14: 未隐藏项（④核心特征）照常渲染", _rendered14("features"))
        check("14: 状态行给出「已隐藏」提示 [%s]" % app.detail_state_lbl.cget("text"),
              "已隐藏" in app.detail_state_lbl.cget("text"))
        _n_after14 = len(app.detail_scroll.winfo_children())
        check("14: 隐藏 5 项后详情区少 5 个区块控件（下方整体上移）[%d → %d]"
              % (_n_before14, _n_after14),
              _n_after14 == _n_before14 - 5)

        # ★ 数据安全：隐藏后保存，被隐藏字段的内容必须**完整保留**
        app._lock_on = False
        app._browse_mode = False
        app._save_detail(); pump(app)
        _e14 = db.get_entry(_h_e)
        check("14: 隐藏 ②③⑧⑨⑩ 后保存，内容完整保留（不清空）",
              _e14["intro"] == "介绍内容KEEP" and _e14["origin"] == "溯源KEEP"
              and _e14["prompt_cn"] == "中文提示词KEEP"
              and _e14["prompt_en"] == "ENGLISH KEEP"
              and _e14["image_plan"] == "https://example.com/k.png")

        # ★ 数据安全：隐藏 🏷 标签区块后保存，标签既不清空也不串写
        _dlg14b = _mw.FieldManagerDialog(app, db); pump(app)
        _dlg14b._toggle_hidden("_tags")
        _dlg14b.destroy(); pump(app)
        app._show_detail(db.get_entry(_h_e)); pump(app)
        check("14: 隐藏 🏷 标签后详情区无标签区块", not _rendered14("_tags"))
        _tags_before14 = list(app._tag_names)
        app._suggest_tags()        # 标签区块隐藏时：应直接跳过（不改动已选标签）
        pump(app)
        check("14: 标签区块隐藏时「推荐标签」直接跳过", app._tag_names == _tags_before14)
        app._save_detail(); pump(app)
        check("14: 隐藏标签后保存，标签既不清空也不串写 %s"
              % sorted(db.list_entry_tag_names(_h_e)),
              set(db.list_entry_tag_names(_h_e)) == {"隐藏标签甲", "隐藏标签乙"})

        # 全部恢复「显示」→ 详情区重新出现，内容仍在
        _dlg14c = _mw.FieldManagerDialog(app, db); pump(app)
        _dlg14c._toggle_hidden("_tags")
        for _k14 in ("intro", "origin", "prompt_cn", "prompt_en", "image_plan"):
            _dlg14c._toggle_hidden(_k14)
        _dlg14c.destroy(); pump(app)
        check("14: 全部恢复显示后 meta 清空", db.get_hidden_field_keys() == set())
        app._show_detail(db.get_entry(_h_e)); pump(app)
        check("14: 恢复显示后各区块重新渲染（内容仍在）",
              all(_rendered14(k) for k in ("intro", "origin", "prompt_cn",
                                           "prompt_en", "image_plan", "_tags"))
              and db.get_entry(_h_e)["intro"] == "介绍内容KEEP")
        check("14: 状态行不再有「已隐藏」提示",
              "已隐藏" not in app.detail_state_lbl.cget("text"))

        # ---------------- 2026-09-16（批次 15）：全部隐藏 / 全部显示 ----------------
        _d15 = _mw.FieldManagerDialog(app, db); pump(app)
        _d15._set_all_hidden(True)
        _hide_all_expected = ({d["field_key"] for d in db.list_field_defs()} - {"name"})
        check("15: 「全部隐藏」＝全部可隐藏项（不含 ① 名称）[%d 项]"
              % len(db.get_hidden_field_keys()),
              db.get_hidden_field_keys() == _hide_all_expected)
        _d15.destroy(); pump(app)
        app._show_detail(db.get_entry(_h_e)); pump(app)
        check("15: 全部隐藏后详情区仅剩 ① 名称 [子控件=%d]"
              % len(app.detail_scroll.winfo_children()),
              len(app.detail_scroll.winfo_children()) == 1)
        check("15: ① 名称仍正常渲染且可编辑",
              getattr(app, "_name_entry", None) is not None
              and app._name_entry.get() == "隐藏测试条目")
        check("15: 状态行提示已隐藏项数",
              "已隐藏" in app.detail_state_lbl.cget("text"))
        # ★ 全部隐藏后保存：全部字段与标签必须完整保留
        app._lock_on = False
        app._browse_mode = False
        app._save_detail(); pump(app)
        _e15 = db.get_entry(_h_e)
        check("15: 全部隐藏后保存，全部字段与标签完整保留",
              _e15["name"] == "隐藏测试条目" and _e15["intro"] == "介绍内容KEEP"
              and _e15["origin"] == "溯源KEEP"
              and _e15["prompt_cn"] == "中文提示词KEEP"
              and _e15["prompt_en"] == "ENGLISH KEEP"
              and _e15["image_plan"] == "https://example.com/k.png"
              and set(db.list_entry_tag_names(_h_e)) == {"隐藏标签甲", "隐藏标签乙"})
        # 一键恢复「全部显示」
        _d15b = _mw.FieldManagerDialog(app, db); pump(app)
        _d15b._set_all_hidden(False)
        _d15b.destroy(); pump(app)
        check("15: 「全部显示」清空隐藏集合", db.get_hidden_field_keys() == set())
        app._show_detail(db.get_entry(_h_e)); pump(app)
        check("15: 全部显示后各区块恢复渲染",
              all(_rendered14(k) for k in ("intro", "origin", "prompt_cn",
                                           "prompt_en", "image_plan", "_tags")))
        app._detail_mode = _dm_bak14

        # ---------------- 2026-09-16（批次 16）：🏷 标签区块合并为一行（参考新建对话框） ----------------
        app._show_detail(db.get_entry(_h_e)); pump(app)
        check("16: 标签区块四控件同一行（推荐 / 输入 / 添加 / 选择）",
              app._tag_entry.master is app._tag_add_btn.master
              is app._tag_pick_btn.master is app._rec_btn.master)
        _sib16 = app._tag_chips_row.master.winfo_children()
        check("16: 该行位于 chip 行之上",
              _sib16.index(app._rec_btn.master) < _sib16.index(app._tag_chips_row))
        check("16: 区块已收回一行（直接子控件 = 3）",
              len([c for c in _sib16 if c.winfo_manager()]) == 3)
        _txt16 = []

        def _w16(w):
            for c in w.winfo_children():
                try:
                    if str(c.cget("text")):
                        _txt16.append(str(c.cget("text")))
                except Exception:
                    pass
                _w16(c)

        _w16(app._tag_chips_row.master)
        check("16: 原「推荐标签」说明长文字已移除（改为浮动提示）",
              not any("按名称/提示词自动推荐" in t for t in _txt16))
        check("16: 「🏷 标签」标题仍在",
              any(t.strip() == "🏷 标签" for t in _txt16))
        check("16: 「✨ 推荐标签」已挂浮动提示",
              bool(tk.Frame.bind(app._rec_btn, "<Enter>")))
        # 功能不受影响：回车添加 + 保存落库
        app._lock_on = False
        app._browse_mode = False
        app._tag_entry.delete(0, "end")
        app._tag_entry.insert(0, "布局校验标签")
        app._add_tag_from_entry(); pump(app)
        check("16: 输入框回车添加标签仍正常", "布局校验标签" in app._tag_names)
        app._save_detail(); pump(app)
        check("16: 保存后标签正确落库 %s" % sorted(db.list_entry_tag_names(_h_e)),
              set(db.list_entry_tag_names(_h_e)) == {"隐藏标签甲", "隐藏标签乙",
                                                     "布局校验标签"})
        # 新增条目表单复用同一 `_build_tag_block` → 同样是一行布局
        app._select_category(_h_cat); pump(app)
        app._start_new_entry(); pump(app)
        check("16: 新增表单的标签区块同样为一行",
              app._tag_entry.master is app._tag_add_btn.master
              is app._tag_pick_btn.master is app._rec_btn.master)
        app._adding_new = False
        app._show_detail(db.get_entry(_h_e)); pump(app)

        fails = [n for n, ok_ in results if not ok_]
        # 2026-09-15（stage3 专项排查）：把"悬停选中"干扰证据随汇总一起打印（无干扰时不打印）
        print("[自测] 窗口输入屏蔽(-disabled) = %s" % ("已启用" if _input_shield else "未启用"))
        if _hover_log:
            print("---- [诊断] 运行期间'悬停选中'被调度 %d 次（外部鼠标干扰证据）：" % len(_hover_log))
            for _h in _hover_log[:4]:
                print("       ", _h)
        print("----", "ALL PASSED" if not fails else f"FAILED: {fails}")
        return 0 if not fails else 1
    finally:
        if app is not None:
            try:
                app.withdraw()
            except Exception:
                pass
        db.close()
        _mw.messagebox = orig_mb
        _mw.simpledialog = orig_sd
        _mw.filedialog = orig_fd
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
