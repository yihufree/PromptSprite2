# -*- coding: utf-8 -*-
"""
tagger_engine.py - 打标引擎（阶段 1，2026-09-14）

**纯函数、无数据库依赖、零第三方依赖**（只用标准库 re），供三处复用：
  1. 阶段 1「预置标签」：离线脚本 `tag_builtin.py` 给主库批量打标；
  2. 阶段 2「批量智能打标」：软件内批量功能（同一引擎，结果 100% 一致）；
  3. 阶段 3「录入时自动推荐标签」：单条推荐。

判定链路（对应《可行性研究报告 v3》§3.2 / §5）：
    ① 显式标签直取（文本里的"标签：A、B"）—— 质量最高，恒排最前
    ② 单主领域判定：分类名/根目录/项目类别映射 → 文本关键词判定 → 兜底
    ③ 领域包词典打分：只装载「通用骨架 + 单主领域包」（兜底时只装通用骨架）
    ④ **各来源"尽力推荐" → 按「策略顺序」拼候选队列 → 统一取前 N（默认 3）**
       2026-09-18（用户确认）：配额不再由来源之间互相扣减——每个被启用的来源都按自己的口径
       产出候选（自身上限 = 每条例目标签数），由**顺序**决定谁进最终结果；
       「显式标签」恒最高优先且不受策略开关影响。

取词（字段取词，2026-09-18 用户确认方案 A + 兜底规则后重写）：
    · 命中已知词（热点词 / 词表标签名 / 词表匹配词，含**两词短语**与**保守词形变体**）
      ⇒ 采用其**规范标签名**（跨领域、免阈值，不受"单主领域装载"与 MIN_SCORE 限制）；
    · 未命中但过**新词闸门**的 token ⇒ 以**原文**作为新标签；
    · **尽力而为**：正常走**严格闸门**（中文 2~6 字、英文两词短语），"有就推荐、没有就算了"（0~3 个）；
    · **兜底规则**（用户第 3 轮确认）：**当「热点词」与「领域+词表」都推荐为 0 时**，
      自动切到**兜底闸门**（中文 2~12 字、英文 1~3 词且 ≤32 字符）重算一次并追加队尾
      ⇒ **保证字段取词至少推荐 1 个**（来源标注 `取词（兜底）`）。

字段权重（实测依据：主库 ⑨英文 89.4%、①名称/⑦配图描述 100%、②介绍 87.9%、
④核心特征 79.5%、⑧中文 10.6%、⑤⑥ 为 0%）：
    name 4 · image_desc 3 · features 2 · intro 2 · prompt_cn 1 · prompt_en 1

匹配词写法（与 app/tagger_dict.py 一致）：
    英文 → 词边界匹配（前后不能是字母/数字，故 "2.39:1"、"b&w" 也能匹配）；
    中文 → 子串匹配；`re:` 前缀 → 正则匹配。

自测：python -m app.tagger_engine
"""
import re

# 参与打分的字段及权重（顺序即优先级）
FIELD_WEIGHTS = (("name", 4.0), ("image_desc", 3.0), ("features", 2.0),
                 ("intro", 2.0), ("prompt_cn", 1.0), ("prompt_en", 1.0))

# 显式标签：形如 "标签：A、B、C" / "关键词:" / "tags: a, b"
# 注意：捕获到"下一个标签关键词"或行尾即止（否则 "tags: a 关键词: b" 会连在一起）
_LABEL_KW = r"(?:标签词|标签|关键词|关键字|key\s*words?|keywords?|tags?)"
_EXPLICIT_RE = re.compile(
    _LABEL_KW + r"\s*[:：]\s*([^\n\r]{1,200}?)(?=\s*" + _LABEL_KW + r"\s*[:：]|[\n\r]|$)",
    re.I | re.M)
# 行内 #标签（要求前面是行首或分隔符，避免命中 URL 片段）
_HASH_RE = re.compile(r"(?:^|[\s，,、;；|（(])\s*#([^\s#，,、;；|）)]{1,24})")

# 单个标签最大长度（与热点词一致）
TAG_MAX_LEN = 24
# 每条例目的标签数上限（用户确认：3）
MAX_TAGS = 3
# 词典候选的最低得分（低于此分视为弱信号，丢弃；实测第 4 名平均 1.8、第 3 名 2.9）
MIN_SCORE = 2.0
# 文本关键词判定领域时的最低命中次数（宁缺勿滥）
DOMAIN_MIN_HITS = 2
# 2026-09-16 10:20（批次 11-4，用户反馈 1①）：取词标签的**长度上限**（按字符类型区分）——
#   实测出厂词表：**中文标签最长仅 5 字**（"二次元角色"/"角色一致性"），英文最长 10 字（"JavaScript"）；
#   中文 >8 字基本是"整句/整段"（如"必须保留产品的外观造型" 11 字，原实现会整句成标签）⇒ 丢弃；
#   英文放宽到 16，避免误杀 photography（11）/ illustration（12）这类正常单词。
_FALLBACK_MAX_CJK = 8
_FALLBACK_MAX_ASCII = 16
# 2026-09-16 10:20（批次 11-4/11-5，用户反馈 1②）：**取词不再独占名额**——
#   实测（真实库 300 条）原实现 99.3% 被取词占满 3 个名额、领域/词典 0 次命中；
#   现由「策略顺序（取词排在领域/词典之后）+ fallback_cap 名额上限」共同保证留出名额。

# ---- 2026-09-15 18:15（批次 8-C）：①名称取词的切分规则与停用词 ----
# 切分符：空白 + 常见中英标点（`.`/`·`/`—`/括号/引号 等）
_TITLE_SPLIT_RE = re.compile(r"[\s,，、;；:：|/\\·—–\-+()（）\[\]【】{}<>《》\"'“”‘’!！?？.。…~*#@&]+")
# 中英/数字边界（如 "AI绘画" → "AI" + "绘画"）
_TITLE_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])|(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])")
# 停用词（保守：只去纯功能词/英文虚词；长度 <2 的由规则过滤）
_TITLE_STOPWORDS = frozenset((
    "a", "an", "the", "of", "and", "or", "to", "for", "with", "in", "on", "at",
    "is", "are", "be", "by", "as", "it", "this", "that",
    "的", "了", "和", "与", "及", "或", "是", "在", "用", "把", "被", "之",
))
# ---- 2026-09-18 14:00（用户确认"取词允许新词成标签 + 严格质量闸门"）：提示词样板词/参数词黑名单 ----
#   实测（主库 2556 条）取词与"新词候选"里高频混入这些词：8K / HDR / Style / Aspect Ratio /
#   Negative Prompt / masterpiece / 1.4 / 50mm —— 它们是**模板样板与生成参数**，不是内容词，
#   作为标签毫无区分度（且会污染「智能自动取词词库」的新词沉淀）。
#   本名单**只作用于"字段取词 / 新词候选"这条路径**（`_clean_token`），
#   **不影响词典打分路径**——即 `8k` 若被写进某个标签的匹配词，词典仍然照常命中。
_PROMPT_BOILERPLATE = frozenset((
    # ① 质量 / 空洞赞美词
    "masterpiece", "best quality", "top quality", "high quality", "highest quality",
    "ultra detailed", "highly detailed", "extremely detailed", "super detailed",
    "ultra realistic", "hyper realistic", "super realistic",
    "ultra high resolution", "high resolution", "high res", "ultra hd", "uhd", "hd", "fhd",
    "sharp focus", "high detail", "fine detail", "intricate detail", "intricate details",
    "award winning", "trending on artstation", "professional photography",
    "8k", "4k", "2k", "1080p", "720p", "hdr", "raw",
    # ② 提示词模板结构词 / 生成参数
    "prompt", "positive prompt", "negative prompt", "negative prompts", "negative",
    "aspect ratio", "ar", "cfg", "cfg scale", "steps", "sampling steps", "seed",
    "sampler", "denoise", "denoising strength", "clip skip", "style", "styles",
    "parameters", "settings", "default", "argument", "arguments", "value", "values",
    # ③ 中文样板词（2026-09-18 实测量化后补充：这些是"界面字段名/模板话术"，不是内容词）
    "超高清", "高清", "极致细节", "杰作", "最佳质量", "高质量", "最高质量",
    "负面提示词", "反向提示词", "负面词", "提示词", "关键词", "画面比例", "图像比例",
    "中文", "英文", "中文版", "英文版", "主题", "主题为", "生成一张", "生成一幅",
    "生成图片", "生成图像", "画面", "内容", "文字", "标题", "名称", "品牌", "参数", "设置",
))
# 2026-09-18 14:00（实测量化后补充）：**两词英文新词**的"模板前后缀"闸门——
#   实测量化发现，`BRAND NAME` / `headline text` / `render goal` / `85mm lens` / `Left section`
#   这类"占位符 / 字段名 / 参数名"会以"两词短语"的形式通过闸门进入标签，故再收一道：
#   首词命中 `_TEMPLATE_LEAD` 或末词命中 `_TEMPLATE_HEAD` ⇒ 不作为新词。
_TEMPLATE_LEAD = frozenset((
    "create", "generate", "render", "master", "global", "basic", "technical",
    "main", "final", "left", "right", "top", "bottom", "center", "middle",
    "upper", "lower", "first", "second", "third",
))
_TEMPLATE_HEAD = frozenset((
    "name", "text", "title", "slogan", "label", "caption", "annotation", "note",
    "prompt", "keyword", "keywords", "tag", "tags", "setting", "settings",
    "profile", "goal", "option", "options", "style", "styles", "color", "colour",
    "word", "words", "lens", "ratio", "size", "version", "type", "format",
    "code", "url", "link", "value", "values", "mode",
    # 2026-09-18（实测量化后再补）：以介词结尾的两词 token 基本是"半句话碎片"（emerging from）
    "from", "with", "into", "onto", "over", "under", "between", "during", "about",
    "for", "and", "or", "of", "to", "in", "on", "at", "by", "as",
))
# 2026-09-18 14:00（用户确认方案 A）：中文"新词"的**功能词前缀**黑名单——
#   以这些词开头的中文 token 基本是"短语/半句"（如"一个女孩""必须保留…"），不作为新词成标签。
_NEW_WORD_CJK_PREFIX = (
    "的", "了", "和", "与", "是", "在", "把", "被", "这", "那", "我", "你", "他", "她", "它",
    "一个", "一种", "一些", "这个", "那个", "非常", "可以", "需要", "必须", "不要", "不能",
    "尽量", "如果", "因为", "所以", "然后", "以及", "或者", "但是", "而且", "为了", "通过",
)
# 中文新词的最大字数（词表内中文标签最长 5 字，故放到 6；再长基本是短语/整句）
_NEW_WORD_MAX_CJK = 6
# ---- 2026-09-18 16:00（用户确认"兜底规则"）：字段取词的**兜底闸门** ----
#   用户口径：「尽力而为，有就推荐、没有就不推荐；**但当热点词与领域+词表都为 0 时，字段取词必须推荐 1~3 个**」。
#   故正常路径仍走上面的**严格闸门**；只有在"另两个来源都为空"时才切到本兜底闸门**重算一次**：
#     英文：1~3 个单词、每词 ≥3 字母、总长 ≤32；中文：2~12 字（仍排除样板词/占位符/纯数字色值）。
#   这样"严格度不降低"，只在"否则整条没有任何标签"时才放宽，兑现"必须推荐"。
_RELAX_MAX_ASCII = 32
_RELAX_MAX_CJK = 12
# ---- 2026-09-15 19:30（批次 10，用户确认 Q2-b）：提示词专用切分符 ----
# 提示词是"逗号分隔的短语列表"，只按逗号/顿号/分号切，保留短语内部空格（如 "film grain"）
# ---- 2026-09-16 10:20（批次 11-4，用户反馈 1）：补齐句末标点 / 冒号 / 引号 / 括号 / 换行 ----
#   原实现只切"逗号/顿号/分号" ⇒ 中文长句（以"。"结尾）整段成了一个"标签"。
_PROMPT_SPLIT_RE = re.compile(r"[,，、;；。！？!?：:\n\r\t“”\"'‘’（）()【】\[\]{}《》<>|/\\]+")
# 2026-09-16 10:20（批次 11-4）：取词"质量闸门"——token 内部仍残留这些符号即丢弃
#   （如 `"age": "20s`、`"Huh?"`、`9:16`、`（reference` 之类 JSON / 标点碎片）
# 2026-09-17（新问题 B 修复，用户反馈"标签不应带标点，尤其是标点前后都有文字的情况"）：
#   补充半角连字符 `-` 与下划线 `_`——使 `K-pop`、`film_grain`、`证件照/职业照` 这类
#   "标点前后都有文字"的 token 一并被拦下（原先只拦了全角破折号 `—`/`–`，半角 `-` 漏网）。
_TAG_NOISE_RE = re.compile(r"[。！？!?，、；;：:，“”\"'‘’（）()【】\[\]{}《》<>|/\\@#$%^&*+=~`…·—–\-_]")

# 2026-09-17（新问题 A/B 修复）：标签"边缘标点"集合——清洗时先剥掉**首尾**这些符号，
#   使 `[世界]` → `世界`、`000000"` → `000000`（再由后续闸门判定是否合格）。
#   **内置/显式标签与字段取词两条路径共用本常量，保证口径一致**。
#   注：本集合**与原 `_clean_token` 的内联 strip 集合逐字符一致**（只是抽成常量），
#     刻意**不扩大**——否则 `top++` 会被"洗净"成 `top` 而逃过质量闸门（既有自测要求丢弃）。
_EDGE_PUNCT = " \t\u3000·-—–:：。.、,，;；!！?？\"'“”‘’()（）[]【】{}《》<>"

# 2026-09-17（新问题 A 修复）：**十六进制色值**判定——提示词里大量出现
#   `#000000` / `#fff` / `#1A2332` 这类颜色码（常带尾部标点，如 `#1A2332.`），
#   原先会被当作标签写入。规则：长度恰为 3/4/6/8（真实色值长度）且全部为十六进制字符、
#   且**至少含 1 个数字**（避免误杀 `dead` / `face` / `beef` 这类正常英文词）。
_HEX_COLOR_RE = re.compile(r"(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


def _strip_edge_punct(s: str) -> str:
    """剥掉字符串首尾的标点/符号/空白（2026-09-17 新问题 A/B 修复，两条路径共用）。"""
    return str(s or "").strip(_EDGE_PUNCT)


def _is_hex_color(s: str) -> bool:
    """是否为十六进制色值（2026-09-17 新问题 A 修复）：`000000` / `1A2332` / `fff1` 之类。"""
    if not s or not any(c.isdigit() for c in s):
        return False
    return bool(_HEX_COLOR_RE.fullmatch(s))


def _is_hash_hex(s: str) -> bool:
    """`#xxx` 通道专用（2026-09-17 新问题 A 修复）：`#fff` / `#000000` / `#1A2332` 等色值。

    与 `_is_hex_color` 的唯一差别：**不要求含数字**——因为 `#fff`、`#FFFFFF` 是常见的
    3/6 位色值简写（全为字母），也必须排除，否则它们会以 `fff` / `FFFFFF` 之名成为标签。
    """
    if not s:
        return False
    return bool(_HEX_COLOR_RE.fullmatch(s))


def is_noise_tag(name: str) -> bool:
    """判断一个**已清洗过的标签名**是否为噪声（2026-09-17 新增，对外公开、口径唯一）。

    命中任一即视为噪声（返回 True）：
      · 空 / 空白 ；
      · 超长（> TAG_MAX_LEN=24）；
      · 纯数字（`12`、`234`、`1145`、`000000`）；
      · 十六进制色值（`1A2332`、`fff1`）；
      · 内部仍含标点/引号/括号/符号（`古风/汉服`、`K-pop偶像`、`aa"bb`）。

    **注意**：本函数**不判"边缘标点"**——边缘标点属清洗阶段职责（`[世界]` → `世界`），
    清洗后仍不合格才算噪声；这一点与 `_clean_token()` / `extract_explicit_tags()._push()`
    的判定顺序一致。
    **用途**：打标引擎的"质量闸门"与仓库根目录的 `clean_junk_tags.py`（存量垃圾标签清理）
    共用本函数，保证"今后不再产生"与"清理已有"两处口径完全一致。
    """
    s = str(name or "")
    if not s or len(s) > TAG_MAX_LEN:
        return True
    if s.isdigit() or _is_hex_color(s):
        return True
    return bool(_TAG_NOISE_RE.search(s))


def clean_tag_name(name: str) -> str:
    """把"原始标签名"清洗为合法标签名；不合格返回空串（2026-09-17 新增，对外公开）。

    顺序：去空白/换行 → 剥离首尾标点 → `is_noise_tag()` 闸门。
    例：`[世界]` → `世界`；`12` → `""`；`古风/汉服` → `""`；`电影感` → `电影感`。

    **用途**：`clean_junk_tags.py`（存量垃圾标签清理）据此区分两类：
      · 清洗结果为空 ⇒ **真噪声**（纯数字 / 色值 / 内部含标点），应删除；
      · 清洗结果非空但与原值不同 ⇒ **边缘标点可洗净**（如 `[世界]`），宜重命名而非删除。
    """
    s = _strip_edge_punct(" ".join(str(name or "").split()))
    return "" if is_noise_tag(s) else s


_SEP_RE = re.compile(r"[-_]+")


def _normalize_separators(s: str) -> str:
    """把半角连字符 / 下划线归一为空格（2026-09-18 取词召回优化）。

    使 `film-grain` / `film_grain` 与词表里的 `film grain` **同形可比**——
    原实现因 `_TAG_NOISE_RE` 含 `-`/`_` 把这类 token **整词丢弃** ⇒ 明明词表里有也取不到。
    只处理**文本切分后的 token**（不影响 `is_noise_tag` 与存量垃圾标签清理口径）。
    """
    if "-" not in s and "_" not in s:
        return s
    return " ".join(_SEP_RE.sub(" ", s).split())


def _clean_token(raw: str, max_ascii: int = _FALLBACK_MAX_ASCII,
                 max_cjk: int = _FALLBACK_MAX_CJK) -> str:
    """2026-09-16 10:20（批次 11-4，用户反馈 1）：取词 token 的"清洗 + 质量闸门"。

    逐步处理：去首尾空白 → **连字符/下划线归一为空格**（2026-09-18）→ 剥离边缘标点 →
    长度 ≥2 → `is_noise_tag()` 质量闸门（丢纯数字 / 十六进制色值 / 超 24 字 / 内部含标点引号括号等碎片）
    → 按字符类型限长 → 英文去首尾停用词 → 丢弃停用词 / **提示词样板词**（2026-09-18）。
    返回可用的 token（不合格返回空串）。

    `max_ascii` / `max_cjk`（2026-09-18 16:00，用户确认"兜底规则"）：长度上限**可注入**，
    供「兜底取词」放宽（默认值＝原口径，行为不变）。
    """
    s = " ".join(str(raw or "").split())
    s = _normalize_separators(s)         # 2026-09-18：`film-grain`/`film_grain` → `film grain`
    s = _strip_edge_punct(s)             # 2026-09-17：边缘标点集合抽为常量，与显式标签路径共用
    if not s or len(s) < 2:              # 取词特有：单字符不作为标签（显式标签不受此限）
        return ""
    if is_noise_tag(s):                  # 2026-09-17：纯数字 / 色值 / 内部含标点 / 超长——口径唯一
        return ""
    if len(s) > (int(max_ascii) if s.isascii() else int(max_cjk)):
        return ""                        # 超长 ⇒ 基本是"整句/整段"，不是标签
    if s.isascii():
        s = _strip_stopwords(s)
        if not s:
            return ""
    if s.lower() in _TITLE_STOPWORDS:
        return ""
    if s.lower() in _PROMPT_BOILERPLATE:  # 2026-09-18：8k / hdr / style / aspect ratio 等样板词
        return ""
    return s


_EN_PAT_CACHE = {}


def _en_pattern(word: str):
    """英文匹配词的已编译正则（词边界：前后不得是字母/数字）。"""
    pat = _EN_PAT_CACHE.get(word)
    if pat is None:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")
        _EN_PAT_CACHE[word] = pat
    return pat


def _count(word: str, text: str) -> int:
    """匹配词在文本中的出现次数（文本须已小写）。"""
    if not word or not text:
        return 0
    if word.startswith("re:"):
        try:
            return len(re.findall(word[3:], text, re.I))
        except re.error:
            return 0
    if word.isascii():
        return len(_en_pattern(word).findall(text))
    return text.count(word)


def _norm_texts(texts) -> dict:
    """把条目字段整理成 {字段: 小写文本}（只保留有权重的字段）。"""
    out = {}
    for field, _w in FIELD_WEIGHTS:
        v = (texts or {}).get(field)
        out[field] = str(v or "").lower()
    return out


def _strip_stopwords(token: str) -> str:
    """2026-09-15 19:30（批次 10）：去掉英文 token 首尾的停用词。

    如 "a girl" → "girl"、"the film grain" → "film grain"、"a boy and a girl" → "boy and a girl"。
    只处理纯英文 token（含空格的短语），中文 token 原样返回。
    """
    if not token or not token.isascii():
        return token
    parts = token.split()
    if not parts:
        return ""
    while parts and parts[0].lower() in _TITLE_STOPWORDS:
        parts.pop(0)
    while parts and parts[-1].lower() in _TITLE_STOPWORDS:
        parts.pop()
    return " ".join(parts)


# ---------------------------------------------------------------------- #
# ① 显式标签直取
# ---------------------------------------------------------------------- #
def extract_explicit_tags(texts, limit: int = MAX_TAGS) -> list:
    """从条目文本中提取"来源已写好的标签"（按出现顺序、去重、最多 limit 个）。

    识别：`标签：A、B` / `关键词:` / `tags: a, b` / 行内 `#A #B`。
    合法性（2026-09-17 起与"字段取词"同口径）：非空、长度 ≤ 24、不含换行、
    **剥离首尾标点后不得为纯数字、不得是十六进制色值、内部不得再含标点符号**
    （即 `12` / `000000` / `#1A2332` / `[世界]`(会洗净为 `世界`) / `古风/汉服` / `K-pop偶像` 之类噪声被丢弃）。
    """
    found, seen = [], set()

    def _push(raw, from_hash: bool = False):
        # 2026-09-17 12:55（新问题 A/B 修复，用户反馈）：显式标签**也必须过"质量闸门"**。
        #   原实现只判"非空 + 长度≤24"，导致下面三类噪声被当成标签写入库：
        #     ① 纯数字（`12` / `234` / `1145`）与十六进制色值（`000000` / `1A2332`）；
        #     ② 行内 `#xxx` 命中的颜色码（`#000000` / `#fff`）与序号（`#12`）；
        #     ③ 内部含标点的碎片（`[世界]` / `古风/汉服` / `K-pop偶像`）。
        #   现与"字段取词"路径统一口径：先剥离首尾标点 → 丢纯数字/色值 → 内部含标点则丢。
        #   **刻意保留的差异**：① 不设最小长度（保留来源明确声明的 `A`/`B` 单字符标签）；
        #                     ② 不做英文停用词剥离（来源声明优先于启发式）。
        s = _strip_edge_punct(" ".join(str(raw or "").split()))
        if not s:
            return
        if is_noise_tag(s):              # 2026-09-17：纯数字 / 色值 / 内部含标点 / 超长（口径唯一）
            return
        # 2026-09-17：`#xxx` 通道额外排除"全字母色值简写"（`#fff` / `#FFFFFF`）
        if from_hash and _is_hash_hex(s):
            return
        if s not in seen:
            seen.add(s)
            found.append(s)

    for field, _w in FIELD_WEIGHTS:
        raw = str((texts or {}).get(field) or "")
        if not raw:
            continue
        for m in _EXPLICIT_RE.finditer(raw):
            for part in re.split(r"[、,，;；|]+", m.group(1)):
                _push(part)
        for m in _HASH_RE.finditer(raw):
            _push(m.group(1), from_hash=True)   # 2026-09-17：标记来源，启用色值排除
        if len(found) >= limit:
            break
    return found[:limit]


# ---------------------------------------------------------------------- #
# ② 单主领域判定
# ---------------------------------------------------------------------- #
def detect_domain(texts, dict_data, ctx_names=None) -> tuple:
    """判定单主领域，返回 (领域名 or None, 判定依据)。

    ① 分类名/根目录/项目类别映射（最可靠）→ ② 文本关键词判定 → ③ 兜底(None)。
    """
    domain_map = dict_data.get("domain_map") or {}
    hay = " ".join(str(x or "").lower() for x in (ctx_names or []) if str(x or "").strip())
    if hay:
        best, best_hits = None, 0
        for dom, words in domain_map.items():
            hits = sum(1 for w in (words or []) if w and _count(str(w).lower(), hay))
            if hits > best_hits:
                best, best_hits = dom, hits
        if best:
            return best, "分类名映射"

    texts_n = _norm_texts(texts)
    known = set((dict_data.get("domains") or {}).keys())
    best, best_score = None, 0.0
    for dom, words in ((dict_data.get("universal") or {}).get("领域") or {}).items():
        if dom not in known:                     # 只判"有领域包"的领域，避免判到无包的领域
            continue
        score = sum(_count(w, texts_n["name"]) * 4.0 + _count(w, texts_n["intro"]) * 2.0
                    + _count(w, texts_n["features"]) * 2.0 + _count(w, texts_n["prompt_cn"])
                    + _count(w, texts_n["prompt_en"])
                    for w in (words or []))
        hits = sum(1 for w in (words or []) if _count(w, texts_n["name"] + texts_n["intro"]
                                                      + texts_n["features"] + texts_n["prompt_en"]))
        if hits >= DOMAIN_MIN_HITS and score > best_score:
            best, best_score = dom, score
    return (best, "文本关键词") if best else (None, "兜底")


# ---------------------------------------------------------------------- #
# ③ 领域包词典打分
# ---------------------------------------------------------------------- #
def score_dimensions(texts, dict_data, domain=None, exclude_dims=None,
                     fallback_global=False) -> dict:
    """按「通用骨架 + 单主领域包」打分，返回 {维度名: [(标签, 得分), ...] 降序}。

    - domain 为 None（兜底）时只装载通用骨架；
    - `fallback_global`（2026-09-15 18:15 批次 8-B，默认 False）：domain 为 None 时
      **改为装载"通用骨架 + 全部领域包"**，且**不再排除「领域」维度**（全词典兜底）；
      同一维度在多层出现时按标签取最高分**合并**（普通模式仍是后者覆盖前者，行为不变）。
      **仅 UI 单条推荐传 True**；批量打标/离线打标保持默认 False ⇒ 结果与改动前完全一致。
    - `exclude_dims`：不参与打分的维度名集合（供「批量打标」勾选"标签重点范围"用）；
    - 只在**有得分的维度**里返回候选。
    """
    texts_n = _norm_texts(texts)
    skip = set(exclude_dims or ())
    layers = []
    uni = dict_data.get("universal") or {}
    u_name = "领域"
    # 领域维度由"单主领域判定"决定，不参与词典打分（避免与判定结果冲突）
    layers.append({k: v for k, v in uni.items() if k != u_name})
    if domain:
        dom_layer = (dict_data.get("domains") or {}).get(domain)
        if dom_layer:
            layers.append(dom_layer)
    elif fallback_global:
        # 2026-09-15 18:15（批次 8-B）：判不出领域时的"全词典兜底"
        #   领域维度与全部领域包一并参与打分（用户确认：仅 UI 单条推荐路径启用）
        if uni.get(u_name):
            layers.append({u_name: uni.get(u_name)})
        for _d, _dl in (dict_data.get("domains") or {}).items():
            if _dl:
                layers.append(_dl)

    out = {}
    for layer in layers:
        for dim, labels in (layer or {}).items():
            if dim in skip:
                continue
            scored = []
            for tag, words in (labels or {}).items():
                s = 0.0
                for w in (words or []):
                    for field, weight in FIELD_WEIGHTS:
                        c = _count(w, texts_n[field])
                        if c:
                            s += c * weight
                if s > 0:
                    scored.append((tag, s))
            if not scored:
                continue
            if fallback_global and dim in out:      # 兜底模式：同名维度合并（取最高分）
                merged = dict(out[dim])
                for tag, s in scored:
                    merged[tag] = max(merged.get(tag, 0.0), s)
                out[dim] = sorted(merged.items(), key=lambda x: (-x[1], x[0]))
            else:
                scored.sort(key=lambda x: (-x[1], x[0]))
                out[dim] = scored
    return out


# 2026-09-18 14:00（用户确认，取代 2026-09-16 的"取词型来源合计名额上限"）：
#   **每个来源各自"尽力推荐"**（自身上限 = 每条例目标签数 `max_tags`），按「策略顺序」拼成
#   一个候选队列（显式标签恒在最前），最后**统一取前 max_tags 个**。
#   用户明确要求：无论热点词 / 领域+词表匹配推荐几个，**字段取词都要尽力推荐 1~3 个**加入
#   候选队列，再"按顺序取前 3"——即配额由**顺序**决定，而不是由来源之间互相扣减名额决定。
#   旧行为（`fallback_cap` / `FALLBACK_RESERVE`）会让取词在队列前段已满时"连算都不算"，
#   实测仅 7.8% 条目的取词标签能进最终结果 ⇒ 已按用户要求改为本模型。
FALLBACK_RESERVE = 0        # 历史常量：保留以免外部引用报错；现行模型不再扣减名额

# ---- 2026-09-16 10:20（批次 11-5，用户确认问题 2）：可配置的"标签推荐策略与顺序" ----
# 「显式标签」恒为最高优先、不可关闭，故**不列入可排序来源**（见 suggest 内部第 ① 步）。
SOURCE_EXPLICIT = "explicit"           # ① 显式标签（来源文本自带"标签：…"）
SOURCE_HOTWORD = "hotword"             # ② 热点词（用「热点词表」匹配条目文本）
SOURCE_DOMAIN_DICT = "domain_dict"     # ③ 领域判定 + 词表匹配（词典打分）
SOURCE_FIELD = "field"                 # ④ 字段取词（①名称 / ⑧中文 / ⑨英文提示词）
SOURCE_EXT = "ext"                     # ⑤ 扩展接口（预留，当前恒不产出）

SOURCE_LABELS = {
    SOURCE_EXPLICIT: "显式标签（来源文本自带，恒最高优先）",
    SOURCE_HOTWORD: "热点词（热点词表匹配）",
    SOURCE_DOMAIN_DICT: "领域判定 + 词表匹配",
    SOURCE_FIELD: "字段取词（①名称 / ⑧中文 / ⑨英文）",
    SOURCE_EXT: "扩展接口（预留）",
}
# 可排序 / 可开关的来源（「显式标签」不在其中）
POLICY_SOURCES = (SOURCE_HOTWORD, SOURCE_DOMAIN_DICT, SOURCE_FIELD, SOURCE_EXT)
# 默认顺序（用户建议：热点词第一 → 领域+词典 → 取词 → 扩展）
DEFAULT_POLICY = {
    "order": list(POLICY_SOURCES),
    "enabled": {SOURCE_HOTWORD: True, SOURCE_DOMAIN_DICT: True,
                SOURCE_FIELD: True, SOURCE_EXT: False},
}


def fallback_cap(max_tags: int) -> int:
    """**已废弃语义**（2026-09-18）：取词型来源的合计名额上限。

    现行模型为"各来源尽力推荐 → 按策略顺序拼队列 → 统一取前 max_tags 个"，
    不再在来源之间扣减名额；本函数保留只为兼容旧引用，返回值恒为 `max_tags`。
    """
    return max(1, int(max_tags))


def normalize_policy(policy=None) -> dict:
    """规范化「标签推荐策略」：非法项一律回退默认值；缺失来源自动补齐（保证不丢来源）。

    policy 形如 {"order": [来源…], "enabled": {来源: 真/假}}；None ⇒ 用 DEFAULT_POLICY。
    2026-09-16（批次 11-5，用户确认问题 2）。
    """
    order, enabled = [], dict(DEFAULT_POLICY["enabled"])
    if isinstance(policy, dict):
        _o = policy.get("order")
        if isinstance(_o, (list, tuple)):
            for _s in _o:
                _s = str(_s or "")
                if _s in POLICY_SOURCES and _s not in order:
                    order.append(_s)
        _e = policy.get("enabled")
        if isinstance(_e, dict):
            for _k, _v in _e.items():
                if _k in POLICY_SOURCES:
                    enabled[_k] = bool(_v)
    for _s in POLICY_SOURCES:            # 未列出的来源追加到末尾（不静默丢弃）
        if _s not in order:
            order.append(_s)
    return {"order": order, "enabled": enabled}


def build_vocab(dict_data) -> dict:
    """构造"已知词"索引，供取词命中校验（方案 D）使用（2026-09-16 批次 11-5）。

    返回 `{"tags": {标签名小写: 标签名}, "words": {匹配词小写: 标签名}}`。
    说明：「领域」维度只由"领域判定"给出（其匹配词是分类关键词，不是标签关键词），故不收录。
    """
    tags, words = {}, {}

    def _collect(layer):
        for _dim, labels in (layer or {}).items():
            if _dim == "领域":
                continue
            for _tag, _ws in (labels or {}).items():
                tags.setdefault(str(_tag).lower(), _tag)
                for _w in (_ws or []):
                    _w = str(_w or "").strip()
                    if _w and not _w.startswith("re:"):
                        words.setdefault(_w.lower(), _tag)

    _collect((dict_data or {}).get("universal") or {})
    for _dl in ((dict_data or {}).get("domains") or {}).values():
        _collect(_dl)
    return {"tags": tags, "words": words}


def hotword_tags(texts, hotwords, limit: int = MAX_TAGS) -> list:
    """2026-09-16（批次 11-5，用户确认问题 2）：**热点词来源**——用「热点词表」匹配条目文本。

    命中即把**该热点词本身**作为候选标签（用户自己维护的词清单，精度高）；
    排序按"字段加权命中次数降序、同分按词表顺序"。
    """
    if not hotwords:
        return []
    texts_n = _norm_texts(texts)
    scored = {}
    for _i, _w in enumerate(hotwords or []):
        _w = str(_w or "").strip()
        if not _w:
            continue
        _key = _w.lower()
        if _key in scored:
            continue
        _s = 0.0
        for _f, _weight in FIELD_WEIGHTS:
            _c = _count(_key, texts_n[_f])
            if _c:
                _s += _c * _weight
        if _s > 0:
            scored[_key] = (_s, _i, _w)
    out = [v[2] for v in sorted(scored.values(), key=lambda x: (-x[0], x[1]))]
    return out[:max(1, int(limit))]


def _en_stem_variants(low: str) -> list:
    """英文 token 的**保守词形变体**（2026-09-18 取词召回优化）。

    只做复/单数与常见词尾归一（`portraits→portrait`、`cities→city`、`frames→frame`、`box→box`），
    且**仅用于"能否命中词表"的兜底尝试**——命中与否最终仍以词表内容为准，
    所以不存在"造词"风险（变体不在词表里就什么也不会发生）。
    """
    out = []
    if not low or not low.isascii() or " " in low:
        return out
    if len(low) > 4 and low.endswith("ies"):
        out.append(low[:-3] + "y")
    if len(low) > 4 and low.endswith("es"):
        out.append(low[:-2])
    if len(low) > 3 and low.endswith("s") and not low.endswith("ss"):
        out.append(low[:-1])
    return out


def _match_vocab_tag(token: str, tags: dict, words: dict, hots: dict) -> str:
    """token → 已知词对应的**规范标签名**（未命中返回空串）。

    命中优先级（2026-09-18 扩充词形兜底）：① 热点词原文 → ② 词表标签名 → ③ 词表匹配词
    → ④ 上述三者的**保守词形变体**（复数/词尾）。
    """
    low = str(token or "").strip().lower()
    if not low:
        return ""
    for _k in (low, *_en_stem_variants(low)):
        for _m in (hots, tags, words):
            if _k in _m:
                return _m[_k]
    return ""


def _new_word_ok(s: str, relax: bool = False) -> bool:
    """**取词"新词"能否直接成为标签的质量闸门**（2026-09-18 用户确认方案 A + 兜底规则）。

    通过条件（全部满足）：
      · 非空、非提示词样板词（`_PROMPT_BOILERPLATE`）；
      · 英文：见下表；且**首词不在 `_TEMPLATE_LEAD`、末词不在 `_TEMPLATE_HEAD`**
        （挡掉 `BRAND NAME` / `headline text` / `render goal` / `85mm lens` 这类占位符与参数名）；
      · 中文：见下表；且不以功能词开头（避免"一个女孩""必须保留…"这类短语成标签）。

    | 模式 | 英文 | 中文 | 何时使用 |
    |---|---|---|---|
    | **严格**（默认，`relax=False`） | **恰好 2 个单词**、每词 ≥3 字母、总长 ≤20 | **2~6 字** | 正常路径（"有就推荐、没有就算了"） |
    | **兜底**（`relax=True`） | **1~3 个单词**、每词 ≥3 字母、总长 ≤32 | **2~12 字** | **仅当"热点词 + 领域/词表"都为 0 时**（兑现"必须推荐"） |

    严格模式下**单个英文词不收**：词表已覆盖绝大多数有意义的英文单词，漏网的多是通用词/名称片段
    （`Windy`／`meadow`／`Boyfriend` 之类），成标签只会变噪声；而兜底模式下必须收（否则"必须推荐"落空）。

    说明：本闸门**只管"新词"**；已命中词表 / 热点词的 token 走 `_match_vocab_tag`
    （映射为规范标签名），不受此限。
    """
    s = str(s or "").strip()
    if not s or s.lower() in _PROMPT_BOILERPLATE:
        return False
    if s.isascii():
        _parts = s.split()
        if relax:
            if not (1 <= len(_parts) <= 3) or any(len(p) < 3 for p in _parts):
                return False
            if len(s) > _RELAX_MAX_ASCII:
                return False
        else:
            if len(_parts) != 2 or any(len(p) < 3 for p in _parts):
                return False
            if len(s) > 20:
                return False
        _lo = [p.lower() for p in _parts]
        return not (_lo[0] in _TEMPLATE_LEAD or _lo[-1] in _TEMPLATE_HEAD)
    if not (2 <= len(s) <= (_RELAX_MAX_CJK if relax else _NEW_WORD_MAX_CJK)):
        return False
    return not s.startswith(_NEW_WORD_CJK_PREFIX)


def field_source_tags(texts, vocab=None, hotwords=None, limit: int = MAX_TAGS,
                      fields=("name", "prompt_cn", "prompt_en"),
                      allow_new: bool = True, relax_new: bool = False) -> list:
    """「字段取词」来源的候选标签（2026-09-18 用户确认方案 A 后重写）。

    规则：
      ① **命中已知词**（热点词原文 / 词表标签名 / 词表匹配词，含**两词短语**与**词形变体兜底**）
         ⇒ 采用其**规范标签名**（保证写法与词表一致，不受词典阈值与单主领域限制）；
      ② **未命中**但通过 `_new_word_ok()` 闸门的 token ⇒ 以**原文**作为新标签
         （`allow_new=False` 时跳过本步 ⇒ 回到旧"必须命中词表"的口径）。

    `relax_new`（2026-09-18 16:00，用户确认"兜底规则"）：切到**兜底闸门**——
    放宽 token 长度（英文 ≤32 / 中文 ≤12）与新词条件（英文 1~3 词），仅由 `suggest()` 在
    "热点词 + 领域/词表都为 0"时调用，保证"字段取词必须推荐 1~3 个"。

    两词短语**只用于"命中词表"**（`high` + `contrast` → 词表里的 `high contrast`），
    命中即消费这两个 token，避免把一个短语拆成两个标签；**新词只从"单词 token"产生**
    （提示词里的短语在切分时本就保留为一个 token）——这样不会把标题里随便相邻的两个英文词
    （`CCD street`）拼成"新标签"。返回按出现顺序去重的标签列表（最多 limit 个）。
    """
    tags = (vocab or {}).get("tags") or {}
    words = (vocab or {}).get("words") or {}
    hots = {}
    for _w in (hotwords or []):
        _w = str(_w or "").strip()
        if _w:
            hots.setdefault(_w.lower(), _w)
    out, seen = [], set()
    _lim = max(1, int(limit))
    _toks = _field_tokens(texts, fields, max(_lim * 8, 24), relax=relax_new)
    _i = 0
    while _i < len(_toks):
        _tok = _toks[_i][0]
        _tag, _used = "", 1
        if _i + 1 < len(_toks) and _toks[_i + 1][1]:      # 下一 token 与它只隔空白 ⇒ 可拼短语
            _tag = _match_vocab_tag(_tok + " " + _toks[_i + 1][0], tags, words, hots)
            if _tag:
                _used = 2
        if not _tag:
            _tag = _match_vocab_tag(_tok, tags, words, hots)
            if not _tag and allow_new and _new_word_ok(_tok, relax=relax_new):
                _tag = _tok
        if _tag and _tag not in seen:
            seen.add(_tag)
            out.append(_tag)
            if len(out) >= _lim:
                break
        _i += _used
    return out


def match_known_tags(texts, fields, limit: int = MAX_TAGS, vocab=None, hotwords=None) -> list:
    """取词 token **必须命中已知词**才采用（2026-09-16 批次 11-5 方案 D 的兼容入口）。

    2026-09-18：实现已统一到 `field_source_tags(..., allow_new=False)`（含两词短语与词形兜底），
    本函数保留为**只取"命中词表/热点词"的薄封装**，供外部按旧口径调用。
    """
    return field_source_tags(texts, vocab, hotwords, limit, fields, allow_new=False)


def field_candidates(texts, fields=("name", "prompt_cn", "prompt_en"), limit: int = 24,
                     vocab=None, hotwords=None) -> list:
    """返回"**过了质量闸门、但未命中词表 / 热点词**"的取词候选（2026-09-16 批次 12-3）。

    用途：供「智能自动取词词库」采集"新词"（app/auto_words.py）——
    已被方案 D 丢弃的词正是"词表外的新词"来源；**本函数不改 suggest() 的任何语义**（纯读）。
    返回：[{"word": 词, "field": 来源字段}]，按字段与出现顺序；同一词只保留首次来源。
    """
    tags = (vocab or {}).get("tags") or {}
    words = (vocab or {}).get("words") or {}
    hots = set()
    for _w in (hotwords or []):
        _s = str(_w or "").strip()
        if _s:
            hots.add(_s.lower())
    out, seen = [], set()
    _lim = max(1, int(limit))
    for _f in fields:
        for _tok in field_fallback_tags({_f: (texts or {}).get(_f)}, (_f,), _lim):
            _low = _tok.lower()
            if _low in hots or _low in tags or _low in words:
                continue                       # 已命中已知词 ⇒ 不是"新词"
            if _tok not in seen:
                seen.add(_tok)
                out.append({"word": _tok, "field": _f})
                if len(out) >= _lim:
                    return out
    return out


def ext_source_tags(texts, dict_data=None, hotwords=None) -> list:
    """「扩展接口」占位（2026-09-16 批次 11-5，用户要求"留一个扩展接口"）。

    预留位：日后要接入新的推荐来源（外部词典 / 大模型建议 / 规则插件…）时，
    **只需在此实现并到设置里启用**，`suggest()` 主流程无需改动。当前恒返回空列表。
    """
    return []


# ---------------------------------------------------------------------- #
# ④ 配额合成（对外主入口）
# ---------------------------------------------------------------------- #
def suggest(texts, dict_data, ctx_names=None, max_tags: int = MAX_TAGS,
            min_score: float = MIN_SCORE, exclude_dims=None,
            fallback_global: bool = False, field_fallback: bool = False,
            policy=None, hotwords=None) -> dict:
    """对一条条目给出标签建议。

    texts: {字段名: 文本}（字段见 FIELD_WEIGHTS；缺失字段视为空）
    ctx_names: 该条目的分类上下文名（根目录 / 项目类别 / 各级分类名），用于领域判定
    exclude_dims: 不参与词典打分的维度名集合（供"标签重点范围"勾选用；
      **"领域"与"显式"不受影响**——前者由领域判定给出，后者来自来源文本）
    fallback_global: 2026-09-15 18:15（批次 8-B，用户确认）新增的**可选**参数，默认 False。
      为 True 且**判不出领域**时，不再只装"通用骨架"，改为对全部领域包的匹配词打分
      （仍受 min_score 约束），结果来源标 `"词典兜底"`；判出领域时与 False 完全同行为。
      **仅 UI 单条推荐传 True；批量打标/离线打标保持默认** ⇒ 批量结果与改动前逐条一致。
    field_fallback: 2026-09-16（批次 11-3，用户确认）新增的**可选**参数，默认 False。
      为 True 时允许「字段取词」来源产出标签；为 False 时该来源整段跳过。
      **只有 UI 单条推荐（详情区 / 快速新建）传 True**；批量打标 / 离线打标保持默认 False。
      **2026-09-18 16:20（用户第 3 轮确认）**：取消原先"**有显式标签就不取词**"的限制——
      用户要求"字段取词任何时候都要尽力推荐"，只是**排在显式标签之后**（显式恒最高优先）。
    policy: 2026-09-16 10:20（批次 11-5，用户确认问题 2）新增的**可选**参数，
      「标签推荐策略与顺序」，形如 `{"order": [来源…], "enabled": {来源: 真/假}}`
      （来源见 `SOURCE_*` / `POLICY_SOURCES`）。None ⇒ 用 `DEFAULT_POLICY`
      （热点词 → 领域+词典 → 取词 → 扩展）。非法项自动回退默认值。
    hotwords: 2026-09-16（批次 11-5）新增的**可选**参数：热点词清单（字符串列表）。
      供「热点词」来源与取词命中校验使用；不传 ⇒ 该来源不产出任何标签（默认行为不变）。
    返回：
      {
        "domain": 领域名 or None,
        "domain_source": "分类名映射" / "文本关键词" / "兜底",
        "tags": [{"tag": 标签名, "dim": 维度, "score": 得分, "source": 来源}, ...],
      }
    标签顺序即优先级：**显式标签（恒最高）→ 按 policy["order"] 依次执行各来源**。
    配额（2026-09-18 按用户确认改为"队列模型"）：**每个被启用的来源都按自己的口径"尽力推荐"**
    （自身上限 = max_tags），按策略顺序拼成一个候选队列，**最后统一取前 max_tags 个**；
    即"谁进最终结果"由**顺序**决定，来源之间**不再互相扣减名额**（旧 `fallback_cap` 语义已废弃）。
    **「兜底取词」规则**（2026-09-18 16:20，用户第 3 轮确认）：字段取词正常按**严格闸门**产出
    （0~3 个都允许，"有就推荐、没有就算了"）；但**当「热点词」与「领域+词表」两个来源都为 0**
    时，自动用**兜底闸门**重算一次取词并追加到队尾 ⇒ **保证字段取词至少推荐 1 个**。
    """
    domain, dsrc = detect_domain(texts, dict_data, ctx_names)
    max_tags = max(1, int(max_tags))
    # 2026-09-15 18:15（批次 8-B）：仅在"判不出领域"时启用全词典兜底
    _fallback = bool(fallback_global) and domain is None
    pol = normalize_policy(policy)

    queue, seen, used_dims = [], set(), set()
    # 2026-09-18 16:20（用户第 3 轮确认"兜底规则"）：分别记录「字段取词」与「热点词 + 领域/词表」
    #   两组的**实际产出条数**，供末尾判定"字段取词是否必须兜底"。
    _n_pick = 0          # 「字段取词」来源实际产出条数
    _n_other = 0         # 「热点词 + 领域/词表」两个来源实际产出条数

    def _push(tag, dim, score, source):
        """把候选追加到"有序候选队列"（同标签只留首次出现）。"""
        if not tag or tag in seen:
            return False
        seen.add(tag)
        queue.append({"tag": tag, "dim": dim, "score": round(float(score), 2),
                      "source": source})
        if dim not in ("显式", "热点词", "取词", "扩展"):
            used_dims.add(dim)
        return True

    # ---- ① 显式标签：恒为最高优先，**不受策略开关 / 顺序影响**（用户确认）----
    explicit = extract_explicit_tags(texts, limit=max_tags)
    for t in explicit:
        _push(t, "显式", 1e9, "显式标签")

    # ---- ② 按「标签推荐策略」的 order 依次执行各来源；enabled 为假则整段跳过 ----
    #   2026-09-18：去掉"已满就 break"与 `_cap_left` 扣减 ⇒ 每个来源都**尽力产出**，
    #   由最后的"取前 max_tags 个"统一裁剪（用户要求：顺序决定取舍，而非互相抢名额）。
    for _src in pol["order"]:
        if not pol["enabled"].get(_src):
            continue

        if _src == SOURCE_HOTWORD:
            # 2026-09-16（批次 11-5）：热点词来源（命中即用该热点词本身作标签）
            for _t in hotword_tags(texts, hotwords, max_tags):
                if _push(_t, "热点词", 1e7, "热点词"):
                    _n_other += 1

        elif _src == SOURCE_DOMAIN_DICT:
            # ② 领域标签（单主领域）+ ③ 词典候选（每维最多 1 个，按得分降序）
            if domain:
                if _push(domain, "领域", 1e8, dsrc):
                    _n_other += 1
            scored = score_dimensions(texts, dict_data, domain, exclude_dims,
                                      fallback_global=_fallback)
            flat = []
            for dim, items in scored.items():
                if dim in used_dims:
                    continue
                flat.append((items[0][0], dim, items[0][1]))
            flat.sort(key=lambda x: (-x[2], x[0]))
            for tag, dim, s in flat:
                if s < min_score:            # 阈值裁剪：宁缺勿滥
                    break
                if _push(tag, dim, s, "词典兜底" if _fallback else "词典"):   # 2026-09-15 批次 8-B
                    _n_other += 1

        elif _src == SOURCE_FIELD:
            # 2026-09-16（批次 11-3）：字段取词——需 field_fallback 放行；
            #   2026-09-18（用户确认方案 A）：命中词表/热点词的映射为**规范标签名**，
            #   未命中但过严格新词闸门的以**原文**成为新标签（见 field_source_tags）；
            #   两者来源标注不同：`标题/提示词`（命中词表）／`取词（新词）`（词表外新词）。
            #   2026-09-18 16:20（用户第 3 轮确认）：**取消"有显式标签就整段跳过"**——
            #   用户要求"字段取词任何时候都要尽力推荐"，只是**排在显式标签之后**（显式恒最前）。
            if not field_fallback:
                continue
            _vocab = build_vocab(dict_data)
            _known = set(_vocab.get("tags", {}).values())
            for _t in field_source_tags(texts, _vocab, hotwords, max_tags):
                if _push(_t, "取词", 1e6,
                         "标题/提示词" if _t in _known else "取词（新词）"):
                    _n_pick += 1

        elif _src == SOURCE_EXT:
            # 扩展接口（预留）：当前恒不产出，日后接入新来源只需实现 ext_source_tags()
            for _t in ext_source_tags(texts, dict_data, hotwords):
                _push(_t, "扩展", 1e6, "扩展接口")

    # ---- ③「兜底取词」规则（2026-09-18 16:20，用户第 3 轮确认）----
    #   用户口径：「尽力而为，有就推荐、没有就不推荐；**但当热点词与领域+词表都为 0 时，
    #   字段取词必须推荐 1~3 个**」。故此处做一次**事后判定**（与策略顺序无关）：
    #   若「热点词 + 领域/词表」两个来源**一个候选都没产出**、且字段取词也**没产出**，
    #   则用**兜底闸门**（放宽长度与词数）重算一次取词，把结果**追加到队尾**
    #   （即排在"显式标签"与其它来源之后），保证"绝不出现整条没有任何标签"。
    if (field_fallback and pol["enabled"].get(SOURCE_FIELD)
            and _n_pick == 0 and _n_other == 0):
        _vocab = build_vocab(dict_data)
        _known = set(_vocab.get("tags", {}).values())
        for _t in field_source_tags(texts, _vocab, hotwords, max_tags, relax_new=True):
            _push(_t, "取词", 1e6,
                  "标题/提示词" if _t in _known else "取词（兜底）")

    return {"domain": domain, "domain_source": dsrc, "tags": queue[:max_tags]}


def tag_names(result) -> list:
    """从 suggest() 结果中取标签名列表。"""
    return [t["tag"] for t in (result or {}).get("tags") or []]


# ---------------------------------------------------------------------- #
# ⑤ 字段取词兜底（2026-09-15 批次 10，用户确认策略：无显式标签时优先取词）
# ---------------------------------------------------------------------- #
def _field_tokens(texts, fields, limit: int, relax: bool = False) -> list:
    """按字段切词，并**保留"与上一个 token 是否只隔空白"**（2026-09-18 取词优化）。

    返回 `[(token, adj)]`：`adj=True` 表示该 token 与前一个 token 在原文本里**只隔空白 /
    连字符 / 下划线** ⇒ 两者可合并成"两词短语"再试命中词表
    （如 `high` + `contrast` → 词表里的 `high contrast`）；跨逗号 / 顿号 / 句末标点的相邻
    token `adj=False`（不会把 `portrait, film` 错拼成短语）。

    切分规则与既有口径**完全一致**（只是多带回一个"相邻"标记）：
      - name：按 空白 + 中英标点 切（短语式标题）；
      - prompt_cn / prompt_en：按 逗号/顿号/分号/句末标点/换行 切（保留段内空格）；
      - 再按中英边界拆 → `_clean_token()` 清洗与质量闸门；**本函数不去重**（去重由调用方负责）。

    `relax`（2026-09-18 16:00，用户确认"兜底规则"）：切到**兜底闸门**的长度上限
    （英文 ≤32 / 中文 ≤12），仅供 `suggest()` 在"另两个来源都为 0"时调用。
    """
    out = []
    limit = max(1, int(limit))
    _ma = _RELAX_MAX_ASCII if relax else _FALLBACK_MAX_ASCII
    _mc = _RELAX_MAX_CJK if relax else _FALLBACK_MAX_CJK
    for field in fields:
        raw = str((texts or {}).get(field) or "")
        if not raw:
            continue
        split_re = _TITLE_SPLIT_RE if field == "name" else _PROMPT_SPLIT_RE
        pos, adj = 0, False
        for _m in list(split_re.finditer(raw)) + [None]:
            _start, _gap = (_m.start(), _m.group(0)) if _m else (len(raw), "")
            _chunk = raw[pos:_start].strip()
            pos = _start + len(_gap)
            if _chunk:
                for _part in _TITLE_BOUNDARY_RE.split(_chunk):
                    s = _clean_token(_part, _ma, _mc)
                    if not s:
                        continue
                    out.append((s, adj))
                    adj = True                     # 同一"段"内的后续 token 与前一个相邻
                    if len(out) >= limit:
                        return out
            # 段间隔符决定"下一段首个 token"是否与前一 token 相邻
            #   （间隔符只由 空白 / 连字符 / 下划线 组成 ⇒ 视为"同一短语内"）
            adj = bool(_gap) and _gap.strip(" \t\u3000-_") == ""
    return out


def field_fallback_tags(texts, fields, limit: int = MAX_TAGS) -> list:
    """2026-09-15 19:30（批次 10，用户确认）：从指定字段依次切词取标签。

    切分规则按字段区分（符合各自书写习惯）：
      - name（①名称/标题）：按 空白 + 中英标点 切（短语式标题）
      - prompt_cn / prompt_en（⑧⑨提示词）：按 逗号/顿号/分号/句末标点/换行 切（列表式提示词，
        保留短语内部空格，如 "film grain" 作为一个词）
    统一后处理：→ 连字符/下划线归一为空格（2026-09-18）→ 中英边界再拆 → `_clean_token()`
      清洗与质量闸门（丢纯数字/色值/样板词/超长碎片，英文去停用词）→ 去重。
    按 fields 顺序依次取，取满 limit 即止。实现见 `_field_tokens()`（本函数是其去重薄封装）。
    """
    out, seen = [], set()
    for s, _adj in _field_tokens(texts, fields, max(1, int(limit)) * 4):
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max(1, int(limit)):
            break
    return out


def title_fallback_tags(name, limit: int = MAX_TAGS) -> list:
    """从①名称切词（field_fallback_tags 的单字段薄封装，保持向后兼容）。"""
    return field_fallback_tags({"name": name}, ("name",), limit)


def suggest_entry(entry, dict_data, ctx_names=None, max_tags: int = MAX_TAGS) -> dict:
    """便捷入口：entry 为 dict 或任意具备属性访问的对象（自动取 6 个字段）。"""
    if isinstance(entry, dict):
        texts = {f: entry.get(f) for f, _w in FIELD_WEIGHTS}
    else:
        texts = {f: getattr(entry, f, None) for f, _w in FIELD_WEIGHTS}
    return suggest(texts, dict_data, ctx_names, max_tags=max_tags)


# ---------------------------------------------------------------------- #
# 自测
# ---------------------------------------------------------------------- #
def _selftest() -> None:
    """引擎自测：显式标签 / 领域判定 / 词典打分 / 配额 / 阈值 / 兜底。"""
    from . import tagger_dict

    D = tagger_dict.builtin_dict()

    # 1. 显式标签提取：多种写法 + 去重 + 超长丢弃 + 遇到下一个关键词即停
    ex = extract_explicit_tags({
        "name": "1. 潦草涂鸦风插画",
        "intro": "标签：二次元变身、潦草涂鸦、表情包插画、AI变装",
        "prompt_en": "tags: anime, doodle 关键词: 涂鸦",
    }, limit=10)
    assert ex[:4] == ["二次元变身", "潦草涂鸦", "表情包插画", "AI变装"], ex
    assert "涂鸦" in ex and "anime" in ex, ex
    assert "doodle 关键词" not in "|".join(ex), ex          # 不得把下一个关键词连进来
    assert extract_explicit_tags({"intro": "标签：" + "超" * 30}) == []
    assert len(extract_explicit_tags({"intro": "标签：A、B、C、D"})) == MAX_TAGS
    # 行内 #标签
    assert extract_explicit_tags({"intro": "用 #新中式 #国风 风格"}) == ["新中式", "国风"]

    # 1-b. 2026-09-17（新问题 A/B 修复）：显式标签的"质量闸门"
    #   ① 纯数字 / 序号 → 丢弃（用户实测反馈"大量阿拉伯数字标签"）
    assert extract_explicit_tags({"intro": "标签：12、234、1145"}) == []
    assert extract_explicit_tags({"intro": "关键词: 1、2、3"}) == []
    #   ② 十六进制色值 / 行内 #颜色码 → 丢弃（提示词里高频出现）
    assert extract_explicit_tags({"intro": "标签：000000、1A2332、fff1"}) == []
    assert extract_explicit_tags({"intro": "背景 #000000 前景 #FFFFFF"}) == []
    assert extract_explicit_tags({"intro": "颜色 #1A2332. 描边 #fff"}) == []
    assert extract_explicit_tags({"intro": "第 #12 张"}) == []
    #   ③ 首尾成对标点 → 洗净保留（标点前后并非都有文字）
    assert extract_explicit_tags({"intro": "标签：[世界]、【门派】"}) == ["世界", "门派"]
    #   ④ 内部含标点（标点前后都有文字）→ 丢弃
    assert extract_explicit_tags({"intro": "标签：古风/汉服、K-pop偶像、香水/美妆"}) == []
    assert extract_explicit_tags({"intro": "标签：1/7比例、亚马逊A+、AI变装"}) == ["AI变装"]
    #   ⑤ 正常显式标签不受影响（含单字符标签，刻意保留）
    assert extract_explicit_tags({"intro": "标签：电影感、胶片颗粒、A"}) == ["电影感", "胶片颗粒", "A"]

    # 1-c. 2026-09-17（新问题 B 修复）：字段取词同样挡住"内部含标点"
    #   ①名称按"空白 + 中英标点"切分，故 `K-pop` 会被切成 `K`(长度<2 丢弃) 与 `pop`(保留)——
    #   **标点本身绝不会进入标签**，符合"标签不含标点"的口径；不改变既有切分设计。
    assert field_fallback_tags({"name": "K-pop 偶像 照片"}, ("name",), 5) == ["pop", "偶像", "照片"]
    #   ⑧⑨提示词不按下划线切分；2026-09-18 取词优化：`_`/`-` 先**归一为空格**再清洗
    #   ⇒ `film_grain` → `film grain`（与词表里的 `film grain` 同形，能真正命中）
    assert field_fallback_tags({"prompt_en": "film_grain, 35mm"}, ("prompt_en",), 5) \
        == ["film grain", "35mm"]
    assert field_fallback_tags({"prompt_en": "film-grain, 35mm"}, ("prompt_en",), 5) \
        == ["film grain", "35mm"]
    assert field_fallback_tags({"name": "12 个 234 技巧"}, ("name",), 5) == ["技巧"]
    #   2026-09-18：提示词样板词/参数词不进 token（8k / hdr / style / aspect ratio / masterpiece）
    assert field_fallback_tags(
        {"prompt_en": "masterpiece, best quality, 8K, HDR, aspect ratio, style, cinematic"},
        ("prompt_en",), 9) == ["cinematic"]

    # 2. 领域判定：分类名映射优先
    dom, src = detect_domain({"name": "x"}, D, ctx_names=["海外AI绘画案例库", "Portrait & People"])
    assert dom == "视觉" and src == "分类名映射", (dom, src)
    dom2, src2 = detect_domain({"name": "x"}, D, ctx_names=["计算机编程"])
    assert dom2 == "编程" and src2 == "分类名映射", (dom2, src2)
    # 文本关键词判定（无分类上下文时）
    dom3, src3 = detect_domain(
        {"name": "Python 脚本修复", "intro": "调试 报错 异常 代码"}, D, ctx_names=[])
    assert dom3 == "编程" and src3 == "文本关键词", (dom3, src3)
    # 兜底
    dom4, src4 = detect_domain({"name": "zzz"}, D, ctx_names=[])
    assert dom4 is None and src4 == "兜底", (dom4, src4)

    # 3. 词典打分：英文词边界（不误命中间）
    sc = score_dimensions({"prompt_en": "portrait of a woman, film grain, 35mm"},
                          D, "视觉")
    assert sc["题材主体"][0][0] == "人像", sc.get("题材主体")
    assert sc["媒介工艺"][0][0] == "胶片", sc.get("媒介工艺")
    # "human" 不得命中 "man"（词边界）
    sc2 = score_dimensions({"prompt_en": "human being"}, D, "视觉")
    assert all(t != "人像" for t, _s in sc2.get("题材主体", [])), sc2.get("题材主体")

    # 4. 配额：每维最多 1 个 + 合计 ≤ 3 + 顺序（显式 → 领域 → 词典）
    r = suggest({"name": "CCD street snap doll girl",
                 "intro": "标签：街拍、玩偶",
                 "prompt_en": "portrait, film grain, shallow depth of field, night"},
                D, ctx_names=["海外AI绘画案例库"])
    names = tag_names(r)
    assert len(names) <= MAX_TAGS, names
    assert names[:2] == ["街拍", "玩偶"], names              # 显式优先
    assert r["domain"] == "视觉" and names[2] == "视觉", r    # 剩余名额给领域标签
    dims = [t["dim"] for t in r["tags"]]
    assert len(dims) == len(set(dims)) or "显式" in dims, dims

    # 5. 阈值裁剪：弱信号丢弃（"夜景"仅 1 次命中 → 得分 1 < 2 被丢弃）
    r2 = suggest({"name": "作品集", "prompt_en": "night"}, D, ctx_names=["图像"])
    assert "夜景" not in tag_names(r2), tag_names(r2)

    # 6. 兜底：判不出领域时，只用通用骨架（不出领域内标签）
    #   2026-09-16（批次 11-3）：取词改为**可选**（默认关）⇒ 本组恢复"无取词"的原始口径
    r3 = suggest({"name": "随手记录", "intro": "素材 参考"}, D, ctx_names=[])
    assert r3["domain"] is None and r3["domain_source"] == "兜底"
    assert set(tag_names(r3)) <= {"素材", "参考"}, tag_names(r3)
    assert all(t["dim"] in ("用途", "显式") for t in r3["tags"]), r3["tags"]
    # 显式开启取词后，"取词"来源可产出标签（2026-09-16 批次 11-5：须命中词表/热点词）
    r3f = suggest({"name": "赛博朋克 涂鸦", "intro": "素材 参考"}, D, ctx_names=[],
                  field_fallback=True)
    assert "赛博朋克" in tag_names(r3f), tag_names(r3f)
    assert not ({"胶片", "3D渲染"} & set(tag_names(r3f))), tag_names(r3f)

    # 7. 领域包隔离：判到某领域时，**不得**出现其它领域包的标签
    #   2026-09-18：出厂词表换成"多领域包"的新版（不再有"文学"包；当日午后为 21 个包）⇒ 本用例改为
    #   **不依赖具体出厂词表**的写法：取第一个领域包作"本领域"、用其判定表首个关键词作上下文，
    #   再断言"结果里绝不出现其它任何领域包的标签"（比原来只测"文学 vs 视觉"更严）。
    _packs7 = list((D.get("domains") or {}).keys())
    assert len(_packs7) >= 2, _packs7
    _keep7 = _packs7[0]
    _kw7 = ((D.get("domain_map") or {}).get(_keep7) or [""])[0]
    _others7 = set()
    for _dn7, _dims7 in (D.get("domains") or {}).items():
        if _dn7 != _keep7:
            for _labels7 in _dims7.values():
                _others7 |= set(_labels7.keys())
    r4 = suggest({"prompt_en": "portrait of a woman, film grain, 35mm"},
                 D, ctx_names=[_kw7])
    assert r4["domain"] == _keep7, r4
    tn = tag_names(r4)
    assert tn, r4
    assert not (set(tn) & _others7), (tn, sorted(_others7 & set(tn)))

    # 8. 空输入不报错、可返回空标签
    r5 = suggest({}, D)
    assert r5["tags"] == [] and r5["domain"] is None, r5

    # 9. 维度范围（exclude_dims）：被排除的维度不出标签，但「领域」不受影响
    _txt = {"name": "Editorial portrait poster",
            "intro": "教程 素材",
            "prompt_en": "portrait, portrait, cinematic, film grain"}
    _all = suggest(_txt, D, ctx_names=["图像"])
    assert "题材主体" in {t["dim"] for t in _all["tags"]}, _all["tags"]
    _ex = suggest(_txt, D, ctx_names=["图像"], exclude_dims=["题材主体", "媒介工艺"])
    dims = {t["dim"] for t in _ex["tags"]}
    assert "题材主体" not in dims and "媒介工艺" not in dims, dims
    assert "领域" in dims and _ex["tags"][0]["dim"] == "领域", _ex["tags"]

    # 10. 批次 8-B：全词典兜底（fallback_global）——默认 False 行为与改动前一致
    #   2026-09-16（批次 11-3）：取词默认关 ⇒ 本组恢复原始断言（无需再插显式标签隔离）
    _t8b = {"name": "portrait film grain cinematic",
            "prompt_en": "portrait, film grain"}
    _off8b = suggest(_t8b, D, ctx_names=[])
    assert _off8b["domain"] is None and _off8b["domain_source"] == "兜底", _off8b
    assert all(t["dim"] in ("用途", "显式") for t in _off8b["tags"]), _off8b["tags"]
    _on8b = suggest(_t8b, D, ctx_names=[], fallback_global=True)
    assert _on8b["tags"], _on8b
    assert all(t["source"] == "词典兜底" for t in _on8b["tags"]), _on8b["tags"]
    assert {"人像", "胶片"} & set(tag_names(_on8b)), tag_names(_on8b)
    # 判出领域时，fallback_global=True 与 False 结果完全一致（行为不变）
    _c_on = suggest(_t8b, D, ctx_names=["海外AI绘画案例库"], fallback_global=True)
    _c_off = suggest(_t8b, D, ctx_names=["海外AI绘画案例库"])
    assert tag_names(_c_on) == tag_names(_c_off) and _c_on["domain"] == _c_off["domain"], \
        (tag_names(_c_on), tag_names(_c_off))

    # 11. 批次 8-C：①名称取词兜底（title_fallback_tags 现为 field_fallback_tags 薄封装）
    _t8c = title_fallback_tags("1. 潮玩玩具包装 · Doll Packaging Poster")
    assert _t8c == ["潮玩玩具包装", "Doll", "Packaging"], _t8c
    assert title_fallback_tags("") == []
    assert title_fallback_tags("a of the") == []
    assert title_fallback_tags("AI绘画 赛博朋克", limit=1) == ["AI"]
    assert title_fallback_tags("超" * 30) == []

    # 12. 批次 10：field_fallback_tags 字段取词（name 空白切 / 提示词逗号切）
    _fb = field_fallback_tags(
        {"name": "AI绘画 赛博朋克 少女",
         "prompt_cn": "一个女孩，长发，微笑",
         "prompt_en": "a girl, long hair, the film grain"},
        ("name", "prompt_cn", "prompt_en"), limit=10)
    assert _fb[:4] == ["AI", "绘画", "赛博朋克", "少女"], _fb
    assert "film grain" in _fb, _fb          # 提示词按逗号切，保留短语内部空格
    assert "long hair" in _fb, _fb
    assert "a girl" not in _fb and "girl" in _fb, _fb   # 英文去首尾停用词

    # 13. 批次 10/11-3/11-5：取词**默认关闭**；开启后按默认策略顺序**排在最后**
    #     （默认顺序：热点词 → 领域+词典 → 取词 → 扩展，见 DEFAULT_POLICY）
    _t10 = {"name": "赛博朋克 少女", "prompt_en": "portrait, film grain"}
    _r10off = suggest(_t10, D, ctx_names=[])
    assert not any(t["dim"] == "取词" for t in _r10off["tags"]), _r10off   # 默认关（11-3）
    _r10 = suggest(_t10, D, ctx_names=[], field_fallback=True)
    _d10 = [t["dim"] for t in _r10["tags"]]
    assert "取词" in _d10 and _d10[-1] == "取词", _r10
    assert "赛博朋克" in tag_names(_r10), tag_names(_r10)

    # 14. 批次 10/11-3 → **2026-09-18 16:20（用户第 3 轮确认）改版**：
    #   「有显式标签就整段跳过取词」的旧口径**已取消**——取词照常尽力产出，
    #   只是**排在显式标签之后**；若 3 个名额被"显式 + 领域/词典"占满，取词候选自然进不了最终结果。
    _t10b = {"name": "AI绘画 赛博朋克", "intro": "标签：显式标",
             "prompt_en": "film grain"}
    assert field_source_tags(_t10b, build_vocab(D), [], MAX_TAGS) \
        == ["绘画", "赛博朋克", "胶片"], "有显式标签时取词也必须照常产出候选"
    _r10b = suggest(_t10b, D, ctx_names=[], field_fallback=True)
    assert _r10b["tags"][0]["tag"] == "显式标" and _r10b["tags"][0]["dim"] == "显式", _r10b
    assert "取词" not in [t["dim"] for t in _r10b["tags"]], _r10b   # 名额被显式/领域/词典占满

    # 15. 批次 11-4：取词质量闸门（丢标题序号 / 纯数字 / 超长整句 / 括号碎片）
    _q1 = field_fallback_tags({"name": "17. 手账涂鸦头像卡 - GPT Image 2 / 2:3"}, ("name",), 5)
    assert _q1 == ["手账涂鸦头像卡", "GPT", "Image"], _q1        # 序号"17"、"/ 2:3" 已被闸门丢弃
    assert field_fallback_tags({"name": "251. 唐朝贵妇遛粉色马甲异形工笔画"}, ("name",), 5) == [], \
        "超长整句（14 字）应被丢弃"
    assert field_fallback_tags(
        {"prompt_cn": "严格保持产品本体完全一致。必须保留产品的外观造型"}, ("prompt_cn",), 5) == [], \
        "中文整句（>8 字）应被丢弃"
    _q2 = field_fallback_tags({"prompt_en": "style (watercolor, 2024), cinematic"},
                              ("prompt_en",), 5)
    assert "cinematic" in _q2 and "2024" not in _q2, _q2         # 纯数字 2024 被丢弃
    assert all(not _TAG_NOISE_RE.search(x) for x in _q2), _q2    # 无标点 / 引号碎片
    assert field_fallback_tags({"prompt_en": "b&w, top++"}, ("prompt_en",), 5) == [], \
        "内部残留符号（b&w / top++）应被丢弃"
    assert field_fallback_tags({"name": "潮玩玩具包装"}, ("name",), 5) == ["潮玩玩具包装"]  # 6 字正常保留

    # 16. 2026-09-18（用户确认）：配额改为"**各来源尽力推荐 → 按策略顺序拼队列 → 取前 max_tags**"
    #   旧语义（取词型来源合计 ≤ max_tags-1、"已满就不再计算取词"）已废弃
    #   ⇒ 兼容函数 `fallback_cap` 恒返回 max_tags；取词是否进最终结果**只由顺序决定**。
    assert fallback_cap(3) == 3 and fallback_cap(1) == 1, (fallback_cap(3), fallback_cap(1))
    #   ① 默认顺序（热点词 → 领域+词典 → 取词）：前两个来源只给出 2 个 ⇒ **取词补上第 3 个**
    _r16a = suggest({"name": "多巴胺穿搭 小清新"}, D, ctx_names=["图像"],
                    hotwords=["多巴胺穿搭"], field_fallback=True)
    assert [t["dim"] for t in _r16a["tags"]] == ["热点词", "领域", "取词"], _r16a
    assert tag_names(_r16a)[2] == "小清新", _r16a
    #   ② 把「字段取词」放到第一位 ⇒ 取词标签排在热点词/词典**之前**（用户要求 2(1)）
    _pol16 = {"order": [SOURCE_FIELD, SOURCE_HOTWORD, SOURCE_DOMAIN_DICT, SOURCE_EXT],
              "enabled": {SOURCE_FIELD: True, SOURCE_HOTWORD: True,
                          SOURCE_DOMAIN_DICT: True, SOURCE_EXT: False}}
    _r16b = suggest({"name": "小清新 手账涂鸦"}, D, ctx_names=[],
                    policy=_pol16, field_fallback=True)
    assert tag_names(_r16b) == ["小清新", "手账涂鸦"], _r16b
    assert all(t["dim"] == "取词" for t in _r16b["tags"]), _r16b
    #   ③ 取词即使进不了最终 3 个，也**照常尽力算出候选**（不再"连算都不算"）
    _r16c = suggest({"name": "多巴胺穿搭 小清新"}, D, ctx_names=["图像"],
                    hotwords=["多巴胺穿搭"], field_fallback=True)
    assert "小清新" in field_source_tags({"name": "多巴胺穿搭 小清新"}, build_vocab(D),
                                        ["多巴胺穿搭"], MAX_TAGS), "取词应尽力产出候选"
    assert "领域" in [t["dim"] for t in _r16c["tags"]], _r16c

    # 17. 批次 11-5：热点词来源（命中即用热点词本身；默认顺序下排在最前）
    _t17 = {"name": "多巴胺穿搭 街拍", "prompt_en": "portrait"}
    _r17 = suggest(_t17, D, ctx_names=[],
                   hotwords=["多巴胺穿搭", "新中式"], field_fallback=True)
    assert _r17["tags"] and _r17["tags"][0]["tag"] == "多巴胺穿搭", _r17
    assert _r17["tags"][0]["dim"] == "热点词", _r17
    # 热点词表为空 ⇒ 该来源不产出（默认行为不变）
    _r17b = suggest(_t17, D, ctx_names=[], hotwords=[], field_fallback=True)
    assert not any(t["dim"] == "热点词" for t in _r17b["tags"]), _r17b

    # 18. 批次 11-5：策略的顺序与开关可配置；「显式标签」恒最高优先、不受开关影响
    _pol18 = {"order": [SOURCE_FIELD, SOURCE_DOMAIN_DICT, SOURCE_HOTWORD, SOURCE_EXT],
              "enabled": {SOURCE_FIELD: True, SOURCE_DOMAIN_DICT: True,
                          SOURCE_HOTWORD: True, SOURCE_EXT: True}}
    _r18 = suggest({"name": "赛博朋克", "intro": "标签：显式标"}, D, ctx_names=[],
                   field_fallback=True, policy=_pol18)
    assert _r18["tags"][0]["tag"] == "显式标", _r18
    # 2026-09-18 16:20：显式标签之后，**取词照样产出**（本策略里取词排第一 ⇒ 紧随显式之后）
    assert tag_names(_r18)[1] == "赛博朋克" and _r18["tags"][1]["dim"] == "取词", _r18
    # 关掉"领域+词典" ⇒ 该来源不再产出；但取词仍工作
    _pol18b = {"order": list(POLICY_SOURCES),
               "enabled": {SOURCE_HOTWORD: False, SOURCE_DOMAIN_DICT: False,
                           SOURCE_FIELD: True, SOURCE_EXT: False}}
    _r18b = suggest({"name": "赛博朋克", "intro": "素材 参考 教程"},
                    D, ctx_names=["海外AI绘画案例库"], field_fallback=True,
                    policy=_pol18b)
    assert "视觉" not in tag_names(_r18b) and "赛博朋克" in tag_names(_r18b), _r18b
    # 非法策略自动回退默认值（不抛异常）
    _r18c = suggest({"name": "赛博朋克"}, D, ctx_names=[],
                    policy={"order": "坏值", "enabled": "坏值"})
    assert isinstance(_r18c.get("tags"), list), _r18c

    # 19. 批次 11-5：方案 D——取词必须命中词表/热点词；词表外的词一律丢弃
    _r19 = suggest({"name": "Windy meadow 赛博朋克"}, D, ctx_names=[], field_fallback=True)
    assert "赛博朋克" in tag_names(_r19), _r19
    assert "Windy" not in tag_names(_r19) and "meadow" not in tag_names(_r19), _r19

    # 20. 批次 12-3：取词"新词候选"（过质量闸门但**未命中**词表/热点词）——自动取词词库的采集源
    _voc20 = build_vocab(D)
    _t20 = {"name": "Windy meadow 赛博朋克", "prompt_en": "portrait, CoolNewThing"}
    _cd20 = field_candidates(_t20, ("name", "prompt_en"), 10, _voc20, [])
    _ws20 = [c["word"] for c in _cd20]
    assert "Windy" in _ws20 and "CoolNewThing" in _ws20, _ws20
    assert "meadow" not in _ws20, _ws20          # 词表匹配词（风景/草地）⇒ 不算新词
    assert "赛博朋克" not in _ws20, _ws20        # 词表标签名 ⇒ 不算新词
    assert "portrait" not in _ws20, _ws20        # 词表匹配词 ⇒ 不算新词
    assert all(c["field"] in ("name", "prompt_en") for c in _cd20), _cd20
    _cd20b = field_candidates(_t20, ("name",), 10, _voc20, ["Windy"])
    assert "Windy" not in [c["word"] for c in _cd20b], _cd20b          # 热点词 ⇒ 不算新词
    assert field_candidates({}, ("name",), 10, _voc20, []) == []

    # 21. 2026-09-18（用户确认方案 A）：取词优化——**两词短语命中 / 词形兜底 / 连字符归一 /
    #     新词成标签 / 严格新词闸门**（全部由 field_source_tags 承载）
    _fs = lambda _t, _lim=MAX_TAGS: field_source_tags(_t, _voc20, [], _lim)
    #   ① 名称里被空格切开的英文短语 → 合并后命中词表里的规范标签
    assert "低角度" in _fs({"name": "Low angle street fashion"}), _fs({"name": "Low angle street fashion"})
    assert _fs({"name": "Long exposure light trail"}, 6)[0] == "长曝光"
    #   注：一个匹配词被多个标签共用时（如 `golden hour` 同时属于 暖调 / 黄金时刻），
    #   取词按**词表顺序**取首个标签（`build_vocab` 的既有口径）——此处不另加断言以免锁死。
    #   ② 连字符 / 下划线归一后再命中词表
    assert "胶片" in _fs({"prompt_en": "film-grain, 35mm"})
    assert "胶片" in _fs({"prompt_en": "film_grain, 35mm"})
    #   ③ 词形兜底（复数）命中词表；且**不会**把 human 误当 man
    assert "人像" in _fs({"prompt_en": "portraits"}), _fs({"prompt_en": "portraits"})
    assert "人像" not in _fs({"prompt_en": "human being"}), _fs({"prompt_en": "human being"})
    #   ④ 新词成标签：中文 2~6 字 / 英文两词短语（提示词里本就是**一个 token**）
    assert _fs({"name": "小清新 手账涂鸦"}) == ["小清新", "手账涂鸦"], _fs({"name": "小清新 手账涂鸦"})
    assert _fs({"prompt_en": "moody atmosphere"}) == ["moody atmosphere"]
    #   ⑤ 严格闸门：单个英文词、样板词、超长中文、功能词开头 —— 都不成新标签
    assert _fs({"name": "Windy"}) == [], _fs({"name": "Windy"})
    assert "Windy" not in _fs({"name": "Windy meadow"}), _fs({"name": "Windy meadow"})
    assert _fs({"prompt_en": "masterpiece, 8K, HDR, aspect ratio, style"}) == []
    assert _fs({"name": "一个女孩 必须保留 超长中文短语示例"}) == []
    #   ⑥ allow_new=False ⇒ 回到旧"必须命中词表"口径（新词一律不产生）
    assert field_source_tags({"name": "小清新 手账涂鸦"}, _voc20, [], MAX_TAGS,
                             allow_new=False) == []
    assert "低角度" in field_source_tags({"name": "Low angle"}, _voc20, [], MAX_TAGS,
                                         allow_new=False)     # 命中词表的照样产出

    # 22. 2026-09-18 16:20（用户第 3 轮确认"兜底规则"）：
    #   「尽力而为，有就推荐、没有就算了；**但当热点词与领域+词表都为 0 时，字段取词必须推荐 1~3 个**」
    #   ① 另两来源有产出 ⇒ **不触发兜底**，取词允许为 0（严格闸门：单个英文词不收）
    _r22a = suggest({"name": "Boyfriend POV photography"}, D, ctx_names=["图像"],
                    field_fallback=True)
    assert tag_names(_r22a) == ["视觉"], _r22a
    assert all(t["dim"] != "取词" for t in _r22a["tags"]), _r22a
    #   ② 另两来源都为 0 ⇒ 自动切**兜底闸门**重算，**保证至少 1 个**（来源标注 `取词（兜底）`）
    _r22b = suggest({"name": "Boyfriend POV photography"}, D, ctx_names=[],
                    field_fallback=True)
    assert tag_names(_r22b), _r22b
    assert all(t["source"] == "取词（兜底）" for t in _r22b["tags"]), _r22b
    assert "Boyfriend" in tag_names(_r22b), _r22b
    #   ③ 取词自己已有产出（即使另两来源为 0）⇒ 不重复兜底
    _r22c = suggest({"name": "小清新"}, D, ctx_names=[], field_fallback=True)
    assert [t["source"] for t in _r22c["tags"]] == ["取词（新词）"], _r22c
    #   ④ 取词关（field_fallback=False，批量/离线默认口径）⇒ 不兜底、不产出
    _r22d = suggest({"name": "Boyfriend POV photography"}, D, ctx_names=[],
                    field_fallback=False)
    assert tag_names(_r22d) == [], _r22d
    #   ⑤ 兜底闸门只在"单词兜底"上放宽：样板词/占位符仍被挡（`BRAND NAME` 不是标签）
    assert field_source_tags({"prompt_en": "BRAND NAME"}, _voc20, [], MAX_TAGS,
                             relax_new=True) == []
    assert field_source_tags({"prompt_en": "Boyfriend"}, _voc20, [], MAX_TAGS,
                             relax_new=True) == ["Boyfriend"], "兜底闸门必须收单个英文词"

    print("[打标引擎] 显式标签/领域判定/词边界/配额(队列模型)/阈值/兜底/领域隔离/空输入/维度范围"
          "/全词典兜底/标题取词/字段取词/取词开关/取词闸门/策略顺序/热点词/命中闸门/新词候选"
          "/取词优化(短语命中·词形兜底·连字符归一·新词成标签·严格闸门)/兜底取词 通过")


if __name__ == "__main__":
    _selftest()
