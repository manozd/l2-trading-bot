"""Clean OCR noise from config/items_database.txt."""

from __future__ import annotations

import re
from pathlib import Path

from market.items_db import DEFAULT_ITEMS_DB

_FILE_HEADER = (
    "# BOHPTS market item names — one per line.\n"
    "# Cleaned from bulk OCR: grades, enchants, SA suffixes removed.\n"
    "# Lines starting with # are ignored.\n"
)

# High Five weapon SA names (longest first for suffix stripping).
_WEAPON_SA_NAMES: tuple[str, ...] = tuple(
    sorted(
        {
            "Magic Mental Shield",
            "Magic Regeneration",
            "Magic Paralyze",
            "Magic Weakness",
            "Magic Silence",
            "Magic Bless Body",
            "Bodily Blessing",
            "Bless the Body",
            "Critical Bleed",
            "Critical Damage",
            "Critical Drain",
            "Critical Poison",
            "Critical Stun",
            "Critical Slow",
            "Critical Anger",
            "Magic Regeneration",
            "Mental Shield",
            "Magic Poison",
            "Magic Power",
            "Magic Focus",
            "Magic Damage",
            "Magic Shield",
            "Magic Chaos",
            "Magic Might",
            "Magic Hold",
            "MP Regeneration",
            "HP Regeneration",
            "Quick Recovery",
            "Towering Blow",
            "Death Whisper",
            "Back Blow",
            "Cheap Shot",
            "Long Blow",
            "Wide Blow",
            "Wild Blow",
            "Crt. Damage",
            "Crt. Stun",
            "Crt. Anger",
            "Crt. Bleed",
            "Crt. Drain",
            "Crt. Poison",
            "Rsk. Evasion",
            "Rsk. Focus",
            "Rsk. Haste",
            "Rsk.Focus",
            "Rsk. Haste",
            "HP Drain",
            "Mana Up",
            "Acumen",
            "Acume",
            "Conversion",
            "Empower",
            "Evasion",
            "Guidance",
            "Focus",
            "Haste",
            "Health",
            "Anger",
            "Light",
            "Miser",
            "Lightning Haste",
            "Critical",
            "Destruction",
            "Destruct",
        },
        key=len,
        reverse=True,
    )
)

_GRADE_FIX = re.compile(
    r"\(([ABCDS])-(?:g(?:ra(?:de)?)?)?(?:\.\.\.)?(?=\)|$|\s|,|\.)",
    re.IGNORECASE,
)
_GRADE_TRUNC = re.compile(r"\(([ABCDS])-gr(?:a(?:de)?)?(?=$|\s|\.)", re.IGNORECASE)
_DOUBLE_PAREN = re.compile(r"\)+")
_TRAILING_ENCHANT = re.compile(
    r"(?:\s+\+\d+|\s+\d+\+|\s+t\+\d+(?:\s+\+\d+)?|\s+0\+|\s+\d+\+:\s*.*)$",
    re.IGNORECASE,
)
_TRAILING_ELLIPSIS = re.compile(r"\.{2,}\s*.*$")
_GARBAGE = re.compile(
    r"^(?:on\s*market|onmarket|\d+\+\s*\+\d+|v\+\d+\s*\+\d+|t\+\d+|v\+\d+|\d+\+\d*$|"
    r"apans|\d+/\d+|2\+0|7\+0|units)$",
    re.IGNORECASE,
)
_SPLIT_MARKERS = re.compile(
    r"\.\.\.\s*|\s+(?=Recipe:\s)|\s+(?=Sealed\s)|\s+(?=Blessed\s)|"
    r"\s+(?=Scroll:\s)|\s+(?=Spellbook:\s)|\s+(?=Ancient\s)|\s+(?=Amulet:\s)|"
    r"\s+(?=Soulshot\s)|\s+(?=Spiritshot\s)|\s+(?=Fish\sStew\s)|"
    r"\s+(?=Red\sSeal\s)|\s+(?=Blue\sSeal\s)|\s+(?=Green\sSeal\s)|"
    r"\s+(?=Coal\b)|\s+(?=Mark\s of\s)|\s+(?=Freya)|\s+(?=Mold\s)|"
    r"\s+(?=Sobekk's\s)|\s+(?=Gemstone\s)|\s+(?=Fabric\b)",
    re.IGNORECASE,
)

_DYE_STATS = ("STR", "CON", "DEX", "INT", "MEN", "WIT")
_DYE_PAIRS: dict[str, tuple[str, ...]] = {
    "STR": ("Con", "Dex"),
    "CON": ("Str", "Dex"),
    "DEX": ("Con", "Str"),
    "INT": ("Men", "Wit"),
    "MEN": ("Int", "Wit"),
    "WIT": ("Int", "Men"),
}


def _fix_grades(name: str) -> str:
    name = _GRADE_FIX.sub(lambda m: f"({m.group(1).upper()}-grade)", name)
    name = _GRADE_TRUNC.sub(lambda m: f"({m.group(1).upper()}-grade)", name)
    name = re.sub(
        r"\(([ABCDS])-(?:grade|grad|gra|gr|g)$",
        r"(\1-grade)",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"\(Best A-g(?:ra(?:de)?)?$", "(Best A-grade)", name, flags=re.IGNORECASE)
    name = _DOUBLE_PAREN.sub(")", name)
    return name


def _strip_enchants(name: str) -> str:
    prev = None
    cur = name.strip()
    while prev != cur:
        prev = cur
        cur = _TRAILING_ENCHANT.sub("", cur).strip()
        cur = re.sub(r"\s+\d+\+\s*$", "", cur).strip()
    return cur


def _strip_weapon_sa(name: str) -> str:
    cur = name.strip()
    changed = True
    while changed:
        changed = False
        for sa in _WEAPON_SA_NAMES:
            pattern = rf"(?:\s+-\s+|\s+){re.escape(sa)}\s*$"
            new = re.sub(pattern, "", cur, flags=re.IGNORECASE).strip()
            if new != cur:
                cur = new
                changed = True
                break
    return cur


def _clean_one(raw: str) -> str | None:
    name = raw.strip()
    if not name or name.startswith("#"):
        return None
    name = _TRAILING_ELLIPSIS.sub("", name).strip()
    name = _fix_grades(name)
    name = _strip_enchants(name)
    name = _strip_weapon_sa(name)
    name = re.sub(r"\s+Focu?$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"\s+- La$", "", name).strip()
    name = re.sub(r"\s+", " ", name).strip(" -")
    if not name or _GARBAGE.match(name):
        return None
    if re.search(r"onmarket\d", name, re.IGNORECASE):
        return None
    if name.casefold() in {"ancient", "+o", "8+", "scroll: enchant", "blessed scroll: enchant"}:
        return None
    if len(name) < 2:
        return None
    return name


def _split_merged_line(line: str) -> list[str]:
    parts = [p.strip() for p in _SPLIT_MARKERS.split(line) if p.strip()]
    return parts or [line.strip()]


def _standard_dyes() -> list[str]:
    out: list[str] = []
    tags = {"STR": "Str", "CON": "Con", "DEX": "Dex", "INT": "Int", "MEN": "Men", "WIT": "Wit"}
    for stat in _DYE_STATS:
        tag = tags[stat]
        for other in _DYE_PAIRS[stat]:
            for minus in (1, 2, 3):
                out.append(f"Dye of {stat} <{tag}+1 {other}-{minus}>")
            for minus in (1, 2):
                out.append(f"Greater Dye of {stat} <{tag}+1 {other}-{minus}>")
            out.extend(
                [
                    f"Greater Dye of {stat} <{tag}+2 {other}-2>",
                    f"Greater Dye of {stat} <{tag}+2 {other}-3>",
                    f"Greater Dye of {stat} <{tag}+3 {other}-3>",
                    f"Greater Dye of {stat} <{tag}+3 {other}-4>",
                    f"Greater Dye of {stat} <{tag}+4 {other}-4>",
                ]
            )
    return sorted(set(out), key=str.casefold)


def _soul_crystals() -> list[str]:
    colors = ("Red", "Blue", "Green")
    out: list[str] = []
    for color in colors:
        for stage in range(1, 16):
            out.append(f"{color} Soul Crystal - Stage {stage}")
    return out


def _grade_shots() -> list[str]:
    grades = ("D-grade", "C-grade", "B-grade", "A-grade", "S-grade")
    out: list[str] = []
    for g in grades:
        out.append(f"Soulshot ({g})")
        out.append(f"Spiritshot ({g})")
        out.append(f"Blessed Spiritshot ({g})")
        out.append(f"Fishing Shot ({g})")
        out.append(f"Crystal ({g})")
        out.append(f"Gemstone ({g})")
        out.append(f"Earth Egg ({g})")
        out.append(f"Memento Mori ({g})")
        out.append(f"Neolithic Crystal ({g})")
        out.append(f"Nonliving Nucleus ({g})")
        out.append(f"Armor Coupon ({g})")
        out.append(f"Jewelry Coupon ({g})")
        out.append(f"Weapon Coupon ({g})")
        out.append(f"Elixir of CP ({g})")
        out.append(f"Elixir of Mind ({g})")
        out.append(f"Elixir of Life ({g})")
    out.append("Weapon Coupon (Best A-grade)")
    out.append("Angelic Essence (A-grade)")
    return out


def _enchant_scrolls_and_stones() -> list[str]:
    grades = ("D-grade", "C-grade", "B-grade", "A-grade", "S-grade")
    out: list[str] = []
    for g in grades:
        out.append(f"Scroll: Enchant Weapon ({g})")
        out.append(f"Scroll: Enchant Armor ({g})")
        out.append(f"Blessed Scroll: Enchant Weapon ({g})")
        out.append(f"Blessed Scroll: Enchant Armor ({g})")
        out.append(f"Lucky Enchant Stone: Weapon ({g})")
        out.append(f"Lucky Enchant Stone: Armor ({g})")
    return out


def _drop_truncated_prefixes(names: dict[str, str]) -> None:
    """Remove truncated lines when a fuller sibling exists."""
    values = list(names.values())
    drop: set[str] = set()

    for name in values:
        if re.search(r"\s-\sSt(?:ag)?$", name):
            drop.add(name.casefold())
        if name.endswith(" Spirits") and "Spiritshot" not in name:
            drop.add(name.casefold())
        if re.search(r"\([ABCDS]-g(?:r(?:a(?:de)?)?)?$", name, re.IGNORECASE):
            drop.add(name.casefold())
        if name in {
            "Scroll: Enchant Armor (",
            "Scroll: Enchant Weapon",
            "Blessed Scroll: Enchant",
            "Lucky Enchant Stone: Ar",
            "Lucky Enchant Stone: We",
        }:
            drop.add(name.casefold())

    # Drop near-duplicate: "Foo (A-grad" when "Foo (A-grade)" exists.
    by_base: dict[str, list[str]] = {}
    for name in values:
        base = re.sub(r"\s*\([^)]*$", "", name).strip().casefold()
        by_base.setdefault(base, []).append(name)
    for siblings in by_base.values():
        full = [s for s in siblings if s.lower().endswith("-grade)")]
        if not full:
            continue
        for s in siblings:
            if s not in full:
                drop.add(s.casefold())

    for key in drop:
        names.pop(key, None)


def clean_items_database_lines(lines: list[str]) -> list[str]:
    names: dict[str, str] = {}

    def add(raw: str | None) -> None:
        if not raw:
            return
        cleaned = _clean_one(raw)
        if not cleaned:
            return
        key = cleaned.casefold()
        if key not in names:
            names[key] = cleaned

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for part in _split_merged_line(line):
            add(part)

    for dye in _standard_dyes():
        add(dye)
    for crystal in _soul_crystals():
        add(crystal)
    for shot in _grade_shots():
        add(shot)
    for enchant in _enchant_scrolls_and_stones():
        add(enchant)

    _drop_truncated_prefixes(names)

    return sorted(names.values(), key=str.casefold)


def clean_items_database_file(
    path: Path = DEFAULT_ITEMS_DB,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    before = sum(1 for ln in raw_lines if ln.strip() and not ln.strip().startswith("#"))
    cleaned = clean_items_database_lines(raw_lines)
    if not dry_run:
        body = _FILE_HEADER + "\n".join(cleaned) + ("\n" if cleaned else "")
        path.write_text(body, encoding="utf-8")
    return before, len(cleaned)
