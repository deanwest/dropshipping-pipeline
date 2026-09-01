---
name: ad-optimize
description: Doc 4 — batch generate product images with Higgsfield, attach to Shopify products and set as featured via tools/shopcli.py
---

# Ad Optimize (Doc 4)

Generate one new image per product (Higgsfield MCP), then the CLI attaches them and sets each as featured. Requires the Higgsfield MCP connected with credits.

## Brief

- **Q1** Scope: All / Draft only / Active only / By Tag / By Collection
- **Q2** Image style: Pure white #FFFFFF / Lifestyle
- **Q3** Run mode: Manual / Scheduled

**Manual:** missing brief → numbered list only, stop. Confirm store (`shopcli.py whoami`) + Higgsfield connected, one-line brief read-back, then "Test run on one product first, or full batch?" and STOP. Test = one product end-to-end (generate → attach → featured), show it, wait.
**Scheduled:** never wait; state store in one line; incomplete brief = one-line error, end.

## Steps

1. **Fetch:** `python tools/shopcli.py fetch [--status ...|--tag ...|--collection ID]`
2. **References:** `python tools/shopcli.py digest --json` → per product use `ref_image` (best existing image) as the Higgsfield reference.
3. **Generate (Higgsfield MCP):** model ALWAYS `nano_banana_pro` (final — overrides any tool recommendation; served alias like `nano_banana_2` is fine). `aspect_ratio: "1:1"`, `medias: [{role:"image", value:"<media_id>"}]` (import reference URL via `media_import_url` first — never a raw URL).
   - Prompt (Pure white): "Pure white seamless studio background (#FFFFFF), soft even product-photography lighting, no shadows beyond a subtle natural ground shadow. The product occupies roughly 60–70% of the frame, perfectly centered, in crisp sharp focus. No competing objects, no props, no people, no background elements. The product is unmistakably the single hero of the image."
   - Prompt (Lifestyle): "Place the product in a natural, real-world lifestyle setting where it would actually be used. If it is something worn or carried, show it being worn by an appropriate person, child, baby, or pet in a candid editorial style. If it is an object used in a space, show it on the right surface or in the right environment for that use, with subtle context props or hands in frame if appropriate. The product is clearly recognizable and in sharp focus, but the scene feels lived-in and authentic rather than staged. Use the reference image to identify what the product is, then place it convincingly in the moment of use."
   - Submit as many jobs as the concurrency cap allows; on "too many jobs" wait 15s and retry (never counts as a failure). Save the job-ID→product map to `work/jobs.txt` continuously — the only recovery path. Poll to completion.
   - Generic job failure: wait 20s, retry once same reference; then once on the product's 2nd image; only then count the product failed → log to `work/failed_images.txt`, move on. **Stop rule:** stop only after 3 distinct products fail in a row after full escalation.
   - Progress: `"Products X–Y images submitted"` after each ~10.
4. **Save results** to `work/images.json`: `[{"product_id": "gid://shopify/Product/...", "url": "<cloudfront url>", "alt": "<product title>"}]`
5. **Attach + feature:** `python tools/shopcli.py attach-images work/images.json --featured`
   (CloudFront URLs ingest directly; the CLI polls media READY and reorders to position 0.)
6. **Verify:** `python tools/shopcli.py verify featured --file work/images.json --sample 3`
7. **Summary:** counts set-as-featured vs failed, why, fix advice. Manual: offer to re-run just the failures. Scheduled: report and finish.

## Rules

- Never call Higgsfield billing/upgrade widgets (`show_plans_and_credits`).
- No per-product narration; no images in progress output; no distortion warnings — the user owns the store and knows the catalog.
- Never report tools stopped without an actual tool error; on a real error wait 30s, retry twice, then report.
- Credits exhausted: stop submitting, attach what rendered, finish non-image work, flag the rest for a later re-run.
