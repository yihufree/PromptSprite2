# -*- coding: utf-8 -*-
"""
incremental_backup.py - 每日变更包备份（2026-08-29 施工新增 M4；2026-09-08 V1.7.0 更名+加固）
需求（用户）：
  - 每台电脑有"电脑代号"，每次关闭软件时对"当日新增/修改"的数据做变更包备份；
  - 当日再次打开新增数据后退出，更新当日变更包文件（幂等重生成，非追加）；
  - 文件名 = 变更包_{电脑代号}_{YYYY-MM-DD}_add_{新增/修改条目数}_del_{删除条目数}.json，
    让"正增量/负增量"一眼可读（删除清零、无新增时如 _add_0_del_1380 即为纯删除包）；
  - 包内含 version4 + summary 元信息；可换机导入合并（删除清单导入先进回收站，可恢复）。
  - 变更包覆盖 增/删/改/空分类 全同步；"负增量"（纯删除包）是正常语义而非故障。

设计要点：
  - 变更基准 = 当日 00:00:00（文件内容为当日全部变更，每次退出重新生成）；
  - 以条目为数据主体，附带完整分类祖先链 / 根目录 / 项目类别 / domain_links，
    保证换机导入后分类与归属上下文完整；
  - 同一天重生成时先删除当日旧文件（含旧前缀"增量"遗留），保证"每机每日单文件"。

自测：python -m app.incremental_backup
"""
import json
import os
import re
import shutil
import socket
from datetime import datetime

from . import config
from .parser import json_io


def _now() -> str:
    """2026-09-17（审核 R-4）：统一到 `config.now_str()`。"""
    return config.now_str()


def _sanitize_code(code: str) -> str:
    """电脑代号文件名安全：过滤非法字符与空白（反斜杠/冒号/星号/问号等）"""
    code = re.sub(r'[\\/:*?"<>|\s]+', "_", code or "").strip("_")
    return code or "PC"


def get_computer_code(db) -> str:
    """取电脑代号：meta.settings_computer_code，无则取主机名并写入（用户可在设置中修改）"""
    code = db.get_meta(config.META_COMPUTER_CODE)
    if code:
        return code
    try:
        code = _sanitize_code(socket.gethostname())
    except Exception:
        code = "PC"
    db.set_meta(config.META_COMPUTER_CODE, code)
    return code


def set_computer_code(db, code: str) -> None:
    db.set_meta(config.META_COMPUTER_CODE, _sanitize_code(code))


def incr_dir(db) -> str:
    """增量备份目录：data/backup/incremental/"""
    return os.path.join(os.path.dirname(db.db_path), config.BACKUP_DIR_NAME,
                        config.INCR_DIR_NAME)


def collect_daily_changes(db, day_start: str) -> dict:
    """收集自 day_start（含）以来变更的条目/分类（含空分类）及其上下文，以及删除日志。

    返回：{'entries', 'categories', 'domains', 'domain_links',
           'projects', 'domain_projects', 'deleted_entries', 'deleted_categories',
           'deleted_domains', 'new_categories'}
    """
    entries = db.list_entries_updated_since(day_start)
    cat_ids, dom_ids = set(), set()
    # 1) 变更条目 → 其分类祖先链
    for e in entries:
        cid = e.get("category_id")
        if not cid:
            continue
        for c in json_io._ancestor_chain_cats(db, cid):   # [一级…自身]
            cat_ids.add(c["id"])
            if c["parent_id"] is None:
                for d in db.linked_domains(c["id"]):
                    dom_ids.add(d["id"])
    # 2) 当日新建/修改的分类（含"新增空分类"）→ 自身 + 祖先链（2026-08-29 增强）
    new_categories = 0
    for c in db.list_categories_changed_since(day_start):
        if (c.get("created_at") or "") >= day_start:  # created_at≥当日 = 当日新创建
            new_categories += 1
        for anc in json_io._ancestor_chain_cats(db, c["id"]):
            cat_ids.add(anc["id"])
            if anc["parent_id"] is None:
                for d in db.linked_domains(anc["id"]):
                    dom_ids.add(d["id"])
    cats = [db.get_category(i) for i in sorted(cat_ids) if db.get_category(i)]
    domains = [db.get_domain(i) for i in sorted(dom_ids) if db.get_domain(i)]
    projects = db.list_projects()
    p_by_id = {p["id"]: p for p in projects}
    domain_projects = {}
    for d in domains:
        if d.get("project_id") and d["project_id"] in p_by_id:
            domain_projects[d["name"]] = p_by_id[d["project_id"]]["name"]
    # 3) 删除日志（2026-08-29 增强）
    deleted_entries, deleted_categories, deleted_domains = [], [], []
    for log in db.list_deletions_since(day_start):
        chain = json.loads(log["chain"] or "[]")
        if log["kind"] == "entry":
            _it = {"name": log["name"], "chain": chain,
                   "content_key": log["content_key"],
                   "payload": log.get("payload") or ""}
            # 2026-09-17（FR-93）：删除清单**携带稳定 ID**（取自删除时的完整快照 payload）。
            #   接收端优先按 uuid 精确删除，避免"靠内容键猜"的歧义；
            #   老日志（v5 之前写入的快照无 uuid）不写该键 ⇒ 接收端回退原"内容键"逻辑。
            #   注：`list_deletions_since` 返回的是 **sqlite3.Row**（无 `.get()`），故按下标取列，
            #   并容错"老库缺 payload 列"（IndexError）。
            _u = ""
            try:
                _pl = json.loads(log["payload"] or "{}")
                _u = str(_pl.get("uuid") or "").strip() if isinstance(_pl, dict) else ""
            except Exception:
                _u = ""
            if _u:
                _it["uuid"] = _u
            deleted_entries.append(_it)
        elif log["kind"] == "category":
            deleted_categories.append({"name": log["name"], "chain": chain})
        elif log["kind"] == "domain":
            deleted_domains.append({"name": log["name"]})
    # 2026-09-09（审核 P0-2 修复）：剔除"当前库仍存在同内容条目"的删除项——
    # 覆盖"回收站恢复后当日删除日志未清"与"当日删除后又重建同内容"两种场景，
    # 避免换机导入时把恢复/重建的条目再次删除同步。
    # 2026-09-17（FR-93）：**有稳定 ID 时改用 uuid 判据**——只要该 uuid 仍在库中
    #   （即已恢复或被重建），就不列入删除清单；uuid 更精确，不再因"另有同内容条目"而误留。
    if deleted_entries:
        from .database import Database
        try:
            _rows = db.list_all_entries()
            existing_keys = {Database.content_key(e) for e in _rows}
            existing_uuids = {str(e.get("uuid") or "").strip() for e in _rows}
            _keep = []
            for d in deleted_entries:
                _u = str(d.get("uuid") or "").strip()
                if _u:
                    if _u not in existing_uuids:
                        _keep.append(d)
                elif d.get("content_key") not in existing_keys:
                    _keep.append(d)
            deleted_entries = _keep
        except Exception:
            pass  # 过滤失败时保留原清单，尽力而为
    return {
        "entries": entries,
        "categories": cats,
        "domain_links": json_io._domain_links(db, sorted(cat_ids)),
        "domains": domains,
        "projects": projects,
        "domain_projects": domain_projects,
        "deleted_entries": deleted_entries,
        "deleted_categories": deleted_categories,
        "deleted_domains": deleted_domains,
        "new_categories": new_categories,
    }


def _change_pack_basename(code: str, day: str, add_entries: int,
                          del_entries: int) -> str:
    """变更包文件名：变更包_{电脑代号}_{日期}_add_{新增/修改条目}_del_{删除条目}.json。

    add/del 按"条目"计（与用户"条目数"心智一致；分类/根目录增减见包内 summary）。
    例如：变更包_DESKTOP-053MORJ_2026-09-08_add_0_del_1380.json（负增量/纯删除包）。
    """
    return (f"{config.INCR_FILE_PREFIX}_{code}_{day}"
            f"_add_{int(add_entries)}_del_{int(del_entries)}.json")


def build_json_v4(db, changes: dict, computer_code: str, day: str,
                  include_snapshot: bool = False) -> dict:
    """组装变更包 JSON v4（含 type/summary 元信息 + 删除清单；兼容 json_io.import_json 导入）。

    include_snapshot（2026-09-08 V1.7.0，默认关）：True 时把被删条目完整快照写入
    deleted_entries[].snapshot，供"⑤ 逆向恢复"；开启会使包体积增大且含已删内容（隐私敏感）。
    """
    del_entries = len(changes["deleted_entries"])
    del_categories = len(changes["deleted_categories"])
    del_domains = len(changes["deleted_domains"])
    deleted_entries_out = []
    for de in changes["deleted_entries"]:
        item = {"name": de["name"], "chain": de["chain"],
                "content_key": de["content_key"]}
        # 2026-09-17（FR-93）：删除项**透传稳定 ID** —— 接收端据此精确删除，不靠内容键猜；
        #   无 uuid（v5 之前的老删除日志）则不写该键，接收端回退原逻辑。
        _u = str(de.get("uuid") or "").strip()
        if _u:
            item["uuid"] = _u
        if include_snapshot and de.get("payload"):
            try:
                snap = json.loads(de["payload"])
                item["snapshot"] = snap
            except (TypeError, ValueError):
                pass
        deleted_entries_out.append(item)
    return {
        "version": json_io.JSON_VERSION,
        "type": "change",
        "exported_at": _now(),
        "computer_code": computer_code,
        "day": day,
        "summary": {
            "add_entries": len(changes["entries"]),
            "add_categories": changes.get("new_categories", 0),
            "add_domains": 0,
            "del_entries": del_entries,
            "del_categories": del_categories,
            "del_domains": del_domains,
            "deleted_snapshots": sum(1 for de in deleted_entries_out
                                     if de.get("snapshot")),
        },
        "projects": [{"name": p["name"], "sort_order": p["sort_order"]}
                     for p in changes["projects"]],
        "domain_projects": changes["domain_projects"],
        "domains": [{"name": d["name"], "sort_order": d["sort_order"]}
                    for d in changes["domains"]],
        "domain_links": changes["domain_links"],
        "categories": [{"parent": (db.get_category(c["parent_id"])["name"]
                                   if c["parent_id"] else None),
                        "name": c["name"], "sort_order": c["sort_order"]}
                       for c in changes["categories"]],
        "entries": [json_io._entry_payload(db, e) for e in changes["entries"]],
        "deleted_entries": deleted_entries_out,
        "deleted_categories": changes["deleted_categories"],
        "deleted_domains": changes["deleted_domains"],
        # 2026-09-14（审核修复 P1-C）：变更包补带 field_defs。
        # 条目载荷本来就含 custom_fields/tags/images，缺定义会导致换机导入后
        # 自定义字段"有值无定义"（详情区不渲染）。与全量导出 export_json 口径对齐。
        "field_defs": _field_defs_payload(db),
    }


def _field_defs_payload(db) -> list:
    """取"字段定义"载荷（与 json_io 全量导出同一口径）；失败返回空表（不阻断变更包生成）"""
    try:
        return json_io._field_defs_payload(db)
    except Exception:                       # noqa: BLE001
        return []


def _remove_today_files(directory: str, code: str, day: str) -> None:
    """删除当日已有变更包文件（含旧前缀"增量"遗留），保证"每机每日单文件"。失败静默。"""
    try:
        prefixes = (f"{config.INCR_FILE_PREFIX}_{code}_{day}_",
                    f"{config.INCR_LEGACY_PREFIX}_{code}_{day}_")
        for f in os.listdir(directory):
            if any(f.startswith(p) and f.endswith(".json") for p in prefixes):
                try:
                    os.remove(os.path.join(directory, f))
                except OSError:
                    pass
    except OSError:
        pass


def write_incremental(db) -> dict:
    """生成/更新当日变更包文件（幂等重生成，文件名含增删计数）。返回 {'ok','path','entries','error'}。

    失败不抛异常（供退出流程静默调用，状态栏/日志提示即可）。
    """
    result = {"ok": False, "path": None, "entries": 0, "error": None}
    try:
        code = get_computer_code(db)
        day = datetime.now().strftime("%Y-%m-%d")
        day_start = f"{day} 00:00:00"
        changes = collect_daily_changes(db, day_start)
        db.set_meta(config.META_INCR_LAST_SYNC, _now())
        # 2026-08-29（增强）：条目/分类（含空分类）/删除 三者均无变化才跳过写入
        has_changes = bool(changes["entries"] or changes["categories"]
                           or changes["deleted_entries"] or changes["deleted_categories"]
                           or changes["deleted_domains"])
        if not has_changes:
            result["ok"] = True   # 无当日变化：跳过写入，不算失败
            return result
        # 2026-09-08（V1.7.0）：是否携带被删快照（默认关；开启后包内含已删内容，隐私敏感）
        try:
            include_snap = (db.get_meta(config.META_CHANGE_PACK_SNAPSHOT) or "0") == "1"
        except Exception:
            include_snap = False
        data = build_json_v4(db, changes, code, day, include_snapshot=include_snap)
        directory = incr_dir(db)
        os.makedirs(directory, exist_ok=True)
        # 2026-09-08（V1.7.0）：文件名带 add/del 计数，让正/负增量一眼可读；
        # 同一天重生成先删除当日旧文件，保证"每机每日单文件"。
        _remove_today_files(directory, code, day)
        fname = _change_pack_basename(code, day,
                                      add_entries=len(changes["entries"]),
                                      del_entries=len(changes["deleted_entries"]))
        path = os.path.join(directory, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:  # 保留天数优先取用户设置
            keep = int(db.get_meta(config.META_INCR_KEEP_DAYS) or config.INCR_KEEP_DAYS)
        except (TypeError, ValueError):
            keep = config.INCR_KEEP_DAYS
        _cleanup_old(directory, keep)
        db.prune_deletion_log(7)  # 2026-08-29（增强）：清理已被捕获的删除日志（保留 7 天）
        result.update({"ok": True, "path": path, "entries": len(changes["entries"])})
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _cleanup_old(directory: str, keep_days: int = config.INCR_KEEP_DAYS) -> None:
    """删除超过保留天数的变更包文件（按文件修改时间；同时清理旧前缀"增量"遗留文件）"""
    try:
        now = datetime.now().timestamp()
        for f in os.listdir(directory):
            if not f.endswith(".json"):
                continue
            if not (f.startswith(config.INCR_FILE_PREFIX)
                    or f.startswith(config.INCR_LEGACY_PREFIX)):
                continue
            path = os.path.join(directory, f)
            try:
                if now - os.path.getmtime(path) > keep_days * 86400:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def export_incremental_to(db, src_json_path: str, out_path: str, fmt: str) -> int:
    """把增量 JSON 文件转换为 Excel/HTML 浏览文件（fmt='excel'|'html'）。

    实现：导入临时库（判重合并）后复用现有 excel_io/html_export 导出，
    不改动当前主库；返回导出的条目数。
    """
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ps_incr_export_")
    try:
        tmp_db_path = os.path.join(tmp, "t.db")
        from .database import Database
        db2 = Database(tmp_db_path)
        try:
            json_io.import_json(db2, src_json_path)
            if fmt == "excel":
                from .parser import excel_io
                return excel_io.export_excel(db2, out_path)
            from .parser import html_export
            return html_export.export_html(db2, out_path)
        finally:
            db2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------- #
# 自测
# ---------------------------------------------------------------------- #
def _selftest() -> None:
    import sqlite3
    import tempfile

    from .database import Database
    from .models import Entry

    tmp = tempfile.mkdtemp(prefix="promptsprite_incr_")
    try:
        db = Database(os.path.join(tmp, "t.db"))
        try:
            db.seed_preset_domains()   # 含自动归属
            doms = db.list_domains()
            assert doms, "预置根目录应存在"
            l1 = db.add_category("一级A", domain_id=doms[0]["id"])
            l2 = db.add_category("二级B", parent_id=l1)
            # 1. 电脑代号：主机名
            code = get_computer_code(db)
            assert code and code != "", code
            # 2. 无当日数据 → 跳过
            r = write_incremental(db)
            assert r["ok"] and r["entries"] == 0, r
            # 3. 当日新增 → 生成文件（v4 变更包，文件名含 add/del 计数）
            e1_id = db.add_entry(Entry(name="增量条目1", category_id=l2))
            r2 = write_incremental(db)
            assert r2["ok"] and r2["entries"] == 1 and r2["path"], r2
            path = r2["path"]
            assert os.path.isfile(path)
            assert os.path.basename(path).endswith("_add_1_del_0.json"), os.path.basename(path)
            data = json.load(open(path, encoding="utf-8"))
            # 2026-09-17（FR-93）：变更包升到 **v6**（新增条目带 uuid、删除项带 uuid）
            assert data["version"] == json_io.JSON_VERSION == 6
            assert all(str(e.get("uuid") or "").strip() for e in data["entries"]), \
                "v6 变更包内条目应携带稳定 ID"
            assert data["type"] == "change"
            assert data["summary"]["add_entries"] == 1
            assert data["summary"]["del_entries"] == 0
            assert data["computer_code"] == code
            assert "domain_projects" in data and data["projects"]
            print("[1] 电脑代号/生成变更包文件 OK:", os.path.basename(path))
            # 4. 再增数据 → 重生成（当日全集，幂等；文件名随计数变化且当日仅一个文件）
            e2_id = db.add_entry(Entry(name="增量条目2", category_id=l2))
            r3 = write_incremental(db)
            assert r3["entries"] == 2, r3
            assert os.path.basename(r3["path"]).endswith("_add_2_del_0.json")
            today_files = [f for f in os.listdir(incr_dir(db))
                           if f.startswith(config.INCR_FILE_PREFIX)]
            assert len(today_files) == 1, today_files
            data3 = json.load(open(r3["path"], encoding="utf-8"))
            assert len(data3["entries"]) == 2
            print("[2] 变更包更新(当日全集+当日单文件) OK")
            # 5. 换机导入合并（判重）：新库导入 → 2 条；再导入 → 不新增
            #    2026-09-17（FR-93/v6）：变更包已携带稳定 ID，重复导入按 uuid 命中 ⇒
            #    表现为**幂等更新**（count 不变、内容一致），而非"跳过"。
            db2 = Database(os.path.join(tmp, "t2.db"))
            try:
                st = json_io.import_json(db2, r3["path"])
                assert st["entries"] == 2, st
                st2 = json_io.import_json(db2, r3["path"])
                assert st2["entries"] == 0 and st2["updated"] == 2, st2
                assert db2.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2
                print("[3] 换机导入合并+判重 OK")
            finally:
                db2.close()
            # 6. 变更包 → Excel/HTML 浏览导出
            out_x = os.path.join(tmp, "incr.xlsx")
            out_h = os.path.join(tmp, "incr.html")
            n1 = export_incremental_to(db, r3["path"], out_x, "excel")
            n2 = export_incremental_to(db, r3["path"], out_h, "html")
            assert n1 == 2 and n2 == 2, (n1, n2)
            assert os.path.isfile(out_x) and os.path.isfile(out_h)
            print("[4] 增量导出 Excel/HTML 浏览 OK")
            # 7. 保留天数清理（新前缀 变更包 与 旧前缀 增量 遗留 一并清理）
            from datetime import timedelta
            old1 = os.path.join(incr_dir(db), f"{config.INCR_FILE_PREFIX}_{code}_2000-01-01.json")
            old2 = os.path.join(incr_dir(db), f"{config.INCR_LEGACY_PREFIX}_{code}_2000-01-01.json")
            for old in (old1, old2):
                with open(old, "w", encoding="utf-8") as f:
                    f.write("{}")
                old_ts = (datetime.now() - timedelta(days=31)).timestamp()
                os.utime(old, (old_ts, old_ts))
            _cleanup_old(incr_dir(db), 30)
            assert not os.path.isfile(old1) and not os.path.isfile(old2)
            print("[5] 超期清理(变更包+旧增量遗留) OK")

            # 6. 新增空分类 → 增量文件应包含（2026-08-29 增强）
            empty_cat = db.add_category("空分类X", domain_id=doms[0]["id"])
            r6 = write_incremental(db)
            assert r6["ok"] and r6["path"], r6
            data6 = json.load(open(r6["path"], encoding="utf-8"))
            assert any(c["name"] == "空分类X" for c in data6["categories"]), "空分类应进入增量"
            assert all("deleted_" not in k or data6.get(k) == [] for k in ("deleted_entries",
                                                                           "deleted_categories",
                                                                           "deleted_domains"))
            print("[6] 新增空分类进入增量 OK")

            # 7. 删除同步回环（2026-08-29 增强）
            db.delete_entry(e1_id)                      # 删除条目
            db.delete_category(empty_cat)               # 删除分类（级联）
            r7 = write_incremental(db)
            data7 = json.load(open(r7["path"], encoding="utf-8"))
            assert any(de["name"] == "增量条目1" for de in data7["deleted_entries"])
            assert any(dc["name"] == "空分类X" for dc in data7["deleted_categories"])
            # 场景A：目标库已含 2 条（测试[5]导入）→ 导入 r7 应删除 增量条目1，保留 增量条目2
            db2 = Database(os.path.join(tmp, "t2.db"))   # 测试[5]已关闭，重新打开（幂等迁移）
            try:
                st7 = json_io.import_json(db2, r7["path"])
                assert st7["deleted"]["entries"] >= 1, st7
                assert not any(e["name"] == "增量条目1" for e in db2.list_all_entries())
                assert any(e["name"] == "增量条目2" for e in db2.list_all_entries())
            finally:
                db2.close()
            # 场景B：目标库先同步到"含空分类Y"状态，再导入删除 → 分类被删除
            cat_y = db.add_category("空分类Y", domain_id=doms[0]["id"])
            db.add_entry(Entry(name="分类内条目", category_id=cat_y))
            r_f1 = write_incremental(db)                 # 文件含空分类Y（当日新建）
            db_y = Database(os.path.join(tmp, "ty.db"))
            try:
                json_io.import_json(db_y, r_f1["path"])
                assert db_y.find_category_by_chain(["空分类Y"]) is not None, "目标库应先同步出空分类Y"
                db.delete_category(cat_y)                # 源库删除空分类Y
                r_f2 = write_incremental(db)             # 文件含删除清单
                st_b = json_io.import_json(db_y, r_f2["path"])
                assert st_b["deleted"]["categories"] >= 1, st_b
                assert db_y.find_category_by_chain(["空分类Y"]) is None, "分类删除应已同步"
            finally:
                db_y.close()
            print("[7] 删除同步回环（条目/分类）OK")

            # 8. 兼容：v3 文件无 deleted_* 字段仍可导入
            v3_no_del = {k: v for k, v in data3.items() if not k.startswith("deleted_")}
            p_compat = os.path.join(tmp, "compat.json")
            with open(p_compat, "w", encoding="utf-8") as f:
                json.dump(v3_no_del, f, ensure_ascii=False, indent=2)
            db3 = Database(os.path.join(tmp, "t3.db"))
            try:
                st8 = json_io.import_json(db3, p_compat)
                assert st8["entries"] == 2, st8
                assert st8.get("deleted") == {"entries": 0, "categories": 0, "domains": 0}, st8
                print("[8] v3 无删除字段兼容导入 OK")
            finally:
                db3.close()

            print("=== 增量备份模块自测全部通过 ===")
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
