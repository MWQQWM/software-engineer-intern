"""Module 2 tests: OCR retries, unreadable scans, export delivery, recovery."""
import json
from unittest.mock import MagicMock, patch

import httpx

from app import config, db, worker
from app import state as st


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _upload(client, sample_csv):
    response = client.post(
        "/api/batches",
        files={"file": ("account_applications.csv", sample_csv, "text/csv")},
    )
    assert response.status_code == 201
    return response.json()


def _ocr_fields(image_ref: str) -> dict:
    return {
        "id_number": "TEST-ID-100001",
        "full_name": "Parsed Name",
        "dob": "1990-01-01",
        "issue_date": "2020-01-01",
        "confidence": 0.91,
        "image_ref": image_ref,
    }


def test_process_returns_immediately_and_ocr_updates_status(client, sample_csv, monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(config, "OCR_RETRY_LIMIT", 2)
    batch = _upload(client, sample_csv)

    def post(_url, json=None):
        image_ref = json["image_ref"]
        if "badscan" in image_ref:
            return FakeResponse(422, {"error": f"cannot read identity-card fields from '{image_ref}' (scan quality)"})
        return FakeResponse(200, _ocr_fields(image_ref))

    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.side_effect = post
    fake_client.__exit__.return_value = False

    monkeypatch.setattr(worker, "start_ocr_batch", lambda _batch_id: None)
    with patch("app.worker.httpx.Client", return_value=fake_client):
        started = client.post(f"/api/batches/{batch['id']}/process")
        assert started.status_code == 200
        assert started.json()["started"] is True
        worker.process_ocr_batch(batch["id"])

    counts = client.get(f"/api/batches/{batch['id']}").json()["status_counts"]
    assert counts.get("ocr_done") == 38
    assert counts.get("ocr_failed") == 5
    failed = client.get(
        f"/api/batches/{batch['id']}/cases",
        params={"status": "ocr_failed", "page_size": 20},
    ).json()["items"]
    assert all("cannot read" in (item["failure_reason"] or "") for item in failed)
    assert all("badscan" in item["image_ref"] for item in failed)


def test_ocr_retries_500_then_succeeds(client, sample_csv, monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(config, "OCR_RETRY_LIMIT", 3)
    batch = _upload(client, sample_csv)
    case = client.get(
        f"/api/batches/{batch['id']}/cases", params={"page_size": 1}
    ).json()["items"][0]
    attempts = {"n": 0}

    def post(_url, json=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return FakeResponse(500, {"error": "transient"})
        return FakeResponse(200, _ocr_fields(json["image_ref"]))

    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.side_effect = post
    fake_client.__exit__.return_value = False

    conn = db.get_conn()
    row = dict(conn.execute("SELECT * FROM cases WHERE id = ?", (case["id"],)).fetchone())
    with patch("app.worker.httpx.Client", return_value=fake_client):
        worker._ocr_one(conn, row)

    updated = client.get(f"/api/cases/{case['id']}").json()
    assert updated["status"] == st.OCR_DONE
    assert attempts["n"] == 2


def test_ocr_timeout_then_failed(client, sample_csv, monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(config, "OCR_RETRY_LIMIT", 2)
    batch = _upload(client, sample_csv)
    case = client.get(
        f"/api/batches/{batch['id']}/cases", params={"page_size": 1}
    ).json()["items"][0]

    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.side_effect = httpx.TimeoutException("hang")
    fake_client.__exit__.return_value = False

    conn = db.get_conn()
    row = dict(conn.execute("SELECT * FROM cases WHERE id = ?", (case["id"],)).fetchone())
    with patch("app.worker.httpx.Client", return_value=fake_client):
        worker._ocr_one(conn, row)

    updated = client.get(f"/api/cases/{case['id']}").json()
    assert updated["status"] == st.OCR_FAILED
    assert updated["failure_reason"] == "ocr timeout"


def test_process_again_recovers_pending_only(client, sample_csv, monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKOFF_SECONDS", 0)
    batch = _upload(client, sample_csv)
    conn = db.get_conn()
    first, second = conn.execute(
        "SELECT * FROM cases WHERE batch_id = ? ORDER BY id LIMIT 2", (batch["id"],)
    ).fetchall()
    conn.execute(
        "UPDATE cases SET status = ?, ocr_json = ? WHERE id = ?",
        (st.OCR_DONE, json.dumps(_ocr_fields(first["image_ref"])), first["id"]),
    )
    conn.commit()

    def post(_url, json=None):
        return FakeResponse(200, _ocr_fields(json["image_ref"]))

    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.side_effect = post
    fake_client.__exit__.return_value = False
    with patch("app.worker.httpx.Client", return_value=fake_client):
        worker.process_ocr_batch(batch["id"])

    unchanged = client.get(f"/api/cases/{first['id']}").json()
    recovered = client.get(f"/api/cases/{second['id']}").json()
    assert unchanged["status"] == st.OCR_DONE
    assert recovered["status"] == st.OCR_DONE
    remaining_pending = client.get(
        f"/api/batches/{batch['id']}/cases", params={"status": "pending"}
    ).json()["total"]
    assert remaining_pending == 0


def test_export_delivers_once_and_marks_exported(client, sample_csv, monkeypatch):
    monkeypatch.setattr(config, "OCR_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(config, "ONBOARDING_RETRY_LIMIT", 3)
    batch = _upload(client, sample_csv)
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM cases WHERE batch_id = ? ORDER BY id LIMIT 1", (batch["id"],)
    ).fetchone()
    conn.execute(
        "UPDATE cases SET status = ?, ocr_json = ? WHERE id = ?",
        (st.STAGE2_APPROVED, json.dumps(_ocr_fields(row["image_ref"])), row["id"]),
    )
    conn.commit()

    calls = {"n": 0}

    def post(_url, content=None, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TimeoutException("lost after ingest")
        return FakeResponse(
            200,
            {
                "delivery_id": "DLV-0001",
                "idempotency_key": headers["Idempotency-Key"],
                "file_name": headers["X-File-Name"],
                "deduplicated": True,
                "attempts": 2,
            },
        )

    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.side_effect = post
    fake_client.__exit__.return_value = False

    with patch("app.worker.httpx.Client", return_value=fake_client):
        exported = client.post(f"/api/batches/{batch['id']}/export")
        assert exported.status_code == 201, exported.text
        again = client.post(f"/api/batches/{batch['id']}/export")
        assert again.status_code == 409

    body = exported.json()
    assert body["receipt"]["delivery_id"] == "DLV-0001"
    assert body["idempotency_key"].startswith(f"batch-{batch['id']}-")
    assert calls["n"] == 2
    assert client.get(f"/api/cases/{row['id']}").json()["status"] == st.EXPORTED
    csv_body = client.get(f"/api/exports/{body['id']}/csv")
    assert csv_body.status_code == 200
    assert row["case_ref"] in csv_body.text
