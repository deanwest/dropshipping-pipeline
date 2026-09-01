---
name: seasonal-sale
description: Doc 7 — start or end a tag-scoped compare-at (strikethrough) sale via tools/shopcli.py; never touches the real selling price
---

# Seasonal Sale Price (Doc 7)

Fully deterministic — the CLI does everything. Never change `price`; only `compareAtPrice` is written (start: `price ÷ (1 − pct/100)` to the cent; end: cleared to null). Safe to re-run.

## Brief

- **Q1** Start sale / End sale
- **Q2** Tag(s) to target (products matching ANY listed tag are in scope)
- **Q3** Discount % (start only)
- **Q4** Run mode: Manual / Scheduled

**Manual:** missing brief items → ask ONLY a numbered list, stop. Then confirm store (`shopcli.py whoami`), one-line brief read-back, ask "Test run on one product first, or full batch?" and STOP. Test = run with `--dry-run`, show one product's computed compare-at prices, wait for approval.
**Scheduled:** never wait; state store in one line; incomplete brief = one-line error, end; never offer retries.

## Steps

1. `python tools/shopcli.py sale start --tags summer,evergreen --pct 20`
   or `python tools/shopcli.py sale end --tags summer`
   (add `--dry-run` for the manual test run). Zero matches = the CLI stops itself; relay that a mistyped tag is the likely cause.
2. `python tools/shopcli.py verify sale --file work/sale.json --sample 3`
   — confirms compare-at landed, exceeds price, and **price is unchanged**.
3. Summary: products/variants updated, skipped variants (nothing to end, or anchor not above price), failures from `work/errors.json`. Manual: offer retry. Scheduled: report and finish.

## Forbidden

- Changing variant `price` (the CLI never sends it — do not work around this).
- Rewriting titles/descriptions/status/tags or generating images here.
- Per-product narration; keep progress lines short.
