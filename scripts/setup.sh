#!/usr/bin/env bash
# Rebuild everything a git clone does not carry.
#
# Four things paleditor needs are built, not committed, so a fresh clone (or a
# deleted and re-cloned directory) has none of them:
#
#   .venv/              the Python environment
#   lib/libooz.so       the Oodle decompressor (see docs/oodle.md)
#   frontend/node_modules
#   frontend/dist/spa   the built web UI
#
# Config and the systemd unit live outside the repo, so those survive.
#
#   scripts/setup.sh --save-dir "/path/to/SaveGames/0/<world-id>"
#   scripts/setup.sh --no-frontend
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SAVE_DIR=""
WANT_FRONTEND=1
EXTRAS=".[parser,dev]"

while [ $# -gt 0 ]; do
  case "$1" in
    --save-dir)    SAVE_DIR="$2"; shift 2 ;;
    --no-frontend) WANT_FRONTEND=0; shift ;;
    --extras)      EXTRAS="$2"; shift 2 ;;
    -h|--help)     sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

cd "$ROOT"
problems=()
note() { printf '  %s\n' "$*"; }

echo "==> python environment"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  note "created .venv"
else
  note ".venv already present"
fi
.venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1 || true
.venv/bin/python -m pip install -q -e "$EXTRAS"
note "installed $EXTRAS"
note "$(.venv/bin/paleditor --version)"

echo "==> oodle decompressor"
if .venv/bin/python -c "from paleditor.saves import oodle; raise SystemExit(0 if oodle.available() else 1)" 2>/dev/null; then
  note "already available"
else
  if [ -n "$SAVE_DIR" ]; then
    "$HERE/oodle/build.sh" --save-dir "$SAVE_DIR" || problems+=("the Oodle build failed; see docs/oodle.md")
  else
    "$HERE/oodle/build.sh" || problems+=("the Oodle build failed; see docs/oodle.md")
  fi
fi

echo "==> frontend"
if [ "$WANT_FRONTEND" -eq 0 ]; then
  note "skipped (--no-frontend)"
elif ! command -v npm >/dev/null; then
  note "npm not installed; skipping. The API runs without it, only the web UI is missing."
else
  ( cd frontend && npm install --silent && npx quasar build >/dev/null ) \
    && note "built frontend/dist/spa" \
    || problems+=("the frontend build failed; the API still works without it")
fi

echo
if [ ${#problems[@]} -gt 0 ]; then
  echo "finished with problems:"
  for p in "${problems[@]}"; do echo "  - $p"; done
else
  echo "ready."
fi
echo
echo "Next:"
echo "  .venv/bin/paleditor check-config  -c /etc/paleditor/paleditor.toml"
echo "  .venv/bin/paleditor check-service -c /etc/paleditor/paleditor.toml"
[ ${#problems[@]} -eq 0 ] || exit 1
