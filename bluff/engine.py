"""Pure, deterministic Bluff Bazaar rules.

No I/O and no clock: every function takes plain data and returns new data, so
the whole game can be unit-tested and replayed. The resolution order is
documented in docs/RULES.md and implemented step by step in `resolve_round`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional

from .config import CARD_TYPES, COMMODITIES, ORDERS, SEASON_KEYS, GameConfig, event_keys
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
    bid: int = 0  # secret bid for this round's cargo lot (Full Bazaar); 0 = no bid

    @classmethod
    def hold(cls, is_auto: bool = False) -> "Submission":
        return cls(action="hold", is_auto=is_auto)


@dataclass(frozen=True)
class Lot:
    """A public harbour cargo lot auctioned this round."""
    commodity: str
    quantity: int
    min_bid: int = 1


@dataclass(frozen=True)
class RoundRules:
    """Per-round rule modifiers. The default instance is exactly the Classic game."""
    max_qty: Optional[int] = None                 # None -> config.max_trade_qty
    unsellable: frozenset = frozenset()           # commodities that cannot be sold this round
    perishables: tuple = ()                       # commodities that can spoil
    spoil_threshold: Optional[int] = None         # None -> no spoilage
    lot: Optional[Lot] = None                     # None -> no cargo auction
    season: Optional[str] = None                  # active monsoon effect key, if any

    def trade_limit(self, config: GameConfig) -> int:
        return self.max_qty or config.max_trade_qty

    def to_public(self) -> dict[str, Any]:
        """JSON-safe form for the player view (all of this is public information)."""
        return {"max_qty": self.max_qty, "unsellable": sorted(self.unsellable),
                "perishables": list(self.perishables), "spoil_threshold": self.spoil_threshold,
                "lot": ({"commodity": self.lot.commodity, "quantity": self.lot.quantity,
                         "min_bid": self.lot.min_bid} if self.lot else None),
                "season": self.season}

    @classmethod
    def from_public(cls, d: Optional[Mapping[str, Any]]) -> "RoundRules":
        if not d:
            return cls()
        lot = d.get("lot")
        return cls(max_qty=d.get("max_qty"), unsellable=frozenset(d.get("unsellable") or ()),
                   perishables=tuple(d.get("perishables") or ()), spoil_threshold=d.get("spoil_threshold"),
                   lot=Lot(lot["commodity"], lot["quantity"], lot["min_bid"]) if lot else None,
                   season=d.get("season"))


CLASSIC = RoundRules()


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


def schedule_season(seed: int) -> str:
    """The monsoon season effect for this game (public from the start)."""
    return random.Random(f"season:{seed}").choice(SEASON_KEYS)


def schedule_lots(seed: int, config: GameConfig) -> list[Lot]:
    """One public cargo lot per round."""
    rng = random.Random(f"lots:{seed}")
    return [Lot(rng.choice(COMMODITIES), rng.randint(config.lot_min_qty, config.lot_max_qty), config.lot_min_bid)
            for _ in range(config.num_rounds)]


def deal_orders(seed: int, player_ids: list[str]) -> dict[str, str]:
    """One secret merchant order per player, drawn without replacement."""
    keys = random.Random(f"orders:{seed}").sample(sorted(ORDERS), len(player_ids))
    return dict(zip(player_ids, keys))


def lot_reserve(config: GameConfig, lot: Lot, prices: Mapping[str, int]) -> int:
    """Minimum bid for a lot this round: a share of its value at the round's prices."""
    value = lot.quantity * prices[lot.commodity]
    return max(lot.min_bid, (value * config.lot_reserve_pct + 99) // 100)


def in_season(config: GameConfig, round_no: int) -> bool:
    return config.full and config.season_start <= round_no <= config.season_end


def round_rules(config: GameConfig, seed: int, round_no: int) -> RoundRules:
    """All rule modifiers for one round. Classic rooms always get CLASSIC."""
    if not config.full:
        return CLASSIC
    season = schedule_season(seed) if in_season(config, round_no) else None
    return RoundRules(
        max_qty=config.season_max_qty if season == "ship_delay" else None,
        unsellable=frozenset({"relics"}) if season == "godown_lock" else frozenset(),
        perishables=config.perishables,
        spoil_threshold=config.wet_spoil_threshold if season == "wet_godowns" else config.spoil_threshold,
        lot=schedule_lots(seed, config)[round_no - 1],
        season=season,
    )


def order_met(order_key: str, player: PlayerState, lots_won: int = 0) -> bool:
    order = ORDERS[order_key]
    if any(player.held(c) < n for c, n in order.get("need", {}).items()):
        return False
    if player.coins < order.get("coins", 0):
        return False
    return lots_won >= order.get("lots", 0)


def order_bonus(order_key: Optional[str], player: PlayerState, lots_won: int = 0) -> int:
    if not order_key:
        return 0
    return ORDERS[order_key]["bonus"] if order_met(order_key, player, lots_won) else 0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _check_trade(config: GameConfig, player: PlayerState, prices: Mapping[str, int], sub: Submission,
                 rules: RoundRules = CLASSIC) -> None:
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
    limit = rules.trade_limit(config)
    if not 1 <= sub.quantity <= limit:
        raise InvalidAction(f"Quantity must be between 1 and {limit}.")
    if sub.action == "buy":
        cost = sub.quantity * prices[sub.commodity]
        if cost > player.coins:
            raise InvalidAction(f"Insufficient funds: costs {cost}, you have {player.coins}.")
        if player.units() + sub.quantity > config.inventory_limit:
            raise InvalidAction(
                f"Inventory full: you hold {player.units()}/{config.inventory_limit} units.")
    else:
        if sub.commodity in rules.unsellable:
            raise InvalidAction(f"{commodity_name(sub.commodity)} cannot be sold this round (monsoon).")
        if player.held(sub.commodity) < sub.quantity:
            raise InvalidAction(f"You only hold {player.held(sub.commodity)} {commodity_name(sub.commodity)}.")


def _after_trade(player: PlayerState, prices: Mapping[str, int], sub: Submission) -> tuple[int, int]:
    """(coins, units) the player would have after their trade settles."""
    if sub.action == "buy":
        return player.coins - sub.quantity * prices[sub.commodity], player.units() + sub.quantity
    if sub.action == "sell":
        return player.coins + sub.quantity * prices[sub.commodity], player.units() - sub.quantity
    return player.coins, player.units()


def _check_bid(config: GameConfig, player: PlayerState, prices: Mapping[str, int], sub: Submission,
               rules: RoundRules) -> None:
    bid = sub.bid
    if not isinstance(bid, int) or isinstance(bid, bool) or bid < 0:
        raise InvalidAction("Bid must be a whole number of coins.")
    if bid == 0:
        return
    lot = rules.lot
    if lot is None:
        raise InvalidAction("There is no cargo to bid on this round.")
    reserve = lot_reserve(config, lot, prices)
    if bid < reserve:
        raise InvalidAction(f"Minimum bid for this cargo is {reserve}.")
    coins, units = _after_trade(player, prices, sub)
    if bid > coins:
        raise InvalidAction(f"Bid too high: after your trade you would have {coins} coins.")
    if units + lot.quantity > config.inventory_limit:
        raise InvalidAction(f"No room for the cargo: you would hold {units}/{config.inventory_limit} units.")


def validate_submission(
    config: GameConfig,
    player: PlayerState,
    prices: Mapping[str, int],
    sub: Submission,
    hand: Iterable[str],
    opponent_ids: Iterable[str],
    card_played_this_round: bool = False,
    rules: RoundRules = CLASSIC,
) -> None:
    """Raise InvalidAction if `sub` is not legal for `player` right now.

    `hand` is the list of unplayed card types the server loaded for the player;
    `opponent_ids` are the other player ids in the room.
    """
    _check_trade(config, player, prices, sub, rules)
    _check_bid(config, player, prices, sub, rules)
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
    rules: RoundRules = CLASSIC,
) -> RoundOutcome:
    """Resolve one round. `submissions` must contain every player (use auto-holds).

    With the default CLASSIC rules this is exactly the original game; Full Bazaar rules add the
    cargo auction (after trades) and spoilage (last).
    """
    order = sorted(players, key=lambda p: p.seat)
    missing = [p.player_id for p in order if p.player_id not in submissions]
    if missing:
        raise InvalidAction(f"missing submissions for {missing}")

    p0 = dict(prices)
    start_worth = {p.player_id: net_worth(p, p0) for p in order}
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
                _check_trade(config, current, p0, sub, rules)
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

    # 2. Harbour cargo auction (Full Bazaar). Highest valid bid wins and pays it; exact ties: unsold.
    auction: Optional[dict[str, Any]] = None
    cargo_value = {pid: 0 for pid in coins}
    if rules.lot is not None:
        lot = rules.lot
        valid_bids: dict[str, int] = {}
        bids_public: dict[str, int] = {}
        for p in order:
            bid = submissions[p.player_id].bid
            if bid <= 0:
                continue
            bids_public[p.player_id] = bid
            if (bid >= lot_reserve(config, lot, p0) and bid <= coins[p.player_id]
                    and sum(inv[p.player_id].values()) + lot.quantity <= config.inventory_limit):
                valid_bids[p.player_id] = bid
        winner, outcome = None, "no bids"
        if valid_bids:
            top = max(valid_bids.values())
            leaders = [pid for pid, b in valid_bids.items() if b == top]
            if len(leaders) == 1:
                winner, outcome = leaders[0], "sold"
                coins[winner] -= top
                inv[winner][lot.commodity] += lot.quantity
                cargo_value[winner] = lot.quantity * p0[lot.commodity] - top
            else:
                outcome = "tie"
        auction = {"commodity": lot.commodity, "quantity": lot.quantity, "bids": bids_public,
                   "reserve": lot_reserve(config, lot, p0),
                   "winner": winner, "price": valid_bids.get(winner) if winner else None, "outcome": outcome}

    # 3. Price movement: event + cards + demand, bounded.
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

    # 4. Double Down.
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

    # 5. Shield compensation for crash losses.
    for pid in shielded:
        refund = sum(inv[pid][c] * min(max(0, p0[c] - p1[c]), crash_amount[c])
                     for c in COMMODITIES if crash_amount[c])
        if refund:
            coins[pid] += refund
            add_effect(pid, "shield_refund", refund)

    # 6. Event coin effects.
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

    # 7. Tax Collector, in the collector's seat order.
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

    # Value change from prices, measured on holdings after trades and cargo (before spoilage).
    price_moves = {pid: sum(inv[pid][c] * (p1[c] - p0[c]) for c in COMMODITIES) for pid in coins}

    # 8. Spoilage (Full Bazaar): holding >= threshold of a perishable loses one unit of it.
    #    Cargo is fresh off the ship: units won this round don't count until next round.
    spoiled_value = {pid: 0 for pid in coins}
    if rules.spoil_threshold is not None:
        fresh = {pid: {c: 0 for c in COMMODITIES} for pid in coins}
        if auction and auction["winner"]:
            fresh[auction["winner"]][auction["commodity"]] = auction["quantity"]
        for p in order:
            for c in rules.perishables:
                if inv[p.player_id][c] - fresh[p.player_id][c] >= rules.spoil_threshold:
                    inv[p.player_id][c] -= 1
                    spoiled_value[p.player_id] -= p1[c]
                    add_effect(p.player_id, "spoiled", 0, commodity=c, units=1)

    # 9. Snapshot.
    new_players = [replace(p, coins=coins[p.player_id], inventory=dict(inv[p.player_id])) for p in order]
    for p in new_players:
        assert p.coins >= 0 and all(q >= 0 for q in p.inventory.values())

    def breakdown(p: PlayerState) -> dict[str, int]:
        pid = p.player_id
        end = net_worth(p, p1)
        cards_events = sum(e["amount"] for e in effects[pid])
        parts = {"start": start_worth[pid], "end": end, "trade_cash": trades[pid]["quantity"] * (
                     trades[pid]["price"] or 0) * (-1 if trades[pid]["action"] == "buy" else 1)
                 if trades[pid]["executed"] else 0,
                 "price_moves": price_moves[pid], "cards_events": cards_events,
                 "cargo": cargo_value[pid], "spoiled": spoiled_value[pid]}
        # Trades at the quoted price are value-neutral, so these parts reconcile exactly.
        assert end - start_worth[pid] == (parts["price_moves"] + cards_events + parts["cargo"] + parts["spoiled"])
        return parts

    summary = {
        "round": round_no,
        "event": event_key,
        "prices_before": p0,
        "prices_after": p1,
        "movement": movement,
        "auction": auction,
        "season": rules.season,
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
                "bid": submissions[p.player_id].bid,
                "breakdown": breakdown(p),
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


def final_ranking(players: list[PlayerState], prices: Mapping[str, int],
                  bonuses: Optional[Mapping[str, int]] = None) -> list[dict[str, Any]]:
    """Competition ranking by net worth (incl. order bonus), then coins; exact ties share a rank."""
    rows = []
    for p in players:
        bonus = (bonuses or {}).get(p.player_id, 0)
        goods = net_worth(p, prices) - p.coins
        rows.append({"player_id": p.player_id, "name": p.name, "seat": p.seat,
                     "net_worth": p.coins + goods + bonus, "coins": p.coins, "inventory_value": goods,
                     "order_bonus": bonus})
    rows.sort(key=lambda r: (-r["net_worth"], -r["coins"], r["seat"]))
    for i, row in enumerate(rows):
        prev = rows[i - 1] if i else None
        if prev and (prev["net_worth"], prev["coins"]) == (row["net_worth"], row["coins"]):
            row["rank"] = prev["rank"]
        else:
            row["rank"] = i + 1
    return rows
