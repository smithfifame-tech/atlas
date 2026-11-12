"""Chemins, réglages persistants et informations système."""

from __future__ import annotations

import json
import os
import platform
import secrets
import threading
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONV_DIR = DATA_DIR / "conversations"
BACKUP_DIR = DATA_DIR / "backups"
UPLOAD_DIR = DATA_DIR / "uploads"
LOG_DIR = DATA_DIR / "logs"
CONFIG_DIR = ROOT / "config"
STATIC_DIR = ROOT / "static"
SETTINGS_FILE = DATA_DIR / "settings.json"
MEMORY_FILE = DATA_DIR / "memoire.md"
TOKEN_FILE = DATA_DIR / "token"
MCP_CONFIG_FILE = CONFIG_DIR / "mcp_servers.json"

for d in (DATA_DIR, CONV_DIR, BACKUP_DIR, UPLOAD_DIR, LOG_DIR, CONFIG_DIR):
    d.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5 (le plus capable)",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (rapide, moins cher, très précis au clic)",
    "claude-haiku-5-5": "Claude Haiku 5.5 (le plus économique)",
}

APPROVAL_MODES = {
    "prudent": "Demande avant toute action qui modifie quelque chose",
    "equilibre": "Demande seulement pour les actions sensibles ou irréversibles",
    "autonome": "N'interrompt que pour les actions catastrophiques",
}

DEFAULTS: dict = {
    "api_key": "",
    "model": "claude-opus-5-5",
    "assistant_name": "Atlas",
    "user_name": "",
    "approval_mode": "equilibre",
    "computer_use": True,
    "web_search": True,
    "web_fetch": True,
    "show_thinking": True,
    "max_tokens": 32000,
    "max_steps": 150,
    "screenshot_max_width": 1366,
    "working_dir": str(Path.home()),
    "custom_instructions": "",
    "location_country": "",
    "location_city": "",
    "location_timezone": "",
    "port": 8765,
    "providers": {},
}

_lock = threading.Lock()


ENV_MAP = {
    "ANTHROPIC_API_KEY": "api_key",
    "ASSISTANT_MODEL": "model",
    "ASSISTANT_NAME": "assistant_name",
    "ASSISTANT_PORT": "port",
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    # 1) Le fichier .env fournit des valeurs par défaut…
    for env, key in ENV_MAP.items():
        val = os.environ.get(env, "").strip()
        if val:
            data[key] = int(val) if key == "port" else val
    # 2) …que les réglages enregistrés depuis l'interface remplacent.
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            data.update({k: v for k, v in saved.items() if v not in ("", None) or k != "api_key"})
        except (OSError, json.JSONDecodeError):
            pass
    return data


def save_settings(updates: dict) -> dict:
    with _lock:
        current = {}
        if SETTINGS_FILE.exists():
            try:
                current = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = {}
        for key, value in updates.items():
            if key in DEFAULTS:
                current[key] = value
        SETTINGS_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return load_settings()


def _hint(key: str) -> str:
    return f"…{key[-4:]}" if key else ""


def public_settings(settings: dict) -> dict:
    """Réglages envoyés à l'interface (les clés API sont masquées)."""
    from .providers import PRESETS, merged_providers

    out = dict(settings)
    key = out.pop("api_key", "")
    out["api_key_set"] = bool(key)
    out["api_key_hint"] = _hint(key)
    providers = {}
    for pid, cfg in merged_providers(settings).items():
        cfg = dict(cfg)
        k = cfg.pop("api_key", "")
        cfg.update({"api_key_set": bool(k), "api_key_hint": _hint(k), "label": PRESETS[pid]["label"],
                    "default_model": PRESETS[pid]["default_model"], "needs_key": PRESETS[pid]["needs_key"],
                    "default_base_url": PRESETS[pid]["base_url"]})
        providers[pid] = cfg
    out["providers"] = providers
    return out


def merge_provider_updates(current: dict, updates: dict) -> dict:
    """Fusionne les réglages des modèles ; un champ de clé laissé vide garde l'ancienne clé."""
    from .providers import DEFAULT_PROVIDERS

    merged = {pid: dict(cfg) for pid, cfg in (current or {}).items()}
    for pid, upd in (updates or {}).items():
        if pid not in DEFAULT_PROVIDERS or not isinstance(upd, dict):
            continue
        entry = merged.setdefault(pid, {})
        for k in ("enabled", "priority", "api_key", "model", "base_url", "vision", "num_ctx", "max_tokens"):
            if k not in upd:
                continue
            if k == "api_key" and not str(upd[k]).strip():
                continue
            entry[k] = upd[k]
    return merged


def get_access_token() -> str:
    """Jeton secret exigé par l'interface locale (protège contre les autres sites web)."""
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


def system_info() -> dict:
    return {
        "os": f"{platform.system()} {platform.release()}",
        "os_family": platform.system(),
        "machine": platform.machine(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "home": str(Path.home()),
        "shell": "PowerShell" if IS_WINDOWS else os.environ.get("SHELL", "/bin/bash"),
        "python": platform.python_version(),
    }
