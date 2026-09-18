# -*- coding: utf-8 -*-
"""tag_cleanup.py —— 存量"垃圾标签"的扫描与清理（数据层，2026-09-17 新增）

**背景**（2026-09-17 用户反馈"自动打标后出现大量阿拉伯数字标签 / 标签带标点"）：
打标引擎的"显式标签"通道原先没有质量闸门，导致三类噪声进了 `tags` / `entry_tags`：
    ① 纯数字 / 序号（`12`、`234`、`1145`）
    ② 十六进制色值（`000000`、`1A2332`，多来自提示词里的 `#000000` 颜色码）
    ③ 内部含标点的碎片（`古风/汉服`、`K-pop偶像`）

**引擎侧已修好**（`tagger_engine.is_noise_tag()`，见 `extract_explicit_tags` / `_clean_token`），
今后不再产生；本模块负责**清理历史遗留**，并被两个调用方共用：
    · CLI：仓库根目录 `clean_junk_tags.py`（`--dry-run/--apply/--check`）
    · UI：`app/ui/tag_cleanup_dialog.py`（设置 → 标签与词表 →「🧹 清理垃圾标签…」）

**口径唯一**：判定一律走 `tagger_engine.clean_tag_name()`，两处调用方不各写一份。
分两类处理：
    A 真噪声   —— `clean_tag_name(名) == ""` ⇒ **删除**标签及其关联；
    B 可洗净   —— 清洗结果非空且与原值不同（`[世界]` → `世界`）⇒ **重命名**；
                  若目标名已存在则**合并关联**（条目不会丢标签）。

**只改** `tags` / `entry_tags` 两表，**不动** `entries` 等任何条目内容。
"""
import os
import shutil
from datetime import datetime

from . import config
from .tagger_engine import TAG_MAX_LEN, clean_tag_name

# 清理前备份文件前缀（独立前缀：不参与任何自动清理，可长期留档手工回滚）
BACKUP_PREFIX = "prompts_precleanjunk"


# ---------------------------------------------------------------------- #
# 一、扫描（只读）
# ---------------------------------------------------------------------- #
def _reason(name: str) -> str:
    """给出被判为噪声的**原因**（仅用于展示；判定本身由 clean_tag_name 负责）。"""
    s = str(name or "")
    if not s.strip():
        return "空名"
    if len(s) > TAG_MAX_LEN:
        return "超长"
    if s.isdigit():
        return "纯数字"
    if len(s) in (3, 4, 6, 8) and any(c.isdigit() for c in s) \
            and all(c in "0123456789abcdefABCDEF" for c in s):
        return "十六进制色值"
    return "含标点/符号"


def scan(conn) -> dict:
    """扫描标签表，返回 {"total","links","drop":[...],"rename":[...]}（只读，不写库）。

    drop 项：{id, name, ns, n, reason}；rename 项：{id, name, ns, n, to}。
    均按"关联条目数"降序，便于优先处理影响面大的项。
    """
    total = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    links = conn.execute("SELECT COUNT(*) FROM entry_tags").fetchone()[0]
    rows = conn.execute(
        "SELECT t.id, t.name, t.namespace, COUNT(et.entry_id) AS n"
        " FROM tags t LEFT JOIN entry_tags et ON et.tag_id = t.id"
        " GROUP BY t.id, t.name, t.namespace").fetchall()
    drop, rename = [], []
    for tid, name, ns, n in rows:
        _to = clean_tag_name(name)
        if not _to:
            drop.append({"id": int(tid), "name": name, "ns": ns or "__global__",
                         "n": int(n or 0), "reason": _reason(name)})
        elif _to != name:
            rename.append({"id": int(tid), "name": name, "ns": ns or "__global__",
                           "n": int(n or 0), "to": _to})
    drop.sort(key=lambda r: (-r["n"], r["name"]))
    rename.sort(key=lambda r: (-r["n"], r["name"]))
    return {"total": int(total), "links": int(links), "drop": drop, "rename": rename}


# ---------------------------------------------------------------------- #
# 二、执行（写库；调用方负责先备份）
# ---------------------------------------------------------------------- #
def _find_tag_id(conn, ns: str, name: str):
    row = conn.execute("SELECT id FROM tags WHERE namespace = ? AND name = ?",
                       (ns, name)).fetchone()
    return int(row[0]) if row else None


def apply(conn) -> dict:
    """按 `scan()` 的结果执行清理（**不备份**，备份由调用方在写库前自行完成）。

    返回 {"drop_tags","drop_links","renamed","merged","moved"} 统计。
    """
    data = scan(conn)
    merged = renamed = moved = 0
    # ① B 类先做（重命名/合并），避免与后面的删除相互影响
    for r in data["rename"]:
        tid = _find_tag_id(conn, r["ns"], r["to"])
        if tid is not None and tid != r["id"]:
            moved += conn.execute(                       # 目标已存在 ⇒ 合并关联
                "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at)"
                " SELECT entry_id, ?, created_at FROM entry_tags WHERE tag_id = ?",
                (tid, r["id"])).rowcount
            conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (r["id"],))
            conn.execute("DELETE FROM tags WHERE id = ?", (r["id"],))
            merged += 1
        else:
            conn.execute("UPDATE tags SET name = ? WHERE id = ?", (r["to"], r["id"]))
            renamed += 1
    # ② A 类：删除标签与关联
    ids = [r["id"] for r in data["drop"]]
    drop_tags = drop_links = 0
    if ids:
        qmarks = ",".join("?" * len(ids))
        drop_links = conn.execute(
            "DELETE FROM entry_tags WHERE tag_id IN (%s)" % qmarks, ids).rowcount
        drop_tags = conn.execute(
            "DELETE FROM tags WHERE id IN (%s)" % qmarks, ids).rowcount
    conn.commit()
    return {"drop_tags": int(drop_tags), "drop_links": int(drop_links),
            "renamed": renamed, "merged": merged, "moved": int(moved)}


def backup_before_clean(db_path: str) -> str:
    """清理前整库备份 → `data/backup/prompts_precleanjunk_<时间戳>.db`；返回备份路径。"""
    d = os.path.join(config.data_dir(), config.BACKUP_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    bak = os.path.join(d, "%s_%s.db" % (BACKUP_PREFIX, stamp))
    shutil.copy2(db_path, bak)
    return bak


# ---------------------------------------------------------------------- #
# 自测
# ---------------------------------------------------------------------- #
def _selftest() -> None:
    """扫描/清理自测（临时库）：A 类删除、B 类重命名、B 类合并、幂等。"""
    import sqlite3
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ps_tagclean_")
    path = os.path.join(tmp, "t.db")
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE tags(id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL"
            " DEFAULT '__global__', name TEXT NOT NULL, color TEXT DEFAULT '',"
            " created_at TEXT, updated_at TEXT, UNIQUE(namespace, name));"
            "CREATE TABLE entry_tags(entry_id INTEGER NOT NULL, tag_id INTEGER NOT NULL,"
            " created_at TEXT, PRIMARY KEY(entry_id, tag_id));")
        # A 类：纯数字 / 色值 / 含标点
        for _n in ("12", "000000", "1A2332", "古风/汉服"):
            conn.execute("INSERT INTO tags(name) VALUES(?)", (_n,))
        # B 类：边缘标点可洗净；其中一个"洗净后与已有标签重名"（世界）
        for _n in ("[世界]", "[门派]", "世界"):
            conn.execute("INSERT INTO tags(name) VALUES(?)", (_n,))
        conn.execute("INSERT INTO tags(name) VALUES('正常标签')")
        # 关联：每个标签各挂 1 条条目（键为"标签名 → id"，便于按名引用）
        _ids = {r[1]: r[0] for r in conn.execute("SELECT id, name FROM tags")}
        for _i, _n in enumerate(_ids.keys()):
            conn.execute("INSERT INTO entry_tags(entry_id, tag_id) VALUES(?,?)",
                         (1000 + _i, _ids[_n]))
        conn.execute("INSERT INTO entry_tags(entry_id, tag_id) VALUES(1000, ?)",
                     (_ids["世界"],))
        conn.commit()

        data = scan(conn)
        _dn = sorted(r["name"] for r in data["drop"])
        assert _dn == ["000000", "12", "1A2332", "古风/汉服"], _dn
        _rn = sorted(r["name"] for r in data["rename"])
        assert _rn == ["[世界]", "[门派]"], _rn
        assert _ids["[世界]"] in [r["id"] for r in data["rename"]], data["rename"]

        st = apply(conn)
        assert st["drop_tags"] == 4 and st["renamed"] == 1 and st["merged"] == 1, st
        _left = {r[0] for r in conn.execute("SELECT name FROM tags")}
        assert _left == {"世界", "门派", "正常标签"}, _left
        # [世界] 与 世界 合并：关联应落在同一个 tag_id 上（且不重复）
        _rows = list(conn.execute(
            "SELECT entry_id FROM entry_tags WHERE tag_id = ?", (_ids["世界"],)))
        assert len(_rows) == len({r[0] for r in _rows}), _rows
        # 幂等：再扫一次应无任何可处理项
        d2 = scan(conn)
        assert not d2["drop"] and not d2["rename"], d2
        print("[标签清理] 扫描/A类删除/B类重命名/B类合并/幂等 通过")

        out = apply(conn)
        assert out["drop_tags"] == 0 and out["renamed"] == 0 and out["merged"] == 0, out
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
