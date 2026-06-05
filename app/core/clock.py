from datetime import UTC, datetime


def utc_now_naive() -> datetime:
    """Return the current UTC time as a naive datetime for TIMESTAMP columns."""
    return datetime.now(UTC).replace(tzinfo=None)
