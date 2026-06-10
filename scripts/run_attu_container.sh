set -euo pipefail

PROJECT_NAME="TYWS"

CONTAINER_NAME="${CONTAINER_NAME:-${PROJECT_NAME}_ATTU}"
ATTU_IMAGE="${ATTU_IMAGE:-zilliz/attu:v2.6}"
ATTU_PORT="${ATTU_PORT:-18001}"
MILVUS_PORT="${MILVUS_PORT:-29531}"
MILVUS_URL="${MILVUS_URL:-host.docker.internal:${MILVUS_PORT}}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not in PATH" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Error: docker daemon is not running" >&2
  exit 1
fi

start_existing_container() {
  docker start "${CONTAINER_NAME}" >/dev/null
  echo "Started existing container: ${CONTAINER_NAME}"
}

create_container() {
  docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${ATTU_PORT}:3000" \
    -e "MILVUS_URL=${MILVUS_URL}" \
    "${ATTU_IMAGE}"

  echo "Created and started container: ${CONTAINER_NAME}"
}

wait_for_attu() {
  local retries=30
  local i
  sleep 3

  echo "Waiting for Attu to become ready..."
  for ((i = 1; i <= retries; i++)); do
    if curl -sf "http://127.0.0.1:${ATTU_PORT}/" >/dev/null 2>&1; then
      echo "Attu is ready."
      return 0
    fi
    sleep 3
  done

  echo "error: Attu did not become ready in time" >&2
  docker logs --tail 20 "${CONTAINER_NAME}" >&2 || true
  exit 1
}

print_connection_info() {
  cat <<EOF

Dev Attu is running.

  Container : ${CONTAINER_NAME}
  Image     : ${ATTU_IMAGE}
  Web port  : ${ATTU_PORT}
  Milvus    : ${MILVUS_URL}

Open in browser:
  http://127.0.0.1:${ATTU_PORT}

Make sure Milvus is running first:
  ./scripts/run_milvus_container.sh

Stop container:
  docker stop ${CONTAINER_NAME}

Remove container:
  docker rm -f ${CONTAINER_NAME}
EOF
}

if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  echo "Container already running: ${CONTAINER_NAME}"
elif docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  start_existing_container
else
  create_container
fi

wait_for_attu
print_connection_info
