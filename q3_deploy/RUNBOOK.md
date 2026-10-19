# Operations runbook

Run these commands from `q3_deploy/` unless a step says otherwise.

## Start the system

1. Copy `.env.example` to `.env`. The example values are correct for Compose service DNS (`mock_ocr`, `mock_onboarding`) and SQLite at `/data/app.db`.
2. Run `docker compose up --build`.
3. Check readiness: `curl -sS http://localhost:8080/api/ready`. Expected: `{"status":"ready"}` after the API healthcheck passes. Liveness is `GET /api/health` → `{"status":"ok"}`.
4. In another terminal, run `bash scripts/smoke_test.sh`. Expected: one batch imported, OCR finished (`pending` reaches 0), one case approved at both stages, export receipt stored, script prints `smoke test ok`.

## Read logs

```bash
docker compose logs --tail=100 api mock_ocr mock_onboarding nginx
```

Add `-f` to follow. Trace a case with its `case_ref` in API logs (`OCR timeout case=` / `OCR 5xx`). Trace an export with `Idempotency-Key` (`batch-<id>-<hash>`) in API logs and `docker compose logs mock_onboarding`. SQLite has no separate database container; the file is `/data/app.db` in the `api` volume `api_data`.

## Diagnose OCR failures

1. `docker compose exec mock_ocr python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9100/online').read().decode())"`. Expected: `{"online": true}`. nginx only forwards `/ocr/*` (so `/ocr/parse` stays intact); `/online` is not on that prefix.
2. Inspect `docker compose logs --tail=100 mock_ocr api`. List failed cases: `curl -sS "http://localhost:8080/api/batches/<id>/cases?status=ocr_failed"`. Unreadable scans include `badscan` in `image_ref` and the OCR `error` text in `failure_reason`. Timeouts store `ocr timeout`. Exhausted HTTP 500 retries store `ocr http 500`.
3. If OCR is down, wait until `/ocr/online` succeeds, then `POST /api/batches/<id>/process` again. Pending cases are retried; `ocr_done` / `ocr_failed` are left unchanged. An unreadable scan cannot be recovered without a new image; do not keep retrying the same `badscan` reference expecting a different result. Request history: `curl -sS http://localhost:8080/ocr/debug/requests` is not exposed (debug is on the mock, not nginx). Use `docker compose exec mock_ocr python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9100/debug/requests').read().decode())"`.

`proxy_pass http://mock_ocr:9100` has no URI path, so `/ocr/parse` is forwarded unchanged.

## Restore a backup

1. Pick a file under `q3_deploy/backups/`, for example `backups/app-YYYYMMDD-HHMMSS.db`. Stop writes: `docker compose stop nginx api`.
2. Copy the file into the API container and replace `/data/app.db`:

   ```bash
   docker compose cp backups/app-YYYYMMDD-HHMMSS.db api:/tmp/restore.db
   docker compose start api
   docker compose exec api python -c "import os, shutil; shutil.copyfile('/tmp/restore.db', os.environ['DATABASE_PATH'])"
   ```

3. `docker compose up -d` (or `docker compose start nginx`), then `curl -sS http://localhost:8080/api/ready` and `curl -sS http://localhost:8080/api/batches/<known-id>`. Expected: ready, and the restored batch/case rows present.
