"""Merge live craft price scans into the on-disk cache."""

from __future__ import annotations

from market.craft.models import AVAILABILITY_AVAILABLE, MaterialPrice


def merge_price_into_cache(
    cached: MaterialPrice | None,
    fresh: MaterialPrice,
) -> MaterialPrice:
    """
    Apply cache policy by availability status.

    - available: replace with fresh live price
    - not_on_market: clear live price; keep last good price as cached reference only
    - insufficient_qty: store fresh result (partial market)
    - scan_uncertain: keep last good unit price for planning; mark stale
    """
    prev_live = cached.unit_price_adena if cached else None
    prev_cached = cached.cached_unit_price_adena if cached else None
    last_good = prev_live if prev_live is not None else prev_cached

    if fresh.availability == AVAILABILITY_AVAILABLE:
        return MaterialPrice(
            item_id=fresh.item_id,
            search_name=fresh.search_name,
            unit_price_adena=fresh.unit_price_adena,
            vendor=fresh.vendor,
            units_available=fresh.units_available,
            listing_count=fresh.listing_count,
            source=fresh.source,
            scanned_at=fresh.scanned_at,
            availability=AVAILABILITY_AVAILABLE,
            availability_note="",
            cached_unit_price_adena=fresh.unit_price_adena,
        )

    if fresh.availability == "not_on_market":
        note = fresh.availability_note or "no listings on market"
        return MaterialPrice(
            item_id=fresh.item_id,
            search_name=fresh.search_name,
            unit_price_adena=None,
            vendor=None,
            units_available=fresh.units_available,
            listing_count=fresh.listing_count,
            source=fresh.source,
            scanned_at=fresh.scanned_at,
            availability="not_on_market",
            availability_note=note,
            cached_unit_price_adena=last_good,
        )

    if fresh.availability == "insufficient_qty":
        return MaterialPrice(
            item_id=fresh.item_id,
            search_name=fresh.search_name,
            unit_price_adena=fresh.unit_price_adena,
            vendor=fresh.vendor,
            units_available=fresh.units_available,
            listing_count=fresh.listing_count,
            source=fresh.source,
            scanned_at=fresh.scanned_at,
            availability="insufficient_qty",
            availability_note=fresh.availability_note,
            cached_unit_price_adena=fresh.unit_price_adena or last_good,
        )

    # scan_uncertain — use last good price for cost planning when available
    note = fresh.availability_note or "scan did not complete reliably"
    use_price = last_good
    return MaterialPrice(
        item_id=fresh.item_id,
        search_name=fresh.search_name,
        unit_price_adena=use_price,
        vendor=cached.vendor if cached and use_price == prev_live else None,
        units_available=cached.units_available if cached and use_price == prev_live else None,
        listing_count=fresh.listing_count,
        source=fresh.source if use_price is None else (cached.source if cached and use_price == prev_live else "cached"),
        scanned_at=fresh.scanned_at,
        availability="scan_uncertain",
        availability_note=note,
        cached_unit_price_adena=use_price,
    )
