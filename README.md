# Dropshipping Store Pipeline

Token-efficient implementation of the 7-document AI dropshipping stack. The
original docs were written to be pasted into a Claude chat, streaming every
product and GraphQL payload through the conversation. Here the deterministic
work (fetching, price math, char limits, batched mutations, verification)
lives in a Python CLI, and slim Claude Code skills load only the rules for the
step you're running. Claude's context is spent exclusively on judgment and
copywriting.

## One-time setup

1. **Shopify custom app** (admin → Settings → Apps and sales channels →
   Develop apps → Create an app). Grant Admin API scopes:
   `read_products, write_products, read_files, write_files, read_themes,
   write_themes, read_publications, write_publications`. Install it and copy
   the Admin API access token.
2. `copy .env.example .env` and fill in `SHOPIFY_STORE` + `SHOPIFY_ACCESS_TOKEN`.
   `.env` is gitignored — credentials never go in code or version control.
3. `pip install -r tools/requirements.txt` (official PyPI; only needed for
   MHTML parsing in the store builder).
4. Sanity check: `python tools/shopcli.py whoami` prints your store name.
5. For image steps: connect the **Higgsfield MCP** (with credits). For the
   product import: connect the **Kopy Connector**.

## The pipeline (execution order = doc number)

| # | Doc | How to run | LLM needed for |
|---|-----|-----------|----------------|
| 1 | Cover | reference only | — |
| 2 | Store Builder | `/store-builder` (interactive, once per store) | design judgment |
| 3 | Product Import | `/product-import` | — (Kopy Connector) |
| 4 | Ad Optimize | `/ad-optimize` | image prompts only |
| 5 | Backend Update | `/backend-update` | titles/descriptions/SEO copy |
| 6 | Product Page Builder | `/page-builder` | benefits/IWT/FAQ copy |
| 7 | Seasonal Sale | `/seasonal-sale` | nothing — pure CLI |

Each skill collects the doc's brief (including Manual vs Scheduled run mode),
drives `tools/shopcli.py`, and reports a short summary. Run artifacts land in
`work/` (gitignored): `products.json`, `rewrites.json`, `sale.json`,
`images.json`, `pages.json`, `errors.json`, `checkpoint.json`.

## The CLI

```
python tools/shopcli.py <command> -h
  whoami            confirm connected store
  fetch             products -> work/products.json (--status/--tag/--collection/--query)
  digest            compact summary (--json) so full dumps never enter context
  push-backend      rewrites + pricing flags (--price-multiply/-min/-round) + --status
  sale start|end    compare-at strikethrough sale by tag (--pct, --dry-run)
  attach-images     attach generated image URLs, --featured reorders to position 0
  ensure-metafields create the 22 doc-6 metafield definitions (idempotent)
  set-metafields    push page copy + IWT images, --assign-template
  theme-list / theme-pull / theme-push   draft-theme file access (live theme refused)
  extract-mhtml     doc-2 Appendix A: capture -> manifest + section fragments
  verify            spot-check backend|sale|featured actually landed
```

Everything supports the docs' own efficiency rules: batches of 10 aliased
mutations, only `userErrors` requested, throttle-aware retries, offline JSON
processing, sub-50-char progress lines, `--dry-run` on every write path.

## Safety rails

- Theme writes refuse the live theme unless `--allow-live` is passed.
- The sale command physically cannot change `price` — only `compareAtPrice`.
- Hard character limits (title <60, SEO title <70, SEO description <160) are
  enforced at push time.
- Every write path has a `verify` counterpart; "no userErrors" is never
  treated as proof.

## Scheduled runs

Docs 4–7 support Scheduled mode (no human present). Run them non-interactively,
e.g.: `claude -p "/backend-update Q1: Draft only, Q2: Increase 20%, ... Q10: Scheduled"`,
or via Claude Code's `/schedule` for recurring runs. To share this tool beyond
personal local use, contact the AI Engineering team (Tech-AI-Engineering@derivco.com)
first for guidance on secrets management and internal hosting.
