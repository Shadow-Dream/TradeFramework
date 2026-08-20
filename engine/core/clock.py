"""Engine wall-clock boundary.

Runtime and repository code use this single helper when durable evidence needs a
UTC timestamp.  Keeping it independent avoids coupling repositories to the
backtest assembly module.
"""

from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
