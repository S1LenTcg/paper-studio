"""Validated AI design vocabulary; no model HTML or CSS enters the document."""
from __future__ import annotations

from .reference_style import FONTS, hex_color, number

OPTIONS = {
    "font": ("sans", "serif", "hand"),
    "heading": ("plain", "rule", "label", "center"),
    "quote": ("line", "pull", "quiet"),
    "image": ("plain", "bleed", "paper"),
    "align": ("left", "justify"),
}
APPEARANCES = {"normal", "lead", "quiet"}


def normalize_design(raw):
    if not isinstance(raw, dict):
        raise ValueError("AI 排版方案格式无效")
    design = {"version": 1, "accent": hex_color(raw.get("accent"), "#315e68"),
              "indent": raw.get("indent") is True}
    for name, choices in OPTIONS.items():
        design[name] = raw.get(name) if raw.get(name) in choices else choices[0]
    return design


def apply_design(soup, doc):
    if not doc.get("aiLayout"):
        return
    design = doc["aiLayout"]
    accent = design["accent"]
    def change(node, styles):
        if node is None:
            return
        from .reference import declarations
        current = {k:v for k,v in declarations(node.get("style", "")).items() if k not in styles}
        current.update(styles)
        node["style"] = ";".join(f"{k}:{v}" for k,v in current.items())
    change(soup.select_one("#output"), {"font-family": FONTS[design["font"]]})
    for node in soup.select("[data-block-index]"):
        block = doc["blocks"][int(node["data-block-index"])]
        kind = block["type"]
        if kind == "heading":
            common = {"display":"block", "padding":"0", "color":accent, "background":"transparent", "border":"0", "border-radius":"0", "box-shadow":"none", "line-height":"1.65", "text-align":"left", "font-size":"19px", "margin":"1.8em 8px .8em"}
            if design["heading"] == "rule": common.update({"border-left":f"3px solid {accent}", "padding-left":"12px"})
            elif design["heading"] == "label": common.update({"display":"table", "margin":"1.8em auto .8em", "background":accent, "color":"#ffffff", "padding":"4px 12px", "text-align":"center"})
            elif design["heading"] == "center": common.update({"text-align":"center", "font-family":FONTS["serif"], "font-weight":"400", "letter-spacing":"1px"})
            change(node,common)
        elif kind == "quote":
            common = {"border":"0", "border-left":f"2px solid {accent}", "border-radius":"0", "box-shadow":"none", "font-style":"normal", "padding":"8px 0 8px 16px", "background":"transparent", "margin":"1.5em 8px", "color":"#687478"}
            if design["quote"] == "pull": common.update({"border-left":"0", "padding":"12px 18px", "font-family":FONTS["serif"], "font-size":"21px", "line-height":"1.9", "text-align":"center", "color":accent})
            elif design["quote"] == "quiet": common.update({"border-left":"0", "padding":"15px 18px", "background":"#f5f6f4", "font-size":"14px"})
            change(node,common);change(node.find("p"),{"color":"inherit", "font-family":"inherit", "font-size":"inherit", "letter-spacing":".3px", "line-height":"inherit", "margin":"0"})
        elif kind == "image":
            if design["image"] == "bleed":change(node,{"margin":"1.5em -22px"})
            elif design["image"] == "paper":change(node,{"margin":"1.6em 0", "padding":"9px", "background":"#f7f5ef", "border":"1px solid #e7e3d8"})
            change(node.find("img"),{"border-radius":"0", "box-shadow":"none"})
        elif kind == "paragraph":
            style = {"font-family":FONTS[design["font"]], "text-align":design["align"], "text-indent":"2em" if design["indent"] else "0", "letter-spacing":".4px"}
            if block.get("appearance") == "lead":style.update({"font-size":f'{min(20,doc["fontSize"]+1):g}px',"line-height":"2", "color":"#6a777b", "text-indent":"0", "margin-bottom":"1.7em"})
            elif block.get("appearance") == "quiet":style.update({"color":"#8c9698", "font-size":f'{max(12,doc["fontSize"]-1):g}px'})
            change(node,style)
