"""Explicit shorthand → canonical name mappings (beat prefix matching)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from market.core.item_id import item_id_from_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALIASES_PATH = PROJECT_ROOT / "config" / "aliases.yaml"


@dataclass(frozen=True)
class NameAlias:
    alias: str
    canonical_name: str
    item_id: str
    source: str = "aliases.yaml"


def load_name_aliases(path: Path = DEFAULT_ALIASES_PATH) -> list[NameAlias]:
    path = path.resolve()
    if not path.is_file():
        return []
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML required for aliases.yaml: pip install pyyaml") from exc

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected mapping with 'aliases' key")

    mapping = raw.get("aliases") or raw
    if not isinstance(mapping, dict):
        raise ValueError(f"{path}: 'aliases' must be a mapping")

    out: list[NameAlias] = []
    seen: set[str] = set()
    for alias_key, canonical in mapping.items():
        alias = str(alias_key).strip()
        name = str(canonical).strip() if canonical is not None else ""
        if not alias or not name:
            continue
        key = alias.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            NameAlias(
                alias=alias,
                canonical_name=name,
                item_id=item_id_from_name(name),
                source=str(path.name),
            )
        )
    return out


def alias_lookup(
    name: str,
    aliases: list[NameAlias] | None = None,
) -> NameAlias | None:
    text = name.strip()
    if not text:
        return None
    items = aliases if aliases is not None else load_name_aliases()
    key = text.casefold()
    for entry in items:
        if entry.alias.casefold() == key:
            return entry
    return None
