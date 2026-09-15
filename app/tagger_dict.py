# -*- coding: utf-8 -*-
"""
tagger_dict.py - 标签词表「出厂种子」（阶段 0 产出，2026-09-14）

本模块只提供**只读的默认词表**（"出厂种子"），供两处使用：
  1. 首次启动时初始化（写入数据库 meta 表键 `tag_dict`）；
  2. 用户在「词表」页签点「恢复出厂词表」时回退到本默认值。

**权威数据在 meta 表**（用户可编辑、可扩展）；本模块**永不参与运行时匹配的读取**，
只作为"默认值/兜底值"。词表的读写、校验、导入导出见 `app/tagger.py`。

结构（与《智能标签功能可行性研究报告 v3》§4.3 一致）：
  {
    "version": 1,
    "updated_at": "...",
    "universal": {维度名: {标签名: [匹配词, ...]}},          # 通用骨架（所有领域共用）
    "domains":   {领域名: {维度名: {标签名: [匹配词...]}}},  # 领域维度包（按单主领域装载）
    "domain_map":{领域名: [分类/根目录名关键词, ...]},        # 分类名 → 领域 的判定表
  }

匹配词写法：
  - 英文一律小写（匹配时文本也已小写）；按"词边界"匹配，避免 "man" 命中 "human"；
  - 中文按"子串"匹配；
  - 以 `re:` 前缀书写时按正则匹配（例如 "re:2\\.39:1"）。

**加一个大类 = 加一个键**：如后期要加「学术」，在 `domains` 里加一个 "学术" 键
（并在 `universal.领域` 与 `domain_map` 各加一项）即可，无需改代码。

自测：python -m app.tagger_dict
"""
import copy

TAG_DICT_VERSION = 1

_BUILTIN = {
    "version": TAG_DICT_VERSION,
    "updated_at": "2026-09-14 12:00:00",

    # ---------------- 通用骨架（所有条目共用；可编辑、可扩展） ----------------
    "universal": {
        "领域": {
            "视觉": ["图像", "视频", "绘画", "生图", "海报", "摄影", "插画", "渲染",
                    "视觉", "ai绘画", "midjourney", "gpt image", "动画", "电影"],
            "文学": ["小说", "散文", "诗歌", "剧本", "文案", "故事", "文学", "叙事", "文风"],
            "音频": ["音频", "音乐", "配音", "音效", "播客", "歌曲", "旋律", "编曲", "tts"],
            "编程": ["代码", "编程", "脚本", "算法", "程序", "函数", "接口", "调试",
                    "python", "javascript", "sql"],
            "学术": ["学术", "论文", "研究", "综述", "文献", "实验", "论证", "课题"],
            "教育": ["教育", "教学", "教案", "课程", "知识点", "练习题", "课件", "课堂"],
            "母婴": ["母婴", "育儿", "宝宝", "婴儿", "孕期", "辅食", "亲子", "幼儿"],
            "成长": ["成长", "自我提升", "习惯养成", "复盘", "心态", "时间管理"],
            "商业": ["商业", "营销", "品牌", "广告", "运营", "电商", "增长", "获客"],
            "生活": ["生活", "家居", "美食", "旅行", "穿搭", "健康", "运动", "理财"],
        },
        "用途": {
            "素材": ["素材", "图库", "参考图", "素材库", "collect"],
            "教程": ["教程", "教学", "入门", "指南", "步骤", "how to", "tutorial"],
            "模板": ["模板", "套用", "可替换", "template", "preset"],
            "案例": ["案例", "示例", "样例", "展示", "case", "demo"],
            "工具": ["工具", "插件", "软件", "平台", "workflow", "pipeline"],
            "参考": ["参考", "速查", "手册", "清单", "对照表", "cheatsheet"],
            "灵感": ["灵感", "创意", "脑洞", "启发", "inspiration"],
            "报告": ["报告", "总结", "复盘", "分析", "结论"],
        },
    },

    # ---------------- 领域维度包（按"单主领域"装载） ----------------
    "domains": {
        # ===== 视觉包（原 v2 的 6 个视觉维度原样搬入）=====
        "视觉": {
            "题材主体": {
                "人像": ["portrait", "headshot", "selfie", "人物", "肖像", "少女", "女性", "男性"],
                "风景": ["landscape", "mountain", "forest", "meadow", "sunset", "风景", "山水", "草原"],
                "产品": ["product", "packshot", "e-commerce", "产品", "商品", "电商", "静物"],
                "建筑": ["architect", "building", "interior", "建筑", "室内", "空间"],
                "美食": ["food", "cuisine", "dish", "美食", "料理", "饮品"],
                "动物": ["animal", "cat", "dog", "bird", "动物"],
                "文字排版": ["typograph", "lettering", "海报字体", "排版", "文字设计"],
                "抽象图形": ["abstract", "geometric", "pattern", "抽象", "几何", "图案"],
                "二次元角色": ["anime", "manga", "chibi", "角色设计", "character design", "二次元"],
                "场景": ["scene", "setting", "场景", "环境"],
            },
            "风格流派": {
                "写实摄影": ["photorealistic", "realistic", "hyperreal", "写实", "真实感"],
                "插画": ["illustration", "illustrat", "drawing", "插画", "手绘"],
                "3D渲染": ["3d", "octane", "blender", "c4d", "render", "isometric", "渲染", "建模", "等距"],
                "水彩": ["watercolo", "gouache", "水彩"],
                "油画": ["oil painting", "impasto", "油画"],
                "动漫": ["anime", "manga", "ghibli", "动漫", "吉卜力", "手办"],
                "像素风": ["pixel art", "voxel", "像素"],
                "国风": ["chinese style", "guofeng", "ink painting", "国风", "中国风", "水墨", "汉服"],
                "粘土手作": ["clay", "figurine", "plush", "knitted", "粘土", "毛绒", "针织"],
                "矢量扁平": ["flat design", "vector", "minimal icon", "扁平", "矢量", "线性图标"],
                "拼贴": ["collage", "拼贴", "混剪"],
            },
            "光影氛围": {
                "电影感": ["cinematic", "cinema", "film still", "movie", "电影", "院线", "宽幅", "遮幅"],
                "极简": ["minimalis", "minimal", "clean", "极简", "留白"],
                "夜景": ["night", "midnight", "夜景", "夜晚", "霓虹"],
                "梦幻": ["dreamy", "ethereal", "fantasy", "fairy", "梦幻", "唯美", "仙"],
                "高对比": ["high contrast", "hard shadow", "chiaroscuro", "高对比", "强反差", "硬阴影"],
                "柔光": ["soft light", "diffused", "soft shadow", "柔光", "漫射", "柔阴影"],
                "冷调": ["cool tone", "blue tone", "冷调", "冷色"],
                "暖调": ["warm tone", "golden hour", "暖调", "暖色", "金色时光"],
                "逆光": ["backlit", "backlight", "rim light", "逆光", "轮廓光"],
                "自然光": ["natural light", "daylight", "自然光", "日光"],
            },
            "媒介工艺": {
                "胶片": ["film grain", "35mm", "kodak", "portra", "analog", "胶片", "颗粒"],
                "黑白": ["black and white", "monochrome", "b&w", "黑白", "单色"],
                "长曝光": ["long exposure", "light trail", "长曝光", "光轨"],
                "微距": ["macro", "微距"],
                "双重曝光": ["double exposure", "双重曝光"],
                "手机摄影": ["smartphone", "iphone", "phone camera", "ccd", "手机拍摄"],
                "广角": ["wide angle", "广角"],
                "鱼眼": ["fisheye", "鱼眼"],
            },
            "地域年代": {
                "日系": ["japanese", "japan", "日系", "日式"],
                "韩系": ["korean", "k-pop", "韩系", "韩式"],
                "欧美": ["western", "european", "american", "欧美", "美式"],
                "复古": ["retro", "vintage", "nostalg", "80s", "90s", "复古", "怀旧"],
                "未来科幻": ["sci-fi", "futuristic", "space", "spaceship", "科幻", "未来", "太空"],
                "赛博朋克": ["cyberpunk", "neon", "cyber", "赛博", "霓虹"],
                "Y2K": ["y2k", "千禧"],
                "古风": ["ancient chinese", "古风", "古代", "朝代"],
            },
            "技术操作": {
                "反推提示词": ["reverse engineer", "反推提示词", "反推图像"],
                "换脸换装": ["replace the appearance", "face swap", "outfit", "换脸", "换装", "变装"],
                "局部重绘": ["inpaint", "outpaint", "局部重绘", "扩图"],
                "角色一致性": ["consistent character", "consistency", "reference image", "一致性", "参考图"],
                "对比展示": ["before and after", "comparison", "side by side", "对比", "前后"],
                "多图参考": ["multi reference", "多图参考", "多张参考"],
                "图生图": ["image to image", "img2img", "图生图"],
            },
        },

        # ===== 文学包 =====
        "文学": {
            "体裁": {
                "短篇小说": ["短篇", "short story"],
                "长篇小说": ["长篇", "连载", "novel"],
                "散文": ["散文", "随笔", "essay"],
                "诗歌": ["诗歌", "诗句", "poem", "poetry"],
                "剧本": ["剧本", "脚本", "screenplay", "分镜"],
                "文案": ["文案", "广告语", "slogan", "copywriting"],
                "议论文": ["议论文", "论述", "论点", "论证"],
                "说明文": ["说明文", "说明书", "解释"],
                "童话": ["童话", "绘本故事", "fairy tale"],
            },
            "题材": {
                "都市情感": ["都市", "情感", "婚恋", "职场"],
                "悬疑推理": ["悬疑", "推理", "侦探", "凶案"],
                "科幻": ["科幻", "星际", "人工智能题材", "末世"],
                "奇幻": ["奇幻", "魔法", "玄幻", "修仙"],
                "历史": ["历史", "年代", "朝代", "民国"],
                "职场": ["职场", "商战", "创业"],
                "青春校园": ["青春", "校园", "学生", "初恋"],
                "家庭": ["家庭", "亲情", "亲子关系", "婆媳"],
            },
            "叙事视角": {
                "第一人称": ["第一人称", "first person", "我"],
                "第三人称": ["第三人称", "third person", "他"],
                "全知视角": ["全知", "上帝视角", "omniscient"],
                "书信体": ["书信", "信件", "letter"],
                "日记体": ["日记", "diary", "日志体"],
            },
            "情绪基调": {
                "治愈": ["治愈", "温暖", "healing", "暖心"],
                "温情": ["温情", "感人", "亲情向"],
                "压抑": ["压抑", "抑郁", "沉重", "绝望"],
                "热血": ["热血", "燃", "励志", "奋斗"],
                "幽默": ["幽默", "搞笑", "轻松", "喜剧"],
                "忧伤": ["忧伤", "悲", "遗憾", "伤感"],
                "悬疑紧张": ["紧张", "惊悚", "悬疑感"],
            },
            "年代地域": {
                "古代背景": ["古代", "古风", "朝代", "宫廷"],
                "民国": ["民国", "旧上海", "战乱"],
                "现代都市": ["现代", "都市", "当下"],
                "未来世界": ["未来", "赛博", "星际时代"],
                "异世界": ["异世界", "架空", "大陆"],
            },
            "篇幅结构": {
                "剧本大纲": ["剧本大纲", "大纲", "故事大纲"],
                "分镜脚本": ["分镜", "脚本", "镜头表"],
                "章节梗概": ["章节", "梗概", "章纲"],
                "人物小传": ["人物小传", "人物设定", "角色设定"],
                "世界观设定": ["世界观", "设定集", "背景设定"],
            },
        },

        # ===== 编程包 =====
        "编程": {
            "技术栈": {
                "Python": ["python", "pip", "pandas", "numpy", "customtkinter", "tkinter"],
                "JavaScript": ["javascript", "js", "typescript", "node"],
                "SQL": ["sql", "sqlite", "查询语句", "建表"],
                "命令行": ["cmd", "powershell", "bash", "命令行", "终端"],
                "前端": ["前端", "html", "css", "react", "vue"],
                "后端": ["后端", "接口", "api", "服务端", "rest"],
                "数据库": ["数据库", "表结构", "索引", "事务"],
                "网络请求": ["http", "urllib", "requests", "抓取", "爬虫"],
            },
            "任务类型": {
                "修复缺陷": ["修复", "bug", "报错", "异常", "traceback", "崩溃"],
                "功能开发": ["新增功能", "开发", "实现", "需求"],
                "重构": ["重构", "优化结构", "整理代码"],
                "性能优化": ["性能", "提速", "耗时", "卡顿", "优化"],
                "单元测试": ["单元测试", "自测", "断言", "assert", "pytest"],
                "部署发布": ["打包", "发布", "部署", "pyinstaller", "安装包"],
                "数据清洗": ["清洗", "去重", "规范化", "整理数据"],
            },
            "框架库": {
                "界面框架": ["customtkinter", "tkinter", "qt", "界面框架", "gui"],
                "Web框架": ["flask", "django", "fastapi", "web框架"],
                "数据处理": ["pandas", "numpy", "openpyxl", "excel处理"],
                "爬虫": ["爬虫", "scrapy", "beautifulsoup", "selenium"],
                "绘图库": ["matplotlib", "pillow", "绘图", "图表库"],
            },
            "难度": {
                "入门": ["入门", "基础", "简单示例", "hello world"],
                "基础": ["基础用法", "常用操作", "基本"],
                "进阶": ["进阶", "高级用法", "原理", "源码分析"],
                "专家": ["专家", "底层", "内核", "深度优化"],
            },
        },
    },

    # ---------------- 分类名 → 领域 判定表（根目录 / 项目类别 / 分类名 子串匹配） ----------------
    "domain_map": {
        "视觉": ["图像", "视频", "绘画", "生图", "海报", "摄影", "插画", "视觉", "动画", "电影",
                "ai绘画", "midjourney", "gpt image", "提示词", "案例库", "design", "poster"],
        "文学": ["文学", "小说", "散文", "诗歌", "剧本", "文案", "故事"],
        "音频": ["音频", "音乐", "配音", "音效", "播客"],
        "编程": ["编程", "计算机", "代码", "开发", "软件", "program"],
        "学术": ["学术", "论文", "研究", "文献", "专业报告"],
        "教育": ["教育", "教学", "课程", "教案", "培训"],
        "母婴": ["母婴", "育儿", "亲子", "宝宝"],
        "成长": ["成长", "自我提升", "复盘"],
        "商业": ["商业", "营销", "品牌", "电商", "广告", "运营"],
        "生活": ["生活", "家居", "美食", "旅行", "穿搭", "健康", "运动"],
    },
}


def builtin_dict() -> dict:
    """返回出厂词表的**深拷贝**（调用方可任意修改，不会污染本模块的默认值）。"""
    return copy.deepcopy(_BUILTIN)


def builtin_domains() -> list:
    """出厂词表里的领域名清单（按定义顺序）。"""
    return list(_BUILTIN.get("domains", {}).keys())


def builtin_stats() -> dict:
    """出厂词表的规模统计（供自测与文档引用）。"""
    uni_dims = len(_BUILTIN["universal"])
    uni_tags = sum(len(v) for v in _BUILTIN["universal"].values())
    dom_stats = {}
    dom_tags_total = 0
    for dom, dims in _BUILTIN["domains"].items():
        n = sum(len(v) for v in dims.values())
        dom_stats[dom] = {"维度": len(dims), "标签": n}
        dom_tags_total += n
    return {"通用骨架维度": uni_dims, "通用骨架标签": uni_tags,
            "领域包": dom_stats, "领域包标签合计": dom_tags_total,
            "标签总数": uni_tags + dom_tags_total}


def _selftest() -> None:
    """出厂词表结构自测（不依赖界面与数据库）。"""
    d = builtin_dict()
    assert d["version"] == TAG_DICT_VERSION
    assert isinstance(d["universal"], dict) and d["universal"], "通用骨架不能为空"
    assert isinstance(d["domains"], dict) and d["domains"], "领域包不能为空"
    assert isinstance(d["domain_map"], dict) and d["domain_map"], "领域判定表不能为空"
    # 三类键对齐：domain_map 覆盖全部领域包
    assert set(d["domain_map"].keys()) >= set(d["domains"].keys()), \
        (set(d["domain_map"]), set(d["domains"]))
    # 结构合法性：维度→标签→[匹配词]
    bad = []
    for dom, dims in d["domains"].items():
        for dim, labels in dims.items():
            assert isinstance(labels, dict) and labels, "领域包维度不能为空：%s/%s" % (dom, dim)
            for tag, words in labels.items():
                if not (isinstance(words, list) and words and all(
                        isinstance(w, str) and w.strip() for w in words)):
                    bad.append("%s/%s/%s" % (dom, dim, tag))
    for dim, labels in d["universal"].items():
        for tag, words in labels.items():
            if not (isinstance(words, list) and words and all(
                    isinstance(w, str) and w.strip() for w in words)):
                bad.append("universal/%s/%s" % (dim, tag))
    assert not bad, "匹配词非法：%s" % bad
    # 深度拷贝隔离：改副本不影响原件
    d2 = builtin_dict()
    d2["domains"]["视觉"]["题材主体"]["人像"].append("XXX")
    assert "XXX" not in builtin_dict()["domains"]["视觉"]["题材主体"]["人像"]
    # 非 ASCII 匹配词（中文）保留原样；英文匹配词一律小写
    for dims in list(d["domains"].values()) + [d["universal"]]:
        for labels in dims.values():
            for words in labels.values():
                for w in words:
                    if w.isascii() and not w.startswith("re:"):
                        assert w == w.lower(), "英文匹配词应小写：%s" % w
    print("[词表种子] 结构/键对齐/匹配词/深拷贝隔离 通过；规模：%s" % builtin_stats())


if __name__ == "__main__":
    _selftest()
