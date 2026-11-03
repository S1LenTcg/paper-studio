from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .bridge import availability
from .standards import PROFILE, audit, paper_id, validate_plan
from .storage import now, valid_id, write_json

TERMINAL = {"verified", "review_required", "failed", "cancelled", "uncertain", "interrupted"}


class StandardsService:
    def __init__(self, app):
        self.app = app
        self.root = app.data / "standard-jobs"
        for path in self.root.glob("*/status.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("stage") not in TERMINAL:
                    state.update(stage="interrupted", message="服务已重启。请先检查副本记录和秀米窗口；不会自动继续修改。", updatedAt=now())
                    write_json(path, state)
            except (OSError, ValueError):
                continue

    def folder(self, job_id):
        if not valid_id(job_id):
            raise ValueError("检查任务编号无效")
        return self.root / job_id

    def get(self, job_id, report=True):
        folder = self.folder(job_id)
        state = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        proc = self.app.procs.get("norm:" + job_id)
        if proc is not None and proc.poll() is not None and state["stage"] not in TERMINAL:
            state.update(stage="interrupted", message="检查进程已退出。先查看秀米及副本链接，不会自动重试。", updatedAt=now())
            write_json(folder / "status.json", state)
        if report and (folder / "report.json").exists():
            state["report"] = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        return state

    def list(self):
        records = []
        for p in self.root.glob("*/status.json"):
            if not valid_id(p.parent.name):
                continue
            try:
                state = self.get(p.parent.name, False)
                records.append({k: state.get(k) for k in ("id", "title", "sourceUrl", "draftUrl", "stage", "message", "createdAt", "copyAttempted", "saveAttempted")})
            except (OSError, ValueError):
                continue
        return sorted(records, key=lambda r: r.get("createdAt") or "", reverse=True)[:30]

    def start(self, data):
        url = data.get("url")
        paper_id(url)
        job_id = data.get("requestId")
        self.folder(job_id)
        options = data.get("options", {})
        if not isinstance(options, dict) or any(type(options.get(k, d)) is not bool for k, d in (("rememberLogin", False), ("leaveOpen", True))):
            raise ValueError("登录和窗口选项无效")
        options = {"rememberLogin": options.get("rememberLogin", False), "leaveOpen": options.get("leaveOpen", True)}
        with self.app.lock:
            if (self.folder(job_id) / "status.json").exists():
                old = self.get(job_id)
                if old["sourceUrl"] != url or old["options"] != options:
                    raise ValueError("同一检查请求不能更改链接或登录选项")
                return old
            if not availability()["available"]:
                raise ValueError("本地秀米适配器尚未就绪")
            if any(p.poll() is None for p in self.app.procs.values()):
                raise ValueError("已有秀米任务正在运行，请先完成或取消")
            folder = self.folder(job_id)
            state = {"id": job_id, "stage": "queued", "message": "规范检查已排队", "sourceUrl": url,
                     "title": "秀米文字规范检查", "profile": PROFILE["id"], "options": options,
                     "copyAttempted": False, "saveAttempted": False, "createdAt": now(), "updatedAt": now()}
            write_json(folder / "status.json", state)
            with (folder / "worker.log").open("w", encoding="utf-8") as log:
                try:
                    self.app.procs["norm:" + job_id] = subprocess.Popen([sys.executable, "-m", "paperstudio.standards_worker", str(folder)],
                        cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=log,
                        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WANYOU_SELENIUM_BROWSER": "chrome"},
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except OSError:
                    state.update(stage="failed", message="无法启动规范检查进程")
                    write_json(folder / "status.json", state)
                    raise
            return state

    def reclassify(self, job_id, data):
        with self.app.lock:
            state = self.get(job_id)
            if state["stage"] != "awaiting_review" or state.get("planSubmitted"):
                raise ValueError("当前任务不在等待确认阶段")
            snapshot = json.loads((self.folder(job_id) / "snapshot.json").read_text(encoding="utf-8"))
            result = audit(snapshot, data.get("roles"), data.get("bodyColor", "#565656"))
            write_json(self.folder(job_id) / "report.json", result)
            return result

    def apply(self, job_id, data):
        with self.app.lock:
            state = self.get(job_id)
            folder = self.folder(job_id)
            if (folder / "plan.json").exists():
                old = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
                if old == {k: data.get(k) for k in old}:
                    return state
                raise ValueError("该任务已经提交修正计划，请查看原任务结果")
            if state["stage"] != "awaiting_review":
                raise ValueError("任务当前不能修正，请重新检查")
            report = state["report"]
            plan = validate_plan(report, data)
            snapshot = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))
            checked = audit(snapshot, plan["roles"], plan["bodyColor"])
            allowed = {i["id"] for i in checked["issues"] if i["automatic"]}
            if set(plan["selected"]) - allowed:
                raise ValueError("选项已变化或包含只能人工处理的问题，请重新核对")
            state.update(planSubmitted=True, message="已提交修正计划，正在准备副本", updatedAt=now())
            write_json(folder / "status.json", {k: v for k, v in state.items() if k != "report"})
            write_json(folder / "plan.json", plan)
            return state

    def cancel(self, job_id):
        with self.app.lock:
            state = self.get(job_id)
            if state["stage"] not in {"queued", "opening", "waiting_login", "inspecting", "awaiting_review"} or state.get("planSubmitted"):
                raise ValueError("副本操作已开始或任务已结束，请先查看结果")
            (self.folder(job_id) / "cancel.request").touch()
            return {"requested": True}
