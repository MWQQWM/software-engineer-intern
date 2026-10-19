# AI interaction record

## Interaction 1: Full take-home implementation

| Field | Description |
|---|---|
| Tool or model | Cursor, Grok 4.6 |
| Prompt or record | Complete the Gekko intern GitHub exercise (`Anthony-Gekko/software-engineer-intern`): Module 1 API and tests, Module 2 OCR/export, Module 3 Compose/nginx/backup/runbook, optional CI, and the three submission markdown files. Work in `E:\工作\gekko`. |
| Affected files | `q1_api/app/config.py`, `db.py`, `state.py`, `validation.py`, `worker.py`, `main.py`; `q1_api/tests/*`; `q3_deploy/*`; `.github/workflows/ci.yml`; `REFLECTION.md`; `TOOLS.md` |

### Adopted output

SQLite instead of MongoDB so Module 1 runs without Docker. Review transitions isolated in `state.py`. Background OCR via a daemon thread and a pending-only worker. OCR client timeout 5s / 3 retries; no retry on HTTP 422. Onboarding POST sends CSV bytes with `Idempotency-Key` derived from batch id plus SHA-256 of the CSV. nginx `proxy_pass` without a URI path. GitHub Actions runs pytest and `docker compose build`.

### Changes and rejected output

MongoDB was not used. FastAPI `BackgroundTasks` was considered; a daemon thread was used instead so the process endpoint does not wait on TestClient's background-task drain and so OCR can use a thread pool. Onboarding was first drafted as multipart `files=`; that was replaced with raw `content=` plus `X-File-Name` to match the mock contract.
