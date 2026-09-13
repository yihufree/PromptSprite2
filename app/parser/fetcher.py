# -*- coding: utf-8 -*-
"""
fetcher.py - 联网抓取层（第 5 期 T2 联网抓取 · 5-a）
创建日期：2026-09-14

职责（**纯函数、无界面、零第三方依赖**，全标准库）：
  1. `fetch_url()`        单 URL 文本抓取：仅 http/https、内置浏览器 UA、可选自定义头（Referer/Cookie）、
                          超时与重试、gzip/deflate 解压、编码探测（utf-8-sig→utf-8→gb18030）、
                          体积上限、**失败分类**（scheme/http/timeout/network/too_big/decode）、**反爬特征识别**；
  2. `detect_source_kind()` 源类型判定（html / markdown / csv / json / text）；
  3. GitHub 支持：`github_owner_repo()` / `github_raw_url()`（blob→raw）/ `github_list_dir()`（目录列举）/
                  `fetch_github_file()`（**多通道降级**：raw → contents API → git blob API）；
  4. 结构化辅助：`clean_layer_name()`（层级名清洗：去 emoji 前缀与"· N cases"后缀）、
                `complete_url()`（相对路径 → 绝对 URL，含 blob→raw 归一）。

设计原则（对应《T2 联网抓取施工计划报告 v2》第三/四章）：
  - 单点收口：所有网络访问只经本模块；
  - 只读、不写盘、不留登录态；不做 JS 渲染、不做风控绕过、不做站内遍历；
  - 自测**不依赖外网**：用本地 `http.server` + 注入假响应（`_http_json` 可被测试替换）。
"""
import base64
import gzip
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
DEFAULT_TIMEOUT = 15                       # 秒
DEFAULT_MAX_BYTES = 10 * 1024 * 1024       # 单次抓取上限 10MB
_ALLOWED_SCHEMES = ("http", "https")
DEFAULT_ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "text/plain;q=0.8,*/*;q=0.7")
# 反爬/风控页特征（体积异常小且命中即判为"疑似被拦截"）
_BLOCK_HINTS = ("安全验证", "人机验证", "验证码", "访问验证", "just a moment",
                "cf-browser-verification", "checking your browser", "captcha",
                "access denied", "request blocked", "enable javascript and cookies")
_BLOCK_MAX_LEN = 60000                     # 拦截页通常远小于正文

GITHUB_RAW = "https://raw.githubusercontent.com"
GITHUB_API = "https://api.github.com"

# 源类型判定用的顺序（越靠前越"结构化"）
_SOURCE_KINDS = ("json", "html", "markdown", "csv", "text")


# ---------------------------------------------------------------------- #
# 一、单 URL 抓取
# ---------------------------------------------------------------------- #
def _decode_bytes(data: bytes, charset: str = "") -> Tuple[str, str]:
    """字节 → 文本 + 实际编码（charset 优先，其后 utf-8-sig → utf-8 → gb18030）"""
    encs = []
    cs = (charset or "").strip().strip('"').lower()
    if cs and cs not in ("iso-8859-1",):     # 很多站点误报 latin-1，忽略之
        encs.append(cs)
    encs += ["utf-8-sig", "utf-8", "gb18030"]
    for enc in encs:
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "utf-8/replace"


def _decompress(data: bytes, content_encoding: str) -> bytes:
    """按 Content-Encoding 解压（标准库 urlopen **不会**自动解压，必须自己处理）"""
    ce = (content_encoding or "").lower()
    try:
        if "gzip" in ce:
            return gzip.decompress(data)
        if "deflate" in ce:
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
    except Exception:
        return data                            # 解压失败则原样返回（后续解码可能报错，交由上层提示）
    return data


def _header_charset(content_type: str) -> str:
    """从 Content-Type 里取 charset"""
    m = re.search(r"charset\s*=\s*[\"']?([\w\-]+)", content_type or "", re.I)
    return m.group(1) if m else ""


def _looks_blocked(text: str) -> bool:
    """疑似反爬/风控拦截页：体积小且命中特征词"""
    if not text or len(text) > _BLOCK_MAX_LEN:
        return False
    low = text.lower()
    return any(h.lower() in low for h in _BLOCK_HINTS)


def _new_result(url: str, channel: str = "direct") -> dict:
    return {"ok": False, "url": url, "final_url": url, "status": 0, "content_type": "",
            "encoding": "", "text": "", "bytes": 0, "error": "", "error_kind": "",
            "blocked_hint": False, "channel": channel}


def fetch_url(url: str, *, headers: Optional[dict] = None,
              timeout: float = DEFAULT_TIMEOUT,
              max_bytes: int = DEFAULT_MAX_BYTES,
              retries: int = 1) -> dict:
    """抓取 URL 文本；返回结果字典（见模块头注释）。

    - 仅允许 http/https；headers 可覆盖 User-Agent（默认浏览器 UA）并追加 Referer/Cookie 等；
    - 超时或临时网络错误按 `retries` 重试；体积超过 `max_bytes` 立即中止（不落盘、不缓存）；
    - 失败必带 `error`（中文可读）与 `error_kind`（scheme/http/timeout/network/too_big/decode/unknown）。
    """
    res = _new_result(url)
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme.lower() not in _ALLOWED_SCHEMES or not parts.netloc:
        res.update(error=f"仅支持 http/https 链接（当前：{parts.scheme or '空'}）",
                   error_kind="scheme")
        return res

    hdrs = {"User-Agent": DEFAULT_UA, "Accept": DEFAULT_ACCEPT,
            "Accept-Encoding": "gzip, deflate",     # 省流量；本模块自行解压
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    for k, v in (headers or {}).items():
        if v:
            hdrs[str(k)] = str(v)

    last_error, last_kind, last_status = "", "unknown", 0
    for attempt in range(max(1, int(retries) + 1)):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200) or 200
                ctype = resp.headers.get("Content-Type", "") or ""
                cenc = resp.headers.get("Content-Encoding", "") or ""
                final_url = resp.geturl() or url
                raw = resp.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    res.update(url=url, final_url=final_url, status=status,
                               content_type=ctype, bytes=len(raw),
                               error=f"内容过大（>{max_bytes // 1024 // 1024}MB），已中止",
                               error_kind="too_big")
                    return res
            data = _decompress(raw, cenc)
            text, enc = _decode_bytes(data, _header_charset(ctype))
            res.update(ok=True, url=url, final_url=final_url, status=status,
                       content_type=ctype, encoding=enc, text=text, bytes=len(data),
                       error="", error_kind="", blocked_hint=_looks_blocked(text))
            return res
        except urllib.error.HTTPError as exc:
            last_status = int(getattr(exc, "code", 0) or 0)
            last_error = f"HTTP {last_status} {getattr(exc, 'reason', '')}".strip()
            last_kind = "http"
            # 403/401/429 常见于反爬：尽力读一点正文做特征判断
            try:
                body = exc.read(200000).decode("utf-8", "replace")
                res["blocked_hint"] = _looks_blocked(body)
            except Exception:
                pass
            if last_status in (401, 403, 429):
                last_error += "（可能被反爬拦截：可在「高级」里填写 Referer / Cookie 后重试）"
            res.update(status=last_status)
            if last_status not in (408, 429, 500, 502, 503, 504):
                break                              # 4xx 属"确定性错误"，不重试
        except (socket.timeout, TimeoutError):
            last_error, last_kind = f"请求超时（>{timeout:g}s）", "timeout"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                last_error, last_kind = f"请求超时（>{timeout:g}s）", "timeout"
            else:
                last_error, last_kind = f"网络不可达：{reason}", "network"
        except Exception as exc:                   # noqa: BLE001
            last_error, last_kind = f"抓取失败：{exc}", "unknown"
        if attempt < retries:
            continue
    res.update(error=last_error or "抓取失败", error_kind=last_kind, status=last_status)
    return res


# ---------------------------------------------------------------------- #
# 二、源类型判定
# ---------------------------------------------------------------------- #
def detect_source_kind(url: str, content_type: str, text: str) -> str:
    """判定源类型：json / html / markdown / csv / text（供向导选择解析器）"""
    ctype = (content_type or "").lower()
    u = (url or "").lower()
    body = (text or "").lstrip()
    if "json" in ctype or body[:1] in ("{", "["):
        try:
            json.loads(body[:200000] or "null")
            return "json"
        except Exception:
            pass
    if "html" in ctype or "xml" in ctype or re.search(
            r"<(html|table|body|div|meta)\b", text or "", re.I):
        return "html"
    if u.endswith((".md", ".markdown")) or "markdown" in ctype:
        return "markdown"
    if "csv" in ctype or u.endswith((".csv", ".tsv")) or _looks_delimited(body):
        return "csv"
    if re.search(r"^\s*\|.*\|\s*$", text or "", re.M) or "#" in body[:2000]:
        return "markdown"
    return "text"


def _looks_delimited(text: str) -> bool:
    """前若干行是否"分隔符列数稳定"（判定 CSV/TSV 的轻量启发式）"""
    lines = [ln for ln in (text or "").splitlines()[:10] if ln.strip()]
    if len(lines) < 2:
        return False
    for d in ("\t", ",", ";", "|"):
        counts = [ln.count(d) for ln in lines]
        if counts[0] >= 1 and len(set(counts)) == 1:
            return True
    return False


# ---------------------------------------------------------------------- #
# 三、GitHub 支持（多通道降级）
# ---------------------------------------------------------------------- #
def github_owner_repo(url: str) -> Tuple[str, str, str, str]:
    """解析 GitHub 链接 → (owner, repo, ref, path)；无法解析返回 ("", "", "", "")。

    支持：`github.com/<o>/<r>/(blob|tree)/<ref>/<path>`、`github.com/<o>/<r>`（首页）、
          `raw.githubusercontent.com/<o>/<r>/<ref>/<path>`
    """
    try:
        p = urllib.parse.urlsplit(url or "")
        host = (p.hostname or "").lower()
        seg = [s for s in (p.path or "").split("/") if s]
        if host == "github.com":
            if len(seg) < 2:
                return ("", "", "", "")
            owner, repo = seg[0], seg[1].replace(".git", "")
            if len(seg) >= 4 and seg[2] in ("blob", "tree"):
                return (owner, repo, seg[3], "/".join(seg[4:]))
            return (owner, repo, "", "")
        if host == "raw.githubusercontent.com":
            if len(seg) < 4:
                return ("", "", "", "")
            return (seg[0], seg[1], seg[2], "/".join(seg[3:]))
    except Exception:
        pass
    return ("", "", "", "")


def github_raw_url(url: str) -> str:
    """把 GitHub `blob` 链接归一为 raw 链接（其它链接原样返回，含 `?raw=true`）"""
    owner, repo, ref, path = github_owner_repo(url)
    if owner and repo and ref and path:
        return f"{GITHUB_RAW}/{owner}/{repo}/{ref}/{path}"
    return url or ""


def _http_json(url: str, *, headers: Optional[dict] = None,
               timeout: float = DEFAULT_TIMEOUT) -> dict:
    """GET 并解析 JSON（GitHub API 用；**自测会替换本函数以避免真实联网**）"""
    hdrs = {"User-Agent": DEFAULT_UA, "Accept": "application/vnd.github+json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "null")


def github_list_dir(owner: str, repo: str, path: str = "", ref: str = "main",
                    *, timeout: float = DEFAULT_TIMEOUT) -> List[dict]:
    """列出仓库某目录（GitHub contents API）→ [{name, path, size, type, download_url, sha}]

    失败时抛 `ValueError`（带中文原因），由界面提示。
    """
    api = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}"
    if ref:
        api += f"?ref={urllib.parse.quote(ref)}"
    try:
        data = _http_json(api, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"目录列举失败：HTTP {exc.code}（私有仓库或路径不存在？）") from exc
    except Exception as exc:                        # noqa: BLE001
        raise ValueError(f"目录列举失败：{exc}") from exc
    if not isinstance(data, list):
        raise ValueError("目录列举失败：该路径不是目录")
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        out.append({"name": it.get("name") or "", "path": it.get("path") or "",
                    "size": int(it.get("size") or 0), "type": it.get("type") or "",
                    "download_url": it.get("download_url") or "", "sha": it.get("sha") or ""})
    return out


def fetch_github_file(owner: str, repo: str, path: str, ref: str = "main", *,
                      headers: Optional[dict] = None, timeout: float = DEFAULT_TIMEOUT,
                      max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """抓取仓库单文件：**多通道降级** raw → contents API(base64) → git blob API(base64)。

    返回与 `fetch_url` 同构的字典，另含 `channel`（raw/contents/blob）与 `tried`（已尝试通道列表）。
    """
    res = _new_result(f"{GITHUB_RAW}/{owner}/{repo}/{ref}/{path}", channel="raw")
    tried = []
    # 1) raw 直连
    raw_url = f"{GITHUB_RAW}/{owner}/{repo}/{ref}/{path}"
    r1 = fetch_url(raw_url, headers=headers, timeout=timeout, max_bytes=max_bytes)
    if r1.get("ok"):
        r1["channel"] = "raw"
        r1["tried"] = ["raw"]
        return r1
    tried.append(f"raw（{r1.get('error') or '失败'}）")
    # 2) contents API（<=1MB 时直接给 base64 内容；更大只给 sha）
    api = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}"
    if ref:
        api += f"?ref={urllib.parse.quote(ref)}"
    try:
        meta = _http_json(api, headers=headers, timeout=timeout)
    except Exception as exc:                        # noqa: BLE001
        tried.append(f"contents API（{exc}）")
        meta = None
    if isinstance(meta, dict):
        content = meta.get("content") or ""
        if content and (meta.get("encoding") or "base64") == "base64":
            try:
                data = base64.b64decode(content)
                text, enc = _decode_bytes(data)
                res.update(ok=True, status=200, content_type=meta.get("type") or "",
                           encoding=enc, text=text, bytes=len(data), channel="contents",
                           blocked_hint=_looks_blocked(text))
                res["tried"] = tried + ["contents API"]
                res["final_url"] = api
                return res
            except Exception as exc:                # noqa: BLE001
                tried.append(f"contents API 解码（{exc}）")
        sha = meta.get("sha") or ""
        if sha:
            # 3) git blob API（大文件专用）
            blob_api = f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs/{sha}"
            try:
                blob = _http_json(blob_api, headers=headers, timeout=timeout)
                data = base64.b64decode((blob or {}).get("content") or "")
                text, enc = _decode_bytes(data)
                res.update(ok=True, status=200, encoding=enc, text=text, bytes=len(data),
                           channel="blob", blocked_hint=_looks_blocked(text))
                res["tried"] = tried + ["contents API(meta)", "git blob API"]
                res["final_url"] = blob_api
                return res
            except Exception as exc:                # noqa: BLE001
                tried.append(f"git blob API（{exc}）")
        elif meta:
            tried.append("contents API（无 content/sha）")
    res.update(error="GitHub 抓取失败：" + "；".join(tried), error_kind="network",
               channel="", tried=tried)
    return res


# ---------------------------------------------------------------------- #
# 三·补、GitHub 批量抓取规格（2026-09-14，5-c）
#   限额：单文件 ≤ 2MB、一次 ≤ 20 个文件、合计 ≤ 20MB（超出时明确提示并可改选）
# ---------------------------------------------------------------------- #
BATCH_MAX_FILES = 20
BATCH_MAX_FILE_BYTES = 2 * 1024 * 1024
BATCH_MAX_TOTAL_BYTES = 20 * 1024 * 1024
BATCH_DEFAULT_EXTS = (".md", ".markdown", ".csv", ".tsv", ".json", ".html", ".htm", ".txt")


def batch_limit_label() -> str:
    """限额的中文说明（界面显示用）"""
    return "限额：单文件 ≤ 2MB、一次 ≤ 20 个文件、合计 ≤ 20MB"


def looks_like_github_dir(url: str) -> bool:
    """是否 GitHub 的"目录类"链接（仓库首页 / `tree/` 目录）；`blob`、raw 是文件"""
    try:
        p = urllib.parse.urlsplit(url or "")
        if (p.hostname or "").lower() != "github.com":
            return False
        seg = [s for s in (p.path or "").split("/") if s]
        if len(seg) < 2:
            return False
        if len(seg) == 2:
            return True                     # github.com/<owner>/<repo>
        return seg[2] == "tree"
    except Exception:
        return False


def github_dir_files(items: List[dict]) -> List[dict]:
    """目录列举结果 → 仅文件项（`type=file`），按名称排序"""
    out = [it for it in (items or [])
           if isinstance(it, dict) and (it.get("type") or "") == "file"]
    out.sort(key=lambda it: str(it.get("name") or "").lower())
    return out


def default_batch_selection(items: List[dict]) -> List[str]:
    """默认勾选：扩展名在 `BATCH_DEFAULT_EXTS` 内的文件 → 返回 path 列表"""
    out = []
    for it in github_dir_files(items):
        path = str(it.get("path") or "")
        ext = os.path.splitext(str(it.get("name") or ""))[1].lower()
        if path and ext in BATCH_DEFAULT_EXTS:
            out.append(path)
    return out


def check_batch_limits(items: List[dict], paths) -> dict:
    """批量限额校验 → {"ok", "count", "total_bytes", "errors": [中文原因]}"""
    by_path = {str(it.get("path") or ""): it for it in (items or []) if isinstance(it, dict)}
    chosen = []
    for p in (paths or []):
        p = str(p)
        it = by_path.get(p) or {"path": p, "name": os.path.basename(p), "size": 0}
        chosen.append(it)
    errors = []
    if not chosen:
        errors.append("至少选择一个文件。")
    if len(chosen) > BATCH_MAX_FILES:
        errors.append(f"一次最多 {BATCH_MAX_FILES} 个文件（当前 {len(chosen)} 个）。")
    big = [str(c.get("name") or c.get("path") or "") for c in chosen
           if int(c.get("size") or 0) > BATCH_MAX_FILE_BYTES]
    if big:
        errors.append("超过单文件上限 2MB：" + "、".join(big[:5]) + ("…" if len(big) > 5 else ""))
    total = sum(int(c.get("size") or 0) for c in chosen)
    if total > BATCH_MAX_TOTAL_BYTES:
        errors.append(f"所选合计 {total / 1024 / 1024:.1f}MB，超过单次上限 20MB。")
    return {"ok": not errors, "count": len(chosen), "total_bytes": total, "errors": errors}


# ---------------------------------------------------------------------- #
# 四、结构化辅助
# ---------------------------------------------------------------------- #
_EMOJI_PREFIX = re.compile(r"^[^\w\u4e00-\u9fff]+", re.UNICODE)
_COUNT_SUFFIX = re.compile(
    r"[（(【\[]?\s*[·•\-–—]?\s*\d+\s*(?:cases?|例|条|个)\s*[)）】\]]?\s*$", re.I)


def clean_layer_name(name: str) -> str:
    """层级名清洗：去首部 emoji/符号、去尾部"· N cases / （N 例）"等计数后缀、压空白。

    例：`🎨 UI与界面 · 22 cases` → `UI与界面`
    """
    s = (name or "").strip()
    if not s:
        return ""
    s = _EMOJI_PREFIX.sub("", s)
    prev = None
    while prev != s:                       # 可能"· 22 cases （520 例）"叠加
        prev = s
        s = _COUNT_SUFFIX.sub("", s).strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def complete_url(base_url: str, maybe_rel: str) -> str:
    """把（可能是相对路径的）链接补全为绝对 URL；GitHub blob 链接归一为 raw。

    - 空串 → ""；已是 http(s)/data: → 归一后返回；
    - `//host/...` → 补协议；`/abs` → 用 base 的 host；其余 → `urljoin(base, rel)`。
    """
    rel = (maybe_rel or "").strip()
    if not rel:
        return ""
    low = rel.lower()
    if low.startswith(("data:", "mailto:", "#")):
        return rel
    if low.startswith("http://") or low.startswith("https://"):
        return github_raw_url(rel)
    if rel.startswith("//"):
        scheme = (urllib.parse.urlsplit(base_url or "").scheme or "https")
        return github_raw_url(f"{scheme}:{rel}")
    try:
        joined = urllib.parse.urljoin(base_url or "", rel)
    except Exception:
        joined = rel
    return github_raw_url(joined)


def guess_layer_columns(headers: List[str]) -> dict:
    """按表头名猜测"4 列层级"映射（供向导自动预填；返回 {layer_key: 列下标}）"""
    hints = {"project": ("项目类别", "项目", "类别"),
             "domain": ("根目录", "领域", "目录"),
             "l1": ("一级",), "l2": ("二级",)}
    out = {}
    for key, words in hints.items():
        for i, h in enumerate(headers or []):
            hn = (h or "").lower()
            if any(w.lower() in hn for w in words):
                out[key] = i
                break
    return out


# ---------------------------------------------------------------------- #
# 自测（python -m app.parser.fetcher）——**不依赖外网**
# ---------------------------------------------------------------------- #
def _fetcher_selftest() -> None:      # pragma: no cover - 自测
    """5-a 自测：本地 HTTP 服务（含 gzip/超时/404/拦截页/需 UA·Referer）＋ 注入假响应（GitHub 多通道）。"""
    import gzip as _gz
    import http.server
    import threading

    TABLE_HTML = ("<html><body><table><tr><th>甲</th><th>乙</th></tr>"
                  "<tr><td>1</td><td>2</td></tr></table></body></html>")
    MD = "| 一级 | 二级 | 名称 |\n|---|---|---|\n| A | B | 条1 |\n"
    CSV = "一级,二级,名称\nA,B,条1\nA,B,条2\n"
    JSON_TXT = '{"a": 1, "b": [2, 3]}'
    BLOCKED = "<html><body>请完成安全验证后再访问</body></html>"

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):        # 静音
            pass

        def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):                 # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            ua = self.headers.get("User-Agent", "")
            ref = self.headers.get("Referer", "")
            if path == "/table.html":
                return self._send(TABLE_HTML.encode())
            if path == "/readme.md":
                return self._send(MD.encode(), "text/markdown; charset=utf-8")
            if path == "/data.csv":
                return self._send(CSV.encode(), "text/csv; charset=utf-8")
            if path == "/a.json":
                return self._send(JSON_TXT.encode(), "application/json")
            if path == "/gz.html":
                raw = _gz.compress(TABLE_HTML.encode())
                return self._send(raw, extra={"Content-Encoding": "gzip"})
            if path == "/big.txt":
                return self._send(b"x" * 5000, "text/plain")
            if path == "/slow":
                import time as _t
                _t.sleep(2.0)
                return self._send(b"late")
            if path == "/e404":
                return self._send(b"not found", code=404)
            if path == "/blocked.html":
                return self._send(BLOCKED.encode())
            if path == "/need-ua":
                if "Mozilla" in ua:
                    return self._send(TABLE_HTML.encode())
                return self._send(b"forbidden", code=403)
            if path == "/need-ref":
                if ref:
                    return self._send(MD.encode(), "text/markdown; charset=utf-8")
                return self._send(b"forbidden", code=403)
            return self._send(b"nope", code=404)

    class _Srv(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):   # 静音（超时用例会残留写失败）
            pass

    srv = _Srv(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        # ---- 1. 正常抓取 + 源类型判定 ----
        r = fetch_url(f"{base}/table.html")
        assert r["ok"] and r["status"] == 200 and "甲" in r["text"] and r["bytes"] > 0
        assert detect_source_kind(r["final_url"], r["content_type"], r["text"]) == "html"
        r_md = fetch_url(f"{base}/readme.md")
        assert detect_source_kind(f"{base}/readme.md", r_md["content_type"], r_md["text"]) == "markdown"
        r_csv = fetch_url(f"{base}/data.csv")
        assert detect_source_kind(f"{base}/data.csv", r_csv["content_type"], r_csv["text"]) == "csv"
        r_js = fetch_url(f"{base}/a.json")
        assert detect_source_kind(f"{base}/a.json", r_js["content_type"], r_js["text"]) == "json"

        # ---- 2. gzip 自动解压 ----
        r_gz = fetch_url(f"{base}/gz.html")
        assert r_gz["ok"] and "<table>" in r_gz["text"]

        # ---- 3. 体积上限 ----
        r_big = fetch_url(f"{base}/big.txt", max_bytes=1000)
        assert (not r_big["ok"]) and r_big["error_kind"] == "too_big"

        # ---- 4. 超时（重试 0 次，避免测试过慢）----
        r_to = fetch_url(f"{base}/slow", timeout=0.4, retries=0)
        assert (not r_to["ok"]) and r_to["error_kind"] == "timeout"

        # ---- 5. 404 → http 错误（不重试）----
        r_404 = fetch_url(f"{base}/e404")
        assert (not r_404["ok"]) and r_404["status"] == 404 and r_404["error_kind"] == "http"

        # ---- 6. 反爬拦截页 → ok=True 但 blocked_hint=True ----
        r_blk = fetch_url(f"{base}/blocked.html")
        assert r_blk["ok"] and r_blk["blocked_hint"] is True

        # ---- 7. 自定义头：内置 UA 满足 /need-ua；Referer 满足 /need-ref ----
        assert fetch_url(f"{base}/need-ua")["ok"] is True
        assert fetch_url(f"{base}/need-ref")["ok"] is False            # 无 Referer → 403
        r_ref = fetch_url(f"{base}/need-ref", headers={"Referer": base + "/"})
        assert r_ref["ok"] is True and "一级" in r_ref["text"]
        r_cookie = fetch_url(f"{base}/need-ref", headers={"Cookie": "a=1", "Referer": base})
        assert r_cookie["ok"] is True

        # ---- 8. 非 http/https 直接拒绝 ----
        r_sch = fetch_url("file:///C:/windows/win.ini")
        assert (not r_sch["ok"]) and r_sch["error_kind"] == "scheme"

        # ---- 9. GitHub 链接解析与 raw 归一 ----
        o, rp, rf, p = github_owner_repo("https://github.com/freestylefly/awesome-gpt-image-2/blob/main/docs/gallery.md")
        assert (o, rp, rf, p) == ("freestylefly", "awesome-gpt-image-2", "main", "docs/gallery.md")
        assert github_raw_url("https://github.com/a/b/blob/main/x/y.md") == \
            "https://raw.githubusercontent.com/a/b/main/x/y.md"
        assert github_raw_url("https://github.com/a/b") == "https://github.com/a/b"
        o2, rp2, rf2, p2 = github_owner_repo("https://raw.githubusercontent.com/a/b/main/c/d.json")
        assert (o2, rp2, rf2, p2) == ("a", "b", "main", "c/d.json")

        # ---- 10. 层级名清洗 ----
        assert clean_layer_name("🎨 UI与界面 · 22 cases") == "UI与界面"
        assert clean_layer_name("插画与艺术（18 例）") == "插画与艺术"
        assert clean_layer_name(" 海报与排版  ") == "海报与排版"
        assert clean_layer_name("图表与信息可视化 - 51 cases （520 例）") == "图表与信息可视化"
        assert clean_layer_name("") == ""

        # ---- 11. 链接补全（相对 → 绝对，含 blob→raw）----
        # 说明：GitHub blob 页面里的相对链接（如 assets/…）会**归一为 raw**，与历史做法一致
        assert complete_url("https://github.com/a/b/blob/main/docs/x.md", "img/1.png") == \
            "https://raw.githubusercontent.com/a/b/main/docs/img/1.png"
        assert complete_url("https://raw.githubusercontent.com/a/b/main/d/x.md",
                            "assets/p1.jpg") == \
            "https://raw.githubusercontent.com/a/b/main/d/assets/p1.jpg"
        assert complete_url("https://a.com/dir/page.html", "/i/p.png") == "https://a.com/i/p.png"
        assert complete_url("https://a.com/dir/page.html", "//cdn.x.com/p.png") == \
            "https://cdn.x.com/p.png"
        assert complete_url("https://a.com/", "https://github.com/a/b/blob/main/i.png") == \
            "https://raw.githubusercontent.com/a/b/main/i.png"
        assert complete_url("https://a.com/", "") == ""

        # ---- 12. GitHub 多通道降级（注入假响应，不联网）----
        payload = base64.b64encode(MD.encode()).decode()
        orig_fetch, orig_json = globals()["fetch_url"], globals()["_http_json"]
        try:
            # 12.1 raw 通道可用
            globals()["fetch_url"] = lambda *a, **k: dict(
                _new_result(a[0] if a else "", channel="raw"),
                ok=True, text=MD, status=200, bytes=len(MD))
            got = fetch_github_file("a", "b", "x.md", "main")
            assert got["ok"] and got["channel"] == "raw"
            # 12.2 raw 失败 → contents API（base64）
            globals()["fetch_url"] = lambda *a, **k: dict(
                _new_result(a[0] if a else "", channel="raw"), ok=False, error="raw 挂了")
            globals()["_http_json"] = lambda url, **k: (
                {"content": payload, "encoding": "base64", "sha": "s1", "type": "file"}
                if "/contents/" in url else {})
            got2 = fetch_github_file("a", "b", "x.md", "main")
            assert got2["ok"] and got2["channel"] == "contents" and "一级" in got2["text"]
            # 12.3 大文件：contents 无 content → git blob API
            globals()["_http_json"] = lambda url, **k: (
                {"content": "", "encoding": "none", "sha": "s2", "type": "file"}
                if "/contents/" in url else {"content": payload, "encoding": "base64"})
            got3 = fetch_github_file("a", "b", "big.md", "main")
            assert got3["ok"] and got3["channel"] == "blob"
            # 12.4 全通道失败 → 明确错误 + 已尝试通道列表
            def _boom(url, **k):
                raise RuntimeError("网络不可达")
            globals()["_http_json"] = _boom
            got4 = fetch_github_file("a", "b", "x.md", "main")
            assert (not got4["ok"]) and got4["tried"] and "raw" in got4["tried"][0]
            # 12.5 目录列举（假响应）
            globals()["_http_json"] = lambda url, **k: [
                {"name": "x.md", "path": "d/x.md", "size": 10, "type": "file",
                 "download_url": "http://x", "sha": "s"},
                {"name": "sub", "path": "d/sub", "size": 0, "type": "dir",
                 "download_url": None, "sha": "s2"}]
            items = github_list_dir("a", "b", "d", "main")
            assert len(items) == 2 and items[0]["name"] == "x.md" and items[1]["type"] == "dir"
            globals()["_http_json"] = lambda url, **k: {"message": "Not Found"}
            try:
                github_list_dir("a", "b", "nope", "main")
                raise AssertionError("非目录应抛 ValueError")
            except ValueError:
                pass
        finally:
            globals()["fetch_url"], globals()["_http_json"] = orig_fetch, orig_json

        # ---- 13. 表头→层级列猜测 ----
        assert guess_layer_columns(["项目类别", "根目录", "一级分类", "二级分类", "名称"]) == \
            {"project": 0, "domain": 1, "l1": 2, "l2": 3}
        assert guess_layer_columns(["名称", "提示词"]) == {}

        # ---- 14. GitHub 批量抓取规格（5-c）：目录判定 / 默认勾选 / 限额 ----
        assert looks_like_github_dir("https://github.com/a/b") is True
        assert looks_like_github_dir("https://github.com/a/b/tree/main/docs") is True
        assert looks_like_github_dir("https://github.com/a/b/blob/main/x.md") is False
        assert looks_like_github_dir("https://raw.githubusercontent.com/a/b/main/x.md") is False
        assert looks_like_github_dir("https://github.com/a/b/issues") is False
        batch_items = [
            {"name": "README.md", "path": "d/README.md", "size": 100, "type": "file"},
            {"name": "data.csv", "path": "d/data.csv", "size": 200, "type": "file"},
            {"name": "photo.png", "path": "d/photo.png", "size": 300, "type": "file"},
            {"name": "sub", "path": "d/sub", "size": 0, "type": "dir"},
        ]
        assert [it["name"] for it in github_dir_files(batch_items)] == \
            ["data.csv", "photo.png", "README.md"]                 # 仅文件、按名称排序
        assert default_batch_selection(batch_items) == ["d/data.csv", "d/README.md"]
        assert check_batch_limits(batch_items, ["d/README.md"])["ok"] is True
        assert check_batch_limits(batch_items, [])["ok"] is False      # 未勾选
        over_n = [{"name": f"f{i}.md", "path": f"d/f{i}.md", "size": 1, "type": "file"}
                  for i in range(BATCH_MAX_FILES + 1)]
        assert any("最多" in e for e in check_batch_limits(over_n,
                                                           [x["path"] for x in over_n])["errors"])
        big = [{"name": "huge.md", "path": "d/huge.md", "size": BATCH_MAX_FILE_BYTES + 1,
                "type": "file"}]
        assert any("2MB" in e for e in check_batch_limits(big, ["d/huge.md"])["errors"])
        many = [{"name": f"b{i}.md", "path": f"d/b{i}.md",
                 "size": BATCH_MAX_TOTAL_BYTES // 19, "type": "file"} for i in range(19)]
        assert any("20MB" in e for e in check_batch_limits(
            many + [{"name": "tail.md", "path": "d/tail.md", "size": BATCH_MAX_TOTAL_BYTES,
                     "type": "file"}], [x["path"] for x in many] + ["d/tail.md"])["errors"])

        print("[抓取层] 本地服务抓取（HTML/MD/CSV/JSON·gzip·限额·超时·404·拦截页识别·UA/Referer/Cookie）/"
              "协议白名单/GitHub 链接解析与 raw 归一/多通道降级（raw→contents→blob）/目录列举/"
              "层级名清洗/链接补全/层级列猜测/批量规格（目录判定·默认勾选·限额） 通过")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    _fetcher_selftest()
