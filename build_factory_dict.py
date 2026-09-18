# -*- coding: utf-8 -*-
"""build_factory_dict.py —— 用一份词表「重生出厂词表」（2026-09-18 新增）

为什么需要本工具：
  出厂词表其实存在**两处**，少改一处就会出现"新装用户是旧词表 / 恢复出厂是旧词表"的不一致：
    ① `app/tagger_dict.py` 的 `_BUILTIN` —— 首次初始化（meta 为空）与「↺ 恢复出厂词表」的种子；
    ② `app/resources/builtin_prompts.db` 的 meta 键 `tag_dict` —— 打包态首次运行会**整库复制**此库，
       且该键属 `clean_builtin_meta.py` 的**保留白名单**，不会被打包脚本清理。
  本工具把这两处**一次改到位**，并把旧的种子文件 / 旧内嵌库 / 新词表 JSON 全部**备份存档**。

用法（**默认只预演，不写盘**）：
    python build_factory_dict.py                       # 预演：来源 = data/prompts.db 里当前词表
    python build_factory_dict.py --json 我的词表.json    # 预演：来源 = 指定 JSON
    python build_factory_dict.py --apply               # 执行（自动备份 + 重写 + 同步内嵌库）
  可选：
    --db <路径>     内嵌库路径（默认 app/resources/builtin_prompts.db）
    --src-db <路径> 取当前词表的库（默认 data/prompts.db）
    --no-selftest   跳过"写后自测"（默认写盘后会跑一次 `python -m app.tagger_dict`）

⚠️ 改完必须**重新打包** EXE，新装用户的出厂词表才是新的；
   已在用的老库不会自动跟随（data/prompts.db 已存在，不会被覆盖）——需在新版里点一次
   「↺ 恢复出厂词表」或「➕ 增量导入…」导入新词表。
"""
import argparse
import ast
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app import tagger                      # noqa: E402  （复用校验/规范化/统计，口径与运行时一致）
from app import tagger_dict as _td_old      # noqa: E402

_DEV_DB = os.path.join(ROOT, "data", "prompts.db")
_BUILTIN_DB = os.path.join(ROOT, "app", "resources", "builtin_prompts.db")
_TARGET_PY = os.path.join(ROOT, "app", "tagger_dict.py")
_ARCHIVE_ROOT = os.path.join(ROOT, "Dev_Work_Log", "archive")

_TAIL_MARK = "\ndef builtin_dict() -> dict:"


def _stats(d: dict) -> dict:
    """规模统计（统一口径，避免各处取错键）：{骨架标签, 领域包数, 领域包标签, 合计}。"""
    s = tagger.dict_summary(d)
    uni = int(s.get("通用骨架标签数") or 0)
    total = int(s.get("标签总数") or 0)
    return {"骨架标签": uni, "领域包数": len(s.get("领域包") or {}),
            "领域包标签": total - uni, "合计": total, "领域包": s.get("领域包") or {}}


# ---------------------------------------------------------------------- #
# 读来源
# ---------------------------------------------------------------------- #
def _load_source(args) -> tuple:
    """返回 (词表 dict, 来源描述)。结构非法直接抛错（拒绝写盘）。"""
    if args.json:
        with open(args.json, "r", encoding="utf-8-sig") as f:
            raw = json.loads(f.read())
        src = _disp_path(args.json)
    else:
        conn = sqlite3.connect(args.src_db)
        try:
            row = conn.execute("select value from meta where key='tag_dict'").fetchone()
        finally:
            conn.close()
        if not row:
            raise SystemExit("✗ %s 的 meta 里没有 tag_dict；请用 --json 指定词表文件" % args.src_db)
        raw = json.loads(row[0])
        src = "%s（meta.tag_dict）" % _disp_path(args.src_db)
    errors = tagger.validate_dict(raw)
    if errors:
        raise SystemExit("✗ 词表结构不合法，未做任何修改：\n  - " + "\n  - ".join(errors))
    return tagger.normalize_dict(raw), src


# ---------------------------------------------------------------------- #
# 生成 app/tagger_dict.py 文本
# ---------------------------------------------------------------------- #
def _q(s) -> str:
    return json.dumps(str(s), ensure_ascii=False)


def _disp_path(p: str) -> str:
    """展示用路径：项目内用相对路径、统一正斜杠（**不把开发机绝对路径写进出厂模块**）。"""
    try:
        rel = os.path.relpath(p, ROOT)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except Exception:
        pass
    return os.path.basename(p)


def _fmt_words(words, indent: int) -> str:
    """`[..]` 字面量；超长自动折行（续行缩进 indent+4，右括号回到 indent 列）。"""
    items = [_q(w) for w in words]
    one = "[" + ", ".join(items) + "]"
    if indent + len(one) <= 100:
        return one
    inner = " " * (indent + 4)
    lines, cur = [], []
    for it in items:
        if cur and indent + len(", ".join(cur + [it])) + 1 > 100:
            lines.append(", ".join(cur) + ",")
            cur = [it]
        else:
            cur.append(it)
    if cur:
        lines.append(", ".join(cur) + ",")
    return "[\n" + "\n".join(inner + ln for ln in lines) + "\n" + " " * indent + "]"


def _build_literal(d: dict, now: str) -> str:
    L = ["_BUILTIN = {",
         '    "version": %d,' % int(d.get("version") or 1),
         '    "updated_at": %s,' % _q(now),
         "",
         "    # ---------------- 通用骨架（所有条目共用；可编辑、可扩展） ----------------",
         '    "universal": {']
    for dim, labels in (d.get("universal") or {}).items():
        L.append("        %s: {" % _q(dim))
        for tag, words in labels.items():
            L.append("            %s: %s," % (_q(tag), _fmt_words(words, 12 + len(_q(tag)) + 2)))
        L.append("        },")
    L += ["    },", "", '    # ---------------- 领域维度包（按"单主领域"装载） ----------------',
          '    "domains": {']
    for dom, dims in (d.get("domains") or {}).items():
        L.append("        # ===== %s 包 =====" % dom)
        L.append("        %s: {" % _q(dom))
        for dim, labels in dims.items():
            L.append("            %s: {" % _q(dim))
            for tag, words in labels.items():
                L.append("                %s: %s," % (_q(tag), _fmt_words(words, 16 + len(_q(tag)) + 2)))
            L.append("            },")
        L.append("        },")
    L += ["    },", "", "    # ---------------- 领域判定表（分类名 / 根目录名 → 领域） ----------------",
          '    "domain_map": {']
    for dom, words in (d.get("domain_map") or {}).items():
        L.append("        %s: %s," % (_q(dom), _fmt_words(words, 8 + len(_q(dom)) + 2)))
    L += ["    },", "}"]
    return "\n".join(L)


def _build_module(d: dict, src: str, now: str) -> str:
    """完整的新 tagger_dict.py 文本 = 新头注释 + 新 _BUILTIN + 原文件尾部函数（原样保留）。"""
    with open(_TARGET_PY, "r", encoding="utf-8") as f:
        old = f.read()
    idx = old.find(_TAIL_MARK)
    if idx < 0:
        raise SystemExit("✗ 未在 %s 找到 `%s` 之后的函数段，已中止（不覆盖）"
                         % (_TARGET_PY, _TAIL_MARK.strip()))
    tail = old[idx:]
    st = _stats(d)
    domains = "、".join(st["领域包"].keys())
    head = '''# -*- coding: utf-8 -*-
"""
tagger_dict.py - 标签词表「出厂种子」（本文件由 `build_factory_dict.py` 生成，**请勿手工逐行维护**）

本模块只提供**只读的默认词表**（"出厂种子"），供两处使用：
  1. 首次启动时初始化（写入数据库 meta 表键 `tag_dict`）；
  2. 用户在「词表」页签点「↺ 恢复出厂词表」时回退到本默认值。

**权威数据在 meta 表**（用户可编辑、可扩展）；本模块**永不参与运行时匹配的读取**，
只作为"默认值/兜底值"。词表的读写、校验、导入导出见 `app/tagger.py`。

结构（与《智能标签功能可行性研究报告 v3》§4.3 一致）：
  {{
    "version": 1,
    "updated_at": "...",
    "universal": {{维度名: {{标签名: [匹配词, ...]}}}},          # 通用骨架（所有领域共用）
    "domains":   {{领域名: {{维度名: {{标签名: [匹配词...]}}}}}},  # 领域维度包（按单主领域装载）
    "domain_map":{{领域名: [分类/根目录名关键词, ...]}},        # 分类名 → 领域 的判定表
  }}

匹配词写法：
  - 英文一律小写（匹配时文本也已小写）；按"词边界"匹配，避免 "man" 命中 "human"；
  - 中文按"子串"匹配；
  - 以 `re:` 前缀书写时按正则匹配（例如 "re:2\\\\.39:1"）。

**加一个大类 = 加一个键**：在 `domains` 里加一个键（并在 `universal.领域` 与 `domain_map`
各加一项）即可，无需改代码。

规模（本版生成时实测）：通用骨架 {uni} 个标签 + {nd} 个领域包 {dom} 个标签
  = **合计 {total} 个标签**。
来源：{src}
生成时间：{now}

自测：python -m app.tagger_dict
"""
import copy

TAG_DICT_VERSION = {ver}

'''.format(uni=st["骨架标签"], nd=st["领域包数"], dom=st["领域包标签"],
           total=st["合计"], src=src, now=now, ver=int(d.get("version") or 1))
    # 领域清单注释（便于人读）
    head += "# 领域包（%d 个）：%s\n\n\n" % (st["领域包数"], domains)
    return head + _build_literal(d, now) + "\n" + tail


# ---------------------------------------------------------------------- #
# 写盘
# ---------------------------------------------------------------------- #
def _write_builtin_db(db_path: str, text: str, archive: str, stamp: str) -> str:
    """把词表写进内嵌库 meta.tag_dict（先备份整个库文件）；返回备份路径（无备份返回 ""）。"""
    bak = ""
    if os.path.isfile(db_path):
        bak = os.path.join(archive, "builtin_prompts_db_备份_%s.db" % stamp)
        shutil.copy2(db_path, bak)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO meta(key, value) VALUES('tag_dict', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (text,))
        conn.commit()
    finally:
        conn.close()
    return bak


def main() -> int:
    ap = argparse.ArgumentParser(description="用一份词表重生出厂词表（tagger_dict.py + 内嵌库）")
    ap.add_argument("--json", default="", help="词表 JSON 文件（默认取 --src-db 里当前词表）")
    ap.add_argument("--src-db", default=_DEV_DB, help="取当前词表的库（默认 data/prompts.db）")
    ap.add_argument("--db", default=_BUILTIN_DB, help="内嵌库路径（默认 app/resources/builtin_prompts.db）")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只预演）")
    ap.add_argument("--no-selftest", action="store_true", help="写盘后不跑 `python -m app.tagger_dict`")
    args = ap.parse_args()

    data, src = _load_source(args)
    st = _stats(data)
    print("来源：%s" % src)
    print("规模：通用骨架 %d 个标签 + %d 个领域包 %d 个标签 = **%d 个标签**"
          % (st["骨架标签"], st["领域包数"], st["领域包标签"], st["合计"]))
    print("领域包：%s" % "、".join(st["领域包"].keys()))
    print("当前出厂种子（未改前）：%s 个标签" % _td_old.builtin_stats().get("标签总数"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_py = _build_module(data, src, now)   # 生成文本（语法自检：不合法立即抛错，绝不写坏文件）
    ast.parse(new_py)

    if not args.apply:
        print("\n将重写 %s：共 %d 行；开头预览——"
              % (os.path.relpath(_TARGET_PY, ROOT), new_py.count("\n") + 1))
        for ln in new_py.split("\n")[:30]:
            print("    | " + ln)
        print("\n（预演模式：未做任何修改；加 --apply 执行）")
        return 0

    archive = os.path.join(_ARCHIVE_ROOT, "%s_词表出厂化" % datetime.now().strftime("%Y%m%d"))
    os.makedirs(archive, exist_ok=True)

    # 备份：旧种子 .py / 旧种子 JSON / 新词表 JSON 存档
    if os.path.isfile(_TARGET_PY):
        shutil.copy2(_TARGET_PY, os.path.join(archive, "tagger_dict_py_备份_%s.py" % stamp))
    with open(os.path.join(archive, "出厂词表_备份_%s.json" % stamp), "w", encoding="utf-8") as f:
        json.dump(_td_old.builtin_dict(), f, ensure_ascii=False, indent=2)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    with open(os.path.join(archive, "新出厂词表_%d标签_%s.json"
                           % (st["合计"], stamp)), "w", encoding="utf-8") as f:
        f.write(text)

    # ① 重写 app/tagger_dict.py
    with open(_TARGET_PY, "w", encoding="utf-8") as f:
        f.write(new_py)
    print("\n✓ 已重写 %s（%d 行）" % (os.path.relpath(_TARGET_PY, ROOT), new_py.count("\n") + 1))

    # ② 同步内嵌库 meta.tag_dict
    bak_db = _write_builtin_db(args.db, text, archive, stamp)
    print("✓ 已同步内嵌库 %s（旧库备份：%s）"
          % (os.path.relpath(args.db, ROOT), os.path.basename(bak_db) if bak_db else "无"))

    print("✓ 备份与存档目录：%s" % os.path.relpath(archive, ROOT))
    print("⚠ 提醒：① 需**重新打包** EXE，新装用户才是新出厂词表；"
          "② 已装老库不会自动跟随，点一次「↺ 恢复出厂…」即切换。")

    if not args.no_selftest:
        print("\n--- 写后自测：python -m app.tagger_dict ---")
        r = subprocess.run([sys.executable, "-m", "app.tagger_dict"], cwd=ROOT)
        print("--- 自测退出码：%d ---" % r.returncode)
        if r.returncode != 0:
            print("✗ 自测未通过 —— 请勿打包，先排查！")
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
