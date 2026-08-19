"""Stage 7: structured extraction. Prompt matches design-spec section 4.2.
Ground rule enforced in the prompt: extract only what's written, flag
ambiguity, cite a page reference for everything.
"""
from .audit import log_event
from .config import StageError, call_structured

SYSTEM_PROMPT = """You are a paralegal AI extracting structured facts from one
classified legal document for trial preparation. Extract only what is
stated in the text. Never infer a fact that is not written. If something is
ambiguous, put it in contradictions_flagged rather than resolving it.
Every item you extract must include a page reference from the supplied
page map; if you cannot determine a page, use "unknown"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "key_facts": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "fact": {"type": "string"}, "page_ref": {"type": "string"}}}},
        "dates": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "date": {"type": "string"}, "event": {"type": "string"},
                "page_ref": {"type": "string"}}}},
        "people": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "role": {"type": "string"},
                "page_ref": {"type": "string"}}}},
        "legal_sections": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "act": {"type": "string"}, "section": {"type": "string"},
                "page_ref": {"type": "string"}}}},
        "evidence_mentioned": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "item": {"type": "string"}, "page_ref": {"type": "string"}}}},
        "contradictions_flagged": {"type": "array", "items": {"type": "string"}},
        "action_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["key_facts", "dates", "people", "legal_sections",
                 "evidence_mentioned", "contradictions_flagged", "action_items"],
}

EMPTY_EXTRACTION = {
    "key_facts": [], "dates": [], "people": [], "legal_sections": [],
    "evidence_mentioned": [], "contradictions_flagged": [], "action_items": [],
}


def extract_document(record: dict) -> dict:
    case_id, doc_id = record["case_id"], record["doc_id"]

    if record["status"] == "needs_review" and not record["normalized_text"].strip():
        record["extraction"] = dict(EMPTY_EXTRACTION)
        return record

    user_content = (
        f"doc_id: {doc_id}\ncategory: {record.get('category', 'Other')}\n"
        f"page_anchor_map: {record['page_anchor_map']}\n\n"
        f"text:\n\"\"\"\n{record['normalized_text'][:12000]}\n\"\"\""
    )

    try:
        result = call_structured(
            system=SYSTEM_PROMPT,
            user_content=user_content,
            tool_name="extract_facts",
            tool_description="Record the structured extraction for this document.",
            input_schema=SCHEMA,
            max_tokens=4096,
        )
        record["extraction"] = result
        log_event(
            case_id, "extract", "ok", doc_id=doc_id,
            facts=len(result["key_facts"]), dates=len(result["dates"]),
        )
    except StageError as exc:
        record["extraction"] = dict(EMPTY_EXTRACTION)
        record["status"] = "needs_review"
        log_event(case_id, "extract", "error", doc_id=doc_id, error=str(exc))

    return record
