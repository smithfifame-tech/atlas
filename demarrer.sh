#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Lance d'abord ./installer.sh"
  exit 1
fi
exec .venv/bin/python -m app.main "$@"
