r"""
eBay Draft Listing Generator (for sports/trading card sellers)
================================================================
Originally built for Matchday Cards — shared with The Pitch community. Set
SHOP_NAME, EBAY_DIR, and OUTPUT_DIR below to your own shop before running.
Outputs eBay's official "Create Drafts" template format exactly.
Drafts go straight to Seller Hub -> Listings -> Drafts for review/publish.

Phase 1 (--rename): Scans Z:\Match Day Cards\eBay for consecutive front/back pairs,
                    uses Claude Vision to rename to "Firstname_Lastname_front.jpg" etc.
                    The same vision call also extracts full card data, which is
                    cached to a "<Player>_data.json" sidecar file so Phase 2 doesn't
                    have to ask Claude to look at the same photos twice.

Phase 2 (--list):   Reads renamed images (reusing the Phase 1 sidecar cache when
                    present, otherwise extracting fresh), uploads photos to
                    Cloudinary, and writes the draft CSV with real photo URLs.

Reliability / automation features:
  - Retries with exponential backoff on transient API / network failures, including
    a malformed/non-JSON response from the model
  - CSV is written incrementally (one crashed card costs you that card, not the batch)
  - Extracted data is schema-validated; cards missing required fields are routed to
    a "Needs Review" folder instead of silently producing a bad draft row
  - Cards are processed concurrently (default 4 at a time) for faster batches
  - Images are downscaled before being sent to Claude's vision API (this only
    affects what Claude sees for data extraction — the full-resolution original is
    still what gets uploaded to Cloudinary and shown on the live eBay listing)
  - Phase 1 and Phase 2 share a single vision call per card (see above) instead of
    each phase re-analyzing the same photos
  - An HTML QC report (thumbnails, titles, prices, warnings) is generated alongside
    the CSV so you can eyeball a batch in seconds before uploading
  - Running Claude API cost (tokens + web search) is estimated and printed at the end

Usage:
    python ebay_draft_lister.py               # Both phases
    python ebay_draft_lister.py --rename       # Rename only
    python ebay_draft_lister.py --list         # CSV only
    python ebay_draft_lister.py --workers 2    # Override concurrency (default 4)

After uploading the CSV in Seller Hub -> Reports -> Upload:
    Go to Listings -> Drafts, open each one, verify, and publish.
    (Photos are pre-populated. Weight/dimensions/Best Offer/location still need to
    be set via a Shipping Business Policy or manually — eBay's draft CSV template
    doesn't support those fields.)

Requirements:
    pip install anthropic requests pillow
    pip install pillow-heif     # only needed if your photos are .heic (iPhone)
    set ANTHROPIC_API_KEY=sk-ant-...
    set CLOUDINARY_CLOUD_NAME=your_cloud_name
    set CLOUDINARY_UPLOAD_PRESET=your_unsigned_preset
"""

import os, re, sys, csv, io, json, time, shutil, base64, argparse, threading, html
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import requests
from PIL import Image

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None  # HEIC input will raise a clear error if this isn't installed

# ─── CONFIG ───────────────────────────────────────────────────
# Change these 3 to match your own shop before your first run.
SHOP_NAME   = "Your Shop Name"                    # shown in listing descriptions + the QC report title
EBAY_DIR    = Path(r"C:\YourShop\eBay")           # folder where you drop raw, unsorted photos
OUTPUT_DIR  = Path(r"C:\YourShop")                # where the finished CSV + QC report get saved

DONE_DIR    = EBAY_DIR / "Done"
REVIEW_DIR  = EBAY_DIR / "Needs Review"
RUN_STAMP   = datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT_CSV  = OUTPUT_DIR / f"eBay_Drafts_{RUN_STAMP}.csv"
OUTPUT_HTML = OUTPUT_DIR / f"eBay_Drafts_{RUN_STAMP}_review.html"

# Trading card singles category (soccer/sports)
CATEGORY_ID = "261328"   # Sports Trading Card Singles

# eBay Condition IDs for trading cards (category 261328)
# 4000 = Ungraded (raw card)   2750 = Graded (PSA/SGC/BGS slabs)
CONDITION_ID       = "4000"
CARD_GRADE_DEFAULT = "Mint"   # Default sub-condition/grade shown for raw ungraded cards
DEFAULT_LISTING_PRICE = 100.00  # Flat starting price; adjust manually per card after research

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

MAX_WORKERS      = 4     # cards processed concurrently
RETRY_ATTEMPTS   = 3     # total attempts per API/network call before giving up
RETRY_BASE_DELAY = 1.5   # seconds; doubles each retry

# Anthropic API pricing, per https://docs.claude.com/en/docs/about-claude/pricing
SONNET_INPUT_PER_MTOK  = 3.00
SONNET_OUTPUT_PER_MTOK = 15.00
WEB_SEARCH_PER_SEARCH  = 0.01

# ─── IMAGE HOSTING (Cloudinary) ────────────────────────────────
# Create a free account at cloudinary.com, then:
#   1. Copy your "Cloud Name" from the dashboard
#   2. Settings -> Upload -> add an UNSIGNED upload preset
#   3. set CLOUDINARY_CLOUD_NAME=your_cloud_name
#      set CLOUDINARY_UPLOAD_PRESET=your_preset_name
CLOUDINARY_CLOUD_NAME    = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_UPLOAD_PRESET = os.environ.get("CLOUDINARY_UPLOAD_PRESET", "")
CLOUDINARY_UPLOAD_URL    = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"

# ─── CLIENT ───────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


# ═══════════════════════════════════════════════════════════════
# COST TRACKING
# ═══════════════════════════════════════════════════════════════

class CostTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.input_tokens = 0
        self.output_tokens = 0
        self.web_searches = 0

    def add_usage(self, usage):
        if usage is None:
            return
        with self._lock:
            self.input_tokens  += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0
            server_tools = getattr(usage, "server_tool_use", None)
            if server_tools:
                self.web_searches += getattr(server_tools, "web_search_requests", 0) or 0

    def total_cost(self) -> float:
        token_cost  = (self.input_tokens  / 1_000_000) * SONNET_INPUT_PER_MTOK
        token_cost += (self.output_tokens / 1_000_000) * SONNET_OUTPUT_PER_MTOK
        search_cost = self.web_searches * WEB_SEARCH_PER_SEARCH
        return token_cost + search_cost

    def summary(self) -> str:
        return (f"Claude usage: {self.input_tokens:,} input / {self.output_tokens:,} output tokens, "
                f"{self.web_searches} web search(es)  ->  est. cost ${self.total_cost():.4f}")


COST = CostTracker()


# ═══════════════════════════════════════════════════════════════
# RETRY HELPER
# ═══════════════════════════════════════════════════════════════

def with_retry(fn, *, attempts: int = RETRY_ATTEMPTS, base_delay: float = RETRY_BASE_DELAY, label: str = ""):
    """Call fn() with retries + exponential backoff. Re-raises the last error if all attempts fail."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"    [Retry {attempt}/{attempts - 1}] {label} failed ({e}); retrying in {delay:.1f}s")
                time.sleep(delay)
    raise last_exc


# Anthropic downscales/tiles any image above this on its end anyway, so sending
# more pixels than this just burns upload time and input tokens for zero gain
# in what the model can actually read off the card.
MAX_VISION_DIMENSION = 1568


def encode_image(path: Path) -> tuple:
    """Read an image and return (base64_data, media_type) sized for the vision API.
    This is ONLY used for Claude vision calls — upload_image() below sends the
    original, full-resolution file to Cloudinary, so the live eBay photos are
    never affected by this downscaling."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        if max(img.size) > MAX_VISION_DIMENSION:
            img.thumbnail((MAX_VISION_DIMENSION, MAX_VISION_DIMENSION), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        data = buf.getvalue()
    return base64.standard_b64encode(data).decode("utf-8"), "image/jpeg"


def upload_image(path: Path) -> str | None:
    """Upload an image to Cloudinary and return its public URL, or None on failure.
    Always re-encodes to full-resolution JPEG first (no downscaling — that's only
    for the vision calls) so the listing photo is something every browser can
    actually display. eBay drafts have shown up with blank/broken photo tiles
    when the source file was HEIC and got uploaded as-is: Cloudinary stores it
    fine, but most browsers (Chrome included) can't render a .heic <img> src."""
    if not CLOUDINARY_CLOUD_NAME or not CLOUDINARY_UPLOAD_PRESET:
        return None

    with Image.open(path) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=95)
        jpeg_bytes = buf.getvalue()
    upload_name = path.with_suffix(".jpg").name

    def _do():
        resp = requests.post(
            CLOUDINARY_UPLOAD_URL,
            data={"upload_preset": CLOUDINARY_UPLOAD_PRESET},
            files={"file": (upload_name, jpeg_bytes, "image/jpeg")},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("secure_url")

    try:
        return with_retry(_do, label=f"upload {path.name}")
    except Exception as e:
        print(f"    [Image upload error] {path.name}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# PHASE 1 — RENAME
# ═══════════════════════════════════════════════════════════════

def is_already_named(name: str) -> bool:
    stem = Path(name).stem.lower()
    return stem.endswith("_front") or stem.endswith("_back")


def get_raw_images(folder: Path) -> list:
    return sorted(
        [f for f in folder.iterdir()
         if f.is_file() and f.suffix.lower() in IMAGE_EXTS
         and not is_already_named(f.name)],
        key=lambda p: p.name.lower()
    )


def is_same_card_pair(img_a: Path, img_b: Path) -> bool:
    """Vision check: do these two images show the front and back of the SAME
    physical card? Used to build pairs correctly even when a card is missing
    its back (or an extra/duplicate image is in the folder), instead of
    blindly assuming every two consecutive files alternate front/back."""
    d1, mt1 = encode_image(img_a)
    d2, mt2 = encode_image(img_b)
    content = [
        {"type":"image","source":{"type":"base64","media_type":mt1,"data":d1}},
        {"type":"text","text":"Image 1."},
        {"type":"image","source":{"type":"base64","media_type":mt2,"data":d2}},
        {"type":"text","text":"Image 2."},
        {"type":"text","text":(
            "Do Image 1 and Image 2 show the front and back of the SAME physical "
            "sports trading card (same player, same card design)? "
            "Reply with ONLY one word: 'yes' or 'no'."
        )},
    ]

    def _do():
        return client.messages.create(model="claude-sonnet-4-6", max_tokens=10,
                                       messages=[{"role":"user","content":content}])
    try:
        r = with_retry(_do, label=f"pair-check {img_a.name}/{img_b.name}")
        COST.add_usage(r.usage)
        answer = r.content[0].text.strip().lower()
        return answer.startswith("y")
    except Exception as e:
        print(f"    [Pair-check error] {img_a.name}/{img_b.name}: {e}")
        return False  # safest default: don't merge two possibly-different cards


def build_pairs(images: list) -> list:
    """Group a flat, sorted list of raw images into (front, back-or-None) pairs,
    using is_same_card_pair to verify adjacency instead of assuming a strict
    alternating order. This is what prevents one missing back photo from
    shifting every pairing after it."""
    if len(images) <= 1:
        return [(images[0], None)] if images else []

    print(f"Checking {len(images)} image(s) for front/back pairing...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        adjacency = list(pool.map(
            lambda idx: is_same_card_pair(images[idx], images[idx + 1]),
            range(len(images) - 1)
        ))

    pairs = []
    i = 0
    while i < len(images):
        if i < len(images) - 1 and adjacency[i]:
            pairs.append((images[i], images[i + 1]))
            i += 2
        else:
            pairs.append((images[i], None))
            i += 1
    return pairs


def slugify_player(name: str) -> str | None:
    """'Erling Haaland' -> 'Erling_Haaland'. Returns None if nothing usable is left."""
    safe = re.sub(r"[^A-Za-z0-9 ]", "", name or "").strip()
    safe = re.sub(r"\s+", " ", safe)
    if not safe:
        return None
    return "_".join(word.capitalize() for word in safe.split(" "))


def data_sidecar_path(folder: Path, player_slug: str) -> Path:
    """Where Phase 1 caches a card's full extracted data so Phase 2 (when run
    right after, which is the default) doesn't have to ask Claude to look at
    the same two photos a second time."""
    return folder / f"{player_slug}_data.json"


_rename_lock = threading.Lock()


def rename_one(front: Path, back) -> bool:
    print(f"  {front.name}" + (f" + {back.name}" if back else " (no back detected)"))

    # One vision call does double duty: it identifies the player for the rename
    # AND extracts the full card data, which gets cached for Phase 2.
    card = extract_card_data(front, back)
    player = slugify_player(card.get("player")) if card else None
    if not player:
        print("    [SKIP] Could not identify player.")
        return False

    ext = front.suffix.lower()
    with _rename_lock:
        nf = EBAY_DIR / f"{player}_front{ext}"
        nb = EBAY_DIR / f"{player}_back{ext}" if back else None
        c = 2
        while nf.exists():
            nf = EBAY_DIR / f"{player}_{c}_front{ext}"
            if nb: nb = EBAY_DIR / f"{player}_{c}_back{ext}"
            c += 1
        final_slug = nf.stem[:-6]  # strip trailing "_front"
        front.rename(nf)
        if back and nb:
            back.rename(nb)

    try:
        data_sidecar_path(EBAY_DIR, final_slug).write_text(json.dumps(card), encoding="utf-8")
    except OSError as e:
        print(f"    [Warning] couldn't cache extracted data ({e}); Phase 2 will re-extract this card")

    print(f"    OK -> {nf.name}")
    if back and nb:
        print(f"    OK -> {nb.name}")
    return True


def phase1_rename():
    print("\n" + "="*56)
    print("PHASE 1: Renaming card images")
    print("="*56)
    EBAY_DIR.mkdir(parents=True, exist_ok=True)
    images = get_raw_images(EBAY_DIR)
    if not images:
        print("No unprocessed images found.")
        return

    pairs = build_pairs(images)
    print(f"\nFound {len(pairs)} card(s) — processing up to {MAX_WORKERS} at a time.\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda p: rename_one(*p), pairs))

    renamed = sum(1 for ok in results if ok)
    print(f"\nPhase 1 done. Renamed {renamed}/{len(pairs)} card(s).")
    print(COST.summary())


# ═══════════════════════════════════════════════════════════════
# PHASE 2 — GENERATE CSV
# ═══════════════════════════════════════════════════════════════

def get_named_pairs(folder: Path) -> list:
    fronts = sorted(
        [f for f in folder.iterdir()
         if f.is_file() and f.suffix.lower() in IMAGE_EXTS
         and f.stem.lower().endswith("_front")],
        key=lambda p: p.name.lower()
    )
    pairs = []
    for front in fronts:
        player = front.stem[:-6].strip()
        back = folder / f"{player}_back{front.suffix}"
        pairs.append((player, front, back if back.exists() else None))
    return pairs


EXTRACT_PROMPT = """
Analyze this sports trading card and return ONLY a JSON object:

{
  "player":       "Full player name (Title Case)",
  "team":         "Club name",
  "league":       "League (Premier League, MLS, La Liga, etc.)",
  "year":         2025,
  "set_name":     "Full set name (e.g. 2025 Topps Chrome MLS)",
  "parallel":     "Parallel name or null if base",
  "card_number":  "Card number as string or null",
  "is_auto":      false,
  "is_relic":     false,
  "is_rookie":    false,
  "print_run":    null,
  "sport":        "Soccer",
  "images_match": true
}

Rules:
- print_run is an integer only (no slash), null if unlimited.
- is_rookie is true only if the card is explicitly marked/labeled as a rookie card
  (e.g. "RC" logo, "Rookie Card" text) or the set is a known rookie-focused product.
- images_match: true only if both images clearly show the front and back of the SAME
  physical card (same player/set/design). Set to false if they appear to be different
  cards. If only one image was provided, set to true.
- Return ONLY the JSON object, no other text, no markdown fences.
"""


def extract_card_data(front: Path, back) -> dict | None:
    """Single vision call that does everything downstream code needs from the
    photos: player name (for the Phase 1 rename) plus full listing data (for the
    Phase 2 CSV row). Called once per card and cached — see data_sidecar_path."""
    content = []
    d, mt = encode_image(front)
    content.append({"type":"image","source":{"type":"base64","media_type":mt,"data":d}})
    content.append({"type":"text","text":"Front of card."})
    if back and back.exists():
        d2, mt2 = encode_image(back)
        content.append({"type":"image","source":{"type":"base64","media_type":mt2,"data":d2}})
        content.append({"type":"text","text":"Back of card."})
    content.append({"type":"text","text":EXTRACT_PROMPT})

    def _do():
        r = client.messages.create(model="claude-sonnet-4-6", max_tokens=400,
                                    messages=[{"role":"user","content":content}])
        COST.add_usage(r.usage)  # count tokens even if the parse below fails, so a
                                  # retried malformed-JSON attempt still shows up in cost
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", r.content[0].text.strip())
        result = json.loads(raw)
        return result[0] if isinstance(result, list) else result

    try:
        # Wrapping the JSON parse inside the retried call means a one-off
        # malformed/non-JSON response from the model gets re-asked instead of
        # immediately failing the card.
        return with_retry(_do, label=f"extract {front.name}")
    except Exception as e:
        print(f"    [Extract error] {e}")
        return None


def validate_and_clean(data: dict) -> tuple:
    """Coerce types and check required fields. Returns (blocking_issues, warnings, cleaned_data)."""
    blocking, warnings = [], []

    player   = str(data.get("player") or "").strip()
    set_name = str(data.get("set_name") or "").strip()
    if not player:
        blocking.append("missing player name")
    if not set_name:
        blocking.append("missing set name")
    data["player"], data["set_name"] = player, set_name

    for field in ("year", "print_run"):
        raw = data.get(field)
        if raw in (None, "", "null"):
            data[field] = None
            continue
        try:
            data[field] = int(raw)
        except (TypeError, ValueError):
            blocking.append(f"invalid {field}: {raw!r}")
            data[field] = None

    data["is_auto"]   = bool(data.get("is_auto"))
    data["is_relic"]  = bool(data.get("is_relic"))
    data["is_rookie"] = bool(data.get("is_rookie"))

    if data.get("images_match") is False:
        warnings.append("front/back images may not match — verify before publishing")

    return blocking, warnings, data


def suggest_price(card: dict) -> float:
    # No comp-search/auto-pricing here — eBay's sold listings aren't reliably
    # indexed by general web search, so a prior attempt at this kept coming back
    # empty. Every card is listed at DEFAULT_LISTING_PRICE and priced manually
    # after research.
    return DEFAULT_LISTING_PRICE


def build_title(card: dict) -> str:
    """Ordered by what actually drives eBay search matches for sports cards:
    player name and year/set are what buyers type first, then parallel/RC/card
    number/print run narrow it down for someone already searching a specific
    card. Team is included only if there's room. Based on Cardlines' and Sports
    Card Investor's eBay title guides plus /r/sportscards seller writeups —
    all converge on player name leading (or immediately following year/set),
    with year, set, parallel, and RC as the other must-have terms.

    If the assembled title runs past eBay's 80-char limit, we drop from the end
    (lowest-priority fields first) rather than hard-truncating mid-word.
    """
    set_name = card.get("set_name", "") or ""
    year = str(card.get("year", "")) if card.get("year") else ""
    # Most set names already start with a year (e.g. "2021/22 Topps Chrome
    # Bundesliga"), so only add a separate year token if it's not already there.
    year_already_in_set = bool(re.match(r"^\d{4}(/\d{2})?\b", set_name))

    parts = []
    if card.get("player"):
        parts.append(card["player"])
    if year and not year_already_in_set:
        parts.append(year)
    if set_name:
        parts.append(set_name)
    if card.get("parallel"):
        parts.append(card["parallel"])
    if card.get("is_rookie"):
        parts.append("RC")
    if card.get("card_number"):
        parts.append(f"#{card['card_number']}")
    if card.get("print_run"):
        parts.append(f"/{card['print_run']}")
    if card.get("is_auto"):
        parts.append("AUTO")
    if card.get("is_relic"):
        parts.append("RELIC")
    if card.get("team"):
        parts.append(card["team"])

    title = " ".join(parts)
    while len(title) > 80 and len(parts) > 1:
        parts.pop()
        title = " ".join(parts)
    return title[:80]


def build_description(card: dict) -> str:
    lines = [
        f"<b>{card.get('player','')}</b> — {card.get('set_name','')}",
        f"Team: {card.get('team','')}" if card.get('team') else "",
        f"Parallel: {card.get('parallel','Base')}",
        f"Print Run: /{card['print_run']}" if card.get('print_run') else "",
        "<b>Rookie Card (RC)</b>" if card.get('is_rookie') else "",
        "<b>On-Card Autograph</b>" if card.get('is_auto') else "",
        "Jersey/Patch Relic" if card.get('is_relic') else "",
        "",
        f"Condition: Ungraded — {CARD_GRADE_DEFAULT}",
        "Ships in penny sleeve inside a top loader with team bag.",
        "Smoke-free, pet-free storage. Fast shipping!",
        "",
        f"Questions? Message me anytime — {SHOP_NAME}",
    ]
    return "<p>" + "<br>".join(l for l in lines if l is not None) + "</p>"


# Exact column header from the downloaded template
HEADER_ROW = "Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8),Custom label (SKU),Category ID,Title,UPC,Price,Quantity,Item photo URL,Condition ID,Description,Format"

INFO_ROWS = [
    "#INFO,Version=0.0.2,Template= eBay-draft-listings-template_US,,,,,,,,",
    "#INFO Action and Category ID are required fields. 1) Set Action to Draft 2) Please find the category ID for your listings here: https://pages.ebay.com/sellerinformation/news/categorychanges.html,,,,,,,,,,",
    '"#INFO After you\'ve successfully uploaded your draft from the Seller Hub Reports tab, complete your drafts to active listings here: https://www.ebay.com/sh/lst/drafts",,,,,,,,,,',
    "#INFO,,,,,,,,,,",
]


def process_card(player_slug: str, front: Path, back) -> dict:
    """Runs extraction, validation, pricing, and photo upload for one card.
    Returns a result dict with status: 'ok' | 'review' | 'failed'."""
    tag = f"[{player_slug}]"
    base = {"player_slug": player_slug, "front": front, "back": back}

    # Reuse Phase 1's extraction if it ran in this folder (the normal default-mode
    # path) instead of paying for a second vision call on the same photos.
    sidecar = data_sidecar_path(front.parent, player_slug)
    card = None
    if sidecar.exists():
        try:
            card = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  {tag} [Warning] couldn't read cached data ({e}); re-extracting")
        finally:
            sidecar.unlink(missing_ok=True)

    if card is None:
        card = extract_card_data(front, back)
    if card is None:
        print(f"  {tag} FAILED — extraction error (left in place for retry)")
        return {**base, "status": "failed", "error": "extraction failed"}

    blocking, warnings, card = validate_and_clean(card)
    if blocking:
        reason = "; ".join(blocking)
        print(f"  {tag} NEEDS REVIEW — {reason}")
        return {**base, "status": "review", "card": card, "reason": reason, "warnings": warnings}

    print(f"  {tag} {card.get('player')} | {card.get('set_name')} | {card.get('parallel') or 'Base'}")
    if warnings:
        print(f"  {tag} WARNING: {'; '.join(warnings)}")

    price = suggest_price(card)

    title = build_title(card)
    desc  = build_description(card)

    photo_urls = []
    front_url = upload_image(front)
    if front_url:
        photo_urls.append(front_url)
    else:
        warnings.append("front photo upload failed")
    if back and back.exists():
        back_url = upload_image(back)
        if back_url:
            photo_urls.append(back_url)
        else:
            warnings.append("back photo upload failed")

    if not photo_urls:
        reason = "no photos uploaded (check Cloudinary env vars / connection)"
        print(f"  {tag} NEEDS REVIEW — {reason}")
        return {**base, "status": "review", "card": card, "reason": reason, "warnings": warnings}

    photo_field = "|".join(photo_urls)  # eBay accepts pipe-separated multi-photo URLs
    row = [
        "Draft", "", CATEGORY_ID, title, "", f"{price:.2f}", "1",
        photo_field, CONDITION_ID, desc, "FixedPrice",
    ]
    print(f"  {tag} OK — ${price:.2f}")
    return {**base, "status": "ok", "card": card, "row": row, "title": title,
            "price": price, "photo_urls": photo_urls, "warnings": warnings}


def write_html_report(listed: list, needs_review: list, failed: list):
    def esc(s): return html.escape(str(s or ""))

    def ok_card(r):
        card = r["card"]
        photo = r["photo_urls"][0] if r["photo_urls"] else ""
        warn = f'<div class="warn">⚠ {esc("; ".join(r["warnings"]))}</div>' if r["warnings"] else ""
        return f'''<div class="card ok">
          <img src="{esc(photo)}" alt="">
          <div class="info">
            <div class="title">{esc(r["title"])}</div>
            <div class="meta">{esc(card.get("player"))} · {esc(card.get("set_name"))} · {esc(card.get("parallel") or "Base")}</div>
            <div class="price">${r["price"]:.2f}</div>
            {warn}
          </div></div>'''

    def review_card(r):
        return f'''<div class="card review">
          <div class="info">
            <div class="title">{esc(r["player_slug"])}</div>
            <div class="meta">Reason: {esc(r["reason"])}</div>
          </div></div>'''

    def failed_card(r):
        return f'''<div class="card failed">
          <div class="info">
            <div class="title">{esc(r["player_slug"])}</div>
            <div class="meta">Error: {esc(r.get("error", ""))}</div>
          </div></div>'''

    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{esc(SHOP_NAME)} — Draft QC Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  h1 {{ font-size:20px; }}
  h2 {{ font-size:16px; margin-top:32px; border-bottom:1px solid #333; padding-bottom:6px; }}
  .grid {{ display:flex; flex-wrap:wrap; gap:14px; margin-top:12px; }}
  .card {{ width:220px; background:#181b21; border-radius:10px; overflow:hidden; border:1px solid #2a2e37; }}
  .card img {{ width:100%; height:220px; object-fit:cover; background:#000; display:block; }}
  .info {{ padding:10px; }}
  .title {{ font-size:13px; font-weight:600; line-height:1.3; margin-bottom:4px; }}
  .meta {{ font-size:12px; color:#9aa0aa; margin-bottom:6px; }}
  .price {{ font-size:15px; font-weight:700; color:#4ade80; }}
  .warn {{ margin-top:6px; font-size:11px; color:#facc15; }}
  .card.review {{ border-color:#facc15; }}
  .card.failed {{ border-color:#f87171; }}
  .count {{ color:#9aa0aa; font-weight:400; }}
</style></head>
<body>
  <h1>{esc(SHOP_NAME)} — Draft QC Report <span class="count">({RUN_STAMP})</span></h1>
  <h2>Ready to publish <span class="count">({len(listed)})</span></h2>
  <div class="grid">{"".join(ok_card(r) for r in listed) or "<i>None</i>"}</div>
  <h2>Needs review <span class="count">({len(needs_review)})</span></h2>
  <div class="grid">{"".join(review_card(r) for r in needs_review) or "<i>None</i>"}</div>
  <h2>Failed — left in eBay folder to retry <span class="count">({len(failed)})</span></h2>
  <div class="grid">{"".join(failed_card(r) for r in failed) or "<i>None</i>"}</div>
</body></html>"""
    OUTPUT_HTML.write_text(doc, encoding="utf-8")


def phase2_list():
    print("\n" + "="*56)
    print("PHASE 2: Generating eBay Drafts CSV")
    print("="*56)

    pairs = get_named_pairs(EBAY_DIR)
    if not pairs:
        print("No renamed _front images found. Run Phase 1 first.")
        return
    print(f"Found {len(pairs)} card(s) — processing up to {MAX_WORKERS} at a time.\n")

    DONE_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_file = open(OUTPUT_CSV, "w", newline="", encoding="utf-8")
    for info in INFO_ROWS:
        csv_file.write(info + "\r\n")
    csv_file.write(HEADER_ROW + "\r\n")
    csv_file.flush()
    writer = csv.writer(csv_file, lineterminator="\r\n")
    csv_lock = threading.Lock()

    listed, needs_review, failed = [], [], []

    def handle_result(result: dict):
        front, back = result["front"], result["back"]
        if result["status"] == "ok":
            with csv_lock:
                writer.writerow(result["row"])
                csv_file.flush()
            shutil.move(str(front), DONE_DIR / front.name)
            if back and back.exists():
                shutil.move(str(back), DONE_DIR / back.name)
            listed.append(result)
        elif result["status"] == "review":
            shutil.move(str(front), REVIEW_DIR / front.name)
            if back and back.exists():
                shutil.move(str(back), REVIEW_DIR / back.name)
            needs_review.append(result)
        else:
            failed.append(result)  # left in place in EBAY_DIR for a retry

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_card, slug, front, back): slug for slug, front, back in pairs}
        for future in as_completed(futures):
            slug = futures[future]
            try:
                handle_result(future.result())
            except Exception as e:
                print(f"  [{slug}] FAILED — unexpected error: {e}")
                failed.append({"player_slug": slug, "front": None, "back": None, "error": str(e)})

    csv_file.close()
    write_html_report(listed, needs_review, failed)

    print(f"\n{'='*56}")
    print(f"{len(listed)} draft(s) written to:")
    print(f"   {OUTPUT_CSV}")
    if needs_review:
        print(f"{len(needs_review)} card(s) moved to '{REVIEW_DIR.name}' — see QC report")
    if failed:
        print(f"{len(failed)} card(s) failed and were left in place to retry")
    print(f"\nQC report: {OUTPUT_HTML}")
    print(f"\n{COST.summary()}")
    print(f"\nNext steps:")
    print(f"   1. Open the QC report and skim thumbnails/prices for anything off")
    print(f"   2. Seller Hub -> Reports -> Upload -> upload the CSV")
    print(f"   3. Listings -> Drafts -> review and publish")
    print(f"{'='*56}\n")


# ═══════════════════════════════════════════════════════════════
# PHASE 3 — REPAIR ALREADY-UPLOADED HEIC PHOTOS
# ═══════════════════════════════════════════════════════════════
# One-time cleanup for drafts created before upload_image() always converted to
# JPEG. Any card whose renamed files in Done/ still end in .heic was uploaded to
# Cloudinary as raw HEIC, which most browsers (including eBay's own listing
# page) can't render — those show up as blank/white photo tiles. This can't
# update the existing broken draft in place (the CSV template has no way to
# reference an existing draft — the SKU column is always left blank), so the
# fix is: re-extract + re-upload each affected card fresh, write a small CSV
# with just those cards, and you delete the old broken drafts by hand in
# Seller Hub before uploading this one.

def phase3_fix_broken_photos():
    print("\n" + "="*56)
    print("PHASE 3: Rebuilding drafts with pre-fix HEIC photos")
    print("="*56)

    pairs = get_named_pairs(DONE_DIR)
    heic_pairs = [(slug, f, b) for slug, f, b in pairs if f.suffix.lower() == ".heic"]
    if not heic_pairs:
        print(f"No HEIC-sourced cards found in '{DONE_DIR.name}' — nothing needs fixing.")
        return

    print(f"Found {len(heic_pairs)} card(s) uploaded before the JPEG fix.")
    print(f"Re-extracting and re-uploading up to {MAX_WORKERS} at a time.\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fix_csv = OUTPUT_DIR / f"eBay_Drafts_PHOTO_FIX_{RUN_STAMP}.csv"
    csv_file = open(fix_csv, "w", newline="", encoding="utf-8")
    for info in INFO_ROWS:
        csv_file.write(info + "\r\n")
    csv_file.write(HEADER_ROW + "\r\n")
    csv_file.flush()
    writer = csv.writer(csv_file, lineterminator="\r\n")
    csv_lock = threading.Lock()

    fixed, unresolved = [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_card, slug, front, back): slug for slug, front, back in heic_pairs}
        for future in as_completed(futures):
            slug = futures[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"  [{slug}] FAILED — unexpected error: {e}")
                unresolved.append(slug)
                continue
            if result["status"] == "ok":
                with csv_lock:
                    writer.writerow(result["row"])
                    csv_file.flush()
                fixed.append(slug)
            else:
                print(f"  [{slug}] still needs manual attention — status: {result['status']}")
                unresolved.append(slug)

    csv_file.close()
    print(f"\n{'='*56}")
    print(f"{len(fixed)} card(s) rebuilt with working photo URLs -> {fix_csv}")
    if unresolved:
        print(f"{len(unresolved)} card(s) could not be auto-fixed: {', '.join(unresolved)}")
    print(f"\n{COST.summary()}")
    print(f"\nNext steps:")
    print(f"   1. In Seller Hub -> Listings -> Drafts, find and delete the old broken")
    print(f"      draft for each card listed above (the ones with blank photo tiles)")
    print(f"   2. Seller Hub -> Reports -> Upload -> upload {fix_csv.name}")
    print(f"   3. Review the recreated drafts and publish")
    print(f"{'='*56}\n")


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def main():
    global MAX_WORKERS
    parser = argparse.ArgumentParser(description="eBay Draft Lister")
    parser.add_argument("--rename",  action="store_true", help="Phase 1: rename images only")
    parser.add_argument("--list",    action="store_true", help="Phase 2: generate CSV only")
    parser.add_argument("--fix-broken-photos", action="store_true",
                         help="Phase 3: rebuild CSV rows for already-listed cards whose photos "
                              "were uploaded as unrenderable HEIC before the JPEG fix")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Concurrent cards to process (default 4)")
    args = parser.parse_args()
    MAX_WORKERS = max(1, args.workers)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("  Run: set ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    if not CLOUDINARY_CLOUD_NAME or not CLOUDINARY_UPLOAD_PRESET:
        print("WARNING: CLOUDINARY_CLOUD_NAME / CLOUDINARY_UPLOAD_PRESET not set.")
        print("  Cards without photos will be routed to 'Needs Review' instead of drafted blind.")
        print("  See the script header for setup steps.\n")

    defaults_left = []
    if SHOP_NAME == "Your Shop Name":
        defaults_left.append("SHOP_NAME")
    if str(EBAY_DIR) == r"C:\YourShop\eBay":
        defaults_left.append("EBAY_DIR")
    if str(OUTPUT_DIR) == r"C:\YourShop":
        defaults_left.append("OUTPUT_DIR")
    if defaults_left:
        print("WARNING: You're still using the default placeholder value(s) for: "
              + ", ".join(defaults_left))
        print("  Open this script and update the CONFIG section near the top before running for real.\n")

    if args.fix_broken_photos:
        phase3_fix_broken_photos()
    elif args.rename:
        phase1_rename()
    elif args.list:
        phase2_list()
    else:
        phase1_rename()
        phase2_list()


if __name__ == "__main__":
    main()