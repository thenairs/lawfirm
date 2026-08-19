"""Stage 10: evidence matrix builder. Prompt matches design-spec section 4.4.
Only items with a traceable source document are included.
"""
import json

from .aggregate import usable_documents
from .audit import log_event
from .config import StageError, call_structured

SYSTEM_PROMPT = """You build an evidence matrix from extracted evidence_mentioned
entries and surrounding case context. For each item, assess which side it
supports, your reliability judgment and why, and its weaknesses. Only
include items with a traceable source document -- never add evidence not
present in the records provided."""

SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_matrix": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "description": {"type": "string"},
                    "supports": {"type": "string", "enum": ["prosecution", "defense", "neutral"]},
                    "reliability": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reliability_reason": {"type": "string"},
                    "weaknesses": {"type": "string"},
                    "source_doc_id": {"type": "string"},
                    "page_ref": {"type": "string"},
                },
            },
        }
    },
    "required": ["evidence_matrix"],
}


def build_evidence_matrix(case_file: dict) -> list:
    case_id = case_file["case_id"]
    docs = usable_documents(case_file)

    evidence_records = [
        {"doc_id": d["doc_id"], "category": d.get("category"), "evidence": d["extraction"]["evidence_mentioned"]}
        for d in docs if d["extraction"]["evidence_mentioned"]
    ]
    charges = [f for d in docs if d.get("category") == "Charge Sheet" for f in d["extraction"]["legal_sections"]]

    if not evidence_records:
        log_event(case_id, "evidence_matrix", "skipped", reason="no evidence extracted")
        return []

    try:
        result = call_structured(
            system=SYSTEM_PROMPT,
            user_content=(
                f"evidence_records:\n{json.dumps(evidence_records, indent=2)}\n\n"
                f"charges:\n{json.dumps(charges, indent=2)}"
            ),
            tool_name="build_evidence_matrix",
            tool_description="Record the evidence matrix.",
            input_schema=SCHEMA,
            max_tokens=8192,
        )
        log_event(case_id, "evidence_matrix", "ok", items=len(result["evidence_matrix"]))
        return result["evidence_matrix"]
    except StageError as exc:
        log_event(case_id, "evidence_matrix", "error", error=str(exc))
        return []
