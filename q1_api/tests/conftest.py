import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "account_applications.csv"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    from app import config, db

    config.DATABASE_PATH = str(db_file)
    db.reset_conn()
    db.init_db()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    db.reset_conn()


@pytest.fixture()
def sample_csv() -> bytes:
    return CSV_PATH.read_bytes()
