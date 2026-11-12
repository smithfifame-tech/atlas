"""Décide quelles actions exigent ton autorisation avant d'être exécutées."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Actions qui peuvent détruire le système ou des données en masse :
# toujours soumises à confirmation, même en mode autonome.
CATASTROPHIC = [
    r"\bformat(\.com)?\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+.*\bof=/dev/",
    r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)[a-z]*\s+(/|~|\$home|/\*|c:\\)\s*$",
    r"\brm\s+-[a-z]*r[a-z]*\s+--no-preserve-root",
    r"\b(del|erase)\s+.*\b[a-z]:\\\s*(/s|/q)",
    r"\b(rd|rmdir)\s+/s\s+.*\b[a-z]:\\\s*$",
    r"remove-item\s+.*\b[a-z]:\\\s*(-recurse|-r)\b.*",
    r"remove-item\s+.*(-recurse|-r)\b.*\s[a-z]:\\\s*$",
    r"\bbcdedit\b",
    r"\bcipher\s+/w",
    r"\bvssadmin\s+delete",
    r"\bwbadmin\s+delete",
    r"clear-disk|initialize-disk|format-volume",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
]

# Actions sensibles : confirmation en mode « équilibré » et « prudent ».
SENSITIVE = [
    r"\brm\s+-[a-z]*r",
    r"\b(del|erase)\b.*(/s|/q)",
    r"\b(rd|rmdir)\s+/s",
    r"remove-item\b.*-(recurse|r)\b",
    r"\bshutdown\b|\brestart-computer\b|\bstop-computer\b|\breboot\b|\bpoweroff\b",
    r"\breg\s+(delete|add|import)\b|set-itemproperty\s+.*hk(lm|cu)",
    r"set-executionpolicy",
    r"\btakeown\b|\bicacls\b|\bchown\s+-r|\bchmod\s+-r",
    r"\bnet\s+(user|localgroup)\b",
    r"(curl|wget|iwr|invoke-webrequest)\b.*\|\s*(sh|bash|iex|invoke-expression|python)",
    r"\biex\b|invoke-expression",
    r"\bgit\s+push\b.*(--force|-f\b)|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-[a-z]*f",
    r"\bdrop\s+(table|database|schema)\b|\btruncate\s+table\b",
    r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b",
    r"\bsudo\b|\brunas\b|start-process\s+.*-verb\s+runas",
    r"\bkill(all)?\s+-9|\btaskkill\b.*/f|stop-process\b.*-force",
    r"\bschtasks\s+/(create|delete)|\bcrontab\s+-r",
    r"\bnetsh\b.*(firewall|advfirewall)",
    r"set-mppreference|disable.*defender",
    r"\buninstall\b|\bwinget\s+uninstall|\bapt(-get)?\s+(remove|purge)",
    r"\bmv\b.*\s/dev/null|>\s*/dev/sd",
]

# Outils de connecteurs (MCP) dont le nom évoque une action à effet externe.
MCP_WRITE_HINTS = re.compile(
    r"(send|delete|remove|trash|destroy|drop|create|post|publish|pay|transfer|purchase|buy|"
    r"order|update|write|edit|modify|share|invite|archive|move|upload|reply|forward|submit|"
    r"approve|cancel|deploy|merge|execute|run|insert|set_|patch|put_)",
    re.IGNORECASE,
)

SYSTEM_DIRS_WIN = ("c:\\windows", "c:\\program files", "c:\\programdata")
SYSTEM_DIRS_UNIX = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/system", "/library", "/var")

COMPUTER_PASSIVE = {"screenshot", "zoom", "cursor_position", "wait", "mouse_move"}


@dataclass
class Decision:
    needs_approval: bool
    reason: str = ""
    level: str = "normal"  # normal | sensible | critique


def _match(patterns: list[str], text: str) -> str | None:
    low = text.lower()
    for pat in patterns:
        if re.search(pat, low):
            return pat
    return None


def _is_system_path(path: str) -> bool:
    try:
        p = str(Path(path).expanduser().resolve()).lower()
    except (OSError, RuntimeError):
        p = path.lower()
    return p.startswith(SYSTEM_DIRS_WIN) or any(p == d or p.startswith(d + "/") for d in SYSTEM_DIRS_UNIX)


def evaluate(tool: str, tool_input: dict, mode: str, toolset: str | None = None) -> Decision:
    """Renvoie si l'action doit être validée par l'utilisateur."""
    mode = mode if mode in ("prudent", "equilibre", "autonome") else "equilibre"

    # --- Contrôle de l'écran (souris / clavier) ---
    if toolset == "computer":
        if tool in COMPUTER_PASSIVE:
            return Decision(False)
        if mode == "prudent":
            return Decision(True, "Action souris/clavier sur ton écran", "normal")
        return Decision(False)

    # --- Commandes système ---
    if tool == "run_command":
        cmd = str(tool_input.get("command", ""))
        if _match(CATASTROPHIC, cmd):
            return Decision(True, "Commande potentiellement destructrice pour le système", "critique")
        if mode == "autonome":
            return Decision(False)
        if mode == "prudent":
            return Decision(True, "Exécution d'une commande sur ton PC", "normal")
        if _match(SENSITIVE, cmd):
            return Decision(True, "Commande sensible (suppression, droits, système, réseau…)", "sensible")
        return Decision(False)

    # --- Édition de fichiers ---
    if tool == "str_replace_based_edit_tool":
        command = tool_input.get("command")
        path = str(tool_input.get("path", ""))
        if command == "view":
            return Decision(False)
        if _is_system_path(path):
            return Decision(True, "Modification d'un fichier système", "critique")
        if mode == "autonome":
            return Decision(False)
        if mode == "prudent":
            return Decision(True, "Modification d'un fichier", "normal")
        if command == "create" and Path(path).expanduser().exists():
            return Decision(True, "Remplacement complet d'un fichier existant (une sauvegarde sera faite)", "sensible")
        return Decision(False)

    if tool in ("delete_path", "move_path"):
        path = str(tool_input.get("path", tool_input.get("source", "")))
        if _is_system_path(path):
            return Decision(True, "Opération sur un dossier système", "critique")
        if tool == "delete_path":
            return Decision(mode != "autonome", "Mise à la corbeille", "sensible")
        return Decision(mode == "prudent", "Déplacement / renommage", "normal")

    # --- Connecteurs (applications externes) ---
    if tool == "connector_call":
        name = f"{tool_input.get('server', '')}.{tool_input.get('tool', '')}"
        if mode == "autonome":
            return Decision(False)
        if mode == "prudent":
            return Decision(True, f"Action dans une application connectée ({name})", "normal")
        if tool_input.get("_read_only"):
            return Decision(False)
        if tool_input.get("_destructive") or MCP_WRITE_HINTS.search(str(tool_input.get("tool", ""))):
            return Decision(True, f"Action dans une application connectée ({name})", "sensible")
        return Decision(False)

    if tool == "manage_connectors" and tool_input.get("action") in ("add", "remove"):
        return Decision(True, "Ajout/suppression d'un connecteur (lance un programme sur ton PC)", "sensible")

    if tool == "open_path" and mode == "prudent":
        return Decision(True, "Ouverture d'un fichier, d'une application ou d'un lien", "normal")

    if tool == "clipboard" and tool_input.get("action") == "write" and mode == "prudent":
        return Decision(True, "Écriture dans le presse-papiers", "normal")

    return Decision(False)


def approval_key(tool: str, tool_input: dict, toolset: str | None) -> str:
    """Clé utilisée pour « toujours autoriser pendant cette conversation »."""
    if toolset == "computer":
        return "computer"
    if tool == "connector_call":
        return f"connector:{tool_input.get('server')}:{tool_input.get('tool')}"
    if tool == "str_replace_based_edit_tool":
        return "edit"
    return tool
