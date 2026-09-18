# -*- coding: utf-8 -*-
"""
tag_builtin.py - 预置标签工具（阶段 1，2026-09-14）

用途：用「打标引擎」（app/tagger_engine.py）给**主库**批量打标，使标签随打包进 EXE
（`build.py` 会把 `data/prompts.db` 复制为内嵌库 `app/resources/builtin_prompts.db`）。

**幂等、可重跑、写库前强制备份**。

用法（在项目根目录执行）：
    python tag_builtin.py --dry-run              # 只预演：统计 + 样例 + 报告，**不写库**
    python tag_builtin.py --dry-run --report docs\\预置标签预演报告.md
    python tag_builtin.py                        # 正式写入（先自动备份）
    python tag_builtin.py --only-untagged        # 只给"当前没有任何标签"的条目打标
    python tag_builtin.py --limit 200            # 只处理前 200 条（试跑用）

参数：
    --db PATH         指定库文件（默认 data/prompts.db）
    --limit N         只处理前 N 条条目
    --only-untagged   只处理当前无标签的条目（保护人工标签）
    --max-tags N      每条例目标签上限（默认 3）
    --replace         写入时**替换**该条目既有标签（默认：并入追加）
    --report FILE     生成 Markdown 报告（统计 + 样例）
    --yes             跳过交互确认（脚本化使用）

安全设计：
    1. `--dry-run` 默认建议先跑：**绝不写库**，只输出统计与样例供人工抽检；
    2. 正式写入前调用 `backup.pretag_snapshot()`（独立前缀 `prompts_pretag_*.db`、
       不按天去重、只留最近 10 份）；**备份失败即中止**，不继续打标；
    3. 写入用 `db.set_entry_tags_bulk(..., touch_updated=False)`：
       预置标签属"数据初始化"，**不刷新 entries.updated_at**，避免把 2555 条灌进当日变更包。
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import backup, config, tagger, tagger_engine  # noqa: E402
from app.database import Database  # noqa: E402

DEFAULT_DB = os.path.join(config.data_dir(), config.DB_FILE_NAME)


def _collect(db, dict_data, limit=0, only_untagged=False, max_tags=3):
    """遍历条目 → 生成 {entry_id: [标签...]} 与统计数据。

    分类上下文（分类链 / 根目录 / 项目类别）**委托 app/tagger.py 的公共实现**
    （与阶段 2「批量智能打标」共用同一份逻辑，避免两处实现不一致）。

    2026-09-16（批次 12-2，用户要求 2）：本工具＝"离线打标"入口，**取词是否启用**
    与「批量智能自动打标」「设置 → 标签与词表」共用同一 meta 键 `config.META_FALLBACK_BATCH`
    （读**目标库**的 meta；无该键 ⇒ 默认"不用取词"，与既有出厂标签口径一致）。
    """
    index = tagger.build_context_index(db)
    # 取词开关（默认关）＋ 热点词清单（供取词命中校验）
    try:
        _use_fb = db.get_meta(config.META_FALLBACK_BATCH) == "1"
    except Exception:
        _use_fb = False
    try:
        _hot = db.list_hotwords()
    except Exception:
        _hot = []
    tagged = set()
    if only_untagged:
        tagged = {r[0] for r in db.conn.execute("SELECT DISTINCT entry_id FROM entry_tags")}
    rows = db.conn.execute(
        "SELECT id, category_id, name, intro, features, image_desc, prompt_cn, prompt_en"
        " FROM entries ORDER BY id").fetchall()

    assignments = {}
    domains, sources, tag_freq, dim_freq = Counter(), Counter(), Counter(), Counter()
    zero_entries, samples = 0, []
    for r in rows:
        if limit and len(assignments) >= limit:
            break
        if only_untagged and r["id"] in tagged:
            continue
        texts = {k: r[k] for k in ("name", "intro", "features", "image_desc",
                                   "prompt_cn", "prompt_en")}
        res = tagger_engine.suggest(texts, dict_data,
                                    tagger.entry_context_names(index, r["category_id"]),
                                    max_tags=max_tags,
                                    field_fallback=_use_fb, hotwords=_hot)   # 批次 12-2
        names = tagger_engine.tag_names(res)
        domains[res["domain"] or "（兜底）"] += 1
        sources[res["domain_source"]] += 1
        if not names:
            zero_entries += 1
        for t in res["tags"]:
            tag_freq[t["tag"]] += 1
            dim_freq[t["dim"]] += 1
        assignments[r["id"]] = names
        if len(samples) < 40:
            samples.append({"id": r["id"], "name": r["name"], "domain": res["domain"],
                            "domain_source": res["domain_source"], "tags": res["tags"]})
    return (assignments,
            {"domains": domains, "sources": sources, "tag_freq": tag_freq,
             "dim_freq": dim_freq, "zero_entries": zero_entries,
             "total": len(assignments)},
            samples)


def build_report(stats, samples, dict_data, total_entries, dry_run):
    """生成 Markdown 报告（供人工抽样验收）。"""
    L = []
    A = L.append
    A("# 预置标签%s报告" % ("预演" if dry_run else "执行"))
    A("")
    A("- 生成时间：见文件名/终端输出")
    A("- 词表：%d 个标签（通用骨架 + %s）"
      % (tagger.count_tags(dict_data), "、".join((dict_data.get("domains") or {}).keys())))
    A("- 处理条目：**%d** 条；完全无标签：**%d** 条（占 %.1f%%）"
      % (stats["total"], stats["zero_entries"],
         stats["zero_entries"] * 100.0 / max(stats["total"], 1)))
    A("")
    A("## 一、领域判定分布")
    A("")
    A("| 领域 | 条目数 |")
    A("|---|---|")
    for k, v in stats["domains"].most_common():
        A("| %s | %d |" % (k, v))
    A("")
    A("判定依据分布：%s" % "；".join("%s %d 条" % (k, v) for k, v in stats["sources"].most_common()))
    A("")
    A("## 二、维度命中分布")
    A("")
    A("| 维度 | 标签数（累计次数） |")
    A("|---|---|")
    for k, v in stats["dim_freq"].most_common():
        A("| %s | %d |" % (k, v))
    A("")
    A("## 三、标签使用频次（前 60）")
    A("")
    A("| 标签 | 条目数 |")
    A("|---|---|")
    for k, v in stats["tag_freq"].most_common(60):
        A("| %s | %d |" % (k, v))
    A("")
    A("## 四、样例（前 %d 条，供人工抽检）" % len(samples))
    A("")
    A("| # | 条目名称 | 领域 | 判定依据 | 标签（维度/得分） |")
    A("|---|---|---|---|---|")
    for i, s in enumerate(samples, 1):
        tags = " ｜ ".join("%s（%s/%.0f）" % (t["tag"], t["dim"], t["score"]) for t in s["tags"])
        A("| %d | %s | %s | %s | %s |" % (i, s["name"], s["domain"] or "-",
                                          s["domain_source"], tags or "（无）"))
    A("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="预置标签工具（阶段 1）")
    ap.add_argument("--db", default=DEFAULT_DB, help="库文件路径（默认 data/prompts.db）")
    ap.add_argument("--dry-run", action="store_true", help="只预演，不写库")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条")
    ap.add_argument("--only-untagged", action="store_true", help="只处理当前无标签的条目")
    ap.add_argument("--max-tags", type=int, default=tagger_engine.MAX_TAGS,
                    help="每条例目标签上限（默认 3）")
    ap.add_argument("--replace", action="store_true",
                    help="写入时替换该条目既有标签（默认：并入追加）")
    ap.add_argument("--report", default="", help="输出 Markdown 报告到指定路径")
    ap.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.db):
        print("[中止] 找不到库文件：%s" % args.db)
        return 2

    db = Database(args.db)
    try:
        total_entries = db.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        dict_data = tagger.load_dict(db, write_if_missing=not args.dry_run)
        print("[词表] %d 个标签（通用骨架 + %s）"
              % (tagger.count_tags(dict_data),
                 "、".join((dict_data.get("domains") or {}).keys())))
        if args.dry_run:
            existing = db.get_meta(config.META_TAG_DICT) or ""
            if not existing.strip():
                print("      （meta 中尚无词表：本次预演使用出厂种子，**未写入库**）")

        assignments, stats, samples = _collect(
            db, dict_data, limit=args.limit,
            only_untagged=args.only_untagged, max_tags=args.max_tags)

        print("[预演] 处理 %d 条；完全无标签 %d 条（%.1f%%）"
              % (stats["total"], stats["zero_entries"],
                 stats["zero_entries"] * 100.0 / max(stats["total"], 1)))
        print("       领域分布：" + "；".join("%s %d" % (k, v)
                                              for k, v in stats["domains"].most_common()))
        print("       判定依据：" + "；".join("%s %d" % (k, v)
                                              for k, v in stats["sources"].most_common()))
        print("       标签频次前 15：" + "；".join("%s %d" % (k, v)
                                                  for k, v in stats["tag_freq"].most_common(15)))
        print("[样例] 前 12 条：")
        for s in samples[:12]:
            print("   · %-30s [%s] %s"
                  % (s["name"][:30], s["domain"] or "兜底",
                     " / ".join(t["tag"] for t in s["tags"]) or "（无标签）"))

        if args.report:
            md = build_report(stats, samples, dict_data, total_entries, args.dry_run)
            with open(args.report, "w", encoding="utf-8") as f:
                f.write(md)
            print("[报告] 已写入：%s" % args.report)

        if args.dry_run:
            print("[完成] 预演结束，**未写入任何数据**。")
            return 0

        # ---- 正式写入：先强制备份，失败即中止 ----
        if not args.yes:
            ans = input("将写入 %d 条条目的标签（先自动备份）。继续？[y/N] " % stats["total"])
            if ans.strip().lower() not in ("y", "yes"):
                print("[取消] 未写入任何数据。")
                return 0
        snap = backup.pretag_snapshot(args.db)
        if not snap.get("ok"):
            print("[中止] 打标前备份失败：%s（未写入任何数据）" % snap.get("error"))
            return 3
        print("[备份] %s（清理旧快照 %d 份）" % (snap["path"], snap.get("removed") or 0))

        res = db.set_entry_tags_bulk(assignments,
                                     mode=("replace" if args.replace else "append"),
                                     touch_updated=False)   # 预置数据：不刷新 updated_at
        print("[完成] 条目 %d 条 · 新增标签关联 %d 条 · 新建标签 %d 个"
              % (res["entries"], res["links"], res["tags_created"]))
        print("       库内标签总数：%d；entry_tags 行数：%d"
              % (db.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
                 db.conn.execute("SELECT COUNT(*) FROM entry_tags").fetchone()[0]))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
