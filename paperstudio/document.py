"""A deliberately small document model shared by preview and export.

There is no user HTML execution. Generated sections contain inline CSS because
copying a web page's classes to a third-party editor does not carry its stylesheet.
"""
from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass

from .reference_style import FONTS, normalize_profile
from .open_themes import OPEN_THEMES, render_open
from .ai_design import APPEARANCES, normalize_design

MAX_BLOCKS = 180
MAX_TEXT = 120_000
TYPES = {"heading", "paragraph", "quote", "image", "divider", "signature"}
THEMES = {
    **{key: {**theme, "accent": "#315e68", "paper": "#ffffff", "ink": "#383838", "use": "doocs/md 开源主题 · 已接入", "source": "https://github.com/doocs/md"} for key, theme in OPEN_THEMES.items()},
    "editorial": {"name": "人物特写", "accent": "#34352f", "paper": "#fcfbf7", "ink": "#31332e", "description": "大字开篇 · 通栏照片 · 独立引语", "use": "采访、人物、活动后记"},
    "forest": {"name": "摄影随记", "accent": "#55584e", "paper": "#fffefa", "ink": "#41433b", "description": "居中题签 · 窄栏正文 · 相纸留边", "use": "摄影、散文、生活记录"},
    "blue": {"name": "校园简报", "accent": "#323733", "paper": "#ffffff", "ink": "#303530", "description": "刊头分线 · 实心小标题 · 紧凑正文", "use": "新闻、通知、活动报道"},
}
BUILTIN_IMAGES = {"/assets/sample.png": "sample.png", "/assets/auditorium.jpg": "auditorium.jpg"}

SAMPLE = {
    "title": "演出结束，\n还有人没走", "subtitle": "我们留在后台，听他们把最后几句话讲完。", "column": "散场之后",
    "author": "采访组 · 示例稿", "theme": "editorial", "fontSize": 15, "lineHeight": 1.95,
    "blocks": [
        {"type": "paragraph", "text": "晚上九点多，观众已经走得差不多了。后台的椅子还没收齐，有人蹲在地上卷胶带，有人抱着外套，等同伴一起回去。"},
        {"type": "image", "src": "/assets/auditorium.jpg", "text": "观众席空下来了，后台还忙了一阵。（示意配图）"},
        {"type": "heading", "text": "上台前，他把手擦了好几遍"},
        {"type": "paragraph", "text": "问起最紧张的时刻，他没提独唱的那几分钟，反而记得候场时手心一直出汗。前面的节目快结束了，他还低头在裤子上擦了两下手。"},
        {"type": "quote", "text": "“其实上台前我还在想，\n万一第一句就唱错了怎么办。”"},
        {"type": "heading", "text": "那天没来得及说的话"},
        {"type": "paragraph", "text": "聊到最后，我们问他还有没有想补充的。他想了一会儿，说要谢谢帮他找电池的同学。“当时连名字都没问，等我回过头，人已经去忙别的了。”"},
        {"type": "divider", "text": ""},
        {"type": "signature", "text": "文 / 采访组\n本文为排版示例，人物与情节为虚构。"},
    ],
}


def string(value, limit, name):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{name}须为不超过 {limit} 字的文字")
    return value


def validate_image(src):
    src = string(src, 12_000_000, "图片")
    if src in BUILTIN_IMAGES:
        return src
    # Raster-only: untrusted SVG and external image URLs are not accepted.
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", src)
    if not match:
        raise ValueError("请使用本地 PNG、JPEG 或 WebP 图片")
    try:
        raw = base64.b64decode(match[2], validate=True)
    except ValueError as exc:
        raise ValueError("图片编码无效") from exc
    signatures = {"png": raw.startswith(b"\x89PNG\r\n\x1a\n"), "jpeg": raw.startswith(b"\xff\xd8\xff"), "webp": raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"}
    if not signatures[match[1]] or len(raw) > 8_000_000:
        raise ValueError("图片格式无效或超过 8 MB")
    return src


def normalize(data):
    if not isinstance(data, dict):
        raise ValueError("文稿格式无效")
    title = string(data.get("title", ""), 160, "标题").strip()
    if not title:
        raise ValueError("请填写标题")
    theme = data.get("theme", "editorial")
    if theme not in THEMES:
        raise ValueError("未知模板")
    try:
        size, line = float(data.get("fontSize", 15)), float(data.get("lineHeight", 1.95))
    except (TypeError, ValueError) as exc:
        raise ValueError("字号或行距无效") from exc
    if not 12 <= size <= 22 or not 1.4 <= line <= 2.5:
        raise ValueError("字号或行距超出范围")
    blocks = data.get("blocks")
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_BLOCKS:
        raise ValueError(f"文稿须包含 1–{MAX_BLOCKS} 个区块")
    clean, total = [], len(title)
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in TYPES:
            raise ValueError("未知区块类型")
        text = string(block.get("text", ""), 20_000, "区块内容")
        total += len(text)
        item = {"type": block["type"], "text": text}
        if block.get("appearance") is not None:
            if not isinstance(block["appearance"],str) or block["appearance"] not in APPEARANCES:
                raise ValueError("未知的段落强调方式")
            item["appearance"] = block["appearance"]
        if block["type"] == "image":
            item["src"] = validate_image(block.get("src", ""))
        clean.append(item)
    if total > MAX_TEXT:
        raise ValueError("文稿超过 12 万字")
    document = {"title": title, "subtitle": string(data.get("subtitle", ""), 500, "副标题"), "column": string(data.get("column", ""), 60, "栏目名"),
            "author": string(data.get("author", ""), 100, "作者"), "theme": theme,
            "fontSize": size, "lineHeight": line, "blocks": clean}
    from .reference_style import number
    document["blockSpacing"] = number(data.get("blockSpacing"), .65, .4, 1.5)
    if data.get("aiLayout") is not None:
        if theme not in OPEN_THEMES:
            raise ValueError("AI 设计需要使用开源主题")
        document["aiLayout"] = normalize_design(data["aiLayout"])
    if data.get("referenceStyle") is not None:
        document["referenceStyle"] = normalize_profile(data["referenceStyle"])
    return document


@dataclass
class Section:
    kind: str
    style: dict
    body: str
    text: str = ""
    layout: str = ""

    def html(self):
        style = ";".join(f"{key}:{value}" for key, value in self.style.items())
        return f'<section data-kind="{self.kind}" data-layout="{self.layout}" style="{style}">{self.body}</section>'

    def xiumi(self):
        camel = lambda s: re.sub(r"-([a-z])", lambda m: m[1].upper(), s)
        return {"style": {camel(k): str(v) for k, v in self.style.items()}, "text": self.body}


def escaped(value):
    return html.escape(value).replace("\n", "<br>")


def paragraph(value, style):
    return '<p style="' + ';'.join(f'{k}:{v}' for k, v in style.items()) + '">' + escaped(value) + '</p>'


def cover(doc, base):
    """Three actual compositions; none inserts placeholder English branding."""
    layout = doc["theme"]
    serif = "'Songti SC',STSong,SimSun,serif"
    if layout == "editorial":
        style = {**base, "padding": "43px 28px 29px"}
        body = paragraph(doc["column"], {"margin": "0 0 23px", "font-size": "11px", "letter-spacing": "2px", "color": "#77786f"}) if doc["column"] else ""
        body += paragraph(doc["title"], {"margin": "0 0 21px", "font-family": serif, "font-size": "34px", "font-weight": "700", "line-height": "1.42", "letter-spacing": "-0.5px"})
        if doc["subtitle"]:
            body += paragraph(doc["subtitle"], {"margin": "0 0 24px", "font-size": "13px", "line-height": "1.85", "color": "#73756c"})
        if doc["author"]:
            body += paragraph(doc["author"], {"margin": "0", "border-top": "1px solid #d8d9d0", "padding-top": "12px", "font-size": "11px", "color": "#7b7d73"})
    elif layout == "forest":
        style = {**base, "padding": "62px 40px 41px", "text-align": "center"}
        body = paragraph(doc["column"], {"margin": "0 0 26px", "font-size": "11px", "letter-spacing": "4px", "color": "#85877c"}) if doc["column"] else ""
        body += paragraph(doc["title"], {"margin": "0 0 25px", "font-family": serif, "font-size": "27px", "font-weight": "400", "line-height": "1.7", "letter-spacing": "2px"})
        if doc["subtitle"]:
            body += paragraph(doc["subtitle"], {"margin": "0 0 23px", "font-size": "12px", "line-height": "2", "color": "#8a8b81"})
        if doc["author"]:
            body += paragraph(doc["author"], {"margin": "0", "font-size": "10px", "letter-spacing": "1px", "color": "#8a8b81"})
    else:
        style = {**base, "padding": "24px 24px 21px", "border-top": "4px solid #323733"}
        body = paragraph(doc["column"], {"margin": "0 0 19px", "padding-bottom": "10px", "border-bottom": "1px solid #b9beb4", "font-size": "15px", "font-weight": "700", "letter-spacing": "1px"}) if doc["column"] else ""
        body += paragraph(doc["title"], {"margin": "0 0 12px", "font-size": "25px", "font-weight": "700", "line-height": "1.5", "letter-spacing": "0"})
        if doc["subtitle"]:
            body += paragraph(doc["subtitle"], {"margin": "0 0 13px", "font-size": "13px", "line-height": "1.75", "color": "#73796e"})
        if doc["author"]:
            body += paragraph(doc["author"], {"margin": "0", "font-size": "11px", "color": "#7b8176"})
    return Section("cover", style, body, doc["title"], layout)


def sections(data):
    doc = normalize(data)
    if doc.get("referenceStyle"):
        return reference_sections(doc)
    if doc["theme"] in OPEN_THEMES:
        return [Section("composition", {"box-sizing": "border-box", "background-color": "#ffffff"}, render_open(doc), doc["title"], doc["theme"])]
    t = THEMES[doc["theme"]]
    layout = doc["theme"]
    serif = "'Songti SC',STSong,SimSun,serif"
    base = {"box-sizing": "border-box", "background-color": t["paper"], "color": t["ink"],
            "font-family": "'Microsoft YaHei','PingFang SC',sans-serif", "font-size": f'{doc["fontSize"]:g}px',
            "line-height": f'{doc["lineHeight"]:g}', "letter-spacing": "0.3px", "overflow-wrap": "break-word"}
    result = [cover(doc, base)]
    inset = {"editorial": "28px", "forest": "39px", "blue": "24px"}[layout]
    for block in doc["blocks"]:
        typ, text = block["type"], block["text"]
        style = {**base, "padding": f"10px {inset}"}
        if typ == "heading":
            if layout == "editorial":
                style.update({"padding": "31px 28px 10px", "font-size": "20px", "font-weight": "700", "line-height": "1.6", "color": t["accent"]})
                body = paragraph(text, {"margin": "0", "font-family": serif})
            elif layout == "forest":
                style.update({"padding": "36px 39px 15px", "text-align": "center", "font-size": "18px", "font-weight": "400", "line-height": "1.8", "color": t["accent"]})
                body = paragraph(text, {"margin": "0", "font-family": serif, "letter-spacing": "1px"})
            else:
                style.update({"padding": "20px 24px 8px"})
                body = paragraph(text, {"margin": "0", "padding": "8px 12px", "font-size": "16px", "font-weight": "700", "line-height": "1.7", "background-color": t["accent"], "color": "#ffffff"})
        elif typ == "quote":
            if layout == "editorial":
                style.update({"padding": "27px 30px 27px 48px"})
                body = paragraph(text, {"margin": "0", "font-family": serif, "font-size": "21px", "line-height": "1.85", "color": t["accent"]})
            elif layout == "forest":
                style.update({"padding": "37px 43px", "text-align": "center"})
                body = paragraph(text, {"margin": "0", "font-family": serif, "font-size": "17px", "line-height": "2.2", "color": t["accent"]})
            else:
                style.update({"padding": "13px 24px"})
                body = paragraph(text, {"margin": "0", "padding": "14px 0", "border-top": "1px solid #cdd1c6", "border-bottom": "1px solid #cdd1c6", "font-size": "14px", "font-weight": "500", "line-height": "1.9"})
        elif typ == "image":
            style["padding"] = {"editorial": "23px 0 15px", "forest": "26px 18px 19px", "blue": "13px 24px 11px"}[layout]
            photo_style = "margin:0;" + ("padding:10px;background-color:#f0eee7;border:1px solid #dedfd5;" if layout == "forest" else "")
            body = f'<p style="{photo_style}"><img src="{html.escape(block["src"], quote=True)}" alt="{html.escape(text, quote=True)}" style="display:block;width:100%;height:auto;"></p>'
            if text:
                align = {"editorial": "left", "forest": "right", "blue": "left"}[layout]
                caption_inset = "28px" if layout == "editorial" else ("4px" if layout == "forest" else "0")
                body += paragraph(text, {"margin": "10px 0 0", "padding": f"0 {caption_inset}", "font-size": "11px", "line-height": "1.8", "color": "#878a80", "text-align": align})
            if block["src"] == "/assets/auditorium.jpg":
                body += '<p style="margin:5px 0 0;padding:0 12px;font-size:9px;line-height:1.7;color:#96998f;text-align:center;">示例配图：<a href="https://commons.wikimedia.org/wiki/File:Open_Seating_(16614957680).jpg" style="color:inherit;">Phil Roeder</a> / <a href="https://creativecommons.org/licenses/by/2.0/" style="color:inherit;">CC BY 2.0</a>，仅缩小尺寸</p>'
        elif typ == "divider":
            widths = {"editorial": "35px", "forest": "18px", "blue": "100%"}
            margin = "15px auto" if layout == "forest" else "14px 0"
            body = paragraph(text, {"margin": margin, "width": widths[layout], "border-top": "1px solid #cfd2c6", "font-size": "11px", "line-height": "1.8"})
        elif typ == "signature":
            style.update({"padding": {"editorial": "10px 28px 36px", "forest": "17px 39px 49px", "blue": "8px 24px 25px"}[layout], "font-size": "11px", "color": "#858b7c", "text-align": "center" if layout == "forest" else "left"})
            body = f'<p style="margin:0;">{escaped(text)}</p>'
        else:
            if layout == "forest":
                style["font-family"] = serif
                style["padding"] = "12px 39px"
            elif layout == "blue":
                style["padding"] = "7px 24px"
            body = paragraph(text, {"margin": "0", "text-align": "justify", "text-indent": "2em" if layout == "blue" else "0"})
        result.append(Section(typ, style, body, text, layout))
    return result


def reference_sections(doc):
    """Apply observed role styles to the user's own blocks, in their own order."""
    spec = doc["referenceStyle"]
    if spec.get("layout"):
        from .reference_layout import compose
        return [Section("composition", {"box-sizing": "border-box", "font-family": FONTS[spec["paragraph"]["font"]], "font-size": f'{doc["fontSize"]:g}px', "line-height": f'{doc["lineHeight"]:g}', "overflow-wrap": "anywhere"}, compose(doc), doc["title"], "reference-structure")]
    paper = spec["paper"]
    para = spec["paragraph"]
    base = {"box-sizing": "border-box", "background-color": paper, "color": para["color"],
            "font-family": FONTS[para["font"]], "font-size": f'{doc["fontSize"]:g}px',
            "line-height": f'{doc["lineHeight"]:g}', "letter-spacing": f'{spec["letterSpacing"]:g}px',
            "overflow-wrap": "break-word"}
    def styled(role, own_size=False):
        r = spec[role]
        style = {**base, "padding": f'{r["space"]:g}px {r["inset"]:g}px',
                 "text-align": r["align"], "font-family": FONTS[r["font"]], "font-weight": str(r["weight"])}
        if role != "paragraph" or own_size:
            style["font-size"] = f'{r["size"]:g}px'
        style["color"] = r["color"]
        if r["background"]:
            style["background-color"] = r["background"]
        if r["borderLeft"]:
            style["border-left"] = f'{r["borderLeft"]:g}px solid {r["borderColor"]}'
        if r["borderBottom"]:
            style["border-bottom"] = f'{r["borderBottom"]:g}px solid {r["borderColor"]}'
        if r["radius"]:
            style["border-radius"] = f'{r["radius"]:g}px'
        return style
    cover_style = styled("cover")
    cover_style["padding-top"] = f'{max(22,spec["cover"]["space"]):g}px'
    body = paragraph(doc["column"], {"margin": "0 0 15px", "font-size": "11px", "font-weight": "400"}) if doc["column"] else ""
    body += paragraph(doc["title"], {"margin": "0 0 16px", "line-height": "1.55"})
    if doc["subtitle"]:
        body += paragraph(doc["subtitle"], {"margin": "0 0 16px", "font-size": "13px", "font-weight": "400", "line-height": "1.9"})
    if doc["author"]:
        body += paragraph(doc["author"], {"margin": "0", "font-size": "11px", "font-weight": "400"})
    result = [Section("cover", cover_style, body, doc["title"], "reference")]
    base["background-color"] = spec["contentPaper"]
    for block in doc["blocks"]:
        kind, text = block["type"], block["text"]
        if kind in {"paragraph", "heading", "quote"}:
            style = styled(kind)
            p_style = {"margin": "0"}
            if kind == "paragraph" and spec["indent"]:
                p_style["text-indent"] = "2em"
            body = paragraph(text, p_style)
        elif kind == "image":
            style = {**base, "padding": f'16px {spec["imageInset"]:g}px'}
            body = '<p style="margin:0;"><img src="' + html.escape(block["src"], quote=True) + '" alt="' + html.escape(text, quote=True) + '" style="display:block;width:100%;height:auto;border-radius:' + f'{spec["imageRadius"]:g}px;' + '"></p>'
            if text:
                body += paragraph(text, {"margin": "9px 0 0", "font-size": "11px", "color": "#86887f", "text-align": spec["captionAlign"]})
            if block["src"] == "/assets/auditorium.jpg":
                body += '<p style="margin:5px 0 0;font-size:9px;text-align:center;color:#92958a;">示例配图：<a href="https://commons.wikimedia.org/wiki/File:Open_Seating_(16614957680).jpg" style="color:inherit;">Phil Roeder</a> / <a href="https://creativecommons.org/licenses/by/2.0/" style="color:inherit;">CC BY 2.0</a>，仅缩小尺寸</p>'
        elif kind == "divider":
            style = {**base, "padding": f'14px {para["inset"]:g}px'}
            body = paragraph(text, {"margin": "0", "border-top": f'1px solid {spec["accent"]}'})
        else:
            style = {**base, "padding": f'16px {para["inset"]:g}px 30px', "font-size": "11px", "text-align": "right"}
            body = paragraph(text, {"margin": "0"})
        result.append(Section(kind, style, body, text, "reference"))
    if spec["frameWidth"]:
        border = f'{spec["frameWidth"]:g}px solid {spec["frameColor"]}'
        for section in result:
            section.style.setdefault("border-left", border)
            section.style["border-right"] = border
        result[0].style["border-top"] = border
        result[-1].style["border-bottom"] = border
    if spec["contentBorder"] and len(result) > 1:
        border = f'{spec["contentBorder"]:g}px solid {spec["contentBorderColor"]}'
        for section in result[1:]:
            section.style.setdefault("border-left", border)
            section.style["border-right"] = border
        result[1].style["border-top"] = border
        result[-1].style["border-bottom"] = border
    return result


def render(data, sample_data_url=None):
    doc = normalize(data)
    body = "".join(s.html() for s in sections(doc))
    if sample_data_url:
        assets = sample_data_url if isinstance(sample_data_url, dict) else {key: sample_data_url for key in BUILTIN_IMAGES}
        for source, destination in assets.items():
            body = body.replace('src="' + source + '"', 'src="' + destination + '"')
    return body


def standalone(data, sample_data_url=None):
    doc = normalize(data)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(doc["title"])}</title>'
            '<style>body{margin:0;background:#eae9e3}main{max-width:677px;margin:0 auto}img{max-width:100%}</style>'
            '</head><body><main class="page">' + render(doc, sample_data_url) + '</main></body></html>')
