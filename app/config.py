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
APP_VERSION = "2.0.0"

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

# ---------- 用户设置（meta 键，2026-08-18 新增"设置"入口）----------
META_REMEMBER_SIZE = "settings_remember_size"   # "1"/"0"：是否记住窗口大小
META_WINDOW_SIZE = "settings_window_size"       # "WxH"：上次窗口大小
META_VIEW_MODE = "settings_view_mode"           # "card"/"list"：默认视图模式
META_DETAIL_MODE = "settings_detail_mode"       # "auto"/"full"/"compact"：详情字段显示策略
# 2026-09-13（1-C-3b）：标签页面的"呈现方式"（记住上次选择）："cloud"=标签云、"list"=列表
META_TAG_VIEW = "settings_tag_view"
# 2026-09-13（1-C-4b）：是否"在条目处显示标签"（默认关；开启后条目区不自动加宽，超长截断+悬浮查看）
META_SHOW_TAGS_IN_LIST = "settings_show_tags_in_list"
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
