"""Domain models for market scanners."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from market.capture_rois import DEFAULT_MARKET_ROI_PATH, PROJECT_ROOT
from market.constants import DEFAULT_PICO_COM
from market.truncated_storage import DEFAULT_TRUNCATED_ITEMS_PATH
from market.core.confidence import PriceConfidence
from market.core.item_id import item_id_from_name
from market.enchant import validate_search_enchant
from market.items_db import DEFAULT_ITEMS_DB, ItemDbEntry
from market.search_input import INPUT_PICO

_LOGS = PROJECT_ROOT / "logs"

IdentityStatus = Literal["unresolved", "matched", "ambiguous", "rejected"]
ItemNameSource = Literal["db_search_query", "ocr_truncated"]


@dataclass(frozen=True)
class ItemRef:
    item_id: str
    search_name: str
    category: str | None = None
    enchant: int | None = None

    @property
    def display_name(self) -> str:
        if self.enchant is not None:
            return f"{self.search_name} +{self.enchant}"
        return self.search_name

    @staticmethod
    def from_search_name(name: str, *, category: str | None = None) -> ItemRef:
        return ItemRef(item_id=item_id_from_name(name), search_name=name, category=category)

    @staticmethod
    def from_entry(entry: ItemDbEntry, *, category: str | None = None) -> ItemRef:
        return ItemRef(
            item_id=entry.item_id,
            search_name=entry.search_name,
            enchant=entry.enchant,
            category=category,
        )


@dataclass
class SearchResult:
    item_id: str
    search_query: str
    item_name: str
    item_name_source: ItemNameSource
    item_ocr: str | None
    price_adena: int | None
    vendor: str | None
    units: int | None
    item_key: str | None
    found: bool
    price_confidence: PriceConfidence
    row_confidence: int
    scanned_at: str
    category: str = "search"
    raw_row: dict[str, Any] | None = None
    expected_enchant: int | None = None
    ocr_enchant: int | None = None
    enchant_match: bool | None = None
    reject_reason: str | None = None

    @classmethod
    def from_db_row(
        cls,
        item: ItemRef,
        row: dict[str, Any] | None,
        *,
        scanned_at: str,
        category: str,
        row_confidence: int,
        price_confidence: PriceConfidence,
    ) -> SearchResult:
        if not row:
            return cls(
                item_id=item.item_id,
                search_query=item.search_name,
                item_name=item.display_name,
                item_name_source="db_search_query",
                item_ocr=None,
                price_adena=None,
                vendor=None,
                units=None,
                item_key=None,
                found=False,
                price_confidence="none",
                row_confidence=0,
                scanned_at=scanned_at,
                category=category,
                raw_row=None,
                expected_enchant=item.enchant,
                ocr_enchant=None,
                enchant_match=None if item.enchant is None else False,
                reject_reason=None,
            )
        ocr_enchant = row.get("enchant")
        ok, reject_reason = validate_search_enchant(ocr_enchant, item.enchant)
        price_adena = row.get("price_adena") if ok else None
        price_conf = price_confidence if ok else "none"
        return cls(
            item_id=item.item_id,
            search_query=item.search_name,
            item_name=item.display_name,
            item_name_source="db_search_query",
            item_ocr=row.get("item"),
            price_adena=price_adena,
            vendor=row.get("vendor"),
            units=row.get("units"),
            item_key=row.get("item_key"),
            found=True,
            price_confidence=price_conf,
            row_confidence=row_confidence,
            scanned_at=scanned_at,
            category=category,
            raw_row=row,
            expected_enchant=item.enchant,
            ocr_enchant=ocr_enchant,
            enchant_match=ok if item.enchant is not None else None,
            reject_reason=reject_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_summary_dict(self) -> dict[str, Any]:
        """Flat dict for CSV / validate logs (legacy-compatible keys)."""
        vendor = self.vendor
        return {
            "item_id": self.item_id,
            "item_full_name": self.item_name,
            "item_name": self.item_name,
            "search_query": self.search_query,
            "name_source": self.item_name_source,
            "item_name_source": self.item_name_source,
            "item_ocr": self.item_ocr,
            "item": self.item_ocr or self.item_name,
            "item_key": self.item_key,
            "price_adena": self.price_adena,
            "min_price_adena": self.price_adena,
            "vendor": vendor,
            "units": self.units,
            "listing_count": 1 if self.price_adena is not None else 0,
            "vendors": [vendor] if vendor else [],
            "sample_page": self.raw_row.get("page") if self.raw_row else None,
            "found": self.found,
            "price_confidence": self.price_confidence,
            "row_confidence": self.row_confidence,
            "scanned_at": self.scanned_at,
            "category": self.category,
            "expected_enchant": self.expected_enchant,
            "ocr_enchant": self.ocr_enchant,
            "enchant_match": self.enchant_match,
            "reject_reason": self.reject_reason,
        }


@dataclass
class UnresolvedListing:
    """Bulk-scan row — OCR hint only, not trusted identity."""

    listing_id: str
    scan_time: str
    page_number: int | None
    row_number: int | None
    visible_name_ocr: str | None
    icon_hash: str | None
    price_adena: int | None
    vendor: str | None
    units: int | None
    raw_ocr_text: str
    identity_status: IdentityStatus = "unresolved"
    item_name_source: ItemNameSource = "ocr_truncated"

    @classmethod
    def from_row_dict(cls, row: dict[str, Any], *, listing_id: str) -> UnresolvedListing:
        return cls(
            listing_id=listing_id,
            scan_time=str(row.get("scanned_at") or ""),
            page_number=row.get("page"),
            row_number=row.get("row"),
            visible_name_ocr=row.get("item"),
            icon_hash=row.get("item_icon_hash"),
            price_adena=row.get("price_adena"),
            vendor=row.get("vendor"),
            units=row.get("units"),
            raw_ocr_text=row.get("raw_text") or "",
            identity_status="unresolved",
            item_name_source="ocr_truncated",
        )


@dataclass
class SearchRunConfig:
    roi_path: Path = DEFAULT_MARKET_ROI_PATH
    items_db: Path = DEFAULT_ITEMS_DB
    pico_com: str = DEFAULT_PICO_COM
    category: str = "search"
    input_mode: str = INPUT_PICO
    search_settle_s: float = 0.45
    back_settle_s: float = 0.5
    start_delay_s: float = 10.0
    limit: int = 0
    start: int = 0
    name_filter: str = ""
    dry_run: bool = False
    resume: bool = False
    out_jsonl: Path = field(default_factory=lambda: _LOGS / "market_search_prices.jsonl")
    min_json: Path = field(default_factory=lambda: _LOGS / "market_search_min.json")
    min_csv: Path = field(default_factory=lambda: _LOGS / "market_search_min.csv")
    validate_csv: Path = field(default_factory=lambda: _LOGS / "market_search_validate.csv")
    validate_log: Path = field(default_factory=lambda: _LOGS / "market_search_validate.log")


@dataclass
class BulkRunConfig:
    roi_path: Path = DEFAULT_MARKET_ROI_PATH
    pico_com: str = DEFAULT_PICO_COM
    category: str = "all_items"
    pages: int = 200
    page_delay_s: float = 0.45
    vendor_page_delay_s: float = 0.2
    max_vendor_pages: int = 1
    start_delay_s: float = 10.0
    dry_run: bool = False
    save_images: bool = False
    images_dir: Path = field(default_factory=lambda: _LOGS / "market_all_items_pages")
    aggregate: bool = False
    include_truncated: bool = False
    truncated_items_path: Path = DEFAULT_TRUNCATED_ITEMS_PATH
    out_jsonl: Path = field(default_factory=lambda: _LOGS / "market_all_items.jsonl")
    min_json: Path = field(default_factory=lambda: _LOGS / "market_all_items_min.json")
    min_csv: Path = field(default_factory=lambda: _LOGS / "market_all_items_min.csv")
