"""Pure, deterministic Bluff Bazaar rules.

No I/O and no clock: every function takes plain data and returns new data, so
the whole game can be unit-tested and replayed. The resolution order is
documented in docs/RULES.md and implemented step by step in `resolve_round`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional

from .config import CARD_TYPES, COMMODITIES, GameConfig, event_keys
from .theme import card_name, commodity_name

ACTIONS = ("buy", "sell", "hold")
SUBMITTABLE_CARDS = tuple(c for c in CARD_TYPES if c != "insider_tip")


class InvalidAction(ValueError):
    """A player action that the rules do not allow. Message is user-facing."""


@dataclass(frozen=True)
class PlayerState:
    player_id: str
    seat: int
    name: str
    coins: int
    inventory: Mapping[str, int] = field(default_factory=dict)

    def units(self) -> int:
        return sum(self.inventory.values())

    def held(self, commodity: str) -> int:
        return self.inventory.get(commodity, 0)


@dataclass(frozen=True)
class CardPlay:
    card_type: str
    target_commodity: Optional[str] = None
    target_player: Optional[str] = None


@dataclass(frozen=True)
class Submission:
    action: str = "hold"
    commodity: Optional[str] = None
    quantity: int = 0
    card: Optional[CardPlay] = None
    is_auto: bool = False

    @classmethod
    def hold(cls, is_auto: bool = False) -> "Submission":
        return cls(action="hold", is_auto=is_auto)


@dataclass(frozen=True)
class RoundOutcome:
    prices: dict[str, int]
    players: list[PlayerState]
    summary: dict[str, Any]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def schedule_events(seed: int, config: GameConfig) -> list[str]:
    """The secret event for each round, drawn without replacement."""
    rng = random.Random(f"events:{seed}")
    return rng.sample(event_keys(), config.num_rounds)


def deal_hands(seed: int, player_ids: list[str], config: GameConfig) -> dict[str, list[str]]:
    """Shuffle a deck with `deck_copies` of each card and deal `hand_size` each, in seat order."""
    if len(player_ids) != config.num_players:
        raise InvalidAction(f"need exactly {config.num_players} players to deal")
    deck = [card for card in CARD_TYPES for _ in range(config.deck_copies)]
    random.Random(f"deal:{seed}").shuffle(deck)
    return {pid: deck[i * config.hand_size:(i + 1) * config.hand_size] for i, pid in enumerate(player_ids)}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _check_trade(config: GameConfig, player: PlayerState, prices: Mapping[str, int], sub: Submission) -> None:
    if sub.action not in ACTIONS:
        raise InvalidAction("Unknown action.")
    if sub.action == "hold":
        if sub.commodity is not None or sub.quantity != 0:
            raise InvalidAction("Hold cannot include a commodity or quantity.")
        return
    if sub.commodity not in COMMODITIES:
        raise InvalidAction("Choose a valid commodity.")
    if not isinstance(sub.quantity, int) or isinstance(sub.quantity, bool):
        raise InvalidAction("Quantity must be a whole number.")
    if not 1 <= sub.quantity <= config.max_trade_qty:
        raise InvalidAction(f"Quantity must be between 1 and {config.max_trade_qty}.")
    if sub.action == "buy":
        cost = sub.quantity * prices[sub.commodity]
        if cost > player.coins:
            raise InvalidAction(f"Insufficient funds: costs {cost}, you have {player.coins}.")
        if player.units() + sub.quantity > config.inventory_limit:
            raise InvalidAction(
                f"Inventory full: you hold {player.units()}/{config.inventory_limit} units.")
    else:
        if player.held(sub.commodity) < sub.quantity:
            raise InvalidAction(f"You only hold {player.held(sub.commodity)} {commodity_name(sub.commodity)}.")


def validate_submission(
    config: GameConfig,
    player: PlayerState,
    prices: Mapping[str, int],
    sub: Submission,
    hand: Iterable[str],
    opponent_ids: Iterable[str],
    card_played_this_round: bool = False,
) -> None:
    """Raise InvalidAction if `sub` is not legal for `player` right now.

    `hand` is the list of unplayed card types the server loaded for the player;
    `opponent_ids` are the other player ids in the room.
    """
    _check_trade(config, player, prices, sub)
    card = sub.card
    if card is None:
        return
    if card_played_this_round:
        raise InvalidAction("You already played a card this round.")
    if card.card_type not in CARD_TYPES:
        raise InvalidAction("Unknown card.")
    if card.card_type not in list(hand):
        raise InvalidAction("That card is not in your hand.")
    if card.card_type == "insider_tip":
        raise InvalidAction(f"{card_name('insider_tip')} is used immediately, not with a trade.")
    if card.card_type in ("market_pump", "market_crash"):
        if card.target_commodity not in COMMODITIES:
            raise InvalidAction("Choose a commodity to target.")
        if card.target_player is not None:
            raise InvalidAction("This card cannot target a player.")
    elif card.card_type == "tax_collector":
        if card.target_player == player.player_id:
            raise InvalidAction("You cannot tax yourself.")
        if card.target_player not in set(opponent_ids):
            raise InvalidAction("Choose an opponent to tax.")
        if card.target_commodity is not None:
            raise InvalidAction("This card cannot target a commodity.")
    else:
        if card.target_commodity is not None or card.target_player is not None:
            raise InvalidAction("This card takes no target.")
        if card.card_type == "double_down" and sub.action == "hold":
            raise InvalidAction(f"{card_name('double_down')} needs a buy or sell.")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def demand_delta(config: GameConfig, net_units: int) -> int:
    magnitude = min(config.demand_cap, abs(net_units) // config.demand_divisor)
    return magnitude if net_units > 0 else -magnitude


def move_price(config: GameConfig, p0: int, raw_delta: int) -> int:
    swing = config.max_swing(p0)
    return _clamp(p0 + _clamp(raw_delta, -swing, swing), config.price_min, config.price_max)


def net_worth(player: PlayerState, prices: Mapping[str, int]) -> int:
    return player.coins + sum(q * prices[c] for c, q in player.inventory.items())


def resolve_round(
    config: GameConfig,
    round_no: int,
    prices: Mapping[str, int],
    players: list[PlayerState],
    submissions: Mapping[str, Submission],
    event_key: str,
) -> RoundOutcome:
    """Resolve one round. `submissions` must contain every player (use auto-holds)."""
    order = sorted(players, key=lambda p: p.seat)
    missing = [p.player_id for p in order if p.player_id not in submissions]
    if missing:
        raise InvalidAction(f"missing submissions for {missing}")

    p0 = dict(prices)
    coins = {p.player_id: p.coins for p in order}
    inv = {p.player_id: {c: p.held(c) for c in COMMODITIES} for p in order}
    effects: dict[str, list[dict[str, Any]]] = {p.player_id: [] for p in order}

    def card_of(pid: str) -> Optional[CardPlay]:
        return submissions[pid].card

    shielded = {pid for pid in coins if card_of(pid) and card_of(pid).card_type == "shield"}

    # 1. Trade settlement at P0 (re-validated against current state).
    trades: dict[str, dict[str, Any]] = {}
    for p in order:
        sub = submissions[p.player_id]
        trade = {"action": sub.action, "commodity": sub.commodity, "quantity": sub.quantity,
                 "executed": False, "price": None, "reason": None, "auto": sub.is_auto}
        if sub.action != "hold":
            current = replace(p, coins=coins[p.player_id], inventory=inv[p.player_id])
            try:
                _check_trade(config, current, p0, sub)
            except InvalidAction as exc:
                trade["reason"] = str(exc)
            else:
                price = p0[sub.commodity]
                if sub.action == "buy":
                    coins[p.player_id] -= sub.quantity * price
                    inv[p.player_id][sub.commodity] += sub.quantity
                else:
                    coins[p.player_id] += sub.quantity * price
                    inv[p.player_id][sub.commodity] -= sub.quantity
                trade.update(executed=True, price=price)
        trades[p.player_id] = trade

    # 2. Price movement: event + cards + demand, bounded.
    event_delta = {c: 0 for c in COMMODITIES}
    if event_key.endswith("_boom") or event_key.endswith("_bust"):
        commodity, kind = event_key.rsplit("_", 1)
        event_delta[commodity] = config.event_move(commodity) * (1 if kind == "boom" else -1)
    card_delta = {c: 0 for c in COMMODITIES}
    crash_amount = {c: 0 for c in COMMODITIES}
    for p in order:
        card = card_of(p.player_id)
        if card and card.card_type in ("market_pump", "market_crash"):
            move = config.card_move(p0[card.target_commodity])
            if card.card_type == "market_pump":
                card_delta[card.target_commodity] += move
            else:
                card_delta[card.target_commodity] -= move
                crash_amount[card.target_commodity] += move
    net_units = {c: 0 for c in COMMODITIES}
    for t in trades.values():
        if t["executed"]:
            net_units[t["commodity"]] += t["quantity"] if t["action"] == "buy" else -t["quantity"]
    p1: dict[str, int] = {}
    movement: dict[str, dict[str, int]] = {}
    for c in COMMODITIES:
        d_demand = demand_delta(config, net_units[c])
        raw = event_delta[c] + card_delta[c] + d_demand
        p1[c] = move_price(config, p0[c], raw)
        movement[c] = {"before": p0[c], "after": p1[c], "event": event_delta[c], "cards": card_delta[c],
                       "demand": d_demand, "net_units": net_units[c]}

    def add_effect(pid: str, kind: str, amount: int, **extra: Any) -> None:
        effects[pid].append({"kind": kind, "amount": amount, **extra})

    # 3. Double Down.
    for p in order:
        card, t = card_of(p.player_id), trades[p.player_id]
        if not card or card.card_type != "double_down":
            continue
        if not t["executed"]:
            add_effect(p.player_id, "double_down", 0, note="trade failed")
            continue
        swing = (p1[t["commodity"]] - p0[t["commodity"]]) * t["quantity"]
        if t["action"] == "sell":
            swing = -swing
        if swing < 0:
            swing = -min(coins[p.player_id], -swing)
        coins[p.player_id] += swing
        add_effect(p.player_id, "double_down", swing)

    # 4. Shield compensation for crash losses.
    for pid in shielded:
        refund = sum(inv[pid][c] * min(max(0, p0[c] - p1[c]), crash_amount[c])
                     for c in COMMODITIES if crash_amount[c])
        if refund:
            coins[pid] += refund
            add_effect(pid, "shield_refund", refund)

    # 5. Event coin effects.
    if event_key == "festival":
        for p in order:
            if trades[p.player_id]["executed"]:
                coins[p.player_id] += config.festival_bonus
                add_effect(p.player_id, "festival", config.festival_bonus)
    elif event_key == "customs":
        for p in order:
            over = sum(inv[p.player_id].values()) - config.customs_threshold
            if over <= 0:
                continue
            if p.player_id in shielded:
                add_effect(p.player_id, "customs", 0, note="blocked by Shield")
                continue
            fee = min(coins[p.player_id], over * config.customs_fee)
            coins[p.player_id] -= fee
            add_effect(p.player_id, "customs", -fee)

    # 6. Tax Collector, in the collector's seat order.
    for p in order:
        card = card_of(p.player_id)
        if not card or card.card_type != "tax_collector":
            continue
        target = card.target_player
        if target not in coins or target == p.player_id:
            add_effect(p.player_id, "tax_collected", 0, note="invalid target")
            continue
        if target in shielded:
            add_effect(p.player_id, "tax_collected", 0, target=target, note="blocked by Shield")
            add_effect(target, "tax_paid", 0, by=p.player_id, note="blocked by Shield")
            continue
        tax = min(config.tax_cap, coins[target] * config.tax_pct // 100)
        coins[target] -= tax
        coins[p.player_id] += tax
        add_effect(p.player_id, "tax_collected", tax, target=target)
        add_effect(target, "tax_paid", -tax, by=p.player_id)

    # 7. Snapshot.
    new_players = [replace(p, coins=coins[p.player_id], inventory=dict(inv[p.player_id])) for p in order]
    for p in new_players:
        assert p.coins >= 0 and all(q >= 0 for q in p.inventory.values())
    summary = {
        "round": round_no,
        "event": event_key,
        "prices_before": p0,
        "prices_after": p1,
        "movement": movement,
        "players": [
            {
                "player_id": p.player_id,
                "seat": p.seat,
                "name": p.name,
                "trade": trades[p.player_id],
                "card": _card_public(card_of(p.player_id)),
                "effects": effects[p.player_id],
                "coins_after": p.coins,
                "net_worth": net_worth(p, p1),
            }
            for p in new_players
        ],
    }
    return RoundOutcome(prices=p1, players=new_players, summary=summary)


def _card_public(card: Optional[CardPlay]) -> Optional[dict[str, Any]]:
    if card is None:
        return None
    return {"type": card.card_type, "target_commodity": card.target_commodity,
            "target_player": card.target_player}


def final_ranking(players: list[PlayerState], prices: Mapping[str, int]) -> list[dict[str, Any]]:
    """Competition ranking by net worth, then coins; exact ties share a rank."""
    rows = []
    for p in players:
        nw = net_worth(p, prices)
        rows.append({"player_id": p.player_id, "name": p.name, "seat": p.seat, "net_worth": nw,
                     "coins": p.coins, "inventory_value": nw - p.coins})
    rows.sort(key=lambda r: (-r["net_worth"], -r["coins"], r["seat"]))
    for i, row in enumerate(rows):
        prev = rows[i - 1] if i else None
        if prev and (prev["net_worth"], prev["coins"]) == (row["net_worth"], row["coins"]):
            row["rank"] = prev["rank"]
        else:
            row["rank"] = i + 1
    return rows
