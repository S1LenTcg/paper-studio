"""Deterministic text-format audit. No model calls or article rewriting."""
from __future__ import annotations

import hashlib
import json
import re

from .bridge import canonical_image, draft_url

PROFILE = {
    "id": "xx-wechat-26-27", "name": "校园图文 · 基础文字规范",
    "source": "内置文字排版规则示例（不含原始运营文档）",
    "body": "14号 · 默认字体 · 左对齐 · 行距1.8 · 字距1 · 首行缩进 · 左右边距15–30",
    "footer": "14号 · 右对齐 · 行距1.5 · 默认字距 · #A0A0A0 · 正文卡片外",
    "notes": ["普通正文颜色 #565656；正式通知可选 #414141。", "段间空一行，或使用等效段距。", "标点使用全角；时间、比分中的冒号使用半角。", "保留原文和强调；标题、图注、装饰默认不自动修改。"],
}
ROLES = {"body", "footer", "heading", "caption", "ignore", "unknown"}

# Only rendered DOM is read. No Angular scopes, cookies, private APIs or model.
SNAPSHOT_JS = r"""
const a=document.querySelector('article');
if(!a) return null;
const css=e=>{const s=getComputedStyle(e);return {
font:parseFloat(s.fontSize),line:parseFloat(s.lineHeight)/parseFloat(s.fontSize),
letter:s.letterSpacing==='normal'?0:parseFloat(s.letterSpacing),color:s.color,
align:s.textAlign,indent:parseFloat(s.textIndent)||0,fontFamily:s.fontFamily,
paddingLeft:parseFloat(s.paddingLeft)||0,paddingRight:parseFloat(s.paddingRight)||0,
marginBottom:parseFloat(s.marginBottom)||0};};
const cells=[...a.querySelectorAll('[contenteditable=true]')].filter(e=>!e.parentElement.closest('[contenteditable=true]'));
return {url:location.href,title:document.querySelector('input.title')?.value||'',
cells:cells.map((e,i)=>{const r=e.getBoundingClientRect();return {
id:String(i),text:e.innerText,html:e.innerHTML,style:css(e),
paragraphs:[...e.querySelectorAll('p')].filter(p=>p.innerText.trim()).map(p=>({text:p.innerText,style:css(p)})),
top:r.top,bottom:r.bottom,topLevel:!!e.parentElement.parentElement.classList.contains('tn-comp-top-level'),
emphasisColors:[...e.querySelectorAll('strong,b,em,i')].filter(s=>getComputedStyle(s).color!==getComputedStyle(s.closest('p')||e).color).map(s=>getComputedStyle(s).color),
embeddedMedia:!!e.querySelector('img,svg,video,audio,iframe'),
inlineOverrides:[...e.querySelectorAll('span[style]')].filter(s=>s.innerText.trim()&&!s.closest('strong,b,em,i')).map(s=>css(s))};}),
images:[...a.querySelectorAll('img')].map(e=>({src:e.currentSrc||e.src,loaded:e.complete&&e.naturalWidth>0}))};
"""


def paper_id(url):
    if not isinstance(url, str) or len(url) > 600 or not draft_url(url):
        raise ValueError("请输入有效的秀米草稿编辑链接（https://xiumi.us/studio/v5#/paper/for/数字编号）")
    return re.search(r"/paper/for/([1-9]\d*)", url).group(1)


def fingerprint(snapshot):
    # Layout coordinates change with scrolling; authored styles and HTML do not.
    value = {"title": snapshot["title"], "cells": [{"text": c["text"], "html": c["html"], "style": c["style"]} for c in snapshot["cells"]],
             "images": [canonical_image(i["src"]) for i in snapshot["images"]]}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def content_identity(snapshot):
    # Inserted blank components may differ; nonempty text must remain exact.
    return {"text": [c["text"] for c in snapshot["cells"] if c["text"].strip()],
            "images": [canonical_image(i["src"]) for i in snapshot["images"]]}


def infer_role(cell):
    text = cell["text"].strip()
    s = cell["style"]
    ps = cell.get("paragraphs") or [{"style": s}]
    if not text:
        return "ignore"
    if len(re.findall(r"图片|文字|编辑|校对|审核", text)) >= 3 and len(text) < 500:
        return "footer"
    if cell.get("embeddedMedia"):
        return "unknown"
    if s.get("font", 14) > 16 and len(text) < 180:
        return "heading"
    if s.get("font", 14) < 14 or (s.get("align") == "center" and 12 < len(text) < 100):
        return "caption"
    if any(p["style"].get("indent", 0) > 10 for p in ps) or (len(text) >= 65 and s.get("align") in {"left", "justify", "start"}):
        return "body"
    if (len(text) <= 12 and s.get("align") == "center") or re.fullmatch(r"[\s\d—\-·END]+", text):
        return "heading"
    return "unknown"


def audit(snapshot, roles=None, body_color="#565656"):
    if body_color not in {"#565656", "#414141"}:
        raise ValueError("正文颜色选项无效")
    if roles is not None and (not isinstance(roles, dict) or set(roles) - {c["id"] for c in snapshot["cells"]} or any(r not in ROLES for r in roles.values())):
        raise ValueError("区块分类无效")
    cells, issues = [], []
    rgb = "rgb(86, 86, 86)" if body_color == "#565656" else "rgb(65, 65, 65)"

    def issue(cell, field, message, current, target, auto=True):
        issues.append({"id": cell["id"] + ":" + field, "cellId": cell["id"], "field": field,
                       "message": message, "current": current, "target": target, "automatic": auto})

    for c in snapshot["cells"]:
        role = (roles or {}).get(c["id"], infer_role(c))
        row = {"id": c["id"], "text": c["text"], "role": role, "style": c["style"]}
        cells.append(row)
        if not c["text"].strip() or role not in {"body", "footer"}:
            continue
        if c.get("embeddedMedia"):
            issue(c, "mixed", "文字与媒体混在同一组件，请人工拆分后检查", "混合组件", "人工处理", False)
            continue
        styles = [p["style"] for p in c.get("paragraphs", [])] or [c["style"]]
        expected = {"font": 14, "line": 1.8 if role == "body" else 1.5,
                    "letter": 1 if role == "body" else 0, "align": "left" if role == "body" else "right",
                    "color": rgb if role == "body" else "rgb(160, 160, 160)"}
        labels = {"font": "字号", "line": "行距", "letter": "字距", "align": "对齐", "color": "颜色"}
        for field, target in expected.items():
            values = list(dict.fromkeys(s.get(field) for s in styles))
            wrong = any(abs((v or 0) - target) > .04 if isinstance(target, (int, float)) else v != target for v in values)
            if wrong:
                issue(c, field, labels[field] + "与规范不一致" + ("；存在强调色，请人工保留" if field == "color" and c.get("emphasisColors") else ""), values, target, not(field == "color" and c.get("emphasisColors")))
        if role == "body":
            # Two CJK characters at 14px plus letter spacing = 30px. The source
            # requires an indent but not its amount; this is our chosen default.
            if any(abs(s.get("indent", 0) - 30) > .1 for s in styles):
                issue(c, "indent", "统一首行缩进为约两个汉字", [s.get("indent") for s in styles], 30)
            defaults = {"arial", "sans-serif", "noto sans cjk sc", "helvetica neue", "helvetica", "hiragino sans gb", "microsoft yahei", "微软雅黑", "-apple-system", "system-ui", "pingfang sc"}
            if any(s.get("fontFamily", "").split(',')[0].strip(' \"\'').lower() not in defaults for s in styles):
                issue(c, "family", "正文使用默认字体", styles[0].get("fontFamily"), "默认字体")
            if c.get("inlineOverrides") and any(any(abs((s.get(f) or 0)-expected[f]) > .04 for f in ("font", "line", "letter")) for s in c["inlineOverrides"]):
                issue(c, "inline", "局部文字有格式覆盖，请检查；保留强调，不批量清除", "局部覆盖", "人工复核", False)
            # Nested native cards own their padding; changing the text padding
            # blindly would double it. Keep this check explicitly manual.
            issue(c, "margins", "核对正文卡片内的实际左右留白（避免重复加边距）", "含父级卡片边距", "15–30像素", False)
            if re.search(r"[,;:!?()]", c["text"]):
                issue(c, "punctuation", "检查半角标点；时间、比分及公式等须结合语义判断", "存在半角符号", "人工核对", False)
        elif not c.get("topLevel"):
            issue(c, "outside", "小尾巴应放在正文卡片外", "可能嵌套在卡片内", "移出卡片", False)

    # Only adjacent body blocks, not captions/headings/images. Coordinate gap
    # includes real blank components and parent spacing already on the page.
    for i, c in enumerate(snapshot["cells"][:-1]):
        if cells[i]["role"] != "body" or not c["text"].strip() or c.get("embeddedMedia"):
            continue
        j = i + 1
        while j < len(cells) and not cells[j]["text"].strip():
            j += 1
        if j < len(cells) and cells[j]["role"] == "body":
            gap = snapshot["cells"][j]["top"] - c["bottom"]
            if -.5 <= gap < 24:
                issue(c, "gap", "连续正文之间缺少空行或等效段距", round(gap, 1), "插入一行原生空行")
    return {"profile": PROFILE, "fingerprint": fingerprint(snapshot), "title": snapshot["title"],
            "bodyColor": body_color, "cells": cells, "issues": issues,
            "summary": {"blocks": sum(bool(c["text"].strip()) for c in cells), "images": len(snapshot["images"]),
                        "automatic": sum(i["automatic"] for i in issues), "manual": sum(not i["automatic"] for i in issues),
                        "unknown": sum(c["role"] == "unknown" for c in cells)}}


def validate_plan(report, data):
    if data.get("fingerprint") != report["fingerprint"]:
        raise ValueError("检查记录已变化，请刷新后重新选择")
    roles = data.get("roles", {})
    if not isinstance(roles, dict) or set(roles) != {c["id"] for c in report["cells"]} or any(r not in ROLES for r in roles.values()):
        raise ValueError("请完整确认区块分类")
    selected = data.get("selected")
    if not isinstance(selected, list) or not selected or len(selected) > 2000 or any(not isinstance(s, str) for s in selected) or len(set(selected)) != len(selected):
        raise ValueError("请至少选择一项可自动修正的问题")
    return {"fingerprint": report["fingerprint"], "roles": roles, "selected": selected, "bodyColor": data.get("bodyColor", "#565656")}
