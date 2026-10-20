"""Play complete games with bots through the real engine, and aggregate metrics."""
from __future__ import annotations

import itertools
import random
import statistics
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from ..config import CARD_TYPES, COMMODITIES, GameConfig
from ..engine import (
    InvalidAction, PlayerState, Submission, deal_hands, deal_orders, final_ranking, net_worth, order_bonus,
    resolve_round, round_rules, schedule_events, validate_submission,
)
from ..theme import rumor_subject
from .bots import Bot, BotView

BotFactory = Callable[[random.Random], Bot]


@dataclass
class GameResult:
    seed: int
    strategies: list[str]                 # by seat (index 0 = seat 1)
    ranking: list[dict]                   # engine.final_ranking rows
    worth_by_round: list[list[int]]       # [round][seat] public net worth after each round
    prices_by_round: list[dict[str, int]]
    card_plays: list[tuple[int, str, str]] = field(default_factory=list)  # (round, strategy, card)
    lots_won: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    orders: list[Optional[str]] = field(default_factory=lambda: [None] * 4)
    illegal: int = 0
    invariant_errors: list[str] = field(default_factory=list)
    # Sum over rounds of each seat's net-worth sources (engine breakdown).
    sources: list[dict[str, int]] = field(default_factory=lambda: [
        {"price_moves": 0, "cards_events": 0, "cargo": 0, "spoiled": 0} for _ in range(4)])


def play_game(config: GameConfig, seed: int, bots: list[BotFactory], insider_mode: str = "full") -> GameResult:
    """insider_mode: 'full' (reveal event), 'partial' (good/bad news only), 'off' (card does nothing)."""
    ids = [f"s{i}" for i in range(1, 5)]
    players = [PlayerState(pid, i + 1, pid, config.starting_coins, {c: 0 for c in COMMODITIES})
               for i, pid in enumerate(ids)]
    agents = [make(random.Random(f"bot:{seed}:{i}")) for i, make in enumerate(bots)]
    hands = {pid: list(h) for pid, h in deal_hands(seed, ids, config).items()}
    orders = deal_orders(seed, ids) if config.full else {}
    events = schedule_events(seed, config)
    prices = dict(config.starting_prices)
    history = [dict(prices)]
    public_worth = {pid: config.starting_coins for pid in ids}
    result = GameResult(seed, [a.name for a in agents], [], [], [], orders=[orders.get(pid) for pid in ids])
    lots_won = {pid: 0 for pid in ids}

    for r in range(1, config.num_rounds + 1):
        rules = round_rules(config, seed, r)
        event = events[r - 1]
        subs: dict[str, Submission] = {}
        for agent, p in zip(agents, players):
            view = BotView(config, r, dict(prices), list(history), p, list(hands[p.player_id]),
                           [(q.player_id, public_worth[q.player_id]) for q in players if q is not p],
                           rumor_subject(event), rules, orders.get(p.player_id))
            if "insider_tip" in view.hand and agent.wants_insider(view):
                hands[p.player_id].remove("insider_tip")
                view.hand.remove("insider_tip")
                view.card_used = True
                result.card_plays.append((r, agent.name, "insider_tip"))
                if insider_mode == "full":
                    view.insider_event = event
                elif insider_mode == "partial":
                    view.insider_hint = (1 if event.endswith("_boom") else -1 if event.endswith("_bust") else 0)
            sub = agent.decide(view)
            others = [q.player_id for q in players if q is not p]
            for candidate in (sub, replace(sub, bid=0), replace(sub, bid=0, card=None), Submission.hold()):
                try:
                    validate_submission(config, p, prices, candidate, view.hand, others, view.card_used, rules)
                except InvalidAction:
                    result.illegal += 1
                    continue
                sub = candidate
                break
            if sub.card:
                hands[p.player_id].remove(sub.card.card_type)
                result.card_plays.append((r, agent.name, sub.card.card_type))
            subs[p.player_id] = sub
        out = resolve_round(config, r, prices, players, subs, event, rules)
        auction = out.summary["auction"]
        if auction and auction["winner"]:
            lots_won[auction["winner"]] += 1
        players, prices = out.players, out.prices
        history.append(dict(prices))
        for row in out.summary["players"]:
            public_worth[row["player_id"]] = row["net_worth"]
            src = result.sources[row["seat"] - 1]
            for k in src:
                src[k] += row["breakdown"][k]
        result.worth_by_round.append([public_worth[pid] for pid in ids])
        result.prices_by_round.append(dict(prices))
        for p in players:
            if p.coins < 0 or any(q < 0 for q in p.inventory.values()):
                result.invariant_errors.append(f"negative state r{r} {p.player_id}")
        if not all(config.price_min <= v <= config.price_max for v in prices.values()):
            result.invariant_errors.append(f"price out of bounds r{r}")

    bonuses = {p.player_id: order_bonus(orders.get(p.player_id), p, lots_won[p.player_id]) for p in players}
    result.ranking = final_ranking(players, prices, bonuses if config.full else None)
    result.lots_won = [lots_won[pid] for pid in ids]
    return result


# ---------------------------------------------------------------------------
# Match schedules
# ---------------------------------------------------------------------------

def mixed_schedule(names: list[str], games: int) -> list[list[str]]:
    """Every 4-strategy subset in every seat rotation, cycled. Each strategy appears in equal numbers."""
    combos = list(itertools.combinations(names, 4)) if len(names) > 4 else [tuple(names)]
    lineups = []
    for i in range(games):
        combo = list(combos[i % len(combos)])
        shift = (i // len(combos)) % 4
        lineups.append(combo[shift:] + combo[:shift])
    return lineups


def run(config: GameConfig, lineups: list[list[str]], factories: dict[str, BotFactory], seed0: int = 1000,
        insider_mode: str = "full") -> list[GameResult]:
    return [play_game(config, seed0 + i, [factories[n] for n in lineup], insider_mode)
            for i, lineup in enumerate(lineups)]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def summarize(results: list[GameResult]) -> dict:
    by_strategy: dict[str, dict[str, list]] = {}
    seat_wins = [0.0] * 4
    gaps, comebacks, ties = [], 0, 0
    illegal = sum(r.illegal for r in results)
    errors = [e for r in results for e in r.invariant_errors]
    card_counts: dict[str, int] = {}
    order_hits: dict[str, list[int]] = {}
    for res in results:
        ranks = {row["seat"]: row for row in res.ranking}
        winners = [s for s, row in ranks.items() if row["rank"] == 1]
        if len(winners) > 1:
            ties += 1
        for s in winners:
            seat_wins[s - 1] += 1 / len(winners)
        nws = [row["net_worth"] for row in res.ranking]
        gaps.append(max(nws) - min(nws))
        mid = res.worth_by_round[len(res.worth_by_round) // 2 - 1]
        last_mid = min(range(4), key=lambda i: mid[i])
        if ranks[last_mid + 1]["rank"] == 1:
            comebacks += 1
        for seat, name in enumerate(res.strategies, start=1):
            row = ranks[seat]
            d = by_strategy.setdefault(name, {"nw": [], "coins": [], "goods": [], "wins": [], "bonus": [],
                                              "price_moves": [], "cards_events": [], "cargo": [], "spoiled": []})
            for k in ("price_moves", "cards_events", "cargo", "spoiled"):
                d[k].append(res.sources[seat - 1][k])
            d["nw"].append(row["net_worth"])
            d["coins"].append(row["coins"])
            d["goods"].append(row["inventory_value"])
            d["bonus"].append(row.get("order_bonus", 0))
            d["wins"].append(1 / len(winners) if seat in winners else 0.0)
            order = res.orders[seat - 1]
            if order:
                order_hits.setdefault(order, []).append(1 if row.get("order_bonus") else 0)
        for _, _, card in res.card_plays:
            card_counts[card] = card_counts.get(card, 0) + 1
    n = len(results)
    table = {
        name: {
            "games": len(d["nw"]),
            "mean": statistics.fmean(d["nw"]),
            "median": statistics.median(d["nw"]),
            "stdev": statistics.pstdev(d["nw"]),
            "win_rate": sum(d["wins"]) / len(d["wins"]),
            "coins": statistics.fmean(d["coins"]),
            "goods": statistics.fmean(d["goods"]),
            "bonus": statistics.fmean(d["bonus"]),
            **{k: statistics.fmean(d[k]) for k in ("price_moves", "cards_events", "cargo", "spoiled")},
        }
        for name, d in by_strategy.items()
    }
    return {
        "games": n, "strategies": table, "seat_win_rate": [w / n for w in seat_wins], "tie_rate": ties / n,
        "mean_gap": statistics.fmean(gaps), "comeback_rate": comebacks / n, "illegal_moves": illegal,
        "invariant_errors": errors, "card_plays": card_counts,
        "order_success": {k: sum(v) / len(v) for k, v in sorted(order_hits.items())},
    }


def mean_price_path(results: list[GameResult]) -> dict[str, list[float]]:
    rounds = len(results[0].prices_by_round)
    return {c: [statistics.fmean(r.prices_by_round[i][c] for r in results) for i in range(rounds)]
            for c in COMMODITIES}
