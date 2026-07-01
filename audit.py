"""
Append-only in-memory audit log.
Every submission and appeal is recorded here before the response is returned.
"""

_log: list[dict] = []


def append_record(record: dict) -> None:
    _log.append(record)


def get_record(content_id: str) -> dict | None:
    for entry in reversed(_log):
        if entry.get("content_id") == content_id:
            return entry
    return None


def update_appeal(content_id: str, appeal: dict) -> bool:
    """Attach an appeal to an existing record and refresh the label's appeal_notice."""
    from labels import generate_label
    for entry in _log:
        if entry.get("content_id") == content_id:
            entry["appeal"]  = appeal
            entry["status"]  = "under_review"
            # Regenerate the label so appeal_notice is populated in the log.
            entry["label"] = generate_label(
                entry["attribution"], entry["confidence"], status="under_review"
            )
            return True
    return False


def get_all(status_filter: str | None = None, limit: int = 50) -> list[dict]:
    entries = list(reversed(_log))
    if status_filter:
        entries = [e for e in entries if e.get("status") == status_filter]
    return entries[:limit]
