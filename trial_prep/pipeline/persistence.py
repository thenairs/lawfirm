import json

from .config import OUTPUT_DIR


def case_dir(case_id: str):
    d = OUTPUT_DIR / case_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_case_file(case_file: dict):
    path = case_dir(case_file["case_id"]) / "case_file.json"
    path.write_text(json.dumps(case_file, indent=2))
    return path


def load_case_file(case_id: str) -> dict:
    path = OUTPUT_DIR / case_id / "case_file.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No case_file.json for case_id={case_id}. Run run_pipeline.py first."
        )
    return json.loads(path.read_text())


def list_case_ids():
    if not OUTPUT_DIR.exists():
        return []
    return sorted(
        (p.name for p in OUTPUT_DIR.iterdir() if p.is_dir()),
        reverse=True,
    )


# --- processing lock, used by the webapp to know a background thread is
# still running a case (initial run or a revision regeneration) without
# needing to interpret which stage of the audit log it's currently on.

def mark_processing(case_id: str, note: str = ""):
    (case_dir(case_id) / ".processing").write_text(note)


def clear_processing(case_id: str):
    lock = case_dir(case_id) / ".processing"
    if lock.exists():
        lock.unlink()


def is_processing(case_id: str) -> bool:
    return (OUTPUT_DIR / case_id / ".processing").exists()
