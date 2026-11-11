from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import threading
import uuid
import webbrowser
from functools import cached_property
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .bridge import availability, draft_url
from .document import BUILTIN_IMAGES, SAMPLE, THEMES, normalize, render, standalone
from .storage import DraftStore, now, valid_id, write_json
from .reference import ReferenceError, analyze_article, fetch_article
from .ai import AIError, AIService
from .standards_service import StandardsService
from .standards import PROFILE
from .paths import ROOT, WEB, default_data_dir

MAX_REQUEST = 35_000_000
TERMINAL = {"failed", "cancelled", "uncertain", "verified", "review_required", "interrupted"}


class Application:
    def __init__(self, data_dir=None):
        self.data = Path(data_dir or default_data_dir()).resolve()
        self.drafts = DraftStore(self.data / "drafts")
        self.procs = {}
        self.lock = threading.Lock()
        self.data.mkdir(parents=True, exist_ok=True)
        self.ai = AIService(self.data / "ai-jobs")
        self.standards = StandardsService(self)
        # A service restart does not silently retry remote writes.
        for path in (self.data / "jobs").glob("*/status.json"):
            try:
                status = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(status, dict):
                    continue
            except (ValueError, OSError):
                continue
            if status.get("stage") not in TERMINAL:
                status.update(stage="interrupted", message="服务已重启，请先查看秀米，再决定是否重新发送", updatedAt=now())
                write_json(path, status)

    @cached_property
    def _sample_urls(self):
        return {source: "data:" + mimetypes.guess_type(filename)[0] + ";base64," + base64.b64encode((WEB / filename).read_bytes()).decode()
                for source, filename in BUILTIN_IMAGES.items()}

    def sample_url(self):
        return self._sample_urls

    def reference_list(self):
        result = []
        for path in (self.data / "references").glob("*/reference.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                result.append({k: record.get(k) for k in ["id", "title", "url", "createdAt"]})
            except (ValueError, OSError):
                continue
        return sorted(result, key=lambda r: r.get("createdAt") or "", reverse=True)[:30]

    def reference(self, reference_id):
        if not valid_id(reference_id):
            raise ValueError("参考记录编号无效")
        folder = self.data / "references" / reference_id
        record = json.loads((folder / "reference.json").read_text(encoding="utf-8"))
        # Upgrade the cached source on demand; never overwrite a user's draft.
        if record.get("layoutRevision") != 4 and (folder / "source.html").exists():
            result = analyze_article((folder / "source.html").read_text(encoding="utf-8"), record.get("url", ""), record.get("title", ""))
            result.update({k: record[k] for k in ["id", "createdAt", "source"] if k in record})
            result["profile"]["id"] = reference_id
            write_json(folder / "reference.json", result)
            record = result
        return record

    def read_reference(self, data):
        source_url = ""
        if isinstance(data.get("html"), str) and data["html"].strip():
            source = data["html"]
            source_name = str(data.get("filename", "导入的 HTML"))[:200]
            mode = "html"
        else:
            source, source_url = fetch_article(data.get("url", ""))
            source_name = ""
            mode = "url"
        result = analyze_article(source, source_url, source_name)
        reference_id = uuid.uuid4().hex
        result.update(id=reference_id, createdAt=now(), source=mode)
        result["profile"]["id"] = reference_id
        folder = self.data / "references" / reference_id
        write_json(folder / "reference.json", result)
        (folder / "source.html").write_text(source, encoding="utf-8")
        return result

    def export(self, doc):
        doc = normalize(doc)
        export_id = uuid.uuid4().hex
        folder = self.data / "exports" / export_id
        folder.mkdir(parents=True)
        (folder / "article.html").write_text(standalone(doc, self.sample_url()), encoding="utf-8")
        write_json(folder / "document.json", doc)
        return {"id": export_id, "url": f"/api/exports/{export_id}/article.html"}

    def start_job(self, doc, request_id=None, options=None):
        doc = normalize(doc)
        if options is not None and not isinstance(options, dict):
            raise ValueError("发送选项格式无效")
        options = {"rememberLogin": (options or {}).get("rememberLogin", False), "leaveOpen": (options or {}).get("leaveOpen", True)}
        if any(type(value) is not bool for value in options.values()):
            raise ValueError("登录和窗口选项必须是布尔值")
        if request_id is not None and not valid_id(request_id):
            raise ValueError("发送请求编号无效")
        fingerprint = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            # The browser keeps this id before POST. A lost response, double
            # click or explicit same-request retry must not create two drafts.
            if request_id and (self.data / "jobs" / request_id / "status.json").exists():
                existing = self.job(request_id)
                if existing.get("documentHash") != fingerprint:
                    raise ValueError("同一发送请求不能改成另一篇文稿")
                if existing.get("options", {"rememberLogin": False, "leaveOpen": True}) != options:
                    raise ValueError("同一发送请求不能更改登录或窗口选项")
                return existing
            if not availability()["available"]:
                raise ValueError("本地秀米适配器尚未就绪，请先导出 HTML")
            if any(p.poll() is None for p in self.procs.values()):
                raise ValueError("已有一个接入任务正在运行，请先完成或取消")
            job_id = request_id or uuid.uuid4().hex
            folder = self.data / "jobs" / job_id
            status = {"id": job_id, "stage": "queued", "message": "接入测试已排队", "createdAt": now(), "updatedAt": now(), "draftUrl": None,
                      "title": doc["title"], "documentHash": fingerprint, "saveAttempted": False, "options": options}
            write_json(folder / "document.json", doc)
            write_json(folder / "status.json", status)
            (folder / "article.html").write_text(standalone(doc, self.sample_url()), encoding="utf-8")
            with (folder / "worker.log").open("w", encoding="utf-8") as log:
                try:
                    self.procs[job_id] = subprocess.Popen([sys.executable, "-m", "paperstudio.bridge", str(folder)], cwd=ROOT, stdout=log, stderr=log,
                        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WANYOU_SELENIUM_BROWSER": "chrome"},
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except OSError:
                    status.update(stage="failed", message="无法启动本地接入进程")
                    write_json(folder / "status.json", status)
                    raise
            return status

    def job(self, job_id):
        if not valid_id(job_id):
            raise ValueError("任务编号无效")
        path = self.data / "jobs" / job_id / "status.json"
        status = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(status, dict) or status.get("id") != job_id or not isinstance(status.get("stage"), str):
            raise ValueError("发送记录格式损坏，请检查本地任务目录")
        proc = self.procs.get(job_id)
        if proc is not None and proc.poll() is not None and status["stage"] not in TERMINAL:
            status.update(stage="interrupted", message="接入进程提前退出，请先查看秀米草稿", updatedAt=now())
            write_json(path, status)
        if status.get("draftUrl") and not draft_url(status["draftUrl"]):
            status["draftUrl"] = None
        # Explain the old adapter's specific login TimeoutError without
        # rewriting historical evidence or guessing from the exception alone.
        if status["stage"] == "failed" and not status.get("errorCode") and not status.get("saveAttempted") and "TimeoutError" in status.get("message", ""):
            try:
                with (path.parent / "worker.log").open("rb") as log:
                    log.seek(0, 2)
                    log.seek(max(0, log.tell() - 8192))
                    trace = log.read()
                if b'raise TimeoutError(' in trace and b'bridge.py' in trace and b'run_job' in trace:
                    status.update(errorCode="login_timeout", failedStage="waiting_login",
                                  message="上次等待登录超过 10 分钟，未写入内容、未保存。新版已接入 all-to-xiumi 登录入口流程；请准备新任务后，在新打开的窗口内完成登录。")
            except OSError:
                pass
        return status

    def job_list(self):
        records = []
        for path in (self.data / "jobs").glob("*/status.json"):
            if not valid_id(path.parent.name):
                continue
            try:
                record = self.job(path.parent.name)
                records.append({k: record.get(k) for k in ("id", "title", "stage", "message", "createdAt", "updatedAt", "draftUrl", "saveAttempted")})
            except (ValueError, KeyError, TypeError, OSError):
                continue
        return sorted(records, key=lambda r: r.get("createdAt") or "", reverse=True)[:30]


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperStudio/0.1"

    @property
    def app(self):
        return self.server.app

    def local_request(self, writing=False):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            return False
        if writing and self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        return True

    def send_bytes(self, body, content_type, status=200, download=None, cache_control="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def do_GET(self):
        if not self.local_request():
            return self.json({"error": "仅支持本机同源访问"}, 403)
        path = urlsplit(self.path).path
        try:
            if path == "/api/health":
                return self.json({"ok": True, "version": __version__})
            if path == "/api/standards":
                return self.json({"profile": PROFILE, "bridge": availability(), "jobs": self.app.standards.list()})
            if path.startswith("/api/standards/jobs/"):
                return self.json(self.app.standards.get(path.rsplit("/", 1)[1]))
            if path == "/api/ai/settings":
                return self.json(self.app.ai.status())
            if path.startswith("/api/ai/jobs/"):
                return self.json(self.app.ai.get(path.rsplit("/",1)[1]))
            if path == "/api/bootstrap":
                return self.json({"sample": {**SAMPLE, "theme": "doocs-classic", "blockSpacing": .65}, "themes": THEMES, "bridge": availability(), "drafts": self.app.drafts.list(), "references": self.app.reference_list()})
            if path == "/api/references":
                return self.json(self.app.reference_list())
            if path.startswith("/api/references/"):
                return self.json(self.app.reference(path.rsplit("/", 1)[1]))
            if path == "/api/drafts":
                return self.json(self.app.drafts.list())
            if path.startswith("/api/drafts/"):
                return self.json(self.app.drafts.get(path.rsplit("/", 1)[1]))
            if path == "/api/xiumi/jobs":
                return self.json(self.app.job_list())
            if path.startswith("/api/jobs/"):
                return self.json(self.app.job(path.rsplit("/", 1)[1]))
            if path.startswith("/api/exports/"):
                parts = path.split("/")
                if len(parts) != 5 or not valid_id(parts[3]) or parts[4] != "article.html":
                    return self.json({"error": "导出编号无效"}, 404)
                return self.send_bytes((self.app.data / "exports" / parts[3] / "article.html").read_bytes(), "text/html; charset=utf-8", download="paper-studio-article.html")
            files = {"/": "index.html", "/compact.css": "compact.css", "/standards.js": "standards.js", "/standards.css": "standards.css", "/app.js": "app.js", "/ai.js": "ai.js", "/styles.css": "styles.css", "/creative": "creative.html", "/creative.css": "creative.css", "/creative.js": "creative.js", **BUILTIN_IMAGES}
            if path in files:
                file = WEB / files[path]
                return self.send_bytes(file.read_bytes(), mimetypes.guess_type(str(file))[0] + ("; charset=utf-8" if file.suffix in {".html", ".js", ".css"} else ""),
                                       cache_control="private, max-age=3600" if path in BUILTIN_IMAGES else "no-store")
            return self.json({"error": "未找到页面"}, 404)
        except FileNotFoundError:
            self.json({"error": "未找到记录"}, 404)
        except ValueError as exc:
            self.json({"error": str(exc)}, 400)

    def do_POST(self):
        if not self.local_request(writing=True):
            return self.json({"error": "仅支持本机同源操作"}, 403)
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.json({"error": "请使用 JSON 请求"}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST:
                return self.json({"error": "请求为空或超过 35 MB"}, 413)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("请求格式无效")
            path = urlsplit(self.path).path
            if path == "/api/standards/jobs":
                return self.json(self.app.standards.start(data), 202)
            if path.startswith("/api/standards/jobs/"):
                parts = path.split("/")
                if len(parts) != 6 or parts[5] not in {"report", "apply", "cancel"}:
                    raise ValueError("规范检查操作无效")
                job_id, action = parts[4], parts[5]
                if action == "report":
                    return self.json(self.app.standards.reclassify(job_id, data))
                if action == "apply":
                    return self.json(self.app.standards.apply(job_id, data), 202)
                return self.json(self.app.standards.cancel(job_id))
            if path == "/api/render":
                images = None if data.get("preview") is True else self.app.sample_url()
                return self.json({"html": render(data.get("document"), images)})
            if path == "/api/ai/settings":
                return self.json(self.app.ai.configure(data))
            if path == "/api/ai/jobs":
                return self.json(self.app.ai.start(data.get("document"),data.get("requirement")),202)
            if path == "/api/references":
                return self.json(self.app.read_reference(data), 201)
            if path == "/api/drafts":
                return self.json(self.app.drafts.save(data.get("document"), data.get("id")))
            if path == "/api/exports":
                return self.json(self.app.export(data.get("document")), 201)
            if path == "/api/xiumi/jobs":
                if data.get("destination") != "new_draft":
                    raise ValueError("只支持新建秀米测试草稿")
                return self.json(self.app.start_job(data.get("document"), data.get("requestId"), data.get("options")), 202)
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[3]
                current = self.app.job(job_id)
                if current["stage"] in {"saving", "verifying"} | TERMINAL:
                    raise ValueError("保存已开始或任务已结束，请先检查结果")
                (self.app.data / "jobs" / job_id / "cancel.request").touch()
                return self.json({"requested": True})
            return self.json({"error": "未找到操作"}, 404)
        except (ReferenceError,AIError) as exc:
            self.json({"error": str(exc), "code": exc.code}, 422)
        except (ValueError, TypeError, KeyError) as exc:
            self.json({"error": str(exc)}, 400)
        except FileNotFoundError:
            self.json({"error": "未找到记录"}, 404)
        except OSError:
            self.json({"error": "本地文件或进程操作失败，请检查任务目录"}, 500)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


def create_server(port=8765, data_dir=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = Application(data_dir)
    return server


def main():
    parser = argparse.ArgumentParser(description="纸间 · 本地推文排版工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--data-dir", type=Path, help="本机数据目录（默认源码目录下 data 或用户应用数据目录）")
    args = parser.parse_args()
    server = create_server(args.port, args.data_dir)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"纸间 Paper Studio v{__version__}\nLocal: {url}\nPress Ctrl+C to stop.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
