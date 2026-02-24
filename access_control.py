"""Access control helpers for member/admin roles."""

from __future__ import annotations

import os
import time
from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    MEMBER = "member"
    EXPIRED = "expired"
    BANNED = "banned"
    UNKNOWN = "unknown"


def parse_admin_ids() -> set[int]:
    """Parse admin IDs from ADMIN_IDS/ADMIN_ID env vars."""
    ids: set[int] = set()
    for raw in os.getenv("ADMIN_IDS", "").split(","):
        raw = raw.strip()
        if raw.isdigit():
            ids.add(int(raw))
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if admin_id.isdigit():
        ids.add(int(admin_id))
    return ids


def now_ts() -> int:
    return int(time.time())
