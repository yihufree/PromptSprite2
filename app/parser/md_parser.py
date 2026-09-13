# -*- coding: utf-8 -*-
"""
md_parser.py - 内置手册 Markdown 解析器
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

针对《第007期_AI视频全视觉风格完整分类手册》固定格式设计：
  - `# 第X维度：…`        → 一级分类（L1，维度）；非"第X维度"的 H1（标题、附录）整段忽略
  - `## 一、写实影像类`    → 二级分类（L2，大类）
  - `### 1. 风格名称`     → 条目（其后含 1-9 数字字段正文）；无数字字段的 H3（第2-4维度索引分组）忽略
  - `1. **字段名**：内容`  → 条目字段；字段⑧完整提示词 特殊处理"中文版：/英文版："（含同行写法）

自测：python -m app.parser.md_parser
"""
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..models import Entry

# 一级分类(H1)过滤：仅"第X维度"开头的标题作为维度分类，其余（文档标题/附录）整段忽略
_DIM_PATTERN = re.compile(r"^第[一二三四五六七八九十百\d]+维度")

# 一级分类名称中的"第X维度："前缀（入库时移除，如 第一维度：按媒介… → 按媒介…）
_DIM_PREFIX = re.compile(r"^第[一二三四五六七八九十百\d]+维度\s*[:：]?\s*")

# 条目字段行：数字序号 + **字段名** + 冒号(可省) + 内容
_FIELD_PATTERN = re.compile(r"^\s*(\d+)\.\s*\*\*([^*]+?)\*\*\s*(?:[:：]\s*)?(.*)$")
_CN_PATTERN = re.compile(r"^\s*中文版\s*[:：]\s*(.*)$")
_EN_PATTERN = re.compile(r"^\s*英文版\s*[:：]\s*(.*)$")
# 标题编号前缀（"一、" / "1." / "1、" 等）
_NUM_PREFIX = re.compile(r"^\s*[一二三四五六七八九十百\d]+[\.、．]\s*")

# 索引条目行（维度 2-4 的索引内容）：
#   编号 + **名称** 特征/…（如 1. **拉美复古胶片** 特征：…）
_INDEX_BOLD = re.compile(r"^\s*\d+\.\s*\*\*([^*]+?)\*\*\s*[:：]?\s*(.*)$")
#   编号 + 名称：内容（如 1. 默片时代（1920s）：手工上色默片…）
_INDEX_NUM = re.compile(r"^\s*\d+\.\s*([^：:]+)[：:]\s*(.*)$")

# 数字序号 → Entry 字段名（⑧为提示词特殊字段）
_FIELD_MAP = {
    1: "name", 2: "intro", 3: "origin", 4: "features", 5: "scenes",
    6: "works", 7: "image_desc", 9: "image_plan",
}
_PROMPT_FIELD_NUM = 8


@dataclass
class CategoryNode:
    """分类节点（L1/L2 共用），条目挂在节点下"""
    name: str
    children: List["CategoryNode"] = field(default_factory=list)
    entries: List[Entry] = field(default_factory=list)


@dataclass
class ParsedManual:
    """解析结果：L1 分类列表 + 解析告警"""
    categories: List[CategoryNode] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def count_entries(self) -> int:
        """递归统计全部条目数"""
        total = 0
        stack = list(self.categories)
        while stack:
            node = stack.pop()
            total += len(node.entries)
            stack.extend(node.children)
        return total


# ---------------------------------------------------------------------- #
# 文本清洗
# ---------------------------------------------------------------------- #
def _unescape(text: str) -> str:
    r"""还原 Markdown 转义符（\& \. \- \_ \+ 等）"""
    if not text:
        return text
    for esc, raw in ((r"\&", "&"), (r"\.", "."), (r"\-", "-"),
                     (r"\_", "_"), (r"\+", "+"), (r"\(", "("), (r"\)", ")")):
        text = text.replace(esc, raw)
    return text


def clean_title(raw: str) -> str:
    """清洗标题：还原转义符 + 剥离编号前缀（如 '一、' '1.' '1、'）"""
    title = _unescape(raw.strip())
    m = _NUM_PREFIX.match(title)
    if m:
        title = title[m.end():]
    return title.strip()


def strip_dimension_prefix(title: str) -> str:
    """移除一级分类名称中的"第X维度："前缀（如 第一维度：按媒介… → 按媒介…）"""
    return _DIM_PREFIX.sub("", title)


def _append_line(existing: str, new: str) -> str:
    """追加一行文本（多行内容保留换行）"""
    if not existing:
        return new
    return existing + "\n" + new


# ---------------------------------------------------------------------- #
# 解析器（状态机）
# ---------------------------------------------------------------------- #
class _Parser:
    def __init__(self, l1_filter: Optional[Callable[[str], bool]]):
        self.manual = ParsedManual()
        self.l1_filter = l1_filter
        self.l1: Optional[CategoryNode] = None     # 当前 L1
        self.l2: Optional[CategoryNode] = None     # 当前 L2
        self.pending: Optional[Entry] = None       # 当前待定条目（H3 创建）
        self.active = False                        # pending 是否已被识别为完整条目
        self.last_key: Optional[str] = None        # 续行追加目标（字段键 / 'cn' / 'en'）
        self._index_buffer: List[str] = []         # 维度2-4 游离索引行缓冲

    # ---- 对外：解析整文 ----
    def parse(self, text: str) -> ParsedManual:
        for raw in text.splitlines():
            self._process(raw.rstrip())
        self._flush()
        return self.manual

    # ---- 行处理 ----
    def _process(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        if stripped.startswith("### "):
            self._flush()
            self.pending = Entry(name=clean_title(stripped[4:]))
            self.active = False
            self.last_key = None
        elif stripped.startswith("## "):
            self._flush()
            if self.l1 is None:  # 不在维度下的 H2（如"前置总说明"）整段忽略
                return
            node = CategoryNode(name=clean_title(stripped[3:]))
            self.l1.children.append(node)
            self.l2 = node
        elif stripped.startswith("# "):
            self._flush()
            name = clean_title(stripped[2:])
            if self.l1_filter is not None and not self.l1_filter(name):
                self.l1 = None  # 非维度 H1：本段内容全部忽略
                self.l2 = None
                return
            node = CategoryNode(name=strip_dimension_prefix(name))  # 移除"第X维度："前缀
            self.manual.categories.append(node)
            self.l1 = node
            self.l2 = None
        else:
            self._content_line(line)

    # ---- 条目正文行 ----
    def _content_line(self, line: str) -> None:
        m = _FIELD_PATTERN.match(line)
        if m and self.pending is not None:
            num = int(m.group(1))
            rest = _unescape(m.group(3))
            if num == _PROMPT_FIELD_NUM:
                self.active = True
                self._consume_prompt(rest)
                self.last_key = None
            elif num in _FIELD_MAP:
                self.active = True
                key = _FIELD_MAP[num]
                setattr(self.pending, key, rest.strip())
                self.last_key = key
            else:  # 未知字段号（如⑩导出教程）→ 仍视为条目内，忽略
                self.active = True
            return

        # 字段⑧ 子行：中文版/英文版（同样还原转义符）
        if self.pending is not None and self.active:
            mc = _CN_PATTERN.match(line)
            if mc:
                self.pending.prompt_cn = _append_line(self.pending.prompt_cn,
                                                      _unescape(mc.group(1).strip()))
                self.last_key = "cn"
                return
            me = _EN_PATTERN.match(line)
            if me:
                self.pending.prompt_en = _append_line(self.pending.prompt_en,
                                                      _unescape(me.group(1).strip()))
                self.last_key = "en"
                return
            if self.last_key:  # 普通续行 → 追加到当前字段
                self._append_to_pending(self.last_key, line.strip())
            return

        # 维度 2-4 的索引内容：
        #  - 处于 "### 分组" 内 → 文本追加到该分组条目介绍
        #  - 无分组的游离文本（编号条目/名录）→ 缓冲，到标题时统一转为条目
        text = _unescape(line.strip())
        if text in ("---", "***", "==="):  # 分隔线忽略
            return
        if self.pending is not None:
            self.pending.intro = _append_line(self.pending.intro, text)
        else:
            self._index_buffer.append(text)

    def _flush_index_buffer(self) -> None:
        """把游离索引行转为条目：编号条目（含 **名称**）逐条建，纯名录合并为一条"""
        buf = [ln for ln in self._index_buffer if ln]
        self._index_buffer = []
        if not buf:
            return
        target = self.l2 if self.l2 is not None else self.l1
        if target is None:
            return
        plain = []
        i = 0
        while i < len(buf):
            line = buf[i]
            mb = _INDEX_BOLD.match(line)
            mn = _INDEX_NUM.match(line)
            if mb or mn:
                self._flush_plain(plain, target)
                plain = []
                if mb:
                    name, rest = mb.group(1).strip(), mb.group(2).strip()
                else:
                    name, rest = mn.group(1).strip(), mn.group(2).strip()
                rest_lines = [rest] if rest else []
                i += 1
                # 收集续行（缩进的补充说明）
                while i < len(buf) and not (_INDEX_BOLD.match(buf[i])
                                            or _INDEX_NUM.match(buf[i])):
                    rest_lines.append(buf[i])
                    i += 1
                target.entries.append(
                    Entry(name=name, intro="\n".join(x for x in rest_lines if x)))
            else:
                plain.append(line)
                i += 1
        self._flush_plain(plain, target)

    @staticmethod
    def _flush_plain(lines, target) -> None:
        """纯文本名录（如导演列表）合并为一条"名录"条目"""
        lines = [ln for ln in lines if ln]
        if not lines:
            return
        target.entries.append(Entry(name="名录", intro="\n".join(lines)))

    def _consume_prompt(self, rest: str) -> None:
        """字段⑧ 同行内容：按 中文版：/英文版： 切分（支持同行与分行两种写法）"""
        if not rest.strip():
            return
        segs = re.split(r"(中文版\s*[:：]|英文版\s*[:：])", rest)
        if len(segs) == 1:  # 无标记 → 兜底归入中文版
            self.pending.prompt_cn = _append_line(self.pending.prompt_cn, segs[0].strip())
            return
        for i in range(1, len(segs), 2):
            marker, content = segs[i], (segs[i + 1] if i + 1 < len(segs) else "")
            if marker.startswith("中文版"):
                self.pending.prompt_cn = _append_line(self.pending.prompt_cn, content.strip())
            else:
                self.pending.prompt_en = _append_line(self.pending.prompt_en, content.strip())

    def _append_to_pending(self, key: str, text: str) -> None:
        text = _unescape(text.strip())
        if key == "cn":
            self.pending.prompt_cn = _append_line(self.pending.prompt_cn, text)
        elif key == "en":
            self.pending.prompt_en = _append_line(self.pending.prompt_en, text)
        else:
            setattr(self.pending, key, _append_line(getattr(self.pending, key), text))

    # ---- 结束当前待定条目 ----
    def _flush(self) -> None:
        if self.pending is not None:
            target = self.l2 if self.l2 is not None else self.l1
            if self.active:
                if target is not None:
                    target.entries.append(self.pending)
                else:
                    self.manual.warnings.append(f"条目 [{self.pending.name}] 无归属分类，已丢弃")
            elif self.pending.name or self.pending.intro:
                # 无数字字段的"### 分组"（维度2-4 索引分组）→ 作为索引条目保留
                if target is not None:
                    target.entries.append(self.pending)
        self._flush_index_buffer()
        self.pending = None
        self.active = False
        self.last_key = None


# ---------------------------------------------------------------------- #
# 对外接口
# ---------------------------------------------------------------------- #
def parse_markdown(text: str,
                   l1_filter: Optional[Callable[[str], bool]] = None) -> ParsedManual:
    """解析 Markdown 文本 → ParsedManual。

    默认 l1_filter 仅接收"第X维度"开头的 H1 作为一级分类；
    传入自定义过滤器可适配其他手册格式。
    """
    if l1_filter is None:
        l1_filter = lambda name: bool(_DIM_PATTERN.match(name))
    return _Parser(l1_filter).parse(text)


def parse_file(path: str,
               l1_filter: Optional[Callable[[str], bool]] = None) -> ParsedManual:
    """解析 Markdown 文件 → ParsedManual"""
    with open(path, encoding="utf-8") as f:
        return parse_markdown(f.read(), l1_filter)


def import_manual(db, manual: ParsedManual, domain_name: str = "视频",
                  progress_cb=None, link_domains: Optional[List[str]] = None) -> dict:
    """将解析结果导入数据库（按 领域→L1→L2→条目 建树）。

    参数：
      db: Database 实例
      manual: ParsedManual 解析结果
      domain_name: 目标根目录名（不存在则自动创建）
      progress_cb: 可选回调 (done, total, entry_name) 用于进度条
      link_domains: 一级分类同时关联的其他领域名列表（多对一共享），
                    默认仅关联 domain_name
    返回：{'entries': n, 'categories': n, 'categories_shared': m}

    2026-08-18（P0-2 修复）：改为"非破坏性合并"——分类按名称复用（不存在才新建），
    条目按（分类内）名称 upsert（存在则更新内容、保留收藏/图片，不存在则新增）。
    重复导入 / 内置手册版本升级不再清空任何现有数据，用户数据永远保留。
    """
    if link_domains is None:
        link_domains = [domain_name]
    domain_ids = []
    for name in link_domains:
        d = next((x for x in db.list_domains() if x["name"] == name), None)
        domain_ids.append(d["id"] if d else db.add_domain(name))

    total = manual.count_entries()
    done, cat_added, cat_used = 0, 0, 0
    for l1 in manual.categories:
        l1_id = _find_category(db, None, l1.name)
        if l1_id is None:
            l1_id = db.add_category(l1.name, domain_id=domain_ids[0])
            cat_added += 1
        else:
            cat_used += 1
        for did in domain_ids:  # 多对一共享：同一一级分类关联多个领域
            db.link_domain_category(did, l1_id)
        for e in l1.entries:  # 灵活层级：无 L2 时条目直接挂 L1
            _upsert_entry(db, l1_id, e)
            done += 1
            if progress_cb:
                progress_cb(done, total, e.name)
        for l2 in l1.children:
            l2_id = _find_category(db, l1_id, l2.name)
            if l2_id is None:
                l2_id = db.add_category(l2.name, parent_id=l1_id)
                cat_added += 1
            else:
                cat_used += 1
            for e in l2.entries:
                _upsert_entry(db, l2_id, e)
                done += 1
                if progress_cb:
                    progress_cb(done, total, e.name)
    return {"entries": done, "categories": cat_added, "categories_shared": cat_used}


def _find_category(db, parent_id, name):
    """按（父分类,名称）查找分类 id；不存在返回 None（合并导入用）"""
    for c in db.list_categories(parent_id=parent_id):
        if c["name"] == name:
            return c["id"]
    return None


def _upsert_entry(db, category_id, src: Entry) -> None:
    """条目按名称 upsert：已存在则更新内容（保留收藏/图片），不存在则新增（合并导入用）"""
    existing = next((e for e in db.list_entries(category_id) if e["name"] == src.name), None)
    if existing is None:
        src.category_id = category_id
        db.add_entry(src)
        return
    db.update_entry(Entry(
        id=existing["id"], category_id=category_id,
        name=src.name, intro=src.intro, origin=src.origin, features=src.features,
        scenes=src.scenes, works=src.works, image_desc=src.image_desc,
        prompt_cn=src.prompt_cn, prompt_en=src.prompt_en, image_plan=src.image_plan,
        image_path=existing["image_path"], is_favorite=existing["is_favorite"]))


# ---------------------------------------------------------------------- #
# 自测
# ---------------------------------------------------------------------- #
def _selftest() -> None:
    from .. import config as cfg

    manual = parse_file(cfg.builtin_manual_path())

    # 1. 四个维度（名称已移除"第X维度："前缀）
    assert len(manual.categories) == 4, f"维度数应为4，实际 {len(manual.categories)}"
    dims = [c.name for c in manual.categories]
    assert dims[0] == "按媒介&艺术载体总分类", dims[0]
    assert dims[1] == "按地域 / 国别分类（完整汇总）", dims[1]
    assert dims[2] == "按年代胶片 / 复古时期完整时间线", dims[2]
    assert dims[3] == "按导演 / 厂牌作者美学流派", dims[3]
    assert not any("维度" in n for n in dims), dims
    print("[1] 四个维度（去前缀） 通过")

    # 2. 各维度 L2 数：6 / 6 / 0 / 2
    l2_counts = [len(c.children) for c in manual.categories]
    assert l2_counts == [6, 6, 0, 2], f"L2 数量异常 {l2_counts}"
    print("[2] 各维度二级分类数(6/6/0/2) 通过")

    # 3. 条目总数：第一维度61 + 维度2-4索引条目 29 = 90
    total = manual.count_entries()
    assert total == 90, f"条目总数应为90，实际 {total}"
    print(f"[3] 条目总数 {total}（61 完整 + 29 索引） 通过")

    # 4. 首条条目 9 字段完整
    first = manual.categories[0].children[0].entries[0]
    assert first.name == "35mm电影胶片风", first.name
    assert first.intro and first.origin and first.features and first.scenes
    assert first.works and first.image_desc and first.image_plan
    assert first.prompt_cn.startswith("4K"), first.prompt_cn[:30]
    assert first.prompt_en.startswith("4K"), first.prompt_en[:30]
    assert "2.39:1" in first.prompt_cn and "\\." not in first.prompt_cn, "转义符未还原"
    print("[4] 首条条目9字段 通过")

    # 5. 同行"中文版："写法（低多边形 Low Poly 3D）
    low_poly = None
    stack = list(manual.categories)
    while stack and low_poly is None:
        node = stack.pop()
        low_poly = next((e for e in node.entries if "低多边形" in e.name), None)
        stack.extend(node.children)
    assert low_poly is not None, "未找到 低多边形 条目"
    assert low_poly.prompt_cn.startswith("4K") and "Low Poly" in low_poly.prompt_en
    print("[5] 同行中文版/分行英文版 通过")

    # 6. 无脏分类（标题/前置说明/需求落地方案 不入库）
    names = [c.name for c in manual.categories]
    assert not any(k in n for n in names for k in ("前置", "需求", "第007期"))
    print("[6] 无脏分类 通过")

    # 7. 第一维度各 L2 条目数合计 61
    per_l2 = [len(l2.entries) for l2 in manual.categories[0].children]
    assert sum(per_l2) == 61 and per_l2[0] == 12, per_l2
    print(f"[7] 第一维度各L2条目数 {per_l2} 通过")

    # 8. 维度2（地域/国别）索引条目：6 个 L2 均应有内容
    dim2 = manual.categories[1]
    per_dim2 = [len(l2.entries) for l2 in dim2.children]
    assert sum(per_dim2) == 13, f"维度2索引条目应13条，实际 {per_dim2}"
    assert per_dim2 == [3, 2, 4, 1, 1, 2], per_dim2
    # 传统国风 分组条目内容
    china = dim2.children[0]
    assert [e.name for e in china.entries] == ["传统国风", "近代国产写实", "现代国漫"]
    assert "水墨写意" in china.entries[0].intro
    # 港台细分：无编号名录合并为一条"名录"
    assert any(e.name == "名录" for e in dim2.children[4].entries)
    print(f"[8] 维度2地域/国别索引条目 {per_dim2} 通过")

    # 9. 维度3（年代）与维度4（导演）索引条目
    dim3 = manual.categories[2]
    assert len(dim3.entries) == 7, f"维度3年代条目应7条，实际 {len(dim3.entries)}"
    assert dim3.entries[0].name == "默片时代（1920s）"
    assert "《大都会》" in dim3.entries[0].intro
    dim4 = manual.categories[3]
    per_dim4 = [len(l2.entries) for l2 in dim4.children]
    assert sum(per_dim4) == 9, f"维度4导演条目应9条，实际 {per_dim4}"
    print(f"[9] 维度3年代条目7条、维度4导演条目{per_dim4} 通过")

    print(f"[统计] 维度 {len(manual.categories)}，分类 {sum(l2_counts)}，条目 {total}")
    print("=== MD 解析器自测通过 ===")


if __name__ == "__main__":
    _selftest()
