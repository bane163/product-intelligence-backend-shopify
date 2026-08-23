"""Cost-weighted processing-credit pricing primitives.

Money is represented with :class:`~decimal.Decimal` and rate cards are
effective-dated/versioned so a settlement can always retain the prices that
were used when work was quoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Any, Mapping


ONE_MILLION = Decimal(1_000_000)
CREDIT_RETAIL_VALUE_USD = Decimal("0.01")
COST_SHARE = Decimal("0.30")
UNCERTAINTY_BUFFER = Decimal("1.25")
LONG_CONTEXT_THRESHOLD = 272_000


@dataclass(frozen=True)
class Plan:
    handle: str
    name: str
    monthly_price_cents: int
    included_credits: int


PLANS: dict[str, Plan] = {
    "starter": Plan("starter", "Starter", 1_900, 1_900),
    "growth": Plan("growth", "Growth", 4_900, 5_400),
    "scale": Plan("scale", "Scale", 14_900, 17_800),
}


@dataclass(frozen=True)
class OpenAIRate:
    provider: str
    model: str
    version: str
    effective_at: datetime
    input_per_million_usd: Decimal
    cached_input_per_million_usd: Decimal
    output_per_million_usd: Decimal
    cache_write_multiplier: Decimal = Decimal("1.25")
    long_context_threshold: int = LONG_CONTEXT_THRESHOLD
    long_input_multiplier: Decimal = Decimal("2")
    long_output_multiplier: Decimal = Decimal("1.5")


GPT_5_6_LUNA_V1 = OpenAIRate(
    provider="openai",
    model="gpt-5.6-luna",
    version="openai:gpt-5.6-luna:v1",
    effective_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    input_per_million_usd=Decimal("0.20"),
    cached_input_per_million_usd=Decimal("0.02"),
    output_per_million_usd=Decimal("1.20"),
)


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int = 0
    cached_input_tokens: int | None = None
    cache_write_tokens: int = 0
    output_tokens: int = 0
    azure_pages: int = 0

    def as_dict(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "output_tokens": self.output_tokens,
            "azure_pages": self.azure_pages,
        }


@dataclass(frozen=True)
class CostBreakdown:
    rate_card_version: str
    openai_cost_usd: Decimal
    azure_cost_usd: Decimal
    platform_cost_usd: Decimal
    raw_cost_usd: Decimal
    credits: int
    usage: ProviderUsage = field(default_factory=ProviderUsage)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rate_card_version": self.rate_card_version,
            "openai_cost_usd": str(self.openai_cost_usd),
            "azure_cost_usd": str(self.azure_cost_usd),
            "platform_cost_usd": str(self.platform_cost_usd),
            "raw_cost_usd": str(self.raw_cost_usd),
            "credits": self.credits,
            "usage": self.usage.as_dict(),
        }


class BillingRateUnavailable(ValueError):
    code = "BILLING_RATE_UNAVAILABLE"


def _non_negative(value: int | None) -> int:
    return max(0, int(value or 0))


def credits_for_raw_cost(raw_cost_usd: Decimal | str | float) -> int:
    """Return whole credits using the product's buffered retail-cost formula."""
    raw = max(Decimal("0"), Decimal(str(raw_cost_usd)))
    value = raw * UNCERTAINTY_BUFFER / COST_SHARE / CREDIT_RETAIL_VALUE_USD
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def calculate_cost(
    *,
    usage: ProviderUsage,
    openai_rate: OpenAIRate | None,
    azure_page_rate_usd: Decimal | str | float | None,
    platform_cost_usd: Decimal | str | float,
    rate_card_version: str | None = None,
) -> CostBreakdown:
    """Price provider usage conservatively and convert it to whole credits.

    When cached-token telemetry is absent, every input token is charged at the
    uncached rate. Reported cached/cache-write tokens are subsets of input.
    """
    input_tokens = _non_negative(usage.input_tokens)
    output_tokens = _non_negative(usage.output_tokens)
    cache_write_tokens = min(input_tokens, _non_negative(usage.cache_write_tokens))
    cached_known = usage.cached_input_tokens is not None
    cached_tokens = (
        min(max(0, input_tokens - cache_write_tokens), _non_negative(usage.cached_input_tokens))
        if cached_known
        else 0
    )
    uncached_tokens = max(0, input_tokens - cached_tokens - cache_write_tokens)

    if input_tokens or output_tokens:
        if openai_rate is None:
            raise BillingRateUnavailable("No effective OpenAI rate for provider/model")
        long_context = input_tokens > openai_rate.long_context_threshold
        input_multiplier = openai_rate.long_input_multiplier if long_context else Decimal("1")
        output_multiplier = openai_rate.long_output_multiplier if long_context else Decimal("1")
        openai_cost = (
            Decimal(uncached_tokens)
            * openai_rate.input_per_million_usd
            * input_multiplier
            + Decimal(cached_tokens)
            * openai_rate.cached_input_per_million_usd
            * input_multiplier
            + Decimal(cache_write_tokens)
            * openai_rate.input_per_million_usd
            * openai_rate.cache_write_multiplier
            * input_multiplier
            + Decimal(output_tokens)
            * openai_rate.output_per_million_usd
            * output_multiplier
        ) / ONE_MILLION
        version = rate_card_version or openai_rate.version
    else:
        openai_cost = Decimal("0")
        version = rate_card_version or (openai_rate.version if openai_rate else "platform-only:v1")

    pages = _non_negative(usage.azure_pages)
    if pages and azure_page_rate_usd is None:
        raise BillingRateUnavailable("Azure Document Intelligence page rate is not configured")
    azure_cost = Decimal(pages) * Decimal(str(azure_page_rate_usd or 0))
    platform_cost = max(Decimal("0"), Decimal(str(platform_cost_usd)))
    raw = openai_cost + azure_cost + platform_cost
    return CostBreakdown(
        rate_card_version=version,
        openai_cost_usd=openai_cost,
        azure_cost_usd=azure_cost,
        platform_cost_usd=platform_cost,
        raw_cost_usd=raw,
        credits=credits_for_raw_cost(raw),
        usage=usage,
    )


def configured_platform_floors(env: Mapping[str, str]) -> dict[str, Decimal]:
    """Read calibrated operation floors; unset values deliberately remain zero."""
    defaults = {
        "document_import": "0",
        "product_intelligence_audit": "0",
        "product_intelligence_regeneration": "0",
    }
    return {
        operation: max(
            Decimal("0"),
            Decimal(env.get(f"BILLING_PLATFORM_FLOOR_{operation.upper()}_USD", default)),
        )
        for operation, default in defaults.items()
    }


def estimate_usage(
    operation_type: str,
    *,
    pages: int = 0,
    rows: int = 0,
    products: int = 0,
) -> ProviderUsage:
    """P95 reservation estimate from locally measurable workload dimensions.

    Coefficients are intentionally environment-overridable by the service layer;
    these safe baselines keep quotes deterministic until benchmark calibration.
    """
    pages = _non_negative(pages)
    rows = _non_negative(rows)
    products = _non_negative(products)
    if operation_type == "document_import":
        units = max(1, pages, (rows + 49) // 50)
        return ProviderUsage(
            input_tokens=4_000 + units * 3_000,
            output_tokens=2_000 + units * 1_500,
            azure_pages=pages,
        )
    if operation_type in {"product_intelligence_audit", "product_intelligence_regeneration"}:
        units = max(1, products)
        return ProviderUsage(input_tokens=2_000 + units * 1_200, output_tokens=800 + units * 500)
    raise BillingRateUnavailable(f"No estimator is configured for operation type {operation_type!r}")
