"""Adapt the vendored doocs/md themes to Paper Studio's typed document.

Only the bundled, reviewed stylesheet is read here. User input is escaped text,
never CSS. Base + default + variant follow upstream themeApplicator.ts, then
variables and simple calculations are resolved and styles are inlined for export.
"""
from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup

VENDOR = Path(__file__).resolve().parent / "vendor" / "doocs-md"
OPEN_THEMES = {
    "doocs-classic": {"name": "开源经典", "variant": "default", "description": "居中题签 · 清晰层级 · 轻量引用"},
    "doocs-simple": {"name": "开源简洁", "variant": "simple", "description": "圆角题签 · 细线图片 · 通透正文"},
    "doocs-grace": {"name": "开源优雅", "variant": "grace", "description": "柔和标题 · 侧线引用 · 照片投影"},
}
SOURCE_URL = "https://github.com/doocs/md"


def text(value):
    return html.escape(value).replace("\n", "<br>")


def tag(name, body, attrs=""):
    return f'<{name}{attrs}>{body}</{name}>'


@lru_cache(maxsize=3)
def stylesheet(variant):
    names = ["base", "default"] + ([variant] if variant != "default" else [])
    return "\n".join((VENDOR / (name + ".css")).read_text(encoding="utf-8") for name in names)


def resolve_css(value, variables):
    # Resolve the innermost var() first (upstream has nested fallbacks).
    for _ in range(12):
        changed = re.sub(r"var\((--[\w-]+)(?:,\s*([^()]*))?\)", lambda m: variables.get(m[1], m[2] or ""), value)
        if changed == value:
            break
        value = changed
    value = value.replace("hsl(0 0% 22%)", "#383838")
    value = re.sub(r"calc\(\s*([\d.]+)(em|px)\s*\*\s*([\d.]+)\s*\)", lambda m: f'{float(m[1])*float(m[3]):g}{m[2]}', value)
    def mix(match):
        c, amount = match[1].lstrip("#"), float(match[2])/100
        if len(c) == 3:
            c = "".join(v*2 for v in c)
        return f'rgba({int(c[:2],16)},{int(c[2:4],16)},{int(c[4:],16)},{amount:g})'
    value = re.sub(r"color-mix\(in srgb,\s*(#[a-fA-F0-9]{3,6})\s+(\d+)%\s*,\s*transparent\)", mix, value)
    return value.strip().replace("!important", "").strip()


def inline_theme(soup, variant, doc):
    from .reference_style import FONTS
    accent=doc.get("aiLayout",{}).get("accent","#315e68")
    variables = {
        "--md-font-family": FONTS[doc.get("aiLayout",{}).get("font","sans")],
        "--md-font-size": f'{doc["fontSize"]:g}px', "--md-line-height": f'{doc["lineHeight"]:g}',
        "--md-primary-color": accent, "--md-link-color": accent, "--foreground": "0 0% 22%",
        "--md-block-spacing": f'{doc.get("blockSpacing",.75):g}',
        "--md-blockquote-background": "#f5f7f7", "--blockquote-background": "#f5f7f7",
    }
    css = re.sub(r"/\*.*?\*/", "", stylesheet(variant), flags=re.S)
    cascade = {}
    for order, (selectors, body) in enumerate(re.findall(r"([^{}]+)\{([^{}]+)\}", css)):
        rules = [(k.strip(), resolve_css(v, variables)) for k,v in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body)]
        for selector in selectors.split(","):
            selector = selector.strip()
            if not selector:
                continue
            specificity = (selector.count("#"), selector.count(".")+selector.count(":"), len(re.findall(r"(?:^|[\s>])(?:[a-z][\w-]*)",selector)), order)
            for node in soup.select(selector):
                styles = cascade.setdefault(id(node), {})
                for key,value in rules:
                    if value and not any(part in value for part in ["var(", "calc(", "color-mix("]):
                        if key not in styles or specificity >= styles[key][0]:
                            styles[key] = (specificity, value)
    for node in soup.find_all(True):
        style = {k:v[1] for k,v in cascade.get(id(node),{}).items()}
        # Our document metadata and photo portability layer override theme defaults.
        style.update({k.strip():v.strip() for k,v in re.findall(r"([\w-]+)\s*:\s*([^;]+)",node.get("style", ""))})
        if style:
            node["style"] = ";".join(k+":"+v for k,v in style.items())


def render_open(doc):
    variant = OPEN_THEMES[doc["theme"]]["variant"]
    cover = ""
    if doc["column"]:
        cover += tag("p", text(doc["column"]), ' style="margin:0 0 18px;font-size:11px;letter-spacing:2px;color:#77858a"')
    cover += tag("h1", text(doc["title"]), ' style="display:block;margin:0;padding:0;border:0;font-size:29px;font-weight:700;line-height:1.45;text-align:left;color:#26363c;text-shadow:none"')
    if doc["subtitle"]:
        cover += tag("p", text(doc["subtitle"]), ' style="margin:16px 0 0;font-size:13px;letter-spacing:.3px;line-height:1.85;color:#7b8588"')
    if doc["author"]:
        cover += tag("p", text(doc["author"]), ' style="margin:16px 0 0;font-size:11px;letter-spacing:.3px;color:#929a9d"')
    body = tag("header", cover, ' style="padding:14px 8px 20px"')
    for index, block in enumerate(doc["blocks"]):
        kind, value = block["type"], text(block["text"].strip() if doc.get("aiLayout") else block["text"])
        attrs = f' data-kind="{kind}" data-block-index="{index}"'
        if kind == "heading":
            body += tag("h2", value, attrs)
        elif kind == "quote":
            body += tag("blockquote", tag("p", value), attrs)
        elif kind == "image":
            photo = '<img src="'+html.escape(block["src"],quote=True)+'" alt="'+html.escape(block["text"],quote=True)+'" style="width:100%;height:auto">'
            if value:
                photo += tag("figcaption",value,' style="margin:10px 0 0;line-height:1.8;letter-spacing:.2px"')
            if block["src"] == "/assets/auditorium.jpg":
                photo += '<figcaption style="font-size:9px;line-height:1.8;margin-top:5px;color:#a0a6a8">示例配图：<a href="https://commons.wikimedia.org/wiki/File:Open_Seating_(16614957680).jpg">Phil Roeder</a> / <a href="https://creativecommons.org/licenses/by/2.0/">CC BY 2.0</a></figcaption>'
            body += tag("figure",photo,attrs)
        elif kind == "divider":
            body += '<hr'+attrs+'>'
            if value:
                body += tag("p",value)
        elif kind == "signature":
            body += tag("p",value,attrs+' style="font-size:11px;line-height:1.9;letter-spacing:.2px;color:#929a9d;text-align:right"')
        else:
            body += tag("p",value,attrs)
    soup = BeautifulSoup('<section id="output" style="box-sizing:border-box;background:#ffffff;padding:26px 22px 34px;overflow-wrap:break-word">'+body+'</section>',"html.parser")
    inline_theme(soup,variant,doc)
    from .ai_design import apply_design
    apply_design(soup,doc)
    return str(soup).replace("<br/>", "<br>")
