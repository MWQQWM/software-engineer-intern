# Module 2: OCR integration and export delivery

Add OCR, export, and delivery modules to `q1_api/app/`. Register the new endpoints on the existing `app.main:app` application. Keep the [fixed repository layout](../README.md#2-fixed-structure-and-naming).

Read the [Module 2 requirements](../README.md#4-module-2-ocr-integration-and-export-delivery), [OCR contract](../mock_ocr/README.md), and [account-opening contract](../mock_onboarding/README.md) before implementing the clients.

## Run without Docker

Start each service in a separate terminal from the repository root:

```bash
python3 mock_ocr/server.py
```

```bash
python3 mock_onboarding/server.py
```

```bash
cd q1_api
uvicorn app.main:app --reload
```

Configure the API with environment variables (defaults are the local mock URLs):

| Variable | Default |
|---|---|
| `OCR_URL` | `http://localhost:9100/ocr/parse` |
| `ONBOARDING_URL` | `http://localhost:9200/ingest` |
| `OCR_TIMEOUT` | `5` |
| `OCR_RETRIES` | `3` |
| `ONBOARDING_TIMEOUT` | `8` |
| `ONBOARDING_RETRIES` | `4` |
| `DATABASE_PATH` | `data/app.db` under the repository root |

Set them in the shell for a local run, or in `q3_deploy/.env` for Compose.

Verify one batch through import, background OCR, both review stages, export, and submission to the account-opening service. Record retry and recovery evidence in [REFLECTION.md](../REFLECTION.md).
