from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .document import normalize


def now():
    return datetime.now(timezone.utc).isoformat()


def valid_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{32}", value))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class DraftConflict(Exception):
    pass


class DraftStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()

    def save(self, document, draft_id=None, expected_version=None, write_id=None):
        document = normalize(document)
        if draft_id is not None and not valid_id(draft_id):
            raise ValueError("草稿编号无效")
        if expected_version not in (None, "legacy") and not valid_id(expected_version):
            raise ValueError("草稿版本无效")
        if write_id is not None and not valid_id(write_id):
            raise ValueError("保存编号无效")
        draft_id = draft_id or uuid.uuid4().hex
        path = self.root / f"{draft_id}.json"
        with self.lock:
            try:
                current = self.get(draft_id)
            except FileNotFoundError:
                current = None
            if current and current["version"] == write_id:
                if current["document"] == document:
                    return current
                raise DraftConflict("保存请求已用于另一份内容，请将当前编辑另存为副本。")
            current_version = current["version"] if current else None
            if current_version != expected_version:
                raise DraftConflict("草稿版本已变化，请将当前编辑另存为副本。")
            record = {"id": draft_id, "version": write_id or uuid.uuid4().hex,
                      "updatedAt": now(), "document": document}
            write_json(path, record)
        return record

    def get(self, draft_id):
        if not valid_id(draft_id):
            raise ValueError("草稿编号无效")
        record = json.loads((self.root / f"{draft_id}.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("id") != draft_id or not isinstance(record.get("document"), dict):
            raise ValueError("草稿文件格式无效")
        if "version" in record and not valid_id(record["version"]):
            raise ValueError("草稿版本无效")
        # Drafts saved before version checking can be edited without migrating files.
        record.setdefault("version", "legacy")
        return record

    def list(self):
        result = []
        for path in self.root.glob("*.json"):
            try:
                r = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(r, dict) or not isinstance(r.get("document"), dict):
                    continue
                if not valid_id(r.get("id")) or r["id"] != path.stem:
                    continue
                if not all(isinstance(value, str) for value in (r.get("updatedAt"), r["document"].get("title"), r["document"].get("theme"))):
                    continue
                result.append({"id": r["id"], "title": r["document"]["title"], "theme": r["document"]["theme"], "reference": bool(r["document"].get("referenceStyle")), "updatedAt": r["updatedAt"]})
            except (ValueError, KeyError, OSError):
                continue
        return sorted(result, key=lambda r: r["updatedAt"], reverse=True)
