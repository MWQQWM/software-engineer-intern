"""Module 1 tests: CSV import, lifecycle, and invalid review transitions."""
from app import db
from app import state as st


def _upload(client, sample_csv):
    response = client.post(
        "/api/batches",
        files={"file": ("account_applications.csv", sample_csv, "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_status(case_id: int, status: str) -> None:
    conn = db.get_conn()
    conn.execute("UPDATE cases SET status = ? WHERE id = ?", (status, case_id))
    conn.commit()


def _first_case(client, batch_id: int, status: str | None = None) -> dict:
    params = {"page_size": 5}
    if status:
        params["status"] = status
    listing = client.get(f"/api/batches/{batch_id}/cases", params=params)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert items
    return items[0]


def test_app_imports():
    from app.main import app

    assert app.title


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/ready").json() == {"status": "ready"}


def test_import_invalid_csv_rows(client, sample_csv):
    payload = _upload(client, sample_csv)
    assert payload["accepted"] == 43
    assert payload["rejected"] == 7
    reasons = {item["row"]: item["reason"] for item in payload["rejections"]}
    assert 6 in reasons and "dob" in reasons[6]
    assert 13 in reasons and "applicant_name" in reasons[13]
    assert 18 in reasons and "duplicate" in reasons[18]
    assert 19 in reasons and "dob" in reasons[19]
    assert 28 in reasons and "dob" in reasons[28]
    assert 41 in reasons and "applicant_name" in reasons[41]
    assert 48 in reasons and "duplicate" in reasons[48]
    batch = client.get(f"/api/batches/{payload['id']}").json()
    assert batch["status_counts"]["pending"] == 43


def test_successful_case_lifecycle(client, sample_csv):
    batch = _upload(client, sample_csv)
    case = _first_case(client, batch["id"], "pending")
    _set_status(case["id"], st.OCR_DONE)

    stage1 = client.post(
        f"/api/cases/{case['id']}/review",
        json={"stage": 1, "decision": "approve"},
    )
    assert stage1.status_code == 200
    assert stage1.json()["status"] == st.STAGE1_APPROVED

    stage2 = client.post(
        f"/api/cases/{case['id']}/review",
        json={"stage": 2, "decision": "approve"},
    )
    assert stage2.status_code == 200
    assert stage2.json()["status"] == st.STAGE2_APPROVED

    fetched = client.get(f"/api/cases/{case['id']}")
    assert fetched.json()["status"] == st.STAGE2_APPROVED


def test_invalid_transitions_return_409(client, sample_csv):
    batch = _upload(client, sample_csv)
    pending = _first_case(client, batch["id"], "pending")

    too_early = client.post(
        f"/api/cases/{pending['id']}/review",
        json={"stage": 1, "decision": "approve"},
    )
    assert too_early.status_code == 409
    assert "pending" in too_early.json()["detail"]

    _set_status(pending["id"], st.OCR_DONE)
    skip_stage1 = client.post(
        f"/api/cases/{pending['id']}/review",
        json={"stage": 2, "decision": "approve"},
    )
    assert skip_stage1.status_code == 409
    assert "stage 2" in skip_stage1.json()["detail"]

    failed = _first_case(client, batch["id"], "pending")
    _set_status(failed["id"], st.OCR_FAILED)
    review_failed = client.post(
        f"/api/cases/{failed['id']}/review",
        json={"stage": 1, "decision": "approve"},
    )
    assert review_failed.status_code == 409
    assert "ocr_failed" in review_failed.json()["detail"]

    approved = client.post(
        f"/api/cases/{pending['id']}/review",
        json={"stage": 1, "decision": "approve"},
    )
    assert approved.status_code == 200
    repeat = client.post(
        f"/api/cases/{pending['id']}/review",
        json={"stage": 1, "decision": "approve"},
    )
    assert repeat.status_code == 409
