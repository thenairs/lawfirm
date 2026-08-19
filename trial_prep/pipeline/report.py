"""Stage: trial prep report composer, per design-spec section 4.7. The
model only drafts the narrative/judgment sections (overview, strengths,
weaknesses, missing documents, risks, final brief) -- it does NOT
re-generate the timeline, evidence matrix, witness summary, or legal
research tables, since those already exist as verified structured data
from earlier stages and re-deriving them in prose risks drift from the
source-cited version.
"""
import json
from datetime import datetime, timezone

from .audit import log_event
from .config import StageError, call_structured

SYSTEM_PROMPT = """You draft the narrative sections of a trial preparation report
for a lawyer, strictly from the structured case data you are given (timeline,
evidence matrix, witness analysis, legal research). Do not introduce facts
that are not present in that data. Every material claim in case_overview,
case_strengths, and case_weaknesses must be traceable to a doc_id/page in
the source data -- reference it inline as (doc_id, page). List any obvious
missing document types for a case like this (e.g. no medical report despite
alleged injury) in missing_documents. This is a DRAFT for attorney review,
not legal advice."""

SCHEMA = {
    "type": "object",
    "properties": {
        "case_overview": {"type": "string"},
        "case_strengths": {"type": "array", "items": {"type": "string"}},
        "case_weaknesses": {"type": "array", "items": {"type": "string"}},
        "missing_documents": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "final_trial_brief": {"type": "string"},
    },
    "required": ["case_overview", "case_strengths", "case_weaknesses",
                 "missing_documents", "risks", "final_trial_brief"],
}

DISCLAIMER = (
    "This is an AI-generated DRAFT for attorney review. It has not been "
    "verified for legal accuracy or completeness and must not be used in "
    "court or relied upon until reviewed and approved by the responsible "
    "attorney."
)


def compose_report(case_file: dict) -> dict:
    case_id = case_file["case_id"]
    doc_summary = [
        {"doc_id": d["doc_id"], "filename": d["filename"], "category": d.get("category"),
         "status": d["status"], "extraction": d.get("extraction")}
        for d in case_file["documents"]
    ]
    context = {
        "documents": doc_summary,
        "timeline": case_file["timeline"],
        "evidence_matrix": case_file["evidence_matrix"],
        "witness_summary": case_file["witness_summary"],
        "legal_research": case_file["legal_research"],
    }

    try:
        result = call_structured(
            system=SYSTEM_PROMPT,
            user_content=f"case_data:\n{json.dumps(context, indent=2)[:20000]}",
            tool_name="compose_report",
            tool_description="Record the drafted narrative sections of the report.",
            input_schema=SCHEMA,
            max_tokens=8192,
        )
    except StageError as exc:
        log_event(case_id, "report_composer", "error", error=str(exc))
        result = {
            "case_overview": "Report generation failed -- see audit log.",
            "case_strengths": [], "case_weaknesses": [],
            "missing_documents": [], "risks": [str(exc)],
            "final_trial_brief": "",
        }

    draft = {
        "sections": result,
        "disclaimer": DISCLAIMER,
        "version": 1,
        "generated_at": case_file.get("_generated_at") or datetime.now(timezone.utc).isoformat(),
    }
    log_event(case_id, "report_composer", "ok")
    return draft
