#!/usr/bin/env python3
"""shopcli - deterministic heavy lifting for the dropshipping doc stack.

Talks straight to the Shopify Admin GraphQL API so product data never has to
stream through the LLM conversation. Claude Code skills drive this tool and
only handle the judgment/copywriting parts.

Setup:
  1. Shopify admin -> Settings -> Apps -> Develop apps -> create a custom app.
     Scopes: read_products, write_products, read_files, write_files,
             read_themes, write_themes, read_publications, write_publications.
  2. Put credentials in a .env file next to this repo root (gitignored):
       SHOPIFY_STORE=your-store-handle          (the *.myshopify.com prefix)
       SHOPIFY_ACCESS_TOKEN=shpat_xxx
       SHOPIFY_API_VERSION=2025-01              (optional)

Commands (run `python tools/shopcli.py <cmd> -h` for flags):
  fetch             pull in-scope products to work/products.json
  digest            print a compact per-product summary Claude can read
  push-backend      push rewrites.json (title/seo/desc/status/tags) + pricing
  sale              start/end a tag-scoped compare-at sale
  attach-images     attach generated image URLs to products, set featured
  ensure-metafields create the doc-6 metafield definitions (idempotent)
  set-metafields    push per-product page copy + iwt images, assign template
  theme-list        list themes with id/role/updatedAt
  theme-pull        download theme files to local disk
  theme-push        upsert local files into a (draft) theme
  extract-mhtml     parse a Chrome .mhtml capture into manifest + fragments
  verify            spot-check that backend/sale/featured work actually landed
"""

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"

# ---------------------------------------------------------------- env / http


def load_env():
    for envfile in (ROOT / ".env", Path.cwd() / ".env"):
        if envfile.is_file():
            for line in envfile.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_endpoint():
    store = os.environ.get("SHOPIFY_STORE")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if not store or not token:
        sys.exit("ERROR: set SHOPIFY_STORE and SHOPIFY_ACCESS_TOKEN in .env")
    ver = os.environ.get("SHOPIFY_API_VERSION", "2025-01")
    store = store.replace(".myshopify.com", "").strip("/")
    return f"https://{store}.myshopify.com/admin/api/{ver}/graphql.json", token


def gql(query, variables=None, max_retries=6):
    """POST one GraphQL request with throttle-aware retries."""
    url, token = api_endpoint()
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": token,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:500]}")
        errors = body.get("errors") or []
        throttled = any(
            (e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors
        )
        if throttled and attempt < max_retries - 1:
            time.sleep(4 * (attempt + 1))
            continue
        if errors:
            raise SystemExit("GraphQL errors: " + json.dumps(errors)[:800])
        return body["data"]
    raise SystemExit("Throttled repeatedly; giving up.")


def shop_name():
    return gql("{ shop { name url } }")["shop"]


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def log_errors(entries):
    if not entries:
        return
    f = WORK / "errors.json"
    existing = load_json(f) if f.is_file() else []
    existing.extend(entries)
    save_json(f, existing)


def gid_num(gid):
    return gid.rsplit("/", 1)[-1]


# -------------------------------------------------------------------- fetch

PRODUCT_FIELDS = """
  id
  title
  handle
  descriptionHtml
  vendor
  productType
  tags
  status
  templateSuffix
  seo { title description }
  featuredImage { url }
  images(first: 5) { edges { node { url } } }
  variants(first: 100) {
    edges { node { id title price compareAtPrice } }
  }
"""


def build_query_filter(args):
    parts = []
    if getattr(args, "status", None):
        parts.append(f"status:{args.status.lower()}")
    for tag in getattr(args, "tag", None) or []:
        parts.append(f"tag:{tag}")
    if getattr(args, "query", None):
        parts.append(args.query)
    return " AND ".join(parts) if parts else None


def fetch_products(query_filter=None, collection=None, limit=None):
    out, cursor = [], None
    while True:
        if collection:
            cid = collection if collection.startswith("gid://") else (
                "gid://shopify/Collection/" + collection
            )
            q = f"""
            query($id: ID!, $after: String) {{
              collection(id: $id) {{
                products(first: 50, after: $after) {{
                  edges {{ node {{ {PRODUCT_FIELDS} }} }}
                  pageInfo {{ hasNextPage endCursor }}
                }}
              }}
            }}"""
            data = gql(q, {"id": cid, "after": cursor})
            conn = (data.get("collection") or {}).get("products")
            if conn is None:
                sys.exit("ERROR: collection not found")
        else:
            q = f"""
            query($query: String, $after: String) {{
              products(first: 50, after: $after, query: $query) {{
                edges {{ node {{ {PRODUCT_FIELDS} }} }}
                pageInfo {{ hasNextPage endCursor }}
              }}
            }}"""
            data = gql(q, {"query": query_filter, "after": cursor})
            conn = data["products"]
        for e in conn["edges"]:
            n = e["node"]
            n["images"] = [i["node"]["url"] for i in n["images"]["edges"]]
            n["variants"] = [v["node"] for v in n["variants"]["edges"]]
            out.append(n)
            if limit and len(out) >= limit:
                return out
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        cursor = conn["pageInfo"]["endCursor"]


def cmd_fetch(args):
    qf = build_query_filter(args)
    products = fetch_products(qf, args.collection, args.limit)
    path = save_json(args.out, products)
    print(f"Fetch complete ({len(products)} products) -> {path}")


def cmd_digest(args):
    products = load_json(args.file)
    if args.json:
        compact = [
            {
                "id": p["id"],
                "title": p["title"],
                "type": p.get("productType"),
                "status": p["status"],
                "tags": p["tags"],
                "price": p["variants"][0]["price"] if p["variants"] else None,
                "variants": len(p["variants"]),
                "desc_snippet": re.sub(r"<[^>]+>", " ", p.get("descriptionHtml") or "")[
                    : args.snippet
                ].strip(),
                "ref_image": (p.get("featuredImage") or {}).get("url")
                or (p["images"][0] if p["images"] else None),
            }
            for p in products
        ]
        print(json.dumps(compact, indent=1))
        return
    for p in products:
        price = p["variants"][0]["price"] if p["variants"] else "-"
        print(
            f"{gid_num(p['id'])} | {p['status']:<6} | {price:>8} | "
            f"{len(p['variants'])}v | {','.join(p['tags'])[:40]:<40} | {p['title'][:60]}"
        )
    print(f"-- {len(products)} products")


# ------------------------------------------------------------------ pricing

TWO = Decimal("0.01")


def q2(d):
    return Decimal(d).quantize(TWO, rounding=ROUND_HALF_UP)


def round_price(price, mode):
    p = Decimal(price)
    if mode in (None, "none"):
        return q2(p)
    base = int(p)
    if mode == "99":
        cands = [Decimal(n) + Decimal("0.99") for n in range(base - 2, base + 3)]
    elif mode == "even99":
        cands = [
            Decimal(n) + Decimal("0.99")
            for n in range(base - 4, base + 5)
            if n % 2 == 0
        ]
    elif mode == "499-999":
        cands = [
            Decimal(n) + Decimal("0.99")
            for n in range(base - 11, base + 12)
            if n % 5 == 4
        ]
    else:
        sys.exit(f"ERROR: unknown rounding mode {mode}")
    cands = [c for c in cands if c > 0]
    return q2(min(cands, key=lambda c: (abs(c - p), -c)))


def compute_price(current, multiply=None, minimum=None, rounding=None):
    p = Decimal(current)
    if multiply:
        p = p * Decimal(str(multiply))
    if minimum and p < Decimal(str(minimum)):
        p = Decimal(str(minimum))
    p = round_price(p, rounding)
    if minimum and p < Decimal(str(minimum)):
        # rounding dipped below the floor; step candidates upward
        while p < Decimal(str(minimum)):
            p = round_price(p + Decimal("1"), rounding)
    return str(q2(p))


# --------------------------------------------------------- text hard limits

LIMITS = {"title": 60, "seo_title": 70, "seo_description": 160}


def trim(text, limit):
    text = (text or "").strip()
    if len(text) < limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-")


# ------------------------------------------------------------- push-backend


def batched(seq, n=10):
    for i in range(0, len(seq), n):
        yield i, seq[i : i + n]


def cmd_push_backend(args):
    rewrites = load_json(args.rewrites) if args.rewrites else []
    products = {p["id"]: p for p in load_json(args.products)}
    if not rewrites:  # pricing/status-only run over every fetched product
        rewrites = [{"product_id": pid} for pid in products]

    jobs = []
    for r in rewrites:
        pid = r.get("product_id") or r.get("id")
        live = products.get(pid)
        if not live:
            print(f"skip {pid}: not in {args.products}")
            continue
        inp = {"id": pid}
        if "title" in r:
            inp["title"] = trim(r["title"], LIMITS["title"])
        if "descriptionHtml" in r:
            inp["descriptionHtml"] = r["descriptionHtml"]
        seo = {}
        if r.get("seo", {}).get("title"):
            seo["title"] = trim(r["seo"]["title"], LIMITS["seo_title"])
        if r.get("seo", {}).get("description"):
            seo["description"] = trim(r["seo"]["description"], LIMITS["seo_description"])
        if seo:
            inp["seo"] = seo
        status = r.get("status") or (args.status.upper() if args.status else None)
        if status and status != "KEEP":
            inp["status"] = status
        if r.get("add_tags") or r.get("remove_tags"):
            tags = list(dict.fromkeys(live["tags"]))  # dedupe, keep order
            for t in r.get("add_tags", []):
                if t not in tags:
                    tags.append(t)
            tags = [t for t in tags if t not in set(r.get("remove_tags", []))]
            inp["tags"] = tags
        variants = None
        if args.price_multiply or args.price_min or args.price_round:
            variants = [
                {
                    "id": v["id"],
                    "price": compute_price(
                        v["price"], args.price_multiply, args.price_min, args.price_round
                    ),
                }
                for v in live["variants"]
            ]
        elif r.get("variants"):
            variants = r["variants"]
        jobs.append({"pid": pid, "input": inp, "variants": variants})

    if args.dry_run:
        for j in jobs[:5]:
            print(json.dumps(j, indent=1))
        print(f"DRY RUN: {len(jobs)} products would be updated")
        return

    errors = []
    for start, batch in batched(jobs, 10):
        var_defs, calls, variables = [], [], {}
        for i, j in enumerate(batch):
            needs_product = len(j["input"]) > 1
            if needs_product:
                var_defs.append(f"$p{i}: ProductUpdateInput!")
                calls.append(f"u{i}: productUpdate(product: $p{i}) "
                             "{ userErrors { field message } }")
                variables[f"p{i}"] = j["input"]
            if j["variants"]:
                var_defs.append(f"$id{i}: ID!")
                var_defs.append(f"$v{i}: [ProductVariantsBulkInput!]!")
                calls.append(
                    f"w{i}: productVariantsBulkUpdate(productId: $id{i}, variants: $v{i}) "
                    "{ userErrors { field message } }"
                )
                variables[f"id{i}"] = j["pid"]
                variables[f"v{i}"] = j["variants"]
        if not calls:
            continue
        m = "mutation(" + ", ".join(var_defs) + ") {\n" + "\n".join(calls) + "\n}"
        data = gql(m, variables)
        for alias, res in data.items():
            for ue in res.get("userErrors") or []:
                idx = int(re.sub(r"\D", "", alias))
                errors.append({"product": batch[idx]["pid"], "error": ue})
        print(f"Products {start + 1}-{start + len(batch)} backend done")
    log_errors(errors)
    print(f"Done: {len(jobs)} products, {len(errors)} errors"
          + (" (see work/errors.json)" if errors else ""))


# --------------------------------------------------------------------- sale


def cmd_sale(args):
    tags_filter = " OR ".join(f"tag:{t}" for t in args.tags)
    products = fetch_products(tags_filter)
    if not products:
        sys.exit("Zero products match the tag filter - nothing to do. "
                 "A likely cause is a mistyped tag.")
    save_json(WORK / "products.json", products)

    plan, skipped = [], 0
    for p in products:
        variants = []
        for v in p["variants"]:
            if args.action == "start":
                price = Decimal(v["price"])
                cap = q2(price / (Decimal(1) - Decimal(args.pct) / Decimal(100)))
                if cap <= price:
                    skipped += 1
                    continue
                variants.append({"id": v["id"], "price": v["price"],
                                 "compareAtPrice": str(cap)})
            else:  # end
                if v.get("compareAtPrice") is None:
                    skipped += 1
                    continue
                variants.append({"id": v["id"], "price": v["price"],
                                 "compareAtPrice": None})
        if variants:
            plan.append({"product_id": p["id"], "variants": variants})
    save_json(WORK / "sale.json", plan)
    print(f"Sale preparation complete ({len(plan)} products, {skipped} variants skipped)")

    if args.dry_run:
        for j in plan[:3]:
            print(json.dumps(j, indent=1))
        print("DRY RUN: nothing pushed")
        return

    errors = []
    for start, batch in batched(plan, 10):
        var_defs, calls, variables = [], [], {}
        for i, j in enumerate(batch):
            var_defs += [f"$id{i}: ID!", f"$v{i}: [ProductVariantsBulkInput!]!"]
            calls.append(
                f"v{i}: productVariantsBulkUpdate(productId: $id{i}, variants: $v{i}) "
                "{ userErrors { field message } }"
            )
            variables[f"id{i}"] = j["product_id"]
            variables[f"v{i}"] = [
                {"id": v["id"], "compareAtPrice": v["compareAtPrice"]}
                for v in j["variants"]
            ]
        m = "mutation(" + ", ".join(var_defs) + ") {\n" + "\n".join(calls) + "\n}"
        data = gql(m, variables)
        for alias, res in data.items():
            for ue in res.get("userErrors") or []:
                idx = int(re.sub(r"\D", "", alias))
                errors.append({"product": batch[idx]["product_id"], "error": ue})
        print(f"Products {start + 1}-{start + len(batch)} sale done")
    log_errors(errors)
    print(f"Done: {len(plan)} products, {len(errors)} errors")


# ------------------------------------------------------------ attach-images


def poll_media_ready(pid, want_stem, tries=12, delay=5):
    q = """
    query($id: ID!) {
      product(id: $id) {
        media(first: 100) {
          nodes {
            id
            ... on MediaImage { status image { url } }
          }
        }
      }
    }"""
    for _ in range(tries):
        nodes = gql(q, {"id": pid})["product"]["media"]["nodes"]
        images = [n for n in nodes if "image" in n]
        match = None
        for n in images:
            url = (n.get("image") or {}).get("url") or ""
            if want_stem and want_stem.lower() in url.lower():
                match = n
        if match is None and images:
            last = images[-1]
            if last.get("status") == "READY" and last.get("image"):
                match = last
        if match and match.get("status") == "READY":
            return match["id"], nodes[0]["id"] if nodes else None
        time.sleep(delay)
    return None, None


def cmd_attach_images(args):
    entries = load_json(args.map)  # [{product_id, url, alt?}]
    errors = []
    for start, batch in batched(entries, 10):
        var_defs, calls, variables = [], [], {}
        for i, e in enumerate(batch):
            var_defs += [f"$id{i}: ID!", f"$m{i}: [CreateMediaInput!]!"]
            calls.append(
                f"a{i}: productUpdate(product: {{id: $id{i}}}, media: $m{i}) "
                "{ userErrors { field message } }"
            )
            variables[f"id{i}"] = e["product_id"]
            variables[f"m{i}"] = [{
                "originalSource": e["url"],
                "mediaContentType": "IMAGE",
                "alt": e.get("alt", "Product image"),
            }]
        if args.dry_run:
            print(f"DRY RUN batch {start + 1}-{start + len(batch)}")
            continue
        m = "mutation(" + ", ".join(var_defs) + ") {\n" + "\n".join(calls) + "\n}"
        data = gql(m, variables)
        for alias, res in data.items():
            for ue in res.get("userErrors") or []:
                idx = int(re.sub(r"\D", "", alias))
                errors.append({"product": batch[idx]["product_id"], "error": ue})
        print(f"Products {start + 1}-{start + len(batch)} images attached")
    if args.dry_run:
        return

    if args.featured:
        for e in entries:
            stem = Path(e["url"].split("?")[0]).stem[:40]
            media_id, first_id = poll_media_ready(e["product_id"], stem)
            if not media_id:
                errors.append({"product": e["product_id"],
                               "error": "new media never became READY"})
                continue
            if media_id == first_id:
                continue  # already featured
            data = gql(
                """mutation($id: ID!, $moves: [MoveInput!]!) {
                     productReorderMedia(id: $id, moves: $moves) {
                       userErrors { field message } } }""",
                {"id": e["product_id"],
                 "moves": [{"id": media_id, "newPosition": "0"}]},
            )
            for ue in data["productReorderMedia"]["userErrors"]:
                errors.append({"product": e["product_id"], "error": ue})
        print("Featured images set")
    log_errors(errors)
    print(f"Done: {len(entries)} products, {len(errors)} errors")


# --------------------------------------------------- metafields (doc 6)

METAFIELD_DEFS = (
    [(f"benefit{i}", "single_line_text_field") for i in (1, 2, 3)]
    + [(f"iwt{i}_heading", "single_line_text_field") for i in (1, 2, 3)]
    + [(f"iwt{i}_body", "multi_line_text_field") for i in (1, 2, 3)]
    + [(f"iwt{i}_image", "file_reference") for i in (1, 2, 3)]
    + [(f"faq{i}_q", "single_line_text_field") for i in range(1, 6)]
    + [(f"faq{i}_a", "multi_line_text_field") for i in range(1, 6)]
)


def cmd_ensure_metafields(args):
    q = """
    { metafieldDefinitions(first: 100, ownerType: PRODUCT, namespace: "custom") {
        nodes { key type { name } } } }"""
    existing = {n["key"] for n in gql(q)["metafieldDefinitions"]["nodes"]}
    missing = [(k, t) for k, t in METAFIELD_DEFS if k not in existing]
    if not missing:
        print("All 22 metafield definitions already exist")
        return
    for k, t in missing:
        data = gql(
            """mutation($def: MetafieldDefinitionInput!) {
                 metafieldDefinitionCreate(definition: $def) {
                   userErrors { field message } } }""",
            {"def": {"name": k, "namespace": "custom", "key": k,
                     "type": t, "ownerType": "PRODUCT"}},
        )
        for ue in data["metafieldDefinitionCreate"]["userErrors"]:
            print(f"  {k}: {ue['message']}")
    print(f"Created {len(missing)} metafield definitions "
          f"({len(existing)} already existed)")


def file_create_images(urls_with_alt):
    """fileCreate a batch of external image URLs; return url -> MediaImage GID."""
    if not urls_with_alt:
        return {}
    files = [{"originalSource": u, "contentType": "IMAGE", "alt": a}
             for u, a in urls_with_alt]
    data = gql(
        """mutation($files: [FileCreateInput!]!) {
             fileCreate(files: $files) {
               files { id fileStatus }
               userErrors { field message } } }""",
        {"files": files},
    )
    for ue in data["fileCreate"]["userErrors"]:
        print(f"  fileCreate: {ue['message']}")
    ids = [f["id"] for f in data["fileCreate"]["files"]]
    # poll until READY
    for _ in range(15):
        nodes = gql(
            """query($ids: [ID!]!) { nodes(ids: $ids) {
                 ... on MediaImage { id fileStatus } } }""",
            {"ids": ids},
        )["nodes"]
        if all(n and n.get("fileStatus") == "READY" for n in nodes):
            break
        time.sleep(4)
    return {u: gid for (u, _), gid in zip(urls_with_alt, ids)}


def cmd_set_metafields(args):
    """Input file: [{product_id, fields: {key: value}, images: {key: url}}]"""
    items = load_json(args.file)
    all_urls = []
    for it in items:
        for key, url in (it.get("images") or {}).items():
            all_urls.append((url, f"{key} image"))
    url_to_gid = file_create_images(list(dict.fromkeys(all_urls)))

    errors = []
    for it in items:
        pid = it["product_id"]
        metafields = [
            {"ownerId": pid, "namespace": "custom", "key": k,
             "type": ("multi_line_text_field" if k.endswith(("_body", "_a"))
                      else "single_line_text_field"),
             "value": str(v)}
            for k, v in (it.get("fields") or {}).items()
        ]
        for k, url in (it.get("images") or {}).items():
            gid = url_to_gid.get(url)
            if not gid:
                errors.append({"product": pid, "error": f"no file id for {k}"})
                continue
            metafields.append({"ownerId": pid, "namespace": "custom", "key": k,
                               "type": "file_reference", "value": gid})
        if args.dry_run:
            print(f"DRY RUN {pid}: {len(metafields)} metafields")
            continue
        for start in range(0, len(metafields), 25):
            data = gql(
                """mutation($m: [MetafieldsSetInput!]!) {
                     metafieldsSet(metafields: $m) {
                       userErrors { field message } } }""",
                {"m": metafields[start : start + 25]},
            )
            for ue in data["metafieldsSet"]["userErrors"]:
                errors.append({"product": pid, "error": ue})
        if args.assign_template:
            data = gql(
                """mutation($p: ProductUpdateInput!) {
                     productUpdate(product: $p) {
                       userErrors { field message } } }""",
                {"p": {"id": pid, "templateSuffix": args.assign_template}},
            )
            for ue in data["productUpdate"]["userErrors"]:
                errors.append({"product": pid, "error": ue})
        print(f"{gid_num(pid)} metafields set")
    log_errors(errors)
    print(f"Done: {len(items)} products, {len(errors)} errors")


# -------------------------------------------------------------------- theme


def cmd_theme_list(args):
    nodes = gql(
        "{ themes(first: 50) { nodes { id name role updatedAt } } }"
    )["themes"]["nodes"]
    for t in nodes:
        print(f"{gid_num(t['id']):>16} | {t['role']:<12} | "
              f"{t['updatedAt']} | {t['name']}")


def theme_gid(theme_id):
    return theme_id if theme_id.startswith("gid://") else (
        "gid://shopify/OnlineStoreTheme/" + theme_id
    )


def theme_role(tid):
    nodes = gql("{ themes(first: 50) { nodes { id role } } }")["themes"]["nodes"]
    for t in nodes:
        if t["id"] == tid:
            return t["role"]
    sys.exit(f"ERROR: theme {tid} not found")


def cmd_theme_pull(args):
    tid = theme_gid(args.theme)
    q = """
    query($id: ID!, $filenames: [String!]!) {
      theme(id: $id) {
        files(filenames: $filenames, first: 50) {
          nodes {
            filename
            body { ... on OnlineStoreThemeFileBodyText { content } }
          }
        }
      }
    }"""
    data = gql(q, {"id": tid, "filenames": args.paths})
    outdir = Path(args.outdir)
    got = set()
    for n in data["theme"]["files"]["nodes"]:
        content = (n.get("body") or {}).get("content")
        if content is None:
            print(f"  {n['filename']}: not a text file, skipped")
            continue
        dest = outdir / n["filename"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        got.add(n["filename"])
        print(f"  pulled {n['filename']}")
    for p in set(args.paths) - got:
        print(f"  MISSING on theme: {p}")


def cmd_theme_push(args):
    tid = theme_gid(args.theme)
    role = theme_role(tid)
    if role == "MAIN" and not args.allow_live:
        sys.exit("REFUSED: that is the LIVE theme. All writes go to a draft "
                 "theme. Pass --allow-live only if you truly mean it.")
    files = []
    for pair in args.pairs:
        local, _, remote = pair.partition("=")
        if not remote:
            sys.exit(f"ERROR: pair must be LOCALFILE=theme/path.liquid ({pair})")
        files.append({
            "filename": remote,
            "body": {"type": "TEXT",
                     "value": Path(local).read_text(encoding="utf-8")},
        })
    data = gql(
        """mutation($id: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!) {
             themeFilesUpsert(themeId: $id, files: $files) {
               upsertedThemeFiles { filename }
               userErrors { field message } } }""",
        {"id": tid, "files": files},
    )
    for ue in data["themeFilesUpsert"]["userErrors"]:
        print(f"  userError: {ue}")
    for f in data["themeFilesUpsert"]["upsertedThemeFiles"] or []:
        print(f"  upserted {f['filename']}")


# ------------------------------------------------------------ extract-mhtml


def cmd_extract_mhtml(args):
    """Appendix A of doc 2: MHTML -> manifest.json + per-section fragments."""
    import email
    from email import policy

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("ERROR: pip install beautifulsoup4 (official PyPI) first")

    src = Path(args.capture)
    raw = src.read_text(encoding="utf-8", errors="ignore")
    if not raw.lstrip().startswith(("From:", "MIME-Version", "Snapshot-Content-Location")):
        sys.exit("REJECTED: not an .mhtml capture; ask for a Chrome "
                 "'Webpage, Single File' re-save")

    msg = email.message_from_string(raw, policy=policy.default)
    html, css_records, css_by_ref, image_map = None, [], {}, {}
    for p in msg.walk():
        ct = p.get_content_type()
        loc = (p.get("Content-Location", "") or "").strip()
        cid = (p.get("Content-ID", "") or "").strip("<>")
        if ct == "text/html" and html is None:
            html = p.get_content()
        elif ct == "text/css" and "shopifycloud" not in loc and "/admin" not in loc:
            txt = p.get_content()
            css_records.append({"location": loc, "cid": cid, "text": txt})
            if loc:
                css_by_ref[loc] = txt
            if cid:
                css_by_ref[f"cid:{cid}"] = txt
        elif ct.startswith("image/"):
            if cid:
                image_map[f"cid:{cid}"] = loc
            if loc:
                image_map[loc] = loc
    if not html:
        sys.exit("REJECTED: no HTML part")

    soup = BeautifulSoup(html, "html.parser")
    if not soup.select(".shopify-section"):
        sys.exit("REJECTED: no shopify-section elements - the model store is "
                 "not built with Shopify. Ask for a different store.")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    cssdir = outdir / "css"
    cssdir.mkdir(exist_ok=True)
    for i, rec in enumerate(css_records):
        (cssdir / f"ext_{i:03d}.css").write_text(rec["text"], encoding="utf-8")

    all_css = "\n".join(r["text"] for r in css_records) + "\n" + "\n".join(
        s.get_text() for s in soup.find_all("style")
    )
    theme = re.search(r"Shopify\.theme\s*=\s*(\{.*?\});", html, re.S)
    fonts = re.findall(
        r'/([a-z0-9_-]+_[nio]\d)(?:\.[a-z0-9]+)?\.woff2(?:[?#][^)"\']*)?', all_css, re.I
    )
    font_faces = []
    for block in re.findall(r"@font-face\s*\{(.*?)\}", all_css, re.S | re.I):
        def grab(pat):
            m = re.search(pat, block, re.I)
            return m.group(1).strip() if m else None
        font_faces.append({
            "family": grab(r'font-family\s*:\s*["\']?([^;"\']+)'),
            "weight": grab(r"font-weight\s*:\s*([^;]+);"),
            "style": grab(r"font-style\s*:\s*([^;]+);"),
            "handle": grab(r"/([a-z0-9_-]+_[nio]\d)(?:\.[a-z0-9]+)?\.woff2"),
        })
    css_vars = dict(re.findall(
        r"(--(?:font-[a-z-]+|page-width|color-[a-z-]+))\s*:\s*([^;]+);", all_css
    ))

    def infer_capture_width():
        desktop = len(soup.select(
            '[class*="-col-desktop"], [class*="--desktop"], [class*="large-up-hide"]'))
        mobile = len(soup.select('[class*="small-hide"], [class*="medium-hide"]'))
        pw = css_vars.get("--page-width", "").strip()
        m = re.match(r"([\d.]+)rem", pw)
        return {"desktop_class_markers": desktop, "mobile_hide_markers": mobile,
                "page_width_px": float(m.group(1)) * 10 if m else None,
                "likely_desktop": desktop > 0}

    GENERIC = ("section-template--", "section-sections--", "shopify-section",
               "color-scheme-")
    SEMANTIC = {
        "multicolumn": "multicolumn", "rich-text": "rich-text",
        "newsletter": "newsletter", "slideshow": "slideshow",
        "multirow": "multirow", "featured-collection": "featured-collection",
        "collapsible-content": "collapsible-content",
        "image-with-text": "image-with-text",
        "announcement-bar": "announcement-bar", "footer": "footer",
        "header": "header",
    }

    def source_key(el):
        sid = el.get("id", "")
        m = re.search(r"__(.+)$", sid)
        return m.group(1) if m else sid.replace("shopify-section-", "")

    def base_type(el):
        for node in [el] + el.find_all(True, limit=120):
            for c in node.get("class", []):
                if any(c.startswith(p) for p in GENERIC):
                    continue
                if c.startswith("section-"):
                    cand = c[len("section-"):]
                    if cand and not cand.startswith(("template--", "sections--")):
                        return cand
                for marker, typ in SEMANTIC.items():
                    if c == marker or c.startswith((marker + "__", marker + "--")):
                        return typ
            classes = set(node.get("class", []))
            if "banner" in classes and node.find(class_="banner__media"):
                return "image-banner"
        return None

    def resolve_image(im):
        for a in ("src", "data-src", "srcset", "data-srcset"):
            v = (im.get(a) or "").strip()
            if not v:
                continue
            first = v.split(",")[0].strip().split(" ")[0]
            if first in image_map:
                return image_map[first]
            if first.startswith("http"):
                return first
        return ""

    def linked_css(el):
        links, chunks = [], []
        for lk in el.find_all("link"):
            rel = lk.get("rel")
            rel = " ".join(rel) if isinstance(rel, list) else (rel or "")
            if "stylesheet" not in rel:
                continue
            href = (lk.get("href") or "").strip()
            if href:
                links.append(href)
                if href in css_by_ref:
                    chunks.append(css_by_ref[href])
        chunks += [s.get_text() for s in el.find_all("style")]
        return links, "\n\n".join(chunks)

    RULE_RE = re.compile(
        r"(@media[^{]+\{(?:[^{}]*\{[^{}]*\}\s*)*\})|([^{}@]+\{[^{}]*\})", re.S
    )

    def class_tokens(el):
        toks = set()
        for node in el.find_all(True):
            toks.update(node.get("class", []))
            if node.name and "-" in node.name:
                toks.add(node.name)
        return {t for t in toks if len(t) > 5 and not t.startswith(
            ("color-scheme-", "shopify-", "section-template--",
             "section-sections--", "grid--", "scroll-trigger"))}

    def global_css_sweep(el):
        sid, key, toks = el.get("id", ""), source_key(el), class_tokens(el)
        hits, seen, out = [], set(), []
        for rec in css_records:
            for m in RULE_RE.finditer(rec["text"]):
                rule = m.group(0)
                sel = rule.split("{", 1)[0]
                if (sid and sid in rule) or (key and key in rule):
                    hits.append(rule)
                    continue
                probe = rule if rule.lstrip().startswith("@media") else sel
                for t in toks:
                    if "." + t in probe or ("<" + t) in probe or re.search(
                        r"(^|[\s,>+~(])" + re.escape(t) + r"([\s,{:.\[)]|$)", probe
                    ):
                        hits.append(rule)
                        break
        for h in hits:
            k = h.strip()
            if k not in seen:
                seen.add(k)
                out.append(k)
        return "\n\n".join(out)

    def custom_elements(el):
        return sorted({n.name for n in el.find_all(True)
                       if "-" in n.name and n.name != "shopify-payment-terms"})

    def behavior_hints(el):
        s = str(el).lower()
        needles = {
            "slider": ("slider-component", "slider--desktop", "slider__slide"),
            "sticky": ("sticky-header", "position: sticky"),
            "details": ("<details", "accordion"),
            "video": ("<video", "banner-video"),
            "tabs": ('role="tab"', "tablist"),
        }
        return [k for k, ns in needles.items() if any(n in s for n in ns)]

    sections = []
    for i, el in enumerate(soup.select(".shopify-section")):
        key = source_key(el)
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", key)[:80] or f"section_{i}"
        links, scoped = linked_css(el)
        swept = global_css_sweep(el)
        combined = (scoped + "\n\n/* --- global sweep --- */\n\n" + swept).strip()
        (outdir / f"{i:02d}_{safe}.html").write_text(str(el), encoding="utf-8")
        (outdir / f"{i:02d}_{safe}.css").write_text(combined, encoding="utf-8")
        src_txt = str(el)[:200000]
        app_content = bool(re.search(
            r"pagefly|gempages|shogun|replo|ecomposer|aigenblock|"
            r"shopify-app-block|instafeed|swym|judge\.me|loox|yotpo",
            src_txt, re.I))
        sections.append({
            "order": i,
            "section_id": el.get("id", ""),
            "source_key": key,
            "classes": el.get("class", []),
            "base_section_type": base_type(el),
            "html_file": f"{i:02d}_{safe}.html",
            "section_css_file": f"{i:02d}_{safe}.css",
            "linked_stylesheets": links,
            "global_css_chars": len(swept),
            "class_tokens": sorted(class_tokens(el))[:60],
            "custom_elements": custom_elements(el),
            "behavior_hints": behavior_hints(el),
            "app_content": app_content,
            "headings": [[h.name, h.get_text(" ", strip=True)]
                         for h in el.find_all(re.compile("^h[1-6]$"))],
            "images": [{"src": resolve_image(im), "alt": im.get("alt", ""),
                        "width": im.get("width"), "height": im.get("height")}
                       for im in el.find_all("img")],
            "svg_count": len(el.find_all("svg")),
        })

    manifest = {
        "theme": theme.group(1) if theme else None,
        "capture_viewport": infer_capture_width(),
        "fonts": sorted(set(fonts)),
        "font_faces": font_faces,
        "css_vars": css_vars,
        "sections": sections,
    }
    save_json(outdir / "manifest.json", manifest)
    save_json(outdir / "image_map.json", image_map)
    print(f"{len(sections)} sections, "
          f"{sum(s['app_content'] for s in sections)} with app markers, "
          f"{sum(1 for v in image_map.values() if v)} images with CDN URLs, "
          f"viewport={manifest['capture_viewport']}, "
          f"fonts={sorted(set(fonts))}")
    print(f"-> {outdir}/manifest.json (read the compact table, not the fragments)")


# ------------------------------------------------------------------- verify


def sample_products(ids, n):
    ids = ids[:n] if n else ids
    q = f"""query($ids: [ID!]!) {{ nodes(ids: $ids) {{
        ... on Product {{ {PRODUCT_FIELDS} }} }} }}"""
    return [p for p in gql(q, {"ids": ids})["nodes"] if p]


def cmd_verify(args):
    problems = []
    if args.what == "backend":
        rewrites = load_json(args.file)
        by_id = {r.get("product_id") or r.get("id"): r for r in rewrites}
        for p in sample_products(list(by_id), args.sample):
            r = by_id[p["id"]]
            if "title" in r and p["title"] != trim(r["title"], 60):
                problems.append(f"{gid_num(p['id'])}: title mismatch")
            if r.get("seo", {}).get("title") and (p["seo"]["title"] or "") != trim(
                r["seo"]["title"], 70
            ):
                problems.append(f"{gid_num(p['id'])}: seo title mismatch")
            if r.get("status") and r["status"] != "KEEP" and p["status"] != r["status"]:
                problems.append(f"{gid_num(p['id'])}: status={p['status']}")
            for t in r.get("add_tags", []):
                if t not in p["tags"]:
                    problems.append(f"{gid_num(p['id'])}: missing tag {t}")
            if len(p["seo"]["title"] or "") > 70 or len(p["seo"]["description"] or "") > 160:
                problems.append(f"{gid_num(p['id'])}: seo over char limit")
    elif args.what == "sale":
        plan = load_json(args.file)
        by_id = {j["product_id"]: j for j in plan}
        for p in sample_products(list(by_id), args.sample):
            want = {v["id"]: v for v in by_id[p["id"]]["variants"]}
            for v in p["variants"]:
                w = want.get(v["id"])
                if not w:
                    continue
                if v["price"] != w["price"]:
                    problems.append(f"{gid_num(p['id'])}: PRICE CHANGED "
                                    f"{w['price']} -> {v['price']} (must not happen)")
                if str(v.get("compareAtPrice")) != str(w["compareAtPrice"]):
                    problems.append(
                        f"{gid_num(p['id'])}: compareAt={v.get('compareAtPrice')} "
                        f"expected {w['compareAtPrice']}")
    elif args.what == "featured":
        entries = load_json(args.file)
        by_id = {e["product_id"]: e for e in entries}
        ids = list(by_id)[: args.sample or len(by_id)]
        q = """query($ids: [ID!]!) { nodes(ids: $ids) { ... on Product {
                 id featuredMedia { ... on MediaImage { image { url } } } } } }"""
        for p in gql(q, {"ids": ids})["nodes"]:
            if not p:
                continue
            url = ((p.get("featuredMedia") or {}).get("image") or {}).get("url", "")
            stem = Path(by_id[p["id"]]["url"].split("?")[0]).stem[:40].lower()
            if stem not in url.lower():
                problems.append(f"{gid_num(p['id'])}: featured is still old image")
    if problems:
        print(f"{len(problems)} problems:")
        for x in problems:
            print("  " + x)
        sys.exit(1)
    print("Verification passed on sample")


# --------------------------------------------------------------------- main


def main():
    load_env()
    ap = argparse.ArgumentParser(prog="shopcli", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="fetch products to JSON")
    p.add_argument("--status", choices=["active", "draft", "archived"])
    p.add_argument("--tag", action="append", help="repeatable")
    p.add_argument("--collection", help="collection id")
    p.add_argument("--query", help="raw Shopify product query filter")
    p.add_argument("--limit", type=int)
    p.add_argument("--out", default=str(WORK / "products.json"))
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("digest", help="compact per-product summary")
    p.add_argument("file", nargs="?", default=str(WORK / "products.json"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--snippet", type=int, default=200)
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("push-backend", help="push rewrites + pricing/status")
    p.add_argument("rewrites", nargs="?", help="rewrites.json (optional if only pricing)")
    p.add_argument("--products", default=str(WORK / "products.json"))
    p.add_argument("--price-multiply", type=float, help="e.g. 1.2, 0.95, 2.0")
    p.add_argument("--price-min", type=float)
    p.add_argument("--price-round", choices=["99", "even99", "499-999", "none"])
    p.add_argument("--status", choices=["active", "draft"], help="set for all")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_push_backend)

    p = sub.add_parser("sale", help="compare-at sale start/end")
    p.add_argument("action", choices=["start", "end"])
    p.add_argument("--tags", required=True, type=lambda s: s.split(","))
    p.add_argument("--pct", type=float, help="discount %% (start only)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_sale)

    p = sub.add_parser("attach-images", help="attach image URLs, set featured")
    p.add_argument("map", help="JSON: [{product_id, url, alt?}]")
    p.add_argument("--featured", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_attach_images)

    p = sub.add_parser("ensure-metafields", help="create doc-6 definitions")
    p.set_defaults(func=cmd_ensure_metafields)

    p = sub.add_parser("set-metafields", help="push page copy + images")
    p.add_argument("file", help="JSON: [{product_id, fields{}, images{}}]")
    p.add_argument("--assign-template", metavar="SUFFIX",
                   help="e.g. universal-with-metafields")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_set_metafields)

    p = sub.add_parser("theme-list")
    p.set_defaults(func=cmd_theme_list)

    p = sub.add_parser("theme-pull", help="download theme files")
    p.add_argument("--theme", required=True)
    p.add_argument("--outdir", default=str(WORK / "theme"))
    p.add_argument("paths", nargs="+", help="e.g. templates/product.json")
    p.set_defaults(func=cmd_theme_pull)

    p = sub.add_parser("theme-push", help="upsert files into a draft theme")
    p.add_argument("--theme", required=True)
    p.add_argument("--allow-live", action="store_true")
    p.add_argument("pairs", nargs="+", metavar="LOCAL=THEMEPATH")
    p.set_defaults(func=cmd_theme_push)

    p = sub.add_parser("extract-mhtml", help="parse Chrome MHTML capture")
    p.add_argument("capture")
    p.add_argument("--outdir", default="capture_sections")
    p.set_defaults(func=cmd_extract_mhtml)

    p = sub.add_parser("verify", help="spot-check pushed work")
    p.add_argument("what", choices=["backend", "sale", "featured"])
    p.add_argument("--file", required=True,
                   help="rewrites.json / sale.json / images.json")
    p.add_argument("--sample", type=int, default=3)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("whoami", help="confirm which store is connected")
    p.set_defaults(func=lambda a: print(json.dumps(shop_name())))

    args = ap.parse_args()
    if args.cmd == "sale" and args.action == "start" and args.pct is None:
        ap.error("sale start requires --pct")
    WORK.mkdir(exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
