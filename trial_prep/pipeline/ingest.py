"""Stage 1-5 of the design spec: upload -> file-type routing -> OCR /
native extraction -> normalization. Produces one DocumentRecord dict per
input file, matching schemas/document_record.schema.json in the design doc.

OCR note: rather than a separate Tesseract binary (a system dependency this
box doesn't have), scanned pages are transcribed by sending the rendered
page image directly to the model's vision endpoint and asking it to both
transcribe and self-report a confidence score, instead of a bolted-on OCR
service.
"""
import base64
import mimetypes
import uuid
from pathlib import Path

import fitz  # PyMuPDF
from docx import Document as DocxDocument

from .audit import log_event
from .config import OCR_CONFIDENCE_THRESHOLD, call_structured

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

OCR_SYSTEM_PROMPT = """You are transcribing a scanned legal document page for a court
case file. Transcribe the text exactly as written, preserving line breaks
where they affect meaning (e.g. numbered clauses). Do not summarize, correct
grammar, or fill in illegible words. If a section is illegible or unclear,
mark it inline as [illegible] and list it in illegible_regions. Report your
own confidence in the transcription as a whole."""

OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "transcribed_text": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "illegible_regions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["transcribed_text", "confidence", "illegible_regions"],
}


def _ocr_image_bytes(image_bytes: bytes, media_type: str, case_id: str, doc_id: str, page_no: int):
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    result = call_structured(
        system=OCR_SYSTEM_PROMPT,
        user_content=[
            {"type": "text", "text": f"Transcribe page {page_no}."},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}", "detail": "high"},
            },
        ],
        tool_name="record_transcription",
        tool_description="Record the OCR transcription and confidence for this page.",
        input_schema=OCR_SCHEMA,
        max_tokens=4096,
    )
    return result


def _extract_pdf(path: Path, case_id: str, doc_id: str):
    """Returns (text, confidence_or_None, page_anchor_map). Pages with
    negligible native text are treated as scanned and OCR'd individually;
    per-page OCR confidences are averaged for the document.
    """
    doc = fitz.open(path)
    full_text_parts = []
    anchor_map = []
    ocr_confidences = []
    char_cursor = 0

    for page_index, page in enumerate(doc, start=1):
        native_text = page.get_text().strip()
        if len(native_text) >= 20:
            page_text = native_text
        else:
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            try:
                ocr_result = _ocr_image_bytes(img_bytes, "image/png", case_id, doc_id, page_index)
                page_text = ocr_result["transcribed_text"]
                ocr_confidences.append(ocr_result["confidence"])
                if ocr_result["illegible_regions"]:
                    log_event(
                        case_id, "ocr", "partial_illegible", doc_id=doc_id,
                        page=page_index, regions=ocr_result["illegible_regions"],
                    )
            except Exception as exc:  # noqa: BLE001 - flagged, not fatal
                page_text = ""
                ocr_confidences.append(0.0)
                log_event(case_id, "ocr", "error", doc_id=doc_id, page=page_index, error=str(exc))

        start = char_cursor
        full_text_parts.append(page_text)
        char_cursor += len(page_text) + 2
        anchor_map.append({"page": page_index, "char_start": start, "char_end": char_cursor})

    text = "\n\n".join(full_text_parts)
    confidence = sum(ocr_confidences) / len(ocr_confidences) if ocr_confidences else None
    return text, confidence, anchor_map


def _extract_docx(path: Path):
    doc = DocxDocument(path)
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paras)
    anchor_map = [{"page": 1, "char_start": 0, "char_end": len(text)}]
    return text, None, anchor_map


def _extract_txt(path: Path):
    text = path.read_text(errors="replace")
    anchor_map = [{"page": 1, "char_start": 0, "char_end": len(text)}]
    return text, None, anchor_map


def _extract_image(path: Path, case_id: str, doc_id: str):
    media_type = mimetypes.guess_type(str(path))[0] or "image/png"
    img_bytes = path.read_bytes()
    result = _ocr_image_bytes(img_bytes, media_type, case_id, doc_id, 1)
    text = result["transcribed_text"]
    anchor_map = [{"page": 1, "char_start": 0, "char_end": len(text)}]
    return text, result["confidence"], anchor_map


def ingest_document(path: Path, case_id: str, sequence_index: int) -> dict:
    doc_id = str(uuid.uuid4())
    ext = path.suffix.lower()
    record = {
        "doc_id": doc_id,
        "case_id": case_id,
        "sequence_index": sequence_index,
        "filename": path.name,
        "source_format": ext.lstrip("."),
        "ocr_confidence": None,
        "normalized_text": "",
        "page_anchor_map": [],
        "status": "processed",
    }

    if ext not in SUPPORTED_EXTENSIONS:
        record["status"] = "unsupported_format"
        log_event(case_id, "file_type_router", "unsupported", doc_id=doc_id, filename=path.name)
        return record

    try:
        if ext == ".pdf":
            text, confidence, anchor_map = _extract_pdf(path, case_id, doc_id)
        elif ext == ".docx":
            text, confidence, anchor_map = _extract_docx(path)
        elif ext == ".txt":
            text, confidence, anchor_map = _extract_txt(path)
        elif ext in IMAGE_EXTENSIONS:
            text, confidence, anchor_map = _extract_image(path, case_id, doc_id)
        else:
            raise ValueError(f"Unhandled extension {ext}")
    except Exception as exc:  # noqa: BLE001
        record["status"] = "extraction_failed"
        log_event(case_id, "extraction", "error", doc_id=doc_id, filename=path.name, error=str(exc))
        return record

    record["normalized_text"] = text
    record["page_anchor_map"] = anchor_map
    record["ocr_confidence"] = confidence

    if confidence is not None and confidence < OCR_CONFIDENCE_THRESHOLD:
        record["status"] = "ocr_low_confidence"
        log_event(case_id, "ocr", "needs_review", doc_id=doc_id, confidence=confidence)
    elif not text.strip():
        record["status"] = "empty_extraction"
        log_event(case_id, "extraction", "needs_review", doc_id=doc_id, reason="no text extracted")
    else:
        log_event(case_id, "extraction", "ok", doc_id=doc_id, chars=len(text), confidence=confidence)

    return record


def ingest_case_folder(folder: Path, case_id: str) -> list:
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    records = []
    for i, path in enumerate(files):
        log_event(case_id, "upload", "received", filename=path.name, sequence_index=i)
        records.append(ingest_document(path, case_id, i))
    return records
