"""Boucle d'agent : envoie la conversation à Claude, exécute les outils, recommence."""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from . import safety
from .api import APIError, stream_message
from .config import CONV_DIR, load_settings
from .prompt import build_system_prompt
from .tools import ToolResult
from .tools import computer as computer_mod
from .tools.connectors import manager
from .tools.registry import build_tools, dispatch

Emit = Callable[[dict], Awaitable[None]]
AskApproval = Callable[[dict], Awaitable[dict]]

HALT_TEXT = "Not executed: an earlier computer action in this turn failed."
INTERRUPTED = "Interrompu par l'utilisateur."


class Conversation:
    def __init__(self, conv_id: str | None = None):
        self.id = conv_id or uuid.uuid4().hex[:12]
        self.title = "Nouvelle conversation"
        self.created = time.time()
        self.updated = self.created
        self.model = ""
        self.system = ""
        self.tools: list[dict] = []
        self.messages: list[dict] = []
        self.ui_log: list[dict] = []
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}
        self.allowed: set[str] = set()  # autorisations « toujours » de cette session

    # ---------- persistance
    @property
    def path(self):
        return CONV_DIR / f"{self.id}.json"

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "created": self.created, "updated": self.updated,
                "model": self.model, "system": self.system, "tools": self.tools, "messages": self.messages,
                "ui_log": self.ui_log, "usage": self.usage}

    def save(self) -> None:
        self.updated = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    @classmethod
    def load(cls, conv_id: str) -> "Conversation":
        path = CONV_DIR / f"{conv_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        conv = cls(data["id"])
        for key in ("title", "created", "updated", "model", "system", "tools", "messages", "ui_log", "usage"):
            if key in data:
                setattr(conv, key, data[key])
        return conv

    def summary(self) -> dict:
        return {"id": self.id, "title": self.title, "updated": self.updated, "model": self.model}

    def log(self, entry: dict) -> None:
        entry.setdefault("t", time.time())
        self.ui_log.append(entry)


def list_conversations() -> list[dict]:
    out = []
    for path in CONV_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as fh:
                head = json.load(fh)
            out.append({"id": head["id"], "title": head.get("title", "?"), "updated": head.get("updated", 0)})
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def _summarize_input(name: str, inp: dict) -> str:
    if name == "run_command":
        return inp.get("command", "")
    if name == "str_replace_based_edit_tool":
        return f"{inp.get('command')} {inp.get('path', '')}"
    if name in ("find_files",):
        return f"{inp.get('pattern')} dans {inp.get('root') or 'dossier de travail'}"
    if name == "search_in_files":
        return f"« {inp.get('query')} » dans {inp.get('root') or 'dossier de travail'}"
    if name == "connector_call":
        return f"{inp.get('server')} → {inp.get('tool')}"
    for key in ("path", "target", "source", "action", "query", "url", "text", "coordinate", "region"):
        if key in inp:
            return f"{key}: {str(inp[key])[:160]}"
    return json.dumps(inp, ensure_ascii=False)[:160]


class Agent:
    """Une instance par tâche en cours."""

    def __init__(self, conv: Conversation, emit: Emit, ask_approval: AskApproval):
        self.conv = conv
        self.emit = emit
        self.ask_approval = ask_approval
        self.settings = load_settings()
        self.computer: computer_mod.Computer | None = None
        self._pending: list[dict] | None = None

    # ---------- préparation
    def _prepare(self) -> None:
        conv = self.conv
        ok, reason = computer_mod.available() if self.settings.get("computer_use") else (False, "désactivé")
        if not conv.messages:
            # Les outils et les instructions sont figés au début de la conversation :
            # les changer en cours invaliderait la réflexion déjà envoyée à l'API.
            conv.model = self.settings["model"]
            conv.tools = build_tools(self.settings, ok)
            conv.system = build_system_prompt(self.settings, ok, reason)
        if any(t.get("type", "").startswith("computer_") for t in conv.tools):
            self.computer = computer_mod.Computer(int(self.settings.get("screenshot_max_width", 1366)))

    def _payload(self) -> dict:
        messages = self.conv.messages
        if messages and messages[-1]["role"] == "user":
            last = copy.copy(messages[-1])
            content = last["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            content = list(content)
            content[-1] = dict(content[-1], cache_control={"type": "ephemeral"})
            last["content"] = content
            messages = messages[:-1] + [last]
        payload: dict[str, Any] = {
            "model": self.conv.model,
            "max_tokens": int(self.settings.get("max_tokens", 32000)),
            "system": [{"type": "text", "text": self.conv.system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "tools": self.conv.tools,
        }
        if self.settings.get("show_thinking", True):
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        return payload

    # ---------- boucle principale
    async def run(self, user_content: list[dict]) -> None:
        conv = self.conv
        self._prepare()
        if not self.settings.get("api_key"):
            await self.emit({"type": "error", "message": "Aucune clé API : ajoute-la dans les Réglages (⚙)."})
            return
        if not conv.messages:
            first_text = next((b.get("text", "") for b in user_content if b.get("type") == "text"), "")
            conv.title = (first_text.strip().splitlines() or ["Nouvelle conversation"])[0][:70] or "Nouvelle conversation"
            await self.emit({"type": "title", "title": conv.title})
        if conv.messages and conv.messages[-1]["role"] == "user":
            # Ex. tâche précédente arrêtée avant la réponse : on fusionne pour garder l'alternance.
            prev = conv.messages[-1]["content"]
            if isinstance(prev, str):
                prev = [{"type": "text", "text": prev}]
            conv.messages[-1]["content"] = list(prev) + user_content
        else:
            conv.messages.append({"role": "user", "content": user_content})
        first_text = next((b.get("text", "") for b in user_content if b.get("type") == "text"), "")
        conv.log({"kind": "user", "text": first_text,
                  "attachments": sum(1 for b in user_content if b.get("type") in ("image", "document"))})
        conv.save()

        try:
            await self._loop()
        except asyncio.CancelledError:
            self._repair_after_interrupt()
            conv.log({"kind": "notice", "text": "Tâche arrêtée."})
            conv.save()
            await self.emit({"type": "notice", "level": "warn", "message": "Tâche arrêtée."})
            raise
        except computer_mod.EmergencyStop:
            self._repair_after_interrupt()
            msg = "Arrêt d'urgence : la souris a été placée dans un coin de l'écran."
            conv.log({"kind": "notice", "text": msg})
            await self.emit({"type": "notice", "level": "warn", "message": msg})
        except APIError as err:
            self._repair_after_interrupt()
            conv.log({"kind": "error", "text": err.message})
            await self.emit({"type": "error", "message": err.message})
        except Exception as err:  # noqa: BLE001
            self._repair_after_interrupt()
            msg = f"Erreur inattendue : {type(err).__name__}: {err}"
            conv.log({"kind": "error", "text": msg})
            await self.emit({"type": "error", "message": msg})
        finally:
            conv.save()
            await self.emit({"type": "done", "usage": conv.usage})

    async def _loop(self) -> None:
        conv = self.conv
        max_steps = int(self.settings.get("max_steps", 150))
        for step in range(max_steps):
            await self.emit({"type": "status", "message": "Réflexion…" if step == 0 else "Je continue…"})
            message = await stream_message(api_key=self.settings["api_key"], payload=self._payload(),
                                           on_event=self._on_stream_event)
            for key in conv.usage:
                conv.usage[key] += int((message.get("usage") or {}).get(key) or 0)
            await self.emit({"type": "usage", "usage": conv.usage})

            content = message.get("content", [])
            stop = message.get("stop_reason")
            incomplete = [b for b in content if b.get("_incomplete")]
            content = [b for b in content if not b.get("_incomplete")]
            if content:
                if conv.messages and conv.messages[-1]["role"] == "assistant":
                    # Suite d'un tour mis en pause (pause_turn) : même tour d'assistant.
                    conv.messages[-1]["content"] = list(conv.messages[-1]["content"]) + content
                else:
                    conv.messages.append({"role": "assistant", "content": content})
                self._log_assistant(content)
            conv.save()

            if stop == "pause_turn":
                if not content:
                    return
                continue
            tool_blocks = [b for b in content if b.get("type") == "tool_use"]
            if tool_blocks:
                results = await self._execute(tool_blocks)
                conv.messages.append({"role": "user", "content": results})
                conv.save()
                continue
            if stop == "max_tokens" or incomplete:
                conv.messages.append({"role": "user", "content": [
                    {"type": "text", "text": "Ta réponse a été coupée (limite de longueur). Continue là où tu t'es arrêté."}]})
                continue
            if stop == "refusal":
                msg = "Le modèle a refusé de poursuivre cette demande."
                conv.log({"kind": "notice", "text": msg})
                await self.emit({"type": "notice", "level": "warn", "message": msg})
            return
        msg = f"Limite de {max_steps} étapes atteinte. Dis « continue » pour poursuivre."
        conv.log({"kind": "notice", "text": msg})
        await self.emit({"type": "notice", "level": "warn", "message": msg})

    async def _on_stream_event(self, ev: dict) -> None:
        etype = ev["type"]
        if etype == "text_delta":
            await self.emit({"type": "text_delta", "index": ev["index"], "text": ev["text"]})
        elif etype == "thinking_delta":
            await self.emit({"type": "thinking_delta", "index": ev["index"], "text": ev["text"]})
        elif etype == "block_start":
            btype = ev["block"].get("type")
            if btype in ("text", "thinking"):
                await self.emit({"type": "block_start", "index": ev["index"], "kind": btype})
        elif etype == "block_stop":
            block = ev["block"]
            btype = block.get("type")
            if btype == "server_tool_use":
                await self.emit({"type": "server_tool", "name": block.get("name"),
                                 "input": _summarize_input(block.get("name", ""), block.get("input") or {})})
            elif btype in ("web_search_tool_result", "web_fetch_tool_result"):
                await self.emit({"type": "server_tool_result", "kind": btype, "summary": _web_summary(block)})
        elif etype == "retry":
            await self.emit({"type": "notice", "level": "info",
                             "message": f"{ev['message']} — nouvel essai dans {ev['delay']} s"})

    def _log_assistant(self, content: list[dict]) -> None:
        for block in content:
            btype = block.get("type")
            if btype == "text" and block.get("text"):
                cites = []
                for c in block.get("citations") or []:
                    if isinstance(c, dict) and c.get("url"):
                        cites.append({"url": c["url"], "title": c.get("title", "")})
                self.conv.log({"kind": "text", "text": block["text"], "citations": cites})
            elif btype == "thinking" and block.get("thinking"):
                self.conv.log({"kind": "thinking", "text": block["thinking"]})
            elif btype == "server_tool_use":
                self.conv.log({"kind": "server_tool", "name": block.get("name"),
                               "input": _summarize_input(block.get("name", ""), block.get("input") or {})})

    # ---------- exécution des outils
    async def _execute(self, blocks: list[dict]) -> list[dict]:
        results: list[dict] = []
        self._pending = results
        computer_failed = False
        last_computer_idx = None
        ran_computer_action = False

        for block in blocks:
            name, inp, toolset = block.get("name", ""), block.get("input") or {}, block.get("toolset_name")
            base: dict = {"type": "tool_result", "tool_use_id": block["id"]}
            if toolset:
                base["toolset_name"] = toolset

            if toolset == "computer" and computer_failed:
                results.append(dict(base, content=HALT_TEXT, is_error=True))
                continue

            summary = _summarize_input(name, inp)
            await self.emit({"type": "tool_start", "id": block["id"], "name": name, "toolset": toolset,
                             "summary": summary, "input": _safe_input(inp)})

            # Autorisation
            check_input = dict(inp)
            if name == "connector_call":
                meta = manager.tool_meta(inp.get("server", ""), inp.get("tool", ""))
                check_input["_read_only"] = meta.get("read_only")
                check_input["_destructive"] = meta.get("destructive")
            decision = safety.evaluate(name, check_input, self.settings.get("approval_mode", "equilibre"), toolset)
            key = safety.approval_key(name, inp, toolset)
            if decision.needs_approval and not (key in self.conv.allowed and decision.level != "critique"):
                answer = await self.ask_approval({
                    "tool_id": block["id"], "name": name, "toolset": toolset, "summary": summary,
                    "reason": decision.reason, "level": decision.level, "input": _safe_input(inp),
                    "can_always": decision.level != "critique",
                })
                if answer.get("decision") == "always" and decision.level != "critique":
                    self.conv.allowed.add(key)
                elif answer.get("decision") != "allow" and answer.get("decision") != "always":
                    note = (answer.get("comment") or "").strip()
                    text = "L'utilisateur a refusé cette action." + (f" Son commentaire : {note}" if note else "")
                    results.append(dict(base, content=text, is_error=True))
                    self.conv.log({"kind": "tool", "name": name, "toolset": toolset, "summary": summary, "ok": False, "preview": text})
                    await self.emit({"type": "tool_result", "id": block["id"], "ok": False, "preview": text})
                    if toolset == "computer":
                        computer_failed = True
                    continue

            # Exécution
            started = time.time()
            if toolset == "computer":
                if self.computer is None:
                    result = ToolResult.error("Contrôle de l'écran indisponible.")
                else:
                    result = await self.computer.run(name, inp)
                    if name not in ("screenshot", "zoom", "cursor_position"):
                        ran_computer_action = True
            else:
                try:
                    result = await dispatch(name, inp, self.settings.get("working_dir") or ".")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    result = ToolResult.error(f"{type(exc).__name__}: {exc}")

            content = result.content
            entry = dict(base, content=content)
            if result.is_error:
                entry["is_error"] = True
                if toolset == "computer":
                    computer_failed = True
            results.append(entry)
            if toolset == "computer":
                last_computer_idx = len(results) - 1

            self.conv.log({"kind": "tool", "name": name, "toolset": toolset, "summary": summary, "ok": not result.is_error,
                           "preview": result.preview[:400], "duration": round(time.time() - started, 1)})
            await self.emit({"type": "tool_result", "id": block["id"], "ok": not result.is_error,
                             "preview": result.preview[:600], "image": result.image_b64,
                             "duration": round(time.time() - started, 1)})

        # Capture automatique en fin de série d'actions à l'écran (évite un aller-retour).
        if (self.computer and ran_computer_action and not computer_failed and last_computer_idx is not None
                and blocks[-1].get("name") not in ("screenshot", "zoom")):
            try:
                await asyncio.sleep(0.4)
                shot = await asyncio.to_thread(self.computer.screen.screenshot)
                entry = results[last_computer_idx]
                content = entry["content"]
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                entry["content"] = list(content) + [{"type": "text", "text": "Capture après les actions :"}, shot]
                await self.emit({"type": "screen", "image": shot["source"]["data"]})
            except Exception:  # noqa: BLE001
                pass

        self._pending = None
        return results

    def _repair_after_interrupt(self) -> None:
        """Garantit que chaque tool_use a son tool_result (sinon l'API refuserait la suite)."""
        msgs = self.conv.messages
        if not msgs or msgs[-1]["role"] != "assistant":
            return
        tool_blocks = [b for b in msgs[-1]["content"] if b.get("type") == "tool_use"]
        if not tool_blocks:
            return
        done = {r["tool_use_id"]: r for r in (self._pending or [])}
        results = []
        for b in tool_blocks:
            if b["id"] in done:
                results.append(done[b["id"]])
                continue
            r = {"type": "tool_result", "tool_use_id": b["id"], "content": INTERRUPTED, "is_error": True}
            if b.get("toolset_name"):
                r["toolset_name"] = b["toolset_name"]
            results.append(r)
        msgs.append({"role": "user", "content": results})
        self._pending = None


def _safe_input(inp: dict) -> dict:
    """Version allégée des paramètres pour l'interface (textes longs coupés)."""
    out = {}
    for k, v in inp.items():
        if isinstance(v, str) and len(v) > 4000:
            out[k] = v[:4000] + f"… (+{len(v) - 4000} caractères)"
        else:
            out[k] = v
    return out


def _web_summary(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        titles = [c.get("title") or c.get("url") for c in content if isinstance(c, dict)]
        return f"{len(titles)} résultat(s) : " + " · ".join(t for t in titles[:5] if t)
    if isinstance(content, dict):
        if content.get("type", "").endswith("error"):
            return f"Erreur : {content.get('error_code')}"
        return f"Page lue : {content.get('url', '')}"
    return ""


def user_content_from(text: str, attachments: list[dict]) -> list[dict]:
    """Construit le message utilisateur (texte + pièces jointes)."""
    from .config import UPLOAD_DIR
    import base64

    blocks: list[dict] = []
    saved: list[str] = []
    for att in attachments or []:
        name = "".join(c for c in str(att.get("name", "fichier")) if c not in '\\/:*?"<>|') or "fichier"
        mime = att.get("media_type", "")
        data = att.get("data", "")
        try:
            raw = base64.b64decode(data)
        except Exception:  # noqa: BLE001
            continue
        dest = UPLOAD_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}"
        dest.write_bytes(raw)
        saved.append(str(dest))
        if mime in ("image/png", "image/jpeg", "image/gif", "image/webp") and len(raw) < 5_000_000:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        elif mime == "application/pdf" and len(raw) < 30_000_000:
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": mime, "data": data},
                           "title": name})
    if saved:
        text = (text or "") + "\n\n[Fichiers joints enregistrés sur le PC : " + "; ".join(saved) + "]"
    blocks.append({"type": "text", "text": text.strip() or "(pièce jointe)"})
    return blocks
