# -*- coding: utf-8 -*-
"""
config.py - PromptSprite 全局配置常量
创建日期：2026-08-12（阶段一：项目初始化与数据地基）
"""
import os
import sys

# ---------- 应用基础信息 ----------
# 2026-09-14：**界面显示名改为 PromptSprite2**（用户要求：只改"主窗口名称标签 + 关于窗口名称标签"
# 两处，**不改**窗口标题/托盘/导出页/打包产物名/文档链接等其它名称）。
# 注：本常量当前仅被"设置 → 关于"首行引用（`settings_dialog.py`），故改动仅影响该处显示名。
APP_NAME = "PromptSprite2"
# 2026-09-12：V1.9.0 —— 界面与体验修订（详情区字段浮动提示窗口重构：修复闪烁/左移不遮正文/
# 可滚动与滚动同步/光标行加深；新增"💬 打开/关闭浮动提示窗口"总开关；🗂 按钮两态配色与提示；
# "条目名称一览"浮层字号与高度调整），数据格式与库结构完全不变。
# 2026-09-14：**V2.0.0** —— 第 1~4 期 + T2 联网抓取全部落地：
#   · **库结构升到 schema v4**（新增 field_defs / entry_field_values / entry_ref_links /
#     tags / entry_tags / entry_images 六表，全部为新增表、存量数据零迁移）；
#   · **JSON 升到 v5**（四层分类路径 + field_defs 随包 + custom_fields/tags/图集）；
#   · 新增标签系统与标签页面、图片图集、列表框三类数据源、离线结构化向导、联网抓取（向导"从网址获取"）。
#   因"数据格式与库结构"均已变化，版本号由 1.9.0 升为 2.0.0（避免与 09-12 版构建产物同名）。
# 2026-09-14：**V2.1.0** —— 智能标签功能（阶段 0/0.5/1/4）+ 设置分页 + 性能优化：
#   · 阶段 0：出厂标签词表（通用骨架 + 领域维度包 + 领域判定表，见 app/tagger_dict.py；
#     **2026-09-18 起出厂词表升级为 809 个标签 / 21 个领域包**，此前 V2.1.0 为 135 个 / 3 个，
#     当日更早一版为 699 个 / 19 个）；
#   · 阶段 0.5：词表管理（标签页面第 4 个页签「词表」+ 设置内入口；导出/导入/恢复出厂；权威数据
#     存 meta 键 tag_dict；加一个"大类"= 加一个领域包，无需改代码）；
#   · 阶段 1：打标引擎（app/tagger_engine.py，三源优先级 + 单主领域装载 + 每维 1 个 + 合计 ≤3）
#     + 预置标签已写入内置库（工具 tag_builtin.py；2026-09-18 按新出厂词表重打后：593 个标签 / 6574 条关联）；
#   · 阶段 4：热点词表（标签页面「热点词」页签 + 设置内入口；逐条添加 / 文本导入 / 来源网址抓取）；
#   · 设置对话框按 4 页分页重组（界面 / 数据与备份 / 标签与词表 / 关于）；
#   · 性能优化：标签云自动换行（原 pack 平铺不换行导致只显示 3 个）、标签页与条目区渲染上限
#     （默认前 50，"显示更多"步长 50；「全部显示」超 500 条先二次确认）、标签一次性预取消除 N+1。
#   本次**未改库结构**（仍 schema v4）与导入导出格式（仍 JSON v5），亦未新增第三方依赖。
# 2026-09-17 16:55（FR-93 / FR-94 收口）：**版本升为 V2.2.0** —— 库结构升 schema v5
#   （`entries` 增 `uuid` 稳定 ID）、导入导出格式升 JSON v6（`uuid` + `locations`，多位置往返保真）、
#   变更包按 uuid 精确同步、Excel 增 `uuid` 列；全部使用标准库 `uuid` 实现，**未新增第三方依赖**。
#   升版依据：《项目需求规格和开发计划书 V9》§9.3 的升版规则（"库结构变更 **或** 导入导出格式变更"即
#   应升版），本次**同时命中这两条**；已就此事向用户提出建议报告，用户于 2026-09-17 明确批准升为 2.2.0。
APP_VERSION = "2.2.0"

# ---------- 数据库 / 目录 ----------
DB_FILE_NAME = "prompts.db"
BACKUP_DIR_NAME = "backup"
IMAGES_DIR_NAME = "images"

# 首次建库时预置的根目录（专业领域）
PRESET_DOMAINS = ["计算机编程", "视频", "图像", "音频", "文学", "学术", "专业报告", "视觉风格分类"]

# ---------- 详情字段显示模式（2026-08-18 新增；2026-09-13 1-B 修复为真正生效）----------
# "自动"模式下：查看以下根目录时详情区 ②~⑦ 全部显示；其余根目录只显示 ② 介绍。
# 2026-09-13（1-B）：移除已不存在的"视觉风格分类"根目录（当前库中无此根目录）。
DETAIL_FULL_FIELDS_DOMAINS = ("视频", "图像")
# "精简"时需隐藏的字段键（③溯源 ④核心特征 ⑤应用场景 ⑥代表作 ⑦代表高清配图；② 介绍保留）
DETAIL_HIDDEN_KEYS = ("origin", "features", "scenes", "works", "image_desc")

# ---------- 内置手册（首次启动自动导入） ----------
# 版本号变更会触发启动时"非破坏性合并"导入：分类按名复用、条目按名 upsert，
# 不再清空任何现有数据（2026-08-18，P0-2 修复）
# 内置手册归入"视觉风格分类"根目录（视频/图像保留为空根目录）
BUILTIN_MANUAL_VERSION = "007-2026-08-18"
BUILTIN_MANUAL_RESOURCE = "resources/builtin_manual.md"
META_BUILTIN_IMPORTED = "builtin_manual_version"

# ---------- 内置完整数据库（打包携带最新数据，2026-08-18 第023条新增） ----------
# 打包时由开发态 data/prompts.db 生成 app/resources/builtin_prompts.db 内嵌进 EXE；
# 打包态首次运行若无 data/prompts.db，则从该资源复制完整最新库（含全部根目录/分类/条目）。
BUILTIN_DB_RESOURCE = "resources/builtin_prompts.db"

# ---------- 全局热键 ----------
GLOBAL_HOTKEY = "ctrl+shift+p"
# 2026-09-17（需求 S-2）：热键**可配置**——用户设置在 meta 键 `settings_hotkey`；
#   未设置 / 非法时回退 `GLOBAL_HOTKEY`（默认值不变，老用户行为完全一致）。
#   规范化与校验见 app/hotkey.py 的 `normalize_hotkey()`。
META_HOTKEY = "settings_hotkey"

# ---------- 用户设置（meta 键，2026-08-18 新增"设置"入口）----------
META_REMEMBER_SIZE = "settings_remember_size"   # "1"/"0"：是否记住窗口大小
META_WINDOW_SIZE = "settings_window_size"       # "WxH"：上次窗口大小
META_VIEW_MODE = "settings_view_mode"           # "card"/"list"：默认视图模式
# 2026-09-16 10:20（批次 11-7，用户要求 3）：条目区排序方式
#   "updated"（默认，最后修改时间倒序）/ "created"（新增时间倒序）/ "name"（名称升序）。
#   仅影响"分类视图"按分类列条目时的排序；搜索/常用/无类等其他视图顺序不变。
META_ENTRY_SORT = "settings_entry_sort"
META_DETAIL_MODE = "settings_detail_mode"       # "auto"/"full"/"compact"：详情字段显示策略
# 2026-09-16（批次 14，用户要求"字段管理中各区块可逐个隐藏/显示"）：
#   详情区"手动隐藏"的字段键列表（逗号分隔，如 "origin,_tags,custom_2"）。
#   - 语义为**硬隐藏**：无论"详情字段显示策略"是精简/自动/全部，被隐藏项都不显示；
#     固定头部"⏵ 显示全部字段"按钮**不会**恢复它，只能在「字段管理」里改回"显示"。
#   - 纯本机显示偏好：存 meta 表、**不随 JSON 包/变更包导出**（换机后重新设置即可）；
#     不改 field_defs 表结构、不改导入导出格式，被隐藏字段的内容仍在库中照常导出与搜索。
#   - 仅作用于详情区（浏览/编辑态）；"新增条目"表单始终显示全部字段。
#   - ① 名称（field_key="name"）不允许隐藏（本键即使写入 "name" 也会在读取/渲染时忽略）。
META_DETAIL_HIDDEN_FIELDS = "settings_detail_hidden_fields"
# 2026-09-13（1-C-3b）：标签页面的"呈现方式"（记住上次选择）："cloud"=标签云、"list"=列表
META_TAG_VIEW = "settings_tag_view"
# 2026-09-13（1-C-4b）：是否"在条目处显示标签"（默认关；开启后条目区不自动加宽，超长截断+悬浮查看）
META_SHOW_TAGS_IN_LIST = "settings_show_tags_in_list"
# 2026-09-14 11:15（阶段 4"热点词"）：热点词表与其"来源网址"列表，均为 JSON 数组字符串，存 meta 表。
# 热点词表是"自动打标"⑧ 维度的候选词库（初始为空，可手动添加 / 文本导入 / 从来源网址抓取），
# 与 tags 表无关：tags 是"已被使用过的标签"，热点词是"待匹配的词条"。
META_HOTWORDS = "tag_hotwords"                   # JSON 数组：["多巴胺穿搭", "新中式", ...]
META_HOTWORD_SOURCES = "tag_hotword_sources"     # JSON 数组：来源网址（默认空，由用户填写）
# 2026-09-14 12:00（阶段 0/0.5）：标签词表（通用骨架 + 领域维度包 + 领域判定表），
# JSON 对象字符串，存 meta 表；**权威数据**（可编辑、可扩展），出厂种子见 app/tagger_dict.py。
META_TAG_DICT = "tag_dict"
# 2026-09-14（阶段 3）：录入时"自动推荐标签"的 T2 开关——⑧中文/⑨英文输入停止 800ms 后
# 自动重算推荐（默认关）。T1（「✨ 推荐标签」按钮 + ①名称框回车/失焦触发）无需开关、始终可用。
META_AUTO_TAG_SUGGEST = "settings_auto_tag_suggest"
# 2026-09-15 13:50（用户要求，批次 4）：界面外观（字体/字号/颜色）配置——JSON 对象字符串，存 meta 表。
# 结构：{"组名": {"family": 字体族或 null, "size": 字号或 null, "fg": 文字色或 null, "bg": 背景色或 null}}
# **null 语义 = "不改（沿用代码里的原值）"** → 用户未设置时界面与现在完全一致。
# 组名与允许项见 app/ui_appearance.py 的 GROUPS（6 组：浮层 / 条目区名称 / 字段浮窗 / 中英文提示词 / ①名称框）。
META_UI_APPEARANCE = "ui_appearance"
# 2026-09-16 10:20（批次 11-6，用户确认问题 2）：**标签推荐策略与顺序**——JSON 对象字符串，存 meta 表。
# 结构：{"order": ["hotword","domain_dict","field","ext"], "enabled": {来源: true/false}}
# 默认顺序（用户建议）：热点词 → 领域+词典 → 取词 → 扩展；「显式标签」恒最高优先、不列入其中。
# 来源标识与默认值见 app/tagger_engine.py 的 SOURCE_* / POLICY_SOURCES / DEFAULT_POLICY。
META_TAG_POLICY = "tag_suggest_policy"
# 2026-09-16（批次 12-2，用户要求 2）：**取词是否用于"批量打标 / 离线打标"**（"1"/"0"，默认 "0"＝不用）。
#   「批量智能自动打标」对话框与「设置 → 标签与词表」页**共用本键（同一开关）**。
#   取值 "0" 时批量/离线打标只走"显式 → 领域 → 词典"，与内置库既有标签口径一致；
#   取值 "1" 时批量/离线打标也启用「字段取词」（会写入取词标签，请先预演确认）。
META_FALLBACK_BATCH = "settings_field_fallback_batch"
# 2026-09-16（批次 12-3，用户要求 1）：**智能自动取词词库**（JSON 对象：{词: 词条}）——
#   存放"取词"过程中未命中词表/热点词的新词，累计频次；达阈值后自动升为热点词，
#   达"词表阈值"则提示用户审核后加入词表标签。见 app/auto_words.py（纯数据层）。
META_AUTO_WORDS = "tag_auto_words"
# 智能自动取词的**设置**（JSON 对象）：{"enabled","include_t2","hot_th","tag_th","cap"}
META_AUTO_WORDS_CFG = "tag_auto_words_cfg"
DETAIL_MODE_AUTO = "auto"       # 按根目录自动显隐（默认）
DETAIL_MODE_FULL = "full"       # 始终全部显示
DETAIL_MODE_COMPACT = "compact" # 始终精简（隐藏 ③-⑦）

# ---------- 自动备份 ----------
# 2026-09-08（V1.7.0 备份策略）：默认保留 30 份；按天去重（同日只留最后一份）；
# 清理仅针对标准命名 prompts_YYYY-MM-DD_*.db，手动快照（snapshot_* 等）永不清理。
BACKUP_KEEP_COUNT = 30

# ---------- 项目类别（四级分类最高层级，2026-08-29 施工新增） ----------
# 预置项目类别（最高层级）
PROJECT_PRESETS = ["日常学习记录", "网上资源收集", "个人梳理资源", "本人创作作品", "个人经验总结"]
# 迁移时"未命中且用户未回答"的兜底项目类别（惰性创建）
PROJECT_FALLBACK = "未明确分类"
# 根目录名（库中实际名称）→ 项目类别名；迁移前弹出供人工核对（施工方案决策 2）
PROJECT_DOMAIN_MAPPING = {
    "日常学习记录": ["视频", "图像", "音频", "文学", "学术", "专业报告"],
    "网上资源收集": ["海外AI绘画案例库", "AI绘画精选案例", "GPT Image 提示词库", "AI生图提示词大全"],
    "个人梳理资源": ["视觉风格分类"],
    "本人创作作品": ["yifree学习与作品"],
    "个人经验总结": ["计算机编程"],
}

# ---------- 每日变更包（原"增量备份"，2026-08-29 新增 / 2026-09-08 V1.7.0 更名+计数命名）----------
INCR_DIR_NAME = "incremental"          # data/backup/incremental/
INCR_KEEP_DAYS = 30                    # 增量备份文件按日保留天数（用户确认）
INCR_FILE_PREFIX = "变更包"            # 文件名前缀（原"增量"）：变更包_电脑代号_日期_addN_delM.json
META_COMPUTER_CODE = "settings_computer_code"   # 电脑代号
META_INCR_LAST_SYNC = "incr_last_sync"          # 增量备份游标
META_INCR_KEEP_DAYS = "settings_incr_keep_days" # 增量备份保留天数（用户设置，默认 INCR_KEEP_DAYS）
META_BACKUP_KEEP = "settings_backup_keep"       # 自动全量备份保留份数（用户设置，默认 BACKUP_KEEP_COUNT；2026-09-08 V1.7.0）
META_CHANGE_PACK_SNAPSHOT = "settings_change_pack_snapshot"  # 变更包是否携带被删快照（"1"/"0"，默认关；支持⑤逆向恢复，2026-09-08 V1.7.0）
# 旧前缀（"增量"）仅用于清理历史遗留文件，避免遗留文件不被清理
INCR_LEGACY_PREFIX = "增量"
META_MIGRATE_MAPPING_VER = "migrate_mapping_version"  # 迁移映射表版本
META_MIGRATE_WIZARD_DISMISSED = "migrate_wizard_dismissed"  # 迁移向导是否已取消过（避免每次启动打扰）

# 项目根目录：config.py 位于 app/ 下，取其上级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- 公共时间工具（2026-09-17，审核报告 R-4）----------
# 背景：`_now()`（"%Y-%m-%d %H:%M:%S"）原先在 database.py / tagger.py / auto_words.py /
#   incremental_backup.py **各写一份**，另有 tagger_batch.py 的 `_now_stamp()`
#   （"%Y-%m-%d_%H-%M-%S"，用于文件名）。现统一到本模块，各处 `_now` 改为**薄封装**，
#   口径/格式以 database.py 的既有实现为准（不改任何已写出数据的格式）。
def now_str() -> str:
    """当前时间字符串，格式 `YYYY-MM-DD HH:MM:SS`（created_at / updated_at 等字段用）。"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_stamp() -> str:
    """当前时间戳字符串，格式 `YYYY-MM-DD_HH-MM-SS`（用于备份 / 批次文件名）。"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def is_frozen() -> bool:
    """是否处于 PyInstaller 打包态"""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> str:
    """只读资源基目录：打包态为 PyInstaller 解压目录 sys._MEIPASS"""
    if is_frozen():
        return sys._MEIPASS
    return PROJECT_ROOT


def data_dir() -> str:
    """可写数据目录：打包态为 exe 同目录下 data/，开发态为项目根下 data/"""
    if is_frozen():
        return os.path.join(os.path.dirname(sys.executable), "data")
    return os.path.join(PROJECT_ROOT, "data")


def builtin_manual_path() -> str:
    """内置手册路径：打包态在解压目录 resources/ 下，开发态在 app/resources/ 下。

    修复（2026-08-12）：打包态曾误拼为 resources/resources/… 导致 EXE 首次导入失败，
    现直接用 BUILTIN_MANUAL_RESOURCE（已含 resources/ 前缀）。
    """
    if is_frozen():
        return os.path.join(resource_dir(), BUILTIN_MANUAL_RESOURCE)
    return os.path.join(PROJECT_ROOT, "app", BUILTIN_MANUAL_RESOURCE)


def builtin_db_path() -> str:
    """内置完整数据库路径（2026-08-18 第023条新增）：
    打包态在解压目录 resources/ 下（EXE 内嵌的最新完整库）；
    开发态直接返回主库路径（无需额外资源，本方法仅打包态首次建库使用）。
    """
    if is_frozen():
        return os.path.join(resource_dir(), BUILTIN_DB_RESOURCE)
    return os.path.join(data_dir(), DB_FILE_NAME)
