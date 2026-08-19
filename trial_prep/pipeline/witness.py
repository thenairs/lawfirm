"""Stage 11: witness statement analyzer. Prompt matches design-spec section
4.5. Fewer than 2 witness documents -> honest "insufficient data" instead
of a fabricated analysis.
"""
import json

from .aggregate import usable_documents
from .audit import log_event
from .config import StageError, call_structured

SYSTEM_PROMPT = """You summarize witness statements for trial prep. For each
witness: summarize their statement, cross-check it against other witness
statements and case facts for contradictions, and draft cross-examination
questions a lawyer could use to probe weaknesses. Questions must target
specific inconsistencies you found -- no generic filler questions."""

SCHEMA = {
    "type": "object",
    "properties": {
        "witnesses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "statement_summary": {"type": "string"},
                    "contradictions": {
                        "type": "array",
                        "items": {"type": "object", "properties": {
                            "with": {"type": "string"}, "detail": {"type": "string"}}},
                    },
                    "cross_examination_questions": {"type": "array", "items": {"type": "string"}},
                    "source_doc_id": {"type": "string"},
                },
            },
        }
    },
    "required": ["witnesses"],
}


def analyze_witnesses(case_file: dict) -> list:
    case_id = case_file["case_id"]
    witness_docs = usable_documents(case_file, category="Witness Statement")

    if len(witness_docs) < 1:
        log_event(case_id, "witness_summary", "skipped", reason="no witness statements")
        return []
    if len(witness_docs) < 2:
        log_event(case_id, "witness_summary", "insufficient_data", count=len(witness_docs))

    statements = [
        {"doc_id": d["doc_id"], "filename": d["filename"], "extraction": d["extraction"]}
        for d in witness_docs
    ]
    other_facts = {
        "timeline": case_file.get("timeline", []),
        "evidence_matrix": case_file.get("evidence_matrix", []),
    }

    try:
        result = call_structured(
            system=SYSTEM_PROMPT,
            user_content=(
                f"witness_statements:\n{json.dumps(statements, indent=2)}\n\n"
                f"other_case_facts:\n{json.dumps(other_facts, indent=2)}"
            ),
            tool_name="analyze_witnesses",
            tool_description="Record the per-witness analysis.",
            input_schema=SCHEMA,
            max_tokens=4096,
        )
        log_event(case_id, "witness_summary", "ok", witnesses=len(result["witnesses"]))
        return result["witnesses"]
    except StageError as exc:
        log_event(case_id, "witness_summary", "error", error=str(exc))
        return []
