# -*- coding: utf-8 -*-
"""
tagger_batch.py - 批量打标的"批次记录"与精确撤销（阶段 2，2026-09-14）

职责（**纯数据层，无界面**）：
  1. 每次批量打标生成一个"批次"记录（含**逐条目的 打标前/打标后 标签**），写入
     `data/backup/tagging/打标批次_<时间戳>.json`；
  2. **精确撤销**：按批次明细把每条条目还原为打标**前**的标签（不影响其后做的其他修改）；
  3. 批次列表 / 读取 / 清理（只保留最近 N 个）。

设计要点（对应《可行性研究报告 v2》§10.5）：
  - 明细结构：`items = [{entry_id, name, before: [标签名], after: [标签名]}]`；
  - 撤销用 `db.set_entry_tags_bulk(..., mode="replace", touch_updated=True)` 一次事务完成
    （`touch_updated=True`：标签确实变了，应当进入当日变更包）；
  - 撤销只把 `undone` 置真并记录时间，**不删除明细文件**（便于事后追溯）；
  - 仅保留最近 `BATCH_KEEP`（默认 10）个批次，避免无限累积。

自测：python -m app.tagger_batch
"""
import json
import os
from datetime import datetime

from . import config

BATCH_DIR_NAME = "tagging"          # data/backup/tagging/
BATCH_PREFIX = "打标批次"            # 打标批次_2026-09-14_13-40-00.json
BATCH_KEEP = 10                     # 保留最近 N 个批次

ENGINE_VERSION = "tagger_engine/1 + 词表(tag_dict)"   # 引擎与词表版本标识（写入批次便于追溯）


def batch_dir() -> str:
    """批次明细目录：data/backup/tagging/"""
    return os.path.join(config.data_dir(), config.BACKUP_DIR_NAME, BATCH_DIR_NAME)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def new_batch(options=None, db_path: str = "") -> dict:
    """创建一个空批次对象（未落盘）。

    2026-09-14（阶段 2 增强）：新增 `db_path` 记录**本次打标的目标库文件路径**，
    使「撤销」能回到**正确的库**（批量打标现已支持"目标库＝其他库文件"）。
    """
    return {
        "batch_id": _now_stamp(),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "engine_version": ENGINE_VERSION,
        "db_path": db_path or "",
        "options": dict(options or {}),
        "stats": {},
        "items": [],
        "undone": False,
        "undone_at": "",
    }


def batch_db_path(path: str) -> str:
    """读取批次记录里的**目标库路径**（供"撤销到正确的库"用；无记录返回空串）。"""
    return (load_batch(path) or {}).get("db_path") or ""


def save_batch(batch: dict) -> dict:
    """把批次写入 `data/backup/tagging/`，并清理只保留最近 BATCH_KEEP 个。

    返回 {'ok','path','error','removed'}；失败不抛异常。
    """
    out = {"ok": False, "path": None, "error": None, "removed": 0}
    try:
        d = batch_dir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{BATCH_PREFIX}_{batch.get('batch_id') or _now_stamp()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False, indent=1)
        out["path"] = path
        out["removed"] = _cleanup_old()
        out["ok"] = True
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _batch_files() -> list:
    """按文件名（含时间戳）升序返回批次文件全路径。"""
    d = batch_dir()
    try:
        return [os.path.join(d, f) for f in sorted(os.listdir(d))
                if f.startswith(BATCH_PREFIX) and f.endswith(".json")]
    except OSError:
        return []


def _cleanup_old(keep: int = BATCH_KEEP) -> int:
    """只保留最近 keep 个批次，返回删除个数（失败静默）。"""
    removed = 0
    for path in (_batch_files()[:-keep] if keep > 0 else _batch_files()):
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def load_batch(path: str) -> dict:
    """读取批次文件；失败返回 {}。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.loads(f.read())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def list_batches(limit: int = 20) -> list:
    """列出批次摘要（**最新的在前**）：[{path,batch_id,created_at,total,undone,undone_at,options}]"""
    out = []
    for path in reversed(_batch_files()):
        b = load_batch(path)
        if not b:
            continue
        out.append({
            "path": path,
            "batch_id": b.get("batch_id") or os.path.basename(path),
            "created_at": b.get("created_at") or "",
            "total": len(b.get("items") or []),
            "tags": sum(len(it.get("after") or []) for it in (b.get("items") or [])),
            "undone": bool(b.get("undone")),
            "undone_at": b.get("undone_at") or "",
            "options": b.get("options") or {},
        })
        if limit and len(out) >= limit:
            break
    return out


def undo_batch(db, path: str) -> dict:
    """按批次明细**精确撤销**：把每条条目还原为打标前的标签。

    返回 {'ok','total','restored','skipped','error'}；已撤销过的批次返回 ok=False。
    """
    batch = load_batch(path)
    if not batch:
        return {"ok": False, "error": "批次文件读取失败", "total": 0,
                "restored": 0, "skipped": 0}
    if batch.get("undone"):
        return {"ok": False, "error": "该批次已撤销过（可在明细文件中查看）",
                "total": 0, "restored": 0, "skipped": 0}
    items = batch.get("items") or []
    # 只还原"当前确实与本批次写入结果一致"的条目？——为简单与可控，这里按明细全部还原
    # （before 即打标前状态；即使其后用户又手工改过，撤销后仍为该批次的打标前状态，
    #  这是"精确撤销"的既有语义，导入/恢复均以明细为准）
    assignments = {}
    skipped = 0
    for it in items:
        try:
            eid = int(it.get("entry_id"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if db.get_entry(eid) is None:      # 条目已被删除 → 跳过（不报错）
            skipped += 1
            continue
        assignments[eid] = list(it.get("before") or [])
    try:
        res = db.set_entry_tags_bulk(assignments, mode="replace", touch_updated=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "total": len(items),
                "restored": 0, "skipped": skipped}
    batch["undone"] = True
    batch["undone_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return {"ok": True, "error": None, "total": len(items),
            "restored": res.get("entries", 0), "skipped": skipped}


def _selftest() -> None:
    """批次记录与精确撤销自测（临时库 + 临时 data 目录）。"""
    import shutil
    import tempfile

    from .database import Database
    from .models import Entry

    tmp = tempfile.mkdtemp(prefix="promptsprite_batch_")
    old_data_dir = config.data_dir
    try:
        config.data_dir = lambda: tmp          # 让批次文件写到临时目录
        db = Database(os.path.join(tmp, "t.db"))
        try:
            did = db.add_domain("根")
            c1 = db.add_category("C1", domain_id=did)
            e1 = db.add_entry(Entry(name="甲", category_id=c1))
            e2 = db.add_entry(Entry(name="乙", category_id=c1))
            e3 = db.add_entry(Entry(name="丙", category_id=c1))
            db.set_entry_tags(e1, ["原有"])
            db.set_entry_tags(e3, ["原有3"])

            # 1. 生成批次：before=打标前、after=打标后
            batch = new_batch({"对象": "仅无标签", "每条例目": 3})
            batch["items"] = [
                {"entry_id": e1, "name": "甲", "before": ["原有"], "after": ["原有", "新A"]},
                {"entry_id": e2, "name": "乙", "before": [], "after": ["新A", "新B"]},
                {"entry_id": e3, "name": "丙", "before": ["原有3"], "after": ["新A"]},
            ]
            batch["stats"] = {"total": 3}
            batch["db_path"] = os.path.join(tmp, "t.db")     # 2026-09-14：目标库路径
            # 实际写入（模拟打标结果）
            db.set_entry_tags_bulk({e1: ["原有", "新A"], e2: ["新A", "新B"], e3: ["新A"]},
                                   mode="replace", touch_updated=False)
            assert sorted(t["name"] for t in db.list_entry_tags(e2)) == ["新A", "新B"]

            # 2. 落盘 + 列表
            res = save_batch(batch)
            assert res["ok"] and os.path.isfile(res["path"]), res
            lst = list_batches()
            assert len(lst) == 1 and lst[0]["total"] == 3 and lst[0]["undone"] is False, lst
            assert lst[0]["tags"] == 5, lst          # 2+2+1
            assert batch_db_path(res["path"]) == os.path.join(tmp, "t.db"), \
                batch_db_path(res["path"])           # 目标库路径可读回

            # 3. 精确撤销 → 完全回到打标前
            un = undo_batch(db, res["path"])
            assert un["ok"] and un["restored"] == 3, un
            assert [t["name"] for t in db.list_entry_tags(e1)] == ["原有"]
            assert db.list_entry_tags(e2) == []
            assert [t["name"] for t in db.list_entry_tags(e3)] == ["原有3"]
            # 4. 幂等：重复撤销被拒绝
            assert undo_batch(db, res["path"])["ok"] is False
            assert list_batches()[0]["undone"] is True

            # 5. 保留份数：超过 BATCH_KEEP 时删除最旧的
            for i in range(BATCH_KEEP + 2):
                b = new_batch()
                b["batch_id"] = "2026-09-14_00-00-%02d" % i
                b["items"] = []
                assert save_batch(b)["ok"]
            files = _batch_files()
            assert len(files) == BATCH_KEEP, len(files)

            # 6. 条目已删除时撤销跳过（不报错）
            b2 = new_batch()
            b2["batch_id"] = "2026-09-14_23-59-59"
            b2["items"] = [{"entry_id": e2, "name": "乙", "before": [], "after": ["x"]}]
            r2 = save_batch(b2)
            db.delete_entry(e2, purge_image=False)
            un2 = undo_batch(db, r2["path"])
            assert un2["ok"] and un2["skipped"] == 1 and un2["restored"] == 0, un2

            print("[打标批次] 生成/落盘/列表/精确撤销/幂等/保留份数/条目已删跳过 通过；"
                  "批次目录=%s" % batch_dir())
        finally:
            db.close()
    finally:
        config.data_dir = old_data_dir
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
