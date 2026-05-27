#!/bin/bash
# SAM Trader V3 — Host-side deploy wrapper
# Usage: ./deploy.sh [options] [start|stop|build]
# Ops commands live in sam-services: docker exec sam-services sam <command>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker/docker-compose.yml"
ENV_FILE="${SCRIPT_DIR}/.env"

DO_BUILD=false
TAG=""
ACTION="start"
EXPLICIT_ACTION=false

usage() {
  cat <<EOF
Usage: ./deploy.sh [options] [action]

Options:
  --build          Build images before starting (or just build if no explicit start)
  --tag <tag>      Git tag to checkout before building
  --setup          Re-run first-run wizard to regenerate .env
  -h, --help       Show this help

Actions: start (default), stop, build

Examples:
  ./deploy.sh start
  ./deploy.sh --build start
  ./deploy.sh --tag v1.0.0 --build
  ./deploy.sh stop
  ./deploy.sh --setup

Daily update: ./deploy.sh --build && docker exec sam-services sam apply
Ops commands:  docker exec sam-services sam <command>
EOF
  exit 1
}

check_prereqs() {
  command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not installed"; exit 1; }
  docker compose version >/dev/null 2>&1 || { echo "ERROR: docker compose not installed"; exit 1; }
  command -v git >/dev/null 2>&1 || { echo "ERROR: git not installed"; exit 1; }
}

run_wizard() {
  echo "INFO: Running first-run wizard..."
  cd "${SCRIPT_DIR}" && python3 scripts/wizard.py
  echo "INFO: Wizard complete. Review .env, then re-run deploy.sh"
  exit 0
}

setup_env() {
  if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    echo "WARN: .env not found"
    run_wizard
  fi
}

ensure_network() {
  if ! docker network inspect sam-net >/dev/null 2>&1; then
    echo "INFO: Creating Docker network sam-net"
    docker network create sam-net
  fi
}

run_git_ops() {
  cd "${SCRIPT_DIR}"
  if [[ -n "${TAG}" ]]; then
    echo "INFO: Fetching tags..."
    git fetch --tags
    echo "INFO: Checking out tag ${TAG}"
    git checkout "${TAG}"
  elif [[ "${DO_BUILD}" == true || "${ACTION}" == "build" ]]; then
    echo "INFO: Pulling latest code..."
    git pull
  fi
}

run_build() {
  run_git_ops
  cd "${SCRIPT_DIR}"
  echo "INFO: Building Docker images..."
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" build
  echo "INFO: Build complete"
}

wait_for_healthy() {
  local service="$1" max="${2:-30}"
  echo "INFO: Waiting for ${service} to become healthy..."
  for ((i = 1; i <= max; i++)); do
    if docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps "$service" 2>/dev/null | grep -q "healthy"; then
      echo "INFO: ${service} is healthy"; return 0
    fi
    sleep 2
  done
  echo "ERROR: ${service} failed to become healthy within $((max * 2))s"; exit 1
}

start_stack() {
  cd "${SCRIPT_DIR}"

  echo "INFO: Starting core infrastructure (postgres, redis)"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d sam-postgres sam-redis
  wait_for_healthy sam-postgres
  wait_for_healthy sam-redis

  echo "INFO: Starting Futu OpenD"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d sam-futu-opend
  wait_for_healthy sam-futu-opend 60

  echo "INFO: Starting IB Gateway"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d sam-ib-gateway
  wait_for_healthy sam-ib-gateway 60

  echo "INFO: Starting sam-trader"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d sam-trader
  wait_for_healthy sam-trader 60

  echo "INFO: Starting sam-services"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d sam-services
  wait_for_healthy sam-services 60

  echo "INFO: Stack is up"
  echo "INFO: Ops commands: docker exec sam-services sam <command>"
}

stop_stack() {
  cd "${SCRIPT_DIR}"
  echo "INFO: Stopping all containers"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" down
  echo "INFO: Stack stopped"
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --build) DO_BUILD=true; shift ;;
      --tag)
        [[ -n "${2:-}" ]] || { echo "ERROR: --tag requires a value"; usage; }
        TAG="$2"; shift 2 ;;
      --setup) run_wizard ;;
      -h|--help) usage ;;
      start|stop|build) ACTION="$1"; EXPLICIT_ACTION=true; shift ;;
      *) echo "ERROR: Unknown option: $1"; usage ;;
    esac
  done

  check_prereqs
  setup_env
  ensure_network

  if [[ "$DO_BUILD" == true && "$EXPLICIT_ACTION" == false ]]; then
    run_build
  elif [[ "$DO_BUILD" == true && "$ACTION" == "start" ]]; then
    run_build; start_stack
  elif [[ "$ACTION" == "build" ]]; then
    run_build
  elif [[ "$ACTION" == "start" ]]; then
    start_stack
  elif [[ "$ACTION" == "stop" ]]; then
    stop_stack
  else
    echo "ERROR: Unknown action: ${ACTION}"; usage
  fi
}

main "$@"
