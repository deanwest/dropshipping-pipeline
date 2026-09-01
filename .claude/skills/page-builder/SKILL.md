---
name: page-builder
description: Doc 6 — build the metafield-driven universal-with-metafields product page template, then fill benefits/IWT/FAQ copy + Higgsfield images per product via tools/shopcli.py
---

# Product Page Builder (Doc 6)

Template once, then per-product: 3 benefits, 3 image-with-text (heading/body/image), 5 FAQs — all as `custom.*` metafields pushed by the CLI. The full spec (Appendix A benefits block Liquid, path details) lives in `docs/6. Product Page Builder.md`; read it ONLY when the template must be created (first manual run).

## Brief

Q1 scope (names/tag/collection/all) · Q2 count (Test With One / 50 / All / N) · Q3 template design (scratch / inspiration-universal / own: {name}) · Q4 image style (Lifestyle / Pure white / custom) · Q5 brand voice · Q6 store name · Q7 remove tag when done? · Q8 set Active when done? · Q9 Manual / Scheduled

**Manual:** missing brief → numbered list only, stop. Confirm store + Higgsfield, verify a draft theme exists (`shopcli.py theme-list` — role UNPUBLISHED), one-line read-back. Q2=Test With One → one product end-to-end, show the preview link, ask "How many next?", wait.
**Scheduled:** never wait. Template creation is MANUAL-ONLY — if `templates/product.universal-with-metafields.json` doesn't exist on the draft theme, one-line error telling the user to run manually once first. Q2=Test-With-One is malformed on a scheduled run → one-line error.

## Metafield convention (namespace `custom`, plain text only — NEVER rich_text)

benefit1..3 · iwt1..3_heading · iwt1..3_body · iwt1..3_image (file) · faq1..5_q · faq1..5_a

## Steps

1. **Definitions:** `python tools/shopcli.py ensure-metafields` (idempotent, creates all 22).
2. **Template (first manual run only).** Check: `python tools/shopcli.py theme-pull --theme <draft-id> templates/product.universal-with-metafields.json`. If it exists, skip creation entirely (ignore Q3). Otherwise read doc 6 Phase 1 + Appendix A and build it:
   - Pull the base per Q3 (default product template / `templates/product.inspiration-universal.json` / the named template) with `theme-pull`; never modify the base itself.
   - Honor the NESTING RULE: every product-column block goes inside the same parent as the theme's own Title/Price blocks.
   - Install the Appendix A benefits block (snippet + renderer branch + schema entry, idempotent), bind every field with `{{ product.metafields.custom.<key>.value }}`, IWT sections contain ONLY image+heading+body blocks, FAQ = exactly 5 bound rows, no email/pop-ups, buttons black/white, backgrounds pure white.
   - Push with `python tools/shopcli.py theme-push --theme <draft-id> local.json=templates/product.universal-with-metafields.json ...` (the CLI refuses live-theme writes). Pull back to confirm bindings.
3. **Scope:** `fetch` per Q1, `digest --json`; apply the Q2 count oldest-first, skipping products whose metafields are already fully set. Save the final list.
4. **Copy (offline, one pass)** for each product from its title/type/description:
   - 3 benefits, ≤8 words, concrete, no filler.
   - 3 IWT pairs: punchy header + 1–2 sentence body, Q5 voice, Q6 store name where natural.
   - 5 FAQs, all about the product (fit, materials, setup, use, care), positively framed; NEVER shipping/returns/delivery/refund/policy.
5. **Images (Higgsfield MCP):** 3 per product (one per IWT header), model ALWAYS `nano_banana_pro`, `aspect_ratio: "3:4"`, product's best image imported via `media_import_url` as first reference (+ any style refs). Lifestyle/pure-white prompts as in the ad-optimize skill, ending "No text at all"; lifestyle adds "Match the mood of the image-with-text header: <header>." Same retry/stop rules: 15s on rate-limit; 20s retry → 2nd reference → failed; stop after 3 distinct product failures; map jobs in `work/jobs.txt`.
6. **Push:** write `work/pages.json`:
   `[{"product_id": "...", "fields": {"benefit1": "...", "iwt1_heading": "...", "iwt1_body": "...", "faq1_q": "...", "faq1_a": "...", ...}, "images": {"iwt1_image": "<url>", "iwt2_image": "<url>", "iwt3_image": "<url>"}}]`
   Then: `python tools/shopcli.py set-metafields work/pages.json --assign-template universal-with-metafields`
   **Only include a product when ALL its text fields and ALL 3 images are ready** — never assign the template to a partially-filled product; incomplete products keep their Q7 tag and status so the next run picks them up.
7. **Finish (Q7/Q8):** for completed products write `work/finish.json` rewrites (`remove_tags` per Q7) and run `push-backend work/finish.json --status active` (Q8=Yes).
8. **Verify:** re-pull the template and confirm bindings; spot-check metafields via `fetch` on 2–3 completed products; preview `/products/<handle>?view=universal-with-metafields&preview_theme_id=<draft id>`. First template-build run only: view the preview in the browser if available.
9. **Summary:** template created/reused, products filled, images attached, tagged-out/activated counts, failures + reasons. ALWAYS end with a preview link to a product completed this run, remind that the template lives on the DRAFT theme, and confirm whether the selected products were assigned to it.

## Rules

- The user never creates a template, metafield, or binding by hand.
- One rejected binding: name that single field and continue — never fail the run.
- No per-product narration; progress under 50 chars; checkpoint to `work/checkpoint.json` near context limits.
