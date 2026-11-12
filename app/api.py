"""Client minimal de l'API Messages d'Anthropic, en streaming (SSE).

On parle directement à l'API en HTTP plutôt que via le SDK : ainsi les nouveaux
types de blocs (outils serveur, toolsets…) sont conservés tels quels et renvoyés
à l'identique, ce que l'API exige pour la réflexion et les recherches web.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

API_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/") + "/v1/messages"
API_VERSION = "2023-06-01"

EventCallback = Callable[[dict], Awaitable[None]]


class APIError(Exception):
    def __init__(self, status: int, message: str, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retryable = retryable


def _friendly_error(status: int, body: str) -> str:
    try:
        data = json.loads(body)
        msg = data.get("error", {}).get("message") or body
    except json.JSONDecodeError:
        msg = body[:500]
    hints = {
        401: "Clé API invalide. Vérifie-la dans les Réglages.",
        403: "Accès refusé par l'API (droits du compte ou fonctionnalité désactivée).",
        404: "Modèle introuvable : vérifie le nom du modèle dans les Réglages.",
        413: "Requête trop volumineuse : démarre une nouvelle conversation.",
        429: "Limite de débit atteinte, nouvelle tentative…",
        529: "L'API est surchargée, nouvelle tentative…",
    }
    hint = hints.get(status)
    return f"{hint} ({msg})" if hint else msg


async def _iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str, dict]]:
    event = ""
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                try:
                    yield event, json.loads(raw)
                except json.JSONDecodeError:
                    pass
            event, data_lines = "", []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            yield event, json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            pass


def _clean_block(block: dict) -> dict:
    """Retire les champs vides ajoutés par le streaming (ex. citations: null)."""
    return {k: v for k, v in block.items() if v is not None}


async def stream_message(
    *,
    api_key: str,
    payload: dict[str, Any],
    on_event: EventCallback,
    extra_headers: dict[str, str] | None = None,
    max_retries: int = 4,
) -> dict:
    """Envoie une requête en streaming et renvoie le message final reconstitué.

    on_event reçoit des événements simplifiés pour l'interface :
      {"type": "text_delta", "index": i, "text": "..."}
      {"type": "thinking_delta", "index": i, "text": "..."}
      {"type": "block_start", "index": i, "block": {...}}
      {"type": "block_stop", "index": i, "block": {...}}
      {"type": "retry", "attempt": n, "message": "..."}
    """
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    body = dict(payload)
    body["stream"] = True

    attempt = 0
    while True:
        attempt += 1
        try:
            return await _stream_once(headers, body, on_event)
        except APIError as err:
            if not err.retryable or attempt > max_retries:
                raise
            delay = min(60, 2 ** attempt)
            await on_event({"type": "retry", "attempt": attempt, "message": err.message, "delay": delay})
            await asyncio.sleep(delay)
        except (httpx.TransportError, httpx.TimeoutException) as err:
            if attempt > max_retries:
                raise APIError(0, f"Connexion à l'API impossible : {err}") from err
            delay = min(60, 2 ** attempt)
            await on_event({"type": "retry", "attempt": attempt, "message": str(err), "delay": delay})
            await asyncio.sleep(delay)


async def _stream_once(headers: dict, body: dict, on_event: EventCallback) -> dict:
    timeout = httpx.Timeout(connect=30.0, read=600.0, write=120.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", API_URL, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                text = (await resp.aread()).decode("utf-8", "replace")
                retryable = resp.status_code in (408, 429, 500, 502, 503, 504, 529)
                raise APIError(resp.status_code, _friendly_error(resp.status_code, text), retryable)

            message: dict[str, Any] = {}
            blocks: dict[int, dict] = {}
            partial_json: dict[int, list[str]] = {}

            async for event, data in _iter_sse(resp):
                etype = data.get("type", event)
                if etype == "message_start":
                    message = data.get("message", {})
                    message["content"] = []
                elif etype == "content_block_start":
                    idx = data["index"]
                    block = dict(data.get("content_block", {}))
                    blocks[idx] = block
                    if block.get("type") in ("tool_use", "server_tool_use"):
                        partial_json[idx] = []
                    await on_event({"type": "block_start", "index": idx, "block": block})
                elif etype == "content_block_delta":
                    idx = data["index"]
                    delta = data.get("delta", {})
                    block = blocks.setdefault(idx, {})
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        block["text"] = block.get("text", "") + delta.get("text", "")
                        await on_event({"type": "text_delta", "index": idx, "text": delta.get("text", "")})
                    elif dtype == "thinking_delta":
                        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                        if delta.get("thinking"):
                            await on_event({"type": "thinking_delta", "index": idx, "text": delta["thinking"]})
                    elif dtype == "signature_delta":
                        block["signature"] = block.get("signature", "") + delta.get("signature", "")
                    elif dtype == "input_json_delta":
                        partial_json.setdefault(idx, []).append(delta.get("partial_json", ""))
                    elif dtype == "citations_delta":
                        block.setdefault("citations", []).append(delta.get("citation"))
                elif etype == "content_block_stop":
                    idx = data["index"]
                    block = blocks.get(idx, {})
                    if idx in partial_json:
                        raw = "".join(partial_json.pop(idx))
                        try:
                            block["input"] = json.loads(raw) if raw.strip() else {}
                        except json.JSONDecodeError:
                            block["input"] = {}
                            block["_incomplete"] = True
                    await on_event({"type": "block_stop", "index": idx, "block": block})
                elif etype == "message_delta":
                    message.update(data.get("delta", {}))
                    if "usage" in data:
                        message.setdefault("usage", {}).update(data["usage"])
                elif etype == "error":
                    err = data.get("error", {})
                    retryable = err.get("type") in ("overloaded_error", "api_error", "rate_limit_error")
                    raise APIError(529 if retryable else 500, err.get("message", "Erreur API"), retryable)
                elif etype == "message_stop":
                    break

            content = []
            for idx in sorted(blocks):
                block = _clean_block(blocks[idx])
                if block.get("type") == "text" and not block.get("text"):
                    continue  # l'API refuse les blocs texte vides
                content.append(block)
            message["content"] = content
            return message
