"""Mémoire durable : un simple fichier texte que tu peux aussi éditer toi-même."""

from __future__ import annotations

from datetime import datetime

from ..config import MEMORY_FILE
from . import ToolResult

HEADER = "# Mémoire de l'assistant\n\n"


def read_memory() -> str:
    if not MEMORY_FILE.exists():
        return ""
    return MEMORY_FILE.read_text(encoding="utf-8").strip()


def _lines() -> list[str]:
    text = read_memory()
    return [l for l in text.splitlines() if l.startswith("- ")]


def _write(lines: list[str]) -> None:
    MEMORY_FILE.write_text(HEADER + "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


async def memory_tool(inp: dict) -> ToolResult:
    action = inp.get("action", "view")
    lines = _lines()
    if action == "view":
        if not lines:
            return ToolResult.text("La mémoire est vide.")
        return ToolResult.text("\n".join(f"{i}. {l[2:]}" for i, l in enumerate(lines, 1)))
    if action == "add":
        fact = " ".join(str(inp.get("fact", "")).split())
        if not fact:
            return ToolResult.error("Rien à mémoriser.")
        if any(fact.lower() == l[2:].split(" (le ")[0].lower() for l in lines):
            return ToolResult.text("Déjà en mémoire.")
        lines.append(f"- {fact} (le {datetime.now():%d/%m/%Y})")
        _write(lines)
        return ToolResult.text(f"Mémorisé : {fact}")
    if action in ("delete", "replace"):
        try:
            idx = int(inp.get("index", 0)) - 1
            assert 0 <= idx < len(lines)
        except (ValueError, AssertionError):
            return ToolResult.error(f"Numéro invalide. La mémoire contient {len(lines)} élément(s) (voir action view).")
        old = lines[idx][2:]
        if action == "delete":
            lines.pop(idx)
            _write(lines)
            return ToolResult.text(f"Oublié : {old}")
        fact = " ".join(str(inp.get("fact", "")).split())
        lines[idx] = f"- {fact} (le {datetime.now():%d/%m/%Y})"
        _write(lines)
        return ToolResult.text(f"Mis à jour : {fact}")
    return ToolResult.error(f"Action inconnue : {action}")
