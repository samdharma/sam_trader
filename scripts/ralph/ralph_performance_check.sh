#!/usr/bin/env bash
# Ralph Wiggum Performance Regression Gate v1
# Runs all performance benchmarks and fails if any test fails or regresses >20%.
#
# Usage:
#   bash scripts/ralph/ralph_performance_check.sh
#   bash scripts/ralph/ralph_performance_check.sh --update-baselines
#
# Environment variables:
#   RALPH_PYTHON_CMD  - Python executable to use
#   RALPH_VENV_PATH   - Path to virtual env (default: .venv)
#   RALPH_TEST_DIR    - Root test directory (default: tests)
#
# Exit codes:
#   0 = all performance tests passed, no regressions
#   1 = one or more tests failed or regressed

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_DIR}"

UPDATE_BASELINES=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --update-baselines)
            UPDATE_BASELINES=true
            shift
            ;;
        *)
            echo "[PERF] Unknown argument: $1"
            exit 1
            ;;
    esac
done

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
        echo "[PERF] ERROR: No Python executable found."
        exit 1
    fi
fi

if [[ -z "${VIRTUAL_ENV:-}" && -f "${VENV_PATH}/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "${VENV_PATH}/bin/activate"
fi

TEST_DIR="${RALPH_TEST_DIR:-tests}"

echo "========================================="
echo "[PERF] Performance Regression Gate"
echo "========================================="

if [[ "${UPDATE_BASELINES}" == "true" ]]; then
    echo "[PERF] Update mode: existing baselines will be overwritten."
    echo ""
fi

if ${PYTHON_CMD} -m pytest "${TEST_DIR}/performance/" -v --tb=short; then
    echo ""
    echo "[PERF] All performance tests PASSED"
else
    echo ""
    echo "[PERF] PERFORMANCE GATE FAILED"
    echo "========================================="
    exit 1
fi

echo ""
echo "========================================="
echo "PERFORMANCE_GATE_PASSED"
echo "========================================="
exit 0
