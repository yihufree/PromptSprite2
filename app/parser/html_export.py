# -*- coding: utf-8 -*-
"""
html_export.py - HTML 静态分享页导出
创建日期：2026-08-12（阶段六创建；阶段八适配全局分类+领域关联 v2）

生成单文件 index.html：按 领域→一级→二级 分组卡片、条目用 <details> 展开
9 字段详情、中英提示词带 JS 复制按钮、关联图片以 base64 内嵌。
"""
import base64
import html
import os
from datetime import datetime

from ..config import data_dir
from .json_io import _gather_categories, _ancestor_chain_cats

_ENTRY_FIELDS = [
    ("② 介绍", "intro"), ("③ 溯源", "origin"), ("④ 核心特征", "features"),
    ("⑤ 应用场景", "scenes"), ("⑥ 代表作", "works"),
    ("⑦ 代表高清配图", "image_desc"), ("⑩ 图像获取方案", "image_plan"),
    # 2026-08-18：字段编号调整，"⑨ 图像获取方案"改为"⑩ 图像获取方案"（与详情页一致）
]

_CSS = """
body{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:0;padding:24px;color:#333}
h1{font-size:24px;margin:0 0 4px}
.meta{color:#999;font-size:12px;margin-bottom:20px}
.dim{margin-top:26px}
.dim>h2{font-size:20px;color:#2E8B57;border-left:4px solid #2E8B57;padding-left:10px}
.cat{margin:14px 0 6px}
.cat>h3{font-size:16px;color:#555}
.cat.empty{color:#bbb;font-style:italic;font-size:13px}
details{background:#fff;border:1px solid #e4e7ec;border-radius:8px;margin:8px 0;padding:10px 14px}
summary{cursor:pointer;font-weight:bold;font-size:15px}
summary .star{color:#f5a623}
.fields{margin-top:8px}
.fields p{margin:6px 0}
.fields .lb{color:#888;font-size:12px}
pre{background:#f7f8fa;border-radius:6px;padding:10px;white-space:pre-wrap;word-break:break-all;margin:4px 0 8px}
img{max-width:360px;border-radius:8px;margin-top:6px}
button{background:#2E8B57;color:#fff;border:none;border-radius:5px;padding:4px 12px;margin-right:6px;cursor:pointer;font-size:12px}
button.gray{background:#8a94a6}
#toast{position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:#2E8B57;color:#fff;
       padding:8px 18px;border-radius:20px;display:none;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,.2)}
"""

_JS = """
function copyText(id){
  const t=document.getElementById(id).textContent;
  navigator.clipboard.writeText(t).then(()=>{
    const tb=document.getElementById('toast');tb.textContent='✅ 已复制';tb.style.display='block';
    setTimeout(()=>{tb.style.display='none';},1200);
  });
}
"""


def _image_tag(e) -> str:
    """条目关联图片 → base64 <img>；无图返回空串"""
    p = e.get("image_path") or ""
    if not p:
        return ""
    # 2026-08-18（P2-1 修复）：校验最终路径仍在 data/ 目录内，防止越界读取
    try:
        root = os.path.abspath(data_dir())
        full = os.path.abspath(os.path.join(root, p))
        if os.path.commonpath([root, full]) != root:
            return ""
    except Exception:
        return ""
    if not os.path.isfile(full):
        return ""
    try:
        ext = os.path.splitext(full)[1].lstrip(".").lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}.get(ext, "image/png")
        with open(full, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f'<img src="data:{mime};base64,{b64}" alt="配图"/>'
    except Exception:
        return ""


def _gallery_img_tag(g) -> str:
    """图集项 → <img>：本地图 base64 内嵌；外链图直接引用网址（导出后离线打开时外链可能不可用）"""
    if not isinstance(g, dict):
        return ""
    if g.get("kind") == "url":
        url = (g.get("source_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return ""
        return f'<img src="{html.escape(url)}" alt="图集外链" loading="lazy"/>'
    p = (g.get("path") or "").strip()
    if not p:
        return ""
    try:
        root = os.path.abspath(data_dir())
        full = os.path.abspath(os.path.join(root, p))
        if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
            return ""
        ext = os.path.splitext(full)[1].lstrip(".").lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}.get(ext, "image/png")
        with open(full, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f'<img src="data:{mime};base64,{b64}" alt="图集"/>'
    except Exception:
        return ""


def _entry_html(e) -> str:
    star = "★ " if e["is_favorite"] else ""
    body = ""
    for label, key in _ENTRY_FIELDS:
        val = (e.get(key) or "").strip()
        if val:
            body += f'<p><span class="lb">{label}：</span><br/>{html.escape(val)}</p>'
    # 2026-09-13（1-A-5 收尾）：自定义字段（显示名, 值），由 export_html 预先附到 _custom_rows
    for _lb, _v in (e.get("_custom_rows") or []):
        body += (f'<p><span class="lb">{html.escape(str(_lb))}：</span><br/>'
                 f'{html.escape(str(_v))}</p>')
    # 2026-09-13（1-C-4）：标签（同样由 export_html 预先附到 _tag_names）
    _tgs = [str(t) for t in (e.get("_tag_names") or []) if str(t).strip()]
    if _tgs:
        body += (f'<p><span class="lb">🏷 标签：</span>'
                 f'{html.escape("、".join(_tgs))}</p>')
    img = _image_tag(e)
    if img:
        body += f"<p>{img}</p>"
    # 2026-09-13（2-d）：图集（本地图 base64 内嵌、外链图直接用网址），由 export_html 预先附到 _gallery
    for _gi, _g in enumerate(e.get("_gallery") or []):
        tag = _gallery_img_tag(_g)
        if tag:
            body += f'<p><span class="lb">图集 {_gi + 1}：</span><br/>{tag}</p>'
    cn = html.escape(e.get("prompt_cn") or "")
    en = html.escape(e.get("prompt_en") or "")
    prompt = (
        f'<p><span class="lb">⑧ 中文版提示词：</span>'
        f'<button onclick="copyText(\'cn_{e["id"]}\')">复制中文</button></p>'
        f'<pre id="cn_{e["id"]}">{cn}</pre>'
        f'<p><span class="lb">⑨ 英文版提示词：</span>'  # 2026-08-18：字段编号调整，⑧英文版→⑨英文版
        f'<button onclick="copyText(\'en_{e["id"]}\')">复制英文</button></p>'
        f'<pre id="en_{e["id"]}">{en}</pre>'
    )
    return (f'<details><summary><span class="star">{star}</span>{html.escape(e["name"])}</summary>'
            f'<div class="fields">{body}{prompt}</div></details>')


def _section_html(title: str, entries) -> str:
    """单个分类的章节 HTML（含空分类提示）"""
    body = [f'<div class="cat"><h3>{html.escape(title)}</h3>']
    if entries:
        body.extend(_entry_html(e) for e in entries)
    else:
        body.append('<div class="cat empty">（空分类，暂无条目）</div>')
    body.append("</div>")
    return "".join(body)


def export_html(db, path, category_id=None) -> int:
    """导出全部（或指定分类子树）为单文件 HTML；返回导出的条目数"""
    sections = []   # [(维度标题或None, [(分类标题, 条目列表), …])]
    total = 0
    # 2026-09-13（1-A-5 收尾）：为条目附上"自定义字段（显示名, 值）"，供 _entry_html 展示
    custom_defs = [d for d in db.list_field_defs() if not d.get("is_builtin")]

    def _with_custom(es):
        for _e in es:
            rows = []
            try:
                _vals = {r["field_key"]: r["value_text"]
                         for r in db.list_entry_field_values(_e["id"])}
            except Exception:
                _vals = {}
            for _d in custom_defs:
                _v = (_vals.get(_d["field_key"]) or "").strip()
                if _v:
                    rows.append((_d["display_name"], _v))
            _e["_custom_rows"] = rows
            # 2026-09-13（1-C-4）：标签名（供 _entry_html 展示）
            try:
                _e["_tag_names"] = db.list_entry_tag_names(_e["id"])
            except Exception:
                _e["_tag_names"] = []
            # 2026-09-13（2-d）：图集（供 _entry_html 展示；本地图将 base64 内嵌）
            try:
                _e["_gallery"] = db.list_entry_images(_e["id"])
            except Exception:
                _e["_gallery"] = []
        return es

    if category_id is None:
        # 按领域分组：领域 → 一级(维度) → 二级(分类)
        for d in db.list_domains():
            domain_sections = []
            for l1 in db.list_categories(domain_id=d["id"], parent_id=None):
                for l2 in db.list_categories(parent_id=l1["id"]):
                    es = _with_custom(db.list_entries(l2["id"]))
                    domain_sections.append((f"{l1['name']} / {l2['name']}", es))
                    total += len(es)
            sections.append((d["name"], domain_sections))
    else:
        # 子树导出：以根分类为标题（含路径上下文）
        chain = _ancestor_chain_cats(db, category_id)
        root_title = " / ".join(c["name"] for c in chain)
        cat_ids = [c["id"] for c in _gather_categories(db, parent_id=category_id)]
        subs = [(c["name"], _with_custom(db.list_entries(c["id"]))) for c in
                _gather_categories(db, parent_id=category_id)]
        for _, es in subs:
            total += len(es)
        sections.append((root_title, subs))

    body_html = []
    for group_title, subs in sections:
        body_html.append(f'<div class="dim"><h2>{html.escape(group_title)}</h2>')
        for title, es in subs:
            body_html.append(_section_html(title, es))
        body_html.append("</div>")

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>PromptSprite 提示词分享页</title>
<style>{_CSS}</style>
</head>
<body>
<h1>🧩 PromptSprite 提示词分享页</h1>
<div class="meta">导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　共 {total} 条</div>
{''.join(body_html)}
<div id="toast"></div>
<script>{_JS}</script>
</body>
</html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return total
