# -*- coding: utf-8 -*-
"""
hierarchy_import.py - 层级资源结构化（第 4 期 T1 离线"网上资源结构化"的数据层）
创建日期：2026-09-13（第 4 期 4-b）

职责（**不含界面**，供向导与自测调用）：
  1. **源解析**：粘贴文本（自动探测 Tab/逗号/分号/竖线）、CSV/TSV/TXT 文件、
     HTML 表格（`html.parser`，取最大表）、Markdown 表格 → 统一的 {headers, rows}
  2. **层级结构化**：按"4 列层级映射（项目类别/根目录/一级/二级）+ 空单元格继承上一行"
     把行数据整理成层级记录；**允许跳层**（缺层按兜底名补齐，保证条目在导航中可见）
  3. **字段映射**：源列 → 内置 10 字段 / 自定义字段（custom_*）/ 标签（可分隔为多个）
  4. **生成 JSON v5 数据文件**（与 `json_io.import_json` 完全对称：可直接保存、也可直接导入）
  5. **预览统计**：相对现有库统计"将新增 / 复用的层级"与"新增 / 判重跳过的条目"

设计约束：只用标准库；不改数据库结构；导入动作复用既有 `json_io.import_json`（判重/进度/事务一致）。
"""
import csv
import json
import os
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

from ..models import Entry
from . import json_io

# 层级键（顺序即"从上到下"；空单元格继承上一行时，上层变化会清空下层的继承值）
LAYER_KEYS = ("project", "domain", "l1", "l2")
LAYER_LABELS = {"project": "项目类别", "domain": "根目录",
                "l1": "一级分类", "l2": "二级分类"}

# 跳层兜底名（用户可在向导中改）
DEFAULT_FALLBACK_PROJECT = "网上资源收集"    # 与 config.PROJECT_PRESETS 之一一致
DEFAULT_FALLBACK_DOMAIN = "资源导入"
DEFAULT_FALLBACK_L1 = "未分组"
# 2026-09-20：二级分类兜底名默认留空＝不补齐（缺二级分类时条目直接挂一级下，保持原行为）
DEFAULT_FALLBACK_L2 = ""

# 内置 10 字段中可作为"映射目标"的载荷键（①条目标题即 name）
BUILTIN_ENTRY_KEYS = ("name", "intro", "origin", "features", "scenes", "works",
                      "image_desc", "prompt_cn", "prompt_en", "image_plan")
BUILTIN_LABELS = {
    "name": "① 条目名称", "intro": "② 介绍", "origin": "③ 溯源",
    "features": "④ 核心特征", "scenes": "⑤ 应用场景", "works": "⑥ 相关作品",
    "image_desc": "⑦ 代表配图描述", "prompt_cn": "⑧ 提示词（中文版）",
    "prompt_en": "⑨ 提示词（英文版）", "image_plan": "⑩ 图像获取方案",
}
TAG_TARGET = "__tag__"          # 字段映射中的"标签"目标（内部标记）
IGNORE_TARGET = ""              # 不映射（忽略该列）
# 链接类字段（2026-09-14，5-b）：抓取来源时自动补全相对链接的目标键
LINK_TARGETS = ("image_plan", "origin")

_SOURCE_EXTS = {
    ".csv": "delimited", ".tsv": "delimited", ".txt": "delimited",
    ".html": "html", ".htm": "html", ".md": "md", ".markdown": "md",
    ".json": "json",      # 2026-09-20 11:30：本地 JSON 源（含非本软件导出的第三方 JSON）
}


# ---------------------------------------------------------------------- #
# 一、源解析
# ---------------------------------------------------------------------- #
def _read_text(path: str) -> str:
    """读文本文件（utf-8-sig → utf-8 → gb18030 依次尝试）"""
    last = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError as exc:
            last = exc
    raise ValueError(f"文件编码无法识别（尝试 utf-8/gb18030）：{last}")


def _split_line(line: str, delim: str) -> List[str]:
    """按单个字符分隔符切一行（用 csv 处理引号；宽容处理多余列）"""
    try:
        return [c.strip() for c in next(csv.reader([line], delimiter=delim,
                                                  skipinitialspace=True))]
    except Exception:
        return [c.strip() for c in line.split(delim)]


def detect_delimiter(text: str) -> str:
    """自动探测分隔符：取前 20 行里出现次数最多者（Tab 优先）"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()][:20]
    if not lines:
        return "\t"
    counts = {d: sum(ln.count(d) for ln in lines) for d in ("\t", ",", ";", "|")}
    best, n = "\t", 0
    for d in ("\t", ",", ";", "|"):      # 同票时 Tab 优先
        if counts[d] > n:
            best, n = d, counts[d]
    return best


def split_delimited(text: str, delimiter: Optional[str] = None) -> dict:
    """把文本切成 {headers, rows, delimiter, total_rows}（首行为表头；掉空行）"""
    lines = [ln for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
             if ln.strip()]
    if not lines:
        return {"headers": [], "rows": [], "delimiter": delimiter or "\t", "total_rows": 0}
    delim = delimiter or detect_delimiter(text)
    rows = [_split_line(ln, delim) for ln in lines]
    return {"headers": rows[0], "rows": rows[1:],
            "delimiter": delim, "total_rows": max(0, len(rows) - 1)}


class _TableHTMLParser(HTMLParser):
    """抽取 HTML 里的所有 <table> → [[[cell,…], …], …]（不支持嵌套表）"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c for c in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_html_tables(text: str) -> List[List[List[str]]]:
    """解析 HTML 文本里的全部表格"""
    p = _TableHTMLParser()
    try:
        p.feed(text or "")
        p.close()
    except Exception:
        pass
    return p.tables


def parse_md_tables(text: str) -> List[List[List[str]]]:
    """解析 Markdown 表格（| a | b |；自动跳过 |---| 分隔行）"""
    tables, cur = [], []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and all(c and set(c) <= set("-: ") for c in cells):
                continue                      # 分隔行
            cur.append(cells)
        else:
            if cur:
                tables.append(cur)
                cur = []
    if cur:
        tables.append(cur)
    return tables


# ---- JSON 源解析（2026-09-20 09:40）---- #
# 背景：用户要求"本地 JSON 文件（非本软件导出）"也纳入第 1 步"选择源"的本地文件支持类型。
# 口径（用户确认的推荐方案）：对象数组取 key 并集 / 二维数组首行作表头 /
#                            嵌套对象拍平为点号路径 / 列表值合成一格。
def _json_cell(val) -> str:
    """JSON 叶子值 → 单元格文本（列表合成一格；布尔转 true/false）"""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float, str)):
        return str(val)
    if isinstance(val, list):
        return "、".join(x for x in (_json_cell(v) for v in val) if x)
    return json.dumps(val, ensure_ascii=False)


def _json_flatten(obj: dict, prefix: str = "") -> dict:
    """把嵌套对象拍平成 `a.b` 点号路径（列表/标量作为叶子）"""
    out = {}
    for k, v in (obj or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(_json_flatten(v, key + "."))
        else:
            out[key] = _json_cell(v)
    return out


def parse_json_records(text: str) -> dict:
    """JSON 文本 → {headers, rows, delimiter, total_rows}（容错解析，2026-09-20 09:40）

    - 顶层为数组元素是对象 → 表头 = 各对象键的**并集**（按首次出现顺序）；
      嵌套对象拍平为 `a.b`；列表值合成一格（"、"连接）；
    - 顶层为二维数组 → **首行作表头**，其余作数据（短行右侧补空）；
    - 顶层为对象 → 取其中**最长的数组值**再按上两条解析（如 {"data": [...]}）；
      无数组值则视为"单行对象"；
    - 无法构成 ≥2 列的表格时报 ValueError（界面会原样提示）。
    """
    try:
        data = json.loads(text or "")
    except Exception as exc:                       # noqa: BLE001
        raise ValueError(f"JSON 解析失败：{exc}")
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list) and v]
        data = max(lists, key=len) if lists else [data]
    if not isinstance(data, list) or not data:
        raise ValueError("JSON 结构无法作为表格：顶层需为非空数组（或含数组的对象）")
    if all(isinstance(x, list) for x in data):
        rows = [[_json_cell(c) for c in r] for r in data]
        ncol = max((len(r) for r in rows), default=0)
        if ncol < 2:
            raise ValueError("JSON 为二维数组但不足 2 列（需首行作表头、至少 2 列）")
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        headers = [rows[0][i] or f"列{i + 1}" for i in range(ncol)]
        body = rows[1:]
    else:
        objs = [_json_flatten(x) for x in data if isinstance(x, dict)]
        if not objs:
            raise ValueError("JSON 数组元素既非对象也非数组，无法转为表格")
        headers = []
        for o in objs:
            for k in o:
                if k not in headers:
                    headers.append(k)
        if len(headers) < 2:
            raise ValueError(f"JSON 仅解析出 {len(headers)} 个可用字段，"
                             "无法作为表格（至少需 2 列）")
        body = [[o.get(h, "") for h in headers] for o in objs]
    return {"headers": headers, "rows": body, "delimiter": "", "total_rows": len(body)}


def _table_to_source(table: List[List[str]], min_cols: int = 2) -> Optional[dict]:
    """二维表 → {headers, rows}（不足 min_cols 列的表视为无效）"""
    rows = [r for r in (table or []) if any((c or "").strip() for c in r)]
    if len(rows) < 2 or len(rows[0]) < min_cols:
        return None
    return {"headers": rows[0], "rows": rows[1:], "delimiter": "", "total_rows": len(rows) - 1}


def list_html_tables(text: str) -> List[dict]:
    """HTML 文本 → 全部**可用表格**（首行作表头、至少 2 列且 ≥1 行数据）。

    2026-09-14（5-b）：供向导"选表"下拉使用（比"只取最大表"更可控）。
    """
    out = []
    for tb in parse_html_tables(text):
        src = _table_to_source(tb)
        if src:
            out.append({"headers": src["headers"], "rows": src["rows"],
                        "total_rows": src["total_rows"]})
    return out


def list_md_tables(text: str) -> List[dict]:
    """Markdown 文本 → 全部**可用表格**（同上）"""
    out = []
    for tb in parse_md_tables(text):
        src = _table_to_source(tb)
        if src:
            out.append({"headers": src["headers"], "rows": src["rows"],
                        "total_rows": src["total_rows"]})
    return out


def pick_largest_table(tables: List[dict]) -> Optional[dict]:
    """从多张表里挑"列数最多、其次行数最多"的一张（选表下拉的默认项）"""
    cands = [t for t in (tables or []) if t and t.get("headers")]
    if not cands:
        return None
    return max(cands, key=lambda t: (max((len(r) for r in t["rows"]), default=0),
                                     t.get("total_rows", 0)))


def table_brief(idx: int, table: dict) -> str:
    """选表下拉用的简述：`表1（12 行 × 5 列）：一级分类 | 二级分类 | 名称 …`"""
    cols = len(table.get("headers") or [])
    head = " | ".join(str(h) for h in (table.get("headers") or [])[:4])
    more = " …" if cols > 4 else ""
    return f"表{idx}（{table.get('total_rows', 0)} 行 × {cols} 列）：{head}{more}"


# ---- GitHub 批量抓取：多文件合并（2026-09-14，5-c）---- #
def _batch_part_tables(name: str, text: str) -> List[dict]:
    """单个抓取文件的内容 → 表格列表（按文件名/内容判定 HTML 或 Markdown）"""
    low = (name or "").lower()
    if low.endswith((".html", ".htm")) or "<table" in (text or "").lower():
        return list_html_tables(text)
    return list_md_tables(text)


def merge_batch_parts(parts: List[dict], header_fmt: str = "# 来源：{name}") -> dict:
    """把多份抓取文本合并为一个"可解析的源"（5-c）。

    - parts：[{"name": 文件名, "text": 内容}]，顺序即用户勾选顺序；
    - **合并标记**：每段前插一行 `# 来源：<文件名>`（方案 4.3 的合并规则）；
    - **同构合并**：各文件表头一致时，额外给出一张"行已拼接"的**单张合并表**
      （否则只给多表/文本，并提示"建议逐文件分别处理"）。
    返回 {"text", "tables", "merged", "warnings"}。
    """
    marks, warnings, per = [], [], []
    for p in (parts or []):
        name = str(p.get("name") or "")
        text = str(p.get("text") or "")
        marks.append(header_fmt.format(name=name))
        marks.append(text)
        per.append((name, _batch_part_tables(name, text)))
    merged_text = "\n\n".join(marks)
    table_sets = [ts for _n, ts in per]
    if table_sets and all(table_sets):
        first = table_sets[0][0].get("headers") or []
        if all((ts[0].get("headers") or []) == first for ts in table_sets):
            rows = []
            for ts in table_sets:
                rows.extend(ts[0].get("rows") or [])
            table = {"headers": first, "rows": rows, "total_rows": len(rows)}
            return {"text": merged_text, "tables": [table], "merged": True,
                    "warnings": warnings}
        warnings.append(f"各文件表头不一致（共 {len(table_sets)} 张表）：已按多张表分别列出，"
                        "建议改用『逐文件分别处理』。")
        return {"text": merged_text, "tables": [], "merged": False, "warnings": warnings}
    no_tbl = [n for n, ts in per if not ts]
    if no_tbl:
        warnings.append("以下文件未识别出可用表格：" + "、".join(no_tbl[:6])
                        + ("…" if len(no_tbl) > 6 else ""))
    return {"text": merged_text, "tables": [], "merged": False, "warnings": warnings}


def parse_file(path: str) -> dict:
    """解析源文件（CSV/TSV/TXT / HTML / Markdown / JSON）→ {headers, rows, …}

    HTML/Markdown 取"列数最多、其次行数最多"的那张表；JSON 走容错解析（见 parse_json_records）；
    同时返回 tables_found 供界面提示。
    """
    ext = os.path.splitext(path or "")[1].lower()
    kind = _SOURCE_EXTS.get(ext)
    if kind is None:
        raise ValueError(f"不支持的源文件类型：{ext or '（无扩展名）'}"
                         "（支持 .csv/.tsv/.txt/.html/.htm/.md/.json）")
    text = _read_text(path)
    if kind == "delimited":
        out = split_delimited(text, "\t" if ext == ".tsv" else None)
        out["tables_found"] = 1
        out["kind"] = "delimited"
        return out
    if kind == "json":                     # 2026-09-20 09:40：本地 JSON 源（含第三方 JSON）
        out = parse_json_records(text)
        out["tables_found"] = 1
        out["kind"] = "json"
        return out
    tables = parse_html_tables(text) if kind == "html" else parse_md_tables(text)
    cands = [t for t in (_table_to_source(tb) for tb in tables) if t]
    if not cands:
        raise ValueError("未在该文件中找到可用的表格（需首行为表头、至少 2 列）")
    best = max(cands, key=lambda t: (max((len(r) for r in t["rows"]), default=0), t["total_rows"]))
    best["tables_found"] = len(tables)
    best["kind"] = kind
    return best


# ---------------------------------------------------------------------- #
# 二、层级结构化（4 列映射 + 空单元格继承上一行 + 允许跳层）
# ---------------------------------------------------------------------- #
def structure_rows(rows: List[list], mapping: dict,
                   inherit: bool = True,
                   fallbacks: Optional[dict] = None,
                   l2_rules: Optional[list] = None,
                   l2_source: Optional[int] = None,
                   other: str = "其他",
                   consts: Optional[dict] = None) -> Tuple[list, list]:
    """按层级映射把二维行整理为层级记录。

    - mapping：{layer_key: 列下标 或 None}（None=该层未映射，按兜底名补齐）；
    2026-09-20 14:20：新增 consts（可选，{layer_key: 固定名称}）——该层**整列固定**为该名称，
      优先级高于 mapping 与 fallbacks；用于向导"该层固定用已有选项 / 新建名称"。
    - inherit：空单元格继承上一行；**上层显式值变化时，清空下层继承值**（合并单元格语义）；
    - l2_rules（2026-09-14，5-d-1）：关键词规则 [(二级分类名, [关键词…])]，启用时**逐行覆盖**二级分类
      （先匹配先得；未命中归 `other`）；规则文本取 `l2_source` 列，未指定则取整行拼接；
    - 返回 (records, warnings)；records 元素 = (源行号, {project/domain/l1/l2, _fallback}, 原行)；
      `_fallback`（2026-09-14，5-b）标记该层是否用了兜底名，供"质量自检"面板统计。
    """
    fb = {"project": DEFAULT_FALLBACK_PROJECT, "domain": DEFAULT_FALLBACK_DOMAIN,
          "l1": DEFAULT_FALLBACK_L1, "l2": DEFAULT_FALLBACK_L2}   # 2026-09-20：l2 改取常量（单一来源）
    fb.update({k: v for k, v in (fallbacks or {}).items() if v is not None})
    # 2026-09-20 14:20：该层固定值（整列固定，优先级最高；见 docstring）
    fx = {k: v for k, v in (consts or {}).items() if v}
    last = {k: "" for k in LAYER_KEYS}
    records, warnings = [], []
    for rowno, row in enumerate(rows or [], 1):
        rec = {}
        used_fb = {k: False for k in LAYER_KEYS}
        for pos, key in enumerate(LAYER_KEYS):
            if fx.get(key):
                fixed = fx[key]
                if last.get(key, "") != fixed:
                    for lower in LAYER_KEYS[pos + 1:]:
                        last[lower] = ""      # 上层变了 → 下层继承值作废
                last[key] = fixed
                rec[key] = fixed
                continue
            col = mapping.get(key)
            raw = ""
            if col is not None and isinstance(col, int) and 0 <= col < len(row):
                raw = (row[col] or "").strip()
            if raw:
                if raw != last.get(key, ""):
                    for lower in LAYER_KEYS[pos + 1:]:
                        last[lower] = ""          # 上层变了 → 下层继承值作废
                last[key] = raw
                rec[key] = raw
            else:
                rec[key] = last.get(key, "") if inherit else ""
        if l2_rules:
            hay = cell(row, l2_source)
            if not hay:
                hay = " ".join(str(c or "") for c in (row or []))
            matched = apply_l2_rules(hay, l2_rules, other)
            rec["l2"] = matched
            rec["_l2_rule"] = matched
            rec["_l2_other"] = (matched == other)
        if not rec["l1"] and not rec["l2"]:
            warnings.append(f"第 {rowno} 行：缺少『一级/二级分类』，已跳过")
            continue
        if not rec["l1"]:
            rec["l1"] = fb["l1"]
            used_fb["l1"] = True
            warnings.append(f"第 {rowno} 行：缺『一级分类』→ 归入『{fb['l1']}』")
        # 2026-09-20：补『二级分类』兜底（原实现无此分支；fb["l2"] 默认为空＝不补齐，保持原行为）
        if not rec["l2"] and fb["l2"]:
            rec["l2"] = fb["l2"]
            used_fb["l2"] = True
            warnings.append(f"第 {rowno} 行：缺『二级分类』→ 归入『{fb['l2']}』")
        if not rec["domain"]:
            rec["domain"] = fb["domain"]
            used_fb["domain"] = True
        if not rec["project"]:
            rec["project"] = fb["project"]
            used_fb["project"] = True
        rec["_fallback"] = used_fb
        records.append((rowno, rec, row))
    return records, warnings


def cell(row: list, col) -> str:
    """安全取单元格文本"""
    if col is None or not isinstance(col, int) or col < 0 or col >= len(row or []):
        return ""
    return (row[col] or "").strip()


# ---- 5-d 增强（2026-09-14）：关键词规则分类 / 行筛选 / 中英分流 ---- #
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LETTER_RE = re.compile(r"[A-Za-z]")


def looks_english_prompt(text: str) -> bool:
    """判定提示词"以英文为主"（历史规则：无汉字即英文；或 字母数 > 汉字数×3 且 字母 > 40）"""
    s = str(text or "")
    cjk = len(_CJK_RE.findall(s))
    letters = len(_LETTER_RE.findall(s))
    if not letters:
        return False
    if cjk == 0:
        return True
    return letters > cjk * 3 and letters > 40


def parse_l2_rules(text: str) -> Tuple[list, list]:
    """解析"关键词规则 → 二级分类"文本 → (rules, errors)。

    每行：`二级分类名 = 关键词1, 关键词2`（`=` 也可写 `＝` / `:` / `：`）；
    `#` 或 `//` 开头为注释，空行忽略；rules 保序（**先匹配先得**）。
    """
    rules, errors = [], []
    for i, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        seg = re.split(r"[:：=＝]", line, maxsplit=1)
        if len(seg) != 2:
            errors.append(f"第 {i} 行：缺少分隔符（应形如『二级分类名 = 关键词1, 关键词2』）")
            continue
        name = seg[0].strip()
        kws = [k.strip() for k in re.split(r"[,，、;；|]+", seg[1]) if k.strip()]
        if not name:
            errors.append(f"第 {i} 行：二级分类名为空")
            continue
        if not kws:
            errors.append(f"第 {i} 行：『{name}』没有关键词")
            continue
        rules.append((name, kws))
    return rules, errors


def apply_l2_rules(text: str, rules: list, other: str = "其他") -> str:
    """按规则确定二级分类名（**先匹配先得**；未命中返回 other）"""
    hay = str(text or "")
    if not hay.strip():
        return other
    low = hay.lower()
    for name, kws in (rules or []):
        for kw in kws:
            if kw and kw.lower() in low:
                return name
    return other


def split_filter_keywords(text: str) -> List[str]:
    """筛选关键词 → 列表（逗号/顿号/分号/竖线/空白分隔）"""
    return [k.strip() for k in re.split(r"[,，、;；|\s]+", str(text or "")) if k.strip()]


def filter_rows(rows: List[list], include: str = "", exclude: str = "") -> Tuple[list, dict]:
    """行筛选：整行文本命中"包含关键词"（任一）才保留；命中"排除关键词"（任一）即剔除。

    返回 (保留的行, 统计)；统计含 in_total/kept/dropped_in/dropped_ex/include/exclude。
    """
    inc = split_filter_keywords(include)
    exc = split_filter_keywords(exclude)
    out = []
    dropped_in = dropped_ex = 0
    for row in (rows or []):
        text = " ".join(str(c or "") for c in (row or [])).lower()
        if inc and not any(k.lower() in text for k in inc):
            dropped_in += 1
            continue
        if exc and any(k.lower() in text for k in exc):
            dropped_ex += 1
            continue
        out.append(row)
    return out, {"in_total": len(rows or []), "kept": len(out),
                 "dropped_in": dropped_in, "dropped_ex": dropped_ex,
                 "include": inc, "exclude": exc}


# ---------------------------------------------------------------------- #
# 三、生成 JSON v5 载荷（可直接保存 / 可直接交 json_io.import_json 导入）
# ---------------------------------------------------------------------- #
def _split_tags(text: str) -> List[str]:
    """标签列拆分（逗号/顿号/分号/斜杠/竖线均可），去重保序"""
    out = []
    for t in re.split(r"[,，、;；/|]+", text or ""):
        t = t.strip()
        if t and t not in out:
            out.append(t)
    return out


def _looks_like_link(val: str) -> bool:
    """保守判断"像链接/相对路径"（避免把"小红书号xxx"这类纯文本误补成 URL）"""
    s = (val or "").strip()
    if not s or any(ch.isspace() for ch in s):
        return False
    if s.startswith(("http://", "https://", "//", "/", "./", "../")):
        return True
    return bool(re.match(r"^[\w\-./%]+\.(jpg|jpeg|png|gif|webp|svg|bmp|md|json|csv|html?|txt)"
                         r"(\?|#|$)", s, re.I))


def build_v5_payload(db, records: list, field_map: dict,
                     source_name: str = "",
                     link_base: str = "",
                     link_stat: Optional[dict] = None,
                     split_lang: bool = False,
                     split_stat: Optional[dict] = None) -> Tuple[dict, list]:
    """按层级记录 + 字段映射生成 **JSON v5 载荷**（与 json_io 完全对称）。

    - field_map：{列下标: 目标}；目标 ∈ 内置载荷键 / TAG_TARGET（标签）/ custom_xxx；
    - link_base / link_stat（2026-09-14，5-b）：给定基准 URL 时，把映射到
      **链接类字段（⑩图像获取方案 / ③溯源）**且"像链接"的值**补全为绝对 URL**（含 blob→raw），
      并把补全条数写入 `link_stat["completed"]`；
    - split_lang / split_stat（2026-09-14，5-d-3）：启用"中英自动分流"时，映射到 **⑧中文版**的列
      若判定"以英文为主"（`looks_english_prompt`）→ 改写进 **⑨英文版**，并把条数写入 `split_stat["moved"]`；
    - 返回 (payload, warnings)；payload 含 projects/domains/domain_links/categories/
      entries 及 field_defs（保证自定义字段定义随包）。
    """
    from . import fetcher                      # 局部导入：避免与抓取层的循环依赖风险

    projects, domains = {}, {}
    links, cat_seen = {}, {}
    cat_order = []
    entries, warnings = [], []
    for rowno, rec, row in records:
        projects.setdefault(rec["project"], 0)
        domains[rec["domain"]] = rec["project"]
        links.setdefault(rec["domain"], [])
        if rec["l1"] not in links[rec["domain"]]:
            links[rec["domain"]].append(rec["l1"])
        for lvl, parent in (("l1", None), ("l2", rec["l1"])):
            name = rec.get(lvl) or ""
            if not name:
                continue
            key = (parent, name)
            if key not in cat_seen:
                cat_seen[key] = len(cat_order)
                cat_order.append({"parent": parent, "name": name, "sort_order": 0})
        ep = {"name": "", "path": [x for x in (rec["l1"], rec["l2"]) if x]}
        cf, tags = {}, []
        for col, target in (field_map or {}).items():
            val = cell(row, col)
            if not val or not target:
                continue
            if target == TAG_TARGET:
                tags.extend(_split_tags(val))
            elif target in BUILTIN_ENTRY_KEYS:
                if (link_base and target in LINK_TARGETS
                        and _looks_like_link(val)):
                    fixed = fetcher.complete_url(link_base, val)
                    if fixed and fixed != val:
                        val = fixed
                        if link_stat is not None:
                            link_stat["completed"] = int(link_stat.get("completed", 0)) + 1
                if split_lang and target == "prompt_cn" and looks_english_prompt(val):
                    target = "prompt_en"
                    if split_stat is not None:
                        split_stat["moved"] = int(split_stat.get("moved", 0)) + 1
                ep[target] = (ep.get(target, "") + ("\n" if ep.get(target) else "") + val)
            elif str(target).startswith("custom_"):
                cf[target] = (cf.get(target, "") + ("\n" if cf.get(target) else "") + val)
        if tags:
            ep["tags"] = list(dict.fromkeys(tags))
        if cf:
            ep["custom_fields"] = cf
        if not ep.get("name"):
            guess = (ep.get("intro") or ep.get("prompt_cn") or "").strip().replace("\n", " ")
            ep["name"] = (guess[:30] + "…") if len(guess) > 30 else (guess or "（未命名）")
            warnings.append(f"第 {rowno} 行：未映射『① 条目名称』，已按内容取名『{ep['name']}』")
        entries.append(ep)
    # 项目/根目录排序按出现顺序
    payload = {
        "version": json_io.JSON_VERSION,
        "type": "full",
        "source": source_name or "",
        "projects": [{"name": n, "sort_order": i} for i, n in enumerate(projects)],
        "domain_projects": dict(domains),
        "domains": [{"name": n, "sort_order": i} for i, n in enumerate(domains)],
        "domain_links": links,
        "categories": cat_order,
        "entries": entries,
    }
    try:
        payload["field_defs"] = json_io._field_defs_payload(db)
    except Exception:
        payload["field_defs"] = []
    return payload, warnings


def write_v5_file(payload: dict, path: str) -> None:
    """把载荷写成 JSON v5 数据文件（可直接被"导入 JSON 备份"读取）"""
    import json as _json
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(payload, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------- #
# 四、预览统计（相对现有库：层级新增/复用；条目新增/判重跳过）
# ---------------------------------------------------------------------- #
def _entry_from_payload(ep: dict) -> Entry:
    """按载荷构造 Entry（供判重用；与 json_io.import_json 的字段口径一致）"""
    return Entry(
        category_id=None,
        name=ep.get("name", ""), intro=ep.get("intro", ""), origin=ep.get("origin", ""),
        features=ep.get("features", ""), scenes=ep.get("scenes", ""),
        works=ep.get("works", ""), image_desc=ep.get("image_desc", ""),
        prompt_cn=ep.get("prompt_cn", ""), prompt_en=ep.get("prompt_en", ""),
        image_plan=ep.get("image_plan", ""),
        is_favorite=int(ep.get("is_favorite", 0) or 0))


def resolve_category_by_path(db, path: list) -> Optional[int]:
    """按名称路径定位**已存在**的分类（最长回退）；不存在返回 None"""
    names = [str(x).strip() for x in (path or []) if str(x).strip()]
    for n in range(len(names), 0, -1):
        cid = db.find_category_by_chain(names[:n])
        if cid:
            return cid
    return None


def preview_stats(db, payload: dict) -> dict:
    """统计（只读）：新增/复用 的项目·根目录·分类，以及新增/判重跳过的条目"""
    from ..database import Database      # 局部导入，避免循环依赖
    existing_projects = {p["name"] for p in db.list_projects()}
    existing_domains = {d["name"] for d in db.list_domains()}
    stat = {
        "projects_new": sum(1 for p in payload.get("projects", [])
                            if p["name"] not in existing_projects),
        "projects_reuse": sum(1 for p in payload.get("projects", [])
                              if p["name"] in existing_projects),
        "domains_new": sum(1 for d in payload.get("domains", [])
                           if d["name"] not in existing_domains),
        "domains_reuse": sum(1 for d in payload.get("domains", [])
                             if d["name"] in existing_domains),
        "cats_new": 0, "cats_reuse": 0,
        "entries_new": 0, "entries_skip": 0, "entries_total": 0,
    }
    # 分类：按"父先子后"重建路径，再看库里有没有
    seen_paths = set()
    for c in payload.get("categories", []):
        parent = c.get("parent")
        chain = ([parent] if parent else []) + [c["name"]]
        if parent and tuple(chain) not in seen_paths:
            # 父级路径不确定时用链首定位（简化：层级最多 2 层分类，方案口径）
            pass
        seen_paths.add(tuple(chain))
        if db.find_category_by_chain(chain):
            stat["cats_reuse"] += 1
        else:
            stat["cats_new"] += 1
    # 条目：按"目标分类内 content_key 判重"（与该分类现有条目比较，逐条累加）
    keys_by_cat = {}
    for ep in payload.get("entries", []):
        stat["entries_total"] += 1
        cid = resolve_category_by_path(db, ep.get("path") or [])
        if cid not in keys_by_cat:
            existing = db.list_uncategorized() if cid is None else db.list_entries(cid)
            keys_by_cat[cid] = {Database.content_key(x) for x in existing}
        k = Database.content_key(_entry_from_payload(ep))
        if k in keys_by_cat[cid]:
            stat["entries_skip"] += 1
        else:
            keys_by_cat[cid].add(k)
            stat["entries_new"] += 1
    return stat


def summarize(stats: dict) -> str:
    """把统计拼成一行中文摘要（界面显示用）"""
    return (f"层级：项目类别 新增 {stats['projects_new']} / 复用 {stats['projects_reuse']}，"
            f"根目录 新增 {stats['domains_new']} / 复用 {stats['domains_reuse']}，"
            f"分类 新增 {stats['cats_new']} / 复用 {stats['cats_reuse']}；"
            f"条目：共 {stats['entries_total']} 条 → 将新增 {stats['entries_new']}、"
            f"判重跳过 {stats['entries_skip']}")


# ---------------------------------------------------------------------- #
# 五、质量自检（2026-09-14，5-b：源自历史教训——正则漏项致整列空、父级缺失落未分类）
# ---------------------------------------------------------------------- #
def quality_report(db, payload: dict, *, records: Optional[list] = None,
                   rows_total: int = 0, stats: Optional[dict] = None,
                   link_stat: Optional[dict] = None,
                   filter_stat: Optional[dict] = None,
                   split_stat: Optional[dict] = None,
                   l2_rule_on: bool = False) -> dict:
    """生成"导入前质量自检"数据（只读，不改任何数据）。

    返回：
      entries_total / empty（内置字段空值计数）/ custom_empty（自定义字段空值计数）/
      duplicates（同分类同名）/ fallback（兜底层级使用行数：project/domain/l1）/
      rows_total / rows_valid（有效行）/ stats（新增·判重）/ link（补全条数）
    """
    ents = payload.get("entries", []) or []
    total = len(ents)
    empty = {}
    for k in BUILTIN_ENTRY_KEYS:
        empty[k] = sum(1 for e in ents if not str(e.get(k) or "").strip())
    custom_empty = {}
    for e in ents:
        for fk, v in (e.get("custom_fields") or {}).items():
            custom_empty.setdefault(fk, 0)
            if not str(v or "").strip():
                custom_empty[fk] += 1
    seen = {}
    for e in ents:
        key = (tuple(e.get("path") or []), str(e.get("name") or ""))
        seen[key] = seen.get(key, 0) + 1
    duplicates = [{"path": list(p), "name": n, "count": c}
                  for (p, n), c in seen.items() if c > 1]
    duplicates.sort(key=lambda d: -d["count"])
    fb_counts = {"project": 0, "domain": 0, "l1": 0, "l2": 0}   # 2026-09-20：补 l2（兜底统计）
    l2_other = 0
    if records:
        for item in records:
            rec = item[1] if len(item) > 1 else {}
            fl = rec.get("_fallback") or {}
            for k in fb_counts:
                if fl.get(k):
                    fb_counts[k] += 1
            if rec.get("_l2_other"):
                l2_other += 1
    return {"entries_total": total, "empty": empty, "custom_empty": custom_empty,
            "duplicates": duplicates[:8], "duplicates_count": len(duplicates),
            "fallback": fb_counts, "rows_total": int(rows_total or 0),
            "rows_valid": len(records or []), "stats": stats or {},
            "link": link_stat or {}, "filter": filter_stat or {}, "split": split_stat or {},
            "l2_other": l2_other, "l2_rule_on": bool(l2_rule_on)}


def format_quality_report(rep: dict, custom_names: Optional[dict] = None) -> str:
    """把质量自检结果拼成"人类可读"的多行文本（界面面板显示用）"""
    lines = []
    st = rep.get("stats") or {}
    fl_ = rep.get("filter") or {}
    filt = ""
    if fl_ and (fl_.get("include") or fl_.get("exclude")):
        filt = (f"｜关键词筛选：保留 {fl_.get('kept', 0)} / {fl_.get('in_total', 0)}"
                f"（不含关键词剔除 {fl_.get('dropped_in', 0)}、命中排除词剔除"
                f" {fl_.get('dropped_ex', 0)}）")
    lines.append("① 数量对账："
                 f"源行数 {rep.get('rows_total', 0)} → 有效 {rep.get('rows_valid', 0)}"
                 f"（跳过 {max(rep.get('rows_total', 0) - rep.get('rows_valid', 0), 0)}）"
                 f" → 条目 {rep.get('entries_total', 0)}；"
                 f"将新增 {st.get('entries_new', '—')}、判重跳过 {st.get('entries_skip', '—')}"
                 + filt)
    empty = rep.get("empty") or {}
    total = rep.get("entries_total", 0)
    parts = [f"{BUILTIN_LABELS.get(k, k)} {v}" for k, v in empty.items() if v]
    lines.append("② 空值统计：" + ("；".join(parts) if parts else "全部字段均有值 ✅")
                 + (f"（共 {total} 条，仅列出 >0 的字段）" if parts else ""))
    cust = rep.get("custom_empty") or {}
    if cust:
        names = custom_names or {}
        lines.append("　　自定义字段空值：" + "；".join(
            f"{(names.get(k) or k)} {v}" for k, v in cust.items()))
    dup = rep.get("duplicates") or []
    if dup:
        examples = "；".join(
            f"{'/'.join(d['path'])}：{d['name']} ×{d['count']}" for d in dup[:3])
        lines.append(f"③ 同分类重名：{rep.get('duplicates_count', 0)} 处（示例：{examples}）")
    else:
        lines.append("③ 同分类重名：无 ✅")
    fb = rep.get("fallback") or {}
    fb_parts = [f"{LAYER_LABELS.get(k, k)} {v} 行" for k, v in fb.items() if v]
    lines.append("④ 兜底层级：" + ("；".join(fb_parts) if fb_parts else "未使用兜底 ✅"))
    link = rep.get("link") or {}
    if link:
        lines.append(f"⑤ 链接补全：已补全 {int(link.get('completed', 0))} 条"
                     + (f"（基准：{link.get('base')}）" if link.get("base") else ""))
    split = rep.get("split") or {}
    if split:
        lines.append(f"⑥ 中英分流：已把 {int(split.get('moved', 0))} 条英文为主的提示词写入『⑨ 英文版』")
    if "l2_other" in rep:
        n_other = int(rep.get("l2_other", 0))
        tot = rep.get("entries_total", 0) or 0
        if rep.get("l2_rule_on"):
            pct = (n_other / tot * 100) if tot else 0
            lines.append(f"⑦ 规则分流：按关键词规则归入二级分类，其中『其他』{n_other} 条"
                         f"（占 {pct:.1f}%）")
        elif n_other:
            lines.append(f"⑦ 规则分流：『其他』{n_other} 条")
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 自测（python -m app.parser.hierarchy_import）
# ---------------------------------------------------------------------- #
def _hierarchy_selftest() -> None:
    """4-b 自测：源解析（粘贴/CSV/HTML/MD）+ 层级继承与跳层 + 载荷生成 + 统计 + 真机导入往返"""
    import json as _json
    import shutil
    import tempfile

    from ..database import Database

    tmp = tempfile.mkdtemp(prefix="promptsprite_hier_")
    try:
        # ---------- 1. 粘贴文本：自动探测分隔符 ----------
        paste_tab = "项目类别\t根目录\t一级分类\t二级分类\t名称\t提示词\t标签\n" \
                    "网上资源收集\t图像\t人像\t写实人像\t案例A\tP-A\t写实,电影感\n" \
                    "\t\t\t\t案例B\tP-B\t写实\n" \
                    "\t\t\t胶片人像\t案例C\tP-C\t\n" \
                    "\t视频\t运镜\t推轨\t案例D\tP-D\t\n"
        src = split_delimited(paste_tab)
        assert src["delimiter"] == "\t" and src["total_rows"] == 4
        assert src["headers"][0] == "项目类别" and len(src["headers"]) == 7
        paste_csv = "a,b,c\n1,2,3\n4,5,6\n"
        assert split_delimited(paste_csv)["delimiter"] == ","

        # ---------- 2. 层级结构化：继承 + 上层变化清空 + 跳层兜底 ----------
        mapping = {"project": 0, "domain": 1, "l1": 2, "l2": 3}
        recs, warns = structure_rows(src["rows"], mapping)
        assert len(recs) == 4, recs
        r1, r2, r3, r4 = (r[1] for r in recs)
        assert (r1["project"], r1["domain"], r1["l1"], r1["l2"]) \
            == ("网上资源收集", "图像", "人像", "写实人像")
        assert (r2["l1"], r2["l2"]) == ("人像", "写实人像"), "空单元格应继承上一行"
        assert (r3["l1"], r3["l2"]) == ("人像", "胶片人像"), "只改二级"
        assert (r4["project"], r4["domain"], r4["l1"], r4["l2"]) \
            == ("网上资源收集", "视频", "运镜", "推轨"), "改上层应清空下层继承"
        # 跳层：只映射"二级"（无一级）→ 兜底一级；未映射根目录 → 兜底根目录
        recs2, warns2 = structure_rows(
            [["一级A", "二级B"], ["", "二级C"]], {"l2": 1})
        assert [r[1]["l1"] for r in recs2] == ["未分组", "未分组"]
        assert recs2[0][1]["domain"] == "资源导入"
        assert recs2[0][1]["project"] == "网上资源收集"
        assert any("归入" in w for w in warns2)

        # ---------- 3. HTML / Markdown 表格解析 ----------
        html = ("<html><body><table><tr><th>甲</th><th>乙</th></tr>"
                "<tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>"
                "<table><tr><th>a</th></tr></table></body></html>")
        assert parse_html_tables(html)[0][0] == ["甲", "乙"]
        md = "说明文字\n\n| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
        assert parse_md_tables(md)[0][0] == ["甲", "乙"]
        assert parse_md_tables(md)[0][2] == ["3", "4"]

        # ---------- 4. 载荷生成 + 统计 + 真机导入往返 ----------
        db = Database(os.path.join(tmp, "a.db"))
        try:
            key = db.add_field_def("来源链接", "text")
            field_map = {4: "name", 5: "prompt_cn", 6: TAG_TARGET, 3: key}
            payload, warns3 = build_v5_payload(db, recs, field_map, source_name="自测")
            assert payload["version"] == json_io.JSON_VERSION
            assert [p["name"] for p in payload["projects"]] == ["网上资源收集"]
            assert payload["domain_projects"]["图像"] == "网上资源收集"
            assert payload["domain_links"]["图像"] == ["人像"]
            names = [c["name"] for c in payload["categories"]]
            assert names.index("人像") < names.index("写实人像") < names.index("胶片人像")
            assert [c["parent"] for c in payload["categories"] if c["name"] == "写实人像"] == ["人像"]
            e1, e3 = payload["entries"][0], payload["entries"][2]
            assert e1["name"] == "案例A" and e1["path"] == ["人像", "写实人像"]
            assert e1["tags"] == ["写实", "电影感"] and e1["custom_fields"][key] == "写实人像"
            assert e3["path"] == ["人像", "胶片人像"] and "tags" not in e3
            assert any(d["field_key"] == key for d in payload["field_defs"])
            assert any("未映射『① 条目名称』" in w for w in warns3) is False   # 名称已映射
            st = preview_stats(db, payload)
            assert st["entries_new"] == 4 and st["entries_skip"] == 0
            # 项目类别/根目录可能已有预置项（视频/图像 等）→ 只校验"新增+复用"总数
            assert st["projects_new"] + st["projects_reuse"] == 1
            assert st["domains_new"] + st["domains_reuse"] == 2
            assert st["cats_new"] == 5            # 人像/写实人像/胶片人像/运镜/推轨
            # 写成文件 → 用既有导入器导入（真机往返）
            out = os.path.join(tmp, "wizard.json")
            write_v5_file(payload, out)
            res = json_io.import_json(db, out)
            assert res["entries"] == 4 and res["skipped"] == 0
            cid = db.find_category_by_chain(["人像", "胶片人像"])
            assert cid and [x["name"] for x in db.list_entries(cid)] == ["案例C"]
            # 再次预览统计 → 全部判重跳过
            st2 = preview_stats(db, payload)
            assert st2["entries_new"] == 0 and st2["entries_skip"] == 4
            assert st2["cats_new"] == 0 and st2["cats_reuse"] == 5
            assert st2["domains_new"] == 0 and st2["projects_new"] == 0
            # 名称未映射时的兜底命名
            p2, w2 = build_v5_payload(db, recs, {5: "prompt_cn"})
            assert p2["entries"][0]["name"] == "P-A" and w2
            assert summarize(st) .startswith("层级：项目类别")
        finally:
            db.close()

        # ---------- 5. 文件解析（CSV 落盘 → parse_file）----------
        csv_path = os.path.join(tmp, "s.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("一级,二级,名称\nA,B,条1\n,,条2\n")
        got = parse_file(csv_path)
        assert got["kind"] == "delimited" and got["total_rows"] == 2
        md_path = os.path.join(tmp, "s.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("| 一级 | 二级 | 名称 |\n|---|---|---|\n| A | B | 条1 |\n")
        assert parse_file(md_path)["headers"] == ["一级", "二级", "名称"]
        html_path = os.path.join(tmp, "s.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        assert parse_file(html_path)["headers"] == ["甲", "乙"]
        try:
            parse_file(os.path.join(tmp, "x.xlsx"))
            raise AssertionError("不支持的扩展名应报错")
        except ValueError:
            pass

        # ---------- 6. 5-b 新增能力：选表 / 兜底标记 / 链接补全 / 质量自检 ----------
        tables = list_html_tables(
            "<table><tr><th>甲</th><th>乙</th></tr><tr><td>1</td><td>2</td></tr></table>"
            "<table><tr><th>a</th><th>b</th><th>c</th></tr>"
            "<tr><td>1</td><td>2</td><td>3</td></tr></table>")
        assert len(tables) == 2
        best = pick_largest_table(tables)
        assert best["headers"] == ["a", "b", "c"]
        assert table_brief(2, best).startswith("表2（1 行 × 3 列）：a | b | c")
        assert len(list_md_tables(md)) == 1

        recs3, _w3 = structure_rows([["X", "Y", "Z"]], {"l1": 0}, fallbacks={"domain": "D0"})
        assert recs3[0][1]["_fallback"] == {"project": True, "domain": True,
                                           "l1": False, "l2": False}, recs3[0][1]

        # 链接补全（仅"像链接"的值才补；纯文本不动）
        lstat = {}
        payload3, _w4 = build_v5_payload(
            None,                       # 不落库：本段只验"链接补全"与"质量自检"
            structure_rows([["一级Z", "二级Z", "条L", "assets/p1.jpg", "小红书号abc"]],
                           {"l1": 0, "l2": 1})[0],
            {2: "name", 3: "image_plan", 4: "origin"},
            link_base="https://raw.githubusercontent.com/o/r/main/docs/x.md",
            link_stat=lstat)
        e = payload3["entries"][0]
        assert e["image_plan"] == "https://raw.githubusercontent.com/o/r/main/docs/assets/p1.jpg"
        assert e["origin"] == "小红书号abc", "纯文本不应被补成 URL"
        assert lstat.get("completed") == 1
        # 质量自检
        rep = quality_report(None, payload3, records=None, rows_total=5,
                             stats={"entries_new": 1, "entries_skip": 0}, link_stat=lstat)
        txt = format_quality_report(rep)
        assert "① 数量对账" in txt and "② 空值统计" in txt and "③ 同分类重名" in txt
        assert "④ 兜底层级" in txt and "⑤ 链接补全" in txt
        rep2 = quality_report(None, {"entries": [{"name": "A", "path": ["p", "q"]},
                                                 {"name": "A", "path": ["p", "q"]}]},
                              records=recs3, rows_total=3)
        assert rep2["duplicates_count"] == 1 and rep2["duplicates"][0]["count"] == 2
        assert rep2["fallback"]["project"] == 1 and rep2["fallback"]["l1"] == 0
        assert rep2["empty"]["prompt_cn"] == 2

        # ---------- 7. 5-c：GitHub 多文件合并（同构合并 / 结构差异 / 合并标记）----------
        md_a = "| 一级分类 | 二级分类 | 名称 |\n|---|---|---|\n| 人像 | 写实 | 条1 |\n"
        md_b = "| 一级分类 | 二级分类 | 名称 |\n|---|---|---|\n| 风光 | 夜景 | 条2 |\n"
        merged = merge_batch_parts([{"name": "part-1.md", "text": md_a},
                                    {"name": "part-2.md", "text": md_b}])
        assert merged["merged"] is True and not merged["warnings"]
        assert "# 来源：part-1.md" in merged["text"] and "# 来源：part-2.md" in merged["text"]
        assert merged["tables"][0]["headers"] == ["一级分类", "二级分类", "名称"]
        assert merged["tables"][0]["total_rows"] == 2          # 同表头 → 行已拼接
        md_c = "| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n"
        diff = merge_batch_parts([{"name": "a.md", "text": md_a},
                                  {"name": "b.md", "text": md_c}])
        assert diff["merged"] is False and not diff["tables"]
        assert any("表头不一致" in w and "逐文件" in w for w in diff["warnings"])
        none_ok = merge_batch_parts([{"name": "c.md", "text": "没有表格的纯文本"}])
        assert none_ok["merged"] is False and any("未识别出可用表格" in w
                                                 for w in none_ok["warnings"])

        # ---------- 8. 5-d 增强：关键词规则 / 行筛选 / 中英分流 ----------
        assert looks_english_prompt("a cinematic portrait of an old fisherman") is True
        assert looks_english_prompt("电影感的人像提示词") is False
        assert looks_english_prompt("") is False
        assert looks_english_prompt(
            "A " + "very " * 20 + "detailed photograph of a harbor at dawn") is True
        rules, errs = parse_l2_rules(
            "# 注释行\n写实人像 = 写实, 摄影\n胶卷质感 = 胶片, 菲林\n坏行没有等号\n空名 = \n")
        assert [r[0] for r in rules] == ["写实人像", "胶卷质感"] and len(errs) == 2
        assert apply_l2_rules("写实风格的人像", rules) == "写实人像"     # 先匹配先得
        assert apply_l2_rules("电影感画面", rules) == "其他"
        assert apply_l2_rules("写实与胶片", rules) == "写实人像", "先匹配先得（第一行优先）"

        # 规则启用时：逐行覆盖二级分类，未命中归"其他"
        recs_r, _wr = structure_rows([["人像", "标题-写实写真"], ["人像", "标题-无关键词"]],
                                     {"l1": 0}, l2_rules=rules, l2_source=1)
        assert [r[1]["l2"] for r in recs_r] == ["写实人像", "其他"]
        assert recs_r[1][1]["_l2_other"] is True and recs_r[0][1]["_l2_other"] is False

        # 行筛选：包含任一保留、排除任一剔除
        rows_f, fstat = filter_rows(
            [["人像", "写实条"], ["风光", "夜景条"], ["人像", "广告条"]],
            include="人像", exclude="广告")
        assert [r[1] for r in rows_f] == ["写实条"]
        assert fstat == {"in_total": 3, "kept": 1, "dropped_in": 1, "dropped_ex": 1,
                         "include": ["人像"], "exclude": ["广告"]}
        assert filter_rows([["甲"]], include="乙")[1]["kept"] == 0

        # 中英分流：映射到 ⑧ 的英文内容 → 写入 ⑨
        sstat = {}
        payload_s, _ws = build_v5_payload(
            None,
            structure_rows([["一级S", "二级S", "英文条", "Snowy mountain, ultra detailed"],
                            ["一级S", "二级S", "中文条", "雪山日出，细节丰富"]],
                           {"l1": 0, "l2": 1})[0],
            {2: "name", 3: "prompt_cn"}, split_lang=True, split_stat=sstat)
        en = [e for e in payload_s["entries"] if e["name"] == "英文条"][0]
        cn = [e for e in payload_s["entries"] if e["name"] == "中文条"][0]
        assert en.get("prompt_en") and not en.get("prompt_cn") and sstat["moved"] == 1
        assert cn.get("prompt_cn") == "雪山日出，细节丰富" and not cn.get("prompt_en")

        # 质量自检：筛选/分流/规则"其他"三项在面板里的呈现
        rep3 = quality_report(None, payload_s, records=recs_r, rows_total=3,
                              filter_stat=fstat, split_stat=sstat, l2_rule_on=True)
        txt3 = format_quality_report(rep3)
        assert "关键词筛选：保留 1 / 3" in txt3
        assert "⑥ 中英分流：已把 1 条" in txt3
        assert "⑦ 规则分流" in txt3 and "『其他』1 条" in txt3

        print("[层级导入] 源解析（粘贴·CSV·HTML·MD）/层级继承与上层清空/跳层兜底/"
              "字段映射（内置·自定义·标签）/载荷与统计/写文件并真机导入往返/判重幂等/"
              "选表·兜底标记·链接补全·质量自检/批量合并（同构·差异·标记）/"
              "5-d（规则分二级·行筛选·中英分流） 通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _hierarchy_selftest()
