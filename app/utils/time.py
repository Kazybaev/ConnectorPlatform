from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """Return a sortable UTC timestamp string."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()
