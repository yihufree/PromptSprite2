# -*- coding: utf-8 -*-
"""P2-14 数据卫生（临时脚本，仅清理开发主库中的测试残留，执行后删除）"""
import os
import sqlite3
from datetime import datetime, timedelta

path = os.path.join(os.getcwd(), "data", "prompts.db")
print("db:", path, "exists=", os.path.exists(path))
if not os.path.exists(path):
    raise SystemExit("no main db")
conn = sqlite3.connect(path)
conn.row_factory = sqlite3.Row


def counts():
    return {
        "domains": conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0],
        "entries": conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
        "deletion_log": conn.execute("SELECT COUNT(*) FROM deletion_log").fetchone()[0],
    }


print("before:", counts())
# 1) 测试残留根目录 gggg（解除关联并删除该根目录行；共享分类数据保留）
row = conn.execute("SELECT id, name FROM domains WHERE name = 'gggg'").fetchone()
if row:
    did = row["id"]
    conn.execute("DELETE FROM domain_category WHERE domain_id = ?", (did,))
    conn.execute("DELETE FROM domains WHERE id = ?", (did,))
    print("removed domain gggg id=", did)
else:
    print("no gggg domain found")
# 2) 删除日志按保留窗口（近 7 天）清理
cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
cur = conn.execute("DELETE FROM deletion_log WHERE deleted_at < ?", (cutoff,))
print("pruned deletion_log rows:", cur.rowcount)
conn.commit()
print("after:", counts())
conn.close()
