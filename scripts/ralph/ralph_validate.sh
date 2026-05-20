#!/usr/bin/env bash
# Ralph Wiggum Validation Gate v1
# Exits 0 only when ALL quality checks pass.
#
# Ralph Loop Test Policy (ENFORCED):
#   - Default tier is TARGETED (affected modules via detect_affected_tests.py).
#   - e2e and performance tiers are NEVER run in the Ralph loop.
#   - The full test suite is NEVER run in the Ralph loop.
#   - Operator override for e2e/performance: RALPH_ALLOW_E2E=1
#
# Strategy:
#   - pytest tier is configurable: smoke | targeted | integration | full | e2e | performance
#     (default: targeted)
#   - black, isort, flake8, and mypy run only on MODIFIED or UNTRACKED Python files.
#     This avoids forcing a massive reformat of legacy code while keeping new changes clean.
#   - e2e and performance tiers are blocked unless RALPH_ALLOW_E2E=1 is set.
#
# Environment variables:
#   RALPH_PYTHON_CMD     - Python executable to use (auto-detected if not set)
#   RALPH_VENV_PATH      - Path to virtual env (default: .venv)
#   RALPH_TEST_DIR       - Root test directory (default: tests)
#   RALPH_ALLOW_E2E      - Set to 1 to allow e2e/performance tiers

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_DIR}"

# Parse arguments
TIER="targeted"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tier)
            TIER="$2"
            shift 2
            ;;
        --tier=*)
            TIER="${1#*=}"
            shift
            ;;
        *)
            echo "[RALPH] Unknown argument: $1"
            echo "Valid tiers: smoke, targeted, integration, full, e2e, performance"
            exit 1
            ;;
    esac
done

# --- Ralph Loop Policy Enforcement ---
if [[ "${TIER}" == "e2e" || "${TIER}" == "performance" ]]; then
    if [[ "${RALPH_ALLOW_E2E:-0}" != "1" ]]; then
        echo "[RALPH] ERROR: ${TIER} tier is blocked in the Ralph loop."
        echo "[RALPH] Targeted-tests-only policy enforced."
        echo "[RALPH] Set RALPH_ALLOW_E2E=1 to override (operator-only)."
        exit 1
    fi
fi

# Detect Python environment
VENV_PATH="${RALPH_VENV_PATH:-${PROJECT_DIR}/.venv}"
PYTHON_CMD="${RALPH_PYTHON_CMD:-}"

if [[ -z "${PYTHON_CMD}" ]]; then
    if [[ -f "${VENV_PATH}/bin/python" ]]; then
        PYTHON_CMD="${VENV_PATH}/bin/python"
    elif [[ -f "${VENV_PATH}/bin/python3" ]]; then
        PYTHON_CMD="${VENV_PATH}/bin/python3"
    elif command -v python3 &>/dev/null; then
        PYTHON_CMD="python3"
    else
        echo "[RALPH] ERROR: No Python executable found. Set RALPH_PYTHON_CMD or create a venv at ${VENV_PATH}"
        exit 1
    fi
fi

# Ensure virtual environment is active (if available)
if [[ -z "${VIRTUAL_ENV:-}" && -f "${VENV_PATH}/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "${VENV_PATH}/bin/activate"
fi

TEST_DIR="${RALPH_TEST_DIR:-tests}"
FAILED=0

echo "========================================="
echo "[RALPH] Validation Gate Starting..."
echo "[RALPH] Test tier: ${TIER}"
echo "[RALPH] Python: ${PYTHON_CMD}"
echo "========================================="

# Determine modified / untracked Python files
MODIFIED_PY=$( (git diff --name-only --diff-filter=ACM && git ls-files --others --exclude-standard) | grep '\.py$' | sort -u || true )

# Override pyproject.toml addopts if needed
PYTEST_ADDOPTS_OVERRIDE=(-o addopts="--tb=short --strict-markers")

# 1. pytest — tiered
case "${TIER}" in
    smoke)
        echo ""
        echo "[1/5] Running SMOKE tests (${TEST_DIR}/unit/ -x -q, unit marker) ..."
        set +e
        ${PYTHON_CMD} -m pytest "${TEST_DIR}/unit/" -x -q --tb=short "${PYTEST_ADDOPTS_OVERRIDE[@]}" -m "unit"
        PYTEST_EXIT=$?
        set -e
        ;;
    targeted|targetted)
        AFFECTED_TESTS=$(${PYTHON_CMD} scripts/ralph/detect_affected_tests.py 2>/dev/null || echo "${TEST_DIR}/unit/")
        if [[ -z "${AFFECTED_TESTS}" ]]; then
            echo ""
            echo "[1/5] No affected tests detected. Skipping pytest."
            PYTEST_EXIT=0
        else
            echo ""
            echo "[1/5] Running TARGETED tests: ${AFFECTED_TESTS} ..."
            set +e
            ${PYTHON_CMD} -m pytest ${AFFECTED_TESTS} -q --tb=short "${PYTEST_ADDOPTS_OVERRIDE[@]}" -m "not e2e and not performance"
            PYTEST_EXIT=$?
            set -e
        fi
        ;;
    integration)
        echo ""
        echo "[1/5] Running INTEGRATION tests (${TEST_DIR}/integration/ -q, integration marker) ..."
        set +e
        ${PYTHON_CMD} -m pytest "${TEST_DIR}/integration/" -q --tb=short "${PYTEST_ADDOPTS_OVERRIDE[@]}" -m "integration"
        PYTEST_EXIT=$?
        set -e
        ;;
    full)
        echo ""
        echo "[1/5] Running FULL pytest suite (${TEST_DIR}/ -q, excludes e2e/performance/broker_live) ..."
        set +e
        ${PYTHON_CMD} -m pytest "${TEST_DIR}/" -q --tb=short "${PYTEST_ADDOPTS_OVERRIDE[@]}" -m "not e2e and not performance and not broker_live"
        PYTEST_EXIT=$?
        set -e
        ;;
    e2e)
        echo ""
        echo "[1/5] Running E2E tests (${TEST_DIR}/e2e/ -v) ..."
        set +e
        ${PYTHON_CMD} -m pytest "${TEST_DIR}/e2e/" -v --tb=short "${PYTEST_ADDOPTS_OVERRIDE[@]}"
        PYTEST_EXIT=$?
        set -e
        ;;
    performance)
        echo ""
        echo "[1/5] Running PERFORMANCE tests (${TEST_DIR}/performance/ -v) ..."
        set +e
        ${PYTHON_CMD} -m pytest "${TEST_DIR}/performance/" -v --tb=short "${PYTEST_ADDOPTS_OVERRIDE[@]}"
        PYTEST_EXIT=$?
        set -e
        ;;
    *)
        echo "[RALPH] Unknown tier: ${TIER}"
        echo "Valid tiers: smoke, targeted, integration, full, e2e, performance"
        exit 1
        ;;
esac

# Handle pytest result
if [[ ${PYTEST_EXIT} -eq 0 ]]; then
    echo "[1/5] pytest ${TIER} PASSED"
elif [[ ${PYTEST_EXIT} -eq 5 ]]; then
    echo "[1/5] pytest ${TIER} PASSED (no tests collected — all deselected or none match filter)"
else
    echo "[1/5] pytest ${TIER} FAILED"
    FAILED=1
fi

if [[ -n "${MODIFIED_PY}" ]]; then
    echo ""
    echo "[RALPH] Modified/untracked Python files detected:"
    echo "${MODIFIED_PY}"
    echo ""

    # 2. black
    echo "[2/5] Running black --check on modified files ..."
    if echo "${MODIFIED_PY}" | xargs ${PYTHON_CMD} -m black --check 2>/dev/null; then
        echo "[2/5] black PASSED"
    else
        echo "[2/5] black FAILED"
        FAILED=1
    fi

    # 3. isort
    echo ""
    echo "[3/5] Running isort --check-only on modified files ..."
    if echo "${MODIFIED_PY}" | xargs ${PYTHON_CMD} -m isort --check-only 2>/dev/null; then
        echo "[3/5] isort PASSED"
    else
        echo "[3/5] isort FAILED"
        FAILED=1
    fi

    # 4. flake8
    echo ""
    echo "[4/5] Running flake8 on modified files ..."
    if echo "${MODIFIED_PY}" | xargs ${PYTHON_CMD} -m flake8 2>/dev/null; then
        echo "[4/5] flake8 PASSED"
    else
        echo "[4/5] flake8 FAILED"
        FAILED=1
    fi

    # 5. mypy
    echo ""
    echo "[5/5] Running mypy on modified files ..."
    MYPY_MODULES=""
    for f in ${MODIFIED_PY}; do
        if [[ "$f" == *.py ]]; then
            if [[ "$f" == *__init__.py ]]; then
                mod=$(echo "$f" | sed 's|^src/||' | sed 's|/|.|g' | sed 's|\.\_\_init\_\_\.py$||')
            else
                mod=$(echo "$f" | sed 's|^src/||' | sed 's|/|.|g' | sed 's|\.py$||')
            fi
            MYPY_MODULES="${MYPY_MODULES} -m ${mod}"
        fi
    done
    if ${PYTHON_CMD} -m mypy --follow-imports=silent ${MYPY_MODULES} 2>/dev/null; then
        echo "[5/5] mypy PASSED"
    else
        echo "[5/5] mypy FAILED"
        FAILED=1
    fi
else
    echo ""
    echo "[RALPH] No modified/untracked Python files detected."
    echo "[RALPH] Skipping targeted black / isort / flake8 / mypy checks."
fi

echo ""
echo "========================================="
if [[ ${FAILED} -eq 0 ]]; then
    echo "RALPH_GATE_PASSED"
    echo "========================================="
    exit 0
else
    echo "RALPH_GATE_FAILED"
    echo "========================================="
    exit 1
fi
