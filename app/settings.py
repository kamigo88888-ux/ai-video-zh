"""Small persistent settings store (data/settings.json) + paths."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("SUBZH_DATA", ROOT / "data"))
JOBS = DATA / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
_FILE = DATA / "settings.json"
_lock = threading.Lock()

DEFAULTS = {
    "anthropic_api_key": "",
    "model": "claude-opus-5-5",
    "tone": "humor",
    "crf": 16,
    "preset": "medium",
}


def _load_env_file():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file()


def load() -> dict:
    with _lock:
        d = dict(DEFAULTS)
        if _FILE.exists():
            try:
                d.update(json.loads(_FILE.read_text()))
            except Exception:
                pass
        return d


def save(updates: dict) -> dict:
    d = load()
    for k, v in updates.items():
        if k in DEFAULTS:
            d[k] = v
    with _lock:
        _FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    return d


# SUBZH_ANTHROPIC_API_KEY is checked first: in hosted environments (e.g. Claude Code
# cloud sessions) ANTHROPIC_API_KEY may be reserved for the host and not reach programs.
ENV_KEY_NAMES = ("SUBZH_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


def api_key_from_env() -> str:
    for name in ENV_KEY_NAMES:
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    return ""


def api_key() -> str:
    return api_key_from_env() or load().get("anthropic_api_key", "")


def model() -> str:
    return os.environ.get("SUBZH_MODEL") or load().get("model") or DEFAULTS["model"]
