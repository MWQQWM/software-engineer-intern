# Implementation reflection

## 1. Design decisions

| Decision | Rationale |
|---|---|
| Database | SQLite. Module 1 must run locally without Docker, and the workload is a single-process staff API with tens of cases per batch. A file database avoids a MongoDB daemon, keeps pytest isolated with a temp file, and Compose still persists `/data/app.db` on volume `api_data`. |
| Background OCR processing | `POST /api/batches/{id}/process` starts a daemon thread and returns immediately. The thread loads `pending` rows and parses them with a pool of 8 workers. Status is written only after a terminal OCR result (`ocr_done` or `ocr_failed`). There is no in-flight status, so a crash leaves cases `pending`. `POST /process` again selects only `pending` rows; `ocr_done` fields are not overwritten. A per-batch lock prevents two workers from racing the same batch. |
| Review state machine | All legal transitions live in `q1_api/app/state.py`. `next_status` is the only function routes call. Invalid moves raise `InvalidTransition`, mapped to HTTP 409. |
| Delivery idempotency key | `batch-{batch_id}-{sha256(csv)[:16]}`. The key is derived from the export identity (batch plus exact CSV bytes). Retries of the same file reuse the key so a lost response after ingest does not create a second delivery. A later export with different `stage2_approved` rows has different bytes and therefore a different key. |

CSV import uses multipart field `file` on `POST /api/batches`.

## 2. Mock observations and retry configuration

| Item | Measurement or decision |
|---|---|
| Observed OCR failure rates | Sequential `POST /ocr/parse` against the mock with seed `42`, `OCR_FAIL_RATE=0.20`, `OCR_HANG_RATE=0.10`, `OCR_HANG_SECONDS=0` (hang still recorded, delay skipped for measurement), 200 readable images: **200 attempts**, `ok=130 (0.65)`, `error_500=38 (0.19)`, `hang=32 (0.16)`. Configured rates are 0.20 / 0.10; the hang share is higher in this sample than 0.10 because the two rolls share one random draw. Unreadable `badscan` images always return HTTP 422 and are not part of that 200. The sample CSV has 5 `badscan` rows among 43 accepted cases (~12% of accepted, 10% of the 50-row file). |
| OCR timeout | **5 seconds**. The mock hang delay is 600s. A 5s client timeout treats a hang as unknown, does not wait for the late 200, and retries. nginx `proxy_read_timeout` is 60s so nginx is not the first timeout for staff `/api` calls. |
| Retry limit | **3 attempts** for OCR, **4** for onboarding, exponential backoff `0.3 * 2^(attempt-1)` seconds. HTTP 422 and HTTP 400 are not retried. HTTP 500 and timeouts are retried. |
| Exhausted retries | Case status becomes `ocr_failed` with `failure_reason` `ocr timeout` or `ocr http 500`. Staff list them with `GET /api/batches/{id}/cases?status=ocr_failed`. Unreadable scans keep the vendor reason (`cannot read identity-card fields from '…' (scan quality)`). Next action: retry `POST /process` for timeouts/500s (still `pending` only if the worker crashed before the write); replace the image for `badscan`. |

## 3. Verification evidence

### OCR recovery

Interrupted run vs clean run (same mock client, deterministic parsed fields for an `image_ref`):

1. Import the sample CSV (43 `pending`).
2. Mark the first case `ocr_done` with stored fields, leaving the rest `pending` (simulates a worker stop after one success).
3. `POST /process` / `process_ocr_batch` again.
4. First case still `ocr_done` with the same JSON; remaining cases leave `pending`.

Covered by `q1_api/tests/test_ocr_export.py::test_process_again_recovers_pending_only`. Parsed fields come from the OCR JSON body and are therefore identical for a repeated `image_ref`. Live `/debug/requests` comparison was not captured on this machine because Docker is not installed; without Docker the same mock was driven in-process for the rate measurement above.

### Account-opening submission

`test_export_delivers_once_and_marks_exported` sends the CSV as raw bytes with `Idempotency-Key` and `X-File-Name`. The first attempt times out (lost receipt after ingest). The retry uses the same key and stores the receipt (`deduplicated: true`, `attempts: 2`). A second `POST /export` with no new `stage2_approved` cases returns 409. Cases move to `exported`.

### Backup retention

`q3_deploy/scripts/backup.sh` copies `/data/app.db` from the running `api` container to `q3_deploy/backups/app-<timestamp>.db` and deletes files beyond the newest seven.

Docker is not installed on the development machine, so the script was not executed against Compose. The same keep-7 rule was exercised on eight dummy files:

```text
before:
app-20261019-000008.db
...
app-20261019-000001.db
after:
app-20261019-000008.db
app-20261019-000007.db
app-20261019-000006.db
app-20261019-000005.db
app-20261019-000004.db
app-20261019-000003.db
app-20261019-000002.db
```

File `000001` was removed. After Docker is available, run `bash scripts/backup.sh` eight times from `q3_deploy/` and attach the real `ls` output.

## 4. AI-generated code requiring further review

None. The implementation was written with an AI coding assistant (Cursor). I can explain the state machine, OCR retry loop, nginx `proxy_pass` (no URI, so `/ocr/parse` is preserved), and idempotency key derivation.

## 5. Incomplete work and next steps

- `python -m pytest q1_api/` passes (11 tests).
- Docker Desktop is not installed here, so `docker compose up --build` and `bash scripts/smoke_test.sh` were not run against a live stack. Compose, Dockerfile, nginx, smoke test, and backup script are in place.
- GitHub Actions workflow is added; it has not run on a public fork yet.
- Optional external partner API was not implemented; CI was chosen instead.

With one additional day: install Docker, run the smoke test, capture `/debug/requests` and `/debug/deliveries` from the live mocks, and attach real backup `ls` output.
