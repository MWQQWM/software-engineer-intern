"""Review state machine. All legal transitions live in this module."""

PENDING = "pending"
OCR_DONE = "ocr_done"
OCR_FAILED = "ocr_failed"
STAGE1_APPROVED = "stage1_approved"
STAGE1_REJECTED = "stage1_rejected"
STAGE2_APPROVED = "stage2_approved"
STAGE2_REJECTED = "stage2_rejected"
EXPORTED = "exported"

TERMINAL = {OCR_FAILED, STAGE1_REJECTED, STAGE2_REJECTED, EXPORTED}

# (stage, decision, current_status) -> next_status
_TRANSITIONS = {
    (1, "approve", OCR_DONE): STAGE1_APPROVED,
    (1, "reject", OCR_DONE): STAGE1_REJECTED,
    (2, "approve", STAGE1_APPROVED): STAGE2_APPROVED,
    (2, "reject", STAGE1_APPROVED): STAGE2_REJECTED,
}


class InvalidTransition(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def next_status(current: str, stage: int, decision: str) -> str:
    if stage not in (1, 2):
        raise InvalidTransition("stage must be 1 or 2")
    if decision not in ("approve", "reject"):
        raise InvalidTransition("decision must be approve or reject")
    if current in TERMINAL:
        raise InvalidTransition(
            f"case is in terminal status '{current}' and cannot be reviewed"
        )
    key = (stage, decision, current)
    nxt = _TRANSITIONS.get(key)
    if nxt is None:
        raise InvalidTransition(_explain(current, stage, decision))
    return nxt


def _explain(current: str, stage: int, decision: str) -> str:
    if current == PENDING:
        return "case is still pending OCR and cannot be reviewed"
    if current == OCR_FAILED:
        return "ocr_failed cases cannot be reviewed"
    if stage == 2 and current != STAGE1_APPROVED:
        return "stage 2 review requires stage1_approved"
    if stage == 1 and current == STAGE1_APPROVED:
        return "stage 1 decision was already recorded"
    if stage == 1 and current == STAGE2_APPROVED:
        return "stage 1 cannot be repeated after stage 2"
    return f"cannot {decision} at stage {stage} from status '{current}'"
