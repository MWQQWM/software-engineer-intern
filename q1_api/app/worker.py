"""OCR worker and onboarding export. Timeouts and retries live here, not in the mock."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import httpx

from . import config, db
from . import state as st

log = logging.getLogger(__name__)

_batch_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_OCR_WORKERS = 8


def _batch_lock(batch_id: int) -> threading.Lock:
    with _locks_guard:
        lock = _batch_locks.get(batch_id)
        if lock is None:
            lock = threading.Lock()
            _batch_locks[batch_id] = lock
        return lock


def start_ocr_batch(batch_id: int) -> None:
    thread = threading.Thread(target=process_ocr_batch, args=(batch_id,), daemon=True)
    thread.start()


def process_ocr_batch(batch_id: int) -> None:
    """Idempotent: only pending cases are sent. A crash leaves them pending."""
    lock = _batch_lock(batch_id)
    if not lock.acquire(blocking=False):
        return
    try:
        conn = db.connect()
        try:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM cases WHERE batch_id = ? AND status = ?",
                    (batch_id, st.PENDING),
                ).fetchall()
            ]
        finally:
            conn.close()
        if not rows:
            return
        with ThreadPoolExecutor(max_workers=_OCR_WORKERS) as pool:
            list(pool.map(_ocr_one_isolated, rows))
    finally:
        lock.release()


def _ocr_one_isolated(row: dict) -> None:
    conn = db.connect()
    try:
        _ocr_one(conn, row)
    finally:
        conn.close()


def _ocr_one(conn, row: dict) -> None:
    case_id = row["id"]
    image_ref = row["image_ref"]
    case_ref = row["case_ref"]
    last_error = "ocr unknown failure"
    for attempt in range(1, config.OCR_RETRY_LIMIT + 1):
        try:
            with httpx.Client(timeout=config.OCR_TIMEOUT_SECONDS) as client:
                resp = client.post(
                    config.OCR_URL,
                    json={"case_id": case_ref, "image_ref": image_ref},
                )
        except httpx.TimeoutException:
            last_error = "ocr timeout"
            log.warning("OCR timeout case=%s attempt=%s", case_id, attempt)
            _backoff(attempt)
            continue
        except httpx.HTTPError as exc:
            last_error = f"ocr transport error: {exc}"
            log.warning("OCR transport case=%s attempt=%s err=%s", case_id, attempt, exc)
            _backoff(attempt)
            continue

        if resp.status_code == 422:
            detail = _safe_json(resp)
            reason = str(detail.get("error") or detail.get("detail") or "unreadable scan")
            _set_ocr_failed(conn, case_id, reason)
            return
        if resp.status_code == 400:
            last_error = f"ocr http 400: {resp.text[:200]}"
            _set_ocr_failed(conn, case_id, last_error)
            return
        if resp.status_code >= 500:
            last_error = f"ocr http {resp.status_code}"
            log.warning("OCR 5xx case=%s attempt=%s", case_id, attempt)
            _backoff(attempt)
            continue
        if resp.status_code != 200:
            last_error = f"ocr http {resp.status_code}"
            _backoff(attempt)
            continue
        payload = _safe_json(resp)
        conn.execute(
            "UPDATE cases SET status = ?, ocr_json = ?, failure_reason = NULL WHERE id = ? AND status = ?",
            (st.OCR_DONE, json.dumps(payload), case_id, st.PENDING),
        )
        conn.commit()
        return
    _set_ocr_failed(conn, case_id, last_error)


def _backoff(attempt: int) -> None:
    delay = config.OCR_BACKOFF_SECONDS * (2 ** (attempt - 1))
    if delay > 0:
        time.sleep(delay)


def _set_ocr_failed(conn, case_id: int, reason: str) -> None:
    conn.execute(
        "UPDATE cases SET status = ?, failure_reason = ? WHERE id = ? AND status = ?",
        (st.OCR_FAILED, reason, case_id, st.PENDING),
    )
    conn.commit()


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"raw": data}
    except ValueError:
        return {"raw": resp.text}


def build_export_csv(conn, batch_id: int) -> tuple[str, list[int]]:
    rows = conn.execute(
        "SELECT * FROM cases WHERE batch_id = ? AND status = ? ORDER BY id",
        (batch_id, st.STAGE2_APPROVED),
    ).fetchall()
    if not rows:
        raise ValueError("no stage2_approved cases to export")
    buf = StringIO()
    fieldnames = [
        "case_ref",
        "applicant_name",
        "id_number",
        "dob",
        "issue_date",
        "image_ref",
        "ocr_id_number",
        "ocr_full_name",
        "ocr_dob",
        "ocr_issue_date",
        "ocr_confidence",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    ids: list[int] = []
    for row in rows:
        ocr = json.loads(row["ocr_json"]) if row["ocr_json"] else {}
        writer.writerow(
            {
                "case_ref": row["case_ref"],
                "applicant_name": row["applicant_name"],
                "id_number": row["id_number"],
                "dob": row["dob"],
                "issue_date": row["issue_date"],
                "image_ref": row["image_ref"],
                "ocr_id_number": ocr.get("id_number", ""),
                "ocr_full_name": ocr.get("full_name", ""),
                "ocr_dob": ocr.get("dob", ""),
                "ocr_issue_date": ocr.get("issue_date", ""),
                "ocr_confidence": ocr.get("confidence", ""),
            }
        )
        ids.append(row["id"])
    return buf.getvalue(), ids


def export_identity_key(batch_id: int, csv_text: str) -> str:
    digest = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()[:16]
    return f"batch-{batch_id}-{digest}"


def export_batch(batch_id: int) -> dict:
    conn = db.get_conn()
    batch = conn.execute("SELECT id FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if batch is None:
        raise LookupError("batch not found")
    csv_text, case_ids = build_export_csv(conn, batch_id)
    key = export_identity_key(batch_id, csv_text)
    existing = conn.execute(
        "SELECT * FROM exports WHERE idempotency_key = ?", (key,)
    ).fetchone()
    if existing is not None:
        return _export_payload(existing)

    filename = f"batch-{batch_id}-{key[-16:]}.csv"
    receipt = _deliver(csv_text, filename, key)
    with db.tx() as tx:
        tx.execute(
            "INSERT INTO exports (batch_id, filename, idempotency_key, csv_text, receipt_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (batch_id, filename, key, csv_text, json.dumps(receipt), db.now_iso()),
        )
        placeholders = ",".join("?" * len(case_ids))
        tx.execute(
            f"UPDATE cases SET status = ? WHERE id IN ({placeholders})",
            [st.EXPORTED, *case_ids],
        )
        row = tx.execute("SELECT * FROM exports WHERE idempotency_key = ?", (key,)).fetchone()
    return _export_payload(row)


def _deliver(csv_text: str, filename: str, idempotency_key: str) -> dict:
    last_error = "onboarding unknown failure"
    payload = csv_text.encode("utf-8")
    headers = {
        "Idempotency-Key": idempotency_key,
        "X-File-Name": filename,
        "Content-Type": "text/csv",
    }
    for attempt in range(1, config.ONBOARDING_RETRY_LIMIT + 1):
        try:
            with httpx.Client(timeout=config.ONBOARDING_TIMEOUT_SECONDS) as client:
                resp = client.post(config.ONBOARDING_URL, content=payload, headers=headers)
        except httpx.TimeoutException:
            last_error = "onboarding timeout"
            log.warning("onboarding timeout attempt=%s", attempt)
            _backoff(attempt)
            continue
        except httpx.HTTPError as exc:
            last_error = f"onboarding transport: {exc}"
            _backoff(attempt)
            continue
        if resp.status_code >= 500:
            last_error = f"onboarding http {resp.status_code}"
            _backoff(attempt)
            continue
        if resp.status_code in (200, 201):
            return _safe_json(resp) or {"status": "ok"}
        last_error = f"onboarding http {resp.status_code}: {resp.text[:200]}"
        if resp.status_code == 400:
            break
        _backoff(attempt)
    raise RuntimeError(last_error)


def _export_payload(row) -> dict:
    return {
        "id": row["id"],
        "batch_id": row["batch_id"],
        "filename": row["filename"],
        "idempotency_key": row["idempotency_key"],
        "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
        "created_at": row["created_at"],
    }
