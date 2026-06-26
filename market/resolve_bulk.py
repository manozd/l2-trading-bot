"""Resolve bulk crawl observations against the variant catalog (fuzzy icon + names)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from market.craft.match import _MIN_ACCEPT_SCORE, _match_score
from market.icon_hash import FUZZY_EXACT_MAX, FUZZY_NAME_ACCEPT_MAX, FUZZY_STRONG_MAX
from market.identity_status import TRUSTED_IDENTITY_STATUSES, is_trusted_identity
from market.variant_catalog import CatalogEntry, VariantCatalog, normalize_display_name

ResolveStatus = str


@dataclass
class ResolveStats:
    total: int = 0
    trusted: int = 0
    aliases_added: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


@dataclass
class _MatchCandidate:
    entry: CatalogEntry
    name_score: int
    icon_distance: int | None
    exact_icon: bool
    prefix_name: bool


def _best_name_score(names: list[str], entry: CatalogEntry) -> int:
    targets = [
        entry.display_name,
        entry.normalized_name,
        entry.search_query or "",
        entry.variant_group or "",
    ]
    best = 0
    for name in names:
        if not name:
            continue
        for target in targets:
            if not target:
                continue
            best = max(best, _match_score(name, target))
            if normalize_display_name(name) == normalize_display_name(target):
                best = max(best, 100)
    return best


def _is_prefix_name_match(name: str, entry: CatalogEntry) -> bool:
    n = normalize_display_name(name)
    for target in (entry.display_name, entry.search_query or "", entry.variant_group or ""):
        t = normalize_display_name(target)
        if not n or not t:
            continue
        if t.startswith(n) or n.startswith(t):
            if len(n) >= max(6, int(len(t) * 0.65)):
                return True
    return False


def _observation_names(obs: dict[str, Any]) -> list[str]:
    """
    Name signals for bulk rows — vendor page OCR first, then list-visible OCR.
    """
    ordered: list[str] = []

    for row in obs.get("vendor_rows") or []:
        raw = str(row.get("raw_text") or "")
        if not raw:
            continue
        head = raw.split(" In stock:", 1)[0].strip()
        head = head.split(" On market:", 1)[0].strip()
        if head:
            ordered.append(head)

    lc = obs.get("list_context") or {}
    visible = lc.get("visible_name_ocr")
    if visible:
        ordered.append(str(visible))

    out: list[str] = []
    seen: set[str] = set()
    for name in ordered:
        key = normalize_display_name(name)
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _status_from_candidate(c: _MatchCandidate) -> ResolveStatus:
    if c.name_score < _MIN_ACCEPT_SCORE and not c.prefix_name:
        return "fuzzy_icon_candidate"

    if c.exact_icon:
        if c.name_score >= _MIN_ACCEPT_SCORE:
            return "exact_icon_name_matched"
        if c.prefix_name:
            return "exact_icon_prefix_matched"
        if c.entry.source == "search_confirmed":
            return "search_confirmed"
        return "icon_only_candidate"

    dist = c.icon_distance if c.icon_distance is not None else 999
    if dist <= FUZZY_EXACT_MAX:
        prefix = "exact_icon"
    elif dist <= FUZZY_NAME_ACCEPT_MAX:
        prefix = "fuzzy_icon"
    else:
        prefix = "fuzzy_icon"

    if c.name_score >= _MIN_ACCEPT_SCORE:
        return f"{prefix}_name_matched"  # type: ignore[return-value]
    if c.prefix_name:
        return f"{prefix}_prefix_matched"  # type: ignore[return-value]
    return "fuzzy_icon_candidate"


def _trusted_status(status: ResolveStatus, candidate_count: int) -> bool:
    return candidate_count == 1 and is_trusted_identity(status)


def _collect_candidates(
    obs: dict[str, Any],
    catalog: VariantCatalog,
) -> list[_MatchCandidate]:
    lc = obs.get("list_context") or {}
    icon_hash = lc.get("icon_hash")
    names = _observation_names(obs)
    candidates: list[_MatchCandidate] = []

    if icon_hash:
        for entry in catalog.find_by_icon(icon_hash):
            name_score = _best_name_score(names, entry)
            prefix = any(_is_prefix_name_match(n, entry) for n in names)
            candidates.append(
                _MatchCandidate(
                    entry=entry,
                    name_score=name_score,
                    icon_distance=0,
                    exact_icon=True,
                    prefix_name=prefix,
                )
            )

        for entry, dist in catalog.fuzzy_icon_candidates(icon_hash):
            if any(c.entry.item_uid == entry.item_uid for c in candidates):
                continue
            name_score = _best_name_score(names, entry)
            if name_score < _MIN_ACCEPT_SCORE and not any(
                _is_prefix_name_match(n, entry) for n in names
            ):
                if dist > FUZZY_STRONG_MAX:
                    continue
            prefix = any(_is_prefix_name_match(n, entry) for n in names)
            candidates.append(
                _MatchCandidate(
                    entry=entry,
                    name_score=name_score,
                    icon_distance=dist,
                    exact_icon=False,
                    prefix_name=prefix,
                )
            )

    if not candidates and names:
        scored: list[tuple[CatalogEntry, int, bool]] = []
        for entry in catalog.entries.values():
            name_score = _best_name_score(names, entry)
            prefix = any(_is_prefix_name_match(n, entry) for n in names)
            if name_score >= _MIN_ACCEPT_SCORE or prefix:
                scored.append((entry, name_score, prefix))
        if len(scored) == 1:
            entry, name_score, prefix = scored[0]
            candidates.append(
                _MatchCandidate(
                    entry=entry,
                    name_score=name_score,
                    icon_distance=None,
                    exact_icon=False,
                    prefix_name=prefix,
                )
            )

    return candidates


def _pick_candidates(candidates: list[_MatchCandidate]) -> tuple[list[_MatchCandidate], ResolveStatus | None]:
    if not candidates:
        return [], None

    trusted: list[_MatchCandidate] = []
    for c in candidates:
        status = _status_from_candidate(c)
        if is_trusted_identity(status):
            trusted.append(c)

    if len(trusted) == 1:
        return trusted, _status_from_candidate(trusted[0])

    if len(trusted) > 1:
        return trusted, "ambiguous_icon_name"

    weak = [c for c in candidates if _status_from_candidate(c) == "fuzzy_icon_candidate"]
    if len(weak) == 1:
        return weak, "fuzzy_icon_candidate"
    if len(weak) > 1:
        return weak, "ambiguous_fuzzy_icon"

    return candidates, "unresolved"


def resolve_observation(
    obs: dict[str, Any],
    catalog: VariantCatalog,
) -> tuple[ResolveStatus, CatalogEntry | None, list[str], int | None]:
    """
    Return ``(status, entry, possible_item_uids, icon_distance)``.
    """
    lc = obs.get("list_context") or {}
    icon_hash = lc.get("icon_hash")
    candidates = _collect_candidates(obs, catalog)

    possible_uids = sorted({c.entry.item_uid for c in candidates})
    picked, ambiguous = _pick_candidates(candidates)

    if ambiguous == "ambiguous_icon_name" or ambiguous == "ambiguous_fuzzy_icon":
        return ambiguous, picked[0].entry if picked else None, possible_uids, None

    if not picked or ambiguous == "unresolved":
        return "unresolved", None, possible_uids, None

    best = picked[0]
    status = _status_from_candidate(best)
    if len(picked) > 1:
        status = "ambiguous_icon_name"
        return status, best.entry, possible_uids, best.icon_distance

    if not _trusted_status(status, 1) and status == "fuzzy_icon_candidate":
        return status, best.entry, possible_uids, best.icon_distance

    return status, best.entry, possible_uids, best.icon_distance


def resolve_bulk_observations(
    observations: list[dict[str, Any]],
    catalog: VariantCatalog,
    *,
    record_aliases: bool = False,
    alias_source: str = "bulk",
) -> tuple[list[dict[str, Any]], ResolveStats]:
    stats = ResolveStats()
    resolved: list[dict[str, Any]] = []

    for obs in observations:
        stats.total += 1
        status, entry, possible_uids, icon_distance = resolve_observation(obs, catalog)

        lc = obs.get("list_context") or {}
        obs_icon = lc.get("icon_hash")

        if (
            record_aliases
            and entry is not None
            and obs_icon
            and is_trusted_identity(status)
            and obs_icon not in entry.all_icon_hashes()
        ):
            catalog.add_icon_alias(
                entry,
                icon_hash=str(obs_icon),
                source=alias_source,
                match_method=status,
                distance_to_canonical=icon_distance,
                scanned_at=obs.get("timestamp"),
            )
            stats.aliases_added += 1

        out = dict(obs)
        identity = dict(out.get("identity") or {})
        identity["status"] = status
        identity["possible_item_uids"] = possible_uids
        identity["source"] = "bulk_resolver"
        if icon_distance is not None:
            identity["icon_distance"] = icon_distance

        if entry is not None:
            identity["item_uid"] = entry.item_uid
            identity["item_id"] = entry.variant_group
            identity["item_name"] = entry.display_name
            identity["catalog_search_query"] = entry.search_query
            identity["canonical_icon_hash"] = entry.icon_hash
        else:
            identity["item_uid"] = None
            identity["item_id"] = None
            identity["item_name"] = None

        out["identity"] = identity
        resolved.append(out)

        stats.by_status[status] = stats.by_status.get(status, 0) + 1
        if is_trusted_identity(status):
            stats.trusted += 1

    return resolved, stats


def load_bulk_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("type") == "bulk_vendor_scan":
            rows.append(row)
    return rows


def write_resolved_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_resolve_summary(stats: ResolveStats) -> None:
    print(f"[resolve] total observations: {stats.total}", flush=True)
    print(f"[resolve] trusted identity: {stats.trusted}", flush=True)
    if stats.aliases_added:
        print(f"[resolve] icon aliases added: {stats.aliases_added}", flush=True)
    print("[resolve] by status:", flush=True)
    for status, count in sorted(stats.by_status.items(), key=lambda x: -x[1]):
        tag = "trusted" if status in TRUSTED_IDENTITY_STATUSES else "untrusted"
        print(f"  {status}: {count} ({tag})", flush=True)
