"""Runtime configuration from environment variables."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DATABASE_PATH = os.environ.get(
    "DATABASE_PATH",
    str(REPO_ROOT / "data" / "app.db"),
)
OCR_URL = os.environ.get("OCR_URL", "http://localhost:9100/ocr/parse")
ONBOARDING_URL = os.environ.get("ONBOARDING_URL", "http://localhost:9200/ingest")
# Hang default is 600s; a short client timeout treats hang as unknown and retries.
OCR_TIMEOUT_SECONDS = float(os.environ.get("OCR_TIMEOUT", "5"))
OCR_RETRY_LIMIT = int(os.environ.get("OCR_RETRIES", "3"))
OCR_BACKOFF_SECONDS = float(os.environ.get("OCR_BACKOFF", "0.3"))
ONBOARDING_TIMEOUT_SECONDS = float(os.environ.get("ONBOARDING_TIMEOUT", "8"))
ONBOARDING_RETRY_LIMIT = int(os.environ.get("ONBOARDING_RETRIES", "4"))
