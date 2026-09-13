# -*- coding: utf-8 -*-
"""
models.py - PromptSprite 数据模型定义
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

2026-08-18（P2-5）：删除冗余的 Domain / Category 数据类（数据库层 v2 后统一返回 dict），
仅保留实际使用的 Entry。根目录/分类数据通过 database.list_domains()/list_categories() 以 dict 访问。
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Entry:
    """提示词条目（9 个标准字段 + 附加属性）"""
    id: Optional[int] = None
    category_id: Optional[int] = None          # NULL = 未分类
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
