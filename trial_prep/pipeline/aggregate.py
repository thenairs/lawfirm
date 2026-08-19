"""Stage 8: case data aggregator. Merges per-document records into the
case_file shape from schemas/case_file.schema.json, and does so without
mutating any upstream document field (append-only enrichment, per §5 of
the design spec) so every later claim can be traced back to a doc_id/page.
"""


def aggregate_case(case_id: str, documents: list) -> dict:
    return {
        "case_id": case_id,
        "documents": documents,
        "timeline": [],
        "evidence_matrix": [],
        "witness_summary": [],
        "legal_research": {"applicable_provisions": [], "precedents": [], "gaps": []},
        "report_draft": None,
        "review": {"status": "not_started", "reviewer": None, "reviewed_at": None, "notes": ""},
        "exports": [],
    }


def usable_documents(case_file: dict, category: str = None) -> list:
    docs = [d for d in case_file["documents"] if d.get("extraction")]
    if category:
        docs = [d for d in docs if d.get("category") == category]
    return docs
