"""Fournisseurs de modèles : Claude (API Anthropic) et alternatives.

La conversation est toujours stockée au format Anthropic (blocs de contenu).
Pour les autres fournisseurs, on la convertit à la volée :
- « openai » : tout service compatible OpenAI (OpenRouter, DeepSeek, Groq, LM Studio…)
- « ollama » : API native d'Ollama (modèles gratuits en local), qui permet de
  régler la taille du contexte.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .api import APIError, EventCallback

PRESETS: dict[str, dict] = {
    "anthropic": {"label": "Claude (Anthropic)", "kind": "anthropic", "base_url": "https://api.anthropic.com",
                  "needs_key": True, "default_model": "claude-opus-5-5"},
    "deepseek": {"label": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1",
                 "needs_key": True, "default_model": "deepseek-chat"},
    "openrouter": {"label": "OpenRouter (des centaines de modèles)", "kind": "openai",
                   "base_url": "https://openrouter.ai/api/v1", "needs_key": True,
                   "default_model": "deepseek/deepseek-chat"},
    "groq": {"label": "Groq (très rapide)", "kind": "openai", "base_url": "https://api.groq.com/openai/v1",
             "needs_key": True, "default_model": "llama-3.3-70b-versatile"},
    "ollama": {"label": "Ollama (gratuit, sur ton PC)", "kind": "ollama", "base_url": "http://127.0.0.1:11434",
               "needs_key": False, "default_model": ""},
    "custom": {"label": "Autre service compatible OpenAI", "kind": "openai", "base_url": "",
               "needs_key": False, "default_model": ""},
}

DEFAULT_PROVIDERS = {
    "anthropic": {"enabled": True, "priority": 1},
    "deepseek": {"enabled": False, "priority": 2, "api_key": "", "model": ""},
    "openrouter": {"enabled": False, "priority": 3, "api_key": "", "model": ""},
    "groq": {"enabled": False, "priority": 4, "api_key": "", "model": ""},
    "ollama": {"enabled": True, "priority": 5, "model": "", "base_url": "", "num_ctx": 16384},
    "custom": {"enabled": False, "priority": 6, "api_key": "", "model": "", "base_url": ""},
}

ALT_NOTE = (
    "\n\n# Modèle actuel\nTu fonctionnes en ce moment avec un modèle alternatif ({label} · {model}). "
    "Le contrôle de l'écran (souris/clavier) n'est pas disponible : utilise le terminal, les fichiers, "
    "open_path et les connecteurs. Pour Internet, utilise web_search_local puis fetch_url. "
    "Appelle les outils avec des arguments JSON valides, un outil à la fois si tu as un doute."
)


@dataclass
class Provider:
    id: str
    kind: str
    label: str
    model: str
    base_url: str
    api_key: str = ""
    vision: bool = False
    num_ctx: int = 16384
    max_tokens: int = 8192
    extra: dict = field(default_factory=dict)

    @property
    def display(self) -> str:
        return f"{self.label} · {self.model or 'modèle auto'}"


def merged_providers(settings: dict) -> dict[str, dict]:
    saved = settings.get("providers") or {}
    out = {}
    for pid, defaults in DEFAULT_PROVIDERS.items():
        cfg = dict(defaults)
        cfg.update(saved.get(pid) or {})
        out[pid] = cfg
    return out


def build_chain(settings: dict) -> tuple[list[Provider], list[str]]:
    """Fournisseurs utilisables, du plus prioritaire au moins prioritaire."""
    chain: list[tuple[int, Provider]] = []
    skipped: list[str] = []
    for pid, cfg in merged_providers(settings).items():
        if not cfg.get("enabled"):
            continue
        preset = PRESETS[pid]
        if pid == "anthropic":
            key, model = settings.get("api_key", ""), settings.get("model") or preset["default_model"]
        else:
            key, model = cfg.get("api_key", ""), cfg.get("model") or preset["default_model"]
        base = (cfg.get("base_url") or preset["base_url"]).rstrip("/")
        if preset["needs_key"] and not key:
            skipped.append(f"{preset['label']} (pas de clé)")
            continue
        if pid == "custom" and not (base and model):
            skipped.append(f"{preset['label']} (adresse ou modèle manquant)")
            continue
        prov = Provider(id=pid, kind=preset["kind"], label=preset["label"], model=model, base_url=base,
                        api_key=key, vision=bool(cfg.get("vision")), num_ctx=int(cfg.get("num_ctx") or 16384),
                        max_tokens=int(cfg.get("max_tokens") or (8192 if pid != "ollama" else 4096)))
        chain.append((int(cfg.get("priority") or 99), prov))
    chain.sort(key=lambda x: x[0])
    return [p for _, p in chain], skipped


async def ollama_models(base_url: str = "") -> list[str]:
    url = (base_url or PRESETS["ollama"]["base_url"]).rstrip("/") + "/api/tags"
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]


# ---------------------------------------------------------------- conversions

EDITOR_FUNCTION = {
    "name": "str_replace_based_edit_tool",
    "description": ("Voir, créer et modifier des fichiers texte. command=view (fichier ou dossier), "
                    "create (écrit file_text), str_replace (remplace old_str unique par new_str), "
                    "insert (ajoute insert_text après la ligne insert_line)."),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "enum": ["view", "create", "str_replace", "insert"]},
            "path": {"type": "string"},
            "file_text": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"},
            "insert_line": {"type": "integer"},
            "insert_text": {"type": "string"},
            "view_range": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["command", "path"],
    },
}


def openai_tools(custom_tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}}
            for t in [EDITOR_FUNCTION, *custom_tools]]


def _blocks(content) -> list[dict]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])


def _result_text(content) -> tuple[str, list[dict]]:
    texts, images = [], []
    for b in _blocks(content):
        if b.get("type") == "text":
            texts.append(b.get("text", ""))
        elif b.get("type") == "image":
            images.append(b)
    return "\n".join(texts) or "(ok)", images


def to_openai_messages(system: str, messages: list[dict], allowed: set[str], vision: bool) -> list[dict]:
    """Conversation Anthropic → messages OpenAI. Les outils inconnus deviennent du texte."""
    out: list[dict] = [{"role": "system", "content": system}]
    as_text: set[str] = set()
    for msg in messages:
        blocks = _blocks(msg["content"])
        if msg["role"] == "assistant":
            texts, calls = [], []
            for b in blocks:
                t = b.get("type")
                if t == "text":
                    texts.append(b.get("text", ""))
                elif t == "tool_use" and b.get("name") in allowed and not b.get("toolset_name"):
                    calls.append({"id": b["id"], "type": "function",
                                  "function": {"name": b["name"],
                                               "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}})
                elif t == "tool_use":
                    as_text.add(b["id"])
                    texts.append(f"[Action {b.get('name')} : {json.dumps(b.get('input') or {}, ensure_ascii=False)[:300]}]")
                elif t == "server_tool_use":
                    texts.append(f"[{b.get('name')} : {json.dumps(b.get('input') or {}, ensure_ascii=False)[:300]}]")
                elif t.endswith("_tool_result"):
                    texts.append(f"[Résultat {t.replace('_tool_result', '')} reçu]")
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts)}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        tool_msgs, parts, notes = [], [], []
        for b in blocks:
            t = b.get("type")
            if t == "tool_result":
                text, images = _result_text(b.get("content"))
                if b.get("is_error"):
                    text = "ERREUR : " + text
                if images:
                    text += f"\n[{len(images)} image(s) non transmise(s) à ce modèle]"
                if b["tool_use_id"] in as_text:
                    notes.append(f"[Résultat : {text[:1500]}]")
                else:
                    tool_msgs.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": text})
            elif t == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif t == "image" and vision:
                src = b["source"]
                parts.append({"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
            elif t in ("image", "document"):
                notes.append(f"[{'Image' if t == 'image' else 'Document'} joint non lisible par ce modèle : "
                             "utilise read_document avec le chemin indiqué]")
        out.extend(tool_msgs)
        if notes:
            parts.append({"type": "text", "text": "\n".join(notes)})
        if parts:
            if all(p["type"] == "text" for p in parts):
                out.append({"role": "user", "content": "\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": "user", "content": parts})
    return out


def to_ollama_messages(oa_messages: list[dict]) -> list[dict]:
    names: dict[str, str] = {}
    out = []
    for m in oa_messages:
        if m["role"] == "assistant":
            entry = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                entry["tool_calls"] = []
                for c in m["tool_calls"]:
                    names[c["id"]] = c["function"]["name"]
                    try:
                        args = json.loads(c["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    entry["tool_calls"].append({"function": {"name": c["function"]["name"], "arguments": args}})
            out.append(entry)
        elif m["role"] == "tool":
            out.append({"role": "tool", "content": m["content"], "tool_name": names.get(m["tool_call_id"], "")})
        elif isinstance(m.get("content"), list):
            text = "\n".join(p.get("text", "") for p in m["content"] if p["type"] == "text")
            images = [p["image_url"]["url"].split(",", 1)[1] for p in m["content"] if p["type"] == "image_url"]
            entry = {"role": m["role"], "content": text}
            if images:
                entry["images"] = images
            out.append(entry)
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def sanitize_for_anthropic(messages: list[dict], allowed: set[str]) -> list[dict]:
    """Retire ce que l'API Anthropic refuserait (réflexion d'un autre modèle, outils inconnus)."""
    as_text: set[str] = set()
    clean: list[dict] = []
    for msg in messages:
        blocks = []
        for b in _blocks(msg["content"]):
            t = b.get("type")
            if t == "thinking" and not b.get("signature"):
                continue
            if t == "tool_use" and not b.get("toolset_name") and b.get("name") not in allowed:
                as_text.add(b["id"])
                blocks.append({"type": "text", "text": f"[Action {b.get('name')} : "
                                                       f"{json.dumps(b.get('input') or {}, ensure_ascii=False)[:300]}]"})
                continue
            if t == "tool_result" and b.get("tool_use_id") in as_text:
                text, _ = _result_text(b.get("content"))
                blocks.append({"type": "text", "text": f"[Résultat : {text[:1500]}]"})
                continue
            blocks.append(b)
        if not blocks:
            blocks = [{"type": "text", "text": "…"}]
        if clean and clean[-1]["role"] == msg["role"]:
            clean[-1] = {"role": msg["role"], "content": _blocks(clean[-1]["content"]) + blocks}
        else:
            clean.append({"role": msg["role"], "content": blocks})
    return clean


# ---------------------------------------------------------------- appels

def _parse_args(raw: str) -> tuple[dict, bool]:
    try:
        val = json.loads(raw) if raw.strip() else {}
        return (val if isinstance(val, dict) else {"value": val}), True
    except json.JSONDecodeError:
        return {}, False


def _finish(text: str, thinking: str, calls: list[dict], stop: str, usage: dict) -> dict:
    content: list[dict] = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})  # sans signature : jamais renvoyé à Claude
    if text.strip():
        content.append({"type": "text", "text": text})
    for c in calls:
        content.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]})
    stop_reason = "tool_use" if calls else ("max_tokens" if stop in ("length", "max_tokens") else "end_turn")
    return {"content": content, "stop_reason": stop_reason, "usage": usage}


async def _emit_text(on_event: EventCallback, state: dict, kind: str, text: str) -> None:
    if not text:
        return
    idx = 0 if kind == "thinking" else 1
    if state.get("open") != kind:
        state["open"] = kind
        await on_event({"type": "block_start", "index": idx, "block": {"type": kind}})
    await on_event({"type": f"{kind}_delta" if kind == "thinking" else "text_delta", "index": idx, "text": text})


async def complete_openai(p: Provider, system: str, messages: list[dict], tools: list[dict],
                          on_event: EventCallback, allowed: set[str]) -> dict:
    body = {"model": p.model, "messages": to_openai_messages(system, messages, allowed, p.vision),
            "tools": openai_tools(tools), "stream": True, "max_tokens": p.max_tokens}
    headers = {"content-type": "application/json"}
    if p.api_key:
        headers["authorization"] = f"Bearer {p.api_key}"
    if p.id == "openrouter":
        headers.update({"HTTP-Referer": "https://atlas-assistant-blond.vercel.app", "X-Title": "Atlas"})
    text, thinking, stop, usage = "", "", "", {}
    calls: dict[int, dict] = {}
    state: dict = {}
    timeout = httpx.Timeout(connect=20, read=300, write=60, pool=20)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{p.base_url}/chat/completions", headers=headers, json=body) as resp:
                if resp.status_code != 200:
                    err = (await resp.aread()).decode("utf-8", "replace")[:500]
                    raise APIError(resp.status_code, f"{p.label} : erreur {resp.status_code} — {err}",
                                   resp.status_code in (429, 500, 502, 503, 529))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise APIError(500, f"{p.label} : {data['error']}")
                    if data.get("usage"):
                        usage = {"input_tokens": data["usage"].get("prompt_tokens", 0),
                                 "output_tokens": data["usage"].get("completion_tokens", 0)}
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        if reasoning:
                            thinking += reasoning
                            await _emit_text(on_event, state, "thinking", reasoning)
                        if delta.get("content"):
                            text += delta["content"]
                            await _emit_text(on_event, state, "text", delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "args": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
                        if choice.get("finish_reason"):
                            stop = choice["finish_reason"]
    except httpx.HTTPError as exc:
        raise APIError(0, f"{p.label} injoignable : {exc}") from exc
    parsed = []
    for _, c in sorted(calls.items()):
        args, ok = _parse_args(c["args"])
        if not ok:
            args = {"_erreur": "arguments JSON invalides", "_brut": c["args"][:500]}
        parsed.append({"id": c["id"] or f"call_{uuid.uuid4().hex[:12]}", "name": c["name"], "input": args})
    return _finish(text, thinking, parsed, stop, usage)


async def complete_ollama(p: Provider, system: str, messages: list[dict], tools: list[dict],
                          on_event: EventCallback, allowed: set[str]) -> dict:
    model = p.model
    if not model:
        try:
            installed = await ollama_models(p.base_url)
        except httpx.HTTPError as exc:
            raise APIError(0, "Ollama n'est pas lancé sur ce PC (ouvre l'application Ollama).") from exc
        if not installed:
            raise APIError(0, "Aucun modèle Ollama installé. Exemple : ollama pull qwen2.5:7b")
        model = installed[0]
    body = {"model": model, "messages": to_ollama_messages(to_openai_messages(system, messages, allowed, p.vision)),
            "tools": openai_tools(tools), "stream": True,
            "options": {"num_ctx": p.num_ctx, "num_predict": p.max_tokens}}
    text, thinking, stop, usage = "", "", "", {}
    calls: list[dict] = []
    state: dict = {}
    timeout = httpx.Timeout(connect=10, read=900, write=60, pool=10)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{p.base_url}/api/chat", json=body) as resp:
                if resp.status_code != 200:
                    err = (await resp.aread()).decode("utf-8", "replace")[:500]
                    raise APIError(resp.status_code, f"Ollama : erreur {resp.status_code} — {err}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise APIError(500, f"Ollama : {data['error']}")
                    msg = data.get("message") or {}
                    if msg.get("thinking"):
                        thinking += msg["thinking"]
                        await _emit_text(on_event, state, "thinking", msg["thinking"])
                    if msg.get("content"):
                        text += msg["content"]
                        await _emit_text(on_event, state, "text", msg["content"])
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            args, _ = _parse_args(args)
                        calls.append({"id": f"call_{uuid.uuid4().hex[:12]}", "name": fn.get("name", ""), "input": args})
                    if data.get("done"):
                        stop = data.get("done_reason", "stop")
                        usage = {"input_tokens": data.get("prompt_eval_count", 0),
                                 "output_tokens": data.get("eval_count", 0)}
    except httpx.ConnectError as exc:
        raise APIError(0, "Ollama n'est pas lancé sur ce PC (ouvre l'application Ollama).") from exc
    except httpx.HTTPError as exc:
        raise APIError(0, f"Ollama : {exc}") from exc
    return _finish(text, thinking, calls, stop, usage)
