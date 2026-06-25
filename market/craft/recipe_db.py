"""Load craft recipes from config/recipes/*.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

from market.craft.models import Recipe, RecipeComponent

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPES_DIR = PROJECT_ROOT / "config" / "recipes"


def _norm_name(name: str) -> str:
    t = name.casefold().strip()
    return re.sub(r"\s+", " ", t)


def load_recipe(path: Path) -> Recipe:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return Recipe.from_dict(data)


def load_recipe_by_id(recipe_id: str, *, recipes_dir: Path = DEFAULT_RECIPES_DIR) -> Recipe:
    path = recipes_dir / f"{recipe_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Recipe not found: {path}")
    recipe = load_recipe(path)
    if recipe.recipe_id != recipe_id:
        raise ValueError(f"{path}: recipe_id mismatch ({recipe.recipe_id!r} != {recipe_id!r})")
    return recipe


def iter_recipe_paths(*, recipes_dir: Path = DEFAULT_RECIPES_DIR) -> list[Path]:
    if not recipes_dir.is_dir():
        return []
    return sorted(recipes_dir.glob("*.json"))


def load_all_recipes(*, recipes_dir: Path = DEFAULT_RECIPES_DIR) -> list[Recipe]:
    recipes: list[Recipe] = []
    for path in iter_recipe_paths(recipes_dir=recipes_dir):
        try:
            recipes.append(load_recipe(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return recipes


def _recipe_query_keys(recipe: Recipe) -> set[str]:
    keys = {
        _norm_name(recipe.search_name),
        _norm_name(recipe.recipe_id),
        _norm_name(recipe.recipe_id.replace("_", " ")),
    }
    return {k for k in keys if k}


def find_recipes_by_query(query: str, *, recipes_dir: Path = DEFAULT_RECIPES_DIR) -> list[Recipe]:
    """Match recipes by display name, id, or partial name."""
    q = _norm_name(query)
    if not q:
        return []

    all_recipes = load_all_recipes(recipes_dir=recipes_dir)
    if not all_recipes:
        return []

    exact = [r for r in all_recipes if q in _recipe_query_keys(r)]
    if exact:
        return exact

    partial: list[Recipe] = []
    for recipe in all_recipes:
        name = _norm_name(recipe.search_name)
        if q in name or name in q:
            partial.append(recipe)
    return partial


def find_recipe_by_query(query: str, *, recipes_dir: Path = DEFAULT_RECIPES_DIR) -> Recipe | None:
    matches = find_recipes_by_query(query, recipes_dir=recipes_dir)
    if len(matches) == 1:
        return matches[0]
    return None


def iter_components(component: RecipeComponent) -> list[RecipeComponent]:
    """Depth-first list of all nodes in a component subtree (including root)."""
    out = [component]
    if component.craft:
        for child in component.craft.components:
            out.extend(iter_components(child))
    return out


def collect_unique_materials(recipe: Recipe) -> list[RecipeComponent]:
    """All distinct materials by item_id (first occurrence wins for search_name)."""
    seen: set[str] = set()
    out: list[RecipeComponent] = []
    for top in recipe.components:
        for node in iter_components(top):
            if node.item_id in seen:
                continue
            seen.add(node.item_id)
            out.append(node)
    return out


def _accumulate_material_qty(
    component: RecipeComponent,
    *,
    mult: int,
    totals: dict[str, int],
) -> None:
    need = mult * component.qty
    totals[component.item_id] = totals.get(component.item_id, 0) + need
    if component.craft:
        for child in component.craft.components:
            _accumulate_material_qty(child, mult=need, totals=totals)


def collect_material_qty_map(recipe: Recipe) -> dict[str, int]:
    """Total units of each item_id required for one craft attempt (sums nested tree)."""
    totals: dict[str, int] = {}
    for top in recipe.components:
        _accumulate_material_qty(top, mult=1, totals=totals)
    return totals
