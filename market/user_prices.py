"""User-facing market prices — read only ``trusted_min_prices_grouped.csv``."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market.craft.match import _MIN_ACCEPT_SCORE, _match_score
from market.craft.trusted_lookup import load_grouped_trusted_rows
from market.trusted_prices import (
    AVAILABILITY_NOT_FOUND,
    DEFAULT_TRUSTED_GROUPED_CSV,
    GroupedTrustedPriceRow,
    SOURCE_BULK,
    SOURCE_M2,
)

_OCR_GARBAGE_MARKERS = (
    "vendor:",
    "min. price",
    "on market",
    "price per",
    "in stock:",
    "adena",
)


@dataclass(frozen=True)
class UserPriceRow:
    """Decision-grade price row for CLI / API output."""

    display_name: str
    variant_group: str | None
    min_price: int | None
    vendor: str | None
    units: int | None
    source: str
    selected_source: str
    confidence: str
    availability: str
    last_seen_at: str
    age_label: str
    is_stale: bool
    warning: str
    identity_status: str
    fungible: bool
    group_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_user_displayable_name(name: str | None) -> bool:
    """Reject obvious OCR glue — never show raw capture text to users."""
    if not name:
        return False
    text = name.strip()
    if len(text) < 2:
        return False
    low = text.casefold()
    if any(marker in low for marker in _OCR_GARBAGE_MARKERS):
        return False
    if low.startswith("vendor"):
        return False
    return True


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def age_hours(ts: str) -> float | None:
    dt = _parse_ts(ts)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0


def format_age_label(ts: str) -> str:
    hours = age_hours(ts)
    if hours is None:
        return "unknown"
    if hours < 1.0:
        mins = max(1, int(hours * 60))
        return f"{mins} min ago"
    if hours < 48.0:
        if hours < 2.0:
            return f"{hours * 60:.0f} min ago"
        return f"{hours:.1f}h ago"
    days = int(hours / 24)
    return f"{days}d ago"


def source_label(row: GroupedTrustedPriceRow) -> str:
    src = row.selected_source or row.source or ""
    if row.availability == AVAILABILITY_NOT_FOUND:
        return "M+2"
    if src in (SOURCE_M2, "m2_search", "search_m2"):
        return "M+2"
    if src == SOURCE_BULK:
        return "bulk"
    return src or "unknown"


def _row_sort_key(row: GroupedTrustedPriceRow) -> tuple:
    src = row.selected_source or row.source or ""
    m2 = 0 if src in (SOURCE_M2, "m2_search", "search_m2") else 1
    stale = 1 if row.is_stale else 0
    not_found = 1 if row.availability == AVAILABILITY_NOT_FOUND else 0
    price = row.min_price if row.min_price is not None else 10**18
    age = age_hours(row.last_seen_at)
    age_key = -(age if age is not None else 9999.0)
    return (not_found, stale, m2, price, age_key)


def collapse_user_price_rows(
    rows: list[GroupedTrustedPriceRow],
    *,
    all_variants: bool = False,
) -> list[GroupedTrustedPriceRow]:
    """
    User list view — fungible rows as-is; gear collapsed to best row per ``variant_group``.
    """
    displayable = [r for r in rows if is_user_displayable_name(r.display_name)]
    if all_variants:
        return sorted(displayable, key=lambda r: (r.display_name or "", _row_sort_key(r)))

    out: list[GroupedTrustedPriceRow] = []
    gear_buckets: dict[str, list[GroupedTrustedPriceRow]] = {}

    for row in displayable:
        if row.fungible:
            out.append(row)
            continue
        key = (row.variant_group or row.group_key).casefold()
        gear_buckets.setdefault(key, []).append(row)

    for bucket in gear_buckets.values():
        out.append(min(bucket, key=_row_sort_key))

    out.sort(key=lambda r: (r.display_name or "").casefold())
    return out


def grouped_row_to_user(row: GroupedTrustedPriceRow) -> UserPriceRow:
    price: int | None
    if row.availability == AVAILABILITY_NOT_FOUND:
        price = None
    else:
        price = row.min_price
    return UserPriceRow(
        display_name=str(row.display_name or row.variant_group or row.group_key),
        variant_group=row.variant_group,
        min_price=price,
        vendor=row.vendor,
        units=row.units,
        source=source_label(row),
        selected_source=row.selected_source or row.source or "",
        confidence=row.confidence or "medium",
        availability=row.availability,
        last_seen_at=row.last_seen_at,
        age_label=format_age_label(row.last_seen_at),
        is_stale=row.is_stale,
        warning=row.warning or "",
        identity_status=row.identity_status or "",
        fungible=row.fungible,
        group_key=row.group_key,
    )


def load_user_prices(
    grouped_csv: Path | None = None,
    *,
    all_variants: bool = False,
    fresh_only: bool = False,
) -> list[UserPriceRow]:
    """Load user-facing prices from grouped trusted CSV only."""
    path = (grouped_csv or DEFAULT_TRUSTED_GROUPED_CSV).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    raw_rows = load_grouped_trusted_rows(grouped_csv=path)
    collapsed = collapse_user_price_rows(raw_rows, all_variants=all_variants)
    user_rows = [grouped_row_to_user(r) for r in collapsed]

    if fresh_only:
        user_rows = [
            r
            for r in user_rows
            if r.availability != AVAILABILITY_NOT_FOUND
            and not r.is_stale
            and r.min_price is not None
        ]
    return user_rows


def filter_user_prices(
    rows: list[UserPriceRow],
    *,
    name_query: str | None = None,
) -> list[UserPriceRow]:
    if not name_query:
        return rows
    query = name_query.strip()
    if not query:
        return rows

    scored: list[tuple[int, UserPriceRow]] = []
    for row in rows:
        target = row.display_name
        score = _match_score(query, target)
        if score >= _MIN_ACCEPT_SCORE:
            scored.append((score, row))
            continue
        if query.casefold() in target.casefold():
            scored.append((70, row))
            continue
        if row.variant_group and query.casefold() in row.variant_group.casefold():
            scored.append((65, row))

    scored.sort(key=lambda t: (-t[0], t[1].display_name.casefold()))
    return [row for _score, row in scored]


def format_user_price_line(row: UserPriceRow) -> str:
    """Single-line summary: name | price | source | age | confidence."""
    if row.availability == AVAILABILITY_NOT_FOUND:
        price_part = "not on market"
    elif row.min_price is not None:
        price_part = f"{row.min_price:,} adena"
    else:
        price_part = "no price"

    parts = [
        row.display_name,
        price_part,
        row.source,
        row.age_label,
        row.confidence,
    ]
    if row.vendor and row.availability != AVAILABILITY_NOT_FOUND:
        parts.insert(2, f"@{row.vendor}")
    if row.is_stale:
        parts.append("stale")
    if row.warning:
        parts.append(f"({row.warning})")
    return "  |  ".join(parts)


def print_user_prices(
    rows: list[UserPriceRow],
    *,
    grouped_csv: Path,
) -> None:
    print(f"[prices] {len(rows)} item(s) from {grouped_csv.resolve()}", flush=True)
    if not rows:
        print(
            "[prices] no rows — run: python -m cli trusted-prices",
            flush=True,
        )
        return
    for row in rows:
        print(format_user_price_line(row), flush=True)


def print_user_prices_json(rows: list[UserPriceRow]) -> None:
    payload = [r.to_dict() for r in rows]
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
