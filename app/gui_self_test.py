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

from app.database import Database
from app.models import Entry
from app.ui import main_window as _mw
from app.ui.main_window import MainWindow
from app.ui.move_selector import MoveSelector


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
    orig_mb, orig_sd = _mw.messagebox, _mw.simpledialog
    _mw.messagebox = mb
    _mw.simpledialog = sd

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

        # ---- 1. 新增主按钮 + 浏览/新增放开 ----
        app._select_domain(did); pump(app)
        app._select_category(l1); pump(app)          # 带子级一级
        check("view cat at L1-with-children", app._view == ("cat", l1))
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
        # ②~⑦ 折叠组默认折叠（标题条内有自己的"展开"按钮）
        check("②~⑦ 折叠组默认折叠",
              app._detail_group_open is False
              and app._detail_group_toggle is not None
              and app._detail_group_toggle.cget("text") == "展开")

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

        fails = [n for n, ok_ in results if not ok_]
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
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
