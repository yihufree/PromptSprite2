# -*- coding: utf-8 -*-
"""
database.py - PromptSprite SQLite 数据访问层
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

职责：
  - 建表（domains / categories / entries / meta）与索引
  - 根目录(Domain)、分类(Category)、条目(Entry) 的增删改查
  - 删除安全机制：删除分类时其下条目自动转入"未分类"(category_id=NULL)
  - 收藏、搜索、统计

自测：python -m app.database
"""
import json  # 2026-08-29（增量备份增强）：删除日志名称链序列化
import os
import re
import shutil  # 2026-09-07：条目"复制到"独立副本时复制关联图片文件
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional

from .config import (data_dir, IMAGES_DIR_NAME, PRESET_DOMAINS, PROJECT_PRESETS,
                     PROJECT_FALLBACK, PROJECT_DOMAIN_MAPPING,
                     META_HOTWORDS, META_HOTWORD_SOURCES,
                     META_DETAIL_HIDDEN_FIELDS)  # 2026-09-16（批次 14）：详情区手动隐藏字段 meta 键
from .config import now_str as _cfg_now_str  # 2026-09-17（审核 R-4）：公共时间工具
from .models import Entry, new_entry_uuid  # 2026-09-17（FR-93）：条目稳定 ID 生成器


def _now() -> str:
    """当前时间字符串（用于 created_at / updated_at）
    2026-09-17（审核 R-4）：实现统一到 `config.now_str()`，本函数保留为薄封装。
    """
    return _cfg_now_str()


# 建表 SQL（schema v3，2026-08-29 四级分类施工）：
#  - 新增 projects 表：项目类别（最高层级），domains.project_id 归属项目类别
#  - 分类为全局共享树（不再归属单一领域），通过 domain_category 实现 领域↔一级分类 多对一关联
#  - 外键：分类删除级联子分类；条目删除分类置 NULL(转入未分类)；领域删除仅解除关联（共享数据保留）
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS domains (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INTEGER DEFAULT 0,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0,
    created_at  TEXT,      -- 2026-08-29（增量备份增强）：分类创建时间（支持"新增空分类"增量）
    updated_at  TEXT       -- 分类最后修改时间（改名/移动等）
);

CREATE TABLE IF NOT EXISTS domain_category (
    domain_id   INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (domain_id, category_id)
);

-- 2026-08-29（增量备份增强）：删除日志——记录被删除的条目/分类/根目录，
-- 供每日增量备份同步"删除操作"到其他电脑（导入时按名称链/内容键应用删除）。
CREATE TABLE IF NOT EXISTS deletion_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,          -- 'entry' | 'category' | 'domain'
    name        TEXT DEFAULT '',        -- 被删对象名称
    chain       TEXT DEFAULT '',        -- JSON 名称链（一级/二级…），entry/category 用
    content_key TEXT DEFAULT '',        -- 条目"详情内容"判重键（entry 用）
    payload     TEXT DEFAULT '',        -- 2026-09-08（V1.7.0）：被删条目完整快照(JSON)，支持"携带被删快照"导出/⑤逆向恢复
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    intro       TEXT DEFAULT '',
    origin      TEXT DEFAULT '',
    features    TEXT DEFAULT '',
    scenes      TEXT DEFAULT '',
    works       TEXT DEFAULT '',
    image_desc  TEXT DEFAULT '',
    prompt_cn   TEXT DEFAULT '',
    prompt_en   TEXT DEFAULT '',
    image_plan  TEXT DEFAULT '',
    image_path  TEXT DEFAULT '',
    is_favorite INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT,
    -- 2026-09-17（FR-93，schema v5）：条目**稳定身份**（uuid4 hex，跨机器一致）。
    --   空串 = 尚未分配（正常不会为空：新增自动生成、老库迁移回填）。
    --   放在最后，与老库 ALTER TABLE ADD COLUMN 的追加位置保持一致（顺序语义不敏感，仅便于核对）。
    uuid        TEXT DEFAULT ''
);

-- 2026-09-17（FR-93，schema v5）：uuid 的**部分唯一索引**——空串（未分配）不参与唯一性约束。
-- 注意：本语句**不能**放进旧库的建表脚本早期执行（旧表尚无 uuid 列）⇒
--   实际由 `_migrate_v4_to_v5()` 在"补列完成之后"执行（本处仅对新库生效）。

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 2026-09-07（条目多位置施工）：条目与分类的"额外关联"（entry_links）。
-- 主挂靠仍存 entries.category_id；本表只存"额外位置"，两表取并集即条目全部可见位置。
-- 任一表被删除行时级联清理（避免悬空关联）。
CREATE TABLE IF NOT EXISTS entry_links (
    entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at  TEXT,
    PRIMARY KEY (entry_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_categories_parent  ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_dc_domain          ON domain_category(domain_id);
CREATE INDEX IF NOT EXISTS idx_dc_category        ON domain_category(category_id);
CREATE INDEX IF NOT EXISTS idx_entries_category   ON entries(category_id);
CREATE INDEX IF NOT EXISTS idx_entries_favorite   ON entries(is_favorite);
CREATE INDEX IF NOT EXISTS idx_entry_links_cat    ON entry_links(category_id);

-- 2026-09-07（第2条改进）：回收站/删除历史——保存被删条目的完整快照，支持恢复
CREATE TABLE IF NOT EXISTS trash (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    payload     TEXT NOT NULL,          -- JSON：完整条目字段 + locations 位置列表 + chain 名称链 + 原 id
    deleted_at  TEXT,
    reason      TEXT DEFAULT ''         -- 删除来源：手动删除 / 级联删除
);
CREATE INDEX IF NOT EXISTS idx_trash_deleted ON trash(deleted_at);

-- 2026-09-13（schema v4，第 1 期地基）字段定义：把详情区"板块"变为可配置元数据。
-- 内置 10 项以 is_builtin=1 预置（可改名、不可删除）；自定义字段用 field_key = custom_xxx。
CREATE TABLE IF NOT EXISTS field_defs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    field_key     TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    field_type    TEXT NOT NULL,          -- text/textarea/link/image/list/number/date/bool/tag/file/audio
    is_builtin    INTEGER DEFAULT 0,
    sort_order    INTEGER DEFAULT 0,
    config_json   TEXT DEFAULT '',
    archived      INTEGER DEFAULT 0,
    created_at    TEXT,
    updated_at    TEXT
);

-- 2026-09-13（schema v4）：自定义字段取值（键值对）。内置 10 字段仍存 entries 既有列，不写入本表。
CREATE TABLE IF NOT EXISTS entry_field_values (
    entry_id   INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    field_key  TEXT NOT NULL,
    value_text TEXT DEFAULT '',
    value_json TEXT DEFAULT '',
    updated_at TEXT,
    PRIMARY KEY (entry_id, field_key)
);
CREATE INDEX IF NOT EXISTS idx_efv_key ON entry_field_values(field_key, entry_id);

-- 2026-09-13（schema v4）：列表框"取值/关联"。本期仅用 ref_type='static'（自定义序列型），
-- 结构一次定义到位，后期补 category/entry/project/domain 时无需改表。
CREATE TABLE IF NOT EXISTS entry_ref_links (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id       INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    field_key      TEXT NOT NULL,
    ref_type       TEXT NOT NULL DEFAULT 'static',
    ref_id         TEXT DEFAULT '',
    label          TEXT DEFAULT '',
    label_snapshot TEXT DEFAULT '',
    sort_order     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_erl_entry ON entry_ref_links(entry_id, field_key);

-- 2026-09-13（schema v4）：标签。namespace 区分"全局标签"(__global__) 与"字段级标签"(field_key)。
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace  TEXT NOT NULL DEFAULT '__global__',
    name       TEXT NOT NULL,
    color      TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(namespace, name)
);

-- 2026-09-13（schema v4）：条目 ↔ 标签（多对多）
CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id   INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at TEXT,
    PRIMARY KEY (entry_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_entry_tags_tag ON entry_tags(tag_id, entry_id);

-- 2026-09-13（第 2 期 2-a）：条目"图集"附加图。
-- 说明：**封面仍存 entries.image_path**（保持既有行为），本表只存"附加图"（is_primary 预留）；
-- kind='local' 时 path=images/entry_{id}_{n}.{ext}；kind='url' 时 source_url 为外链（可"下载到本地"转换）。
CREATE TABLE IF NOT EXISTS entry_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL DEFAULT 'local',
    path       TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    is_primary INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    caption    TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_entry_images_entry ON entry_images(entry_id, sort_order);
"""

# 数据库结构版本（meta 键 schema_version）；
# v1=旧版按领域归属分类，v2=全局分类+领域关联，v3=四级分类（项目类别），v4=字段定义/自定义取值/标签
SCHEMA_VERSION = "4"

# schema v4：内置字段定义（field_key, 显示名, 类型, is_builtin, sort_order）
# 说明：① 条目名称 也在其中（与界面 10 个区块一一对应）；⑩ 图像获取方案 为链接型。
# 2026-09-16（批次 13，用户要求"详情区区块自由排序"）：新增 3 个**虚拟区块**
#   （_tags 标签 / _location 位置 / _time 时间），它们不是 entries 表的真实字段，
#   仅用于"详情区区块排序"——渲染时按 sort_order 决定位置，不存在对应的列。
#   sort_order 用负数，确保排在内置 ②~⑩ 之前（与原有渲染顺序一致）。
_PRESET_FIELDS = (
    ("name", "① 条目名称", "text", 1, 0),
    ("_location", "🧭 位置", "virtual", 1, -3),
    ("_time", "🕒 时间", "virtual", 1, -2),
    ("_tags", "🏷 标签", "virtual", 1, -1),
    ("intro", "② 介绍", "textarea", 1, 1),
    ("origin", "③ 溯源", "textarea", 1, 2),
    ("features", "④ 核心特征", "textarea", 1, 3),
    ("scenes", "⑤ 应用场景", "textarea", 1, 4),
    ("works", "⑥ 代表作", "textarea", 1, 5),
    ("image_desc", "⑦ 代表高清配图", "textarea", 1, 6),
    ("prompt_cn", "⑧ 中文版提示词", "textarea", 1, 7),
    ("prompt_en", "⑨ 英文版提示词", "textarea", 1, 8),
    ("image_plan", "⑩ 图像获取方案", "link", 1, 9),
)

# schema v4：允许的字段类型白名单（防非法类型写入）
FIELD_TYPES = ("text", "textarea", "link", "image", "list",
               "number", "date", "bool", "tag", "file", "audio")

# 2026-09-13（第 3 期 3-a）：列表框（list）字段的"数据源类型"。
#   sequence   = 用户自定义静态序列（**已实现**）；
#   tree_level = 目录层级型（项目类别/根目录/一级/二级；全部节点或指定父节点子树）——**3-b 已实现**；
#   entries    = 条目型（全部/某分类下）——**预留，3-c 实现**。
# 三类共用同一套公共能力（单选/多选、允许新建项、路径前缀、失联项占位），
# 因判别字段与结构一次定义到位，后期补另两类**无需改表、无需数据迁移**。
LIST_SOURCE_TYPES = ("sequence", "tree_level", "entries")

# 2026-09-13（第 3 期 3-b）：「目录层级型」候选节点所在层级（对应四级目录树）。
TREE_LEVELS = ("project", "domain", "cat1", "cat2")
# 「目录层级型」的范围：all = 全库该层级全部节点；nodes = 仅取 node_refs 指定父节点子树内该层级节点。
TREE_SCOPES = ("all", "nodes")


def _ref_token(kind: str, oid: int) -> str:
    """引用对象"稳定令牌"（project:2 / domain:5 / cat:12 / entry:34）——条目里存令牌，改名不影响引用。"""
    return f"{kind}:{int(oid)}"


def _parse_ref_token(token) -> tuple:
    """解析引用令牌 → (kind, id)；非法返回 ("", 0)。"""
    s = str(token or "").strip()
    if ":" not in s:
        return ("", 0)
    kind, _, sid = s.partition(":")
    if kind not in ("project", "domain", "cat", "entry") or not sid.isdigit():
        return ("", 0)
    return (kind, int(sid))


# schema v4：内置字段键（顺序与 _PRESET_FIELDS 一致）；内置字段取值仍存 entries 既有列
_BUILTIN_FIELD_KEYS = tuple(f[0] for f in _PRESET_FIELDS)

# schema v4：标签命名空间——全局标签（条目级，用于跨分类聚合浏览）；
# 字段级标签用相应 field_key 作 namespace（随 `tag` 类型字段一并提供，结构已就绪）
GLOBAL_TAG_NS = "__global__"


class Database:
    """SQLite 数据访问封装（线程内单连接使用）"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(data_dir(), "prompts.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")  # 启用外键约束（每连接需单独开启）
        self.init_schema()
        self._migrate_if_needed()
        self._normalize_dimension_prefixes()

    # ------------------------------------------------------------------ #
    # 基础
    # ------------------------------------------------------------------ #
    def init_schema(self) -> None:
        """建表 + 索引"""
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.commit()

    def _has_column(self, table: str, column: str) -> bool:
        """检查表是否包含某列（用于识别旧版数据库结构）"""
        cols = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == column for r in cols)

    def _migrate_if_needed(self) -> None:
        """结构迁移：v1 → v2 → v3 → v4 按序执行（幂等）＋ v3 增量增强补列。

        - v1→v2：旧版分类按 domain_id 归属单一领域 → 全局分类 + domain_category 多对一关联；
        - v2→v3：新增 projects 表 + domains.project_id 列 + 预置项目类别（四级分类最高层级）；
        - v3 增强（2026-08-29）：categories 加 created_at/updated_at 列（历史数据回填为旧时间戳，
          避免首次增量误把存量分类当"今日新增"）、新建 deletion_log 删除日志表。
        - v3→v4（2026-09-13，第 1 期地基）：新增 field_defs/entry_field_values/entry_ref_links/
          tags/entry_tags 五表（建表由 _SCHEMA_SQL 完成）＋ 预置内置 10 个字段定义；不搬动既有数据。
        - v4→v5（2026-09-17，FR-93）：`entries` 补 `uuid` 列（条目**稳定身份**）＋ 回填 ＋
          部分唯一索引；**不搬动既有数据**，不动 entry_links（多位置关系不变）。
        注：v2→v3 仅做"结构"升级（建表/加列/预置），不移动任何数据；
        根目录→项目类别的"归属分配"由 assign_domains_to_projects() 执行（迁移向导/自动迁移）。
        """
        self._migrate_v1_to_v2()
        self._migrate_v2_to_v3()
        self._ensure_v3_enhancements()
        self._migrate_v3_to_v4()
        # 2026-09-16（批次 13）：补齐详情区虚拟区块定义（_tags/_location/_time），
        # 无论新库旧库都执行（幂等），确保老库也能使用"区块自由排序"功能。
        self.ensure_virtual_blocks()
        # 2026-09-17（FR-93，schema v5）：条目稳定 ID（uuid）——加列 + 回填 + 部分唯一索引。
        self._migrate_v4_to_v5()

    def _migrate_v4_to_v5(self) -> None:
        """v4 → v5（2026-09-17，FR-93：条目**稳定 ID**）——**幂等**。

        做什么：
          1. 为 `entries` 补 `uuid` 列（**仅**老库需要；新库建表时已带该列）；
          2. 为 `uuid` 为空/为 NULL 的条目**逐个回填** `new_entry_uuid()`（uuid4 hex）；
          3. 建立**部分唯一索引** `idx_entries_uuid`（空串不参与唯一性）；
          4. 写 `schema_version = "5"`。

        **不搬动任何既有数据**：不动条目的任何字段，也不动 `entry_links`
        （多位置关系仍按 `entries.id` 记录，uuid 只解决"跨机器认人"）。

        重要（步骤顺序）：索引**必须**在补列之后建立——若把 `CREATE UNIQUE INDEX ... (uuid)`
        放进 `_SCHEMA_SQL` 并在旧库上提前执行，会因"旧表没有 uuid 列"而报错。
        故本方法在**版本已为 5 时也会**补建索引（防止极端情况下索引缺失）。
        """
        if self.get_meta("schema_version") != "5":
            if not self._has_column("entries", "uuid"):
                self.conn.execute("ALTER TABLE entries ADD COLUMN uuid TEXT DEFAULT ''")
                self.conn.commit()
            # 回填：只为"缺失"的条目生成（可重复执行，已分配的不动 ⇒ 幂等）
            _ids = [r["id"] for r in self.conn.execute(
                "SELECT id FROM entries WHERE uuid IS NULL OR uuid = ''").fetchall()]
            for _eid in _ids:
                self.conn.execute("UPDATE entries SET uuid = ? WHERE id = ?",
                                  (new_entry_uuid(), _eid))
            self.conn.commit()
            self.set_meta("schema_version", "5")
        # 索引：无论何时都确保存在（幂等；空串不参与唯一性）
        if self._has_column("entries", "uuid"):
            self.conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_uuid"
                " ON entries(uuid) WHERE uuid <> ''")
            self.conn.commit()

    def _ensure_v3_enhancements(self) -> None:
        """v3 增量备份增强（幂等）：categories 时间戳列 + deletion_log 表 + entry_links 表。
        历史分类回填为固定旧时间戳（1970-01-01），保证首次增量不误报存量分类。
        """
        if not self._has_column("categories", "created_at"):
            self.conn.execute(
                "ALTER TABLE categories ADD COLUMN created_at TEXT")
        if not self._has_column("categories", "updated_at"):
            self.conn.execute(
                "ALTER TABLE categories ADD COLUMN updated_at TEXT")
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS deletion_log ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " kind TEXT NOT NULL, name TEXT DEFAULT '', chain TEXT DEFAULT '',"
            " content_key TEXT DEFAULT '', deleted_at TEXT)")
        # 2026-09-08（V1.7.0）：删除日志补 payload 列（被删条目完整快照，⑤逆向恢复用）
        if not self._has_column("deletion_log", "payload"):
            self.conn.execute(
                "ALTER TABLE deletion_log ADD COLUMN payload TEXT DEFAULT ''")
        # 2026-09-07（条目多位置施工）：存量库幂等补建 entry_links 表 + 索引
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS entry_links ("
            " entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,"
            " category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,"
            " created_at TEXT,"
            " PRIMARY KEY (entry_id, category_id))")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entry_links_cat ON entry_links(category_id)")
        # 2026-09-07（第2条改进）：存量库幂等补建 trash 回收站表 + 索引
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS trash ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT NOT NULL, payload TEXT NOT NULL,"
            " deleted_at TEXT, reason TEXT DEFAULT '')")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trash_deleted ON trash(deleted_at)")
        # 回填：旧数据无时间戳 → 设为固定旧时间（不在任何"今日"范围内）；
        # 2026-08-29（复审优化）：仅当存在空值时执行，避免每次打开全表 UPDATE
        if self.conn.execute(
            "SELECT COUNT(*) FROM categories WHERE created_at IS NULL OR updated_at IS NULL"
        ).fetchone()[0] > 0:
            self.conn.execute(
                "UPDATE categories SET created_at = COALESCE(created_at, '1970-01-01 00:00:00'),"
                " updated_at = COALESCE(updated_at, '1970-01-01 00:00:00')"
            )
        self.conn.commit()

    def _migrate_v1_to_v2(self) -> None:
        """v1 → v2：旧版一级分类(parent_id IS NULL)生成领域关联，再移除 domain_id 遗留列。"""
        if self.get_meta("schema_version") is not None:
            return
        if self._has_column("categories", "domain_id"):
            self.conn.execute(
                "INSERT OR IGNORE INTO domain_category(domain_id, category_id) "
                "SELECT domain_id, id FROM categories WHERE parent_id IS NULL"
            )
            try:
                self.conn.execute("ALTER TABLE categories DROP COLUMN domain_id")
            except sqlite3.OperationalError:
                pass  # 新库无该列时忽略
        self.set_meta("schema_version", "2")

    def _migrate_v2_to_v3(self) -> None:
        """v2 → v3（结构升级，幂等）：projects 表 + domains.project_id 列 + 预置项目类别。

        2026-09-13（schema v4）：本步骤的判定由"== 当前版本"改为"版本已≥v3 即跳过"，
        并把目标版本写死为 "3"，使 v4 库不会重复执行本步骤（v4 升级交给 _migrate_v3_to_v4）。
        """
        if self.get_meta("schema_version") in ("3", "4"):
            return
        # 1. projects 表（_SCHEMA_SQL 已含 CREATE IF NOT EXISTS，确保旧库也有）
        self.conn.executescript(_SCHEMA_SQL)
        # 2. domains 加列（幂等）
        if not self._has_column("domains", "project_id"):
            self.conn.execute(
                "ALTER TABLE domains ADD COLUMN project_id INTEGER "
                "REFERENCES projects(id) ON DELETE SET NULL"
            )
        # 3. 预置项目类别
        self.seed_preset_projects()
        # 4. 版本号
        self.set_meta("schema_version", "3")

    def _migrate_v3_to_v4(self) -> None:
        """v3 → v4（第 1 期地基，幂等）：字段定义等五表 + 预置内置字段。

        新表由 _SCHEMA_SQL 的 CREATE TABLE IF NOT EXISTS 建立（init_schema 已执行），
        本步骤只负责：预置内置 10 个字段定义（仅当 field_defs 为空）＋ 写版本号。
        不搬动任何既有数据（内置 10 字段仍存 entries 既有列）。
        """
        if self.get_meta("schema_version") == "4":
            return
        self.conn.executescript(_SCHEMA_SQL)   # 旧库确保五表存在（幂等）
        self.seed_preset_fields()
        self.set_meta("schema_version", "4")

    def _normalize_dimension_prefixes(self) -> None:
        """归一化一级分类名称：移除"第X维度："前缀（幂等，兼容已按旧名导入的库）"""
        pat = re.compile(r"^第[一二三四五六七八九十百\d]+维度\s*[:：]?\s*")
        rows = self.conn.execute(
            "SELECT id, name FROM categories WHERE parent_id IS NULL"
        ).fetchall()
        changed = False
        for r in rows:
            new_name = pat.sub("", r["name"])
            if new_name != r["name"]:
                self.conn.execute(
                    "UPDATE categories SET name = ?, updated_at = ? WHERE id = ?",
                    (new_name, _now(), r["id"]),  # 2026-08-29：改名同步 updated_at
                )
                changed = True
        if changed:
            self.conn.commit()

    # ------------------------------------------------------------------ #
    # 字段定义 FieldDef（2026-09-13 schema v4，第 1 期地基）
    #   内置 10 项：可改名、不可删除；自定义项 field_key = custom_xxx。
    #   本小节只含"元数据读写"最小集合，界面/搜索等按施工子步骤逐步接入。
    # ------------------------------------------------------------------ #
    def seed_preset_fields(self) -> None:
        """预置内置 10 个字段定义（仅当 field_defs 表为空；幂等）"""
        if self.conn.execute("SELECT COUNT(*) FROM field_defs").fetchone()[0]:
            return
        now = _now()
        for field_key, display_name, field_type, is_builtin, order in _PRESET_FIELDS:
            self.conn.execute(
                "INSERT INTO field_defs(field_key, display_name, field_type, is_builtin,"
                " sort_order, config_json, archived, created_at, updated_at)"
                " VALUES(?,?,?,?,?,'',0,?,?)",
                (field_key, display_name, field_type, is_builtin, order, now, now))
        self.conn.commit()

    # 2026-09-16（批次 13）：为已有数据库补齐"虚拟区块"定义（_tags/_location/_time）。
    #   这三个区块不是 entries 表的真实列，仅用于详情区排序；老库在 schema v4 迁移时
    #   只有 10 个内置字段，需要在此补齐。幂等：已存在则跳过。
    def ensure_virtual_blocks(self) -> None:
        """补齐虚拟区块定义（_tags / _location / _time），幂等。"""
        now = _now()
        existing = {r["field_key"] for r in self.conn.execute(
            "SELECT field_key FROM field_defs").fetchall()}
        for field_key, display_name, field_type, is_builtin, order in _PRESET_FIELDS:
            if field_key.startswith("_") and field_key not in existing:
                self.conn.execute(
                    "INSERT INTO field_defs(field_key, display_name, field_type, is_builtin,"
                    " sort_order, config_json, archived, created_at, updated_at)"
                    " VALUES(?,?,?,?,?,'',0,?,?)",
                    (field_key, display_name, field_type, is_builtin, order, now, now))
        self.conn.commit()

    def list_field_defs(self, include_archived: bool = False) -> List[dict]:
        """按 sort_order 列出字段定义（默认不含已归档项）"""
        sql = "SELECT * FROM field_defs"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY sort_order, id"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get_field_def(self, field_key: str) -> Optional[dict]:
        """按 field_key 取字段定义；不存在返回 None"""
        row = self.conn.execute(
            "SELECT * FROM field_defs WHERE field_key = ?", (field_key,)).fetchone()
        return dict(row) if row else None

    def rename_field_def(self, field_key: str, display_name: str) -> None:
        """重命名字段显示名（仅改显示名，不改数据库列名，故不影响兼容）"""
        self.conn.execute(
            "UPDATE field_defs SET display_name = ?, updated_at = ? WHERE field_key = ?",
            (display_name, _now(), field_key))
        self.conn.commit()

    def upsert_field_def(self, field_key: str, display_name: str,
                         field_type: str = "text", is_builtin: int = 0,
                         sort_order: Optional[int] = None, config_json: str = "",
                         archived: int = 0) -> str:
        """按 field_key 写入/更新字段定义（2026-09-13 第 4 期 4-a：JSON 随包恢复字段定义）。

        - field_key 为空 → ValueError；field_type 不在白名单 → 回退 text（导入容错优先）；
        - 已存在：更新显示名/类型/排序/配置/归档态，**不改 is_builtin**（内置仍是内置）；
        - 不存在：按给定 key 插入（sort_order 缺省时追加到末尾）。
        返回 field_key。
        """
        key = (field_key or "").strip()
        if not key:
            raise ValueError("field_key 不能为空")
        ftype = field_type if field_type in FIELD_TYPES else "text"
        name = (display_name or "").strip() or key
        old = self.get_field_def(key)
        now = _now()
        if old is not None:
            order = old["sort_order"] if sort_order is None else int(sort_order)
            self.conn.execute(
                "UPDATE field_defs SET display_name = ?, field_type = ?, sort_order = ?,"
                " config_json = ?, archived = ?, updated_at = ? WHERE field_key = ?",
                (name, ftype, order, config_json or "", 1 if archived else 0, now, key))
        else:
            order = (self.conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM field_defs").fetchone()[0]
                if sort_order is None else int(sort_order))
            self.conn.execute(
                "INSERT INTO field_defs(field_key, display_name, field_type, is_builtin,"
                " sort_order, config_json, archived, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (key, name, ftype, 1 if is_builtin else 0, order, config_json or "",
                 1 if archived else 0, now, now))
        self.conn.commit()
        return key

    def field_defs_diff(self, incoming: list) -> List[dict]:
        """比较"外来字段定义"与本地差异（2026-09-13：JSON 导入前逐项确认用，只读）。

        返回 `[{field_key, display_name, status, incoming, current}]`：
        - status = "new"：本地没有该字段（可直接新增）；
        - status = "diff"：本地已有但显示名/类型/配置/归档态不同（由使用者决定是否覆盖）；
        - status = "same"：完全一致（无需处理）。
        """
        out = []
        for d in (incoming or []):
            if not isinstance(d, dict) or not str(d.get("field_key") or "").strip():
                continue
            key = str(d["field_key"]).strip()
            inc = {
                "field_key": key,
                "display_name": (d.get("display_name") or key),
                "field_type": (d.get("field_type") or "text"),
                "is_builtin": int(d.get("is_builtin") or 0),
                "sort_order": int(d.get("sort_order") or 0),
                "config_json": d.get("config_json") or "",
                "archived": int(d.get("archived") or 0),
            }
            cur = self.get_field_def(key)
            if cur is None:
                status = "new"
            else:
                same = (str(cur.get("display_name") or "") == str(inc["display_name"])
                        and (cur.get("field_type") or "text") == inc["field_type"]
                        and (cur.get("config_json") or "") == inc["config_json"]
                        and int(cur.get("archived") or 0) == inc["archived"])
                status = "same" if same else "diff"
            out.append({"field_key": key, "display_name": inc["display_name"],
                        "status": status, "incoming": inc, "current": cur})
        return out

    def add_field_def(self, display_name: str, field_type: str = "text",
                      config_json: str = "") -> str:
        """新增一个**自定义字段**定义，返回生成的 field_key（custom_1、custom_2 …）。

        - display_name 为空 → ValueError；
        - field_type 不在 FIELD_TYPES 白名单 → ValueError；
        - sort_order 追加到末尾；is_builtin 恒为 0（自定义字段）；
        - field_key 自动取不冲突的 custom_N，调用方无需关心。
        """
        name = (display_name or "").strip()
        if not name:
            raise ValueError("字段显示名不能为空")
        if field_type not in FIELD_TYPES:
            raise ValueError(f"未知字段类型：{field_type}")
        existing = {r["field_key"] for r in
                    self.conn.execute("SELECT field_key FROM field_defs")}
        n = 1
        while f"custom_{n}" in existing:
            n += 1
        key = f"custom_{n}"
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM field_defs").fetchone()[0]
        now = _now()
        self.conn.execute(
            "INSERT INTO field_defs(field_key, display_name, field_type, is_builtin,"
            " sort_order, config_json, archived, created_at, updated_at)"
            " VALUES(?,?,?,0,?,?,0,?,?)",
            (key, name, field_type, order, config_json or "", now, now))
        self.conn.commit()
        return key

    # ------------------------------------------------------------------ #
    # 字段"数据源"配置（列表框 list）——2026-09-13，第 3 期 3-a
    #   存于 field_defs.config_json；判别字段一次定义到位（sequence/tree_level/entries），
    #   后期补 tree_level/entries 时无需改表、无需迁移。
    # ------------------------------------------------------------------ #
    def get_field_config(self, field_key: str) -> dict:
        """读取字段配置（解析失败/为空返回 {}）"""
        row = self.conn.execute(
            "SELECT config_json FROM field_defs WHERE field_key = ?",
            (field_key,)).fetchone()
        if row is None or not row["config_json"]:
            return {}
        try:
            cfg = json.loads(row["config_json"])
        except (TypeError, ValueError):
            return {}
        return cfg if isinstance(cfg, dict) else {}

    def set_field_config(self, field_key: str, cfg: dict) -> None:
        """写入字段配置（整体覆盖）"""
        self.conn.execute(
            "UPDATE field_defs SET config_json = ?, updated_at = ? WHERE field_key = ?",
            (json.dumps(cfg or {}, ensure_ascii=False), _now(), field_key))
        self.conn.commit()

    def list_field_config(self, field_key: str) -> dict:
        """列表框字段的"数据源配置"（缺省/异常时返回安全默认：序列型、空序列、单选）。

        返回：{source_type, items, multi, allow_new, path_prefix, level, scope, node_refs}
        - items     仅对 `sequence` 有意义（自定义序列）；
        - level/scope/node_refs 仅对 `tree_level`（3-b）有意义；
        - allow_new（允许新建项）只对 `sequence` 有效——层级型/条目型不能凭空造节点。
        """
        cfg = self.get_field_config(field_key) or {}
        src = cfg.get("source_type")
        if src not in LIST_SOURCE_TYPES:
            src = "sequence"
        items = []
        for x in (cfg.get("items") or []):
            s = str(x).strip()
            if s and s not in items:
                items.append(s)
        level = cfg.get("level") if cfg.get("level") in TREE_LEVELS else "cat1"
        scope = cfg.get("scope") if cfg.get("scope") in TREE_SCOPES else "all"
        node_refs = []
        for x in (cfg.get("node_refs") or []):
            s = str(x).strip()
            if _parse_ref_token(s)[0] and s not in node_refs:
                node_refs.append(s)
        return {
            "source_type": src,
            "items": items,
            "multi": bool(cfg.get("multi")),
            "allow_new": bool(cfg.get("allow_new", True)) and src == "sequence",
            "path_prefix": bool(cfg.get("path_prefix")),
            "level": level,
            "scope": scope,
            "node_refs": node_refs,
        }

    def resolve_list_options(self, field_key: str) -> List[dict]:
        """列表框候选值统一入口 → [{"value": 存库值, "label": 显示文本}]。

        - `sequence`（3-a）：用户自定义静态序列；
        - `tree_level`（3-b）：目录层级型（项目类别/根目录/一级/二级），值为稳定令牌；
        - `entries`（3-c）：条目型（全部条目 / 指定节点子树内条目），值为条目稳定令牌。
        """
        cfg = self.list_field_config(field_key)
        if cfg["source_type"] == "sequence":
            return [{"value": x, "label": x} for x in cfg["items"]]
        if cfg["source_type"] == "tree_level":
            return self.tree_level_options(cfg["level"], cfg["scope"],
                                           cfg["node_refs"], cfg["path_prefix"])
        if cfg["source_type"] == "entries":
            return self.entries_options(cfg["scope"], cfg["node_refs"], cfg["path_prefix"])
        return []

    # ------------------------------------------------------------------ #
    # 引用令牌的公共能力（3-b 目录层级型 / 3-c 条目型共用）
    # ------------------------------------------------------------------ #
    def ref_name(self, token: str) -> str:
        """引用令牌 → 当前对象名（"指定节点"摘要、失联提示用）；找不到返回空串。"""
        kind, oid = _parse_ref_token(token)
        table = {"project": "projects", "domain": "domains",
                 "cat": "categories", "entry": "entries"}.get(kind)
        if not table:
            return ""
        row = self.conn.execute(f"SELECT name FROM {table} WHERE id = ?", (oid,)).fetchone()
        return (row["name"] if row else "") or ""

    def _ref_report(self, tokens) -> dict:
        """统计"给定令牌被哪些自定义字段引用"（3-c 删除前提示用，只读）。

        返回 {"total": 引用处数, "fields": [{"field_key","display_name","count"}]}。
        """
        toks = [str(t).strip() for t in (tokens or []) if _parse_ref_token(t)[0]]
        if not toks:
            return {"total": 0, "fields": []}
        # 2026-09-17（审核报告 S-3）：转义 LIKE 通配符 `%` / `_`（及转义符自身 `\`），
        #   并显式声明 ESCAPE —— 否则令牌里若含通配符会导致引用计数**偏多**。
        #   （实际令牌形如 `cat:12` / `entry:345`，本不含这些字符；此处为防御性处理。）
        def _like(t: str) -> str:
            _e = (t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
            return '%%"%s"%%' % _e      # 注意：% 需写成 %%（这里是 Python 格式化，不是 LIKE）
        where = " OR ".join(["value_json LIKE ? ESCAPE '\\'"] * len(toks))
        rows = self.conn.execute(
            "SELECT field_key, COUNT(1) AS n FROM entry_field_values"
            f" WHERE {where} GROUP BY field_key ORDER BY n DESC",
            [_like(t) for t in toks]).fetchall()
        names = {d["field_key"]: d["display_name"]
                 for d in self.list_field_defs(include_archived=True)}
        fields = [{"field_key": r["field_key"],
                   "display_name": names.get(r["field_key"], r["field_key"]),
                   "count": r["n"]} for r in rows]
        return {"total": sum(f["count"] for f in fields), "fields": fields}

    def refs_using_category(self, category_id: int) -> dict:
        """该分类及其全部子分类被引用的情况（3-c：删除分类前提示）"""
        return self._ref_report([_ref_token("cat", c)
                                 for c in self._collect_category_ids(category_id)])

    def refs_using_domain(self, domain_id: int) -> dict:
        """该根目录被引用的情况（3-c：删除根目录前提示）"""
        return self._ref_report([_ref_token("domain", domain_id)])

    def refs_using_entry(self, entry_id: int) -> dict:
        """该条目被引用的情况（3-c：删除条目前提示）"""
        return self._ref_report([_ref_token("entry", entry_id)])

    # ------------------------------------------------------------------ #
    # 目录层级型数据源（列表框 list 的第 2 类数据源）——2026-09-13，第 3 期 3-b
    #   候选节点来自既有四级目录树（项目类别 → 根目录 → 一级 → 二级）；
    #   条目里保存"稳定令牌 + 快照名"，故目录改名不影响引用，
    #   目录被删除后可精确识别为失联（占位显示见 3-b 界面层；删除提示见 3-c）。
    # ------------------------------------------------------------------ #
    def _tree_snapshot(self) -> dict:
        """一次性取全量四级目录数据（项目/根目录/分类/领域↔一级关联），供候选枚举使用。"""
        projects = {r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM projects")}
        domains = {r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM domains")}
        cats = {r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM categories")}
        links: dict = {}    # 分类 → [根目录]
        dlinks: dict = {}   # 根目录 → [一级分类]（3-c：按节点圈定条目用）
        for r in self.conn.execute("SELECT domain_id, category_id FROM domain_category"):
            links.setdefault(r["category_id"], []).append(r["domain_id"])
            dlinks.setdefault(r["domain_id"], []).append(r["category_id"])
        return {"projects": projects, "domains": domains, "cats": cats,
                "links": links, "dlinks": dlinks}

    @staticmethod
    def _cat_chain(cats: dict, cid: int) -> List[int]:
        """分类 id 链：自身 → … → 一级（倒序存放；防环）"""
        out = []
        seen = set()
        while cid and cid in cats and cid not in seen:
            seen.add(cid)
            out.append(cid)
            cid = cats[cid]["parent_id"]
        return out

    def _cat_domains(self, snap: dict, cid: int) -> List[int]:
        """某分类（含其各级父分类）关联的全部根目录 id（多对多：一级分类可挂多个根目录）"""
        out = set()
        for c in self._cat_chain(snap["cats"], cid):
            out |= set(snap["links"].get(c) or [])
        return sorted(out, key=lambda d: (snap["domains"][d]["sort_order"], d))

    def _cat_path(self, snap: dict, cid: int) -> List[str]:
        """分类的显示路径：项目 / 根目录 / 一级 / 二级（取**首个**关联根目录；无关联则省略根目录）"""
        doms = self._cat_domains(snap, cid)
        path = []
        if doms:
            d = snap["domains"][doms[0]]
            p = snap["projects"].get(d["project_id"])
            if p:
                path.append(p["name"])
            path.append(d["name"])
        path += [snap["cats"][c]["name"]
                 for c in reversed(self._cat_chain(snap["cats"], cid))]
        return path

    def _cat_sort_key(self, snap: dict, cid: int) -> tuple:
        """分类排序键（与左侧四级目录顺序一致：项目→根目录→层级→排序）"""
        doms = self._cat_domains(snap, cid)
        d = snap["domains"][doms[0]] if doms else None
        p = snap["projects"].get(d["project_id"]) if d else None
        return (p["sort_order"] if p else 9999,
                d["sort_order"] if d else 9999,
                len(self._cat_chain(snap["cats"], cid)),
                snap["cats"][cid]["sort_order"], cid)

    def _ref_category_ids(self, snap: dict, refs) -> set:
        """给定父节点令牌集合（项目/根目录/分类）→ 其覆盖的**全部分类 id（含子树）**（3-c）"""
        cats = snap["cats"]
        children: dict = {}
        for cid, c in cats.items():
            children.setdefault(c["parent_id"], []).append(cid)

        def subtree(cid):
            out, stack = set(), [cid]
            while stack:
                x = stack.pop()
                if x in out or x not in cats:
                    continue
                out.add(x)
                stack.extend(children.get(x, []))
            return out

        out = set()
        for tok in (refs or ()):
            kind, oid = _parse_ref_token(tok)
            if kind == "cat" and oid in cats:
                out |= subtree(oid)
            elif kind == "domain":
                for cid in (snap["dlinks"].get(oid) or []):
                    out |= subtree(cid)
            elif kind == "project":
                for did, d in snap["domains"].items():
                    if d["project_id"] == oid:
                        for cid in (snap["dlinks"].get(did) or []):
                            out |= subtree(cid)
        return out

    def _tree_ancestors(self, snap: dict, kind: str, oid: int) -> set:
        """节点的"自身 + 全部祖先"令牌集合（用于"指定节点"范围过滤）"""
        if kind == "project":
            return {_ref_token("project", oid)}
        if kind == "domain":
            out = {_ref_token("domain", oid)}
            pid = snap["domains"][oid]["project_id"]
            if pid:
                out.add(_ref_token("project", pid))
            return out
        out = {_ref_token("cat", c) for c in self._cat_chain(snap["cats"], oid)}
        for did in self._cat_domains(snap, oid):
            out.add(_ref_token("domain", did))
            pid = snap["domains"][did]["project_id"]
            if pid:
                out.add(_ref_token("project", pid))
        return out

    def tree_level_options(self, level: str = "cat1", scope: str = "all",
                           node_refs=(), path_prefix: bool = True) -> List[dict]:
        """目录层级型候选值 → [{"value": 稳定令牌, "label": 显示文本}]（3-b）。

        - level：候选节点所在层级（project/domain/cat1/cat2）；
        - scope：all=全库该层级全部节点；nodes=仅取 node_refs 指定父节点子树内该层级节点；
        - path_prefix：True=显示完整层级路径（项目 / 根目录 / 一级 / 二级），False=只显示节点名。
        """
        if level not in TREE_LEVELS:
            level = "cat1"
        if scope not in TREE_SCOPES:
            scope = "all"
        refs = {str(x).strip() for x in (node_refs or []) if _parse_ref_token(x)[0]}
        snap = self._tree_snapshot()
        projects, domains, cats = snap["projects"], snap["domains"], snap["cats"]

        items = []   # (排序键, 令牌, 节点名, 路径列表)
        if level == "project":
            for pid, p in projects.items():
                if scope == "nodes" and not (self._tree_ancestors(snap, "project", pid) & refs):
                    continue
                items.append(((p["sort_order"], 0, 0, pid), _ref_token("project", pid),
                              p["name"], [p["name"]]))
        elif level == "domain":
            for did, d in domains.items():
                if scope == "nodes" and not (self._tree_ancestors(snap, "domain", did) & refs):
                    continue
                p = projects.get(d["project_id"])
                path = ([p["name"]] if p else []) + [d["name"]]
                items.append(((p["sort_order"] if p else 9999, d["sort_order"], 0, did),
                              _ref_token("domain", did), d["name"], path))
        else:
            want_depth = 1 if level == "cat1" else 2
            for cid, c in cats.items():
                if len(self._cat_chain(cats, cid)) != want_depth:
                    continue
                if scope == "nodes" and not (self._tree_ancestors(snap, "cat", cid) & refs):
                    continue
                items.append((self._cat_sort_key(snap, cid), _ref_token("cat", cid),
                              c["name"], self._cat_path(snap, cid)))
        items.sort(key=lambda x: x[0])
        return [{"value": token,
                 "label": (" / ".join(path) if path_prefix else name)}
                for _key, token, name, path in items]

    # ------------------------------------------------------------------ #
    # 条目型数据源（列表框 list 的第 3 类数据源）——2026-09-13，第 3 期 3-c
    #   候选＝全库条目 或 node_refs 指定节点（项目/根目录/分类）子树内的条目；
    #   同样存"稳定令牌 + 快照名"（entry:{id}），条目改名跟随、删除显示失联占位。
    # ------------------------------------------------------------------ #
    def entries_options(self, scope: str = "all", node_refs=(),
                        path_prefix: bool = True) -> List[dict]:
        """条目型候选值 → [{"value": "entry:{id}", "label": 显示文本}]（3-c）。

        - scope：all=全库条目；nodes=仅取 node_refs 指定节点子树内的条目；
        - path_prefix：True=显示"分类路径 / 条目名"，False=只显示条目名。
        """
        if scope not in TREE_SCOPES:
            scope = "all"
        snap = self._tree_snapshot()
        allowed = None
        if scope == "nodes":
            allowed = self._ref_category_ids(snap, node_refs)
            if not allowed:
                return []          # 未指定节点 → 无候选（界面会提示）
        rows = self.conn.execute(
            "SELECT id, name, category_id FROM entries").fetchall()
        items = []
        for r in rows:
            cid = r["category_id"]
            if allowed is not None and cid not in allowed:
                continue
            key = self._cat_sort_key(snap, cid) if cid in snap["cats"] else (9998, 9998, 0, 0, 0)
            path = self._cat_path(snap, cid) if cid in snap["cats"] else []
            items.append((key + (r["name"] or "", r["id"]), r["id"],
                          r["name"] or "", path))
        items.sort(key=lambda x: x[0])
        return [{"value": _ref_token("entry", eid),
                 "label": (" / ".join(path + [name]) if (path_prefix and path) else name)}
                for _key, eid, name, path in items]

    def archive_field_def(self, field_key: str) -> None:
        """归档（=界面上的"删除"）一个自定义字段定义。

        2026-09-13（1-A-5 第 3 步，用户确认的语义）：**只隐藏、不删数据**——
        归档后该字段不再出现在详情区与字段列表（默认），但 `entry_field_values` 中
        已填写的内容全部保留，可通过 restore_field_def() 恢复。
        内置字段（is_builtin=1）不允许归档 → ValueError。
        """
        d = self.get_field_def(field_key)
        if d is None:
            raise ValueError(f"字段不存在：{field_key}")
        if d.get("is_builtin"):
            raise ValueError("内置字段不可删除（可改名）")
        self.conn.execute(
            "UPDATE field_defs SET archived = 1, updated_at = ? WHERE field_key = ?",
            (_now(), field_key))
        self.conn.commit()

    def restore_field_def(self, field_key: str) -> None:
        """恢复一个已归档的自定义字段（取值本就保留，恢复后立即回到详情区）"""
        self.conn.execute(
            "UPDATE field_defs SET archived = 0, updated_at = ? WHERE field_key = ?",
            (_now(), field_key))
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 2026-09-16（批次 14，用户要求"字段管理中各区块可逐个隐藏/显示"）
    #   详情区"手动隐藏"字段：**纯显示偏好**，存 meta 表（逗号分隔的 field_key），
    #   不改 field_defs 表结构、不改导入导出格式；被隐藏字段的内容仍在库中，
    #   照常导出、照常参与搜索，取消隐藏后立即回到详情区。
    #   语义为**硬隐藏**：无论详情字段显示策略如何都不显示（详见 config 注释）。
    # ------------------------------------------------------------------ #
    def get_hidden_field_keys(self) -> set:
        """读取详情区"手动隐藏"的字段键集合（无设置/异常时返回空集合）。

        - ① 名称（field_key="name"）**永不隐藏**：读取时直接剔除，
          保证任何写入路径（含手工改库）都无法把名称项隐藏掉；
        - 只做"去空、去重、剔除 name"，不校验字段是否存在
          （字段可能稍后被归档/删除，此处无需强耦合）。
        """
        try:
            raw = self.get_meta(META_DETAIL_HIDDEN_FIELDS) or ""
        except Exception:                                   # noqa: BLE001
            return set()
        out = set()
        for part in str(raw).split(","):
            k = part.strip()
            if k and k != "name":
                out.add(k)
        return out

    def set_hidden_field_keys(self, keys) -> None:
        """写入详情区"手动隐藏"的字段键集合（排序后逗号拼接，幂等）。

        写入前统一过滤：空串剔除、去重、**剔除 name**（名称项不可隐藏）。
        """
        safe = sorted({str(k).strip() for k in (keys or [])
                       if str(k).strip() and str(k).strip() != "name"})
        self.set_meta(META_DETAIL_HIDDEN_FIELDS, ",".join(safe))

    def _touch_entry_updated_at(self, entry_id: int) -> None:
        """把条目的 `entries.updated_at` 刷新为当前时间（2026-09-14，审核修复 P1-D）。

        为什么需要：每日变更包按 `entries.updated_at >= 当日` 采集条目
        （`incremental_backup.collect_daily_changes`）。标签与自定义字段的取值存在
        **独立表**里，若只写这两张表而不动 `entries.updated_at`，那么"只打了标签 /
        只改了自定义字段"的改动**不会进当日变更包**，换机同步会**静默丢数据**。
        因此所有"标签 / 自定义字段取值"的写入口都要同步 touch 一次本条目的时间戳。
        """
        self.conn.execute("UPDATE entries SET updated_at = ? WHERE id = ?",
                          (_now(), entry_id))

    # ---- 自定义字段取值（entry_field_values）-------------------------- #
    def set_entry_field_value(self, entry_id: int, field_key: str,
                              value_text: str = "", value_json: str = "") -> None:
        """写入/更新一个自定义字段取值（upsert）。

        约定：内置 10 字段仍写 entries 既有列，**不调用本方法**；
        本方法只服务 field_key = custom_xxx 的自定义字段。

        2026-09-17（审核报告 M-2）：把上述"隐式契约"**显式化**——对非 `custom_` 前缀的
        `field_key` 直接抛 `ValueError`。拒绝两类误用：
          · 内置 10 字段（name/intro/…）：它们的值在 `entries` 的列里，写进本表会造成
            "同一字段两处存储"的不一致；
          · 虚拟区块（`_tags` / `_location` / `_time`）：它们只是详情区的显示区块，
            **不是条目的真实字段**，不应有取值。
        现有调用方（UI 的 `_extra_field_getters`、导入路径、自测）本就只传自定义字段，
        故本断言**不改变任何既有行为**，只为将来新增调用点时兜底。
        """
        _k = str(field_key or "")
        if not _k.startswith("custom_"):
            raise ValueError(
                "set_entry_field_value() 仅接受 custom_ 前缀的自定义字段，收到：%r" % field_key)
        self.conn.execute(
            "INSERT INTO entry_field_values(entry_id, field_key, value_text, value_json,"
            " updated_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(entry_id, field_key) DO UPDATE SET"
            " value_text = excluded.value_text, value_json = excluded.value_json,"
            " updated_at = excluded.updated_at",
            (entry_id, _k, value_text or "", value_json or "", _now()))
        self._touch_entry_updated_at(entry_id)   # P1-D：让变更包能采集到这次改动
        self.conn.commit()

    def get_entry_field_value(self, entry_id: int, field_key: str) -> Optional[str]:
        """取单个自定义字段取值文本；未写入返回 None（区别于"写入空串"）"""
        row = self.conn.execute(
            "SELECT value_text FROM entry_field_values WHERE entry_id = ? AND field_key = ?",
            (entry_id, field_key)).fetchone()
        return row["value_text"] if row else None

    def list_entry_field_values(self, entry_id: int) -> List[dict]:
        """列出某条目的全部自定义字段取值行（原始结构，含 value_text/value_json）"""
        rows = self.conn.execute(
            "SELECT field_key, value_text, value_json, updated_at FROM entry_field_values"
            " WHERE entry_id = ? ORDER BY field_key", (entry_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_entry_field_value(self, entry_id: int, field_key: str) -> None:
        """删除某条目的某自定义字段取值（如清空该字段）"""
        self.conn.execute(
            "DELETE FROM entry_field_values WHERE entry_id = ? AND field_key = ?",
            (entry_id, field_key))
        self._touch_entry_updated_at(entry_id)   # P1-D：清空也是一次改动
        self.conn.commit()

    def get_entry_fields(self, entry_id: int) -> dict:
        """统一字段读取层（第 1 期地基）：返回"内置 10 项 + 自定义项"合并字典。

        - 内置项：取自 entries 既有列，**始终返回**（无值则为 ''），与既有读取习惯一致；
        - 自定义项：取自 entry_field_values.value_text，仅返回已写入的项；
        - 内置键优先：若存在同键的取值行（正常不会发生），不覆盖内置值。
        供详情区/导出/复制/备份等统一消费，避免各处各写一份字段清单。
        条目不存在时返回空 dict。
        """
        e = self.get_entry(entry_id)
        if e is None:
            return {}
        out = {k: (e.get(k) or "") for k in _BUILTIN_FIELD_KEYS}
        for r in self.conn.execute(
                "SELECT field_key, value_text FROM entry_field_values"
                " WHERE entry_id = ? ORDER BY field_key", (entry_id,)):
            if r["field_key"] not in out:
                out[r["field_key"]] = r["value_text"] or ""
        return out

    # ------------------------------------------------------------------ #
    # 标签 Tag（2026-09-13 schema v4，第 1 期 1-C-1）
    #   全局标签 namespace=GLOBAL_TAG_NS；字段级标签用 field_key 作 namespace（结构已就绪）。
    #   标签只是"多对多关联"，删除标签不影响条目本身；删除条目会级联清理关联。
    # ------------------------------------------------------------------ #
    def list_tags(self, namespace: str = GLOBAL_TAG_NS) -> List[dict]:
        """列出某命名空间下的全部标签（按名称排序）"""
        rows = self.conn.execute(
            "SELECT * FROM tags WHERE namespace = ? ORDER BY name", (namespace,)).fetchall()
        return [dict(r) for r in rows]

    def list_tags_with_counts(self, namespace: str = GLOBAL_TAG_NS) -> List[dict]:
        """列出标签 + 关联条目数（计数降序、同名升序）——标签云/列表数据源。"""
        rows = self.conn.execute(
            "SELECT t.*, COUNT(et.entry_id) AS entry_count FROM tags t"
            " LEFT JOIN entry_tags et ON et.tag_id = t.id"
            " WHERE t.namespace = ?"
            " GROUP BY t.id ORDER BY entry_count DESC, t.name", (namespace,)).fetchall()
        return [dict(r) for r in rows]

    def get_tag(self, tag_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
        return dict(row) if row else None

    def get_tag_by_name(self, name: str, namespace: str = GLOBAL_TAG_NS) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM tags WHERE namespace = ? AND name = ?",
            (namespace, (name or "").strip())).fetchone()
        return dict(row) if row else None

    def add_tag(self, name: str, namespace: str = GLOBAL_TAG_NS, color: str = "") -> int:
        """新建标签；已存在同名同命名空间则直接复用其 id（幂等）。空名 → ValueError。"""
        n = (name or "").strip()
        if not n:
            raise ValueError("标签名不能为空")
        existing = self.get_tag_by_name(n, namespace)
        if existing:
            return existing["id"]
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO tags(namespace, name, color, created_at, updated_at)"
            " VALUES(?,?,?,?,?)", (namespace, n, color or "", now, now))
        self.conn.commit()
        return cur.lastrowid

    def rename_tag(self, tag_id: int, new_name: str) -> None:
        """重命名标签；目标名已被同命名空间其它标签占用 → ValueError（请改用 merge_tags）。"""
        n = (new_name or "").strip()
        if not n:
            raise ValueError("标签名不能为空")
        t = self.get_tag(tag_id)
        if t is None:
            raise ValueError("标签不存在")
        dup = self.get_tag_by_name(n, t["namespace"])
        if dup and dup["id"] != tag_id:
            raise ValueError(f"已存在同名标签「{n}」，请使用「合并」功能")
        self.conn.execute("UPDATE tags SET name = ?, updated_at = ? WHERE id = ?",
                          (n, _now(), tag_id))
        self.conn.commit()

    def set_field_type(self, field_key: str, field_type: str) -> None:
        """修改**自定义字段**的类型（2026-09-14，审核补充 P5）。

        - 内置 10 项不允许改类型（方案：内置仅可改名）→ 抛 `ValueError`；
        - `field_type` 不在白名单 → 抛 `ValueError`；
        - **只**更新 `field_type` 与 `updated_at`，不动 `config_json`（避免误清"列表框数据源"配置）、
          排序与归档态；类型未变化时直接返回。
        """
        d = self.get_field_def(field_key)
        if d is None:
            raise ValueError("字段不存在")
        if d.get("is_builtin"):
            raise ValueError("内置字段不可修改类型（仅可改名）")
        if field_type not in FIELD_TYPES:
            raise ValueError(f"未知字段类型：{field_type}")
        if d.get("field_type") == field_type:
            return
        self.conn.execute(
            "UPDATE field_defs SET field_type = ?, updated_at = ? WHERE field_key = ?",
            (field_type, _now(), field_key))
        self.conn.commit()

    def merge_tags(self, src_tag_id: int, dst_tag_id: int) -> None:
        """把 src 标签的全部条目关联并入 dst 标签，然后删除 src（同名冲突的合并入口）。"""
        if src_tag_id == dst_tag_id:
            return
        if self.get_tag(src_tag_id) is None or self.get_tag(dst_tag_id) is None:
            raise ValueError("标签不存在")
        # P1-D：受影响的条目（其标签集合发生变化）需要刷新 updated_at，才能进当日变更包
        _ids = [r["entry_id"] for r in self.conn.execute(
            "SELECT entry_id FROM entry_tags WHERE tag_id = ?", (src_tag_id,)).fetchall()]
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at)"
                    " SELECT entry_id, ?, created_at FROM entry_tags WHERE tag_id = ?",
                    (dst_tag_id, src_tag_id))
                self.conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (src_tag_id,))
                self.conn.execute("DELETE FROM tags WHERE id = ?", (src_tag_id,))
                for _eid in _ids:
                    self._touch_entry_updated_at(_eid)
        except Exception:
            self.conn.rollback()
            raise

    def delete_tag(self, tag_id: int) -> None:
        """删除标签及其全部条目关联（**不影响条目本身**）"""
        # P1-D：被摘掉标签的条目需刷新 updated_at，否则这次改动进不了当日变更包
        _ids = [r["entry_id"] for r in self.conn.execute(
            "SELECT entry_id FROM entry_tags WHERE tag_id = ?", (tag_id,)).fetchall()]
        self.conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (tag_id,))
        self.conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        for _eid in _ids:
            self._touch_entry_updated_at(_eid)
        self.conn.commit()

    def purge_unused_tags(self, namespace: str = GLOBAL_TAG_NS) -> int:
        """清理该命名空间下"无任何条目关联"的标签，返回清理数量。"""
        cur = self.conn.execute(
            "DELETE FROM tags WHERE namespace = ? AND id NOT IN"
            " (SELECT tag_id FROM entry_tags)", (namespace,))
        self.conn.commit()
        return cur.rowcount or 0

    # ---- 条目 ↔ 标签 ---- #
    def list_entry_tags(self, entry_id: int, namespace: str = GLOBAL_TAG_NS) -> List[dict]:
        """某条目已打的标签（含 id/name/color，按名称排序）"""
        rows = self.conn.execute(
            "SELECT t.* FROM tags t JOIN entry_tags et ON et.tag_id = t.id"
            " WHERE et.entry_id = ? AND t.namespace = ? ORDER BY t.name",
            (entry_id, namespace)).fetchall()
        return [dict(r) for r in rows]

    def set_entry_tags(self, entry_id: int, names, namespace: str = GLOBAL_TAG_NS) -> None:
        """按名称列表**整体替换**某条目的标签（缺失的标签自动创建）。用于详情区标签保存。"""
        cleaned, seen = [], set()
        for n in (names or []):
            s = (n or "").strip()
            if s and s not in seen:
                seen.add(s)
                cleaned.append(s)
        try:
            with self.conn:
                self.conn.execute(
                    "DELETE FROM entry_tags WHERE entry_id = ? AND tag_id IN"
                    " (SELECT id FROM tags WHERE namespace = ?)", (entry_id, namespace))
                now = _now()
                for s in cleaned:
                    tid = self.conn.execute(
                        "SELECT id FROM tags WHERE namespace = ? AND name = ?",
                        (namespace, s)).fetchone()
                    if tid is None:
                        cur = self.conn.execute(
                            "INSERT INTO tags(namespace, name, color, created_at, updated_at)"
                            " VALUES(?,?,'',?,?)", (namespace, s, now, now))
                        tid = cur.lastrowid
                    else:
                        tid = tid["id"]
                    self.conn.execute(
                        "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at)"
                        " VALUES(?,?,?)", (entry_id, tid, now))
                self._touch_entry_updated_at(entry_id)   # P1-D
        except Exception:
            self.conn.rollback()
            raise

    def add_entry_tag(self, entry_id: int, name: str,
                      namespace: str = GLOBAL_TAG_NS) -> int:
        """给条目追加一个标签（不存在则新建），返回 tag_id"""
        tid = self.add_tag(name, namespace)
        self.conn.execute(
            "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at) VALUES(?,?,?)",
            (entry_id, tid, _now()))
        self._touch_entry_updated_at(entry_id)           # P1-D
        self.conn.commit()
        return tid

    def remove_entry_tag(self, entry_id: int, tag_id: int) -> None:
        self.conn.execute(
            "DELETE FROM entry_tags WHERE entry_id = ? AND tag_id = ?", (entry_id, tag_id))
        self._touch_entry_updated_at(entry_id)           # P1-D
        self.conn.commit()

    def list_entries_by_tags(self, tag_ids, mode: str = "and",
                             namespace: str = GLOBAL_TAG_NS) -> List[dict]:
        """按标签**跨分类**查询条目。

        - mode="and"：同时包含全部给定标签；
        - mode="or" ：包含任一给定标签。
        返回与其它 list_* 一致的 entries 行（按 updated_at DESC, id 排序）；无 tag_ids 返回 []。
        """
        ids = [int(t) for t in (tag_ids or [])]
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        if mode == "and":
            sql = ("SELECT e.* FROM entries e JOIN entry_tags et ON et.entry_id = e.id"
                   " WHERE et.tag_id IN (" + ph + ")"
                   " GROUP BY e.id HAVING COUNT(DISTINCT et.tag_id) = ?"
                   " ORDER BY e.updated_at DESC, e.id")
            params = tuple(ids) + (len(ids),)
        else:
            sql = ("SELECT DISTINCT e.* FROM entries e JOIN entry_tags et ON et.entry_id = e.id"
                   " WHERE et.tag_id IN (" + ph + ")"
                   " ORDER BY e.updated_at DESC, e.id")
            params = tuple(ids)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def list_tags_for_entries(self, entry_ids) -> dict:
        """**批量**取多个条目的标签名（2026-09-14，性能优化：消除逐条查询的 N+1）。

        返回 {entry_id: [标签名, ...]}（按标签名排序）；未传/空 → {}。
        按 IN 分批（每批 500）以避开 SQLite 变量数量限制。
        用途：条目区渲染时一次性预取标签（实测 100 条可省约 2 秒）。
        """
        ids = []
        for x in (entry_ids or []):
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        if not ids:
            return {}
        out = {}
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            ph = ",".join("?" * len(batch))
            rows = self.conn.execute(
                "SELECT et.entry_id AS eid, t.name AS name FROM entry_tags et"
                " JOIN tags t ON t.id = et.tag_id"
                " WHERE et.entry_id IN (" + ph + ")"
                " ORDER BY et.entry_id, t.name", batch).fetchall()
            for r in rows:
                out.setdefault(r["eid"], []).append(r["name"])
        return out

    def set_entry_tags_bulk(self, assignments, mode: str = "append",
                            touch_updated: bool = True,
                            namespace: str = GLOBAL_TAG_NS) -> dict:
        """批量写入"条目 → 标签名列表"（**单事务、幂等**）（2026-09-14 12:30，阶段 1）。

        assignments: {entry_id: [标签名, ...]} 或 [(entry_id, [标签名, ...]), ...]
        mode: "append"=并入既有标签（默认） / "replace"=替换该条目的全部标签
        touch_updated: 是否刷新 entries.updated_at
            - True（默认）：正常用户操作（改动应进当日变更包，与 P1-D 修复一致）；
            - **False**：**预置数据初始化**（阶段 1 离线打标）——预置标签随库分发，
              不应被当成"用户今日修改"而灌进当日变更包。
        返回：{'entries': 处理条目数, 'links': 新增关联数, 'tags_created': 新建标签数}
        """
        items = list(assignments.items()) if isinstance(assignments, dict) \
            else list(assignments or [])
        items = [(int(eid), list(names or [])) for eid, names in items]
        if not items:
            return {"entries": 0, "links": 0, "tags_created": 0}

        ts = _now()
        # 1) 规范化 + 汇总全部标签名（跨条目去重、保持出现顺序）
        norm, all_names, seen = [], [], set()
        for eid, names in items:
            cleaned = []
            for n in names:
                s = (n or "").strip()
                if s and s not in cleaned:
                    cleaned.append(s)
                    if s not in seen:
                        seen.add(s)
                        all_names.append(s)
            norm.append((eid, cleaned))

        with self.conn:
            # 2) 确保标签存在（幂等）
            created, id_of = 0, {}
            for s in all_names:
                row = self.conn.execute(
                    "SELECT id FROM tags WHERE namespace = ? AND name = ?",
                    (namespace, s)).fetchone()
                if row:
                    id_of[s] = row["id"]
                else:
                    id_of[s] = self.conn.execute(
                        "INSERT INTO tags(namespace, name, color, created_at, updated_at)"
                        " VALUES(?,?,'',?,?)", (namespace, s, ts, ts)).lastrowid
                    created += 1
            # 3) 写关联（INSERT OR IGNORE → 重复执行不产生重复行）
            links = 0
            for eid, cleaned in norm:
                if mode == "replace":
                    self.conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (eid,))
                for s in cleaned:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at)"
                        " VALUES(?,?,?)", (eid, id_of[s], ts))
                    links += cur.rowcount or 0
            # 4) 时间戳（预置数据初始化时不动，避免污染当日变更包）
            if touch_updated:
                for eid, _c in norm:
                    self.conn.execute("UPDATE entries SET updated_at = ? WHERE id = ?", (ts, eid))
        return {"entries": len(norm), "links": links, "tags_created": created}

    # ------------------------------------------------------------------ #
    # 热点词表（2026-09-14 11:15，阶段 4 之 4-a）
    #   存于 meta 表（键 META_HOTWORDS / META_HOTWORD_SOURCES），值为 JSON 数组字符串
    #   （**不新增数据库表、不改 schema 版本**，纯 meta 读写）。
    #   用途：供"自动打标"的 ⑧ 热点词维度使用（命中即作为标签；每条例目最多取 1 个）。
    #   与 tags 表无关：tags 是"已被使用过的标签"，热点词是"待匹配的词条库"。
    # ------------------------------------------------------------------ #
    HOTWORD_MAX_LEN = 24        # 单个词条最大长度（字符）；超长视为非法（防误导入整段文本）
    HOTWORD_MAX_COUNT = 2000    # 词条总数上限（防误导入超大文件）

    @staticmethod
    def normalize_hotword(text) -> str:
        """规范化单个热点词：去首尾空白 + 压缩内部连续空白；非法（空串/超长）返回 ""。

        说明：**不做大小写统一**（中文无需；英文热点词保留用户原样，避免"Neon"被改成"neon"
        后与用户预期不符），匹配阶段由引擎自行决定是否忽略大小写。
        """
        s = " ".join(str(text or "").split())
        if not s or len(s) > Database.HOTWORD_MAX_LEN:
            return ""
        return s

    @staticmethod
    def parse_hotwords_text(text) -> List[str]:
        """把"粘贴 / 导入的多行文本"解析为热点词列表（去重、丢弃非法项）。

        分隔规则：换行 / 逗号（, ，）/ 顿号（、）/ 分号（; ；）/ 竖线（|）/ 制表符。
        """
        parts = re.split(r"[\r\n,，、;；|\t]+", str(text or ""))
        out, seen = [], set()
        for p in parts:
            s = Database.normalize_hotword(p)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def list_hotwords(self) -> List[str]:
        """读取热点词表（有序、已去重）。读不到 / 非法 JSON 时返回 []（不抛异常）。"""
        raw = self.get_meta(META_HOTWORDS) or ""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        out, seen = [], set()
        for x in data:
            s = self.normalize_hotword(x)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def count_hotwords(self) -> int:
        """热点词条数（供界面显示）。"""
        return len(self.list_hotwords())

    def set_hotwords(self, words) -> int:
        """**整体替换**热点词表（去重、丢弃非法项、截断到上限），返回写入条数。"""
        return len(self.add_hotwords(words, replace=True)["total_list"])

    def add_hotwords(self, words, replace: bool = False) -> dict:
        """新增（默认**并集追加**，幂等）/ 整体替换热点词表。

        返回：{'added': [新增词...], 'added_count': int, 'skipped': int（已存在或超上限）,
               'invalid': int（空/超长被丢弃）, 'total': int（写入后总条数）,
               'total_list': [写入后的完整词表]}
        """
        cur = [] if replace else self.list_hotwords()
        have = set(cur)
        added, skipped, invalid = [], 0, 0
        for x in (words or []):
            s = self.normalize_hotword(x)
            if not s:
                invalid += 1
                continue
            if s in have:
                skipped += 1
                continue
            if len(cur) + len(added) >= self.HOTWORD_MAX_COUNT:
                skipped += 1                     # 超出上限的词条计入"跳过"
                continue
            added.append(s)
            have.add(s)
        if added or replace:
            self.set_meta(META_HOTWORDS, json.dumps(cur + added, ensure_ascii=False))
        total_list = cur + added
        return {"added": added, "added_count": len(added), "skipped": skipped,
                "invalid": invalid, "total": len(total_list), "total_list": total_list}

    def remove_hotwords(self, words) -> int:
        """按名删除热点词（幂等），返回**实际删除条数**（不存在的不计）。"""
        cur = self.list_hotwords()
        targets = {self.normalize_hotword(x) for x in (words or [])}
        targets.discard("")
        if not targets:
            return 0
        keep = [w for w in cur if w not in targets]
        removed = len(cur) - len(keep)
        if removed:
            self.set_meta(META_HOTWORDS, json.dumps(keep, ensure_ascii=False))
        return removed

    def list_hotword_sources(self) -> List[str]:
        """读取"热点词来源网址"列表（仅保留 http/https、去重、按填写顺序）。"""
        raw = self.get_meta(META_HOTWORD_SOURCES) or ""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        out, seen = [], set()
        for x in data:
            s = str(x or "").strip()
            if s and s.lower().startswith(("http://", "https://")) and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def set_hotword_sources(self, urls) -> int:
        """**整体替换**来源网址列表（仅保留 http/https、去重），返回写入条数。"""
        out, seen = [], set()
        for x in (urls or []):
            s = str(x or "").strip()
            if s and s.lower().startswith(("http://", "https://")) and s not in seen:
                seen.add(s)
                out.append(s)
        self.set_meta(META_HOTWORD_SOURCES, json.dumps(out, ensure_ascii=False))
        return len(out)

    # ------------------------------------------------------------------ #
    # 条目图集 entry_images（2026-09-13，第 2 期 2-a）
    #   封面＝entries.image_path（既有行为完全不变）；本表只存"附加图"。
    # ------------------------------------------------------------------ #
    def list_entry_images(self, entry_id: int) -> List[dict]:
        """列出某条目的附加图（按 sort_order, id）"""
        rows = self.conn.execute(
            "SELECT * FROM entry_images WHERE entry_id = ? ORDER BY sort_order, id",
            (entry_id,)).fetchall()
        return [dict(r) for r in rows]

    def count_entry_images(self, entry_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM entry_images WHERE entry_id = ?", (entry_id,)).fetchone()[0]

    def _gallery_next_index(self, entry_id: int) -> int:
        """图集序号基数（用于 entry_{id}_{n}.{ext} 命名，避免与既有图集图重名）"""
        row = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM entry_images WHERE entry_id = ?",
            (entry_id,)).fetchone()
        return int(row[0] or 0)

    def add_entry_image(self, entry_id: int, kind: str = "local", path: str = "",
                        source_url: str = "", caption: str = "") -> int:
        """新增一张附加图（kind='local' 本地文件 / 'url' 外链），返回 id"""
        if kind not in ("local", "url"):
            raise ValueError("图片类型只能是 local 或 url")
        if kind == "url" and not (source_url or "").strip():
            raise ValueError("外链图片必须提供网址")
        order = self._gallery_next_index(entry_id)
        cur = self.conn.execute(
            "INSERT INTO entry_images(entry_id, kind, path, source_url, is_primary,"
            " sort_order, caption, created_at) VALUES(?,?,?,?,0,?,?,?)",
            (entry_id, kind, path or "", source_url or "", order, caption or "", _now()))
        self.conn.commit()
        return cur.lastrowid

    def remove_entry_image(self, image_id: int, purge_file: bool = True) -> None:
        """删除一张附加图（kind='local' 且 purge_file=True 时同步删除本地文件）"""
        row = self.conn.execute(
            "SELECT * FROM entry_images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            return
        if purge_file and row["kind"] == "local" and row["path"]:
            self._remove_image_file(row["path"])
        self.conn.execute("DELETE FROM entry_images WHERE id = ?", (image_id,))
        self.conn.commit()

    def set_entry_image_local(self, image_id: int, path: str, source_url: str = "") -> None:
        """把一张外链图登记为"已下载到本地"（kind→local；保留 source_url 以备追溯）"""
        self.conn.execute(
            "UPDATE entry_images SET kind = 'local', path = ?,"
            " source_url = COALESCE(NULLIF(?, ''), source_url) WHERE id = ?",
            (path or "", source_url or "", image_id))
        self.conn.commit()

    def gallery_file_rel(self, entry_id: int, ext: str) -> str:
        """生成下一个图集本地文件名（相对 data/）：images/entry_{id}_{n}{ext}，保证不与在用文件冲突"""
        used = {r["path"] for r in self.list_entry_images(entry_id) if r["path"]}
        e = str(ext or "")
        if e and not e.startswith("."):
            e = "." + e
        e = e or ".png"
        n = self._gallery_next_index(entry_id)
        while True:
            rel = os.path.join(IMAGES_DIR_NAME, f"entry_{entry_id}_{n}{e}")
            if rel not in used and not os.path.isfile(os.path.join(data_dir(), rel)):
                return rel
            n += 1

    def swap_entry_image_order(self, entry_id: int, image_id: int, delta: int) -> bool:
        """图集内上移/下移（只在本条目的附加图之间；越界返回 False）"""
        ids = [r["id"] for r in self.list_entry_images(entry_id)]
        if image_id not in ids:
            return False
        i = ids.index(image_id)
        j = i + delta
        if j < 0 or j >= len(ids):
            return False
        for k, rid in enumerate(ids):   # 先物化为 0..n-1，避免并列 sort_order 时交换无效
            self.conn.execute("UPDATE entry_images SET sort_order = ? WHERE id = ?", (k, rid))
        self.conn.execute("UPDATE entry_images SET sort_order = ? WHERE id = ?", (j, ids[i]))
        self.conn.execute("UPDATE entry_images SET sort_order = ? WHERE id = ?", (i, ids[j]))
        self.conn.commit()
        return True

    def close(self) -> None:
        self.conn.close()

    def seed_preset_domains(self) -> None:
        """写入预置根目录（仅当表为空时）；随后按映射自动归入项目类别（新库开箱即用四级）。"""
        if self.list_domains():
            return
        for name in PRESET_DOMAINS:
            self.add_domain(name)
        self.assign_domains_to_projects(PROJECT_DOMAIN_MAPPING)

    # ------------------------------------------------------------------ #
    # 元信息
    # ------------------------------------------------------------------ #
    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 排序（2026-09-11 08:52 用户要求 2）：分类列"上移/下移"
    # ------------------------------------------------------------------ #
    _ORDER_TABLES = ("projects", "domains", "categories", "field_defs")  # 允许重排的表白名单（防拼接外部输入）

    def _order_value(self, table: str, row_id: int) -> int:
        """取某行的 sort_order（不存在时返回 0）"""
        row = self.conn.execute(
            f"SELECT sort_order FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return int(row["sort_order"] or 0) if row else 0

    def swap_order(self, table: str, ordered_ids: List[int], item_id: int,
                   delta: int) -> bool:
        """把 item_id 在其所在列内与相邻项对调排序号（-1=上移，+1=下移）。

        ordered_ids 为该列当前的可见顺序（与界面渲染顺序一致）。
        - 常规：只对调两项的 sort_order 值，不动其它行（避免影响共享分类在其它根目录中的顺序）；
        - 两项 sort_order 相同（历史并列数据）：先把可见列表按当前顺序物化为 0..n-1 再对调，
          保证"上移/下移"确实生效；
        - item_id 不在列表内或已到边界 → 返回 False，不写库。
        """
        if table not in self._ORDER_TABLES:
            raise ValueError(f"不支持重排的表：{table}")
        if item_id not in ordered_ids:
            return False
        i = ordered_ids.index(item_id)
        j = i + delta
        if j < 0 or j >= len(ordered_ids):
            return False
        a, b = ordered_ids[i], ordered_ids[j]
        va, vb = self._order_value(table, a), self._order_value(table, b)
        if va == vb:
            for k, rid in enumerate(ordered_ids):
                self.conn.execute(
                    f"UPDATE {table} SET sort_order = ? WHERE id = ?", (k, rid))
            va, vb = i, j
        self.conn.execute(
            f"UPDATE {table} SET sort_order = ? WHERE id = ?", (vb, a))
        self.conn.execute(
            f"UPDATE {table} SET sort_order = ? WHERE id = ?", (va, b))
        self.conn.commit()
        return True

    # ------------------------------------------------------------------ #
    # 根目录 Domain
    # ------------------------------------------------------------------ #
    def add_domain(self, name: str, project_id: Optional[int] = None) -> int:
        """新建根目录；project_id 指定其所属项目类别（四级分类，可为空=未分配）"""
        order = self.conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM domains").fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO domains(name, sort_order, project_id) VALUES(?, ?, ?)",
            (name, order, project_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def rename_domain(self, domain_id: int, new_name: str) -> None:
        self.conn.execute("UPDATE domains SET name = ? WHERE id = ?", (new_name, domain_id))
        self.conn.commit()

    def get_domain(self, domain_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
        return dict(row) if row else None

    def list_domains(self, project_id: Optional[int] = None) -> List[dict]:
        """列出根目录；project_id 非空时仅返回该项目的根目录（四级分类过滤）"""
        if project_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM domains WHERE project_id = ? ORDER BY sort_order, id",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM domains ORDER BY sort_order, id").fetchall()
        return [dict(r) for r in rows]

    def list_unassigned_domains(self) -> List[dict]:
        """project_id 为空的根目录（迁移分配前的存量 / 新建未指定项目的）"""
        rows = self.conn.execute(
            "SELECT * FROM domains WHERE project_id IS NULL ORDER BY sort_order, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_domain(self, domain_id: int) -> dict:
        """删除根目录：仅解除 领域↔一级分类 关联（分类与条目为共享数据，不删除）。
        返回受影响统计 {'categories': n, 'entries': m}（n/m 为该领域视角下的数量）
        """
        stat = self.count_domain_items(domain_id)
        d = self.get_domain(domain_id)
        if d:
            self._log_deletion("domain", d["name"])  # 2026-08-29：记录根目录删除日志
        self.conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
        self.conn.commit()
        return stat

    def count_domain_items(self, domain_id: int) -> dict:
        """统计某根目录关联的分类数（一级+子分类）与条目数（用于删除确认弹窗提示）"""
        cat_ids = self._domain_category_ids(domain_id)
        entries = 0
        if cat_ids:
            ph = ",".join("?" * len(cat_ids))
            entries = self.conn.execute(
                f"SELECT COUNT(*) FROM entries WHERE category_id IN ({ph})", cat_ids
            ).fetchone()[0]
        return {"categories": len(cat_ids), "entries": entries}

    def _domain_category_ids(self, domain_id: int) -> List[int]:
        """某领域关联的一级分类及其全部子分类 id 集合"""
        l1_rows = self.conn.execute(
            "SELECT c.id FROM categories c JOIN domain_category dc ON dc.category_id = c.id "
            "WHERE dc.domain_id = ? AND c.parent_id IS NULL", (domain_id,)
        ).fetchall()
        ids = []
        for row in l1_rows:
            ids.extend(self._collect_category_ids(row["id"]))
        return ids

    # ------------------------------------------------------------------ #
    # 项目类别 Project（2026-08-29 四级分类施工新增）
    # ------------------------------------------------------------------ #
    def seed_preset_projects(self) -> None:
        """写入预置项目类别（仅当表为空时）"""
        if self.list_projects():
            return
        for name in PROJECT_PRESETS:
            self.add_project(name)

    def add_project(self, name: str) -> int:
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM projects"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO projects(name, sort_order) VALUES(?, ?)", (name, order)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_project(self, project_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None

    def get_project_by_name(self, name: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> List[dict]:
        rows = self.conn.execute("SELECT * FROM projects ORDER BY sort_order, id").fetchall()
        return [dict(r) for r in rows]

    def rename_project(self, project_id: int, new_name: str) -> None:
        self.conn.execute("UPDATE projects SET name = ? WHERE id = ?", (new_name, project_id))
        self.conn.commit()

    def _project_id_tx(self, name: str) -> int:
        """事务内：按名查找项目类别，不存在则插入（不 commit，供批量/迁移事务使用）"""
        row = self.conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM projects"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO projects(name, sort_order) VALUES(?, ?)", (name, order)
        )
        return cur.lastrowid

    def ensure_project(self, name: str) -> int:
        """按名查找或新建项目类别（惰性创建，如"未明确分类"兜底）"""
        pid = self._project_id_tx(name)
        self.conn.commit()
        return pid

    def count_project_domains(self, project_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM domains WHERE project_id = ?", (project_id,)
        ).fetchone()[0]

    def delete_project(self, project_id: int,
                       fallback_project_id: Optional[int] = None) -> dict:
        """删除项目类别：其下根目录移至 fallback_project_id（无则置空=未分配）。
        返回受影响统计 {'domains': n}（UI 层负责确认弹窗与兜底选择）"""
        stat = {"domains": self.count_project_domains(project_id)}
        if fallback_project_id is not None:
            self.conn.execute(
                "UPDATE domains SET project_id = ? WHERE project_id = ?",
                (fallback_project_id, project_id),
            )
        else:
            self.conn.execute(
                "UPDATE domains SET project_id = NULL WHERE project_id = ?", (project_id,)
            )
        self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self.conn.commit()
        return stat

    def move_domain_to_project(self, domain_id: int,
                               project_id: Optional[int]) -> None:
        """移动根目录到其他项目类别（仅更新归属，分类树/条目不动）"""
        self.conn.execute(
            "UPDATE domains SET project_id = ? WHERE id = ?", (project_id, domain_id)
        )
        self.conn.commit()

    def copy_domain_to_project(self, domain_id: int, project_id: int,
                               new_name: str) -> int:
        """复制根目录到其他项目类别：建改名副本挂目标项目（重名加序号由调用方保证），源保留"""
        src = self.get_domain(domain_id)
        if not src:
            raise ValueError("根目录不存在")
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO domains(name, sort_order, project_id) VALUES(?, ?, ?)",
                    (new_name, self._next_domain_order_tx(), project_id),
                )
                new_domain_id = cur.lastrowid
                for l1 in self.list_categories(domain_id=domain_id, parent_id=None):
                    self._copy_subtree_tx(l1["id"], None, l1["name"], new_domain_id)
            return new_domain_id
        except Exception:
            self.conn.rollback()
            raise

    def assign_domains_to_projects(self, mapping: dict, on_unmatched=None) -> dict:
        """按名称把根目录分配到项目类别（迁移/向导核心，单事务、幂等可重跑）。

        - mapping: {项目类别名: [根目录名, ...]}（按库中实际名称精确匹配）；
        - on_unmatched: 回调 fn(domain_name, [项目名...]) -> 选中的项目名；返回 None 表示未回答，
          该根目录归入兜底项目 PROJECT_FALLBACK（"未明确分类"，惰性创建）。
        返回 {'matched': n, 'unmatched': m, 'fallback': k}
        """
        stats = {"matched": 0, "unmatched": 0, "fallback": 0}
        try:
            with self.conn:
                # 1) 预置项目类别确保存在
                for pname in PROJECT_PRESETS:
                    self._project_id_tx(pname)
                # 2) 匹配项：按映射精确名称更新归属
                for pname, dom_names in mapping.items():
                    pid = self._project_id_tx(pname)
                    for dname in dom_names:
                        row = self.conn.execute(
                            "SELECT id FROM domains WHERE name = ?", (dname,)
                        ).fetchone()
                        if row:
                            self.conn.execute(
                                "UPDATE domains SET project_id = ? WHERE id = ?",
                                (pid, row["id"]),
                            )
                            stats["matched"] += 1
                # 3) 未匹配项：仍无归属的根目录 → 弹窗选择 / 兜底
                projects = [p["name"] for p in self.list_projects()]
                for d in self.conn.execute(
                    "SELECT * FROM domains WHERE project_id IS NULL ORDER BY sort_order, id"
                ).fetchall():
                    chosen = None
                    if on_unmatched is not None:
                        try:
                            chosen = on_unmatched(d["name"], list(projects))
                        except Exception:
                            chosen = None  # 回调异常视为未回答，安全兜底
                    if chosen:
                        pid = self._project_id_tx(chosen)
                        self.conn.execute(
                            "UPDATE domains SET project_id = ? WHERE id = ?", (pid, d["id"])
                        )
                        stats["unmatched"] += 1
                    else:
                        pid = self._project_id_tx(PROJECT_FALLBACK)
                        self.conn.execute(
                            "UPDATE domains SET project_id = ? WHERE id = ?", (pid, d["id"])
                        )
                        stats["fallback"] += 1
            return stats
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ #
    # 删除日志 DeletionLog（2026-08-29 增量备份增强：同步"删除"到其他电脑）
    # ------------------------------------------------------------------ #
    def _category_name_chain(self, category_id: int) -> List[str]:
        """分类名称链（从一级到自身）；无父级返回 [自身]"""
        names = []
        cid = category_id
        while cid:
            c = self.get_category(cid)
            if not c:
                break
            names.append(c["name"])
            cid = c["parent_id"]
        return names[::-1]

    def _log_deletion(self, kind: str, name: str = "",
                      chain: Optional[list] = None,
                      content_key: str = "",
                      payload: str = "") -> None:
        """写入删除日志（kind: entry/category/domain）。

        payload（2026-09-08 V1.7.0）：条目被删时写入完整快照(JSON)，供"携带被删快照"
        变更包导出与⑤逆向恢复；分类/根目录等非条目删除传空。
        """
        self.conn.execute(
            "INSERT INTO deletion_log(kind, name, chain, content_key, payload, deleted_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (kind, name, json.dumps(chain or [], ensure_ascii=False),
             content_key, payload or "", _now()),
        )

    def list_deletions_since(self, since: str) -> List[dict]:
        """自 since（含）以来的删除日志"""
        rows = self.conn.execute(
            "SELECT * FROM deletion_log WHERE deleted_at >= ? ORDER BY id", (since,)
        ).fetchall()
        return [dict(r) for r in rows]

    def prune_deletion_log(self, keep_days: int = 7) -> None:
        """清理超过保留天数的删除日志（已被增量文件捕获后的历史清理）"""
        self.conn.execute(
            "DELETE FROM deletion_log WHERE deleted_at < ?",
            ((datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S"),),
        )
        self.conn.commit()

    def find_category_by_chain(self, chain: List[str]) -> Optional[int]:
        """按名称链（一级/二级…）定位分类 id；任一级不存在返回 None"""
        cid = None
        for name in chain:
            row = self.conn.execute(
                "SELECT id FROM categories WHERE parent_id IS ? AND name = ?",
                (cid, name),
            ).fetchone()
            if not row:
                return None
            cid = row["id"]
        return cid

    def delete_entries_by_content_key(self, content_key: str,
                                      cat_id: Optional[int] = None) -> int:
        """按"详情内容"判重键删除条目（增量删除同步用，尽力而为）。
        cat_id 指定则仅在该分类及其子树匹配；否则全库匹配。返回删除条数。
        """
        ids = []
        if cat_id is not None:
            for e in self.list_entries(cat_id, include_descendants=True):
                if self.content_key(e) == content_key:
                    ids.append(e["id"])
        else:
            for e in self.list_all_entries():
                if self.content_key(e) == content_key:
                    ids.append(e["id"])
        for eid in ids:
            self.delete_entry(eid)  # 复用删除（含图片清理与删除日志）
        return len(ids)

    # ------------------------------------------------------------------ #
    # 分类 Category
    # ------------------------------------------------------------------ #
    def add_category(self, name: str, parent_id: Optional[int] = None,
                     domain_id: Optional[int] = None) -> int:
        """新建分类（全局共享树）：
        - parent_id=None 表示一级分类，同时建立 领域↔分类 关联（支持多对一共享）
        - parent_id 指定则为子分类，无需 domain_id
        """
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories WHERE parent_id IS ?",
            (parent_id,),
        ).fetchone()[0]
        ts = _now()  # 2026-08-29（增量备份增强）：记录分类创建/修改时间
        cur = self.conn.execute(
            "INSERT INTO categories(parent_id, name, sort_order, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (parent_id, name, order, ts, ts),
        )
        cid = cur.lastrowid
        if parent_id is None and domain_id is not None:
            self.link_domain_category(domain_id, cid)
        self.conn.commit()
        return cid

    def link_domain_category(self, domain_id: int, category_id: int) -> None:
        """将一级分类关联到某领域（多对一共享）；重复关联自动忽略"""
        self.conn.execute(
            "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
            (domain_id, category_id),
        )
        self.conn.commit()

    def linked_domains(self, category_id: int) -> List[dict]:
        """返回关联到该分类（一级）的领域列表"""
        rows = self.conn.execute(
            "SELECT d.* FROM domains d JOIN domain_category dc ON dc.domain_id = d.id "
            "WHERE dc.category_id = ? ORDER BY d.sort_order, d.id", (category_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def category_root(self, category_id: int) -> Optional[int]:
        """返回分类所属的顶级（一级）分类 id；无父级返回自身"""
        cid = category_id
        while True:
            c = self.get_category(cid)
            if not c or c["parent_id"] is None:
                return cid if c else None
            cid = c["parent_id"]

    def rename_category(self, category_id: int, new_name: str) -> None:
        # 2026-08-29（增量备份增强）：改名更新 updated_at
        self.conn.execute(
            "UPDATE categories SET name = ?, updated_at = ? WHERE id = ?",
            (new_name, _now(), category_id),
        )
        self.conn.commit()

    def get_category(self, category_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
        return dict(row) if row else None

    def list_categories(self, domain_id: Optional[int] = None,
                        parent_id: Optional[int] = None) -> List[dict]:
        """列出分类：
        - parent_id 为 None：一级分类；若指定 domain_id 则仅返回该领域关联的一级分类
        - parent_id 指定：返回该分类的子分类
        """
        if parent_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM categories WHERE parent_id = ? ORDER BY sort_order, id",
                (parent_id,),
            ).fetchall()
        elif domain_id is not None:
            rows = self.conn.execute(
                "SELECT c.* FROM categories c JOIN domain_category dc ON dc.category_id = c.id "
                "WHERE dc.domain_id = ? AND c.parent_id IS NULL "
                "ORDER BY c.sort_order, c.id",
                (domain_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_descendants(self, category_id: int) -> dict:
        """递归统计某分类下的子分类数与条目数（用于删除确认弹窗提示）"""
        cats = 0
        entries = 0
        stack = [category_id]
        while stack:
            cid = stack.pop()
            children = self.conn.execute(
                "SELECT id FROM categories WHERE parent_id = ?", (cid,)
            ).fetchall()
            for child in children:
                cats += 1
                stack.append(child["id"])
            entries += self.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE category_id = ?", (cid,)
            ).fetchone()[0]
        return {"categories": cats, "entries": entries}

    def category_has_children(self, category_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM categories WHERE parent_id = ? LIMIT 1", (category_id,)
        ).fetchone()
        return row is not None

    def delete_category(self, category_id: int) -> dict:
        """删除分类：级联删除子分类；其下条目经外键 SET NULL 自动转入未分类。
        返回受影响统计 {'categories': n, 'entries': m}
        """
        stat = self.count_descendants(category_id)
        # 2026-08-29（增量备份增强）：记录被删分类及其全部子分类的删除日志（含各自名称链）
        for cid in self._collect_category_ids(category_id):
            c = self.get_category(cid)
            if c:
                self._log_deletion("category", c["name"],
                                   chain=self._category_name_chain(cid))
        self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        self.conn.commit()
        return stat

    # ------------------------------------------------------------------ #
    # 条目 Entry
    # ------------------------------------------------------------------ #
    @staticmethod
    def _entry_uuid_for(entry) -> str:
        """取该条目的**稳定 ID**；为空则现场生成（2026-09-17，FR-93）。

        - 普通新增：`Entry.uuid` 为空 ⇒ 自动分配；
        - 导入 / 回收站恢复：`Entry.uuid` 已有值 ⇒ **沿用**（这正是"同一条目跨机器认人"的关键）；
        - "复制到（独立副本）"与"分类子树深拷贝"：调用方会显式传**空串** ⇒ 分配新 ID（副本是新条目）。
        """
        _u = str(getattr(entry, "uuid", "") or "").strip()
        return _u or new_entry_uuid()

    @staticmethod
    def _entry_params(entry: Entry) -> tuple:
        return (
            entry.category_id, entry.name, entry.intro, entry.origin, entry.features,
            entry.scenes, entry.works, entry.image_desc, entry.prompt_cn, entry.prompt_en,
            entry.image_plan, entry.image_path, entry.is_favorite,
        )

    def add_entry(self, entry: Entry) -> int:
        ts = _now()
        # 2026-09-16（批次 11-7，用户要求 3）：`Entry.created_at` 非空时**保留原创建时间**
        #   （导入/恢复场景）；为空则仍取当前时间 ⇒ 普通新增行为**完全不变**。
        ca = str(getattr(entry, "created_at", "") or "").strip() or ts
        # 2026-09-17（FR-93）：稳定 ID —— 空则自动分配，已有则沿用（导入/恢复）
        cur = self.conn.execute(
            "INSERT INTO entries(category_id, name, intro, origin, features, scenes, works, "
            "image_desc, prompt_cn, prompt_en, image_plan, image_path, is_favorite, "
            "created_at, updated_at, uuid) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*self._entry_params(entry), ca, ts, self._entry_uuid_for(entry)),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_entries_batch(self, entries: list) -> int:
        """批量插入条目（单事务提交，比逐条 add_entry 快；JSON/Excel 大文件导入用）。

        2026-08-18（P2-4 新增）：避免大文件导入时逐条 commit 的性能与碎片化开销。
        2026-09-16（批次 11-7）：`Entry.created_at` 非空时保留原创建时间（导出→导入不丢）。
        2026-09-17（FR-93）：每条写入稳定 ID（空则分配、已有则沿用）。
        """
        ts = _now()
        cols = ("category_id", "name", "intro", "origin", "features", "scenes", "works",
                "image_desc", "prompt_cn", "prompt_en", "image_plan", "image_path",
                "is_favorite", "created_at", "updated_at", "uuid")
        ph = ",".join("?" * len(cols))
        params = [(*self._entry_params(e),
                   str(getattr(e, "created_at", "") or "").strip() or ts, ts,
                   self._entry_uuid_for(e))
                  for e in entries]
        self.conn.executemany(
            f"INSERT INTO entries({', '.join(cols)}) VALUES({ph})", params)
        self.conn.commit()
        return len(entries)

    # 参与"详情内容"判重的字段（不含收藏/图片/时间戳等附加属性）
    _CONTENT_FIELDS = ("name", "intro", "origin", "features", "scenes", "works",
                       "image_desc", "prompt_cn", "prompt_en", "image_plan")

    @staticmethod
    def content_key(row) -> str:
        """条目"详情内容"判重键：名称 + ②~⑩ 九个内容字段拼接。

        2026-08-18（P1-1 新增）：Excel/JSON 导入去重——仅当详情内容完全相同时视为重复，
        名称相同但内容不同仍会新增。兼容 dict（查询行/JSON 条目）与 Entry 对象。
        """
        parts = []
        for k in Database._CONTENT_FIELDS:
            if isinstance(row, dict):
                v = row.get(k) or ""
            else:
                v = getattr(row, k, "") or ""
            parts.append(str(v))
        return "\x1f".join(parts)

    def update_entry(self, entry: Entry) -> None:
        # 2026-09-17（FR-93）：**刻意不更新 uuid** —— 修改条目必须保持其稳定身份不变
        #   （这正是"换机同步时'修改'能识别为同一条"的前提）。
        self.conn.execute(
            "UPDATE entries SET category_id = ?, name = ?, intro = ?, origin = ?, features = ?, "
            "scenes = ?, works = ?, image_desc = ?, prompt_cn = ?, prompt_en = ?, "
            "image_plan = ?, image_path = ?, is_favorite = ?, updated_at = ? WHERE id = ?",
            (*self._entry_params(entry), _now(), entry.id),
        )
        self.conn.commit()

    def get_entry(self, entry_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None

    def get_entry_by_uuid(self, entry_uuid: str) -> Optional[dict]:
        """按**稳定 ID** 取条目（2026-09-17，FR-93：导入 / 变更包"按 uuid 认人"用）。

        空串 / 空值直接返回 None（空串不是有效身份）；不存在返回 None。
        """
        _u = str(entry_uuid or "").strip()
        if not _u:
            return None
        row = self.conn.execute("SELECT * FROM entries WHERE uuid = ?", (_u,)).fetchone()
        return dict(row) if row else None

    def delete_entry(self, entry_id: int, purge_image: bool = True) -> None:
        """物理删除条目（硬删除）。

        purge_image（2026-09-07 第2条改进）：是否同步删除关联图片文件。
        手动删除/级联删除先经 trash_entry() 写入回收站并保留图片（恢复可用），
        只有"回收站彻底删除/清空"时才 purge_image=True 释放图片。
        """
        entry = self.get_entry(entry_id)
        if entry and purge_image:
            # 2026-09-13（2-d）：彻底删除时同步释放图集本地文件（外链图无文件）
            for _g in self.list_entry_images(entry_id):
                if _g.get("kind") == "local" and _g.get("path"):
                    self._remove_image_file(_g["path"])
        if entry and entry.get("image_path") and purge_image:
            self._remove_image_file(entry["image_path"])  # 同步删除关联图片（尽力而为）
        # 2026-08-29（增量备份增强）：记录删除日志，供换机同步删除
        if entry:
            self._log_deletion(
                "entry", entry["name"],
                chain=self._category_name_chain(entry["category_id"]) if entry.get("category_id") else [],
                content_key=self.content_key(entry),
                # V1.7.0：完整快照(⑤逆向恢复)；2026-09-13 起含自定义字段取值
                payload=json.dumps(self.entry_snapshot(entry), ensure_ascii=False),
            )
        self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 回收站 / 删除历史（2026-09-07 第2条改进：可恢复删除的条目）
    # 说明：deletion_log 只记"名字+内容键"用于换机增量同步；
    #       trash 保存完整内容快照，用于本机"恢复删除的条目"。
    # ------------------------------------------------------------------ #
    def entry_snapshot(self, entry: dict) -> dict:
        """条目"完整快照"：entries 行全部列 + 自定义字段取值 + 标签（2026-09-13，1-A-5 第 3 步·下 / 1-C-4）。

        供"回收站快照"与"删除日志快照（变更包/⑤逆向恢复）"共用，确保自定义字段与标签都不丢。
        无自定义字段/标签时仍写入空结构（结构稳定、便于判断）。
        """
        snap = dict(entry)
        rows = self.list_entry_field_values(entry["id"])
        snap["custom_fields"] = {r["field_key"]: r["value_text"] for r in rows}
        # 2026-09-13（3-b）：结构化取值（目录层级型＝令牌+快照名）一并入快照，
        # 使回收站恢复 / 变更包逆向恢复后引用不失效；无则**不写该键**（结构稳定且不膨胀）。
        _thr = {r["field_key"]: r["value_json"] for r in rows if r["value_json"]}
        if _thr:
            snap["custom_fields_json"] = _thr
        snap["tags"] = self.list_entry_tag_names(entry["id"])
        # 2026-09-13（2-d）：图集一并入快照（回收站恢复/删除日志⑤逆向恢复都不丢多图）
        snap["images"] = self.list_entry_images(entry["id"])
        return snap

    def list_entry_tag_names(self, entry_id: int,
                             namespace: str = GLOBAL_TAG_NS) -> List[str]:
        """某条目的标签名列表（按名称排序）——供快照/载荷/导出使用"""
        return [t["name"] for t in self.list_entry_tags(entry_id, namespace)]

    def trash_entry(self, entry_id: int, reason: str = "手动删除") -> bool:
        """把条目移入回收站：完整快照入 trash → 硬删除（保留图片文件，便于恢复）。

        返回是否成功（条目不存在返回 False）。
        """
        e = self.get_entry(entry_id)
        if not e:
            return False
        payload = self.entry_snapshot(e)   # 2026-09-13：快照含自定义字段取值
        payload["locations"] = self._entry_location_ids(entry_id)  # 全部位置（含主挂靠）
        payload["chain"] = (self._category_name_chain(e["category_id"])
                            if e.get("category_id") else [])
        self.conn.execute(
            "INSERT INTO trash(name, payload, deleted_at, reason) VALUES(?, ?, ?, ?)",
            (e["name"], json.dumps(payload, ensure_ascii=False), _now(), reason),
        )
        self.conn.commit()
        self.delete_entry(entry_id, purge_image=False)  # 保留图片，供恢复后继续显示
        return True

    def list_trash(self) -> List[dict]:
        """回收站列表（最新删除在前）；payload 解析为 dict，供 UI 展示/恢复"""
        rows = self.conn.execute(
            "SELECT * FROM trash ORDER BY deleted_at DESC, id DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(r["payload"])
            except (TypeError, ValueError):
                d["payload"] = {}
            out.append(d)
        return out

    def count_trash(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM trash").fetchone()[0]

    def restore_from_trash(self, trash_id: int) -> Optional[int]:
        """从回收站恢复条目（重建为新 id，保留原内容/收藏/位置与创建时间）。

        分类已被删除的位置自动剔除；无任何现存位置 → 恢复为「未分类」。
        返回新条目 id；trash_id 不存在返回 None。
        """
        row = self.conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id,)).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        now = _now()
        # 现存位置：原主挂靠优先，其次按 id 升序
        locs = [c for c in (payload.get("locations") or []) if self.get_category(c)]
        main = payload.get("category_id")
        if main not in locs:
            main = locs[0] if locs else None
        others = [c for c in locs if c != main]
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO entries(category_id, name, intro, origin, features, scenes, "
                    "works, image_desc, prompt_cn, prompt_en, image_plan, image_path, "
                    "is_favorite, created_at, updated_at, uuid) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (main,
                     payload.get("name", row["name"]),
                     payload.get("intro", ""), payload.get("origin", ""),
                     payload.get("features", ""), payload.get("scenes", ""),
                     payload.get("works", ""), payload.get("image_desc", ""),
                     payload.get("prompt_cn", ""), payload.get("prompt_en", ""),
                     payload.get("image_plan", ""), payload.get("image_path", ""),
                     1 if payload.get("is_favorite") else 0,
                     payload.get("created_at") or now, now,
                     # 2026-09-17（FR-93）：**沿用快照里的稳定 ID**（恢复后仍是"同一条目"）；
                     # 老快照（v5 之前写入）没有 uuid ⇒ 现场分配一个。
                     str(payload.get("uuid") or "").strip() or new_entry_uuid()))
                new_id = cur.lastrowid
                # 2026-09-13（1-A-5 第 3 步·下）：从回收站快照恢复自定义字段取值
                # 2026-09-13（3-b）：结构化取值（目录层级型的令牌+快照名）同时恢复
                _cfj = payload.get("custom_fields_json") or {}
                for fk, v in (payload.get("custom_fields") or {}).items():
                    self.conn.execute(
                        "INSERT OR REPLACE INTO entry_field_values(entry_id, field_key,"
                        " value_text, value_json, updated_at) VALUES(?, ?, ?, ?, ?)",
                        (new_id, fk, v or "", _cfj.get(fk) or "", now))
                # 2026-09-13（1-C-4）：从快照恢复标签（缺失标签自动创建）
                for _n in (payload.get("tags") or []):
                    _n = (_n or "").strip()
                    if not _n:
                        continue
                    _row = self.conn.execute(
                        "SELECT id FROM tags WHERE namespace = ? AND name = ?",
                        (GLOBAL_TAG_NS, _n)).fetchone()
                    if _row:
                        _tid = _row["id"]
                    else:
                        _tid = self.conn.execute(
                            "INSERT INTO tags(namespace, name, color, created_at, updated_at)"
                            " VALUES(?,?,'',?,?)", (GLOBAL_TAG_NS, _n, now, now)).lastrowid
                    self.conn.execute(
                        "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at)"
                        " VALUES(?,?,?)", (new_id, _tid, now))
                for cid in others:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO entry_links(entry_id, category_id, created_at) "
                        "VALUES(?, ?, ?)", (new_id, cid, now))
                # 2026-09-13（2-d）：从快照恢复图集（本地图文件在回收站期间被保留，直接重新挂回）
                for _i, _g in enumerate(payload.get("images") or []):
                    if not isinstance(_g, dict):
                        continue
                    _kind = _g.get("kind") if _g.get("kind") in ("local", "url") else "local"
                    self.conn.execute(
                        "INSERT INTO entry_images(entry_id, kind, path, source_url,"
                        " is_primary, sort_order, caption, created_at)"
                        " VALUES(?,?,?,?,0,?,?,?)",
                        (new_id, _kind, _g.get("path") or "", _g.get("source_url") or "",
                         _g.get("sort_order") if isinstance(_g.get("sort_order"), int) else _i,
                         _g.get("caption") or "", _g.get("created_at") or now))
                self.conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
            return new_id
        except Exception:
            self.conn.rollback()
            raise

    def purge_trash(self, trash_id: int) -> None:
        """彻底删除回收站中的一条（连同其关联图片文件释放）。

        2026-09-09（审核 P1-7 修复）：同时清空 deletion_log 中同内容的 payload 快照，
        缩短"以为已彻底删除、实则可被变更包快照导出"的隐私窗口（保留名称/链/指纹以便同步）。
        """
        row = self.conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id,)).fetchone()
        if not row:
            return
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        img = payload.get("image_path") if isinstance(payload, dict) else ""
        if img:
            self._remove_image_file(img)
        # 2026-09-13（2-d）：彻底删除时同步释放快照中的图集本地文件
        if isinstance(payload, dict):
            for _g in (payload.get("images") or []):
                if isinstance(_g, dict) and _g.get("kind") == "local" and _g.get("path"):
                    self._remove_image_file(_g["path"])
        if isinstance(payload, dict) and payload:
            try:
                key = self.content_key(payload)
                if key:
                    self.conn.execute(
                        "UPDATE deletion_log SET payload = '' "
                        "WHERE kind = 'entry' AND content_key = ?", (key,))
            except Exception:
                pass
        self.conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
        self.conn.commit()

    def clear_trash(self) -> int:
        """清空回收站（返回清理条数）；2026-09-09（P1-7）：同步清除 deletion_log 快照 payload"""
        items = self.conn.execute("SELECT id, payload FROM trash").fetchall()
        for it in items:
            try:
                payload = json.loads(it["payload"])
                img = payload.get("image_path") if isinstance(payload, dict) else ""
                if img:
                    self._remove_image_file(img)
                # 2026-09-14（审核修复 P1-B）：清空回收站时同步释放**图集**本地文件
                # （此前只释放封面，清空后 entry_*_n.ext 成为永久孤儿文件）
                if isinstance(payload, dict):
                    for _g in (payload.get("images") or []):
                        if (isinstance(_g, dict) and _g.get("kind") == "local"
                                and _g.get("path")):
                            self._remove_image_file(_g["path"])
                if isinstance(payload, dict) and payload:
                    try:
                        key = self.content_key(payload)
                        if key:
                            self.conn.execute(
                                "UPDATE deletion_log SET payload = '' "
                                "WHERE kind = 'entry' AND content_key = ?", (key,))
                    except Exception:
                        pass
            except (TypeError, ValueError):
                pass
        n = self.conn.execute("SELECT COUNT(*) FROM trash").fetchone()[0]
        self.conn.execute("DELETE FROM trash")
        self.conn.commit()
        return n

    def trash_entries_batch(self, entry_ids: list, reason: str = "变更包删除同步",
                            log_deletion: bool = True) -> int:
        """批量把条目移入回收站（2026-09-08 V1.7.0：变更包删除同步用）。

        与 trash_entry 语义一致（完整快照入 trash、保留图片、写删除日志），
        但全部操作在**单事务**内完成，避免逐条 commit 的性能开销。
        log_deletion（2026-09-09 P2-13）：本机删除/级联删除传 True（需广播）；
        "变更包删除同步"传 False——接收端不把同步删除再写成"本机删除"，
        避免本机当日变更包出现"回声删除"（负增量噪音）。
        已不存在/重复 id 自动忽略；返回实际移入条数。
        """
        seen, del_ids = set(), []
        for eid in entry_ids:
            if eid is None or eid in seen:
                continue
            seen.add(eid)
            del_ids.append(eid)
        if not del_ids:
            return 0
        now = _now()
        trash_rows, log_rows, alive = [], [], []
        for eid in del_ids:
            e = self.get_entry(eid)
            if not e:
                continue
            # 2026-09-14（审核修复 P1-A）：改与 trash_entry 同源的 entry_snapshot，
            # 使"变更包删除同步"入站的条目也带自定义字段取值 / 标签 / 图集，
            # 恢复不丢数据、彻底删除时图集文件也能被释放（此前用 dict(e)，三项全缺）。
            payload = self.entry_snapshot(e)
            payload["locations"] = self._entry_location_ids(eid)
            payload["chain"] = (self._category_name_chain(e["category_id"])
                                if e.get("category_id") else [])
            payload_json = json.dumps(payload, ensure_ascii=False)
            trash_rows.append((e["name"], payload_json, now, reason))
            if log_deletion:
                log_rows.append(("entry", e["name"],
                                 json.dumps(payload["chain"], ensure_ascii=False),
                                 self.content_key(e), payload_json, now))
            alive.append(eid)
        if not alive:
            return 0
        ph = ",".join("?" * len(alive))
        try:
            with self.conn:
                self.conn.executemany(
                    "INSERT INTO trash(name, payload, deleted_at, reason) VALUES(?, ?, ?, ?)",
                    trash_rows)
                if log_deletion and log_rows:
                    self.conn.executemany(
                        "INSERT INTO deletion_log(kind, name, chain, content_key, payload, "
                        "deleted_at) VALUES(?, ?, ?, ?, ?, ?)", log_rows)
                self.conn.execute(
                    f"DELETE FROM entries WHERE id IN ({ph})", alive)
            return len(alive)
        except Exception:
            self.conn.rollback()
            raise

    def list_entries_added_since(self, since: str) -> List[dict]:
        """按 created_at >= since 列出最近新增的条目（第3条改进：查看添加历史）"""
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE created_at >= ? "
            "ORDER BY created_at DESC, id DESC", (since,)).fetchall()
        return [dict(r) for r in rows]

    def category_path(self, category_id: Optional[int]) -> str:
        """分类路径显示文本（如"视觉风格分类 › 某分类"；无分类返回「未分类」）"""
        if not category_id:
            return "未分类"
        return " › ".join(self._category_name_chain(category_id))

    def _remove_image_file(self, image_path: str) -> None:
        """删除条目关联的本地图片文件（相对 data/ 的路径，失败静默）。

        2026-08-18（P2-1 修复）：校验最终绝对路径仍在 data/ 目录内，防止越界读写。
        """
        try:
            root = os.path.abspath(data_dir())
            full = os.path.abspath(os.path.join(root, image_path))
            try:
                in_root = os.path.commonpath([root, full]) == root
            except ValueError:
                in_root = False  # 不同盘符等情况：视为越界，拒绝
            if not in_root:
                return  # 越出 data/ 目录（如被篡改为 ../、绝对路径或异盘路径），拒绝删除
            if os.path.isfile(full):
                os.remove(full)
        except OSError:
            pass

    # 条目区排序白名单（2026-09-16 批次 11-7，用户要求 3）：避免把用户输入拼进 SQL
    _ENTRY_ORDER_SQL = {
        "updated": "updated_at DESC, id",
        "created": "created_at DESC, id DESC",
        "name": "name COLLATE NOCASE ASC, id",
    }

    @classmethod
    def entry_order_sql(cls, order_by: Optional[str] = None) -> str:
        """条目排序 SQL 片段（**白名单**）：非法 / None ⇒ 默认"最后修改时间倒序"。"""
        return cls._ENTRY_ORDER_SQL.get(order_by or "", cls._ENTRY_ORDER_SQL["updated"])

    def list_entries(self, category_id: int, include_descendants: bool = False,
                     order_by: Optional[str] = None) -> List[dict]:
        """列出某分类可见条目（主挂靠=该分类 ∪ 关联表含该分类，去重）。

        include_descendants=True 时含所有子分类子树内的可见条目。
        2026-09-07（条目多位置施工）：单分类列举由"只看 category_id"改为两路并集，
        使"关联到"的条目也能在对应分类下列出。
        order_by（2026-09-16 批次 11-7，用户要求 3）：条目区排序方式，**白名单**取值——
          None / "updated"（默认，最后修改时间倒序）/ "created"（新增时间倒序）/ "name"（名称升序）；
          非法值一律回退默认 ⇒ 老调用方（不传）行为**完全不变**。
        """
        _order = self.entry_order_sql(order_by)
        if include_descendants:
            ids = self._collect_category_ids(category_id)
            if not ids:
                return []
            ph = ",".join("?" * len(ids))
            rows = self.conn.execute(
                "SELECT * FROM ("
                f" SELECT e.* FROM entries e WHERE e.category_id IN ({ph})"
                " UNION "
                f" SELECT e.* FROM entries e JOIN entry_links l ON l.entry_id = e.id"
                f"  WHERE l.category_id IN ({ph})"
                f") ORDER BY {_order}",
                ids + ids,
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM ("
                " SELECT e.* FROM entries e WHERE e.category_id = ?"
                " UNION "
                " SELECT e.* FROM entries e JOIN entry_links l ON l.entry_id = e.id"
                "  WHERE l.category_id = ?"
                f") ORDER BY {_order}",
                (category_id, category_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_entries(self, category_id: Optional[int] = None,
                      include_descendants: bool = False) -> int:
        """统计某分类可见条目数（COUNT，不加载行）。

        2026-09-07（条目多位置施工）：改为按并集统计"位置可见条目数"；
        category_id=None 仍返回全局物理条目总数（不重复计关联）。
        """
        if category_id is None:
            return self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        if include_descendants:
            ids = self._collect_category_ids(category_id)
            if not ids:
                return 0
            ph = ",".join("?" * len(ids))
            return self.conn.execute(
                "SELECT COUNT(*) FROM ("
                f" SELECT e.id FROM entries e WHERE e.category_id IN ({ph})"
                " UNION "
                f" SELECT e.id FROM entries e JOIN entry_links l ON l.entry_id = e.id"
                f"  WHERE l.category_id IN ({ph})"
                ")",
                ids + ids,
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM ("
            " SELECT e.id FROM entries e WHERE e.category_id = ?"
            " UNION "
            " SELECT e.id FROM entries e JOIN entry_links l ON l.entry_id = e.id"
            "  WHERE l.category_id = ?"
            ")",
            (category_id, category_id),
        ).fetchone()[0]

    def _collect_category_ids(self, category_id: int) -> List[int]:
        ids = []
        stack = [category_id]
        while stack:
            cid = stack.pop()
            ids.append(cid)
            children = self.conn.execute(
                "SELECT id FROM categories WHERE parent_id = ?", (cid,)
            ).fetchall()
            stack.extend(child["id"] for child in children)
        return ids

    def list_uncategorized(self) -> List[dict]:
        """列出未分类条目：主挂靠为空 **且** 无任何关联位置。

        2026-09-07（条目多位置施工）：若条目经 entry_links 关联到某分类，
        即使 category_id 为空也不再视为"未分类"。
        """
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE category_id IS NULL "
            "AND id NOT IN (SELECT entry_id FROM entry_links) "
            "ORDER BY updated_at DESC, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_favorites(self) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE is_favorite = 1 ORDER BY updated_at DESC, id"
        ).fetchall()
        return [dict(r) for r in rows]

    # 2026-09-14（用户要求，"无标签条目"入口的数据层）：没有任何标签的条目。
    #   与 list_uncategorized（无分类）对称；供「🏷 无标签条目」视图、标签页面入口
    #   与搜索框 `#无标签` 语法共用，便于逐条为其打标签。
    def list_untagged(self) -> List[dict]:
        """列出**没有任何标签**的条目（按修改时间倒序）。"""
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE id NOT IN "
            "(SELECT DISTINCT entry_id FROM entry_tags) "
            "ORDER BY updated_at DESC, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_untagged(self) -> int:
        """没有标签的条目数（供入口按钮显示数量）。

        2026-09-15（审核 M-2，用户同意）：失败时**返回 −1 并打印一行日志**，不再静默 `return 0`——
        原来"异常 → 0"会把"库被锁 / 损坏 / 缺表"伪装成"确实没有无标签条目"，
        UI 随之显示"（0）"而掩盖故障；UI 侧对 −1 显示"未知"。
        """
        try:
            return self.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE id NOT IN "
                "(SELECT DISTINCT entry_id FROM entry_tags)").fetchone()[0]
        except Exception as exc:                    # 2026-09-15（审核 M-2）：不再吞异常返回 0
            print(f"[无标签条目] 计数失败（返回 -1 表示未知）：{exc}")
            return -1

    def list_all_entries(self) -> List[dict]:
        rows = self.conn.execute("SELECT * FROM entries ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def list_entries_updated_since(self, since: str) -> List[dict]:
        """按 updated_at >= since 列出条目（增量备份收集用，2026-08-29 新增）"""
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE updated_at >= ? ORDER BY id", (since,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_categories_changed_since(self, since: str) -> List[dict]:
        """按 created_at/updated_at >= since 列出分类（含"新增空分类"，2026-08-29 新增）"""
        rows = self.conn.execute(
            "SELECT * FROM categories WHERE created_at >= ? OR updated_at >= ? "
            "ORDER BY id", (since, since)
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, keyword: str) -> List[dict]:
        """全局搜索：匹配全部文本字段（名称/介绍/溯源/特征/场景/代表作/配图/中英提示词/图像方案）。

        2026-08-18（第020条，P2-B1 修复）：对 LIKE 通配符 % / _ 做转义（ESCAPE '\\'），
        使搜索含 % 或 _ 的关键词按字面匹配，避免意外通配匹配到多余结果。
        2026-09-13（schema v4，第 1 期 1-A）：追加匹配"自定义字段取值"
        （entry_field_values.value_text），内置 10 字段的匹配逻辑与排序保持不变。
        """
        # 转义顺序：先转义反斜杠自身，再转义 % 与 _（ESCAPE 字符为反斜杠）
        escaped = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        kw = f"%{escaped}%"
        esc = " ESCAPE '\\' "
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE name LIKE ?" + esc + "OR intro LIKE ?" + esc +
            "OR origin LIKE ?" + esc + "OR features LIKE ?" + esc + "OR scenes LIKE ?" + esc +
            "OR works LIKE ?" + esc + "OR image_desc LIKE ?" + esc + "OR prompt_cn LIKE ?" + esc +
            "OR prompt_en LIKE ?" + esc + "OR image_plan LIKE ?" + esc +
            "OR EXISTS (SELECT 1 FROM entry_field_values v"
            "           WHERE v.entry_id = entries.id AND v.value_text LIKE ?" + esc + ") " +
            "OR EXISTS (SELECT 1 FROM entry_tags et JOIN tags t ON t.id = et.tag_id"
            "           WHERE et.entry_id = entries.id AND t.name LIKE ?" + esc + ") " +
            "ORDER BY updated_at DESC, id",
            (kw,) * 12,
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # 条目多位置：关联 / 复制 / 移动（2026-09-07 施工）
    # 统一原语 set_entry_locations：关联=add；解除=remove；移动=remove(全部)+add(目标)
    # 不变量：条目只要有位置，主挂靠(category_id)恰为其一；位置清空则 category_id=NULL。
    # ------------------------------------------------------------------ #
    def _entry_location_ids(self, entry_id: int) -> List[int]:
        """条目当前全部位置分类 id（主挂靠 + 关联，去重、升序）"""
        e = self.get_entry(entry_id)
        ids = []
        if e and e.get("category_id") is not None:
            ids.append(e["category_id"])
        rows = self.conn.execute(
            "SELECT category_id FROM entry_links WHERE entry_id = ? "
            "ORDER BY created_at, rowid", (entry_id,)
        ).fetchall()
        for r in rows:
            if r["category_id"] not in ids:
                ids.append(r["category_id"])
        return ids

    def list_entry_locations(self, entry_id: int) -> List[int]:
        """条目全部位置分类 id（含主挂靠；供 UI 位置提示/移动对话框用）"""
        return self._entry_location_ids(entry_id)

    def _entry_location_apply_tx(self, entry_id: int, remove: List[int],
                                 add: List[int]) -> None:
        """位置编辑核心（须在调用方事务内执行，不自行 commit）。

        规则：
        1) add 的目标若已在任一位置则忽略；
        2) remove 同时作用于 主挂靠 与 关联表；
        3) 主挂靠被移除后，若仍有剩余关联位置则自动提升其一为主挂靠；
        4) 全部位置清空 → category_id=NULL（回落未分类）。
        """
        e = self.get_entry(entry_id)
        if not e:
            raise ValueError("条目不存在")
        main = e["category_id"]
        now = set(self._entry_location_ids(entry_id))
        for cid in dict.fromkeys(remove or []):
            if cid is None or cid not in now:
                continue
            now.discard(cid)
            self.conn.execute(
                "DELETE FROM entry_links WHERE entry_id = ? AND category_id = ?",
                (entry_id, cid))
        for cid in dict.fromkeys(add or []):
            if cid is None or cid in now:
                continue
            now.add(cid)
            self.conn.execute(
                "INSERT INTO entry_links(entry_id, category_id, created_at) "
                "VALUES(?, ?, ?)", (entry_id, cid, _now()))
        # 主挂靠维护：main 是否还在剩余位置中
        if main is not None and main not in now:
            main = None
        if main is None and now:
            main = min(now)  # 提升最小 id 的关联位置为主挂靠
            self.conn.execute(
                "DELETE FROM entry_links WHERE entry_id = ? AND category_id = ?",
                (entry_id, main))
        self.conn.execute(
            "UPDATE entries SET category_id = ?, updated_at = ? WHERE id = ?",
            (main, _now(), entry_id))

    def set_entry_locations(self, entry_id: int, remove: Optional[list] = None,
                            add: Optional[list] = None) -> None:
        """统一位置编辑原语（原子）。remove/add 为分类 id 列表。"""
        try:
            with self.conn:
                self._entry_location_apply_tx(entry_id, remove or [], add or [])
        except Exception:
            self.conn.rollback()
            raise

    def link_entry(self, entry_id: int, category_id: int) -> None:
        """把条目关联到某分类（若该分类已是主挂靠则忽略）"""
        self.set_entry_locations(entry_id, remove=[], add=[category_id])

    def unlink_entry(self, entry_id: int, category_id: int) -> None:
        """解除条目在某分类的关联（含解除主挂靠；仅剩位置会自动提升）"""
        self.set_entry_locations(entry_id, remove=[category_id], add=[])

    def move_entry(self, entry_id: int, category_id: Optional[int]) -> None:
        """把条目整体转移：仅保留目标位置（清空其它全部关联）。

        category_id=None 表示移入未分类。兼容旧调用（单位置对象行为不变）；
        2026-09-07：多位置下"移动到"＝ remove(全部位置) + add(目标)。
        """
        self.set_entry_locations(entry_id,
                                 remove=self._entry_location_ids(entry_id),
                                 add=[] if category_id is None else [category_id])

    def copy_entry_to(self, entry_id: int, target_cat_id: int,
                      new_name: Optional[str] = None) -> int:
        """"复制到（独立副本）"：将条目内容拷贝到目标分类成为独立新条目。

        - 内容 9 字段全拷贝、收藏清零、时间戳新建；
        - 关联图片复制为新文件（entry_{新id}.{ext}），避免删除一份误删另一份；
        - 新条目不带任何额外关联（只有目标主挂靠）。
        - 名称：目标分类下同名自动加"（副本）/（副本2）…"（2026-09-07，便于区分）。
        返回新条目 id。
        """
        e = self.get_entry(entry_id)
        if not e:
            raise ValueError("条目不存在")
        ts = _now()
        try:
            with self.conn:
                name = new_name or self.unique_entry_name(e["name"], target_cat_id)
                cur = self.conn.execute(
                    "INSERT INTO entries(category_id, name, intro, origin, features, scenes, "
                    "works, image_desc, prompt_cn, prompt_en, image_plan, image_path, "
                    "is_favorite, created_at, updated_at, uuid) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (target_cat_id, name, e["intro"], e["origin"], e["features"],
                     e["scenes"], e["works"], e["image_desc"], e["prompt_cn"],
                     e["prompt_en"], e["image_plan"], e["image_path"], 0, ts, ts,
                     # 2026-09-17（FR-93）：**复制到 = 独立副本** ⇒ 分配**新**稳定 ID
                     #   （不可沿用原条目 uuid，否则跨机器会被误认成"同一条目"）。
                     new_entry_uuid()))
                new_id = cur.lastrowid
                # 2026-09-13（1-A-5 第 3 步·下）：自定义字段取值一并复制（独立副本）
                for r in self.conn.execute(
                        "SELECT field_key, value_text, value_json FROM entry_field_values"
                        " WHERE entry_id = ?", (entry_id,)):
                    self.conn.execute(
                        "INSERT OR REPLACE INTO entry_field_values(entry_id, field_key,"
                        " value_text, value_json, updated_at) VALUES(?, ?, ?, ?, ?)",
                        (new_id, r["field_key"], r["value_text"], r["value_json"], ts))
                # 2026-09-13（1-C-4）：标签一并复制（独立副本共用同一批标签对象）
                for r in self.conn.execute(
                        "SELECT tag_id FROM entry_tags WHERE entry_id = ?", (entry_id,)):
                    self.conn.execute(
                        "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at)"
                        " VALUES(?, ?, ?)", (new_id, r["tag_id"], ts))
                if e.get("image_path"):
                    new_rel = self._copy_image_file(e["image_path"], new_id)
                    if new_rel:
                        self.conn.execute(
                            "UPDATE entries SET image_path = ? WHERE id = ?",
                            (new_rel, new_id))
                # 2026-09-13（2-d）：图集一并复制（本地图复制为新文件，避免删一份误删另一份）
                for _i, _g in enumerate(self.list_entry_images(entry_id)):
                    if _g.get("kind") == "url":
                        self.conn.execute(
                            "INSERT INTO entry_images(entry_id, kind, path, source_url,"
                            " is_primary, sort_order, caption, created_at)"
                            " VALUES(?,?,?,?,0,?,?,?)",
                            (new_id, "url", "", _g.get("source_url") or "", _i,
                             _g.get("caption") or "", ts))
                        continue
                    _rel = self._copy_gallery_file(_g.get("path") or "", new_id, _i)
                    if _rel:
                        self.conn.execute(
                            "INSERT INTO entry_images(entry_id, kind, path, source_url,"
                            " is_primary, sort_order, caption, created_at)"
                            " VALUES(?,?,?,?,0,?,?,?)",
                            (new_id, "local", _rel, _g.get("source_url") or "", _i,
                             _g.get("caption") or "", ts))
            return new_id
        except Exception:
            self.conn.rollback()
            raise

    def _copy_image_file(self, old_rel: str, new_entry_id: int) -> Optional[str]:
        """复制条目封面图片到新条目名下（相对 data/ 路径安全校验；失败返回 None）"""
        try:
            root = os.path.abspath(data_dir())
            src = os.path.abspath(os.path.join(root, old_rel))
            if os.path.commonpath([root, src]) != root or not os.path.isfile(src):
                return None
            ext = os.path.splitext(old_rel)[1] or ""
            img_dir = os.path.join(root, IMAGES_DIR_NAME)
            os.makedirs(img_dir, exist_ok=True)
            dest_rel = os.path.join(IMAGES_DIR_NAME, f"entry_{new_entry_id}{ext}")
            shutil.copy2(src, os.path.join(root, dest_rel))
            return dest_rel
        except (OSError, ValueError):
            return None

    def _copy_gallery_file(self, old_rel: str, new_entry_id: int,
                           idx: int) -> Optional[str]:
        """复制图集本地图到新条目名下：images/entry_{新id}_{序号}{ext}（失败返回 None）"""
        if not old_rel:
            return None
        try:
            root = os.path.abspath(data_dir())
            src = os.path.abspath(os.path.join(root, old_rel))
            if os.path.commonpath([root, src]) != root or not os.path.isfile(src):
                return None
            ext = os.path.splitext(old_rel)[1] or ""
            img_dir = os.path.join(root, IMAGES_DIR_NAME)
            os.makedirs(img_dir, exist_ok=True)
            dest_rel = os.path.join(IMAGES_DIR_NAME, f"entry_{new_entry_id}_{idx}{ext}")
            shutil.copy2(src, os.path.join(root, dest_rel))
            return dest_rel
        except (OSError, ValueError):
            return None

    # ---- 保存一致性提示 / 副本命名辅助（2026-09-07） ----
    def find_content_duplicates(self, key: str,
                                exclude_entry_id: Optional[int] = None,
                                limit: int = 5) -> List[dict]:
        """按"详情内容键"查找同内容条目（用于保存前轻提示；可排除自身）。

        返回最多 limit 条 {"id", "name"}；全库逐条比对（保存频率低，量级可接受）。
        """
        hits = []
        for row in self.conn.execute("SELECT * FROM entries").fetchall():
            e = dict(row)
            if exclude_entry_id is not None and e["id"] == exclude_entry_id:
                continue
            if self.content_key(e) == key:
                hits.append({"id": e["id"], "name": e["name"]})
                if len(hits) >= limit:
                    break
        return hits

    def _entry_name_exists(self, name: str, category_id: int) -> bool:
        """某分类（主挂靠或关联）下是否已存在同名条目"""
        row = self.conn.execute(
            "SELECT 1 FROM entries WHERE category_id = ? AND name = ? LIMIT 1",
            (category_id, name),
        ).fetchone()
        if row:
            return True
        row = self.conn.execute(
            "SELECT 1 FROM entry_links l JOIN entries e ON e.id = l.entry_id "
            "WHERE l.category_id = ? AND e.name = ? LIMIT 1",
            (category_id, name),
        ).fetchone()
        return row is not None

    def unique_entry_name(self, name: str, category_id: int) -> str:
        """目标分类下条目名去重：重名 → name（副本）→ name（副本2）…"""
        if not self._entry_name_exists(name, category_id):
            return name
        base = f"{name}（副本）"
        if not self._entry_name_exists(base, category_id):
            return base
        i = 2
        while self._entry_name_exists(f"{name}（副本{i}）", category_id):
            i += 1
        return f"{name}（副本{i}）"

    # ------------------------------------------------------------------ #
    # 分类删除保护（2026-09-07 施工，DB 层；UI 短语输入在阶段 3 接入）
    # ------------------------------------------------------------------ #
    _CASCADE_PHRASE = "删除全部下级内容"

    def delete_category_safe(self, category_id: int) -> dict:
        """安全删除单个分类：拒绝有子分类；其直挂条目仅解除本分类位置，
        无其它位置的条目转未分类，不真删任何条目。返回 {'categories','entries'}。
        """
        if self.category_has_children(category_id):
            raise ValueError("该分类仍有下级分类，请先处理下级（或改用级联删除）")
        entries = [e["id"] for e in self.list_entries(category_id)]
        try:
            with self.conn:
                for eid in entries:
                    self._entry_location_apply_tx(eid, remove=[category_id], add=[])
                c = self.get_category(category_id)
                if c:
                    self._log_deletion("category", c["name"],
                                       chain=self._category_name_chain(category_id))
                self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            return {"categories": 1, "entries": len(entries)}
        except Exception:
            self.conn.rollback()
            raise

    def delete_category_cascade(self, category_id: int, confirm_phrase: str) -> dict:
        """级联删除整棵（该分类 + 全部下级分类 + 其全部直挂条目真删）。

        必须传入确认短语并等于 _CASCADE_PHRASE 才执行（DB 层兜底防误删）。
        返回 {'categories','entries'}（entries 为物理删除数）。
        """
        if confirm_phrase != self._CASCADE_PHRASE:
            raise ValueError("确认短语不正确，已取消级联删除")
        ids = self._collect_category_ids(category_id)
        if not ids:
            return {"categories": 0, "entries": 0}
        ph = ",".join("?" * len(ids))
        affected = [r["id"] for r in self.conn.execute(
            "SELECT e.id FROM entries e WHERE e.category_id IN (" + ph + ")"
            " UNION "
            "SELECT l.entry_id AS id FROM entry_links l WHERE l.category_id IN (" + ph + ")",
            ids + ids,
        ).fetchall()]
        try:
            # 条目先全部移入回收站（完整快照 + 保留图片），再从主表硬删除；
            # 分类结构与图片资源在用户"彻底删除/清空回收站"时才最终释放
            for eid in affected:
                self.trash_entry(eid, reason="级联删除")
            # 记录分类删除日志
            for cid in ids:
                c = self.get_category(cid)
                if c:
                    self._log_deletion("category", c["name"],
                                       chain=self._category_name_chain(cid))
            self.conn.execute(
                "DELETE FROM categories WHERE id IN (" + ph + ")", ids)
            self.conn.commit()
            return {"categories": len(ids), "entries": len(affected)}
        except Exception:
            self.conn.rollback()
            raise

    def toggle_favorite(self, entry_id: int) -> int:
        """切换收藏状态，返回新状态(0/1)"""
        self.conn.execute(
            "UPDATE entries SET is_favorite = 1 - is_favorite, updated_at = ? WHERE id = ?",
            (_now(), entry_id),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT is_favorite FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return row["is_favorite"] if row else 0

    def set_entry_image(self, entry_id: int, image_path: str) -> None:
        """更新条目的关联图片路径（阶段三：图片预览）"""
        self.conn.execute(
            "UPDATE entries SET image_path = ?, updated_at = ? WHERE id = ?",
            (image_path, _now(), entry_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 复制/移动分类子树（2026-08-21 第004条新增：各级目录"复制到/移动到"）
    # 语义（经用户两轮确认，见 20260821_PromptSprite_04 开发工作记录 第003条）：
    #   - 复制 = 多重关联 / 新建改名副本（源保留）；移动 = 调整关联 / 改名重挂载 + 删除源
    #   - 级别永不改变（平铺子级保持原级别，不产生三级）；下移加前缀、上移去前缀、重名加序号
    # ------------------------------------------------------------------ #
    def unlink_domain_category(self, domain_id: int, category_id: int) -> None:
        """解除 根目录↔一级分类 关联（移动=调整关联关系）"""
        self.conn.execute(
            "DELETE FROM domain_category WHERE domain_id = ? AND category_id = ?",
            (domain_id, category_id),
        )
        self.conn.commit()

    @staticmethod
    def strip_prefix(name: str) -> str:
        """上移去前缀：去掉最左侧 'xxx.' 前缀段（对话框默认值，用户可编辑）"""
        return name.split(".", 1)[1] if "." in name else name

    def _next_domain_order_tx(self) -> int:
        """事务内：取下一个根目录排序号（不 commit）"""
        return self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM domains"
        ).fetchone()[0]

    def _domain_name_exists(self, name: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM domains WHERE name = ? LIMIT 1", (name,)).fetchone()
        return row is not None

    def unique_domain_name(self, name: str) -> str:
        """新建根目录名去重（name → name(2) → ...；domains.name 有 UNIQUE 约束）"""
        if not self._domain_name_exists(name):
            return name
        i = 2
        while self._domain_name_exists(f"{name}({i})"):
            i += 1
        return f"{name}({i})"

    def category_name_exists(self, name: str, parent_id: Optional[int] = None,
                             domain_id: Optional[int] = None) -> bool:
        """检测分类名在目标位置是否已存在：
        - parent_id 非空 → 该父级下的子分类；
        - parent_id 为空且 domain_id 非空 → 该根目录关联的一级分类。
        """
        if parent_id is not None:
            row = self.conn.execute(
                "SELECT 1 FROM categories WHERE parent_id = ? AND name = ? LIMIT 1",
                (parent_id, name),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM categories c JOIN domain_category dc ON dc.category_id = c.id "
                "WHERE dc.domain_id = ? AND c.parent_id IS NULL AND c.name = ? LIMIT 1",
                (domain_id, name),
            ).fetchone()
        return row is not None

    def unique_category_name(self, name: str, parent_id: Optional[int] = None,
                             domain_id: Optional[int] = None) -> str:
        """目标位置下分类名自动加序号：name → name(2) → name(3) ..."""
        if not self.category_name_exists(name, parent_id=parent_id, domain_id=domain_id):
            return name
        i = 2
        while self.category_name_exists(f"{name}({i})", parent_id=parent_id, domain_id=domain_id):
            i += 1
        return f"{name}({i})"

    def _insert_category_tx(self, parent_id: Optional[int], name: str,
                            new_domain_id: Optional[int] = None) -> int:
        """事务内新建分类（不 commit）；parent_id=None 且给 new_domain_id 时建立根目录关联"""
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories WHERE parent_id IS ?",
            (parent_id,),
        ).fetchone()[0]
        ts = _now()  # 2026-08-29（增量备份增强）：复制等新建分类记录时间戳
        cur = self.conn.execute(
            "INSERT INTO categories(parent_id, name, sort_order, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (parent_id, name, order, ts, ts),
        )
        cid = cur.lastrowid
        if parent_id is None and new_domain_id is not None:
            self.conn.execute(
                "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
                (new_domain_id, cid),
            )
        return cid

    def _copy_aux_tx(self, src_id: int, new_id: int, ts: str) -> None:
        """事务内复制条目的"附加数据"：自定义字段取值 + 标签关联 + 图集（2-d，不 commit）。

        - 图集本地图复制为独立新文件（避免删一份误删另一份）；外链图直接复制记录；
        - 封面（entries.image_path）沿用既有"引用同一文件"策略，不在本方法处理。
        """
        for r in self.conn.execute(
                "SELECT field_key, value_text, value_json FROM entry_field_values"
                " WHERE entry_id = ?", (src_id,)):
            self.conn.execute(
                "INSERT OR REPLACE INTO entry_field_values(entry_id, field_key,"
                " value_text, value_json, updated_at) VALUES(?, ?, ?, ?, ?)",
                (new_id, r["field_key"], r["value_text"], r["value_json"], ts))
        for r in self.conn.execute(
                "SELECT tag_id FROM entry_tags WHERE entry_id = ?", (src_id,)):
            self.conn.execute(
                "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id, created_at)"
                " VALUES(?, ?, ?)", (new_id, r["tag_id"], ts))
        for _i, _g in enumerate(self.list_entry_images(src_id)):
            if _g.get("kind") == "url":
                self.conn.execute(
                    "INSERT INTO entry_images(entry_id, kind, path, source_url,"
                    " is_primary, sort_order, caption, created_at) VALUES(?,?,?,?,0,?,?,?)",
                    (new_id, "url", "", _g.get("source_url") or "", _i,
                     _g.get("caption") or "", ts))
                continue
            _rel = self._copy_gallery_file(_g.get("path") or "", new_id, _i)
            if _rel:
                self.conn.execute(
                    "INSERT INTO entry_images(entry_id, kind, path, source_url,"
                    " is_primary, sort_order, caption, created_at) VALUES(?,?,?,?,0,?,?,?)",
                    (new_id, "local", _rel, _g.get("source_url") or "", _i,
                     _g.get("caption") or "", ts))

    def _copy_subtree_tx(self, src_id: int, new_parent_id: Optional[int], new_name: str,
                         new_domain_id: Optional[int] = None) -> int:
        """事务内深拷贝分类子树（含条目；条目封面引用同一文件，不 commit）。返回新分类 id

        2026-09-13（2-d）：条目副本一并复制**自定义字段取值、标签关联与图集**
        （此前遗漏；标签为 V2 定案"复制分类子树时标签一并复制"）。
        """
        cid = self._insert_category_tx(new_parent_id, new_name, new_domain_id)
        ts = _now()
        for e in self.list_entries(src_id):
            # 2026-09-17（FR-93）：分类子树深拷贝产出的是**独立副本** ⇒ uuid 置空以分配**新** ID
            #   （不可沿用原条目的 uuid，否则跨机器会被误认成"同一条目"）。
            entry = Entry(**{**e, "id": None, "category_id": cid, "uuid": ""})
            cur = self.conn.execute(
                "INSERT INTO entries(category_id, name, intro, origin, features, scenes, works, "
                "image_desc, prompt_cn, prompt_en, image_plan, image_path, is_favorite, "
                "created_at, updated_at, uuid) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*self._entry_params(entry), ts, ts, new_entry_uuid()),
            )
            self._copy_aux_tx(e["id"], cur.lastrowid, ts)
        for child in self.list_categories(parent_id=src_id):
            self._copy_subtree_tx(child["id"], cid, child["name"])
        return cid

    # ---- 根目录 A → 根目录 B（作 B 的一级分类） ----
    def move_domain_to_domain(self, domain_id: int, target_domain_id: int) -> dict:
        """移动根目录 A 到根目录 B 下作为一级分类：
        1) A 的每个一级分类 C：仅被 A 关联 → 改名 'A.C' 并建立与 B 的关联；
           被多根目录共享 → 复制改名副本 'A.C' 挂 B 下（原共享分类保留原名）；
        2) 删除根目录 A。E、F 等二级分类级别与名称不变。
        """
        if domain_id == target_domain_id:
            raise ValueError("不能移动到自身")
        src = self.get_domain(domain_id)
        target = self.get_domain(target_domain_id)
        if not src or not target:
            raise ValueError("根目录不存在")
        prefix = src["name"] + "."
        stats = {"renamed": 0, "copied": 0}
        try:
            with self.conn:
                for l1 in self.list_categories(domain_id=domain_id, parent_id=None):
                    new_name = self.unique_category_name(prefix + l1["name"],
                                                         domain_id=target_domain_id)
                    if len(self.linked_domains(l1["id"])) <= 1:
                        # 仅被 A 关联：改名 + 建立与 B 的关联
                        self.conn.execute(
                            "UPDATE categories SET name = ?, updated_at = ? WHERE id = ?",
                            (new_name, _now(), l1["id"]))  # 2026-08-29：改名同步 updated_at
                        self.conn.execute(
                            "INSERT OR IGNORE INTO domain_category(domain_id, category_id) "
                            "VALUES(?, ?)", (target_domain_id, l1["id"]),
                        )
                        stats["renamed"] += 1
                    else:
                        # 被多根目录共享：复制改名副本，原共享分类保留原名
                        self._copy_subtree_tx(l1["id"], None, new_name, target_domain_id)
                        stats["copied"] += 1
                self.conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
            return stats
        except Exception:
            self.conn.rollback()
            raise

    def copy_domain_to_domain(self, domain_id: int, target_domain_id: int) -> dict:
        """复制根目录 A 到根目录 B 下作为一级分类：为 A 的每个一级分类建改名副本 'A.C' 挂 B 下，A 保留"""
        if domain_id == target_domain_id:
            raise ValueError("不能复制到自身")
        src = self.get_domain(domain_id)
        target = self.get_domain(target_domain_id)
        if not src or not target:
            raise ValueError("根目录不存在")
        prefix = src["name"] + "."
        stats = {"copied": 0}
        try:
            with self.conn:
                for l1 in self.list_categories(domain_id=domain_id, parent_id=None):
                    new_name = self.unique_category_name(prefix + l1["name"],
                                                         domain_id=target_domain_id)
                    self._copy_subtree_tx(l1["id"], None, new_name, target_domain_id)
                    stats["copied"] += 1
            return stats
        except Exception:
            self.conn.rollback()
            raise

    # ---- 一级分类 C ----
    def move_l1_to_domain(self, category_id: int, from_domain_id: int,
                          target_domain_id: Optional[int] = None,
                          new_name: Optional[str] = None) -> dict:
        """移动一级分类 C：将 C 的关联关系从 from_domain 调整为 target_domain（同级平移，不改名）；
        target_domain_id=None → 新建根目录项（默认名去前缀，可传 new_name 指定）。"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is not None:
            raise ValueError("仅支持一级分类")
        if target_domain_id is None:
            new_dom = self.unique_domain_name(new_name or self.strip_prefix(cat["name"]))
            try:
                with self.conn:
                    cur = self.conn.execute(
                        "INSERT INTO domains(name, sort_order) VALUES(?, ?)",
                        (new_dom, self._next_domain_order_tx()),
                    )
                    target_domain_id = cur.lastrowid
            except Exception:
                self.conn.rollback()
                raise
        if from_domain_id == target_domain_id:
            raise ValueError("目标根目录与来源相同")
        try:
            with self.conn:
                self.conn.execute(
                    "DELETE FROM domain_category WHERE domain_id = ? AND category_id = ?",
                    (from_domain_id, category_id),
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
                    (target_domain_id, category_id),
                )
            return {"domain_id": target_domain_id}
        except Exception:
            self.conn.rollback()
            raise

    def copy_l1_to_domain(self, category_id: int,
                          target_domain_id: Optional[int] = None,
                          new_name: Optional[str] = None) -> dict:
        """复制一级分类 C 到根目录：多重关联（目标根目录建立关联，保留原关联）；
        target_domain_id=None → 新建根目录项（默认名去前缀，可传 new_name 指定）。"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is not None:
            raise ValueError("仅支持一级分类")
        if target_domain_id is None:
            new_dom = self.unique_domain_name(new_name or self.strip_prefix(cat["name"]))
            try:
                with self.conn:
                    cur = self.conn.execute(
                        "INSERT INTO domains(name, sort_order) VALUES(?, ?)",
                        (new_dom, self._next_domain_order_tx()),
                    )
                    target_domain_id = cur.lastrowid
            except Exception:
                self.conn.rollback()
                raise
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
                    (target_domain_id, category_id),
                )
            return {"domain_id": target_domain_id}
        except Exception:
            self.conn.rollback()
            raise

    def move_l1_to_l2(self, category_id: int, target_l1_id: int) -> dict:
        """移动一级分类 C 到一级分类 D 下作二级：C 的直接子级改名加前缀(C.)并挂到 D 下（级别不变），删除 C"""
        src = self.get_category(category_id)
        target = self.get_category(target_l1_id)
        if not src or src["parent_id"] is not None:
            raise ValueError("仅支持一级分类")
        if not target or target["parent_id"] is not None:
            raise ValueError("目标必须是一级分类")
        if category_id == target_l1_id:
            raise ValueError("不能移动到自身")
        prefix = src["name"] + "."
        stats = {"children": 0}
        try:
            with self.conn:
                for child in self.list_categories(parent_id=category_id):
                    new_name = self.unique_category_name(prefix + child["name"],
                                                         parent_id=target_l1_id)
                    self.conn.execute(
                        "UPDATE categories SET name = ?, parent_id = ?, updated_at = ? WHERE id = ?",
                        (new_name, target_l1_id, _now(), child["id"]))  # 2026-08-29：移动同步 updated_at
                    stats["children"] += 1
                # 2026-08-21（第005条修复）：源一级分类直接挂载的条目先迁移到目标一级分类下，
                # 避免删除源分类时因外键 ON DELETE SET NULL 转入未分类（复制/移动不删除条目）
                self.conn.execute(
                    "UPDATE entries SET category_id = ?, updated_at = ? WHERE category_id = ?",
                    (target_l1_id, _now(), category_id),
                )
                # 源一级分类已无子级，安全删除（其根目录关联级联清理）
                self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            return stats
        except Exception:
            self.conn.rollback()
            raise

    def copy_l1_to_l2(self, category_id: int, target_l1_id: int) -> dict:
        """复制一级分类 C 到一级分类 D 下作二级：C 的直接子级复制改名副本(C.)挂 D 下，C 保留"""
        src = self.get_category(category_id)
        target = self.get_category(target_l1_id)
        if not src or src["parent_id"] is not None:
            raise ValueError("仅支持一级分类")
        if not target or target["parent_id"] is not None:
            raise ValueError("目标必须是一级分类")
        if category_id == target_l1_id:
            raise ValueError("不能复制到自身")
        prefix = src["name"] + "."
        stats = {"children": 0}
        try:
            with self.conn:
                for child in self.list_categories(parent_id=category_id):
                    new_name = self.unique_category_name(prefix + child["name"],
                                                         parent_id=target_l1_id)
                    self._copy_subtree_tx(child["id"], target_l1_id, new_name)
                    stats["children"] += 1
            return stats
        except Exception:
            self.conn.rollback()
            raise

    # ---- 二级分类 E ----
    def move_l2_to_domain(self, category_id: int,
                          target_domain_id: Optional[int] = None) -> dict:
        """移动二级分类 E 到根目录下作一级：E 提升为一级（parent 置空）并关联目标根目录；
        target_domain_id=None → 新建根目录项（默认名去前缀）。"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is None:
            raise ValueError("仅支持二级分类")
        if target_domain_id is None:
            new_dom = self.unique_domain_name(self.strip_prefix(cat["name"]))
            try:
                with self.conn:
                    cur = self.conn.execute(
                        "INSERT INTO domains(name, sort_order) VALUES(?, ?)",
                        (new_dom, self._next_domain_order_tx()),
                    )
                    target_domain_id = cur.lastrowid
            except Exception:
                self.conn.rollback()
                raise
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE categories SET parent_id = NULL, updated_at = ? WHERE id = ?",
                    (_now(), category_id))  # 2026-08-29：提升一级同步 updated_at
                self.conn.execute(
                    "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
                    (target_domain_id, category_id),
                )
            return {"domain_id": target_domain_id}
        except Exception:
            self.conn.rollback()
            raise

    def copy_l2_to_domain(self, category_id: int,
                          target_domain_id: Optional[int] = None,
                          new_name: Optional[str] = None) -> dict:
        """复制二级分类 E 到根目录下作一级：建改名副本（默认去前缀）提升为一级并关联目标根目录；E 保留"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is None:
            raise ValueError("仅支持二级分类")
        base = new_name or self.strip_prefix(cat["name"])
        if target_domain_id is not None:
            new_name = self.unique_category_name(base, domain_id=target_domain_id)
        else:
            new_name = base
        if target_domain_id is None:
            new_dom = self.unique_domain_name(new_name)
            try:
                with self.conn:
                    cur = self.conn.execute(
                        "INSERT INTO domains(name, sort_order) VALUES(?, ?)",
                        (new_dom, self._next_domain_order_tx()),
                    )
                    target_domain_id = cur.lastrowid
            except Exception:
                self.conn.rollback()
                raise
        try:
            with self.conn:
                new_cat_id = self._copy_subtree_tx(category_id, None, new_name, target_domain_id)
            return {"category_id": new_cat_id, "domain_id": target_domain_id}
        except Exception:
            self.conn.rollback()
            raise

    def move_l2_to_l2(self, category_id: int, target_l1_id: int) -> dict:
        """移动二级分类 E 到一级分类 D 下作二级：同级平移（parent 改 D，名称不变）"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is None:
            raise ValueError("仅支持二级分类")
        target = self.get_category(target_l1_id)
        if not target or target["parent_id"] is not None:
            raise ValueError("目标必须是一级分类")
        if cat["parent_id"] == target_l1_id:
            raise ValueError("目标与当前父级相同")
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE categories SET parent_id = ?, updated_at = ? WHERE id = ?",
                    (target_l1_id, _now(), category_id))  # 2026-08-29：移动同步 updated_at
            return {}
        except Exception:
            self.conn.rollback()
            raise

    def copy_l2_to_l2(self, category_id: int, target_l1_id: int) -> dict:
        """复制二级分类 E 到一级分类 D 下作二级：建副本挂 D 下（重名自动加序号），E 保留"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is None:
            raise ValueError("仅支持二级分类")
        target = self.get_category(target_l1_id)
        if not target or target["parent_id"] is not None:
            raise ValueError("目标必须是一级分类")
        if cat["parent_id"] == target_l1_id:
            raise ValueError("目标与当前父级相同")
        new_name = self.unique_category_name(cat["name"], parent_id=target_l1_id)
        try:
            with self.conn:
                new_cat_id = self._copy_subtree_tx(category_id, target_l1_id, new_name)
            return {"category_id": new_cat_id}
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def stats(self) -> dict:
        return {
            "projects": self.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "domains": self.conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0],
            "categories": self.conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
            "entries": self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
            "uncategorized": self.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE category_id IS NULL"
            ).fetchone()[0],
            "favorites": self.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE is_favorite = 1"
            ).fetchone()[0],
        }


# ---------------------------------------------------------------------- #
# 自测
# ---------------------------------------------------------------------- #
def _selftest() -> None:
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_selftest_")
    db = Database(os.path.join(tmp, "test.db"))
    try:
        # 1. 预置根目录
        db.seed_preset_domains()
        domains = db.list_domains()
        # 2026-08-18 15:50：断言由 7 个预置根目录更新为 8 个（新增"视觉风格分类"，P0-1 修复）
        assert len(domains) == 8, f"预置根目录应为8个，实际 {len(domains)}"
        assert [d["name"] for d in domains] == ["计算机编程", "视频", "图像", "音频", "文学", "学术", "专业报告", "视觉风格分类"]
        print("[1] 预置根目录 通过")

        # 2. 根目录增删改查
        vid_id = domains[1]["id"]
        extra_id = db.add_domain("测试域")
        assert db.get_domain(extra_id)["name"] == "测试域"
        db.rename_domain(extra_id, "测试域2")
        assert db.get_domain(extra_id)["name"] == "测试域2"
        print("[2] 根目录增删改查 通过")

        # 3. 分类（L1维度 → L2大类）+ 多对一共享
        l1 = db.add_category("第一维度：按媒介&艺术载体总分类", domain_id=vid_id)
        l2 = db.add_category("写实影像类", parent_id=l1)
        assert db.list_categories(domain_id=vid_id, parent_id=None)[0]["name"] == "第一维度：按媒介&艺术载体总分类"
        assert db.list_categories(domain_id=vid_id, parent_id=l1)[0]["name"] == "写实影像类"
        # 同一 L1 关联到 图像 领域（多对一）
        img_id = domains[2]["id"]
        db.link_domain_category(img_id, l1)
        assert db.list_categories(domain_id=img_id, parent_id=None)[0]["name"] == "第一维度：按媒介&艺术载体总分类"
        assert len(db.linked_domains(l1)) == 2
        print("[3] 二级分类 + 多对一共享 通过")

        # 4. 条目新增 + 9字段回读
        e = Entry(
            category_id=l2, name="35mm电影胶片风",
            intro="好莱坞院线标准商业电影写实基底",
            origin="1960年后好莱坞35mm胶片工业体系",
            features="2.39:1宽遮幅，橙蓝冷暖对冲",
            scenes="都市情感短剧、悬疑犯罪",
            works="《盗梦空间》《流浪地球》",
            image_desc="雨夜城市街道，冷蓝夜色搭配暖橙路灯",
            prompt_cn="4K超高清，2.39:1宽幅遮幅电影画面，35mm胶片实拍…",
            prompt_en="4K ultra HD, 2.39:1 widescreen cinematic frame…",
            image_plan="绘图工具设置比例21:9、4K分辨率",
        )
        eid = db.add_entry(e)
        got = db.get_entry(eid)
        assert got["name"] == "35mm电影胶片风" and got["prompt_cn"].startswith("4K超高清")
        assert got["category_id"] == l2 and got["prompt_en"].startswith("4K ultra HD")
        print("[4] 条目新增/9字段回读 通过")

        # 5. 编辑保存
        e2 = Entry(**{k: got[k] for k in got})
        e2.name = "35mm电影胶片风（新版）"
        e2.features += "；新增特征测试"
        db.update_entry(e2)
        got2 = db.get_entry(eid)
        assert got2["name"] == "35mm电影胶片风（新版）" and "新增特征测试" in got2["features"]
        print("[5] 条目编辑 通过")

        # 6. 搜索
        hits = db.search("胶片")
        assert any(h["id"] == eid for h in hits)
        print("[6] 全局搜索 通过")

        # 7. 收藏
        assert db.toggle_favorite(eid) == 1
        assert any(f["id"] == eid for f in db.list_favorites())
        assert db.toggle_favorite(eid) == 0
        print("[7] 收藏切换 通过")

        # 8. 未分类与移动
        u1 = db.add_entry(Entry(name="未分类测试条目"))
        assert any(u["name"] == "未分类测试条目" for u in db.list_uncategorized())
        db.move_entry(u1, l1)
        assert db.get_entry(u1)["category_id"] == l1
        db.move_entry(u1, None)
        assert db.get_entry(u1)["category_id"] is None
        print("[8] 未分类/移动 通过")

        # 9. 删除分类 → 条目自动转入未分类
        stat = db.count_descendants(l1)
        assert stat == {"categories": 1, "entries": 1}, f"统计异常 {stat}"
        deleted = db.delete_category(l1)
        assert deleted == {"categories": 1, "entries": 1}
        assert db.get_entry(eid)["category_id"] is None
        print("[9] 删除分类转入未分类 通过")

        # 10. 删除根目录 → 仅解除关联，共享分类/条目保留
        # 2026-08-18 15:52：原断言依赖 [9] 已删除的 l1，必然失败；改为重建共享分类后验证
        l1b = db.add_category("共享维度B", domain_id=vid_id)
        db.link_domain_category(img_id, l1b)  # 同一级分类再关联到图像领域（多对一）
        dstat = db.delete_domain(extra_id)
        assert dstat == {"categories": 0, "entries": 0}
        db.delete_domain(img_id)  # 删除图像领域
        assert db.list_categories(domain_id=img_id, parent_id=None) == []  # 图像领域视角为空
        assert db.list_categories(domain_id=vid_id, parent_id=None)[0]["name"] == "共享维度B"  # 视频领域仍可见
        print("[10] 删除根目录仅解除关联 通过")

        # 11. 元信息
        db.set_meta("k", "v")
        assert db.get_meta("k") == "v"
        print("[11] 元信息 通过")

        # 12. 根目录→根目录 移动/复制（2026-08-21 第004条新增）
        da = db.add_domain("A")
        dbx = db.add_domain("B")
        c1 = db.add_category("C", domain_id=da)
        c2 = db.add_category("D", domain_id=da)
        e1 = db.add_category("E", parent_id=c1)
        e2 = db.add_category("F", parent_id=c1)
        en1 = db.add_entry(Entry(name="EF条目", category_id=e1))
        # 12.1 移动根目录 A → B：C/D 改名 A.C/A.D 并关联 B，E/F 不变，A 删除
        db.move_domain_to_domain(da, dbx)
        assert db.get_domain(da) is None
        assert db.get_category(c1)["name"] == "A.C"
        assert db.get_category(c2)["name"] == "A.D"
        assert db.get_category(c1)["parent_id"] is None
        assert db.get_category(e1)["name"] == "E" and db.get_category(e1)["parent_id"] == c1
        assert db.get_category(e2)["name"] == "F"
        assert db.get_entry(en1)["category_id"] == e1
        assert any(x["id"] == dbx for x in db.linked_domains(c1))
        print("[12] 移动根目录→另一根目录(作一级) 通过")
        # 12.2 复制根目录 B → 新根目录 C：副本名 B.A.C/B.A.D 且带子树条目
        dc = db.add_domain("C")
        db.copy_domain_to_domain(dbx, dc)
        copied = db.list_categories(domain_id=dc, parent_id=None)
        assert sorted(c["name"] for c in copied) == ["B.A.C", "B.A.D"]
        b_ac = [c for c in copied if c["name"] == "B.A.C"][0]
        subs = db.list_categories(parent_id=b_ac["id"])
        assert sorted(s["name"] for s in subs) == ["E", "F"]
        assert len(db.list_entries(subs[0]["id"])) == 1
        print("[13] 复制根目录→新根目录(作一级) 通过")
        # 12.3 移动一级分类到"新建根目录项"（默认名去前缀，重名加序号）
        new_d = db.move_l1_to_domain(c1, dbx, None)
        assert db.get_domain(new_d["domain_id"])["name"] == "C(2)"  # 域"C"已存在
        assert not any(x["id"] == dbx for x in db.linked_domains(c1))
        assert any(x["id"] == new_d["domain_id"] for x in db.linked_domains(c1))
        print("[14] 移动一级分类→新建根目录项 通过")
        # 12.4 复制一级分类到根目录（多重关联）
        db.copy_l1_to_domain(c1, dc)
        ids = [x["id"] for x in db.linked_domains(c1)]
        assert new_d["domain_id"] in ids and dc in ids
        print("[15] 复制一级分类→根目录(多重关联) 通过")
        # 12.5 移动一级分类 → 一级分类下作二级（子级改名加前缀；源直挂条目随迁）
        l1g = db.add_category("G", domain_id=dbx)
        l2h = db.add_category("H", parent_id=l1g)
        l2i = db.add_category("I", parent_id=l1g)
        en2 = db.add_entry(Entry(name="HI条目", category_id=l2h))
        en3 = db.add_entry(Entry(name="G直挂条目", category_id=l1g))  # 005 修复验证
        db.move_l1_to_l2(l1g, b_ac["id"])
        assert db.get_category(l1g) is None
        assert db.get_category(l2h)["name"] == "G.H"
        assert db.get_category(l2h)["parent_id"] == b_ac["id"]
        assert db.get_category(l2i)["name"] == "G.I"
        assert db.get_entry(en2)["category_id"] == l2h
        # 005 修复：源一级分类直接挂载的条目迁移到目标一级分类，不转入未分类
        assert db.get_entry(en3)["category_id"] == b_ac["id"]
        assert db.get_entry(en3)["name"] == "G直挂条目"
        print("[16] 移动一级分类→一级分类下作二级 通过")
        # 12.6 复制一级分类 → 一级分类下作二级（源保留）
        l1j = db.add_category("J", domain_id=dc)
        l2k = db.add_category("K", parent_id=l1j)
        db.copy_l1_to_l2(l1j, b_ac["id"])
        assert any(s["name"] == "J.K" for s in db.list_categories(parent_id=b_ac["id"]))
        assert db.get_category(l1j) is not None and db.get_category(l2k)["parent_id"] == l1j
        print("[17] 复制一级分类→一级分类下作二级 通过")
        # 12.7 二级分类 移动/复制
        e_new = db.move_l2_to_domain(e1, None)  # E 提升为一级 + 新建根目录项(名 E)
        assert db.get_category(e1)["parent_id"] is None
        assert db.get_domain(e_new["domain_id"])["name"] == "E"
        assert any(x["id"] == e_new["domain_id"] for x in db.linked_domains(e1))
        db.copy_l2_to_l2(e2, b_ac["id"])
        assert any(s["name"] == "F" for s in db.list_categories(parent_id=b_ac["id"]))
        assert db.get_category(e2)["parent_id"] == c1
        db.move_l2_to_l2(e2, b_ac["id"])
        assert db.get_category(e2)["parent_id"] == b_ac["id"]
        print("[18] 二级分类 移动/复制 通过")
        # 12.8 重名自动加序号
        l2h2 = db.add_category("H", parent_id=l1j)
        db.copy_l2_to_l2(l2h2, b_ac["id"])
        db.copy_l2_to_l2(l2h2, b_ac["id"])
        names = [s["name"] for s in db.list_categories(parent_id=b_ac["id"])]
        assert names.count("H") == 1 and names.count("H(2)") == 1
        print("[19] 重名自动加序号 通过")
        # 12.9 共享一级分类 → 移动根目录时退化为复制副本
        dom_p = db.add_domain("P")
        dom_q = db.add_domain("Q")
        l1m = db.add_category("M", domain_id=dom_p)
        db.link_domain_category(dom_q, l1m)
        dom_r = db.add_domain("R")
        st = db.move_domain_to_domain(dom_p, dom_r)
        assert st == {"renamed": 0, "copied": 1}
        assert db.get_category(l1m)["name"] == "M"
        rm = db.list_categories(domain_id=dom_r, parent_id=None)
        assert len(rm) == 1 and rm[0]["name"] == "P.M"
        assert any(x["id"] == dom_q for x in db.linked_domains(l1m))
        print("[20] 共享分类移动退化为复制 通过")

        # ---- 21~24：四级分类（2026-08-29 M1 新增）----
        # 21. 项目类别预置 + 预置根目录自动归属 + 过滤
        projects = db.list_projects()
        assert len(projects) == 5
        pnames = [p["name"] for p in projects]
        assert pnames == ["日常学习记录", "网上资源收集", "个人梳理资源", "本人创作作品", "个人经验总结"]
        p_map = {p["name"]: p["id"] for p in projects}
        dom_by_name = {d["name"]: d for d in db.list_domains()}
        preset_names = {"计算机编程", "视频", "图像", "音频", "文学", "学术", "专业报告", "视觉风格分类"}
        existing = set(dom_by_name)
        # 说明：测试[10]已删除"图像"等根目录，此处仅校验仍存在的预置根目录均已自动归属
        assert all(dom_by_name[n].get("project_id") is not None
                   for n in preset_names & existing), "预置根目录应已自动归入项目"
        assert dom_by_name["视频"]["project_id"] == p_map["日常学习记录"]
        assert dom_by_name["计算机编程"]["project_id"] == p_map["个人经验总结"]
        assert dom_by_name["视觉风格分类"]["project_id"] == p_map["个人梳理资源"]
        # 图像已在[10]删除，仅校验现存预置
        assert {d["name"] for d in db.list_domains(project_id=p_map["日常学习记录"])} == \
            {"视频", "音频", "文学", "学术", "专业报告"}
        print("[21] 项目类别预置/自动归属/过滤 通过")

        # 22. 项目类别增删改 + 删除兜底
        p_extra = db.add_project("测试项目")
        assert db.get_project(p_extra)["name"] == "测试项目"
        db.rename_project(p_extra, "测试项目2")
        assert db.get_project(p_extra)["name"] == "测试项目2"
        dom_tmp = db.add_domain("临时域", project_id=p_extra)
        assert db.get_domain(dom_tmp)["project_id"] == p_extra
        fallback_id = db.ensure_project("未明确分类")
        stat = db.delete_project(p_extra, fallback_project_id=fallback_id)
        assert stat == {"domains": 1}
        assert db.get_domain(dom_tmp)["project_id"] == fallback_id
        assert db.get_project(p_extra) is None
        print("[22] 项目类别增删改/删除兜底 通过")

        # 23. 移动/复制根目录到项目类别
        p_src = p_map["个人经验总结"]
        p_dst = p_map["网上资源收集"]
        dom_m = db.add_domain("移动域", project_id=p_src)
        db.move_domain_to_project(dom_m, p_dst)
        assert db.get_domain(dom_m)["project_id"] == p_dst
        dom_c = db.add_domain("复制域", project_id=p_src)
        c_l1 = db.add_category("复制一级", domain_id=dom_c)
        c_l2 = db.add_category("复制二级", parent_id=c_l1)
        db.add_entry(Entry(name="复制条目", category_id=c_l2))
        new_id = db.copy_domain_to_project(dom_c, p_dst, db.unique_domain_name("复制域"))
        assert db.get_domain(new_id)["project_id"] == p_dst
        assert db.get_domain(dom_c)["project_id"] == p_src  # 源保留
        new_l1 = db.list_categories(domain_id=new_id, parent_id=None)
        assert len(new_l1) == 1 and new_l1[0]["name"] == "复制一级"
        new_l2 = db.list_categories(parent_id=new_l1[0]["id"])
        assert len(new_l2) == 1 and new_l2[0]["name"] == "复制二级"
        assert len(db.list_entries(new_l2[0]["id"])) == 1
        print("[23] 移动/复制根目录到项目类别 通过")

        # 24. assign_domains_to_projects：未命中 → 选择 / 兜底 / 幂等
        for d in db.list_unassigned_domains():   # 先把测试遗留的无归属根目录隔离
            db.move_domain_to_project(d["id"], p_src)
        dom_x = db.add_domain("未知根目录X")
        dom_y = db.add_domain("未知根目录Y")

        def _choose(name, projects):
            return "日常学习记录" if name == "未知根目录X" else None

        st = db.assign_domains_to_projects({}, _choose)
        assert st == {"matched": 0, "unmatched": 1, "fallback": 1}, st
        fb = db.get_project_by_name("未明确分类")["id"]
        assert db.get_domain(dom_x)["project_id"] == p_map["日常学习记录"]
        assert db.get_domain(dom_y)["project_id"] == fb
        st2 = db.assign_domains_to_projects({}, _choose)   # 幂等：无新变化
        assert st2 == {"matched": 0, "unmatched": 0, "fallback": 0}
        print("[24] 归属分配/未命中兜底/幂等 通过")

        # ---- 25~27：增量备份增强（2026-08-29）----
        # 25. 分类时间戳：新建/改名/移动会写入 created_at/updated_at
        ts_cat = db.add_category("时间戳分类", domain_id=p_src)
        c_row = db.get_category(ts_cat)
        assert c_row["created_at"] and c_row["updated_at"], "新建分类应带时间戳"
        db.rename_category(ts_cat, "时间戳分类2")
        assert db.get_category(ts_cat)["updated_at"] >= c_row["updated_at"]
        today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
        changed = db.list_categories_changed_since(today_start)
        assert any(x["id"] == ts_cat for x in changed), "当日新建分类应被 list_categories_changed_since 命中"
        print("[25] 分类时间戳/当日变更查询 通过")

        # 26. 删除日志：条目/分类(级联)/根目录
        e_del = db.add_entry(Entry(name="待删条目", category_id=c_l2))
        db.delete_entry(e_del)
        logs = db.list_deletions_since(today_start)
        entry_log = [x for x in logs if x["kind"] == "entry" and x["name"] == "待删条目"]
        assert entry_log and entry_log[0]["content_key"], "删除条目应记录内容键"
        # 删除分类（含子分类级联日志）
        cat_del = db.add_category("待删一级", domain_id=p_src)
        cat_del2 = db.add_category("待删二级", parent_id=cat_del)
        db.delete_category(cat_del)
        cat_logs = [x for x in db.list_deletions_since(today_start)
                    if x["kind"] == "category"]
        assert any(x["name"] == "待删一级" and json.loads(x["chain"]) == ["待删一级"]
                   for x in cat_logs)
        assert any(x["name"] == "待删二级" and json.loads(x["chain"]) == ["待删一级", "待删二级"]
                   for x in cat_logs)
        # 删除根目录日志
        dom_del = db.add_domain("待删根目录")
        db.delete_domain(dom_del)
        assert any(x["kind"] == "domain" and x["name"] == "待删根目录"
                   for x in db.list_deletions_since(today_start))
        print("[26] 删除日志（条目/分类级联/根目录）通过")

        # 27. find_category_by_chain + delete_entries_by_content_key
        ch_l1 = db.add_category("链一级", domain_id=p_src)
        ch_l2 = db.add_category("链二级", parent_id=ch_l1)
        e1 = db.add_entry(Entry(name="同内容", intro="X", category_id=ch_l2))
        e2 = db.add_entry(Entry(name="同内容", intro="X", category_id=ch_l2))
        assert db.find_category_by_chain(["链一级", "链二级"]) == ch_l2
        assert db.find_category_by_chain(["链一级", "不存在"]) is None
        k = Database.content_key(db.get_entry(e1))
        n = db.delete_entries_by_content_key(k, cat_id=ch_l2)
        assert n == 2, n   # 内容键相同的两条都被删除
        assert db.get_entry(e1) is None and db.get_entry(e2) is None
        print("[27] 按名称链查找/按内容键删除 通过")

        # ---- 28~30：条目多位置 / 复制到 / 删除保护（2026-09-07 施工）----
        # 28. 关联/解除/统一原语/整体转移/未分类回落
        pj_id = db.list_projects()[0]["id"]
        dom_m = db.add_domain("多位置域", project_id=pj_id)
        la = db.add_category("AA", domain_id=dom_m)
        lb = db.add_category("BB", domain_id=dom_m)
        lc = db.add_category("CC", parent_id=la)
        ld = db.add_category("DD", parent_id=lb)
        m1 = db.add_entry(Entry(name="多位置条目", category_id=lc, prompt_cn="P1"))
        db.link_entry(m1, ld)
        assert set(db.list_entry_locations(m1)) == {lc, ld}
        assert len(db.list_entries(ld)) == 1 and db.count_entries(ld) == 1
        assert all(x["id"] != m1 for x in db.list_uncategorized())
        db.unlink_entry(m1, ld)
        assert db.list_entry_locations(m1) == [lc] and db.count_entries(ld) == 0
        # 统一原语：add 两处 + remove 主挂靠 lc → 提升剩余其一为主挂靠
        db.set_entry_locations(m1, remove=[lc], add=[lb, ld])
        assert set(db.list_entry_locations(m1)) == {lb, ld}
        assert db.get_entry(m1)["category_id"] == min(lb, ld)
        # 整体转移 move_entry → 仅保留目标
        db.move_entry(m1, lb)
        assert db.list_entry_locations(m1) == [lb]
        assert db.count_entries(lb) == 1 and db.count_entries(ld) == 0
        # 唯一位置解除 → 未分类
        db.unlink_entry(m1, lb)
        assert db.list_entry_locations(m1) == []
        assert db.get_entry(m1)["category_id"] is None
        assert any(x["id"] == m1 for x in db.list_uncategorized())
        print("[28] 条目多位置 关联/解除/统一原语/整体转移 通过")

        # 29. 复制到（独立副本）：内容拷贝、收藏清零、独立演化
        m2 = db.add_entry(Entry(name="源条目", category_id=lc, intro="I1",
                                prompt_cn="CP", is_favorite=1))
        m2_new = db.copy_entry_to(m2, lb)
        c2 = db.get_entry(m2_new)
        assert m2_new != m2 and c2["category_id"] == lb
        assert c2["name"] == "源条目" and c2["prompt_cn"] == "CP"
        assert c2["is_favorite"] == 0 and db.list_entry_locations(m2_new) == [lb]
        db.update_entry(Entry(id=m2_new, category_id=lb, name="源条目2", prompt_cn="CP2"))
        assert db.get_entry(m2)["prompt_cn"] == "CP"  # 原条目不受副本影响
        print("[29] 条目复制到（独立副本）通过")

        # 30. 分类删除保护：安全删除 / 级联删除短语守卫
        dom_d = db.add_domain("删除域", project_id=pj_id)
        g1 = db.add_category("GA", domain_id=dom_d)
        h1 = db.add_category("HA", parent_id=g1)
        e_in = db.add_entry(Entry(name="子条目", category_id=h1))
        e_share = db.add_entry(Entry(name="共享条目", category_id=h1))
        db.link_entry(e_share, lc)  # 另有其它位置
        try:  # 有子分类 → 安全删除被拒
            db.delete_category_safe(g1)
            raise SystemExit("应拒绝删除含下级分类")
        except ValueError:
            pass
        st = db.delete_category_safe(h1)
        assert st == {"categories": 1, "entries": 2}, st
        assert db.get_category(h1) is None
        assert db.get_entry(e_in)["category_id"] is None           # 无其它位置 → 未分类
        assert any(x["id"] == e_in for x in db.list_uncategorized())
        assert set(db.list_entry_locations(e_share)) == {lc}        # 其它位置保留
        try:  # 短语不符 → 拒绝
            db.delete_category_cascade(g1, "错误短语")
            raise SystemExit("应拒绝错误确认短语")
        except ValueError:
            pass
        h2 = db.add_category("HB", parent_id=g1)
        e_in2 = db.add_entry(Entry(name="子条目2", category_id=h2))
        st2 = db.delete_category_cascade(g1, Database._CASCADE_PHRASE)
        assert st2 == {"categories": 2, "entries": 1}, st2          # g1+h2、条目真删
        assert db.get_category(g1) is None and db.get_category(h2) is None
        assert db.get_entry(e_in2) is None
        print("[30] 分类删除保护：安全删除/级联短语 通过")

        # 31. 条目排序白名单 + 创建时间保真（2026-09-16 批次 11-7，用户要求 3）
        assert Database.entry_order_sql(None) == "updated_at DESC, id"
        assert Database.entry_order_sql("坏值; DROP TABLE") == "updated_at DESC, id", "非法值须回退默认"
        assert "created_at" in Database.entry_order_sql("created")
        assert "name" in Database.entry_order_sql("name")
        _did31 = db.add_domain("排序测试根")
        _cid31 = db.add_category("排序测试类", domain_id=_did31)
        _eid_old = db.add_entry(Entry(category_id=_cid31, name="排序-1",
                                      created_at="2001-01-01 00:00:00"))
        _eid_new = db.add_entry(Entry(category_id=_cid31, name="排序-2"))
        assert db.get_entry(_eid_old)["created_at"] == "2001-01-01 00:00:00", \
            "Entry.created_at 非空时须保留（导入/恢复不丢时间）"
        _ca_new = db.get_entry(_eid_new)["created_at"]
        assert _ca_new and _ca_new != "2001-01-01 00:00:00", "未指定时仍取当前时间"
        _by_created = [x["name"] for x in db.list_entries(_cid31, order_by="created")]
        assert _by_created == ["排序-2", "排序-1"], _by_created         # 新增时间倒序（新的在前）
        _by_name = [x["name"] for x in db.list_entries(_cid31, order_by="name")]
        assert _by_name == ["排序-1", "排序-2"], _by_name               # 名称升序
        assert len(db.list_entries(_cid31, order_by="怪值")) == 2       # 非法排序不报错
        # 批量插入同样保留 created_at
        _n31 = db.add_entries_batch([Entry(category_id=_cid31, name="排序-3",
                                           created_at="2002-02-02 00:00:00")])
        assert _n31 == 1
        _c31 = [x for x in db.list_entries(_cid31, order_by="created")
                if x["name"] == "排序-3"]
        assert _c31 and _c31[0]["created_at"] == "2002-02-02 00:00:00", _c31
        print("[31] 条目排序白名单/创建时间保真 通过")

        print(f"[统计] {db.stats()}")
        print("=== 数据库层全部自测通过 ===")
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _migrate_selftest() -> None:
    """v2 → v3 迁移自测：结构升级 + 归属分配 + 兜底 + 幂等（在临时 v2 库上进行）"""
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_migrate_")
    try:
        v2 = os.path.join(tmp, "v2.db")
        conn = sqlite3.connect(v2)
        conn.executescript("""
            CREATE TABLE domains (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, sort_order INTEGER DEFAULT 0);
            CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER DEFAULT NULL, name TEXT NOT NULL, sort_order INTEGER DEFAULT 0);
            CREATE TABLE domain_category (domain_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL, PRIMARY KEY (domain_id, category_id));
            CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER,
                name TEXT NOT NULL, intro TEXT DEFAULT '', origin TEXT DEFAULT '',
                features TEXT DEFAULT '', scenes TEXT DEFAULT '', works TEXT DEFAULT '',
                image_desc TEXT DEFAULT '', prompt_cn TEXT DEFAULT '', prompt_en TEXT DEFAULT '',
                image_plan TEXT DEFAULT '', image_path TEXT DEFAULT '',
                is_favorite INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        conn.execute("INSERT INTO meta(key,value) VALUES('schema_version','2')")
        conn.executemany("INSERT INTO domains(name, sort_order) VALUES(?,?)",
                         [("视频", 0), ("计算机编程", 1), ("未知根目录", 2)])
        conn.execute("INSERT INTO categories(parent_id,name,sort_order) VALUES(NULL,'一级A',0)")
        conn.execute("INSERT INTO categories(parent_id,name,sort_order) VALUES(1,'二级B',0)")
        conn.execute("INSERT INTO entries(category_id,name) VALUES(2,'迁移条目')")
        conn.commit()
        conn.close()

        _u = ""   # 迁移后第 1 条的 uuid（供"重开幂等"断言比对；提前初始化避免掩盖真实错误）
        db = Database(v2)  # 触发结构迁移 v2→v3→v4→v5
        try:
            assert db.get_meta("schema_version") == "5"
            assert db._has_column("domains", "project_id")
            assert len(db.list_projects()) == 5
            assert db.get_entry(1)["name"] == "迁移条目"  # 数据无损
            # v3→v4：五表建成 + 内置 10 字段预置
            for t in ("field_defs", "entry_field_values", "entry_ref_links",
                      "tags", "entry_tags"):
                assert db.conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    (t,)).fetchone()[0] == 1, t
            assert len(db.list_field_defs()) == 13  # 2026-09-16：10 内置 + 3 虚拟区块
            # v4→v5（2026-09-17，FR-93）：entries 补 uuid 列 + 回填 + 部分唯一索引
            assert db._has_column("entries", "uuid")
            _u = db.get_entry(1)["uuid"]
            assert _u and len(_u) == 32, _u                     # 老条目已被回填
            assert db.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE uuid IS NULL OR uuid=''").fetchone()[0] == 0
            assert db.conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index'"
                " AND name='idx_entries_uuid'").fetchone()[0] == 1
            # 归属分配：全命中 + 未知根目录未回答 → 兜底"未明确分类"
            st = db.assign_domains_to_projects(PROJECT_DOMAIN_MAPPING)
            assert st["matched"] == 2, st
            assert st["unmatched"] == 0 and st["fallback"] == 1
            p_map = {p["name"]: p["id"] for p in db.list_projects()}
            assert db.get_domain(1)["project_id"] == p_map["日常学习记录"]   # 视频
            assert db.get_domain(2)["project_id"] == p_map["个人经验总结"]   # 计算机编程
            assert db.get_domain(3)["project_id"] == db.get_project_by_name("未明确分类")["id"]
            # 幂等：重跑不再产生变化
            st2 = db.assign_domains_to_projects({}, None)
            assert st2 == {"matched": 0, "unmatched": 0, "fallback": 0}, st2
            print("[迁移] v2→v5 结构升级/字段预置/uuid回填/归属分配/兜底/幂等 通过")
        finally:
            db.close()
        # 幂等：重开库不再重复迁移（uuid 保持不变）
        db2 = Database(v2)
        try:
            assert db2.get_meta("schema_version") == "5"
            assert db2.get_entry(1)["uuid"] == _u                   # 重开不改 uuid
            assert len(db2.list_projects()) == 6  # 5 预置 + 未明确分类
            assert len(db2.list_field_defs()) == 13  # 2026-09-16：10 内置 + 3 虚拟（迁移后补齐）
            print("[迁移] 重开幂等（含 uuid 不变）通过")
        finally:
            db2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fields_selftest() -> None:
    """字段定义（第 1 期地基；2026-09-17 起库结构为 v5）自测：预置/读取/改名/幂等/不重复预置。"""
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_fields_")
    try:
        p = os.path.join(tmp, "fields.db")
        db = Database(p)
        try:
            assert db.get_meta("schema_version") == "5"
            defs = db.list_field_defs()
            assert len(defs) == 13, len(defs)  # 2026-09-16：10 内置 + 3 虚拟区块
            # 顺序：虚拟区块（sort_order 负）→ name → ②~⑩
            assert [d["field_key"] for d in defs] == [
                "_location", "_time", "_tags",
                "name", "intro", "origin", "features", "scenes",
                "works", "image_desc", "prompt_cn", "prompt_en", "image_plan"]
            # 13 项均为内置、不可删除（is_builtin=1）
            assert all(d["is_builtin"] == 1 for d in defs)
            # 类型：① 短文本；⑧⑨ 长文本；⑩ 链接
            assert db.get_field_def("name")["field_type"] == "text"
            assert db.get_field_def("prompt_cn")["field_type"] == "textarea"
            assert db.get_field_def("image_plan")["field_type"] == "link"
            # 改名：只改显示名，field_key 不变
            db.rename_field_def("image_desc", "⑦ 高清配图（改名测试）")
            assert db.get_field_def("image_desc")["display_name"] == "⑦ 高清配图（改名测试）"
            assert db.get_field_def("image_desc")["field_key"] == "image_desc"
            # 重开幂等：不重复预置，改名保留
            db.close()
            db = Database(p)
            assert len(db.list_field_defs()) == 13  # 2026-09-16：10 内置 + 3 虚拟区块
            assert db.get_field_def("image_desc")["display_name"] == "⑦ 高清配图（改名测试）"
            assert db.get_field_def("__no_such__") is None
            print("[字段] schema v4 预置/类型/改名/幂等 通过")
            # ---- 1-A-5 第 1 步：新增自定义字段 ----
            assert db.add_field_def("作者", "text") == "custom_1"
            assert db.add_field_def("发布日期", "date") == "custom_2"
            defs2 = db.list_field_defs()
            assert len(defs2) == 15, len(defs2)  # 2026-09-16：13 预置 + 2 自定义
            assert defs2[-1]["field_key"] == "custom_2"    # 追加到末尾
            assert defs2[-1]["is_builtin"] == 0            # 自定义字段
            assert db.get_field_def("custom_1")["field_type"] == "text"
            for bad in (("坏类型", "unknown"), ("   ", "text")):   # 非法类型 / 空名
                try:
                    db.add_field_def(*bad)
                    raise SystemExit(f"应拒绝：{bad}")
                except ValueError:
                    pass
            # 重开：自定义字段与改名均保留，且键不重复
            db.close()
            db = Database(p)
            assert len(db.list_field_defs()) == 15  # 2026-09-16：13 预置 + 2 自定义
            assert db.add_field_def("第三个", "textarea") == "custom_3"
            print("[字段] 新增自定义字段（键生成/类型校验/末尾追加/持久化）通过")
            # ---- 1-A-5 第 3 步（上）：归档/恢复 + 自定义字段排序 ----
            assert db.get_field_def("custom_1")["archived"] == 0
            db.archive_field_def("custom_1")
            assert db.get_field_def("custom_1")["archived"] == 1
            assert "custom_1" not in [d["field_key"] for d in db.list_field_defs()]
            assert "custom_1" in [d["field_key"]
                                  for d in db.list_field_defs(include_archived=True)]
            db.restore_field_def("custom_1")
            assert db.get_field_def("custom_1")["archived"] == 0
            for bad in ("intro", "name"):      # 内置字段不可归档
                try:
                    db.archive_field_def(bad)
                    raise SystemExit(f"内置字段不应可归档：{bad}")
                except ValueError:
                    pass
            # 排序：自定义字段之间（2026-09-16：UI 层已放开内置/虚拟也参与排序）
            cids = [d["id"] for d in db.list_field_defs() if not d["is_builtin"]]
            assert len(cids) == 3, cids
            assert db.swap_order("field_defs", cids, cids[1], -1) is True
            order = [d["field_key"] for d in db.list_field_defs() if not d["is_builtin"]]
            assert order[0] == "custom_2", order
            cids2 = [d["id"] for d in db.list_field_defs() if not d["is_builtin"]]
            assert db.swap_order("field_defs", cids2, cids2[0], -1) is False  # 边界不写库
            # 归档不影响已填内容
            eid2 = db.add_entry(Entry(name="归档测试条目"))
            db.set_entry_field_value(eid2, "custom_1", "保留内容")
            db.archive_field_def("custom_1")
            assert db.get_entry_field_value(eid2, "custom_1") == "保留内容"
            db.restore_field_def("custom_1")
            print("[字段] 归档/恢复/内置不可归档/排序/取值保留 通过")
            # ---- 2026-09-16（批次 14）：详情区"手动隐藏"字段（meta 存储，硬隐藏）----
            _n_defs_before = len(db.list_field_defs())   # 隐藏前后字段定义数应完全不变
            assert db.get_hidden_field_keys() == set()          # 默认无隐藏
            db.set_hidden_field_keys(["origin", "_tags", "custom_2"])
            assert db.get_hidden_field_keys() == {"origin", "_tags", "custom_2"}
            # 去空 / 去重 / 剔除 name（① 名称永不隐藏）
            db.set_hidden_field_keys(["origin", "origin", "  ", "name", " _time "])
            assert db.get_hidden_field_keys() == {"origin", "_time"}
            # 幂等：重复写入同一集合不产生变化
            db.set_hidden_field_keys(["origin", "_time"])
            assert db.get_hidden_field_keys() == {"origin", "_time"}
            # 清空
            db.set_hidden_field_keys([])
            assert db.get_hidden_field_keys() == set()
            # 隐藏**不改变**字段定义与取值（纯显示偏好，不影响数据）
            assert len(db.list_field_defs()) == _n_defs_before
            assert db.get_entry_field_value(eid2, "custom_1") == "保留内容"
            print("[字段] 手动隐藏（meta 读写/去空去重/剔除name/清空/不影响数据）通过")
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _field_values_selftest() -> None:
    """schema v4 自定义字段取值 + 统一读取层 + 搜索纳入自定义字段 自测（第 1 期 1-A-2）。"""
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_fv_")
    try:
        p = os.path.join(tmp, "fv.db")
        db = Database(p)
        try:
            did = db.add_domain("测试根目录")
            l1 = db.add_category("测试一级", domain_id=did)
            eid = db.add_entry(Entry(name="字段测试条目", category_id=l1,
                                     intro="内置介绍内容"))
            # 1. 自定义字段：写入 / 读取 / upsert（同键不新增行）
            db.set_entry_field_value(eid, "custom_author", "张三")
            assert db.get_entry_field_value(eid, "custom_author") == "张三"
            db.set_entry_field_value(eid, "custom_author", "李四")
            assert db.get_entry_field_value(eid, "custom_author") == "李四"
            assert len(db.list_entry_field_values(eid)) == 1
            # 2. 未写入返回 None（区别于写入空串）
            assert db.get_entry_field_value(eid, "custom_none") is None
            # 3. 统一读取层：内置 13 项恒在（10 内置 + 3 虚拟）+ 自定义项并入；不存在条目返回空 dict
            f = db.get_entry_fields(eid)
            assert len([k for k in f if not k.startswith("custom_")]) == 13, f
            assert f["name"] == "字段测试条目"
            assert f["intro"] == "内置介绍内容"
            assert f["custom_author"] == "李四"
            assert db.get_entry_fields(999999) == {}
            # 4. 搜索：命中自定义字段取值；内置字段搜索行为不变
            assert [x["id"] for x in db.search("李四")] == [eid]
            assert [x["id"] for x in db.search("字段测试条目")] == [eid]
            assert [x["id"] for x in db.search("内置介绍")] == [eid]
            assert db.search("不存在的词xyz") == []
            # 5. content_key 不含自定义字段（判重语义不变）
            k1 = Database.content_key(db.get_entry(eid))
            db.set_entry_field_value(eid, "custom_author", "王五")
            k2 = Database.content_key(db.get_entry(eid))
            assert k1 == k2
            # 6. 删除单个取值
            db.delete_entry_field_value(eid, "custom_author")
            assert db.get_entry_field_value(eid, "custom_author") is None
            assert "custom_author" not in db.get_entry_fields(eid)
            # 7. 级联：删除条目 → 取值行随之清除
            db.set_entry_field_value(eid, "custom_x", "X")
            assert len(db.list_entry_field_values(eid)) == 1
            db.delete_entry(eid, purge_image=False)
            assert db.conn.execute(
                "SELECT COUNT(1) FROM entry_field_values WHERE entry_id = ?",
                (eid,)).fetchone()[0] == 0
            print("[字段取值] 写入/upsert/读取层/搜索/判重不变/删除/级联 通过")
            # 8. 复制条目 / 回收站 / 删除日志快照 均携带自定义字段与标签（1-A-5 第 3 步·下 / 1-C-4）
            eid3 = db.add_entry(Entry(name="联动测试条目", category_id=l1))
            db.set_entry_field_value(eid3, "custom_link", "联动值")
            db.set_entry_tags(eid3, ["联动标签"])
            copy_id = db.copy_entry_to(eid3, l1)
            assert db.get_entry_field_value(copy_id, "custom_link") == "联动值"
            assert db.list_entry_tag_names(copy_id) == ["联动标签"]
            assert db.trash_entry(eid3) is True
            tr = db.list_trash()[0]
            assert (tr["payload"].get("custom_fields") or {}).get("custom_link") == "联动值"
            assert tr["payload"].get("tags") == ["联动标签"]
            rid = db.restore_from_trash(tr["id"])
            assert db.get_entry_field_value(rid, "custom_link") == "联动值"
            assert db.list_entry_tag_names(rid) == ["联动标签"]
            db.delete_entry(rid, purge_image=False)
            row = db.conn.execute(
                "SELECT payload FROM deletion_log WHERE kind = 'entry'"
                " ORDER BY id DESC LIMIT 1").fetchone()
            _snap = json.loads(row["payload"])
            assert _snap.get("custom_fields", {}).get("custom_link") == "联动值"
            assert _snap.get("tags") == ["联动标签"]
            print("[字段] 复制/回收站/删除日志快照 均携带自定义字段与标签 通过")
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tags_selftest() -> None:
    """schema v4 标签（1-C-1）自测：打标签/计数/且或查询/改名/合并/删除/清理/级联/搜索。"""
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_tags_")
    try:
        p = os.path.join(tmp, "tags.db")
        db = Database(p)
        try:
            did = db.add_domain("测试根")
            l1 = db.add_category("测试L1", domain_id=did)
            e1 = db.add_entry(Entry(name="条目一", category_id=l1))
            e2 = db.add_entry(Entry(name="条目二", category_id=l1))
            e3 = db.add_entry(Entry(name="条目三", category_id=l1))
            # 1. 打标签：自动建标签 + 去重
            db.set_entry_tags(e1, ["写实", "电影感", "写实"])
            db.set_entry_tags(e2, ["写实", "胶片"])
            db.set_entry_tags(e3, ["电影感"])
            assert sorted(t["name"] for t in db.list_entry_tags(e1)) == ["写实", "电影感"]
            # 2. 标签计数
            counts = {t["name"]: t["entry_count"] for t in db.list_tags_with_counts()}
            assert counts == {"写实": 2, "电影感": 2, "胶片": 1}, counts
            # 3. 按标签跨分类查询：and / or / 空
            t_xs = db.get_tag_by_name("写实")["id"]
            t_dy = db.get_tag_by_name("电影感")["id"]
            t_jp = db.get_tag_by_name("胶片")["id"]
            assert [x["name"] for x in db.list_entries_by_tags([t_xs, t_dy], "and")] == ["条目一"]
            assert {x["name"] for x in db.list_entries_by_tags([t_xs, t_dy], "or")} == \
                {"条目一", "条目二", "条目三"}
            assert [x["name"] for x in db.list_entries_by_tags([t_jp], "or")] == ["条目二"]
            assert db.list_entries_by_tags([], "and") == []
            # 4. add_tag 幂等 + 空名拒绝
            assert db.add_tag("写实") == t_xs
            try:
                db.add_tag("   ")
                raise SystemExit("空标签名应被拒绝")
            except ValueError:
                pass
            # 5. 改名 + 重名冲突
            db.rename_tag(t_jp, "菲林")
            assert db.get_tag_by_name("菲林") is not None
            try:
                db.rename_tag(t_jp, "写实")
                raise SystemExit("重名改名应被拒绝")
            except ValueError:
                pass
            # 6. 合并：菲林 → 写实
            db.merge_tags(db.get_tag_by_name("菲林")["id"], t_xs)
            assert db.get_tag_by_name("菲林") is None
            assert sorted(x["name"] for x in db.list_entries_by_tags([t_xs], "or")) == \
                ["条目一", "条目二"]
            # 7. 搜索命中标签名
            assert {x["id"] for x in db.search("写实")} == {e1, e2}
            # 8. 删除标签不影响条目
            db.delete_tag(t_dy)
            assert db.get_tag(t_dy) is None
            assert db.get_entry(e3) is not None and db.list_entry_tags(e3) == []
            # 9. 清理未使用标签
            db.add_tag("无人使用")
            assert db.purge_unused_tags() == 1
            # 10. 删除条目 → 标签关联级联清理
            db.delete_entry(e1, purge_image=False)
            assert db.conn.execute(
                "SELECT COUNT(1) FROM entry_tags WHERE entry_id = ?", (e1,)).fetchone()[0] == 0
            # 11. 无标签条目（2026-09-14 新增 list_untagged / count_untagged）
            _un = db.list_untagged()
            _un_ids = {x["id"] for x in _un}
            assert e3 in _un_ids, _un_ids                    # e3 的标签刚被删掉 → 应无标签
            assert e1 not in _un_ids, _un_ids                # e1 已被删除 → 不在条目表
            # 定义自洽：列出的每一条都确实没有任何标签
            assert all(not db.list_entry_tags(x["id"]) for x in _un)
            assert db.count_untagged() == len(_un), (db.count_untagged(), len(_un))
            db.set_entry_tags(e3, ["补一个"])
            assert e3 not in {x["id"] for x in db.list_untagged()}   # 打上标签后应移出列表
            # 12. 2026-09-15（审核 M-2）：计数失败时返回 **−1**（不再静默返回 0）。
            #     在**另一个临时库**里删掉 entry_tags 表来模拟"缺表/异常"，避免影响本用例连接。
            _tmp2 = tempfile.mkdtemp(prefix="promptsprite_untagged_")
            try:
                _db2 = Database(os.path.join(_tmp2, "t.db"))
                _db2.conn.execute("DROP TABLE entry_tags")
                _db2.conn.commit()
                assert _db2.count_untagged() == -1, "缺表时应返回 -1（表示未知）"
                _db2.close()
            finally:
                shutil.rmtree(_tmp2, ignore_errors=True)
            print("[标签] 打标签/计数/且或查询/改名/合并/删除/清理/级联/搜索 通过")
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _bulk_tags_selftest() -> None:
    """批量写标签（2026-09-14 12:30，阶段 1）自测。

    覆盖：append 并入 / 幂等（重复执行不重复写）/ replace 替换 / touch_updated 开关 /
    空输入不报错。
    """
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_bulktags_")
    try:
        db = Database(os.path.join(tmp, "t.db"))
        try:
            did = db.add_domain("根")
            l1 = db.add_category("L1", domain_id=did)
            e1 = db.add_entry(Entry(name="A", category_id=l1))
            e2 = db.add_entry(Entry(name="B", category_id=l1))
            db.set_entry_tags(e1, ["已有"])

            # 1. append：并入既有标签（跨条目共享标签只建一次 → 新1/新2 共 2 个新标签）
            r = db.set_entry_tags_bulk({e1: ["新1", "新2"], e2: ["新2"]}, mode="append")
            assert r["entries"] == 2 and r["links"] == 3 and r["tags_created"] == 2, r
            assert sorted(t["name"] for t in db.list_entry_tags(e1)) == ["已有", "新1", "新2"]

            # 2. 幂等：重复执行不再新增关联 / 不再新建标签
            r2 = db.set_entry_tags_bulk({e1: ["新1", "新2"]}, mode="append")
            assert r2["links"] == 0 and r2["tags_created"] == 0, r2

            # 3. replace：替换该条目全部标签
            r3 = db.set_entry_tags_bulk({e1: ["只此一个"]}, mode="replace")
            assert r3["links"] == 1 and [t["name"] for t in db.list_entry_tags(e1)] == ["只此一个"], r3

            # 4. touch_updated=False 不动 entries.updated_at（预置数据初始化）
            db.conn.execute("UPDATE entries SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
                            (e2,))
            db.conn.commit()
            db.set_entry_tags_bulk({e2: ["额外"]}, touch_updated=False)
            assert db.get_entry(e2)["updated_at"] == "2000-01-01 00:00:00", "不应刷新时间戳"

            # 5. touch_updated=True 刷新时间戳（默认行为）
            db.set_entry_tags_bulk({e2: ["再一个"]}, touch_updated=True)
            assert db.get_entry(e2)["updated_at"] != "2000-01-01 00:00:00", "应刷新时间戳"

            # 6. 空输入不报错
            assert db.set_entry_tags_bulk([]) == {"entries": 0, "links": 0, "tags_created": 0}
            assert db.set_entry_tags_bulk({e1: []}, mode="replace")["links"] == 0
            assert db.list_entry_tags(e1) == []

            print("[批量标签] append/replace/幂等/时间戳控制/空输入 通过")
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _hotwords_selftest() -> None:
    """热点词表（2026-09-14，阶段 4 之 4-a）自测。

    覆盖：文本解析（多分隔符/去重/去空）、并集追加的幂等性、整体替换、按名删除、
    非法项（空/超长）丢弃、超上限截断、来源网址过滤、非法 JSON 容错、与 tags 表隔离。
    """
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_hotwords_")
    try:
        p = os.path.join(tmp, "hotwords.db")
        db = Database(p)
        try:
            # 1. 初始为空
            assert db.list_hotwords() == [], db.list_hotwords()
            assert db.count_hotwords() == 0
            assert db.list_hotword_sources() == []

            # 2. 文本解析：换行 / 逗号 / 顿号 / 分号 / 竖线 / 制表符 + 去空去重 + 压缩空白
            parsed = Database.parse_hotwords_text(
                "多巴胺穿搭\n新中式,  赛博朋克、松弛感;慵懒风|美拉德\t多巴胺穿搭\n\n  \n")
            assert parsed == ["多巴胺穿搭", "新中式", "赛博朋克", "松弛感", "慵懒风", "美拉德"], parsed

            # 3. 并集追加 + 幂等（重复词不新增，计入 skipped）
            r1 = db.add_hotwords(parsed)
            assert r1["added_count"] == 6 and r1["total"] == 6, r1
            r2 = db.add_hotwords(["多巴胺穿搭", "新中式"])
            assert r2["added_count"] == 0 and r2["skipped"] == 2 and r2["total"] == 6, r2
            assert db.list_hotwords() == parsed

            # 4. 非法项（空串 / 超长）被丢弃并计入 invalid
            long_word = "超" * (Database.HOTWORD_MAX_LEN + 1)
            r3 = db.add_hotwords(["", "   ", long_word, "莫兰迪"])
            assert r3["invalid"] == 3 and r3["added_count"] == 1, r3
            assert db.count_hotwords() == 7

            # 5. 按名删除（幂等：不存在的不计数）
            assert db.remove_hotwords(["莫兰迪", "不存在的词"]) == 1
            assert db.remove_hotwords([]) == 0
            assert db.count_hotwords() == 6

            # 6. 整体替换（replace）：直接给定新表
            db.set_hotwords(["极简", "极简", "国风"])
            assert db.list_hotwords() == ["极简", "国风"], db.list_hotwords()

            # 7. 超上限截断（临时把上限改小，避免写 2000 条）
            _keep_max = Database.HOTWORD_MAX_COUNT
            try:
                Database.HOTWORD_MAX_COUNT = 2
                r4 = db.add_hotwords(["甲", "乙", "丙"])
                assert r4["added_count"] == 0 and r4["skipped"] == 3, r4
                db.set_hotwords(["甲", "乙", "丙"])
                assert db.list_hotwords() == ["甲", "乙"], db.list_hotwords()
            finally:
                Database.HOTWORD_MAX_COUNT = _keep_max

            # 8. 来源网址：仅保留 http/https、去重、按顺序
            n = db.set_hotword_sources(["https://a.com/x", "http://b.com", "  ",
                                        "ftp://c.com", "https://a.com/x", "b.com"])
            assert n == 2, n
            assert db.list_hotword_sources() == ["https://a.com/x", "http://b.com"]

            # 9. 非法 JSON 容错（不抛异常，返回空）
            db.set_meta(META_HOTWORDS, "{不是数组}")
            assert db.list_hotwords() == []
            db.set_meta(META_HOTWORDS, '{"a": 1}')
            assert db.list_hotwords() == []
            db.set_meta(META_HOTWORD_SOURCES, "not-json")
            assert db.list_hotword_sources() == []

            # 10. 与 tags 表隔离：热点词不会产生任何标签 / 条目关联
            db.set_hotwords(["多巴胺穿搭", "新中式"])
            assert db.list_tags() == [] and db.list_tags_with_counts() == []
            assert db.conn.execute("SELECT COUNT(1) FROM entry_tags").fetchone()[0] == 0

            print("[热点词] 文本解析/追加幂等/替换/删除/非法项/超上限/来源网址/容错/与tags隔离 通过")
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _list_config_selftest() -> None:
    """第 3 期列表框"数据源配置"自测：3-a 序列型 / 3-b 目录层级型 / 3-c 条目型 + 引用统计。"""
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_listsrc_")
    try:
        db = Database(os.path.join(tmp, "lc.db"))
        try:
            key = db.add_field_def("风格", "list")
            # 1. 缺省配置：默认序列型、空序列、单选、允许新建、无路径前缀
            cfg = db.list_field_config(key)
            assert cfg["source_type"] == "sequence" and cfg["items"] == []
            assert cfg["multi"] is False and cfg["allow_new"] is True
            assert cfg["path_prefix"] is False
            assert db.resolve_list_options(key) == []
            # 2. 写入序列 + 多选 + 关闭新建项
            db.set_field_config(key, {"source_type": "sequence",
                                      "items": ["写实", "电影感", "写实", "", " 电影感 "],
                                      "multi": True, "allow_new": False,
                                      "path_prefix": True})
            cfg = db.list_field_config(key)
            assert cfg["items"] == ["写实", "电影感"]          # 去重 + 去空 + 去空白
            assert cfg["multi"] is True and cfg["allow_new"] is False
            assert cfg["path_prefix"] is True
            assert [o["value"] for o in db.resolve_list_options(key)] == ["写实", "电影感"]
            assert [o["label"] for o in db.resolve_list_options(key)] == ["写实", "电影感"]
            # 3. 非法 source_type → 回退为 sequence（不报错）
            db.set_field_config(key, {"source_type": "不存在", "items": ["A"]})
            assert db.list_field_config(key)["source_type"] == "sequence"
            # 4. 预留类型：可写入并被识别（entries 为 3-c 实现，取值入口暂返回空）
            db.set_field_config(key, {"source_type": "entries"})
            assert db.list_field_config(key)["source_type"] == "entries"
            assert db.resolve_list_options(key) == []
            # 5. 配置损坏（非 JSON）→ 安全默认，不抛异常
            db.conn.execute("UPDATE field_defs SET config_json = ? WHERE field_key = ?",
                            ("{坏数据", key))
            db.conn.commit()
            assert db.list_field_config(key)["source_type"] == "sequence"
            # 6. 目录层级型（3-b）：层级 / 范围 / 路径前缀 / 稳定令牌 / 改名与删除
            pid = db.add_project("视频项目")
            did = db.add_domain("图像", project_id=pid)
            l1 = db.add_category("人像", domain_id=did)
            l2 = db.add_category("写实人像", parent_id=l1)
            did2 = db.add_domain("音频", project_id=pid)
            l1b = db.add_category("配乐", domain_id=did2)
            db.set_field_config(key, {"source_type": "tree_level", "level": "cat1",
                                      "scope": "all", "multi": True,
                                      "path_prefix": True, "allow_new": True,
                                      "items": ["不该生效"]})
            cfg = db.list_field_config(key)
            assert cfg["source_type"] == "tree_level" and cfg["level"] == "cat1"
            assert cfg["scope"] == "all" and cfg["node_refs"] == []
            assert cfg["allow_new"] is False        # 层级型不能"新建项"
            opts = db.resolve_list_options(key)
            assert [o["value"] for o in opts] == [f"cat:{l1}", f"cat:{l1b}"]   # 稳定令牌
            assert opts[0]["label"] == "视频项目 / 图像 / 人像"                 # 路径前缀
            assert all("不该生效" not in o["label"] for o in opts)              # 序列项对层级型不生效
            # 二级 + 只显示节点名
            db.set_field_config(key, {"source_type": "tree_level", "level": "cat2",
                                      "scope": "all", "path_prefix": False})
            assert [(o["value"], o["label"]) for o in db.resolve_list_options(key)] \
                == [(f"cat:{l2}", "写实人像")]
            # 范围＝指定节点（按根目录过滤）
            db.set_field_config(key, {"source_type": "tree_level", "level": "cat1",
                                      "scope": "nodes", "node_refs": [f"domain:{did2}"],
                                      "path_prefix": True})
            assert [o["value"] for o in db.resolve_list_options(key)] == [f"cat:{l1b}"]
            # 范围＝指定项目 → 其下全部（含二级）
            db.set_field_config(key, {"source_type": "tree_level", "level": "cat2",
                                      "scope": "nodes", "node_refs": [f"project:{pid}"]})
            assert [o["value"] for o in db.resolve_list_options(key)] == [f"cat:{l2}"]
            # 非法 level/scope/node_refs → 安全回退，不抛异常
            db.set_field_config(key, {"source_type": "tree_level", "level": "不存在",
                                      "scope": "xx", "node_refs": ["坏令牌", "cat:1"]})
            cfg = db.list_field_config(key)
            assert cfg["level"] == "cat1" and cfg["scope"] == "all"
            assert cfg["node_refs"] == ["cat:1"]
            # 改名 → 显示自动跟随新名（令牌不变）；删除节点 → 候选里消失（失联判定前提）
            db.set_field_config(key, {"source_type": "tree_level", "level": "cat1",
                                      "scope": "all", "path_prefix": False})
            db.rename_category(l1, "人物")
            assert db.resolve_list_options(key)[0]["label"] == "人物"
            db.delete_category(l1b)
            assert [o["value"] for o in db.resolve_list_options(key)] == [f"cat:{l1}"]
            assert db.ref_name(f"cat:{l1}") == "人物"
            assert db.ref_name(f"project:{pid}") == "视频项目"
            assert db.ref_name(f"domain:{did}") == "图像"
            assert db.ref_name("坏令牌") == ""
            # 7. 条目型（3-c）：全部 / 指定节点子树 / 令牌 / 改名跟随 / 删除失效 / 引用统计
            e1 = db.add_entry(Entry(category_id=l2, name="写实少女"))   # 二级分类下
            e2 = db.add_entry(Entry(category_id=l1, name="人像写真"))   # 一级分类下
            e3 = db.add_entry(Entry(category_id=None, name="未分类条目"))
            db.set_field_config(key, {"source_type": "entries", "scope": "all",
                                      "path_prefix": True, "multi": True})
            cfg = db.list_field_config(key)
            assert cfg["source_type"] == "entries" and cfg["allow_new"] is False
            opts = db.resolve_list_options(key)
            assert [o["value"] for o in opts] == [f"entry:{e2}", f"entry:{e1}", f"entry:{e3}"]
            assert opts[0]["label"] == "视频项目 / 图像 / 人物 / 人像写真"
            assert opts[1]["label"] == "视频项目 / 图像 / 人物 / 写实人像 / 写实少女"
            assert opts[2]["label"] == "未分类条目"          # 未分类：无分类路径
            # 范围＝指定节点（分类子树 / 根目录 / 项目）
            db.set_field_config(key, {"source_type": "entries", "scope": "nodes",
                                      "node_refs": [f"cat:{l2}"], "path_prefix": False})
            assert [(o["value"], o["label"]) for o in db.resolve_list_options(key)] \
                == [(f"entry:{e1}", "写实少女")]
            db.set_field_config(key, {"source_type": "entries", "scope": "nodes",
                                      "node_refs": [f"domain:{did}"], "path_prefix": False})
            assert [o["value"] for o in db.resolve_list_options(key)] == [f"entry:{e2}", f"entry:{e1}"]
            db.set_field_config(key, {"source_type": "entries", "scope": "nodes",
                                      "node_refs": [f"project:{pid}"], "path_prefix": False})
            assert len(db.resolve_list_options(key)) == 2
            db.set_field_config(key, {"source_type": "entries", "scope": "nodes",
                                      "node_refs": [], "path_prefix": False})
            assert db.resolve_list_options(key) == []        # 未指定节点 → 无候选
            # 引用统计（删除前提示；先写入引用再验证）
            db.set_field_config(key, {"source_type": "entries", "scope": "all"})
            db.set_entry_field_value(
                e2, key, "写实少女",
                json.dumps([{"value": f"entry:{e1}", "label": "写实少女"}], ensure_ascii=False))
            rep = db.refs_using_entry(e1)
            assert rep["total"] == 1 and rep["fields"][0]["field_key"] == key
            assert db.refs_using_entry(e3)["total"] == 0
            key2 = db.add_field_def("所属层级", "list")
            db.set_entry_field_value(
                e2, key2, "写实人像",
                json.dumps([{"value": f"cat:{l2}", "label": "人物 / 写实人像"}], ensure_ascii=False))
            assert db.refs_using_category(l2)["total"] == 1  # 自身
            assert db.refs_using_category(l1)["total"] == 1  # 子分类的引用也算
            db.set_entry_field_value(
                e3, key2, "图像",
                json.dumps([{"value": f"domain:{did}", "label": "视频项目 / 图像"}], ensure_ascii=False))
            assert db.refs_using_domain(did)["total"] == 1
            assert db.refs_using_domain(did2)["total"] == 0
            # 改名跟随 / 删除失效
            db.conn.execute("UPDATE entries SET name = ? WHERE id = ?", ("人像写真集", e2))
            db.conn.commit()
            assert db.resolve_list_options(key)[0]["label"] == "人像写真集"
            db.delete_entry(e1)
            assert [o["value"] for o in db.resolve_list_options(key)] == [f"entry:{e2}", f"entry:{e3}"]
            print("[列表框] 默认配置/序列写入去重/多选/新建项/路径前缀/非法回退/损坏容错/"
                  "目录层级（层级·范围·路径前缀·令牌稳定·改名跟随·删除失效）/"
                  "条目（全部·指定节点·令牌·改名跟随·删除失效·引用统计） 通过")
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _images_selftest() -> None:
    """第 2 期 2-a/2-d：条目图集自测。

    2-a：增/列/计数/命名/排序/转本地/删除/级联；
    2-d：联动（条目快照 / 复制条目 / 回收站恢复 / 彻底删除释放文件 / 子树深拷贝）。
    """
    global data_dir
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_img_")
    _orig_data_dir = data_dir
    try:
        tmpdata = os.path.join(tmp, "data")
        os.makedirs(os.path.join(tmpdata, IMAGES_DIR_NAME), exist_ok=True)
        data_dir = lambda: tmpdata   # 自测期间 data/ 指向临时目录（不碰真实数据）
        p = os.path.join(tmp, "img.db")
        db = Database(p)
        try:
            did = db.add_domain("测试根")
            l1 = db.add_category("测试L1", domain_id=did)
            eid = db.add_entry(Entry(name="图集条目", category_id=l1))
            assert db.list_entry_images(eid) == []
            # 1. 新增：本地 + 外链
            i1 = db.add_entry_image(eid, "local", path="images/entry_x_0.png")
            i2 = db.add_entry_image(eid, "url", source_url="https://a.com/1.jpg")
            assert db.count_entry_images(eid) == 2
            imgs = db.list_entry_images(eid)
            assert [r["id"] for r in imgs] == [i1, i2]
            assert imgs[1]["kind"] == "url"
            assert imgs[1]["source_url"] == "https://a.com/1.jpg"
            # 2. 参数校验：非法类型 / 外链缺网址
            for bad in (("bad", ""), ("url", "")):
                try:
                    db.add_entry_image(eid, bad[0], source_url=bad[1])
                    raise SystemExit("应拒绝非法参数")
                except ValueError:
                    pass
            # 3. 图集文件名递增且不冲突（entry_{id}_{n}.{ext}）
            r1 = db.gallery_file_rel(eid, ".png")
            r2 = db.gallery_file_rel(eid, "jpg")
            assert r1.endswith(".png") and f"entry_{eid}_" in r1
            assert r2.endswith(".jpg") and f"entry_{eid}_" in r2
            # 4. 排序：上移生效、越界不写
            assert db.swap_entry_image_order(eid, i2, -1) is True
            assert [r["id"] for r in db.list_entry_images(eid)] == [i2, i1]
            assert db.swap_entry_image_order(eid, i2, -1) is False
            # 5. 外链 → 本地（保留 source_url）
            db.set_entry_image_local(i2, "images/entry_x_1.jpg")
            row = next(r for r in db.list_entry_images(eid) if r["id"] == i2)
            assert row["kind"] == "local" and row["path"] == "images/entry_x_1.jpg"
            assert row["source_url"] == "https://a.com/1.jpg"
            # 6. 删除一张附加图
            db.remove_entry_image(i1, purge_file=False)
            assert db.count_entry_images(eid) == 1
            # 7. 删除条目 → 图集级联清理
            db.delete_entry(eid, purge_image=False)
            assert db.conn.execute(
                "SELECT COUNT(1) FROM entry_images WHERE entry_id = ?",
                (eid,)).fetchone()[0] == 0

            # ---- 2-d：联动（真实文件） ----
            def _mk(rel: str) -> str:
                full = os.path.join(tmpdata, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as f:
                    f.write(b"\x89PNG\r\n\x1a\n")
                return rel

            e2 = db.add_entry(Entry(name="联动条目", category_id=l1))
            cover = _mk(os.path.join(IMAGES_DIR_NAME, f"entry_{e2}.png"))
            db.set_entry_image(e2, cover)
            g1 = _mk(db.gallery_file_rel(e2, ".png"))
            db.add_entry_image(e2, "local", path=g1)
            db.add_entry_image(e2, "url", source_url="https://a.com/z.jpg")
            db.set_entry_tags(e2, ["写实"])
            # 8. 快照含图集（回收站/删除日志共用）
            assert len(db.entry_snapshot(db.get_entry(e2)).get("images") or []) == 2
            # 9. 复制条目：图集行复制 + 本地图复制为新文件（互不牵连）
            cid2 = db.add_category("测试L2", domain_id=did)
            new_id = db.copy_entry_to(e2, cid2)
            n_imgs = db.list_entry_images(new_id)
            assert len(n_imgs) == 2
            assert n_imgs[0]["kind"] == "local" and n_imgs[0]["path"] != g1
            assert os.path.isfile(os.path.join(tmpdata, n_imgs[0]["path"]))
            assert n_imgs[1]["kind"] == "url" and n_imgs[1]["source_url"] == "https://a.com/z.jpg"
            assert db.get_entry(new_id)["image_path"] != cover
            assert db.list_entry_tag_names(new_id) == ["写实"]
            # 10. 回收站：进站 → 恢复 → 图集仍在（文件在回收站期间被保留）
            assert db.trash_entry(e2) is True
            _tid = db.conn.execute("SELECT id FROM trash ORDER BY id DESC LIMIT 1").fetchone()[0]
            r_id = db.restore_from_trash(_tid)
            assert r_id and db.count_entry_images(r_id) == 2
            # 11. 彻底删除：本地图集文件被释放
            _fp = [r["path"] for r in db.list_entry_images(r_id) if r["kind"] == "local"][0]
            db.delete_entry(r_id, purge_image=True)
            assert db.count_entry_images(r_id) == 0
            assert not os.path.isfile(os.path.join(tmpdata, _fp))
            # 12. 子树深拷贝：标签与图集一并复制（2-d 补齐此前的遗漏）
            new_cat = db._copy_subtree_tx(cid2, None, "副本分类")
            db.conn.commit()
            copied = db.list_entries(new_cat)
            assert copied and db.count_entry_images(copied[0]["id"]) == 2
            assert db.list_entry_tag_names(copied[0]["id"]) == ["写实"]
            print("[图集] 增/列/计数/命名/排序/转本地/删除/级联/复制/快照/回收站/彻底删除/子树 通过")
        finally:
            db.close()
    finally:
        data_dir = _orig_data_dir
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
    _migrate_selftest()
    _fields_selftest()
    _field_values_selftest()
    _tags_selftest()
    _bulk_tags_selftest()  # 2026-09-14 12:30（阶段 1）：批量写标签
    _hotwords_selftest()   # 2026-09-14（阶段 4 之 4-a）：热点词表读写
    _list_config_selftest()
    _images_selftest()
