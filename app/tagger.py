# -*- coding: utf-8 -*-
"""
tagger.py - 标签词表的存取 / 校验 / 导入导出（阶段 0.5，2026-09-14）

职责（**纯数据层，无界面**）：
  1. `load_dict(db)`   读取运行时词表；meta 无值或损坏时用出厂种子初始化（损坏数据先备份不丢）；
  2. `save_dict(db, data)`  校验 → 规范化 → 写入 meta；
  3. `validate_dict(data)` / `normalize_dict(data)` / `dict_summary(data)` / `count_tags(data)`；
  4. `reset_dict(db)`  恢复出厂词表；
  5. `export_dict(db, path)` / `import_dict(db, path)`  JSON 导出/导入（导入前自动备份当前词表）；
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

# 校验时最多报告的条数（避免刷屏）
_MAX_REPORT = 8


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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

            print("[词表] 种子/初始化/校验拒绝/规范化/存取/导出导入/备份/恢复出厂/损坏自愈"
                  "/标签名扁平化与候选池 通过；"
                  "标签总数=%d" % count_tags(seed))
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
