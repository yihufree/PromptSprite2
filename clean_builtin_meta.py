# -*- coding: utf-8 -*-
"""clean_builtin_meta.py —— 清理"出厂内置库"里的运行态 / 个人化 meta 记录

背景（2026-09-15 审核报告 M-1）：`app/resources/builtin_prompts.db` 是随包发布的**出厂库**，
但开发机运行程序时会把运行态数据写进它的 `meta` 表，例如：
    settings_computer_code = DESKTOP-hahaha   ← 开发机**计算机名**（对外发布属信息泄露）
    settings_window_size   = 1360x780
    settings_view_mode     = list
    settings_detail_mode   = compact
    settings_tag_view      = cloud
    incr_last_sync         = 2026-09-14 21:01:13
新用户首次安装会直接继承这些值（窗口大小 / 视图模式 / 标签页停留位置，并携带开发机计算机名）。

本工具：删除该库 `meta` 表中**除白名单外**的一切键。白名单（必须保留）：
    schema_version          —— 库结构版本（删除会触发重复迁移）
    builtin_manual_version  —— 内置手册版本（删除会导致手册被重复导入）
    tag_dict                —— 出厂标签词表

用法（默认**预演**，不写盘）：
    python clean_builtin_meta.py                  # 预演：列出将要删除的键
    python clean_builtin_meta.py --apply          # 执行：先备份到 data/backup/ 再删除
    python clean_builtin_meta.py --check          # 校验：仍有运行态键则退出码 1（可作打包前置校验）
    python clean_builtin_meta.py --db <路径> [--apply | --check]
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_ROOT, "app", "resources", "builtin_prompts.db")
_BACKUP_DIR = os.path.join(_ROOT, "data", "backup")

# 允许保留的 meta 键；其余键一律视为"运行态 / 个人化"，出厂前应清掉
KEEP_KEYS = ("schema_version", "builtin_manual_version", "tag_dict")


def _list_keys(conn) -> list:
    return [r[0] for r in conn.execute("SELECT key FROM meta ORDER BY key")]


def main() -> int:
    ap = argparse.ArgumentParser(description="清理出厂内置库的运行态 meta 键")
    ap.add_argument("--db", default=_DEFAULT_DB,
                    help="内置库路径（默认 app/resources/builtin_prompts.db）")
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认只预演）")
    ap.add_argument("--check", action="store_true", help="仅校验：存在运行态键时退出码 1")
    ap.add_argument("--no-backup", action="store_true", help="执行时不备份（默认备份到 data/backup/）")
    args = ap.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"✗ 文件不存在：{db_path}")
        return 2

    conn = sqlite3.connect(db_path)
    try:
        keys = _list_keys(conn)
        drop = [k for k in keys if k not in KEEP_KEYS]
        print(f"内置库：{db_path}")
        print(f"当前 meta 键（{len(keys)}）：{', '.join(keys)}")
        print(f"白名单保留（{len(KEEP_KEYS)}）：{', '.join(KEEP_KEYS)}")
        print(f"待清理（{len(drop)}）：{', '.join(drop) if drop else '（无）'}")

        if args.check:
            if drop:
                print("✗ 存在运行态键：出厂前请先执行 --apply")
                return 1
            print("✓ 无运行态键")
            return 0

        if not args.apply:
            print("（预演模式：未做任何修改；加 --apply 执行）")
            return 0

        if drop and not args.no_backup:
            os.makedirs(_BACKUP_DIR, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            bak = os.path.join(_BACKUP_DIR, f"builtin_prompts_clean_{stamp}.db")
            shutil.copy2(db_path, bak)
            print(f"已备份：{bak}")

        if drop:
            conn.execute("DELETE FROM meta WHERE key NOT IN (%s)"
                         % ",".join("?" * len(KEEP_KEYS)), KEEP_KEYS)
            conn.commit()

        left = _list_keys(conn)
        print(f"清理后 meta 键（{len(left)}）：{', '.join(left)}")
        print("✓ 完成")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
