"""One-shot repairs for config/items_database.txt — truncations, merges, bot priorities."""

from __future__ import annotations

from pathlib import Path

from market.items_db import DEFAULT_ITEMS_DB

_HEADER = (
    "# BOHPTS market item names — one per line.\n"
    "# Cleaned from bulk OCR: grades, enchants, SA suffixes removed.\n"
    "# Lines starting with # are ignored.\n"
)

# Exact replacements (truncated / typo → full name).
_REPLACEMENTS: dict[str, str] = {
    "Ancient Lesser Giant Bo": "Ancient Lesser Giant Book",
    "Basalt Battlehammer Hea": "Basalt Battlehammer Head",
    "Behemoth'Tuning Fork P": "Behemoth's Tuning Fork Piece",
    "Boots of Nightmare Heav": "Boots of Nightmare Heavy Armor",
    "Black Ore Necklace Bead": "Black Ore Necklace Beads",
    "Dark Legion's Edqe Blade": "Dark Legion's Edge Blade",
    "Deinonychus Mesozoic St": "Deinonychus Mesozoic Stone",
    "Doom Plate Armor Temper": "Doom Plate Armor Temper Pattern",
    "Flaming Dragon Skull Pi": "Flaming Dragon Skull Piece",
    "Giant's Codex - Discip": "Giant's Codex - Discipline",
    "Great Black Wolf Neckla": "Great Black Wolf Necklace",
    "High-grade Magic Orname": "High-grade Magic Ornament",
    "Imperial Staff - Destin": "Imperial Staff - Destruction",
    "Improved Buffalo Panpip": "Improved Buffalo Panpipe",
    "Improved Kookaburra Oca": "Improved Kookaburra Ocarina",
    "Improved Top-grade Life": "Improved Top-grade Life Stone",
    "Leather Armor of Nightm": "Leather Armor of Nightmare",
    "Low-grade Magic Ornamen": "Low-grade Magic Ornament",
    "Majestic Boots Heavy Ar": "Majestic Boots Heavy Armor",
    "Majestic Circlet Heavy": "Majestic Circlet Heavy Armor",
    "Majestic Gauntlets Heav": "Majestic Gauntlets Heavy Armor",
    "Mammon's Varnish Enhanc": "Mammon's Varnish Enhancer",
    "Mid-grade Magic Ornamen": "Mid-grade Magic Ornament",
    "Onyx Beast's Eye Earrin": "Onyx Beast's Eye Earring",
    "Passive Book: Combat Au": "Passive Book: Combat Aura",
    "Passive Book: Sigil Mas": "Passive Book: Sigil Mastery",
    "Tallum Boots Heavy Armo": "Tallum Boots Heavy Armor",
    "Tallum Blade*Dark Legio": "Tallum Blade*Dark Legion's Edge",
    "Fish Stew - Magnus": "Fish Stew - Mastery",
    "Recipe: Artisan's frame": "Recipe: Artisan's Frame (60%)",
    "Recipe: Basalt Battleha": "Recipe: Basalt Battlehammer (60%)",
    "Recipe: Behemoth' Tunin": "Recipe: Behemoth's Tuning Fork (60%)",
    "Recipe: Black Ore Earri": "Recipe: Black Ore Earring (60%)",
    "Recipe: Black Ore Neckl": "Recipe: Black Ore Necklace (60%)",
    "Recipe: Blue Wolf Breas": "Recipe: Blue Wolf Breastplate (60%)",
    "Recipe: Blue Wolf Gaite": "Recipe: Blue Wolf Gaiters (60%)",
    "Recipe: Blue Wolf Helme": "Recipe: Blue Wolf Helmet (60%)",
    "Recipe: Blue Wolf Leath": "Recipe: Blue Wolf Leather Armor (60%)",
    "Recipe: Blue Wolf Stock": "Recipe: Blue Wolf Stockings (60%)",
    "Recipe: Carnage Bow(60%)": "Recipe: Carnage Bow (60%)",
    "Recipe: Dark Legion's E": "Recipe: Dark Legion's Edge (60%)",
    "Recipe: Destroyer Hamme": "Recipe: Destroyer Hammer (60%)",
    "Recipe: Doom Plate Armo": "Recipe: Doom Plate Armor (60%)",
    "Recipe: Dragon Hunter A": "Recipe: Dragon Hunter Axe (60%)",
    "Recipe: Durable Metal P": "Recipe: Durable Metal Plate (60%)",
    "Recipe: Elysian(60%)": "Recipe: Elysian (60%)",
    "Recipe: Flaming Dragon I": "Recipe: Flaming Dragon Skull (60%)",
    "Recipe: Forgotten Blade": "Recipe: Forgotten Blade (60%)",
    "Recipe: Greater Blessed": "Recipe: Greater Blessed Spiritshot (60%)",
    "Recipe: Greater Fish Oi": "Recipe: Greater Fish Oil (60%)",
    "Recipe: Heaven's Divide": "Recipe: Heaven's Divider (60%)",
    "Recipe: Kaim Vanul's Bo": "Recipe: Kaim Vanul's Bones (60%)",
    "Recipe: Leather Armor o": "Recipe: Leather Armor of Doom (60%)",
    "Recipe: Premium Fish Oi": "Recipe: Premium Fish Oil (60%)",
    "Recipe: Soul Bow(60%)": "Recipe: Soul Bow (60%)",
    "Recipe: Staff of Evil S": "Recipe: Staff of Evil Spirits (60%)",
    "Recipe: Stockings of Do": "Recipe: Stockings of Doom (60%)",
    "Recipe: Sword of Miracl": "Recipe: Sword of Miracles (60%)",
    "Blessed Scroll of Resurection": "Blessed Scroll of Resurrection",
    "Top-grade Life Stone": "Top-Grade Life Stone",
    "Improved Top-grade Life Stone": "Improved Top-Grade Life Stone",
}

# Merged OCR lines → multiple canonical names.
_SPLITS: dict[str, list[str]] = {
    "Animal Bone Major Healing Potion": ["Animal Bone", "Major Healing Potion"],
    "Arcana Mace Head Ring of Seal Gemstone": ["Arcana Mace Head", "Ring of Seal Gemstone"],
    "Arcana Mace Saint Spear": ["Arcana Mace Head", "Saint Spear"],
    "Arcsmith's Anvil Life Force": ["Arcsmith's Anvil", "Life Force"],
    "Avadon Breastplate Avadon Leather Armor": ["Avadon Breastplate", "Avadon Leather Armor"],
    "Black Ore Earring Black Ore Necklace": ["Black Ore Earring", "Black Ore Necklace"],
    "Braided Hemp Metal Hardener": ["Braided Hemp", "Metal Hardener"],
    "Compound Helmet Adamantite Ring": ["Compound Helmet", "Adamantite Ring"],
    "Dark Stone Blue Wolf Gaiters": ["Dark Stone", "Blue Wolf Gaiters"],
    "Elysian Head Heaven's Divider Edge": ["Elysian Head", "Heaven's Divider Edge"],
    "Holy Stone Leather Armor of Doom": ["Holy Stone", "Leather Armor of Doom"],
    "Leonard Destroyer Hammer Piece": ["Leonard", "Destroyer Hammer Piece"],
    "Life Stone Full Plate Shield": ["Mid-grade Life Stone", "Full Plate Shield"],
    "Meteor Shower Head Destruction Tombstone": ["Meteor Shower Head", "Destruction Tombstone"],
    "Naga Storm Soul Separator": ["Naga Storm", "Soul Separator"],
    "Gemstone Coarse Bone Powder": ["Gemstone (A-grade)", "Coarse Bone Powder"],
    "Themis' Tongue Piece Greater STR Dye <STR +": ["Themis' Tongue Piece", "Greater STR Dye <Str+1 Con-1>"],
    "Warsmith's Holder Zubei's Breastplate": ["Warsmith's Holder", "Zubei's Breastplate"],
    "Water Stone Tallum Tunic": ["Water Stone", "Tallum Tunic"],
}

_DROP: set[str] = {
    "Chef's",
    "Bellion Cestus - Great",
    "Naga Storm Critical Dam",
    "Orcish Poleaxe Long Blo",
    "Orcish Poleaxe Wide Blo",
    "Themis Tongue",
    "Themis'Tongue",
}

_EXTRA: list[str] = [
    "Ancient Adena",
    "Animal Bone",
    "Avadon Breastplate",
    "Avadon Leather Armor",
    "Blue Wolf Gaiters",
    "Coarse Bone Powder",
    "Compound Helmet",
    "Dark Stone",
    "Destruction Tombstone",
    "Full Plate Shield",
    "Gemstone A",
    "Gemstone S",
    "Imperial Crusader Helmet",
    "Life Force",
    "Major Arcana Boots",
    "Metal Hardener",
    "Recipe: Draconic Bow (60%)",
    "Ring of Seal Gemstone",
    "Suede",
    "Top-Grade Life Stone",
    "Warsmith's Holder",
    "Zubei's Breastplate",
    "Arcana Mace Head",
    "Leonard",
    "Heaven's Divider Edge",
    "Elysian Head",
    "Meteor Shower Head",
    "Greater STR Dye <Str+1 Con-1>",
]


def repair_items_database_lines(lines: list[str]) -> list[str]:
    names: dict[str, str] = {}

    def add(raw: str | None) -> None:
        if not raw:
            return
        text = raw.strip()
        if not text or text.startswith("#"):
            return
        if text in _DROP:
            return
        if text in _SPLITS:
            for part in _SPLITS[text]:
                add(part)
            return
        text = _REPLACEMENTS.get(text, text)
        key = text.casefold()
        if key not in names:
            names[key] = text

    for line in lines:
        add(line.strip())

    for extra in _EXTRA:
        add(extra)

    return sorted(names.values(), key=str.casefold)


def repair_items_database_file(path: Path = DEFAULT_ITEMS_DB) -> int:
    path = path.resolve()
    raw = path.read_text(encoding="utf-8").splitlines()
    repaired = repair_items_database_lines(raw)
    body = _HEADER + "\n".join(repaired) + ("\n" if repaired else "")
    path.write_text(body, encoding="utf-8")
    return len(repaired)
