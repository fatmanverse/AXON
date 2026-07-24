#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" != "--confirm" ] || [ -z "${2:-}" ]; then
  echo "用法: $0 --confirm /path/to/axon-YYYY...dump" >&2
  exit 2
fi
backup="$2"
[ -f "$backup" ] || { echo "备份不存在: $backup" >&2; exit 1; }
DATABASE_URL="${YIMAI_DATABASE_URL:?YIMAI_DATABASE_URL is required}"
DATABASE_URL="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
DATABASE_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"

if [ -f "$backup.sha256" ]; then
  (cd "$(dirname "$backup")" && sha256sum --check "$(basename "$backup").sha256")
fi
echo "即将覆盖目标数据库: $DATABASE_URL" >&2
pg_restore --dbname="$DATABASE_URL" --clean --if-exists --no-owner --exit-on-error "$backup"
