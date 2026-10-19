#!/usr/bin/env bash
# Drive one batch through nginx: upload -> OCR -> both reviews -> export.
set -euo pipefail

BASE="${BASE:-http://localhost:8080}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CSV="$ROOT/data/account_applications.csv"
CURL=(curl)
# Git Bash cannot open Unicode Unix paths with its bundled curl; use the Windows path.
if command -v cygpath >/dev/null 2>&1; then
  CSV="$(cygpath -w "$CSV")"
  if command -v curl.exe >/dev/null 2>&1; then
    CURL=(curl.exe)
  fi
fi

python_cmd() {
  local c
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      case "$(command -v "$c")" in
        *WindowsApps*) continue ;;
      esac
      "$c" "$@"
      return
    fi
  done
  echo "python not found" >&2
  return 1
}

echo "== upload"
UPLOAD="$("${CURL[@]}" -sS -F "file=@${CSV};type=text/csv" "$BASE/api/batches")"
echo "$UPLOAD"
BATCH_ID="$(printf '%s' "$UPLOAD" | python_cmd -c "import json,sys; print(json.load(sys.stdin)['id'])")"

echo "== process batch $BATCH_ID"
"${CURL[@]}" -sS -X POST "$BASE/api/batches/$BATCH_ID/process"
echo

echo "== wait for OCR"
python_cmd - "$BASE" "$BATCH_ID" <<'PY'
import json, sys, time, urllib.request

base, batch_id = sys.argv[1], sys.argv[2]
deadline = time.time() + 180
while time.time() < deadline:
    with urllib.request.urlopen(f"{base}/api/batches/{batch_id}") as resp:
        payload = json.load(resp)
    counts = payload.get("status_counts") or {}
    pending = counts.get("pending", 0)
    print(f"status_counts={counts}", flush=True)
    if pending == 0:
        sys.exit(0)
    time.sleep(2)
print("OCR did not finish within 180s", file=sys.stderr)
sys.exit(1)
PY

echo "== review one ocr_done case through both stages"
CASE_ID="$(python_cmd - "$BASE" "$BATCH_ID" <<'PY'
import json, sys, urllib.request

base, batch_id = sys.argv[1], sys.argv[2]
url = f"{base}/api/batches/{batch_id}/cases?status=ocr_done&page_size=1"
with urllib.request.urlopen(url) as resp:
    items = json.load(resp)["items"]
if not items:
    raise SystemExit("no ocr_done cases to review")
print(items[0]["id"])
PY
)"

"${CURL[@]}" -sS -X POST "$BASE/api/cases/$CASE_ID/review" \
  -H "Content-Type: application/json" \
  -d '{"stage":1,"decision":"approve"}'
echo
"${CURL[@]}" -sS -X POST "$BASE/api/cases/$CASE_ID/review" \
  -H "Content-Type: application/json" \
  -d '{"stage":2,"decision":"approve"}'
echo

echo "== export"
EXPORT="$("${CURL[@]}" -sS -X POST "$BASE/api/batches/$BATCH_ID/export")"
echo "$EXPORT"
printf '%s' "$EXPORT" | python_cmd -c "import json,sys; body=json.load(sys.stdin); assert body.get('receipt'), body"

echo "== final batch"
"${CURL[@]}" -sS "$BASE/api/batches/$BATCH_ID"
echo
echo "smoke test ok"
