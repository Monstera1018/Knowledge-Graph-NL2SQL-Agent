set -euo pipefail

PROJECT_NAME="TYWS"

CONTAINER_NAME="${CONTAINER_NAME:-${PROJECT_NAME}_NEO4J}"
NEO4J_IMAGE="${NEO4J_IMAGE:-neo4j:ubi10}"
NEO4J_HTTP_PORT="${NEO4J_HTTP_PORT:-17475}"
NEO4J_BOLT_PORT="${NEO4J_BOLT_PORT:-17688}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-}"

if [ -z "${NEO4J_PASSWORD}" ]; then
  echo "Error: set NEO4J_PASSWORD before running this script" >&2
  exit 1
fi

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
    -p "${NEO4J_HTTP_PORT}:7474" \
    -p "${NEO4J_BOLT_PORT}:7687" \
    -e "NEO4J_AUTH=${NEO4J_USER}/${NEO4J_PASSWORD}" \
    "${NEO4J_IMAGE}"

  echo "Created and started container: ${CONTAINER_NAME}"
}

wait_for_neo4j() {
  local retries=30
  local i
  sleep 3

  echo "Waiting for Neo4j to become ready..."
  for ((i = 1; i <= retries; i++)); do
    if docker exec "${CONTAINER_NAME}" cypher-shell \
      -a "bolt://localhost:7687" \
      -u "${NEO4J_USER}" \
      -p "${NEO4J_PASSWORD}" \
      "RETURN 1" >/dev/null 2>&1; then
      echo "Neo4j is ready."
      return 0
    fi
    sleep 3
  done

  echo "error: Neo4j did not become ready in time" >&2
  docker logs --tail 20 "${CONTAINER_NAME}" >&2 || true
  exit 1
}

print_connection_info() {
  cat <<EOF

Dev Neo4j is running.

  Container : ${CONTAINER_NAME}
  Image     : ${NEO4J_IMAGE}
  HTTP port : ${NEO4J_HTTP_PORT}
  Bolt port : ${NEO4J_BOLT_PORT}
  User      : ${NEO4J_USER}
  Password  : ${NEO4J_PASSWORD}

Browser:
  http://127.0.0.1:${NEO4J_HTTP_PORT}

Bolt URL:
  bolt://127.0.0.1:${NEO4J_BOLT_PORT}

Cypher Shell example:
  docker exec -it ${CONTAINER_NAME} cypher-shell -a bolt://localhost:7687 -u ${NEO4J_USER} -p ${NEO4J_PASSWORD}

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

wait_for_neo4j
print_connection_info
