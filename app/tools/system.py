"""Commandes, fichiers, recherche, documents, presse-papiers."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import io
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ..config import BACKUP_DIR, IS_MAC, IS_WINDOWS, LOG_DIR
from . import ToolResult, truncate

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".next", ".cache", "dist", "build",
    "$recycle.bin", "system volume information", "windows", "appdata", "library", "proc", "sys",
    "dev", ".npm", ".gradle", ".m2", "site-packages", ".idea", ".vscode-server",
}

TEXT_EXT_BINARY_HINT = {".exe", ".dll", ".so", ".dylib", ".bin", ".zip", ".7z", ".rar", ".png", ".jpg",
                        ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".wav", ".mov", ".pdf", ".docx",
                        ".xlsx", ".pptx", ".iso", ".msi", ".class", ".pyc", ".woff", ".woff2", ".ttf"}


def resolve_path(path: str, cwd: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(str(path).strip().strip('"'))))
    if not p.is_absolute():
        p = Path(cwd) / p
    return p


# ---------------------------------------------------------------- commandes

def _shell_argv(command: str, shell: str | None) -> list[str]:
    if IS_WINDOWS:
        if shell == "cmd":
            return ["cmd.exe", "/d", "/s", "/c", command]
        prefix = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
                  "$OutputEncoding=[System.Text.Encoding]::UTF8;"
                  "$ProgressPreference='SilentlyContinue';")
        exe = shutil.which("pwsh") if shell == "pwsh" else None
        return [exe or "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", prefix + command]
    sh = shutil.which("bash") or "/bin/sh"
    return [sh, "-lc", command]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def run_command(inp: dict, cwd: str) -> ToolResult:
    command = str(inp.get("command", "")).strip()
    if not command:
        return ToolResult.error("Aucune commande fournie.")
    workdir = resolve_path(inp.get("cwd") or cwd, cwd)
    if not workdir.is_dir():
        return ToolResult.error(f"Dossier introuvable : {workdir}")
    timeout = max(1, min(int(inp.get("timeout", 120) or 120), 1800))
    argv = _shell_argv(command, inp.get("shell"))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    popen_kw: dict = {"cwd": str(workdir), "env": env}
    if IS_WINDOWS:
        popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kw["start_new_session"] = True

    if inp.get("background"):
        log = LOG_DIR / f"process_{datetime.now():%Y%m%d_%H%M%S}.log"
        fh = open(log, "wb")
        proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **popen_kw)
        return ToolResult.text(
            f"Processus lancé en arrière-plan (PID {proc.pid}).\nSa sortie est écrite dans : {log}\n"
            "Lis ce fichier pour suivre son avancement.",
            preview=f"PID {proc.pid} en arrière-plan",
        )

    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            **popen_kw)
    started = time.time()
    try:
        out, err = await asyncio.to_thread(proc.communicate, None, timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, err = await asyncio.to_thread(proc.communicate)
        timed_out = True
    except asyncio.CancelledError:
        _kill_tree(proc)
        raise

    def dec(b: bytes) -> str:
        for enc in ("utf-8", "cp1252" if IS_WINDOWS else "latin-1"):
            try:
                return b.decode(enc)
            except UnicodeDecodeError:
                continue
        return b.decode("utf-8", "replace")

    stdout, stderr = dec(out or b"").rstrip(), dec(err or b"").rstrip()
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    status = f"[code de sortie {proc.returncode} · {time.time() - started:.1f} s]"
    if timed_out:
        status = f"[ARRÊTÉ après {timeout} s (délai dépassé) — relance avec background=true pour un long processus]"
    text = truncate("\n".join(parts) or "(aucune sortie)") + "\n" + status
    preview = (stdout or stderr or "(aucune sortie)")[-300:]
    return ToolResult(content=text, is_error=bool(proc.returncode) and not stdout, preview=preview)


# ---------------------------------------------------------------- éditeur de texte

def backup_file(path: Path) -> Path | None:
    if not path.is_file():
        return None
    day = BACKUP_DIR / datetime.now().strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    dest = day / f"{datetime.now():%H%M%S}_{path.name}"
    shutil.copy2(path, dest)
    return dest


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _list_dir(path: Path, depth: int = 2) -> str:
    lines = [f"{path}"]

    def walk(p: Path, level: int, prefix: str):
        try:
            entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError) as exc:
            lines.append(f"{prefix}[inaccessible : {exc}]")
            return
        shown = 0
        for e in entries:
            if e.name.startswith(".") and level > 0:
                continue
            shown += 1
            if shown > 200:
                lines.append(f"{prefix}… ({len(entries) - 200} éléments de plus)")
                break
            if e.is_dir():
                lines.append(f"{prefix}{e.name}/")
                if level + 1 < depth and e.name.lower() not in SKIP_DIRS:
                    walk(e, level + 1, prefix + "  ")
            else:
                try:
                    size = e.stat().st_size
                    lines.append(f"{prefix}{e.name}  ({_human(size)})")
                except OSError:
                    lines.append(f"{prefix}{e.name}")

    walk(path, 0, "  ")
    return "\n".join(lines)


def _human(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} Po"


async def text_editor(inp: dict, cwd: str) -> ToolResult:
    command = inp.get("command")
    path = resolve_path(inp.get("path", ""), cwd)
    try:
        if command == "view":
            if path.is_dir():
                return ToolResult.text(_list_dir(path), preview=f"Dossier {path}")
            if not path.exists():
                return ToolResult.error(f"Introuvable : {path}")
            if path.suffix.lower() in TEXT_EXT_BINARY_HINT:
                return await read_document({"path": str(path)}, cwd)
            text = _read_text(path)
            lines = text.splitlines()
            rng = inp.get("view_range")
            start, end = 1, len(lines)
            if isinstance(rng, list) and len(rng) == 2:
                start = max(1, int(rng[0]))
                end = len(lines) if int(rng[1]) == -1 else min(len(lines), int(rng[1]))
            body = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1))
            limit = int(inp.get("max_characters") or 60000)
            return ToolResult.text(truncate(body, limit), preview=f"{path.name} ({len(lines)} lignes)")

        if command == "create":
            text = inp.get("file_text", "")
            backup = backup_file(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
            msg = f"Fichier écrit : {path}"
            if backup:
                msg += f"\nAncienne version sauvegardée : {backup}"
            return ToolResult.text(msg)

        if command == "str_replace":
            if not path.is_file():
                return ToolResult.error(f"Fichier introuvable : {path}")
            old, new = inp.get("old_str", ""), inp.get("new_str", "")
            text = _read_text(path)
            count = text.count(old) if old else 0
            if count == 0:
                return ToolResult.error("Texte à remplacer introuvable (vérifie espaces et retours à la ligne).")
            if count > 1:
                return ToolResult.error(f"Le texte apparaît {count} fois : ajoute du contexte pour qu'il soit unique.")
            backup_file(path)
            path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="")
            line = text[: text.index(old)].count("\n") + 1
            return ToolResult.text(f"Modifié : {path} (vers la ligne {line})")

        if command == "insert":
            if not path.is_file():
                return ToolResult.error(f"Fichier introuvable : {path}")
            at = int(inp.get("insert_line", 0))
            new = inp.get("insert_text", inp.get("new_str", ""))
            lines = _read_text(path).split("\n")
            if at < 0 or at > len(lines):
                return ToolResult.error(f"Ligne {at} hors limites (0–{len(lines)}).")
            backup_file(path)
            lines[at:at] = new.split("\n")
            path.write_text("\n".join(lines), encoding="utf-8", newline="")
            return ToolResult.text(f"Texte inséré après la ligne {at} de {path}")

        return ToolResult.error(f"Commande inconnue : {command}")
    except PermissionError:
        return ToolResult.error(f"Permission refusée : {path}")
    except OSError as exc:
        return ToolResult.error(f"Erreur fichier : {exc}")


# ---------------------------------------------------------------- recherche

def _walk(root: Path, include_hidden: bool, deadline: float):
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        if time.time() > deadline:
            return
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS
                       and (include_hidden or not d.startswith("."))]
        yield dirpath, filenames


def _find_files_sync(inp: dict, cwd: str) -> ToolResult:
    root = resolve_path(inp.get("root") or cwd, cwd)
    if not root.is_dir():
        return ToolResult.error(f"Dossier introuvable : {root}")
    pattern = str(inp.get("pattern", "*")).strip() or "*"
    max_results = min(int(inp.get("max_results", 100) or 100), 1000)
    include_hidden = bool(inp.get("include_hidden"))
    modified_days = inp.get("modified_within_days")
    min_time = time.time() - float(modified_days) * 86400 if modified_days else None
    deadline = time.time() + 45
    is_glob = any(c in pattern for c in "*?[")
    pat_low = pattern.lower()
    found: list[tuple[float, str]] = []
    for dirpath, filenames in _walk(root, include_hidden, deadline):
        for name in filenames:
            low = name.lower()
            ok = fnmatch.fnmatch(low, pat_low) if is_glob else pat_low in low
            if not ok:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if min_time and st.st_mtime < min_time:
                continue
            found.append((st.st_mtime, f"{full}  ({_human(st.st_size)}, modifié {datetime.fromtimestamp(st.st_mtime):%d/%m/%Y %H:%M})"))
            if len(found) >= max_results * 3:
                break
    found.sort(reverse=True)
    lines = [f for _, f in found[:max_results]]
    timeout_note = "\n[Recherche arrêtée après 45 s : précise un dossier plus ciblé.]" if time.time() > deadline else ""
    if not lines:
        return ToolResult.text(f"Aucun fichier correspondant à « {pattern} » dans {root}.{timeout_note}")
    return ToolResult.text(f"{len(lines)} résultat(s) (les plus récents d'abord) :\n" + "\n".join(lines) + timeout_note,
                           preview=f"{len(lines)} fichier(s) trouvé(s)")


async def find_files(inp: dict, cwd: str) -> ToolResult:
    return await asyncio.to_thread(_find_files_sync, inp, cwd)


def _search_text_sync(inp: dict, cwd: str) -> ToolResult:
    root = resolve_path(inp.get("root") or cwd, cwd)
    query = str(inp.get("query", ""))
    if not query:
        return ToolResult.error("Aucun texte à chercher.")
    flags = 0 if inp.get("case_sensitive") else re.IGNORECASE
    try:
        rx = re.compile(query if inp.get("regex") else re.escape(query), flags)
    except re.error as exc:
        return ToolResult.error(f"Expression régulière invalide : {exc}")
    file_glob = str(inp.get("file_pattern", "*")).lower() or "*"
    max_results = min(int(inp.get("max_results", 100) or 100), 1000)
    deadline = time.time() + 60
    targets = [root] if root.is_file() else None
    results: list[str] = []
    files_hit = 0

    def scan(fp: str):
        nonlocal files_hit
        try:
            if os.path.getsize(fp) > 10_000_000 or Path(fp).suffix.lower() in TEXT_EXT_BINARY_HINT:
                return
            with open(fp, "rb") as fh:
                head = fh.read(4096)
                if b"\x00" in head:
                    return
                data = head + fh.read()
        except OSError:
            return
        text = data.decode("utf-8", "replace")
        hit = False
        for no, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hit = True
                results.append(f"{fp}:{no}: {line.strip()[:240]}")
                if len(results) >= max_results:
                    break
        files_hit += hit

    if targets:
        scan(str(root))
    elif root.is_dir():
        for dirpath, filenames in _walk(root, bool(inp.get("include_hidden")), deadline):
            for name in filenames:
                if fnmatch.fnmatch(name.lower(), file_glob):
                    scan(os.path.join(dirpath, name))
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
    else:
        return ToolResult.error(f"Introuvable : {root}")
    if not results:
        return ToolResult.text(f"Aucune occurrence de « {query} » dans {root}.")
    return ToolResult.text(f"{len(results)} ligne(s) dans {files_hit} fichier(s) :\n" + "\n".join(results),
                           preview=f"{len(results)} occurrence(s) dans {files_hit} fichier(s)")


async def search_in_files(inp: dict, cwd: str) -> ToolResult:
    return await asyncio.to_thread(_search_text_sync, inp, cwd)


# ---------------------------------------------------------------- documents

def image_block(img, max_side: int = 1568, fmt: str = "JPEG") -> dict:
    from PIL import Image

    img = img.convert("RGB") if fmt == "JPEG" else img
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=80)
    media = "image/jpeg" if fmt == "JPEG" else "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def _read_document_sync(inp: dict, cwd: str) -> ToolResult:
    path = resolve_path(inp.get("path", ""), cwd)
    if not path.is_file():
        return ToolResult.error(f"Fichier introuvable : {path}")
    ext = path.suffix.lower()
    try:
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            from PIL import Image

            with Image.open(path) as img:
                block = image_block(img)
            return ToolResult(content=[{"type": "text", "text": f"Image : {path}"}, block],
                              preview=f"Image {path.name}", image_b64=block["source"]["data"])
        if ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            n = len(reader.pages)
            first = max(1, int(inp.get("start_page", 1) or 1))
            last = min(n, int(inp.get("end_page", first + 49) or first + 49))
            parts = [f"PDF {path.name} — {n} page(s), extrait p.{first}–{last}"]
            for i in range(first - 1, last):
                parts.append(f"\n--- Page {i + 1} ---\n{reader.pages[i].extract_text() or '(pas de texte : page scannée ?)'}")
            return ToolResult.text(truncate("\n".join(parts), 100000), preview=f"PDF {n} pages")
        if ext == ".docx":
            import docx

            d = docx.Document(str(path))
            parts = [p.text for p in d.paragraphs]
            for ti, table in enumerate(d.tables, 1):
                parts.append(f"\n[Tableau {ti}]")
                for row in table.rows:
                    parts.append(" | ".join(c.text.strip() for c in row.cells))
            return ToolResult.text(truncate("\n".join(parts), 100000), preview=f"Word {path.name}")
        if ext in (".xlsx", ".xlsm"):
            import openpyxl

            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            max_rows = int(inp.get("max_rows", 200) or 200)
            parts = []
            for ws in wb.worksheets:
                parts.append(f"\n=== Feuille « {ws.title} » ===")
                for r, row in enumerate(ws.iter_rows(values_only=True), 1):
                    if r > max_rows:
                        parts.append(f"… (lignes suivantes non affichées, max_rows={max_rows})")
                        break
                    parts.append(" | ".join("" if v is None else str(v) for v in row))
            return ToolResult.text(truncate("\n".join(parts), 100000), preview=f"Excel {path.name}")
        if ext == ".pptx":
            try:
                from pptx import Presentation
            except ImportError:
                return ToolResult.error("Lecture des .pptx : installe le module avec  .venv\\Scripts\\pip install python-pptx  (facultatif).")
            prs = Presentation(str(path))
            parts = []
            for i, slide in enumerate(prs.slides, 1):
                parts.append(f"\n--- Diapositive {i} ---")
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                        parts.append(shape.text_frame.text)
            return ToolResult.text(truncate("\n".join(parts), 100000), preview=f"PowerPoint {path.name}")
        return ToolResult.text(truncate(_read_text(path), 100000), preview=path.name)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(f"Lecture impossible de {path.name} : {exc}")


async def read_document(inp: dict, cwd: str) -> ToolResult:
    return await asyncio.to_thread(_read_document_sync, inp, cwd)


# ---------------------------------------------------------------- fichiers

async def delete_path(inp: dict, cwd: str) -> ToolResult:
    path = resolve_path(inp.get("path", ""), cwd)
    if not path.exists():
        return ToolResult.error(f"Introuvable : {path}")
    try:
        from send2trash import send2trash

        await asyncio.to_thread(send2trash, str(path))
        return ToolResult.text(f"Envoyé à la corbeille : {path} (récupérable)")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(f"Suppression impossible : {exc}")


async def move_path(inp: dict, cwd: str) -> ToolResult:
    src = resolve_path(inp.get("source", ""), cwd)
    dst = resolve_path(inp.get("destination", ""), cwd)
    if not src.exists():
        return ToolResult.error(f"Introuvable : {src}")
    if dst.exists() and not dst.is_dir() and not inp.get("overwrite"):
        return ToolResult.error(f"La destination existe déjà : {dst} (overwrite=true pour remplacer)")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if inp.get("copy"):
            if src.is_dir():
                await asyncio.to_thread(shutil.copytree, src, dst / src.name if dst.is_dir() else dst)
            else:
                await asyncio.to_thread(shutil.copy2, src, dst)
            return ToolResult.text(f"Copié : {src} → {dst}")
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return ToolResult.text(f"Déplacé : {src} → {dst}")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(f"Échec : {exc}")


async def open_path(inp: dict, cwd: str) -> ToolResult:
    target = str(inp.get("target", "")).strip()
    if not target:
        return ToolResult.error("Rien à ouvrir.")
    is_url = re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) and not re.match(r"^[a-z]:\\", target, re.I)
    path = None if is_url else resolve_path(target, cwd)
    try:
        if IS_WINDOWS:
            os.startfile(target if is_url or not path.exists() else str(path))  # type: ignore[attr-defined]
        elif IS_MAC:
            if is_url or path.exists():
                subprocess.Popen(["open", target if is_url else str(path)])
            else:
                subprocess.Popen(["open", "-a", target])
        else:
            if is_url or path.exists():
                subprocess.Popen(["xdg-open", target if is_url else str(path)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen([target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        return ToolResult.text(f"Ouvert : {target}. Prends une capture d'écran pour vérifier.")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(f"Ouverture impossible de « {target} » : {exc}")


async def clipboard(inp: dict, cwd: str) -> ToolResult:
    try:
        import pyperclip
    except ImportError:
        return ToolResult.error("pyperclip n'est pas installé.")
    try:
        if inp.get("action") == "write":
            pyperclip.copy(str(inp.get("text", "")))
            return ToolResult.text("Texte copié dans le presse-papiers.")
        return ToolResult.text(truncate(pyperclip.paste() or "(presse-papiers vide)", 50000))
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(f"Presse-papiers inaccessible : {exc}")
