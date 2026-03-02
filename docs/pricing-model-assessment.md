# Pricing Model Assessment (Supabase + OpenAI + FastAPI)

This document gives a production pricing framework for your app based on:
- Supabase (database, storage, realtime)
- OpenAI (LLM usage)
- FastAPI backend deployment (API + worker)

## 1) Recommended pricing model

Use a **hybrid model**: monthly platform subscription + usage overage.

Why this fits your stack:
- OpenAI costs are variable, so overage protects margins.
- Supabase and backend have fixed monthly costs, so base subscription ensures predictable revenue.
- Merchants still get predictable plan pricing with a clear included usage amount.

---

## 2) Target unit economics

Target gross margin: **~80%**.

Core formula:

```text
blended_variable_cost_per_file =
  openai_cost_per_file
  + supabase_variable_cost_per_file
  + backend_runtime_cost_per_file

minimum_price_per_file_for_80_percent_margin =
  blended_variable_cost_per_file / (1 - 0.80)
  = blended_variable_cost_per_file * 5
```

Practical rule:
- Set overage price to **5x measured variable cost per file** (with a floor).
- Recalculate monthly as token patterns change.

---

## 3) Cost structure to include in pricing

## Fixed monthly costs (baseline)
- Supabase plan: typically **$25-$100+ / month** depending plan and usage.
- FastAPI hosting (API + worker): typically **$40-$180 / month** for small-to-mid production.
- Monitoring/logging/misc infra: typically **$20-$80 / month**.

Estimated fixed baseline: **$85-$360 / month**.

## Variable costs (per processed file/job)
- OpenAI tokens (input + output) — usually the largest variable driver.
- Supabase variable usage (storage growth, egress, heavy realtime usage).
- Backend CPU time for processing/conversion/queue work.

---

## 4) Recommended launch pricing (Hybrid)

These numbers are designed for your selected **growth target (50-300 files/month)** and margin objective, assuming you keep blended variable cost near or below ~$0.17/file.

| Plan | Monthly Price | Included Files | Overage |
|---|---:|---:|---:|
| Starter | $79 | 80 files | max($0.85, 5x measured variable cost/file) |
| Growth | $219 | 250 files | max($0.85, 5x measured variable cost/file) |
| Scale | $599 | 700 files | max($0.85, 5x measured variable cost/file) |

Additional pricing policy:
- Annual billing discount: **15%**.
- If measured blended variable cost rises above **$0.17/file**, increase prices/overage using the formula above.

---

## 5) Backend deployment recommendation (cost-aware)

For early production, deploy:
- **FastAPI API service** (always on)
- **Worker service** (can scale independently)

Use one platform for both (for simplicity), then split by workload later if needed.

Recommended setup:
1. Small always-on API instance.
2. Separate worker instance with autoscaling based on queue depth.
3. Keep Supabase managed; do not self-host Postgres initially.

This gives better cost control than a single oversized instance and lets heavy jobs scale without overpaying for idle API capacity.

---

## 6) Pricing operations checklist (monthly)

1. Measure:
   - Avg input/output tokens per file
   - Avg compute time per file
   - Supabase storage/egress growth
2. Recompute `blended_variable_cost_per_file`.
3. Verify overage >= `5x blended_variable_cost_per_file`.
4. Check gross margin by plan (Starter/Growth/Scale).
5. Adjust included limits or plan prices if margin drops below target.

---

## 7) Immediate next actions

1. Instrument cost telemetry per run (tokens, runtime, storage deltas).
2. Launch with the 3 hybrid tiers above.
3. Review actual margins after first full billing cycle and adjust overage first (before changing plan names/features).
