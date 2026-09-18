# -*- coding: utf-8 -*-
"""
auto_words.py - 智能自动取词词库（数据层，2026-09-16 批次 12-3）

**目标（用户要求 1）**：把"取词"过程中**未命中词表 / 热点词**的新词沉淀下来并累计频次；
- 达到「热点词阈值」→ **自动**加入热点词表（用户决策：达到阈值自动写入）；
- 达到「词表阈值」→ **提示**用户审核，用户选择确认后才加入**词表标签**（用户决策：需人工确认）。
形成闭环：取词发现新词 → 高频沉淀 → 升级为规范词 → 下次推荐直接命中 ⇒ **逐步减少对取词的依赖**。

**存储**：meta 键 `config.META_AUTO_WORDS`（JSON 对象）——**零 schema 变更、零新依赖**，
与既有"词表存 meta（`tag_dict`）""热点词存 meta（`tag_hotwords`）"完全同范式。
**设置**：meta 键 `config.META_AUTO_WORDS_CFG`（JSON 对象）：开关 / 采集范围 / 两个阈值 / 上限。

词条结构：
    {"n": 命中次数, "e": 出现过的**不同条目数**, "first": 首现时间, "last": 末现时间,
     "src": 来源字段(name/prompt_cn/prompt_en), "state": new/hot/tag/ignored, "eid": 上次条目 id}

**频率口径**：以 `e`（不同条目数）为主判据 —— 同一条目内重复出现只算 1；
用 `eid` 记忆"上次是哪条条目"，避免 T2 自动推荐对同一条目反复触发导致虚高。

自测：python -m app.auto_words
"""
import json
from datetime import datetime

from . import config, tagger

# 设置默认值（用户决策：阈值均可在设置中修改）
DEFAULT_CFG = {
    "enabled": True,        # 是否启用"自动取词词库"（采集）
    "include_t2": False,    # 采集范围是否包含 T2 自动推荐（默认只采集"点击推荐按钮"路径）
    "hot_th": 3,            # 热点词阈值：不同条目数达到即**自动**加入热点词表
    "tag_th": 5,            # 词表阈值：达到即**提示**用户审核，确认后加入词表标签
    "cap": 2000,            # 词库条数上限（超出按"末现时间"淘汰最冷）
}
_CFG_KEYS = ("enabled", "include_t2", "hot_th", "tag_th", "cap")
# 状态取值
ST_NEW, ST_HOT, ST_TAG, ST_IGNORED = "new", "hot", "tag", "ignored"


def _now() -> str:
    """2026-09-17（审核 R-4）：统一到 `config.now_str()`。"""
    return config.now_str()


# ---------------------------------------------------------------------- #
# 一、设置（meta：META_AUTO_WORDS_CFG）
# ---------------------------------------------------------------------- #
def normalize_cfg(cfg) -> dict:
    """规范化设置：未知键丢弃；阈值/上限非法（非正整数）一律回退默认。"""
    out = dict(DEFAULT_CFG)
    if not isinstance(cfg, dict):
        return out
    for _k in _CFG_KEYS:
        if _k not in cfg:
            continue
        _v = cfg[_k]
        if _k in ("enabled", "include_t2"):
            out[_k] = bool(_v)
        else:
            try:
                _n = int(_v)
            except (TypeError, ValueError):
                continue
            if _n >= 1:
                out[_k] = _n
    return out


def load_cfg(db) -> dict:
    """读取设置；无值 / JSON 损坏 / 结构非法 ⇒ 返回**默认设置**。"""
    try:
        raw = db.get_meta(config.META_AUTO_WORDS_CFG)
        return normalize_cfg(json.loads(raw) if raw else None)
    except Exception:
        return dict(DEFAULT_CFG)


def save_cfg(db, cfg) -> dict:
    """保存设置（先归一化再写 meta）；返回实际写入的规范化结果。"""
    out = normalize_cfg(cfg)
    db.set_meta(config.META_AUTO_WORDS_CFG, json.dumps(out, ensure_ascii=False))
    return out


# ---------------------------------------------------------------------- #
# 二、词库读写（meta：META_AUTO_WORDS）
# ---------------------------------------------------------------------- #
def load(db) -> dict:
    """读取词库 {词: 词条}；无值 / 损坏 ⇒ 返回 {}（不抛异常，避免影响推荐主流程）。"""
    try:
        raw = db.get_meta(config.META_AUTO_WORDS)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(db, data) -> None:
    """写回词库（先按上限瘦身，避免 meta 无限膨胀）。"""
    db.set_meta(config.META_AUTO_WORDS,
                json.dumps(trim(data, load_cfg(db).get("cap", DEFAULT_CFG["cap"])),
                           ensure_ascii=False))


def trim(data: dict, cap: int) -> dict:
    """超过上限时按"末现时间"新→旧保留前 cap 条，其余淘汰。"""
    if not isinstance(data, dict) or cap <= 0 or len(data) <= cap:
        return data if isinstance(data, dict) else {}
    items = sorted(data.items(), key=lambda kv: str((kv[1] or {}).get("last") or ""),
                   reverse=True)
    return dict(items[:int(cap)])


def list_words(db, order: str = "freq") -> list:
    """词库列表（供管理对话框）：[{"word","n","e","first","last","src","state"}]。

    order="freq"（默认，按不同条目数降序）/"time"（按末现时间降序）/"word"（按词升序）。
    """
    data = load(db)
    rows = []
    for _w, _it in data.items():
        _it = _it if isinstance(_it, dict) else {}
        rows.append({"word": _w,
                     "n": int(_it.get("n") or 0),
                     "e": int(_it.get("e") or 0),
                     "first": _it.get("first") or "",
                     "last": _it.get("last") or "",
                     "src": _it.get("src") or "",
                     "state": _it.get("state") or ST_NEW})
    if order == "time":
        rows.sort(key=lambda r: (r["last"], r["e"]), reverse=True)
    elif order == "word":
        rows.sort(key=lambda r: r["word"])
    else:
        rows.sort(key=lambda r: (-r["e"], -r["n"], r["word"]))
    return rows


def clear(db) -> None:
    """清空词库（设置里的"清空自动取词词库"）。"""
    db.set_meta(config.META_AUTO_WORDS, "")


# ---------------------------------------------------------------------- #
# 三、采集（由 UI 单条推荐路径调用）
# ---------------------------------------------------------------------- #
def record(db, words, entry_id=None) -> dict:
    """把"未命中词表/热点词的取词候选"记入词库，并按阈值自动提升为热点词。

    words: 可迭代的 (词, 来源字段) 二元组，或纯词字符串（来源留空）；
    entry_id: 当前条目 id（用于"不同条目数 e"的去重计数，可为 None）。
    返回（供 UI 提示）：{"added": [...], "hot": [...], "ready_tag": [...]}——
      added＝本次新增/累加的词；hot＝**本次自动加入热点词表**的词；ready_tag＝已达"词表阈值"待审核的词。
    """
    cfg = load_cfg(db)
    res = {"added": [], "hot": [], "ready_tag": []}
    if not cfg.get("enabled"):
        return res
    items = []
    for _w in (words or []):
        if isinstance(_w, (tuple, list)) and _w:
            _word, _src = str(_w[0] or "").strip(), str(_w[1] if len(_w) > 1 else "")
        else:
            _word, _src = str(_w or "").strip(), ""
        if _word:
            items.append((_word, _src))
    if not items:
        return res

    data = load(db)
    ts = _now()
    seen = set()
    for _word, _src in items:
        if _word in seen:
            continue
        seen.add(_word)
        it = data.get(_word)
        if not isinstance(it, dict):
            it = {"n": 0, "e": 0, "first": ts, "last": ts, "src": _src,
                  "state": ST_NEW, "eid": None}
        it["n"] = int(it.get("n") or 0) + 1
        # 「不同条目数」：与上次记的条目不同才 +1（避免 T2 对同一条目反复触发虚高）
        if entry_id is None or it.get("eid") != entry_id:
            it["e"] = int(it.get("e") or 0) + 1
            it["eid"] = entry_id
        it["last"] = ts
        if not it.get("first"):
            it["first"] = ts
        if not it.get("src") and _src:
            it["src"] = _src
        if it.get("state") not in (ST_HOT, ST_TAG, ST_IGNORED):
            it["state"] = ST_NEW
        data[_word] = it
        res["added"].append(_word)

    # 自动提升：达「热点词阈值」且未处理过 ⇒ 写入热点词表（用户决策：达到阈值自动写入）
    _hot_now = []
    _hth = int(cfg.get("hot_th") or DEFAULT_CFG["hot_th"])
    for _word, _it in data.items():
        if _it.get("state") == ST_NEW and int(_it.get("e") or 0) >= _hth:
            _hot_now.append(_word)
    if _hot_now:
        try:
            db.add_hotwords(_hot_now)
        except Exception:
            _hot_now = []
        for _word in _hot_now:
            data[_word]["state"] = ST_HOT
            data[_word]["hot_at"] = ts
    res["hot"] = sorted(_hot_now)

    # 待审核（词表阈值）：仅提示，不自动写
    _tth = int(cfg.get("tag_th") or DEFAULT_CFG["tag_th"])
    res["ready_tag"] = sorted(
        _w for _w, _it in data.items()
        if int(_it.get("e") or 0) >= _tth
        and _it.get("state") in (ST_NEW, ST_HOT))

    save(db, data)
    return res


# ---------------------------------------------------------------------- #
# 四、提升（管理对话框调用）
# ---------------------------------------------------------------------- #
def promote_hot(db, words) -> int:
    """把词加入「热点词表」并标记状态；返回**实际发生变更的词数**（2026-09-17 F-3 修复）。

    原实现返回"传入词数"，若词已不在词库（曾被删除 / 清空）提示数字会虚高。
    现只统计"确实在词库中、并被本次标记为热点词"的条数。
    """
    _ws = [str(_w or "").strip() for _w in (words or []) if str(_w or "").strip()]
    if not _ws:
        return 0
    try:
        db.add_hotwords(_ws)
    except Exception:
        return 0
    data = load(db)
    _ts = _now()
    _n = 0
    for _w in _ws:
        it = data.get(_w)
        if not isinstance(it, dict):
            continue                     # 词已不在词库 ⇒ 未产生任何变更，不计入
        if it.get("state") != ST_TAG:    # 已入词表的保持 ST_TAG（不回退）
            it["state"] = ST_HOT
        it["hot_at"] = _ts
        _n += 1
    save(db, data)
    return _n


def promote_tag(db, word, domain, dim, tag_name=None) -> tuple:
    """把词加入**词表标签**（需用户确认领域与维度）：返回 (是否成功, 说明)。

    写入位置：`domains[domain][dim][标签名] = [该词]`（domain 用"通用骨架"时写 `universal`）。
    已存在同名标签 ⇒ 只补匹配词（不覆盖既有匹配词）。
    """
    _w = str(word or "").strip()
    _d = str(domain or "").strip()
    _dim = str(dim or "").strip()
    _tag = str(tag_name or _w).strip()
    if not (_w and _dim and _tag):
        return False, "词 / 维度 / 标签名不能为空"
    try:
        data = tagger.load_dict(db)
    except Exception as exc:
        return False, "词表读取失败：%s" % exc
    if _d and _d != "通用骨架":
        if _d not in (data.get("domains") or {}):
            return False, "领域包不存在：%s" % _d
        layer = data["domains"][_d]
    else:
        layer = data.setdefault("universal", {})
    labels = layer.setdefault(_dim, {})
    words = labels.get(_tag)
    if isinstance(words, list):
        if _w not in words:
            words.append(_w)
    else:
        labels[_tag] = [_w]
    try:
        tagger.save_dict(db, data)
    except Exception as exc:
        return False, "词表写入失败：%s" % exc
    # 状态标记
    _it = load(db)
    if _w in _it:
        _it[_w]["state"] = ST_TAG
        _it[_w]["tag_at"] = _now()
        _it[_w]["tag_where"] = "%s / %s / %s" % (_d or "通用骨架", _dim, _tag)
        save(db, _it)
    return True, "已加入词表标签「%s」（%s / %s）" % (_tag, _d or "通用骨架", _dim)


def ignore(db, words) -> int:
    """把词标记为"忽略"（不再提示提升）。"""
    _ws = [str(_w or "").strip() for _w in (words or []) if str(_w or "").strip()]
    if not _ws:
        return 0
    data = load(db)
    for _w in _ws:
        if _w in data:
            data[_w]["state"] = ST_IGNORED
    save(db, data)
    return len(_ws)


def remove(db, words) -> int:
    """从词库删除词（彻底移除，日后仍会被重新采集）。"""
    _ws = [str(_w or "").strip() for _w in (words or []) if str(_w or "").strip()]
    if not _ws:
        return 0
    data = load(db)
    _n = 0
    for _w in _ws:
        if _w in data:
            data.pop(_w, None)
            _n += 1
    save(db, data)
    return _n


def stats(db) -> dict:
    """概览统计（设置页/对话框标题用）：总数 / 达标热点 / 达标词表 / 已提升数。"""
    cfg = load_cfg(db)
    data = load(db)
    _hth = int(cfg.get("hot_th") or DEFAULT_CFG["hot_th"])
    _tth = int(cfg.get("tag_th") or DEFAULT_CFG["tag_th"])
    done_hot = done_tag = ready_hot = ready_tag = 0
    for _w, _it in data.items():
        _it = _it if isinstance(_it, dict) else {}
        _e = int(_it.get("e") or 0)
        _st = _it.get("state") or ST_NEW
        if _st == ST_HOT:
            done_hot += 1
        elif _st == ST_TAG:
            done_tag += 1
        if _st in (ST_NEW, ST_HOT) and _e >= _tth:
            ready_tag += 1
        if _st == ST_NEW and _e >= _hth:
            ready_hot += 1
    return {"total": len(data), "hot_th": _hth, "tag_th": _tth,
            "done_hot": done_hot, "done_tag": done_tag,
            "ready_hot": ready_hot, "ready_tag": ready_tag}


# ---------------------------------------------------------------------- #
# 自测
# ---------------------------------------------------------------------- #
def _selftest() -> None:
    """数据层自测：设置在内存里跑（不依赖真实数据库的 set_meta/get_meta 之外的能力）。"""
    class _FakeDB:
        def __init__(self):
            self.meta = {}
            self.hot = []

        def get_meta(self, k):
            return self.meta.get(k)

        def set_meta(self, k, v):
            self.meta[k] = v

        def add_hotwords(self, ws):
            for w in ws:
                if w not in self.hot:
                    self.hot.append(w)

        def list_hotwords(self):
            return list(self.hot)

    db = _FakeDB()

    # 1. 设置：默认值 / 归一化（非法值与未知键）
    cfg = load_cfg(db)
    assert cfg == DEFAULT_CFG, cfg
    assert normalize_cfg({"hot_th": "坏", "tag_th": 0, "cap": -5})["hot_th"] == 3
    assert normalize_cfg({"tag_th": 0})["tag_th"] == 5 and normalize_cfg({"cap": -5})["cap"] == 2000
    assert normalize_cfg({"unknown": 1}) == DEFAULT_CFG
    db.set_meta(config.META_AUTO_WORDS_CFG, "{坏 JSON")
    assert load_cfg(db) == DEFAULT_CFG
    save_cfg(db, {"hot_th": 2, "tag_th": 4, "enabled": False})
    assert load_cfg(db)["hot_th"] == 2 and load_cfg(db)["enabled"] is False

    # 2. 采集：n / e 计数 + 同条目重复不增 e
    save_cfg(db, {"enabled": True, "hot_th": 3, "tag_th": 5})
    r1 = record(db, [("新词甲", "name"), ("新词乙", "prompt_cn")], entry_id=1)
    assert sorted(r1["added"]) == ["新词乙", "新词甲"], r1
    data = load(db)
    assert data["新词甲"]["n"] == 1 and data["新词甲"]["e"] == 1, data
    assert data["新词甲"]["src"] == "name", data
    record(db, [("新词甲", "name")], entry_id=1)              # 同一条目 → e 不增
    assert load(db)["新词甲"]["e"] == 1 and load(db)["新词甲"]["n"] == 2
    record(db, [("新词甲", "name")], entry_id=2)              # 换条目 → e+1
    assert load(db)["新词甲"]["e"] == 2

    # 3. 自动提升为热点词（e ≥ hot_th=3）
    r3 = record(db, [("新词甲", "name")], entry_id=3)
    assert r3["hot"] == ["新词甲"], r3
    assert "新词甲" in db.list_hotwords(), db.list_hotwords()
    assert load(db)["新词甲"]["state"] == ST_HOT

    # 4. 词表阈值：仅"待审核"，不自动写词表
    save_cfg(db, {"enabled": True, "hot_th": 99, "tag_th": 2})
    r4 = record(db, [("新词丙", "name")], entry_id=9)
    record(db, [("新词丙", "name")], entry_id=10)
    r4b = record(db, [("新词丙", "name")], entry_id=11)
    assert "新词丙" in r4b["ready_tag"], r4b
    assert db.meta.get(config.META_TAG_DICT) in (None, ""), "词表阈值不得自动写入词表"

    # 5. 忽略 / 删除 / 清空
    assert ignore(db, ["新词乙"]) == 1 and load(db)["新词乙"]["state"] == ST_IGNORED
    assert remove(db, ["新词乙"]) == 1 and "新词乙" not in load(db)
    assert clear(db) is None and load(db) == {}

    # 6. 上限淘汰（cap=2）：条数受控（"按末现时间淘汰"由第 7 项的 trim 断言精确校验——
    #    同一秒内写入时 last 相同，稳定排序保留先写入者，故此处只断言条数上限）
    save_cfg(db, {"cap": 2, "hot_th": 99, "tag_th": 99})
    for _w in ("甲", "乙", "丙"):
        record(db, [(_w, "name")], entry_id=None)
    _kept = load(db)
    assert len(_kept) == 2, _kept
    assert all(_w in _kept for _w in ("甲", "乙")), _kept

    # 7. trim / list_words / stats
    assert trim({"a": {"last": "2020-01-01 00:00:00"}, "b": {"last": "2030-01-01 00:00:00"}}, 1) \
        == {"b": {"last": "2030-01-01 00:00:00"}}
    assert list_words(db, "word") == sorted(list_words(db, "word"), key=lambda r: r["word"])
    _st = stats(db)
    assert set(_st) >= {"total", "hot_th", "tag_th", "done_hot", "done_tag",
                        "ready_hot", "ready_tag"}, _st

    # 8. 关闭开关 ⇒ 不采集
    save_cfg(db, {"enabled": False})
    assert record(db, [("不该进来", "name")], entry_id=1)["added"] == []
    assert "不该进来" not in load(db)

    print("[自动取词] 设置归一化/采集计数(n·e)/自动升级热点词/词表待审核/忽略·删除·清空"
          "/上限淘汰/列表与统计/开关 通过")


if __name__ == "__main__":
    _selftest()
