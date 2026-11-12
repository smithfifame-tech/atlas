"""Connecteurs MCP : relient l'assistant à n'importe quelle application.

Le fichier config/mcp_servers.json utilise le même format que Claude Desktop,
donc la configuration indiquée dans le README de n'importe quel serveur MCP
peut être copiée telle quelle :

{
  "mcpServers": {
    "nom": {"command": "npx", "args": ["-y", "paquet-mcp"], "env": {"CLE": "..."}},
    "distant": {"url": "https://exemple.com/mcp", "headers": {"Authorization": "Bearer ..."}}
  }
}
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from contextlib import AsyncExitStack
from typing import Any

from ..config import IS_WINDOWS, LOG_DIR, MCP_CONFIG_FILE
from . import ToolResult, truncate


def _get(obj: Any, *names: str, default=None):
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            val = getattr(obj, n)
            if val is not None:
                return val
    return default


def load_config() -> dict:
    if not MCP_CONFIG_FILE.exists():
        MCP_CONFIG_FILE.write_text(json.dumps({"mcpServers": {}}, indent=2), encoding="utf-8")
    try:
        data = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"config/mcp_servers.json invalide : {exc}") from exc
    return data if isinstance(data, dict) else {"mcpServers": {}}


def save_config(data: dict) -> None:
    MCP_CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class ServerConnection:
    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.status = "arrêté"
        self.error = ""
        self.tools: list[dict] = []
        self.session = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()

    @property
    def disabled(self) -> bool:
        return bool(self.cfg.get("disabled"))

    async def start(self, timeout: float = 60) -> None:
        if self.disabled:
            self.status = "désactivé"
            return
        self._stop.clear()
        self._ready.clear()
        self.status = "connexion…"
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            self.status = "erreur"
            self.error = f"Pas de réponse après {timeout:.0f} s"
            await self.stop()

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
        self._task = None
        self.session = None
        if self.status == "connecté":
            self.status = "arrêté"

    async def _run(self) -> None:
        from mcp import ClientSession

        try:
            async with AsyncExitStack() as stack:
                if self.cfg.get("url"):
                    read, write = await self._open_http(stack)
                else:
                    read, write = await self._open_stdio(stack)
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self.session = session
                await self.refresh_tools()
                self.status = "connecté"
                self.error = ""
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:  # noqa: BLE001
            self.status = "erreur"
            self.error = _flatten_error(exc)
        finally:
            self.session = None
            self._ready.set()

    async def _open_stdio(self, stack: AsyncExitStack):
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = str(self.cfg.get("command", ""))
        if not command:
            raise ValueError("« command » ou « url » manquant")
        resolved = shutil.which(command) or (shutil.which(command + ".cmd") if IS_WINDOWS else None) or command
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (self.cfg.get("env") or {}).items()})
        params = StdioServerParameters(command=resolved, args=[str(a) for a in self.cfg.get("args", [])],
                                       env=env, cwd=self.cfg.get("cwd"))
        errlog = open(LOG_DIR / f"mcp_{self.name}.log", "a", encoding="utf-8")
        stack.callback(errlog.close)
        return await stack.enter_async_context(stdio_client(params, errlog=errlog))

    async def _open_http(self, stack: AsyncExitStack):
        url = self.cfg["url"]
        headers = {k: str(v) for k, v in (self.cfg.get("headers") or {}).items()}
        transport = self.cfg.get("transport", "http")
        if transport == "sse":
            from mcp.client.sse import sse_client

            streams = await stack.enter_async_context(sse_client(url, headers=headers))
            return streams[0], streams[1]
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        client = await stack.enter_async_context(create_mcp_http_client(headers=headers))
        streams = await stack.enter_async_context(streamable_http_client(url, http_client=client))
        return streams[0], streams[1]

    async def refresh_tools(self) -> None:
        tools: list[dict] = []
        cursor = None
        while True:
            if cursor:
                from mcp import types

                res = await self.session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
            else:
                res = await self.session.list_tools()
            for t in _get(res, "tools", default=[]):
                ann = _get(t, "annotations")
                tools.append({
                    "name": _get(t, "name"),
                    "description": (_get(t, "description", default="") or "").strip(),
                    "input_schema": _get(t, "input_schema", "inputSchema", default={}) or {},
                    "read_only": bool(_get(ann, "read_only_hint", "readOnlyHint", default=False)) if ann else False,
                    "destructive": bool(_get(ann, "destructive_hint", "destructiveHint", default=False)) if ann else False,
                })
            cursor = _get(res, "next_cursor", "nextCursor")
            if not cursor:
                break
        self.tools = tools

    def summary(self) -> dict:
        return {"name": self.name, "status": self.status, "error": self.error,
                "tools": [t["name"] for t in self.tools], "disabled": self.disabled,
                "kind": "distant" if self.cfg.get("url") else "local"}


def _flatten_error(exc: BaseException) -> str:
    inner = getattr(exc, "exceptions", None)
    if inner:
        return "; ".join(_flatten_error(e) for e in inner)
    return f"{type(exc).__name__}: {exc}"


class ConnectorManager:
    def __init__(self):
        self.servers: dict[str, ServerConnection] = {}
        self._lock = asyncio.Lock()

    async def start_all(self) -> None:
        async with self._lock:
            cfg = load_config().get("mcpServers", {})
            await asyncio.gather(*(self._start_one(n, c) for n, c in cfg.items()), return_exceptions=True)

    async def _start_one(self, name: str, cfg: dict) -> None:
        conn = ServerConnection(name, cfg)
        self.servers[name] = conn
        await conn.start()

    async def stop_all(self) -> None:
        await asyncio.gather(*(s.stop() for s in self.servers.values()), return_exceptions=True)
        self.servers.clear()

    async def reload(self) -> None:
        await self.stop_all()
        await self.start_all()

    def overview(self) -> list[dict]:
        return [s.summary() for s in self.servers.values()]

    def prompt_summary(self) -> str:
        if not self.servers:
            return "Aucun connecteur configuré pour l'instant."
        lines = []
        for s in self.servers.values():
            if s.status == "connecté":
                names = ", ".join(t["name"] for t in s.tools[:40])
                more = f" (+{len(s.tools) - 40})" if len(s.tools) > 40 else ""
                lines.append(f"- {s.name} (connecté) : {names}{more}")
            else:
                lines.append(f"- {s.name} ({s.status}{' : ' + s.error[:120] if s.error else ''})")
        return "\n".join(lines)

    def tool_meta(self, server: str, tool: str) -> dict:
        conn = self.servers.get(server)
        if not conn:
            return {}
        return next((t for t in conn.tools if t["name"] == tool), {})

    # ----- outils exposés à Claude
    async def list_tools(self, inp: dict) -> ToolResult:
        server = inp.get("server")
        targets = [self.servers[server]] if server in self.servers else list(self.servers.values())
        if server and server not in self.servers:
            return ToolResult.error(f"Connecteur inconnu : {server}. Connus : {', '.join(self.servers) or 'aucun'}")
        if not targets:
            return ToolResult.text("Aucun connecteur configuré. Utilise manage_connectors (action add) pour en ajouter.")
        out = []
        for s in targets:
            out.append(f"## {s.name} — {s.status}" + (f" ({s.error})" if s.error else ""))
            for t in s.tools:
                schema = json.dumps(t["input_schema"], ensure_ascii=False)
                flag = " [lecture seule]" if t["read_only"] else (" [destructif]" if t["destructive"] else "")
                out.append(f"### {t['name']}{flag}\n{t['description'][:800]}\nParamètres : {truncate(schema, 3000)}")
        return ToolResult.text(truncate("\n".join(out), 60000), preview=f"{sum(len(s.tools) for s in targets)} outil(s)")

    async def call(self, inp: dict) -> ToolResult:
        server, tool = inp.get("server"), inp.get("tool")
        args = inp.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return ToolResult.error("« arguments » doit être un objet JSON.")
        conn = self.servers.get(server)
        if not conn:
            return ToolResult.error(f"Connecteur inconnu : {server}. Connus : {', '.join(self.servers) or 'aucun'}")
        if conn.status != "connecté" or conn.session is None:
            return ToolResult.error(f"Le connecteur {server} n'est pas connecté ({conn.status} {conn.error}).")
        try:
            res = await asyncio.wait_for(conn.session.call_tool(tool, args), timeout=300)
        except asyncio.TimeoutError:
            return ToolResult.error("Le connecteur n'a pas répondu en 5 minutes.")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Erreur du connecteur : {_flatten_error(exc)}")
        blocks: list[dict] = []
        image = None
        for c in _get(res, "content", default=[]) or []:
            ctype = _get(c, "type")
            if ctype == "text":
                blocks.append({"type": "text", "text": truncate(_get(c, "text", default=""), 60000)})
            elif ctype == "image":
                mime = _get(c, "mime_type", "mimeType", default="image/png")
                if mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                    data = _get(c, "data", default="")
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
                    image = data
            elif ctype == "resource":
                resource = _get(c, "resource")
                text = _get(resource, "text")
                uri = _get(resource, "uri", default="")
                blocks.append({"type": "text", "text": truncate(f"[Ressource {uri}]\n{text or '(binaire)'}", 60000)})
            elif ctype == "resource_link":
                blocks.append({"type": "text", "text": f"[Lien de ressource] {_get(c, 'uri', default='')}"})
        structured = _get(res, "structured_content", "structuredContent")
        if structured and not blocks:
            blocks.append({"type": "text", "text": truncate(json.dumps(structured, ensure_ascii=False, indent=1), 60000)})
        if not blocks:
            blocks.append({"type": "text", "text": "(réponse vide)"})
        is_error = bool(_get(res, "is_error", "isError", default=False))
        preview = next((b["text"][:300] for b in blocks if b["type"] == "text"), "image")
        return ToolResult(content=blocks, is_error=is_error, preview=preview, image_b64=image)

    async def manage(self, inp: dict) -> ToolResult:
        action = inp.get("action", "list")
        if action == "list":
            if not self.servers:
                return ToolResult.text("Aucun connecteur configuré.")
            return ToolResult.text(json.dumps(self.overview(), ensure_ascii=False, indent=1))
        if action == "reload":
            await self.reload()
            return ToolResult.text("Connecteurs relancés :\n" + self.prompt_summary())
        name = str(inp.get("name", "")).strip()
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            return ToolResult.error("Nom de connecteur invalide (lettres, chiffres, - et _).")
        cfg_all = load_config()
        servers = cfg_all.setdefault("mcpServers", {})
        if action == "remove":
            if name in self.servers:
                await self.servers.pop(name).stop()
            servers.pop(name, None)
            save_config(cfg_all)
            return ToolResult.text(f"Connecteur {name} supprimé.")
        if action == "add":
            entry: dict = {}
            if inp.get("url"):
                entry["url"] = inp["url"]
                if inp.get("headers"):
                    entry["headers"] = inp["headers"]
                if inp.get("transport"):
                    entry["transport"] = inp["transport"]
            elif inp.get("command"):
                entry["command"] = inp["command"]
                entry["args"] = inp.get("args") or []
                if inp.get("env"):
                    entry["env"] = inp["env"]
            else:
                return ToolResult.error("Il faut « command » (+ args) pour un connecteur local, ou « url » pour un distant.")
            servers[name] = entry
            save_config(cfg_all)
            if name in self.servers:
                await self.servers[name].stop()
            conn = ServerConnection(name, entry)
            self.servers[name] = conn
            await conn.start(timeout=180)
            if conn.status != "connecté":
                return ToolResult.error(f"Connecteur enregistré mais connexion échouée : {conn.error}\n"
                                        f"Journal : data/logs/mcp_{name}.log")
            tools = ", ".join(t["name"] for t in conn.tools)
            return ToolResult.text(f"Connecteur {name} connecté. Outils disponibles : {tools}\n"
                                   "Utilise connector_tools pour voir leurs paramètres.")
        return ToolResult.error(f"Action inconnue : {action}")


manager = ConnectorManager()
