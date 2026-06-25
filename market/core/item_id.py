"""Stable item_id from database search names."""

from __future__ import annotations

import re


def item_id_from_name(name: str, *, enchant: int | None = None) -> str:
    t = name.lower().strip()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    base = t[:64] or "unknown"
    if enchant is not None:
        return f"{base}__e{enchant}"
    return base
