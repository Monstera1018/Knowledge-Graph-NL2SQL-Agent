set -euo pipefail

PROJECT_NAME="TYWS"

CONTAINER_NAME="${CONTAINER_NAME:-${PROJECT_NAME}_DB}"
MYSQL_IMAGE="${MYSQL_IMAGE:-mysql:8.4.7-oraclelinux9}"
MYSQL_PORT="${MYSQL_PORT:-13307}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-}"
MYSQL_DATABASE="${MYSQL_DATABASE:-${PROJECT_NAME}}"
MYSQL_USER="${MYSQL_USER:-dev}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-}"

if [ -z "${MYSQL_ROOT_PASSWORD}" ] || [ -z "${MYSQL_PASSWORD}" ]; then
  echo "Error: set MYSQL_ROOT_PASSWORD and MYSQL_PASSWORD before running this script" >&2
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
    -p "${MYSQL_PORT}:3306" \
    -e "MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}" \
    -e "MYSQL_DATABASE=${MYSQL_DATABASE}" \
    -e "MYSQL_USER=${MYSQL_USER}" \
    -e "MYSQL_PASSWORD=${MYSQL_PASSWORD}" \
    "${MYSQL_IMAGE}" \
    --character-set-server=utf8mb4 \
    --collation-server=utf8mb4_unicode_ci

  echo "Created and started container: ${CONTAINER_NAME}"
}

wait_for_mysql() {
  local retries=30
  local i
  sleep 3

  echo "Waiting for MySQL to become ready..."
  for ((i = 1; i <= retries; i++)); do
    if docker exec "${CONTAINER_NAME}" mysqladmin ping -h 127.0.0.1 -uroot -p"${MYSQL_ROOT_PASSWORD}" --silent >/dev/null 2>&1; then
      echo "MySQL is ready."
      return 0
    fi
    sleep 3
  done

  echo "error: MySQL did not become ready in time" >&2
  docker logs --tail 20 "${CONTAINER_NAME}" >&2 || true
  exit 1
}

print_connection_info() {
  cat <<EOF

Dev MySQL is running.

  Container      : ${CONTAINER_NAME}
  Image          : ${MYSQL_IMAGE}
  Host port      : ${MYSQL_PORT}
  Database       : ${MYSQL_DATABASE}
  Root Password  : ${MYSQL_ROOT_PASSWORD}
  User           : ${MYSQL_USER}
  Password       : ${MYSQL_PASSWORD}

Connection URL:
  mysql://${MYSQL_USER}:${MYSQL_PASSWORD}@127.0.0.1:${MYSQL_PORT}/${MYSQL_DATABASE}

CLI example:
  mysql -h 127.0.0.1 -P ${MYSQL_PORT} -u ${MYSQL_USER} -p${MYSQL_PASSWORD} ${MYSQL_DATABASE}

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

wait_for_mysql
print_connection_info
