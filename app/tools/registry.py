"""Définition des outils envoyés à Claude et aiguillage vers leur implémentation."""

from __future__ import annotations

from ..config import IS_WINDOWS
from . import ToolResult, system
from .connectors import manager
from .memory import memory_tool

SHELL_NAME = "PowerShell (Windows)" if IS_WINDOWS else "bash"

CUSTOM_TOOLS: list[dict] = [
    {
        "name": "run_command",
        "description": (
            f"Exécute une commande dans le terminal du PC de l'utilisateur ({SHELL_NAME}) et renvoie sa sortie. "
            "Sert à tout ce qu'un terminal permet : lancer des programmes et scripts, installer des logiciels "
            "(winget, npm, pip…), gérer git, inspecter le système, manipuler des fichiers en masse. "
            "Pour un serveur ou une tâche longue, utilise background=true puis lis le journal indiqué. "
            "Ne lance jamais de commande interactive qui attend une saisie."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "La commande à exécuter."},
                "cwd": {"type": "string", "description": "Dossier d'exécution (par défaut : dossier de travail)."},
                "timeout": {"type": "integer", "description": "Délai max en secondes (défaut 120, max 1800)."},
                "background": {"type": "boolean", "description": "Lancer sans attendre la fin (serveurs, longues tâches)."},
                "shell": {"type": "string", "enum": ["powershell", "cmd", "pwsh"],
                          "description": "Windows uniquement : interpréteur à utiliser (défaut powershell)."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "find_files",
        "description": (
            "Cherche des fichiers par nom sur le PC (motif glob comme *.pdf ou simple morceau de nom), "
            "résultats triés du plus récent au plus ancien. Idéal pour retrouver un document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Ex. 'facture', '*.docx', 'rapport*2026*.pdf'."},
                "root": {"type": "string", "description": "Dossier de départ (défaut : dossier de travail ; ex. ~/Documents, D:\\)."},
                "modified_within_days": {"type": "number", "description": "Seulement les fichiers modifiés ces N derniers jours."},
                "max_results": {"type": "integer"},
                "include_hidden": {"type": "boolean"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search_in_files",
        "description": "Cherche un texte (ou une expression régulière) à l'intérieur des fichiers texte/code d'un dossier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "root": {"type": "string", "description": "Dossier ou fichier où chercher."},
                "file_pattern": {"type": "string", "description": "Filtre de noms, ex. '*.py' ou '*.txt'."},
                "regex": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_document",
        "description": "Lit le contenu d'un document : PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), image, texte.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_page": {"type": "integer", "description": "PDF : première page (1 par défaut)."},
                "end_page": {"type": "integer", "description": "PDF : dernière page."},
                "max_rows": {"type": "integer", "description": "Excel : lignes max par feuille (200 par défaut)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "move_path",
        "description": "Déplace, renomme ou copie un fichier ou un dossier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "copy": {"type": "boolean", "description": "Copier au lieu de déplacer."},
                "overwrite": {"type": "boolean"},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "delete_path",
        "description": "Envoie un fichier ou un dossier à la corbeille (récupérable). Préfère toujours cet outil à une suppression définitive.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "open_path",
        "description": (
            "Ouvre un fichier avec son application par défaut, un lien dans le navigateur, ou lance une application "
            "par son nom (ex. 'notepad', 'calc', 'chrome', 'Visual Studio Code')."
        ),
        "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
    },
    {
        "name": "clipboard",
        "description": "Lit ou écrit le presse-papiers du PC.",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["read", "write"]}, "text": {"type": "string"}},
            "required": ["action"],
        },
    },
    {
        "name": "memory",
        "description": (
            "Mémoire durable entre les conversations. N'y enregistre que ce que l'utilisateur te demande de retenir "
            "ou des préférences/faits stables qu'il t'a donnés (jamais de mots de passe ni de numéros de carte). "
            "view = lister, add = ajouter, replace/delete = modifier un élément par son numéro."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["view", "add", "replace", "delete"]},
                "fact": {"type": "string"},
                "index": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "connector_tools",
        "description": "Liste les outils (et leurs paramètres) des applications connectées via MCP.",
        "input_schema": {
            "type": "object",
            "properties": {"server": {"type": "string", "description": "Nom du connecteur (vide = tous)."}},
        },
    },
    {
        "name": "connector_call",
        "description": "Appelle un outil d'une application connectée (Gmail, Notion, GitHub, base de données…).",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Nom du connecteur."},
                "tool": {"type": "string", "description": "Nom exact de l'outil."},
                "arguments": {"type": "object", "description": "Arguments conformes au schéma de l'outil."},
            },
            "required": ["server", "tool"],
        },
    },
    {
        "name": "manage_connectors",
        "description": (
            "Gère les connecteurs MCP : list, add, remove, reload. Pour connecter une nouvelle application, "
            "trouve d'abord son serveur MCP officiel (recherche web), puis ajoute-le : local avec command+args "
            "(ex. command 'npx', args ['-y','@modelcontextprotocol/server-filesystem','C:/Users/moi']), "
            "ou distant avec url (+ headers d'authentification). Demande les clés API à l'utilisateur, n'en invente jamais."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add", "remove", "reload"]},
                "name": {"type": "string"},
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "url": {"type": "string"},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "transport": {"type": "string", "enum": ["http", "sse"]},
            },
            "required": ["action"],
        },
    },
]

EDITOR_TOOL = {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"}


def build_tools(settings: dict, computer_ok: bool) -> list[dict]:
    tools: list[dict] = []
    if settings.get("computer_use") and computer_ok:
        tools.append({"type": "computer_toolset_20260801"})
    tools.append(EDITOR_TOOL)
    tools.extend(CUSTOM_TOOLS)
    if settings.get("web_search"):
        ws: dict = {"type": "web_search_20260318", "name": "web_search", "max_uses": 20}
        loc = {k: settings.get(f"location_{k}") for k in ("country", "city", "timezone")}
        loc = {k: v for k, v in loc.items() if v}
        if loc:
            ws["user_location"] = {"type": "approximate", **loc}
        tools.append(ws)
    if settings.get("web_fetch"):
        tools.append({"type": "web_fetch_20260318", "name": "web_fetch", "max_uses": 20,
                      "max_content_tokens": 60000, "citations": {"enabled": True}})
    tools[-1] = dict(tools[-1], cache_control={"type": "ephemeral"})
    return tools


async def dispatch(name: str, inp: dict, cwd: str) -> ToolResult:
    if name == "run_command":
        return await system.run_command(inp, cwd)
    if name == "str_replace_based_edit_tool":
        return await system.text_editor(inp, cwd)
    if name == "find_files":
        return await system.find_files(inp, cwd)
    if name == "search_in_files":
        return await system.search_in_files(inp, cwd)
    if name == "read_document":
        return await system.read_document(inp, cwd)
    if name == "move_path":
        return await system.move_path(inp, cwd)
    if name == "delete_path":
        return await system.delete_path(inp, cwd)
    if name == "open_path":
        return await system.open_path(inp, cwd)
    if name == "clipboard":
        return await system.clipboard(inp, cwd)
    if name == "memory":
        return await memory_tool(inp)
    if name == "connector_tools":
        return await manager.list_tools(inp)
    if name == "connector_call":
        clean = {k: v for k, v in inp.items() if not k.startswith("_")}
        return await manager.call(clean)
    if name == "manage_connectors":
        return await manager.manage(inp)
    return ToolResult.error(f"Outil inconnu : {name}")
