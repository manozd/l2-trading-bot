"""Item name database for market search."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from market.core.item_id import item_id_from_name
from market.pc_keyboard import SEARCH_ALLOWED, validate_search_text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ITEMS_DB = PROJECT_ROOT / "config" / "items_database.txt"

_NAME_RE = re.compile(r"^[A-Za-z0-9()\- %:]{2,120}$")


@dataclass(frozen=True)
class ItemDbEntry:
    search_name: str
    enchant: int | None = None

    @property
    def item_id(self) -> str:
        return item_id_from_name(self.search_name, enchant=self.enchant)

    @property
    def display_name(self) -> str:
        if self.enchant is not None:
            return f"{self.search_name} +{self.enchant}"
        return self.search_name


def is_valid_item_name(name: str) -> bool:
    name = name.strip()
    if not name or not _NAME_RE.match(name):
        return False
    return all(c in SEARCH_ALLOWED for c in name)


def parse_item_line(line: str) -> ItemDbEntry | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "|" in line:
        name_part, enc_part = line.rsplit("|", 1)
        name_part = name_part.strip()
        enc_part = enc_part.strip().lstrip("+")
        if not enc_part.isdigit():
            return None
        enchant = int(enc_part)
        if enchant < 0 or enchant > 30:
            return None
        if not is_valid_item_name(name_part):
            return None
        return ItemDbEntry(search_name=validate_search_text(name_part), enchant=enchant)
    if not is_valid_item_name(line):
        return None
    return ItemDbEntry(search_name=validate_search_text(line), enchant=None)


def load_item_entries(path: Path = DEFAULT_ITEMS_DB) -> list[ItemDbEntry]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found.\n"
            "Create it (one item per line, optional |enchant) or run:\n"
            "  python tools/extract_item_names.py --system \"I:\\Games\\Lineage 2 bohpts\\system\""
        )
    entries: list[ItemDbEntry] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = parse_item_line(line)
        if entry is None:
            continue
        key = entry.item_id.casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    entries.sort(key=lambda e: e.display_name.casefold())
    return entries


def load_item_names(path: Path = DEFAULT_ITEMS_DB) -> list[str]:
    """Base search names only (backward compatible)."""
    return [e.search_name for e in load_item_entries(path)]


def save_item_names(names: list[str], path: Path = DEFAULT_ITEMS_DB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = []
    seen: set[str] = set()
    for n in names:
        if not is_valid_item_name(n):
            continue
        k = n.casefold()
        if k in seen:
            continue
        seen.add(k)
        clean.append(n)
    clean.sort(key=str.casefold)
    body = "\n".join(clean) + ("\n" if clean else "")
    path.write_text(body, encoding="utf-8")
