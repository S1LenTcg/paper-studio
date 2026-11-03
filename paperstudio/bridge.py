"""Isolated, experimental adapter to the user's installed all-to-xiumi.

The application authors its own typed document and calls the external publisher
only inside this worker. No upstream source is vendored. A click is not evidence
of saving: the job is verified again after refreshing its newly created draft.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

from bs4 import BeautifulSoup

from .document import BUILTIN_IMAGES, normalize, render
from .storage import now, write_json
from .paths import ROOT, WEB

REQUIRED = ["_xiumi_login_state", "_fill_xiumi_fields", "_fill_xiumi_body_then_images", "_click_save", "_mark_xiumi_document_dirty",
            "_open_xiumi_editor", "_visible_login_links", "_wait_for_manual_login"]
EDITOR = "https://xiumi.us/studio/v5?lang=zh_CN#/paper/for/new"
HOME = "https://xiumi.us/studio/v5?lang=zh_CN#/"


class BridgeError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class JobCancelled(RuntimeError):
    pass


def canonical_image(value):
    """Ignore Xiumi's renderer-only image transform, retain asset identity."""
    url = urlsplit(value)
    query = urlencode([(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if k != "x-oss-process"])
    return urlunsplit((url.scheme, url.netloc, url.path, query, ""))


def open_editor(upstream, driver, update, canceled):
    """Reuse upstream's home -> login -> My Xiumi -> new-paper workflow.

    Replace only its CLI input() wait with a cancellable desktop-app wait. No
    password or QR automation; the actual login remains in the browser window.
    """
    original_wait = upstream._wait_for_manual_login

    def wait_for_login(browser, timeout):
        update("waiting_login", "请在新打开的秀米窗口完成登录（不是日常浏览器窗口）；登录后自动继续。",
               loginDeadline=(datetime.now(timezone.utc) + timedelta(seconds=timeout)).isoformat())
        # The old adapter omitted this step and silently watched the homepage.
        # Upstream identifies and opens the visible login entry for the user.
        for link in upstream._visible_login_links(browser):
            try:
                if not hasattr(link, "is_displayed") or link.is_displayed():
                    link.click()
                    break
            except Exception:
                continue
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if canceled():
                raise JobCancelled()
            if upstream._xiumi_login_state(browser).get("authenticated"):
                update("opening", "已确认登录，正在从“我的秀米”新建图文。", loginDeadline=None)
                return True
            time.sleep(1)
        raise BridgeError("login_timeout", "等待登录超过 10 分钟：未开始写入，也未保存。请准备新任务，并在新打开的秀米窗口内及时完成登录；可勾选记住本工具登录供后续复用。")

    upstream._wait_for_manual_login = wait_for_login
    try:
        if canceled():
            raise JobCancelled()
        update("opening", "正在按 all-to-xiumi 流程打开秀米入口；首次使用需要在新窗口登录。")
        upstream._open_xiumi_editor(driver, HOME, 600, 35)
        if canceled():
            raise JobCancelled()
        if not draft_url(driver.current_url, new=True) or not upstream._xiumi_login_state(driver).get("authenticated"):
            raise BridgeError("new_editor_unconfirmed", "未确认已登录的新建图文页面，未写入内容；请检查秀米窗口是否有登录或恢复提示。")
        update("opening", "新建图文页面已就绪。", loginDeadline=None)
    finally:
        upstream._wait_for_manual_login = original_wait


def draft_url(value, new=False):
    """Accept only the exact Xiumi editor and expected document route."""
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and url.netloc == "xiumi.us" and
                url.path.rstrip("/") == "/studio/v5" and
                bool(re.fullmatch(r"/paper/for/" + ("new" if new else r"[1-9]\d*") + r"(?:/cube/\d+)?/?", url.fragment)))
    except (TypeError, ValueError):
        return False


def prepare_content(doc, folder):
    """Adapt our own validated render; never depend on HTML attribute order.

    Open themes have one reading canvas but one semantic node per source block.
    Split those nodes into independent publisher sections. Complex reference
    trees stay intact; claiming independent source blocks there would be false.
    """
    import base64
    soup = BeautifulSoup(render(doc), "html.parser")
    photos = soup.find_all("img")
    source_photos = [b for b in doc["blocks"] if b["type"] == "image"]
    if len(photos) != len(source_photos):
        raise ValueError("图片数量与文稿不一致，已停止发送")
    for index, (photo, block) in enumerate(zip(photos, source_photos)):
        if photo.get("src") != block["src"]:
            raise ValueError("图片顺序与文稿不一致，已停止发送")
        if block["src"] in BUILTIN_IMAGES:
            filename = BUILTIN_IMAGES[block["src"]]
            raw = (WEB / filename).read_bytes()
            ext = Path(filename).suffix.lstrip(".")
        else:
            header, payload = block["src"].split(",", 1)
            ext = header.split("/")[1].split(";")[0]
            raw = base64.b64decode(payload, validate=True)
        path = Path(folder) / f"image-{index}.{ext}"
        path.write_bytes(raw)
        photo["src"] = path.name
    warnings = []
    canvas = soup.select_one("#output") if not doc.get("referenceStyle") else None
    if canvas is not None:
        from .reference import declarations
        style = declarations(canvas.get("style", ""))
        # Remove the full-canvas padding; reapply its sides to each section,
        # and its top/bottom only at the beginning/end of the article.
        padding = style.pop("padding", "26px 22px 34px").split()
        padding = (padding * 4)[:4] if len(padding) == 1 else padding
        if len(padding) == 2:
            padding *= 2
        if len(padding) == 3:
            padding.append(padding[1])
        groups = []
        for child in list(canvas.children):
            if not getattr(child, "name", None):
                continue
            if child.name == "header" or child.has_attr("data-block-index"):
                groups.append([child])
            elif groups:
                groups[-1].append(child)
        if len(groups) != len(doc["blocks"]) + 1:
            raise ValueError("正文区块无法逐一映射，已停止发送")
        result = BeautifulSoup("", "html.parser")
        for index, group in enumerate(groups):
            section = result.new_tag("section")
            block_style = {**style, "padding": f'{padding[0] if index == 0 else "0"} {padding[1]} {padding[2] if index == len(groups)-1 else "0"} {padding[3]}'}
            section["style"] = ";".join(f"{k}:{v}" for k, v in block_style.items())
            for node in group:
                section.append(node.extract())
            result.append(section)
        soup = result
    if doc.get("referenceStyle", {}).get("layout"):
        warnings.append("复杂参考结构按整体组件发送；各源区块独立移动与换图仍需在秀米检查。")
    components = len(soup.find_all("section", recursive=False))
    return str(soup), {"components": components, "images": len(photos), "warnings": warnings}


def snapshot(driver):
    return driver.execute_script('''const a=document.querySelector('article');const t=document.querySelector('input.title');
const editables=a?[...a.querySelectorAll('[contenteditable=true]')].filter(e=>!e.parentElement.closest('[contenteditable=true]')):[];
return {title:t?t.value:'',text:a?a.innerText:'',editableCount:editables.length,
images:a?[...a.querySelectorAll('img')].map(i=>({src:i.currentSrc||i.src,loaded:i.complete&&i.naturalWidth>0})):[]};''')


def source_path():
    value = os.environ.get("PAPER_XIUMI_SOURCE")
    # This default follows the workspace layout, never the process cwd.
    return Path(value).expanduser().resolve() if value else ROOT.parents[1] / "all-to-xiumi"


def availability():
    source = source_path()
    selenium = importlib.util.find_spec("selenium") is not None
    publisher = source / "all_to_xiumi" / "xiumi_publish.py"
    ready = selenium and publisher.is_file()
    return {"available": ready, "selenium": selenium, "publisherFound": publisher.is_file(),
            "adapter": "all-to-xiumi / experimental", "remoteVerified": False,
            "message": "本地适配器就绪，尚未验证本次秀米会话" if ready else "缺少 Selenium 或 all-to-xiumi，仍可编辑和导出 HTML"}


def compact(value):
    return re.sub(r"\s+", "", value or "")


def verify(doc, snapshot, expected_components=None, baseline_images=None):
    """Keep unmeasurable editing checks pending rather than green."""
    text = compact(snapshot.get("text", ""))
    fragments = [b["text"] for b in doc["blocks"] if b["type"] != "divider" and b["text"]]
    # Match repeated fragments in order: one surviving copy must not hide a
    # missing second paragraph. The editor title is checked separately, since
    # reference layouts can deliberately omit a body title.
    cursor = 0
    text_ok = True
    for fragment in fragments:
        position = text.find(compact(fragment), cursor)
        if position < 0:
            text_ok = False
            break
        cursor = position + len(compact(fragment))
    text_ok = text_ok and (not doc["subtitle"] or compact(doc["subtitle"]) in text)
    expected_images = sum(b["type"] == "image" for b in doc["blocks"])
    images = snapshot.get("images", [])
    checks = {
        "title": snapshot.get("title") == doc["title"].replace("\n", " "),
        "text": text_ok,
        "imagesPersisted": len(images) == expected_images and all(i.get("src", "").startswith("https://") for i in images),
        "imagesLoaded": len(images) == expected_images and all(i.get("loaded") for i in images),
        "editableBlocks": snapshot.get("editableCount", 0) >= (expected_components if expected_components is not None else len(doc["blocks"]) + 1),
    }
    if baseline_images is not None:
        checks["imageOrder"] = [canonical_image(i.get("src", "")) for i in images] == [canonical_image(i) for i in baseline_images]
    return {"automatic": checks, "manual": {"visualMatch": "pending", "independentImageReplacement": "pending", "moveBlock": "pending"},
            "automaticPassed": all(checks.values())}


def run_job(job_dir):
    job_dir = Path(job_dir).resolve()
    status_path = job_dir / "status.json"
    state = json.loads(status_path.read_text(encoding="utf-8"))
    if state.get("stage") != "queued" or state.get("saveAttempted"):
        return  # This worker is never a resume/retry mechanism for remote writes.

    def update(stage, message, **extra):
        state.update({"stage": stage, "message": message, "updatedAt": now(), **extra})
        write_json(status_path, state)

    def canceled():
        return (job_dir / "cancel.request").exists()

    driver = None
    save_attempted = False
    settings = state.get("options") or {}
    leave_open = bool(settings.get("leaveOpen", False))
    remember_login = bool(settings.get("rememberLogin", False))
    try:
        doc = normalize(json.loads((job_dir / "document.json").read_text(encoding="utf-8")))
        if not availability()["available"]:
            raise RuntimeError("本地适配器依赖尚未就绪")
        sys.path.insert(0, str(source_path()))
        from all_to_xiumi import xiumi_publish as upstream
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        for name in REQUIRED:
            if not callable(getattr(upstream, name, None)):
                raise RuntimeError("all-to-xiumi 版本不兼容：缺少 " + name)
        # Keep upstream debug output inside this job, not the dependency checkout.
        upstream._XIUMI_DEBUG_LOG_PATH = job_dir / "upstream-debug.jsonl"
        update("opening", "正在打开秀米测试窗口")
        options = Options()
        options.page_load_strategy = "eager"
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_experimental_option("detach", leave_open)
        if remember_login:
            profile = job_dir.parent.parent / "browser" / "xiumi"
            profile.mkdir(parents=True, exist_ok=True)
            options.add_argument("--user-data-dir=" + str(profile))
        # Use only our own opt-in profile. Do not copy daily-browser cookies or
        # upstream certificate-bypass flags.
        driver = webdriver.Chrome(options=options, service=Service(log_output=os.devnull))
        driver._wanyou_browser_name = "chrome"
        driver.set_page_load_timeout(45)
        open_editor(upstream, driver, update, canceled)
        update("writing", "正在写入新测试稿并上传图片")
        content, manifest = prepare_content(doc, job_dir)
        write_json(job_dir / "manifest.json", manifest)
        update("writing", "正在写入新测试稿并上传图片", expected=manifest)
        upstream._fill_xiumi_fields(driver, doc["title"].replace("\n", " "), doc["author"], "", doc["subtitle"])
        _, applied = upstream._fill_xiumi_body_then_images(driver, content, job_dir / "article.html", preserve_styles=True)
        if not applied:
            raise RuntimeError("组件模型写入未确认，已停止保存")
        if canceled():
            update("cancelled", "已取消，未保存秀米草稿")
            return
        # The upstream style-aware path can return "skipped" even after image
        # upload. Treat the rendered persistent URLs, not that counter, as proof.
        update("preflight", "正在检查正文、图片与可编辑组件；检查通过后才保存")
        deadline = time.monotonic() + 25
        preflight = None
        before_snapshot = {}
        while time.monotonic() < deadline:
            if canceled():
                update("cancelled", "已取消，未保存秀米草稿")
                return
            before_snapshot = snapshot(driver)
            preflight = verify(doc, before_snapshot, manifest["components"])
            if preflight["automaticPassed"]:
                break
            time.sleep(.5)
        update("preflight", "保存前检查完成", preflight=preflight)
        if not preflight or not preflight["automaticPassed"]:
            update("failed", "正文、图片或组件的保存前检查未通过，未点击保存；可导出 HTML", errorCode="preflight_failed", failedStage="preflight")
            return
        baseline_images = [i["src"] for i in before_snapshot.get("images", [])]
        write_json(job_dir / "preflight.json", {"verification": preflight, "imageUrls": baseline_images})
        if not draft_url(driver.current_url, new=True) or not upstream._xiumi_login_state(driver).get("authenticated"):
            raise RuntimeError("保存前未确认已登录的新建图文页面")
        if canceled():
            update("cancelled", "已取消，未保存秀米草稿")
            return
        upstream._mark_xiumi_document_dirty(driver)
        before = driver.current_url
        update("saving", "正在保存新测试稿；此阶段不会自动重试", saveAttempted=True)
        save_attempted = True
        upstream._click_save(driver)
        # Upstream treats some unsaved /for/new URL variants as saved; require
        # the numeric id of our newly created draft instead.
        deadline = time.monotonic() + 45
        url = before
        while time.monotonic() < deadline:
            url = driver.current_url
            if draft_url(url):
                break
            time.sleep(.5)
        if not draft_url(url):
            update("uncertain", "保存结果未确认，请在秀米查看，避免重复发送", draftUrl=url if draft_url(url) else None)
            return
        update("verifying", "已保存，正在重新打开核对文字、图片和区块", draftUrl=url)
        driver.refresh()
        verification = verify(doc, {}, manifest["components"], baseline_images)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if not draft_url(driver.current_url) or urlsplit(driver.current_url).fragment.split("/cube/")[0].rstrip("/") != urlsplit(url).fragment.split("/cube/")[0].rstrip("/"):
                raise RuntimeError("重新打开后的草稿地址发生变化")
            verification = verify(doc, snapshot(driver), manifest["components"], baseline_images)
            if verification["automaticPassed"]:
                break
            time.sleep(1)
        write_json(job_dir / "verification.json", verification)
        try:
            driver.save_screenshot(str(job_dir / "reopened.png"))
        except Exception:
            pass  # An evidence screenshot failure cannot erase a known save.
        update("verified" if verification["automaticPassed"] else "review_required",
               "重新打开自动核对通过；排版和单独换图仍需人工检查" if verification["automaticPassed"] else "草稿已保存，部分核对未通过，请打开检查",
               verification=verification)
    except JobCancelled:
        update("cancelled", "已取消，未点击保存。", loginDeadline=None)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        phase = state.get("stage", "opening")
        if save_attempted and driver is not None:
            try:
                if draft_url(driver.current_url) and not state.get("draftUrl"):
                    state["draftUrl"] = driver.current_url
            except Exception:
                pass
        if save_attempted:
            code, message = "save_uncertain", "保存后发生异常，请先查看秀米草稿，避免重复发送。"
        elif isinstance(exc, BridgeError):
            code, message = exc.code, str(exc)
        elif phase == "opening":
            code, message = "editor_open_failed", "未能打开已登录的新建图文页面，未写入内容。请检查秀米窗口、网络，以及是否有未关闭的本工具窗口。"
        elif phase == "waiting_login":
            code, message = "login_failed", "登录状态未能确认，未写入内容。请检查本工具打开的秀米窗口，再准备新任务。"
        else:
            code, message = "write_failed", "内容写入或保存前检查未完成，未点击保存。请查看秀米窗口；详细记录在本地任务目录。"
        update("uncertain" if save_attempted else "failed", message, errorCode=code, failedStage=phase)
    finally:
        if driver is not None:
            preserve = leave_open and state.get("stage") != "cancelled"
            if preserve:
                # detach=True keeps the user-facing Chrome window after its
                # driver service exits, matching upstream's inspection flow.
                try:
                    driver.service.stop()
                    state["browserLeftOpen"] = True
                    state["message"] += " 秀米窗口已保留，可直接检查。再次使用记住登录的模式前，请先关闭上次窗口。"
                    write_json(status_path, state)
                except Exception:
                    preserve = False
            if not preserve:
                try:
                    driver.quit()
                except Exception:
                    pass



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir")
    run_job(parser.parse_args().job_dir)
