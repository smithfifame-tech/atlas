"""Outils que l'assistant peut utiliser sur ton PC."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Résultat d'un outil : texte simple ou liste de blocs (texte / image)."""

    content: str | list[dict[str, Any]]
    is_error: bool = False
    preview: str = ""  # résumé court affiché dans l'interface
    image_b64: str | None = None  # capture d'écran à montrer dans l'interface
    meta: dict = field(default_factory=dict)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True, preview=message[:300])

    @classmethod
    def text(cls, text: str, preview: str | None = None) -> "ToolResult":
        return cls(content=text or "(aucune sortie)", preview=(preview if preview is not None else text[:300]))


def truncate(text: str, limit: int = 30000) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    cut = len(text) - limit
    return f"{text[:half]}\n\n[… {cut} caractères coupés …]\n\n{text[-half:]}"
