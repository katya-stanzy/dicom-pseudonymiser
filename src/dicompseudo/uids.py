"""Deterministic UID regeneration.

The same original UID always maps to the same new UID for a given salt, so studies,
series and references between files stay linked. Without the salt the mapping cannot be
reversed or reproduced. New UIDs use the 2.25 root (UUID-derived, PS3.5 B.2).
"""
from __future__ import annotations

import hashlib


def new_uid(original: str, salt: str) -> str:
    h = hashlib.sha256(f"{salt}|uid|{original.strip()}".encode()).digest()
    return "2.25." + str(int.from_bytes(h[:16], "big"))
