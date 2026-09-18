# -*- coding: utf-8 -*-
"""
tagger.py - 标签词表的存取 / 校验 / 导入导出（阶段 0.5，2026-09-14）

职责（**纯数据层，无界面**）：
  1. `load_dict(db)`   读取运行时词表；meta 无值或损坏时用出厂种子初始化（损坏数据先备份不丢）；
  2. `save_dict(db, data)`  校验 → 规范化 → 写入 meta；
  3. `validate_dict(data)` / `normalize_dict(data)` / `dict_summary(data)` / `count_tags(data)`；
  4. `reset_dict(db)`  恢复出厂词表；
  5. `export_dict(db, path)` / `import_dict(db, path)`  JSON 导出/导入（导入前自动备份当前词表）；
     2026-09-17（用户需求）新增 `merge_dict(db, path)`：JSON **增量合并**（只增不删，与"整体替换"并存）；
  6. `read_dict_file(path)` / `write_dict_file(path, data)`  纯文件读写（供导入导出复用）。

设计要点（对应《可行性研究报告 v3》§4）：
  - **权威数据在 meta 表**（键 `config.META_TAG_DICT`）；出厂种子见 `app/tagger_dict.py`；
  - **加一个大类 = 加一个键**：`domains` 下加一个领域包（并在 `universal.领域`、`domain_map` 各加一项）；
  - **零新增表、零 schema 变更、零新增依赖**（只用标准库 json/os）；
  - 导入严格校验：**结构错误直接拒绝**（不写入）；局部空项**忽略并统计**。

自测：python -m app.tagger
"""
import json
import os
from datetime import datetime

from . import config
from . import tagger_dict
# 2026-09-17（审核 R-2）：`run_ui_suggest()` 需要调用打标引擎；
#   tagger_engine 只依赖标准库 re，无循环导入风险。
from . import tagger_engine

# 校验时最多报告的条数（避免刷屏）
_MAX_REPORT = 8


def _now() -> str:
    """2026-09-17（审核 R-4）：统一到 `config.now_str()`。"""
    return config.now_str()


def _backup_dir() -> str:
    return os.path.join(config.data_dir(), config.BACKUP_DIR_NAME)


# ---------------------------------------------------------------------- #
# 一、校验 / 规范化
# ---------------------------------------------------------------------- #
def _check_layer(layer, where: str, errors: list) -> None:
    """校验一层"{维度名: {标签名: [匹配词]}}"。"""
    if not isinstance(layer, dict) or not layer:
        errors.append("%s 必须是非空的「维度 → 标签」对象" % where)
        return
    for dim, labels in layer.items():
        if not isinstance(dim, str) or not dim.strip():
            errors.append("%s 存在空的维度名" % where)
            continue
        if not isinstance(labels, dict) or not labels:
            errors.append("%s/%s 必须是非空的「标签名 → 匹配词数组」对象" % (where, dim))
            continue
        for tag, words in labels.items():
            if not isinstance(tag, str) or not tag.strip():
                errors.append("%s/%s 存在空的标签名" % (where, dim))
                continue
            if not isinstance(words, list) or not words:
                errors.append("%s/%s/%s 的匹配词必须是非空数组" % (where, dim, tag))
                continue
            for w in words:
                if not isinstance(w, str) or not w.strip():
                    errors.append("%s/%s/%s 存在空的匹配词" % (where, dim, tag))
                    break


def validate_dict(data) -> list:
    """校验词表结构；返回**错误清单**（空列表 = 合法）。结构错误即拒绝写入。"""
    errors = []
    if not isinstance(data, dict):
        return ["词表根节点必须是 JSON 对象（{}）"]
    if "universal" not in data:
        errors.append("缺少 universal（通用骨架）")
    else:
        _check_layer(data.get("universal"), "universal", errors)
    if "domains" not in data:
        errors.append("缺少 domains（领域维度包）")
    else:
        doms = data.get("domains")
        if not isinstance(doms, dict) or not doms:
            errors.append("domains 必须是非空对象（领域名 → 维度包）")
        else:
            for dom, layer in doms.items():
                if not isinstance(dom, str) or not dom.strip():
                    errors.append("domains 存在空的领域名")
                    continue
                _check_layer(layer, "domains/%s" % dom, errors)
    dm = data.get("domain_map")
    if dm is not None:
        if not isinstance(dm, dict):
            errors.append("domain_map 必须是对象（领域名 → 分类名关键词数组）")
        else:
            for dom, words in dm.items():
                if not isinstance(words, list) or not all(
                        isinstance(w, str) and w.strip() for w in words):
                    errors.append("domain_map/%s 必须是非空的字符串数组" % dom)
    return errors[:_MAX_REPORT]


def _norm_word(w) -> str:
    """规范化单个匹配词：去首尾空白 + 压缩内部连续空白；英文一律小写。

    - `re:` 前缀的正则**原样保留**（压缩空白可能改变正则语义），仅去首尾空白；
    - 空串返回 ""（调用方据此丢弃）。
    """
    s = str(w or "").strip()
    if not s:
        return ""
    if s.startswith("re:"):
        return s
    s = " ".join(s.split())
    if s.isascii():
        s = s.lower()
    return s


def _norm_layer(layer) -> dict:
    """规范化一层：标签名去空白；匹配词去空白、英文小写、去重；丢弃空项。"""
    out = {}
    for dim, labels in (layer or {}).items():
        dim = str(dim).strip()
        if not dim or not isinstance(labels, dict):
            continue
        kept = {}
        for tag, words in labels.items():
            tag = str(tag).strip()
            if not tag:
                continue
            ws, seen = [], set()
            for w in (words or []):
                s = _norm_word(w)
                if s and s not in seen:
                    seen.add(s)
                    ws.append(s)
            if ws:
                kept[tag] = ws
        if kept:
            out[dim] = kept
    return out


def normalize_dict(data) -> dict:
    """规范化整份词表（保持原有顺序；丢弃空项）。"""
    out = {
        "version": int(data.get("version") or tagger_dict.TAG_DICT_VERSION),
        "updated_at": str(data.get("updated_at") or _now()),
        "universal": _norm_layer(data.get("universal")),
        "domains": {str(d).strip(): _norm_layer(v)
                    for d, v in (data.get("domains") or {}).items() if str(d).strip()},
        "domain_map": {},
    }
    for dom, words in (data.get("domain_map") or {}).items():
        dom = str(dom).strip()
        ws, seen = [], set()
        for w in (words or []):
            s = _norm_word(w)
            if s and s not in seen:
                seen.add(s)
                ws.append(s)
        if dom and ws:
            out["domain_map"][dom] = ws
    out["domains"] = {d: v for d, v in out["domains"].items() if v}
    return out


def count_tags(data) -> int:
    """标签总数（通用骨架 + 全部领域包）。"""
    n = sum(len(v) for v in (data.get("universal") or {}).values())
    for dims in (data.get("domains") or {}).values():
        n += sum(len(v) for v in (dims or {}).values())
    return n


def dict_tag_names(data) -> list:
    """词表中全部标签名（通用骨架 + 各领域包；去重、保持出现顺序）。

    2026-09-15 18:30（批次 9）：供"标签选择器候选池"与"自动补全数据源"使用（纯读、无副作用）。
    """
    out, seen = [], set()
    layers = [data.get("universal") or {}]
    layers.extend((data.get("domains") or {}).values())
    for layer in layers:
        for labels in (layer or {}).values():
            for tag in (labels or {}):
                if tag and tag not in seen:
                    seen.add(tag)
                    out.append(tag)
    return out


def tag_name_pool(db) -> list:
    """标签候选池 = 库中已用标签 ∪ 词表标签 ∪ 热点词（去重、保持出现顺序）。

    2026-09-15 18:30（批次 9，用户确认 C18/C19）：供详情区与快速新建窗口的「📋 选择…」
    以及详情区"输入自动补全"共用。**只读**：词表缺失时 `write_if_missing=False` ⇒ 不写库。
    """
    out, seen = [], set()

    def _push(n):
        s = str(n or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    try:
        for t in db.list_tags():
            _push(t.get("name") if isinstance(t, dict) else t)
    except Exception:
        pass
    try:
        for n in dict_tag_names(load_dict(db, write_if_missing=False)):
            _push(n)
    except Exception:
        pass
    try:
        for n in db.list_hotwords():
            _push(n)
    except Exception:
        pass
    return out


def dict_summary(data) -> dict:
    """结构预览用的摘要（供「词表」页签展示）。"""
    uni = {dim: len(labels) for dim, labels in (data.get("universal") or {}).items()}
    doms = {}
    for dom, dims in (data.get("domains") or {}).items():
        detail = {dim: len(labels) for dim, labels in (dims or {}).items()}
        doms[dom] = {"维度数": len(detail), "标签数": sum(detail.values()), "维度明细": detail}
    return {"version": data.get("version"), "updated_at": data.get("updated_at"),
            "通用骨架": uni, "通用骨架标签数": sum(uni.values()),
            "领域包": doms, "标签总数": count_tags(data),
            "领域判定表": {d: len(w) for d, w in (data.get("domain_map") or {}).items()}}


# ---------------------------------------------------------------------- #
# 一·补、「标签推荐策略与顺序」的存取（2026-09-16 批次 11-6，用户确认问题 2）
#   纯数据层：只读写 meta 键 `config.META_TAG_POLICY`；校验/归一化交给
#   `tagger_engine.normalize_policy`（单一真源），本模块不重复实现规则。
# ---------------------------------------------------------------------- #
def load_policy(db) -> dict:
    """读取「标签推荐策略与顺序」；meta 无值 / JSON 损坏 / 结构非法 ⇒ 返回**默认策略**。

    默认策略：热点词 → 领域+词典 → 取词 → 扩展（用户建议的顺序）；
    「显式标签」恒最高优先，不在此列表内。
    """
    from . import tagger_engine          # 函数内延迟导入：保持"数据层不依赖引擎"的既有边界
    try:
        raw = db.get_meta(config.META_TAG_POLICY)
        data = json.loads(raw) if raw else None
    except Exception:
        data = None
    return tagger_engine.normalize_policy(data)


def save_policy(db, data) -> dict:
    """保存「标签推荐策略与顺序」（先归一化再写 meta）；返回实际写入的规范化结果。"""
    from . import tagger_engine
    pol = tagger_engine.normalize_policy(data)
    db.set_meta(config.META_TAG_POLICY, json.dumps(pol, ensure_ascii=False))
    return pol


def reset_policy(db) -> dict:
    """清除策略设置（回到默认：meta 键删除 ⇒ 读取时自动落到默认值）。"""
    from . import tagger_engine
    try:
        db.set_meta(config.META_TAG_POLICY, "")
    except Exception:
        pass
    return tagger_engine.normalize_policy(None)


# ---------------------------------------------------------------------- #
# 二、存取（meta 表）
# ---------------------------------------------------------------------- #
def _write_backup(text: str, prefix: str):
    """把文本写入 data/backup/<prefix>_<时间戳>.json；失败返回 None（不阻断主流程）。"""
    try:
        d = _backup_dir()
        os.makedirs(d, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        path = os.path.join(d, "%s_%s.json" % (prefix, ts))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text or "")
        return path
    except Exception:
        return None


def load_dict(db, write_if_missing: bool = True) -> dict:
    """读取运行时词表；meta 无值或损坏时用出厂种子初始化。

    - meta 为空 → 写入出厂种子并返回（`write_if_missing=False` 时**只返回种子、不写库**，
      供 `--dry-run` 等"只读预演"场景使用）；
    - meta 有值但**非法**（JSON 解析失败或结构不合法）→ 先把原始串备份到 data/backup/
      （`tag_dict_broken_*.json`，**不丢用户数据**），再用出厂种子覆盖并返回；
    - 返回值前统一做规范化。
    """
    raw = ""
    try:
        raw = db.get_meta(config.META_TAG_DICT) or ""
    except Exception:
        raw = ""
    if raw.strip():
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict) and not validate_dict(data):
            return normalize_dict(data)
        _write_backup(raw, "tag_dict_broken")     # 损坏数据先留档，绝不静默丢弃
    seed = tagger_dict.builtin_dict()
    if write_if_missing:
        try:
            db.set_meta(config.META_TAG_DICT, json.dumps(seed, ensure_ascii=False))
        except Exception:
            pass
    return seed


def save_dict(db, data) -> dict:
    """校验 → 规范化 → 写入 meta。返回 {'ok','errors','tags'}（校验失败不写入）。"""
    errors = validate_dict(data)
    if errors:
        return {"ok": False, "errors": errors, "tags": 0}
    norm = normalize_dict(data)
    norm["updated_at"] = _now()
    db.set_meta(config.META_TAG_DICT, json.dumps(norm, ensure_ascii=False))
    return {"ok": True, "errors": [], "tags": count_tags(norm)}


def reset_dict(db) -> dict:
    """恢复出厂词表（先把当前词表备份到 data/backup/）。返回 {'ok','tag_dict','backup','tags'}。"""
    old = ""
    try:
        old = db.get_meta(config.META_TAG_DICT) or ""
    except Exception:
        old = ""
    backup = _write_backup(old, "tag_dict_before_reset") if old.strip() else None
    seed = tagger_dict.builtin_dict()
    db.set_meta(config.META_TAG_DICT, json.dumps(seed, ensure_ascii=False))
    return {"ok": True, "tag_dict": seed, "backup": backup, "tags": count_tags(seed)}


# ---------------------------------------------------------------------- #
# 三、JSON 文件读写 / 导入导出
# ---------------------------------------------------------------------- #
def write_dict_file(path: str, data) -> dict:
    """把词表写入 JSON 文件。返回 {'ok','path','error'}。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": path, "error": None}
    except Exception as exc:
        return {"ok": False, "path": path, "error": str(exc)}


def read_dict_file(path: str) -> dict:
    """读取 JSON 文件并校验。返回 {'ok','data','errors','error'}。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.loads(f.read())
    except Exception as exc:
        return {"ok": False, "data": None, "errors": [], "error": str(exc)}
    errors = validate_dict(data)
    if errors:
        return {"ok": False, "data": None, "errors": errors, "error": "结构校验未通过"}
    return {"ok": True, "data": normalize_dict(data), "errors": [], "error": None}


def export_dict(db, path: str) -> dict:
    """导出当前运行时词表到 JSON 文件。返回 {'ok','path','tags','error'}。"""
    data = load_dict(db)
    res = write_dict_file(path, data)
    res["tags"] = count_tags(data)
    return res


def import_dict(db, path: str) -> dict:
    """从 JSON 文件导入词表（**整体替换**）。

    - 结构校验不通过 → 拒绝，不写入（返回 errors）；
    - 通过 → 先把当前词表备份到 data/backup/（`tag_dict_before_import_*.json`），再写入。
    返回 {'ok','tags','before_tags','backup','domains','errors','error'}。
    """
    read = read_dict_file(path)
    if not read["ok"]:
        return {"ok": False, "errors": read["errors"], "error": read["error"],
                "tags": 0, "before_tags": 0, "backup": None, "domains": []}
    new = read["data"]
    old_raw = ""
    try:
        old_raw = db.get_meta(config.META_TAG_DICT) or ""
    except Exception:
        old_raw = ""
    before_tags = count_tags(json.loads(old_raw)) if old_raw.strip() else 0
    backup = _write_backup(old_raw, "tag_dict_before_import") if old_raw.strip() else None
    new["updated_at"] = _now()
    db.set_meta(config.META_TAG_DICT, json.dumps(new, ensure_ascii=False))
    return {"ok": True, "errors": [], "error": None, "tags": count_tags(new),
            "before_tags": before_tags, "backup": backup,
            "domains": list((new.get("domains") or {}).keys())}


# ---------------------------------------------------------------------- #
# 三之 2、**增量合并**导入（2026-09-17，用户需求）
#   与「整体替换」(`import_dict`) 并存：**只增不删** —— 每次只想加几个词时，
#   把"只想加的那部分"导进来即可，出厂/现有词表原样保留，不必再手工并整份文件。
#   两侧输入都已规范化（`read_dict_file` / `load_dict` 都过 `normalize_dict`）：
#   维度名 / 标签名 / 匹配词均已去首尾空白、英文小写、单层去重 ⇒ 同名视为同一个，
#   不会因"多打一个空格"而长出重名维度。
# ---------------------------------------------------------------------- #
def _merge_layer(cur_layer, new_layer) -> tuple:
    """并集合并一层（维度 → {标签: [匹配词]}），**只增不删、保序去重**。

    返回 `(合并后的一层, 新增标签数, 新增维度数, 新增匹配词数)`。
    原有维度/标签的**顺序与位置一字不动**；新维度追加在后，新标签追加在该维度末尾，
    新匹配词追加在该标签的词表末尾。
    """
    out = {d: {t: list(ws) for t, ws in (labels or {}).items()}
           for d, labels in (cur_layer or {}).items()}
    add_tags = add_dims = add_words = 0
    for dim, labels in (new_layer or {}).items():
        if dim not in out:
            out[dim] = {}
            add_dims += 1
        for tag, words in (labels or {}).items():
            if tag not in out[dim]:
                out[dim][tag] = []
                add_tags += 1
            have = out[dim][tag]
            seen = set(have)
            for w in words:
                if w not in seen:
                    seen.add(w)
                    have.append(w)
                    add_words += 1
    return out, add_tags, add_dims, add_words


def _merge_data(cur, new) -> tuple:
    """**纯函数**：把 `new` 增量并入 `cur`（只增不删），返回 `(合并后的词表, 统计)`。

    `merge_dict()`（写库）与 `merge_preview()`（界面预演）**共用本函数** ⇒ 界面看到的
    "将新增多少"与实际执行的合并口径**永远一致**（不存在两套实现走偏的可能）。
    """
    # ① 通用骨架
    uni, a_tags1, a_dims1, a_words1 = _merge_layer(cur.get("universal"),
                                                   new.get("universal"))
    # ② 领域维度包（新领域包 = 直接并入；已存在的 = 逐层并集）
    merged_domains = {d: v for d, v in (cur.get("domains") or {}).items()}
    a_tags2 = a_dims2 = a_words2 = a_domains = 0
    for dom, layer in (new.get("domains") or {}).items():
        if dom not in merged_domains:
            a_domains += 1
        merged_domains[dom], _t, _d, _w = _merge_layer(merged_domains.get(dom), layer)
        a_tags2 += _t
        a_dims2 += _d
        a_words2 += _w
    # ③ 领域判定表
    merged_map = {d: list(ws) for d, ws in (cur.get("domain_map") or {}).items()}
    a_map = 0
    for dom, words in (new.get("domain_map") or {}).items():
        have = merged_map.setdefault(dom, [])
        seen = set(have)
        for w in words:
            if w not in seen:
                seen.add(w)
                have.append(w)
                a_map += 1

    merged = {
        "version": max(int(cur.get("version") or 0), int(new.get("version") or 0))
                   or tagger_dict.TAG_DICT_VERSION,
        "updated_at": _now(),
        "universal": uni,
        "domains": merged_domains,
        "domain_map": merged_map,
    }
    stat = {"added_labels": a_tags1 + a_tags2, "added_dims": a_dims1 + a_dims2,
            "added_domains": a_domains, "added_words": a_words1 + a_words2,
            "added_map_words": a_map,
            "tags_before": count_tags(cur), "tags_after": count_tags(merged)}
    return merged, stat


def merge_preview(cur, new) -> dict:
    """**只算命不写库**：返回增量合并的统计（供界面确认框预览）。返回同上统计字段。"""
    return _merge_data(cur, new)[1]


def merge_dict(db, path: str) -> dict:
    """从 JSON 文件**增量合并**词表（只增不删，现有词表全部保留）。

    与 `import_dict()`（**整体替换**）的分工：
      - 同名维度 / 同名标签 → 匹配词**并集去重**（原有顺序不变，新词追加在后）；
      - 新维度 / 新标签 / **新领域包** / 新 `domain_map` 项 → **自动新增**；
      - 现有条目**一个不删**（本操作绝不会让词表变小）。
    安全约定：结构校验不通过 → **拒绝、不写入**（返回 errors）；通过 → 写库前先把当前
    词表备份到 `data/backup/`（`tag_dict_before_merge_*.json`）；写库仍走 `save_dict()`。

    返回 {'ok','added_labels','added_dims','added_domains','added_words','added_map_words',
          'tags_before','tags_after','backup','errors','error'}。
    """
    empty = {"ok": False, "errors": [], "error": None, "added_labels": 0, "added_dims": 0,
             "added_domains": 0, "added_words": 0, "added_map_words": 0,
             "tags_before": 0, "tags_after": 0, "backup": None}
    read = read_dict_file(path)
    if not read["ok"]:
        bad = dict(empty)
        bad["errors"], bad["error"] = read["errors"], read["error"]
        return bad
    cur = load_dict(db)
    merged, stat = _merge_data(cur, read["data"])
    old_raw = ""
    try:
        old_raw = db.get_meta(config.META_TAG_DICT) or ""
    except Exception:
        old_raw = ""
    backup = _write_backup(old_raw, "tag_dict_before_merge") if old_raw.strip() else None
    res = save_dict(db, merged)          # 校验 + 规范化 + 写库（校验不过则不写）
    if not res.get("ok"):
        bad = dict(empty)
        bad["errors"], bad["tags_before"], bad["backup"] = res.get("errors") or [], stat["tags_before"], backup
        return bad
    out = {"ok": True, "errors": [], "error": None, "backup": backup}
    out.update(stat)
    out["tags_after"] = res["tags"]
    return out


# ---------------------------------------------------------------------- #
# 四、条目 → 分类上下文（供"领域判定"用；阶段 1 预置标签 与 阶段 2 批量打标 **共用**）
#   2026-09-14：由 tag_builtin.py 抽到本模块，避免两处重复实现（预置标签预演时曾因
#   "只查条目自身分类的根目录归属"导致 63% 条目判不出领域 → 此处已按"沿链回溯"实现）。
# ---------------------------------------------------------------------- #
def build_context_index(db) -> dict:
    """一次性构建"分类链 + 根目录/项目类别归属"索引（避免逐条查询）。

    返回 {'cats': {id: (name, parent_id)}, 'cat_domains': {cat_id: [(项目类别名, 根目录名)]}}
    """
    cats = {r["id"]: (r["name"], r["parent_id"])
            for r in db.conn.execute("SELECT id, name, parent_id FROM categories")}
    cat_domains = {}
    for r in db.conn.execute(
            "SELECT dc.category_id AS cid, d.name AS dname, p.name AS pname"
            " FROM domain_category dc"
            " JOIN domains d ON d.id = dc.domain_id"
            " LEFT JOIN projects p ON p.id = d.project_id"):
        cat_domains.setdefault(r["cid"], []).append((r["pname"], r["dname"]))
    return {"cats": cats, "cat_domains": cat_domains}


def entry_context_names(index, cat_id) -> list:
    """条目的分类上下文名（项目类别 + 根目录 + 各级分类名，去重保序）。

    关键：根目录归属（domain_category）通常关联在**一级分类**上，而条目多挂在**二级/更深分类**，
    故必须**沿分类链逐级向上回溯**，任一级的归属都算。
    """
    cats = (index or {}).get("cats") or {}
    cat_domains = (index or {}).get("cat_domains") or {}
    names, chain = [], []
    cur, depth = cat_id, 0
    while cur is not None and depth < 8:
        row = cats.get(cur)
        if not row:
            break
        chain.append(row[0])
        for pname, dname in (cat_domains.get(cur) or []):
            if pname:
                names.append(pname)
            if dname:
                names.append(dname)
        cur = row[1]
        depth += 1
    names.extend(reversed(chain))
    out, seen = [], set()
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def all_dimension_names(dict_data, with_universal: bool = True) -> list:
    """词表中的全部维度名（去重保序）：通用骨架的维度 + 各领域包的维度。

    供「批量打标」的"标签重点范围"勾选列表使用。
    """
    out, seen = [], set()
    if with_universal:
        for dim in (dict_data.get("universal") or {}).keys():
            if dim not in seen:
                seen.add(dim)
                out.append(dim)
    for dims in (dict_data.get("domains") or {}).values():
        for dim in (dims or {}).keys():
            if dim not in seen:
                seen.add(dim)
                out.append(dim)
    return out


# ---------------------------------------------------------------------- #
# UI「单条推荐」的公共流程（2026-09-17，审核报告 R-2）
#   背景：主窗口 MainWindow._suggest_tags 与「快速新建」QuickAddWindow._suggest_tags
#   原先**各自实现**同一套流程（读词表 → 调引擎 → 取词采集 → 来源标注），
#   仅"守卫条件 / 提示方式 / chip 刷新"不同。抽出本函数后，两处只保留差异部分，
#   保证"改一次策略两窗口同时生效"，避免日后走偏。
# ---------------------------------------------------------------------- #
def run_ui_suggest(db, texts, ctx_names=None, from_auto: bool = False, collect=None) -> dict:
    """执行一次"UI 单条推荐"的公共流程（**不含任何界面代码**）。

    参数：
      db          —— Database 实例（读词表 / 策略 / 热点词）
      texts       —— {字段名: 文本}（name / intro / features / image_desc / prompt_cn / prompt_en）
      ctx_names   —— 分类上下文名（用于领域判定），可为 None
      from_auto   —— 是否来自 T2 防抖自动推荐（用于"取词采集范围"判断）
      collect     —— 可选回调 `(texts, dict_data, from_auto) -> str`：
                     由调用方实现的"取词采集"提示语（两窗口的 entry_id 语义不同，故外置）

    返回：{"names": [...], "source_note": "（来源：…）", "auto_note": "…", "dict_data": {...}}
    异常：词表读取 / 引擎调用失败时**抛出**，由调用方决定提示方式（toast / messagebox）。

    说明：**固定**启用 `fallback_global=True`（全词典兜底）与 `field_fallback=True`（字段取词），
    并传入用户配置的「推荐策略与顺序」与热点词清单——这是"UI 单条推荐"的既定口径，
    与"批量 / 离线打标"（不启用取词）刻意区分，两者不可混用。
    """
    dict_data = load_dict(db)
    res = tagger_engine.suggest(
        texts, dict_data, ctx_names,
        fallback_global=True, field_fallback=True,
        policy=load_policy(db), hotwords=db.list_hotwords())
    names = tagger_engine.tag_names(res)
    # 2026-09-15 19:30（批次 10）：来源标注——取词优先于词典兜底显示
    # 2026-09-18（取词优化 + 用户第 3 轮"兜底规则"）：取词来源细分三种
    #   「标题/提示词」（命中词表）／「取词（新词）」／「取词（兜底）」，此处统一提示
    source_note = ""
    if names:
        _sources = {t.get("source") for t in (res.get("tags") or [])}
        if "取词（兜底）" in _sources:
            source_note = "（来源：标题/提示词，兜底取词）"
        elif "取词（新词）" in _sources:
            source_note = "（来源：标题/提示词，含新词）"
        elif "标题/提示词" in _sources:
            source_note = "（来源：标题/提示词）"
        elif "词典兜底" in _sources:
            source_note = "（来源：词典兜底）"
    auto_note = ""
    if callable(collect):
        try:
            auto_note = collect(texts, dict_data, from_auto) or ""
        except Exception:
            auto_note = ""       # 取词采集失败绝不影响推荐主流程
    return {"names": names, "source_note": source_note,
            "auto_note": auto_note, "dict_data": dict_data}


def _selftest() -> None:
    """词表存取自测：出厂种子/初始化/校验/规范化/存取/导出导入/恢复出厂/损坏自愈。"""
    import shutil
    import tempfile

    from .database import Database

    tmp = tempfile.mkdtemp(prefix="promptsprite_tagger_")
    try:
        db = Database(os.path.join(tmp, "t.db"))
        try:
            # 1. 出厂种子合法
            seed = tagger_dict.builtin_dict()
            assert validate_dict(seed) == [], validate_dict(seed)

            # 2. 首次 load → 自动写入 meta，且与种子等价
            d1 = load_dict(db)
            assert count_tags(d1) == count_tags(seed) > 0
            assert (db.get_meta(config.META_TAG_DICT) or "").strip(), "应已写入 meta"

            # 3. 校验：结构错误被拒绝（缺 universal / 维度值类型错 / domain_map 非法）
            assert validate_dict({"domains": {"视觉": {"题材主体": {"人像": ["x"]}}}}), "缺 universal 应报错"
            assert validate_dict({"universal": {"领域": {"视觉": []}}, "domains": {"视觉": {}}}), \
                "空对象应报错"
            assert validate_dict({"universal": {"领域": {"视觉": ["x"]}}, "domains": {"视觉": {}}}), \
                "维度值应为对象"
            bad = tagger_dict.builtin_dict()
            bad["domain_map"] = {"视觉": "不是数组"}
            assert validate_dict(bad), "domain_map 非法应报错"
            # save_dict 遇到非法结构：不写入
            r = save_dict(db, {"domains": {}})
            assert not r["ok"] and r["errors"], r

            # 4. 规范化：去空白 / 英文小写 / 去重 / 丢弃空项
            norm = normalize_dict({
                "universal": {"领域": {"视觉": [" 图像 ", "IMAGE", "图像", ""]}},
                "domains": {" 文学 ": {"体裁": {"短篇": ["Short  Story", "short story"]}}},
                "domain_map": {"文学": [" 小说 ", "小说"]},
            })
            assert norm["universal"]["领域"]["视觉"] == ["图像", "image"], norm
            assert norm["domains"]["文学"]["体裁"]["短篇"] == ["short story"], norm
            assert norm["domain_map"]["文学"] == ["小说"], norm

            # 5. save_dict 正常写入 + load 回读一致
            d2 = tagger_dict.builtin_dict()
            d2["domains"]["学术"] = {"学科": {"计算机科学": ["计算机科学", "cs"]}}
            d2["domain_map"]["学术"] = ["学术", "论文"]
            r = save_dict(db, d2)
            assert r["ok"] and r["tags"] > 0, r
            back = load_dict(db)
            assert "学术" in back["domains"], back["domains"].keys()
            assert dict_summary(back)["标签总数"] == r["tags"]

            # 6. 导出 → 导入（整体替换）→ 导入前备份存在
            p = os.path.join(tmp, "dict.json")
            e = export_dict(db, p)
            assert e["ok"] and os.path.isfile(p) and e["tags"] == r["tags"], e
            only_visual = {"version": 1, "universal": {"领域": {"视觉": ["图像"]}},
                           "domains": {"视觉": {"题材主体": {"人像": ["portrait"]}}},
                           "domain_map": {"视觉": ["图像"]}}
            p2 = os.path.join(tmp, "dict2.json")
            assert write_dict_file(p2, only_visual)["ok"]
            im = import_dict(db, p2)
            assert im["ok"] and im["tags"] == 2 and im["backup"], im
            assert list(load_dict(db)["domains"].keys()) == ["视觉"]

            # 7. 导入非法文件 → 拒绝且不改动现有词表
            p3 = os.path.join(tmp, "bad.json")
            with open(p3, "w", encoding="utf-8") as f:
                f.write('{"universal": {}, "domains": {}}')
            im2 = import_dict(db, p3)
            assert not im2["ok"] and im2["errors"], im2
            assert list(load_dict(db)["domains"].keys()) == ["视觉"], "非法导入不得改动现有词表"

            # 8. 恢复出厂
            rs = reset_dict(db)
            assert rs["ok"] and rs["backup"], rs
            assert set(load_dict(db)["domains"].keys()) == set(seed["domains"].keys())

            # 9. 损坏自愈：meta 写入坏 JSON → load 用种子初始化，且坏数据已备份
            db.set_meta(config.META_TAG_DICT, "{不是 JSON")
            d3 = load_dict(db)
            assert count_tags(d3) == count_tags(seed)
            assert (db.get_meta(config.META_TAG_DICT) or "").startswith("{")
            broken = [f for f in os.listdir(_backup_dir()) if f.startswith("tag_dict_broken_")]
            assert broken, "损坏数据应已备份到 data/backup/"

            # 10. 标签名扁平化与候选池（2026-09-15 批次 9）
            _names9 = dict_tag_names(seed)
            assert _names9 and len(_names9) == len(set(_names9)), "标签名应非空且已去重"
            assert len(_names9) <= count_tags(seed)
            _pool9 = tag_name_pool(db)
            assert _pool9 and len(_pool9) == len(set(_pool9)), "候选池应非空且已去重"
            assert set(_names9) <= set(_pool9), "候选池须包含全部词表标签"

            # 11. 「标签推荐策略与顺序」存取（2026-09-16 批次 11-6）
            from . import tagger_engine as _te11
            assert load_policy(db)["order"] == list(_te11.POLICY_SOURCES), \
                "默认顺序应为 热点词 → 领域+词典 → 取词 → 扩展"
            _saved11 = save_policy(db, {
                "order": [_te11.SOURCE_FIELD, _te11.SOURCE_DOMAIN_DICT,
                          _te11.SOURCE_HOTWORD, _te11.SOURCE_EXT],
                "enabled": {_te11.SOURCE_FIELD: True, _te11.SOURCE_DOMAIN_DICT: False,
                            _te11.SOURCE_HOTWORD: True, _te11.SOURCE_EXT: False}})
            _back11 = load_policy(db)
            assert _back11 == _saved11, (_back11, _saved11)
            assert _back11["order"][0] == _te11.SOURCE_FIELD, _back11
            assert _back11["enabled"][_te11.SOURCE_DOMAIN_DICT] is False, _back11
            # 非法结构自动回退默认（不抛异常）
            db.set_meta(config.META_TAG_POLICY, "{坏 JSON")
            assert load_policy(db)["order"] == list(_te11.POLICY_SOURCES)
            db.set_meta(config.META_TAG_POLICY, json.dumps({"order": "坏值"}))
            assert load_policy(db)["order"] == list(_te11.POLICY_SOURCES)
            reset_policy(db)
            assert load_policy(db)["order"] == list(_te11.POLICY_SOURCES), "reset 后应回默认"

            # 12. 增量合并（2026-09-17 用户需求）：**只增不删**，与"整体替换"并存
            _pre12 = load_dict(db)
            _before12 = count_tags(_pre12)
            _add12 = {
                "version": 1,
                "universal": {"领域": {"视觉": ["计算机视觉测试词"]}},
                "domains": {
                    "视觉": {"题材主体": {"人像": ["headshot2"], "新增标签甲": ["新词甲"]},
                             "新增维度乙": {"新维度标签丙": ["新词丙"]}},
                    "学术": {"学科": {"计算机科学": ["计算机科学", "cs"]}},
                },
                "domain_map": {"视觉": ["图像", "新领域关键词"], "学术": ["学术", "论文"]},
            }
            # domain_map 的"新增词数"**取决于当前词表里已有什么** ⇒ 期望值按合并前实测推算
            #   （2026-09-18：出厂词表换版后此处口径自动跟随，不再是写死的数字）
            _exp_map12 = sum(1 for _dom, _ws in _add12["domain_map"].items()
                             for _w in _ws
                             if _w not in (_pre12.get("domain_map") or {}).get(_dom, []))
            p12 = os.path.join(tmp, "add_only.json")
            assert write_dict_file(p12, _add12)["ok"]
            m12 = merge_dict(db, p12)
            assert m12["ok"] and m12["backup"], m12
            _d12 = load_dict(db)
            # ① 只增不删：标签数增加，且**原词表标签一个不少**
            assert count_tags(_d12) > _before12, (count_tags(_d12), _before12)
            assert set(dict_tag_names(seed)) <= set(dict_tag_names(_d12)), "原词表标签不得丢失"
            # ② 同名标签：匹配词并集（原有在前、新词追加在后）
            assert _d12["universal"]["领域"]["视觉"][-1] == "计算机视觉测试词"
            assert _d12["domains"]["视觉"]["题材主体"]["人像"][-1] == "headshot2"
            # ③ 新标签 / 新维度 / 新领域包 / domain_map 自动新增
            assert "新增标签甲" in _d12["domains"]["视觉"]["题材主体"]
            assert "新增维度乙" in _d12["domains"]["视觉"]
            assert "学术" in _d12["domains"]
            assert "新领域关键词" in _d12["domain_map"]["视觉"]      # 新的判定关键词已并入
            assert _d12["domain_map"]["学术"].count("学术") == 1      # 已存在则**不重复追加**
            assert (m12["added_labels"], m12["added_dims"], m12["added_domains"]) == (3, 2, 1), m12
            assert m12["added_map_words"] == _exp_map12, (m12, _exp_map12)
            # ④ 非法文件 → 拒绝，且词表一字不动
            m12b = merge_dict(db, p3)
            assert not m12b["ok"] and m12b["errors"], m12b
            assert count_tags(load_dict(db)) == count_tags(_d12), "非法合并不得改动词表"
            # ⑤ 幂等：同一份文件再来一次 → 不再新增；且"预演"与"实际"口径一致
            _prev12 = merge_preview(load_dict(db), _add12)
            assert (_prev12["added_labels"], _prev12["added_words"],
                    _prev12["added_map_words"]) == (0, 0, 0), _prev12
            m12c = merge_dict(db, p12)
            assert m12c["ok"] and m12c["tags_after"] == m12c["tags_before"], m12c

            print("[词表] 种子/初始化/校验拒绝/规范化/存取/导出导入/备份/恢复出厂/损坏自愈"
                  "/标签名扁平化与候选池/推荐策略存取/增量合并（只增不删） 通过；"
                  "标签总数=%d" % count_tags(seed))
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
