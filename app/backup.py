# -*- coding: utf-8 -*-
"""
backup.py - 启动自动静默备份 / 导入前快照
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

逻辑（2026-09-08 V1.7.0 备份策略修订）：
  1. 主库 prompts.db 存在 → 复制到 data/backup/prompts_时间戳.db
  2. 按天去重：同一天内只保留最后一份（避免一天开多次产生重复快照）
  3. 仅保留最近 N 份（默认 BACKUP_KEEP_COUNT=30，可在设置中修改），更早的自动删除
  4. 清理仅针对标准命名 prompts_YYYY-MM-DD_*.db；手动快照（snapshot_*、*_清理前_* 等）永不清理
  5. 失败（磁盘满等）→ 返回失败信息，由调用方在状态栏显示黄点警告

自测：python -m app.backup
"""
import os
import re
import shutil
import sqlite3
from datetime import datetime
from typing import Optional

from .config import data_dir, DB_FILE_NAME, BACKUP_DIR_NAME, BACKUP_KEEP_COUNT, META_BACKUP_KEEP

# 仅匹配标准自动备份名（prompts_YYYY-MM-DD_HH-MM-SS_ffffff.db）；
# snapshot_*、prompts_施工前_* 等手动快照永远不会被自动清理。
_BACKUP_FILE_RE = re.compile(r"^prompts_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}_\d+\.db$")

# 导入前快照（V1.7.0）：独立前缀，不参与任何自动清理
PREIMPORT_PREFIX = "prompts_preimport"


def _read_keep_from_meta(db_path: str, default: int) -> int:
    """从主库 meta 读取用户设置的备份保留份数；读取失败或非法回退默认值。"""
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (META_BACKUP_KEEP,)).fetchone()
        finally:
            conn.close()
        if row:
            return max(int(row[0]), 1)
    except Exception:
        pass
    return default


def preimport_snapshot(db_path: Optional[str] = None) -> dict:
    """导入变更包前自动快照（V1.7.0，可回滚）。

    名称 prompts_preimport_YYYY-MM-DD_HH-MM-SS.db，独立前缀、不参与自动清理。
    返回 {'ok','path','error'}；失败不抛异常。
    """
    result = {"ok": False, "path": None, "error": None}
    db_path = db_path or os.path.join(data_dir(), DB_FILE_NAME)
    if not os.path.isfile(db_path):
        result["error"] = "主数据库不存在，无法生成导入前快照"
        return result
    backup_dir = os.path.join(os.path.dirname(db_path), BACKUP_DIR_NAME)
    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        dest = os.path.join(backup_dir, f"{PREIMPORT_PREFIX}_{ts}.db")
        shutil.copy2(db_path, dest)
        result.update({"ok": True, "path": dest})
    except Exception as exc:
        result["error"] = str(exc)
    return result


def backup_db(db_path: Optional[str] = None, keep: Optional[int] = None) -> dict:
    """执行一次启动备份。

    返回：{'ok': bool, 'path': Optional[str], 'error': Optional[str]}
    """
    result = {"ok": False, "path": None, "error": None}
    db_path = db_path or os.path.join(data_dir(), DB_FILE_NAME)

    if not os.path.isfile(db_path):
        result["error"] = "主数据库不存在，跳过备份"
        return result

    backup_dir = os.path.join(os.path.dirname(db_path), BACKUP_DIR_NAME)
    try:
        os.makedirs(backup_dir, exist_ok=True)
        keep = (keep if keep is not None else BACKUP_KEEP_COUNT)
        # 优先取用户设置（设置未写入前读不到，用默认值）
        keep = _read_keep_from_meta(db_path, keep)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        day = ts[:10]
        dest = os.path.join(backup_dir, f"prompts_{ts}.db")
        shutil.copy2(db_path, dest)
        _dedupe_by_day(backup_dir, day, dest)
        _cleanup_old_backups(backup_dir, keep)
        result["ok"] = True
        result["path"] = dest
    except Exception as exc:  # 磁盘满等异常 → 静默返回失败
        result["error"] = str(exc)
    return result


def _dedupe_by_day(backup_dir: str, day: str, keep_path: str) -> None:
    """按天去重：删除同日更早的标准备份（保留最新 keep_path 这一份）。"""
    keep_name = os.path.basename(keep_path)
    try:
        for f in os.listdir(backup_dir):
            if f == keep_name or not f.endswith(".db"):
                continue
            m = _BACKUP_FILE_RE.match(f)
            if m and m.group(1) == day:
                try:
                    os.remove(os.path.join(backup_dir, f))
                except OSError:
                    pass
    except OSError:
        pass


def _cleanup_old_backups(backup_dir: str, keep: int) -> None:
    """按文件名时间排序，只保留最近 keep 份（仅匹配标准备份名，快照等其他文件不受影响）"""
    files = sorted(
        f for f in os.listdir(backup_dir)
        if _BACKUP_FILE_RE.match(f)
    )
    for old in files[:-keep] if keep > 0 else files:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            pass


def _selftest() -> None:
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_backup_")
    try:
        db_path = os.path.join(tmp, "prompts.db")
        with open(db_path, "w", encoding="utf-8") as f:
            f.write("test-db-content")

        # 1. 手动快照不受清理影响
        snap = os.path.join(tmp, BACKUP_DIR_NAME, "snapshot_清理前_20260829.db")
        os.makedirs(os.path.dirname(snap), exist_ok=True)
        with open(snap, "w", encoding="utf-8") as f:
            f.write("snapshot")
        for _ in range(7):
            r = backup_db(db_path, keep=5)
            assert r["ok"], r
        files = [f for f in os.listdir(os.path.join(tmp, BACKUP_DIR_NAME)) if f.endswith(".db")]
        # 同日去重 → 7 次仅保留 1 份标准备份 + 快照 = 2 个
        assert len(files) == 2, files
        assert os.path.isfile(snap), "快照不应被备份清理误删"
        print("[1] 备份 + 按天去重 + 快照不受影响 通过")

        # 2. 跨天累计超过上限 → 只保留最近 keep 份（把旧文件 mtime 改早）
        from datetime import timedelta
        import time as _time
        bdir = os.path.join(tmp, BACKUP_DIR_NAME)
        # 伪造 6 个不同日期标准备份
        for i, d in enumerate(("2026-09-01", "2026-09-02", "2026-09-03",
                               "2026-09-04", "2026-09-05", "2026-09-06")):
            p = os.path.join(bdir, f"prompts_{d}_00-00-00_000000.db")
            with open(p, "w", encoding="utf-8") as f:
                f.write("x")
        r = backup_db(db_path, keep=5)
        assert r["ok"], r
        std = [f for f in os.listdir(bdir) if _BACKUP_FILE_RE.match(f)]
        assert len(std) == 5, std  # 只保留最近 5 份
        print("[2] 超过保留份数自动清理 通过")

        # 3. 主库不存在 → 跳过（不视为错误）
        r = backup_db(os.path.join(tmp, "missing.db"))
        assert not r["ok"] and "不存在" in r["error"]
        print("[3] 主库不存在跳过 通过")

        # 4. 导入前快照（独立前缀、不参与清理）
        r = preimport_snapshot(db_path)
        assert r["ok"] and os.path.isfile(r["path"]), r
        _cleanup_old_backups(bdir, 5)
        assert os.path.isfile(r["path"]), "导入前快照不应被自动清理"
        print("[4] 导入前快照(可回滚) 通过")
        print("=== 备份模块自测通过 ===")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
