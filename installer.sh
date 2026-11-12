#!/usr/bin/env bash
# Installation pour macOS et Linux.
set -e
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "Python 3.10+ est requis : https://www.python.org/downloads/"
  exit 1
fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo "Python 3.10 minimum requis."; exit 1; }

echo "[1/3] Environnement Python…"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
echo "[2/3] Dépendances…"
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt
echo "[3/3] Configuration…"
[ -f .env ] || cp .env.example .env
chmod +x demarrer.sh

if [ "$(uname)" = "Darwin" ]; then
  echo
  echo "macOS : autorise ton Terminal dans Réglages Système → Confidentialité et sécurité"
  echo "  → Accessibilité ET Enregistrement de l'écran, sinon l'assistant ne pourra ni voir ni cliquer."
elif [ "$(uname)" = "Linux" ]; then
  echo
  echo "Linux : le contrôle de l'écran nécessite une session X11 (pas Wayland) et :"
  echo "  sudo apt install xclip"
fi
command -v node >/dev/null || echo "Conseil : installe Node.js (https://nodejs.org) pour les connecteurs MCP."
echo
echo "Terminé ! Lance l'assistant avec : ./demarrer.sh"
