---
name: backend-update
description: Doc 5 — batch update Shopify products; rewrite titles/descriptions/SEO, adjust pricing, set status, apply seasonal tags via tools/shopcli.py
---

# Backend Update (Doc 5)

Batch-update products. All Shopify I/O goes through `python tools/shopcli.py` — never stream product data through the conversation.

## Brief (collect ALL before doing anything else)

- **Q1** Scope: All / Draft only / Active only / By Tag
- **Q2** Pricing: Keep / Increase 20% / Decrease 5% / Double
- **Q3** Minimum price: None / 19.99 / 29.99 / 39.99 / 49.99
- **Q4** Rounding: Nearest .99 / Nearest even .99 / Nearest 4.99-or-9.99 / None
- **Q5** Status: Active / Draft / Keep
- **Q6** Rewrite titles? (title + SEO title together)
- **Q7** Rewrite descriptions? (description + SEO description together)
- **Q8** Seasonal tag: Auto-infer per product / Tag all as [season] / Skip
- **Q9** Title format: Default / user-specified
- **Q10** Run mode: Manual / Scheduled

**Manual mode:** if brief incomplete, ask ONLY a numbered list of missing questions, nothing else, then stop. Once complete: confirm the store (`shopcli.py whoami`), read the brief back in one line, then ask "Test run on one product first, or full batch?" and STOP. On test: process exactly one product, show it, stop for approval; skip it in the full run.
**Scheduled mode:** never wait for input anywhere. State the store in one line. Incomplete brief = one-line error naming the missing question, then end. Never ask retry questions. Only a genuine tool error or zero-match scope halts.

## Steps

1. **Fetch:** `python tools/shopcli.py fetch [--status draft|active] [--tag X]`
   → `work/products.json`. Zero products = stop and report.
2. **Read compact data only:** `python tools/shopcli.py digest --json` (never Read work/products.json — it is large).
3. **Write `work/rewrites.json`** — one offline pass, an array of:
   `{"product_id": "gid://shopify/Product/...", "title"?, "descriptionHtml"?, "seo": {"title"?, "description"?}, "add_tags"?: ["summer"]}`
   Include title+seo.title only if Q6=rewrite; descriptionHtml+seo.description only if Q7=rewrite; add_tags only if Q8≠skip. Pricing and status are handled by CLI flags, not this file.

   **Content formats (hard limits — the CLI trims overflow, but write to fit):**
   - Title: short product name, <60 chars (e.g. `WaterBead Jacket`). Q9 custom format wins if given.
   - SEO title: `[Product Name] - [keyword-rich descriptor]`, <70 chars.
   - Description: exactly 3 paragraphs of 200–250 words each as `<p>`, then a `<ul><li>` spec list.
   - SEO description: one keyword-dense sentence, <160 chars.
   - Brand name: use the store name from `whoami`; if it's "My Store"-like, ask (Manual) / error (Scheduled).
   - Season tags: vocabulary is exactly `winter spring summer fall evergreen`. Auto-infer from title/type/description; `evergreen` when nothing seasonal is obvious. Tags are ADDED, never removed; existing tags untouched.
   - **Safety copy (always):** baby/kids/pet products get supervision disclaimers; pool/water products get drowning-prevention warnings; remove or soften medical/health claims.
4. **Push:** map the brief to flags —
   Q2: Increase 20% → `--price-multiply 1.2`; Decrease 5% → `0.95`; Double → `2.0`; Keep → omit.
   Q3 → `--price-min 19.99` etc. Q4: `.99 → --price-round 99`; even → `even99`; 4.99/9.99 → `499-999`.
   Q5 → `--status active|draft` (Keep → omit).
   `python tools/shopcli.py push-backend work/rewrites.json --price-multiply 1.2 --price-min 19.99 --price-round 99 --status active`
   (Test run: write a one-item rewrites file and push that first.)
5. **Verify:** `python tools/shopcli.py verify backend --file work/rewrites.json --sample 3`
6. **Summary:** counts of updated/failed, why, fix advice (errors in `work/errors.json`). Manual: offer retry. Scheduled: report and finish.

## Rules

- No per-product narration; progress lines under 50 chars.
- Never stop the batch for single-product errors.
- "No userErrors" ≠ verified — always run step 5.
- Context limit mid-run: note the last completed batch in `work/checkpoint.json`; user resumes with "Continue from batch N".
