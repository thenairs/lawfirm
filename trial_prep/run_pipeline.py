#!/usr/bin/env python3
"""Entry point for stages 1-9 of the design spec: upload through report
draft. Stops at the human review gate on purpose -- run approve_and_export.py
after a lawyer has reviewed output/<case_id>/draft_report.md.

Usage:
    python run_pipeline.py <case_documents_folder> [--case-id NAME]
"""
import argparse
import sys
import uuid
from pathlib import Path

from pipeline.aggregate import aggregate_case
from pipeline.audit import log_event
from pipeline.classify import classify_document
from pipeline.evidence import build_evidence_matrix
from pipeline.extract import extract_document
from pipeline.ingest import ingest_case_folder
from pipeline.legal_research import run_legal_research
from pipeline.persistence import case_dir, save_case_file
from pipeline.report import compose_report
from pipeline.timeline import generate_timeline
from pipeline.witness import analyze_witnesses


def render_draft_markdown(case_file: dict) -> str:
    s = case_file["report_draft"]["sections"]
    lines = [
        "# Trial Preparation Report -- DRAFT (pending attorney review)",
        "",
        f"> {case_file['report_draft']['disclaimer']}",
        "",
        f"**Case ID:** {case_file['case_id']}  ",
        f"**Generated:** {case_file['report_draft']['generated_at']}",
        "",
        "## Documents processed",
    ]
    for d in case_file["documents"]:
        lines.append(f"- `{d['doc_id'][:8]}` {d['filename']} -> **{d.get('category', '?')}** "
                      f"(status: {d['status']})")

    lines += ["", "## Case Overview", s["case_overview"]]

    lines += ["", "## Chronological Timeline"]
    for e in case_file["timeline"]:
        lines.append(f"- **{e.get('date')}** [{e.get('status')}] {e.get('event')} "
                      f"(source: {', '.join(e.get('source_doc_ids', []))})")

    lines += ["", "## Evidence Summary"]
    for e in case_file["evidence_matrix"]:
        lines.append(f"- **{e.get('item')}** -- supports: {e.get('supports')}, "
                      f"reliability: {e.get('reliability')} ({e.get('reliability_reason')}), "
                      f"weaknesses: {e.get('weaknesses')}")

    lines += ["", "## Witness Summary"]
    for w in case_file["witness_summary"]:
        lines.append(f"### {w.get('name')} ({w.get('role')})")
        lines.append(w.get("statement_summary", ""))
        for c in w.get("contradictions", []):
            lines.append(f"- contradiction vs {c.get('with')}: {c.get('detail')}")
        for q in w.get("cross_examination_questions", []):
            lines.append(f"- cross-exam Q: {q}")

    lines += ["", "## Case Strengths"] + [f"- {x}" for x in s["case_strengths"]]
    lines += ["", "## Case Weaknesses"] + [f"- {x}" for x in s["case_weaknesses"]]
    lines += ["", "## Missing Documents"] + [f"- {x}" for x in s["missing_documents"]]
    lines += ["", "## Risks"] + [f"- {x}" for x in s["risks"]]

    lines += ["", "## Applicable Laws"]
    lr = case_file["legal_research"]
    for p in lr["applicable_provisions"]:
        lines.append(f"- {p.get('citation')}: {p.get('relevance')}")
    for p in lr["precedents"]:
        lines.append(f"- {p.get('citation')}: {p.get('holding')} -- {p.get('relevance_to_case')}")
    if lr["gaps"]:
        lines.append("\n_Research gaps:_")
        for g in lr["gaps"]:
            lines.append(f"- {g}")

    lines += ["", "## Final Trial Brief", s["final_trial_brief"]]
    return "\n".join(lines)


def run_case(documents_folder: Path, case_id: str) -> dict:
    """Runs stages 1-9 (ingest through report draft) for one case and
    returns the saved case_file. Shared by the CLI entry point below and
    webapp/app.py, which calls this on a background thread per upload.
    """
    print(f"=== Case {case_id} ===")

    print("\n[1-5] Ingest, OCR, extract text")
    documents = ingest_case_folder(documents_folder, case_id)

    print("\n[6] Classify")
    documents = [classify_document(d) for d in documents]

    print("\n[7] Extract structured facts")
    documents = [extract_document(d) for d in documents]

    case_file = aggregate_case(case_id, documents)

    print("\n[9] Timeline")
    case_file["timeline"] = generate_timeline(case_file)

    print("\n[10] Evidence matrix")
    case_file["evidence_matrix"] = build_evidence_matrix(case_file)

    print("\n[11] Witness analysis")
    case_file["witness_summary"] = analyze_witnesses(case_file)

    print("\n[12] Legal research (RAG)")
    case_file["legal_research"] = run_legal_research(case_file)

    print("\n[13] Compose report draft")
    case_file["report_draft"] = compose_report(case_file)

    save_case_file(case_file)
    draft_path = case_dir(case_id) / "draft_report.md"
    draft_path.write_text(render_draft_markdown(case_file))

    log_event(case_id, "pipeline", "draft_ready")
    print(f"\n=== Draft ready: {draft_path} ===")
    return case_file


def main():
    parser = argparse.ArgumentParser(description="Run trial-prep pipeline through report draft.")
    parser.add_argument("documents_folder", type=Path)
    parser.add_argument("--case-id", default=None)
    args = parser.parse_args()

    if not args.documents_folder.is_dir():
        print(f"Not a folder: {args.documents_folder}", file=sys.stderr)
        sys.exit(1)

    case_id = args.case_id or f"case-{uuid.uuid4().hex[:8]}"
    run_case(args.documents_folder, case_id)

    print("Review it, then run:")
    print(f"  python approve_and_export.py {case_id} --reviewer \"Your Name\" --approve")
    print("or to request revisions:")
    print(f"  python approve_and_export.py {case_id} --reviewer \"Your Name\" --revise \"notes here\"")


if __name__ == "__main__":
    main()
