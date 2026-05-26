#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Jardin — evaluation runner
#
# Usage:
#   ./evals/run_evals.sh [--test-id tc_001] [--verbose]
#
# Exit codes:
#   0  all good — pass rate ≥ 70 %
#   1  pass rate < 70 %  or  environment / dependency error
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV="${REPO_ROOT}/backend/.venv"
EVAL_SCRIPT="${SCRIPT_DIR}/run_evals.py"

# ── Activate virtualenv ────────────────────────────────────────────────────────
if [[ ! -f "${VENV}/bin/activate" ]]; then
  echo "ERROR: virtualenv not found at ${VENV}" >&2
  echo "       Run: cd backend && python -m venv .venv && pip install -r requirements.txt" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${VENV}/bin/activate"

# ── Check required environment variable ───────────────────────────────────────
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set." >&2
  echo "       Export it or add it to .env and re-run." >&2
  exit 1
fi

# ── Source .env if present (for local runs outside Docker) ────────────────────
ENV_FILE="${REPO_ROOT}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  # Only export lines that look like VAR=value (skip comments and blanks)
  set -a
  # shellcheck source=/dev/null
  grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${ENV_FILE}" | while IFS= read -r line; do
    export "${line?}"
  done
  set +a
fi

# ── Dependency check ───────────────────────────────────────────────────────────
python - <<'PYCHECK'
import importlib, sys

missing = []
for mod in ("anthropic", "langchain_anthropic", "langgraph", "pydantic"):
    if importlib.util.find_spec(mod) is None:
        missing.append(mod)

if missing:
    print(f"ERROR: missing Python packages: {', '.join(missing)}", file=sys.stderr)
    print("       Run: pip install -r backend/requirements.txt", file=sys.stderr)
    sys.exit(1)
PYCHECK

# ── Run evals ─────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║             Jardin garden planner — eval suite               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

python "${EVAL_SCRIPT}" "$@"
EXIT_CODE=$?

if [[ ${EXIT_CODE} -eq 0 ]]; then
  echo "✓ Eval suite PASSED (pass rate ≥ 70%)"
else
  echo "✗ Eval suite FAILED (pass rate < 70%)" >&2
fi

exit ${EXIT_CODE}
