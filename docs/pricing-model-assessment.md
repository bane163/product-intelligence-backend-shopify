# Processing-credit pricing and rollout

Stockpile uses Shopify App Pricing with one monthly meter named
`processing_credits`. Merchants see credits; provider pages, tokens, runtime,
and storage remain internal cost inputs.

| Plan | Monthly price | Included credits | Overage |
|---|---:|---:|---:|
| Starter | $19 | 1,900 | $0.01/credit |
| Growth | $49 | 5,400 | $0.01/credit |
| Scale | $149 | 17,800 | $0.01/credit |

Credits reset on the monthly Shopify billing cycle and do not roll over. There
is no processing trial or annual option at launch. Overage starts disabled and
the merchant can set a hard monthly limit from $0 through $500.

## Cost conversion

Every paid operation is quoted and settled against the immutable rate card that
was effective when it was reserved:

```text
raw variable cost = model tokens + document-analysis pages + platform floor
credits = ceil(raw variable cost × 1.25 ÷ 0.30 × 100)
```

GPT-5.6 Luna v1 uses $0.20 per million input tokens, $0.02 per million cached
input tokens, and $1.20 per million output tokens. Cache writes cost 1.25 times
uncached input. Requests above 272,000 input tokens use the full-request 2x
input and 1.5x output multipliers. If cached-token telemetry is absent, all
input is conservatively priced as uncached.

Azure Document Intelligence's page rate and the calibrated operation platform
floors are deployment configuration. Credit enforcement must remain disabled
until the benchmark matrix confirms those inputs, complete paid-call capture,
quotes at or above actual settlement, and at least 70% modeled variable margin.

## Billing behavior

Unsupported, corrupt, and password-protected files are rejected before a
reservation. Successful paid calls settle only consumed work up to the quoted
maximum. Provider/platform failures release unused reservations; estimator
misses are absorbed. Shopify writes, uploads, and review actions are not metered
unless they invoke a paid provider.

Shopify subscription state comes from the Partner API and usage is delivered
through the App Events API outbox. `202 Accepted` means delivered but not yet
validated, so operations must reconcile local delivered totals against Shopify
aggregate usage and monitor Dev Dashboard validation failures.
