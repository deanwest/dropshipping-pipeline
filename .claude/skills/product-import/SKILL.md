---
name: product-import
description: Doc 3 — import all products from a source store via the Kopy Connector, then set them to draft for optimization
---

# Product Import (Doc 3)

1. Ask for the source **store link** if not provided.
2. Import all of that store's products into the user's store using the **Kopy Connector** (an MCP/app connection — if it is not connected, say so and stop; there is no CLI fallback for the import itself).
3. Set the imported products to **draft** so they can be optimized before going live. If the connector can't set status, do it with the CLI:
   `python tools/shopcli.py fetch --status active` → confirm scope is only the newly imported products (filter by vendor/tag if needed, e.g. `--query "vendor:X"`) → `python tools/shopcli.py push-backend --status draft`
4. Report the import count and remind the user the next steps in the pipeline are `/ad-optimize` (doc 4) and `/backend-update` (doc 5).

Never set pre-existing products to draft — verify the scope before pushing status.
