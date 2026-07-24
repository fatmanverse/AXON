#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/axon}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
DATABASE_URL="${YIMAI_DATABASE_URL:?YIMAI_DATABASE_URL is required}"
DATABASE_URL="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
DATABASE_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/axon-$timestamp.dump"
pg_dump --dbname="$DATABASE_URL" --format=custom --file="$target"
chmod 600 "$target"
sha256sum "$target" > "$target.sha256"
find "$BACKUP_DIR" -type f -name 'axon-*.dump' -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f -name 'axon-*.dump.sha256' -mtime "+$RETENTION_DAYS" -delete
printf '%s\n' "$target"
