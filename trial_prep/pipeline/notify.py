"""Stage: notify the lawyer when processing/export is complete. Local
demo implementation uses a native macOS notification plus a log line;
swap `send` for a real email/Slack/Teams webhook call in production (see
design-spec API integration points).
"""
import platform
import subprocess

from .audit import log_event


def send(case_id: str, message: str):
    log_event(case_id, "notify", "sent", message=message)
    print(f"\n[NOTIFICATION] {message}")
    if platform.system() == "Darwin":
        safe_message = message.replace("\\", "\\\\").replace('"', "'")
        try:
            subprocess.run(
                ["osascript", "-e", f'display notification "{safe_message}" with title "Trial Prep"'],
                check=False, timeout=5,
            )
        except Exception:  # noqa: BLE001 - notification is best-effort
            pass
