"""Read public article HTML and derive style evidence for local typesetting.

No browser profile, login cookies, third-party AI, source JavaScript, or remote
stylesheet is used. Public HTTPS addresses are pinned for each connection.
"""
from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import re
import socket
import ssl
import time
from collections import Counter
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from .reference_style import hex_color, normalize_profile, number
from .reference_layout import extract_layout, source_preview

MAX_HTML = 4_000_000
MAX_NODES = 20_000
INHERIT = {"font-family", "font-size", "font-weight", "color", "line-height", "letter-spacing", "text-align", "text-indent"}
VOID_COLOR = {"transparent", "inherit", "initial", "unset", "none", ""}


class ReferenceError(ValueError):
    def __init__(self, message, code="unreadable"):
        super().__init__(message)
        self.code = code


def validated_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ReferenceError("请粘贴一条完整的公开文章链接", "invalid_url")
    value = value.strip()
    if any(ord(c) < 32 for c in value):
        raise ReferenceError("文章链接包含无效字符", "invalid_url")
    parts = urlsplit(value)
    try:
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError()
        host = parts.hostname.rstrip('.').encode("idna").decode("ascii")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,251}[a-zA-Z0-9]", host):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ReferenceError("仅支持公开的 HTTPS 文章链接，不支持本机或内网地址", "invalid_url")
    return urlunsplit(("https", host, quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"), quote(parts.query, safe="=&%/?;:+,@!$'()*-._~"), ""))


def public_addresses(host):
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    except OSError as exc:
        raise ReferenceError("文章域名暂时无法访问，请稍后重试或导入 HTML", "network") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ReferenceError("链接指向本机或内网，已停止读取", "invalid_url")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address, timeout):
        super().__init__(hostname, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch_article(url):
    url = validated_url(url)
    deadline = time.monotonic() + 28
    for _ in range(4):
        parts = urlsplit(url)
        address = public_addresses(parts.hostname)[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReferenceError("读取文章超时，请改用 HTML 文件", "network")
        conn = PinnedHTTPS(parts.hostname, address, min(10, remaining))
        try:
            conn.request("GET", urlunsplit(("", "", parts.path, parts.query, "")), headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "gzip", "Connection": "close",
            })
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                target = response.getheader("Location")
                if not target:
                    raise ReferenceError("文章跳转地址无效", "unavailable")
                url = validated_url(urljoin(url, target))
                continue
            if response.status in {401, 403, 429}:
                raise ReferenceError("平台要求登录或验证，无法直接读取；可在浏览器打开后保存 HTML 再导入", "verification_required")
            if response.status != 200:
                raise ReferenceError(f"文章未能读取（HTTP {response.status}），请检查链接或导入 HTML", "unavailable")
            content_type = response.getheader("Content-Type", "")
            if not any(t in content_type.lower() for t in ["text/html", "application/xhtml"]):
                raise ReferenceError("这个链接返回的不是图文网页", "unreadable")
            chunks, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise ReferenceError("读取文章超时，请改用 HTML 文件", "network")
                chunk = response.read1(min(65536, MAX_HTML + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk); size += len(chunk)
                if size > MAX_HTML:
                    raise ReferenceError("文章文件超过 4 MB，请保存正文 HTML 后导入", "too_large")
            raw = b"".join(chunks)
            if response.getheader("Content-Encoding", "").lower() == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_HTML + 1)
                if len(raw) > MAX_HTML:
                    raise ReferenceError("解压后的文章超过 4 MB", "too_large")
            match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
            encoding = match[1] if match else "utf-8"
            try:
                text = raw.decode(encoding, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            return text, url
        except ReferenceError:
            raise
        except (OSError, http.client.HTTPException, EOFError) as exc:
            raise ReferenceError("网络读取失败，未能取得文章正文；可以导入 HTML 文件继续", "network") from exc
        finally:
            conn.close()
    raise ReferenceError("文章跳转次数过多，请使用最终文章地址", "unavailable")


def css_color(value):
    value = str(value or "").strip().lower()
    if value in VOID_COLOR:
        return ""
    direct = hex_color(value)
    if direct:
        return direct
    named = {"white": "#ffffff", "black": "#000000", "red": "#ff0000", "blue": "#0000ff", "gray": "#808080", "grey": "#808080", "green": "#008000"}
    if value in named:
        return named[value]
    match = re.search(r"rgba?\(\s*([\d.]+)(%)?\s*[, ]\s*([\d.]+)(%)?\s*[, ]\s*([\d.]+)(%)?(?:\s*[,/]\s*([\d.]+)(%)?)?\s*\)", value)
    if match:
        if match[7] and float(match[7]) / (100 if match[8] else 1) < .2:
            return ""
        values = [round(min(255, float(match[i]) * (2.55 if match[i+1] else 1))) for i in [1, 3, 5]]
        return "#" + "".join(f"{v:02x}" for v in values)
    return ""


def declarations(text):
    return {key.lower(): value.strip().replace("!important", "").strip() for key, value in re.findall(r"([\w-]+)\s*:\s*([^;{}]+)", text or "")}


def background_color(style):
    color = css_color(style.get("background-color")) or css_color(style.get("background"))
    if color:
        return color
    gradient = style.get("background-image", "")
    if "gradient(" in gradient:
        stops = re.findall(r"rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}", gradient)
        return css_color(stops[-1]) if stops else ""
    return ""


def px(value, fallback=0, base=16):
    match = re.match(r"^\s*(-?[\d.]+)\s*(px|em|rem|pt|%)?", str(value or ""))
    if not match:
        return fallback
    n = float(match[1]); unit = match[2]
    return n * ({"em": base, "rem": 16, "pt": 4/3, "%": base/100}.get(unit, 1))


def font_kind(value):
    if re.search(r"kai|楷|仿宋", value, re.I):
        return "hand"
    if re.search(r"song|宋|serif|times|georgia", value, re.I) and not re.search(r"sans-serif", value, re.I):
        return "serif"
    return "sans"


def box(style, key):
    values = style.get(key, "").split()
    if not values:
        values = ["0"]
    horizontal = values[1] if len(values) > 1 else values[0]
    vertical = values[0]
    return max(0, px(style.get(key + "-left", horizontal))), max(0, px(style.get(key + "-top", vertical)))


def role_from(style, role="paragraph"):
    padding, vertical = box(style, "padding")
    margin, space = box(style, "margin")
    borders = " ".join(style.get(k, "") for k in ["border-left", "border-bottom", "border", "border-color"])
    found = re.search(r"#[\da-fA-F]{3,8}|rgba?\([^)]*\)", borders)
    left = px(style.get("border-left-width", style.get("border-left", "")))
    bottom = px(style.get("border-bottom-width", style.get("border-bottom", "")))
    background = background_color(style)
    return {
        "size": px(style.get("font-size"), 28 if role == "cover" else (19 if role == "heading" else 15)),
        "weight": 700 if style.get("font-weight", "") in {"bold", "bolder"} else px(style.get("font-weight"), 400),
        "align": style.get("text-align", "left" if role in {"cover", "heading"} else "justify"),
        "font": font_kind(style.get("font-family", "")),
        "color": css_color(style.get("color")) or "#333333", "background": background,
        "inset": padding or margin or 24, "space": max(vertical, space, 8),
        "borderLeft": left, "borderBottom": bottom,
        "borderColor": css_color(found[0]) if found else "#d0d0d0",
        "radius": px(style.get("border-radius")),
    }


def analyze_article(raw_html, source_url="", source_name=""):
    if not isinstance(raw_html, str) or len(raw_html.encode("utf-8")) > MAX_HTML:
        raise ReferenceError("HTML 文件为空或超过 4 MB", "too_large")
    soup = BeautifulSoup(raw_html, "html.parser")
    all_nodes = soup.find_all(True, limit=MAX_NODES + 1)
    if len(all_nodes) > MAX_NODES:
        raise ReferenceError("页面结构过于复杂，请只保存文章正文后导入", "too_large")
    root = soup.find(id="js_content") or soup.select_one(".rich_media_content") or soup.find("article") or soup.find("main")
    body_text = soup.get_text(" ", strip=True)[:15000]
    if root is None and any(marker in body_text for marker in ["环境异常", "完成验证", "访问过于频繁", "请在微信客户端", "访问已停止", "请先登录", "内容已删除", "该内容已被发布者删除"]):
        raise ReferenceError("平台返回了登录、验证或不可用页面，没有取得文章正文。请打开原链接确认，或保存 HTML 后导入", "verification_required")
    root = root or soup.body or soup
    title_node = soup.find(id="activity-name") or soup.select_one('meta[property="og:title"]') or soup.find("h1") or soup.title
    title = ((title_node.get("content") or title_node.get_text(" ", strip=True)) if title_node else source_name) or "参考文章"
    style_map = {}
    warnings = []
    # Only bounded simple CSS rules, never scripts or linked stylesheets.
    rule_count = 0
    for sheet in soup.find_all("style", limit=8):
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]+)\}", sheet.get_text()[:100_000]):
            if rule_count >= 180:
                break
            for selector in selectors.split(",")[:12]:
                selector = selector.strip()
                if not selector or len(selector) > 150 or not re.fullmatch(r"[\w.#\-\s>]+", selector):
                    continue
                rule_count += 1
                try:
                    for node in soup.select(selector, limit=3000):
                        style_map.setdefault(id(node), {}).update(declarations(body))
                except Exception:
                    continue
    def direct(node):
        return {**style_map.get(id(node), {}), **declarations(node.get("style", ""))}
    def computed(node):
        chain = list(node.parents)[:35]
        result = {}
        for parent in reversed(chain):
            if isinstance(parent, Tag):
                result.update({k: v for k, v in direct(parent).items() if k in INHERIT})
        own = direct(node)
        result.update(own)
        if node.name in {"h1", "h2", "h3", "h4"}:
            if "font-size" not in own:
                result["font-size"] = {"h1": "32px", "h2": "24px", "h3": "20px", "h4": "18px"}[node.name]
            if "font-weight" not in own:
                result["font-weight"] = "700"
        if node.name in {"strong", "b"}:
            result.setdefault("font-weight", "700")
        return result
    def visual(node):
        result = computed(node)
        # Span-based headings and framed sections are common in WeChat HTML.
        text = node.get_text(" ", strip=True)
        for child in node.find_all(["span", "strong", "b"], limit=8):
            if len(child.get_text(" ", strip=True)) >= max(1, len(text)*.7):
                result.update(computed(child))
                if child.name in {"strong", "b"}:
                    result["font-weight"] = "700"
                break
        for parent in node.parents:
            if parent is root or not isinstance(parent, Tag):
                break
            if len(parent.get_text(" ", strip=True)) > max(180, len(text)*1.5 + 10):
                break
            for key, value in direct(parent).items():
                if key.startswith(("border", "padding", "background", "margin")):
                    result.setdefault(key, value)
        return result
    records = []
    candidates = root.find_all(["p", "h1", "h2", "h3", "h4", "blockquote"], limit=1500)
    if not candidates:
        candidates = [n for n in root.find_all(["section", "div"], limit=3000) if not n.find(["section", "div", "p"])]
    for node in candidates:
        if any(isinstance(p, Tag) and p.name in {"script", "style", "nav", "noscript"} for p in node.parents):
            continue
        style = visual(node)
        if style.get("display") == "none" or style.get("visibility") == "hidden":
            continue
        text = node.get_text(" ", strip=True)
        if len(text) < 2:
            continue
        records.append({"tag": node.name, "text": text[:500], "style": style, "node": node})
    full_text = root.get_text(" ", strip=True)
    if len(full_text) < 40 or len(records) < 2:
        raise ReferenceError("未能识别足够的文章正文，请使用完整推文链接，或保存正文 HTML 后导入", "no_article")
    prose = [r for r in records if len(r["text"]) >= 38 and r["tag"] not in {"h1", "h2", "h3", "h4", "blockquote"}]
    def representative(items):
        if not items:
            return {}
        counts = Counter(tuple(sorted(r["style"].items())) for r in items)
        return dict(counts.most_common(1)[0][0])
    paragraph_style = representative(prose or records)
    body_size = px(paragraph_style.get("font-size"), 15)
    heading_records = [r for r in records if len(r["text"]) <= 80 and (r["tag"] in {"h1", "h2", "h3", "h4"} or px(r["style"].get("font-size")) >= body_size+2 or (r["style"].get("font-weight") in {"bold", "700", "800", "900"} and len(r["text"]) <= 45))]
    quote_records = [r for r in records if r["tag"] == "blockquote" or (len(r["text"]) >= 25 and px(r["style"].get("border-left")) > 0)]
    heading_style = representative([r for r in heading_records if r["tag"] != "h1"] or heading_records)
    title_style = visual(title_node) if isinstance(title_node, Tag) and title_node.name != "meta" else {}
    if heading_records:
        early = [r for r in heading_records if r in records[:8]]
        if early:
            candidate = max(early, key=lambda r: px(r["style"].get("font-size"), 18))["style"]
            if not title_style or px(candidate.get("font-size")) > px(title_style.get("font-size")):
                title_style = candidate
    root_style = computed(root) if isinstance(root, Tag) else {}
    # A whole-article frame is often a section inside #js_content.
    for container in root.find_all(["section", "div"], limit=160):
        candidate = direct(container)
        if len(container.get_text(" ", strip=True)) >= len(full_text)*.7 and any(k in candidate for k in ["background-color", "background", "border", "border-width"]):
            root_style.update(candidate)
            break
    paper = ""
    for node in [root, *list(root.parents)[:8]]:
        if isinstance(node, Tag):
            paper = css_color(direct(node).get("background-color")) or css_color(direct(node).get("background"))
            if paper:
                break
    paper = css_color(root_style.get("background-color")) or css_color(root_style.get("background")) or paper
    palette = Counter()
    for record in records:
        for key in ["color", "background-color", "border-color"]:
            color = css_color(record["style"].get(key))
            if color:
                palette[color] += 1
        background = background_color(record["style"])
        if background:
            palette[background] += 1
    accent = css_color(heading_style.get("color")) or css_color(title_style.get("color")) or "#404040"
    heading_background = background_color(heading_style)
    if heading_background and accent and min(int(accent[i:i+2],16) for i in [1,3,5]) > 180:
        accent = heading_background
    if accent in {"#ffffff", "#333333", "#000000"}:
        accent = next((c for c, _ in palette.most_common() if max(int(c[i:i+2],16) for i in [1,3,5])-min(int(c[i:i+2],16) for i in [1,3,5]) > 35), accent)
    line = paragraph_style.get("line-height", "1.95")
    line_height = px(line, 1.95, body_size) / max(body_size, 12) if re.search(r"px|em|pt|%", line) else number(line, 1.95, 1.4, 2.5)
    images = root.find_all("img", limit=1000)
    image_style = computed(images[0]) if images else {}
    image_inset = 0
    if images:
        for parent in list(images[0].parents)[:6]:
            if parent is root:
                break
            if isinstance(parent, Tag):
                pad, _ = box(direct(parent), "padding")
                image_inset = max(image_inset, pad)
    quote_style = representative(quote_records)
    content_paper, content_border, content_border_color = "", 0, ""
    if prose:
        for parent in [prose[0]["node"], *list(prose[0]["node"].parents)[:20]]:
            if not isinstance(parent, Tag):
                continue
            parent_style = direct(parent)
            candidate_color = background_color(parent_style)
            if candidate_color:
                content_paper = candidate_color
                content_border = px(parent_style.get("border-width", parent_style.get("border", "")))
                border_value = parent_style.get("border-color", parent_style.get("border", ""))
                match = re.search(r"#[\da-fA-F]{3,8}|rgba?\([^)]*\)", border_value)
                content_border_color = css_color(match[0]) if match else ""
                break
    if not quote_style:
        quote_style = {"color": accent, "font-size": "17px", "border-left": f"3px solid {accent}", "padding": "18px"}
        warnings.append("未识别到独立引语，引用区块采用与文章配色一致的基础样式。")
    if not heading_records:
        heading_style = {"color": accent, "font-size": str(max(18, body_size+3))+"px", "font-weight": "700"}
        warnings.append("未识别到明确小标题，小标题使用基于正文字号推导的层级。")
    styled_count = sum(bool(r["node"].get("style") or style_map.get(id(r["node"]))) for r in records)
    if styled_count < 2:
        warnings.append("可读取的样式较少；外部样式表与脚本生成的效果没有纳入。")
    if re.search(r"<svg|background-image\s*:|linear-gradient|position\s*:\s*absolute|transform\s*:", raw_html, re.I):
        warnings.append("参考页包含装饰图片、SVG 或特殊定位；当前提取文字、间距和边框，不保证还原这些装饰。")
    layout, source_texts, layout_summary = extract_layout(root, direct, computed, visual)
    profile = normalize_profile({
        "title": title, "paper": paper or "#ffffff", "accent": accent,
        "contentPaper": content_paper or paper or "#ffffff", "contentBorder": content_border, "contentBorderColor": content_border_color,
        "lineHeight": line_height, "letterSpacing": px(paragraph_style.get("letter-spacing"), .3),
        "indent": px(paragraph_style.get("text-indent")) > 0,
        "cover": role_from(title_style, "cover"), "heading": role_from(heading_style, "heading"),
        "paragraph": role_from(paragraph_style), "quote": role_from(quote_style, "quote"),
        "imageInset": image_inset, "imageRadius": px(image_style.get("border-radius")),
        "captionAlign": "center", "frameWidth": px(root_style.get("border-width", root_style.get("border", ""))),
        "frameColor": css_color(root_style.get("border-color")) or accent,
        "layout": layout,
    })
    warnings = [w for w in warnings if not w.startswith(("未识别到独立引语", "未识别到明确小标题"))]
    warnings.append("保留原文容器、边距、宽度与图文位置；原文图片以占位展示，新稿只使用你的图片。文章标题默认保留在文稿信息中，避免另加一个原文没有的大标题。")
    return {"title": str(title)[:160], "url": source_url, "profile": profile,
            "stats": {"paragraphs": len(prose), "headings": len(heading_records), "images": len(images), "styledBlocks": styled_count},
            "palette": list(dict.fromkeys([profile["paper"], profile["contentPaper"], profile["accent"], profile["paragraph"]["color"], *[c for c,_ in palette.most_common(4)]]))[:6],
            "warnings": list(dict.fromkeys(warnings)),
            "evidence": {"heading": heading_records[0]["text"][:80] if heading_records else "", "quote": quote_records[0]["text"][:100] if quote_records else ""},
            "layoutRevision": 4, "layoutSummary": layout_summary, "sourcePreview": source_preview(layout, source_texts),
            "method": "HTML 结构与样式迁移（未调用 AI）"}
