"""A bounded, inert layout tree. It contains styles and slots, never source HTML.

Keep the article's nesting instead of averaging every paragraph into one theme.
Text and photographs are supplied separately at render time.
"""
from __future__ import annotations

import html
import re
from collections import Counter

from bs4 import Tag

LIMIT = 1800
ROLES = {"paragraph", "heading", "cover", "quote", "image", "signature", "divider"}
TAGS = {"section", "div", "p", "span", "h1", "h2", "h3", "h4", "blockquote", "figure", "figcaption", "ul", "ol", "li"}


def color(value):
    value = str(value).strip().lower()
    if re.fullmatch(r"#[a-f0-9]{3,8}", value) and len(value) in {4, 7, 9}:
        return value
    if value in {"transparent", "currentcolor", "white", "black", "red", "gray", "grey", "blue", "green"}:
        return value
    if re.fullmatch(r"rgba?\([\d\s.,%/]+\)", value):
        return value
    return ""


def lengths(value, maximum=120, negative=False, units="px|em|rem|%", count=4, auto=False):
    values = str(value).strip().split()
    if not 1 <= len(values) <= count:
        return ""
    for part in values:
        if auto and part == "auto":
            continue
        match = re.fullmatch(r"(-?(?:\d+(?:\.\d+)?|\.\d+))(" + units + r")?", part)
        if not match:
            return ""
        number = float(match[1])
        limit = min(maximum, 8) if match[2] in {"em", "rem"} else maximum
        if not (-min(80, limit) if negative else 0) <= number <= limit:
            return ""
        if number and match[2] is None:
            return ""
    return " ".join(values)


def safe_style(raw):
    if not isinstance(raw, dict):
        return {}
    result = {}
    enums = {
        "display": {"block", "inline", "inline-block", "flex", "inline-flex"},
        "text-align": {"left", "center", "right", "justify"},
        "font-style": {"normal", "italic"}, "font-weight": {"normal", "bold", *map(str, range(100, 901, 100))},
        "position": {"relative", "absolute"}, "overflow": {"hidden", "visible"},
        "flex-direction": {"row", "column", "row-reverse", "column-reverse"},
        "flex-wrap": {"wrap", "nowrap"}, "align-items": {"center", "flex-start", "flex-end", "stretch", "baseline"},
        "align-self": {"auto", "center", "flex-start", "flex-end", "stretch"},
        "justify-content": {"center", "flex-start", "flex-end", "space-between", "space-around", "space-evenly"},
        "vertical-align": {"top", "middle", "bottom", "baseline"},
        "box-sizing": {"border-box", "content-box"}, "text-decoration": {"none", "underline", "line-through"},
        "white-space": {"normal", "pre-wrap"},
    }
    for key, value in list(raw.items())[:100]:
        if not isinstance(key, str) or not isinstance(value, str) or len(value) > 400:
            continue
        value = value.strip().lower()
        if any(t in value for t in ["url", "expression", "var(", "\\", "<", ">", ";", "{", "}", "@"]):
            continue
        clean = ""
        if key in enums:
            clean = value if value in enums[key] else ""
        elif key in {"color", "background-color", "border-color", "border-top-color", "border-bottom-color", "border-left-color", "border-right-color"}:
            clean = color(value) or (" ".join(value.split()) if key == "border-color" and all(color(c) for c in value.split()) else "")
        elif key == "background":
            clean = color(value)
        elif key in {"background-image", "background"} and "gradient(" in value:
            clean = ""
        elif key.startswith(("margin", "padding")):
            clean = lengths(value, negative=key.startswith("margin"), auto=key.startswith("margin"))
        elif key in {"width", "max-width", "min-width", "height", "min-height", "max-height", "top", "right", "bottom", "left", "gap"}:
            clean = lengths(value, maximum=800 if "width" in key else 120, negative=key in {"top", "right", "bottom", "left"}, count=1, auto=True)
        elif key in {"font-size", "letter-spacing", "text-indent"}:
            clean = lengths(value, maximum=52 if key == "font-size" else 20, negative=key == "letter-spacing", count=1)
        elif key == "line-height":
            clean = value if re.fullmatch(r"(?:[0-4](?:\.\d+)?)", value) else lengths(value, maximum=120, count=1)
        elif key in {"border", "border-top", "border-right", "border-bottom", "border-left"}:
            match = re.fullmatch(r"([\d.]+px|0)\s+(solid|dashed|dotted|double)\s+(.+)", value)
            if match and float(match[1].removesuffix("px")) <= 12 and color(match[3]):
                clean = value
        elif key in {"border-width", "border-top-width", "border-right-width", "border-bottom-width", "border-left-width"}:
            clean = lengths(value, maximum=24)
        elif key == "border-radius":
            clean = lengths(value, maximum=100)
        elif key == "border-style":
            clean = value if all(v in {"none", "solid", "dashed", "dotted", "double"} for v in value.split()) else ""
        elif key == "flex":
            clean = value if value in {"none", "auto"} or re.fullmatch(r"\d{1,3}(?:\.\d+)? \d{1,3}(?:\.\d+)? (?:\d{1,3}(?:\.\d+)?(?:px|%)?|auto)", value) else ""
        elif key == "flex-flow":
            clean = value if re.fullmatch(r"(?:row|column)(?: (?:wrap|nowrap))?", value) else ""
        elif key in {"flex-grow", "flex-shrink"}:
            clean = value if re.fullmatch(r"\d{1,3}(?:\.\d+)?", value) else ""
        elif key == "transform":
            clean = value if re.fullmatch(r"(?:(?:rotate|translatex|translatey|translate|scale)\([-\d.,% pxdeg]+\)\s*){1,4}", value) else ""
        elif key == "box-shadow":
            clean = value if re.fullmatch(r"[-\d.px\s]+(?:#[a-f0-9]{3,8}|rgba?\([\d\s.,%]+\))", value) else ""
        elif key == "font-family":
            if re.search(r"kai|楷|仿宋", value):
                clean = "KaiTi,STKaiti,serif"
            elif re.search(r"song|宋|serif|times|georgia", value) and "sans-serif" not in value:
                clean = "STSong,SimSun,serif"
            else:
                clean = "'Microsoft YaHei','PingFang SC',sans-serif"
        # Gradients are data-only; no other CSS functions are admitted here.
        if key in {"background", "background-image"} and "gradient(" in value:
            if re.fullmatch(r"(?:linear|radial)-gradient\([\da-f#.,()%\sdegprcntobmlifhtx]+\)", value) and 2 <= len(re.findall(r"#[a-f0-9]{3,8}|rgba?\([^)]*\)", value)) <= 8:
                clean = value
        if clean:
            result[key] = clean
    return result


def normalize_layout(raw):
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("参考结构格式无效")
    count, seen = 0, set()
    def walk(node, depth=0):
        nonlocal count
        count += 1
        if count > LIMIT or depth > 40 or not isinstance(node, dict):
            raise ValueError("参考结构过于复杂")
        result = {"tag": node.get("tag") if node.get("tag") in TAGS else "section", "style": safe_style(node.get("style", {}))}
        if "slot" in node:
            slot = node["slot"]
            if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < LIMIT or slot in seen or node.get("role") not in ROLES:
                raise ValueError("参考内容位置无效")
            seen.add(slot)
            result.update(slot=slot, role=node["role"])
            if node["role"] == "image":
                try:
                    ratio = float(node.get("ratio", .6))
                except (TypeError, ValueError):
                    ratio = .6
                result["ratio"] = round(min(2, max(.15, ratio)), 3)
        else:
            children = node.get("children", [])
            if not isinstance(children, list):
                raise ValueError("参考容器格式无效")
            result["children"] = [walk(child, depth + 1) for child in children]
        return result
    tree = walk(raw.get("tree"))
    if not seen:
        raise ValueError("参考结构缺少内容位置")
    return {"version": 1, "tree": tree}


def extract_layout(root, direct, computed, visual):
    texts, role_counts, depth_max, containers, decorative_images = {}, Counter(), 0, 0, 0
    skipped = {"script", "style", "noscript", "iframe", "object", "embed", "input", "button", "video", "audio", "canvas", "svg", "mp-style-type"}
    def build(node, depth=0):
        nonlocal depth_max, containers, decorative_images
        if not isinstance(node, Tag) or depth > 38 or node.name in skipped:
            return None
        own = direct(node)
        if own.get("display") == "none":
            return None
        depth_max = max(depth_max, depth)
        text = node.get_text(" ", strip=True)
        terminal = node.name in {"p", "h1", "h2", "h3", "h4", "li", "figcaption"} and not node.find(["p", "h1", "h2", "h3", "h4", "img"])
        terminal = terminal or node.name == "blockquote" and not node.find("blockquote")
        # Some article editors use sections instead of paragraphs throughout.
        terminal = terminal or (node.name in {"section", "div"} and bool(text) and not node.find(["p", "section", "div", "img", "h1", "h2", "h3", "blockquote"]))
        if terminal and text:
            style = safe_style(computed(node))
            v = visual(node)
            for key in ["font-size", "color", "font-weight", "font-family", "letter-spacing", "line-height", "text-align"]:
                if key in v:
                    style.update(safe_style({key: v[key]}))
            role = "paragraph"
            bold = v.get("font-weight") in {"bold", "700", "800", "900"}
            if node.name == "h1":
                role = "cover"
            elif node.name in {"h2", "h3", "h4"} or (len(text) <= 85 and bold and not re.match(r"^(问|答|Q|A)[：:.\s]", text)):
                role = "heading"
            elif node.name == "blockquote":
                role = "quote"
            elif len(text) <= 100 and ("编辑" in text or "审核" in text or "供稿" in text) and v.get("text-align") == "right":
                role = "signature"
            slot = len(texts)
            texts[slot] = text
            role_counts[role] += 1
            return {"tag": node.name if node.name in TAGS else "p", "style": style, "slot": slot, "role": role}
        if node.name == "img":
            # Tiny ornamental images must never consume a user's photograph.
            for parent in [node, *list(node.parents)[:3]]:
                width = re.fullmatch(r"([\d.]+)px", direct(parent).get("width", "")) if isinstance(parent, Tag) else None
                if width and float(width[1]) <= 48:
                    decorative_images += 1
                    return {"tag": "section", "style": {"height": "8px"}, "children": []}
            try:
                ratio = float(node.get("data-ratio") or float(node.get("height", 0)) / max(1, float(node.get("width", 1))) or .6)
            except (TypeError, ValueError):
                ratio = .6
            slot = len(texts); texts[slot] = ""
            role_counts["image"] += 1
            return {"tag": "figure", "style": {**safe_style(own), "margin": "0", "width": "100%"}, "slot": slot, "role": "image", "ratio": ratio}
        children = [item for child in node.children if (item := build(child, depth + 1)) is not None]
        if not children:
            # Keep inert CSS separators and short empty spacer paragraphs.
            style = safe_style(own)
            if node.name == "br" or node.name in {"p", "span"} and not text:
                return {"tag": "section", "style": {"height": "16px"}, "children": []} if node.name == "p" else None
            return {"tag": "section", "style": style, "children": []} if style else None
        containers += 1
        style = safe_style(own)
        if style.get("display") == "inline-block" and "width" not in style and any(c.get("role") == "image" for c in children):
            # Lazy-loaded source photos have intrinsic dimensions; a placeholder
            # does not. Keep its frame at the same available article width.
            style["width"] = "100%"
        return {"tag": node.name if node.name in TAGS else "section", "style": style, "children": children}
    layout = normalize_layout({"version": 1, "tree": build(root)})
    summary = {"containers": containers, "depth": depth_max, "slots": sum(role_counts.values()), "roles": dict(role_counts), "decorativeImagesOmitted": decorative_images}
    return layout, texts, summary


def slots_in(tree):
    if "slot" in tree:
        return [tree]
    return [slot for child in tree.get("children", []) for slot in slots_in(child)]


def element(tag, style, body, attrs=""):
    css = ";".join(f"{k}:{v}" for k, v in style.items())
    return f'<{tag}{attrs} style="{html.escape(css, quote=True)}">{body}</{tag}>'


def draw_tree(tree, assignments, source=False, font_scale=1, line_scale=1):
    if "slot" in tree:
        items = assignments.get(tree["slot"], [])
        if not items:
            return "", False, True
        result = []
        for item in items:
            style = {"margin": "0", **tree["style"]}
            if tree["role"] in {"paragraph", "quote"} and not source:
                for key, scale in [("font-size", font_scale), ("line-height", line_scale)]:
                    match = re.fullmatch(r"([\d.]+)(px)?", style.get(key, ""))
                    if match:
                        style[key] = f'{float(match[1])*scale:g}' + (match[2] or "")
            style.setdefault("box-sizing", "border-box")
            style.setdefault("margin", "0")
            style["overflow-wrap"] = "anywhere"
            kind = item.get("type", tree["role"])
            text = html.escape(item.get("text", "")).replace("\n", "<br>")
            if kind == "image":
                if tree["role"] != "image":
                    style = {"margin": "16px 0", "width": "100%"}
                if source:
                    # Keep photo proportions for comparison without copying or fetching it.
                    body = element("div", {"width": "100%", "aspect-ratio": f'{1/tree.get("ratio",.6):g}', "background": "#e7e7e3", "display": "flex", "align-items": "center", "justify-content": "center", "color": "#888888", "font-size": "12px", "line-height": "1.8"}, "原文图片位置")
                else:
                    body = '<img src="' + html.escape(item["src"], quote=True) + '" alt="' + html.escape(item.get("text", ""), quote=True) + '" style="display:block;width:100%;height:auto">'
                    if text:
                        body += element("figcaption", {"font-size": "11px", "line-height": "1.8", "text-align": "center", "margin": "8px 0"}, text)
                    if item["src"] == "/assets/auditorium.jpg":
                        body += '<p style="font-size:9px;line-height:1.7;color:#888;text-align:center">示例配图：<a href="https://commons.wikimedia.org/wiki/File:Open_Seating_(16614957680).jpg">Phil Roeder</a> / <a href="https://creativecommons.org/licenses/by/2.0/">CC BY 2.0</a></p>'
            else:
                body = text
                # A heading fallback must remain legible when the source has none.
                if kind == "heading" and tree["role"] != "heading":
                    style.update({"font-weight": "700", "text-indent": "0", "margin-top": "22px"})
            attrs = f' data-reference-slot="{tree["slot"]}" data-kind="{kind}"'
            if "index" in item:
                attrs += f' data-block-index="{item["index"]}"'
            tag = "figure" if kind == "image" else ("p" if tree["tag"] in {"figure", "figcaption"} else tree["tag"])
            result.append(element(tag, style, body, attrs))
        return "".join(result), True, True
    children = [draw_tree(c, assignments, source, font_scale, line_scale) for c in tree.get("children", [])]
    active = any(c[1] for c in children)
    has_slots = any(c[2] for c in children)
    if has_slots and not active:
        return "", False, True
    # A short draft must not inherit separators belonging to unfilled Q&A groups.
    last_active = max((i for i,c in enumerate(children) if c[1]),default=-1)
    last_position = max((i for i,c in enumerate(children) if c[2]),default=-1)
    body = "".join(c[0] for i,c in enumerate(children) if source or last_position <= last_active or i <= last_active)
    style = {"box-sizing": "border-box", **tree["style"]}
    return element(tree["tag"], style, body), active, has_slots


def source_preview(layout, texts):
    assignments = {slot["slot"]: [{"type": "image" if slot["role"] == "image" else slot["role"], "text": texts.get(slot["slot"], "")}] for slot in slots_in(layout["tree"])}
    return draw_tree(layout["tree"], assignments, source=True)[0]


def compose(doc):
    spec = doc["referenceStyle"]
    tree = spec["layout"]["tree"]
    slots = slots_in(tree)
    body_slots = [s for s in slots if s["role"] != "cover"]
    if not body_slots:
        body_slots = slots
    assignments, pages = {}, []
    cursor = 0
    last = None
    def flush():
        if assignments:
            pages.append(draw_tree(tree, assignments, font_scale=doc["fontSize"]/max(12,spec["paragraph"]["size"]), line_scale=doc["lineHeight"]/spec["lineHeight"])[0])
    covers = [s for s in slots if s["role"] == "cover"]
    if covers:
        assignments[covers[0]["slot"]] = [{"type": "cover", "text": doc["title"]}]
    blocks = ([{"type": "paragraph", "text": doc["subtitle"]}] if doc["subtitle"] else [])
    blocks += [{**b, "index": i} for i, b in enumerate(doc["blocks"])]
    if doc["author"]:
        blocks.append({"type": "signature", "text": doc["author"]})
    for block in blocks:
        kind = block["type"]
        if kind == "divider":
            # A standalone divider borrows the source's spacing, not a new theme.
            if last is not None:
                assignments.setdefault(last["slot"], []).append({**block, "text": block["text"] or "—"})
                continue
        desired = {"quote": "paragraph"}.get(kind, kind)
        pool = [s for s in body_slots if s["role"] == kind]
        pool = pool or [s for s in body_slots if s["role"] == desired]
        pool = pool or [s for s in body_slots if s["role"] == "paragraph"] or body_slots
        chosen = next((s for s in pool if s["slot"] >= cursor), None)
        if chosen is None and kind in {"paragraph", "quote", "signature"} and last is not None and last["role"] in {"paragraph", "quote", "signature"}:
            chosen = last
        elif chosen is None:
            flush(); assignments = {}; cursor = 0
            chosen = pool[0]
        assignments.setdefault(chosen["slot"], []).append(block)
        cursor = chosen["slot"] + 1
        last = chosen
    flush()
    body = "".join(pages)
    if spec.get("showTitle") and not covers:
        body = element("header", {"padding": "24px 20px", "font-size": "24px", "font-weight": "700", "line-height": "1.5", "background-color": "#ffffff"}, html.escape(doc["title"]).replace("\n", "<br>")) + body
    return body
