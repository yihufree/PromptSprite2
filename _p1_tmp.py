# -*- coding: utf-8 -*-
"""P1-3/P1-6/P1-7 回归（临时脚本，测试后删除）"""
import json
import os
import shutil
import tempfile

from app.database import Database
from app.models import Entry
from app.parser import json_io
from app.incremental_backup import collect_daily_changes, build_json_v4

tmp = tempfile.mkdtemp(prefix="ps_p1_")
ok = True


def check(name, cond):
    global ok
    print(("PASS" if cond else "FAIL"), "-", name)
    ok = ok and bool(cond)


try:
    # ===== P1-3：删除同步按 chain 收敛（同内容两份在 A、B 分类，只删链所指那份） =====
    s = Database(os.path.join(tmp, "p13_src.db"))
    ca = s.add_category("A类")
    suba = s.add_category("A子", parent_id=ca)
    cb = s.add_category("B类")
    subb = s.add_category("B子", parent_id=cb)
    ea = s.add_entry(Entry(name="同内容条目", intro="same", category_id=suba))
    eb = s.add_entry(Entry(name="同内容条目", intro="same", category_id=subb))
    # 源机仅删 A 链下的那份（日志链=A类/A子）
    s.trash_entry(ea, reason="x")
    s.conn.execute("DELETE FROM trash")  # 保留 deletion_log
    s.conn.commit()
    st = collect_daily_changes(s, "2000-01-01 00:00:00")
    pack = build_json_v4(s, st, "T", "2026-09-05", include_snapshot=True)
    # 说明：因源库 B 链仍存在同内容副本，collect 的 P0-2 过滤会剔除该删除项；
    # 为专门验证 P1-3"按 chain 收敛"，这里手工构造一条带 chain 的删除清单（等同删除日志未过滤场景）。
    ckey = Database.content_key({"name": "同内容条目", "intro": "same"})
    pack["deleted_entries"] = [{"name": "同内容条目", "chain": ["A类", "A子"],
                                "content_key": ckey}]
    p = os.path.join(tmp, "p13.json")
    json.dump(pack, open(p, "w", encoding="utf-8"), ensure_ascii=False)

    t = Database(os.path.join(tmp, "p13_tgt.db"))
    ca2 = t.add_category("A类"); suba2 = t.add_category("A子", parent_id=ca2)
    cb2 = t.add_category("B类"); subb2 = t.add_category("B子", parent_id=cb2)
    eA = t.add_entry(Entry(name="同内容条目", intro="same", category_id=suba2))
    eB = t.add_entry(Entry(name="同内容条目", intro="same", category_id=subb2))
    r = json_io.import_json(t, p, deletion_mode="apply")
    names = {(x["name"], x["category_id"]) for x in t.list_all_entries()}
    check("P1-3 只删 A 链一份(B 保留)", r["deleted"]["entries"] == 1
          and t.get_entry(eB) is not None and t.get_entry(eA) is None)
    t.close(); s.close()

    # ===== P1-6：逆向恢复把新一级分类链回根目录 =====
    src2 = Database(os.path.join(tmp, "p16_src.db"))
    dom = src2.add_domain("根R") if not src2.list_domains() else src2.list_domains()[0]["id"]
    # 建链并在删除前记录分类
    l1 = src2.add_category("恢复链", domain_id=dom)
    l2 = src2.add_category("恢复子", parent_id=l1)
    eid = src2.add_entry(Entry(name="要恢复", intro="c", category_id=l2))
    # 导出删除前全量（含 domain_links）
    full2 = os.path.join(tmp, "full16.json")
    json_io.export_json(src2, full2, computer_code="T", day="2026-09-01")
    src2.delete_entry(eid)
    st2 = collect_daily_changes(src2, "2000-01-01 00:00:00")
    pack2 = build_json_v4(src2, st2, "T", "2026-09-05", include_snapshot=True)
    p2 = os.path.join(tmp, "p16.json")
    json.dump(pack2, open(p2, "w", encoding="utf-8"), ensure_ascii=False)
    # 全新空库走 ⑤ 逆向恢复（无历史分类）
    t2 = Database(os.path.join(tmp, "p16_tgt.db"))
    # 逆向恢复需要包内分类先存在才“复用”，否则重建+根目录链接依赖 domain_links（export 含 dom）
    rv = json_io.import_json(t2, p2, deletion_mode="reverse")
    check("P1-6 逆向恢复 1 条", rv["recovered"] == 1)
    l1c = t2.find_category_by_chain(["恢复链"])
    linked = t2.linked_domains(l1c) if l1c else []
    check("P1-6 新建一级分类已关联根目录", bool(linked) and linked[0]["name"] == "根R")
    t2.close(); src2.close()

    # ===== P1-7：彻底删除/清空回收站清除 deletion_log.payload =====
    s3 = Database(os.path.join(tmp, "p17.db"))
    l3 = s3.add_category("X")
    e3 = s3.add_entry(Entry(name="隐私条目", intro="secret", category_id=l3))
    s3.trash_entry(e3, reason="测试")  # 入回收站并写日志 payload
    row = s3.conn.execute(
        "SELECT payload FROM deletion_log WHERE kind='entry' AND name='隐私条目'"
    ).fetchone()
    check("P1-7 删除日志含 payload", bool(row and row["payload"]))
    tid = s3.list_trash()[0]["id"]
    s3.purge_trash(tid)
    row2 = s3.conn.execute(
        "SELECT payload FROM deletion_log WHERE kind='entry' AND name='隐私条目'"
    ).fetchone()
    check("P1-7 彻底删除后 payload 已清空", row2 is not None and row2["payload"] == "")
    # 清空回收站路径
    s3.trash_entry(s3.add_entry(Entry(name="B2", intro="b", category_id=l3)), reason="t")
    s3.clear_trash()
    row3 = s3.conn.execute(
        "SELECT payload FROM deletion_log WHERE kind='entry' AND name='B2'").fetchone()
    check("P1-7 清空回收站后 payload 已清空", row3 is not None and row3["payload"] == "")
    s3.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("RESULT:", "ALL_OK" if ok else "HAS_FAILURES")
