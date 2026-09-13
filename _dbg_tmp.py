# -*- coding: utf-8 -*-
import os
import shutil
import tempfile

from app.database import Database
from app.models import Entry
from app.parser import json_io
from app.incremental_backup import collect_daily_changes, build_json_v4

tmp = tempfile.mkdtemp(prefix="dbg_")
try:
    s = Database(os.path.join(tmp, "s.db"))
    ca = s.add_category("A类"); suba = s.add_category("A子", parent_id=ca)
    cb = s.add_category("B类"); subb = s.add_category("B子", parent_id=cb)
    ea = s.add_entry(Entry(name="同内容条目", intro="same", category_id=suba))
    s.add_entry(Entry(name="同内容条目", intro="same", category_id=subb))
    s.trash_entry(ea, reason="x")
    s.conn.execute("DELETE FROM trash"); s.conn.commit()
    st = collect_daily_changes(s, "2000-01-01 00:00:00")
    print("deleted_entries:", st["deleted_entries"])
    pack = build_json_v4(s, st, "T", "2026-09-05", include_snapshot=True)
    p = os.path.join(tmp, "p.json")
    import json
    json.dump(pack, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    t = Database(os.path.join(tmp, "t.db"))
    ca2 = t.add_category("A类"); suba2 = t.add_category("A子", parent_id=ca2)
    cb2 = t.add_category("B类"); subb2 = t.add_category("B子", parent_id=cb2)
    eA = t.add_entry(Entry(name="同内容条目", intro="same", category_id=suba2))
    eB = t.add_entry(Entry(name="同内容条目", intro="same", category_id=subb2))
    print("cid found A子:", t.find_category_by_chain(["A类", "A子"]))
    r = json_io.import_json(t, p, deletion_mode="apply")
    print("result:", r)
    print("remaining:", [(x["name"], x["id"], x["category_id"]) for x in t.list_all_entries()])
    print("eA exists?", t.get_entry(eA) is not None, "eB exists?", t.get_entry(eB) is not None)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
