# -*- coding: utf-8 -*-
"""
models.py - PromptSprite 数据模型定义
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

2026-08-18（P2-5）：删除冗余的 Domain / Category 数据类（数据库层 v2 后统一返回 dict），
仅保留实际使用的 Entry。根目录/分类数据通过 database.list_domains()/list_categories() 以 dict 访问。
"""
import uuid
from dataclasses import dataclass
from typing import Optional


def new_entry_uuid() -> str:
    """生成条目**稳定 ID**（2026-09-17，FR-93 / schema v5）。

    用 `uuid4().hex`（32 位小写十六进制、无连字符）：跨机器唯一，且不暴露生成时间与机器信息。
    用途：① 老库迁移时回填；② 新增条目自动分配；③ "复制到（独立副本）"分配**新** ID；
         ④ 导入 v6 包时若同一包内 uuid 重复，为重复项重新分配。
    """
    return uuid.uuid4().hex


@dataclass
class Entry:
    """提示词条目（9 个标准字段 + 附加属性）"""
    id: Optional[int] = None
    category_id: Optional[int] = None          # NULL = 未分类
    # 2026-09-17（FR-93 / schema v5）：跨机器稳定的条目身份（uuid4 hex）。
    #   为空表示"该条尚未分配"（正常流程下不会为空：新增自动生成、迁移会回填）。
    uuid: str = ""
    name: str = ""                             # ① 风格名称
    intro: str = ""                            # ② 介绍
    origin: str = ""                           # ③ 溯源
    features: str = ""                         # ④ 核心特征
    scenes: str = ""                           # ⑤ 应用场景
    works: str = ""                            # ⑥ 代表作
    image_desc: str = ""                       # ⑦ 代表高清配图（文字描述）
    prompt_cn: str = ""                        # ⑧ 中文版提示词
    prompt_en: str = ""                        # ⑨ 英文版提示词（2026-08-18：编号由⑧调整为⑨）
    image_plan: str = ""                       # ⑩ 图像获取方案（2026-08-18：编号由⑨调整为⑩）
    image_path: str = ""                       # 关联本地图片路径（相对 data/）
    is_favorite: int = 0                       # 收藏标记 0/1
    created_at: str = ""
    updated_at: str = ""
