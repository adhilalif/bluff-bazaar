"""Heuristic bot players. They are diagnostics, not strong AI.

Each bot sees only a `BotView`: what a real player sees on their own screen (prices and history,
their own coins/goods/hand/order, the public rumor and cargo lot, the season, and their own
Insider Tip reveal). Bots never see opponents' hands or pending moves.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from ..config import CARD_TARGETS, COMMODITIES, ORDERS, GameConfig
from ..engine import CardPlay, PlayerState, RoundRules, Submission, lot_reserve


@dataclass
class BotView:
    config: GameConfig
    round_no: int
    prices: dict[str, int]
    history: list[dict[str, int]]          # prices at the start of each round so far (incl. this one)
    me: PlayerState
    hand: list[str]                        # unplayed card types
    opponents: list[tuple[str, int]]       # (player_id, last public net worth)
    rumor_subject: Optional[str]           # commodity the rumor is about, or None (global rumor)
    rules: RoundRules
    order: Optional[str] = None
    insider_event: Optional[str] = None    # set only if this bot used Insider Tip this round
    insider_hint: Optional[int] = None     # partial-hint experiment: +1 good news, -1 bad, 0 neutral
    card_used: bool = False                # Insider Tip already used this round


class Bot:
    name = "base"
    use_tips = True

    def __init__(self, rng: random.Random):
        self.rng = rng

    def wants_insider(self, v: BotView) -> bool:
        return False

    def decide(self, v: BotView) -> Submission:
        raise NotImplementedError

    # ---- helpers -----------------------------------------------------------
    @staticmethod
    def max_buy(v: BotView, c: str, coins: Optional[int] = None) -> int:
        coins = v.me.coins if coins is None else coins
        space = v.config.inventory_limit - v.me.units()
        return max(0, min(v.rules.trade_limit(v.config), coins // v.prices[c], space))

    @staticmethod
    def max_sell(v: BotView, c: str) -> int:
        if c in v.rules.unsellable:
            return 0
        return min(v.rules.trade_limit(v.config), v.me.held(c))

    @staticmethod
    def ratio(v: BotView, c: str) -> float:
        return v.prices[c] / v.history[0][c]

    def richest_opponent(self, v: BotView) -> str:
        return max(v.opponents, key=lambda o: (o[1], self.rng.random()))[0]


class Passive(Bot):
    name = "passive"

    def decide(self, v):
        return Submission.hold()


class RandomTrader(Bot):
    name = "random"

    def wants_insider(self, v):
        return self.rng.random() < 0.3

    def decide(self, v):
        action = self.rng.choice(["buy", "sell", "hold"])
        commodity, qty = None, 0
        if action == "buy":
            commodity = self.rng.choice(COMMODITIES)
            n = self.max_buy(v, commodity)
            qty = self.rng.randint(1, n) if n else 0
        elif action == "sell":
            held = [c for c in COMMODITIES if self.max_sell(v, c)]
            if held:
                commodity = self.rng.choice(held)
                qty = self.rng.randint(1, self.max_sell(v, commodity))
        if not qty:
            action, commodity, qty = "hold", None, 0
        card = None
        playable = [c for c in v.hand if c != "insider_tip"]
        if playable and not v.card_used and self.rng.random() < 0.3:
            t = self.rng.choice(playable)
            if t == "double_down" and action == "hold":
                t = None
            if t:
                card = CardPlay(t, self.rng.choice(COMMODITIES) if CARD_TARGETS[t] == "commodity" else None,
                                self.rng.choice(v.opponents)[0] if CARD_TARGETS[t] == "player" else None)
        bid = 0
        if v.rules.lot and self.rng.random() < 0.25:
            reserve = lot_reserve(v.config, v.rules.lot, v.prices)
            spend = v.me.coins - (qty * v.prices[commodity] if action == "buy" else 0)
            if spend >= reserve:
                bid = self.rng.randint(reserve, max(reserve, min(spend, reserve * 2)))
        return Submission(action, commodity, qty, card, bid=bid)


class TrendTrader(Bot):
    """Buys goods that are cheap relative to their starting price; sells after they rise."""
    name = "trend"
    sell_at = 1.15
    buy_below = 1.10
    bid_fraction = 0.65
    card_rate = 0.4

    def spoil_risk(self, v: BotView, c: str, extra: int = 0) -> bool:
        return (v.rules.spoil_threshold is not None and c in v.rules.perishables
                and v.me.held(c) + extra >= v.rules.spoil_threshold)

    def trade(self, v: BotView) -> tuple[str, Optional[str], int]:
        # Dump perishables that are about to spoil.
        for c in v.rules.perishables:
            if self.spoil_risk(v, c) and self.max_sell(v, c):
                return "sell", c, min(self.max_sell(v, c), v.me.held(c) - v.rules.spoil_threshold + 1)
        risers = [c for c in COMMODITIES if self.max_sell(v, c) and self.ratio(v, c) >= self.sell_at]
        if risers:
            c = max(risers, key=lambda c: self.ratio(v, c))
            return "sell", c, self.max_sell(v, c)
        cheap = sorted((c for c in COMMODITIES if self.ratio(v, c) <= self.buy_below), key=lambda c: self.ratio(v, c))
        for c in cheap:
            n = min(3, self.max_buy(v, c))
            while n and self.spoil_risk(v, c, n):
                n -= 1
            if n:
                return "buy", c, n
        return "hold", None, 0

    def card(self, v: BotView, action: str, commodity: Optional[str]) -> Optional[CardPlay]:
        if v.card_used or self.rng.random() > self.card_rate:
            return None
        playable = [c for c in v.hand if c != "insider_tip"]
        if not playable:
            return None
        goods_value = sum(v.me.held(c) * v.prices[c] for c in COMMODITIES)
        if "shield" in playable and goods_value > 60:
            return CardPlay("shield")
        if "tax_collector" in playable:
            return CardPlay("tax_collector", target_player=self.richest_opponent(v))
        if "double_down" in playable and action == "buy":
            return CardPlay("double_down")
        if "market_pump" in playable:
            held = max(COMMODITIES, key=lambda c: (v.me.held(c) + (3 if c == commodity and action == "buy" else 0)))
            return CardPlay("market_pump", held)
        if "market_crash" in playable:
            target = max((c for c in COMMODITIES if not v.me.held(c)), key=lambda c: v.prices[c], default=None)
            if target:
                return CardPlay("market_crash", target)
        return None

    def bid(self, v: BotView, action: str, commodity: Optional[str], qty: int) -> int:
        lot = v.rules.lot
        if not lot:
            return 0
        coins = v.me.coins - (qty * v.prices[commodity] if action == "buy" else 0) \
            + (qty * v.prices[commodity] if action == "sell" else 0)
        units = v.me.units() + (qty if action == "buy" else -qty if action == "sell" else 0)
        if units + lot.quantity > v.config.inventory_limit:
            return 0
        if self.spoil_risk(v, lot.commodity, lot.quantity):
            return 0
        value = lot.quantity * v.prices[lot.commodity]
        reserve = lot_reserve(v.config, lot, v.prices)
        bid = max(reserve, int(value * self.bid_fraction * self.rng.uniform(0.9, 1.3)))
        return bid if bid <= coins else 0

    def decide(self, v):
        action, commodity, qty = self.trade(v)
        return Submission(action, commodity, qty, self.card(v, action, commodity),
                          bid=self.bid(v, action, commodity, qty))


class PumpTrader(TrendTrader):
    """Trend trading, but always pumps what it holds and crashes what it doesn't."""
    name = "pump"
    card_rate = 1.0

    def card(self, v, action, commodity):
        if v.card_used:
            return None
        if "market_pump" in v.hand:
            target = commodity if action == "buy" else max(COMMODITIES, key=v.me.held)
            return CardPlay("market_pump", target)
        if "market_crash" in v.hand:
            target = max((c for c in COMMODITIES if not v.me.held(c)), key=lambda c: v.prices[c], default=None)
            if target:
                return CardPlay("market_crash", target)
        return super().card(v, action, commodity)


class InsiderTrader(TrendTrader):
    """Trend trading that uses Insider Tip as early as possible and acts on the reveal."""
    name = "insider"

    def __init__(self, rng, use_tips: bool = True):
        super().__init__(rng)
        self.use_tips = use_tips

    def wants_insider(self, v):
        return self.use_tips and v.rumor_subject is not None

    def decide(self, v):
        direction = None
        target = v.rumor_subject
        if v.insider_event and target:
            direction = 1 if v.insider_event.endswith("_boom") else -1 if v.insider_event.endswith("_bust") else 0
        elif v.insider_hint is not None and target:
            direction = v.insider_hint
        if direction == 1 and self.max_buy(v, target):
            n = self.max_buy(v, target)
            while n and self.spoil_risk(v, target, n):
                n -= 1
            if n:
                card = CardPlay("double_down") if "double_down" in v.hand and not v.card_used else None
                return Submission("buy", target, n, card, bid=self.bid(v, "buy", target, n))
        if direction == -1 and self.max_sell(v, target):
            n = self.max_sell(v, target)
            return Submission("sell", target, n, None, bid=self.bid(v, "sell", target, n))
        return super().decide(v)


class OrderChaser(TrendTrader):
    """Trend trading that steers purchases and bids toward its secret merchant order."""
    name = "order"

    def needs(self, v: BotView) -> dict[str, int]:
        if not v.order:
            return {}
        return {c: n - v.me.held(c) for c, n in ORDERS[v.order].get("need", {}).items() if v.me.held(c) < n}

    def trade(self, v):
        need = self.needs(v)
        for c in sorted(need, key=lambda c: v.prices[c]):
            n = min(need[c], self.max_buy(v, c))
            while n and self.spoil_risk(v, c, n):
                n -= 1
            if n:
                return "buy", c, n
        action, c, q = super().trade(v)
        if action == "sell" and v.order and c in ORDERS[v.order].get("need", {}):
            keep = ORDERS[v.order]["need"][c]
            q = min(q, max(0, v.me.held(c) - keep))
            if not q:
                return "hold", None, 0
        return action, c, q

    def bid(self, v, action, commodity, qty):
        base = super().bid(v, action, commodity, qty)
        lot = v.rules.lot
        if base and lot and v.order and (lot.commodity in self.needs(v) or v.order == "harbour_baron"):
            coins = v.me.coins - (qty * v.prices[commodity] if action == "buy" else 0)
            return min(int(base * 1.4), max(coins, 0)) or base
        return base


STRATEGIES = {cls.name: cls for cls in (Passive, RandomTrader, TrendTrader, PumpTrader, InsiderTrader, OrderChaser)}
