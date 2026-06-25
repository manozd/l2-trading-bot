"""Map item-name characters to Pico KEY commands."""

from __future__ import annotations

from market.pc_keyboard import SEARCH_ALLOWED, validate_search_text

INPUT_PICO = "pico"
INPUT_PC = "pc"
INPUT_PASTE = "paste"

_PICO_NAMED: dict[str, str] = {
    " ": "SPACE",
    "-": "MINUS",
    "(": "LPAREN",
    ")": "RPAREN",
    "%": "PERCENT",
    ":": "COLON",
    "'": "APOSTROPHE",
}


def pico_strips_characters(text: str) -> bool:
    """True if the name contains chars the current Pico firmware cannot type."""
    for ch in text:
        if ch.isalpha() or ch.isdigit():
            continue
        if ch in _PICO_NAMED:
            continue
        return True
    return False


def unsupported_pico_chars(text: str) -> set[str]:
    return {c for c in text if c not in SEARCH_ALLOWED or (not c.isalnum() and c not in _PICO_NAMED)}


def iter_pico_key_tokens(text: str) -> list[str]:
    """Expand a validated item name into Pico KEY token list."""
    validate_search_text(text)
    tokens: list[str] = []
    for ch in text:
        if ch.isalpha():
            tokens.append(ch.lower())
        elif ch.isdigit():
            tokens.append(ch)
        elif ch in _PICO_NAMED:
            tokens.append(_PICO_NAMED[ch])
        else:
            raise ValueError(f"Unsupported character for Pico typing: {ch!r}")
    return tokens
