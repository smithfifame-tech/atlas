"""Accès web local, pour les modèles qui n'ont pas la recherche web intégrée de Claude."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import ToolResult, truncate

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def _clean(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def html_to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


async def web_search_local(inp: dict) -> ToolResult:
    query = str(inp.get("query", "")).strip()
    if not query:
        return ToolResult.error("Aucune recherche fournie.")
    limit = max(1, min(int(inp.get("max_results", 8) or 8), 20))
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as client:
            resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "fr-fr"})
    except httpx.HTTPError as exc:
        return ToolResult.error(f"Recherche impossible : {exc}")
    results = []
    for block in re.findall(r'(?s)<div class="result results_links.*?</div>\s*</div>', resp.text):
        link = re.search(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link:
            continue
        href = html.unescape(link.group(1))
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if "duckduckgo.com/y.js" in href:
            continue  # publicité
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        results.append(f"{len(results) + 1}. {_clean(link.group(2))}\n   {href}\n   {_clean(snippet.group(1)) if snippet else ''}")
        if len(results) >= limit:
            break
    if not results:
        return ToolResult.text(f"Aucun résultat pour « {query} ».")
    return ToolResult.text("\n".join(results) + "\n\nUtilise fetch_url pour lire une page.",
                           preview=f"{len(results)} résultat(s)")


async def fetch_url(inp: dict) -> ToolResult:
    url = str(inp.get("url", "")).strip()
    if not re.match(r"^https?://", url):
        return ToolResult.error("Adresse invalide : elle doit commencer par http:// ou https://")
    try:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return ToolResult.error(f"Page inaccessible : {exc}")
    ctype = resp.headers.get("content-type", "")
    if "html" in ctype:
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", resp.text)
        text = html_to_text(resp.text)
        head = f"{_clean(title.group(1)) if title else url}\n{url}\n\n"
    elif ctype.startswith("text/") or "json" in ctype:
        text, head = resp.text, f"{url}\n\n"
    else:
        return ToolResult.error(f"Contenu non textuel ({ctype}). Télécharge-le avec run_command si besoin.")
    limit = max(2000, min(int(inp.get("max_characters", 30000) or 30000), 100000))
    return ToolResult.text(truncate(head + text, limit), preview=f"Page lue ({resp.status_code}) : {url}")


LOCAL_WEB_TOOLS = [
    {
        "name": "web_search_local",
        "description": "Recherche sur Internet (moteur DuckDuckGo). Renvoie titres, liens et extraits.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"},
                                                          "max_results": {"type": "integer"}},
                         "required": ["query"]},
    },
    {
        "name": "fetch_url",
        "description": "Télécharge une page web et renvoie son texte lisible.",
        "input_schema": {"type": "object", "properties": {"url": {"type": "string"},
                                                          "max_characters": {"type": "integer"}},
                         "required": ["url"]},
    },
]
