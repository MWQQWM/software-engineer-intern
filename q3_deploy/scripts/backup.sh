#!/usr/bin/env bash
# Back up the running SQLite file and keep the seven newest copies.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
COMPOSE=(docker compose -f "$ROOT/docker-compose.yml" --project-directory "$ROOT")
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/app-$STAMP.db"

mkdir -p "$BACKUP_DIR"

echo "before:"
ls -1 "$BACKUP_DIR"/app-*.db 2>/dev/null || echo "(no backups yet)"

"${COMPOSE[@]}" exec -T api python -c "
import os, shutil
src = os.environ.get('DATABASE_PATH', '/data/app.db')
shutil.copyfile(src, '/tmp/app-backup.db')
print(src)
"
"${COMPOSE[@]}" cp api:/tmp/app-backup.db "$DEST"
echo "wrote $DEST"

keep=0
ls -1t "$BACKUP_DIR"/app-*.db | while IFS= read -r f; do
  keep=$((keep + 1))
  if [ "$keep" -gt 7 ]; then
    rm -f "$f"
  fi
done

echo "after:"
ls -1t "$BACKUP_DIR"/app-*.db
