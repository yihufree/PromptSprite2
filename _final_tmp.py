# -*- coding: utf-8 -*-
"""审核后修订整合回归（P0-2/P1/P2，临时脚本，测试后删除）"""
import json
import os
import shutil
import tempfile

from app.database import Database
from app.models import Entry
from app.parser import json_io
from app.incremental_backup import (collect_daily_changes, build_json_v4,
                                    write_incremental, incr_dir, _change_pack_basename)

tmp = tempfile.mkdtemp(prefix="ps_final_")
ok = True


def check(name, cond):
    global ok
    print(("PASS" if cond else "FAIL"), "-", name)
    ok = ok and bool(cond)


try:
    # ---------- A. V1.7.0 变更包命名/幂等/同日单文件 ----------
    d = Database(os.path.join(tmp, "a.db"))
    l1 = d.add_category("类A")
    l2 = d.add_category("类B", parent_id=l1)
    d.add_entry(Entry(name="x1", intro="a", category_id=l2))
    r = write_incremental(d)
    check("A1 文件名 add_N 下划线命名",
          r["ok"] and "_add_1_del_0.json" in os.path.basename(r["path"]))
    d.add_entry(Entry(name="x2", intro="b", category_id=l2))
    r2 = write_incremental(d)
    cur = [f for f in os.listdir(incr_dir(d)) if f.startswith("变更包_")]
    check("A2 当日仅一个文件", len(cur) == 1 and os.path.basename(r2["path"]) in cur)
    pk = json.load(open(r2["path"], encoding="utf-8"))
    check("A3 v4 summary 存在且 add=2",
          pk.get("version") == 4 and pk.get("type") == "change"
          and pk["summary"]["add_entries"] == 2)
    d.close()

    # ---------- B. 删除同步：范围收敛 + 接收端不写删除日志（P1-3/P2-13） ----------
    s = Database(os.path.join(tmp, "b_src.db"))
    cA = s.add_category("A类"); cA2 = s.add_category("A子", parent_id=cA)
    cB = s.add_category("B类"); cB2 = s.add_category("B子", parent_id=cB)
    ea = s.add_entry(Entry(name="内容", intro="same", category_id=cA2))
    eb = s.add_entry(Entry(name="内容", intro="same", category_id=cB2))
    s.trash_entry(ea, reason="x")
    s.conn.execute("DELETE FROM trash")
    s.conn.commit()
    st = collect_daily_changes(s, "2000-01-01 00:00:00")
    pack = build_json_v4(s, st, "T", "2026-09-05")
    ckey = Database.content_key({"name": "内容", "intro": "same"})
    pack["deleted_entries"] = [{"name": "内容", "chain": ["A类", "A子"],
                                "content_key": ckey}]
    p_b = os.path.join(tmp, "b.json")
    json.dump(pack, open(p_b, "w", encoding="utf-8"), ensure_ascii=False)

    t = Database(os.path.join(tmp, "b_tgt.db"))
    tA = t.add_category("A类"); tA2 = t.add_category("A子", parent_id=tA)
    tB = t.add_category("B类"); tB2 = t.add_category("B子", parent_id=tB)
    eA = t.add_entry(Entry(name="内容", intro="same", category_id=tA2))
    eB = t.add_entry(Entry(name="内容", intro="same", category_id=tB2))
    res = json_io.import_json(t, p_b, deletion_mode="apply")
    check("B1 按 chain 收敛只删 A 链", res["deleted"]["entries"] == 1
          and t.get_entry(eA) is None and t.get_entry(eB) is not None)
    # 接收端 trash 里有快照但未写本机删除日志（P2-13 回声）
    nlog = t.conn.execute(
        "SELECT COUNT(*) FROM deletion_log WHERE kind='entry' AND content_key=?", (ckey,)
    ).fetchone()[0]
    check("B2 接收端不写删除日志(无回声)", t.count_trash() == 1 and nlog == 0)
    s.close(); t.close()

    # ---------- C. 回收站恢复后删除项被过滤（P0-2） ----------
    r = Database(os.path.join(tmp, "c.db"))
    lc = r.add_category("C类")
    eid = r.add_entry(Entry(name="恢复条目", intro="secret", category_id=lc))
    r.trash_entry(eid, reason="测试")
    tid = r.list_trash()[0]["id"]
    new_id = r.restore_from_trash(tid)  # 恢复：内容仍存在（返回新 id）
    stc = collect_daily_changes(r, "2000-01-01 00:00:00")
    check("C1 恢复后删除项不再广播", len(stc["deleted_entries"]) == 0)
    # P1-7：再次删除并彻底清理后 payload 被清空
    r.trash_entry(new_id, reason="测试2")
    tid2 = r.list_trash()[0]["id"]
    r.purge_trash(tid2)
    row = r.conn.execute("SELECT payload FROM deletion_log "
                         "WHERE kind='entry' AND name='恢复条目'").fetchone()
    check("C2 彻底删除后 payload 清空", row is not None and row["payload"] == "")
    r.close()

    # ---------- D. 逆向恢复 + 根目录归属（P1-6） ----------
    s2 = Database(os.path.join(tmp, "d_src.db"))
    dom = s2.add_domain("根R")
    d1 = s2.add_category("恢复链", domain_id=dom)
    d2 = s2.add_category("恢复子", parent_id=d1)
    e1 = s2.add_entry(Entry(name="回", intro="c", category_id=d2))
    full = os.path.join(tmp, "d_full.json")
    json_io.export_json(s2, full, computer_code="T", day="2026-09-01")
    s2.delete_entry(e1)
    st2 = collect_daily_changes(s2, "2000-01-01 00:00:00")
    pack2 = build_json_v4(s2, st2, "T", "2026-09-05", include_snapshot=True)
    p_d = os.path.join(tmp, "d.json")
    json.dump(pack2, open(p_d, "w", encoding="utf-8"), ensure_ascii=False)
    t2 = Database(os.path.join(tmp, "d_tgt.db"))
    rv = json_io.import_json(t2, p_d, deletion_mode="reverse")
    cid_l1 = t2.find_category_by_chain(["恢复链"])
    links = t2.linked_domains(cid_l1) if cid_l1 else []
    check("D1 逆向恢复+根目录归属", rv["recovered"] == 1 and bool(links)
          and links[0]["name"] == "根R")
    s2.close(); t2.close()

    # ---------- E. Excel 空行推进进度（P2-16） ----------
    try:
        from openpyxl import Workbook
    except Exception:
        check("E1 Excel 空行进度", False)
    else:
        xp = os.path.join(tmp, "blanks.xlsx")
        wb = Workbook(); ws = wb.active
        ws.append(["领域", "一级分类", "二级分类", "名称", "介绍", "溯源", "核心特征",
                   "应用场景", "代表作", "配图描述", "提示词中文", "提示词英文",
                   "图像获取方案", "收藏"])
        ws.append(["", "", "", "", "", "", "", "", "", "", "", "", "", ""])  # 空行
        ws.append(["", "", "", "真条目", "hi", "", "", "", "", "", "", "", "", "0"])
        wb.save(xp)
        from app.parser import excel_io
        calls = []
        te = Database(os.path.join(tmp, "e.db"))
        try:
            excel_io.import_excel(te, xp, progress_cb=lambda d, t, n: calls.append(d))
            check("E1 空行推进进度到总量",
                  calls and calls[-1] == len(calls) and calls[-1] == 2)
        finally:
            te.close()

    # ---------- F. 数据比对同名分类不互相覆盖（P2-15） ----------
    fa = Database(os.path.join(tmp, "f_a.db")); fb = Database(os.path.join(tmp, "f_b.db"))
    fa.add_category("同名"); fa.add_category("同名2", parent_id=fa.add_category("父"))
    fb.add_category("同名"); fb.add_category("同名3", parent_id=fb.add_category("父"))
    from app.ui.compare_dialog import compare_databases
    diff = compare_databases(fa, fb)
    check("F1 比对按 id 计数不丢同名",
          diff["cat_del"] == 1 and diff["cat_add"] == 1)
    fa.close(); fb.close()

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("RESULT:", "ALL_OK" if ok else "HAS_FAILURES")
