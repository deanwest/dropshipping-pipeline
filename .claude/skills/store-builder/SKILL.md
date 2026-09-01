---
name: store-builder
description: Doc 2 — replicate an inspiration store's home + product page onto the draft theme, then rebrand (images, copy, logo). Interactive, run once per store.
---

# Shopify Store Builder (Doc 2)

This is the one genuinely interactive, judgment-heavy doc in the stack, run once per store. The authoritative spec is `docs/2. Basic Shopify Store Builder 2.0.md` — **Read it in full when this skill is invoked and follow it exactly** (5 phases, hard user-response gates, STOCK-SAFE vs CUSTOM REPLICA classification, verification gates). This skill only maps its tooling onto this environment.

## Tooling substitutions

- **Appendix A (MHTML parsing)** is already implemented — do not rewrite it:
  `python tools/shopcli.py extract-mhtml <capture.mhtml> --outdir capture_sections`
  → `manifest.json`, `image_map.json`, per-section `NN_key.html` + `NN_key.css` (with the mandatory global CSS sweep and capture-viewport inference). Read `manifest.json` selectively; open a section's HTML/CSS fragment only when classifying or building that section. Never paste the raw capture into context.
- **Theme reads/writes:** prefer the Shopify MCP if connected; otherwise
  `python tools/shopcli.py theme-list` (pick the most recently updated UNPUBLISHED theme per the theme-selection rule),
  `theme-pull --theme <id> <paths...>`, and
  `theme-push --theme <id> LOCAL=themepath ...` (refuses the live theme by design — all writes go to the draft).
- **Images (Phases 3/5):** Higgsfield MCP, model always `nano_banana_pro`, references imported first. Import results into Shopify with `fileCreate` via the MCP, or list the CloudFront URLs and use theme settings per the doc.
- Maintain `build_state.json` and `open_issues` in the project root as the doc requires.

## Non-negotiables to carry from the doc

- Phase gating with hard stops: never continue past a question in the same turn.
- Only replicate stores on the SAME theme as the draft; the ThemeSpotter confirmation from the user is mandatory.
- All writes to the DRAFT theme only. Copy lock until Phase 4. Cleanup rules (no email capture, no pop-ups, vendor branding cleared). Product safety: never hardcode the model store's products/prices.
- open_issues gate: an entry must state why a CUSTOM REPLICA was impossible, not merely difficult.
- Keep user-facing messages simple and non-technical; send every scripted message verbatim at its exact point.
