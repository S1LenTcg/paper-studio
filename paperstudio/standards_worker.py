"""UI-only Xiumi audit/correction worker; all writes target a verified copy."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .bridge import BridgeError, JobCancelled, open_editor, source_path
from .standards import SNAPSHOT_JS, audit, content_identity, fingerprint, paper_id
from .storage import now, write_json


class XiumiTextUI:
    def __init__(self, driver):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.support.ui import WebDriverWait
        self.d, self.By, self.Keys, self.Actions, self.Wait = driver, By, Keys, ActionChains, WebDriverWait

    def visible(self, selector, xpath=False):
        def find(_):
            found = self.d.find_elements(self.By.XPATH if xpath else self.By.CSS_SELECTOR, selector)
            return next((e for e in found if e.is_displayed() and e.is_enabled()), False)
        return self.Wait(self.d, 15).until(find)

    def button(self, text):
        # Only fixed product labels are passed here, never article content.
        return self.visible("//button[normalize-space(.)='" + text + "']", True)

    def snapshot(self):
        def read(_):
            s = self.d.execute_script(SNAPSHOT_JS)
            return s if s and any(c["text"].strip() for c in s["cells"]) and all(i["loaded"] for i in s["images"]) else False
        result = self.Wait(self.d, 40).until(read)
        if len(result["cells"]) > 600 or sum(len(c["text"]) for c in result["cells"]) > 200000:
            raise ValueError("文章超过本版检查上限（600个文字组件或20万字）")
        return result

    def select(self, index, expected):
        cells = self.d.find_elements(self.By.CSS_SELECTOR, 'article [contenteditable=true]')
        e = cells[index]
        if e.get_attribute("innerText") != expected:
            raise ValueError("区块内容或顺序变化，已停止修改")
        # Xiumi's single click may leave the previous cell active. A real
        # double click, followed by DOM verification, is required.
        self.Actions(self.d).move_to_element(e).double_click().perform()
        self.Wait(self.d, 8).until(lambda _: "tn-on-editing" in (e.get_attribute("class") or ""))
        e.send_keys(self.Keys.CONTROL, "a")
        selected = self.d.execute_script("return window.getSelection()?.toString()||''")
        if selected.strip() != expected.strip():
            raise ValueError("未能完整选中当前文字组件，已停止修改")
        return e

    def fill(self, selector, value):
        e = self.visible(selector)
        e.send_keys(self.Keys.CONTROL, "a")
        e.send_keys(str(value))
        e.send_keys(self.Keys.ENTER)

    def correct(self, index, cell, field, target):
        e = self.select(index, cell["text"])
        if field == "font":
            self.fill('input.size-input', 14)
        elif field == "family":
            self.visible('.font-family > button').click()
            self.visible('.font-family-list li[title="默认字体"] a').click()
        elif field == "align":
            e.send_keys(self.Keys.CONTROL, "l" if target == "left" else "r")
        elif field in {"line", "letter"}:
            self.button("间距").click()
            self.fill('.spacing-panel .line-spacing input' if field == "line" else '.spacing-panel .letter-spacing input', target)
            self.button("间距").click()
        elif field == "color":
            self.visible('.op-text-sec.text-color button.tn-color-circle').click()
            self.visible('[title="添加颜色到调色板"]').click()
            color_input = self.visible('.color-picker-container:not(.ng-hide) .color-block-solid .sp-input')
            color_input.send_keys(self.Keys.CONTROL, "a")
            color_input.send_keys(target, self.Keys.TAB)
            buttons = self.d.find_elements(self.By.XPATH, "//div[contains(@class,'color-picker-container')]//button[normalize-space(.)='应用颜色']")
            candidates = [b for b in buttons if b.is_displayed()]
            if not candidates:
                raise ValueError("未找到颜色应用按钮")
            candidates[-1].click()
        elif field == "indent":
            current = self.d.execute_script("return [...document.querySelectorAll('article [contenteditable=true].tn-on-editing p')].filter(p=>p.innerText.trim()).map(p=>parseFloat(getComputedStyle(p).textIndent)||0)")
            if any(v > 0 for v in current):
                e.send_keys(self.Keys.CONTROL, self.Keys.ALT, "\\")
            e.send_keys(self.Keys.CONTROL, self.Keys.ALT, "\\")
        elif field == "gap":
            self.button("后插空行").click()
        else:
            raise ValueError("不支持自动修改此项目")
        # End this edit so transient paragraph margins are committed before
        # checking, never mistake an in-editor preview for a persisted change.
        self.visible('input.title').click()

    def duplicate(self, source_id):
        self.visible('button.op-btn.more').click()
        self.visible("//*[self::a or self::li][normalize-space(.)='另存一个图文']", True).click()
        def copied(_):
            try:
                return self.d.current_url if paper_id(self.d.current_url) != source_id else False
            except ValueError:
                return False
        return self.Wait(self.d, 45).until(copied)


def run_job(folder):
    folder = Path(folder).resolve()
    state = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    if state.get("stage") != "queued" or state.get("copyAttempted") or state.get("saveAttempted"):
        return
    driver = None
    options = state["options"]

    def update(stage, message, **extra):
        state.update(stage=stage, message=message, updatedAt=now(), **extra)
        write_json(folder / "status.json", state)

    def canceled():
        return (folder / "cancel.request").exists()

    def guard(copy_id):
        if paper_id(driver.current_url) != copy_id or copy_id == paper_id(state["sourceUrl"]):
            raise ValueError("未确认独立副本地址，已停止修改")

    try:
        sys.path.insert(0, str(source_path()))
        from all_to_xiumi import xiumi_publish as upstream
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        chrome = Options()
        chrome.page_load_strategy = "eager"
        chrome.add_argument("--no-first-run")
        chrome.add_argument("--no-default-browser-check")
        chrome.add_experimental_option("detach", options["leaveOpen"])
        if options["rememberLogin"]:
            profile = folder.parent.parent / "browser" / "xiumi"
            profile.mkdir(parents=True, exist_ok=True)
            chrome.add_argument("--user-data-dir=" + str(profile))
        upstream._XIUMI_DEBUG_LOG_PATH = folder / "upstream-debug.jsonl"
        update("opening", "正在打开纸间专属秀米窗口；请在该窗口完成登录")
        driver = webdriver.Chrome(options=chrome, service=Service(log_output=os.devnull))
        driver._wanyou_browser_name = "chrome"
        driver.set_page_load_timeout(45)
        open_editor(upstream, driver, update, canceled)
        source_id = paper_id(state["sourceUrl"])
        driver.get(state["sourceUrl"])
        ui = XiumiTextUI(driver)
        update("inspecting", "正在读取原稿的文字组件和实际格式，原稿只读")
        original = ui.snapshot()
        if paper_id(original["url"]) != source_id:
            raise ValueError("打开的文章与目标链接不一致")
        write_json(folder / "snapshot.json", original)
        report = audit(original)
        write_json(folder / "report.json", report)
        update("awaiting_review", "检查完成。请在纸间确认区块分类并选择修正项；秀米窗口请保持打开。", title=original["title"])
        deadline = time.monotonic() + 1800
        while not (folder / "plan.json").exists():
            if canceled():
                raise JobCancelled()
            if time.monotonic() > deadline:
                update("interrupted", "等待确认超过30分钟。未创建副本；请重新检查。")
                return
            time.sleep(.5)
        plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
        if canceled():
            raise JobCancelled()
        if paper_id(driver.current_url) != source_id or fingerprint(ui.snapshot()) != plan["fingerprint"]:
            raise ValueError("原稿内容或格式在检查后发生变化，请重新检查；未创建副本")
        selected = set(plan["selected"])
        checked = audit(original, plan["roles"], plan["bodyColor"])
        operations = [i for i in checked["issues"] if i["id"] in selected and i["automatic"]]
        if {i["id"] for i in operations} != selected:
            raise ValueError("修正计划无效")
        # Record before the creation click: a lost response must never trigger
        # an automatic second copy, including after service restarts.
        update("copying", "正在用秀米“另存一个图文”建立副本；不会覆盖原稿", copyAttempted=True)
        copy_url = ui.duplicate(source_id)
        copy_id = paper_id(copy_url)
        update("correcting", "副本已建立，正在仅修正所选文字格式", draftUrl=copy_url)
        copied = ui.snapshot()
        if content_identity(copied) != content_identity(original):
            raise ValueError("副本的文字或图片与原稿不一致，未继续修改")
        guard(copy_id)
        title = original["title"] + "（文字格式校正版）"
        e = ui.visible('input.title')
        e.send_keys(ui.Keys.CONTROL, "a")
        e.send_keys(title, ui.Keys.TAB)
        # Work backwards so newly inserted blank components don't shift any
        # remaining target index. Keep gap insertion last within each cell.
        operations.sort(key=lambda i: (int(i["cellId"]), i["field"] == "gap"), reverse=True)
        grouped = {}
        for op in operations:
            grouped.setdefault(int(op["cellId"]), []).append(op)
        for index in sorted(grouped, reverse=True):
            for op in sorted(grouped[index], key=lambda i: (i["field"] == "gap", ["font", "family", "line", "letter", "align", "color", "indent", "gap"].index(i["field"]))):
                guard(copy_id)
                ui.correct(index, original["cells"][index], op["field"], op["target"])
        guard(copy_id)
        before_save = ui.snapshot()
        if content_identity(before_save) != content_identity(original):
            raise ValueError("保存前文字或图片对比未通过，已停止保存副本")
        # Mapping shifts only by blank cells; re-map roles in source order.
        def mapped_roles(snap):
            source = iter((c for c in original["cells"] if c["text"].strip()))
            result = {}
            for c in snap["cells"]:
                result[c["id"]] = plan["roles"][next(source)["id"]] if c["text"].strip() else "ignore"
            return result
        remaining = audit(before_save, mapped_roles(before_save), plan["bodyColor"])
        # IDs move after inserted blanks. Check by source text + field instead.
        def unresolved(result):
            source = iter(c["id"] for c in original["cells"] if c["text"].strip())
            mapping = {c["id"]: next(source) for c in result["cells"] if c["text"].strip()}
            targets = {(i["cellId"], i["field"]) for i in operations}
            return [i for i in result["issues"] if (mapping.get(i["cellId"]), i["field"]) in targets]
        if unresolved(remaining):
            write_json(folder / "preflight.json", remaining)
            raise ValueError("部分选中格式未能通过原生控件修正，未保存；请在副本内核对")
        update("saving", "保存副本中；保存开始后不会自动重试", saveAttempted=True)
        ui.visible('button.op-btn.save').click()
        # Copy already has a numeric ID; ID existence is NOT save evidence.
        # A reload and exact content/style checks below establish persistence.
        time.sleep(2)
        update("verifying", "正在重新打开副本，核对内容、图片及所选格式")
        driver.refresh()
        guard(copy_id)
        after = ui.snapshot()
        same = content_identity(after) == content_identity(original)
        final_report = audit(after, mapped_roles(after), plan["bodyColor"]) if same else None
        checks = {"textAndImages": same, "title": after["title"] == title,
                  "selectedFormats": same and not unresolved(final_report), "imagesLoaded": all(i["loaded"] for i in after["images"])}
        write_json(folder / "verification.json", {"checks": checks, "report": final_report, "manual": {"visualLayout": "pending", "independentEditing": "pending"}})
        try:
            driver.save_screenshot(str(folder / "reopened.png"))
        except Exception:
            pass
        update("verified" if all(checks.values()) else "review_required",
               "副本保存并回读核对通过；请打开检查视觉效果及人工项目" if all(checks.values()) else "副本已保存，部分回读核对未通过，请打开检查", verification=checks)
    except JobCancelled:
        update("cancelled", "已取消规范检查，未创建副本、未修改原稿")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        uncertain = state.get("saveAttempted") or (state.get("copyAttempted") and not state.get("draftUrl"))
        message = str(exc) if isinstance(exc, (ValueError, BridgeError)) else "秀米页面操作未完成。请查看保留的窗口及本地任务日志。"
        update("uncertain" if uncertain else "failed", message + (" 先检查副本，不会自动重试。" if state.get("copyAttempted") else " 原稿未修改。"))
    finally:
        if driver is not None:
            if options["leaveOpen"]:
                try:
                    driver.service.stop()
                except Exception:
                    pass
            else:
                try:
                    driver.quit()
                except Exception:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir")
    run_job(parser.parse_args().job_dir)
