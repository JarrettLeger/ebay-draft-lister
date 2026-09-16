# eBay Draft Lister

A Python script that turns a folder of raw trading card photos into ready-to-review eBay draft listings — automatically identifying each card, writing an optimized title and description, uploading photos, and producing eBay's official "Create Drafts" CSV format.

Originally built for [Matchday Cards](https://github.com/) and shared with **The Pitch** community of soccer card sellers, but it works for any sports/trading card shop.

## What it does

1. **Phase 1 — Rename** (`--rename`): scans a folder of unsorted photos, finds front/back pairs, and uses Claude's vision API to identify each card and rename the pair to `Firstname_Lastname_front.jpg` / `_back.jpg`. The same vision call extracts full card data (set, parallel, print run, autograph/relic flags, etc.) and caches it to a `<Player>_data.json` sidecar so Phase 2 never has to re-analyze the same photos.
2. **Phase 2 — List** (`--list`): reads the renamed images (reusing the Phase 1 cache when available), uploads full-resolution photos to Cloudinary, and writes an eBay draft-listing CSV plus an HTML QC report you can skim before uploading anything.

Running the script with no flags does both phases back to back.

There's also a repair mode, **`--fix-broken-photos`**, for drafts that were already created with photos uploaded in a browser-incompatible format (see Troubleshooting below).

### What it deliberately does *not* do

It does not price your cards. Early testing tried having the AI look up recent sold comps and suggest a price automatically, but card values swing too widely — even within the same set and parallel — for that to be reliably accurate. Every card is listed at one flat placeholder price (`DEFAULT_LISTING_PRICE`, $100 by default) instead. You set real prices yourself, per card, before publishing. This tool's job is to save you the busywork of typing titles/descriptions and uploading photos — not to guess what a card is worth.

## Requirements

```
pip install anthropic requests pillow
pip install pillow-heif     # only needed if your photos are .heic (iPhone default format)
```

You'll also need:

- An [Anthropic API key](https://console.anthropic.com/) (Claude vision calls)
- A free [Cloudinary](https://cloudinary.com/) account for photo hosting (used for the URLs eBay's CSV template needs)

Set these as environment variables before running:

```
set ANTHROPIC_API_KEY=sk-ant-...
set CLOUDINARY_CLOUD_NAME=your_cloud_name
set CLOUDINARY_UPLOAD_PRESET=your_unsigned_preset
```

(Cloudinary: copy your Cloud Name from the dashboard, then go to Settings → Upload and add an **unsigned** upload preset.)

## Setup

Open `ebay_draft_lister.py` and edit the CONFIG section near the top:

| Setting | What to change it to |
|---|---|
| `SHOP_NAME` | Your shop's name — shown in every listing description and on the QC report |
| `EBAY_DIR` | Full path to the folder where you drop raw, unsorted card photos |
| `OUTPUT_DIR` | Where the finished CSV and QC report get saved |
| `DEFAULT_LISTING_PRICE` | Your default placeholder price (still adjust per card afterward) |

The script will print a warning at startup if any of these are still at their default placeholder values.

## Usage

```
python ebay_draft_lister.py               # Both phases
python ebay_draft_lister.py --rename       # Rename only
python ebay_draft_lister.py --list         # CSV only
python ebay_draft_lister.py --workers 2    # Override concurrency (default 4)
python ebay_draft_lister.py --fix-broken-photos   # Repair drafts with broken (HEIC) photos
```

After the run, upload the CSV in eBay Seller Hub → Reports → Upload. Then:

1. Open the QC report and skim for warnings or anything that looks wrong.
2. Go to Listings → Drafts, select every new draft, and click **Resume Drafts**. This opens a bulk editor for fields the CSV template can't set — Package Details, Shipping Policy, Condition, Item Location, Payment Policy, Return Policy, etc. — for every card at once. Save for later.
3. Go into each draft individually one more time to set your real price, then publish.

## Troubleshooting

**`PIL.UnidentifiedImageError` on `.HEIC` files** — install `pillow-heif` (`pip install pillow-heif`). iPhones shoot HEIC by default.

**Blank/white photo tiles on eBay drafts** — this happens if photos were uploaded to Cloudinary as raw HEIC; browsers (including eBay's own listing pages) can't render a `.heic` `<img>` tag. This script always converts to JPEG before uploading, so it shouldn't happen on drafts created with the current version. If you have older drafts created before this fix, run `python ebay_draft_lister.py --fix-broken-photos` — it finds the affected cards, re-uploads correct JPEG photos, and writes a small corrective CSV. You'll still need to delete the old broken drafts manually in Seller Hub and upload the fix CSV in their place (eBay's CSV format has no way to update an existing draft in place).

## Notes

- Images are downscaled only for the Claude vision call (to save cost/latency) — the full-resolution original is always what gets uploaded to Cloudinary and shown on the live listing.
- Cards missing required data are routed to a `Needs Review` folder instead of silently producing a bad draft row.
- Running Claude API cost is estimated and printed at the end of each run.

## License

MIT — use it, fork it, adapt it for your own shop.
