"""CSV row validation for staff batch import."""
import csv
import io
import re
from datetime import datetime

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COLUMNS = [
    "case_ref",
    "applicant_name",
    "id_number",
    "dob",
    "issue_date",
    "image_ref",
]


def parse_csv(raw: str) -> tuple[list[dict], list[dict]]:
    """Return (accepted_rows, rejections). Row numbers are 1-based including header."""
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None:
        return [], [{"row": 1, "reason": "CSV has no header"}]
    missing = [c for c in COLUMNS if c not in reader.fieldnames]
    if missing:
        return [], [{"row": 1, "reason": f"missing columns: {', '.join(missing)}"}]

    accepted: list[dict] = []
    rejections: list[dict] = []
    seen_refs: set[str] = set()
    for index, raw_row in enumerate(reader, start=2):
        row = {k: (raw_row.get(k) or "").strip() for k in COLUMNS}
        reason = _row_reason(row, seen_refs)
        if reason:
            rejections.append({"row": index, "reason": reason})
            continue
        seen_refs.add(row["case_ref"])
        accepted.append(row)
    return accepted, rejections


def _row_reason(row: dict, seen_refs: set[str]) -> str | None:
    problems: list[str] = []
    if not row["case_ref"]:
        problems.append("case_ref is empty")
    elif row["case_ref"] in seen_refs:
        problems.append(f"duplicate case_ref '{row['case_ref']}'")
    if not row["applicant_name"]:
        problems.append("applicant_name is empty")
    if not row["id_number"]:
        problems.append("id_number is empty")
    if not _valid_date(row["dob"]):
        problems.append(f"dob '{row['dob']}' is not a valid YYYY-MM-DD date")
    if not _valid_date(row["issue_date"]):
        problems.append(f"issue_date '{row['issue_date']}' is not a valid YYYY-MM-DD date")
    if not row["image_ref"]:
        problems.append("image_ref is empty")
    if not problems:
        return None
    return "; ".join(problems)


def _valid_date(value: str) -> bool:
    if not DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True
