"""SQLite access. Module 1 runs without Docker, so a file database is enough."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_local = threading.local()


def connect(path: str | None = None) -> sqlite3.Connection:
    db_path = path or config.DATABASE_PATH
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def reset_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init_db(conn: sqlite3.Connection | None = None) -> None:
    c = conn or get_conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            accepted INTEGER NOT NULL DEFAULT 0,
            rejected INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS import_rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            row_number INTEGER NOT NULL,
            reason TEXT NOT NULL,
            FOREIGN KEY (batch_id) REFERENCES batches(id)
        );
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            case_ref TEXT NOT NULL,
            applicant_name TEXT NOT NULL,
            id_number TEXT NOT NULL,
            dob TEXT NOT NULL,
            issue_date TEXT NOT NULL,
            image_ref TEXT NOT NULL,
            status TEXT NOT NULL,
            ocr_json TEXT,
            failure_reason TEXT,
            FOREIGN KEY (batch_id) REFERENCES batches(id)
        );
        CREATE TABLE IF NOT EXISTS exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            csv_text TEXT NOT NULL,
            receipt_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (batch_id) REFERENCES batches(id)
        );
        CREATE INDEX IF NOT EXISTS idx_cases_batch ON cases(batch_id);
        CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(batch_id, status);
        """
    )
    c.commit()


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def status_counts(conn: sqlite3.Connection, batch_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM cases WHERE batch_id = ? GROUP BY status",
        (batch_id,),
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def batch_payload(conn: sqlite3.Connection, batch_id: int) -> dict | None:
    batch = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if batch is None:
        return None
    rejections = conn.execute(
        "SELECT row_number, reason FROM import_rejections WHERE batch_id = ? ORDER BY row_number",
        (batch_id,),
    ).fetchall()
    return {
        "id": batch["id"],
        "created_at": batch["created_at"],
        "accepted": batch["accepted"],
        "rejected": batch["rejected"],
        "rejections": [
            {"row": r["row_number"], "reason": r["reason"]} for r in rejections
        ],
        "status_counts": status_counts(conn, batch_id),
    }


def case_payload(row: sqlite3.Row) -> dict:
    ocr = json.loads(row["ocr_json"]) if row["ocr_json"] else None
    return {
        "id": row["id"],
        "batch_id": row["batch_id"],
        "case_ref": row["case_ref"],
        "applicant_name": row["applicant_name"],
        "id_number": row["id_number"],
        "dob": row["dob"],
        "issue_date": row["issue_date"],
        "image_ref": row["image_ref"],
        "status": row["status"],
        "ocr": ocr,
        "failure_reason": row["failure_reason"],
    }
