"""Derives a case's UI status from the same files the pipeline already
writes (case_file.json, the .processing lock, the audit log) rather than
maintaining a separate state machine. Used by webapp/app.py.
"""
from .audit import read_events
from .config import OUTPUT_DIR
from .persistence import case_dir, is_processing, load_case_file


def get_status(case_id: str) -> dict:
    if is_processing(case_id):
        events = read_events(case_id)
        return {"state": "processing", "recent_events": events[-8:]}

    error_path = case_dir(case_id) / ".error"
    if error_path.exists():
        return {"state": "error", "message": error_path.read_text(), "recent_events": read_events(case_id)[-8:]}

    try:
        case_file = load_case_file(case_id)
    except FileNotFoundError:
        return {"state": "unknown"}

    if case_file["review"]["status"] == "approved" and case_file.get("exports"):
        return {"state": "approved", "case_file": case_file}
    return {"state": "draft_ready", "case_file": case_file}


def list_cases() -> list:
    if not OUTPUT_DIR.exists():
        return []
    cases = []
    for d in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        status = get_status(d.name)
        summary = {"case_id": d.name, "state": status["state"]}
        if "case_file" in status:
            summary["doc_count"] = len(status["case_file"]["documents"])
            summary["review_status"] = status["case_file"]["review"]["status"]
        cases.append(summary)
    return cases
