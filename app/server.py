"""Serveur local : interface web + WebSocket temps réel. N'écoute que sur 127.0.0.1."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agent import Agent, Conversation, list_conversations, user_content_from
from .config import (APPROVAL_MODES, CONV_DIR, MCP_CONFIG_FILE, MEMORY_FILE, MODELS, STATIC_DIR,
                     get_access_token, load_settings, merge_provider_updates, public_settings, save_settings,
                     system_info)
from .tools import computer as computer_mod
from .tools.connectors import manager

TOKEN = get_access_token()
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class Runtime:
    """État vivant d'une conversation : tâche en cours et demandes d'autorisation."""

    def __init__(self, conv: Conversation):
        self.conv = conv
        self.task: asyncio.Task | None = None
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


class Hub:
    def __init__(self):
        self.sockets: set[WebSocket] = set()
        self.runtimes: dict[str, Runtime] = {}

    async def broadcast(self, event: dict) -> None:
        data = json.dumps(event, ensure_ascii=False)
        dead = []
        for ws in list(self.sockets):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    def runtime(self, conv_id: str) -> Runtime:
        if conv_id not in self.runtimes:
            self.runtimes[conv_id] = Runtime(Conversation.load(conv_id))
        return self.runtimes[conv_id]

    async def start(self, conv_id: str | None, text: str, attachments: list[dict]) -> str:
        if conv_id:
            rt = self.runtime(conv_id)
        else:
            conv = Conversation()
            rt = Runtime(conv)
            self.runtimes[conv.id] = rt
        if rt.running:
            raise RuntimeError("Une tâche est déjà en cours dans cette conversation.")
        cid = rt.conv.id

        async def emit(ev: dict) -> None:
            ev["conversation_id"] = cid
            await self.broadcast(ev)

        async def ask(req: dict) -> dict:
            rid = uuid.uuid4().hex[:10]
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            payload = dict(req, type="approval_request", request_id=rid, conversation_id=cid)
            rt.pending[rid] = (fut, payload)
            await self.broadcast(payload)
            try:
                return await fut
            finally:
                rt.pending.pop(rid, None)
                await self.broadcast({"type": "approval_closed", "request_id": rid, "conversation_id": cid})

        agent = Agent(rt.conv, emit, ask)
        content = user_content_from(text, attachments)
        rt.task = asyncio.create_task(agent.run(content), name=f"agent-{cid}")
        await self.broadcast({"type": "conversation_started", "conversation_id": cid, "text": text,
                              "attachments": len(attachments or [])})
        return cid

    async def stop(self, conv_id: str) -> None:
        rt = self.runtimes.get(conv_id)
        if rt and rt.running:
            for fut, _ in list(rt.pending.values()):
                if not fut.done():
                    fut.set_result({"decision": "deny"})
            rt.task.cancel()

    def answer(self, conv_id: str, request_id: str, decision: str, comment: str = "") -> None:
        rt = self.runtimes.get(conv_id)
        if rt and request_id in rt.pending:
            fut, _ = rt.pending[request_id]
            if not fut.done():
                fut.set_result({"decision": decision, "comment": comment})


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    starter = asyncio.create_task(manager.start_all())
    yield
    starter.cancel()
    for rt in hub.runtimes.values():
        if rt.running:
            rt.task.cancel()
    await manager.stop_all()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _host_ok(host: str | None) -> bool:
    if not host:
        return False
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name in ALLOWED_HOSTS


def require_token(request: Request) -> None:
    if not _host_ok(request.headers.get("host")):
        raise HTTPException(403, "Hôte non autorisé")
    tok = request.headers.get("x-token") or request.query_params.get("token") or ""
    if not secrets.compare_digest(tok, TOKEN):
        raise HTTPException(401, "Jeton invalide : rouvre l'adresse affichée au démarrage.")


@app.get("/")
async def index(request: Request):
    if not _host_ok(request.headers.get("host")):
        raise HTTPException(403, "Hôte non autorisé")
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/state", dependencies=[Depends(require_token)])
async def state():
    settings = load_settings()
    ok, reason = computer_mod.available()
    return {
        "version": __version__,
        "settings": public_settings(settings),
        "models": MODELS,
        "modes": APPROVAL_MODES,
        "conversations": list_conversations(),
        "running": [cid for cid, rt in hub.runtimes.items() if rt.running],
        "connectors": manager.overview(),
        "computer": {"available": ok, "reason": reason},
        "system": system_info(),
    }


@app.post("/api/settings", dependencies=[Depends(require_token)])
async def update_settings(request: Request):
    body = await request.json()
    if "api_key" in body and not str(body["api_key"]).strip():
        body.pop("api_key")  # champ laissé vide = on garde la clé actuelle
    for key in ("max_tokens", "max_steps", "screenshot_max_width"):
        if key in body:
            body[key] = int(body[key])
    if "providers" in body:
        body["providers"] = merge_provider_updates(load_settings().get("providers") or {}, body["providers"])
    if body.get("model") and body["model"] not in MODELS and not str(body["model"]).startswith("claude-"):
        raise HTTPException(400, "Modèle inconnu")
    return public_settings(save_settings(body))


@app.get("/api/conversations/{conv_id}", dependencies=[Depends(require_token)])
async def get_conversation(conv_id: str):
    if not conv_id.isalnum():
        raise HTTPException(400)
    try:
        rt = hub.runtime(conv_id)
    except FileNotFoundError:
        raise HTTPException(404, "Conversation introuvable") from None
    c = rt.conv
    return {"id": c.id, "title": c.title, "model": c.model, "usage": c.usage, "log": c.ui_log,
            "running": rt.running, "pending": [p for _, p in rt.pending.values()]}


@app.delete("/api/conversations/{conv_id}", dependencies=[Depends(require_token)])
async def delete_conversation(conv_id: str):
    if not conv_id.isalnum():
        raise HTTPException(400)
    await hub.stop(conv_id)
    hub.runtimes.pop(conv_id, None)
    (CONV_DIR / f"{conv_id}.json").unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/ollama/models", dependencies=[Depends(require_token)])
async def get_ollama_models():
    from .providers import merged_providers, ollama_models

    base = merged_providers(load_settings())["ollama"].get("base_url") or ""
    try:
        return {"running": True, "models": await ollama_models(base)}
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "models": [], "error": str(exc)}


@app.get("/api/memory", dependencies=[Depends(require_token)])
async def get_memory():
    return {"text": MEMORY_FILE.read_text(encoding="utf-8") if MEMORY_FILE.exists() else ""}


@app.post("/api/memory", dependencies=[Depends(require_token)])
async def set_memory(request: Request):
    body = await request.json()
    MEMORY_FILE.write_text(str(body.get("text", "")), encoding="utf-8")
    return {"ok": True}


@app.get("/api/connectors", dependencies=[Depends(require_token)])
async def get_connectors():
    text = MCP_CONFIG_FILE.read_text(encoding="utf-8") if MCP_CONFIG_FILE.exists() else '{"mcpServers": {}}'
    return {"config": text, "servers": manager.overview()}


@app.post("/api/connectors", dependencies=[Depends(require_token)])
async def set_connectors(request: Request):
    body = await request.json()
    text = str(body.get("config", ""))
    try:
        data = json.loads(text)
        assert isinstance(data.get("mcpServers", {}), dict)
    except (json.JSONDecodeError, AssertionError, AttributeError) as exc:
        return JSONResponse({"ok": False, "error": f"JSON invalide : {exc}"}, status_code=400)
    MCP_CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    await manager.reload()
    return {"ok": True, "servers": manager.overview()}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    origin = ws.headers.get("origin", "")
    tok = ws.query_params.get("token", "")
    host_ok = _host_ok(ws.headers.get("host"))
    origin_ok = not origin or any(origin.startswith(f"http://{h}") for h in ALLOWED_HOSTS)
    if not (host_ok and origin_ok and secrets.compare_digest(tok, TOKEN)):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.sockets.add(ws)
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            mtype = msg.get("type")
            try:
                if mtype == "send":
                    await hub.start(msg.get("conversation_id"), str(msg.get("text", "")), msg.get("attachments") or [])
                elif mtype == "stop":
                    await hub.stop(str(msg.get("conversation_id")))
                elif mtype == "approval":
                    hub.answer(str(msg.get("conversation_id")), str(msg.get("request_id")),
                               str(msg.get("decision", "deny")), str(msg.get("comment", "")))
                elif mtype == "ping":
                    await ws.send_text('{"type":"pong"}')
            except Exception as exc:  # noqa: BLE001
                await ws.send_text(json.dumps({"type": "error", "conversation_id": msg.get("conversation_id"),
                                               "message": str(exc)}, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    finally:
        hub.sockets.discard(ws)
