"""Stage 6: classification. Prompt matches design-spec section 4.1."""
from .audit import log_event
from .config import CLASSIFICATION_CONFIDENCE_THRESHOLD, DOCUMENT_CATEGORIES, StageError, call_structured

SYSTEM_PROMPT = f"""You are a legal document classification specialist supporting
Indian criminal trial preparation. Classify the document into exactly one
category: {", ".join(DOCUMENT_CATEGORIES)}.
Base the decision only on document content, never on filename.
If no category fits with confidence >= 0.6, return "Other"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": DOCUMENT_CATEGORIES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["category", "confidence", "reasoning"],
}


def classify_document(record: dict) -> dict:
    case_id, doc_id = record["case_id"], record["doc_id"]

    if record["status"] not in ("processed",) and record["status"] != "ocr_low_confidence":
        record["category"] = "Other"
        record["classification_confidence"] = 0.0
        record["classification_reasoning"] = f"skipped: document status is {record['status']}"
        return record

    text = record["normalized_text"][:6000]
    user_content = f"filename: {record['filename']}\n\nextracted_text:\n\"\"\"\n{text}\n\"\"\""

    try:
        result = call_structured(
            system=SYSTEM_PROMPT,
            user_content=user_content,
            tool_name="classify_document",
            tool_description="Record the document's category, confidence, and reasoning.",
            input_schema=SCHEMA,
        )
    except StageError as exc:
        record["category"] = "Other"
        record["classification_confidence"] = 0.0
        record["classification_reasoning"] = "classification failed"
        record["status"] = "needs_review"
        log_event(case_id, "classify", "error", doc_id=doc_id, error=str(exc))
        return record

    record["category"] = result["category"]
    record["classification_confidence"] = result["confidence"]
    record["classification_reasoning"] = result["reasoning"]

    if result["confidence"] < CLASSIFICATION_CONFIDENCE_THRESHOLD:
        record["category"] = "Other"
        record["status"] = "needs_review"
        log_event(case_id, "classify", "low_confidence", doc_id=doc_id, confidence=result["confidence"])
    else:
        log_event(case_id, "classify", "ok", doc_id=doc_id, category=result["category"], confidence=result["confidence"])

    return record
