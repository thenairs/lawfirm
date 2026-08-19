"""Structured audit/error log, one JSONL file per case. This is the trail
referenced throughout the design spec: every node writes
{ts, case_id, doc_id, node, status, ...} so a failure can be reconstructed
after the fact instead of disappearing into a stack trace.
"""
import json
from datetime import datetime, timezone

from .config import LOGS_DIR


def _log_path(case_id: str) -> str:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return str(LOGS_DIR / f"{case_id}.jsonl")


def log_event(case_id: str, node: str, status: str, *, doc_id: str = None, **fields):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "doc_id": doc_id,
        "node": node,
        "status": status,
        **fields,
    }
    with open(_log_path(case_id), "a") as f:
        f.write(json.dumps(entry) + "\n")
    marker = "x" if status in ("error", "needs_review") else "-"
    print(f"  [{marker}] {node}" + (f" ({doc_id})" if doc_id else "") + f" -> {status}")


def read_events(case_id: str):
    path = _log_path(case_id)
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []
