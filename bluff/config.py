"""Static game data and per-room configuration for Bluff Bazaar."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

COMMODITIES: tuple[str, ...] = ("crystals", "spices", "energy", "metals", "relics")

COMMODITY_INFO: dict[str, dict[str, str]] = {
    "crystals": {"icon": "💎", "label": "Crystals"},
    "spices": {"icon": "🌶️", "label": "Spices"},
    "energy": {"icon": "⚡", "label": "Energy"},
    "metals": {"icon": "⚙️", "label": "Metals"},
    "relics": {"icon": "🏺", "label": "Relics"},
}

DEFAULT_STARTING_PRICES: dict[str, int] = {
    "crystals": 20,
    "spices": 10,
    "energy": 15,
    "metals": 12,
    "relics": 25,
}

# Card keys and how each card is targeted.
CARD_TYPES: tuple[str, ...] = (
    "market_pump", "market_crash", "shield", "insider_tip", "tax_collector", "double_down",
)
CARD_INFO: dict[str, dict[str, str]] = {
    "market_pump": {"icon": "📈", "name": "Market Pump", "target": "commodity",
                    "text": "Push a commodity's price up by 25% (min +2)."},
    "market_crash": {"icon": "📉", "name": "Market Crash", "target": "commodity",
                     "text": "Drag a commodity's price down by 25% (min −2)."},
    "shield": {"icon": "🛡️", "name": "Shield", "target": "none",
               "text": "Block Tax Collector and Customs; refund your crash losses."},
    "insider_tip": {"icon": "🔍", "name": "Insider Tip", "target": "instant",
                    "text": "Use now: privately reveal this round's true event."},
    "tax_collector": {"icon": "💰", "name": "Tax Collector", "target": "player",
                      "text": "Take 15% of an opponent's coins (max 15)."},
    "double_down": {"icon": "🎲", "name": "Double Down", "target": "none",
                    "text": "Your trade's price swing is paid — or charged — again."},
}

GLOBAL_RUMOR = "The bazaar feels oddly quiet…"


def commodity_rumor(commodity: str) -> str:
    return f"Traders are whispering about {COMMODITY_INFO[commodity]['label'].lower()}…"


@dataclass(frozen=True)
class GameConfig:
    num_players: int = 4
    num_rounds: int = 8
    starting_coins: int = 100
    inventory_limit: int = 10
    max_trade_qty: int = 5
    hand_size: int = 3
    deck_copies: int = 2
    starting_prices: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_STARTING_PRICES))
    price_min: int = 2
    price_max: int = 99
    max_swing_pct: int = 40
    min_swing: int = 3
    demand_divisor: int = 2
    demand_cap: int = 4
    card_move_pct: int = 25
    card_move_min: int = 2
    event_move_pct: int = 25
    event_move_min: int = 2
    tax_pct: int = 15
    tax_cap: int = 15
    festival_bonus: int = 4
    customs_threshold: int = 6
    customs_fee: int = 1
    turn_seconds: int = 90

    def __post_init__(self) -> None:
        if self.num_players != 4:
            raise ValueError("Bluff Bazaar MVP requires exactly 4 players")
        if self.num_rounds < 1:
            raise ValueError("num_rounds must be >= 1")
        if set(self.starting_prices) != set(COMMODITIES):
            raise ValueError("starting_prices must define every commodity")
        for c, p in self.starting_prices.items():
            if not isinstance(p, int) or not (self.price_min <= p <= self.price_max):
                raise ValueError(f"starting price for {c} must be an int in [{self.price_min}, {self.price_max}]")
        if self.deck_copies * len(CARD_TYPES) < self.num_players * self.hand_size:
            raise ValueError("deck too small for hands")
        if self.num_rounds > len(event_keys()):
            raise ValueError("not enough events for the number of rounds")
        for name in ("starting_coins", "inventory_limit", "max_trade_qty", "turn_seconds"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameConfig":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def event_move(self, commodity: str) -> int:
        base = self.starting_prices[commodity]
        return max(self.event_move_min, pct_round(base, self.event_move_pct))

    def card_move(self, price: int) -> int:
        return max(self.card_move_min, pct_round(price, self.card_move_pct))

    def max_swing(self, price: int) -> int:
        return max(self.min_swing, price * self.max_swing_pct // 100)


def pct_round(value: int, pct: int) -> int:
    """Integer percentage rounded half up (no float drift)."""
    return (value * pct + 50) // 100


def event_keys() -> list[str]:
    keys: list[str] = []
    for c in COMMODITIES:
        keys += [f"{c}_boom", f"{c}_bust"]
    return keys + ["calm", "festival", "customs"]


def event_info(key: str) -> dict[str, str]:
    """Display info for an event key."""
    if key == "calm":
        return {"name": "Calm Day", "icon": "🌤️", "rumor": GLOBAL_RUMOR,
                "text": "Nothing unusual happens."}
    if key == "festival":
        return {"name": "Trade Festival", "icon": "🎉", "rumor": GLOBAL_RUMOR,
                "text": "Every player whose trade executed earns a bonus."}
    if key == "customs":
        return {"name": "Customs Inspection", "icon": "🛃", "rumor": GLOBAL_RUMOR,
                "text": "Pay 1 coin per unit held above 6. Shield blocks."}
    commodity, kind = key.rsplit("_", 1)
    if commodity not in COMMODITIES or kind not in ("boom", "bust"):
        raise KeyError(key)
    label = COMMODITY_INFO[commodity]["label"]
    return {
        "name": f"{label} {'Boom' if kind == 'boom' else 'Bust'}",
        "icon": "🚀" if kind == "boom" else "🕳️",
        "rumor": commodity_rumor(commodity),
        "text": f"{label} price {'rises' if kind == 'boom' else 'falls'} sharply.",
    }
