"""Locate checked-out or installed assets without writing into site-packages."""
from __future__ import annotations

import os
import sys
from importlib.resources import files
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_CHECKOUT = (ROOT / "pyproject.toml").is_file() and (ROOT / "web" / "index.html").is_file()
WEB = ROOT / "web" if SOURCE_CHECKOUT else Path(str(files("paperstudio_web")))


def default_data_dir():
    explicit = os.environ.get("PAPER_STUDIO_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if SOURCE_CHECKOUT:
        return ROOT / "data"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PaperStudio" / "data"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "paper-studio"
