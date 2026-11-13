"""Pure legacy-compatible title and CSV definitions; no legacy API imports."""

import re

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

HEADER_ROW = "Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8),Custom label (SKU),Category ID,Title,UPC,Price,Quantity,Item photo URL,Condition ID,Description,Format"

INFO_ROWS = [
    "#INFO,Version=0.0.2,Template= eBay-draft-listings-template_US,,,,,,,,",
    "#INFO Action and Category ID are required fields. 1) Set Action to Draft 2) Please find the category ID for your listings here: https://pages.ebay.com/sellerinformation/news/categorychanges.html,,,,,,,,,,",
    '"#INFO After you\'ve successfully uploaded your draft from the Seller Hub Reports tab, complete your drafts to active listings here: https://www.ebay.com/sh/lst/drafts",,,,,,,,,,',
    "#INFO,,,,,,,,,,",
]
