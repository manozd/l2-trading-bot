"""Match OCR search-result rows to target item names."""

from __future__ import annotations

import re

from market.full_list_parser import MarketRow

_GRADE_RE = re.compile(r"\(([a-z])-grade\)", re.I)
_MIN_ACCEPT_SCORE = 55

_UI_CHROME = frozenset({
    "buy items",
    "buy item",
    "full list equipment",
    "enchanted eguipment",
    "enchanted equipment",
    "attr. equipment",
    "attribute equipment",
    "other",
    "equipment",
    "materials",
    "consumables",
})


def _norm(name: str) -> str:
    t = name.casefold().strip()
    t = re.sub(r"\.{2,}$", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _compact(name: str) -> str:
    """Alphanumeric only — tolerates OCR dropping spaces and punctuation."""
    return re.sub(r"[^a-z0-9]", "", _norm(name))


def _compact_extra_allowed(extra: str) -> bool:
    """Reject long alpha suffixes — e.g. ``shaft`` after ``draconicbow``."""
    if not extra:
        return True
    if len(extra) <= 2:
        return True
    if re.fullmatch(r"\d+%?", extra):
        return True
    if re.fullmatch(r"[0-9o]{1,3}", extra):
        return True
    return False


def _compact_near_match(a: str, b: str, *, max_edits: int = 1) -> bool:
    """One OCR typo in compact form (e.g. ``moid`` vs ``mold``)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > max_edits:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= max_edits
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(long) - len(short) != 1:
        return False
    for i in range(len(long)):
        if long[:i] + long[i + 1 :] == short:
            return True
    return False


def is_ocr_garbage_item(item: str) -> bool:
    """Reject stock-count fragments mistaken for item names (e.g. ``16,436 units``)."""
    t = _norm(item)
    if not t:
        return True
    if re.search(r"\bunits?\b", t, re.I):
        return True
    if re.fullmatch(r"[\d,\s]+", t.replace(" ", "")):
        return True
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    return digits > 0 and letters <= 2 and digits >= 3


def _compact_prefix_match(a: str, b: str, *, min_ratio: float = 0.82) -> bool:
    """True when OCR truncated the name or garbled the last character (e.g. ``6o`` vs ``60``)."""
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if longer.startswith(shorter):
        extra = longer[len(shorter) :]
        if _compact_extra_allowed(extra) and len(shorter) >= int(len(longer) * min_ratio):
            return True
    if len(shorter) >= 8 and len(longer) - len(shorter) <= 2 and longer.startswith(shorter[:-1]):
        return True
    return False


def _is_recipe_row(item: str) -> bool:
    return _norm(item).startswith("recipe:")


def is_ui_chrome_row(row: MarketRow) -> bool:
    item = _norm(row.item or row.raw_text or "")
    if not item:
        return True
    if item in _UI_CHROME:
        return True
    if item.startswith("full list"):
        return True
    compact = _compact(item)
    hub_frags = (
        "equipment",
        "enchanted",
        "consumables",
        "materials",
        "materialparts",
        "recipes",
        "other",
        "fulllist",
    )
    hub_hits = sum(1 for frag in hub_frags if frag in compact)
    if hub_hits >= 2:
        return True
    if hub_hits == 1 and not item.startswith("recipe:") and row.price_adena is None:
        return True
    if "equipment" in item and "recipe:" not in item and len(item) < 40:
        return True
    return False


def filter_search_result_rows(rows: list[MarketRow]) -> list[MarketRow]:
    return [r for r in rows if not is_ui_chrome_row(r)]


def is_likely_search_hub(rows: list[MarketRow]) -> bool:
    """Category hub (Equipment, Materials, …) — not filtered item search results."""
    if not rows:
        return False
    if filter_search_result_rows(rows):
        return False
    return all(is_ui_chrome_row(r) for r in rows)


def on_search_results_screen(rows: list[MarketRow]) -> bool:
    """True when the item search-results list is showing (not the category hub)."""
    if filter_search_result_rows(rows):
        return True
    if not rows or is_likely_search_hub(rows):
        return False
    return True


def _extract_grade(name: str) -> str | None:
    m = _GRADE_RE.search(name)
    return m.group(1).casefold() if m else None


def _strip_grade(name: str) -> str:
    return _GRADE_RE.sub("", _norm(name)).strip()


def _extra_after_target(item: str, target: str) -> str | None:
    """Text in ``item`` after a shared prefix with ``target`` (normalized)."""
    item_n, target_n = _norm(item), _norm(target)
    if item_n == target_n:
        return None
    if item_n.startswith(target_n):
        rest = item_n[len(target_n) :].lstrip(" -:")
        return rest or None
    return None


def _is_allowed_extra(rest: str) -> bool:
    if not rest:
        return True
    if _GRADE_RE.search(rest):
        return True
    if re.fullmatch(r"\(\d+%\)", rest):
        return True
    return False


def _match_score(row_item: str, target_name: str) -> int:
    item = _norm(row_item)
    target = _norm(target_name)
    if not item or not target:
        return 0

    want_recipe = _is_recipe_row(target)
    if _is_recipe_row(item) and not want_recipe:
        return 0

    if item == target:
        return 100

    target_grade = _extract_grade(target)
    row_grade = _extract_grade(item)
    if target_grade and row_grade != target_grade:
        return 0

    ci = _compact(row_item)
    ct = _compact(target_name)
    if ci and ct:
        if ci == ct:
            return 100
        if _compact_prefix_match(ci, ct):
            return 91
        if len(ci) >= 8 and len(ct) >= 8 and _compact_near_match(ci, ct):
            return 86
        if want_recipe and not _is_recipe_row(item):
            recipe_target = re.sub(r"^recipe:\s*", "", target, flags=re.I)
            ct_recipe = _compact(recipe_target)
            if ct_recipe and _compact_prefix_match(ci, ct_recipe):
                return 89

    if not _is_recipe_row(item) and want_recipe and item != target:
        return 0

    extra = _extra_after_target(row_item, target_name)
    if extra is not None and not _is_allowed_extra(extra):
        return 0

    if target.startswith(item) and len(item) >= len(target) - 4:
        return 92

    if target_grade:
        base_item = _strip_grade(item)
        base_target = _strip_grade(target)
        if base_item == base_target:
            return 98
        if base_target in base_item or base_item in base_target:
            return 90

    if item.startswith(target + " "):
        return 95

    if target in item:
        if item.startswith(target):
            return 85
        extra_len = len(item) - len(target)
        if extra_len > 8:
            return 35
        return 60

    if item in target:
        return 70

    return 0


def _weak_single_word_fallback(
    rows: list[MarketRow],
    target_search_name: str,
    query: str,
) -> MarketRow | None:
    """
    When OCR garbles a short item name (e.g. ``apans`` for ``Suede``), accept the
    only simple list row if the search query equals the target.
    """
    if " " in target_search_name.strip():
        return None
    if _norm(query) != _norm(target_search_name):
        return None

    simple: list[MarketRow] = []
    for row in rows:
        item = row.item or ""
        item_n = _norm(item)
        if not item_n or _is_recipe_row(item) or is_ocr_garbage_item(item):
            continue
        if "-" in item or "grade" in item_n:
            continue
        if _extra_after_target(item, target_search_name):
            continue
        if _match_score(item, target_search_name) >= _MIN_ACCEPT_SCORE:
            continue
        if len(item_n.split()) > 2:
            continue
        simple.append(row)

    if len(simple) == 1:
        return simple[0]
    return None


def _pick_sort_key(row: MarketRow, target_search_name: str, score: int) -> tuple:
    item = row.item or ""
    exact_case = 1 if item == target_search_name else 0
    not_recipe = 0 if _is_recipe_row(item) and not _is_recipe_row(target_search_name) else 1
    not_chrome = 0 if is_ui_chrome_row(row) else 1
    name_len = len(_norm(item))
    return (score, exact_case, not_recipe, not_chrome, -name_len, -row.row)


def _score_row_match(
    item: str,
    target_search_name: str,
    *,
    search_query: str | None = None,
) -> int:
    """Match OCR row to target; optional query fallback only when grade is unambiguous."""
    score = _match_score(item, target_search_name)
    query = search_query or target_search_name
    if query == target_search_name or score >= _MIN_ACCEPT_SCORE:
        return score
    if _extract_grade(target_search_name):
        return score
    return max(score, _match_score(item, query) - 5)


def pick_result_row(
    rows: list[MarketRow],
    target_search_name: str,
    *,
    search_query: str | None = None,
) -> MarketRow | None:
    """
    Pick the best search-results row for ``target_search_name``.

    Never falls back to row 1 blindly. Returns None if no confident match.
    """
    rows = filter_search_result_rows(rows)
    if not rows:
        return None

    rows = [r for r in rows if not is_ocr_garbage_item(r.item or "")]
    if not rows:
        return None

    query = search_query or target_search_name
    scored: list[tuple[tuple, MarketRow]] = []
    for row in rows:
        item = row.item or ""
        score = _score_row_match(item, target_search_name, search_query=query)
        if score >= _MIN_ACCEPT_SCORE:
            scored.append((_pick_sort_key(row, target_search_name, score), row))

    if not scored:
        return _weak_single_word_fallback(rows, target_search_name, query)

    scored.sort(key=lambda t: t[0], reverse=True)
    best_key, best_row = scored[0]
    if len(scored) > 1 and scored[1][0] == best_key:
        tied = [row for key, row in scored if key == best_key]
        return min(tied, key=lambda r: r.row)

    return best_row


def find_search_result_price_row(
    rows: list[MarketRow],
    target_search_name: str,
    *,
    search_query: str | None = None,
) -> MarketRow | None:
    """Best matching row on the search-results screen that already shows a min price."""
    query = search_query or target_search_name
    picked = pick_result_row(rows, target_search_name, search_query=query)
    if (
        picked is not None
        and picked.price_adena is not None
        and picked.price_adena >= 50
        and not is_ui_chrome_row(picked)
    ):
        return picked

    candidates: list[tuple[int, MarketRow]] = []
    for row in filter_search_result_rows(rows):
        if row.price_adena is None or row.price_adena < 50:
            continue
        item = row.item or ""
        score = _score_row_match(item, target_search_name, search_query=query)
        if score >= _MIN_ACCEPT_SCORE:
            candidates.append((score, row))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def visual_click_row(visible: list[MarketRow], picked: MarketRow | None) -> int:
    """1-based visual row index for clicking (not OCR band number)."""
    ordered = sorted(visible, key=lambda r: r.row)
    if not ordered:
        return 1
    if picked is None:
        return 1
    for i, row in enumerate(ordered):
        if row.row == picked.row and (row.item or "") == (picked.item or ""):
            return i + 1
    for i, row in enumerate(ordered):
        if row.row == picked.row:
            return i + 1
    return 1


def format_result_rows(rows: list[MarketRow]) -> str:
    parts: list[str] = []
    for row in rows:
        label = row.item or row.raw_text or "?"
        parts.append(f"row{row.row}:{label!r}")
    return ", ".join(parts) if parts else "(empty)"
