set -euo pipefail

PROJECT_NAME="TYWS"

CONTAINER_NAME="${CONTAINER_NAME:-${PROJECT_NAME}_MILVUS}"
MILVUS_IMAGE="${MILVUS_IMAGE:-milvusdb/milvus:v2.6.11}"
MILVUS_PORT="${MILVUS_PORT:-29531}"
MILVUS_METRICS_PORT="${MILVUS_METRICS_PORT:-19092}"

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
    --security-opt seccomp=unconfined \
    -p "${MILVUS_PORT}:19530" \
    -p "${MILVUS_METRICS_PORT}:9091" \
    -e "DEPLOY_MODE=STANDALONE" \
    -e "ETCD_USE_EMBED=true" \
    -e "ETCD_DATA_DIR=/var/lib/milvus/etcd" \
    -e "COMMON_STORAGETYPE=local" \
    "${MILVUS_IMAGE}" \
    milvus run standalone

  echo "Created and started container: ${CONTAINER_NAME}"
}

wait_for_milvus() {
  local retries=40
  local i
  sleep 3

  echo "Waiting for Milvus to become ready..."
  for ((i = 1; i <= retries; i++)); do
    if docker exec "${CONTAINER_NAME}" curl -sf "http://127.0.0.1:9091/healthz" >/dev/null 2>&1; then
      echo "Milvus is ready."
      return 0
    fi
    sleep 3
  done

  echo "error: Milvus did not become ready in time" >&2
  docker logs --tail 20 "${CONTAINER_NAME}" >&2 || true
  exit 1
}

print_connection_info() {
  cat <<EOF

Dev Milvus is running.

  Container     : ${CONTAINER_NAME}
  Image         : ${MILVUS_IMAGE}
  gRPC port     : ${MILVUS_PORT}
  Metrics port  : ${MILVUS_METRICS_PORT}
  Mode          : embedded etcd + local storage

Python example:
  from pymilvus import connections
  connections.connect(host="127.0.0.1", port="${MILVUS_PORT}")

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

wait_for_milvus
print_connection_info
