"""Validate a small style vocabulary; no source CSS or HTML is executed."""
from __future__ import annotations

import re

FONTS = {
    "sans": "'Microsoft YaHei','PingFang SC',sans-serif",
    "serif": "'Songti SC',STSong,SimSun,serif",
    "hand": "KaiTi,STKaiti,'Kaiti SC',SimSun,serif",
}
DEFAULT_ROLE = {
    "size": 15, "weight": 400, "align": "justify", "color": "#333333", "background": "",
    "font": "sans", "inset": 24, "space": 12, "borderLeft": 0, "borderBottom": 0,
    "borderColor": "#c8c8c8", "radius": 0,
}


def number(value, default, low, high):
    try:
        value = float(value)
        return round(max(low, min(high, value)), 2) if value == value else default
    except (TypeError, ValueError):
        return default


def hex_color(value, default=""):
    if not isinstance(value, str):
        return default
    value = value.lower().strip()
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:])
    return default


def normalize_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("参考样式格式无效")
    result = {
        "version": 1,
        "title": str(profile.get("title", "参考文章"))[:160],
        "paper": hex_color(profile.get("paper"), "#ffffff"),
        "contentPaper": hex_color(profile.get("contentPaper"), hex_color(profile.get("paper"), "#ffffff")),
        "contentBorder": number(profile.get("contentBorder"), 0, 0, 5),
        "contentBorderColor": hex_color(profile.get("contentBorderColor"), "#d0d0d0"),
        "accent": hex_color(profile.get("accent"), "#404040"),
        "lineHeight": number(profile.get("lineHeight"), 1.95, 1.4, 2.5),
        "letterSpacing": number(profile.get("letterSpacing"), .3, 0, 3),
        "indent": bool(profile.get("indent", False)),
        "imageInset": number(profile.get("imageInset"), 24, 0, 45),
        "imageRadius": number(profile.get("imageRadius"), 0, 0, 30),
        "captionAlign": profile.get("captionAlign") if profile.get("captionAlign") in {"left", "center", "right"} else "center",
        "frameWidth": number(profile.get("frameWidth"), 0, 0, 5),
        "frameColor": hex_color(profile.get("frameColor"), "#d0d0d0"),
    }
    for role in ["cover", "heading", "paragraph", "quote"]:
        raw = profile.get(role, {})
        if not isinstance(raw, dict):
            raise ValueError("参考区块样式格式无效")
        item = dict(DEFAULT_ROLE)
        item.update({
            "size": number(raw.get("size"), 28 if role == "cover" else (19 if role == "heading" else 15), 12, 52 if role == "cover" else 32),
            "weight": int(number(raw.get("weight"), 700 if role in {"cover", "heading"} else 400, 400, 900)),
            "align": raw.get("align") if raw.get("align") in {"left", "center", "right", "justify"} else ("left" if role in {"cover", "heading"} else "justify"),
            "font": raw.get("font") if raw.get("font") in FONTS else "sans",
            "color": hex_color(raw.get("color"), result["accent"] if role == "heading" else "#333333"),
            "background": hex_color(raw.get("background")),
            "inset": number(raw.get("inset"), 24, 0, 50),
            "space": number(raw.get("space"), 12, 0, 45),
            "borderLeft": number(raw.get("borderLeft"), 0, 0, 8),
            "borderBottom": number(raw.get("borderBottom"), 0, 0, 5),
            "borderColor": hex_color(raw.get("borderColor"), result["accent"]),
            "radius": number(raw.get("radius"), 0, 0, 30),
        })
        result[role] = item
    reference_id = profile.get("id")
    if reference_id is not None:
        if not isinstance(reference_id, str) or not re.fullmatch(r"[0-9a-f]{32}", reference_id):
            raise ValueError("参考记录编号无效")
        result["id"] = reference_id
    if profile.get("layout") is not None:
        from .reference_layout import normalize_layout
        result["layout"] = normalize_layout(profile["layout"])
        result["version"] = 2
        result["showTitle"] = profile.get("showTitle") is True
    return result
