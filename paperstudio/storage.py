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


class DraftStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()

    def save(self, document, draft_id=None):
        document = normalize(document)
        if draft_id is not None and not valid_id(draft_id):
            raise ValueError("草稿编号无效")
        draft_id = draft_id or uuid.uuid4().hex
        record = {"id": draft_id, "updatedAt": now(), "document": document}
        with self.lock:
            write_json(self.root / f"{draft_id}.json", record)
        return record

    def get(self, draft_id):
        if not valid_id(draft_id):
            raise ValueError("草稿编号无效")
        return json.loads((self.root / f"{draft_id}.json").read_text(encoding="utf-8"))

    def list(self):
        result = []
        for path in self.root.glob("*.json"):
            try:
                r = json.loads(path.read_text(encoding="utf-8"))
                result.append({"id": r["id"], "title": r["document"]["title"], "theme": r["document"]["theme"], "reference": bool(r["document"].get("referenceStyle")), "updatedAt": r["updatedAt"]})
            except (ValueError, KeyError):
                continue
        return sorted(result, key=lambda r: r["updatedAt"], reverse=True)
