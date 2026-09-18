# -*- coding: utf-8 -*-
"""clean_junk_tags.py —— 清理库中**已存在的"垃圾标签"**（CLI，2026-09-17 新增）

**背景**（2026-09-17 用户反馈"自动打标后出现大量阿拉伯数字标签 / 标签带标点"）：
打标引擎的"显式标签"通道原先没有质量闸门，导致三类噪声写进了 `tags` / `entry_tags`：
    ① 纯数字 / 序号（`12`、`234`、`1145`）
    ② 十六进制色值（`000000`、`1A2332`，多来自提示词里的 `#000000` 颜色码）
    ③ 内部含标点的碎片（`古风/汉服`、`K-pop偶像`）

    **引擎侧已修好**（`app/tagger_engine.is_noise_tag()`），今后不再产生；
    **本脚本用于清理历史遗留**。

**本脚本是薄壳**：扫描 / 清理 / 备份的全部逻辑都在 `app/tag_cleanup.py`（数据层），
与软件内入口「设置 → 标签与词表 → 🧹 清理垃圾标签…」**共用同一份实现**，口径唯一。

处理口径（详见 `app/tag_cleanup.py` 模块文档）：
    A 类 真噪声（清洗后为空）      → **删除**标签及关联（纯数字 / 色值 / 内部含标点）
    B 类 边缘标点可洗净（`[世界]`） → **重命名**；目标名已存在则**合并关联**（不丢条目）

用法（默认**预演**，不写盘）：
    python clean_junk_tags.py                       # 预演：列出 A 类删除项与 B 类重命名项
    python clean_junk_tags.py --apply               # 执行：先备份到 data/backup/ 再处理
    python clean_junk_tags.py --check               # 校验：仍有 A 类噪声则退出码 1
    python clean_junk_tags.py --db <路径> [--apply | --check]
    python clean_junk_tags.py --no-backup --apply   # 执行但不备份（不推荐）

安全说明：
    · 只改 `tags` / `entry_tags` 两张表，**不动任何条目内容（entries）**；
    · `--apply` 默认先整库备份为 `data/backup/prompts_precleanjunk_<时间戳>.db`
      （独立前缀，**不参与任何自动清理**，可随时手工回滚）。
"""
import argparse
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import tag_cleanup  # noqa: E402

_DEFAULT_DB = os.path.join(_ROOT, "data", "prompts.db")


def main() -> int:
    ap = argparse.ArgumentParser(description="清理库中已存在的垃圾标签（纯数字 / 色值 / 含标点）")
    ap.add_argument("--db", default=_DEFAULT_DB, help="目标库路径（默认 data/prompts.db）")
    ap.add_argument("--apply", action="store_true", help="真正执行处理（默认只预演）")
    ap.add_argument("--check", action="store_true", help="仅校验：存在 A 类真噪声时退出码 1")
    ap.add_argument("--no-backup", action="store_true",
                    help="执行时不备份（默认备份到 data/backup/，不推荐）")
    args = ap.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print("✗ 文件不存在：%s" % db_path)
        return 2

    conn = sqlite3.connect(db_path)
    try:
        data = tag_cleanup.scan(conn)
        drop, rename = data["drop"], data["rename"]

        print("目标库：%s" % db_path)
        print("当前标签总数：%d ｜ 标签关联总数：%d" % (data["total"], data["links"]))
        print("判定口径：app/tag_cleanup.py（内部调用 tagger_engine.clean_tag_name）")
        print("A 类 真噪声（删除）：%d 个，合计关联 %d 条"
              % (len(drop), sum(r["n"] for r in drop)))
        print("B 类 边缘标点可洗净（重命名/合并）：%d 个，合计关联 %d 条"
              % (len(rename), sum(r["n"] for r in rename)))

        if drop:
            print("-" * 72)
            print("[A] 将被删除")
            print("%-6s %-26s %-8s %s" % ("ID", "标签名", "关联数", "原因"))
            for r in drop:
                print("%-6d %-26s %-8d %s" % (r["id"], r["name"][:24], r["n"], r["reason"]))
        if rename:
            print("-" * 72)
            print("[B] 将被重命名（目标名已存在则合并关联）")
            print("%-6s %-24s %-6s %-24s %s" % ("ID", "原名", "关联数", "新名", "说明"))
            _names = {r[0] for r in conn.execute("SELECT name FROM tags")}
            for r in rename:
                print("%-6d %-24s %-6d %-24s %s"
                      % (r["id"], r["name"][:22], r["n"], r["to"][:22],
                         "合并到已有标签" if r["to"] in _names else "重命名"))
        if drop or rename:
            print("-" * 72)

        if args.check:
            if drop:
                print("✗ 存在 A 类真噪声：如需清理请执行 --apply")
                return 1
            print("✓ 无 A 类真噪声")
            return 0

        if not drop and not rename:
            print("✓ 无垃圾标签")
            return 0

        if not args.apply:
            print("（预演模式：未做任何修改；确认无误后加 --apply 执行）")
            return 0

        if not args.no_backup:
            print("已备份：%s" % tag_cleanup.backup_before_clean(db_path))

        st = tag_cleanup.apply(conn)
        left = tag_cleanup.scan(conn)
        print("B 类：重命名 %d 个、合并 %d 个（迁移关联 %d 条）"
              % (st["renamed"], st["merged"], st["moved"]))
        print("A 类：删除标签 %d 个、删除关联 %d 条" % (st["drop_tags"], st["drop_links"]))
        print("清理后：标签总数 %d ｜ 关联总数 %d ｜ 剩余 A 类 %d ｜ 剩余 B 类 %d"
              % (left["total"], left["links"], len(left["drop"]), len(left["rename"])))
        print("✓ 完成（条目内容 entries 未做任何改动）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
