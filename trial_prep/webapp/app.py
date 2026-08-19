#!/usr/bin/env python3
"""Web frontend for the trial-prep pipeline. Thin wrapper around the same
run_case / revise_case / approve_case functions the CLI uses -- this app
adds no pipeline logic of its own, just upload handling, background
threading for the slow AI stages, and rendering case_file.json as HTML.

Run with: python webapp/app.py
"""
import re
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from approve_and_export import approve_case, revise_case
from pipeline.config import ROOT
from pipeline.ingest import SUPPORTED_EXTENSIONS
from pipeline.persistence import case_dir, clear_processing, mark_processing
from pipeline.status import get_status, list_cases
from run_pipeline import run_case

UPLOAD_DIR = ROOT / "uploads"

app = Flask(__name__)
app.secret_key = "trial-prep-local-dev"  # local single-user tool; not served publicly


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", name.strip().lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def _run_in_background(target, case_id, *args):
    def wrapper():
        mark_processing(case_id)
        try:
            target(case_id, *args)
        except Exception as exc:  # noqa: BLE001 -- surfaced via .error, not crashed silently
            (case_dir(case_id) / ".error").write_text(f"{type(exc).__name__}: {exc}")
        finally:
            clear_processing(case_id)

    threading.Thread(target=wrapper, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html", cases=list_cases())


@app.route("/cases", methods=["POST"])
def create_case():
    files = [f for f in request.files.getlist("documents") if f and f.filename]
    if not files:
        flash("Select at least one document to upload.")
        return redirect(url_for("index"))

    case_name = request.form.get("case_name", "").strip()
    case_id = _slugify(case_name) if case_name else f"case-{uuid.uuid4().hex[:8]}"
    if (Path(app.root_path).parent / "output" / case_id).exists():
        case_id = f"{case_id}-{uuid.uuid4().hex[:4]}"

    dest = UPLOAD_DIR / case_id
    dest.mkdir(parents=True, exist_ok=True)

    skipped = []
    saved = 0
    for f in files:
        filename = secure_filename(f.filename)
        if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            skipped.append(f.filename)
            continue
        f.save(dest / filename)
        saved += 1

    if not saved:
        flash("None of the uploaded files are a supported type "
              "(pdf, docx, txt, png, jpg, jpeg, webp).")
        return redirect(url_for("index"))
    if skipped:
        flash(f"Skipped unsupported files: {', '.join(skipped)}")

    _run_in_background(lambda cid: run_case(dest, cid), case_id)
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/cases/<case_id>")
def case_detail(case_id):
    status = get_status(case_id)
    if status["state"] == "unknown":
        abort(404)
    return render_template("case_detail.html", case_id=case_id, status=status)


@app.route("/cases/<case_id>/approve", methods=["POST"])
def approve(case_id):
    reviewer = request.form.get("reviewer", "").strip()
    if not reviewer:
        flash("Enter a reviewer name before approving.")
        return redirect(url_for("case_detail", case_id=case_id))
    try:
        approve_case(case_id, reviewer)
    except Exception as exc:  # noqa: BLE001
        flash(f"Export failed: {exc}")
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/cases/<case_id>/revise", methods=["POST"])
def revise(case_id):
    reviewer = request.form.get("reviewer", "").strip()
    notes = request.form.get("notes", "").strip()
    if not reviewer or not notes:
        flash("Enter both a reviewer name and revision notes.")
        return redirect(url_for("case_detail", case_id=case_id))

    _run_in_background(revise_case, case_id, reviewer, notes)
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/cases/<case_id>/download/<fmt>")
def download(case_id, fmt):
    if fmt not in ("docx", "pdf"):
        abort(404)
    path = case_dir(case_id) / f"trial_prep_report.{fmt}"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    UPLOAD_DIR.mkdir(exist_ok=True)
    # use_reloader=False: the reloader spawns a second watcher process,
    # which just complicates keeping this running as a single background
    # process for local use.
    app.run(debug=True, port=5050, use_reloader=False)
