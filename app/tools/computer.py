"""Contrôle de l'écran : captures, souris et clavier (toolset computer_toolset_20260801).

Les coordonnées envoyées par Claude sont dans l'espace de pixels des captures
qu'on lui renvoie ; on les convertit vers l'espace réel de l'écran.
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
import time

from ..config import IS_MAC, IS_WINDOWS
from . import ToolResult

MAX_LONG_EDGE = 2576
MAX_VISUAL_TOKENS = 4784

_pg = None
_init_error: str | None = None


class EmergencyStop(Exception):
    """L'utilisateur a jeté la souris dans un coin de l'écran."""


def _load():
    global _pg, _init_error
    if _pg is not None or _init_error is not None:
        return _pg
    try:
        if IS_WINDOWS:
            import ctypes

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
        import sys
        import types

        # pyautogui importe « mouseinfo », qui quitte Python si tkinter manque (Linux).
        # On n'en a pas besoin : on le remplace par un module vide.
        sys.modules.setdefault("mouseinfo", types.ModuleType("mouseinfo"))
        import pyautogui  # noqa: WPS433

        pyautogui.FAILSAFE = True  # souris dans un coin = arrêt d'urgence
        pyautogui.PAUSE = 0.05
        pyautogui.size()
        _pg = pyautogui
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        _init_error = f"Contrôle de l'écran indisponible : {exc}"
    return _pg


def available() -> tuple[bool, str]:
    pg = _load()
    if pg is None:
        return False, _init_error or "indisponible"
    try:
        import mss  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        return False, f"Module manquant : {exc}"
    return True, ""


class Screen:
    def __init__(self, max_width: int = 1366):
        self.max_width = max_width
        self.logical_w = 0
        self.logical_h = 0
        self.shot_w = 0
        self.shot_h = 0
        self.last_b64: str | None = None

    # ---------- géométrie
    def _refresh_geometry(self):
        pg = _load()
        self.logical_w, self.logical_h = pg.size()
        w = min(self.max_width, self.logical_w, MAX_LONG_EDGE)
        h = round(self.logical_h * w / self.logical_w)
        while math.ceil(w / 28) * math.ceil(h / 28) > MAX_VISUAL_TOKENS or max(w, h) > MAX_LONG_EDGE:
            w = int(w * 0.95)
            h = round(self.logical_h * w / self.logical_w)
        self.shot_w, self.shot_h = w, h

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        if not self.shot_w:
            self._refresh_geometry()
        sx = round(float(x) * self.logical_w / self.shot_w)
        sy = round(float(y) * self.logical_h / self.shot_h)
        if not (0 <= sx <= self.logical_w and 0 <= sy <= self.logical_h):
            raise ValueError(f"Coordonnées hors de l'écran : ({x}, {y})")
        return min(sx, self.logical_w - 1), min(sy, self.logical_h - 1)

    def to_shot(self, sx: float, sy: float) -> tuple[int, int]:
        return round(sx * self.shot_w / self.logical_w), round(sy * self.shot_h / self.logical_h)

    # ---------- captures
    def _grab(self):
        import mss
        from PIL import Image

        with mss.mss() as sct:
            mon = sct.monitors[1]
            raw = sct.grab(mon)
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def _encode(self, img) -> str:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()

    def screenshot(self) -> dict:
        from PIL import Image, ImageDraw

        self._refresh_geometry()
        img = self._grab().resize((self.shot_w, self.shot_h), Image.LANCZOS)
        try:  # on dessine le curseur (absent des captures système)
            cx, cy = self.to_shot(*_load().position())
            d = ImageDraw.Draw(img)
            d.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), outline=(255, 0, 0), width=2)
            d.line((cx - 10, cy, cx + 10, cy), fill=(255, 0, 0))
            d.line((cx, cy - 10, cx, cy + 10), fill=(255, 0, 0))
        except Exception:
            pass
        self.last_b64 = self._encode(img)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": self.last_b64}}

    def zoom(self, region) -> dict:
        from PIL import Image

        if not self.shot_w:
            self._refresh_geometry()
        x0, y0, x1, y1 = [float(v) for v in region]
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Région invalide : il faut [x0, y0, x1, y1] avec x1 > x0 et y1 > y0")
        full = self._grab()
        fx, fy = full.width / self.shot_w, full.height / self.shot_h
        box = (max(0, int(x0 * fx)), max(0, int(y0 * fy)), min(full.width, int(x1 * fx)), min(full.height, int(y1 * fy)))
        crop = full.crop(box)
        scale = min(self.shot_w / crop.width, self.shot_h / crop.height)
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS)
        b64 = self._encode(crop)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}


KEY_ALIASES = {
    "return": "enter", "kp_enter": "enter", "enter": "enter",
    "backspace": "backspace", "escape": "esc", "esc": "esc",
    "page_down": "pagedown", "page_up": "pageup", "next": "pagedown", "prior": "pageup",
    "delete": "delete", "del": "delete", "insert": "insert", "home": "home", "end": "end",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "tab": "tab", "space": "space", "minus": "-", "plus": "+", "equal": "=", "comma": ",", "period": ".",
    "slash": "/", "backslash": "\\", "semicolon": ";", "apostrophe": "'", "grave": "`",
    "bracketleft": "[", "bracketright": "]", "caps_lock": "capslock", "print": "printscreen",
    "control": "ctrl", "control_l": "ctrl", "control_r": "ctrl", "ctrl": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "altright", "option": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shiftright",
    "audiovolumeup": "volumeup", "audiovolumedown": "volumedown", "audiomute": "volumemute",
}


def map_key(name: str) -> str:
    low = name.strip().lower()
    if low in ("super", "super_l", "super_r", "win", "windows", "meta", "cmd", "command"):
        return "command" if IS_MAC else "win"
    if low.startswith("xf86"):
        low = low[4:]
    return KEY_ALIASES.get(low, low)


def split_combo(text: str) -> list[str]:
    parts = [p for p in str(text).replace(" ", "").split("+") if p]
    if str(text).endswith("++"):
        parts.append("+")
    return [map_key(p) for p in parts]


class Computer:
    def __init__(self, max_width: int = 1366):
        self.screen = Screen(max_width)

    async def run(self, name: str, inp: dict) -> ToolResult:
        try:
            return await asyncio.to_thread(self._run_sync, name, inp)
        except Exception as exc:  # noqa: BLE001
            pg = _load()
            if pg is not None and isinstance(exc, pg.FailSafeException):
                raise EmergencyStop() from exc
            return ToolResult.error(f"Échec de l'action {name} : {exc}")

    def _modifiers(self, text):
        return split_combo(text) if text else []

    def _click(self, inp: dict, button: str = "left", clicks: int = 1):
        pg = _load()
        mods = self._modifiers(inp.get("text"))
        for m in mods:
            pg.keyDown(m)
        try:
            if inp.get("coordinate") is not None:
                x, y = self.screen.to_screen(*inp["coordinate"])
                pg.click(x=x, y=y, clicks=clicks, interval=0.08, button=button)
            else:
                pg.click(clicks=clicks, interval=0.08, button=button)
        finally:
            for m in reversed(mods):
                pg.keyUp(m)

    def _type(self, text: str):
        pg = _load()
        if text.isascii():
            for i, chunk in enumerate(text.split("\n")):
                if i:
                    pg.press("enter")
                if chunk:
                    pg.write(chunk, interval=0.008)
            return
        # Caractères accentués / emoji : on passe par le presse-papiers.
        import pyperclip

        previous = None
        try:
            previous = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        time.sleep(0.05)
        pg.hotkey("command" if IS_MAC else "ctrl", "v")
        time.sleep(0.15)
        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass

    def _run_sync(self, name: str, inp: dict) -> ToolResult:
        pg = _load()
        if pg is None:
            return ToolResult.error(_init_error or "Contrôle de l'écran indisponible.")
        if name == "screenshot":
            block = self.screen.screenshot()
            return ToolResult(content=[block], preview=f"Capture {self.screen.shot_w}×{self.screen.shot_h}",
                              image_b64=block["source"]["data"])
        if name == "zoom":
            block = self.screen.zoom(inp.get("region", []))
            return ToolResult(content=[block], preview="Zoom", image_b64=block["source"]["data"])
        if name == "left_click":
            self._click(inp, "left")
        elif name == "right_click":
            self._click(inp, "right")
        elif name == "middle_click":
            self._click(inp, "middle")
        elif name == "double_click":
            self._click(inp, "left", 2)
        elif name == "triple_click":
            self._click(inp, "left", 3)
        elif name == "left_click_drag":
            sx, sy = self.screen.to_screen(*inp["start_coordinate"])
            ex, ey = self.screen.to_screen(*inp["coordinate"])
            mods = self._modifiers(inp.get("text"))
            for m in mods:
                pg.keyDown(m)
            try:
                pg.moveTo(sx, sy)
                pg.mouseDown(button="left")
                pg.moveTo(ex, ey, duration=0.4)
                pg.mouseUp(button="left")
            finally:
                for m in reversed(mods):
                    pg.keyUp(m)
        elif name == "mouse_move":
            x, y = self.screen.to_screen(*inp["coordinate"])
            pg.moveTo(x, y, duration=0.15)
        elif name == "left_mouse_down":
            pg.mouseDown(button="left")
        elif name == "left_mouse_up":
            pg.mouseUp(button="left")
        elif name == "cursor_position":
            if not self.screen.shot_w:
                self.screen._refresh_geometry()
            x, y = self.screen.to_shot(*pg.position())
            return ToolResult.text(f"X={x}, Y={y}")
        elif name == "scroll":
            if inp.get("coordinate") is not None:
                x, y = self.screen.to_screen(*inp["coordinate"])
                pg.moveTo(x, y)
            amount = max(1, min(int(inp.get("scroll_amount", 3) or 3), 50))
            direction = inp.get("scroll_direction", "down")
            mods = self._modifiers(inp.get("text"))
            for m in mods:
                pg.keyDown(m)
            try:
                factor = 120 if IS_WINDOWS else 1
                if direction in ("up", "down"):
                    pg.scroll((amount if direction == "up" else -amount) * factor)
                else:
                    try:
                        pg.hscroll((amount if direction == "right" else -amount) * factor)
                    except Exception:
                        pg.keyDown("shift")
                        pg.scroll((amount if direction == "left" else -amount) * factor)
                        pg.keyUp("shift")
            finally:
                for m in reversed(mods):
                    pg.keyUp(m)
        elif name == "type":
            self._type(str(inp.get("text", "")))
        elif name == "key":
            keys = split_combo(inp.get("text", ""))
            repeat = max(1, min(int(inp.get("repeat", 1) or 1), 100))
            for _ in range(repeat):
                if len(keys) == 1:
                    pg.press(keys[0])
                else:
                    pg.hotkey(*keys)
        elif name == "hold_key":
            keys = split_combo(inp.get("text", ""))
            duration = max(0.0, min(float(inp.get("duration", 1) or 1), 300))
            for k in keys:
                pg.keyDown(k)
            time.sleep(duration)
            for k in reversed(keys):
                pg.keyUp(k)
        elif name == "wait":
            time.sleep(max(0.0, min(float(inp.get("duration", 1) or 1), 300)))
        else:
            return ToolResult.error(f"Action inconnue : {name}")
        time.sleep(0.25)  # laisse l'interface se mettre à jour
        return ToolResult.text("OK", preview=_describe(name, inp))


def _describe(name: str, inp: dict) -> str:
    if "coordinate" in inp:
        return f"{name} en {inp['coordinate']}"
    if name in ("type", "key", "hold_key"):
        return f"{name} : {str(inp.get('text', ''))[:80]}"
    return name
