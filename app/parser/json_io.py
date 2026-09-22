# -*- coding: utf-8 -*-
"""
json_io.py - JSON 数据导入导出（完整备份/还原 / 每日变更包）
创建日期：2026-08-12（阶段六创建；阶段八重构为全局分类+领域关联 v2）

导出格式（version 5，2026-09-13 第 4 期 4-a：分类/条目路径支持任意深度（四层）＋ 随包携带
`field_defs`；仍兼容 v2/v3/v4 导入）：
{
  "version": 5,
  "type": "full" | "change",          // full=完整备份；change=某日变更包（每日备份）
  "exported_at": "…",
  "computer_code": "PC-HOME",         // 变更包来源电脑代号（普通导出可为空）
  "day": "2026-09-08",                // 变更包日期（普通导出可为空）
  "summary": {                        // 元信息：让文件"一眼可读"（导入预览/导出披露用）
      "add_entries": n, "add_categories": n, "add_domains": n,   // 新增/修改的对象数
      "del_entries": n, "del_categories": n, "del_domains": n    // 删除的对象数
  },
  "field_defs":   [{"field_key": "custom_1", "display_name": "…", "field_type": "list",
                    "is_builtin": 0, "sort_order": 10, "config_json": "…", "archived": 0}, …],
  "projects":      [{"name": "日常学习记录", "sort_order": 0}, …],
  "domain_projects": {"视频": "日常学习记录", …},
  "domains":      [{"name": "视频", "sort_order": 0}, …],
  "domain_links": {"视频": ["第一维度：…", …]},
  "categories":   [{"parent": null, "name": "第一维度：…", "sort_order": 0}, …],
  "entries":      [{"path": ["一级", "二级", "三级", "四级"], 9字段…, "is_favorite": 0}, …],
  "deleted_entries": […], "deleted_categories": […], "deleted_domains": […]  // 仅变更包有
}
说明：分类为全局共享树；path 不含领域；path 为空表示"未分类"；
      **categories 必须"父先子后"**（导入按名称路径逐级解析，支持 1~4 层）；
      version 2/3/4 旧文件仍可导入（无 summary/无 deleted_* 字段/无 field_defs 按 0/空处理）。
"""
import json
from datetime import datetime
from typing import Optional

from ..database import Database, GLOBAL_TAG_NS  # 2026-08-18（P1-1）：内容判重键；2026-09-13：标签命名空间
from ..models import Entry

# 导入导出格式版本（当前）：
#   v5（2026-09-13，4-a）：四层分类路径 + field_defs 随包；
#   **v6（2026-09-17，FR-93/FR-94）：每条例目携带稳定 ID（`uuid`）；
#     多位置条目导出只写一次、位置清单写入 `locations`（导入时重建关联）。**
#   兼容策略：**v2~v5 老文件导入路径完全不变**（无 uuid ⇒ 走原"内容键"匹配）；
#   裁剪说明：v6 只增键、不改既有键语义 ⇒ 老版本软件读 v6 包会忽略 uuid/locations
#   （但**库结构已是 schema v5**，回退旧版 EXE 仍需同时恢复数据库备份）。
JSON_VERSION = 6
JSON_VERSION_V5 = 5   # 上一版（保留常量，便于对照与判断"是否携带 uuid"）


# ---------------------------------------------------------------------- #
# 分类树收集（供本模块与 excel/html 导出共用）
# ---------------------------------------------------------------------- #
def _gather_categories(db, domain_id=None, parent_id=None):
    """收集分类：按领域(domain_id)/子树根(parent_id)收集；两者皆空收集全部一级分类及后代"""
    if parent_id is not None:
        roots = [db.get_category(parent_id)]
    elif domain_id is not None:
        roots = db.list_categories(domain_id=domain_id, parent_id=None)
    else:
        roots = db.list_categories(parent_id=None)
    cats = []
    stack = list(roots)
    while stack:
        c = stack.pop()
        if not c:
            continue
        cats.append(c)
        stack.extend(db.list_categories(parent_id=c["id"]))
    return cats


def _chain_names(db, category_id) -> list:
    """分类名称链（不含领域）：[L1名, L2名…]"""
    names = []
    c = db.get_category(category_id)
    while c:
        names.append(c["name"])
        c = db.get_category(c["parent_id"]) if c["parent_id"] else None
    return names[::-1]


def _ancestor_chain_cats(db, category_id) -> list:
    """从一级分类到自身（含自身）的分类列表（子树导出时保留路径上下文）"""
    chain = []
    c = db.get_category(category_id)
    while c:
        chain.append(c)
        c = db.get_category(c["parent_id"]) if c["parent_id"] else None
    chain.reverse()
    return chain


def _domain_links(db, cat_ids) -> dict:
    """{领域名: [一级分类名, …]}：仅统计给定分类集合中的一级分类"""
    links = {}
    for cid in cat_ids:
        c = db.get_category(cid)
        if c and c["parent_id"] is None:
            for d in db.linked_domains(cid):
                links.setdefault(d["name"], []).append(c["name"])
    return links


def _scope_categories(db, category_id=None, domain_id=None, project_id=None):
    """解析导出范围 → `(cats, scope_ids)`；2026-09-22（用户要求 3-1）新增。

    背景：原"导出当前分类"只认 `category_id`（一级/二级分类），无法按"项目类别""根目录"
    导出整个分支。此处把三种范围统一为一套解析，供 JSON/Excel/HTML 导出共用。

    - `category_id` 指定 → 该分类子树 + **祖先链**（保留路径上下文）；
    - `domain_id`   指定 → 该根目录（领域）关联的全部分类（一级分类及其全部子级）；
    - `project_id`  指定 → 该项目类别下全部根目录所关联的全部分类；
    - 三者皆空           → 全部分类（全库导出）。

    返回：
      - `cats`：需要写入包/参与展示的分类 dict 列表（去重保序）；
      - `scope_ids`：**条目位置过滤范围**（list，按收集顺序）；`None` 表示全库、不限制位置。
    优先级（正常只会传其一）：category_id > domain_id > project_id。
    """
    if category_id is not None:
        subtree = _gather_categories(db, parent_id=category_id)
        seen, cats = set(), []
        for c in _ancestor_chain_cats(db, category_id) + subtree:
            if c["id"] not in seen:
                seen.add(c["id"])
                cats.append(c)
        return cats, [c["id"] for c in subtree]
    if domain_id is not None:
        cats = _gather_categories(db, domain_id=domain_id)
        return cats, [c["id"] for c in cats]
    if project_id is not None:
        seen, cats = set(), []
        for d in db.list_domains(project_id=project_id):
            for c in _gather_categories(db, domain_id=d["id"]):
                if c["id"] not in seen:
                    seen.add(c["id"])
                    cats.append(c)
        return cats, [c["id"] for c in cats]
    return _gather_categories(db), None


def scope_title(db, category_id=None, domain_id=None, project_id=None) -> str:
    """导出范围的显示名（2026-09-22，用户要求 3-1）：分类路径 / 根目录名 / 项目类别名。

    供 HTML 分节标题与界面"默认文件名"共用；任何异常返回空串（不阻断导出流程）。
    """
    try:
        if category_id is not None:
            return " / ".join(c["name"] for c in _ancestor_chain_cats(db, category_id))
        if domain_id is not None:
            d = db.get_domain(domain_id)
            return (d or {}).get("name") or ""
        if project_id is not None:
            p = db.get_project(project_id)
            return (p or {}).get("name") or ""
    except Exception:                                  # noqa: BLE001
        pass
    return ""


def apply_field_defs(db, defs, resolver=None) -> dict:
    """把随包的字段定义写入本地（2026-09-13：支持"逐项确认"）。

    - `resolver`（可选）：`fn(diff_rows) -> {field_key: "add"|"overwrite"|"skip"}`；
      由界面提供（逐项选择是新增还是覆盖），未提供则沿用既有"按 key 全量合并（覆盖）"行为；
    - diff_rows 由 `db.field_defs_diff()` 生成（status: new/diff/same）；
    - 返回 `{"added": n, "overwritten": n, "skipped": n}`——`skipped` 含"完全一致无需处理"的项。
    """
    rows = db.field_defs_diff(defs)
    decisions = {}
    if resolver is not None and rows:
        try:
            decisions = resolver(rows) or {}
        except Exception:
            decisions = {}
    stat = {"added": 0, "overwritten": 0, "skipped": 0}
    for r in rows:
        key, status = r["field_key"], r["status"]
        if status == "same":
            stat["skipped"] += 1
            continue
        act = decisions.get(key)
        if act is None:
            act = "add" if status == "new" else "overwrite"
        if act == "skip" or (status == "diff" and act == "add"):
            stat["skipped"] += 1
            continue
        inc = r["incoming"]
        db.upsert_field_def(field_key=key, display_name=inc["display_name"],
                            field_type=inc["field_type"], is_builtin=inc["is_builtin"],
                            sort_order=inc["sort_order"], config_json=inc["config_json"],
                            archived=inc["archived"])
        stat["added" if status == "new" else "overwritten"] += 1
    return stat


def _field_defs_payload(db) -> list:
    """自定义字段定义载荷（含内置 10 项的改名与归档态；2026-09-13，4-a）。

    随包导出/导入，保证换机后"字段定义 + 字段取值"成套恢复。
    """
    out = []
    for d in db.list_field_defs(include_archived=True):
        out.append({
            "field_key": d.get("field_key") or "",
            "display_name": d.get("display_name") or "",
            "field_type": d.get("field_type") or "text",
            "is_builtin": 1 if d.get("is_builtin") else 0,
            "sort_order": int(d.get("sort_order") or 0),
            "config_json": d.get("config_json") or "",
            "archived": 1 if d.get("archived") else 0,
        })
    return out


def _entry_locations(db, e, allowed_cat_ids=None) -> list:
    """该条目的**位置清单**（每项为分类名链 list）；2026-09-17（FR-94）新增。

    - `allowed_cat_ids` 非空（子树导出）⇒ 只保留落在该范围内的位置；
    - 主挂靠（`entries.category_id`）与"关联位置"（`entry_links`）一并取并集；
    - 去重保序；无位置返回空列表。
    """
    try:
        _ids = list(db.list_entry_locations(e["id"]) or [])
    except Exception:
        _ids = []
    if not _ids and e.get("category_id"):
        _ids = [e["category_id"]]
    out = []
    for cid in _ids:
        if not cid:
            continue
        if allowed_cat_ids is not None and cid not in allowed_cat_ids:
            continue
        _chain = _chain_names(db, cid)
        if _chain and _chain not in out:
            out.append(_chain)
    return out


def _entry_payload(db, e, locations=None) -> dict:
    payload = {k: e[k] for k in ("name", "intro", "origin", "features", "scenes", "works",
                                 "image_desc", "prompt_cn", "prompt_en", "image_plan",
                                 "is_favorite")}
    payload["path"] = _chain_names(db, e["category_id"]) if e["category_id"] else []
    # 2026-09-17（FR-93，JSON v6）：随包携带**稳定 ID**（跨机器认人）。
    #   为空则不写该键 —— 保持与旧格式一致、文件不膨胀（旧包无该键 ⇒ 走原"内容键"匹配）。
    try:
        _uuid = str(e["uuid"] or "").strip() if "uuid" in e.keys() else ""
    except Exception:
        _uuid = ""
    if _uuid:
        payload["uuid"] = _uuid
    # 2026-09-17（FR-94，JSON v6）：**多位置条目**的位置清单（仅在多于 1 个位置时写）。
    #   子树导出时本条目只出现一次，靠本数组保留"1 条 + N 位置"的关联关系。
    if locations and len(locations) > 1:
        payload["locations"] = [list(x) for x in locations]
    # 2026-09-16（批次 11-7，用户要求 3）：随包携带**条目的创建时间**，导出→导入 / 换机后不丢。
    #   注意：**不携带 `updated_at`**（导入时仍取当前时间）——`updated_at` 驱动"当日变更包"的
    #   采集语义（`list_entries_updated_since`），若保留旧值会导致刚导入的条目不再进当日变更包。
    #   为空则不写该键，保持与旧格式一致、文件不膨胀（旧文件无该键 ⇒ 导入时按导入时刻）。
    try:
        _ca = str(e["created_at"] or "").strip() if "created_at" in e.keys() else ""
    except Exception:
        _ca = ""
    if _ca:
        payload["created_at"] = _ca
    # 2026-09-13（1-A-5 第 3 步·下）：自定义字段取值（为空则**不写**该键，保持与旧格式一致、文件不膨胀）
    try:
        cf = {r["field_key"]: r["value_text"] for r in db.list_entry_field_values(e["id"])}
    except Exception:
        cf = {}
    if cf:
        payload["custom_fields"] = cf
    # 2026-09-13（3-b）：结构化取值（目录层级型＝令牌+快照名）随包携带，
    # 否则导入后层级引用会退化为"失联"；为空则不写该键（旧格式一致、文件不膨胀）。
    try:
        cfj = {r["field_key"]: r["value_json"] for r in db.list_entry_field_values(e["id"])
               if r["value_json"]}
    except Exception:
        cfj = {}
    if cfj:
        payload["custom_fields_json"] = cfj
    # 2026-09-13（1-C-4）：标签（为空则不写该键，保持与旧格式一致）
    try:
        tags = db.list_entry_tag_names(e["id"])
    except Exception:
        tags = []
    if tags:
        payload["tags"] = tags
    # 2026-09-13（2-d）：图集（为空则不写该键，保持与旧格式一致、文件不膨胀）
    try:
        imgs = db.list_entry_images(e["id"])
    except Exception:
        imgs = []
    if imgs:
        payload["images"] = [{"kind": r.get("kind") or "local",
                              "path": r.get("path") or "",
                              "source_url": r.get("source_url") or "",
                              "caption": r.get("caption") or ""} for r in imgs]
    return payload


def _pending_ids(db, max_id_before: int, expected: int):
    """批量插入后取"插入前最大 id"之后的 id 序列；数量与 expected 不一致时返回 None（宁可不写）。"""
    ids = [r["id"] for r in db.conn.execute(
        "SELECT id FROM entries WHERE id > ? ORDER BY id", (max_id_before,)).fetchall()]
    return ids if len(ids) == expected else None


def _restore_pending_custom_fields(db, max_id_before: int, pending_cf: list,
                                   pending_cfj: list = None) -> int:
    """导入后写回自定义字段取值（2026-09-13，1-A-5 第 3 步·下）。

    entries 批量插入按 pending 顺序分配递增 id，故取"插入前最大 id"之后的 id 序列，
    与 pending 一一对应；数量不一致时不写（宁可少写也不写错条目）。返回写入条目数。
    pending_cfj（2026-09-13，3-b）：同序的结构化取值（value_json），用于目录层级型
    等"令牌+快照名"字段；缺省/长度不符时该字段的 value_json 记空（与旧包行为一致）。
    """
    if not pending_cf or not any(pending_cf):
        return 0
    ids = _pending_ids(db, max_id_before, len(pending_cf))
    if ids is None:
        return 0
    cfj_ok = bool(pending_cfj) and len(pending_cfj) == len(pending_cf)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for i, (eid, cf) in enumerate(zip(ids, pending_cf)):
        if not cf:
            continue
        thr = (pending_cfj[i] or {}) if (cfj_ok and isinstance(pending_cfj[i], dict)) else {}
        for fk, v in cf.items():
            db.conn.execute(
                "INSERT OR REPLACE INTO entry_field_values(entry_id, field_key,"
                " value_text, value_json, updated_at) VALUES(?, ?, ?, ?, ?)",
                (eid, fk, v or "", thr.get(fk) or "", ts))
        n += 1
    db.conn.commit()
    return n


def _restore_pending_tags(db, max_id_before: int, pending_tags: list) -> int:
    """导入后写回标签（2026-09-13，1-C-4）：缺失标签自动创建；数量不一致时不写。"""
    if not pending_tags or not any(pending_tags):
        return 0
    ids = _pending_ids(db, max_id_before, len(pending_tags))
    if ids is None:
        return 0
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ns = GLOBAL_TAG_NS
    n = 0
    for eid, names in zip(ids, pending_tags):
        if not names:
            continue
        for raw in names:
            name = (raw or "").strip()
            if not name:
                continue
            row = db.conn.execute(
                "SELECT id FROM tags WHERE namespace = ? AND name = ?", (ns, name)).fetchone()
            tid = row["id"] if row else db.conn.execute(
                "INSERT INTO tags(namespace, name, color, created_at, updated_at)"
                " VALUES(?,?,'',?,?)", (ns, name, ts, ts)).lastrowid
            db.conn.execute(
                "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at)"
                " VALUES(?,?,?)", (eid, tid, ts))
        n += 1
    db.conn.commit()
    return n


def _restore_pending_images(db, max_id_before: int, pending_imgs: list) -> int:
    """导入后写回图集（2026-09-13，2-d）。

    - **外链图**：总是写回（跨机可用）；
    - **本地图**：仅当文件在本机确实存在时才写回（跨机导入时目标机没有图片文件，
      写回会得到一堆"图片不可读"的死记录）；数量不一致时不写（宁可少写也不写错条目）。
    返回写入条目数。
    """
    import os
    from .. import config

    if not pending_imgs or not any(pending_imgs):
        return 0
    ids = _pending_ids(db, max_id_before, len(pending_imgs))
    if ids is None:
        return 0
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    root = os.path.abspath(config.data_dir())
    n = 0
    for eid, imgs in zip(ids, pending_imgs):
        if not imgs:
            continue
        order = 0
        for g in imgs:
            if not isinstance(g, dict):
                continue
            kind = g.get("kind") if g.get("kind") in ("local", "url") else "local"
            url = (g.get("source_url") or "").strip()
            rel = (g.get("path") or "").strip()
            if kind == "local":
                if not rel:
                    continue
                if os.path.commonpath([root, os.path.abspath(os.path.join(root, rel))]) != root:
                    continue
                if not os.path.isfile(os.path.join(root, rel)):
                    continue
            elif not url:
                continue
            db.conn.execute(
                "INSERT INTO entry_images(entry_id, kind, path, source_url,"
                " is_primary, sort_order, caption, created_at) VALUES(?,?,?,?,0,?,?,?)",
                (eid, kind, rel, url, order, g.get("caption") or "", ts))
            order += 1
        if order:
            n += 1
    db.conn.commit()
    return n


# ---------------------------------------------------------------------- #
# 导出
# ---------------------------------------------------------------------- #
def export_json(db, path, category_id=None, computer_code=None, day=None,
                project_id=None, domain_id=None) -> int:
    """导出全部（或指定分类子树 / 根目录子树 / 项目类别子树）为 JSON（**v6**）；返回条目数。

    computer_code/day：增量备份场景补充来源信息（电脑代号/日期）；普通导出可省略。
    project_id/domain_id：**2026-09-22（用户要求 3-1）新增**——按"项目类别""根目录"导出
      该分支下的全部数据（分类 + 条目）。三者只需传其一，优先级 category_id > domain_id
      > project_id；皆不传＝全库导出（与原来完全一致）。

    2026-09-17（FR-93/FR-94，v6）两处行为变化：
      ① 每条例目携带 `uuid`（稳定 ID，跨机器认人）；
      ② **子树导出时多位置条目只写一次**（原先每个位置各写一行 ⇒ 再导入会变成多份副本），
         各位置写入 `locations` 数组，导入时据此重建关联（"1 条 + N 位置"）。
         因此 `summary.add_entries` 由"位置行数"变为**唯一条目数**（更准确）。
    """
    export_cats, _scope_list = _scope_categories(
        db, category_id=category_id, domain_id=domain_id, project_id=project_id)
    _scope_ids = None      # 子树导出时的位置范围（None = 全库，不限制位置）
    if _scope_list is None:
        export_entries = db.list_all_entries()
    else:
        # 2026-09-17（FR-94）：按**条目 id 去重**——多位置条目在子树内只出现一次
        _seen_e, export_entries = set(), []
        for _cid in _scope_list:
            for _e in db.list_entries(_cid):
                if _e["id"] in _seen_e:
                    continue
                _seen_e.add(_e["id"])
                export_entries.append(_e)
        _scope_ids = set(_scope_list)

    projects = db.list_projects()
    p_by_id = {p["id"]: p for p in projects}
    domain_projects = {}
    for d in db.list_domains():
        if d.get("project_id") and d["project_id"] in p_by_id:
            domain_projects[d["name"]] = p_by_id[d["project_id"]]["name"]
    data = {
        "version": JSON_VERSION,
        "type": "full",
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "add_entries": len(export_entries),
            "add_categories": len(export_cats),
            "add_domains": len(db.list_domains()),
            "del_entries": 0,
            "del_categories": 0,
            "del_domains": 0,
        },
        "projects": [{"name": p["name"], "sort_order": p["sort_order"]} for p in projects],
        "domain_projects": domain_projects,
        "domains": [{k: d[k] for k in ("name", "sort_order")} for d in db.list_domains()],
        "domain_links": _domain_links(db, [c["id"] for c in export_cats]),
        "categories": [{"parent": (db.get_category(c["parent_id"])["name"]
                                   if c["parent_id"] else None),
                        "name": c["name"], "sort_order": c["sort_order"]}
                       for c in export_cats],
        "entries": [_entry_payload(db, e, _entry_locations(db, e, _scope_ids))
                    for e in export_entries],
    }
    # 2026-09-13（4-a）：自定义字段定义随包携带（否则换机导入后"字段值有、字段定义无"）
    try:
        data["field_defs"] = _field_defs_payload(db)
    except Exception:
        data["field_defs"] = []
    if computer_code:
        data["computer_code"] = computer_code
    if day:
        data["day"] = day
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return len(export_entries)


# ---------------------------------------------------------------------- #
# 导入
# ---------------------------------------------------------------------- #
def count_json_entries(path) -> int:
    """预览：读取 JSON 中的条目数（用于进度条总量）"""
    with open(path, encoding="utf-8") as f:
        return len(json.load(f).get("entries", []))


def read_pack_summary(path) -> dict:
    """解析 JSON 备份/变更包摘要（v2/v3/v4 兼容；导入预览与导出披露用，不改数据）。

    返回 summary 各项：有 summary 字段用字段值，否则按区块长度推算。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    add_entries = len(data.get("entries", []))
    add_categories = len(data.get("categories", []))
    add_domains = len(data.get("domains", []))
    del_entries = data.get("deleted_entries", [])
    del_categories = len(data.get("deleted_categories", []))
    del_domains = len(data.get("deleted_domains", []))
    # 携带被删快照数量（⑤逆向恢复的前提）
    deleted_snapshots = sum(1 for x in del_entries
                            if isinstance(x, dict) and x.get("snapshot"))
    s = data.get("summary") or {}
    out = {
        "version": data.get("version"),
        "type": data.get("type", "full"),
        "computer_code": data.get("computer_code", "") or "",
        "day": data.get("day", "") or "",
        "exported_at": data.get("exported_at", "") or "",
        "add_entries": s.get("add_entries", add_entries),
        "add_categories": s.get("add_categories", add_categories),
        "add_domains": s.get("add_domains", add_domains),
        "del_entries": s.get("del_entries", len(del_entries)),
        "del_categories": s.get("del_categories", del_categories),
        "del_domains": s.get("del_domains", del_domains),
        "deleted_snapshots": int(s.get("deleted_snapshots", deleted_snapshots)),
    }
    out["add_total"] = (out["add_entries"] + out["add_categories"]
                        + out["add_domains"])
    out["del_total"] = (out["del_entries"] + out["del_categories"]
                        + out["del_domains"])
    out["has_snapshots"] = out["deleted_snapshots"] > 0
    return out


def _resolve_category_by_path(cat_path: dict, path) -> Optional[int]:
    """按"名称路径"定位分类（支持 1~4 层，2026-09-13 4-a）。

    从**最长路径**逐级回退（4→3→2→1），均未命中返回 None（→ 未分类）；
    这样旧的两层文件行为不变，而三/四级条目也能落到最深一级。
    """
    names = [str(x) for x in (path or []) if str(x).strip()]
    for n in range(len(names), 0, -1):
        cid = cat_path.get(tuple(names[:n]))
        if cid:
            return cid
    return None


def _ensure_chain_categories(db, names, top_links=None) -> Optional[int]:
    """按名称链逐级查找，缺失则创建，返回末级分类 id（2026-09-08 V1.7.0：⑤逆向恢复用）。

    top_links（2026-09-09 P1-6）：{一级分类名: [根目录 id, ...]}——新建的一级分类若在
    变更包 domain_links 中有原归属，则同时建立 根目录↔一级分类 关联，避免恢复成"孤儿分类"。
    names 为空返回 None。
    """
    pid = None
    for idx, name in enumerate(names or []):
        row = db.conn.execute(
            "SELECT id FROM categories WHERE parent_id IS ? AND name = ?",
            (pid, name)).fetchone()
        if row:
            pid = row["id"]
        else:
            pid = db.add_category(name, parent_id=pid)
            if idx == 0 and top_links:
                for did in top_links.get(name, ()):
                    db.link_domain_category(did, pid)
    return pid


# ---------------------------------------------------------------------- #
# v6（2026-09-17，FR-93/FR-94）：稳定 ID 匹配与多位置重建
# ---------------------------------------------------------------------- #
def _extra_location_paths(ep) -> list:
    """取包内 `locations` 中**除主路径（`path`）之外**的其余位置（2026-09-17，FR-94）。

    用于导入时建立"关联位置"；主路径由 `path` 负责挂靠，避免重复。
    去重保序；项不是 list/tuple 时跳过（容错）。
    """
    _main = list(ep.get("path") or [])
    out = []
    for x in (ep.get("locations") or []):
        if not isinstance(x, (list, tuple)):
            continue
        x = list(x)
        if x and x != _main and x not in out:
            out.append(x)
    return out


def _link_extra_locations(db, entry_id: int, cat_path: dict, ep) -> int:
    """把 `locations` 中的额外位置解析为分类并建立关联；返回成功建立的个数（容错，不抛）。"""
    n = 0
    for paths in _extra_location_paths(ep):
        cid = _resolve_category_by_path(cat_path, paths)
        if cid is None:
            continue
        try:
            db.link_entry(entry_id, cid)   # 已是主挂靠/已关联 ⇒ 内部忽略
            n += 1
        except Exception:
            pass
    return n


def _apply_v6_aux(db, entry_id: int, ep) -> None:
    """v6「更新（修改）」附带的字段处理（2026-09-17，用户确认口径）。

    - `tags`         **有该键则整体替换**（忠实还原来源端）；
    - `custom_fields`**有该键则整体替换**（先清空该条目的全部自定义字段取值，再写入包内值）；
    - `images`       **并入（追加）**——JSON 不携带图片文件本体，整体替换会让目标库
                     已有的本地图片引用丢失，故只追加、不删除；
    - **键缺失时一律不动**（源端未提供 ≠ 源端为空）。
    所有子步骤单独 try/except：任一失败不影响整包导入。
    """
    if "tags" in ep:
        try:
            db.set_entry_tags(entry_id,
                              [str(x) for x in (ep.get("tags") or []) if str(x or "").strip()])
        except Exception:
            pass
    if "custom_fields" in ep:
        try:
            _cf = ep.get("custom_fields") or {}
            _cfj = ep.get("custom_fields_json") or {}
            for _r in db.list_entry_field_values(entry_id):
                db.delete_entry_field_value(entry_id, _r["field_key"])
            for _fk, _v in _cf.items():
                db.set_entry_field_value(entry_id, _fk, _v or "", _cfj.get(_fk) or "")
        except Exception:
            pass
    for _im in (ep.get("images") or []):
        try:
            db.add_entry_image(entry_id, _im.get("kind") or "local",
                               _im.get("path") or "", _im.get("source_url") or "",
                               _im.get("caption") or "")
        except Exception:
            pass


def _link_pending_locations(db, max_id_before: int, pending: list, pending_loc: list) -> int:
    """批量插入后，按"id 递增＝插入顺序"为新条目建立额外位置关联；返回建立的关联数。

    与 `_restore_pending_*` 同一策略：**仅当新增 id 数量与 pending 完全对应时才写**，
    否则宁可不写（避免把位置关联挂到错误的条目上）。
    """
    if not pending_loc or not any(pending_loc):
        return 0
    ids = _pending_ids(db, max_id_before, len(pending))
    if not ids:
        return 0
    out = 0
    for _e, _locs in zip(ids, pending_loc):
        for _cid in (_locs or []):
            try:
                db.link_entry(_e, _cid)
                out += 1
            except Exception:
                pass
    return out


def import_json(db, path, progress_cb=None, deletion_mode="apply",
                apply_additions=True, field_defs_resolver=None) -> dict:
    """导入 JSON 备份/变更包（v2~**v6** 兼容）：重建项目类别/全局分类树/领域关联/条目；返回统计。

    deletion_mode（2026-09-08 V1.7.0）：
      - "apply"：应用文件内 deleted_* 删除清单（同步删除，先进回收站可恢复）；
      - "skip" ：忽略删除清单（仅合并新增/修改，最安全）；
      - "reverse"：逆向恢复——把带快照的删除清单反向转为新增导入（不执行删除）。
    删除清单为空时各模式等价；reverse 仅在包内 deleted_entries 携带 snapshot 时才有实际效果。
    apply_additions（2026-09-08 V1.7.0）：False 时仅应用删除清单，不新增/修改任何数据
    （对应导入向导"② 仅应用删除"）。
    field_defs_resolver（2026-09-13）：随包 `field_defs` 的"逐项确认"回调
    （`fn(diff_rows) -> {field_key: "add"/"overwrite"/"skip"}`）；不传则按 key 全量合并。

    **v6 匹配策略（2026-09-17，FR-93/FR-94）——三步**：
      ① 包内条目带 `uuid` 且目标库已存在同 uuid ⇒ 判定为**同一条目**，走「更新」路径
         （更新 ①~⑩ 内容字段与收藏；标签/自定义字段"有则整体替换"；图集"并入"）；
      ② 无 uuid（v2~v5 老包）或 uuid 未命中 ⇒ **回退现有的"内容键"判重**（行为与改造前完全一致）；
      ③ 两者都不命中 ⇒ 新增（沿用包内 uuid，无则分配新的）。
    另：v6 包的 `locations`（多位置清单）会在导入时为该条目**建立关联位置**，
    从而实现"多位置条目导出→导入仍是 1 条 + N 位置"（FR-94）。
    返回统计新增 `updated`（按 uuid 命中并更新的条数）。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ver = data.get("version", 1)
    if ver not in (2, 3, 4, 5, 6):
        raise ValueError("JSON 版本不兼容，请使用本软件导出的备份文件")

    add_entries = data.get("entries", [])
    del_entries = data.get("deleted_entries", [])
    del_categories = data.get("deleted_categories", [])
    del_domains = data.get("deleted_domains", [])
    if not apply_additions:
        # "仅应用删除"：清空所有新增/修改区段（项目/领域/分类/关联/条目），保留删除清单
        add_entries = []
        data = {**data, "projects": [], "domains": [],
                "categories": [], "domain_links": {}}
    # 两阶段进度总量 = 新增/修改 +（apply）删除对象数 /（reverse）逐条扫描的删除清单数
    del_units = (len(del_entries) + len(del_categories) + len(del_domains)
                 if deletion_mode == "apply" else 0)
    snap_units = len(del_entries) if deletion_mode == "reverse" else 0
    total = len(add_entries) + max(del_units, snap_units)

    # 1. 项目类别（v3+：恢复 根目录→项目类别 归属；v2 无则跳过）
    project_map = {}
    for pr in data.get("projects", []):
        name = pr["name"]
        existing = db.get_project_by_name(name)
        if existing:
            project_map[name] = existing["id"]
        else:
            try:
                project_map[name] = db.add_project(name)
            except Exception:
                project_map[name] = db.ensure_project(name)
    domain_projects = data.get("domain_projects", {})

    # 1.5 字段定义（2026-09-13，4-a：v5 起随包携带；旧包无此键 → 跳过）
    #     2026-09-13 追加：可传 resolver 做"逐项确认（新增/覆盖/跳过）"，默认按 key 合并；
    #     仅应用删除（apply_additions=False）时不改字段定义。
    if apply_additions:
        apply_field_defs(db, data.get("field_defs") or [], resolver=field_defs_resolver)

    # 2. 领域（按名称复用或新建；v3+ 恢复项目归属）
    domain_map = {}
    for d in data.get("domains", []):
        name = d["name"]
        existing = next((x for x in db.list_domains() if x["name"] == name), None)
        did = existing["id"] if existing else db.add_domain(name)
        pname = domain_projects.get(name)
        if pname and pname in project_map:
            db.move_domain_to_project(did, project_map[pname])
        domain_map[name] = did

    # 3. 全局分类（父先子后；**支持 1~4 层任意深度**）
    #    2026-08-18（P1-3）：分类按名称复用（与 Excel 导入一致），重复导入不再产生重复分类；
    #    2026-09-13（4-a）：改为按"名称路径元组"定位父级，突破原来只能两层的限制。
    cat_path = {}      # (L1, L2, …) -> 分类 id
    name_paths = {}    # 名称 -> [出现过的路径元组…]（取最近一次出现者作为父级）
    for c in data.get("categories", []):
        parent = c.get("parent")
        if parent:
            ppaths = name_paths.get(parent) or []
            if not ppaths:
                continue      # 父级缺失，跳过（与原行为一致）
            ppath = ppaths[-1]
        else:
            ppath = ()
        pid = cat_path.get(ppath)
        if parent and pid is None:
            continue
        existing = next((x for x in db.list_categories(parent_id=pid)
                         if x["name"] == c["name"]), None)
        if existing:
            cid = existing["id"]
        else:
            cid = db.add_category(c["name"], parent_id=pid)
        path = ppath + (c["name"],)
        cat_path[path] = cid
        name_paths.setdefault(c["name"], []).append(path)

    # 4. 领域 ↔ 一级分类 关联（多对一共享）
    for dom_name, l1_names in data.get("domain_links", {}).items():
        did = domain_map.get(dom_name)
        if did is None:
            continue
        for l1 in l1_names:
            cid = cat_path.get((l1,))
            if cid:
                db.link_domain_category(did, cid)

    # 5. 条目（2026-08-18：P2-4 批量插入；P1-1 详情内容去重——内容相同才跳过）
    #    2026-09-17（FR-93/FR-94，v6）：**稳定 ID 优先**——命中同 uuid ⇒ 更新同一条目、不新建；
    #    并处理 `locations`（多位置）。旧包（无 uuid）完全走原路径，行为不变。
    done, skipped, processed, updated = 0, 0, 0, 0
    pending = []
    pending_cf = []   # 2026-09-13：与 pending 一一对应的自定义字段取值（导入后写回）
    pending_cfj = []  # 2026-09-13（3-b）：同序的结构化取值（目录层级型的 令牌+快照名）
    pending_tags = []  # 2026-09-13（1-C-4）：与 pending 一一对应的标签名列表（导入后写回）
    pending_img = []   # 2026-09-13（2-d）：与 pending 一一对应的图集（导入后写回）
    pending_loc = []   # 2026-09-17（FR-94）：与 pending 一一对应的"额外位置"分类 id 列表
    seen_by_cat = {}  # category_id(None=未分类) -> 现有条目"详情内容"键集合
    for ep in add_entries:
        p = ep.get("path") or []
        cid = _resolve_category_by_path(cat_path, p)   # 2026-09-13（4-a）：支持 1~4 层
        e = Entry(category_id=cid, name=ep.get("name", ""),
                  intro=ep.get("intro", ""), origin=ep.get("origin", ""),
                  features=ep.get("features", ""), scenes=ep.get("scenes", ""),
                  works=ep.get("works", ""), image_desc=ep.get("image_desc", ""),
                  prompt_cn=ep.get("prompt_cn", ""), prompt_en=ep.get("prompt_en", ""),
                  image_plan=ep.get("image_plan", ""),
                  is_favorite=int(ep.get("is_favorite", 0)),
                  created_at=str(ep.get("created_at") or ""))   # 2026-09-16（11-7）：保留原创建时间
        processed += 1
        # ---- v6 第 ① 步：稳定 ID 命中 ⇒ 更新同一条目（"修改"同步的真正落地）----
        _uuid = str(ep.get("uuid") or "").strip()
        _hit = db.get_entry_by_uuid(_uuid) if _uuid else None
        if _hit is not None:
            _upd = Entry(id=_hit["id"],
                         # 路径解析失败（目标库缺该分类）时**保留原分类**，避免把条目打成"未分类"
                         category_id=(cid if cid is not None else _hit.get("category_id")),
                         name=e.name, intro=e.intro, origin=e.origin, features=e.features,
                         scenes=e.scenes, works=e.works, image_desc=e.image_desc,
                         prompt_cn=e.prompt_cn, prompt_en=e.prompt_en,
                         image_plan=e.image_plan, is_favorite=e.is_favorite)
            db.update_entry(_upd)
            _apply_v6_aux(db, _hit["id"], ep)                       # 标签/字段替换、图集并入
            _link_extra_locations(db, _hit["id"], cat_path, ep)     # FR-94：多位置
            updated += 1
            if progress_cb:
                progress_cb(processed, total, f"{e.name}（更新）")
            continue
        # ---- v6 第 ③ 步：新增时**沿用包内稳定 ID**（无则 Entry.uuid 留空 ⇒ 自动分配）----
        if _uuid:
            e.uuid = _uuid
        keys = seen_by_cat.setdefault(cid, set())
        if not keys:
            existing = db.list_uncategorized() if cid is None else db.list_entries(cid)
            keys.update(Database.content_key(x) for x in existing)
        key = Database.content_key(e)
        if key in keys:
            skipped += 1
            if progress_cb:
                progress_cb(processed, total, f"{e.name}（重复跳过）")
            continue
        keys.add(key)
        pending.append(e)
        pending_cf.append(ep.get("custom_fields") or {})   # 2026-09-13：自定义字段取值
        pending_cfj.append(ep.get("custom_fields_json") or {})  # 3-b：结构化取值
        pending_tags.append(ep.get("tags") or [])          # 2026-09-13（1-C-4）：标签
        pending_img.append(ep.get("images") or [])         # 2026-09-13（2-d）：图集
        # 2026-09-17（FR-94）：额外位置——此处先解析为分类 id，批量插入后按序建立关联
        pending_loc.append([_c for _c in (
            _resolve_category_by_path(cat_path, _x) for _x in _extra_location_paths(ep))
            if _c is not None])
        done += 1
        if progress_cb:
            progress_cb(processed, total, e.name)
    # 5.5 逆向恢复（2026-09-08 V1.7.0）：deletion_mode="reverse" 时把带快照的删除清单
    #     反向转为新增导入（不执行删除）；与新增共用判重与批量插入。
    recovered = 0
    if deletion_mode == "reverse":
        base = len(add_entries)
        # P1-6：从包内 domain_links/domain_map 收集"一级分类名 → 根目录 id"，
        # 逆向恢复新建一级分类时恢复其原根目录归属，避免"孤儿分类"。
        top_links = {}
        if domain_map:
            for dname, l1_names in data.get("domain_links", {}).items():
                did = domain_map.get(dname)
                if did is None:
                    continue
                for n in l1_names:
                    top_links.setdefault(n, []).append(did)
        for i, de in enumerate(del_entries):
            sn = de.get("snapshot")
            if not isinstance(sn, dict):
                if progress_cb:
                    progress_cb(base + i + 1, total,
                                f"（本包未携带快照，无法恢复）{de.get('name','')}")
                continue
            cid = _ensure_chain_categories(db, de.get("chain") or [],
                                           top_links=top_links)
            e = Entry(category_id=cid, name=sn.get("name") or de.get("name", ""),
                      intro=sn.get("intro", ""), origin=sn.get("origin", ""),
                      features=sn.get("features", ""), scenes=sn.get("scenes", ""),
                      works=sn.get("works", ""), image_desc=sn.get("image_desc", ""),
                      prompt_cn=sn.get("prompt_cn", ""), prompt_en=sn.get("prompt_en", ""),
                      image_plan=sn.get("image_plan", ""),
                      is_favorite=int(sn.get("is_favorite", 0)),
                      created_at=str(sn.get("created_at") or ""),   # 2026-09-16（11-7）：保留原创建时间
                      # 2026-09-17（FR-93）：逆向恢复**沿用快照的稳定 ID**（恢复后仍是同一条目）；
                      # 快照无 uuid（v5 之前）⇒ 留空，由 add_entries_batch 自动分配。
                      uuid=str(sn.get("uuid") or de.get("uuid") or "").strip())
            keys = seen_by_cat.setdefault(cid, set())
            if not keys:
                existing = db.list_uncategorized() if cid is None else db.list_entries(cid)
                keys.update(Database.content_key(x) for x in existing)
            key = Database.content_key(e)
            if key in keys:  # 内容已存在（可能是当初同步删除后留在回收站/未删干净），跳过
                if progress_cb:
                    progress_cb(base + i + 1, total, f"{e.name}（已存在，跳过）")
                continue
            keys.add(key)
            pending.append(e)
            pending_cf.append(sn.get("custom_fields") or {})   # 2026-09-13：快照中的自定义字段取值
            pending_cfj.append(sn.get("custom_fields_json") or {})  # 3-b：快照中的结构化取值
            pending_tags.append(sn.get("tags") or [])          # 2026-09-13（1-C-4）：快照中的标签
            pending_img.append(sn.get("images") or [])         # 2026-09-13（2-d）：快照中的图集
            pending_loc.append([])      # 2026-09-17：逆向恢复不带 locations（保持与 pending 对齐）
            recovered += 1
            if progress_cb:
                progress_cb(base + i + 1, total, f"逆向恢复条目：{e.name}")
    if pending:
        # 2026-09-13（1-A-5 第 3 步·下）：批量插入前记录最大 id，插入后按"id 递增＝插入顺序"
        # 把自定义字段取值写回（仅当 id 数量与 pending 完全对应时才写，避免写错条目）。
        _max_id_before = db.conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM entries").fetchone()[0]
        db.add_entries_batch(pending)
        _restore_pending_custom_fields(db, _max_id_before, pending_cf, pending_cfj)
        _restore_pending_tags(db, _max_id_before, pending_tags)   # 2026-09-13（1-C-4）：标签写回
        _restore_pending_images(db, _max_id_before, pending_img)  # 2026-09-13（2-d）：图集写回
        _link_pending_locations(db, _max_id_before, pending, pending_loc)  # 2026-09-17（FR-94）：多位置

    # 6. 删除同步（2026-08-29 增量备份增强 / 2026-09-08 V1.7.0 加固）：
    #    - 仅 deletion_mode="apply" 时执行；
    #    - 分类：按名称链定位后删除（其子条目保留并转"未分类"，与原库语义一致）；
    #    - 条目：按"详情内容"判重键，一次性建索引后批量移入回收站（可恢复、保留图片）；
    #    - 根目录：按名称删除（仅解除关联，共享分类数据保留）。
    deleted = {"entries": 0, "categories": 0, "domains": 0}
    if deletion_mode == "apply" and del_units:
        base = len(add_entries)
        # 6a 分类
        for i, dc in enumerate(del_categories):
            chain = dc.get("chain") or []
            if chain:
                cid = db.find_category_by_chain(chain)
                if cid is not None:
                    db.delete_category(cid)
                    deleted["categories"] += 1
            if progress_cb:
                progress_cb(base + i + 1, total, f"同步删除分类：{dc.get('name','')}")
        base += len(del_categories)
        # 6b 条目（2026-09-09 审核 P1-3 修复：默认按包内 chain 收敛到分类子树匹配，
        # 链为空或定位失败才全库兜底，减少"同内容多行/多位置"的跨分类误删；
        # 仍为一次建索引 O(N) + 批量单事务移入回收站可恢复）
        if del_entries:
            rows = db.list_all_entries()
            key_index = {}
            cat_of = {}
            for e in rows:
                key_index.setdefault(Database.content_key(e), []).append(e["id"])
                cat_of[e["id"]] = e.get("category_id")

            def _sub_ids(root_cid):
                out = {root_cid}
                stack = [root_cid]
                while stack:
                    cur = stack.pop()
                    for ch in db.list_categories(parent_id=cur):
                        out.add(ch["id"])
                        stack.append(ch["id"])
                return out

            subtree_cache = {}
            hit_ids = []
            for i, de in enumerate(del_entries):
                # 2026-09-17（FR-93）：包内删除项**带稳定 ID** ⇒ 按 uuid 精确删除。
                #   命中即止（不再走内容键）——避免"同内容的其他条目"被误删；
                #   未命中（目标库没有该条目）⇒ 视为"无需删除"，跳过。
                _du = str(de.get("uuid") or "").strip()
                if _du:
                    _dh = db.get_entry_by_uuid(_du)
                    if _dh is not None:
                        hit_ids.append(_dh["id"])
                        if progress_cb:
                            progress_cb(base + i + 1, total,
                                        f"同步删除条目（按稳定 ID）：{de.get('name','')}")
                    elif progress_cb:
                        progress_cb(base + i + 1, total,
                                    f"（按稳定 ID 未命中，跳过）{de.get('name','')}")
                    continue
                chain = de.get("chain") or []
                cid = db.find_category_by_chain(chain) if chain else None
                cand = key_index.get(de.get("content_key", ""), ())
                if cid is not None:
                    sub = subtree_cache.get(cid)
                    if sub is None:
                        sub = _sub_ids(cid)
                        subtree_cache[cid] = sub
                    ids = [x for x in cand if cat_of.get(x) in sub]
                else:
                    ids = list(cand)  # 链为空/定位失败 → 全库兜底
                hit_ids.extend(ids)
                if progress_cb:
                    progress_cb(base + i + 1, total, f"同步删除条目：{de.get('name','')}")
            if hit_ids:
                deleted["entries"] += db.trash_entries_batch(
                    list(dict.fromkeys(hit_ids)), reason="变更包删除同步",
                    log_deletion=False)  # P2-13：接收端不写本机删除日志，避免回声广播
        base += len(del_entries)
        # 6c 根目录
        for i, dd in enumerate(del_domains):
            dom = next((x for x in db.list_domains() if x["name"] == dd.get("name")), None)
            if dom is not None:
                db.delete_domain(dom["id"])
                deleted["domains"] += 1
            if progress_cb:
                progress_cb(base + i + 1, total, f"同步删除根目录：{dd.get('name','')}")

    return {"entries": done, "skipped": skipped,
            # 2026-09-17（FR-93，v6）：按稳定 ID 命中并**更新**的条数（旧包恒为 0）
            "updated": updated,
            "categories": len(data.get("categories", [])),
            "deleted": deleted, "mode": deletion_mode,
            "recovered": recovered}


# ---------------------------------------------------------------------- #
# 自测（2026-09-13，第 4 期 4-a）：JSON v5 四层路径 + 字段定义随包 + 旧版兼容
# ---------------------------------------------------------------------- #
def _json_v5_selftest() -> None:
    """JSON v5 自测：四层分类/条目路径往返、field_defs/标签/图集随包、旧版兼容、判重幂等。"""
    import json as _json
    import os as _os
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_jsonv5_")
    try:
        # ---------- A. 造一份"四层"数据并导出 ---------- #
        db = Database(_os.path.join(tmp, "a.db"))
        out = _os.path.join(tmp, "pack.json")
        try:
            pid = db.add_project("测试项目")
            did = db.add_domain("测试根目录", project_id=pid)
            c1 = db.add_category("一级A", domain_id=did)
            c2 = db.add_category("二级B", parent_id=c1)
            c3 = db.add_category("三级C", parent_id=c2)
            c4 = db.add_category("四级D", parent_id=c3)
            db.add_entry(Entry(category_id=c1, name="条-一级"))
            db.add_entry(Entry(category_id=c2, name="条-二级"))
            db.add_entry(Entry(category_id=c3, name="条-三级"))
            e4 = db.add_entry(Entry(category_id=c4, name="条-四级"))
            key = db.add_field_def("适用条目", "list")
            db.set_field_config(key, {"source_type": "tree_level", "level": "cat1",
                                      "scope": "all", "path_prefix": True})
            db.set_entry_field_value(
                e4, key, "一级A",
                _json.dumps([{"value": f"cat:{c1}", "label": "一级A"}], ensure_ascii=False))
            db.set_entry_tags(e4, ["标签甲"])
            db.add_entry_image(e4, "url", "", "https://example.com/a.png", "外链图")
            db.rename_field_def("intro", "② 我的介绍")      # 内置改名也应随包
            n_export = export_json(db, out)
            assert n_export == 4
        finally:
            db.close()
        data = _json.load(open(out, encoding="utf-8"))
        assert data["version"] == JSON_VERSION == 6
        # 2026-09-17（FR-93，v6）：每条例目都应携带 32 位稳定 ID
        assert all(len(str(e.get("uuid") or "")) == 32 for e in data["entries"]), \
            [e.get("uuid") for e in data["entries"]]
        print("[JSON v6] 导出携带 uuid 通过")
        names = [c["name"] for c in data["categories"]]
        assert names.index("一级A") < names.index("二级B") \
            < names.index("三级C") < names.index("四级D"), "分类须父先子后"
        paths = {e["name"]: e.get("path") for e in data["entries"]}
        assert paths["条-四级"] == ["一级A", "二级B", "三级C", "四级D"]
        assert paths["条-三级"] == ["一级A", "二级B", "三级C"]
        assert any(d["field_key"] == key for d in data["field_defs"])
        assert any(d["field_key"] == "intro" and d["display_name"] == "② 我的介绍"
                   for d in data["field_defs"])

        # ---------- B. 导入到新库：四层重建 + 各归其位 + 定义/取值/标签/图集 ---------- #
        db2 = Database(_os.path.join(tmp, "b.db"))
        try:
            st = import_json(db2, out)
            assert st["entries"] == 4 and st["skipped"] == 0
            cid4 = db2.find_category_by_chain(["一级A", "二级B", "三级C", "四级D"])
            cid3 = db2.find_category_by_chain(["一级A", "二级B", "三级C"])
            cid2 = db2.find_category_by_chain(["一级A", "二级B"])
            cid1 = db2.find_category_by_chain(["一级A"])
            assert None not in (cid1, cid2, cid3, cid4), "四层分类应全部重建"
            assert db2.get_category(cid4)["parent_id"] == cid3
            assert db2.get_category(cid3)["parent_id"] == cid2
            assert [x["name"] for x in db2.list_entries(cid4)] == ["条-四级"]
            assert [x["name"] for x in db2.list_entries(cid3)] == ["条-三级"]
            assert [x["name"] for x in db2.list_entries(cid2)] == ["条-二级"]
            assert [x["name"] for x in db2.list_entries(cid1)] == ["条-一级"]
            # 字段定义（含配置与内置改名）
            d = db2.get_field_def(key)
            assert d and d["field_type"] == "list" and d["config_json"], "自定义字段定义应恢复"
            assert db2.get_field_def("intro")["display_name"] == "② 我的介绍"
            # 字段取值 / 标签 / 图集
            eid4 = db2.list_entries(cid4)[0]["id"]
            assert "一级A" in (db2.get_entry_field_value(eid4, key) or "")
            rows = {r["field_key"]: r for r in db2.list_entry_field_values(eid4)}
            assert _json.loads(rows[key]["value_json"])[0]["value"] == f"cat:{c1}"
            assert db2.list_entry_tag_names(eid4) == ["标签甲"]
            assert len(db2.list_entry_images(eid4)) == 1
            # 幂等：**重复导入同一 v6 包**不新增条目——按稳定 ID 命中 ⇒ 走「更新」路径
            #   （2026-09-17 FR-93：v6 起重复导入是"幂等更新"，count 不变、内容一致；
            #    旧包场景仍表现为"跳过"，见本节 C 段与 incremental_backup 自测）
            st2 = import_json(db2, out)
            assert st2["entries"] == 0 and st2["updated"] == 4 and st2["skipped"] == 0, st2
        finally:
            db2.close()

        # ---------- C. 旧版（v2/v4）文件兼容：仍按原名解析 + 无新键不报错 ---------- #
        legacy = _os.path.join(tmp, "legacy_v2.json")
        with open(legacy, "w", encoding="utf-8") as f:
            _json.dump({
                "version": 2,
                "domains": [{"name": "旧根目录", "sort_order": 0}],
                "domain_links": {"旧根目录": ["旧一级"]},
                "categories": [{"parent": None, "name": "旧一级", "sort_order": 0},
                               {"parent": "旧一级", "name": "旧二级", "sort_order": 0}],
                "entries": [{"name": "旧条目", "path": ["旧一级", "旧二级"]}],
            }, f, ensure_ascii=False)
        db3 = Database(_os.path.join(tmp, "c.db"))
        try:
            st3 = import_json(db3, legacy)
            assert st3["entries"] == 1
            cid = db3.find_category_by_chain(["旧一级", "旧二级"])
            assert cid and [x["name"] for x in db3.list_entries(cid)] == ["旧条目"]
        finally:
            db3.close()

        # ---------- D. 深路径但目标层级缺失 → 回退到最近存在的祖先（不丢条目） ---------- #
        fb = _os.path.join(tmp, "fallback.json")
        with open(fb, "w", encoding="utf-8") as f:
            _json.dump({
                "version": 5,
                "domains": [{"name": "FB根", "sort_order": 0}],
                "domain_links": {"FB根": ["A"]},
                "categories": [{"parent": None, "name": "A", "sort_order": 0},
                               {"parent": "A", "name": "B", "sort_order": 0}],
                "entries": [{"name": "深路径条目", "path": ["A", "B", "C", "D"]}],
            }, f, ensure_ascii=False)
        db4 = Database(_os.path.join(tmp, "d.db"))
        try:
            import_json(db4, fb)
            cid2 = db4.find_category_by_chain(["A", "B"])
            assert [x["name"] for x in db4.list_entries(cid2)] == ["深路径条目"]
            assert db4.list_uncategorized() == []
        finally:
            db4.close()

        # ---------- E. 创建时间随包（2026-09-16 批次 11-7，用户要求 3）---------- #
        #   导出带上 entries[].created_at ⇒ 再导入时**保留原创建时间**；
        #   旧文件无该键 ⇒ 回退"导入时刻"（向后兼容，不报错）。
        ca_src = _os.path.join(tmp, "created_src.db")
        db5 = Database(ca_src)
        _ca_keep = "2003-03-03 03:03:03"
        try:
            _d5 = db5.add_domain("CA根")
            _c5 = db5.add_category("CA类", domain_id=_d5)
            db5.add_entry(Entry(category_id=_c5, name="时间条目", created_at=_ca_keep))
        finally:
            db5.close()
        ca_json = _os.path.join(tmp, "created.json")
        db5 = Database(ca_src)
        try:
            export_json(db5, ca_json)
        finally:
            db5.close()
        with open(ca_json, "r", encoding="utf-8") as f:
            _doc5 = _json.load(f)
        assert (_doc5["entries"][0].get("created_at") or "") == _ca_keep, _doc5["entries"][0]
        ca_dst = _os.path.join(tmp, "created_dst.db")
        db6 = Database(ca_dst)
        try:
            import_json(db6, ca_json)
            _got5 = [x for x in db6.list_all_entries() if x["name"] == "时间条目"]
            assert _got5 and _got5[0]["created_at"] == _ca_keep, _got5
        finally:
            db6.close()
        # 旧文件（无 created_at）导入不报错，且取导入时刻
        legacy = _os.path.join(tmp, "legacy_no_ca.json")
        with open(legacy, "w", encoding="utf-8") as f:
            _json.dump({"version": 5, "domains": [{"name": "LX", "sort_order": 0}],
                        "domain_links": {"LX": ["LX类"]},
                        "categories": [{"parent": None, "name": "LX类", "sort_order": 0}],
                        "entries": [{"name": "旧格式条目", "path": ["LX类"]}]},
                       f, ensure_ascii=False)
        db7 = Database(_os.path.join(tmp, "legacy.db"))
        try:
            import_json(db7, legacy)
            _got7 = [x for x in db7.list_all_entries() if x["name"] == "旧格式条目"]
            assert _got7 and _got7[0]["created_at"], "旧文件导入须回退为导入时刻"
        finally:
            db7.close()

        print("[JSON] v5 四层路径（分类父先子后·条目按最长路径落位·缺失回退）/字段定义随包"
              "（含 list 配置与内置改名）/标签·图集随包/判重幂等/旧版 v2 兼容/创建时间随包 通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _json_v5_selftest()
