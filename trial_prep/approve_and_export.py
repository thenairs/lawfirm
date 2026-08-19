#!/usr/bin/env python3
"""Entry point for the human review gate (design-spec §11) through export
and notification (stages 10-11). This is the only path that can produce a
PDF/DOCX -- there is no way to export a report that hasn't been explicitly
approved here.

Usage:
    python approve_and_export.py <case_id> --reviewer "Jane Doe" --approve
    python approve_and_export.py <case_id> --reviewer "Jane Doe" --revise "notes"
"""
import argparse
from datetime import datetime, timezone

from pipeline.audit import log_event
from pipeline.export import export_docx, export_pdf
from pipeline.notify import send
from pipeline.persistence import case_dir, load_case_file, save_case_file
from pipeline.report import compose_report
from run_pipeline import render_draft_markdown


def revise_case(case_id: str, reviewer: str, notes: str) -> dict:
    """Sends a draft back for regeneration with reviewer notes attached.
    Shared by the CLI and webapp/app.py (run on a background thread there,
    since compose_report is a slow call)."""
    case_file = load_case_file(case_id)
    now = datetime.now(timezone.utc).isoformat()

    case_file["review"] = {
        "status": "revision_requested", "reviewer": reviewer,
        "reviewed_at": now, "notes": notes,
    }
    log_event(case_id, "review_gate", "revision_requested", reviewer=reviewer, notes=notes)

    print("Regenerating draft with reviewer notes...")
    case_file["report_draft"] = compose_report({**case_file, "_reviewer_notes": notes})
    save_case_file(case_file)
    draft_path = case_dir(case_id) / "draft_report.md"
    draft_path.write_text(render_draft_markdown(case_file))
    print(f"New draft written to {draft_path}.")
    return case_file


def approve_case(case_id: str, reviewer: str) -> dict:
    """Approves a draft, exports DOCX/PDF, and notifies. Shared by the CLI
    and webapp/app.py."""
    case_file = load_case_file(case_id)
    now = datetime.now(timezone.utc).isoformat()

    case_file["review"] = {
        "status": "approved", "reviewer": reviewer, "reviewed_at": now, "notes": "",
    }
    log_event(case_id, "review_gate", "approved", reviewer=reviewer)

    out_dir = case_dir(case_id)
    docx_path = out_dir / "trial_prep_report.docx"
    pdf_path = out_dir / "trial_prep_report.pdf"

    try:
        export_docx(case_file, docx_path)
        export_pdf(case_file, pdf_path)
        case_file["exports"] = [
            {"format": "docx", "url": str(docx_path), "generated_at": now},
            {"format": "pdf", "url": str(pdf_path), "generated_at": now},
        ]
        save_case_file(case_file)
        send(case_id, f"Trial prep report for case {case_id} is ready: {docx_path.name}, {pdf_path.name}")
    except Exception as exc:  # noqa: BLE001
        log_event(case_id, "export", "error", error=str(exc))
        send(case_id, f"Export FAILED for case {case_id}: {exc}")
        raise

    print(f"\nExported:\n  {docx_path}\n  {pdf_path}")
    return case_file


def main():
    parser = argparse.ArgumentParser(description="Review gate: approve for export, or request revisions.")
    parser.add_argument("case_id")
    parser.add_argument("--reviewer", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--revise", metavar="NOTES")
    args = parser.parse_args()

    if args.revise:
        revise_case(args.case_id, args.reviewer, args.revise)
        print("Re-review and run this script again.")
    else:
        approve_case(args.case_id, args.reviewer)


if __name__ == "__main__":
    main()

# test line
# 
