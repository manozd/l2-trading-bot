"""Load M+2 priority item lists from config/target_lists.yaml."""

from __future__ import annotations

from pathlib import Path

from market.core.models import ItemRef
from market.core.item_id import item_id_from_name
from market.pc_keyboard import validate_search_text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_LISTS = PROJECT_ROOT / "config" / "target_lists.yaml"


def load_target_list_refs(
    target_lists: Path,
    *,
    category: str | None = None,
) -> list[ItemRef]:
    """Load priority scan items from YAML. Raises SystemExit if file missing or empty."""
    path = target_lists.resolve()
    if not path.is_file():
        raise SystemExit(
            f"Missing target list: {path}\n"
            "Add priority items to config/target_lists.yaml for M+2 / search mode."
        )
    refs = _load_yaml_refs(path)
    if not refs:
        raise SystemExit(f"No items in {path}")
    if category:
        refs = [r for r in refs if r.category == category]
        if not refs:
            raise SystemExit(f"No items in category {category!r} in {path}")
    return refs


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
            if isinstance(name, dict):
                if len(name) == 1:
                    key, val = next(iter(name.items()))
                    name = f"{key}: {val}" if val is not None else str(key)
                    print(
                        f"[catalog] target_lists: coerced mapping to search name {name!r} "
                        f"(quote lines with ':' in YAML, e.g. \"{name}\")",
                        flush=True,
                    )
                else:
                    print(
                        f"[catalog] target_lists: skip non-string entry in {cat!r}: {name!r}",
                        flush=True,
                    )
                    continue
            if not isinstance(name, str):
                print(
                    f"[catalog] target_lists: skip non-string entry in {cat!r}: {name!r}",
                    flush=True,
                )
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
    return refs
