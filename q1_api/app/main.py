"""Staff API for the intern take-home."""
from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import db, validation, worker
from . import state as st
from .state import InvalidTransition

app = FastAPI(title="Gekko intern staff API", version="1.0.0")


@app.on_event("startup")
def startup() -> None:
    db.init_db()


@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/ready")
def ready() -> dict:
    try:
        conn = db.get_conn()
        conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unreachable: {exc}") from exc
    return {"status": "ready"}


@app.post("/api/batches", status_code=201)
async def create_batch(file: UploadFile = File(...)) -> dict:
    raw_bytes = await file.read()
    try:
        raw = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8") from exc
    accepted, rejections = validation.parse_csv(raw)
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO batches (created_at, accepted, rejected) VALUES (?, ?, ?)",
            (db.now_iso(), len(accepted), len(rejections)),
        )
        batch_id = cur.lastrowid
        for row in accepted:
            conn.execute(
                "INSERT INTO cases (batch_id, case_ref, applicant_name, id_number, dob, "
                "issue_date, image_ref, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_id,
                    row["case_ref"],
                    row["applicant_name"],
                    row["id_number"],
                    row["dob"],
                    row["issue_date"],
                    row["image_ref"],
                    st.PENDING,
                ),
            )
        for item in rejections:
            conn.execute(
                "INSERT INTO import_rejections (batch_id, row_number, reason) VALUES (?, ?, ?)",
                (batch_id, item["row"], item["reason"]),
            )
        payload = db.batch_payload(conn, batch_id)
    return payload


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: int) -> dict:
    payload = db.batch_payload(db.get_conn(), batch_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return payload


@app.get("/api/batches/{batch_id}/cases")
def list_cases(
    batch_id: int,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    conn = db.get_conn()
    if db.batch_payload(conn, batch_id) is None:
        raise HTTPException(status_code=404, detail="batch not found")
    where = "batch_id = ?"
    args: list = [batch_id]
    if status:
        where += " AND status = ?"
        args.append(status)
    total = conn.execute(f"SELECT COUNT(*) AS n FROM cases WHERE {where}", args).fetchone()["n"]
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT * FROM cases WHERE {where} ORDER BY id LIMIT ? OFFSET ?",
        [*args, page_size, offset],
    ).fetchall()
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [db.case_payload(r) for r in rows],
    }


@app.get("/api/cases/{case_id}")
def get_case(case_id: int) -> dict:
    row = db.get_conn().execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    return db.case_payload(row)


class ReviewBody(BaseModel):
    stage: int = Field(..., ge=1, le=2)
    decision: str


@app.post("/api/cases/{case_id}/review")
def review_case(case_id: int, body: ReviewBody) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        nxt = st.next_status(row["status"], body.stage, body.decision)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    conn.execute("UPDATE cases SET status = ? WHERE id = ?", (nxt, case_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return db.case_payload(updated)


@app.post("/api/batches/{batch_id}/process")
def process_batch(batch_id: int) -> dict:
    payload = db.batch_payload(db.get_conn(), batch_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="batch not found")
    worker.start_ocr_batch(batch_id)
    return {"batch_id": batch_id, "started": True}


@app.post("/api/batches/{batch_id}/export", status_code=201)
def export_batch(batch_id: int) -> dict:
    try:
        return worker.export_batch(batch_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/exports/{export_id}")
def get_export(export_id: int) -> dict:
    row = db.get_conn().execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="export not found")
    return worker._export_payload(row)


@app.get("/api/exports/{export_id}/csv")
def download_export(export_id: int) -> PlainTextResponse:
    row = db.get_conn().execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="export not found")
    return PlainTextResponse(
        row["csv_text"],
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
    )
