# -*- coding: utf-8 -*-
"""
tagger_engine.py - 打标引擎（阶段 1，2026-09-14）

**纯函数、无数据库依赖、零第三方依赖**（只用标准库 re），供三处复用：
  1. 阶段 1「预置标签」：离线脚本 `tag_builtin.py` 给主库批量打标；
  2. 阶段 2「批量智能打标」：软件内批量功能（同一引擎，结果 100% 一致）；
  3. 阶段 3「录入时自动推荐标签」：单条推荐。

判定链路（对应《可行性研究报告 v3》§3.2 / §5）：
    ① 显式标签直取（文本里的"标签：A、B"）—— 质量最高，最多占 3 个名额
    ② 单主领域判定：分类名/根目录/项目类别映射 → 文本关键词判定 → 兜底
    ③ 领域包词典打分：只装载「通用骨架 + 单主领域包」（兜底时只装通用骨架）
    ④ 配额：显式标签之外**每维最多 1 个**，合计 1~3 个；得分低于阈值者丢弃（宁缺勿滥）

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
# ---- 2026-09-15 19:30（批次 10，用户确认 Q2-b）：提示词专用切分符 ----
# 提示词是"逗号分隔的短语列表"，只按逗号/顿号/分号切，保留短语内部空格（如 "film grain"）
_PROMPT_SPLIT_RE = re.compile(r"[,，、;；]+")

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
    合法性：非空、长度 ≤ 24、不含换行。
    """
    found, seen = [], set()

    def _push(raw):
        s = " ".join(str(raw or "").split()).strip(" \t·-—:：")
        if not s or len(s) > TAG_MAX_LEN:
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
            _push(m.group(1))
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


# ---------------------------------------------------------------------- #
# ④ 配额合成（对外主入口）
# ---------------------------------------------------------------------- #
def suggest(texts, dict_data, ctx_names=None, max_tags: int = MAX_TAGS,
            min_score: float = MIN_SCORE, exclude_dims=None,
            fallback_global: bool = False) -> dict:
    """对一条条目给出标签建议。

    texts: {字段名: 文本}（字段见 FIELD_WEIGHTS；缺失字段视为空）
    ctx_names: 该条目的分类上下文名（根目录 / 项目类别 / 各级分类名），用于领域判定
    exclude_dims: 不参与词典打分的维度名集合（供"标签重点范围"勾选用；
      **"领域"与"显式"不受影响**——前者由领域判定给出，后者来自来源文本）
    fallback_global: 2026-09-15 18:15（批次 8-B，用户确认）新增的**可选**参数，默认 False。
      为 True 且**判不出领域**时，不再只装"通用骨架"，改为对全部领域包的匹配词打分
      （仍受 min_score 约束），结果来源标 `"词典兜底"`；判出领域时与 False 完全同行为。
      **仅 UI 单条推荐传 True；批量打标/离线打标保持默认** ⇒ 批量结果与改动前逐条一致。
    返回：
      {
        "domain": 领域名 or None,
        "domain_source": "分类名映射" / "文本关键词" / "兜底",
        "tags": [{"tag": 标签名, "dim": 维度, "score": 得分, "source": 来源}, ...],
      }
    标签顺序即优先级：显式标签 → 取词（名称/提示词，仅无显式标签时）→ 领域 → 其余维度（按得分降序）。
    """
    domain, dsrc = detect_domain(texts, dict_data, ctx_names)
    max_tags = max(1, int(max_tags))
    # 2026-09-15 18:15（批次 8-B）：仅在"判不出领域"时启用全词典兜底
    _fallback = bool(fallback_global) and domain is None

    picked, used_dims = [], set()

    def _add(tag, dim, score, source):
        if tag in {p["tag"] for p in picked}:
            return False
        picked.append({"tag": tag, "dim": dim, "score": round(float(score), 2),
                       "source": source})
        if dim != "显式":
            used_dims.add(dim)
        return True

    # ① 显式标签（最多 max_tags 个名额）
    explicit = extract_explicit_tags(texts, limit=max_tags)
    for t in explicit:
        _add(t, "显式", 1e9, "显式标签")

    # 2026-09-15 19:30（批次 10，用户确认）：**无显式标签**时，优先从
    #   ①名称 + ⑧中文提示词 + ⑨英文提示词 直接切词取标签（凑够 max_tags 即止）；
    #   不足时再由 ②领域 + ③词典 补足。有显式标签时跳过本步，行为与改动前一致。
    if not explicit:
        for t in field_fallback_tags(texts, ("name", "prompt_cn", "prompt_en"),
                                     max_tags):
            if not _add(t, "取词", 1e7, "标题/提示词"):
                continue
            if len(picked) >= max_tags:
                break

    # ② 领域标签（单主领域，占 1 个名额）
    if len(picked) < max_tags and domain:
        _add(domain, "领域", 1e8, dsrc)

    # ③ 词典候选（每维最多 1 个，按得分降序）
    if len(picked) < max_tags:
        scored = score_dimensions(texts, dict_data, domain, exclude_dims,
                                  fallback_global=_fallback)
        flat = []
        for dim, items in scored.items():
            if dim in used_dims:
                continue
            flat.append((items[0][0], dim, items[0][1]))
        flat.sort(key=lambda x: (-x[2], x[0]))
        for tag, dim, s in flat:
            if len(picked) >= max_tags:
                break
            if s < min_score:            # 阈值裁剪：宁缺勿滥
                break
            _add(tag, dim, s, "词典兜底" if _fallback else "词典")   # 2026-09-15 批次 8-B

    return {"domain": domain, "domain_source": dsrc, "tags": picked}


def tag_names(result) -> list:
    """从 suggest() 结果中取标签名列表。"""
    return [t["tag"] for t in (result or {}).get("tags") or []]


# ---------------------------------------------------------------------- #
# ⑤ 字段取词兜底（2026-09-15 批次 10，用户确认策略：无显式标签时优先取词）
# ---------------------------------------------------------------------- #
def field_fallback_tags(texts, fields, limit: int = MAX_TAGS) -> list:
    """2026-09-15 19:30（批次 10，用户确认）：从指定字段依次切词取标签。

    切分规则按字段区分（符合各自书写习惯）：
      - name（①名称/标题）：按 空白 + 中英标点 切（短语式标题）
      - prompt_cn / prompt_en（⑧⑨提示词）：只按 逗号/顿号/分号 切（列表式提示词，
        保留短语内部空格，如 "film grain" 作为一个词）
    统一后处理：中英边界再拆 → 英文去首尾停用词 → 长度 2~TAG_MAX_LEN → 去停用词 → 去重保序。
    按 fields 顺序依次取，取满 limit 即止。
    """
    out, seen = [], set()
    limit = max(1, int(limit))
    for field in fields:
        raw = str((texts or {}).get(field) or "")
        if not raw:
            continue
        split_re = _TITLE_SPLIT_RE if field == "name" else _PROMPT_SPLIT_RE
        for token in split_re.split(raw):
            for part in _TITLE_BOUNDARY_RE.split(token.strip()):
                s = part.strip(" \t·-—:：")
                if s and s.isascii():                  # 英文短语去首尾停用词
                    s = _strip_stopwords(s)
                if not s or len(s) < 2 or len(s) > TAG_MAX_LEN:
                    continue
                if s.lower() in _TITLE_STOPWORDS:
                    continue
                if s not in seen:
                    seen.add(s)
                    out.append(s)
                    if len(out) >= limit:
                        return out
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
    #   2026-09-15 19:30（批次 10）：无显式标签时先从 name 取词"随手记录"，再用通用骨架补足
    r3 = suggest({"name": "随手记录", "intro": "素材 参考"}, D, ctx_names=[])
    assert r3["domain"] is None and r3["domain_source"] == "兜底"
    assert "随手记录" in tag_names(r3), tag_names(r3)
    assert all(t["dim"] in ("用途", "取词") for t in r3["tags"]), r3["tags"]
    assert not ({"胶片", "赛博朋克", "3D渲染"} & set(tag_names(r3))), tag_names(r3)

    # 7. 领域包隔离：文学条目不得命中视觉包标签
    #   2026-09-15 19:30（批次 10）：无显式标签时先取词"短篇小说"，再领域+词典
    r4 = suggest({"name": "短篇小说", "intro": "第一人称 治愈 都市情感 剧本大纲"},
                 D, ctx_names=["文学"])
    assert r4["domain"] == "文学", r4
    tn = tag_names(r4)
    assert "短篇小说" in tn, tn                              # 取词优先
    assert ({"文学", "剧本大纲", "第一人称", "治愈", "都市情感"} & set(tn)), tn
    assert not ({"胶片", "赛博朋克", "3D渲染"} & set(tn)), tn

    # 8. 空输入不报错、可返回空标签
    r5 = suggest({}, D)
    assert r5["tags"] == [] and r5["domain"] is None, r5

    # 9. 维度范围（exclude_dims）：被排除的维度不出标签，但「领域」不受影响
    #   2026-09-15 19:30（批次 10）：加显式标签隔离取词步骤，专注测 exclude_dims
    _txt = {"name": "Editorial portrait poster",
            "intro": "标签：测标\n教程 素材",
            "prompt_en": "portrait, portrait, cinematic, film grain"}
    _all = suggest(_txt, D, ctx_names=["图像"])
    assert "题材主体" in {t["dim"] for t in _all["tags"]}, _all["tags"]
    _ex = suggest(_txt, D, ctx_names=["图像"], exclude_dims=["题材主体", "媒介工艺"])
    dims = {t["dim"] for t in _ex["tags"]}
    assert "题材主体" not in dims and "媒介工艺" not in dims, dims
    assert "领域" in dims, _ex["tags"]

    # 10. 批次 8-B：全词典兜底（fallback_global）——默认 False 行为与改动前一致
    #   2026-09-15 19:30（批次 10）：加显式标签以隔离"取词"步骤，专注测 fallback_global
    _t8b = {"name": "portrait film grain cinematic",
            "intro": "标签：测标",
            "prompt_en": "portrait, film grain"}
    _off8b = suggest(_t8b, D, ctx_names=[])
    assert _off8b["domain"] is None and _off8b["domain_source"] == "兜底", _off8b
    assert _off8b["tags"][0]["tag"] == "测标", _off8b["tags"]
    assert all(t["dim"] in ("用途", "显式") for t in _off8b["tags"]), _off8b["tags"]
    _on8b = suggest(_t8b, D, ctx_names=[], fallback_global=True)
    assert _on8b["tags"], _on8b
    assert all(t["source"] in ("词典兜底", "显式标签") for t in _on8b["tags"]), _on8b["tags"]
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

    # 13. 批次 10：suggest 无显式标签时优先取词（先于领域/词典）
    _t10 = {"name": "AI绘画 赛博朋克", "prompt_en": "film grain, portrait"}
    _r10 = suggest(_t10, D, ctx_names=[])
    assert _r10["tags"] and _r10["tags"][0]["source"] == "标题/提示词", _r10
    assert _r10["tags"][0]["dim"] == "取词", _r10
    assert {"AI", "绘画", "赛博朋克"} & set(tag_names(_r10)), tag_names(_r10)

    # 14. 批次 10：有显式标签时不取词，行为与改动前一致
    _t10b = {"name": "AI绘画 赛博朋克", "intro": "标签：显式标",
             "prompt_en": "film grain"}
    _r10b = suggest(_t10b, D, ctx_names=[])
    assert _r10b["tags"][0]["tag"] == "显式标" and _r10b["tags"][0]["dim"] == "显式", _r10b
    assert not any(t["dim"] == "取词" for t in _r10b["tags"]), _r10b

    print("[打标引擎] 显式标签/领域判定/词边界/配额/阈值/兜底/领域隔离/空输入/维度范围"
          "/全词典兜底/标题取词/字段取词/取词优先级 通过")


if __name__ == "__main__":
    _selftest()
