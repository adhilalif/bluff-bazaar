"""All player-facing display strings for the Malabar / Kerala theme, in one editable place.

Internal ids (commodity keys, card types, event keys) are stored in the database and used by the
engine; they never change. This module only maps those ids to what players see. Editing a name or
description here changes nothing about game mechanics.

Style: Manglish (Malayalam written in Latin letters) names are primary; every name has an
English gloss that players can show or hide. Rules text and UI chrome stay in English.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Optional

BRAND_TITLE = "Bluff Bazaar"
BRAND_TAGLINE = "The Malabar Trade Game"

ICON_DIR = Path(__file__).resolve().parent / "icons"

# ---------------------------------------------------------------------------
# Commodities. Keys are the stable internal ids (kept from the original game).
# ---------------------------------------------------------------------------
COMMODITY_DISPLAY: dict[str, dict[str, str]] = {
    "spices":   {"name": "Thenga",     "en": "Coconut",       "icon": "thenga",     "color": "#7A4A2B"},
    "relics":   {"name": "Kurumulaku", "en": "Black pepper",  "icon": "kurumulaku", "color": "#26221F"},
    "crystals": {"name": "Elakka",     "en": "Cardamom",      "icon": "elakka",     "color": "#5E8A40"},
    "energy":   {"name": "Chakka",     "en": "Jackfruit",     "icon": "chakka",     "color": "#8DA03A"},
    "metals":   {"name": "Nendran",    "en": "Kerala banana", "icon": "nendran",    "color": "#C4932A"},
}
# The order players see commodities in (internal order is irrelevant to display).
DISPLAY_ORDER: tuple[str, ...] = ("spices", "relics", "crystals", "energy", "metals")

# ---------------------------------------------------------------------------
# Cards. Keys are the stable card types stored in bluff.player_cards.
# ---------------------------------------------------------------------------
CARD_DISPLAY: dict[str, dict[str, str]] = {
    "market_pump":   {"icon": "📈", "name": "Vila Keri!", "en": "Price up!",
                      "text": "Push one commodity's price up by 25% (at least +2)."},
    "market_crash":  {"icon": "📉", "name": "Vila Idinju!", "en": "Price crash!",
                      "text": "Drag one commodity's price down by 25% (at least −2)."},
    "shield":        {"icon": "🛡️", "name": "Raksha", "en": "Protection",
                      "text": "Blocks Chungam and harbour customs; refunds your losses from Vila Idinju!."},
    "insider_tip":   {"icon": "🔍", "name": "Oru Karyam Parayam…", "en": "Let me tell you something…",
                      "text": "Use it now: privately reveals this round's true market event."},
    "tax_collector": {"icon": "💰", "name": "Chungam", "en": "Toll",
                      "text": "Take 15% of one opponent's coins (max 15)."},
    "double_down":   {"icon": "🎲", "name": "Iratti Labham", "en": "Double profit",
                      "text": "Your trade's price swing is paid to you — or charged — a second time."},
}

# ---------------------------------------------------------------------------
# Market events. Each existing event key gets a themed title; effects are unchanged.
# ---------------------------------------------------------------------------
GLOBAL_EVENT_DISPLAY: dict[str, dict[str, str]] = {
    "calm":     {"icon": "🌧️", "title": "Mazha Thudangi!", "en": "Monsoon Delays",
                 "text": "Monsoon rain slows the harbour. Nothing else changes this round."},
    "festival": {"icon": "🌼", "title": "Onam Vishesham", "en": "Harbour Festival",
                 "text": "Festive crowds! Every trader whose trade went through earns +4 coins."},
    "customs":  {"icon": "⚓", "title": "Kadavil Parishodhana", "en": "Harbour Customs",
                 "text": "Pay 1 coin for each unit you hold above 6. Raksha protects you."},
}
BOOM_DISPLAY = {"icon": "⛵", "title": "Nalla Kachavadam!", "en": "Spice Route Opens",
                "text": "Buyers arrive from the spice route: {name} price rises."}
PEPPER_BOOM_DISPLAY = {"icon": "🌿", "title": "Kurumulaku Season!", "en": "Pepper Harvest",
                       "text": "Pepper demand surges: {name} price rises."}
BUST_DISPLAY = {"icon": "🔻", "title": "Vila Poyi!", "en": "Price Slump",
                "text": "The market is flooded: {name} price falls."}

# Rumors must stay ambiguous: a boom and a bust of the same commodity share one rumor, and all
# three global events share another. tests/test_theme.py enforces this.
COMMODITY_RUMOR = "Traders at the kadavu are whispering about {name}…"
GLOBAL_RUMOR = "Dark clouds are gathering over the harbour…"

# ---------------------------------------------------------------------------
# Short reactions (shown at most once per round, or on the winner screen).
# ---------------------------------------------------------------------------
REACTIONS: dict[str, dict[str, str]] = {
    "profit": {"text": "Entha oru labham!", "en": "What a profit!"},
    "caught": {"text": "Kallan pidichu!", "en": "Caught the thief!"},
    "risk":   {"text": "Risk edukkua alle?", "en": "Taking a risk, eh?"},
    "winner": {"text": "Adipoli! Bazaarile Raja!", "en": "Brilliant! King of the bazaar!"},
}
PROFIT_REACTION_THRESHOLD = 10  # net-worth gain in one round that earns "Entha oru labham!"

ACTION_LABELS = {"buy": "🛒 Buy", "sell": "💸 Sell", "hold": "✋ Hold"}

MARKET_PRESETS = {  # display name -> preset key used by app.py
    "Classic Kadavu": "Classic",
    "Village Chantha": "Bargain Bazaar",
    "Spice Lords": "High Rollers",
}


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------

def commodity_name(c: str) -> str:
    return COMMODITY_DISPLAY[c]["name"]


def commodity_en(c: str) -> str:
    return COMMODITY_DISPLAY[c]["en"]


def commodity_label(c: Optional[str], english: bool = False) -> str:
    """Plain-text label, e.g. 'Thenga' or 'Thenga (coconut)'."""
    if not c:
        return ""
    name = commodity_name(c)
    return f"{name} ({commodity_en(c).lower()})" if english else name


@lru_cache(maxsize=None)
def icon_data_uri(icon: str) -> str:
    svg = (ICON_DIR / f"{icon}.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def commodity_icon(c: str) -> str:
    return icon_data_uri(COMMODITY_DISPLAY[c]["icon"])


def card_name(card_type: Optional[str]) -> str:
    return CARD_DISPLAY[card_type]["name"] if card_type else "No card"


def card_label(card_type: Optional[str], english: bool = False) -> str:
    if not card_type:
        return "No card"
    d = CARD_DISPLAY[card_type]
    return f"{d['icon']} {d['name']}" + (f" ({d['en']})" if english else "")


def event_display(key: str) -> dict[str, str]:
    """Title, English title, icon, description and rumor for an event key."""
    if key in GLOBAL_EVENT_DISPLAY:
        d = dict(GLOBAL_EVENT_DISPLAY[key])
        d["rumor"] = GLOBAL_RUMOR
        d["commodity"] = ""
        return d
    commodity, kind = key.rsplit("_", 1)
    if commodity not in COMMODITY_DISPLAY or kind not in ("boom", "bust"):
        raise KeyError(key)
    if kind == "bust":
        base = BUST_DISPLAY
    elif commodity == "relics":
        base = PEPPER_BOOM_DISPLAY
    else:
        base = BOOM_DISPLAY
    name = commodity_name(commodity)
    return {"icon": base["icon"], "title": base["title"], "en": base["en"],
            "text": base["text"].format(name=name), "rumor": rumor_for(key), "commodity": commodity}


def event_title(key: str, english: bool = False) -> str:
    d = event_display(key)
    title = f"{d['icon']} {d['title']}"
    if d["commodity"] and d["title"] != PEPPER_BOOM_DISPLAY["title"]:
        title += f" · {commodity_name(d['commodity'])}"
    return title + (f" ({d['en']})" if english else "")


def rumor_for(key: str) -> str:
    """The public rumor for an event. Never reveals direction or which global event it is."""
    if key in GLOBAL_EVENT_DISPLAY:
        return GLOBAL_RUMOR
    commodity = key.rsplit("_", 1)[0]
    return COMMODITY_RUMOR.format(name=commodity_name(commodity))


def rumor_subject(key: str) -> Optional[str]:
    """The commodity a rumor is about (public), or None for the shared global rumor."""
    return None if key in GLOBAL_EVENT_DISPLAY else key.rsplit("_", 1)[0]


def reaction(key: str, english: bool = False) -> str:
    r = REACTIONS[key]
    return r["text"] + (f" ({r['en']})" if english else "")


# ---------------------------------------------------------------------------
# Full Bazaar rules (display only; logic and numbers live in bluff/config.py and bluff/engine.py)
# ---------------------------------------------------------------------------
RULE_MODE_DISPLAY = {
    "full": {"name": "Full Bazaar", "text": "Secret orders, perishable fruit, harbour cargo auctions and a monsoon season."},
    "classic": {"name": "Classic", "text": "The original game: trades, cards and market events only."},
}

ORDER_DISPLAY: dict[str, dict[str, str]] = {
    "pepper_exporter":      {"icon": "🌿", "name": "Kurumulaku Exporter", "en": "Pepper exporter",
                             "text": "End with 4+ Kurumulaku."},
    "cardamom_connoisseur": {"icon": "💚", "name": "Elakka Connoisseur", "en": "Cardamom buyer",
                             "text": "End with 3+ Elakka."},
    "fruit_stall":          {"icon": "🍌", "name": "Pazham Kada", "en": "Fruit stall",
                             "text": "End with 2+ Chakka and 2+ Nendran (careful: they spoil)."},
    "spice_house":          {"icon": "🏠", "name": "Spice House", "en": "Spice house",
                             "text": "End with 2+ Kurumulaku and 2+ Elakka."},
    "coconut_king":         {"icon": "🥥", "name": "Thenga Muthalali", "en": "Coconut boss",
                             "text": "End with 5+ Thenga."},
    "full_stall":           {"icon": "🧺", "name": "Ella Saadhanavum", "en": "A bit of everything",
                             "text": "End with at least 1 of every good."},
    "cash_chest":           {"icon": "💰", "name": "Panappetti", "en": "Cash box",
                             "text": "End with 150+ coins."},
    "harbour_baron":        {"icon": "⚓", "name": "Thuramukha Muthalali", "en": "Harbour boss",
                             "text": "Win 2+ cargo auctions."},
}

SEASON_DISPLAY: dict[str, dict[str, str]] = {
    "ship_delay":  {"title": "Kappal Thamasam", "en": "Ships delayed", "text": "Max 3 units per trade."},
    "godown_lock": {"title": "Godown Pootti", "en": "Pepper godowns sealed", "text": "Kurumulaku cannot be sold."},
    "wet_godowns": {"title": "Nanavulla Godown", "en": "Damp godowns",
                    "text": "Chakka and Nendran spoil when you hold 2 or more."},
}

CARGO_TITLE = "Kappal Cargo"   # "ship cargo"
CARGO_EN = "Harbour cargo auction"


def order_label(key: Optional[str], english: bool = False) -> str:
    if not key:
        return ""
    d = ORDER_DISPLAY[key]
    return f"{d['icon']} {d['name']}" + (f" ({d['en']})" if english else "")


def season_label(key: Optional[str], english: bool = False) -> str:
    if not key:
        return ""
    d = SEASON_DISPLAY[key]
    return f"🌧️ {d['title']}" + (f" ({d['en']})" if english else "")
