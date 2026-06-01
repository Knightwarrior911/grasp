#!/usr/bin/env bash
# Grasp installer (macOS / Linux). Idempotent. Prints "GRASP INSTALL OK" or "FAILED: <reason>".
# Run:  bash scripts/install.sh
# NOTE: Grasp is built and tested for Windows. On mac/linux the pointer/keyboard backends
# import but DPI/mss behaviour differs; treat as experimental there.
set -euo pipefail

fail() { echo "FAILED: $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Grasp repo: $REPO_ROOT"

PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$("$cand" -c 'import sys; print(sys.executable)')"; break
    fi
  fi
done
[ -n "$PY" ] || fail "no Python 3.10+ found."
echo "Using Python: $PY"

echo "Installing pip dependencies..."
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install -r "$REPO_ROOT/requirements.txt" --quiet || fail "pip install failed"

echo "Smoke test (import + scale tests)..."
export PYTHONPATH="$REPO_ROOT"
"$PY" -c "import grasp, grasp.server; from grasp import Computer; print('import OK')" || fail "grasp import failed"
"$PY" -m pytest -q "$REPO_ROOT/tests" || echo "WARN: some tests failed (install still usable)"

if command -v claude >/dev/null 2>&1; then
  echo "Registering 'grasp' MCP server with Claude Code..."
  claude mcp remove grasp -s user >/dev/null 2>&1 || true
  claude mcp add grasp -s user -e "PYTHONPATH=$REPO_ROOT" -- "$PY" -m grasp || fail "claude mcp add failed"
  echo; echo "GRASP INSTALL OK"
  echo "Restart Claude Code, then ask it to 'take a screenshot of my screen with Grasp'."
else
  echo; echo "GRASP INSTALL OK (Python side)"
  echo "Claude Code CLI ('claude') not found. Add this to your MCP config:"
  echo "  \"grasp\": { \"command\": \"$PY\", \"args\": [\"-m\",\"grasp\"], \"env\": { \"PYTHONPATH\": \"$REPO_ROOT\" } }"
fi
