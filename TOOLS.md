# Tool usage record

| Tool or source | Use | Files materially influenced |
|---|---|---|
| Cursor (Grok 4.6) | Implemented the FastAPI service, tests, Compose stack, CI workflow, and submission templates from the repository README and mock contracts | `q1_api/app/*`, `q1_api/tests/*`, `q3_deploy/*`, `.github/workflows/ci.yml`, `REFLECTION.md`, `AI_LOG.md` |
| FastAPI / Starlette docs | Multipart upload, HTTPException 409, TestClient | `q1_api/app/main.py`, `q1_api/tests/*` |
| httpx docs | Timeouts, retries, raw `content=` POST for onboarding | `q1_api/app/worker.py` |
| nginx `proxy_pass` documentation | Forward `/ocr/parse` without stripping the path (no URI on `proxy_pass`) | `q3_deploy/nginx.conf` |
| Mock OCR and onboarding README + `server.py` | Request bodies, headers, failure codes, debug endpoints | `q1_api/app/worker.py`, `REFLECTION.md` |

Record AI interactions that influenced design or generated code in [AI_LOG.md](AI_LOG.md).
