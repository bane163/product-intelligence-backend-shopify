"""Conservative, same-product SKU pattern inference for import preflight."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


_SEPARATORS = ("-", "_", ".")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _variants(product: dict[str, Any]) -> list[dict[str, Any]]:
    raw = product.get("variants")
    if isinstance(raw, dict):
        raw = raw.get("nodes")
    return (
        [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, list)
        else []
    )


def _option_names(product: dict[str, Any]) -> list[str]:
    raw = (
        product.get("productOptions")
        if isinstance(product.get("productOptions"), list)
        else product.get("options")
    )
    if isinstance(raw, list):
        names = [_text(item.get("name")) for item in raw if isinstance(item, dict)]
        if any(names):
            return [name for name in names if name]
    for variant in _variants(product):
        pairs = variant_option_pairs(variant, [])
        if pairs:
            return [name for name, _ in pairs]
    return []


def variant_option_pairs(
    variant: dict[str, Any], option_names: list[str]
) -> list[tuple[str, str]]:
    raw = variant.get("selectedOptions") or variant.get("selected_options")
    if not isinstance(raw, list):
        raw = variant.get("optionValues") or variant.get("option_values")
    pairs: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            if "value" in item:  # Shopify selectedOptions shape.
                name = _text(
                    item.get("name")
                    or item.get("optionName")
                    or item.get("option_name")
                )
                value = _text(item.get("value"))
            else:  # productSet optionValues shape.
                name = _text(item.get("optionName") or item.get("option_name"))
                value = _text(item.get("name"))
            if not name and index < len(option_names):
                name = option_names[index]
            if name and value:
                pairs.append((name, value))
    if not pairs:
        for index, name in enumerate(option_names[:3], start=1):
            value = _text(variant.get(f"option{index}"))
            if value:
                pairs.append((name, value))
    return pairs


def variant_option_values(
    variant: dict[str, Any], option_names: list[str]
) -> tuple[str, ...] | None:
    pairs = variant_option_pairs(variant, option_names)
    by_name = {name.casefold(): value for name, value in pairs}
    values = tuple(by_name.get(name.casefold(), "") for name in option_names)
    return values if values and all(values) else None


def matching_live_variant(
    imported_variant: dict[str, Any],
    live_product: dict[str, Any] | None,
    imported_product: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(live_product, dict):
        return None
    live_variants = _variants(live_product)
    imported_id = _text(imported_variant.get("id"))
    if imported_id:
        matches = [
            item for item in live_variants if _text(item.get("id")) == imported_id
        ]
        if len(matches) == 1:
            return matches[0]
    names = _option_names(imported_product) or _option_names(live_product)
    wanted = variant_option_values(imported_variant, names)
    if (
        wanted is None
        and len(live_variants) == 1
        and len(_variants(imported_product)) == 1
    ):
        return live_variants[0]
    matches = [
        item for item in live_variants if variant_option_values(item, names) == wanted
    ]
    return matches[0] if wanted is not None and len(matches) == 1 else None


def carry_forward_required_live_values(
    product: dict[str, Any], live_product: dict[str, Any] | None
) -> None:
    """Fill blank update-only required fields from the uniquely matched live value."""
    if not isinstance(live_product, dict):
        return
    if not _text(product.get("title")) and _text(live_product.get("title")):
        product["title"] = live_product["title"]
    for variant in _variants(product):
        live_variant = matching_live_variant(variant, live_product, product)
        if not live_variant:
            continue
        for field in ("sku", "price"):
            if not _text(variant.get(field)) and _text(live_variant.get(field)):
                variant[field] = live_variant[field]


def _normalized_token(value: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9]+", value))


def _case_style(token: str) -> str | None:
    letters = [char for char in token if char.isalpha()]
    if not letters:
        return "unchanged"
    if all(char.isupper() for char in letters):
        return "upper"
    if all(char.islower() for char in letters):
        return "lower"
    if token[:1].isupper() and all(
        char.islower() for char in token[1:] if char.isalpha()
    ):
        return "title"
    return None


def _styled(value: str, style: str) -> str:
    if style == "upper":
        return value.upper()
    if style == "lower":
        return value.lower()
    if style == "title":
        return value[:1].upper() + value[1:].lower()
    return value


@dataclass(frozen=True)
class _Pattern:
    prefix: str
    separator: str
    mappings: tuple[dict[str, str], ...]
    full_value_positions: tuple[bool, ...]
    styles: tuple[str, ...]

    def render(self, option_values: tuple[str, ...]) -> str | None:
        if len(option_values) != len(self.mappings):
            return None
        suffix: list[str] = []
        for index, value in enumerate(option_values):
            observed = self.mappings[index].get(value.casefold())
            if observed:
                suffix.append(observed)
                continue
            if not self.full_value_positions[index]:
                return None
            token = _normalized_token(value)
            if not token:
                return None
            suffix.append(_styled(token, self.styles[index]))
        return self.separator.join([self.prefix, *suffix])


def _learn_pattern(
    examples: Iterable[tuple[str, tuple[str, ...]]], option_count: int
) -> _Pattern | None:
    rows = list(examples)
    if not rows or option_count < 1:
        return None
    candidates: list[_Pattern] = []
    for separator in _SEPARATORS:
        parsed: list[tuple[str, list[str], tuple[str, ...]]] = []
        for sku, values in rows:
            pieces = sku.rsplit(separator, option_count)
            if (
                len(pieces) != option_count + 1
                or not pieces[0]
                or any(not part for part in pieces[1:])
            ):
                break
            parsed.append((pieces[0], pieces[1:], values))
        if len(parsed) != len(rows) or len({row[0] for row in parsed}) != 1:
            continue
        mappings: list[dict[str, str]] = [dict() for _ in range(option_count)]
        full_positions: list[bool] = [True] * option_count
        styles: list[str | None] = [None] * option_count
        valid = True
        for _, tokens, values in parsed:
            for index, (token, value) in enumerate(zip(tokens, values, strict=True)):
                key = value.casefold()
                if key in mappings[index] and mappings[index][key] != token:
                    valid = False
                    break
                mappings[index][key] = token
                full_positions[index] &= (
                    token.casefold() == _normalized_token(value).casefold()
                )
                style = _case_style(token)
                if style is None or (
                    styles[index] is not None and styles[index] != style
                ):
                    valid = False
                    break
                styles[index] = style
            if not valid:
                break
        if valid:
            candidates.append(
                _Pattern(
                    parsed[0][0],
                    separator,
                    tuple(mappings),
                    tuple(full_positions),
                    tuple(style or "unchanged" for style in styles),
                )
            )
    return candidates[0] if len(candidates) == 1 else None


def generate_missing_skus(
    product: dict[str, Any], live_product: dict[str, Any] | None = None
) -> dict[int, str]:
    """Return candidate SKUs for unmatched blank variants, without mutating input."""
    option_names = _option_names(product) or (
        _option_names(live_product) if live_product else []
    )
    if not option_names:
        return {}
    examples: list[tuple[str, tuple[str, ...]]] = []
    for sibling in [
        *_variants(product),
        *(_variants(live_product) if live_product else []),
    ]:
        sku = _text(sibling.get("sku"))
        values = variant_option_values(sibling, option_names)
        if sku and values:
            examples.append((sku, values))
    pattern = _learn_pattern(examples, len(option_names))
    if pattern is None:
        return {}
    generated: dict[int, str] = {}
    for index, variant in enumerate(_variants(product)):
        if _text(variant.get("sku")) or matching_live_variant(
            variant, live_product, product
        ):
            continue
        values = variant_option_values(variant, option_names)
        candidate = pattern.render(values) if values else None
        if candidate:
            generated[index] = candidate
    return generated
