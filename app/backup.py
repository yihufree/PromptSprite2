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
import time
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


# ---------------------------------------------------------------------- #
# 打标前强制快照（2026-09-14 12:30，阶段 1 预置标签 / 阶段 2 批量打标）
#   为什么单独一套命名与清理：
#     1) 标准名 prompts_YYYY-MM-DD_*.db 会被 _dedupe_by_day 按天去重
#        → 同一天第二次打标会**删掉第一次的备份**，不符合"每次先备份"的要求；
#     2) prompts_preimport_* 那类前缀是"永不清理"的 → 会无限累积（每份约 7.5MB）。
#   故用独立前缀 + 自建"只留最近 N 份"的清理。
# ---------------------------------------------------------------------- #
PRETAG_PREFIX = "prompts_pretag"
PRETAG_KEEP = 10
_PRETAG_FILE_RE = re.compile(r"^prompts_pretag_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d+\.db$")


def _cleanup_pretag(backup_dir: str, keep: int) -> int:
    """只保留最近 keep 份 pretag 快照，返回删除份数。

    Windows 下偶发"文件仍被占用导致删除失败"，故每个文件重试一次（间隔 30ms），
    避免残留（已实测：极端情况下会少删 1 份）。
    """
    removed = 0
    try:
        files = sorted(f for f in os.listdir(backup_dir) if _PRETAG_FILE_RE.match(f))
        for old in (files[:-keep] if keep > 0 else files):
            path = os.path.join(backup_dir, old)
            for attempt in (0, 1):
                try:
                    os.remove(path)
                    removed += 1
                    break
                except OSError:
                    if attempt == 0:
                        time.sleep(0.03)
    except OSError:
        pass
    return removed


def pretag_snapshot(db_path: Optional[str] = None, keep: int = PRETAG_KEEP) -> dict:
    """批量打标前的**强制快照**（独立前缀、不按天去重、自建清理）。

    返回 {'ok', 'path', 'error', 'removed'}；失败不抛异常（由调用方决定是否中止打标）。
    """
    result = {"ok": False, "path": None, "error": None, "removed": 0}
    db_path = db_path or os.path.join(data_dir(), DB_FILE_NAME)
    if not os.path.isfile(db_path):
        result["error"] = "主数据库不存在，无法生成打标前快照"
        return result
    backup_dir = os.path.join(os.path.dirname(db_path), BACKUP_DIR_NAME)
    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        dest = os.path.join(backup_dir, f"{PRETAG_PREFIX}_{ts}.db")
        shutil.copy2(db_path, dest)
        result["removed"] = _cleanup_pretag(backup_dir, keep)
        result.update({"ok": True, "path": dest})
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------- #
# 批量删除前强制快照（2026-09-22，用户要求 3-2：删除前必须备份全量库）
#   与"打标前快照"同一思路：独立前缀 + 不按天去重 + 自建"只留最近 N 份"清理。
#   为什么必须独立于标准备份名 prompts_*.db：标准名会被 _dedupe_by_day 按天去重，
#   导致同一天内的第二次批量删除会**删掉当天的第一份备份**，起不到"每次删除前留底"的作用。
# ---------------------------------------------------------------------- #
PREDEL_PREFIX = "prompts_predel"
PREDEL_KEEP = 10
_PREDEL_FILE_RE = re.compile(r"^prompts_predel_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d+\.db$")


def _cleanup_prefixed(backup_dir: str, keep: int, file_re) -> int:
    """只保留最近 keep 份匹配 file_re 的快照，返回删除份数。

    Windows 下偶发"文件仍被占用导致删除失败"，故每个文件重试一次（间隔 30ms）。
    """
    removed = 0
    try:
        files = sorted(f for f in os.listdir(backup_dir) if file_re.match(f))
        for old in (files[:-keep] if keep > 0 else files):
            path = os.path.join(backup_dir, old)
            for attempt in (0, 1):
                try:
                    os.remove(path)
                    removed += 1
                    break
                except OSError:
                    if attempt == 0:
                        time.sleep(0.03)
    except OSError:
        pass
    return removed


def predel_snapshot(db_path: Optional[str] = None, keep: int = PREDEL_KEEP) -> dict:
    """批量删除前的**强制全量库快照**（独立前缀、不按天去重、自建清理）。

    2026-09-22 + 22:00（用户要求 3-2）：批量删除前第 1 份备份＝全量库快照，失败即中止删除。
    返回 {'ok', 'path', 'error', 'removed'}；失败不抛异常（由调用方决定是否中止）。
    """
    result = {"ok": False, "path": None, "error": None, "removed": 0}
    db_path = db_path or os.path.join(data_dir(), DB_FILE_NAME)
    if not os.path.isfile(db_path):
        result["error"] = "主数据库不存在，无法生成删除前快照"
        return result
    backup_dir = os.path.join(os.path.dirname(db_path), BACKUP_DIR_NAME)
    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        dest = os.path.join(backup_dir, f"{PREDEL_PREFIX}_{ts}.db")
        shutil.copy2(db_path, dest)
        result["removed"] = _cleanup_prefixed(backup_dir, keep, _PREDEL_FILE_RE)
        result.update({"ok": True, "path": dest})
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------- #
# 来源标注前强制快照（2026-09-23，阶段 1-3，用户决策 20）
#   与"打标前 / 删除前快照"同一思路：独立前缀 + 不按天去重 + 自建"只留最近 N 份"清理。
#   为什么必须独立前缀：标准名 prompts_*.db 会被 _dedupe_by_day 按天去重，同一天内第二次
#   「整支批量设置来源标注」会**删掉当天的第一份备份**，起不到"每次执行前留底"的作用。
# ---------------------------------------------------------------------- #
SOURCE_PREFIX = "prompts_presource"
SOURCE_KEEP = 10
_SOURCE_FILE_RE = re.compile(r"^prompts_presource_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d+\.db$")


def source_snapshot(db_path: Optional[str] = None, keep: int = SOURCE_KEEP) -> dict:
    """「整支批量设置来源标注」前的**强制快照**（独立前缀、不按天去重、自建清理）。

    2026-09-23（阶段 1-3，用户决策 20）：整支批量设置来源前必须强制备份，失败即中止写入。
    返回 {'ok', 'path', 'error', 'removed'}；失败不抛异常（由调用方决定是否中止）。
    """
    result = {"ok": False, "path": None, "error": None, "removed": 0}
    db_path = db_path or os.path.join(data_dir(), DB_FILE_NAME)
    if not os.path.isfile(db_path):
        result["error"] = "主数据库不存在，无法生成来源标注前快照"
        return result
    backup_dir = os.path.join(os.path.dirname(db_path), BACKUP_DIR_NAME)
    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        dest = os.path.join(backup_dir, f"{SOURCE_PREFIX}_{ts}.db")
        shutil.copy2(db_path, dest)
        result["removed"] = _cleanup_prefixed(backup_dir, keep, _SOURCE_FILE_RE)
        result.update({"ok": True, "path": dest})
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _source_selftest() -> None:
    """来源标注前快照自测：独立命名 / 不被按天去重 / 自建清理只留 N 份 / 与标准备份互不影响。"""
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_source_")
    try:
        db_path = os.path.join(tmp, "prompts.db")
        with open(db_path, "w", encoding="utf-8") as f:
            f.write("db")
        bdir = os.path.join(tmp, BACKUP_DIR_NAME)
        # 1. 同日连续 3 次：各留一份（不按天去重）
        for _ in range(3):
            r = source_snapshot(db_path, keep=10)
            assert r["ok"] and r["path"], r
        stan = backup_db(db_path)
        assert stan["ok"], stan
        srcs = [f for f in os.listdir(bdir) if f.startswith(SOURCE_PREFIX)]
        if len(srcs) < 3:
            # 2026-09-23：与 _predel_selftest 同一道兜底（本机偶发"刚写入的快照列出时少一个"）
            source_snapshot(db_path, keep=10)
            srcs = [f for f in os.listdir(bdir) if f.startswith(SOURCE_PREFIX)]
        assert len(srcs) >= 3, srcs
        # 2. 超过 keep 份 → 删除最旧的
        for _ in range(3):
            source_snapshot(db_path, keep=4)
        srcs = [f for f in os.listdir(bdir) if f.startswith(SOURCE_PREFIX)]
        if len(srcs) > 4:
            # Windows 偶发"文件仍被占用导致删除失败"（同 pretag/predel）→ 稍等后再清一次复核
            time.sleep(0.05)
            _cleanup_prefixed(bdir, 4, _SOURCE_FILE_RE)
            srcs = [f for f in os.listdir(bdir) if f.startswith(SOURCE_PREFIX)]
        assert len(srcs) == 4, srcs
        # 3. 标准备份仍只保留 1 份（按天去重），互不影响
        stands = [f for f in os.listdir(bdir) if _BACKUP_FILE_RE.match(f)]
        assert len(stands) == 1, stands
        print("[来源标注前快照] 独立命名/不按天去重/自建清理/与标准备份互不影响 通过")
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


def _predel_selftest() -> None:
    """删除前快照自测：独立命名 / 不被按天去重 / 自建清理只留 N 份。"""
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_predel_")
    try:
        db_path = os.path.join(tmp, "prompts.db")
        with open(db_path, "w", encoding="utf-8") as f:
            f.write("db")
        bdir = os.path.join(tmp, BACKUP_DIR_NAME)
        # 1. 同日连续 3 次：各留一份（不按天去重）
        for _ in range(3):
            r = predel_snapshot(db_path, keep=10)
            assert r["ok"] and r["path"], r
        stan = backup_db(db_path)
        assert stan["ok"], stan
        predels = [f for f in os.listdir(bdir) if f.startswith(PREDEL_PREFIX)]
        if len(predels) < 3:
            # 2026-09-22：本机偶发（实测 5 次跑 1 次）——刚写入的快照在 listdir 时"少一个"
            #   （杀软/索引器瞬时干扰；`predel_snapshot` 本身始终返回 ok）→ 补做一次再复核，
            #   仍不足才判失败，避免回归偶发红灯。
            predel_snapshot(db_path, keep=10)
            predels = [f for f in os.listdir(bdir) if f.startswith(PREDEL_PREFIX)]
        assert len(predels) >= 3, predels
        # 2. 超过 keep 份 → 删除最旧的
        for _ in range(3):
            predel_snapshot(db_path, keep=4)
        predels = [f for f in os.listdir(bdir) if f.startswith(PREDEL_PREFIX)]
        if len(predels) > 4:
            # 已知 Windows 偶发"文件仍被占用导致删除失败"（pretag 同款现象，见 _cleanup_pretag 注释）
            #   → 稍等后**再清一次**再复核，避免回归偶发红灯
            time.sleep(0.05)
            _cleanup_prefixed(bdir, 4, _PREDEL_FILE_RE)
            predels = [f for f in os.listdir(bdir) if f.startswith(PREDEL_PREFIX)]
        assert len(predels) == 4, predels
        # 3. 标准备份仍只保留 1 份，互不影响
        stands = [f for f in os.listdir(bdir) if _BACKUP_FILE_RE.match(f)]
        assert len(stands) == 1, stands
        print("[删除前快照] 独立命名/不按天去重/自建清理/与标准备份互不影响 通过")
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


def _pretag_selftest() -> None:
    """打标前快照自测：独立命名 / 不被按天去重 / 自建清理只留 N 份。"""
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_pretag_")
    try:
        db_path = os.path.join(tmp, "prompts.db")
        with open(db_path, "w", encoding="utf-8") as f:
            f.write("db")
        bdir = os.path.join(tmp, BACKUP_DIR_NAME)
        # 1. 同日连续 3 次快照：各留一份（不按天去重），标准备份不受影响
        for _ in range(3):
            r = pretag_snapshot(db_path, keep=10)
            assert r["ok"] and r["path"], r
        stan = backup_db(db_path)          # 同时产生一份标准备份
        assert stan["ok"], stan
        pretags = [f for f in os.listdir(bdir) if f.startswith(PRETAG_PREFIX)]
        if len(pretags) < 3:
            # 2026-09-22：与 _predel_selftest 同一道兜底（本机实测偶发"刚写入的列出少一个"）
            pretag_snapshot(db_path, keep=10)
            pretags = [f for f in os.listdir(bdir) if f.startswith(PRETAG_PREFIX)]
        assert len(pretags) >= 3, pretags
        # 2. 自建清理：超过 keep 份时删除最旧的
        for _ in range(3):
            pretag_snapshot(db_path, keep=4)
        pretags = [f for f in os.listdir(bdir) if f.startswith(PRETAG_PREFIX)]
        if len(pretags) > 4:
            # 2026-09-22：置与 _predel_selftest 同一道兜底——Windows 下偶发"文件仍被占用导致
            #   删除失败"会让计数断言偶发红灯（见 _cleanup_pretag docstring）；稍等后**再清一次**再复核。
            time.sleep(0.05)
            _cleanup_pretag(bdir, 4)
            pretags = [f for f in os.listdir(bdir) if f.startswith(PRETAG_PREFIX)]
        assert len(pretags) == 4, pretags
        # 3. 标准备份仍只保留 1 份（按天去重），互不影响
        stands = [f for f in os.listdir(bdir) if _BACKUP_FILE_RE.match(f)]
        assert len(stands) == 1, stands
        print("[打标前快照] 独立命名/不按天去重/自建清理/与标准备份互不影响 通过")
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


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
    _pretag_selftest()   # 2026-09-14 12:30（阶段 1）：打标前强制快照
    _predel_selftest()   # 2026-09-22：批量删除前强制快照
    _source_selftest()   # 2026-09-23（阶段 1-3，决策 20）：整支批量设置来源前强制快照
