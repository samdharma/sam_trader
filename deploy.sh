#!/bin/bash
# SAM Trader V3 — Deployment Script
# Usage: ./deploy.sh [--with-futu] [--with-ib] [--with-services] [start|stop|restart]
#
# Ops commands (status, health, backup, restore, quote, logs) are handled by
# the `sam` CLI inside the sam-services container.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker/docker-compose.yml"

# Profiles
WITH_FUTU=false
WITH_IB=false
WITH_SERVICES=false

# Action
ACTION="start"

usage() {
  cat <<EOF
Usage: ./deploy.sh [options] [action]

Options:
  --with-futu      Include Futu OpenD broker profile
  --with-ib        Include IB Gateway broker profile
  --with-services  Include sam-services operations container

Actions:
  start            Start the stack (default)
  stop             Stop all containers
  restart          Restart the stack gracefully

Examples:
  ./deploy.sh --with-futu
  ./deploy.sh --with-futu --with-services start
  ./deploy.sh stop
EOF
  exit 1
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

check_prereqs() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed"
    exit 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: docker compose plugin is not installed"
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is not installed"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

setup_env() {
  if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    if [[ -f "${SCRIPT_DIR}/.env.example" ]]; then
      echo "INFO: .env not found; copying from .env.example"
      cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
      echo "WARN: Please edit .env with your credentials before running again"
      exit 1
    else
      echo "ERROR: .env not found and .env.example is missing"
      exit 1
    fi
  fi
}

# ---------------------------------------------------------------------------
# Docker network
# ---------------------------------------------------------------------------

ensure_network() {
  if ! docker network inspect sam-net >/dev/null 2>&1; then
    echo "INFO: Creating Docker network sam-net"
    docker network create sam-net
  fi
}

# ---------------------------------------------------------------------------
# Health gating
# ---------------------------------------------------------------------------

wait_for_healthy() {
  local service="$1"
  local max_attempts="${2:-30}"
  echo "INFO: Waiting for ${service} to become healthy..."
  for ((i = 1; i <= max_attempts; i++)); do
    if docker compose -f "${COMPOSE_FILE}" ps "$service" 2>/dev/null | grep -q "healthy"; then
      echo "INFO: ${service} is healthy"
      return 0
    fi
    sleep 2
  done
  echo "ERROR: ${service} failed to become healthy within $((max_attempts * 2))s"
  exit 1
}

# ---------------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------------

start_stack() {
  cd "${SCRIPT_DIR}"

  local profile_args=()
  if [[ "$WITH_FUTU" == true ]]; then
    profile_args+=("--profile" "futu")
  fi
  if [[ "$WITH_IB" == true ]]; then
    profile_args+=("--profile" "ib")
  fi
  if [[ "$WITH_SERVICES" == true ]]; then
    profile_args+=("--profile" "services")
  fi

  echo "INFO: Starting core infrastructure (postgres, redis)"
  docker compose -f "${COMPOSE_FILE}" up -d sam-postgres sam-redis

  wait_for_healthy sam-postgres
  wait_for_healthy sam-redis

  if [[ "$WITH_FUTU" == true ]]; then
    echo "INFO: Starting Futu OpenD"
    docker compose -f "${COMPOSE_FILE}" "${profile_args[@]}" up -d sam-futu-opend
    wait_for_healthy sam-futu-opend 60
  fi

  if [[ "$WITH_IB" == true ]]; then
    echo "INFO: Starting IB Gateway"
    docker compose -f "${COMPOSE_FILE}" "${profile_args[@]}" up -d sam-ib-gateway
    wait_for_healthy sam-ib-gateway 60
  fi

  echo "INFO: Starting sam-trader"
  docker compose -f "${COMPOSE_FILE}" "${profile_args[@]}" up -d sam-trader
  wait_for_healthy sam-trader 60

  if [[ "$WITH_SERVICES" == true ]]; then
    echo "INFO: Starting sam-services"
    docker compose -f "${COMPOSE_FILE}" "${profile_args[@]}" up -d sam-services
    wait_for_healthy sam-services 60
  fi

  echo "INFO: Stack is up"
}

stop_stack() {
  cd "${SCRIPT_DIR}"
  echo "INFO: Stopping all containers"
  docker compose -f "${COMPOSE_FILE}" --profile futu --profile ib --profile services down
  echo "INFO: Stack stopped"
}

restart_stack() {
  echo "INFO: Restarting stack with graceful state preservation"
  # Signal graceful restart via Redis (sam-trader saves state before restart)
  local redis_cmd=("docker" "exec" "sam-redis" "redis-cli")
  if [[ -n "${REDIS_PASSWORD:-}" ]]; then
    redis_cmd+=("-a" "$REDIS_PASSWORD")
  fi
  redis_cmd+=("PUBLISH" "sam:restart_request" "graceful")
  "${redis_cmd[@]}" >/dev/null 2>&1 || true

  # Restart containers
  cd "${SCRIPT_DIR}"
  local profile_args=()
  if [[ "$WITH_FUTU" == true ]]; then
    profile_args+=("--profile" "futu")
  fi
  if [[ "$WITH_IB" == true ]]; then
    profile_args+=("--profile" "ib")
  fi
  if [[ "$WITH_SERVICES" == true ]]; then
    profile_args+=("--profile" "services")
  fi

  docker compose -f "${COMPOSE_FILE}" "${profile_args[@]}" restart sam-trader
  echo "INFO: sam-trader restarted (state preserved in Redis)"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  # Parse options
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --with-futu)
        WITH_FUTU=true
        shift
        ;;
      --with-ib)
        WITH_IB=true
        shift
        ;;
      --with-services)
        WITH_SERVICES=true
        shift
        ;;
      -h|--help)
        usage
        ;;
      start|stop|restart)
        ACTION="$1"
        shift
        ;;
      *)
        echo "ERROR: Unknown option: $1"
        usage
        ;;
    esac
  done

  check_prereqs
  setup_env
  ensure_network

  case "$ACTION" in
    start)
      start_stack
      ;;
    stop)
      stop_stack
      ;;
    restart)
      restart_stack
      ;;
    *)
      echo "ERROR: Unknown action: $ACTION"
      usage
      ;;
  esac
}

main "$@"
