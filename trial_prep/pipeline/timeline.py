"""Stage 9: timeline generator. Prompt matches design-spec section 4.3.
Conflicting dates are kept and marked disputed rather than silently resolved.
"""
import json

from .aggregate import usable_documents
from .audit import log_event
from .config import StageError, call_structured

SYSTEM_PROMPT = """You build a single chronological case timeline from structured
extraction records spanning multiple documents. Merge events by date. If two
documents disagree on a date or sequence for the same event, keep both
entries and mark them "disputed" with both source references instead of
picking a winner."""

SCHEMA = {
    "type": "object",
    "properties": {
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "event": {"type": "string"},
                    "source_doc_ids": {"type": "array", "items": {"type": "string"}},
                    "page_refs": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["confirmed", "disputed"]},
                },
            },
        }
    },
    "required": ["timeline"],
}


def generate_timeline(case_file: dict) -> list:
    case_id = case_file["case_id"]
    docs = usable_documents(case_file)
    dated_records = [
        {"doc_id": d["doc_id"], "filename": d["filename"], "category": d.get("category"),
         "dates": d["extraction"]["dates"]}
        for d in docs if d["extraction"]["dates"]
    ]

    if not dated_records:
        log_event(case_id, "timeline", "skipped", reason="no dated events extracted")
        return []

    try:
        result = call_structured(
            system=SYSTEM_PROMPT,
            user_content=f"extraction_records:\n{json.dumps(dated_records, indent=2)}",
            tool_name="build_timeline",
            tool_description="Record the merged chronological timeline.",
            input_schema=SCHEMA,
            max_tokens=8192,
        )
        log_event(case_id, "timeline", "ok", events=len(result["timeline"]))
        return result["timeline"]
    except StageError as exc:
        log_event(case_id, "timeline", "error", error=str(exc))
        return []
