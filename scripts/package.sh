#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BUILD_WEB=false
OUTPUT=""

usage() {
  cat <<'EOF'
用法: scripts/package.sh [选项]

将项目打包为 tar.gz，用于部署。会自动排除 .git、.venv、node_modules 等开发/缓存目录。

选项:
  --build-web    打包前先执行 web 前端构建 (npm ci && npm run build)
  -o, --output   指定输出文件路径 (默认: dist/zh_YB_ALL-<时间戳>.tar.gz)
  -h, --help     显示此帮助

示例:
  scripts/package.sh
  scripts/package.sh --build-web
  scripts/package.sh -o /tmp/zh_YB_ALL.tar.gz
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-web)
      BUILD_WEB=true
      shift
      ;;
    -o | --output)
      OUTPUT="${2:?缺少 --output 参数值}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$BUILD_WEB" == true ]]; then
  echo ">> 构建前端..."
  (cd "$ROOT/web" && npm ci && npm run build)
fi

WEB_DIST_REL="web/dist/web/browser"
WEB_DIST="$ROOT/$WEB_DIST_REL"
if [[ ! -d "$WEB_DIST" ]]; then
  echo "警告: 未找到前端构建产物 $WEB_DIST_REL" >&2
  echo "      部署后需手动构建，或使用 --build-web" >&2
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUTPUT="${OUTPUT:-$ROOT/dist/TYWS-${TIMESTAMP}.tar.gz}"
mkdir -p "$(dirname "$OUTPUT")"

PROJECT_NAME="$(basename "$ROOT")"
PARENT="$(dirname "$ROOT")"

echo ">> 打包到 $OUTPUT"

# 禁止 macOS tar 打包扩展属性 / AppleDouble（._*）文件
export COPYFILE_DISABLE=1

tar -czf "$OUTPUT" \
  --exclude="$PROJECT_NAME/.git" \
  --exclude="$PROJECT_NAME/.venv" \
  --exclude="$PROJECT_NAME/.idea" \
  --exclude="$PROJECT_NAME/.vscode" \
  --exclude="$PROJECT_NAME/.cursor" \
  --exclude='.DS_Store' \
  --exclude='._*' \
  --exclude='__MACOSX' \
  --exclude='.AppleDouble' \
  --exclude='.LSOverride' \
  --exclude="$PROJECT_NAME/.env" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude="$PROJECT_NAME/.pytest_cache" \
  --exclude="$PROJECT_NAME/.mypy_cache" \
  --exclude='*.egg-info' \
  --exclude="$PROJECT_NAME/web/node_modules" \
  --exclude="$PROJECT_NAME/web/.angular" \
  --exclude="$PROJECT_NAME/resources/cache" \
  --exclude="$PROJECT_NAME/resources/伪造数据" \
  --exclude="$PROJECT_NAME/dist" \
  -C "$PARENT" "$PROJECT_NAME"

if [[ -d "$WEB_DIST" ]]; then
  if ! tar -tzf "$OUTPUT" | grep -q "^$PROJECT_NAME/$WEB_DIST_REL/"; then
    echo "错误: 前端构建产物存在，但未进入打包文件: $WEB_DIST_REL" >&2
    exit 1
  fi
fi

SIZE="$(du -h "$OUTPUT" | awk '{print $1}')"
echo ">> 完成: $OUTPUT ($SIZE)"
