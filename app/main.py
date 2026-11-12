"""Point d'entrée : python -m app.main"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import webbrowser

import uvicorn

from .config import get_access_token, load_settings


def main() -> None:
    settings = load_settings()
    port = int(settings.get("port", 8765))
    token = get_access_token()
    url = f"http://127.0.0.1:{port}/?token={token}"

    print("=" * 64)
    print(f"  {settings.get('assistant_name', 'Atlas')} est prêt.")
    print(f"  Ouvre cette adresse dans ton navigateur :\n  {url}")
    print("  (Garde cette fenêtre ouverte. Ctrl+C pour arrêter.)")
    if not settings.get("api_key"):
        print("  ⚠  Aucune clé API : ajoute-la dans les Réglages de l'interface.")
    print("=" * 64)

    if "--no-browser" not in sys.argv:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()

    config = uvicorn.Config("app.server:app", host="127.0.0.1", port=port, log_level="warning",
                            loop="none", ws_max_size=64 * 1024 * 1024)
    server = uvicorn.Server(config)
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
