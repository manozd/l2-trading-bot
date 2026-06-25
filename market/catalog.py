"""Load item targets from flat text or YAML category lists."""

from __future__ import annotations

from pathlib import Path

from market.core.models import ItemRef
from market.core.item_id import item_id_from_name
from market.items_db import DEFAULT_ITEMS_DB, load_item_entries
from market.pc_keyboard import validate_search_text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_LISTS = PROJECT_ROOT / "config" / "target_lists.yaml"


def load_item_refs(
    *,
    items_db: Path = DEFAULT_ITEMS_DB,
    target_lists: Path | None = None,
    category: str | None = None,
) -> list[ItemRef]:
    """Load items from YAML target lists (optional filter) or flat items_database.txt."""
    if target_lists and target_lists.is_file():
        refs = _load_yaml_refs(target_lists)
        if category:
            refs = [r for r in refs if r.category == category]
        return refs

    entries = load_item_entries(items_db)
    return [ItemRef.from_entry(e) for e in entries]


def _load_yaml_refs(path: Path) -> list[ItemRef]:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML required for target_lists.yaml: pip install pyyaml") from exc

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected mapping of category -> item names")

    refs: list[ItemRef] = []
    seen: set[str] = set()
    for cat, names in raw.items():
        if not isinstance(names, list):
            continue
        for name in names:
            if not isinstance(name, str):
                continue
            name = name.strip()
            if not name or name.startswith("#"):
                continue
            validate_search_text(name)
            kid = item_id_from_name(name)
            if kid in seen:
                continue
            seen.add(kid)
            refs.append(ItemRef(item_id=kid, search_name=name, category=str(cat)))
    refs.sort(key=lambda r: (r.category or "", r.search_name.casefold()))
    return refs
